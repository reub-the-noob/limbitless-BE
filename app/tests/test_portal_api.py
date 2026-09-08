from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crud, security
from app.models import AuditAction, AuditLogEntry, User, UserRole
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


def make_patient_user(db: Session, world: World, email: str) -> User:
    return crud.create_user(
        db,
        email=email,
        password="pw",
        role=UserRole.patient,
        practice_id=world.practice_a.id,
        site_id=world.site_a1.id,
    )


def link(client, world: World, patient_id: int, user: User):
    return client.post(
        f"/patients/{patient_id}/link-user",
        json={"user_id": user.id},
        headers=auth(world.clinician_a),
    )


def linked(client, db: Session, world: World, email="p1@portal.test"):
    """A patient record wired to a fresh patient-role login."""
    patient = make_patient(client, world, site_id=world.site_a1.id)
    user = make_patient_user(db, world, email)
    assert link(client, world, patient["id"], user).status_code == 200
    return patient, user


# --- linking ---------------------------------------------------------


def test_link_requires_a_patient_role_account(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)
    resp = client.post(
        f"/patients/{patient['id']}/link-user",
        json={"user_id": world.clinician_a.id},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 400


def test_a_login_can_hold_records_at_more_than_one_practice(
    client, db: Session, world: World
) -> None:
    first, user = linked(client, db, world)
    second = make_patient(
        client, world, national_id="9001010000002", site_id=world.site_a1.id
    )
    assert link(client, world, second["id"], user).status_code == 200

    records = client.get("/portal/records", headers=auth(user)).json()
    assert {r["patient_id"] for r in records} == {first["id"], second["id"]}


def test_a_record_already_linked_to_another_login_is_a_conflict(
    client, db: Session, world: World
) -> None:
    patient, _ = linked(client, db, world)
    someone_else = make_patient_user(db, world, "other@portal.test")
    assert link(client, world, patient["id"], someone_else).status_code == 409


def test_unlink(client, db: Session, world: World) -> None:
    patient, user = linked(client, db, world)
    resp = client.delete(
        f"/patients/{patient['id']}/link-user", headers=auth(world.clinician_a)
    )
    assert resp.status_code == 200 and resp.json()["user_id"] is None
    assert client.get("/portal/me", headers=auth(user)).status_code == 404


# --- the portal ----------------------------------------------------


def test_unlinked_patient_login_gets_404(
    client, db: Session, world: World
) -> None:
    lonely = make_patient_user(db, world, "lonely@portal.test")
    assert client.get("/portal/me", headers=auth(lonely)).status_code == 404


def test_me_returns_the_linked_record_with_names(
    client, db: Session, world: World
) -> None:
    patient, user = linked(client, db, world)
    body = client.get("/portal/me", headers=auth(user)).json()
    assert body["id"] == patient["id"]
    assert body["practice_name"] == world.practice_a.name
    assert body["site_name"] == world.site_a1.name


def test_involvements_and_milestones_are_scoped_to_me(
    client, db: Session, world: World
) -> None:
    patient, user = linked(client, db, world)
    inv = client.post(
        f"/patients/{patient['id']}/involvements",
        json={"kind": "orthotic_need", "region": "spine"},
        headers=auth(world.clinician_a),
    )
    assert inv.status_code == 201

    involvements = client.get(
        "/portal/me/involvements", headers=auth(user)
    ).json()
    assert len(involvements) == 1
    assert involvements[0]["kind"] == "orthotic_need"
    assert involvements[0]["devices"] == []

    assert client.get("/portal/me/milestones", headers=auth(user)).json() == []


def _link_second(client, db, world, user, national_id="9001010000002"):
    p = make_patient(client, world, national_id=national_id, site_id=world.site_a1.id)
    assert link(client, world, p["id"], user).status_code == 200
    return p


def test_records_lists_every_linked_record(client, db: Session, world: World) -> None:
    first, user = linked(client, db, world)
    second = _link_second(client, db, world, user)

    records = client.get("/portal/records", headers=auth(user)).json()
    assert {r["patient_id"] for r in records} == {first["id"], second["id"]}
    assert all("practice_name" in r for r in records)


def test_single_record_endpoints_need_no_patient_id(
    client, db: Session, world: World
) -> None:
    patient, user = linked(client, db, world)
    me = client.get("/portal/me", headers=auth(user))
    assert me.status_code == 200 and me.json()["id"] == patient["id"]


def test_multi_record_endpoints_require_patient_id(
    client, db: Session, world: World
) -> None:
    first, user = linked(client, db, world)
    second = _link_second(client, db, world, user)

    # ambiguous without ?patient_id=
    assert client.get("/portal/me", headers=auth(user)).status_code == 400

    # each record resolves when named
    a = client.get(
        "/portal/me", params={"patient_id": first["id"]}, headers=auth(user)
    )
    assert a.status_code == 200 and a.json()["id"] == first["id"]
    b = client.get(
        "/portal/me", params={"patient_id": second["id"]}, headers=auth(user)
    )
    assert b.status_code == 200 and b.json()["id"] == second["id"]

    # a record the login isn't linked to -> 404, same as no link at all
    stranger = make_patient(client, world, national_id="9009009009009")
    assert (
        client.get(
            "/portal/me", params={"patient_id": stranger["id"]}, headers=auth(user)
        ).status_code
        == 404
    )


def test_instruments_follow_the_involvement_kind(
    client, db: Session, world: World
) -> None:
    patient, user = linked(client, db, world)
    client.post(
        f"/patients/{patient['id']}/involvements",
        json={"kind": "orthotic_need", "region": "lower_limb_left"},
        headers=auth(world.clinician_a),
    )
    instruments = client.get(
        "/portal/me/instruments", headers=auth(user)
    ).json()["instruments"]
    assert "orthosis_comfort_score" in instruments
    assert "pain_phantom" not in instruments


def test_submit_a_prom(client, db: Session, world: World) -> None:
    patient, user = linked(client, db, world)

    resp = client.post(
        "/portal/me/proms",
        json={"instrument": "socket_comfort_score", "responses": {"score": 3}},
        headers=auth(user),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["flagged"] is True
    assert body["recorded_by_id"] == user.id

    listed = client.get("/portal/me/proms", headers=auth(user)).json()
    assert [p["id"] for p in listed] == [body["id"]]

    (entry,) = db.scalars(
        select(AuditLogEntry).where(
            AuditLogEntry.action == AuditAction.create,
            AuditLogEntry.entity_type == "prom_record",
            AuditLogEntry.actor_id == user.id,
        )
    ).all()
    assert entry.practice_id == world.practice_a.id


def test_submit_a_prom_rejects_an_out_of_range_score(
    client, db: Session, world: World
) -> None:
    _, user = linked(client, db, world)
    resp = client.post(
        "/portal/me/proms",
        json={"instrument": "quest_satisfaction", "responses": {"score": 9}},
        headers=auth(user),
    )
    assert resp.status_code == 400


def test_staff_cannot_use_the_portal(client, world: World) -> None:
    assert (
        client.get("/portal/me", headers=auth(world.clinician_a)).status_code
        == 403
    )
    assert (
        client.get("/portal/me", headers=auth(world.admin_a)).status_code == 403
    )


def test_browse_availability_shows_only_open_slots_in_my_practice(
    client, db: Session, world: World
) -> None:
    _, user = linked(client, db, world)
    start = datetime.now().replace(
        hour=9, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    open_slot = client.post(
        "/availability",
        json={
            "start_time": start.isoformat(),
            "end_time": (start + timedelta(minutes=30)).isoformat(),
            "appointment_type": "review",
        },
        headers=auth(world.clinician_a),
    ).json()
    blocked = client.post(
        "/availability",
        json={
            "start_time": (start + timedelta(hours=1)).isoformat(),
            "end_time": (start + timedelta(hours=1, minutes=30)).isoformat(),
            "appointment_type": "review",
            "status": "blocked",
        },
        headers=auth(world.clinician_a),
    ).json()
    other_practice = client.post(
        "/availability",
        json={
            "start_time": start.isoformat(),
            "end_time": (start + timedelta(minutes=30)).isoformat(),
            "appointment_type": "review",
        },
        headers=auth(world.clinician_b),
    ).json()

    rows = client.get("/portal/availability", headers=auth(user)).json()
    ids = {r["id"] for r in rows}
    assert open_slot["id"] in ids
    assert blocked["id"] not in ids
    assert other_practice["id"] not in ids


# --- appointments ----------------------------------------------------


def make_slot(client, world: World, **overrides) -> dict:
    start = datetime.now().replace(
        hour=9, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    body = {
        "start_time": start.isoformat(),
        "end_time": (start + timedelta(minutes=30)).isoformat(),
        "appointment_type": "review",
    }
    body.update(overrides)
    resp = client.post(
        "/availability", json=body, headers=auth(world.clinician_a)
    )
    assert resp.status_code == 201
    return resp.json()


def test_book_an_open_slot(client, db: Session, world: World) -> None:
    _, user = linked(client, db, world)
    slot = make_slot(client, world)

    resp = client.post(
        "/portal/appointments",
        json={"slot_id": slot["id"]},
        headers=auth(user),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "booked"
    assert body["practitioner_id"] == world.clinician_a.id

    listed = client.get("/portal/appointments", headers=auth(user)).json()
    assert [a["id"] for a in listed] == [body["id"]]

    slot_after = client.get(
        f"/availability/{slot['id']}", headers=auth(world.clinician_a)
    ).json()
    assert slot_after["status"] == "booked"


def test_cannot_book_a_blocked_slot(client, db: Session, world: World) -> None:
    _, user = linked(client, db, world)
    slot = make_slot(client, world, status="blocked")
    resp = client.post(
        "/portal/appointments",
        json={"slot_id": slot["id"]},
        headers=auth(user),
    )
    assert resp.status_code == 409


def test_cannot_book_another_practices_slot(
    client, db: Session, world: World
) -> None:
    _, user = linked(client, db, world)
    other_slot = client.post(
        "/availability",
        json={
            "start_time": (
                datetime.now().replace(
                    hour=9, minute=0, second=0, microsecond=0
                )
                + timedelta(days=1)
            ).isoformat(),
            "end_time": (
                datetime.now().replace(
                    hour=9, minute=30, second=0, microsecond=0
                )
                + timedelta(days=1)
            ).isoformat(),
            "appointment_type": "review",
        },
        headers=auth(world.clinician_b),
    ).json()
    resp = client.post(
        "/portal/appointments",
        json={"slot_id": other_slot["id"]},
        headers=auth(user),
    )
    assert resp.status_code == 404


def test_patient_cancel_within_the_notice_window_is_flagged_late(
    client, db: Session, world: World
) -> None:
    _, user = linked(client, db, world)
    # Default practice notice window is 24h; a slot starting in 1h is
    # inside it.
    soon = datetime.now() + timedelta(hours=1)
    slot = client.post(
        "/availability",
        json={
            "start_time": soon.isoformat(),
            "end_time": (soon + timedelta(minutes=30)).isoformat(),
            "appointment_type": "review",
        },
        headers=auth(world.clinician_a),
    ).json()
    appt = client.post(
        "/portal/appointments",
        json={"slot_id": slot["id"]},
        headers=auth(user),
    ).json()

    resp = client.post(
        f"/portal/appointments/{appt['id']}/cancel", headers=auth(user)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "cancelled_by_patient"
    assert body["late_cancellation"] is True

    slot_after = client.get(
        f"/availability/{slot['id']}", headers=auth(world.clinician_a)
    ).json()
    assert slot_after["status"] == "open"


def test_patient_cancel_outside_the_notice_window_is_not_late(
    client, db: Session, world: World
) -> None:
    _, user = linked(client, db, world)
    # A week out is safely outside the default 24h notice window
    # regardless of what time of day the suite happens to run.
    far = datetime.now() + timedelta(days=7)
    slot = make_slot(
        client,
        world,
        start_time=far.isoformat(),
        end_time=(far + timedelta(minutes=30)).isoformat(),
    )
    appt = client.post(
        "/portal/appointments",
        json={"slot_id": slot["id"]},
        headers=auth(user),
    ).json()

    resp = client.post(
        f"/portal/appointments/{appt['id']}/cancel", headers=auth(user)
    )
    assert resp.status_code == 200
    assert resp.json()["late_cancellation"] is False


def test_cannot_cancel_someone_elses_appointment(
    client, db: Session, world: World
) -> None:
    _, user_a = linked(client, db, world, "a@portal.test")
    slot = make_slot(client, world)
    appt = client.post(
        "/portal/appointments",
        json={"slot_id": slot["id"]},
        headers=auth(user_a),
    ).json()

    other_patient = make_patient(
        client, world, national_id="9001010000009"
    )
    other_user = make_patient_user(db, world, "b@portal.test")
    assert link(client, world, other_patient["id"], other_user).status_code == 200

    resp = client.post(
        f"/portal/appointments/{appt['id']}/cancel", headers=auth(other_user)
    )
    assert resp.status_code == 404


# --- reschedule --------------------------------------------------------


def test_reschedule_is_one_atomic_action(client, db: Session, world: World) -> None:
    _, user = linked(client, db, world)
    old_slot = make_slot(client, world)
    new_slot = make_slot(
        client,
        world,
        start_time=(
            datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
            + timedelta(days=2)
        ).isoformat(),
        end_time=(
            datetime.now().replace(hour=9, minute=30, second=0, microsecond=0)
            + timedelta(days=2)
        ).isoformat(),
    )
    appt = client.post(
        "/portal/appointments",
        json={"slot_id": old_slot["id"]},
        headers=auth(user),
    ).json()

    resp = client.post(
        f"/portal/appointments/{appt['id']}/reschedule",
        json={"new_slot_id": new_slot["id"]},
        headers=auth(user),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["previous"]["status"] == "rescheduled"
    assert body["previous"]["rescheduled_to_id"] == body["new"]["id"]
    assert body["new"]["status"] == "booked"
    assert body["new"]["rescheduled_from_id"] == body["previous"]["id"]

    listed = client.get("/portal/appointments", headers=auth(user)).json()
    assert {a["status"] for a in listed} == {"rescheduled", "booked"}

    old_slot_after = client.get(
        f"/availability/{old_slot['id']}", headers=auth(world.clinician_a)
    ).json()
    assert old_slot_after["status"] == "open"


def test_cannot_reschedule_someone_elses_appointment(
    client, db: Session, world: World
) -> None:
    _, user_a = linked(client, db, world, "a@portal.test")
    slot = make_slot(client, world)
    new_slot = make_slot(
        client,
        world,
        start_time=(
            datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
            + timedelta(days=2)
        ).isoformat(),
        end_time=(
            datetime.now().replace(hour=9, minute=30, second=0, microsecond=0)
            + timedelta(days=2)
        ).isoformat(),
    )
    appt = client.post(
        "/portal/appointments",
        json={"slot_id": slot["id"]},
        headers=auth(user_a),
    ).json()

    other_patient = make_patient(client, world, national_id="9001010000010")
    other_user = make_patient_user(db, world, "c@portal.test")
    assert link(client, world, other_patient["id"], other_user).status_code == 200

    resp = client.post(
        f"/portal/appointments/{appt['id']}/reschedule",
        json={"new_slot_id": new_slot["id"]},
        headers=auth(other_user),
    )
    assert resp.status_code == 404
