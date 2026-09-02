from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


def database_url() -> str:
    configured = get_settings().database_url
    if configured.startswith("postgres://"):
        configured = configured.replace("postgres://", "postgresql+psycopg://", 1)
    elif configured.startswith("postgresql://"):
        configured = configured.replace("postgresql://", "postgresql+psycopg://", 1)
    if configured:
        return configured
    path = Path(__file__).resolve().parents[1] / "talentflow.db"
    return f"sqlite:///{path}"


class Base(DeclarativeBase):
    pass


engine = create_engine(
    database_url(),
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if database_url().startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
