from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import security
from app.models import AuditLogEntry, User
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


def add_milestone(
    client,
    world: World,
    patient_id: int,
    milestone_type: str,
    *,
    status: str = "not_started",
    target_date: date | None = None,
    user: User | None = None,
) -> dict:
    body: dict[str, object] = {
        "milestone_type": milestone_type,
        "status": status,
    }
    if target_date is not None:
        body["target_date"] = target_date.isoformat()
    return client.post(
        f"/patients/{patient_id}/milestones",
        json=body,
        headers=auth(user or world.clinician_a),
    ).json()


def add_flagged_prom(client, world: World, patient_id: int) -> dict:
    return client.post(
        f"/patients/{patient_id}/proms",
        json={"instrument": "pain_residual_limb", "responses": {"score": 9}},
        headers=auth(world.clinician_a),
    ).json()


def dash(client, world: World, user: User | None = None, **params) -> dict:
    return client.get(
        "/dashboard", params=params, headers=auth(user or world.clinician_a)
    ).json()


# --- basics --------------------------------------------------------------


def test_empty_dashboard(client, world: World) -> None:
    body = dash(client, world)
    assert body == {
        "active_patients": 0,
        "patients_by_phase": {},
        "overdue_count": 0,
        "overdue": [],
        "upcoming_count": 0,
        "upcoming": [],
        "flagged_prom_count": 0,
        "flagged_proms": [],
    }


def test_active_patient_count_is_practice_scoped(client, world: World) -> None:
    make_patient(client, world, national_id="1")
    make_patient(client, world, national_id="2")
    make_patient(client, world, national_id="3", active=False)
    make_patient(client, world, user=world.clinician_b, national_id="1")

    assert dash(client, world)["active_patients"] == 2


def test_patients_by_phase(client, world: World) -> None:
    for nid in ("1", "2"):
        p = make_patient(client, world, national_id=nid)
        add_milestone(
            client, world, p["id"], "gait_functional_training", status="in_progress"
        )
    p3 = make_patient(client, world, national_id="3")
    add_milestone(
        client, world, p3["id"], "cast_socket_fabrication", status="in_progress"
    )
    p4 = make_patient(client, world, national_id="4")
    add_milestone(
        client, world, p4["id"], "gait_functional_training", status="complete"
    )

    assert dash(client, world)["patients_by_phase"] == {
        "gait_functional_training": 2,
        "cast_socket_fabrication": 1,
    }


# --- milestones -------------------------------------------------------------


def test_overdue_milestones(client, world: World) -> None:
    p = make_patient(client, world)
    add_milestone(
        client, world, p["id"], "gait_functional_training",
        status="in_progress", target_date=TODAY - timedelta(days=3),
    )
    add_milestone(
        client, world, p["id"], "cast_socket_fabrication",
        status="complete", target_date=TODAY - timedelta(days=10),
    )
    add_milestone(
        client, world, p["id"], "initial_fitting_delivery",
        status="not_started", target_date=TODAY + timedelta(days=5),
    )

    body = dash(client, world)
    assert body["overdue_count"] == 1
    (row,) = body["overdue"]
    assert row["milestone_type"] == "gait_functional_training"
    assert row["days_overdue"] == 3
    assert row["patient_name"] == "Pat One"


def test_upcoming_milestones_and_window(client, world: World) -> None:
    p = make_patient(client, world)
    add_milestone(
        client, world, p["id"], "gait_functional_training",
        status="not_started", target_date=TODAY + timedelta(days=5),
    )
    add_milestone(
        client, world, p["id"], "independent_ambulation_adl",
        status="not_started", target_date=TODAY + timedelta(days=30),
    )
    add_milestone(
        client, world, p["id"], "cast_socket_fabrication",
        status="not_started", target_date=TODAY,
    )

    default = dash(client, world)
    assert default["upcoming_count"] == 2
    assert {r["milestone_type"] for r in default["upcoming"]} == {
        "gait_functional_training",
        "cast_socket_fabrication",
    }
    assert default["upcoming"][0]["days_until"] == 0  # due today sorts first

    widened = dash(client, world, upcoming_days=45)
    assert widened["upcoming_count"] == 3


# --- proms --------------------------------------------------------------


def test_flagged_proms(client, world: World) -> None:
    p = make_patient(client, world)
    add_flagged_prom(client, world, p["id"])
    client.post(
        f"/patients/{p['id']}/proms",
        json={"instrument": "pain_phantom", "responses": {"score": 2}},
        headers=auth(world.clinician_a),
    )

    body = dash(client, world)
    assert body["flagged_prom_count"] == 1
    (row,) = body["flagged_proms"]
    assert row["instrument"] == "pain_residual_limb"
    assert row["score"] == 9.0
    assert row["flag_reason"]
    assert row["patient_name"] == "Pat One"


# --- scoping ----------------------------------------------------------------


def test_view_mine_limits_to_assigned_patients(client, world: World) -> None:
    mine = make_patient(client, world, national_id="1")
    other = make_patient(client, world, national_id="2")
    client.post(
        f"/patients/{mine['id']}/assignments",
        json={"user_id": world.clinician_a.id},
        headers=auth(world.clinician_a),
    )
    for pid in (mine["id"], other["id"]):
        add_flagged_prom(client, world, pid)
        add_milestone(
            client, world, pid, "gait_functional_training",
            status="in_progress", target_date=TODAY - timedelta(days=1),
        )

    everyone = dash(client, world)
    assert everyone["flagged_prom_count"] == 2
    assert everyone["overdue_count"] == 2

    just_mine = dash(client, world, view="mine")
    assert just_mine["flagged_prom_count"] == 1
    assert just_mine["overdue_count"] == 1
    assert just_mine["active_patients"] == 1
    assert just_mine["flagged_proms"][0]["patient_id"] == mine["id"]


def test_dashboard_is_isolated_from_other_practices(client, world: World) -> None:
    b_patient = make_patient(client, world, user=world.clinician_b, national_id="9")
    add_flagged_prom_b = client.post(
        f"/patients/{b_patient['id']}/proms",
        json={"instrument": "pain_residual_limb", "responses": {"score": 10}},
        headers=auth(world.clinician_b),
    )
    assert add_flagged_prom_b.status_code == 201

    body = dash(client, world)
    assert body["flagged_prom_count"] == 0
    assert body["active_patients"] == 0


# --- access -----------------------------------------------------------------


def test_platform_admin_cannot_read_dashboard(client, world: World) -> None:
    assert (
        client.get("/dashboard", headers=auth(world.platform_admin)).status_code
        == 403
    )


def test_dashboard_requires_auth(client, world: World) -> None:
    assert client.get("/dashboard").status_code == 401


def test_practice_admin_can_read_dashboard(client, world: World) -> None:
    assert (
        client.get("/dashboard", headers=auth(world.admin_a)).status_code == 200
    )


def test_dashboard_read_is_audited(client, db: Session, world: World) -> None:
    dash(client, world)
    rows = list(
        db.scalars(
            select(AuditLogEntry).where(AuditLogEntry.entity_type == "dashboard")
        )
    )
    assert rows and rows[0].actor_id == world.clinician_a.id
