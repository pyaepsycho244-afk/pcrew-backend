import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from db import Base, engine, get_db, init_lock_trigger, SessionLocal
import models as m

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
    yield

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
