from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crud, security
from app.models import AccountLinkRequest, AccountLinkStatus, User, UserRole
from app.tests.conftest import World


def auth(user: User) -> dict[str, str]:
    token = security.create_access_token(
        user.id,
        role=user.role.value,
        practice_id=user.practice_id,
        site_id=user.site_id,
    )
    return {"Authorization": f"Bearer {token}"}


def make_walkin(client, world: World, **overrides) -> dict:
    """A walk-in patient record, created by staff the way every patient
    record is created before the person ever has a login."""
    body = {
        "first_name": "Pat",
        "last_name": "One",
        "date_of_birth": "1990-01-01",
        "national_id": "9001010000001",
        "contact_email": "pat.one@example.co.za",
        "contact_phone": "071 234 5678",
    }
    body.update(overrides)
    resp = client.post(
        "/patients", json=body, headers=auth(world.clinician_a)
    )
    assert resp.status_code == 201
    return resp.json()


def register(client, db: Session, email="pat.one@login.test") -> User:
    return crud.create_user(
        db, email=email, password="pw", role=UserRole.patient
    )


def test_claims_by_national_id_and_email(client, db: Session, world: World) -> None:
    patient = make_walkin(client, world)
    user = register(client, db)

    resp = client.post(
        "/account-link-requests",
        json={"identifier": "9001010000001", "contact_value": "pat.one@example.co.za"},
        headers=auth(user),
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == patient["id"]

    me = client.get("/portal/me", headers=auth(user))
    assert me.status_code == 200 and me.json()["id"] == patient["id"]

    (entry,) = db.scalars(select(AccountLinkRequest)).all()
    assert entry.status == AccountLinkStatus.verified
    assert entry.patient_id == patient["id"]
    assert entry.verified_at is not None


def test_claims_by_phone_ignoring_formatting(
    client, db: Session, world: World
) -> None:
    make_walkin(client, world)
    user = register(client, db)

    resp = client.post(
        "/account-link-requests",
        json={"identifier": "9001010000001", "contact_value": "0712345678"},
        headers=auth(user),
    )
    assert resp.status_code == 200


def test_claims_by_passport_number(client, db: Session, world: World) -> None:
    make_walkin(
        client, world, national_id=None, passport_number="A1234567"
    )
    user = register(client, db)

    resp = client.post(
        "/account-link-requests",
        json={"identifier": "A1234567", "contact_value": "pat.one@example.co.za"},
        headers=auth(user),
    )
    assert resp.status_code == 200


def test_wrong_contact_value_is_rejected_generically(
    client, db: Session, world: World
) -> None:
    make_walkin(client, world)
    user = register(client, db)

    resp = client.post(
        "/account-link-requests",
        json={"identifier": "9001010000001", "contact_value": "someone-else@example.co.za"},
        headers=auth(user),
    )
    assert resp.status_code == 400
    assert "couldn't find" in resp.json()["detail"].lower()

    (entry,) = db.scalars(select(AccountLinkRequest)).all()
    assert entry.status == AccountLinkStatus.rejected
    assert entry.patient_id is None


def test_unknown_identifier_gets_the_same_generic_rejection(
    client, db: Session, world: World
) -> None:
    make_walkin(client, world)
    user = register(client, db)

    resp = client.post(
        "/account-link-requests",
        json={"identifier": "0000000000000", "contact_value": "pat.one@example.co.za"},
        headers=auth(user),
    )
    assert resp.status_code == 400
    assert "couldn't find" in resp.json()["detail"].lower()


def test_a_login_can_claim_records_at_more_than_one_practice(
    client, db: Session, world: World
) -> None:
    """Section 5.11: a person seen as a walk-in at two practices links
    both records to the one login."""
    make_walkin(client, world)
    other = make_walkin(
        client,
        world,
        national_id="9001010000002",
        contact_email="pat.two@example.co.za",
    )
    user = register(client, db)

    first = client.post(
        "/account-link-requests",
        json={"identifier": "9001010000001", "contact_value": "pat.one@example.co.za"},
        headers=auth(user),
    )
    assert first.status_code == 200

    second = client.post(
        "/account-link-requests",
        json={"identifier": "9001010000002", "contact_value": other["contact_email"]},
        headers=auth(user),
    )
    assert second.status_code == 200

    records = client.get("/portal/records", headers=auth(user)).json()
    assert {r["patient_id"] for r in records} == {
        first.json()["id"],
        second.json()["id"],
    }


def test_an_already_claimed_record_cannot_be_claimed_again(
    client, db: Session, world: World
) -> None:
    make_walkin(client, world)
    first_user = register(client, db, "first@login.test")
    client.post(
        "/account-link-requests",
        json={"identifier": "9001010000001", "contact_value": "pat.one@example.co.za"},
        headers=auth(first_user),
    )

    second_user = register(client, db, "second@login.test")
    resp = client.post(
        "/account-link-requests",
        json={"identifier": "9001010000001", "contact_value": "pat.one@example.co.za"},
        headers=auth(second_user),
    )
    assert resp.status_code == 400


def test_staff_cannot_use_the_claim_endpoint(client, world: World) -> None:
    resp = client.post(
        "/account-link-requests",
        json={"identifier": "9001010000001", "contact_value": "x"},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 403
