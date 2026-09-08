from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crud, security
from app.models import AuditAction, AuditLogEntry, User, UserRole
from app.tests.conftest import World


def auth(user: User) -> dict[str, str]:
    token = security.create_access_token(
        user.id,
        role=user.role.value,
        practice_id=user.practice_id,
        site_id=user.site_id,
    )
    return {"Authorization": f"Bearer {token}"}


def make_patient(client, world: World, **overrides) -> dict:
    body = {
        "first_name": "Pat",
        "last_name": "One",
        "date_of_birth": "1990-01-01",
        "national_id": "9001010000001",
        "site_id": world.site_a1.id,
    }
    body.update(overrides)
    resp = client.post(
        "/patients", json=body, headers=auth(world.clinician_a)
    )
    assert resp.status_code == 201
    return resp.json()


def reviewer(db: Session, email="rev@medscheme.test") -> User:
    return crud.create_user(
        db, email=email, password="pw", role=UserRole.medical_aid_reviewer
    )


def grant(client, world: World, patient_id: int, rev: User):
    return client.post(
        f"/patients/{patient_id}/review-access",
        json={"reviewer_id": rev.id},
        headers=auth(world.clinician_a),
    )


# --- granting --------------------------------------------------------


def test_grant_and_list_and_revoke(client, db: Session, world: World) -> None:
    patient = make_patient(client, world)
    rev = reviewer(db)

    resp = grant(client, world, patient["id"], rev)
    assert resp.status_code == 201
    assert resp.json()["reviewer_email"] == rev.email

    listed = client.get(
        f"/patients/{patient['id']}/review-access",
        headers=auth(world.clinician_a),
    ).json()
    assert [g["reviewer_id"] for g in listed] == [rev.id]

    assert grant(client, world, patient["id"], rev).status_code == 409

    revoked = client.delete(
        f"/patients/{patient['id']}/review-access/{rev.id}",
        headers=auth(world.clinician_a),
    )
    assert revoked.status_code == 204
    assert (
        client.get("/review/patients", headers=auth(rev)).json() == []
    )


def test_grant_rejects_a_non_reviewer(client, db: Session, world: World) -> None:
    patient = make_patient(client, world)
    resp = client.post(
        f"/patients/{patient['id']}/review-access",
        json={"reviewer_id": world.clinician_a.id},
        headers=auth(world.clinician_a),
    )
    assert resp.status_code == 400


# --- the reviewer's view ------------------------------------------


def test_reviewer_only_sees_granted_patients(
    client, db: Session, world: World
) -> None:
    shared = make_patient(client, world, national_id="1")
    hidden = make_patient(client, world, national_id="2")
    rev = reviewer(db)
    grant(client, world, shared["id"], rev)

    rows = client.get("/review/patients", headers=auth(rev)).json()
    assert [r["id"] for r in rows] == [shared["id"]]
    assert rows[0]["practice_name"] == world.practice_a.name

    assert (
        client.get(
            f"/review/patients/{shared['id']}", headers=auth(rev)
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/review/patients/{hidden['id']}", headers=auth(rev)
        ).status_code
        == 404
    )


def test_review_bundle_has_the_record_and_audits_the_read(
    client, db: Session, world: World
) -> None:
    patient = make_patient(client, world)
    rev = reviewer(db)
    grant(client, world, patient["id"], rev)
    client.post(
        f"/patients/{patient['id']}/involvements",
        json={"kind": "orthotic_need", "region": "spine"},
        headers=auth(world.clinician_a),
    )

    bundle = client.get(
        f"/review/patients/{patient['id']}", headers=auth(rev)
    ).json()
    assert bundle["patient"]["id"] == patient["id"]
    assert bundle["practice_name"] == world.practice_a.name
    assert len(bundle["involvements"]) == 1
    assert bundle["involvements"][0]["devices"] == []
    assert bundle["milestones"] == [] and bundle["proms"] == []

    (entry,) = db.scalars(
        select(AuditLogEntry).where(
            AuditLogEntry.action == AuditAction.read,
            AuditLogEntry.entity_type == "patient",
            AuditLogEntry.actor_id == rev.id,
        )
    ).all()
    assert entry.practice_id == world.practice_a.id


def test_staff_cannot_use_the_review_endpoints(client, world: World) -> None:
    assert (
        client.get("/review/patients", headers=auth(world.clinician_a)).status_code
        == 403
    )
    assert (
        client.get(
            "/review/patients", headers=auth(world.platform_admin)
        ).status_code
        == 403
    )


def test_reviewer_cannot_write(client, db: Session, world: World) -> None:
    patient = make_patient(client, world)
    rev = reviewer(db)
    grant(client, world, patient["id"], rev)
    # no such endpoint / method for a reviewer
    assert (
        client.post(
            f"/patients/{patient['id']}/involvements",
            json={"kind": "amputation", "region": "lower_limb_left"},
            headers=auth(rev),
        ).status_code
        == 403
    )
