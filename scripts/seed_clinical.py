"""Sample clinical data for the local dev database.

Imported and run by :mod:`scripts.seed` after the practices, sites and
users exist. Everything is deterministic and idempotent: a patient is
keyed by ``(practice_id, national_id)`` and, if already present, is left
untouched along with all of its child records.

Each patient has one or more :class:`~app.models.LimbInvolvement` rows
(an amputation, a congenital absence, or an intact part needing an
orthosis); devices hang off an involvement. Dates are anchored to
``date.today()`` so the dashboard's "overdue" / "upcoming" buckets stay
meaningful whenever the seed is run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app import pathways, proms
from app.models import (
    AccountPatientLink,
    Appointment,
    AppointmentStatus,
    AppointmentType,
    AssignmentRole,
    AuditAction,
    AuditLogEntry,
    AvailabilitySlot,
    BodyRegion,
    CarePathway,
    CauseOfLimbLoss,
    ClinicalNote,
    CoverageDetermination,
    CoverageStatus,
    Device,
    DeviceStatus,
    DeviceType,
    InvolvementKind,
    InvolvementStatus,
    LimbInvolvement,
    LimbLossLevel,
    MedicalAidMembership,
    MilestoneStatus,
    Notification,
    NotificationType,
    Patient,
    PatientAssignment,
    PromInstrument,
    PromRecord,
    RecoveryMilestone,
    ReviewGrant,
    SlotStatus,
    User,
)

TODAY = date.today()

# region shorthands
LL_LEFT = BodyRegion.lower_limb_left
LL_RIGHT = BodyRegion.lower_limb_right
UL_LEFT = BodyRegion.upper_limb_left
UL_RIGHT = BodyRegion.upper_limb_right
SPINE = BodyRegion.spine


def _days(offset: int) -> date:
    return TODAY + timedelta(days=offset)


@dataclass(frozen=True)
class DeviceSpec:
    device_type: DeviceType
    status: DeviceStatus
    manufacturer: str
    model: str
    mount_location: str | None = None
    joint_type: str | None = None
    trimline: str | None = None
    strap_configuration: str | None = None
    padding_liner: str | None = None
    # Precise body-map position (viewBox fractions); both or neither.
    map_x: float | None = None
    map_y: float | None = None


@dataclass(frozen=True)
class InvolvementSpec:
    kind: InvolvementKind
    region: BodyRegion
    level: LimbLossLevel | None = None
    cause: CauseOfLimbLoss | None = None
    notes: str | None = None
    devices: tuple[DeviceSpec, ...] = ()


@dataclass(frozen=True)
class PromSpec:
    instrument: PromInstrument
    score: float
    recorded_days_ago: int


@dataclass(frozen=True)
class MedicalAidSpec:
    scheme_name: str
    plan_option: str
    membership_number: str


@dataclass(frozen=True)
class SlotSpec:
    practitioner_email: str
    day_offset: int
    hour: int
    minute: int
    duration_minutes: int
    appointment_type: AppointmentType
    status: SlotStatus = SlotStatus.open
    notes: str | None = None


@dataclass(frozen=True)
class PatientSpec:
    first_name: str
    last_name: str
    dob: str
    national_id: str
    practice: str
    site: str
    assign_clinician: str
    assign_prosthetist: str | None
    pathway: CarePathway
    pathway_start_days_ago: int
    milestones_done: int
    # in-progress milestone target relative to today (negative == overdue)
    next_due_offset: int
    involvements: tuple[InvolvementSpec, ...]
    is_active: bool = True
    proms: tuple[PromSpec, ...] = ()
    notes: tuple[str, ...] = ()
    # email of a patient-role login to link for the self-service portal
    portal_email: str | None = None
    # emails of medical-aid reviewers granted read access to this record
    review_by: tuple[str, ...] = ()
    # medical-aid membership (Section 5.9 coverage-check trigger); None
    # means self-pay
    medical_aid: MedicalAidSpec | None = None
    # a walk-in patient's contact detail on file - the second factor for
    # the self-service account-claim flow (Section 5.11); most seeded
    # patients leave these blank, matching how sparse real walk-in
    # records often are
    contact_email: str | None = None
    contact_phone: str | None = None


# --- helpers to keep the common cases terse ---------------------------


def amputation(
    region: BodyRegion,
    level: LimbLossLevel,
    cause: CauseOfLimbLoss,
    *devices: DeviceSpec,
    notes: str | None = None,
) -> InvolvementSpec:
    return InvolvementSpec(
        InvolvementKind.amputation, region, level, cause, notes, devices
    )


def orthotic(
    region: BodyRegion, *devices: DeviceSpec, notes: str | None = None
) -> InvolvementSpec:
    return InvolvementSpec(
        InvolvementKind.orthotic_need, region, None, None, notes, devices
    )


def dev(
    device_type: DeviceType,
    status: DeviceStatus,
    manufacturer: str,
    model: str,
) -> DeviceSpec:
    return DeviceSpec(device_type, status, manufacturer, model)


# --- the fixture -----------------------------------------------------

NORTHGATE = "Northgate Rehabilitation Network"
NG_MAIN = "Northgate Main Hospital"
NG_LAB = "Rosebank Gait Lab"
CAPE = "Cape Mobility Clinic"
CAPE_SITE = "Cape Mobility Clinic"
SUNRISE = "Sunrise Prosthetics & Orthotics"
SUNRISE_SITE = "Sunrise Durban Rooms"

DV = DeviceStatus
DT = DeviceType
CL = CauseOfLimbLoss
LV = LimbLossLevel

PATIENTS: list[PatientSpec] = [
    PatientSpec(
        "Thabo", "Molefe", "1984-02-11", "8402115032081",
        NORTHGATE, NG_MAIN, "clinician@northgate-rehab.co.za",
        "prosthetist@northgate-rehab.co.za",
        CarePathway.lower_limb, 140, 5, -9,
        involvements=(
            amputation(
                LL_LEFT, LV.transfemoral, CL.trauma,
                dev(DT.myoelectric, DV.active, "Ottobock", "Genium X3"),
            ),
        ),
        proms=(
            PromSpec(PromInstrument.pain_residual_limb, 8, 4),
            PromSpec(PromInstrument.pain_residual_limb, 6, 32),
            PromSpec(PromInstrument.socket_comfort_score, 7, 4),
        ),
        notes=(
            "Socket check: mild distal pressure, adjusted fit. Reviewing in 2 weeks.",
            "Gait training progressing; still guarding on stairs.",
        ),
        portal_email="thabo.molefe@patient.limbitless.co.za",
        review_by=("reviewer@medscheme.co.za",),
        medical_aid=MedicalAidSpec(
            "Discovery Health", "Classic Comprehensive", "DH-1004829217"
        ),
    ),
    PatientSpec(
        "Lerato", "Dlamini", "1991-07-03", "9107035800086",
        NORTHGATE, NG_MAIN, "clinician@northgate-rehab.co.za",
        "prosthetist@northgate-rehab.co.za",
        CarePathway.lower_limb, 64, 3, 4,
        involvements=(
            amputation(
                LL_RIGHT, LV.transtibial, CL.dysvascular,
                dev(DT.body_powered, DV.active, "Blatchford", "Elan"),
            ),
        ),
        proms=(
            PromSpec(PromInstrument.socket_comfort_score, 3, 3),
            PromSpec(PromInstrument.pain_residual_limb, 5, 3),
            PromSpec(PromInstrument.locomotor_capabilities_index, 34, 10),
        ),
        notes=("Reports the socket rubbing at the brim after ~2h of wear.",),
        # A genuine walk-in record with no portal login yet - the one to
        # try the self-service account-claim flow against (Section 5.11).
        contact_email="lerato.dlamini@example.co.za",
        contact_phone="082 555 0102",
    ),
    PatientSpec(
        "Naledi", "Khumalo", "1978-11-19", "7811195090083",
        NORTHGATE, NG_LAB, "clinician@northgate-rehab.co.za",
        "prosthetist@northgate-rehab.co.za",
        CarePathway.upper_limb, 30, 2, 7,
        involvements=(
            amputation(
                UL_LEFT, LV.transhumeral, CL.tumour,
                dev(DT.myoelectric, DV.in_fitting, "Ossur", "i-Limb Quantum"),
            ),
        ),
        proms=(
            PromSpec(PromInstrument.pain_phantom, 7, 6),
            PromSpec(PromInstrument.pain_phantom, 4, 27),
        ),
        notes=("Myoelectric site mapping done; two strong signal sites identified.",),
    ),
    PatientSpec(
        "Sipho", "Nkosi", "1969-05-28", "6905285041088",
        NORTHGATE, NG_MAIN, "clinician@northgate-rehab.co.za", None,
        CarePathway.lower_limb, 220, 4, -38,
        involvements=(
            amputation(
                LL_RIGHT, LV.transfemoral, CL.dysvascular,
                dev(DT.body_powered, DV.replaced, "Ottobock", "3R60"),
                dev(DT.body_powered, DV.active, "Ottobock", "3R80"),
            ),
        ),
        proms=(
            PromSpec(PromInstrument.pain_residual_limb, 9, 2),
            PromSpec(PromInstrument.locomotor_capabilities_index, 18, 9),
        ),
        notes=(
            "Missed last two appointments. Follow-up call placed; rebooked.",
            "Residual limb volume loss - needs socket review, likely refit.",
        ),
    ),
    PatientSpec(
        "Ayesha", "Patel", "1996-09-14", "9609145600088",
        NORTHGATE, NG_LAB, "clinician@northgate-rehab.co.za",
        "prosthetist@northgate-rehab.co.za",
        CarePathway.upper_limb, 8, 1, 10,
        involvements=(
            InvolvementSpec(
                InvolvementKind.congenital_absence, UL_LEFT, LV.transradial,
                None, "Congenital transradial limb difference.",
                (dev(DT.passive_cosmetic, DV.planned, "Steeper", "Realistic Hand"),),
            ),
        ),
        proms=(PromSpec(PromInstrument.pain_residual_limb, 2, 5),),
        notes=("Initial assessment. Goals: bimanual tasks, cycling.",),
    ),
    PatientSpec(
        "Bongani", "Zulu", "1988-01-07", "8801075035087",
        NORTHGATE, NG_MAIN, "prosthetist@northgate-rehab.co.za",
        "prosthetist@northgate-rehab.co.za",
        CarePathway.lower_limb, 300, 7, 0,
        involvements=(
            amputation(
                LL_LEFT, LV.transtibial, CL.trauma,
                dev(DT.body_powered, DV.active, "Ottobock", "1C30"),
                dev(DT.activity_specific, DV.active, "Ossur", "Cheetah Xtend"),
                notes="Everyday leg plus a running blade - both active.",
            ),
        ),
        proms=(
            PromSpec(PromInstrument.locomotor_capabilities_index, 52, 14),
            PromSpec(PromInstrument.socket_comfort_score, 9, 14),
        ),
        notes=("Discharged to annual review. Running programme going well.",),
    ),
    PatientSpec(
        "Michelle", "van Wyk", "1974-03-22", "7403225088084",
        NORTHGATE, NG_MAIN, "clinician@northgate-rehab.co.za", None,
        CarePathway.lower_limb, 45, 2, -3,
        involvements=(
            amputation(
                LL_RIGHT, LV.knee_disarticulation, CL.infection,
                dev(DT.body_powered, DV.in_fitting, "Blatchford", "KX06"),
            ),
        ),
        proms=(PromSpec(PromInstrument.socket_comfort_score, 4, 1),),
        notes=("Wound fully healed. Cleared to begin prosthetic fitting.",),
    ),
    PatientSpec(
        "Johannes", "Botha", "1962-12-02", "6212025044085",
        NORTHGATE, NG_MAIN, "clinician@northgate-rehab.co.za", None,
        CarePathway.lower_limb, 500, 7, 0,
        is_active=False,
        involvements=(
            amputation(
                LL_LEFT, LV.transtibial, CL.dysvascular,
                dev(DT.body_powered, DV.retired, "Ottobock", "1C30"),
            ),
        ),
        notes=("Relocated to another province. Records closed at this practice.",),
    ),
    PatientSpec(
        "Zanele", "Mthembu", "1990-06-30", "9006305100082",
        CAPE, CAPE_SITE, "clinician@capemobility.co.za", None,
        CarePathway.lower_limb, 90, 3, -5,
        involvements=(
            amputation(
                LL_LEFT, LV.transfemoral, CL.trauma,
                dev(DT.myoelectric, DV.active, "Ossur", "Power Knee"),
            ),
        ),
        proms=(
            PromSpec(PromInstrument.pain_residual_limb, 7, 3),
            PromSpec(PromInstrument.socket_comfort_score, 6, 3),
        ),
        notes=("Cape Mobility patient - included to check practice isolation.",),
    ),
    PatientSpec(
        "David", "Fourie", "1983-08-16", "8308165042083",
        CAPE, CAPE_SITE, "clinician@capemobility.co.za", None,
        CarePathway.upper_limb, 20, 2, 6,
        involvements=(
            amputation(
                UL_RIGHT, LV.transradial, CL.trauma,
                dev(DT.body_powered, DV.active, "Hosmer", "Hook 5XA"),
            ),
        ),
        proms=(PromSpec(PromInstrument.pain_phantom, 3, 8),),
        notes=("Adjusting harness tension; good control of the terminal device.",),
    ),
    # Same person as the Northgate "Thabo Molefe" record (id 1), seen as
    # a walk-in at a second practice - the multi-practice claim case
    # (Section 5.11). Both records link to thabo.molefe@'s one login.
    PatientSpec(
        "Thabo", "Molefe", "1984-02-11", "8402115032081",
        CAPE, CAPE_SITE, "clinician@capemobility.co.za", None,
        CarePathway.orthotic, 30, 1, 5,
        involvements=(
            orthotic(
                LL_RIGHT,
                DeviceSpec(
                    DT.orthosis_afo, DV.in_fitting, "Ossur", "Foot-Up",
                    trimline="Anterior shell",
                    mount_location="Right ankle",
                ),
                notes="Contralateral foot drop; AFO fitting while the "
                "prosthetic side is managed at Northgate.",
            ),
        ),
        proms=(PromSpec(PromInstrument.orthosis_comfort_score, 6, 5),),
        notes=("Cross-practice: amputation care is at Northgate.",),
        portal_email="thabo.molefe@patient.limbitless.co.za",
    ),
    # Bilateral amputee: two involvements, different levels and sides.
    PatientSpec(
        "Kagiso", "Sithole", "1971-10-05", "7110055033089",
        NORTHGATE, NG_MAIN, "clinician@northgate-rehab.co.za",
        "prosthetist@northgate-rehab.co.za",
        CarePathway.lower_limb, 160, 4, 5,
        involvements=(
            amputation(
                LL_LEFT, LV.transfemoral, CL.dysvascular,
                dev(DT.body_powered, DV.active, "Ottobock", "3R80"),
                notes="Left transfemoral.",
            ),
            amputation(
                LL_RIGHT, LV.transtibial, CL.dysvascular,
                dev(DT.body_powered, DV.active, "Ottobock", "Triton"),
                notes="Right transtibial; liner recently replaced.",
            ),
        ),
        proms=(
            PromSpec(PromInstrument.locomotor_capabilities_index, 30, 12),
            PromSpec(PromInstrument.pain_residual_limb, 5, 6),
            PromSpec(PromInstrument.socket_comfort_score, 6, 6),
        ),
        notes=(
            "Bilateral amputee. Both sockets reviewed.",
            "Standing tolerance improving; parallel-bar work continues.",
        ),
        review_by=("reviewer@medscheme.co.za",),
        medical_aid=MedicalAidSpec(
            "Bonitas Medical Fund", "BonComprehensive", "BON-5583210"
        ),
    ),
    # Sunrise Prosthetics & Orthotics (third practice).
    PatientSpec(
        "Precious", "Ndlovu", "1993-02-18", "9302185044084",
        SUNRISE, SUNRISE_SITE, "clinician@sunrise-prosthetics.co.za", None,
        CarePathway.lower_limb, 50, 2, 6,
        involvements=(
            amputation(
                LL_RIGHT, LV.transtibial, CL.trauma,
                dev(DT.body_powered, DV.active, "Blatchford", "Avalon"),
            ),
        ),
        proms=(PromSpec(PromInstrument.socket_comfort_score, 8, 5),),
        notes=("Sunrise patient - gives the third practice a caseload.",),
    ),
    PatientSpec(
        "Themba", "Cele", "1980-09-09", "8009095033083",
        SUNRISE, SUNRISE_SITE, "clinician@sunrise-prosthetics.co.za", None,
        CarePathway.lower_limb, 110, 3, -4,
        involvements=(
            amputation(
                LL_LEFT, LV.transfemoral, CL.dysvascular,
                dev(DT.myoelectric, DV.active, "Ossur", "Rheo Knee XC"),
            ),
        ),
        proms=(
            PromSpec(PromInstrument.pain_residual_limb, 7, 3),
            PromSpec(PromInstrument.locomotor_capabilities_index, 40, 9),
        ),
        notes=("Prosthetic knee alignment adjusted; happy with swing control.",),
    ),
    # No limb loss - orthotic devices only (post-stroke foot drop + a
    # spinal brace): two orthotic involvements, no amputation level.
    PatientSpec(
        "Refilwe", "Adams", "1966-04-27", "6604275099082",
        NORTHGATE, NG_MAIN, "clinician@northgate-rehab.co.za", None,
        CarePathway.orthotic, 40, 2, 8,
        involvements=(
            orthotic(
                LL_LEFT,
                DeviceSpec(
                    DT.orthosis_afo, DV.active, "Blatchford", "Carbon AFO",
                    joint_type="Articulated ankle, dorsiflexion assist",
                    trimline="Posterior, supramalleolar",
                    strap_configuration="Calf band + ankle strap (Velcro)",
                    padding_liner="Plastazote lining",
                    mount_location="Left ankle, posterior",
                    map_x=0.556, map_y=0.93,  # left ankle on the 240x360 figure
                ),
                notes="Post-stroke left foot drop.",
            ),
            orthotic(
                SPINE,
                DeviceSpec(
                    DT.orthosis_spinal, DV.active, "Aspen", "TLSO",
                    trimline="Thoracolumbar, sternal to pubic",
                    strap_configuration="Anterior overlapping panels, 2 straps",
                    padding_liner="Foam-lined, moisture-wicking",
                    mount_location="Thoracolumbar",
                    map_x=0.5, map_y=0.34,  # mid-torso / lumbar
                ),
                notes="Lumbar support for transfers. No amputation.",
            ),
        ),
        proms=(
            PromSpec(PromInstrument.locomotor_capabilities_index, 26, 7),
            PromSpec(PromInstrument.orthosis_comfort_score, 3, 5),  # flagged
            PromSpec(PromInstrument.quest_satisfaction, 4, 5),
        ),
        notes=("Carbon AFO issued and a back brace for lumbar support.",),
        portal_email="refilwe.adams@patient.limbitless.co.za",
        review_by=("reviewer@medscheme.co.za",),
        medical_aid=MedicalAidSpec(
            "Momentum Health", "Ingwe Option", "MH-7729014"
        ),
    ),
]

# Practitioner availability (requirements Section 5.9) - published slots a
# patient can browse and book into. day_offset is relative to TODAY so a
# reseed always lands in the future.
AT = AppointmentType
SLOTS: list[SlotSpec] = [
    SlotSpec("clinician@northgate-rehab.co.za", 1, 9, 0, 30, AT.review),
    SlotSpec("clinician@northgate-rehab.co.za", 1, 9, 30, 30, AT.initial_assessment),
    SlotSpec(
        "clinician@northgate-rehab.co.za", 2, 14, 0, 60, AT.fitting,
        SlotStatus.blocked, notes="Admin time - not for booking.",
    ),
    SlotSpec("prosthetist@northgate-rehab.co.za", 1, 10, 0, 45, AT.fitting),
    SlotSpec("prosthetist@northgate-rehab.co.za", 4, 11, 0, 30, AT.adjustment),
    SlotSpec("clinician@capemobility.co.za", 1, 9, 0, 30, AT.review),
]


@dataclass(frozen=True)
class AppointmentSpec:
    patient_national_id: str
    practice_name: str
    # identifies which SLOTS entry this books into
    practitioner_email: str
    day_offset: int
    hour: int
    minute: int
    status: AppointmentStatus = AppointmentStatus.booked
    cancellation_reason: str | None = None
    # Coverage determination realism (Section 5.9): None = the patient
    # has no membership, so booking wouldn't have opened one; otherwise
    # this is the state it's shown in, mirroring what
    # crud.book_appointment / crud.decide_coverage would have produced.
    coverage_status: CoverageStatus | None = None
    coverage_decided_by_email: str | None = None
    coverage_authorization_number: str | None = None
    coverage_valid_until_days: int | None = None  # relative to TODAY


# Books a patient into one of the SLOTS above (requirements Section 5.9).
# One stays booked & its coverage approved, one is shown already
# cancelled by the practitioner (coverage left pending, since the visit
# never happened), one is booked with coverage still awaiting review - a
# realistic mix for whenever the FE booking/reviewer screens land.
APPOINTMENTS: list[AppointmentSpec] = [
    AppointmentSpec(
        "8402115032081",  # Thabo Molefe
        NORTHGATE, "clinician@northgate-rehab.co.za", 1, 9, 0,
        coverage_status=CoverageStatus.approved,
        coverage_decided_by_email="reviewer@medscheme.co.za",
        coverage_authorization_number="DH-AUTH-004417",
        coverage_valid_until_days=60,
    ),
    AppointmentSpec(
        "7110055033089",  # Kagiso Sithole
        NORTHGATE, "prosthetist@northgate-rehab.co.za", 1, 10, 0,
        status=AppointmentStatus.cancelled_by_practitioner,
        cancellation_reason="Practitioner called in sick.",
        coverage_status=CoverageStatus.pending,
    ),
    AppointmentSpec(
        "6604275099082",  # Refilwe Adams
        NORTHGATE, "prosthetist@northgate-rehab.co.za", 4, 11, 0,
        coverage_status=CoverageStatus.pending,
    ),
]


# --- builders --------------------------------------------------------


# How many days after (positive) or before (negative) target each
# completed milestone was actually finished, cycled by order index so the
# adherence report has a realistic mix rather than "all 2 days late".
_COMPLETION_OFFSETS = (-2, 0, 3, -1, 1, -3, 5)


def _milestones(spec: PatientSpec, patient_id: int) -> list[RecoveryMilestone]:
    template = pathways.template_for(spec.pathway) or []
    start = _days(-spec.pathway_start_days_ago)
    rows: list[RecoveryMilestone] = []
    for index, milestone_type in enumerate(template):
        base_target = start + timedelta(days=14 * index)
        if index < spec.milestones_done:
            status = MilestoneStatus.complete
            target_date = base_target
            completed_date = base_target + timedelta(
                days=_COMPLETION_OFFSETS[index % len(_COMPLETION_OFFSETS)]
            )
        elif index == spec.milestones_done:
            status = MilestoneStatus.in_progress
            target_date = _days(spec.next_due_offset)
            completed_date = None
        else:
            status = MilestoneStatus.not_started
            target_date = _days(
                spec.next_due_offset + 14 * (index - spec.milestones_done)
            )
            completed_date = None
        rows.append(
            RecoveryMilestone(
                patient_id=patient_id,
                care_pathway=spec.pathway,
                milestone_type=milestone_type,
                order_index=index,
                status=status,
                target_date=target_date,
                completed_date=completed_date,
            )
        )
    return rows


def _proms(
    spec: PatientSpec, patient_id: int, recorded_by_id: int | None
) -> list[PromRecord]:
    rows: list[PromRecord] = []
    for p in spec.proms:
        score, flagged, reason = proms.evaluate(p.instrument, {"score": p.score})
        recorded = datetime.combine(_days(-p.recorded_days_ago), time(9, 0))
        rows.append(
            PromRecord(
                patient_id=patient_id,
                instrument=p.instrument,
                responses={"score": p.score},
                score=score,
                flagged=flagged,
                flag_reason=reason,
                recorded_at=recorded,
                recorded_by_id=recorded_by_id,
            )
        )
    return rows


def _seed_audit(
    db: Session,
    *,
    patient: Patient,
    devices: list[Device],
    prom_rows: list[PromRecord],
    actor_id: int | None,
    practice_id: int,
    start: date,
) -> int:
    """A representative slice of the audit trail so the practice-admin
    audit view has something to page through on a fresh database.
    Timestamps are anchored near the patient's registration."""
    base = datetime.combine(start, time(9, 0))
    rows: list[AuditLogEntry] = [
        AuditLogEntry(
            actor_id=actor_id,
            practice_id=practice_id,
            action=AuditAction.create,
            entity_type="patient",
            entity_id=patient.id,
            timestamp=base,
        ),
        AuditLogEntry(
            actor_id=actor_id,
            practice_id=practice_id,
            action=AuditAction.read,
            entity_type="dashboard",
            entity_id=None,
            timestamp=base + timedelta(days=1, hours=2),
        ),
    ]
    for offset, device in enumerate(devices):
        rows.append(
            AuditLogEntry(
                actor_id=actor_id,
                practice_id=practice_id,
                action=AuditAction.create,
                entity_type="device",
                entity_id=device.id,
                timestamp=base + timedelta(days=3 + offset, hours=1),
            )
        )
    for offset, prom in enumerate(prom_rows):
        rows.append(
            AuditLogEntry(
                actor_id=actor_id,
                practice_id=practice_id,
                action=AuditAction.create,
                entity_type="prom_record",
                entity_id=prom.id,
                timestamp=prom.recorded_at,
            )
        )
    db.add_all(rows)
    return len(rows)


def seed_clinical(
    db: Session,
    practices: dict,
    sites: dict,
    users_by_email: dict[str, User],
) -> dict[str, int]:
    added = {
        "patients": 0,
        "involvements": 0,
        "devices": 0,
        "milestones": 0,
        "proms": 0,
        "notes": 0,
        "audit": 0,
        "medical_aid_memberships": 0,
    }

    for spec in PATIENTS:
        practice = practices[spec.practice]
        site = sites[(spec.practice, spec.site)]
        if (
            db.query(Patient)
            .filter_by(practice_id=practice.id, national_id=spec.national_id)
            .one_or_none()
            is not None
        ):
            continue

        patient = Patient(
            practice_id=practice.id,
            site_id=site.id,
            first_name=spec.first_name,
            last_name=spec.last_name,
            date_of_birth=date.fromisoformat(spec.dob),
            national_id=spec.national_id,
            is_active=spec.is_active,
            contact_email=spec.contact_email,
            contact_phone=spec.contact_phone,
        )
        db.add(patient)
        db.flush()
        # Register the patient roughly when their pathway started, so the
        # reports "new patients" window means something (otherwise every
        # reseed makes the whole caseload look brand new).
        patient.created_at = datetime.combine(
            _days(-spec.pathway_start_days_ago), time(9, 0)
        )
        if spec.portal_email:
            portal_user = users_by_email.get(spec.portal_email)
            if portal_user is not None:
                db.add(
                    AccountPatientLink(
                        patient_id=patient.id, user_id=portal_user.id
                    )
                )
        db.flush()
        added["patients"] += 1

        for reviewer_email in spec.review_by:
            reviewer = users_by_email.get(reviewer_email)
            if reviewer is not None:
                db.add(
                    ReviewGrant(
                        patient_id=patient.id, reviewer_id=reviewer.id
                    )
                )

        if spec.medical_aid is not None:
            db.add(
                MedicalAidMembership(
                    patient_id=patient.id,
                    scheme_name=spec.medical_aid.scheme_name,
                    plan_option=spec.medical_aid.plan_option,
                    membership_number=spec.medical_aid.membership_number,
                )
            )
            added["medical_aid_memberships"] += 1

        clinician = users_by_email.get(spec.assign_clinician)
        start = _days(-spec.pathway_start_days_ago)
        if clinician is not None:
            db.add(
                PatientAssignment(
                    patient_id=patient.id,
                    user_id=clinician.id,
                    role=AssignmentRole.clinician,
                    start_date=start,
                    practice_id=practice.id,
                    site_id=site.id,
                )
            )
        if spec.assign_prosthetist:
            prosthetist = users_by_email.get(spec.assign_prosthetist)
            if prosthetist is not None and prosthetist.id != getattr(
                clinician, "id", None
            ):
                db.add(
                    PatientAssignment(
                        patient_id=patient.id,
                        user_id=prosthetist.id,
                        role=AssignmentRole.prosthetist,
                        start_date=start,
                        practice_id=practice.id,
                        site_id=site.id,
                    )
                )

        device_objs: list[Device] = []
        for inv in spec.involvements:
            involvement = LimbInvolvement(
                patient_id=patient.id,
                kind=inv.kind,
                region=inv.region,
                level=inv.level,
                cause=inv.cause,
                onset_date=start,
                status=InvolvementStatus.active,
                notes=inv.notes,
            )
            db.add(involvement)
            db.flush()
            added["involvements"] += 1
            for d in inv.devices:
                device = Device(
                    involvement_id=involvement.id,
                    device_type=d.device_type,
                    status=d.status,
                    manufacturer=d.manufacturer,
                    model=d.model,
                    mount_location=d.mount_location,
                    joint_type=d.joint_type,
                    trimline=d.trimline,
                    strap_configuration=d.strap_configuration,
                    padding_liner=d.padding_liner,
                    map_x=d.map_x,
                    map_y=d.map_y,
                )
                db.add(device)
                device_objs.append(device)
                added["devices"] += 1

        milestone_rows = _milestones(spec, patient.id)
        db.add_all(milestone_rows)
        added["milestones"] += len(milestone_rows)

        recorder_id = getattr(clinician, "id", None)
        prom_rows = _proms(spec, patient.id, recorder_id)
        db.add_all(prom_rows)
        added["proms"] += len(prom_rows)

        for body in spec.notes:
            db.add(
                ClinicalNote(
                    patient_id=patient.id, author_id=recorder_id, body=body
                )
            )
            added["notes"] += 1

        db.flush()
        added["audit"] += _seed_audit(
            db,
            patient=patient,
            devices=device_objs,
            prom_rows=prom_rows,
            actor_id=recorder_id,
            practice_id=practice.id,
            start=start,
        )

    db.commit()
    return added


def seed_availability(db: Session, users_by_email: dict[str, User]) -> int:
    """Publish sample slots for a couple of practitioners. Keyed by
    (practitioner, start_time) so a reseed without ``--reset`` is a
    no-op once these exist."""
    added = 0
    for spec in SLOTS:
        practitioner = users_by_email.get(spec.practitioner_email)
        if practitioner is None:
            continue
        start = datetime.combine(
            _days(spec.day_offset), time(spec.hour, spec.minute)
        )
        exists = (
            db.query(AvailabilitySlot)
            .filter_by(practitioner_id=practitioner.id, start_time=start)
            .one_or_none()
        )
        if exists is not None:
            continue
        db.add(
            AvailabilitySlot(
                practice_id=practitioner.practice_id,
                site_id=practitioner.site_id,
                practitioner_id=practitioner.id,
                start_time=start,
                end_time=start + timedelta(minutes=spec.duration_minutes),
                appointment_type=spec.appointment_type,
                status=spec.status,
                notes=spec.notes,
            )
        )
        added += 1
    db.commit()
    return added


def seed_appointments(
    db: Session, practices: dict, users_by_email: dict[str, User]
) -> int:
    """Book a couple of sample appointments into the slots above, each
    with a coverage determination in a different state (Section 5.9) -
    a realistic mix for whenever the FE booking/reviewer screens land.
    Keyed by (patient, slot), so a reseed without ``--reset`` is a no-op
    once these exist."""
    added = 0
    for spec in APPOINTMENTS:
        practice = practices.get(spec.practice_name)
        practitioner = users_by_email.get(spec.practitioner_email)
        if practice is None or practitioner is None:
            continue
        patient = (
            db.query(Patient)
            .filter_by(
                practice_id=practice.id,
                national_id=spec.patient_national_id,
            )
            .one_or_none()
        )
        if patient is None:
            continue
        start = datetime.combine(
            _days(spec.day_offset), time(spec.hour, spec.minute)
        )
        slot = (
            db.query(AvailabilitySlot)
            .filter_by(practitioner_id=practitioner.id, start_time=start)
            .one_or_none()
        )
        if slot is None:
            continue
        exists = (
            db.query(Appointment)
            .filter_by(patient_id=patient.id, slot_id=slot.id)
            .one_or_none()
        )
        if exists is not None:
            continue

        appointment = Appointment(
            practice_id=slot.practice_id,
            site_id=slot.site_id,
            patient_id=patient.id,
            practitioner_id=slot.practitioner_id,
            slot_id=slot.id,
            appointment_type=slot.appointment_type,
            scheduled_start=slot.start_time,
            scheduled_end=slot.end_time,
            status=spec.status,
        )
        slot.status = SlotStatus.booked
        if spec.status != AppointmentStatus.booked:
            appointment.cancellation_reason = spec.cancellation_reason
            appointment.cancelled_at = datetime.now()
            # Mirrors crud.cancel_appointment: a cancelled appointment
            # releases its slot back to open so it can be rebooked.
            slot.status = SlotStatus.open
        db.add(appointment)
        db.flush()

        if spec.coverage_status is not None:
            membership = (
                db.query(MedicalAidMembership)
                .filter_by(patient_id=patient.id)
                .one_or_none()
            )
            if membership is not None:
                decided_by = (
                    users_by_email.get(spec.coverage_decided_by_email)
                    if spec.coverage_decided_by_email
                    else None
                )
                db.add(
                    CoverageDetermination(
                        appointment_id=appointment.id,
                        medical_aid_membership_id=membership.id,
                        status=spec.coverage_status,
                        authorization_number=spec.coverage_authorization_number,
                        valid_until=(
                            _days(spec.coverage_valid_until_days)
                            if spec.coverage_valid_until_days is not None
                            else None
                        ),
                        decided_by_id=decided_by.id if decided_by else None,
                        decided_at=(
                            datetime.now()
                            if spec.coverage_status != CoverageStatus.pending
                            else None
                        ),
                    )
                )

        added += 1
    db.commit()
    return added


# Recipient email -> the notifications they start with. Enough of a mix
# (unread / read, immediate / future deliver_at, a few types) for the FE
# inbox and header badge to have something to show on a fresh DB.
_NOTIFICATIONS: dict[str, list[dict]] = {
    "thabo.molefe@patient.limbitless.co.za": [
        {
            "type": NotificationType.prom_flagged,
            "unread": True,
            "payload": {
                "patient_name": "Thabo Molefe",
                "instrument": "pain_residual_limb",
                "score": 8,
                "flag_reason": "score 8 at or above the 7 threshold",
            },
        },
        {
            "type": NotificationType.coverage_decided,
            "unread": False,
            "days_ago": 6,
            "payload": {
                "status": "approved",
                "appointment_type": "review",
                "scheduled_start": (TODAY + timedelta(days=2)).isoformat() + "T09:00:00",
            },
        },
        {
            "type": NotificationType.appointment_reminder,
            "unread": True,
            # already due (past deliver_at) so it shows immediately
            "deliver_hours": -2,
            "payload": {
                "appointment_type": "review",
                "scheduled_start": (TODAY + timedelta(days=1)).isoformat() + "T09:00:00",
                "practitioner_email": "clinician@northgate-rehab.co.za",
            },
        },
    ],
    "clinician@northgate-rehab.co.za": [
        {
            "type": NotificationType.prom_flagged,
            "unread": True,
            "payload": {
                "patient_name": "Thabo Molefe",
                "instrument": "pain_residual_limb",
                "score": 8,
                "flag_reason": "score 8 at or above the 7 threshold",
            },
        },
        {
            "type": NotificationType.patient_reassigned,
            "unread": True,
            "payload": {"patient_name": "Kagiso Sithole", "role": "prosthetist"},
        },
        {
            "type": NotificationType.milestone_overdue,
            "unread": True,
            "payload": {
                "patient_name": "Thabo Molefe",
                "milestone_type": "independent_ambulation_adl",
                "target_date": (TODAY - timedelta(days=9)).isoformat(),
            },
        },
    ],
}


def seed_notifications(db: Session, users_by_email: dict[str, User]) -> int:
    """Give a couple of users a starting inbox (Section 5.6). Idempotent:
    a user who already has any notification is left alone."""
    added = 0
    now = datetime.now()
    for email, specs in _NOTIFICATIONS.items():
        user = users_by_email.get(email)
        if user is None:
            continue
        if (
            db.query(Notification).filter_by(user_id=user.id).first()
            is not None
        ):
            continue
        for spec in specs:
            created = now - timedelta(days=spec.get("days_ago", 0))
            deliver_at = (
                now + timedelta(hours=spec["deliver_hours"])
                if "deliver_hours" in spec
                else None
            )
            db.add(
                Notification(
                    user_id=user.id,
                    type=spec["type"],
                    payload=spec["payload"],
                    deliver_at=deliver_at,
                    read_at=None if spec["unread"] else created,
                    # Pre-stamped as emailed: these are illustration data,
                    # not real events - turning on the email backend
                    # shouldn't fire a backlog for them.
                    emailed_at=created,
                    created_at=created,
                )
            )
            added += 1
    db.commit()
    return added


def reset_clinical(db: Session) -> None:
    models = (
        AuditLogEntry,
        CoverageDetermination,
        Appointment,
        ReviewGrant,
        MedicalAidMembership,
        ClinicalNote,
        PromRecord,
        RecoveryMilestone,
        Device,
        LimbInvolvement,
        PatientAssignment,
        AccountPatientLink,
        Patient,
    )
    for model in models:
        db.query(model).delete()
    db.commit()

    # Restart the id sequences so a reseed lands on the same ids every
    # time (Postgres only; the test suite runs on SQLite and never calls
    # this). Keeps PHASE-*-TEST-SCENARIOS.md id references stable.
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        for model in models:
            db.execute(
                text(
                    f"ALTER SEQUENCE {model.__tablename__}_id_seq RESTART WITH 1"
                )
            )
        db.commit()
