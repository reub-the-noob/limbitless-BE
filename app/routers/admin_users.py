"""Practice-administrator user management (requirements Section 4).

A practice administrator manages the staff accounts of their own
practice: clinicians, prosthetists and other practice administrators.
Platform-level roles (platform_administrator, patient, medical_aid_reviewer)
are not practice-scoped and cannot be assigned here. Every write is
audited; a practice admin cannot lock themselves out.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud, schemas
from app.audit import AuditRecorder, get_audit_recorder
from app.database import get_db
from app.deps import get_current_user, get_request_scope, require_roles
from app.models import AuditAction, User, UserRole
from app.scoping import RequestScope

router = APIRouter(prefix="/admin/users", tags=["admin"])

ASSIGNABLE_ROLES = frozenset(
    {
        UserRole.clinician,
        UserRole.prosthetist,
        UserRole.practice_administrator,
    }
)

_only_practice_admin = require_roles(UserRole.practice_administrator)

_USER_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND, detail="User not found"
)
_EMAIL_TAKEN = HTTPException(
    status.HTTP_409_CONFLICT, detail="A user with that email already exists"
)


def _check_role(role: UserRole | None) -> None:
    if role is not None and role not in ASSIGNABLE_ROLES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                "role must be one of: "
                + ", ".join(sorted(r.value for r in ASSIGNABLE_ROLES))
            ),
        )


def _check_site(db: Session, site_id: int | None, practice_id: int) -> None:
    if site_id is not None and not crud.site_belongs_to_practice(
        db, site_id=site_id, practice_id=practice_id
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="site_id does not belong to your practice",
        )


def _require_user(db: Session, user_id: int, practice_id: int) -> User:
    user = crud.get_practice_user(db, practice_id=practice_id, user_id=user_id)
    if user is None:
        raise _USER_NOT_FOUND
    return user


def _user_read(user: User) -> schemas.UserRead:
    """UserRead with the practice / site names resolved (the list and
    detail views show them)."""
    return schemas.UserRead(
        id=user.id,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        practice_id=user.practice_id,
        site_id=user.site_id,
        practice_name=user.practice.name if user.practice else None,
        site_name=user.site.name if user.site else None,
    )


@router.get(
    "",
    response_model=schemas.UserPage,
    dependencies=[Depends(_only_practice_admin)],
)
def list_users(
    scope: RequestScope = Depends(get_request_scope),
    db: Session = Depends(get_db),
    role: UserRole | None = Query(default=None),
    active: bool | None = Query(default=None),
    q: str | None = Query(default=None, description="matches email"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> schemas.UserPage:
    rows, total = crud.list_practice_users(
        db,
        practice_id=scope.practice_id,
        role=role,
        active=active,
        query=q,
        limit=limit,
        offset=offset,
    )
    return schemas.UserPage(
        items=[_user_read(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=schemas.UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_only_practice_admin)],
)
def create_user(
    data: schemas.AdminUserCreate,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.UserRead:
    _check_role(data.role)
    _check_site(db, data.site_id, scope.practice_id)
    try:
        user = crud.create_practice_user(
            db,
            practice_id=scope.practice_id,
            email=data.email,
            password=data.password,
            role=data.role,
            site_id=data.site_id,
        )
        recorder(AuditAction.create, "user", user.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _EMAIL_TAKEN
    return _user_read(user)


@router.get(
    "/{user_id}",
    response_model=schemas.UserRead,
    dependencies=[Depends(_only_practice_admin)],
)
def read_user(
    user_id: int,
    scope: RequestScope = Depends(get_request_scope),
    db: Session = Depends(get_db),
) -> schemas.UserRead:
    return _user_read(_require_user(db, user_id, scope.practice_id))


@router.patch(
    "/{user_id}",
    response_model=schemas.UserRead,
    dependencies=[Depends(_only_practice_admin)],
)
def update_user(
    user_id: int,
    data: schemas.AdminUserUpdate,
    scope: RequestScope = Depends(get_request_scope),
    caller: User = Depends(get_current_user),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.UserRead:
    user = _require_user(db, user_id, scope.practice_id)
    _check_role(data.role)
    if "site_id" in data.model_dump(exclude_unset=True):
        _check_site(db, data.site_id, scope.practice_id)

    if user.id == caller.id:
        if data.is_active is False:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail="You cannot deactivate your own account",
            )
        if data.role is not None and data.role != UserRole.practice_administrator:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail="You cannot remove your own administrator role",
            )

    try:
        crud.update_user(db, user, data)
        recorder(AuditAction.update, "user", user.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _EMAIL_TAKEN
    return _user_read(user)


@router.post(
    "/{user_id}/set-password",
    response_model=schemas.UserRead,
    dependencies=[Depends(_only_practice_admin)],
)
def set_password(
    user_id: int,
    data: schemas.AdminPasswordSet,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.UserRead:
    user = _require_user(db, user_id, scope.practice_id)
    crud.set_user_password(db, user, password=data.password)
    recorder(AuditAction.update, "user", user.id)
    db.commit()
    return _user_read(user)
