"""Milestone due / overdue notifications (requirements Section 5.6):
raised by the maintenance pass, superseded on completion."""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crud, security
from app.models import (
    MilestoneStatus,
    MilestoneType,
    Notification,
    NotificationType,
    PatientAssignment,
    RecoveryMilestone,
    User,
)
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


def assign(client, world: World, patient_id: int, user: User) -> None:
    resp = client.post(
        f"/patients/{patient_id}/assignments",
        json={"user_id": user.id},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 201


def milestone(
    client, db: Session, world: World, patient_id: int, *, target_offset_days: int
) -> RecoveryMilestone:
    resp = client.post(
        f"/patients/{patient_id}/milestones",
        json={
            "milestone_type": MilestoneType.gait_functional_training.value,
            "care_pathway": "lower_limb",
            "target_date": (
                date.today() + timedelta(days=target_offset_days)
            ).isoformat(),
        },
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 201, resp.text
    return db.get(RecoveryMilestone, resp.json()["id"])


def _notifs(db: Session, kind: NotificationType) -> list[Notification]:
    return list(
        db.scalars(
            select(Notification).where(Notification.type == kind)
        ).all()
    )


def test_scan_raises_a_due_notification_per_care_team_member(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)
    assign(client, world, patient["id"], world.clinician_a)
    assign(client, world, patient["id"], world.prosthetist_a)
    milestone(client, db, world, patient["id"], target_offset_days=-2)

    result = crud.scan_milestone_notifications(db, grace_days=7)

    assert result == {"due": 2, "overdue": 0}
    due = _notifs(db, NotificationType.milestone_due)
    assert {n.user_id for n in due} == {world.clinician_a.id, world.prosthetist_a.id}
    assert due[0].subject_type == "recovery_milestone"
    assert due[0].payload["patient_id"] == patient["id"]


def test_scan_raises_overdue_once_past_the_grace_window(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)
    assign(client, world, patient["id"], world.clinician_a)
    milestone(client, db, world, patient["id"], target_offset_days=-10)

    result = crud.scan_milestone_notifications(db, grace_days=7)
    assert result == {"due": 1, "overdue": 1}


def test_scan_is_idempotent(client, db: Session, world: World) -> None:
    patient = make_patient(client, world)
    assign(client, world, patient["id"], world.clinician_a)
    milestone(client, db, world, patient["id"], target_offset_days=-10)

    crud.scan_milestone_notifications(db, grace_days=7)
    assert crud.scan_milestone_notifications(db, grace_days=7) == {
        "due": 0,
        "overdue": 0,
    }


def test_scan_ignores_completed_and_dateless_milestones(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)
    assign(client, world, patient["id"], world.clinician_a)

    done = milestone(client, db, world, patient["id"], target_offset_days=-10)
    done.status = MilestoneStatus.complete
    dateless = milestone(client, db, world, patient["id"], target_offset_days=-10)
    dateless.target_date = None
    db.commit()

    assert crud.scan_milestone_notifications(db, grace_days=7) == {
        "due": 0,
        "overdue": 0,
    }


def test_scan_matches_but_writes_nothing_without_a_care_team(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)  # no assignments
    milestone(client, db, world, patient["id"], target_offset_days=-10)

    assert crud.scan_milestone_notifications(db, grace_days=7) == {
        "due": 0,
        "overdue": 0,
    }


def test_completing_a_milestone_supersedes_its_unread_nudges(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)
    assign(client, world, patient["id"], world.clinician_a)
    ms = milestone(client, db, world, patient["id"], target_offset_days=-10)
    crud.scan_milestone_notifications(db, grace_days=7)
    assert _notifs(db, NotificationType.milestone_due)

    resp = client.post(
        f"/patients/{patient['id']}/milestones/{ms.id}/complete",
        json={},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 200

    assert _notifs(db, NotificationType.milestone_due) == []
    assert _notifs(db, NotificationType.milestone_overdue) == []


def test_maintenance_endpoint_reports_milestone_and_email_counts(
    client, db: Session, world: World, monkeypatch
) -> None:
    from app import config

    monkeypatch.setattr(config, "NOTIFICATIONS_DISPATCH_TOKEN", "secret")
    patient = make_patient(client, world)
    assign(client, world, patient["id"], world.clinician_a)
    milestone(client, db, world, patient["id"], target_offset_days=-10)

    resp = client.post(
        "/notifications/dispatch-due", headers={"X-Dispatch-Token": "secret"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["milestones_due"] == 1
    assert body["milestones_overdue"] == 1
    # every due notification is emailed the same tick: the reassignment
    # from assign(), plus the two milestone nudges just raised.
    assert body["emails_sent"] == 3
