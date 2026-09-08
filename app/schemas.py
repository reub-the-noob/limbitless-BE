"""Pydantic request/response schemas for the Limb-itless API."""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.models import (
    AppointmentStatus,
    AppointmentType,
    AssignmentRole,
    AuditAction,
    BodyRegion,
    CarePathway,
    CauseOfLimbLoss,
    CoverageStatus,
    DeviceStatus,
    DeviceType,
    InvolvementKind,
    InvolvementStatus,
    LimbLossLevel,
    MapFace,
    MilestoneStatus,
    MilestoneType,
    NotificationType,
    PracticeType,
    PromInstrument,
    SiteType,
    SlotStatus,
    UserRole,
)


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class PatientRegister(BaseModel):
    """Self-service patient sign-up (requirements Section 5.11) - no
    practice/site, no linked record yet; that comes from claiming a
    walk-in record afterward, if one exists."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    role: UserRole
    is_active: bool
    practice_id: int | None
    site_id: int | None
    practice_name: str | None = None
    site_name: str | None = None
    scheme_name: str | None = None


class PatientCreate(BaseModel):
    first_name: str = Field(min_length=1, max_length=120)
    last_name: str = Field(min_length=1, max_length=120)
    date_of_birth: date
    national_id: str | None = Field(default=None, max_length=20)
    passport_number: str | None = Field(default=None, max_length=40)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=500)
    medical_history: str | None = None
    comorbidities: str | None = None
    site_id: int | None = None

    @model_validator(mode="after")
    def _identity_present(self) -> "PatientCreate":
        if not self.national_id and not self.passport_number:
            raise ValueError("national_id or passport_number is required")
        return self


class PatientUpdate(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=120)
    last_name: str | None = Field(default=None, min_length=1, max_length=120)
    date_of_birth: date | None = None
    national_id: str | None = Field(default=None, max_length=20)
    passport_number: str | None = Field(default=None, max_length=40)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=500)
    medical_history: str | None = None
    comorbidities: str | None = None
    site_id: int | None = None


class PatientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    practice_id: int
    site_id: int | None
    first_name: str
    last_name: str
    date_of_birth: date
    national_id: str | None
    passport_number: str | None
    contact_email: str | None
    contact_phone: str | None
    address: str | None
    medical_history: str | None
    comorbidities: str | None
    is_active: bool
    user_id: int | None
    created_at: datetime
    updated_at: datetime


class PatientLinkUser(BaseModel):
    user_id: int


class PatientPage(BaseModel):
    items: list[PatientRead]
    total: int
    limit: int
    offset: int


class PortalProfile(PatientRead):
    """The patient's own record for the self-service portal, with the
    practice / site names resolved."""

    practice_name: str | None = None
    site_name: str | None = None


class PortalRecordSummary(BaseModel):
    """One row of ``GET /portal/records`` - the record picker for a login
    linked to more than one clinical record (Section 5.11)."""

    patient_id: int
    first_name: str
    last_name: str
    practice_name: str | None = None
    site_name: str | None = None


class AccountLinkClaim(BaseModel):
    """A self-service claim of an existing walk-in record (Section
    5.11). ``contact_value`` is checked against the candidate record's
    ``contact_email`` or ``contact_phone`` - whichever it looks like -
    as the second factor the identity number alone can't provide."""

    identifier: str = Field(min_length=1, max_length=40)
    contact_value: str = Field(min_length=1, max_length=320)


class PortalInstruments(BaseModel):
    """PROM instruments the portal should offer this patient, chosen from
    their involvement kinds."""

    instruments: list[PromInstrument]


class PortalPromCreate(BaseModel):
    """A PROM submitted by the patient themselves — instrument + answers
    only; it is never tied to a specific device or involvement here."""

    instrument: PromInstrument
    responses: dict[str, Any]
    recorded_at: datetime | None = None
    notes: str | None = None


# --- limb involvements -------------------------------------------------


class InvolvementCreate(BaseModel):
    kind: InvolvementKind
    region: BodyRegion
    level: LimbLossLevel | None = None
    cause: CauseOfLimbLoss | None = None
    onset_date: date | None = None
    status: InvolvementStatus = InvolvementStatus.active
    notes: str | None = None


class InvolvementUpdate(BaseModel):
    kind: InvolvementKind | None = None
    region: BodyRegion | None = None
    level: LimbLossLevel | None = None
    cause: CauseOfLimbLoss | None = None
    onset_date: date | None = None
    status: InvolvementStatus | None = None
    notes: str | None = None


class InvolvementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: int
    kind: InvolvementKind
    region: BodyRegion
    level: LimbLossLevel | None
    cause: CauseOfLimbLoss | None
    onset_date: date | None
    status: InvolvementStatus
    notes: str | None
    created_at: datetime
    updated_at: datetime


class AssignmentCreate(BaseModel):
    user_id: int
    site_id: int | None = None
    start_date: date | None = None
    notes: str | None = None


class AssignmentEnd(BaseModel):
    end_date: date | None = None


class AssignmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: int
    user_id: int
    user_email: str
    practice_id: int
    site_id: int | None
    role: AssignmentRole
    start_date: date
    end_date: date | None
    notes: str | None
    created_at: datetime


class ClinicalStaffRead(BaseModel):
    """A user who can be assigned to a patient: an active clinician or
    prosthetist in the caller's practice. Enough to populate the
    "assign staff" picker (`User` has no name fields, only an email)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    role: UserRole
    site_id: int | None
    site_name: str | None = None


class _DeviceOptionalFields(BaseModel):
    manufacturer: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=200)
    serial_number: str | None = Field(default=None, max_length=120)
    socket_type: str | None = Field(default=None, max_length=200)
    liner_type: str | None = Field(default=None, max_length=200)
    suspension_type: str | None = Field(default=None, max_length=200)
    terminal_device: str | None = Field(default=None, max_length=200)
    joint_type: str | None = Field(default=None, max_length=200)
    trimline: str | None = Field(default=None, max_length=200)
    strap_configuration: str | None = Field(default=None, max_length=200)
    padding_liner: str | None = Field(default=None, max_length=200)
    mount_location: str | None = Field(default=None, max_length=200)
    # Precise body-map position: viewBox fractions in [0, 1], both or
    # neither. Sending one without the other is a 422; sending both as
    # null clears the position. ``map_face`` picks the figure side; the
    # server defaults it to anterior whenever a position is set and
    # forces it back to null whenever the position is cleared.
    map_x: float | None = Field(default=None, ge=0, le=1)
    map_y: float | None = Field(default=None, ge=0, le=1)
    map_face: MapFace | None = Field(default=None)
    cast_scan_date: date | None = None
    delivery_date: date | None = None
    fitted_date: date | None = None
    warranty_start: date | None = None
    warranty_expiry: date | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _map_coords_together(self) -> "_DeviceOptionalFields":
        if (self.map_x is None) != (self.map_y is None):
            raise ValueError("map_x and map_y must be set together")
        return self


class DeviceCreate(_DeviceOptionalFields):
    device_type: DeviceType
    status: DeviceStatus = DeviceStatus.planned


class DeviceUpdate(_DeviceOptionalFields):
    device_type: DeviceType | None = None
    status: DeviceStatus | None = None


class DeviceRead(_DeviceOptionalFields):
    model_config = ConfigDict(from_attributes=True)

    id: int
    involvement_id: int
    device_type: DeviceType
    status: DeviceStatus
    replaces_device_id: int | None
    created_at: datetime
    updated_at: datetime


class InvolvementDetail(InvolvementRead):
    devices: list[DeviceRead]


class MilestoneCreate(BaseModel):
    milestone_type: MilestoneType
    care_pathway: CarePathway = CarePathway.other
    involvement_id: int | None = None
    device_id: int | None = None
    order_index: int = 0
    status: MilestoneStatus = MilestoneStatus.not_started
    target_date: date | None = None
    completed_date: date | None = None
    notes: str | None = None


class MilestoneUpdate(BaseModel):
    milestone_type: MilestoneType | None = None
    care_pathway: CarePathway | None = None
    involvement_id: int | None = None
    device_id: int | None = None
    order_index: int | None = None
    status: MilestoneStatus | None = None
    target_date: date | None = None
    completed_date: date | None = None
    notes: str | None = None


class MilestoneComplete(BaseModel):
    completed_date: date | None = None


class MilestoneRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: int
    involvement_id: int | None
    device_id: int | None
    care_pathway: CarePathway
    milestone_type: MilestoneType
    order_index: int
    status: MilestoneStatus
    target_date: date | None
    completed_date: date | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class PathwayApply(BaseModel):
    care_pathway: CarePathway
    involvement_id: int | None = None
    device_id: int | None = None
    start_date: date | None = None
    interval_days: int = Field(default=14, ge=1, le=365)


class PromCreate(BaseModel):
    instrument: PromInstrument
    responses: dict[str, Any]
    involvement_id: int | None = None
    device_id: int | None = None
    recorded_at: datetime | None = None
    notes: str | None = None


class PromUpdate(BaseModel):
    instrument: PromInstrument | None = None
    responses: dict[str, Any] | None = None
    involvement_id: int | None = None
    device_id: int | None = None
    recorded_at: datetime | None = None
    notes: str | None = None


class PromRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: int
    involvement_id: int | None
    device_id: int | None
    instrument: PromInstrument
    responses: dict[str, Any]
    score: float | None
    flagged: bool
    flag_reason: str | None
    recorded_at: datetime
    recorded_by_id: int | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class NoteCreate(BaseModel):
    body: str = Field(min_length=1)
    involvement_id: int | None = None


class NoteUpdate(BaseModel):
    body: str | None = Field(default=None, min_length=1)
    involvement_id: int | None = None


class NoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: int
    involvement_id: int | None
    author_id: int | None
    body: str
    created_at: datetime
    updated_at: datetime


class TimelineEvent(BaseModel):
    kind: Literal["milestone", "prom", "note"]
    occurred_at: datetime
    ref_id: int
    title: str
    milestone: MilestoneRead | None = None
    prom: PromRead | None = None
    note: NoteRead | None = None


# --- medical-aid reviewer access ------------------------------------


class ReviewGrantCreate(BaseModel):
    reviewer_id: int


class ReviewGrantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: int
    reviewer_id: int
    reviewer_email: str
    granted_by_id: int | None
    created_at: datetime


class ReviewPatientSummary(BaseModel):
    """One row of a reviewer's patient list."""

    id: int
    first_name: str
    last_name: str
    date_of_birth: date
    practice_id: int
    practice_name: str | None
    involvement_count: int


class ReviewBundle(BaseModel):
    """Everything a reviewer sees for one granted patient, read-only."""

    patient: PatientRead
    practice_name: str | None
    site_name: str | None
    involvements: list[InvolvementDetail]
    milestones: list[MilestoneRead]
    proms: list[PromRead]
    notes: list[NoteRead]


class OverdueMilestone(BaseModel):
    milestone_id: int
    patient_id: int
    patient_name: str
    milestone_type: MilestoneType
    status: MilestoneStatus
    target_date: date
    days_overdue: int


class UpcomingMilestone(BaseModel):
    milestone_id: int
    patient_id: int
    patient_name: str
    milestone_type: MilestoneType
    status: MilestoneStatus
    target_date: date
    days_until: int


class FlaggedProm(BaseModel):
    prom_id: int
    patient_id: int
    patient_name: str
    instrument: PromInstrument
    score: float | None
    flag_reason: str | None
    recorded_at: datetime


class DashboardSummary(BaseModel):
    active_patients: int
    patients_by_phase: dict[str, int]
    overdue_count: int
    overdue: list[OverdueMilestone]
    upcoming_count: int
    upcoming: list[UpcomingMilestone]
    flagged_prom_count: int
    flagged_proms: list[FlaggedProm]


class ReportBreakdown(BaseModel):
    """One row of a grouped count. ``key`` is a raw enum value (the FE
    humanises it) so the camelizer leaves it alone."""

    key: str
    count: int


class CaseloadReport(BaseModel):
    active_patients: int
    inactive_patients: int
    new_patients: int  # created within `since_days`
    by_involvement_kind: list[ReportBreakdown]  # distinct active patients


class MilestoneAdherenceReport(BaseModel):
    completed: int  # complete, with both a target and a completed date
    completed_on_time: int
    completed_late: int
    avg_days_late: float | None  # over the late ones only
    open_overdue: int  # not complete, target date in the past


class OutcomeMeasureReport(BaseModel):
    records: int
    recorded_in_period: int  # recorded_at within `since_days`
    flagged: int
    patients_with_flag: int  # distinct active patients
    by_instrument: list[ReportBreakdown]


class DeviceReport(BaseModel):
    total: int
    prostheses: int
    orthoses: int
    by_type: list[ReportBreakdown]
    by_status: list[ReportBreakdown]


class ReportSummary(BaseModel):
    since_days: int
    caseload: CaseloadReport
    milestones: MilestoneAdherenceReport
    outcome_measures: OutcomeMeasureReport
    devices: DeviceReport


# --- per-patient progress report (requirements Section 5.5) --------------


class MilestoneReportSummary(BaseModel):
    total: int
    completed: int
    completed_on_time: int
    completed_late: int
    in_progress: int
    not_started: int
    overdue: int


class PromTrendPoint(BaseModel):
    recorded_at: datetime
    score: float | None
    flagged: bool


class PromTrend(BaseModel):
    """One instrument's readings, oldest first - the same shape the
    existing FE trend chart (built for the patient-detail PROM panel)
    already expects."""

    instrument: PromInstrument
    latest_score: float | None
    latest_recorded_at: datetime
    flagged: bool
    points: list[PromTrendPoint]


class PatientReport(BaseModel):
    """A per-patient progress report - milestones met, current PROM
    trends - viewable on screen and exportable (print/PDF) for sharing
    with a receiving clinician at handover."""

    patient: PatientRead
    practice_name: str | None
    site_name: str | None
    generated_at: datetime
    involvements: list[InvolvementDetail]
    milestone_summary: MilestoneReportSummary
    milestones: list[MilestoneRead]
    prom_trends: list[PromTrend]


class AdminUserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: UserRole
    site_id: int | None = None


class AdminUserUpdate(BaseModel):
    email: EmailStr | None = None
    role: UserRole | None = None
    site_id: int | None = None
    is_active: bool | None = None


class AdminPasswordSet(BaseModel):
    password: str = Field(min_length=8, max_length=128)


class UserPage(BaseModel):
    items: list[UserRead]
    total: int
    limit: int
    offset: int


class AuditEntryRead(BaseModel):
    id: int
    actor_id: int | None
    actor_email: str | None  # None if the user has since been deleted
    action: AuditAction
    entity_type: str
    entity_id: int | None
    timestamp: datetime


class AuditPage(BaseModel):
    items: list[AuditEntryRead]
    total: int
    limit: int
    offset: int


class ActorRef(BaseModel):
    id: int
    email: str


class AuditFacets(BaseModel):
    """Distinct values in the caller's practice, to populate the filter
    dropdowns."""

    entity_types: list[str]
    actors: list[ActorRef]


class PracticeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    type: PracticeType
    address: str | None = Field(default=None, max_length=500)


class PracticeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    type: PracticeType | None = None
    address: str | None = Field(default=None, max_length=500)
    # Section 5.10: how much notice a patient must give to cancel without
    # it being recorded as a late cancellation.
    cancellation_notice_hours: int | None = Field(default=None, ge=0)


class PracticeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: PracticeType
    address: str | None
    cancellation_notice_hours: int
    created_at: datetime
    updated_at: datetime


class SiteInline(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    type: SiteType
    address: str | None = Field(default=None, max_length=500)


class SiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    type: SiteType | None = None
    address: str | None = Field(default=None, max_length=500)


class SiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    practice_id: int
    name: str
    type: SiteType
    address: str | None
    created_at: datetime
    updated_at: datetime


class AdminCredentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class PracticeOnboard(BaseModel):
    practice: PracticeCreate
    first_site: SiteInline
    first_admin: AdminCredentials


class OnboardResult(BaseModel):
    practice: PracticeRead
    first_site: SiteRead
    first_admin: UserRead


class PracticeSummary(PracticeRead):
    site_count: int
    user_count: int
    patient_count: int


class PracticeDetail(PracticeSummary):
    sites: list[SiteRead]


class PracticePage(BaseModel):
    items: list[PracticeSummary]
    total: int
    limit: int
    offset: int


# --- medical-aid membership -------------------------------------------


class MedicalAidMembershipCreate(BaseModel):
    scheme_name: str = Field(min_length=1, max_length=200)
    plan_option: str = Field(min_length=1, max_length=200)
    membership_number: str = Field(min_length=1, max_length=100)


class MedicalAidMembershipUpdate(BaseModel):
    scheme_name: str | None = Field(default=None, min_length=1, max_length=200)
    plan_option: str | None = Field(default=None, min_length=1, max_length=200)
    membership_number: str | None = Field(
        default=None, min_length=1, max_length=100
    )


class MedicalAidMembershipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: int
    scheme_name: str
    plan_option: str
    membership_number: str
    created_at: datetime
    updated_at: datetime


# --- availability -----------------------------------------------------


class AvailabilitySlotCreate(BaseModel):
    site_id: int | None = None
    start_time: datetime
    end_time: datetime
    appointment_type: AppointmentType
    status: SlotStatus = SlotStatus.open
    notes: str | None = None

    @model_validator(mode="after")
    def _check_times(self) -> "AvailabilitySlotCreate":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class AvailabilitySlotUpdate(BaseModel):
    start_time: datetime | None = None
    end_time: datetime | None = None
    appointment_type: AppointmentType | None = None
    status: SlotStatus | None = None
    notes: str | None = None


class AvailabilitySlotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    practice_id: int
    site_id: int | None
    practitioner_id: int
    practitioner_email: str
    start_time: datetime
    end_time: datetime
    appointment_type: AppointmentType
    status: SlotStatus
    notes: str | None
    created_at: datetime
    updated_at: datetime


# --- appointments -------------------------------------------------------


class AppointmentBook(BaseModel):
    slot_id: int
    notes: str | None = Field(default=None, max_length=2000)


class AppointmentCreate(AppointmentBook):
    """Staff booking a slot on a patient's behalf (front-desk/phone)."""

    patient_id: int


class AppointmentCancel(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class AppointmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    practice_id: int
    site_id: int | None
    patient_id: int
    patient_name: str
    practitioner_id: int
    practitioner_email: str
    slot_id: int | None
    appointment_type: AppointmentType
    scheduled_start: datetime
    scheduled_end: datetime
    status: AppointmentStatus
    cancellation_reason: str | None
    cancelled_at: datetime | None
    late_cancellation: bool
    rescheduled_to_id: int | None
    rescheduled_from_id: int | None
    coverage_status: CoverageStatus | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class AppointmentReschedule(BaseModel):
    new_slot_id: int


class RescheduleResult(BaseModel):
    """Reschedule is one atomic action - both sides of it back at once
    so the caller doesn't need a second request to see the new booking."""

    previous: AppointmentRead
    new: AppointmentRead


# --- coverage determination (medical-aid reviewer) -----------------------


class CoverageApprove(BaseModel):
    authorization_number: str | None = Field(default=None, max_length=100)
    valid_until: date | None = None
    notes: str | None = Field(default=None, max_length=2000)


class CoverageDeny(BaseModel):
    notes: str | None = Field(default=None, max_length=2000)


class CoverageDeterminationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    appointment_id: int
    patient_id: int
    patient_name: str
    practice_name: str | None
    scheme_name: str
    appointment_type: AppointmentType
    scheduled_start: datetime
    status: CoverageStatus
    authorization_number: str | None
    valid_until: date | None
    decided_by_id: int | None
    decided_at: datetime | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


# --- notifications (Section 5.6) -------------------------------------


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: NotificationType
    payload: dict[str, Any]
    subject_type: str | None
    subject_id: int | None
    deliver_at: datetime | None
    read_at: datetime | None
    created_at: datetime


class NotificationPage(BaseModel):
    items: list[NotificationRead]
    total: int
    unread: int
    limit: int
    offset: int


class UnreadCount(BaseModel):
    unread: int


class NotificationMaintenanceResult(BaseModel):
    """Outcome of one maintenance tick (``POST /notifications/dispatch
    -due``): milestone due/overdue notifications raised, then emails
    sent for everything now due."""

    milestones_due: int = 0
    milestones_overdue: int = 0
    emails_sent: int = 0
    emails_failed: int = 0
    emails_skipped: int = 0
