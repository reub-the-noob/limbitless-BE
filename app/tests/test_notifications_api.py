"""Notifications (requirements Section 5.6): the inbox endpoints plus the
event triggers that write into it."""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crud, security
from app.models import Notification, NotificationType, User, UserRole
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
    resp = client.post("/patients", json=body, headers=auth(world.clinician_a))
    assert resp.status_code == 201
    return resp.json()


def link_user(client, db: Session, world: World, patient_id: int, email: str) -> User:
    user = crud.create_user(
        db,
        email=email,
        password="pw",
        role=UserRole.patient,
        practice_id=world.practice_a.id,
    )
    resp = client.post(
        f"/patients/{patient_id}/link-user",
        json={"user_id": user.id},
        headers=auth(world.admin_a),
    )
    assert resp.status_code == 200
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
    resp = client.post("/availability", json=body, headers=auth(world.clinician_a))
    assert resp.status_code == 201
    return resp.json()


# --- inbox endpoints -------------------------------------------------


def _seed(db: Session, user_id: int, **kw) -> Notification:
    row = Notification(
        user_id=user_id,
        type=kw.get("type", NotificationType.appointment_booked),
        payload=kw.get("payload", {}),
        deliver_at=kw.get("deliver_at"),
        read_at=kw.get("read_at"),
    )
    db.add(row)
    db.commit()
    return row


def test_inbox_is_scoped_to_the_caller_and_newest_first(
    client, db: Session, world: World
) -> None:
    _seed(db, world.clinician_a.id, payload={"n": 1})
    _seed(db, world.clinician_a.id, payload={"n": 2})
    _seed(db, world.prosthetist_a.id, payload={"other": True})

    resp = client.get("/notifications", headers=auth(world.clinician_a))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["unread"] == 2
    assert [item["payload"]["n"] for item in body["items"]] == [2, 1]
    # the deep-link fields are on the wire
    assert "subject_type" in body["items"][0]
    assert "subject_id" in body["items"][0]


def test_unread_filter_and_count(client, db: Session, world: World) -> None:
    _seed(db, world.clinician_a.id)
    read_one = _seed(db, world.clinician_a.id, read_at=datetime.now())

    all_resp = client.get("/notifications", headers=auth(world.clinician_a)).json()
    assert all_resp["total"] == 2 and all_resp["unread"] == 1

    unread_resp = client.get(
        "/notifications?unread=true", headers=auth(world.clinician_a)
    ).json()
    assert unread_resp["total"] == 1
    assert all(item["id"] != read_one.id for item in unread_resp["items"])

    count = client.get(
        "/notifications/unread-count", headers=auth(world.clinician_a)
    ).json()
    assert count["unread"] == 1


def test_future_deliver_at_is_hidden_until_due(
    client, db: Session, world: World
) -> None:
    _seed(db, world.clinician_a.id, deliver_at=datetime.now() + timedelta(hours=6))
    _seed(db, world.clinician_a.id, deliver_at=datetime.now() - timedelta(hours=1))

    body = client.get("/notifications", headers=auth(world.clinician_a)).json()
    assert body["total"] == 1
    assert body["unread"] == 1
    count = client.get(
        "/notifications/unread-count", headers=auth(world.clinician_a)
    ).json()
    assert count["unread"] == 1


def test_mark_read_one_and_all(client, db: Session, world: World) -> None:
    a = _seed(db, world.clinician_a.id)
    _seed(db, world.clinician_a.id)

    read = client.post(
        f"/notifications/{a.id}/read", headers=auth(world.clinician_a)
    )
    assert read.status_code == 200 and read.json()["read_at"] is not None
    assert (
        client.get(
            "/notifications/unread-count", headers=auth(world.clinician_a)
        ).json()["unread"]
        == 1
    )

    all_read = client.post(
        "/notifications/read-all", headers=auth(world.clinician_a)
    )
    assert all_read.status_code == 200 and all_read.json()["unread"] == 0


def test_cannot_read_someone_elses_notification(
    client, db: Session, world: World
) -> None:
    other = _seed(db, world.prosthetist_a.id)
    resp = client.post(
        f"/notifications/{other.id}/read", headers=auth(world.clinician_a)
    )
    assert resp.status_code == 404


# --- event triggers -----------------------------------------------


def test_staff_booking_notifies_the_linked_patient(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)
    patient_user = link_user(
        client, db, world, patient["id"], "pat.one@example.co.za"
    )
    slot = make_slot(client, world)

    client.post(
        "/appointments",
        json={"patient_id": patient["id"], "slot_id": slot["id"]},
        headers=auth(world.clinician_a),
    )

    rows = db.scalars(
        select(Notification).where(
            Notification.user_id == patient_user.id,
            Notification.type == NotificationType.appointment_booked,
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].payload["booked_by"] == world.clinician_a.email
    assert rows[0].subject_type == "appointment"


def test_booking_for_an_unlinked_patient_writes_no_notification(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)
    slot = make_slot(client, world)

    client.post(
        "/appointments",
        json={"patient_id": patient["id"], "slot_id": slot["id"]},
        headers=auth(world.clinician_a),
    )

    assert db.scalars(select(Notification)).all() == []


def test_practitioner_cancel_notifies_the_patient(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)
    patient_user = link_user(
        client, db, world, patient["id"], "pat.one@example.co.za"
    )
    slot = make_slot(client, world)
    appt = client.post(
        "/appointments",
        json={"patient_id": patient["id"], "slot_id": slot["id"]},
        headers=auth(world.clinician_a),
    ).json()

    client.post(
        f"/appointments/{appt['id']}/cancel",
        json={"reason": "Clinician off sick"},
        headers=auth(world.clinician_a),
    )

    rows = db.scalars(
        select(Notification).where(
            Notification.user_id == patient_user.id,
            Notification.type == NotificationType.appointment_cancelled,
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].payload["reason"] == "Clinician off sick"


def test_flagged_prom_notifies_the_care_team(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)
    # put clinician_a on the care team
    client.post(
        f"/patients/{patient['id']}/assignments",
        json={"user_id": world.clinician_a.id},
        headers=auth(world.clinician_a),
    )

    client.post(
        f"/patients/{patient['id']}/proms",
        json={
            "instrument": "pain_residual_limb",
            "responses": {"score": 9},
        },
        headers=auth(world.clinician_a),
    )

    rows = db.scalars(
        select(Notification).where(
            Notification.type == NotificationType.prom_flagged
        )
    ).all()
    assert [r.user_id for r in rows] == [world.clinician_a.id]
    assert rows[0].payload["score"] == 9


def test_unflagged_prom_writes_no_notification(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)
    client.post(
        f"/patients/{patient['id']}/assignments",
        json={"user_id": world.clinician_a.id},
        headers=auth(world.clinician_a),
    )
    client.post(
        f"/patients/{patient['id']}/proms",
        json={"instrument": "pain_residual_limb", "responses": {"score": 2}},
        headers=auth(world.clinician_a),
    )
    assert (
        db.scalars(
            select(Notification).where(
                Notification.type == NotificationType.prom_flagged
            )
        ).all()
        == []
    )


def test_assignment_notifies_the_assigned_clinician(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)

    client.post(
        f"/patients/{patient['id']}/assignments",
        json={"user_id": world.prosthetist_a.id},
        headers=auth(world.clinician_a),
    )

    rows = db.scalars(
        select(Notification).where(
            Notification.type == NotificationType.patient_reassigned
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].user_id == world.prosthetist_a.id
    assert rows[0].payload["patient_id"] == patient["id"]


def test_coverage_decision_notifies_the_patient(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)
    patient_user = link_user(
        client, db, world, patient["id"], "pat.one@example.co.za"
    )
    client.post(
        f"/patients/{patient['id']}/medical-aid",
        json={
            "scheme_name": "Discovery Health",
            "plan_option": "Classic",
            "membership_number": "DH-1",
        },
        headers=auth(world.clinician_a),
    )
    slot = make_slot(client, world)
    client.post(
        "/appointments",
        json={"patient_id": patient["id"], "slot_id": slot["id"]},
        headers=auth(world.clinician_a),
    )

    rev = crud.create_user(
        db,
        email="rev@medscheme.test",
        password="pw",
        role=UserRole.medical_aid_reviewer,
    )
    rev.scheme_name = "Discovery Health"
    db.commit()
    coverage_id = client.get("/review/coverage", headers=auth(rev)).json()[0]["id"]
    client.post(
        f"/review/coverage/{coverage_id}/approve",
        json={"authorization_number": "AUTH-9"},
        headers=auth(rev),
    )

    rows = db.scalars(
        select(Notification).where(
            Notification.user_id == patient_user.id,
            Notification.type == NotificationType.coverage_decided,
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].payload["status"] == "approved"


# --- appointment reminders (time-based, R-42) -------------------------


def _slot_at(client, world: World, start: datetime) -> dict:
    return make_slot(
        client,
        world,
        start_time=start.isoformat(),
        end_time=(start + timedelta(minutes=30)).isoformat(),
    )


def _book(client, world: World, patient_id: int, slot_id: int) -> dict:
    resp = client.post(
        "/appointments",
        json={"patient_id": patient_id, "slot_id": slot_id},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 201
    return resp.json()


def _reminders(db: Session, user_id: int) -> list[Notification]:
    return list(
        db.scalars(
            select(Notification).where(
                Notification.user_id == user_id,
                Notification.type == NotificationType.appointment_reminder,
            )
        ).all()
    )


def test_booking_schedules_a_reminder_24h_before(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)
    patient_user = link_user(
        client, db, world, patient["id"], "pat.one@example.co.za"
    )
    start = datetime.now().replace(microsecond=0) + timedelta(days=3)
    slot = _slot_at(client, world, start)
    _book(client, world, patient["id"], slot["id"])

    (reminder,) = _reminders(db, patient_user.id)
    assert reminder.deliver_at == start - timedelta(hours=24)
    assert reminder.subject_type == "appointment"
    # future deliver_at -> not shown in the inbox yet
    body = client.get("/notifications", headers=auth(patient_user)).json()
    assert all(
        item["type"] != "appointment_reminder" for item in body["items"]
    )


def test_no_reminder_for_a_past_appointment(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)
    patient_user = link_user(
        client, db, world, patient["id"], "pat.one@example.co.za"
    )
    slot = _slot_at(
        client, world, datetime.now().replace(microsecond=0) - timedelta(days=1)
    )
    _book(client, world, patient["id"], slot["id"])
    assert _reminders(db, patient_user.id) == []


def test_cancelling_supersedes_a_pending_reminder(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)
    patient_user = link_user(
        client, db, world, patient["id"], "pat.one@example.co.za"
    )
    start = datetime.now().replace(microsecond=0) + timedelta(days=3)
    slot = _slot_at(client, world, start)
    appt = _book(client, world, patient["id"], slot["id"])
    assert len(_reminders(db, patient_user.id)) == 1

    client.post(
        f"/appointments/{appt['id']}/cancel",
        json={"reason": "Clinic closed"},
        headers=auth(world.clinician_a),
    )
    assert _reminders(db, patient_user.id) == []


def test_rescheduling_moves_the_reminder_to_the_new_time(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)
    patient_user = link_user(
        client, db, world, patient["id"], "pat.one@example.co.za"
    )
    first_start = datetime.now().replace(microsecond=0) + timedelta(days=3)
    second_start = first_start + timedelta(days=2)
    first = _slot_at(client, world, first_start)
    second = _slot_at(client, world, second_start)
    appt = _book(client, world, patient["id"], first["id"])

    client.post(
        f"/appointments/{appt['id']}/reschedule",
        json={"new_slot_id": second["id"]},
        headers=auth(world.clinician_a),
    )

    reminders = _reminders(db, patient_user.id)
    assert len(reminders) == 1
    assert reminders[0].deliver_at == second_start - timedelta(hours=24)


def test_staff_reschedule_notifies_the_patient(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)
    patient_user = link_user(
        client, db, world, patient["id"], "pat.one@example.co.za"
    )
    first = make_slot(client, world)
    second = make_slot(
        client,
        world,
        start_time=(TOMORROW_9 + timedelta(days=1)).isoformat(),
        end_time=(TOMORROW_9 + timedelta(days=1, minutes=30)).isoformat(),
    )
    appt = client.post(
        "/appointments",
        json={"patient_id": patient["id"], "slot_id": first["id"]},
        headers=auth(world.clinician_a),
    ).json()

    client.post(
        f"/appointments/{appt['id']}/reschedule",
        json={"new_slot_id": second["id"]},
        headers=auth(world.clinician_a),
    )

    rows = db.scalars(
        select(Notification).where(
            Notification.user_id == patient_user.id,
            Notification.type == NotificationType.appointment_rescheduled,
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].payload["appointment_id"] != appt["id"]
