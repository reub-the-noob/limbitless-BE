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


def make_patient(client, world: World, **overrides) -> dict:
    body = {
        "first_name": "Ann",
        "last_name": "Bell",
        "date_of_birth": "1990-01-01",
        "national_id": "9001010000001",
    }
    body.update(overrides)
    resp = client.post("/patients", json=body, headers=auth(world.clinician_a))
    assert resp.status_code == 201
    return resp.json()


def involvement_body(**overrides) -> dict:
    body = {
        "kind": "amputation",
        "region": "lower_limb_left",
        "level": "transtibial",
        "causes": ["trauma"],
    }
    body.update(overrides)
    return body


def audit(db: Session, action: AuditAction) -> list[AuditLogEntry]:
    return list(
        db.scalars(
            select(AuditLogEntry).where(
                AuditLogEntry.action == action,
                AuditLogEntry.entity_type == "limb_involvement",
            )
        )
    )


def test_create_and_read_an_involvement(client, db: Session, world: World) -> None:
    patient = make_patient(client, world)

    resp = client.post(
        f"/patients/{patient['id']}/involvements",
        json=involvement_body(),
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "amputation"
    assert body["region"] == "lower_limb_left"
    assert body["status"] == "active"
    assert body["patient_id"] == patient["id"]
    assert [r.entity_id for r in audit(db, AuditAction.create)] == [body["id"]]

    detail = client.get(
        f"/patients/{patient['id']}/involvements/{body['id']}",
        headers=auth(world.clinician_a),
    ).json()
    assert detail["devices"] == []


def test_orthotic_involvement_needs_no_level_or_cause(client, world: World) -> None:
    patient = make_patient(client, world)
    resp = client.post(
        f"/patients/{patient['id']}/involvements",
        json={"kind": "orthotic_need", "region": "spine"},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["level"] is None and body["causes"] == []


def test_involvement_records_multiple_causes(client, world: World) -> None:
    patient = make_patient(client, world)

    created = client.post(
        f"/patients/{patient['id']}/involvements",
        json=involvement_body(causes=["dysvascular", "infection"]),
        headers=auth(world.clinician_a),
    )
    assert created.status_code == 201
    iid = created.json()["id"]
    assert created.json()["causes"] == ["dysvascular", "infection"]

    # persists on the read path
    got = client.get(
        f"/patients/{patient['id']}/involvements/{iid}",
        headers=auth(world.clinician_a),
    ).json()
    assert got["causes"] == ["dysvascular", "infection"]

    # PATCH replaces the whole set; duplicates are collapsed
    patched = client.patch(
        f"/patients/{patient['id']}/involvements/{iid}",
        json={"causes": ["trauma", "trauma", "infection"]},
        headers=auth(world.clinician_a),
    )
    assert patched.status_code == 200
    assert patched.json()["causes"] == ["trauma", "infection"]

    # an empty list clears them
    cleared = client.patch(
        f"/patients/{patient['id']}/involvements/{iid}",
        json={"causes": []},
        headers=auth(world.clinician_a),
    )
    assert cleared.json()["causes"] == []


def test_list_returns_every_involvement(client, world: World) -> None:
    patient = make_patient(client, world)
    for region in ("lower_limb_left", "lower_limb_right"):
        client.post(
            f"/patients/{patient['id']}/involvements",
            json=involvement_body(region=region),
            headers=auth(world.clinician_a),
        )
    rows = client.get(
        f"/patients/{patient['id']}/involvements", headers=auth(world.clinician_a)
    ).json()
    assert {r["region"] for r in rows} == {"lower_limb_left", "lower_limb_right"}


def test_update_can_resolve_an_involvement(client, db: Session, world: World) -> None:
    patient = make_patient(client, world)
    created = client.post(
        f"/patients/{patient['id']}/involvements",
        json=involvement_body(),
        headers=auth(world.clinician_a),
    ).json()

    resp = client.patch(
        f"/patients/{patient['id']}/involvements/{created['id']}",
        json={"status": "resolved", "notes": "healed"},
        headers=auth(world.prosthetist_a),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"
    assert any(r.entity_id == created["id"] for r in audit(db, AuditAction.update))


def test_roles_and_scoping(client, world: World) -> None:
    patient = make_patient(client, world)
    base = f"/patients/{patient['id']}/involvements"

    # practice admin reads but does not write
    assert client.get(base, headers=auth(world.admin_a)).status_code == 200
    assert (
        client.post(
            base, json=involvement_body(), headers=auth(world.admin_a)
        ).status_code
        == 403
    )

    # another practice cannot see this patient's involvements
    assert client.get(base, headers=auth(world.clinician_b)).status_code == 404
