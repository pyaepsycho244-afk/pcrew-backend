from datetime import datetime, timedelta, timezone
from sqlalchemy import select
import models as m
from providers import ApiFootball, STATE, ProviderError

LEAGUE_IDS = {"Premier League": 39, "La Liga": 140, "Serie A": 135, "Bundesliga": 78, "Ligue 1": 61}
LIVE = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "INT", "SUSP"}
DONE = {"FT", "AET", "PEN"}
UPCOMING = {"NS", "TBD"}
api = ApiFootball()

def status_of(short: str) -> str:
    if short in LIVE: return "live"
    if short in DONE: return "finished"
    if short in UPCOMING: return "upcoming"
    return "postponed"  # PST, CANC, ABD, AWD, WO

def season_now() -> int:
    d = datetime.now(timezone.utc)
    return d.year if d.month >= 7 else d.year - 1

def set_league_ids(db):
    for lg in db.scalars(select(m.League)):
        if not lg.provider_id and lg.name in LEAGUE_IDS:
            lg.provider_id = str(LEAGUE_IDS[lg.name])
    db.commit()

def _team(db, t, league_id):
    row = db.scalar(select(m.Team).where(m.Team.provider_id == str(t["id"])))
    if not row:
        row = m.Team(name=t["name"], short_name=t["name"][:3].upper(), league_id=league_id,
                     logo_url=t.get("logo"), provider_id=str(t["id"]))
        db.add(row); db.flush()
    return row

def _apply(db, item, league_id):
    fx, tm, goals = item["fixture"], item["teams"], item["goals"]
    home, away = _team(db, tm["home"], league_id), _team(db, tm["away"], league_id)
    f = db.scalar(select(m.Fixture).where(m.Fixture.provider_id == str(fx["id"])))
    if not f:
        f = m.Fixture(league_id=league_id, home_team_id=home.id, away_team_id=away.id,
                      kickoff=datetime.fromisoformat(fx["date"]), provider_id=str(fx["id"]))
        db.add(f)
    f.kickoff = datetime.fromisoformat(fx["date"])
    f.status = status_of(fx["status"]["short"])
    db.flush()
    if goals["home"] is None:
        return
    if f.status == "finished":
        r = db.scalar(select(m.MatchResult).where(m.MatchResult.fixture_id == f.id)) or m.MatchResult(fixture_id=f.id, home_goals=0, away_goals=0)
        r.home_goals, r.away_goals = goals["home"], goals["away"]
        db.add(r)
    elif f.status == "live":
        lv = db.scalar(select(m.LiveMatchData).where(m.LiveMatchData.fixture_id == f.id)) or m.LiveMatchData(fixture_id=f.id)
        lv.minute, lv.period = fx["status"].get("elapsed"), fx["status"]["short"]
        lv.home_goals, lv.away_goals = goals["home"], goals["away"]
        if item.get("events") is not None:
            lv.events = [{"minute": e["time"]["elapsed"], "team": e["team"]["name"], "player": (e.get("player") or {}).get("name"),
                          "type": e["type"], "detail": e["detail"]} for e in item["events"]]
        db.add(lv)

def sync_fixtures(db):
    """About 5 API requests per run (one per league)."""
    season = season_now()
    STATE["season"] = season
    try:
        for lg in db.scalars(select(m.League).where(m.League.provider_id.is_not(None))).all():
            for item in api.fixtures(int(lg.provider_id), season):
                _apply(db, item, lg.id)
            db.commit()
        STATE["last_fixture_sync"] = datetime.now(timezone.utc).isoformat()
        STATE["last_error"] = None
    except ProviderError as e:
        db.rollback()
        STATE["last_error"] = f"fixtures: {e}"

def sync_live(db):
    """Costs 0 requests when no match is near; otherwise 1 request per run."""
    now = datetime.now(timezone.utc)
    near = db.scalar(select(m.Fixture.id).where(m.Fixture.kickoff.between(now - timedelta(hours=3), now + timedelta(minutes=5)),
                                                m.Fixture.status.in_(["upcoming", "live"])).limit(1))
    if near is None:
        return
    try:
        ids = [int(x.provider_id) for x in db.scalars(select(m.League).where(m.League.provider_id.is_not(None)))]
        seen = set()
        for item in api.live(ids):
            lg = db.scalar(select(m.League).where(m.League.provider_id == str(item["league"]["id"])))
            if lg:
                _apply(db, item, lg.id); seen.add(str(item["fixture"]["id"]))
        for f in db.scalars(select(m.Fixture).where(m.Fixture.status == "live")).all():
            if f.provider_id not in seen:  # just ended: fetch the final state once
                for item in api.fixture(int(f.provider_id)):
                    _apply(db, item, f.league_id)
        db.commit()
        STATE["last_live_sync"] = now.isoformat()
        STATE["last_error"] = None
    except ProviderError as e:
        db.rollback()
        STATE["last_error"] = f"live: {e}"
