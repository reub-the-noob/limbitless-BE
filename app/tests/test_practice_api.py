from sqlalchemy.orm import Session

from app import crud, security
from app.models import User, UserRole
from app.tests.conftest import World


def auth(user: User) -> dict[str, str]:
    token = security.create_access_token(
        user.id,
        role=user.role.value,
        practice_id=user.practice_id,
        site_id=user.site_id,
    )
    return {"Authorization": f"Bearer {token}"}


def staff(client, user: User):
    return client.get("/practice/clinical-staff", headers=auth(user))


def test_lists_active_clinical_staff_for_the_callers_practice(
    client, world: World
) -> None:
    resp = staff(client, world.clinician_a)
    assert resp.status_code == 200

    rows = resp.json()
    assert [r["email"] for r in rows] == [
        world.clinician_a.email,
        world.prosthetist_a.email,
    ]
    assert {r["role"] for r in rows} == {"clinician", "prosthetist"}
    by_email = {r["email"]: r for r in rows}
    assert by_email[world.clinician_a.email]["site_name"] == world.site_a1.name
    assert by_email[world.prosthetist_a.email]["site_name"] is None


def test_excludes_admins_other_practices_and_inactive_users(
    client, db: Session, world: World
) -> None:
    crud.create_user(
        db,
        email="retired.a@x.io",
        password="pw",
        role=UserRole.clinician,
        practice_id=world.practice_a.id,
    )
    retired = crud.get_user_by_email(db, "retired.a@x.io")
    retired.is_active = False
    db.commit()

    emails = {r["email"] for r in staff(client, world.clinician_a).json()}

    assert world.admin_a.email not in emails  # practice administrator
    assert world.clinician_b.email not in emails  # other practice
    assert "retired.a@x.io" not in emails  # inactive


def test_practice_administrator_may_read_the_roster(client, world: World) -> None:
    resp = staff(client, world.admin_a)
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_platform_administrator_has_no_practice_roster(
    client, world: World
) -> None:
    assert staff(client, world.platform_admin).status_code == 403


def test_requires_authentication(client) -> None:
    assert client.get("/practice/clinical-staff").status_code == 401
