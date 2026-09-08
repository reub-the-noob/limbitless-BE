"""Platform-administrator endpoints (requirements Section 4).

A platform administrator onboards new practices (each with a first site
and a first practice administrator) and manages platform-level accounts.
They have no access to clinical data. Writes are audited; a practice
onboard is a single transaction, so a duplicate admin email rolls the
whole thing back.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud, schemas
from app.audit import AuditRecorder, get_audit_recorder
from app.database import get_db
from app.deps import require_roles
from app.models import AuditAction, Practice, UserRole

router = APIRouter(prefix="/platform", tags=["platform"])

_only_platform_admin = require_roles(UserRole.platform_administrator)

_PRACTICE_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND, detail="Practice not found"
)
_EMAIL_TAKEN = HTTPException(
    status.HTTP_409_CONFLICT, detail="A user with that email already exists"
)


def _summary(db: Session, practice: Practice) -> schemas.PracticeSummary:
    return schemas.PracticeSummary(
        **schemas.PracticeRead.model_validate(practice).model_dump(),
        **crud.practice_counts(db, practice.id),
    )


def _require_practice(db: Session, practice_id: int) -> Practice:
    practice = crud.get_practice(db, practice_id)
    if practice is None:
        raise _PRACTICE_NOT_FOUND
    return practice


@router.post(
    "/practices",
    response_model=schemas.OnboardResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_only_platform_admin)],
)
def onboard_practice(
    data: schemas.PracticeOnboard,
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.OnboardResult:
    try:
        practice = crud.create_practice(db, data.practice)
        site = crud.create_site(db, practice_id=practice.id, data=data.first_site)
        admin = crud.add_user(
            db,
            email=data.first_admin.email,
            password=data.first_admin.password,
            role=UserRole.practice_administrator,
            practice_id=practice.id,
            site_id=site.id,
        )
        recorder(
            AuditAction.create, "practice", practice.id, practice_id=practice.id
        )
        recorder(AuditAction.create, "user", admin.id, practice_id=practice.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _EMAIL_TAKEN
    return schemas.OnboardResult(
        practice=schemas.PracticeRead.model_validate(practice),
        first_site=schemas.SiteRead.model_validate(site),
        first_admin=schemas.UserRead.model_validate(admin),
    )


@router.get(
    "/practices",
    response_model=schemas.PracticePage,
    dependencies=[Depends(_only_platform_admin)],
)
def list_practices(
    db: Session = Depends(get_db),
    q: str | None = Query(default=None, description="matches practice name"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> schemas.PracticePage:
    rows, total = crud.list_practices(db, query=q, limit=limit, offset=offset)
    return schemas.PracticePage(
        items=[_summary(db, row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/practices/{practice_id}",
    response_model=schemas.PracticeDetail,
    dependencies=[Depends(_only_platform_admin)],
)
def read_practice(
    practice_id: int, db: Session = Depends(get_db)
) -> schemas.PracticeDetail:
    practice = _require_practice(db, practice_id)
    return schemas.PracticeDetail(
        **_summary(db, practice).model_dump(),
        sites=[schemas.SiteRead.model_validate(s) for s in practice.sites],
    )


@router.patch(
    "/practices/{practice_id}",
    response_model=schemas.PracticeRead,
    dependencies=[Depends(_only_platform_admin)],
)
def update_practice(
    practice_id: int,
    data: schemas.PracticeUpdate,
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.PracticeRead:
    practice = _require_practice(db, practice_id)
    crud.update_practice(db, practice, data)
    recorder(
        AuditAction.update, "practice", practice.id, practice_id=practice.id
    )
    db.commit()
    return schemas.PracticeRead.model_validate(practice)


@router.post(
    "/practices/{practice_id}/admins",
    response_model=schemas.UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_only_platform_admin)],
)
def add_practice_admin(
    practice_id: int,
    data: schemas.AdminCredentials,
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.UserRead:
    _require_practice(db, practice_id)
    try:
        admin = crud.add_user(
            db,
            email=data.email,
            password=data.password,
            role=UserRole.practice_administrator,
            practice_id=practice_id,
        )
        recorder(AuditAction.create, "user", admin.id, practice_id=practice_id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _EMAIL_TAKEN
    return schemas.UserRead.model_validate(admin)


@router.post(
    "/admins",
    response_model=schemas.UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_only_platform_admin)],
)
def add_platform_admin(
    data: schemas.AdminCredentials,
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.UserRead:
    try:
        admin = crud.add_user(
            db,
            email=data.email,
            password=data.password,
            role=UserRole.platform_administrator,
        )
        recorder(AuditAction.create, "user", admin.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _EMAIL_TAKEN
    return schemas.UserRead.model_validate(admin)
