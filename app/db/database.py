"""
app/db/database.py

Database engine setup and session management.

We use SQLAlchemy with a SQLite backend. SQLite is chosen for the MVP because:
  - Zero infrastructure (no separate database server to run)
  - File-based (easy to inspect, backup, reset)
  - Sufficient for single-user portfolio demos
  - Can be swapped for Postgres later by changing DATABASE_URL in .env

For a production system you would replace SQLite with PostgreSQL and add
connection pooling. This file is the only place that would need to change.
"""

import os
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session, DeclarativeBase
from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Ensure the data directory exists ──────────────────────────────────────────
# SQLite needs the parent directory to exist before it can create the .db file
def _ensure_data_dir() -> None:
    db_path = settings.database_url.replace("sqlite:///", "")
    data_dir = os.path.dirname(db_path)
    if data_dir and not os.path.exists(data_dir):
        os.makedirs(data_dir, exist_ok=True)
        logger.info(f"Created data directory: {data_dir}")


_ensure_data_dir()


# ── Engine ────────────────────────────────────────────────────────────────────
# connect_args={"check_same_thread": False} is required for SQLite when
# multiple threads share the same connection (FastAPI uses a thread pool).
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
    echo=settings.is_development,  # Log SQL in dev; silence in prod
)


# Enable WAL mode for SQLite — better concurrent read performance
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


# ── Session factory ───────────────────────────────────────────────────────────
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


# ── Base class for all ORM models ─────────────────────────────────────────────
class Base(DeclarativeBase):
    """All SQLAlchemy ORM models inherit from this."""
    pass


# ── Dependency injection helper for FastAPI ───────────────────────────────────
def get_db():
    """
    FastAPI dependency that yields a database session.

    Usage in a route:
        @router.get("/incidents")
        def list_incidents(db: Session = Depends(get_db)):
            ...

    The session is always closed after the request, even if an exception occurs.
    """
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Database initialization ───────────────────────────────────────────────────
def init_db() -> None:
    """
    Create all database tables if they don't already exist.

    Called once at application startup from main.py.
    Safe to call multiple times — SQLAlchemy uses CREATE TABLE IF NOT EXISTS.
    """
    # Import models here to ensure they are registered with Base.metadata
    # before we call create_all. Without this import, the tables won't be created.
    from app.db import models  # noqa: F401

    logger.info("Initializing database...")
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables ready.")


def check_db_connection() -> bool:
    """
    Verify the database is reachable. Used in the health check endpoint.

    Returns:
        True if the database responds, False otherwise.
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return False
