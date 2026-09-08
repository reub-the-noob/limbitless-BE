from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app import crud, security
from app.models import User, UserRole
from app.tests.conftest import World


def auth(user: User) -> dict[str, str]:
    token = security.create_access_token(
        user.id,
        role=user.role.value,
        practice_id=user.practice_id,
        site_id=user.site_id,
    )
    return {"Authorization": f"Bearer {token}"}


def make_patient(client, world: World, **overrides) -> dict:
    body = {
        "first_name": "Pat",
        "last_name": "One",
        "date_of_birth": "1990-01-01",
        "national_id": "9001010000001",
    }
    body.update(overrides)
    resp = client.post(
        "/patients", json=body, headers=auth(world.clinician_a)
    )
    assert resp.status_code == 201
    return resp.json()


def give_membership(client, world: World, patient_id: int, **overrides) -> dict:
    body = {
        "scheme_name": "Discovery Health",
        "plan_option": "Classic Comprehensive",
        "membership_number": "DH-12345",
    }
    body.update(overrides)
    resp = client.post(
        f"/patients/{patient_id}/medical-aid",
        json=body,
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 201
    return resp.json()


def reviewer(db: Session, email="rev@medscheme.test", scheme_name=None) -> User:
    user = crud.create_user(
        db, email=email, password="pw", role=UserRole.medical_aid_reviewer
    )
    if scheme_name:
        user.scheme_name = scheme_name
        db.commit()
    return user


TOMORROW_9 = datetime.now().replace(
    hour=9, minute=0, second=0, microsecond=0
) + timedelta(days=1)


def make_slot(client, world: World, **overrides) -> dict:
    body = {
        "start_time": TOMORROW_9.isoformat(),
        "end_time": (TOMORROW_9 + timedelta(minutes=30)).isoformat(),
        "appointment_type": "review",
    }
    body.update(overrides)
    resp = client.post(
        "/availability", json=body, headers=auth(world.clinician_a)
    )
    assert resp.status_code == 201
    return resp.json()


def book(client, world: World, patient_id: int, slot_id: int) -> dict:
    resp = client.post(
        "/appointments",
        json={"patient_id": patient_id, "slot_id": slot_id},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 201
    return resp.json()


# --- creation on booking ------------------------------------------------


def test_booking_a_medical_aid_patient_opens_a_pending_coverage(
    client, world: World
) -> None:
    patient = make_patient(client, world)
    give_membership(client, world, patient["id"])
    slot = make_slot(client, world)

    appt = book(client, world, patient["id"], slot["id"])
    assert appt["coverage_status"] == "pending"

    coverage = client.get(
        f"/appointments/{appt['id']}/coverage", headers=auth(world.clinician_a)
    )
    assert coverage.status_code == 200
    body = coverage.json()
    assert body["status"] == "pending"
    assert body["scheme_name"] == "Discovery Health"
    assert body["patient_name"] == "Pat One"


def test_booking_a_self_pay_patient_has_no_coverage(
    client, world: World
) -> None:
    patient = make_patient(client, world)
    slot = make_slot(client, world)

    appt = book(client, world, patient["id"], slot["id"])
    assert appt["coverage_status"] is None

    resp = client.get(
        f"/appointments/{appt['id']}/coverage", headers=auth(world.clinician_a)
    )
    assert resp.status_code == 404


# --- reviewer scoping: grant OR scheme match ----------------------------


def test_reviewer_sees_coverage_via_scheme_match_without_a_grant(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)
    give_membership(client, world, patient["id"], scheme_name="Bonitas")
    slot = make_slot(client, world)
    appt = book(client, world, patient["id"], slot["id"])
    rev = reviewer(db, scheme_name="Bonitas")

    rows = client.get("/review/coverage", headers=auth(rev)).json()
    assert [r["appointment_id"] for r in rows] == [appt["id"]]

    patients = client.get("/review/patients", headers=auth(rev)).json()
    assert [p["id"] for p in patients] == [patient["id"]]


def test_reviewer_with_a_different_scheme_does_not_see_it(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)
    give_membership(client, world, patient["id"], scheme_name="Bonitas")
    slot = make_slot(client, world)
    book(client, world, patient["id"], slot["id"])
    rev = reviewer(db, scheme_name="Discovery Health")

    assert client.get("/review/coverage", headers=auth(rev)).json() == []
    assert client.get("/review/patients", headers=auth(rev)).json() == []


def test_reviewer_sees_coverage_via_grant_without_a_scheme_match(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)
    give_membership(client, world, patient["id"], scheme_name="Momentum Health")
    slot = make_slot(client, world)
    appt = book(client, world, patient["id"], slot["id"])
    # This reviewer represents a different scheme entirely - only the
    # manual grant gives them access.
    rev = reviewer(db, scheme_name="Discovery Health")
    client.post(
        f"/patients/{patient['id']}/review-access",
        json={"reviewer_id": rev.id},
        headers=auth(world.clinician_a),
    )

    rows = client.get("/review/coverage", headers=auth(rev)).json()
    assert [r["appointment_id"] for r in rows] == [appt["id"]]


# --- deciding ------------------------------------------------------------


def test_approve_and_deny(client, db: Session, world: World) -> None:
    patient = make_patient(client, world)
    give_membership(client, world, patient["id"])
    slot = make_slot(client, world)
    appt = book(client, world, patient["id"], slot["id"])
    rev = reviewer(db, scheme_name="Discovery Health")

    coverage_id = client.get("/review/coverage", headers=auth(rev)).json()[0]["id"]
    resp = client.post(
        f"/review/coverage/{coverage_id}/approve",
        json={"authorization_number": "AUTH-001", "valid_until": "2027-01-01"},
        headers=auth(rev),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["authorization_number"] == "AUTH-001"
    assert body["decided_by_id"] == rev.id
    assert body["decided_at"] is not None

    appt_after = client.get(
        f"/appointments/{appt['id']}", headers=auth(world.clinician_a)
    ).json()
    assert appt_after["coverage_status"] == "approved"


def test_deny_records_a_reason_in_notes(client, db: Session, world: World) -> None:
    patient = make_patient(client, world)
    give_membership(client, world, patient["id"])
    slot = make_slot(client, world)
    book(client, world, patient["id"], slot["id"])
    rev = reviewer(db, scheme_name="Discovery Health")

    coverage_id = client.get("/review/coverage", headers=auth(rev)).json()[0]["id"]
    resp = client.post(
        f"/review/coverage/{coverage_id}/deny",
        json={"notes": "Plan doesn't cover this treatment type"},
        headers=auth(rev),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "denied"


def test_cannot_decide_an_already_decided_coverage(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)
    give_membership(client, world, patient["id"])
    slot = make_slot(client, world)
    book(client, world, patient["id"], slot["id"])
    rev = reviewer(db, scheme_name="Discovery Health")

    coverage_id = client.get("/review/coverage", headers=auth(rev)).json()[0]["id"]
    client.post(f"/review/coverage/{coverage_id}/approve", json={}, headers=auth(rev))
    resp = client.post(
        f"/review/coverage/{coverage_id}/deny", json={}, headers=auth(rev)
    )
    assert resp.status_code == 400


def test_status_filter_narrows_to_pending(client, db: Session, world: World) -> None:
    patient = make_patient(client, world)
    give_membership(client, world, patient["id"])
    slot = make_slot(client, world)
    book(client, world, patient["id"], slot["id"])
    rev = reviewer(db, scheme_name="Discovery Health")
    coverage_id = client.get("/review/coverage", headers=auth(rev)).json()[0]["id"]
    client.post(f"/review/coverage/{coverage_id}/approve", json={}, headers=auth(rev))

    pending = client.get(
        "/review/coverage?status=pending", headers=auth(rev)
    ).json()
    assert pending == []
    approved = client.get(
        "/review/coverage?status=approved", headers=auth(rev)
    ).json()
    assert len(approved) == 1


# --- reschedule carry-over ------------------------------------------------


def other_slot(client, world: World, **overrides) -> dict:
    return make_slot(
        client,
        world,
        start_time=(TOMORROW_9 + timedelta(days=1)).isoformat(),
        end_time=(TOMORROW_9 + timedelta(days=1, minutes=30)).isoformat(),
        **overrides,
    )


def test_reschedule_carries_over_an_approved_unexpired_same_type_coverage(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)
    give_membership(client, world, patient["id"])
    old_slot = make_slot(client, world, appointment_type="review")
    new_slot = other_slot(client, world, appointment_type="review")
    appt = book(client, world, patient["id"], old_slot["id"])
    rev = reviewer(db, scheme_name="Discovery Health")
    coverage_id = client.get("/review/coverage", headers=auth(rev)).json()[0]["id"]
    client.post(
        f"/review/coverage/{coverage_id}/approve",
        json={"valid_until": (date.today() + timedelta(days=30)).isoformat()},
        headers=auth(rev),
    )

    resp = client.post(
        f"/appointments/{appt['id']}/reschedule",
        json={"new_slot_id": new_slot["id"]},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 200
    body = resp.json()
    # The determination moves WITH the visit - the old (now rescheduled)
    # appointment is left with none, the new one has it.
    assert body["previous"]["coverage_status"] is None
    assert body["new"]["coverage_status"] == "approved"

    # Same coverage row moved - not a second one.
    new_coverage = client.get(
        f"/appointments/{body['new']['id']}/coverage",
        headers=auth(world.clinician_a),
    ).json()
    assert new_coverage["id"] == coverage_id


def test_reschedule_retriggers_coverage_for_a_different_appointment_type(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)
    give_membership(client, world, patient["id"])
    old_slot = make_slot(client, world, appointment_type="review")
    new_slot = other_slot(client, world, appointment_type="fitting")
    appt = book(client, world, patient["id"], old_slot["id"])
    rev = reviewer(db, scheme_name="Discovery Health")
    coverage_id = client.get("/review/coverage", headers=auth(rev)).json()[0]["id"]
    client.post(
        f"/review/coverage/{coverage_id}/approve",
        json={"valid_until": (date.today() + timedelta(days=30)).isoformat()},
        headers=auth(rev),
    )

    resp = client.post(
        f"/appointments/{appt['id']}/reschedule",
        json={"new_slot_id": new_slot["id"]},
        headers=auth(world.clinician_a),
    ).json()
    assert resp["previous"]["coverage_status"] == "approved"
    assert resp["new"]["coverage_status"] == "pending"

    new_coverage = client.get(
        f"/appointments/{resp['new']['id']}/coverage",
        headers=auth(world.clinician_a),
    ).json()
    assert new_coverage["id"] != coverage_id


def test_reschedule_retriggers_coverage_when_the_approval_expired(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)
    give_membership(client, world, patient["id"])
    old_slot = make_slot(client, world, appointment_type="review")
    new_slot = other_slot(client, world, appointment_type="review")
    appt = book(client, world, patient["id"], old_slot["id"])
    rev = reviewer(db, scheme_name="Discovery Health")
    coverage_id = client.get("/review/coverage", headers=auth(rev)).json()[0]["id"]
    client.post(
        f"/review/coverage/{coverage_id}/approve",
        json={"valid_until": (date.today() - timedelta(days=1)).isoformat()},
        headers=auth(rev),
    )

    resp = client.post(
        f"/appointments/{appt['id']}/reschedule",
        json={"new_slot_id": new_slot["id"]},
        headers=auth(world.clinician_a),
    ).json()
    assert resp["new"]["coverage_status"] == "pending"


def test_reschedule_does_not_carry_over_a_still_pending_coverage(
    client, world: World
) -> None:
    patient = make_patient(client, world)
    give_membership(client, world, patient["id"])
    old_slot = make_slot(client, world, appointment_type="review")
    new_slot = other_slot(client, world, appointment_type="review")
    appt = book(client, world, patient["id"], old_slot["id"])

    resp = client.post(
        f"/appointments/{appt['id']}/reschedule",
        json={"new_slot_id": new_slot["id"]},
        headers=auth(world.clinician_a),
    ).json()
    assert resp["previous"]["coverage_status"] == "pending"
    assert resp["new"]["coverage_status"] == "pending"


def test_reschedule_a_self_pay_appointment_has_no_coverage_either_side(
    client, world: World
) -> None:
    patient = make_patient(client, world)
    old_slot = make_slot(client, world)
    new_slot = other_slot(client, world)
    appt = book(client, world, patient["id"], old_slot["id"])

    resp = client.post(
        f"/appointments/{appt['id']}/reschedule",
        json={"new_slot_id": new_slot["id"]},
        headers=auth(world.clinician_a),
    ).json()
    assert resp["previous"]["coverage_status"] is None
    assert resp["new"]["coverage_status"] is None
