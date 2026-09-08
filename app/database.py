import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# Defaults to a local Postgres instance so development matches the intended
# production database. Override with the DATABASE_URL env var (see
# .env.example); the SQLite branch below keeps the test suite driver-free.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://limbitless:limbitless@localhost:5432/limbitless",
)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Base class every ORM model inherits from."""


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
