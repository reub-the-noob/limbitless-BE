"""Authentication endpoints: login, token refresh, current user,
self-service patient registration (requirements Section 5.11)."""

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud, schemas, security
from app.database import get_db
from app.deps import get_current_user
from app.models import User, UserRole

router = APIRouter(prefix="/auth", tags=["auth"])

_INVALID_REFRESH = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
)
_EMAIL_TAKEN = HTTPException(
    status.HTTP_409_CONFLICT, detail="A user with that email already exists"
)


def _tokens_for(user: User) -> schemas.Token:
    return schemas.Token(
        access_token=security.create_access_token(
            user.id,
            role=user.role.value,
            practice_id=user.practice_id,
            site_id=user.site_id,
            token_version=user.token_version,
        ),
        refresh_token=security.create_refresh_token(
            user.id, token_version=user.token_version
        ),
    )


@router.post("/login", response_model=schemas.Token)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> schemas.Token:
    user = crud.get_user_by_email(db, form.username)
    if user is None or not security.verify_password(
        form.password, user.hashed_password
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )
    return _tokens_for(user)


@router.post(
    "/register", response_model=schemas.Token, status_code=status.HTTP_201_CREATED
)
def register(
    data: schemas.PatientRegister, db: Session = Depends(get_db)
) -> schemas.Token:
    """Self-service sign-up for a patient account (Section 5.11) - no
    practice/site and no linked clinical record yet. Claiming an
    existing walk-in record, if one exists, is a separate step
    (``POST /account-link-requests``) once signed in."""
    try:
        user = crud.create_user(
            db,
            email=data.email,
            password=data.password,
            role=UserRole.patient,
        )
    except IntegrityError:
        db.rollback()
        raise _EMAIL_TAKEN
    return _tokens_for(user)


@router.post("/refresh", response_model=schemas.Token)
def refresh(
    body: schemas.RefreshRequest, db: Session = Depends(get_db)
) -> schemas.Token:
    try:
        payload = security.decode_token(
            body.refresh_token, expected_type=security.REFRESH_TOKEN_TYPE
        )
        user_id = int(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise _INVALID_REFRESH
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _INVALID_REFRESH
    if (payload.get("tv") or 0) != (user.token_version or 0):
        raise _INVALID_REFRESH
    return _tokens_for(user)


@router.post("/logout-all", response_model=schemas.MessageResponse)
def logout_all(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> schemas.MessageResponse:
    """Sign out of every session: bump ``token_version`` so every access
    and refresh token issued so far (this one included) stops validating.
    The client must log in again."""
    user.token_version += 1
    db.commit()
    return schemas.MessageResponse(detail="Signed out of all sessions")


@router.get("/me", response_model=schemas.UserRead)
def me(user: User = Depends(get_current_user)) -> schemas.UserRead:
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
