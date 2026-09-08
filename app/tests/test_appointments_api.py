from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app import security
from app.models import User
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


def test_book_appointment_marks_the_slot_booked(
    client, world: World
) -> None:
    patient = make_patient(client, world)
    slot = make_slot(client, world)

    resp = client.post(
        "/appointments",
        json={"patient_id": patient["id"], "slot_id": slot["id"]},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "booked"
    assert body["patient_id"] == patient["id"]
    assert body["patient_name"] == "Pat One"
    assert body["practitioner_id"] == world.clinician_a.id
    assert body["scheduled_start"] == slot["start_time"]

    slot_after = client.get(
        f"/availability/{slot['id']}", headers=auth(world.clinician_a)
    ).json()
    assert slot_after["status"] == "booked"


def test_cannot_double_book_a_slot(client, world: World) -> None:
    patient = make_patient(client, world)
    other = make_patient(client, world, national_id="9001010000002")
    slot = make_slot(client, world)

    first = client.post(
        "/appointments",
        json={"patient_id": patient["id"], "slot_id": slot["id"]},
        headers=auth(world.clinician_a),
    )
    assert first.status_code == 201

    second = client.post(
        "/appointments",
        json={"patient_id": other["id"], "slot_id": slot["id"]},
        headers=auth(world.clinician_a),
    )
    assert second.status_code == 409


def test_list_is_practice_scoped(client, world: World) -> None:
    patient = make_patient(client, world)
    slot = make_slot(client, world)
    client.post(
        "/appointments",
        json={"patient_id": patient["id"], "slot_id": slot["id"]},
        headers=auth(world.clinician_a),
    )

    rows = client.get(
        "/appointments", headers=auth(world.admin_a)
    ).json()
    assert len(rows) == 1

    other_side = client.get(
        "/appointments", headers=auth(world.clinician_b)
    ).json()
    assert other_side == []


def test_practitioner_cancel_requires_a_reason(client, world: World) -> None:
    patient = make_patient(client, world)
    slot = make_slot(client, world)
    appt = client.post(
        "/appointments",
        json={"patient_id": patient["id"], "slot_id": slot["id"]},
        headers=auth(world.clinician_a),
    ).json()

    empty_reason = client.post(
        f"/appointments/{appt['id']}/cancel",
        json={},
        headers=auth(world.clinician_a),
    )
    assert empty_reason.status_code == 400

    resp = client.post(
        f"/appointments/{appt['id']}/cancel",
        json={"reason": "Practitioner unavailable"},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "cancelled_by_practitioner"
    assert body["cancellation_reason"] == "Practitioner unavailable"
    assert body["cancelled_at"] is not None

    # The slot is released and can be rebooked.
    slot_after = client.get(
        f"/availability/{slot['id']}", headers=auth(world.clinician_a)
    ).json()
    assert slot_after["status"] == "open"


def test_cannot_cancel_an_already_cancelled_appointment(
    client, world: World
) -> None:
    patient = make_patient(client, world)
    slot = make_slot(client, world)
    appt = client.post(
        "/appointments",
        json={"patient_id": patient["id"], "slot_id": slot["id"]},
        headers=auth(world.clinician_a),
    ).json()
    client.post(
        f"/appointments/{appt['id']}/cancel",
        json={"reason": "Practitioner unavailable"},
        headers=auth(world.clinician_a),
    )
    resp = client.post(
        f"/appointments/{appt['id']}/cancel",
        json={"reason": "Again"},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 400


def test_mark_no_show(client, world: World) -> None:
    patient = make_patient(client, world)
    slot = make_slot(client, world)
    appt = client.post(
        "/appointments",
        json={"patient_id": patient["id"], "slot_id": slot["id"]},
        headers=auth(world.clinician_a),
    ).json()

    resp = client.post(
        f"/appointments/{appt['id']}/no-show", headers=auth(world.clinician_a)
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_show"

    # A no-show does not release the slot back to open.
    slot_after = client.get(
        f"/availability/{slot['id']}", headers=auth(world.clinician_a)
    ).json()
    assert slot_after["status"] == "booked"


def test_booking_a_practice_admin_is_forbidden(client, world: World) -> None:
    patient = make_patient(client, world)
    slot = make_slot(client, world)
    resp = client.post(
        "/appointments",
        json={"patient_id": patient["id"], "slot_id": slot["id"]},
        headers=auth(world.admin_a),
    )
    assert resp.status_code == 403


def test_booking_an_other_practice_patient_404s(client, world: World) -> None:
    slot = make_slot(client, world)
    resp = client.post(
        "/appointments",
        json={"patient_id": 999999, "slot_id": slot["id"]},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 404


# --- reschedule --------------------------------------------------------


def test_reschedule_is_one_atomic_action(client, world: World) -> None:
    patient = make_patient(client, world)
    old_slot = make_slot(client, world)
    new_slot = make_slot(
        client,
        world,
        start_time=(TOMORROW_9 + timedelta(days=1)).isoformat(),
        end_time=(TOMORROW_9 + timedelta(days=1, minutes=30)).isoformat(),
    )
    appt = client.post(
        "/appointments",
        json={"patient_id": patient["id"], "slot_id": old_slot["id"]},
        headers=auth(world.clinician_a),
    ).json()

    resp = client.post(
        f"/appointments/{appt['id']}/reschedule",
        json={"new_slot_id": new_slot["id"]},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 200
    body = resp.json()
    previous, new = body["previous"], body["new"]

    assert previous["status"] == "rescheduled"
    assert previous["rescheduled_to_id"] == new["id"]
    assert new["rescheduled_from_id"] == previous["id"]
    assert new["status"] == "booked"
    assert new["scheduled_start"] == new_slot["start_time"]

    old_slot_after = client.get(
        f"/availability/{old_slot['id']}", headers=auth(world.clinician_a)
    ).json()
    assert old_slot_after["status"] == "open"
    new_slot_after = client.get(
        f"/availability/{new_slot['id']}", headers=auth(world.clinician_a)
    ).json()
    assert new_slot_after["status"] == "booked"


def test_cannot_reschedule_into_a_slot_that_is_not_open(
    client, world: World
) -> None:
    patient = make_patient(client, world)
    other = make_patient(client, world, national_id="9001010000003")
    slot_a = make_slot(client, world)
    slot_b = make_slot(
        client,
        world,
        start_time=(TOMORROW_9 + timedelta(days=1)).isoformat(),
        end_time=(TOMORROW_9 + timedelta(days=1, minutes=30)).isoformat(),
    )
    appt_a = client.post(
        "/appointments",
        json={"patient_id": patient["id"], "slot_id": slot_a["id"]},
        headers=auth(world.clinician_a),
    ).json()
    client.post(
        "/appointments",
        json={"patient_id": other["id"], "slot_id": slot_b["id"]},
        headers=auth(world.clinician_a),
    )

    resp = client.post(
        f"/appointments/{appt_a['id']}/reschedule",
        json={"new_slot_id": slot_b["id"]},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 409


def test_cannot_reschedule_an_already_cancelled_appointment(
    client, world: World
) -> None:
    patient = make_patient(client, world)
    slot = make_slot(client, world)
    other_slot = make_slot(
        client,
        world,
        start_time=(TOMORROW_9 + timedelta(days=1)).isoformat(),
        end_time=(TOMORROW_9 + timedelta(days=1, minutes=30)).isoformat(),
    )
    appt = client.post(
        "/appointments",
        json={"patient_id": patient["id"], "slot_id": slot["id"]},
        headers=auth(world.clinician_a),
    ).json()
    client.post(
        f"/appointments/{appt['id']}/cancel",
        json={"reason": "Unavailable"},
        headers=auth(world.clinician_a),
    )

    resp = client.post(
        f"/appointments/{appt['id']}/reschedule",
        json={"new_slot_id": other_slot["id"]},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 400
