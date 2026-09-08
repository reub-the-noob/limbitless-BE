from sqlalchemy import select
from sqlalchemy.orm import Session

from app import security
from app.models import AuditLogEntry, User
from app.tests.conftest import World

PW = "correct-horse-8"


def auth(user: User) -> dict[str, str]:
    token = security.create_access_token(
        user.id,
        role=user.role.value,
        practice_id=user.practice_id,
        site_id=user.site_id,
    )
    return {"Authorization": f"Bearer {token}"}


def new_user(**overrides) -> dict[str, object]:
    body: dict[str, object] = {
        "email": "new.clinician@northgate.co.za",
        "password": PW,
        "role": "clinician",
    }
    body.update(overrides)
    return body


def create(client, world: World, **overrides):
    return client.post(
        "/admin/users", json=new_user(**overrides), headers=auth(world.admin_a)
    )


def user_audit(db: Session) -> list[AuditLogEntry]:
    return list(
        db.scalars(
            select(AuditLogEntry).where(AuditLogEntry.entity_type == "user")
        )
    )


# --- list -------------------------------------------------------------------


def test_list_is_scoped_to_the_admins_practice(client, world: World) -> None:
    body = client.get("/admin/users", headers=auth(world.admin_a)).json()
    emails = {u["email"] for u in body["items"]}
    assert emails == {"clin.a@x.io", "pros.a@x.io", "admin.a@x.io"}
    assert body["total"] == 3


def test_list_resolves_practice_and_site_names(client, world: World) -> None:
    body = client.get("/admin/users", headers=auth(world.admin_a)).json()
    clinician = next(u for u in body["items"] if u["email"] == "clin.a@x.io")
    assert clinician["practice_name"] == world.practice_a.name
    assert clinician["site_name"] == world.site_a1.name


def test_list_filters(client, world: World) -> None:
    by_role = client.get(
        "/admin/users", params={"role": "clinician"}, headers=auth(world.admin_a)
    ).json()
    assert [u["email"] for u in by_role["items"]] == ["clin.a@x.io"]

    by_email = client.get(
        "/admin/users", params={"q": "pros"}, headers=auth(world.admin_a)
    ).json()
    assert [u["email"] for u in by_email["items"]] == ["pros.a@x.io"]

    created = create(client, world).json()
    client.patch(
        f"/admin/users/{created['id']}",
        json={"is_active": False},
        headers=auth(world.admin_a),
    )
    inactive = client.get(
        "/admin/users", params={"active": "false"}, headers=auth(world.admin_a)
    ).json()
    assert [u["id"] for u in inactive["items"]] == [created["id"]]


# --- create ------------------------------------------------------------


def test_create_clinician(client, db: Session, world: World) -> None:
    resp = create(client, world)

    assert resp.status_code == 201
    body = resp.json()
    assert body["role"] == "clinician"
    assert body["practice_id"] == world.practice_a.id
    assert user_audit(db)[0].entity_id == body["id"]

    login = client.post(
        "/auth/login",
        data={"username": body["email"], "password": PW},
    )
    assert login.status_code == 200


def test_create_rejects_non_practice_roles(client, world: World) -> None:
    assert create(client, world, role="platform_administrator").status_code == 400
    assert create(client, world, role="patient").status_code == 400


def test_create_duplicate_email_conflicts(client, world: World) -> None:
    assert create(client, world, email="clin.a@x.io").status_code == 409


def test_create_rejects_a_foreign_site(client, db: Session, world: World) -> None:
    from app.models import Site, SiteType

    other = Site(
        name="B Rooms", type=SiteType.location, practice_id=world.practice_b.id
    )
    db.add(other)
    db.commit()
    assert create(client, world, site_id=other.id).status_code == 400


def test_create_rejects_a_short_password(client, world: World) -> None:
    assert create(client, world, password="short").status_code == 422


# --- read / scope --------------------------------------------------------


def test_read_user(client, world: World) -> None:
    created = create(client, world).json()
    resp = client.get(
        f"/admin/users/{created['id']}", headers=auth(world.admin_a)
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == created["email"]


def test_user_from_another_practice_is_404(client, world: World) -> None:
    for path in (
        f"/admin/users/{world.clinician_b.id}",
    ):
        assert client.get(path, headers=auth(world.admin_a)).status_code == 404
    assert (
        client.patch(
            f"/admin/users/{world.clinician_b.id}",
            json={"role": "prosthetist"},
            headers=auth(world.admin_a),
        ).status_code
        == 404
    )


# --- update --------------------------------------------------------------


def test_patch_role_and_audit(client, db: Session, world: World) -> None:
    resp = client.patch(
        f"/admin/users/{world.clinician_a.id}",
        json={"role": "prosthetist"},
        headers=auth(world.admin_a),
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "prosthetist"
    assert any(r.entity_id == world.clinician_a.id for r in user_audit(db))


def test_deactivating_a_user_blocks_their_login(client, world: World) -> None:
    client.patch(
        f"/admin/users/{world.clinician_a.id}",
        json={"is_active": False},
        headers=auth(world.admin_a),
    )
    login = client.post(
        "/auth/login", data={"username": "clin.a@x.io", "password": "pw"}
    )
    assert login.status_code == 403


def test_admin_cannot_deactivate_self(client, world: World) -> None:
    resp = client.patch(
        f"/admin/users/{world.admin_a.id}",
        json={"is_active": False},
        headers=auth(world.admin_a),
    )
    assert resp.status_code == 403


def test_admin_cannot_drop_own_admin_role(client, world: World) -> None:
    resp = client.patch(
        f"/admin/users/{world.admin_a.id}",
        json={"role": "clinician"},
        headers=auth(world.admin_a),
    )
    assert resp.status_code == 403


def test_admin_can_edit_own_non_critical_fields(client, world: World) -> None:
    resp = client.patch(
        f"/admin/users/{world.admin_a.id}",
        json={"site_id": world.site_a1.id},
        headers=auth(world.admin_a),
    )
    assert resp.status_code == 200
    assert resp.json()["site_id"] == world.site_a1.id


# --- password -----------------------------------------------------------


def test_set_password(client, world: World) -> None:
    client.post(
        f"/admin/users/{world.clinician_a.id}/set-password",
        json={"password": "brand-new-secret"},
        headers=auth(world.admin_a),
    )
    assert (
        client.post(
            "/auth/login", data={"username": "clin.a@x.io", "password": "pw"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/auth/login",
            data={"username": "clin.a@x.io", "password": "brand-new-secret"},
        ).status_code
        == 200
    )


# --- access -----------------------------------------------------------------


def test_non_admins_are_forbidden(client, world: World) -> None:
    assert (
        client.get("/admin/users", headers=auth(world.clinician_a)).status_code
        == 403
    )
    assert (
        client.get("/admin/users", headers=auth(world.platform_admin)).status_code
        == 403
    )


def test_requires_authentication(client, world: World) -> None:
    assert client.get("/admin/users").status_code == 401
