from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import security
from app.models import AuditAction, AuditLogEntry, Patient, User
from app.tests.conftest import World

TODAY = date.today()


def auth(user: User) -> dict[str, str]:
    token = security.create_access_token(
        user.id,
        role=user.role.value,
        practice_id=user.practice_id,
        site_id=user.site_id,
    )
    return {"Authorization": f"Bearer {token}"}


def make_patient(
    client, world: World, user: User | None = None, active: bool = True, **overrides
) -> dict:
    body = {
        "first_name": "Pat",
        "last_name": "One",
        "date_of_birth": "1990-01-01",
        "national_id": "0000000000001",
    }
    body.update(overrides)
    writer = user or world.clinician_a
    patient = client.post("/patients", json=body, headers=auth(writer)).json()
    if not active:
        client.post(
            f"/patients/{patient['id']}/deactivate", headers=auth(writer)
        )
    return patient


def make_involvement(
    client, world: World, patient_id: int, user: User | None = None, **overrides
) -> dict:
    body = {"kind": "amputation", "region": "lower_limb_left"}
    body.update(overrides)
    return client.post(
        f"/patients/{patient_id}/involvements",
        json=body,
        headers=auth(user or world.clinician_a),
    ).json()


def make_device(
    client, world: World, patient_id: int, involvement_id: int, **body
) -> dict:
    payload = {"device_type": "body_powered"}
    payload.update(body)
    return client.post(
        f"/patients/{patient_id}/involvements/{involvement_id}/devices",
        json=payload,
        headers=auth(world.clinician_a),
    ).json()


def add_milestone(
    client,
    world: World,
    patient_id: int,
    milestone_type: str = "gait_functional_training",
    **body,
) -> dict:
    payload: dict[str, object] = {"milestone_type": milestone_type}
    payload.update(body)
    return client.post(
        f"/patients/{patient_id}/milestones",
        json=payload,
        headers=auth(world.clinician_a),
    ).json()


def add_prom(
    client, world: World, patient_id: int, score: int, instrument: str
) -> dict:
    return client.post(
        f"/patients/{patient_id}/proms",
        json={"instrument": instrument, "responses": {"score": score}},
        headers=auth(world.clinician_a),
    ).json()


def report(client, world: World, user: User | None = None, **params) -> dict:
    return client.get(
        "/reports/summary",
        params=params,
        headers=auth(user or world.clinician_a),
    ).json()


def as_dict(rows: list[dict]) -> dict[str, int]:
    return {row["key"]: row["count"] for row in rows}


# --- basics ------------------------------------------------------------


def test_empty_report(client, world: World) -> None:
    body = report(client, world, since_days=45)
    assert body["since_days"] == 45
    assert body["caseload"] == {
        "active_patients": 0,
        "inactive_patients": 0,
        "new_patients": 0,
        "by_involvement_kind": [],
    }
    assert body["milestones"] == {
        "completed": 0,
        "completed_on_time": 0,
        "completed_late": 0,
        "avg_days_late": None,
        "open_overdue": 0,
    }
    assert body["devices"]["total"] == 0


def test_since_days_is_validated(client, world: World) -> None:
    assert (
        client.get(
            "/reports/summary",
            params={"since_days": 0},
            headers=auth(world.clinician_a),
        ).status_code
        == 422
    )


# --- caseload --------------------------------------------------------


def test_caseload_counts_and_involvement_kinds(client, world: World) -> None:
    a = make_patient(client, world, national_id="1")
    make_involvement(client, world, a["id"], kind="amputation")
    b = make_patient(client, world, national_id="2")
    make_involvement(client, world, b["id"], kind="orthotic_need", level=None)
    make_patient(client, world, national_id="3", active=False)
    make_patient(client, world, user=world.clinician_b, national_id="1")

    caseload = report(client, world)["caseload"]
    assert caseload["active_patients"] == 2
    assert caseload["inactive_patients"] == 1
    assert as_dict(caseload["by_involvement_kind"]) == {
        "amputation": 1,
        "orthotic_need": 1,
    }


def test_new_patients_respects_the_window(
    client, db: Session, world: World
) -> None:
    recent = make_patient(client, world, national_id="1")
    old = make_patient(client, world, national_id="2")
    db.execute(
        Patient.__table__.update()
        .where(Patient.id == old["id"])
        .values(created_at=datetime.now() - timedelta(days=90))
    )
    db.commit()

    assert report(client, world, since_days=30)["caseload"]["new_patients"] == 1
    assert report(client, world, since_days=180)["caseload"]["new_patients"] == 2


# --- milestone adherence -------------------------------------------


def test_milestone_adherence(client, world: World) -> None:
    p = make_patient(client, world, national_id="1")
    add_milestone(
        client,
        world,
        p["id"],
        status="complete",
        target_date=(TODAY - timedelta(days=10)).isoformat(),
        completed_date=(TODAY - timedelta(days=12)).isoformat(),
    )  # on time (2 days early)
    add_milestone(
        client,
        world,
        p["id"],
        "cast_socket_fabrication",
        status="complete",
        target_date=(TODAY - timedelta(days=10)).isoformat(),
        completed_date=(TODAY - timedelta(days=4)).isoformat(),
    )  # 6 days late
    add_milestone(
        client,
        world,
        p["id"],
        "initial_fitting_delivery",
        status="in_progress",
        target_date=(TODAY - timedelta(days=3)).isoformat(),
    )  # open + overdue

    ms = report(client, world)["milestones"]
    assert ms["completed"] == 2
    assert ms["completed_on_time"] == 1
    assert ms["completed_late"] == 1
    assert ms["avg_days_late"] == 6.0
    assert ms["open_overdue"] == 1


# --- outcome measures --------------------------------------------


def test_outcome_measures(client, world: World) -> None:
    p1 = make_patient(client, world, national_id="1")
    add_prom(client, world, p1["id"], 9, "pain_residual_limb")  # flagged
    add_prom(client, world, p1["id"], 2, "pain_residual_limb")  # not flagged
    p2 = make_patient(client, world, national_id="2")
    add_prom(client, world, p2["id"], 3, "socket_comfort_score")  # flagged

    om = report(client, world)["outcome_measures"]
    assert om["records"] == 3
    assert om["recorded_in_period"] == 3
    assert om["flagged"] == 2
    assert om["patients_with_flag"] == 2
    assert as_dict(om["by_instrument"]) == {
        "pain_residual_limb": 2,
        "socket_comfort_score": 1,
    }


# --- devices ----------------------------------------------------


def test_device_breakdown(client, world: World) -> None:
    p = make_patient(client, world, national_id="1")
    inv = make_involvement(client, world, p["id"])
    make_device(
        client, world, p["id"], inv["id"],
        device_type="body_powered", status="active",
    )
    make_device(
        client, world, p["id"], inv["id"],
        device_type="myoelectric", status="planned",
    )
    ortho = make_patient(client, world, national_id="2")
    oinv = make_involvement(
        client, world, ortho["id"], kind="orthotic_need", level=None
    )
    make_device(
        client, world, ortho["id"], oinv["id"],
        device_type="orthosis_afo", status="active",
    )

    devices = report(client, world)["devices"]
    assert devices["total"] == 3
    assert devices["prostheses"] == 2
    assert devices["orthoses"] == 1
    assert as_dict(devices["by_status"]) == {"active": 2, "planned": 1}
    assert as_dict(devices["by_type"])["orthosis_afo"] == 1


# --- scoping + roles + audit ----------------------------------


def test_report_is_practice_scoped(client, world: World) -> None:
    mine = make_patient(client, world, national_id="1")
    make_involvement(client, world, mine["id"])
    theirs = make_patient(client, world, user=world.clinician_b, national_id="1")
    make_involvement(client, world, theirs["id"], user=world.clinician_b)

    assert report(client, world)["caseload"]["active_patients"] == 1


def test_practice_administrator_may_read(client, world: World) -> None:
    assert (
        client.get(
            "/reports/summary", headers=auth(world.admin_a)
        ).status_code
        == 200
    )


def test_platform_administrator_cannot_read(client, world: World) -> None:
    assert (
        client.get(
            "/reports/summary", headers=auth(world.platform_admin)
        ).status_code
        == 403
    )


def test_read_is_audited(client, db: Session, world: World) -> None:
    report(client, world)
    rows = db.scalars(
        select(AuditLogEntry).where(
            AuditLogEntry.action == AuditAction.read,
            AuditLogEntry.entity_type == "report",
        )
    ).all()
    assert len(rows) == 1
