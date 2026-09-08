from sqlalchemy import select
from sqlalchemy.orm import Session

from app import security
from app.models import AuditAction, AuditLogEntry, User
from app.tests.conftest import World


def auth(user: User) -> dict[str, str]:
    token = security.create_access_token(
        user.id,
        role=user.role.value,
        practice_id=user.practice_id,
        site_id=user.site_id,
    )
    return {"Authorization": f"Bearer {token}"}


def make_patient(client, world: World, user: User | None = None, **overrides) -> dict:
    body = {
        "first_name": "Ann",
        "last_name": "Bell",
        "date_of_birth": "1990-01-01",
        "national_id": "9001010000001",
    }
    body.update(overrides)
    resp = client.post(
        "/patients", json=body, headers=auth(user or world.clinician_a)
    )
    assert resp.status_code == 201
    return resp.json()


def make_involvement(
    client, world: World, patient_id: int, user: User | None = None, **overrides
) -> dict:
    body = {
        "kind": "amputation",
        "region": "lower_limb_left",
        "level": "transtibial",
        "cause": "trauma",
    }
    body.update(overrides)
    resp = client.post(
        f"/patients/{patient_id}/involvements",
        json=body,
        headers=auth(user or world.clinician_a),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def make_patient_with_involvement(client, world: World, **patient) -> tuple[dict, dict]:
    p = make_patient(client, world, **patient)
    i = make_involvement(client, world, p["id"])
    return p, i


def device_body(**overrides) -> dict:
    body = {"device_type": "body_powered"}
    body.update(overrides)
    return body


def create_device(client, world: World, patient_id: int, involvement_id: int, **body):
    return client.post(
        f"/patients/{patient_id}/involvements/{involvement_id}/devices",
        json=device_body(**body),
        headers=auth(world.clinician_a),
    )


def audit_rows(db: Session, action: AuditAction) -> list[AuditLogEntry]:
    return list(
        db.scalars(
            select(AuditLogEntry).where(
                AuditLogEntry.action == action,
                AuditLogEntry.entity_type == "device",
            )
        )
    )


def test_create_device_defaults_to_planned(client, db: Session, world: World) -> None:
    patient, inv = make_patient_with_involvement(client, world)

    resp = create_device(client, world, patient["id"], inv["id"])

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "planned"
    assert body["involvement_id"] == inv["id"]
    assert body["replaces_device_id"] is None
    assert [r.entity_id for r in audit_rows(db, AuditAction.create)] == [body["id"]]


def test_orthosis_componentry_round_trips(
    client, db: Session, world: World
) -> None:
    patient, inv = make_patient_with_involvement(client, world)

    resp = create_device(
        client,
        world,
        patient["id"],
        inv["id"],
        device_type="orthosis_afo",
        joint_type="Articulated ankle",
        trimline="Posterior leaf spring",
        strap_configuration="3-strap Velcro",
        padding_liner="Plastazote",
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["joint_type"] == "Articulated ankle"
    assert body["trimline"] == "Posterior leaf spring"
    assert body["strap_configuration"] == "3-strap Velcro"
    assert body["padding_liner"] == "Plastazote"
    # the prosthesis fields are simply left null
    assert body["socket_type"] is None

    got = client.get(
        f"/patients/{patient['id']}/involvements/{inv['id']}/devices/{body['id']}",
        headers=auth(world.clinician_a),
    ).json()
    assert got["strap_configuration"] == "3-strap Velcro"


def test_create_requires_a_writing_role(client, world: World) -> None:
    patient, inv = make_patient_with_involvement(client, world)
    base = f"/patients/{patient['id']}/involvements/{inv['id']}/devices"
    assert (
        client.post(base, json=device_body(), headers=auth(world.admin_a)).status_code
        == 403
    )
    assert (
        client.post(
            base, json=device_body(), headers=auth(world.platform_admin)
        ).status_code
        == 403
    )
    assert client.post(base, json=device_body()).status_code == 401


def test_multiple_active_devices_on_one_limb_are_allowed(client, world: World) -> None:
    # status is informational now - an everyday leg + a running blade
    patient, inv = make_patient_with_involvement(client, world)
    assert create_device(
        client, world, patient["id"], inv["id"], status="active"
    ).status_code == 201
    assert create_device(
        client, world, patient["id"], inv["id"], status="active"
    ).status_code == 201


def test_involvement_not_found_for_a_stray_id(client, world: World) -> None:
    patient = make_patient(client, world)
    resp = client.get(
        f"/patients/{patient['id']}/involvements/999/devices",
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 404


def test_list_and_status_filter(client, world: World) -> None:
    patient, inv = make_patient_with_involvement(client, world)
    create_device(client, world, patient["id"], inv["id"], status="planned")
    create_device(client, world, patient["id"], inv["id"], status="active")

    base = f"/patients/{patient['id']}/involvements/{inv['id']}/devices"
    all_rows = client.get(base, headers=auth(world.clinician_a)).json()
    assert len(all_rows) == 2

    active = client.get(
        base, params={"device_status": "active"}, headers=auth(world.clinician_a)
    ).json()
    assert [d["status"] for d in active] == ["active"]


def test_patient_devices_overview_spans_involvements(client, world: World) -> None:
    patient = make_patient(client, world)
    left = make_involvement(client, world, patient["id"], region="lower_limb_left")
    right = make_involvement(client, world, patient["id"], region="lower_limb_right")
    create_device(client, world, patient["id"], left["id"])
    create_device(client, world, patient["id"], right["id"])

    overview = client.get(
        f"/patients/{patient['id']}/devices", headers=auth(world.clinician_a)
    ).json()
    assert len(overview) == 2
    assert {d["involvement_id"] for d in overview} == {left["id"], right["id"]}


def test_cross_practice_device_routes_are_not_found(client, world: World) -> None:
    patient, inv = make_patient_with_involvement(client, world)
    device = create_device(client, world, patient["id"], inv["id"]).json()
    headers_b = auth(world.clinician_b)
    base = f"/patients/{patient['id']}/involvements/{inv['id']}/devices"

    assert client.get(base, headers=headers_b).status_code == 404
    assert client.get(f"{base}/{device['id']}", headers=headers_b).status_code == 404
    assert (
        client.patch(
            f"{base}/{device['id']}", json={"model": "x"}, headers=headers_b
        ).status_code
        == 404
    )


def test_device_from_another_involvement_is_not_found(client, world: World) -> None:
    patient = make_patient(client, world)
    i1 = make_involvement(client, world, patient["id"], region="lower_limb_left")
    i2 = make_involvement(client, world, patient["id"], region="lower_limb_right")
    device = create_device(client, world, patient["id"], i1["id"]).json()

    resp = client.get(
        f"/patients/{patient['id']}/involvements/{i2['id']}/devices/{device['id']}",
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 404


def test_update_device_and_audit(client, db: Session, world: World) -> None:
    patient, inv = make_patient_with_involvement(client, world)
    device = create_device(client, world, patient["id"], inv["id"]).json()

    resp = client.patch(
        f"/patients/{patient['id']}/involvements/{inv['id']}/devices/{device['id']}",
        json={"manufacturer": "Ottobock", "status": "in_fitting"},
        headers=auth(world.prosthetist_a),
    )
    assert resp.status_code == 200
    assert resp.json()["manufacturer"] == "Ottobock"
    assert any(r.entity_id == device["id"] for r in audit_rows(db, AuditAction.update))


def test_replace_device_links_and_supersedes(client, db: Session, world: World) -> None:
    patient, inv = make_patient_with_involvement(client, world)
    old = create_device(
        client, world, patient["id"], inv["id"], status="active"
    ).json()

    resp = client.post(
        f"/patients/{patient['id']}/involvements/{inv['id']}/devices/{old['id']}/replace",
        json=device_body(status="active", manufacturer="Ossur"),
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 201
    new = resp.json()
    assert new["replaces_device_id"] == old["id"]
    assert new["manufacturer"] == "Ossur"
    assert new["involvement_id"] == inv["id"]

    old_again = client.get(
        f"/patients/{patient['id']}/involvements/{inv['id']}/devices/{old['id']}",
        headers=auth(world.clinician_a),
    ).json()
    assert old_again["status"] == "replaced"

    creates = {r.entity_id for r in audit_rows(db, AuditAction.create)}
    updates = {r.entity_id for r in audit_rows(db, AuditAction.update)}
    assert new["id"] in creates and old["id"] in updates


def test_practice_admin_can_read_devices(client, world: World) -> None:
    patient, inv = make_patient_with_involvement(client, world)
    create_device(client, world, patient["id"], inv["id"])
    assert (
        client.get(
            f"/patients/{patient['id']}/involvements/{inv['id']}/devices",
            headers=auth(world.admin_a),
        ).status_code
        == 200
    )


def test_filter_patients_by_device_type(client, world: World) -> None:
    p1, inv1 = make_patient_with_involvement(client, world, national_id="1")
    make_patient(client, world, national_id="2")
    create_device(client, world, p1["id"], inv1["id"], device_type="myoelectric")

    listed = client.get(
        "/patients",
        params={"device_type": "myoelectric"},
        headers=auth(world.clinician_a),
    ).json()
    assert [item["id"] for item in listed["items"]] == [p1["id"]]


# --- body-map position (R-45) --------------------------------------


def _patch(client, world: World, patient_id, inv_id, device_id, body):
    return client.patch(
        f"/patients/{patient_id}/involvements/{inv_id}/devices/{device_id}",
        json=body,
        headers=auth(world.clinician_a),
    )


def test_device_map_position_set_read_and_cleared(client, world: World) -> None:
    p, inv = make_patient_with_involvement(client, world)
    device = create_device(client, world, p["id"], inv["id"]).json()
    assert device["map_x"] is None and device["map_y"] is None

    placed = _patch(
        client, world, p["id"], inv["id"], device["id"], {"map_x": 0.55, "map_y": 0.9}
    )
    assert placed.status_code == 200
    assert placed.json()["map_x"] == 0.55 and placed.json()["map_y"] == 0.9

    # shows up on the patient-wide overview the body-map reads
    overview = client.get(
        f"/patients/{p['id']}/devices", headers=auth(world.clinician_a)
    ).json()
    assert overview[0]["map_x"] == 0.55

    cleared = _patch(
        client, world, p["id"], inv["id"], device["id"], {"map_x": None, "map_y": None}
    )
    assert cleared.status_code == 200
    assert cleared.json()["map_x"] is None and cleared.json()["map_y"] is None


def test_device_map_position_rejects_half_a_pair(client, world: World) -> None:
    p, inv = make_patient_with_involvement(client, world)
    device = create_device(client, world, p["id"], inv["id"]).json()

    resp = _patch(client, world, p["id"], inv["id"], device["id"], {"map_x": 0.5})
    assert resp.status_code == 422


def test_device_map_position_rejects_out_of_range(client, world: World) -> None:
    p, inv = make_patient_with_involvement(client, world)
    device = create_device(client, world, p["id"], inv["id"]).json()

    resp = _patch(
        client, world, p["id"], inv["id"], device["id"], {"map_x": 1.4, "map_y": 0.2}
    )
    assert resp.status_code == 422


def test_device_can_be_created_with_a_position(client, world: World) -> None:
    p, inv = make_patient_with_involvement(client, world)
    resp = create_device(
        client, world, p["id"], inv["id"], map_x=0.5, map_y=0.34
    )
    assert resp.status_code == 201
    assert resp.json()["map_x"] == 0.5 and resp.json()["map_y"] == 0.34
