from sqlalchemy import select
from sqlalchemy.orm import Session

from app import security
from app.models import AuditAction, AuditLogEntry, User
from app.pathways import LOWER_LIMB_PATHWAY
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


def make_device(client, world: World, patient_id: int) -> dict:
    inv = client.post(
        f"/patients/{patient_id}/involvements",
        json={
            "kind": "amputation",
            "region": "lower_limb_left",
            "level": "transtibial",
            "causes": ["trauma"],
        },
        headers=auth(world.clinician_a),
    )
    assert inv.status_code == 201, inv.text
    resp = client.post(
        f"/patients/{patient_id}/involvements/{inv.json()['id']}/devices",
        json={"device_type": "body_powered"},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 201
    return resp.json()


def apply_pathway(client, world: World, patient_id: int, **body):
    return client.post(
        f"/patients/{patient_id}/pathways",
        json={"care_pathway": "lower_limb", **body},
        headers=auth(world.clinician_a),
    )


def milestone_audit(db: Session, action: AuditAction) -> list[AuditLogEntry]:
    return list(
        db.scalars(
            select(AuditLogEntry).where(
                AuditLogEntry.action == action,
                AuditLogEntry.entity_type == "recovery_milestone",
            )
        )
    )


# --- single milestones ----------------------------------------------------


def test_create_milestone_defaults(client, db: Session, world: World) -> None:
    patient = make_patient(client, world)

    resp = client.post(
        f"/patients/{patient['id']}/milestones",
        json={"milestone_type": "gait_functional_training"},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "not_started"
    assert body["care_pathway"] == "other"
    assert [r.entity_id for r in milestone_audit(db, AuditAction.create)] == [body["id"]]


def test_create_needs_a_writing_role(client, world: World) -> None:
    patient = make_patient(client, world)
    payload = {"milestone_type": "gait_functional_training"}
    assert (
        client.post(
            f"/patients/{patient['id']}/milestones",
            json=payload,
            headers=auth(world.admin_a),
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/patients/{patient['id']}/milestones", json=payload
        ).status_code
        == 401
    )


def test_milestone_device_must_belong_to_the_patient(client, world: World) -> None:
    p1 = make_patient(client, world, national_id="1")
    p2 = make_patient(client, world, national_id="2")
    device = make_device(client, world, p2["id"])

    resp = client.post(
        f"/patients/{p1['id']}/milestones",
        json={"milestone_type": "initial_fitting_delivery", "device_id": device["id"]},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 400


def test_update_and_complete_milestone(client, db: Session, world: World) -> None:
    patient = make_patient(client, world)
    created = client.post(
        f"/patients/{patient['id']}/milestones",
        json={"milestone_type": "gait_functional_training"},
        headers=auth(world.clinician_a),
    ).json()

    updated = client.patch(
        f"/patients/{patient['id']}/milestones/{created['id']}",
        json={"status": "in_progress", "notes": "started gait training"},
        headers=auth(world.prosthetist_a),
    ).json()
    assert updated["status"] == "in_progress"

    completed = client.post(
        f"/patients/{patient['id']}/milestones/{created['id']}/complete",
        json={"completed_date": "2026-05-01"},
        headers=auth(world.clinician_a),
    ).json()
    assert completed["status"] == "complete"
    assert completed["completed_date"] == "2026-05-01"
    assert any(
        r.entity_id == created["id"] for r in milestone_audit(db, AuditAction.update)
    )


# --- pathway instantiation ---------------------------------------------------


def test_apply_pathway_creates_the_ordered_template(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)

    resp = apply_pathway(client, world, patient["id"], start_date="2026-01-01", interval_days=14)

    assert resp.status_code == 201
    rows = resp.json()
    assert len(rows) == len(LOWER_LIMB_PATHWAY)
    assert [r["order_index"] for r in rows] == list(range(len(LOWER_LIMB_PATHWAY)))
    assert [r["milestone_type"] for r in rows] == [t.value for t in LOWER_LIMB_PATHWAY]
    assert rows[0]["target_date"] == "2026-01-01"
    assert rows[1]["target_date"] == "2026-01-15"
    assert all(r["status"] == "not_started" for r in rows)
    assert all(r["care_pathway"] == "lower_limb" for r in rows)
    assert milestone_audit(db, AuditAction.create)


def test_apply_the_orthotic_pathway(client, world: World) -> None:
    from app.pathways import ORTHOTIC_PATHWAY

    patient = make_patient(client, world)
    resp = apply_pathway(client, world, patient["id"], care_pathway="orthotic")

    assert resp.status_code == 201
    rows = resp.json()
    assert [r["milestone_type"] for r in rows] == [
        t.value for t in ORTHOTIC_PATHWAY
    ]
    assert rows[0]["milestone_type"] == "orthotic_assessment"
    assert all(r["care_pathway"] == "orthotic" for r in rows)


def test_apply_pathway_without_a_template_is_400(client, world: World) -> None:
    patient = make_patient(client, world)
    resp = client.post(
        f"/patients/{patient['id']}/pathways",
        json={"care_pathway": "other"},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 400


def test_applying_a_pathway_twice_is_409(client, world: World) -> None:
    patient = make_patient(client, world)
    assert apply_pathway(client, world, patient["id"]).status_code == 201
    assert apply_pathway(client, world, patient["id"]).status_code == 409


def test_apply_pathway_ties_milestones_to_a_device(client, world: World) -> None:
    patient = make_patient(client, world)
    device = make_device(client, world, patient["id"])

    rows = apply_pathway(
        client, world, patient["id"], device_id=device["id"]
    ).json()
    assert all(r["device_id"] == device["id"] for r in rows)


def test_apply_pathway_rejects_a_foreign_device(client, world: World) -> None:
    p1 = make_patient(client, world, national_id="1")
    p2 = make_patient(client, world, national_id="2")
    device = make_device(client, world, p2["id"])

    resp = apply_pathway(client, world, p1["id"], device_id=device["id"])
    assert resp.status_code == 400


# --- listing / filtering ---------------------------------------------------


def test_list_milestones_and_filters(client, world: World) -> None:
    patient = make_patient(client, world)
    rows = apply_pathway(client, world, patient["id"]).json()

    all_lower = client.get(
        f"/patients/{patient['id']}/milestones",
        params={"care_pathway": "lower_limb"},
        headers=auth(world.clinician_a),
    ).json()
    assert len(all_lower) == len(LOWER_LIMB_PATHWAY)
    assert [r["order_index"] for r in all_lower] == sorted(
        r["order_index"] for r in all_lower
    )

    client.post(
        f"/patients/{patient['id']}/milestones/{rows[0]['id']}/complete",
        json={},
        headers=auth(world.clinician_a),
    )
    complete = client.get(
        f"/patients/{patient['id']}/milestones",
        params={"milestone_status": "complete"},
        headers=auth(world.clinician_a),
    ).json()
    assert [r["id"] for r in complete] == [rows[0]["id"]]


def test_practice_admin_can_read_milestones(client, world: World) -> None:
    patient = make_patient(client, world)
    apply_pathway(client, world, patient["id"])
    assert (
        client.get(
            f"/patients/{patient['id']}/milestones", headers=auth(world.admin_a)
        ).status_code
        == 200
    )


def test_cross_practice_milestone_routes_are_404(client, world: World) -> None:
    patient = make_patient(client, world, user=world.clinician_a)
    rows = apply_pathway(client, world, patient["id"]).json()
    headers_b = auth(world.clinician_b)
    base = f"/patients/{patient['id']}/milestones"

    assert client.get(base, headers=headers_b).status_code == 404
    assert client.get(f"{base}/{rows[0]['id']}", headers=headers_b).status_code == 404
    assert (
        client.post(
            f"/patients/{patient['id']}/pathways",
            json={"care_pathway": "lower_limb"},
            headers=headers_b,
        ).status_code
        == 404
    )


def test_milestone_from_another_patient_is_404(client, world: World) -> None:
    p1 = make_patient(client, world, national_id="1")
    p2 = make_patient(client, world, national_id="2")
    rows = apply_pathway(client, world, p1["id"]).json()

    resp = client.get(
        f"/patients/{p2['id']}/milestones/{rows[0]['id']}",
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 404


def test_filter_patients_by_phase(client, world: World) -> None:
    p1 = make_patient(client, world, national_id="1")
    make_patient(client, world, national_id="2")
    rows = apply_pathway(client, world, p1["id"]).json()
    gait = next(r for r in rows if r["milestone_type"] == "gait_functional_training")
    client.patch(
        f"/patients/{p1['id']}/milestones/{gait['id']}",
        json={"status": "in_progress"},
        headers=auth(world.clinician_a),
    )

    listed = client.get(
        "/patients",
        params={"phase": "gait_functional_training"},
        headers=auth(world.clinician_a),
    ).json()
    assert [item["id"] for item in listed["items"]] == [p1["id"]]


def _involvement(client, world: World, patient_id: int) -> int:
    resp = client.post(
        f"/patients/{patient_id}/involvements",
        json={"kind": "amputation", "region": "lower_limb_left", "level": "transtibial"},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_milestone_and_pathway_can_be_tied_to_an_involvement(
    client, world: World
) -> None:
    patient = make_patient(client, world)
    inv = _involvement(client, world, patient["id"])

    one = client.post(
        f"/patients/{patient['id']}/milestones",
        json={"milestone_type": "gait_functional_training", "involvement_id": inv},
        headers=auth(world.clinician_a),
    ).json()
    assert one["involvement_id"] == inv

    rows = apply_pathway(
        client, world, patient["id"], care_pathway="lower_limb", involvement_id=inv
    ).json()
    assert rows and all(r["involvement_id"] == inv for r in rows)


def test_milestone_rejects_a_foreign_involvement(client, world: World) -> None:
    p1 = make_patient(client, world, national_id="1")
    p2 = make_patient(client, world, national_id="2")
    inv = _involvement(client, world, p2["id"])
    resp = client.post(
        f"/patients/{p1['id']}/milestones",
        json={"milestone_type": "gait_functional_training", "involvement_id": inv},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 400
