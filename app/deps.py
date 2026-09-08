"""FastAPI dependencies for authentication and authorization.

- ``get_current_user`` — validate the bearer token, load an active User
- ``require_roles`` — gate an endpoint to a set of roles
- ``get_request_scope`` — the caller's practice/site boundary for the
  query-scoping layer (:mod:`app.scoping`)
"""

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app import security
from app.database import get_db
from app.models import User, UserRole
from app.scoping import RequestScope

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

_CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    try:
        payload = security.decode_token(
            token, expected_type=security.ACCESS_TOKEN_TYPE
        )
        user_id = int(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise _CREDENTIALS_ERROR
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _CREDENTIALS_ERROR
    return user


def require_roles(*roles: UserRole):
    """Dependency factory: 403 unless the caller holds one of ``roles``."""

    allowed = set(roles)

    def _guard(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role for this operation",
            )
        return user

    return _guard


def get_request_scope(user: User = Depends(get_current_user)) -> RequestScope:
    """The caller's tenant boundary.

    403 for a user with no practice (platform administrator, medical aid
    reviewer): those roles reach data through their own cross-practice
    endpoints, not the practice-scoped query path.
    """
    if user.practice_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This operation runs within a practice; caller has none",
        )
    return RequestScope(practice_id=user.practice_id, site_id=user.site_id)
