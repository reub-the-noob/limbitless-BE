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
        "first_name": "Ann",
        "last_name": "Bell",
        "date_of_birth": "1990-01-01",
        "national_id": "9001010000001",
    }
    body.update(overrides)
    resp = client.post(
        "/patients", json=body, headers=auth(world.clinician_a)
    )
    assert resp.status_code == 201
    return resp.json()


def add_involvement(client, world: World, patient_id: int, **overrides) -> dict:
    body = {"kind": "amputation", "region": "lower_limb_left", "level": "transtibial"}
    body.update(overrides)
    resp = client.post(
        f"/patients/{patient_id}/involvements",
        json=body,
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 201
    return resp.json()


def add_milestone(client, world: World, patient_id: int, **overrides) -> dict:
    body = {"milestone_type": "gait_functional_training", "status": "not_started"}
    body.update(overrides)
    resp = client.post(
        f"/patients/{patient_id}/milestones",
        json=body,
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 201
    return resp.json()


def add_prom(client, world: World, patient_id: int, **overrides) -> dict:
    body = {
        "instrument": "pain_residual_limb",
        "responses": {"score": 5},
        "recorded_at": "2026-06-01T10:00:00",
    }
    body.update(overrides)
    resp = client.post(
        f"/patients/{patient_id}/proms",
        json=body,
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 201
    return resp.json()


def test_report_includes_involvements_and_devices(client, world: World) -> None:
    patient = make_patient(client, world)
    involvement = add_involvement(client, world, patient["id"])
    client.post(
        f"/patients/{patient['id']}/involvements/{involvement['id']}/devices",
        json={
            "device_type": "body_powered",
            "status": "active",
            "manufacturer": "Ottobock",
            "model": "3R80",
        },
        headers=auth(world.clinician_a),
    )

    report = client.get(
        f"/patients/{patient['id']}/report", headers=auth(world.clinician_a)
    ).json()
    assert report["patient"]["id"] == patient["id"]
    assert len(report["involvements"]) == 1
    assert report["involvements"][0]["devices"][0]["manufacturer"] == "Ottobock"


def test_milestone_summary_counts_on_time_late_and_overdue(
    client, world: World
) -> None:
    patient = make_patient(client, world)
    add_milestone(
        client, world, patient["id"],
        milestone_type="gait_functional_training",
        status="complete",
        target_date="2026-01-01",
        completed_date="2026-01-01",
    )
    add_milestone(
        client, world, patient["id"],
        milestone_type="independent_ambulation_adl",
        status="complete",
        target_date="2026-01-01",
        completed_date="2026-01-10",
    )
    add_milestone(
        client, world, patient["id"],
        milestone_type="community_reintegration_followup",
        status="in_progress",
        target_date="2020-01-01",
    )
    add_milestone(
        client, world, patient["id"],
        milestone_type="prosthetic_use_training",
        status="not_started",
        target_date="2099-01-01",
    )

    report = client.get(
        f"/patients/{patient['id']}/report", headers=auth(world.clinician_a)
    ).json()
    summary = report["milestone_summary"]
    assert summary["total"] == 4
    assert summary["completed"] == 2
    assert summary["completed_on_time"] == 1
    assert summary["completed_late"] == 1
    assert summary["in_progress"] == 1
    assert summary["not_started"] == 1
    assert summary["overdue"] == 1
    assert len(report["milestones"]) == 4


def test_prom_trends_group_by_instrument_oldest_first(
    client, world: World
) -> None:
    patient = make_patient(client, world)
    add_prom(
        client, world, patient["id"],
        instrument="pain_residual_limb",
        responses={"score": 8},
        recorded_at="2026-01-01T09:00:00",
    )
    add_prom(
        client, world, patient["id"],
        instrument="pain_residual_limb",
        responses={"score": 3},
        recorded_at="2026-02-01T09:00:00",
    )
    add_prom(
        client, world, patient["id"],
        instrument="socket_comfort_score",
        responses={"score": 7},
        recorded_at="2026-01-15T09:00:00",
    )

    report = client.get(
        f"/patients/{patient['id']}/report", headers=auth(world.clinician_a)
    ).json()
    trends = {t["instrument"]: t for t in report["prom_trends"]}
    assert set(trends) == {"pain_residual_limb", "socket_comfort_score"}

    pain = trends["pain_residual_limb"]
    assert [p["score"] for p in pain["points"]] == [8, 3]
    assert [p["flagged"] for p in pain["points"]] == [True, False]
    assert pain["latest_score"] == 3
    assert pain["flagged"] is False  # 3 is below the pain-flag threshold

    scs = trends["socket_comfort_score"]
    assert scs["latest_score"] == 7


def test_scoped_to_practice(client, world: World) -> None:
    patient = make_patient(client, world)
    resp = client.get(
        f"/patients/{patient['id']}/report", headers=auth(world.clinician_b)
    )
    assert resp.status_code == 404


def test_practice_administrator_can_read(client, world: World) -> None:
    patient = make_patient(client, world)
    resp = client.get(
        f"/patients/{patient['id']}/report", headers=auth(world.admin_a)
    )
    assert resp.status_code == 200


def test_platform_admin_forbidden(client, world: World) -> None:
    patient = make_patient(client, world)
    resp = client.get(
        f"/patients/{patient['id']}/report",
        headers=auth(world.platform_admin),
    )
    assert resp.status_code == 403
