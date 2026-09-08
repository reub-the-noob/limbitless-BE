from collections.abc import Generator

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import security
from app.database import get_db
from app.deps import get_current_user, get_request_scope, require_roles
from app.models import Practice, PracticeType, Site, SiteType, User, UserRole


@pytest.fixture
def api(db: Session) -> Generator[tuple[TestClient, dict[str, User]], None, None]:
    practice = Practice(name="P", type=PracticeType.private_practice)
    db.add(practice)
    db.flush()
    site = Site(name="S", type=SiteType.location, practice_id=practice.id)
    db.add(site)
    db.flush()
    users = {
        "clinician": User(
            email="c@x.io", hashed_password="x", role=UserRole.clinician,
            practice_id=practice.id, site_id=site.id,
        ),
        "prosthetist": User(
            email="p@x.io", hashed_password="x", role=UserRole.prosthetist,
            practice_id=practice.id, site_id=site.id,
        ),
        "platform": User(
            email="a@x.io", hashed_password="x",
            role=UserRole.platform_administrator,
        ),
        "inactive": User(
            email="i@x.io", hashed_password="x", role=UserRole.clinician,
            practice_id=practice.id, is_active=False,
        ),
    }
    db.add_all(users.values())
    db.flush()

    app = FastAPI()

    @app.get("/me")
    def me(user: User = Depends(get_current_user)) -> dict[str, object]:
        return {"id": user.id, "role": user.role.value}

    @app.get("/clinician-only")
    def clinician_only(
        user: User = Depends(require_roles(UserRole.clinician)),
    ) -> dict[str, bool]:
        return {"ok": True}

    @app.get("/scope")
    def scope(scope=Depends(get_request_scope)) -> dict[str, object]:
        return {"practice_id": scope.practice_id, "site_id": scope.site_id}

    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app), users


def _auth(user: User) -> dict[str, str]:
    token = security.create_access_token(
        user.id,
        role=user.role.value,
        practice_id=user.practice_id,
        site_id=user.site_id,
    )
    return {"Authorization": f"Bearer {token}"}


def test_me_requires_a_token(api) -> None:
    client, _ = api
    assert client.get("/me").status_code == 401


def test_me_accepts_a_valid_access_token(api) -> None:
    client, users = api
    resp = client.get("/me", headers=_auth(users["clinician"]))
    assert resp.status_code == 200
    assert resp.json()["role"] == "clinician"


def test_me_rejects_a_refresh_token(api) -> None:
    client, users = api
    token = security.create_refresh_token(users["clinician"].id)
    resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_me_rejects_an_inactive_user(api) -> None:
    client, users = api
    assert client.get("/me", headers=_auth(users["inactive"])).status_code == 401


def test_require_roles_allows_the_matching_role(api) -> None:
    client, users = api
    assert client.get(
        "/clinician-only", headers=_auth(users["clinician"])
    ).status_code == 200


def test_require_roles_forbids_another_role(api) -> None:
    client, users = api
    assert client.get(
        "/clinician-only", headers=_auth(users["prosthetist"])
    ).status_code == 403


def test_request_scope_exposes_practice_and_site(api) -> None:
    client, users = api
    resp = client.get("/scope", headers=_auth(users["clinician"]))
    assert resp.status_code == 200
    assert resp.json() == {
        "practice_id": users["clinician"].practice_id,
        "site_id": users["clinician"].site_id,
    }


def test_request_scope_forbids_a_user_without_a_practice(api) -> None:
    client, users = api
    assert client.get("/scope", headers=_auth(users["platform"])).status_code == 403
