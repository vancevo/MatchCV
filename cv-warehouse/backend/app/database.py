from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


database_url = get_settings().database_url
connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# create_all() never alters an existing table, and this service has no migration tool, so columns added
# after the first release are declared here and added on startup when the table predates them.
ADDED_COLUMNS = {
    "cv_documents": (
        ("level", "VARCHAR(40) NOT NULL DEFAULT ''"),
        ("certifications", "JSON"),
        ("edited_fields", "JSON"),
    ),
}


def ensure_columns() -> list[str]:
    """ALTER TABLE ... ADD COLUMN for every declared column the database does not have yet."""
    added: list[str] = []
    inspector = inspect(engine)
    for table, columns in ADDED_COLUMNS.items():
        if not inspector.has_table(table):
            continue
        present = {column["name"] for column in inspector.get_columns(table)}
        with engine.begin() as connection:
            for name, ddl in columns:
                if name not in present:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                    added.append(f"{table}.{name}")
    return added
