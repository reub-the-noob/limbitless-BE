"""Shared test fixtures.

The suite runs against an in-memory SQLite database so it needs no
Postgres and no driver. SQLite renders the native Postgres enums as
VARCHAR + CHECK, which is enough to exercise the ORM models.
"""

from collections.abc import Generator
from dataclasses import dataclass

import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import crud, models  # noqa: F401  - models registers on Base.metadata
from app import security
from app.database import Base, get_db
from app.models import Practice, PracticeType, Site, SiteType, User, UserRole


@pytest.fixture(autouse=True)
def _fast_password_hashing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run bcrypt at its lowest cost for the whole test session.

    ``world`` alone hashes five passwords per test, and bcrypt at the
    production cost dominates the run. This keeps real hashing, salting
    and verification - only the deliberate slowness goes away, and no
    test asserts the production cost factor.
    """
    monkeypatch.setattr(
        security,
        "hash_password",
        lambda password: bcrypt.hashpw(
            password.encode(), bcrypt.gensalt(rounds=4)
        ).decode(),
    )


@pytest.fixture
def db() -> Generator[Session, None, None]:
    """A session backed by a fresh in-memory database per test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = testing_session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def client(db: Session) -> Generator[TestClient, None, None]:
    """A TestClient whose request handlers use the test session."""
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@dataclass
class World:
    """Two practices, a site in the first, and one user per relevant role."""

    practice_a: Practice
    practice_b: Practice
    site_a1: Site
    clinician_a: User
    prosthetist_a: User
    admin_a: User
    clinician_b: User
    platform_admin: User


@pytest.fixture
def world(db: Session) -> World:
    practice_a = Practice(name="Practice A", type=PracticeType.private_practice)
    practice_b = Practice(name="Practice B", type=PracticeType.private_practice)
    db.add_all([practice_a, practice_b])
    db.flush()
    site_a1 = Site(
        name="A Rooms", type=SiteType.location, practice_id=practice_a.id
    )
    db.add(site_a1)
    db.flush()

    def make(email: str, role: UserRole, practice_id=None, site_id=None) -> User:
        return crud.create_user(
            db,
            email=email,
            password="pw",
            role=role,
            practice_id=practice_id,
            site_id=site_id,
        )

    return World(
        practice_a=practice_a,
        practice_b=practice_b,
        site_a1=site_a1,
        clinician_a=make(
            "clin.a@x.io", UserRole.clinician, practice_a.id, site_a1.id
        ),
        prosthetist_a=make("pros.a@x.io", UserRole.prosthetist, practice_a.id),
        admin_a=make(
            "admin.a@x.io", UserRole.practice_administrator, practice_a.id
        ),
        clinician_b=make("clin.b@x.io", UserRole.clinician, practice_b.id),
        platform_admin=make("platform@x.io", UserRole.platform_administrator),
    )
