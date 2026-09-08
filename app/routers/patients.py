"""Patient endpoints: create, view, list, update, (de)activate.

Every query is scoped to the caller's practice (:func:`get_request_scope`)
and every read and write is recorded through the audit log
(:func:`get_audit_recorder`). Clinicians and prosthetists have full
access; practice administrators are read-only (requirements Section 4).
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud, schemas
from app.audit import AuditRecorder, get_audit_recorder
from app.database import get_db
from app.deps import get_current_user, get_request_scope, require_roles
from app.models import AuditAction, DeviceType, MilestoneType, User, UserRole
from app.scoping import RequestScope

router = APIRouter(prefix="/patients", tags=["patients"])

CRUD_ROLES = (UserRole.clinician, UserRole.prosthetist)
READ_ROLES = (*CRUD_ROLES, UserRole.practice_administrator)

_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, detail="Patient not found")


def _check_site(db: Session, site_id: int | None, practice_id: int) -> None:
    if site_id is not None and not crud.site_belongs_to_practice(
        db, site_id=site_id, practice_id=practice_id
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="site_id does not belong to your practice",
        )


@router.post(
    "",
    response_model=schemas.PatientRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def create_patient(
    data: schemas.PatientCreate,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.PatientRead:
    _check_site(db, data.site_id, scope.practice_id)
    try:
        patient = crud.create_patient(db, practice_id=scope.practice_id, data=data)
        recorder(AuditAction.create, "patient", patient.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="A patient with that national_id or passport already exists",
        )
    return schemas.PatientRead.model_validate(patient)


@router.get(
    "",
    response_model=schemas.PatientPage,
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def list_patients(
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
    q: str | None = Query(default=None, description="matches first or last name"),
    active: bool | None = Query(default=True),
    assigned_to: int | None = Query(
        default=None, description="user id with a current assignment"
    ),
    device_type: DeviceType | None = Query(
        default=None, description="has a device of this category"
    ),
    phase: MilestoneType | None = Query(
        default=None, description="has an in-progress milestone of this type"
    ),
    flagged_prom: bool = Query(
        default=False, description="has at least one flagged PROM result"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> schemas.PatientPage:
    rows, total = crud.list_patients(
        db,
        practice_id=scope.practice_id,
        query=q,
        active=active,
        assigned_to=assigned_to,
        device_type=device_type,
        phase=phase,
        flagged_prom=flagged_prom,
        limit=limit,
        offset=offset,
    )
    recorder(AuditAction.read, "patient", None)
    db.commit()
    return schemas.PatientPage(
        items=[schemas.PatientRead.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{patient_id}",
    response_model=schemas.PatientRead,
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def read_patient(
    patient_id: int,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.PatientRead:
    patient = crud.get_patient(
        db, practice_id=scope.practice_id, patient_id=patient_id
    )
    if patient is None:
        raise _NOT_FOUND
    recorder(AuditAction.read, "patient", patient.id)
    db.commit()
    return schemas.PatientRead.model_validate(patient)


@router.patch(
    "/{patient_id}",
    response_model=schemas.PatientRead,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def update_patient(
    patient_id: int,
    data: schemas.PatientUpdate,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.PatientRead:
    patient = crud.get_patient(
        db, practice_id=scope.practice_id, patient_id=patient_id
    )
    if patient is None:
        raise _NOT_FOUND
    if "site_id" in data.model_dump(exclude_unset=True):
        _check_site(db, data.site_id, scope.practice_id)
    try:
        crud.update_patient(db, patient, data)
        recorder(AuditAction.update, "patient", patient.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="That change conflicts with an existing patient record",
        )
    return schemas.PatientRead.model_validate(patient)


@router.post(
    "/{patient_id}/link-user",
    response_model=schemas.PatientRead,
    dependencies=[
        Depends(require_roles(*CRUD_ROLES, UserRole.practice_administrator))
    ],
)
def link_patient_user(
    patient_id: int,
    data: schemas.PatientLinkUser,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.PatientRead:
    """Tie a ``patient``-role login to this record so the person can use
    the self-service portal."""
    patient = crud.get_patient(
        db, practice_id=scope.practice_id, patient_id=patient_id
    )
    if patient is None:
        raise _NOT_FOUND

    user = crud.get_linkable_patient_user(
        db, user_id=data.user_id, practice_id=scope.practice_id
    )
    if user is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="user_id must be an active patient-role account in your practice",
        )
    owner = crud.patient_link_owner(db, patient_id=patient.id)
    if owner == user.id:
        return schemas.PatientRead.model_validate(patient)  # already linked, no-op
    if owner is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="That record is already linked to a different login",
        )

    crud.link_patient(db, patient_id=patient.id, user_id=user.id)
    recorder(AuditAction.update, "patient", patient.id)
    db.commit()
    return schemas.PatientRead.model_validate(patient)


@router.delete(
    "/{patient_id}/link-user",
    response_model=schemas.PatientRead,
    dependencies=[
        Depends(require_roles(*CRUD_ROLES, UserRole.practice_administrator))
    ],
)
def unlink_patient_user(
    patient_id: int,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.PatientRead:
    patient = crud.get_patient(
        db, practice_id=scope.practice_id, patient_id=patient_id
    )
    if patient is None:
        raise _NOT_FOUND
    crud.unlink_patient(db, patient_id=patient.id)
    recorder(AuditAction.update, "patient", patient.id)
    db.commit()
    return schemas.PatientRead.model_validate(patient)


_REVIEW_ACCESS_ROLES = (*READ_ROLES,)  # clinician / prosthetist / practice admin


@router.get(
    "/{patient_id}/review-access",
    response_model=list[schemas.ReviewGrantRead],
    dependencies=[Depends(require_roles(*_REVIEW_ACCESS_ROLES))],
)
def list_review_access(
    patient_id: int,
    scope: RequestScope = Depends(get_request_scope),
    db: Session = Depends(get_db),
) -> list[schemas.ReviewGrantRead]:
    if (
        crud.get_patient(
            db, practice_id=scope.practice_id, patient_id=patient_id
        )
        is None
    ):
        raise _NOT_FOUND
    return [
        schemas.ReviewGrantRead(
            id=grant.id,
            patient_id=grant.patient_id,
            reviewer_id=grant.reviewer_id,
            reviewer_email=email,
            granted_by_id=grant.granted_by_id,
            created_at=grant.created_at,
        )
        for grant, email in crud.list_review_grants(db, patient_id=patient_id)
    ]


@router.post(
    "/{patient_id}/review-access",
    response_model=schemas.ReviewGrantRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*_REVIEW_ACCESS_ROLES))],
)
def grant_review_access(
    patient_id: int,
    data: schemas.ReviewGrantCreate,
    caller: User = Depends(get_current_user),
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.ReviewGrantRead:
    patient = crud.get_patient(
        db, practice_id=scope.practice_id, patient_id=patient_id
    )
    if patient is None:
        raise _NOT_FOUND
    reviewer = crud.get_active_reviewer(db, user_id=data.reviewer_id)
    if reviewer is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="reviewer_id must be an active medical-aid reviewer",
        )
    try:
        grant = crud.create_review_grant(
            db,
            patient_id=patient.id,
            reviewer_id=reviewer.id,
            granted_by_id=caller.id,
        )
        recorder(AuditAction.update, "patient", patient.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="That reviewer already has access to this patient",
        )
    return schemas.ReviewGrantRead(
        id=grant.id,
        patient_id=grant.patient_id,
        reviewer_id=grant.reviewer_id,
        reviewer_email=reviewer.email,
        granted_by_id=grant.granted_by_id,
        created_at=grant.created_at,
    )


@router.delete(
    "/{patient_id}/review-access/{reviewer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles(*_REVIEW_ACCESS_ROLES))],
)
def revoke_review_access(
    patient_id: int,
    reviewer_id: int,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> None:
    patient = crud.get_patient(
        db, practice_id=scope.practice_id, patient_id=patient_id
    )
    if patient is None:
        raise _NOT_FOUND
    if not crud.delete_review_grant(
        db, patient_id=patient_id, reviewer_id=reviewer_id
    ):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="No such grant"
        )
    recorder(AuditAction.update, "patient", patient.id)
    db.commit()


def _set_active(
    patient_id: int,
    *,
    active: bool,
    scope: RequestScope,
    recorder: AuditRecorder,
    db: Session,
) -> schemas.PatientRead:
    patient = crud.get_patient(
        db, practice_id=scope.practice_id, patient_id=patient_id
    )
    if patient is None:
        raise _NOT_FOUND
    crud.set_patient_active(db, patient, active=active)
    recorder(AuditAction.update, "patient", patient.id)
    db.commit()
    return schemas.PatientRead.model_validate(patient)


@router.post(
    "/{patient_id}/deactivate",
    response_model=schemas.PatientRead,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def deactivate_patient(
    patient_id: int,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.PatientRead:
    return _set_active(
        patient_id, active=False, scope=scope, recorder=recorder, db=db
    )


@router.post(
    "/{patient_id}/reactivate",
    response_model=schemas.PatientRead,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def reactivate_patient(
    patient_id: int,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.PatientRead:
    return _set_active(
        patient_id, active=True, scope=scope, recorder=recorder, db=db
    )
