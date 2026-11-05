import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Depends, Query, HTTPException
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased
from db import Base, engine, get_db, init_lock_trigger, SessionLocal
import models as m
import sync
from providers import STATE, ProviderError, cached

LEAGUES = [("Premier League", "England"), ("La Liga", "Spain"), ("Serie A", "Italy"),
           ("Bundesliga", "Germany"), ("Ligue 1", "France")]

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    init_lock_trigger()
    with SessionLocal() as db:
        if db.scalar(select(func.count()).select_from(m.League)) == 0:
            db.add_all([m.League(name=n, country=c) for n, c in LEAGUES])
            db.commit()
        sync.set_league_ids(db)
    sched = BackgroundScheduler(timezone="UTC")
    def run(fn):
        def job():
            with SessionLocal() as s:
                fn(s)
        return job
    sched.add_job(run(sync.sync_fixtures), "interval", hours=6, next_run_time=datetime.now(timezone.utc), max_instances=1)
    sched.add_job(run(sync.sync_live), "interval", seconds=60, max_instances=1)
    sched.start()
    yield
    sched.shutdown(wait=False)

app = FastAPI(title="PCREW FOOTBALL TIP API", version="0.1.0", lifespan=lifespan)
origin = os.environ.get("FRONTEND_ORIGIN", "http://localhost:5173")
app.add_middleware(CORSMiddleware, allow_origins=[origin], allow_methods=["GET"], allow_headers=["*"])

@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"status": "ok", "database": "connected"}

@app.get("/api/leagues")
def leagues(db: Session = Depends(get_db)):
    return [{"id": l.id, "name": l.name, "country": l.country} for l in db.scalars(select(m.League).order_by(m.League.id))]

@app.get("/api/fixtures")
def fixtures(league_id: int | None = None, status: str | None = Query(None, pattern="^(upcoming|live|finished)$"),
             db: Session = Depends(get_db)):
    q = select(m.Fixture).order_by(m.Fixture.kickoff)
    if league_id: q = q.where(m.Fixture.league_id == league_id)
    if status: q = q.where(m.Fixture.status == status)
    return [{"id": f.id, "league_id": f.league_id, "home_team_id": f.home_team_id, "away_team_id": f.away_team_id,
             "kickoff": f.kickoff, "status": f.status} for f in db.scalars(q.limit(200))]

@app.get("/api/predictions")
def predictions(fixture_id: int | None = None, db: Session = Depends(get_db)):
    q = select(m.Prediction).order_by(m.Prediction.created_at.desc())
    if fixture_id: q = q.where(m.Prediction.fixture_id == fixture_id)
    return [{"id": p.id, "fixture_id": p.fixture_id, "model_version": p.model_version, "is_pass": p.is_pass,
             "p_home": p.p_home, "p_draw": p.p_draw, "p_away": p.p_away, "lock_time": p.lock_time}
            for p in db.scalars(q.limit(200))]

@app.get("/api/performance")
def performance(db: Session = Depends(get_db)):
    h = m.PredictionHistory
    settled = db.scalar(select(func.count()).select_from(h)) or 0
    passed = db.scalar(select(func.count()).select_from(h).where(h.correct.is_(None))) or 0
    if settled == passed:
        return {"settled": settled, "passed": passed, "accuracy": None, "brier": None, "log_loss": None,
                "note": "No settled predictions yet. Nothing is shown until real results exist."}
    scored = settled - passed
    right = db.scalar(select(func.count()).select_from(h).where(h.correct.is_(True))) or 0
    return {"settled": settled, "passed": passed, "accuracy": right / scored,
            "brier": db.scalar(select(func.avg(h.brier))), "log_loss": db.scalar(select(func.avg(h.log_loss)))}


@app.get("/api/status")
def status():
    return {**STATE, "api_key_set": bool(os.environ.get("API_FOOTBALL_KEY", "").strip())}

@app.get("/api/matches")
def matches(league_id: int | None = None, status: str | None = Query(None, pattern="^(upcoming|live|finished)$"),
            db: Session = Depends(get_db)):
    H, A = aliased(m.Team), aliased(m.Team)
    now = datetime.now(timezone.utc)
    q = (select(m.Fixture, m.League, H, A, m.LiveMatchData, m.MatchResult)
         .join(m.League, m.League.id == m.Fixture.league_id)
         .join(H, H.id == m.Fixture.home_team_id).join(A, A.id == m.Fixture.away_team_id)
         .outerjoin(m.LiveMatchData, m.LiveMatchData.fixture_id == m.Fixture.id)
         .outerjoin(m.MatchResult, m.MatchResult.fixture_id == m.Fixture.id)
         .where(m.Fixture.kickoff.between(now - timedelta(days=3), now + timedelta(days=14)))
         .order_by(m.Fixture.kickoff).limit(300))
    if league_id: q = q.where(m.Fixture.league_id == league_id)
    if status: q = q.where(m.Fixture.status == status)
    out = []
    for f, lg, h, a, lv, res in db.execute(q):
        score = [res.home_goals, res.away_goals] if res else ([lv.home_goals, lv.away_goals] if lv and f.status == "live" else None)
        team = lambda t: {"id": t.id, "name": t.name, "short": t.short_name, "logo": t.logo_url}
        out.append({"id": f.id, "league": lg.name, "league_id": lg.id, "home": team(h), "away": team(a),
                    "kickoff": f.kickoff, "status": f.status, "minute": lv.minute if lv else None, "score": score})
    return out

@app.get("/api/matches/{fixture_id}/detail")
def match_detail(fixture_id: int, db: Session = Depends(get_db)):
    f = db.get(m.Fixture, fixture_id)
    if not f: raise HTTPException(404, "Match not found")
    home, away = db.get(m.Team, f.home_team_id), db.get(m.Team, f.away_team_id)
    pid, ttl = int(f.provider_id), (60 if f.status == "live" else 3600)
    try:
        ev = cached(("ev", pid), ttl, lambda: sync.api.events(pid))
        lu = cached(("lu", pid), ttl, lambda: sync.api.lineups(pid))
        st = cached(("st", pid), ttl, lambda: sync.api.statistics(pid))
        h2 = cached(("h2", pid), 86400, lambda: sync.api.head_to_head(int(home.provider_id), int(away.provider_id)))
    except ProviderError as e:
        raise HTTPException(502, f"Data provider error: {e}")
    return {
        "events": [{"minute": e["time"]["elapsed"], "team": e["team"]["name"], "player": (e.get("player") or {}).get("name"),
                    "type": e["type"], "detail": e["detail"]} for e in ev],
        "lineups": [{"team": x["team"]["name"], "formation": x.get("formation"),
                     "start": [{"name": p["player"]["name"], "number": p["player"]["number"], "pos": p["player"]["pos"]} for p in x["startXI"]],
                     "bench": [{"name": p["player"]["name"], "number": p["player"]["number"]} for p in x["substitutes"]]} for x in lu],
        "statistics": [{"team": x["team"]["name"], "stats": {s["type"]: s["value"] for s in x["statistics"]}} for x in st],
        "h2h": [{"date": x["fixture"]["date"], "home": x["teams"]["home"]["name"], "away": x["teams"]["away"]["name"],
                 "score": [x["goals"]["home"], x["goals"]["away"]]} for x in h2],
    }
