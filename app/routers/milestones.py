"""Recovery milestone endpoints: log and update milestones on a patient's
rehab pathway, complete them, and apply a standard pathway template.

Nested under a patient, so every request resolves that patient within the
caller's practice (404 otherwise). Reads and writes are audited.
Clinicians and prosthetists write; practice administrators read
(requirements Section 4).
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import crud, notifications, pathways, schemas
from app.audit import AuditRecorder, get_audit_recorder
from app.database import get_db
from app.deps import get_request_scope, require_roles
from app.models import (
    AuditAction,
    CarePathway,
    MilestoneStatus,
    Patient,
    RecoveryMilestone,
    UserRole,
)
from app.scoping import RequestScope

router = APIRouter(prefix="/patients/{patient_id}", tags=["recovery-milestones"])

CRUD_ROLES = (UserRole.clinician, UserRole.prosthetist)
READ_ROLES = (*CRUD_ROLES, UserRole.practice_administrator)

_PATIENT_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND, detail="Patient not found"
)
_MILESTONE_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND, detail="Milestone not found"
)


def _require_patient(db: Session, patient_id: int, practice_id: int) -> Patient:
    patient = crud.get_patient(db, practice_id=practice_id, patient_id=patient_id)
    if patient is None:
        raise _PATIENT_NOT_FOUND
    return patient


def _require_milestone(
    db: Session, patient_id: int, milestone_id: int
) -> RecoveryMilestone:
    milestone = crud.get_milestone(
        db, patient_id=patient_id, milestone_id=milestone_id
    )
    if milestone is None:
        raise _MILESTONE_NOT_FOUND
    return milestone


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
    "/milestones",
    response_model=schemas.MilestoneRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def create_milestone(
    patient_id: int,
    data: schemas.MilestoneCreate,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.MilestoneRead:
    _require_patient(db, patient_id, scope.practice_id)
    _check_device(db, data.device_id, patient_id)
    _check_involvement(db, data.involvement_id, patient_id)
    milestone = crud.create_milestone(db, patient_id=patient_id, data=data)
    recorder(AuditAction.create, "recovery_milestone", milestone.id)
    db.commit()
    return schemas.MilestoneRead.model_validate(milestone)


@router.get(
    "/milestones",
    response_model=list[schemas.MilestoneRead],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def list_milestones(
    patient_id: int,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
    care_pathway: CarePathway | None = Query(default=None),
    milestone_status: MilestoneStatus | None = Query(default=None),
) -> list[schemas.MilestoneRead]:
    _require_patient(db, patient_id, scope.practice_id)
    rows = crud.list_milestones(
        db,
        patient_id=patient_id,
        care_pathway=care_pathway,
        status=milestone_status,
    )
    recorder(AuditAction.read, "recovery_milestone", None)
    db.commit()
    return [schemas.MilestoneRead.model_validate(row) for row in rows]


@router.get(
    "/milestones/{milestone_id}",
    response_model=schemas.MilestoneRead,
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def read_milestone(
    patient_id: int,
    milestone_id: int,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.MilestoneRead:
    _require_patient(db, patient_id, scope.practice_id)
    milestone = _require_milestone(db, patient_id, milestone_id)
    recorder(AuditAction.read, "recovery_milestone", milestone.id)
    db.commit()
    return schemas.MilestoneRead.model_validate(milestone)


@router.patch(
    "/milestones/{milestone_id}",
    response_model=schemas.MilestoneRead,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def update_milestone(
    patient_id: int,
    milestone_id: int,
    data: schemas.MilestoneUpdate,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.MilestoneRead:
    _require_patient(db, patient_id, scope.practice_id)
    milestone = _require_milestone(db, patient_id, milestone_id)
    fields = data.model_dump(exclude_unset=True)
    if "device_id" in fields:
        _check_device(db, data.device_id, patient_id)
    if "involvement_id" in fields:
        _check_involvement(db, data.involvement_id, patient_id)
    crud.update_milestone(db, milestone, data)
    recorder(AuditAction.update, "recovery_milestone", milestone.id)
    db.commit()
    return schemas.MilestoneRead.model_validate(milestone)


@router.post(
    "/milestones/{milestone_id}/complete",
    response_model=schemas.MilestoneRead,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def complete_milestone(
    patient_id: int,
    milestone_id: int,
    data: schemas.MilestoneComplete,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.MilestoneRead:
    _require_patient(db, patient_id, scope.practice_id)
    milestone = _require_milestone(db, patient_id, milestone_id)
    crud.complete_milestone(db, milestone, completed_date=data.completed_date)
    recorder(AuditAction.update, "recovery_milestone", milestone.id)
    notifications.supersede_milestone_notifications(db, milestone.id)
    db.commit()
    return schemas.MilestoneRead.model_validate(milestone)


@router.post(
    "/pathways",
    response_model=list[schemas.MilestoneRead],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def apply_pathway(
    patient_id: int,
    data: schemas.PathwayApply,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> list[schemas.MilestoneRead]:
    _require_patient(db, patient_id, scope.practice_id)
    _check_device(db, data.device_id, patient_id)
    _check_involvement(db, data.involvement_id, patient_id)

    template = pathways.template_for(data.care_pathway)
    if template is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"No pathway template for {data.care_pathway.value}",
        )
    if crud.patient_has_pathway(
        db, patient_id=patient_id, care_pathway=data.care_pathway
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="This patient already has milestones for that pathway",
        )

    rows = crud.apply_pathway(
        db,
        patient_id=patient_id,
        care_pathway=data.care_pathway,
        template=template,
        device_id=data.device_id,
        involvement_id=data.involvement_id,
        start_date=data.start_date,
        interval_days=data.interval_days,
    )
    recorder(AuditAction.create, "recovery_milestone", None)
    db.commit()
    return [schemas.MilestoneRead.model_validate(row) for row in rows]
