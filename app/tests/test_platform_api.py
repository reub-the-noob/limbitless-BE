from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import security
from app.models import AuditLogEntry, Practice, User, UserRole
from app.tests.conftest import World

PW = "onboard-pass-1"


def auth(user: User) -> dict[str, str]:
    token = security.create_access_token(
        user.id,
        role=user.role.value,
        practice_id=user.practice_id,
        site_id=user.site_id,
    )
    return {"Authorization": f"Bearer {token}"}


def onboard_body(**overrides) -> dict:
    body = {
        "practice": {"name": "Cape Prosthetics", "type": "private_practice"},
        "first_site": {"name": "Sea Point Rooms", "type": "location"},
        "first_admin": {"email": "lead@capeprosthetics.co.za", "password": PW},
    }
    body.update(overrides)
    return body


def onboard(client, world: World, **overrides):
    return client.post(
        "/platform/practices",
        json=onboard_body(**overrides),
        headers=auth(world.platform_admin),
    )


# --- onboarding -----------------------------------------------------------


def test_onboard_creates_practice_site_and_admin(
    client, db: Session, world: World
) -> None:
    resp = onboard(client, world)

    assert resp.status_code == 201
    body = resp.json()
    assert body["practice"]["name"] == "Cape Prosthetics"
    admin = body["first_admin"]
    assert admin["role"] == "practice_administrator"
    assert admin["practice_id"] == body["practice"]["id"]
    assert admin["site_id"] == body["first_site"]["id"]

    login = client.post(
        "/auth/login", data={"username": admin["email"], "password": PW}
    )
    assert login.status_code == 200

    kinds = {
        r.entity_type for r in db.scalars(select(AuditLogEntry)) if r.entity_type
    }
    assert {"practice", "user"} <= kinds


def test_onboard_with_a_taken_email_rolls_back(client, db: Session, world: World) -> None:
    before = db.scalar(select(func.count()).select_from(Practice))

    resp = onboard(client, world, first_admin={"email": "clin.a@x.io", "password": PW})

    assert resp.status_code == 409
    assert db.scalar(select(func.count()).select_from(Practice)) == before


def test_onboard_validation(client, world: World) -> None:
    assert onboard(
        client, world, practice={"name": "X", "type": "nonsense"}
    ).status_code == 422
    assert onboard(
        client, world, first_admin={"email": "a@b.co.za", "password": "short"}
    ).status_code == 422


# --- listing / detail ---------------------------------------------------


def test_list_practices_with_counts(client, world: World) -> None:
    client.post(
        "/patients",
        json={
            "first_name": "P",
            "last_name": "Q",
            "date_of_birth": "1990-01-01",
            "national_id": "1",
        },
        headers=auth(world.clinician_a),
    )

    body = client.get(
        "/platform/practices", headers=auth(world.platform_admin)
    ).json()
    by_name = {p["name"]: p for p in body["items"]}
    assert by_name["Practice A"]["user_count"] == 3
    assert by_name["Practice A"]["site_count"] == 1
    assert by_name["Practice A"]["patient_count"] == 1
    assert by_name["Practice B"]["user_count"] == 1
    assert by_name["Practice B"]["patient_count"] == 0


def test_list_search(client, world: World) -> None:
    body = client.get(
        "/platform/practices",
        params={"q": "actice b"},
        headers=auth(world.platform_admin),
    ).json()
    assert [p["name"] for p in body["items"]] == ["Practice B"]


def test_practice_detail_lists_sites(client, world: World) -> None:
    resp = client.get(
        f"/platform/practices/{world.practice_a.id}",
        headers=auth(world.platform_admin),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [s["name"] for s in body["sites"]] == ["A Rooms"]
    assert body["site_count"] == 1


def test_practice_detail_404(client, world: World) -> None:
    assert (
        client.get(
            "/platform/practices/9999", headers=auth(world.platform_admin)
        ).status_code
        == 404
    )


def test_patch_practice(client, db: Session, world: World) -> None:
    resp = client.patch(
        f"/platform/practices/{world.practice_a.id}",
        json={"name": "Practice A (renamed)"},
        headers=auth(world.platform_admin),
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Practice A (renamed)"
    assert any(
        r.entity_type == "practice" and r.entity_id == world.practice_a.id
        for r in db.scalars(select(AuditLogEntry))
    )


# --- admins -----------------------------------------------------------------


def test_add_recovery_practice_admin(client, world: World) -> None:
    resp = client.post(
        f"/platform/practices/{world.practice_a.id}/admins",
        json={"email": "backup.admin@practice-a.co.za", "password": PW},
        headers=auth(world.platform_admin),
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "practice_administrator"
    assert resp.json()["practice_id"] == world.practice_a.id
    assert (
        client.post(
            "/auth/login",
            data={"username": "backup.admin@practice-a.co.za", "password": PW},
        ).status_code
        == 200
    )


def test_add_practice_admin_duplicate_email(client, world: World) -> None:
    assert (
        client.post(
            f"/platform/practices/{world.practice_a.id}/admins",
            json={"email": "clin.a@x.io", "password": PW},
            headers=auth(world.platform_admin),
        ).status_code
        == 409
    )


def test_add_practice_admin_missing_practice(client, world: World) -> None:
    assert (
        client.post(
            "/platform/practices/9999/admins",
            json={"email": "x@y.co.za", "password": PW},
            headers=auth(world.platform_admin),
        ).status_code
        == 404
    )


def test_create_platform_admin(client, world: World) -> None:
    resp = client.post(
        "/platform/admins",
        json={"email": "ops@limbitless.co.za", "password": PW},
        headers=auth(world.platform_admin),
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "platform_administrator"
    assert resp.json()["practice_id"] is None

    token = client.post(
        "/auth/login",
        data={"username": "ops@limbitless.co.za", "password": PW},
    ).json()["access_token"]
    assert (
        client.get(
            "/platform/practices", headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 200
    )


# --- access -----------------------------------------------------------------


def test_non_platform_admins_are_forbidden(client, world: World) -> None:
    for user in (world.admin_a, world.clinician_a):
        assert (
            client.get(
                "/platform/practices", headers=auth(user)
            ).status_code
            == 403
        )


def test_requires_authentication(client, world: World) -> None:
    assert client.get("/platform/practices").status_code == 401
