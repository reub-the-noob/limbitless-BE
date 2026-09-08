from collections.abc import Generator

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import security
from app.audit import AuditRecorder, get_audit_recorder, record
from app.database import get_db
from app.models import (
    AuditAction,
    AuditLogEntry,
    Practice,
    PracticeType,
    User,
    UserRole,
)


@pytest.fixture
def practice(db: Session) -> Practice:
    practice = Practice(name="Clinic", type=PracticeType.private_practice)
    db.add(practice)
    db.flush()
    return practice


@pytest.fixture
def clinician(db: Session, practice: Practice) -> User:
    user = User(
        email="doc@clinic.io",
        hashed_password="x",
        role=UserRole.clinician,
        practice_id=practice.id,
    )
    db.add(user)
    db.flush()
    return user


def _entries(db: Session) -> list[AuditLogEntry]:
    return list(db.scalars(select(AuditLogEntry)))


def test_record_appends_an_entry(db: Session, clinician: User, practice: Practice) -> None:
    record(
        db,
        actor_id=clinician.id,
        action=AuditAction.read,
        entity_type="patient",
        entity_id=5,
        practice_id=practice.id,
    )

    (entry,) = _entries(db)
    assert entry.actor_id == clinician.id
    assert entry.action is AuditAction.read
    assert entry.entity_type == "patient"
    assert entry.entity_id == 5
    assert entry.practice_id == practice.id
    assert entry.timestamp is not None


def test_record_allows_a_null_entity_id(db: Session, clinician: User) -> None:
    record(
        db,
        actor_id=clinician.id,
        action=AuditAction.read,
        entity_type="patient",
    )
    assert _entries(db)[0].entity_id is None


def test_recorder_fills_actor_and_defaults_practice(
    db: Session, clinician: User
) -> None:
    AuditRecorder(db=db, actor=clinician)(AuditAction.update, "patient", 9)

    (entry,) = _entries(db)
    assert entry.actor_id == clinician.id
    assert entry.practice_id == clinician.practice_id
    assert entry.action is AuditAction.update


def test_recorder_practice_can_be_overridden(db: Session, clinician: User) -> None:
    AuditRecorder(db=db, actor=clinician)(
        AuditAction.read, "patient", 9, practice_id=999
    )
    assert _entries(db)[0].practice_id == 999


def test_get_audit_recorder_dependency_records_the_caller(
    db: Session, clinician: User
) -> None:
    app = FastAPI()

    @app.post("/touch/{patient_id}")
    def touch(
        patient_id: int, recorder: AuditRecorder = Depends(get_audit_recorder)
    ) -> dict[str, bool]:
        recorder(AuditAction.read, "patient", patient_id)
        recorder.db.commit()
        return {"ok": True}

    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)

    token = security.create_access_token(
        clinician.id,
        role=clinician.role.value,
        practice_id=clinician.practice_id,
        site_id=clinician.site_id,
    )
    resp = client.post(
        "/touch/7", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200

    (entry,) = _entries(db)
    assert entry.actor_id == clinician.id
    assert entry.entity_type == "patient"
    assert entry.entity_id == 7
