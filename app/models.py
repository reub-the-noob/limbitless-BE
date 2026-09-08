"""SQLAlchemy ORM models for the Limb-itless domain.

Importing this module registers every model on ``Base.metadata``, which is
what Alembic autogenerate diffs against.

``Practice`` is the tenant boundary: every clinical entity added from here
on carries a ``practice_id`` (via :class:`PracticeScoped`) so isolation is
structural. ``Site`` is the location/department level beneath a practice; a
single-site practice simply has one ``Site`` row, so scoping never needs a
special case.
"""

import enum
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    Enum,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

__all__ = [
    "Base",
    "TimestampMixin",
    "PracticeScoped",
    "SiteScoped",
    "PracticeType",
    "SiteType",
    "UserRole",
    "AuditAction",
    "CauseOfLimbLoss",
    "LimbLossLevel",
    "InvolvementKind",
    "BodyRegion",
    "InvolvementStatus",
    "AssignmentRole",
    "LimbSide",
    "DeviceType",
    "DeviceStatus",
    "CarePathway",
    "MilestoneType",
    "MilestoneStatus",
    "PromInstrument",
    "Practice",
    "Site",
    "User",
    "AuditLogEntry",
    "Patient",
    "LimbInvolvement",
    "PatientAssignment",
    "Device",
    "RecoveryMilestone",
    "PromRecord",
    "ClinicalNote",
    "ReviewGrant",
    "MedicalAidMembership",
    "AppointmentType",
    "SlotStatus",
    "AvailabilitySlot",
    "AppointmentStatus",
    "Appointment",
    "CoverageStatus",
    "CoverageDetermination",
    "AccountLinkStatus",
    "VerificationMethod",
    "AccountLinkRequest",
    "AccountPatientLink",
    "NotificationType",
    "Notification",
]


class TimestampMixin:
    """Adds ``created_at`` / ``updated_at`` to a model."""

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )


class PracticeScoped:
    """Mixin: a required ``practice_id`` FK — the tenant boundary for a row."""

    practice_id: Mapped[int] = mapped_column(
        ForeignKey("practices.id", ondelete="CASCADE"), index=True
    )


class SiteScoped:
    """Mixin: an optional ``site_id`` FK for rows scoped below the practice."""

    site_id: Mapped[int | None] = mapped_column(
        ForeignKey("sites.id", ondelete="SET NULL"), index=True
    )


class PracticeType(enum.Enum):
    hospital_network = "hospital_network"
    private_practice = "private_practice"


class SiteType(enum.Enum):
    location = "location"
    department = "department"


class Practice(TimestampMixin, Base):
    """A private hospital network or independent practice — one tenant."""

    __tablename__ = "practices"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    type: Mapped[PracticeType] = mapped_column(Enum(PracticeType, name="practice_type"))
    address: Mapped[str | None] = mapped_column(String(500))
    # Minimum notice a patient must give to cancel without it being
    # recorded as a late cancellation (Section 5.10) - practice-configurable,
    # not a fixed platform rule.
    cancellation_notice_hours: Mapped[int] = mapped_column(
        server_default=text("24")
    )

    sites: Mapped[list["Site"]] = relationship(
        back_populates="practice", cascade="all, delete-orphan"
    )


class Site(PracticeScoped, TimestampMixin, Base):
    """A physical location or department within a practice."""

    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    type: Mapped[SiteType] = mapped_column(Enum(SiteType, name="site_type"))
    address: Mapped[str | None] = mapped_column(String(500))

    practice: Mapped["Practice"] = relationship(back_populates="sites")


class UserRole(enum.Enum):
    platform_administrator = "platform_administrator"
    practice_administrator = "practice_administrator"
    clinician = "clinician"
    prosthetist = "prosthetist"
    patient = "patient"
    medical_aid_reviewer = "medical_aid_reviewer"


class User(TimestampMixin, Base):
    """A login identity.

    ``practice_id`` / ``site_id`` are nullable because platform
    administrators and medical aid reviewers act across practices; every
    other role is bound to one practice and usually one site. User keeps
    its own nullable FKs rather than :class:`PracticeScoped`, whose
    ``practice_id`` is the non-null tenant boundary for clinical rows.

    ``scheme_name`` is meaningful only for a ``medical_aid_reviewer`` -
    which scheme they review for. It's what gives a reviewer automatic,
    cross-practice access to any patient whose active
    :class:`MedicalAidMembership` matches (Section 5.12's real design),
    alongside - not instead of - the manual per-patient
    :class:`ReviewGrant` a treating practice can still hand out (that
    mechanism is closer to the Phase-4 consent concept; both coexist,
    see ``crud.get_reviewer_patient``/``list_reviewer_patients``).
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="user_role"))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
    practice_id: Mapped[int | None] = mapped_column(
        ForeignKey("practices.id", ondelete="CASCADE"), index=True
    )
    site_id: Mapped[int | None] = mapped_column(
        ForeignKey("sites.id", ondelete="SET NULL"), index=True
    )
    scheme_name: Mapped[str | None] = mapped_column(String(200))

    practice: Mapped["Practice | None"] = relationship()
    site: Mapped["Site | None"] = relationship()


class AuditAction(enum.Enum):
    read = "read"
    create = "create"
    update = "update"
    delete = "delete"


class AuditLogEntry(Base):
    """Append-only record that an actor read or changed patient data
    (requirements Section 5.7). Rows are never updated.

    ``actor_id`` and ``practice_id`` use ``ON DELETE SET NULL`` — removing
    a user or practice must not erase the trail. ``practice_id`` is not in
    the requirements field list; it is here so a practice administrator can
    query access to their own practice's data without joining through
    (possibly deleted) entities.
    """

    __tablename__ = "audit_log_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    practice_id: Mapped[int | None] = mapped_column(
        ForeignKey("practices.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[AuditAction] = mapped_column(Enum(AuditAction, name="audit_action"))
    entity_type: Mapped[str] = mapped_column(String(100), index=True)
    entity_id: Mapped[int | None] = mapped_column(index=True)
    timestamp: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)


class CauseOfLimbLoss(enum.Enum):
    trauma = "trauma"
    dysvascular = "dysvascular"
    infection = "infection"
    tumour = "tumour"
    congenital = "congenital"
    other = "other"


class LimbLossLevel(enum.Enum):
    partial_foot = "partial_foot"
    ankle_disarticulation = "ankle_disarticulation"
    transtibial = "transtibial"
    knee_disarticulation = "knee_disarticulation"
    transfemoral = "transfemoral"
    hip_disarticulation = "hip_disarticulation"
    partial_hand = "partial_hand"
    wrist_disarticulation = "wrist_disarticulation"
    transradial = "transradial"
    elbow_disarticulation = "elbow_disarticulation"
    transhumeral = "transhumeral"
    shoulder_disarticulation = "shoulder_disarticulation"


class InvolvementKind(enum.Enum):
    amputation = "amputation"
    congenital_absence = "congenital_absence"
    orthotic_need = "orthotic_need"


class BodyRegion(enum.Enum):
    lower_limb_left = "lower_limb_left"
    lower_limb_right = "lower_limb_right"
    upper_limb_left = "upper_limb_left"
    upper_limb_right = "upper_limb_right"
    spine = "spine"
    trunk = "trunk"
    other = "other"


class InvolvementStatus(enum.Enum):
    active = "active"
    resolved = "resolved"


class Patient(PracticeScoped, SiteScoped, TimestampMixin, Base):
    """A person under prosthetic or orthotic care (requirements Section
    5.11). Holds identity and contact detail only; what is clinically
    wrong with the patient lives in one or more :class:`LimbInvolvement`
    rows. ``national_id`` / ``passport_number`` is the durable identity
    key and at least one must be present; IDs are unique within a practice
    but not across the platform (Section 6).
    """

    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str] = mapped_column(String(120))
    last_name: Mapped[str] = mapped_column(String(120), index=True)
    date_of_birth: Mapped[date] = mapped_column(Date)
    national_id: Mapped[str | None] = mapped_column(String(20))
    passport_number: Mapped[str | None] = mapped_column(String(40))
    contact_email: Mapped[str | None] = mapped_column(String(320))
    contact_phone: Mapped[str | None] = mapped_column(String(40))
    address: Mapped[str | None] = mapped_column(String(500))
    medical_history: Mapped[str | None] = mapped_column(Text)
    comorbidities: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())

    practice: Mapped["Practice"] = relationship()
    site: Mapped["Site | None"] = relationship()
    # The self-service-portal login this record is claimed by, if any
    # (requirements Section 5.11). A record belongs to at most one login;
    # a login may hold several records (walk-ins at different practices) -
    # see :class:`AccountPatientLink`.
    account_link: Mapped["AccountPatientLink | None"] = relationship(
        back_populates="patient",
        cascade="all, delete-orphan",
        single_parent=True,
    )

    @property
    def user_id(self) -> int | None:
        """The ``users.id`` this record is linked to, or ``None``. Kept as
        a read-only shim so ``patient.user_id`` still works everywhere it
        did before the link became a join row."""
        return self.account_link.user_id if self.account_link else None

    __table_args__ = (
        CheckConstraint(
            "national_id IS NOT NULL OR passport_number IS NOT NULL",
            name="ck_patients_identity_present",
        ),
        UniqueConstraint(
            "practice_id", "national_id", name="uq_patients_practice_national_id"
        ),
        UniqueConstraint(
            "practice_id", "passport_number", name="uq_patients_practice_passport"
        ),
    )


class LimbInvolvement(TimestampMixin, Base):
    """One affected body location for a patient: an amputation, a
    congenital limb absence, or an intact part that needs an orthosis.
    A bilateral amputee has two rows (one per limb); an orthotics patient
    has one row per braced region. Devices, and optionally milestones /
    PROMs / notes, hang off an involvement rather than the patient, so
    recovery and the body-map view are limb-specific.

    ``level`` (an amputation level) and ``cause`` apply to amputations and
    congenital absence; for ``orthotic_need`` they are null and ``notes``
    carries the presenting problem (foot drop, scoliosis, …).
    """

    __tablename__ = "limb_involvements"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(
        ForeignKey("patients.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[InvolvementKind] = mapped_column(
        Enum(InvolvementKind, name="involvement_kind")
    )
    region: Mapped[BodyRegion] = mapped_column(
        Enum(BodyRegion, name="body_region")
    )
    level: Mapped[LimbLossLevel | None] = mapped_column(
        Enum(LimbLossLevel, name="limb_loss_level", create_type=False)
    )
    cause: Mapped[CauseOfLimbLoss | None] = mapped_column(
        Enum(CauseOfLimbLoss, name="cause_of_limb_loss", create_type=False)
    )
    onset_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[InvolvementStatus] = mapped_column(
        Enum(InvolvementStatus, name="involvement_status"),
        default=InvolvementStatus.active,
    )
    notes: Mapped[str | None] = mapped_column(Text)

    patient: Mapped["Patient"] = relationship()


class AssignmentRole(enum.Enum):
    clinician = "clinician"
    prosthetist = "prosthetist"


class PatientAssignment(PracticeScoped, SiteScoped, TimestampMixin, Base):
    """A clinician or prosthetist assigned to a patient for a period of
    time (requirements Section 5.1 / 4). Assignments are never deleted:
    ending one sets ``end_date``, leaving the history intact for
    continuity of care. ``end_date IS NULL`` means the assignment is
    current. Phase 1 assignments stay within one practice; cross-practice
    access is the ``ConsentGrant`` mechanism (Section 5.12, Phase 4).
    """

    __tablename__ = "patient_assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(
        ForeignKey("patients.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[AssignmentRole] = mapped_column(
        Enum(AssignmentRole, name="assignment_role")
    )
    start_date: Mapped[date] = mapped_column(
        Date, server_default=func.current_date()
    )
    end_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)

    patient: Mapped["Patient"] = relationship()
    user: Mapped["User"] = relationship()

    __table_args__ = (
        Index(
            "uq_patient_assignments_active",
            "patient_id",
            "user_id",
            "role",
            unique=True,
            postgresql_where=text("end_date IS NULL"),
            sqlite_where=text("end_date IS NULL"),
        ),
    )


# No longer used by the ORM after R-25 — the side is encoded in
# ``LimbInvolvement.region``. Kept as a value object for now; the
# ``limb_side`` PG type is dropped in a later cleanup.
class LimbSide(enum.Enum):
    left = "left"
    right = "right"
    bilateral = "bilateral"


class DeviceType(enum.Enum):
    # Prostheses (replace a missing limb segment).
    body_powered = "body_powered"
    myoelectric = "myoelectric"
    passive_cosmetic = "passive_cosmetic"
    activity_specific = "activity_specific"
    # Orthoses (support or align an intact body part). ``limb_level`` is
    # not applicable to these.
    orthosis_afo = "orthosis_afo"
    orthosis_kafo = "orthosis_kafo"
    orthosis_spinal = "orthosis_spinal"
    orthosis_upper_limb = "orthosis_upper_limb"


class DeviceStatus(enum.Enum):
    planned = "planned"
    in_fitting = "in_fitting"
    active = "active"
    in_repair = "in_repair"
    replaced = "replaced"
    retired = "retired"


class Device(TimestampMixin, Base):
    """A prosthetic or orthotic device fitted (or being fitted) for a
    :class:`LimbInvolvement` (requirements Section 5.2). The involvement
    supplies the limb / region and the amputation level, so the device
    only records what kind of device it is and its componentry.
    ``replaces_device_id`` links a device to the one it superseded so the
    history stays traceable. ``status`` is informational — a patient can
    hold several active devices for one limb (everyday leg, shower leg,
    running blade). ``mount_location`` is optional free text for the
    body-map view (e.g. "posterior", "lateral strut").

    ``map_x`` / ``map_y`` are an optional precise position on the body-map
    figure: fractions in ``[0, 1]`` of the SVG viewBox (top-left origin),
    so they survive a change to the figure's own coordinate space. Set
    together or not at all; a device without them falls back to its
    involvement's region marker.
    """

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    involvement_id: Mapped[int] = mapped_column(
        ForeignKey("limb_involvements.id", ondelete="CASCADE"), index=True
    )
    device_type: Mapped[DeviceType] = mapped_column(
        Enum(DeviceType, name="device_type")
    )
    status: Mapped[DeviceStatus] = mapped_column(
        Enum(DeviceStatus, name="device_status"),
        default=DeviceStatus.planned,
    )
    mount_location: Mapped[str | None] = mapped_column(String(200))
    # Precise body-map position: viewBox fractions in [0, 1], both or
    # neither (see the class docstring).
    map_x: Mapped[float | None] = mapped_column(Float)
    map_y: Mapped[float | None] = mapped_column(Float)

    manufacturer: Mapped[str | None] = mapped_column(String(200))
    model: Mapped[str | None] = mapped_column(String(200))
    serial_number: Mapped[str | None] = mapped_column(String(120))

    # Prosthesis componentry
    socket_type: Mapped[str | None] = mapped_column(String(200))
    liner_type: Mapped[str | None] = mapped_column(String(200))
    suspension_type: Mapped[str | None] = mapped_column(String(200))
    terminal_device: Mapped[str | None] = mapped_column(String(200))
    # Orthosis componentry (the frontend shows one set or the other by
    # device type; all optional either way)
    joint_type: Mapped[str | None] = mapped_column(String(200))
    trimline: Mapped[str | None] = mapped_column(String(200))
    strap_configuration: Mapped[str | None] = mapped_column(String(200))
    padding_liner: Mapped[str | None] = mapped_column(String(200))

    cast_scan_date: Mapped[date | None] = mapped_column(Date)
    delivery_date: Mapped[date | None] = mapped_column(Date)
    fitted_date: Mapped[date | None] = mapped_column(Date)
    warranty_start: Mapped[date | None] = mapped_column(Date)
    warranty_expiry: Mapped[date | None] = mapped_column(Date)

    notes: Mapped[str | None] = mapped_column(Text)
    replaces_device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL")
    )

    involvement: Mapped["LimbInvolvement"] = relationship()


class CarePathway(enum.Enum):
    lower_limb = "lower_limb"
    upper_limb = "upper_limb"
    orthotic = "orthotic"
    other = "other"


class MilestoneType(enum.Enum):
    # Lower-limb default pathway (requirements Section 5.3)
    pre_prosthetic_assessment = "pre_prosthetic_assessment"
    cast_socket_fabrication = "cast_socket_fabrication"
    initial_fitting_delivery = "initial_fitting_delivery"
    wear_schedule_desensitization = "wear_schedule_desensitization"
    gait_functional_training = "gait_functional_training"
    independent_ambulation_adl = "independent_ambulation_adl"
    community_reintegration_followup = "community_reintegration_followup"
    # Upper-limb / generic
    prosthetic_use_training = "prosthetic_use_training"
    myoelectric_training = "myoelectric_training"
    # Orthotic pathway (requirements Section 5.3)
    orthotic_assessment = "orthotic_assessment"
    orthosis_casting = "orthosis_casting"
    other = "other"


class MilestoneStatus(enum.Enum):
    not_started = "not_started"
    in_progress = "in_progress"
    complete = "complete"
    delayed = "delayed"


class RecoveryMilestone(TimestampMixin, Base):
    """A step in a patient's rehab pathway (requirements Section 5.3).
    Scoped through ``patient_id``. ``care_pathway`` + ``order_index`` place
    the milestone on the timeline; the standard steps for a pathway come
    from :mod:`app.pathways`, applied via the pathway endpoint. A milestone
    may optionally be tied to the device it concerns.
    """

    __tablename__ = "recovery_milestones"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(
        ForeignKey("patients.id", ondelete="CASCADE"), index=True
    )
    involvement_id: Mapped[int | None] = mapped_column(
        ForeignKey("limb_involvements.id", ondelete="SET NULL"), index=True
    )
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), index=True
    )
    care_pathway: Mapped[CarePathway] = mapped_column(
        Enum(CarePathway, name="care_pathway"), default=CarePathway.other
    )
    milestone_type: Mapped[MilestoneType] = mapped_column(
        Enum(MilestoneType, name="milestone_type")
    )
    order_index: Mapped[int] = mapped_column(default=0)
    status: Mapped[MilestoneStatus] = mapped_column(
        Enum(MilestoneStatus, name="milestone_status"),
        default=MilestoneStatus.not_started,
    )
    target_date: Mapped[date | None] = mapped_column(Date)
    completed_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)

    patient: Mapped["Patient"] = relationship()


class PromInstrument(enum.Enum):
    pain_residual_limb = "pain_residual_limb"
    pain_phantom = "pain_phantom"
    socket_comfort_score = "socket_comfort_score"
    locomotor_capabilities_index = "locomotor_capabilities_index"
    # Orthotics
    orthosis_comfort_score = "orthosis_comfort_score"
    quest_satisfaction = "quest_satisfaction"


class PromRecord(TimestampMixin, Base):
    """A patient-reported outcome measure (requirements Section 5.4).
    Scoped through ``patient_id``, optionally tied to a device. Instrument
    is a type, not a fixed set of fields: raw answers live in ``responses``
    (JSON), while ``score`` / ``flagged`` / ``flag_reason`` are derived at
    write time from :mod:`app.proms` so trend charts and the dashboard's
    "flagged results" view can query them directly.
    """

    __tablename__ = "prom_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(
        ForeignKey("patients.id", ondelete="CASCADE"), index=True
    )
    involvement_id: Mapped[int | None] = mapped_column(
        ForeignKey("limb_involvements.id", ondelete="SET NULL"), index=True
    )
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), index=True
    )
    instrument: Mapped[PromInstrument] = mapped_column(
        Enum(PromInstrument, name="prom_instrument"), index=True
    )
    responses: Mapped[dict] = mapped_column(JSON)
    score: Mapped[float | None] = mapped_column(Float, index=True)
    flagged: Mapped[bool] = mapped_column(
        default=False, server_default=text("false"), index=True
    )
    flag_reason: Mapped[str | None] = mapped_column(String(255))
    recorded_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), index=True
    )
    recorded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(Text)

    patient: Mapped["Patient"] = relationship()


class ClinicalNote(TimestampMixin, Base):
    """A free-text clinical note on a patient (requirements Section 7).
    Scoped through ``patient_id``; ``author_id`` is the user who wrote it
    (``ON DELETE SET NULL`` so the note survives the author leaving).
    Notes appear on the patient timeline (Section 5.3).
    """

    __tablename__ = "clinical_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(
        ForeignKey("patients.id", ondelete="CASCADE"), index=True
    )
    involvement_id: Mapped[int | None] = mapped_column(
        ForeignKey("limb_involvements.id", ondelete="SET NULL"), index=True
    )
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    body: Mapped[str] = mapped_column(Text)

    patient: Mapped["Patient"] = relationship()


class ReviewGrant(TimestampMixin, Base):
    """Access for a medical-aid reviewer to one patient's record — the
    Phase-2 slice of the cross-practice consent mechanism (Section 5.12).
    A staff member at the treating practice grants it; the reviewer then
    has read-only access to that patient through the ``/review``
    endpoints. Removing the grant revokes access.
    """

    __tablename__ = "review_grants"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(
        ForeignKey("patients.id", ondelete="CASCADE"), index=True
    )
    reviewer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    granted_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    patient: Mapped["Patient"] = relationship()
    reviewer: Mapped["User"] = relationship(foreign_keys=[reviewer_id])

    __table_args__ = (
        UniqueConstraint(
            "patient_id",
            "reviewer_id",
            name="uq_review_grants_patient_reviewer",
        ),
    )


class MedicalAidMembership(TimestampMixin, Base):
    """A patient's medical-aid membership (requirements Section 5.9). At
    most one per patient — its presence is what determines whether a
    booked appointment needs a coverage check before it's confirmed, or
    is self-pay and skips straight there.
    """

    __tablename__ = "medical_aid_memberships"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(
        ForeignKey("patients.id", ondelete="CASCADE"), unique=True, index=True
    )
    scheme_name: Mapped[str] = mapped_column(String(200))
    plan_option: Mapped[str] = mapped_column(String(200))
    membership_number: Mapped[str] = mapped_column(String(100))

    patient: Mapped["Patient"] = relationship()


class AppointmentType(enum.Enum):
    initial_assessment = "initial_assessment"
    fitting = "fitting"
    review = "review"
    adjustment = "adjustment"
    follow_up = "follow_up"
    other = "other"


class SlotStatus(enum.Enum):
    open = "open"
    booked = "booked"
    blocked = "blocked"


class AvailabilitySlot(PracticeScoped, SiteScoped, TimestampMixin, Base):
    """A block of time a practitioner has opened up for booking
    (requirements Section 5.9). Publishing the slot *is* the
    confirmation — a patient booking into it converts it straight from
    ``open`` to ``booked``, with no separate practitioner approval step.
    A practitioner blocks time (a day off, admin time) the same way, by
    creating a slot with status ``blocked`` up front.
    """

    __tablename__ = "availability_slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    practitioner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    start_time: Mapped[datetime] = mapped_column(index=True)
    end_time: Mapped[datetime] = mapped_column()
    appointment_type: Mapped[AppointmentType] = mapped_column(
        Enum(AppointmentType, name="appointment_type")
    )
    status: Mapped[SlotStatus] = mapped_column(
        Enum(SlotStatus, name="slot_status"), default=SlotStatus.open
    )
    notes: Mapped[str | None] = mapped_column(Text)

    practitioner: Mapped["User"] = relationship(foreign_keys=[practitioner_id])


class AppointmentStatus(enum.Enum):
    booked = "booked"
    cancelled_by_patient = "cancelled_by_patient"
    cancelled_by_practitioner = "cancelled_by_practitioner"
    no_show = "no_show"
    rescheduled = "rescheduled"


class Appointment(PracticeScoped, SiteScoped, TimestampMixin, Base):
    """A booked visit (requirements Section 5.9/5.10).

    Created by booking into an open :class:`AvailabilitySlot`, which
    flips straight to ``booked`` — there's no separate approval step.
    ``appointment_type``/``scheduled_start``/``scheduled_end`` are
    snapshotted off the slot at booking time rather than read live off
    it, so the appointment's own record stays stable even though the
    slot can't be edited once booked (see ``routers.availability``).

    ``slot_id`` is nullable for the urgent-request exception path
    (Section 5.9) - a later slice; every appointment booked through the
    normal flow has one. Coverage confirmation (``CoverageDetermination``)
    is a separate entity, added in a later slice - self-pay patients (no
    ``MedicalAidMembership``) skip that step entirely; this model doesn't
    need to know which is which. (Reschedule's coverage carry-over rule -
    keep the existing coverage decision only if the treatment type is
    unchanged and it hasn't expired, else re-trigger the coverage flow -
    is deferred to that slice for the same reason.)

    Cancelling releases the slot back to ``open`` (see
    ``crud.cancel_appointment``) so it can be rebooked.

    Rescheduling (Section 5.10) is ONE atomic action, not a cancel: the
    old appointment's slot is released and a new one taken, in the same
    transaction (see ``crud.reschedule_appointment``). The old row gets
    its own ``rescheduled`` status - distinct from ``cancelled_by_*`` so
    it doesn't read as an unexplained cancel - and ``rescheduled_to_id``
    points at the new row, which carries ``rescheduled_from_id`` back.
    Plain integer FKs (no ORM relationship object), same as
    ``Device.replaces_device_id``.
    """

    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(
        ForeignKey("patients.id", ondelete="CASCADE"), index=True
    )
    practitioner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    slot_id: Mapped[int | None] = mapped_column(
        ForeignKey("availability_slots.id", ondelete="SET NULL"), index=True
    )
    appointment_type: Mapped[AppointmentType] = mapped_column(
        Enum(AppointmentType, name="appointment_type")
    )
    scheduled_start: Mapped[datetime] = mapped_column(index=True)
    scheduled_end: Mapped[datetime] = mapped_column()
    status: Mapped[AppointmentStatus] = mapped_column(
        Enum(AppointmentStatus, name="appointment_status"),
        default=AppointmentStatus.booked,
    )
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    cancelled_at: Mapped[datetime | None] = mapped_column()
    late_cancellation: Mapped[bool] = mapped_column(server_default=text("false"))
    rescheduled_to_id: Mapped[int | None] = mapped_column(
        ForeignKey("appointments.id", ondelete="SET NULL"), index=True
    )
    rescheduled_from_id: Mapped[int | None] = mapped_column(
        ForeignKey("appointments.id", ondelete="SET NULL"), index=True
    )
    notes: Mapped[str | None] = mapped_column(Text)

    patient: Mapped["Patient"] = relationship()
    practitioner: Mapped["User"] = relationship(foreign_keys=[practitioner_id])
    slot: Mapped["AvailabilitySlot | None"] = relationship()
    coverage: Mapped["CoverageDetermination | None"] = relationship(
        back_populates="appointment"
    )


class CoverageStatus(enum.Enum):
    pending = "pending"
    approved = "approved"
    denied = "denied"


class CoverageDetermination(TimestampMixin, Base):
    """A reviewer's coverage decision for one booked appointment
    (requirements Section 5.9) - captured manually by a medical-aid
    reviewer through the platform, never fetched live from a scheme's
    own systems (out of scope).

    Created automatically, ``pending``, the moment a patient with a
    :class:`MedicalAidMembership` books (see ``crud.book_appointment``)
    - a self-pay patient's appointment never gets one, and this table is
    how the rest of the system tells the two apart rather than the
    appointment needing to know. One row per appointment
    (``appointment_id`` unique).

    On reschedule (Section 5.10), an ``approved`` determination that
    hasn't expired carries over to the new appointment - literally
    re-pointed at it, same row - only if the appointment type is
    unchanged; otherwise the old row stays attached to the old
    (``rescheduled``) appointment as history and a fresh ``pending`` one
    is created for the new appointment, re-triggering the coverage flow
    (see ``crud.reschedule_appointment``).
    """

    __tablename__ = "coverage_determinations"

    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(
        ForeignKey("appointments.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    medical_aid_membership_id: Mapped[int] = mapped_column(
        ForeignKey("medical_aid_memberships.id", ondelete="CASCADE"),
        index=True,
    )
    status: Mapped[CoverageStatus] = mapped_column(
        Enum(CoverageStatus, name="coverage_status"),
        default=CoverageStatus.pending,
    )
    authorization_number: Mapped[str | None] = mapped_column(String(100))
    valid_until: Mapped[date | None] = mapped_column(Date)
    decided_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    decided_at: Mapped[datetime | None] = mapped_column()
    notes: Mapped[str | None] = mapped_column(Text)

    appointment: Mapped["Appointment"] = relationship(back_populates="coverage")
    membership: Mapped["MedicalAidMembership"] = relationship()
    decided_by: Mapped["User | None"] = relationship(
        foreign_keys=[decided_by_id]
    )


class AccountLinkStatus(enum.Enum):
    pending = "pending"
    verified = "verified"
    rejected = "rejected"


class VerificationMethod(enum.Enum):
    contact_email = "contact_email"
    contact_phone = "contact_phone"


class AccountLinkRequest(TimestampMixin, Base):
    """A patient's self-service claim of an existing walk-in record
    (requirements Section 5.11) - the platform never requires a login to
    create or maintain a patient record, so this is how the person
    behind one later connects it to their own account.

    Matching on ``national_id``/``passport_number`` alone would not be
    enough - that number isn't secret - so ``verification_method``
    records which contact detail (already on the walk-in record) was
    checked, and the row's ``status`` records whether it actually
    matched. The check happens in one request (see
    ``crud.claim_walk_in_patient``): nothing about a candidate record -
    not even whether one exists - is ever revealed unless both the
    identity number *and* the contact detail match, so an attacker can't
    use this to probe who has a record at all. Every attempt is logged
    here regardless of outcome, which is the audit trail Section 5.7
    wants for anything touching patient data.

    Only affects what the claiming patient can see, per the section - a
    different practitioner or practice gaining access is Section 5.12's
    (separate, unbuilt) ``ConsentGrant`` mechanism.

    ``patient_id`` is nullable: a rejected attempt might not have found
    even one candidate matching the identity number, so there is nothing
    to point the row at.
    """

    __tablename__ = "account_link_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    patient_id: Mapped[int | None] = mapped_column(
        ForeignKey("patients.id", ondelete="SET NULL"), index=True
    )
    verification_method: Mapped[VerificationMethod] = mapped_column(
        Enum(VerificationMethod, name="verification_method")
    )
    status: Mapped[AccountLinkStatus] = mapped_column(
        Enum(AccountLinkStatus, name="account_link_status"),
        default=AccountLinkStatus.pending,
    )
    verified_at: Mapped[datetime | None] = mapped_column()

    user: Mapped["User"] = relationship()
    patient: Mapped["Patient | None"] = relationship()


class AccountPatientLink(TimestampMixin, Base):
    """One claimed record on a self-service-portal login (requirements
    Section 5.11). ``patient_id`` is unique - a record is claimed by at
    most one login - but ``user_id`` is not: a person seen as a walk-in
    at more than one practice has a separate ``Patient`` row per practice
    and can hold all of them on the one account.

    ``verification_method`` is the contact detail checked when the person
    claimed the record themselves; it is ``NULL`` for a link a staff
    member made directly. The permanent audit trail of *attempts* stays
    in :class:`AccountLinkRequest`; this table is just the live set of
    links.
    """

    __tablename__ = "account_patient_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(
        ForeignKey("patients.id", ondelete="CASCADE"), unique=True, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    verification_method: Mapped[VerificationMethod | None] = mapped_column(
        Enum(VerificationMethod, name="verification_method")
    )

    patient: Mapped["Patient"] = relationship(back_populates="account_link")
    user: Mapped["User"] = relationship()


class NotificationType(enum.Enum):
    """What happened, from the recipient's point of view (requirements
    Section 5.6). ``appointment_reminder`` is created up front with a
    future ``Notification.deliver_at``; ``milestone_due`` /
    ``milestone_overdue`` are materialised by the maintenance pass when a
    milestone is found past its target date and still open; the rest
    deliver immediately at the triggering event."""

    appointment_booked = "appointment_booked"
    appointment_cancelled = "appointment_cancelled"
    appointment_rescheduled = "appointment_rescheduled"
    appointment_reminder = "appointment_reminder"
    coverage_decided = "coverage_decided"
    prom_flagged = "prom_flagged"
    patient_reassigned = "patient_reassigned"
    milestone_due = "milestone_due"
    milestone_overdue = "milestone_overdue"


class Notification(Base):
    """An in-app message for one user (requirements Section 5.6). Email
    delivery is out of scope for this pass — the section allows "in-app
    and/or email … in-app at minimum".

    Addressed to a ``User``, not a practice: a medical-aid reviewer or
    platform admin has no practice, and a notification is inherently
    personal. ``payload`` is a denormalised JSON snapshot (names, times,
    ids) so the list renders without joins and still reads correctly
    after the source row changes.

    ``deliver_at`` supports the time-based kinds without a scheduler: the
    row is written when the triggering event happens, and the list
    endpoint hides it until ``deliver_at`` has passed (``NULL`` = show
    immediately). A reminder that is overtaken — its appointment
    cancelled or rescheduled before it is due — is deleted while still
    pending (unread and not yet delivered).
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[NotificationType] = mapped_column(
        Enum(NotificationType, name="notification_type")
    )
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    # What the notification is about, as a logical (type, id) pair — the
    # same shape as AuditLogEntry.entity_type/entity_id. Lets a
    # superseded reminder be found and deleted without JSON queries, and
    # lets the FE deep-link a row to the right screen. Not a real FK: the
    # subject may be any of several tables.
    subject_type: Mapped[str | None] = mapped_column(String(50), index=True)
    subject_id: Mapped[int | None] = mapped_column(index=True)
    deliver_at: Mapped[datetime | None] = mapped_column(index=True)
    read_at: Mapped[datetime | None] = mapped_column()
    # NULL until the email-dispatch pass sends this notification by email
    # (Section 5.6). Stays NULL on a failed send so the next pass retries.
    emailed_at: Mapped[datetime | None] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), index=True
    )

    user: Mapped["User"] = relationship()
