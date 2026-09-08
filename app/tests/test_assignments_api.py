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


def assign(client, world: World, patient_id: int, user: User, **body) -> object:
    return client.post(
        f"/patients/{patient_id}/assignments",
        json={"user_id": user.id, **body},
        headers=auth(world.clinician_a),
    )


def audit_rows(db: Session, action: AuditAction) -> list[AuditLogEntry]:
    return list(
        db.scalars(
            select(AuditLogEntry).where(
                AuditLogEntry.action == action,
                AuditLogEntry.entity_type == "patient_assignment",
            )
        )
    )


def test_assign_a_clinician(client, db: Session, world: World) -> None:
    patient = make_patient(client, world)

    resp = assign(client, world, patient["id"], world.prosthetist_a)

    assert resp.status_code == 201
    body = resp.json()
    assert body["role"] == "prosthetist"
    assert body["user_email"] == world.prosthetist_a.email
    assert body["end_date"] is None

    (entry,) = audit_rows(db, AuditAction.create)
    assert entry.entity_id == body["id"]


def test_assigning_a_non_clinician_is_rejected(client, world: World) -> None:
    patient = make_patient(client, world)
    resp = assign(client, world, patient["id"], world.admin_a)
    assert resp.status_code == 400


def test_assigning_a_user_from_another_practice_is_rejected(
    client, world: World
) -> None:
    patient = make_patient(client, world)
    resp = assign(client, world, patient["id"], world.clinician_b)
    assert resp.status_code == 400


def test_duplicate_current_assignment_conflicts(client, world: World) -> None:
    patient = make_patient(client, world)
    assert assign(client, world, patient["id"], world.clinician_a).status_code == 201
    assert assign(client, world, patient["id"], world.clinician_a).status_code == 409


def test_list_returns_history_newest_first(client, world: World) -> None:
    patient = make_patient(client, world)
    first = assign(client, world, patient["id"], world.clinician_a).json()
    client.post(
        f"/patients/{patient['id']}/assignments/{first['id']}/end",
        json={},
        headers=auth(world.clinician_a),
    )
    assign(client, world, patient["id"], world.prosthetist_a)

    rows = client.get(
        f"/patients/{patient['id']}/assignments", headers=auth(world.clinician_a)
    ).json()
    # same start_date (today), so the newer row (higher id) comes first
    assert [r["role"] for r in rows] == ["prosthetist", "clinician"]
    assert rows[1]["end_date"] is not None

    active = client.get(
        f"/patients/{patient['id']}/assignments",
        params={"active": "true"},
        headers=auth(world.clinician_a),
    ).json()
    assert [r["role"] for r in active] == ["prosthetist"]


def test_ending_sets_end_date_and_is_idempotent(client, world: World) -> None:
    patient = make_patient(client, world)
    created = assign(client, world, patient["id"], world.clinician_a).json()
    url = f"/patients/{patient['id']}/assignments/{created['id']}/end"

    first = client.post(url, json={}, headers=auth(world.clinician_a)).json()
    assert first["end_date"] is not None

    second = client.post(
        url, json={"end_date": "2020-01-01"}, headers=auth(world.clinician_a)
    ).json()
    assert second["end_date"] == first["end_date"]


def test_ending_with_an_explicit_date(client, world: World) -> None:
    patient = make_patient(client, world)
    created = assign(client, world, patient["id"], world.clinician_a).json()

    ended = client.post(
        f"/patients/{patient['id']}/assignments/{created['id']}/end",
        json={"end_date": "2026-01-15"},
        headers=auth(world.clinician_a),
    ).json()
    assert ended["end_date"] == "2026-01-15"


def test_reassign_frees_the_slot(client, world: World) -> None:
    patient = make_patient(client, world)
    first = assign(client, world, patient["id"], world.clinician_a).json()
    client.post(
        f"/patients/{patient['id']}/assignments/{first['id']}/end",
        json={},
        headers=auth(world.clinician_a),
    )
    # the same user can now be assigned again
    assert assign(client, world, patient["id"], world.clinician_a).status_code == 201


def test_cross_practice_patient_assignments_are_not_found(
    client, world: World
) -> None:
    patient = make_patient(client, world, user=world.clinician_a)
    headers_b = auth(world.clinician_b)

    assert (
        client.get(
            f"/patients/{patient['id']}/assignments", headers=headers_b
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/patients/{patient['id']}/assignments",
            json={"user_id": world.clinician_b.id},
            headers=headers_b,
        ).status_code
        == 404
    )


def test_practice_admin_reads_but_cannot_assign(client, world: World) -> None:
    patient = make_patient(client, world)

    assert (
        client.get(
            f"/patients/{patient['id']}/assignments", headers=auth(world.admin_a)
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/patients/{patient['id']}/assignments",
            json={"user_id": world.clinician_a.id},
            headers=auth(world.admin_a),
        ).status_code
        == 403
    )


def test_ending_an_unknown_assignment_is_404(client, world: World) -> None:
    patient = make_patient(client, world)
    resp = client.post(
        f"/patients/{patient['id']}/assignments/9999/end",
        json={},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 404


def test_filter_patients_by_assigned_to(client, world: World) -> None:
    p1 = make_patient(client, world, national_id="1")
    make_patient(client, world, national_id="2")
    created = assign(client, world, p1["id"], world.clinician_a).json()

    listed = client.get(
        "/patients",
        params={"assigned_to": world.clinician_a.id},
        headers=auth(world.clinician_a),
    ).json()
    assert [item["id"] for item in listed["items"]] == [p1["id"]]

    client.post(
        f"/patients/{p1['id']}/assignments/{created['id']}/end",
        json={},
        headers=auth(world.clinician_a),
    )
    after = client.get(
        "/patients",
        params={"assigned_to": world.clinician_a.id},
        headers=auth(world.clinician_a),
    ).json()
    assert after["total"] == 0
