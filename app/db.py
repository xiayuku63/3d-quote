"""SQLAlchemy engine + session management.

Use get_session() for new code. Existing get_db_conn() (raw sqlite3)
still works in parallel — they share the same DB_PATH.
"""

import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session, DeclarativeBase

from .config import DB_PATH

# Ensure parent directory exists for file-based SQLite
_db_dir = os.path.dirname(os.path.abspath(DB_PATH))
if _db_dir and not os.path.exists(_db_dir):
    os.makedirs(_db_dir, exist_ok=True)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
    echo=False,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """Enable WAL mode and foreign keys."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_session() -> Session:
    """Yield a SQLAlchemy session (use as FastAPI dependency)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_orm() -> None:
    """Create all tables from ORM models. Safe to call repeatedly."""
    from . import models_orm  # noqa — ensure models are registered
    Base.metadata.create_all(bind=engine)
