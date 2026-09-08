from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Patient,
    Practice,
    PracticeType,
    Site,
    SiteType,
)
from app.scoping import RequestScope, apply_scope, scope_to_practice, scope_to_site


def _practice(db: Session, name: str = "Northside Ortho") -> Practice:
    practice = Practice(name=name, type=PracticeType.private_practice)
    db.add(practice)
    db.flush()
    return practice


def _patient(db: Session, practice: Practice, **overrides) -> Patient:
    fields = {
        "first_name": "Thandeka",
        "last_name": "Mokoena",
        "date_of_birth": date(1985, 4, 12),
        "national_id": "8504125800086",
        "practice_id": practice.id,
    }
    fields.update(overrides)
    patient = Patient(**fields)
    db.add(patient)
    db.flush()
    return patient


def test_create_patient_sets_defaults(db: Session) -> None:
    practice = _practice(db)
    patient = _patient(db, practice, comorbidities="Type 2 diabetes")
    db.refresh(patient)

    assert patient.id is not None
    assert patient.is_active is True
    assert patient.site_id is None
    assert patient.comorbidities == "Type 2 diabetes"
    assert patient.created_at is not None


def test_identity_key_is_required(db: Session) -> None:
    practice = _practice(db)
    db.add(
        Patient(
            first_name="No",
            last_name="Id",
            date_of_birth=date(2000, 1, 1),
            practice_id=practice.id,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_passport_alone_satisfies_the_identity_key(db: Session) -> None:
    practice = _practice(db)
    patient = _patient(
        db, practice, national_id=None, passport_number="A01234567"
    )
    assert patient.id is not None


def test_national_id_is_unique_within_a_practice(db: Session) -> None:
    practice = _practice(db)
    _patient(db, practice, national_id="8001015800080")

    db.add(
        Patient(
            first_name="Thandeka",
            last_name="Other",
            date_of_birth=date(2000, 1, 1),
            national_id="8001015800080",
            practice_id=practice.id,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_same_national_id_is_allowed_at_a_different_practice(db: Session) -> None:
    practice_a = _practice(db, "Practice A")
    practice_b = _practice(db, "Practice B")

    _patient(db, practice_a, national_id="9001015800089")
    _patient(db, practice_b, national_id="9001015800089")
    db.flush()

    assert db.scalar(select(Patient).where(Patient.practice_id == practice_a.id))
    assert db.scalar(select(Patient).where(Patient.practice_id == practice_b.id))


def test_scoping_helpers_filter_patients_by_practice_and_site(db: Session) -> None:
    practice_a = _practice(db, "A")
    practice_b = _practice(db, "B")
    site_1 = Site(name="Rooms", type=SiteType.location, practice_id=practice_a.id)
    site_2 = Site(name="Gait Lab", type=SiteType.department, practice_id=practice_a.id)
    db.add_all([site_1, site_2])
    db.flush()

    _patient(db, practice_a, national_id="1", site_id=site_1.id, last_name="A1")
    _patient(db, practice_a, national_id="2", site_id=site_2.id, last_name="A2")
    _patient(db, practice_a, national_id="3", site_id=None, last_name="A0")
    _patient(db, practice_b, national_id="1", site_id=None, last_name="B1")

    by_practice = db.scalars(
        scope_to_practice(select(Patient), Patient, practice_a.id)
    ).all()
    assert {p.last_name for p in by_practice} == {"A1", "A2", "A0"}

    by_site = db.scalars(scope_to_site(select(Patient), Patient, site_1.id)).all()
    assert {p.last_name for p in by_site} == {"A1"}

    scoped = db.scalars(
        apply_scope(
            select(Patient),
            Patient,
            RequestScope(practice_id=practice_a.id, site_id=site_2.id),
        )
    ).all()
    assert {p.last_name for p in scoped} == {"A2"}
