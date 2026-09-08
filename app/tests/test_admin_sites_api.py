from sqlalchemy import select
from sqlalchemy.orm import Session

from app import security
from app.models import AuditLogEntry, Site, SiteType, User
from app.tests.conftest import World


def auth(user: User) -> dict[str, str]:
    token = security.create_access_token(
        user.id,
        role=user.role.value,
        practice_id=user.practice_id,
        site_id=user.site_id,
    )
    return {"Authorization": f"Bearer {token}"}


def site_audit(db: Session) -> list[AuditLogEntry]:
    return list(
        db.scalars(
            select(AuditLogEntry).where(AuditLogEntry.entity_type == "site")
        )
    )


def test_list_is_scoped_to_the_admins_practice(client, db: Session, world: World) -> None:
    db.add(
        Site(name="B Rooms", type=SiteType.location, practice_id=world.practice_b.id)
    )
    db.commit()

    rows = client.get("/admin/sites", headers=auth(world.admin_a)).json()
    assert [s["name"] for s in rows] == ["A Rooms"]
    assert rows[0]["practice_id"] == world.practice_a.id


def test_list_type_filter(client, world: World) -> None:
    client.post(
        "/admin/sites",
        json={"name": "Gait Lab", "type": "department"},
        headers=auth(world.admin_a),
    )
    depts = client.get(
        "/admin/sites", params={"type": "department"}, headers=auth(world.admin_a)
    ).json()
    assert [s["name"] for s in depts] == ["Gait Lab"]


def test_create_site(client, db: Session, world: World) -> None:
    resp = client.post(
        "/admin/sites",
        json={"name": "Northgate Annex", "type": "location", "address": "2 Way St"},
        headers=auth(world.admin_a),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["practice_id"] == world.practice_a.id
    assert body["address"] == "2 Way St"
    assert [r.entity_id for r in site_audit(db)] == [body["id"]]


def test_create_requires_practice_admin(client, world: World) -> None:
    payload = {"name": "X", "type": "location"}
    assert (
        client.post(
            "/admin/sites", json=payload, headers=auth(world.clinician_a)
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/admin/sites", json=payload, headers=auth(world.platform_admin)
        ).status_code
        == 403
    )
    assert client.post("/admin/sites", json=payload).status_code == 401


def test_read_site_and_cross_practice_404(client, db: Session, world: World) -> None:
    other = Site(
        name="B Rooms", type=SiteType.location, practice_id=world.practice_b.id
    )
    db.add(other)
    db.commit()

    assert (
        client.get(
            f"/admin/sites/{world.site_a1.id}", headers=auth(world.admin_a)
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/admin/sites/{other.id}", headers=auth(world.admin_a)
        ).status_code
        == 404
    )


def test_patch_site_and_audit(client, db: Session, world: World) -> None:
    resp = client.patch(
        f"/admin/sites/{world.site_a1.id}",
        json={"name": "A Rooms (Level 2)", "type": "department"},
        headers=auth(world.admin_a),
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "A Rooms (Level 2)"
    assert resp.json()["type"] == "department"
    assert any(r.entity_id == world.site_a1.id for r in site_audit(db))


def test_patch_cross_practice_site_is_404(client, db: Session, world: World) -> None:
    other = Site(
        name="B Rooms", type=SiteType.location, practice_id=world.practice_b.id
    )
    db.add(other)
    db.commit()

    assert (
        client.patch(
            f"/admin/sites/{other.id}",
            json={"name": "hax"},
            headers=auth(world.admin_a),
        ).status_code
        == 404
    )
