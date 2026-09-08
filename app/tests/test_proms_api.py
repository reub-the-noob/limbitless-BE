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


def make_device(client, world: World, patient_id: int) -> dict:
    inv = client.post(
        f"/patients/{patient_id}/involvements",
        json={
            "kind": "amputation",
            "region": "lower_limb_left",
            "level": "transtibial",
            "cause": "trauma",
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


def record(client, world: World, patient_id: int, **body):
    return client.post(
        f"/patients/{patient_id}/proms",
        json={"instrument": "pain_residual_limb", "responses": {"score": 5}, **body},
        headers=auth(world.clinician_a),
    )


def prom_audit(db: Session, action: AuditAction) -> list[AuditLogEntry]:
    return list(
        db.scalars(
            select(AuditLogEntry).where(
                AuditLogEntry.action == action,
                AuditLogEntry.entity_type == "prom_record",
            )
        )
    )


# --- create ----------------------------------------------------------------


def test_high_pain_prom_is_flagged_and_audited(client, db: Session, world: World) -> None:
    patient = make_patient(client, world)

    resp = record(client, world, patient["id"], responses={"score": 8})

    assert resp.status_code == 201
    body = resp.json()
    assert body["score"] == 8.0
    assert body["flagged"] is True
    assert body["flag_reason"]
    assert body["recorded_by_id"] == world.clinician_a.id
    assert [r.entity_id for r in prom_audit(db, AuditAction.create)] == [body["id"]]


def test_moderate_pain_prom_is_not_flagged(client, world: World) -> None:
    patient = make_patient(client, world)
    body = record(client, world, patient["id"], responses={"score": 4}).json()
    assert body["flagged"] is False
    assert body["score"] == 4.0


def test_out_of_range_score_is_rejected(client, world: World) -> None:
    patient = make_patient(client, world)
    resp = record(client, world, patient["id"], responses={"score": 12})
    assert resp.status_code == 400


def test_low_socket_comfort_is_flagged(client, world: World) -> None:
    patient = make_patient(client, world)
    body = record(
        client,
        world,
        patient["id"],
        instrument="socket_comfort_score",
        responses={"score": 3},
    ).json()
    assert body["flagged"] is True


def test_orthosis_comfort_score_is_recorded_and_flagged(
    client, world: World
) -> None:
    patient = make_patient(client, world)
    body = record(
        client,
        world,
        patient["id"],
        instrument="orthosis_comfort_score",
        responses={"score": 4},
    ).json()
    assert body["flagged"] is True
    assert "Orthosis Comfort Score" in body["flag_reason"]


def test_quest_satisfaction_rejects_a_zero_score(client, world: World) -> None:
    patient = make_patient(client, world)
    resp = record(
        client,
        world,
        patient["id"],
        instrument="quest_satisfaction",
        responses={"score": 0},
    )
    assert resp.status_code == 400


def test_recorded_at_can_be_backdated(client, world: World) -> None:
    patient = make_patient(client, world)
    body = record(
        client, world, patient["id"], recorded_at="2026-01-05T09:00:00"
    ).json()
    assert body["recorded_at"].startswith("2026-01-05T09:00:00")


def test_prom_device_must_belong_to_the_patient(client, world: World) -> None:
    p1 = make_patient(client, world, national_id="1")
    p2 = make_patient(client, world, national_id="2")
    device = make_device(client, world, p2["id"])

    resp = record(client, world, p1["id"], device_id=device["id"])
    assert resp.status_code == 400


def test_create_needs_a_writing_role(client, world: World) -> None:
    patient = make_patient(client, world)
    payload = {"instrument": "pain_phantom", "responses": {"score": 3}}
    assert (
        client.post(
            f"/patients/{patient['id']}/proms",
            json=payload,
            headers=auth(world.admin_a),
        ).status_code
        == 403
    )
    assert (
        client.post(f"/patients/{patient['id']}/proms", json=payload).status_code
        == 401
    )


# --- list / read ---------------------------------------------------------


def test_single_instrument_list_is_oldest_first(client, world: World) -> None:
    patient = make_patient(client, world)
    record(
        client, world, patient["id"], responses={"score": 6},
        recorded_at="2026-03-01T10:00:00",
    )
    record(
        client, world, patient["id"], responses={"score": 4},
        recorded_at="2026-01-01T10:00:00",
    )

    rows = client.get(
        f"/patients/{patient['id']}/proms",
        params={"instrument": "pain_residual_limb"},
        headers=auth(world.clinician_a),
    ).json()
    assert [r["recorded_at"][:10] for r in rows] == ["2026-01-01", "2026-03-01"]


def test_flagged_filter(client, world: World) -> None:
    patient = make_patient(client, world)
    record(client, world, patient["id"], responses={"score": 9})
    record(client, world, patient["id"], responses={"score": 2})

    flagged = client.get(
        f"/patients/{patient['id']}/proms",
        params={"flagged": "true"},
        headers=auth(world.clinician_a),
    ).json()
    assert [r["score"] for r in flagged] == [9.0]


def test_practice_admin_can_read_proms(client, world: World) -> None:
    patient = make_patient(client, world)
    record(client, world, patient["id"])
    assert (
        client.get(
            f"/patients/{patient['id']}/proms", headers=auth(world.admin_a)
        ).status_code
        == 200
    )


def test_cross_practice_prom_routes_are_404(client, world: World) -> None:
    patient = make_patient(client, world, user=world.clinician_a)
    prom = record(client, world, patient["id"]).json()
    headers_b = auth(world.clinician_b)
    base = f"/patients/{patient['id']}/proms"

    assert client.get(base, headers=headers_b).status_code == 404
    assert client.get(f"{base}/{prom['id']}", headers=headers_b).status_code == 404


def test_prom_from_another_patient_is_404(client, world: World) -> None:
    p1 = make_patient(client, world, national_id="1")
    p2 = make_patient(client, world, national_id="2")
    prom = record(client, world, p1["id"]).json()

    resp = client.get(
        f"/patients/{p2['id']}/proms/{prom['id']}", headers=auth(world.clinician_a)
    )
    assert resp.status_code == 404


# --- update ------------------------------------------------------------


def test_update_reevaluates_the_flag(client, db: Session, world: World) -> None:
    patient = make_patient(client, world)
    prom = record(client, world, patient["id"], responses={"score": 9}).json()
    assert prom["flagged"] is True

    updated = client.patch(
        f"/patients/{patient['id']}/proms/{prom['id']}",
        json={"responses": {"score": 2}},
        headers=auth(world.prosthetist_a),
    ).json()
    assert updated["flagged"] is False
    assert updated["score"] == 2.0
    assert any(
        r.entity_id == prom["id"] for r in prom_audit(db, AuditAction.update)
    )


def test_update_out_of_range_is_rejected(client, world: World) -> None:
    patient = make_patient(client, world)
    prom = record(client, world, patient["id"], responses={"score": 5}).json()

    resp = client.patch(
        f"/patients/{patient['id']}/proms/{prom['id']}",
        json={"responses": {"score": 99}},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 400

    unchanged = client.get(
        f"/patients/{patient['id']}/proms/{prom['id']}",
        headers=auth(world.clinician_a),
    ).json()
    assert unchanged["score"] == 5.0


# --- patient filter --------------------------------------------------------


def test_filter_patients_by_flagged_prom(client, world: World) -> None:
    p1 = make_patient(client, world, national_id="1")
    make_patient(client, world, national_id="2")
    record(client, world, p1["id"], responses={"score": 9})

    listed = client.get(
        "/patients",
        params={"flagged_prom": "true"},
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


def test_prom_can_be_tied_to_an_involvement(client, world: World) -> None:
    patient = make_patient(client, world)
    inv = _involvement(client, world, patient["id"])
    created = record(client, world, patient["id"], involvement_id=inv).json()
    assert created["involvement_id"] == inv


def test_prom_rejects_a_foreign_involvement(client, world: World) -> None:
    p1 = make_patient(client, world, national_id="1")
    p2 = make_patient(client, world, national_id="2")
    inv = _involvement(client, world, p2["id"])
    resp = record(client, world, p1["id"], involvement_id=inv)
    assert resp.status_code == 400
