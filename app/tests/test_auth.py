import pytest
from sqlalchemy.orm import Session

from app import crud
from app.models import Practice, PracticeType, Site, SiteType, User, UserRole

PASSWORD = "correct-horse-battery"


@pytest.fixture
def user(db: Session) -> User:
    practice = Practice(name="Clinic", type=PracticeType.private_practice)
    db.add(practice)
    db.flush()
    return crud.create_user(
        db,
        email="doc@clinic.io",
        password=PASSWORD,
        role=UserRole.clinician,
        practice_id=practice.id,
    )


def _login(client, email: str, password: str):
    return client.post(
        "/auth/login", data={"username": email, "password": password}
    )


def test_login_returns_a_token_pair(client, user: User) -> None:
    resp = _login(client, user.email, PASSWORD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]


def test_login_rejects_a_wrong_password(client, user: User) -> None:
    assert _login(client, user.email, "nope").status_code == 401


def test_login_rejects_an_unknown_email(client, user: User) -> None:
    assert _login(client, "stranger@clinic.io", PASSWORD).status_code == 401


def test_login_rejects_an_inactive_account(client, db: Session, user: User) -> None:
    user.is_active = False
    db.commit()
    assert _login(client, user.email, PASSWORD).status_code == 403


def test_me_returns_the_authenticated_user(client, user: User) -> None:
    token = _login(client, user.email, PASSWORD).json()["access_token"]
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == user.email
    assert body["role"] == "clinician"
    assert body["practice_id"] == user.practice_id
    assert body["practice_name"] == "Clinic"
    assert body["site_id"] is None
    assert body["site_name"] is None


def test_me_includes_the_site_name_when_the_user_has_one(
    client, db: Session, user: User
) -> None:
    site = Site(
        name="Gait Lab", type=SiteType.department, practice_id=user.practice_id
    )
    db.add(site)
    db.flush()
    user.site_id = site.id
    db.commit()

    token = _login(client, user.email, PASSWORD).json()["access_token"]
    body = client.get(
        "/auth/me", headers={"Authorization": f"Bearer {token}"}
    ).json()

    assert body["site_id"] == site.id
    assert body["site_name"] == "Gait Lab"


def test_me_requires_authentication(client, user: User) -> None:
    assert client.get("/auth/me").status_code == 401


def test_refresh_issues_a_working_access_token(client, user: User) -> None:
    refresh_token = _login(client, user.email, PASSWORD).json()["refresh_token"]
    resp = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    new_access = resp.json()["access_token"]
    me = client.get(
        "/auth/me", headers={"Authorization": f"Bearer {new_access}"}
    )
    assert me.status_code == 200


def test_refresh_rejects_an_access_token(client, user: User) -> None:
    access_token = _login(client, user.email, PASSWORD).json()["access_token"]
    resp = client.post("/auth/refresh", json={"refresh_token": access_token})
    assert resp.status_code == 401


# --- session revocation ("sign out everywhere") ----------------------


def test_login_token_carries_the_token_version(client, user: User) -> None:
    from app import security

    tokens = _login(client, user.email, PASSWORD).json()
    access = security.decode_token(
        tokens["access_token"], expected_type=security.ACCESS_TOKEN_TYPE
    )
    refresh = security.decode_token(
        tokens["refresh_token"], expected_type=security.REFRESH_TOKEN_TYPE
    )
    assert access["tv"] == 0 and refresh["tv"] == 0


def test_logout_all_invalidates_every_existing_token(client, user: User) -> None:
    tokens = _login(client, user.email, PASSWORD).json()
    bearer = {"Authorization": f"Bearer {tokens['access_token']}"}

    assert client.post("/auth/logout-all", headers=bearer).status_code == 200

    # the access token that made the call no longer validates
    assert client.get("/auth/me", headers=bearer).status_code == 401
    # nor does the refresh token issued alongside it
    assert (
        client.post(
            "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        ).status_code
        == 401
    )


def test_logout_all_requires_authentication(client, user: User) -> None:
    assert client.post("/auth/logout-all").status_code == 401


def test_a_fresh_login_after_logout_all_works(client, user: User) -> None:
    first = _login(client, user.email, PASSWORD).json()
    client.post(
        "/auth/logout-all",
        headers={"Authorization": f"Bearer {first['access_token']}"},
    )

    second = _login(client, user.email, PASSWORD).json()
    me = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {second['access_token']}"},
    )
    assert me.status_code == 200


# --- self-service patient registration (Section 5.11) -------------------


def test_register_creates_a_patient_login_with_no_practice(client) -> None:
    resp = client.post(
        "/auth/register",
        json={"email": "new.patient@example.co.za", "password": "correct-horse"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["token_type"] == "bearer"

    me = client.get(
        "/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    ).json()
    assert me["role"] == "patient"
    assert me["practice_id"] is None


def test_register_rejects_a_duplicate_email(client, user: User) -> None:
    resp = client.post(
        "/auth/register",
        json={"email": user.email, "password": "correct-horse"},
    )
    assert resp.status_code == 409


def test_register_rejects_a_short_password(client) -> None:
    resp = client.post(
        "/auth/register",
        json={"email": "short@example.co.za", "password": "short"},
    )
    assert resp.status_code == 422
