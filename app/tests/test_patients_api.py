import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import security
from app.models import AuditAction, AuditLogEntry, Site, SiteType, User
from app.tests.conftest import World


def auth(user: User) -> dict[str, str]:
    token = security.create_access_token(
        user.id,
        role=user.role.value,
        practice_id=user.practice_id,
        site_id=user.site_id,
    )
    return {"Authorization": f"Bearer {token}"}


def payload(**overrides) -> dict[str, object]:
    body: dict[str, object] = {
        "first_name": "Thandeka",
        "last_name": "Mokoena",
        "date_of_birth": "1985-04-12",
        "national_id": "8504125800086",
    }
    body.update(overrides)
    return body


def audit_rows(db: Session, action: AuditAction | None = None) -> list[AuditLogEntry]:
    stmt = select(AuditLogEntry)
    if action is not None:
        stmt = stmt.where(AuditLogEntry.action == action)
    return list(db.scalars(stmt))


# --- create -------------------------------------------------------------------


def test_clinician_creates_a_patient(client, db: Session, world: World) -> None:
    resp = client.post("/patients", json=payload(), headers=auth(world.clinician_a))

    assert resp.status_code == 201
    body = resp.json()
    assert body["practice_id"] == world.practice_a.id
    assert body["is_active"] is True

    (entry,) = audit_rows(db, AuditAction.create)
    assert entry.entity_type == "patient"
    assert entry.entity_id == body["id"]
    assert entry.actor_id == world.clinician_a.id


def test_create_requires_an_identity_key(client, world: World) -> None:
    resp = client.post(
        "/patients",
        json=payload(national_id=None, passport_number=None),
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 422


def test_create_rejects_a_site_from_another_practice(
    client, db: Session, world: World
) -> None:
    other_site = Site(
        name="B Rooms", type=SiteType.location, practice_id=world.practice_b.id
    )
    db.add(other_site)
    db.commit()

    resp = client.post(
        "/patients",
        json=payload(site_id=other_site.id),
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 400


def test_practice_admin_cannot_create(client, world: World) -> None:
    resp = client.post("/patients", json=payload(), headers=auth(world.admin_a))
    assert resp.status_code == 403


def test_platform_admin_cannot_create(client, world: World) -> None:
    resp = client.post(
        "/patients", json=payload(), headers=auth(world.platform_admin)
    )
    assert resp.status_code == 403


def test_create_requires_authentication(client, world: World) -> None:
    assert client.post("/patients", json=payload()).status_code == 401


# --- read / list ------------------------------------------------------------


def _create(client, world: World, user: User | None = None, **overrides) -> dict:
    user = user or world.clinician_a
    resp = client.post(
        "/patients", json=payload(**overrides), headers=auth(user)
    )
    assert resp.status_code == 201
    return resp.json()


def test_read_own_practice_patient_is_audited(
    client, db: Session, world: World
) -> None:
    created = _create(client, world)

    resp = client.get(
        f"/patients/{created['id']}", headers=auth(world.clinician_a)
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]

    reads = audit_rows(db, AuditAction.read)
    assert any(r.entity_id == created["id"] for r in reads)


def test_cross_practice_patient_is_invisible(client, world: World) -> None:
    created = _create(client, world, user=world.clinician_a)
    pid = created["id"]
    headers_b = auth(world.clinician_b)

    assert client.get(f"/patients/{pid}", headers=headers_b).status_code == 404
    assert (
        client.patch(
            f"/patients/{pid}", json={"contact_phone": "x"}, headers=headers_b
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/patients/{pid}/deactivate", headers=headers_b
        ).status_code
        == 404
    )


def test_list_is_scoped_to_the_callers_practice(client, world: World) -> None:
    _create(client, world, user=world.clinician_a, national_id="1")
    _create(client, world, user=world.clinician_a, national_id="2")
    _create(client, world, user=world.clinician_b, national_id="1")

    resp = client.get("/patients", headers=auth(world.clinician_a))
    body = resp.json()
    assert body["total"] == 2
    assert all(item["practice_id"] == world.practice_a.id for item in body["items"])


def test_list_name_search(client, world: World) -> None:
    _create(client, world, national_id="1", last_name="Mokoena")
    _create(client, world, national_id="2", last_name="Naidoo")

    resp = client.get("/patients", params={"q": "nai"}, headers=auth(world.clinician_a))
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["last_name"] == "Naidoo"


def test_list_pagination(client, world: World) -> None:
    for i in range(3):
        _create(client, world, national_id=str(i), last_name=f"P{i}")

    first = client.get(
        "/patients", params={"limit": 2, "offset": 0}, headers=auth(world.clinician_a)
    ).json()
    assert (first["total"], len(first["items"])) == (3, 2)

    second = client.get(
        "/patients", params={"limit": 2, "offset": 2}, headers=auth(world.clinician_a)
    ).json()
    assert len(second["items"]) == 1


def test_practice_admin_can_read(client, world: World) -> None:
    _create(client, world)
    assert client.get("/patients", headers=auth(world.admin_a)).status_code == 200


# --- update / (de)activate -------------------------------------------------


def test_update_changes_fields_and_audits(
    client, db: Session, world: World
) -> None:
    created = _create(client, world)

    resp = client.patch(
        f"/patients/{created['id']}",
        json={"contact_phone": "+27 21 000 0000", "medical_history": "PVD"},
        headers=auth(world.prosthetist_a),
    )
    assert resp.status_code == 200
    assert resp.json()["contact_phone"] == "+27 21 000 0000"

    updates = audit_rows(db, AuditAction.update)
    assert any(
        r.entity_id == created["id"] and r.actor_id == world.prosthetist_a.id
        for r in updates
    )


def test_update_to_a_duplicate_national_id_conflicts(client, world: World) -> None:
    _create(client, world, national_id="AAA")
    p2 = _create(client, world, national_id="BBB")

    resp = client.patch(
        f"/patients/{p2['id']}",
        json={"national_id": "AAA"},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 409


def test_deactivate_and_reactivate(client, world: World) -> None:
    created = _create(client, world)
    pid = created["id"]

    assert (
        client.post(
            f"/patients/{pid}/deactivate", headers=auth(world.clinician_a)
        ).json()["is_active"]
        is False
    )

    default_list = client.get("/patients", headers=auth(world.clinician_a)).json()
    assert all(item["id"] != pid for item in default_list["items"])

    inactive_list = client.get(
        "/patients", params={"active": "false"}, headers=auth(world.clinician_a)
    ).json()
    assert any(item["id"] == pid for item in inactive_list["items"])

    assert (
        client.post(
            f"/patients/{pid}/reactivate", headers=auth(world.clinician_a)
        ).json()["is_active"]
        is True
    )
