from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Practice, Site, User, UserRole
from app.schemas import UserRead
from scripts.seed import USERS, reset, seed


def _count(db: Session, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def test_seed_creates_the_fixture(db: Session) -> None:
    seed(db)

    assert _count(db, Practice) == 3
    assert _count(db, Site) == 5
    assert _count(db, User) == len(USERS)
    assert set(db.scalars(select(User.role))) == set(UserRole)

    # the multi-site network has two sites; the private practice has one
    network = db.scalar(
        select(Practice).where(Practice.type == "hospital_network")
    )
    assert len(network.sites) == 2


def test_seeded_users_serialise_as_UserRead(db: Session) -> None:
    # UserRead.email is an EmailStr; reserved TLDs (.local, .test) would
    # pass the DB but 500 on the /auth/me response.
    seed(db)
    for user in db.scalars(select(User)):
        UserRead.model_validate(user)


def test_seed_is_idempotent(db: Session) -> None:
    seed(db)
    counts = {m: _count(db, m) for m in (Practice, Site, User)}

    seed(db)

    assert {m: _count(db, m) for m in (Practice, Site, User)} == counts


def test_reset_clears_the_seeded_tables(db: Session) -> None:
    seed(db)
    reset(db)

    assert _count(db, Practice) == 0
    assert _count(db, Site) == 0
    assert _count(db, User) == 0
