"""Practice-level reporting aggregation (requirements Section 5.6).

Where the dashboard answers "what needs my attention today", these
rollups answer "how is the practice doing" — caseload composition,
milestone adherence, outcome-measure coverage and the device inventory.
Everything is scoped to one practice; a ``since_days`` window drives the
"new" / "in period" figures.
"""

from datetime import date, datetime, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app import crud, schemas
from app.models import (
    Device,
    DeviceType,
    LimbInvolvement,
    MilestoneStatus,
    Patient,
    PromRecord,
    RecoveryMilestone,
)

_ORTHOSIS_TYPES = {t for t in DeviceType if t.value.startswith("orthosis_")}


def _count(db: Session, stmt: Select) -> int:
    return db.scalar(select(func.count()).select_from(stmt.subquery())) or 0


def _breakdown(rows: list) -> list[schemas.ReportBreakdown]:
    """Rows of ``(enum_member, count)`` -> ``ReportBreakdown`` list."""
    return [
        schemas.ReportBreakdown(key=key.value, count=count)
        for key, count in rows
    ]


def _caseload(
    db: Session, *, practice_id: int, since: date
) -> schemas.CaseloadReport:
    in_practice = Patient.practice_id == practice_id

    active = _count(
        db, select(Patient.id).where(in_practice, Patient.is_active.is_(True))
    )
    inactive = _count(
        db, select(Patient.id).where(in_practice, Patient.is_active.is_(False))
    )
    new_patients = _count(
        db, select(Patient.id).where(in_practice, Patient.created_at >= since)
    )

    kind_rows = db.execute(
        select(
            LimbInvolvement.kind,
            func.count(func.distinct(LimbInvolvement.patient_id)),
        )
        .join(Patient, Patient.id == LimbInvolvement.patient_id)
        .where(in_practice, Patient.is_active.is_(True))
        .group_by(LimbInvolvement.kind)
    ).all()

    return schemas.CaseloadReport(
        active_patients=active,
        inactive_patients=inactive,
        new_patients=new_patients,
        by_involvement_kind=_breakdown(kind_rows),
    )


def _milestones(
    db: Session, *, practice_id: int, today: date
) -> schemas.MilestoneAdherenceReport:
    done_rows = db.execute(
        select(
            RecoveryMilestone.completed_date, RecoveryMilestone.target_date
        )
        .join(Patient, Patient.id == RecoveryMilestone.patient_id)
        .where(
            Patient.practice_id == practice_id,
            RecoveryMilestone.status == MilestoneStatus.complete,
            RecoveryMilestone.completed_date.is_not(None),
            RecoveryMilestone.target_date.is_not(None),
        )
    ).all()
    late_days = [
        (completed - target).days
        for completed, target in done_rows
        if completed > target
    ]
    on_time = len(done_rows) - len(late_days)
    avg_late = round(sum(late_days) / len(late_days), 1) if late_days else None

    open_overdue = _count(
        db,
        select(RecoveryMilestone.id)
        .join(Patient, Patient.id == RecoveryMilestone.patient_id)
        .where(
            Patient.practice_id == practice_id,
            RecoveryMilestone.target_date.is_not(None),
            RecoveryMilestone.target_date < today,
            RecoveryMilestone.status != MilestoneStatus.complete,
        ),
    )

    return schemas.MilestoneAdherenceReport(
        completed=len(done_rows),
        completed_on_time=on_time,
        completed_late=len(late_days),
        avg_days_late=avg_late,
        open_overdue=open_overdue,
    )


def _outcome_measures(
    db: Session, *, practice_id: int, since: date
) -> schemas.OutcomeMeasureReport:
    in_practice = (
        select(PromRecord)
        .join(Patient, Patient.id == PromRecord.patient_id)
        .where(Patient.practice_id == practice_id)
    )

    records = _count(db, in_practice)
    in_period = _count(db, in_practice.where(PromRecord.recorded_at >= since))
    flagged = _count(db, in_practice.where(PromRecord.flagged.is_(True)))

    patients_with_flag = (
        db.scalar(
            select(func.count(func.distinct(PromRecord.patient_id)))
            .select_from(PromRecord)
            .join(Patient, Patient.id == PromRecord.patient_id)
            .where(
                Patient.practice_id == practice_id,
                Patient.is_active.is_(True),
                PromRecord.flagged.is_(True),
            )
        )
        or 0
    )

    instrument_rows = db.execute(
        select(PromRecord.instrument, func.count())
        .join(Patient, Patient.id == PromRecord.patient_id)
        .where(Patient.practice_id == practice_id)
        .group_by(PromRecord.instrument)
    ).all()

    return schemas.OutcomeMeasureReport(
        records=records,
        recorded_in_period=in_period,
        flagged=flagged,
        patients_with_flag=patients_with_flag,
        by_instrument=_breakdown(instrument_rows),
    )


def _devices(db: Session, *, practice_id: int) -> schemas.DeviceReport:
    def scoped(*columns):
        return (
            select(*columns)
            .select_from(Device)
            .join(
                LimbInvolvement,
                LimbInvolvement.id == Device.involvement_id,
            )
            .join(Patient, Patient.id == LimbInvolvement.patient_id)
            .where(Patient.practice_id == practice_id)
        )

    type_rows = db.execute(
        scoped(Device.device_type, func.count()).group_by(
            Device.device_type
        )
    ).all()
    status_rows = db.execute(
        scoped(Device.status, func.count()).group_by(
            Device.status
        )
    ).all()

    total = sum(count for _, count in type_rows)
    orthoses = sum(
        count for kind, count in type_rows if kind in _ORTHOSIS_TYPES
    )

    return schemas.DeviceReport(
        total=total,
        prostheses=total - orthoses,
        orthoses=orthoses,
        by_type=_breakdown(type_rows),
        by_status=_breakdown(status_rows),
    )


def build_report(
    db: Session, *, practice_id: int, since_days: int = 30
) -> schemas.ReportSummary:
    today = date.today()
    since = today - timedelta(days=since_days)
    return schemas.ReportSummary(
        since_days=since_days,
        caseload=_caseload(db, practice_id=practice_id, since=since),
        milestones=_milestones(db, practice_id=practice_id, today=today),
        outcome_measures=_outcome_measures(
            db, practice_id=practice_id, since=since
        ),
        devices=_devices(db, practice_id=practice_id),
    )


def _milestone_summary(
    milestones: list[RecoveryMilestone], *, today: date
) -> schemas.MilestoneReportSummary:
    completed = [m for m in milestones if m.status == MilestoneStatus.complete]
    on_time = [
        m
        for m in completed
        if m.target_date is not None
        and m.completed_date is not None
        and m.completed_date <= m.target_date
    ]
    overdue = [
        m
        for m in milestones
        if m.status != MilestoneStatus.complete
        and m.target_date is not None
        and m.target_date < today
    ]
    return schemas.MilestoneReportSummary(
        total=len(milestones),
        completed=len(completed),
        completed_on_time=len(on_time),
        completed_late=len(completed) - len(on_time),
        in_progress=len(
            [m for m in milestones if m.status == MilestoneStatus.in_progress]
        ),
        not_started=len(
            [m for m in milestones if m.status == MilestoneStatus.not_started]
        ),
        overdue=len(overdue),
    )


def _prom_trends(proms: list[PromRecord]) -> list[schemas.PromTrend]:
    """Group readings by instrument, oldest first within each group - the
    same order the FE's existing trend chart already expects."""
    by_instrument: dict = {}
    for prom in proms:
        by_instrument.setdefault(prom.instrument, []).append(prom)

    trends = []
    for instrument, rows in by_instrument.items():
        rows = sorted(rows, key=lambda p: p.recorded_at)
        latest = rows[-1]
        trends.append(
            schemas.PromTrend(
                instrument=instrument,
                latest_score=latest.score,
                latest_recorded_at=latest.recorded_at,
                flagged=latest.flagged,
                points=[
                    schemas.PromTrendPoint(
                        recorded_at=p.recorded_at,
                        score=p.score,
                        flagged=p.flagged,
                    )
                    for p in rows
                ],
            )
        )
    trends.sort(key=lambda t: t.instrument.value)
    return trends


def build_patient_report(
    db: Session, *, patient: Patient
) -> schemas.PatientReport:
    """A per-patient progress report - milestones met, current PROM
    trends - for viewing on screen or exporting (print/PDF) to share
    with a receiving clinician at handover (Section 5.5)."""
    involvements = crud.list_involvements(db, patient_id=patient.id)
    involvement_details = [
        schemas.InvolvementDetail(
            **schemas.InvolvementRead.model_validate(involvement).model_dump(),
            devices=[
                schemas.DeviceRead.model_validate(device)
                for device in crud.list_devices(db, involvement_id=involvement.id)
            ],
        )
        for involvement in involvements
    ]

    milestones = list(crud.list_milestones(db, patient_id=patient.id))
    proms = list(crud.list_proms(db, patient_id=patient.id))

    return schemas.PatientReport(
        patient=schemas.PatientRead.model_validate(patient),
        practice_name=patient.practice.name if patient.practice else None,
        site_name=patient.site.name if patient.site else None,
        generated_at=datetime.now(),
        involvements=involvement_details,
        milestone_summary=_milestone_summary(milestones, today=date.today()),
        milestones=[schemas.MilestoneRead.model_validate(m) for m in milestones],
        prom_trends=_prom_trends(proms),
    )
