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


def membership_body(**overrides) -> dict:
    body = {
        "scheme_name": "Discovery Health",
        "plan_option": "Classic Comprehensive",
        "membership_number": "DH-12345",
    }
    body.update(overrides)
    return body


def base(patient_id: int) -> str:
    return f"/patients/{patient_id}/medical-aid"


def test_create_read_update_delete(client, world: World) -> None:
    patient = make_patient(client, world)

    assert (
        client.get(base(patient["id"]), headers=auth(world.clinician_a)).status_code
        == 404
    )

    created = client.post(
        base(patient["id"]), json=membership_body(), headers=auth(world.clinician_a)
    )
    assert created.status_code == 201
    assert created.json()["scheme_name"] == "Discovery Health"

    assert (
        client.get(base(patient["id"]), headers=auth(world.admin_a)).json()[
            "membership_number"
        ]
        == "DH-12345"
    )

    updated = client.patch(
        base(patient["id"]),
        json={"membership_number": "DH-99999"},
        headers=auth(world.clinician_a),
    )
    assert updated.json()["membership_number"] == "DH-99999"
    assert updated.json()["scheme_name"] == "Discovery Health"

    assert (
        client.delete(base(patient["id"]), headers=auth(world.clinician_a)).status_code
        == 204
    )
    assert (
        client.get(base(patient["id"]), headers=auth(world.clinician_a)).status_code
        == 404
    )


def test_only_one_membership_per_patient(client, world: World) -> None:
    patient = make_patient(client, world)
    client.post(
        base(patient["id"]), json=membership_body(), headers=auth(world.clinician_a)
    )
    resp = client.post(
        base(patient["id"]), json=membership_body(), headers=auth(world.clinician_a)
    )
    assert resp.status_code == 409


def test_practice_administrator_is_read_only(client, world: World) -> None:
    patient = make_patient(client, world)
    assert (
        client.post(
            base(patient["id"]), json=membership_body(), headers=auth(world.admin_a)
        ).status_code
        == 403
    )


def test_scoped_to_practice(client, world: World) -> None:
    patient = make_patient(client, world)
    resp = client.get(base(patient["id"]), headers=auth(world.clinician_b))
    assert resp.status_code == 404
