"""Limb-involvement endpoints: the affected body locations for a patient
(amputation, congenital absence, or an intact part needing an orthosis).

Nested under a patient, so every request resolves that patient within the
caller's practice first (404 otherwise). Involvements are never deleted —
``status = resolved`` retires one, keeping its device history intact.
Reads and writes are audited. Clinicians and prosthetists write; practice
administrators read (requirements Section 4).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import crud, schemas
from app.audit import AuditRecorder, get_audit_recorder
from app.database import get_db
from app.deps import get_request_scope, require_roles
from app.models import AuditAction, LimbInvolvement, Patient, UserRole
from app.scoping import RequestScope

router = APIRouter(
    prefix="/patients/{patient_id}/involvements", tags=["limb-involvements"]
)

CRUD_ROLES = (UserRole.clinician, UserRole.prosthetist)
READ_ROLES = (*CRUD_ROLES, UserRole.practice_administrator)

_PATIENT_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND, detail="Patient not found"
)
_INVOLVEMENT_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND, detail="Involvement not found"
)


def _require_patient(db: Session, patient_id: int, practice_id: int) -> Patient:
    patient = crud.get_patient(db, practice_id=practice_id, patient_id=patient_id)
    if patient is None:
        raise _PATIENT_NOT_FOUND
    return patient


def _require_involvement(
    db: Session, patient_id: int, involvement_id: int
) -> LimbInvolvement:
    involvement = crud.get_involvement(
        db, patient_id=patient_id, involvement_id=involvement_id
    )
    if involvement is None:
        raise _INVOLVEMENT_NOT_FOUND
    return involvement


@router.post(
    "",
    response_model=schemas.InvolvementRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def create_involvement(
    patient_id: int,
    data: schemas.InvolvementCreate,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.InvolvementRead:
    _require_patient(db, patient_id, scope.practice_id)
    involvement = crud.create_involvement(db, patient_id=patient_id, data=data)
    recorder(AuditAction.create, "limb_involvement", involvement.id)
    db.commit()
    return schemas.InvolvementRead.model_validate(involvement)


@router.get(
    "",
    response_model=list[schemas.InvolvementRead],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def list_involvements(
    patient_id: int,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> list[schemas.InvolvementRead]:
    _require_patient(db, patient_id, scope.practice_id)
    rows = crud.list_involvements(db, patient_id=patient_id)
    recorder(AuditAction.read, "limb_involvement", None)
    db.commit()
    return [schemas.InvolvementRead.model_validate(row) for row in rows]


@router.get(
    "/{involvement_id}",
    response_model=schemas.InvolvementDetail,
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def read_involvement(
    patient_id: int,
    involvement_id: int,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.InvolvementDetail:
    _require_patient(db, patient_id, scope.practice_id)
    involvement = _require_involvement(db, patient_id, involvement_id)
    devices = crud.list_devices(db, involvement_id=involvement_id)
    recorder(AuditAction.read, "limb_involvement", involvement.id)
    db.commit()
    return schemas.InvolvementDetail(
        **schemas.InvolvementRead.model_validate(involvement).model_dump(),
        devices=[schemas.DeviceRead.model_validate(d) for d in devices],
    )


@router.patch(
    "/{involvement_id}",
    response_model=schemas.InvolvementRead,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def update_involvement(
    patient_id: int,
    involvement_id: int,
    data: schemas.InvolvementUpdate,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.InvolvementRead:
    _require_patient(db, patient_id, scope.practice_id)
    involvement = _require_involvement(db, patient_id, involvement_id)
    crud.update_involvement(db, involvement, data)
    recorder(AuditAction.update, "limb_involvement", involvement.id)
    db.commit()
    return schemas.InvolvementRead.model_validate(involvement)
