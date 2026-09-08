"""Behavioural tests for the query-scoping helpers.

Uses a throwaway model on its own metadata that carries plain
practice_id / site_id columns (the mixins' FKs need the practices / sites
tables, which is more than the helpers care about — they only read the
attributes). PracticeScoped itself is exercised via Site in test_models.
"""

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from app.scoping import RequestScope, apply_scope, scope_to_practice, scope_to_site


class _Base(DeclarativeBase):
    pass


class _Row(_Base):
    __tablename__ = "rows"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column()
    practice_id: Mapped[int] = mapped_column()
    site_id: Mapped[int | None] = mapped_column()


@pytest.fixture
def db() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all(
        [
            _Row(label="p1-s1", practice_id=1, site_id=1),
            _Row(label="p1-s2", practice_id=1, site_id=2),
            _Row(label="p1-none", practice_id=1, site_id=None),
            _Row(label="p2-s1", practice_id=2, site_id=1),
        ]
    )
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _labels(session: Session, stmt) -> set[str]:
    return {row.label for row in session.scalars(stmt)}


def test_scope_to_practice_filters_by_practice(db: Session) -> None:
    assert _labels(db, scope_to_practice(select(_Row), _Row, 1)) == {
        "p1-s1",
        "p1-s2",
        "p1-none",
    }


def test_scope_to_site_filters_by_site(db: Session) -> None:
    assert _labels(db, scope_to_site(select(_Row), _Row, 1)) == {"p1-s1", "p2-s1"}


def test_apply_scope_practice_only(db: Session) -> None:
    stmt = apply_scope(select(_Row), _Row, RequestScope(practice_id=1))
    assert _labels(db, stmt) == {"p1-s1", "p1-s2", "p1-none"}


def test_apply_scope_practice_and_site(db: Session) -> None:
    stmt = apply_scope(select(_Row), _Row, RequestScope(practice_id=1, site_id=1))
    assert _labels(db, stmt) == {"p1-s1"}
