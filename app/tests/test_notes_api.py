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


def add_note(client, world: World, patient_id: int, body: str = "First review", user=None):
    return client.post(
        f"/patients/{patient_id}/notes",
        json={"body": body},
        headers=auth(user or world.clinician_a),
    )


def note_audit(db: Session, action: AuditAction) -> list[AuditLogEntry]:
    return list(
        db.scalars(
            select(AuditLogEntry).where(
                AuditLogEntry.action == action,
                AuditLogEntry.entity_type == "clinical_note",
            )
        )
    )


# --- notes --------------------------------------------------------------


def test_create_note_records_the_author(client, db: Session, world: World) -> None:
    patient = make_patient(client, world)

    resp = add_note(client, world, patient["id"], "Reviewed socket fit")

    assert resp.status_code == 201
    body = resp.json()
    assert body["author_id"] == world.clinician_a.id
    assert body["body"] == "Reviewed socket fit"
    assert [r.entity_id for r in note_audit(db, AuditAction.create)] == [body["id"]]


def test_empty_note_is_rejected(client, world: World) -> None:
    patient = make_patient(client, world)
    resp = client.post(
        f"/patients/{patient['id']}/notes",
        json={"body": ""},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 422


def test_note_create_needs_a_writing_role(client, world: World) -> None:
    patient = make_patient(client, world)
    assert add_note(client, world, patient["id"], user=world.admin_a).status_code == 403
    assert (
        client.post(f"/patients/{patient['id']}/notes", json={"body": "x"}).status_code
        == 401
    )


def test_notes_list_is_newest_first(client, world: World) -> None:
    patient = make_patient(client, world)
    first = add_note(client, world, patient["id"], "one").json()
    second = add_note(client, world, patient["id"], "two").json()

    rows = client.get(
        f"/patients/{patient['id']}/notes", headers=auth(world.clinician_a)
    ).json()
    assert [r["id"] for r in rows] == [second["id"], first["id"]]


def test_update_note_body_and_audit(client, db: Session, world: World) -> None:
    patient = make_patient(client, world)
    note = add_note(client, world, patient["id"], "draft").json()

    updated = client.patch(
        f"/patients/{patient['id']}/notes/{note['id']}",
        json={"body": "final version"},
        headers=auth(world.prosthetist_a),
    ).json()
    assert updated["body"] == "final version"
    assert any(r.entity_id == note["id"] for r in note_audit(db, AuditAction.update))


def test_practice_admin_can_read_notes(client, world: World) -> None:
    patient = make_patient(client, world)
    add_note(client, world, patient["id"])
    assert (
        client.get(
            f"/patients/{patient['id']}/notes", headers=auth(world.admin_a)
        ).status_code
        == 200
    )


def test_cross_practice_note_routes_are_404(client, world: World) -> None:
    patient = make_patient(client, world, user=world.clinician_a)
    note = add_note(client, world, patient["id"]).json()
    headers_b = auth(world.clinician_b)
    base = f"/patients/{patient['id']}/notes"

    assert client.get(base, headers=headers_b).status_code == 404
    assert client.get(f"{base}/{note['id']}", headers=headers_b).status_code == 404
    assert (
        client.patch(
            f"{base}/{note['id']}", json={"body": "x"}, headers=headers_b
        ).status_code
        == 404
    )


def test_note_from_another_patient_is_404(client, world: World) -> None:
    p1 = make_patient(client, world, national_id="1")
    p2 = make_patient(client, world, national_id="2")
    note = add_note(client, world, p1["id"]).json()

    resp = client.get(
        f"/patients/{p2['id']}/notes/{note['id']}", headers=auth(world.clinician_a)
    )
    assert resp.status_code == 404


# --- timeline ---------------------------------------------------------------


def _seed_timeline(client, world: World, patient_id: int) -> None:
    client.post(
        f"/patients/{patient_id}/milestones",
        json={
            "milestone_type": "gait_functional_training",
            "status": "complete",
            "completed_date": "2027-01-01",
        },
        headers=auth(world.clinician_a),
    )
    client.post(
        f"/patients/{patient_id}/proms",
        json={
            "instrument": "pain_residual_limb",
            "responses": {"score": 8},
            "recorded_at": "2026-06-01T10:00:00",
        },
        headers=auth(world.clinician_a),
    )
    add_note(client, world, patient_id, "Discharge planning discussion")


def test_timeline_merges_and_orders_events(client, db: Session, world: World) -> None:
    patient = make_patient(client, world)
    _seed_timeline(client, world, patient["id"])

    events = client.get(
        f"/patients/{patient['id']}/timeline", headers=auth(world.clinician_a)
    ).json()

    assert [e["kind"] for e in events] == ["milestone", "note", "prom"]
    assert events[0]["milestone"]["milestone_type"] == "gait_functional_training"
    assert events[1]["note"]["body"] == "Discharge planning discussion"
    assert "(flagged)" in events[2]["title"]
    assert events[2]["prom"]["score"] == 8.0

    reads = list(
        db.scalars(
            select(AuditLogEntry).where(
                AuditLogEntry.entity_type == "patient_timeline"
            )
        )
    )
    assert reads and reads[0].entity_id == patient["id"]


def test_timeline_limit(client, world: World) -> None:
    patient = make_patient(client, world)
    _seed_timeline(client, world, patient["id"])

    events = client.get(
        f"/patients/{patient['id']}/timeline",
        params={"limit": 2},
        headers=auth(world.clinician_a),
    ).json()
    assert len(events) == 2


def test_timeline_is_empty_for_a_new_patient(client, world: World) -> None:
    patient = make_patient(client, world)
    events = client.get(
        f"/patients/{patient['id']}/timeline", headers=auth(world.clinician_a)
    ).json()
    assert events == []


def test_timeline_cross_practice_is_404(client, world: World) -> None:
    patient = make_patient(client, world, user=world.clinician_a)
    _seed_timeline(client, world, patient["id"])

    resp = client.get(
        f"/patients/{patient['id']}/timeline", headers=auth(world.clinician_b)
    )
    assert resp.status_code == 404


def _involvement(client, world: World, patient_id: int) -> int:
    resp = client.post(
        f"/patients/{patient_id}/involvements",
        json={"kind": "amputation", "region": "lower_limb_left", "level": "transtibial"},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_note_can_be_tied_to_an_involvement(client, world: World) -> None:
    patient = make_patient(client, world)
    inv = _involvement(client, world, patient["id"])

    created = client.post(
        f"/patients/{patient['id']}/notes",
        json={"body": "Left socket rubbing", "involvement_id": inv},
        headers=auth(world.clinician_a),
    ).json()
    assert created["involvement_id"] == inv

    cleared = client.patch(
        f"/patients/{patient['id']}/notes/{created['id']}",
        json={"involvement_id": None},
        headers=auth(world.clinician_a),
    ).json()
    assert cleared["involvement_id"] is None


def test_note_rejects_a_foreign_involvement(client, world: World) -> None:
    p1 = make_patient(client, world, national_id="1")
    p2 = make_patient(client, world, national_id="2")
    inv = _involvement(client, world, p2["id"])

    resp = client.post(
        f"/patients/{p1['id']}/notes",
        json={"body": "x", "involvement_id": inv},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 400
