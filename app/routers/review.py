"""Medical-aid reviewer access (requirements Section 5.12 + the Section
5.9 coverage-decision workflow, Phase-2/booking slices).

A reviewer sees a patient's record two ways, and both are checked
throughout this module (see ``crud._reviewer_access_clause``):

- a treating-practice staff member hands out a manual, per-patient
  :class:`~app.models.ReviewGrant` (R-34's original design - closer to
  the Phase-4 consent concept than to this section, kept for the cases
  a scheme match wouldn't cover), or
- automatically: the reviewer's own ``User.scheme_name`` matches the
  patient's active :class:`~app.models.MedicalAidMembership.scheme_name`
  - scheme-scoped and cross-practice, no grant needed, which is the
  requirements doc's actual Section 5.12 design.

Patient-record reads are read-only, as before. Coverage determinations
are the one place a reviewer writes: ``POST .../approve`` or
``.../deny`` on a ``pending`` :class:`~app.models.CoverageDetermination`
(never fetched live from a scheme's own systems - captured manually,
per Section 5.9). Everything here is audited under the reviewer's actor
id, stamped with the *patient's* practice so that practice's audit
trail shows the access.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import crud, notifications, schemas
from app.audit import AuditRecorder, get_audit_recorder
from app.database import get_db
from app.deps import get_current_user, require_roles
from app.models import AuditAction, CoverageStatus, Patient, User, UserRole

router = APIRouter(
    prefix="/review",
    tags=["review"],
    dependencies=[Depends(require_roles(UserRole.medical_aid_reviewer))],
)

_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND,
    detail="Patient not found or not shared with you",
)
_COVERAGE_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND,
    detail="Coverage determination not found or not shared with you",
)
_NOT_PENDING = HTTPException(
    status.HTTP_400_BAD_REQUEST,
    detail="This coverage determination has already been decided",
)


@router.get("/patients", response_model=list[schemas.ReviewPatientSummary])
def my_review_patients(
    caller: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[schemas.ReviewPatientSummary]:
    return [
        schemas.ReviewPatientSummary(
            id=patient.id,
            first_name=patient.first_name,
            last_name=patient.last_name,
            date_of_birth=patient.date_of_birth,
            practice_id=patient.practice_id,
            practice_name=practice_name,
            involvement_count=involvement_count,
        )
        for patient, practice_name, involvement_count in crud.list_reviewer_patients(
            db, reviewer_id=caller.id
        )
    ]


def _granted_patient(db: Session, caller: User, patient_id: int) -> Patient:
    patient = crud.get_reviewer_patient(
        db, reviewer_id=caller.id, patient_id=patient_id
    )
    if patient is None:
        raise _NOT_FOUND
    return patient


@router.get(
    "/patients/{patient_id}", response_model=schemas.ReviewBundle
)
def review_patient(
    patient_id: int,
    caller: User = Depends(get_current_user),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.ReviewBundle:
    patient = _granted_patient(db, caller, patient_id)

    involvements = [
        schemas.InvolvementDetail(
            **schemas.InvolvementRead.model_validate(involvement).model_dump(),
            devices=[
                schemas.DeviceRead.model_validate(device)
                for device in crud.list_devices(
                    db, involvement_id=involvement.id
                )
            ],
        )
        for involvement in crud.list_involvements(db, patient_id=patient.id)
    ]

    recorder(
        AuditAction.read,
        "patient",
        patient.id,
        practice_id=patient.practice_id,
    )
    db.commit()

    return schemas.ReviewBundle(
        patient=schemas.PatientRead.model_validate(patient),
        practice_name=patient.practice.name if patient.practice else None,
        site_name=patient.site.name if patient.site else None,
        involvements=involvements,
        milestones=[
            schemas.MilestoneRead.model_validate(row)
            for row in crud.list_milestones(db, patient_id=patient.id)
        ],
        proms=[
            schemas.PromRead.model_validate(row)
            for row in crud.list_proms(db, patient_id=patient.id)
        ],
        notes=[
            schemas.NoteRead.model_validate(row)
            for row in crud.list_notes(db, patient_id=patient.id)
        ],
    )


def _coverage_read(coverage) -> schemas.CoverageDeterminationRead:
    appointment = coverage.appointment
    patient = appointment.patient
    return schemas.CoverageDeterminationRead(
        id=coverage.id,
        appointment_id=coverage.appointment_id,
        patient_id=patient.id,
        patient_name=f"{patient.first_name} {patient.last_name}",
        practice_name=patient.practice.name if patient.practice else None,
        scheme_name=coverage.membership.scheme_name,
        appointment_type=appointment.appointment_type,
        scheduled_start=appointment.scheduled_start,
        status=coverage.status,
        authorization_number=coverage.authorization_number,
        valid_until=coverage.valid_until,
        decided_by_id=coverage.decided_by_id,
        decided_at=coverage.decided_at,
        notes=coverage.notes,
        created_at=coverage.created_at,
        updated_at=coverage.updated_at,
    )


@router.get(
    "/coverage", response_model=list[schemas.CoverageDeterminationRead]
)
def my_review_coverage(
    caller: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    status_: CoverageStatus | None = Query(default=None, alias="status"),
) -> list[schemas.CoverageDeterminationRead]:
    """This reviewer's coverage queue - every determination for a patient
    they can see, newest first. ``?status_=pending`` for just the ones
    still awaiting a decision."""
    rows = crud.list_reviewer_coverage(
        db, reviewer_id=caller.id, status=status_
    )
    return [_coverage_read(row) for row in rows]


def _granted_coverage(db: Session, caller: User, coverage_id: int):
    coverage = crud.get_reviewer_coverage(
        db, reviewer_id=caller.id, coverage_id=coverage_id
    )
    if coverage is None:
        raise _COVERAGE_NOT_FOUND
    return coverage


@router.get(
    "/coverage/{coverage_id}",
    response_model=schemas.CoverageDeterminationRead,
)
def read_coverage(
    coverage_id: int,
    caller: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> schemas.CoverageDeterminationRead:
    return _coverage_read(_granted_coverage(db, caller, coverage_id))


@router.post(
    "/coverage/{coverage_id}/approve",
    response_model=schemas.CoverageDeterminationRead,
)
def approve_coverage(
    coverage_id: int,
    data: schemas.CoverageApprove,
    caller: User = Depends(get_current_user),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.CoverageDeterminationRead:
    coverage = _granted_coverage(db, caller, coverage_id)
    if coverage.status != CoverageStatus.pending:
        raise _NOT_PENDING
    crud.decide_coverage(
        db,
        coverage,
        status=CoverageStatus.approved,
        decided_by_id=caller.id,
        authorization_number=data.authorization_number,
        valid_until=data.valid_until,
        notes=data.notes,
    )
    recorder(
        AuditAction.update,
        "coverage_determination",
        coverage.id,
        practice_id=coverage.appointment.patient.practice_id,
    )
    notifications.notify_coverage_decided(db, coverage)
    db.commit()
    return _coverage_read(coverage)


@router.post(
    "/coverage/{coverage_id}/deny",
    response_model=schemas.CoverageDeterminationRead,
)
def deny_coverage(
    coverage_id: int,
    data: schemas.CoverageDeny,
    caller: User = Depends(get_current_user),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.CoverageDeterminationRead:
    coverage = _granted_coverage(db, caller, coverage_id)
    if coverage.status != CoverageStatus.pending:
        raise _NOT_PENDING
    crud.decide_coverage(
        db,
        coverage,
        status=CoverageStatus.denied,
        decided_by_id=caller.id,
        notes=data.notes,
    )
    recorder(
        AuditAction.update,
        "coverage_determination",
        coverage.id,
        practice_id=coverage.appointment.patient.practice_id,
    )
    notifications.notify_coverage_decided(db, coverage)
    db.commit()
    return _coverage_read(coverage)
