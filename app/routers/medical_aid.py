"""A patient's medical-aid membership (requirements Section 5.9).

At most one per patient. Its presence is what a booked appointment
checks to decide whether it needs a coverage confirmation step or can go
straight to confirmed as self-pay.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import crud, schemas
from app.audit import AuditRecorder, get_audit_recorder
from app.database import get_db
from app.deps import get_request_scope, require_roles
from app.models import AuditAction, UserRole
from app.scoping import RequestScope

router = APIRouter(
    prefix="/patients/{patient_id}/medical-aid", tags=["medical-aid"]
)

CRUD_ROLES = (UserRole.clinician, UserRole.prosthetist)
READ_ROLES = (*CRUD_ROLES, UserRole.practice_administrator)

_PATIENT_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND, detail="Patient not found"
)
_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND, detail="No medical-aid membership on file"
)


def _require_patient(db: Session, patient_id: int, practice_id: int):
    patient = crud.get_patient(db, practice_id=practice_id, patient_id=patient_id)
    if patient is None:
        raise _PATIENT_NOT_FOUND
    return patient


@router.get(
    "",
    response_model=schemas.MedicalAidMembershipRead,
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def read_membership(
    patient_id: int,
    scope: RequestScope = Depends(get_request_scope),
    db: Session = Depends(get_db),
) -> schemas.MedicalAidMembershipRead:
    _require_patient(db, patient_id, scope.practice_id)
    membership = crud.get_membership(db, patient_id=patient_id)
    if membership is None:
        raise _NOT_FOUND
    return schemas.MedicalAidMembershipRead.model_validate(membership)


@router.post(
    "",
    response_model=schemas.MedicalAidMembershipRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def create_membership(
    patient_id: int,
    data: schemas.MedicalAidMembershipCreate,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.MedicalAidMembershipRead:
    _require_patient(db, patient_id, scope.practice_id)
    if crud.get_membership(db, patient_id=patient_id) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="This patient already has a medical-aid membership on file",
        )
    membership = crud.create_membership(db, patient_id=patient_id, data=data)
    recorder(AuditAction.create, "medical_aid_membership", membership.id)
    db.commit()
    return schemas.MedicalAidMembershipRead.model_validate(membership)


@router.patch(
    "",
    response_model=schemas.MedicalAidMembershipRead,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def update_membership(
    patient_id: int,
    data: schemas.MedicalAidMembershipUpdate,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.MedicalAidMembershipRead:
    _require_patient(db, patient_id, scope.practice_id)
    membership = crud.get_membership(db, patient_id=patient_id)
    if membership is None:
        raise _NOT_FOUND
    crud.update_membership(db, membership, data)
    recorder(AuditAction.update, "medical_aid_membership", membership.id)
    db.commit()
    return schemas.MedicalAidMembershipRead.model_validate(membership)


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def delete_membership(
    patient_id: int,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> None:
    _require_patient(db, patient_id, scope.practice_id)
    membership = crud.get_membership(db, patient_id=patient_id)
    if membership is None:
        raise _NOT_FOUND
    recorder(AuditAction.delete, "medical_aid_membership", membership.id)
    crud.delete_membership(db, membership)
    db.commit()
