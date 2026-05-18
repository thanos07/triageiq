"""
app/db/database.py

Database engine setup and session management.

Supports both SQLite (local dev, default) and PostgreSQL (production deployments
on Streamlit Cloud / Neon / Supabase / RDS). Dialect is auto-detected from the
DATABASE_URL — no other code needs to change to swap backends.

  - sqlite:///./data/triage.db        →  SQLite, file-based (local dev)
  - postgresql://user:pw@host/db      →  Postgres (Neon, Supabase, RDS, etc.)

For production deployments, point DATABASE_URL at a managed Postgres instance
via Streamlit secrets or environment variables. Everything else — models, CRUD,
agents, pipeline — works unchanged.
"""

import os
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session, DeclarativeBase
from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Detect dialect once, up front ─────────────────────────────────────────────
_DB_URL = settings.database_url
IS_SQLITE = _DB_URL.startswith("sqlite")
IS_POSTGRES = _DB_URL.startswith("postgresql") or _DB_URL.startswith("postgres")

# Neon's connection string starts with "postgres://" but SQLAlchemy 2.x only
# accepts "postgresql://". Normalize so users can paste either form.
if _DB_URL.startswith("postgres://"):
    _DB_URL = _DB_URL.replace("postgres://", "postgresql://", 1)


# ── Ensure the data directory exists (SQLite only) ────────────────────────────
def _ensure_data_dir() -> None:
    """SQLite needs the parent directory to exist before it can create the .db file."""
    if not IS_SQLITE:
        return
    db_path = _DB_URL.replace("sqlite:///", "")
    data_dir = os.path.dirname(db_path)
    if data_dir and not os.path.exists(data_dir):
        os.makedirs(data_dir, exist_ok=True)
        logger.info(f"Created data directory: {data_dir}")


_ensure_data_dir()


# ── Engine (dialect-aware kwargs) ─────────────────────────────────────────────
if IS_SQLITE:
    # check_same_thread=False lets SQLAlchemy share connections across threads
    # (Streamlit + background pipeline thread).
    engine_kwargs = {
        "connect_args": {"check_same_thread": False},
        "echo": settings.is_development,
    }
elif IS_POSTGRES:
    # pool_pre_ping handles connections dropped by Neon's idle-timeout autosuspend.
    # pool_recycle forces a fresh connection every 5 min to stay ahead of timeouts.
    engine_kwargs = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
        "echo": False,  # Postgres echo is noisy; off even in dev
    }
else:
    engine_kwargs = {"echo": settings.is_development}

engine = create_engine(_DB_URL, **engine_kwargs)


# ── SQLite-specific PRAGMA setup ──────────────────────────────────────────────
if IS_SQLITE:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        """Enable WAL journal mode and foreign-key enforcement on every SQLite connection."""
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
