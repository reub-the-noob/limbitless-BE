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


def iso(dt: datetime) -> str:
    return dt.isoformat()


TOMORROW_9 = datetime.now().replace(
    hour=9, minute=0, second=0, microsecond=0
) + timedelta(days=1)


def slot_body(**overrides) -> dict:
    body = {
        "start_time": iso(TOMORROW_9),
        "end_time": iso(TOMORROW_9 + timedelta(minutes=30)),
        "appointment_type": "review",
    }
    body.update(overrides)
    return body


def create(client, world: World, user: User | None = None, **overrides):
    return client.post(
        "/availability",
        json=slot_body(**overrides),
        headers=auth(user or world.clinician_a),
    )


def test_practitioner_publishes_a_slot(client, world: World) -> None:
    resp = create(client, world)
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "open"
    assert body["practitioner_id"] == world.clinician_a.id
    assert body["practice_id"] == world.practice_a.id


def test_overlapping_slots_for_the_same_practitioner_conflict(
    client, world: World
) -> None:
    assert create(client, world).status_code == 201
    overlapping = create(
        client,
        world,
        start_time=iso(TOMORROW_9 + timedelta(minutes=15)),
        end_time=iso(TOMORROW_9 + timedelta(minutes=45)),
    )
    assert overlapping.status_code == 409


def test_two_practitioners_can_have_the_same_time(client, world: World) -> None:
    assert create(client, world, user=world.clinician_a).status_code == 201
    assert create(client, world, user=world.prosthetist_a).status_code == 201


def test_end_before_start_is_rejected(client, world: World) -> None:
    resp = create(
        client,
        world,
        start_time=iso(TOMORROW_9),
        end_time=iso(TOMORROW_9 - timedelta(minutes=30)),
    )
    assert resp.status_code == 422


def test_list_is_practice_scoped(client, world: World) -> None:
    create(client, world, user=world.clinician_a)
    create(client, world, user=world.clinician_b)
    rows = client.get(
        "/availability", headers=auth(world.admin_a)
    ).json()
    assert [r["practitioner_id"] for r in rows] == [world.clinician_a.id]


def test_only_the_owning_practitioner_can_edit_their_slot(
    client, world: World
) -> None:
    slot = create(client, world, user=world.clinician_a).json()
    resp = client.patch(
        f"/availability/{slot['id']}",
        json={"status": "blocked"},
        headers=auth(world.prosthetist_a),
    )
    assert resp.status_code == 403


def test_block_a_slot(client, world: World) -> None:
    slot = create(client, world).json()
    resp = client.patch(
        f"/availability/{slot['id']}",
        json={"status": "blocked"},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "blocked"


def test_read_only_role_cannot_publish(client, world: World) -> None:
    assert create(client, world, user=world.admin_a).status_code == 403


def test_requires_authentication(client) -> None:
    assert client.get("/availability").status_code == 401
