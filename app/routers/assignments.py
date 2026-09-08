"""Patient assignment endpoints: assign a clinician/prosthetist, list the
assignment history for a patient, and end an assignment.

Nested under a patient, so every request first resolves that patient
within the caller's practice (404 otherwise). Reads and writes are
audited. Clinicians and prosthetists assign and end; practice
administrators may read (requirements Section 4).
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud, notifications, schemas
from app.audit import AuditRecorder, get_audit_recorder
from app.database import get_db
from app.deps import get_request_scope, require_roles
from app.models import AuditAction, Patient, PatientAssignment, UserRole
from app.scoping import RequestScope

router = APIRouter(
    prefix="/patients/{patient_id}/assignments", tags=["patient-assignments"]
)

CRUD_ROLES = (UserRole.clinician, UserRole.prosthetist)
READ_ROLES = (*CRUD_ROLES, UserRole.practice_administrator)

_PATIENT_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND, detail="Patient not found"
)
_ASSIGNMENT_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND, detail="Assignment not found"
)


def _require_patient(db: Session, patient_id: int, practice_id: int) -> Patient:
    patient = crud.get_patient(db, practice_id=practice_id, patient_id=patient_id)
    if patient is None:
        raise _PATIENT_NOT_FOUND
    return patient


def _read(assignment: PatientAssignment) -> schemas.AssignmentRead:
    return schemas.AssignmentRead(
        id=assignment.id,
        patient_id=assignment.patient_id,
        user_id=assignment.user_id,
        user_email=assignment.user.email,
        practice_id=assignment.practice_id,
        site_id=assignment.site_id,
        role=assignment.role,
        start_date=assignment.start_date,
        end_date=assignment.end_date,
        notes=assignment.notes,
        created_at=assignment.created_at,
    )


@router.post(
    "",
    response_model=schemas.AssignmentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def create_assignment(
    patient_id: int,
    data: schemas.AssignmentCreate,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.AssignmentRead:
    patient = _require_patient(db, patient_id, scope.practice_id)

    user = crud.get_assignable_user(
        db, user_id=data.user_id, practice_id=scope.practice_id
    )
    if user is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="user_id must be an active clinician or prosthetist in your practice",
        )
    if data.site_id is not None and not crud.site_belongs_to_practice(
        db, site_id=data.site_id, practice_id=scope.practice_id
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="site_id does not belong to your practice",
        )

    try:
        assignment = crud.create_assignment(
            db,
            patient=patient,
            user=user,
            site_id=data.site_id,
            start_date=data.start_date,
            notes=data.notes,
        )
        recorder(AuditAction.create, "patient_assignment", assignment.id)
        notifications.notify_patient_reassigned(db, assignment)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="That user already has a current assignment for this patient",
        )
    return _read(assignment)


@router.get(
    "",
    response_model=list[schemas.AssignmentRead],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def list_assignments(
    patient_id: int,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
    active: bool | None = Query(default=None),
) -> list[schemas.AssignmentRead]:
    _require_patient(db, patient_id, scope.practice_id)
    rows = crud.list_assignments(db, patient_id=patient_id, active=active)
    recorder(AuditAction.read, "patient_assignment", None)
    db.commit()
    return [_read(row) for row in rows]


@router.post(
    "/{assignment_id}/end",
    response_model=schemas.AssignmentRead,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def end_assignment(
    patient_id: int,
    assignment_id: int,
    data: schemas.AssignmentEnd,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.AssignmentRead:
    _require_patient(db, patient_id, scope.practice_id)
    assignment = crud.get_assignment(
        db,
        assignment_id=assignment_id,
        patient_id=patient_id,
        practice_id=scope.practice_id,
    )
    if assignment is None:
        raise _ASSIGNMENT_NOT_FOUND

    crud.end_assignment(db, assignment, end_date=data.end_date)
    recorder(AuditAction.update, "patient_assignment", assignment.id)
    db.commit()
    return _read(assignment)
