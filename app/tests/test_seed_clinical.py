"""The clinical sample seed: shape checks and the special-case patients
(bilateral amputee, orthotics-only).
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    BodyRegion,
    Device,
    DeviceType,
    InvolvementKind,
    LimbInvolvement,
    Patient,
    PromRecord,
    RecoveryMilestone,
)
from scripts.seed import seed
from scripts.seed_clinical import PATIENTS, reset_clinical


def _count(db: Session, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def _involvements(db: Session, patient_id: int) -> list[LimbInvolvement]:
    return db.scalars(
        select(LimbInvolvement).where(LimbInvolvement.patient_id == patient_id)
    ).all()


def _devices(db: Session, patient_id: int) -> list[Device]:
    return db.scalars(
        select(Device)
        .join(LimbInvolvement, LimbInvolvement.id == Device.involvement_id)
        .where(LimbInvolvement.patient_id == patient_id)
    ).all()


def test_seed_creates_every_sample_patient(db: Session) -> None:
    seed(db)
    assert _count(db, Patient) == len(PATIENTS)
    assert _count(db, LimbInvolvement) >= len(PATIENTS)
    assert _count(db, RecoveryMilestone) > len(PATIENTS)
    assert _count(db, Device) >= len(PATIENTS)


def test_bilateral_patient_has_an_involvement_per_side(db: Session) -> None:
    seed(db)
    kagiso = db.scalar(select(Patient).where(Patient.national_id == "7110055033089"))
    involvements = _involvements(db, kagiso.id)
    regions = {i.region for i in involvements}
    assert {BodyRegion.lower_limb_left, BodyRegion.lower_limb_right} <= regions
    # different amputation level on each side, one device each
    assert len({i.level for i in involvements}) == 2
    assert all(i.kind is InvolvementKind.amputation for i in involvements)
    assert len(_devices(db, kagiso.id)) == 2


def test_orthotics_patient_has_no_amputation_and_orthotic_devices(
    db: Session,
) -> None:
    seed(db)
    refilwe = db.scalar(select(Patient).where(Patient.national_id == "6604275099082"))
    involvements = _involvements(db, refilwe.id)
    assert involvements
    assert all(i.kind is InvolvementKind.orthotic_need for i in involvements)
    assert all(i.level is None and i.cause is None for i in involvements)
    assert any(i.region is BodyRegion.spine for i in involvements)

    devices = _devices(db, refilwe.id)
    assert devices
    assert all(d.device_type.value.startswith("orthosis_") for d in devices)
    # orthosis componentry is populated, prosthesis componentry is not
    assert all(d.trimline and d.strap_configuration for d in devices)
    assert all(d.socket_type is None for d in devices)


def test_seed_clinical_is_idempotent(db: Session) -> None:
    seed(db)
    models = (Patient, LimbInvolvement, Device, RecoveryMilestone, PromRecord)
    counts = {m: _count(db, m) for m in models}
    seed(db)
    assert {m: _count(db, m) for m in models} == counts


def test_reset_clinical_clears_patient_data(db: Session) -> None:
    seed(db)
    reset_clinical(db)
    assert _count(db, Patient) == 0
    assert _count(db, LimbInvolvement) == 0
    assert _count(db, Device) == 0
    assert _count(db, RecoveryMilestone) == 0


def test_orthosis_device_type_values_exist() -> None:
    values = {t.value for t in DeviceType}
    assert {
        "orthosis_afo",
        "orthosis_kafo",
        "orthosis_spinal",
        "orthosis_upper_limb",
    } <= values
