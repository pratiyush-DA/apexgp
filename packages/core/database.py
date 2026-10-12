"""Database Engine and Session Management

Provides SQLAlchemy session factories with dialect-specific optimizations
(WAL mode for SQLite, pool settings for PostgreSQL).
"""

import os
from contextlib import contextmanager
from typing import Generator
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from packages.core.models import Base

DEFAULT_DB_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/apexgraph.db")


def get_engine(db_url: str = DEFAULT_DB_URL):
    # Ensure data directory exists for sqlite
    if db_url.startswith("sqlite:///"):
        sqlite_path = db_url.replace("sqlite:///", "")
        db_dir = os.path.dirname(sqlite_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

        engine = create_engine(
            db_url,
            connect_args={"check_same_thread": False},
            echo=False,
        )

        # Enable SQLite Foreign Keys and WAL journal mode
        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

        return engine

    # PostgreSQL configuration
    return create_engine(
        db_url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        echo=False,
    )


engine = get_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db(target_engine=None):
    """Initializes tables in database."""
    eng = target_engine or engine
    Base.metadata.create_all(bind=eng)


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """Context manager for database sessions."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency for database sessions."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
