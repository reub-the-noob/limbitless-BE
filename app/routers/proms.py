"""Patient-reported outcome (PROM) endpoints: record a measure, list a
patient's measures (single-instrument lists come back oldest-first as
trend data), read one, and correct one.

Nested under a patient, so every request resolves that patient within the
caller's practice (404 otherwise). Reads and writes are audited.
Clinicians and prosthetists write; practice administrators read
(requirements Section 4). Score and the clinical flag are derived from
:mod:`app.proms` at write time.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import crud, notifications, schemas
from app.audit import AuditRecorder, get_audit_recorder
from app.database import get_db
from app.deps import get_current_user, get_request_scope, require_roles
from app.models import AuditAction, Patient, PromInstrument, PromRecord, User, UserRole
from app.scoping import RequestScope

router = APIRouter(prefix="/patients/{patient_id}/proms", tags=["proms"])

CRUD_ROLES = (UserRole.clinician, UserRole.prosthetist)
READ_ROLES = (*CRUD_ROLES, UserRole.practice_administrator)

_PATIENT_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND, detail="Patient not found"
)
_PROM_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND, detail="PROM record not found"
)


def _require_patient(db: Session, patient_id: int, practice_id: int) -> Patient:
    patient = crud.get_patient(db, practice_id=practice_id, patient_id=patient_id)
    if patient is None:
        raise _PATIENT_NOT_FOUND
    return patient


def _require_prom(db: Session, patient_id: int, prom_id: int) -> PromRecord:
    prom = crud.get_prom(db, patient_id=patient_id, prom_id=prom_id)
    if prom is None:
        raise _PROM_NOT_FOUND
    return prom


def _check_device(db: Session, device_id: int | None, patient_id: int) -> None:
    if device_id is not None and (
        crud.get_device_for_patient(db, patient_id=patient_id, device_id=device_id) is None
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="device_id does not belong to this patient",
        )


def _check_involvement(
    db: Session, involvement_id: int | None, patient_id: int
) -> None:
    if involvement_id is not None and (
        crud.get_involvement(
            db, patient_id=patient_id, involvement_id=involvement_id
        )
        is None
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="involvement_id does not belong to this patient",
        )


@router.post(
    "",
    response_model=schemas.PromRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def create_prom(
    patient_id: int,
    data: schemas.PromCreate,
    scope: RequestScope = Depends(get_request_scope),
    caller: User = Depends(get_current_user),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.PromRead:
    _require_patient(db, patient_id, scope.practice_id)
    _check_device(db, data.device_id, patient_id)
    _check_involvement(db, data.involvement_id, patient_id)
    try:
        prom = crud.create_prom(
            db, patient_id=patient_id, recorded_by_id=caller.id, data=data
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    recorder(AuditAction.create, "prom_record", prom.id)
    if prom.flagged:
        notifications.notify_prom_flagged(db, prom)
    db.commit()
    return schemas.PromRead.model_validate(prom)


@router.get(
    "",
    response_model=list[schemas.PromRead],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def list_proms(
    patient_id: int,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
    instrument: PromInstrument | None = Query(default=None),
    flagged: bool | None = Query(default=None),
) -> list[schemas.PromRead]:
    _require_patient(db, patient_id, scope.practice_id)
    rows = crud.list_proms(
        db, patient_id=patient_id, instrument=instrument, flagged=flagged
    )
    recorder(AuditAction.read, "prom_record", None)
    db.commit()
    return [schemas.PromRead.model_validate(row) for row in rows]


@router.get(
    "/{prom_id}",
    response_model=schemas.PromRead,
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def read_prom(
    patient_id: int,
    prom_id: int,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.PromRead:
    _require_patient(db, patient_id, scope.practice_id)
    prom = _require_prom(db, patient_id, prom_id)
    recorder(AuditAction.read, "prom_record", prom.id)
    db.commit()
    return schemas.PromRead.model_validate(prom)


@router.patch(
    "/{prom_id}",
    response_model=schemas.PromRead,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def update_prom(
    patient_id: int,
    prom_id: int,
    data: schemas.PromUpdate,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.PromRead:
    _require_patient(db, patient_id, scope.practice_id)
    prom = _require_prom(db, patient_id, prom_id)
    was_flagged = prom.flagged
    fields = data.model_dump(exclude_unset=True)
    if "device_id" in fields:
        _check_device(db, data.device_id, patient_id)
    if "involvement_id" in fields:
        _check_involvement(db, data.involvement_id, patient_id)
    try:
        crud.update_prom(db, prom, data)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    recorder(AuditAction.update, "prom_record", prom.id)
    # Only when a correction pushes a score across the threshold - not on
    # every edit of an already-flagged record.
    if prom.flagged and not was_flagged:
        notifications.notify_prom_flagged(db, prom)
    db.commit()
    return schemas.PromRead.model_validate(prom)
