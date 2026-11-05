import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base

url = os.environ.get("DATABASE_URL", "")
if not url:
    raise RuntimeError("DATABASE_URL is not set. Add it in Railway > Variables.")
if url.startswith("postgres://"):
    url = url.replace("postgres://", "postgresql+psycopg2://", 1)
elif url.startswith("postgresql://"):
    url = url.replace("postgresql://", "postgresql+psycopg2://", 1)

engine = create_engine(url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()

FUNC = """
CREATE OR REPLACE FUNCTION protect_locked_prediction() RETURNS trigger AS $$
BEGIN
  IF OLD.lock_time <= now() THEN
    RAISE EXCEPTION 'Prediction % is locked and cannot be changed', OLD.id;
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""
DROP = "DROP TRIGGER IF EXISTS predictions_lock ON predictions"
TRIG = """
CREATE TRIGGER predictions_lock BEFORE UPDATE OR DELETE ON predictions
FOR EACH ROW EXECUTE FUNCTION protect_locked_prediction()
"""

def init_lock_trigger():
    """Locked predictions can never be edited or deleted, even by the app."""
    with engine.begin() as c:
        for stmt in (FUNC, DROP, TRIG):
            c.execute(text(stmt))

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
