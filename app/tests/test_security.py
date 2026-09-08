import jwt
import pytest

from app import config, security


def test_hash_password_round_trips() -> None:
    hashed = security.hash_password("s3cret-pw")
    assert hashed != "s3cret-pw"
    assert security.verify_password("s3cret-pw", hashed) is True


def test_verify_password_rejects_wrong_password() -> None:
    hashed = security.hash_password("s3cret-pw")
    assert security.verify_password("wrong", hashed) is False


def test_hashes_are_salted() -> None:
    a = security.hash_password("same")
    b = security.hash_password("same")
    assert a != b
    assert security.verify_password("same", a)
    assert security.verify_password("same", b)


def test_access_token_carries_role_and_scope() -> None:
    token = security.create_access_token(
        42, role="clinician", practice_id=7, site_id=3
    )
    payload = security.decode_token(token, expected_type="access")
    assert payload["sub"] == "42"
    assert payload["role"] == "clinician"
    assert payload["practice_id"] == 7
    assert payload["site_id"] == 3


def test_refresh_token_round_trips() -> None:
    token = security.create_refresh_token(42)
    payload = security.decode_token(token, expected_type="refresh")
    assert payload["sub"] == "42"
    assert payload["type"] == "refresh"


def test_decode_rejects_mismatched_type() -> None:
    refresh = security.create_refresh_token(1)
    with pytest.raises(jwt.InvalidTokenError):
        security.decode_token(refresh, expected_type="access")


def test_decode_rejects_tampered_token() -> None:
    token = security.create_refresh_token(1)
    with pytest.raises(jwt.InvalidTokenError):
        security.decode_token(token + "x", expected_type="refresh")


def test_decode_rejects_expired_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "ACCESS_TOKEN_EXPIRE_MINUTES", -1)
    token = security.create_access_token(
        1, role="clinician", practice_id=1, site_id=None
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        security.decode_token(token, expected_type="access")
