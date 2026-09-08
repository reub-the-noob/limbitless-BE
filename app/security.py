"""Password hashing and JWT encode/decode.

Deliberately free of FastAPI imports so it stays unit-testable on its
own; the request-facing dependencies live in :mod:`app.deps`.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app import config

ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"


def hash_password(password: str) -> str:
    """Hash a password with bcrypt.

    bcrypt only considers the first 72 bytes of the input; user-facing
    length limits are enforced at the schema layer where passwords are
    set.
    """
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def _create_token(
    subject: Any,
    token_type: str,
    expires_delta: timedelta,
    extra: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, config.JWT_SECRET_KEY, algorithm=config.JWT_ALGORITHM)


def create_access_token(
    subject: Any,
    *,
    role: str,
    practice_id: int | None,
    site_id: int | None,
) -> str:
    return _create_token(
        subject,
        ACCESS_TOKEN_TYPE,
        timedelta(minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES),
        {"role": role, "practice_id": practice_id, "site_id": site_id},
    )


def create_refresh_token(subject: Any) -> str:
    return _create_token(
        subject,
        REFRESH_TOKEN_TYPE,
        timedelta(days=config.REFRESH_TOKEN_EXPIRE_DAYS),
    )


def decode_token(token: str, *, expected_type: str) -> dict[str, Any]:
    """Decode and validate a token.

    Raises ``jwt.InvalidTokenError`` (or a subclass) on a bad signature,
    an expired token, or a token whose ``type`` claim doesn't match.
    """
    payload = jwt.decode(
        token, config.JWT_SECRET_KEY, algorithms=[config.JWT_ALGORITHM]
    )
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(f"expected a {expected_type} token")
    return payload
