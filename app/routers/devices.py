"""Prosthetic / orthotic device endpoints.

Devices hang off a :class:`~app.models.LimbInvolvement`, so the CRUD is
nested ``/patients/{patient_id}/involvements/{involvement_id}/devices``:
every request resolves the patient (within the caller's practice) and
then the involvement before touching a device. A separate
``GET /patients/{patient_id}/devices`` returns every device across all of
a patient's involvements for the detail overview and the body-map view.

Reads and writes are audited. Clinicians and prosthetists write; practice
administrators read (requirements Section 4).
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import crud, schemas
from app.audit import AuditRecorder, get_audit_recorder
from app.database import get_db
from app.deps import get_request_scope, require_roles
from app.models import (
    AuditAction,
    DeviceStatus,
    LimbInvolvement,
    Patient,
    Device,
    UserRole,
)
from app.scoping import RequestScope

router = APIRouter(
    prefix="/patients/{patient_id}/involvements/{involvement_id}/devices",
    tags=["devices"],
)
overview_router = APIRouter(prefix="/patients/{patient_id}", tags=["devices"])

CRUD_ROLES = (UserRole.clinician, UserRole.prosthetist)
READ_ROLES = (*CRUD_ROLES, UserRole.practice_administrator)

_PATIENT_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND, detail="Patient not found"
)
_INVOLVEMENT_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND, detail="Involvement not found"
)
_DEVICE_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND, detail="Device not found"
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


def _require_device(
    db: Session, involvement_id: int, device_id: int
) -> Device:
    device = crud.get_device(
        db, involvement_id=involvement_id, device_id=device_id
    )
    if device is None:
        raise _DEVICE_NOT_FOUND
    return device


def _resolve(
    db: Session, patient_id: int, involvement_id: int, practice_id: int
) -> None:
    _require_patient(db, patient_id, practice_id)
    _require_involvement(db, patient_id, involvement_id)


@router.post(
    "",
    response_model=schemas.DeviceRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def create_device(
    patient_id: int,
    involvement_id: int,
    data: schemas.DeviceCreate,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.DeviceRead:
    _resolve(db, patient_id, involvement_id, scope.practice_id)
    device = crud.create_device(db, involvement_id=involvement_id, data=data)
    recorder(AuditAction.create, "device", device.id)
    db.commit()
    return schemas.DeviceRead.model_validate(device)


@router.get(
    "",
    response_model=list[schemas.DeviceRead],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def list_devices(
    patient_id: int,
    involvement_id: int,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
    device_status: DeviceStatus | None = Query(default=None),
) -> list[schemas.DeviceRead]:
    _resolve(db, patient_id, involvement_id, scope.practice_id)
    rows = crud.list_devices(
        db, involvement_id=involvement_id, status=device_status
    )
    recorder(AuditAction.read, "device", None)
    db.commit()
    return [schemas.DeviceRead.model_validate(row) for row in rows]


@router.get(
    "/{device_id}",
    response_model=schemas.DeviceRead,
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def read_device(
    patient_id: int,
    involvement_id: int,
    device_id: int,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.DeviceRead:
    _resolve(db, patient_id, involvement_id, scope.practice_id)
    device = _require_device(db, involvement_id, device_id)
    recorder(AuditAction.read, "device", device.id)
    db.commit()
    return schemas.DeviceRead.model_validate(device)


@router.patch(
    "/{device_id}",
    response_model=schemas.DeviceRead,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def update_device(
    patient_id: int,
    involvement_id: int,
    device_id: int,
    data: schemas.DeviceUpdate,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.DeviceRead:
    _resolve(db, patient_id, involvement_id, scope.practice_id)
    device = _require_device(db, involvement_id, device_id)
    crud.update_device(db, device, data)
    recorder(AuditAction.update, "device", device.id)
    db.commit()
    return schemas.DeviceRead.model_validate(device)


@router.post(
    "/{device_id}/replace",
    response_model=schemas.DeviceRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def replace_device(
    patient_id: int,
    involvement_id: int,
    device_id: int,
    data: schemas.DeviceCreate,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.DeviceRead:
    _resolve(db, patient_id, involvement_id, scope.practice_id)
    old = _require_device(db, involvement_id, device_id)
    new = crud.replace_device(db, old=old, data=data)
    recorder(AuditAction.update, "device", old.id)
    recorder(AuditAction.create, "device", new.id)
    db.commit()
    return schemas.DeviceRead.model_validate(new)


@overview_router.get(
    "/devices",
    response_model=list[schemas.DeviceRead],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def list_patient_devices(
    patient_id: int,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> list[schemas.DeviceRead]:
    _require_patient(db, patient_id, scope.practice_id)
    rows = crud.list_patient_devices(db, patient_id=patient_id)
    recorder(AuditAction.read, "device", None)
    db.commit()
    return [schemas.DeviceRead.model_validate(row) for row in rows]
