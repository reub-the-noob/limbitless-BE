from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app import security
from app.models import AuditAction, AuditLogEntry, User
from app.tests.conftest import World


def auth(user: User) -> dict[str, str]:
    token = security.create_access_token(
        user.id,
        role=user.role.value,
        practice_id=user.practice_id,
        site_id=user.site_id,
    )
    return {"Authorization": f"Bearer {token}"}


def entry(
    db: Session,
    *,
    actor: User | None,
    practice_id: int | None,
    action: AuditAction = AuditAction.create,
    entity_type: str = "patient",
    entity_id: int | None = 1,
    days_ago: int = 0,
) -> AuditLogEntry:
    row = AuditLogEntry(
        actor_id=actor.id if actor else None,
        practice_id=practice_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        timestamp=datetime.now() - timedelta(days=days_ago),
    )
    db.add(row)
    db.commit()
    return row


def audit(client, user: User, **params):
    return client.get("/admin/audit", params=params, headers=auth(user))


def test_lists_the_practices_trail_newest_first(
    client, db: Session, world: World
) -> None:
    entry(db, actor=world.clinician_a, practice_id=world.practice_a.id, days_ago=2)
    entry(
        db,
        actor=world.prosthetist_a,
        practice_id=world.practice_a.id,
        action=AuditAction.update,
        entity_type="device",
        days_ago=1,
    )
    entry(db, actor=world.clinician_b, practice_id=world.practice_b.id)

    body = audit(client, world.admin_a).json()
    assert body["total"] == 2
    assert [e["entity_type"] for e in body["items"]] == ["device", "patient"]
    assert body["items"][0]["actor_email"] == world.prosthetist_a.email


def test_filters_by_actor_action_and_entity_type(
    client, db: Session, world: World
) -> None:
    entry(db, actor=world.clinician_a, practice_id=world.practice_a.id, entity_type="patient")
    entry(
        db,
        actor=world.clinician_a,
        practice_id=world.practice_a.id,
        action=AuditAction.read,
        entity_type="dashboard",
    )
    entry(db, actor=world.prosthetist_a, practice_id=world.practice_a.id, entity_type="device")

    assert audit(client, world.admin_a, actor_id=world.clinician_a.id).json()["total"] == 2
    assert audit(client, world.admin_a, action="read").json()["total"] == 1
    assert audit(client, world.admin_a, entity_type="device").json()["total"] == 1


def test_filters_by_date_range_inclusive(
    client, db: Session, world: World
) -> None:
    entry(db, actor=world.clinician_a, practice_id=world.practice_a.id, days_ago=10)
    entry(db, actor=world.clinician_a, practice_id=world.practice_a.id, days_ago=1)

    today = datetime.now().date()
    only_recent = audit(
        client,
        world.admin_a,
        date_from=(today - timedelta(days=3)).isoformat(),
    ).json()
    assert only_recent["total"] == 1

    upto = audit(
        client,
        world.admin_a,
        date_to=(today - timedelta(days=5)).isoformat(),
    ).json()
    assert upto["total"] == 1


def test_actor_email_is_null_when_the_user_is_gone(
    client, db: Session, world: World
) -> None:
    entry(db, actor=None, practice_id=world.practice_a.id, entity_type="note")
    (item,) = audit(client, world.admin_a).json()["items"]
    assert item["actor_id"] is None
    assert item["actor_email"] is None


def test_paging(client, db: Session, world: World) -> None:
    for i in range(5):
        entry(
            db,
            actor=world.clinician_a,
            practice_id=world.practice_a.id,
            entity_id=i,
            days_ago=i,
        )
    page = audit(client, world.admin_a, limit=2, offset=2).json()
    assert page["total"] == 5
    assert len(page["items"]) == 2
    assert page["limit"] == 2 and page["offset"] == 2


def test_facets_are_practice_scoped(client, db: Session, world: World) -> None:
    entry(db, actor=world.clinician_a, practice_id=world.practice_a.id, entity_type="patient")
    entry(db, actor=world.prosthetist_a, practice_id=world.practice_a.id, entity_type="device")
    entry(db, actor=world.clinician_b, practice_id=world.practice_b.id, entity_type="milestone")

    facets = client.get(
        "/admin/audit/facets", headers=auth(world.admin_a)
    ).json()
    assert set(facets["entity_types"]) == {"patient", "device"}
    assert {a["email"] for a in facets["actors"]} == {
        world.clinician_a.email,
        world.prosthetist_a.email,
    }


def test_only_a_practice_administrator_may_read(
    client, db: Session, world: World
) -> None:
    entry(db, actor=world.clinician_a, practice_id=world.practice_a.id)
    assert audit(client, world.clinician_a).status_code == 403
    assert audit(client, world.platform_admin).status_code == 403
    assert audit(client, world.admin_a).status_code == 200
