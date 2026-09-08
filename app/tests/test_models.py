from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Practice, PracticeType, Site, SiteType


def _practice(db: Session, name: str = "Northside Ortho") -> Practice:
    practice = Practice(name=name, type=PracticeType.private_practice)
    db.add(practice)
    db.flush()
    return practice


def test_practice_has_many_sites(db: Session) -> None:
    practice = _practice(db)
    practice.sites.append(Site(name="Main Rooms", type=SiteType.location))
    practice.sites.append(Site(name="Gait Lab", type=SiteType.department))
    db.flush()

    stored = db.scalar(select(Practice).where(Practice.id == practice.id))
    assert {s.name for s in stored.sites} == {"Main Rooms", "Gait Lab"}
    assert all(s.practice_id == practice.id for s in stored.sites)
    assert stored.sites[0].practice is stored


def test_enum_values_round_trip(db: Session) -> None:
    practice = _practice(db)
    site = Site(name="Main Rooms", type=SiteType.location, practice=practice)
    db.add(site)
    db.flush()
    db.refresh(site)

    assert site.type is SiteType.location
    assert practice.type is PracticeType.private_practice


def test_deleting_practice_cascades_to_sites(db: Session) -> None:
    practice = _practice(db)
    practice.sites.append(Site(name="Main Rooms", type=SiteType.location))
    db.flush()

    db.delete(practice)
    db.flush()

    assert db.scalars(select(Site)).all() == []


def test_timestamps_are_populated(db: Session) -> None:
    practice = _practice(db)
    db.refresh(practice)
    assert practice.created_at is not None
    assert practice.updated_at is not None
