from sqlalchemy import (Column, Integer, BigInteger, String, DateTime, Boolean, ForeignKey,
                        Numeric, JSON, CheckConstraint, UniqueConstraint, func)
from db import Base

TS = lambda: DateTime(timezone=True)

class League(Base):
    __tablename__ = "leagues"
    id = Column(Integer, primary_key=True)
    name = Column(String(80), unique=True, nullable=False)
    country = Column(String(60))
    provider_id = Column(String(40))

class Team(Base):
    __tablename__ = "teams"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    short_name = Column(String(10))
    league_id = Column(Integer, ForeignKey("leagues.id"))
    logo_url = Column(String(300))  # only from the data provider
    provider_id = Column(String(40), unique=True)

class Fixture(Base):
    __tablename__ = "fixtures"
    id = Column(Integer, primary_key=True)
    league_id = Column(Integer, ForeignKey("leagues.id"), nullable=False)
    home_team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    away_team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    kickoff = Column(TS(), nullable=False)
    status = Column(String(20), default="upcoming")  # upcoming | live | finished
    provider_id = Column(String(40), unique=True)

class MatchResult(Base):
    __tablename__ = "match_results"
    id = Column(Integer, primary_key=True)
    fixture_id = Column(Integer, ForeignKey("fixtures.id"), unique=True, nullable=False)
    home_goals = Column(Integer, nullable=False)
    away_goals = Column(Integer, nullable=False)
    recorded_at = Column(TS(), server_default=func.now())

class LiveMatchData(Base):
    __tablename__ = "live_match_data"
    id = Column(Integer, primary_key=True)
    fixture_id = Column(Integer, ForeignKey("fixtures.id"), unique=True, nullable=False)
    minute = Column(Integer)
    period = Column(String(20))
    home_goals = Column(Integer, default=0)
    away_goals = Column(Integer, default=0)
    events = Column(JSON)
    updated_at = Column(TS(), server_default=func.now(), onupdate=func.now())

class MatchStatistic(Base):
    __tablename__ = "match_statistics"
    id = Column(Integer, primary_key=True)
    fixture_id = Column(Integer, ForeignKey("fixtures.id"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    stats = Column(JSON)
    __table_args__ = (UniqueConstraint("fixture_id", "team_id"),)

class Prediction(Base):
    """Immutable after lock_time (enforced by a database trigger)."""
    __tablename__ = "predictions"
    id = Column(Integer, primary_key=True)
    fixture_id = Column(Integer, ForeignKey("fixtures.id"), nullable=False)
    model_version = Column(String(40), nullable=False)
    p_home = Column(Numeric(6, 5))
    p_draw = Column(Numeric(6, 5))
    p_away = Column(Numeric(6, 5))
    is_pass = Column(Boolean, nullable=False, default=False)
    inputs = Column(JSON)  # snapshot of features used
    created_at = Column(TS(), server_default=func.now())
    lock_time = Column(TS(), nullable=False)
    __table_args__ = (
        UniqueConstraint("fixture_id", "model_version"),
        CheckConstraint("is_pass OR abs(p_home + p_draw + p_away - 1) < 0.01", name="probs_sum_to_one"),
    )

class PredictionHistory(Base):
    """Written after the result is known. Never touches the prediction row."""
    __tablename__ = "prediction_history"
    id = Column(Integer, primary_key=True)
    prediction_id = Column(Integer, ForeignKey("predictions.id"), unique=True, nullable=False)
    actual = Column(String(1), nullable=False)  # H, D or A
    correct = Column(Boolean)  # null when the prediction was a PASS
    brier = Column(Numeric(8, 6))
    log_loss = Column(Numeric(8, 6))
    settled_at = Column(TS(), server_default=func.now())

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String(80))
    language = Column(String(5), default="en")
    created_at = Column(TS(), server_default=func.now())

class FavoriteTeam(Base):
    __tablename__ = "favorite_teams"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    __table_args__ = (UniqueConstraint("user_id", "team_id"),)

class NewsItem(Base):
    __tablename__ = "football_news"
    id = Column(Integer, primary_key=True)
    title = Column(String(300), nullable=False)
    source = Column(String(80), nullable=False)
    url = Column(String(500), unique=True, nullable=False)
    category = Column(String(30))
    published_at = Column(TS())

class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True)
    actor = Column(String(80), nullable=False)
    action = Column(String(40), nullable=False)
    table_name = Column(String(60))
    record_id = Column(Integer)
    detail = Column(JSON)
    created_at = Column(TS(), server_default=func.now())
