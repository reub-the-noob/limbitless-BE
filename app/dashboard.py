"""Clinician / prosthetist dashboard aggregation (requirements Section 5.5).

Everything is scoped to one practice; ``assigned_to`` further narrows it to
the patients a given user currently has an assignment to ("my caseload").
"""

from datetime import date, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app import schemas
from app.models import (
    MilestoneStatus,
    Patient,
    PatientAssignment,
    PromRecord,
    RecoveryMilestone,
)

LIST_LIMIT = 20


def _patient_scope(practice_id: int, assigned_to: int | None) -> Select:
    stmt = select(Patient.id).where(Patient.practice_id == practice_id)
    if assigned_to is not None:
        stmt = stmt.where(
            Patient.id.in_(
                select(PatientAssignment.patient_id).where(
                    PatientAssignment.user_id == assigned_to,
                    PatientAssignment.end_date.is_(None),
                )
            )
        )
    return stmt


def _name(first: str, last: str) -> str:
    return f"{first} {last}"


def build_summary(
    db: Session,
    *,
    practice_id: int,
    assigned_to: int | None = None,
    upcoming_days: int = 14,
) -> schemas.DashboardSummary:
    today = date.today()
    scope = _patient_scope(practice_id, assigned_to)

    active_patients = db.scalar(
        select(func.count())
        .select_from(Patient)
        .where(Patient.id.in_(scope), Patient.is_active.is_(True))
    )

    phase_rows = db.execute(
        select(
            RecoveryMilestone.milestone_type,
            func.count(func.distinct(RecoveryMilestone.patient_id)),
        )
        .where(
            RecoveryMilestone.patient_id.in_(scope),
            RecoveryMilestone.status == MilestoneStatus.in_progress,
        )
        .group_by(RecoveryMilestone.milestone_type)
    ).all()
    patients_by_phase = {mt.value: count for mt, count in phase_rows}

    overdue_stmt = (
        select(RecoveryMilestone, Patient.first_name, Patient.last_name)
        .join(Patient, Patient.id == RecoveryMilestone.patient_id)
        .where(
            RecoveryMilestone.patient_id.in_(scope),
            RecoveryMilestone.target_date.is_not(None),
            RecoveryMilestone.target_date < today,
            RecoveryMilestone.status != MilestoneStatus.complete,
        )
        .order_by(RecoveryMilestone.target_date.asc())
    )
    overdue_count = db.scalar(
        select(func.count()).select_from(overdue_stmt.subquery())
    )
    overdue = [
        schemas.OverdueMilestone(
            milestone_id=m.id,
            patient_id=m.patient_id,
            patient_name=_name(first, last),
            milestone_type=m.milestone_type,
            status=m.status,
            target_date=m.target_date,
            days_overdue=(today - m.target_date).days,
        )
        for m, first, last in db.execute(overdue_stmt.limit(LIST_LIMIT)).all()
    ]

    upcoming_stmt = (
        select(RecoveryMilestone, Patient.first_name, Patient.last_name)
        .join(Patient, Patient.id == RecoveryMilestone.patient_id)
        .where(
            RecoveryMilestone.patient_id.in_(scope),
            RecoveryMilestone.target_date.is_not(None),
            RecoveryMilestone.target_date >= today,
            RecoveryMilestone.target_date <= today + timedelta(days=upcoming_days),
            RecoveryMilestone.status != MilestoneStatus.complete,
        )
        .order_by(RecoveryMilestone.target_date.asc())
    )
    upcoming_count = db.scalar(
        select(func.count()).select_from(upcoming_stmt.subquery())
    )
    upcoming = [
        schemas.UpcomingMilestone(
            milestone_id=m.id,
            patient_id=m.patient_id,
            patient_name=_name(first, last),
            milestone_type=m.milestone_type,
            status=m.status,
            target_date=m.target_date,
            days_until=(m.target_date - today).days,
        )
        for m, first, last in db.execute(upcoming_stmt.limit(LIST_LIMIT)).all()
    ]

    flagged_stmt = (
        select(PromRecord, Patient.first_name, Patient.last_name)
        .join(Patient, Patient.id == PromRecord.patient_id)
        .where(
            PromRecord.patient_id.in_(scope), PromRecord.flagged.is_(True)
        )
        .order_by(PromRecord.recorded_at.desc())
    )
    flagged_count = db.scalar(
        select(func.count()).select_from(flagged_stmt.subquery())
    )
    flagged_proms = [
        schemas.FlaggedProm(
            prom_id=p.id,
            patient_id=p.patient_id,
            patient_name=_name(first, last),
            instrument=p.instrument,
            score=p.score,
            flag_reason=p.flag_reason,
            recorded_at=p.recorded_at,
        )
        for p, first, last in db.execute(flagged_stmt.limit(LIST_LIMIT)).all()
    ]

    return schemas.DashboardSummary(
        active_patients=active_patients or 0,
        patients_by_phase=patients_by_phase,
        overdue_count=overdue_count or 0,
        overdue=overdue,
        upcoming_count=upcoming_count or 0,
        upcoming=upcoming,
        flagged_prom_count=flagged_count or 0,
        flagged_proms=flagged_proms,
    )
