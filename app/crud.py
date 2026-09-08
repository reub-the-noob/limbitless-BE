"""Database access helpers for the Limb-itless API.

Patient reads and writes go through :func:`get_patient` / :func:`list_patients`
etc., which apply the practice scope via :mod:`app.scoping` so isolation
is enforced in one place rather than per endpoint.
"""

import logging
from collections.abc import Sequence
from datetime import date, datetime, timedelta

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app import proms, schemas, security

logger = logging.getLogger(__name__)
from app.models import (
    AccountLinkRequest,
    AccountLinkStatus,
    AccountPatientLink,
    Appointment,
    AppointmentStatus,
    AssignmentRole,
    AuditAction,
    AuditLogEntry,
    AvailabilitySlot,
    CarePathway,
    ClinicalNote,
    CoverageDetermination,
    CoverageStatus,
    Device,
    DeviceStatus,
    DeviceType,
    LimbInvolvement,
    MapFace,
    MedicalAidMembership,
    MilestoneStatus,
    MilestoneType,
    Notification,
    NotificationType,
    Patient,
    PatientAssignment,
    Practice,
    PromInstrument,
    PromRecord,
    RecoveryMilestone,
    ReviewGrant,
    Site,
    SiteType,
    SlotStatus,
    User,
    UserRole,
    VerificationMethod,
)
from app.scoping import scope_to_practice


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def create_user(
    db: Session,
    *,
    email: str,
    password: str,
    role: UserRole,
    practice_id: int | None = None,
    site_id: int | None = None,
) -> User:
    user = User(
        email=email,
        hashed_password=security.hash_password(password),
        role=role,
        practice_id=practice_id,
        site_id=site_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def site_belongs_to_practice(db: Session, *, site_id: int, practice_id: int) -> bool:
    stmt = select(Site.id).where(
        Site.id == site_id, Site.practice_id == practice_id
    )
    return db.scalar(stmt) is not None


def create_patient(
    db: Session, *, practice_id: int, data: schemas.PatientCreate
) -> Patient:
    patient = Patient(**data.model_dump(), practice_id=practice_id)
    db.add(patient)
    db.flush()
    db.refresh(patient)
    return patient


def get_patient(db: Session, *, practice_id: int, patient_id: int) -> Patient | None:
    stmt = scope_to_practice(
        select(Patient).where(Patient.id == patient_id), Patient, practice_id
    )
    return db.scalar(stmt)


def list_linked_patients(db: Session, *, user_id: int) -> Sequence[Patient]:
    """Every clinical record a ``patient``-role login is claimed to
    (requirements Section 5.11), practice then surname order."""
    return db.scalars(
        select(Patient)
        .join(AccountPatientLink, AccountPatientLink.patient_id == Patient.id)
        .join(Practice, Practice.id == Patient.practice_id)
        .where(AccountPatientLink.user_id == user_id, Patient.is_active.is_(True))
        .order_by(Practice.name, Patient.last_name, Patient.id)
    ).all()


def get_linked_patient(
    db: Session, *, user_id: int, patient_id: int
) -> Patient | None:
    """One specific record a login is linked to (or ``None``)."""
    return db.scalar(
        select(Patient)
        .join(AccountPatientLink, AccountPatientLink.patient_id == Patient.id)
        .where(
            AccountPatientLink.user_id == user_id,
            Patient.id == patient_id,
            Patient.is_active.is_(True),
        )
    )


def patient_link_owner(db: Session, *, patient_id: int) -> int | None:
    """The ``users.id`` currently linked to this record, if any."""
    return db.scalar(
        select(AccountPatientLink.user_id).where(
            AccountPatientLink.patient_id == patient_id
        )
    )


def link_patient(
    db: Session,
    *,
    patient_id: int,
    user_id: int,
    verification_method: VerificationMethod | None = None,
) -> AccountPatientLink:
    link = AccountPatientLink(
        patient_id=patient_id,
        user_id=user_id,
        verification_method=verification_method,
    )
    db.add(link)
    db.flush()
    return link


def unlink_patient(db: Session, *, patient_id: int) -> bool:
    """Remove the login link from a record. Returns whether one existed."""
    link = db.scalar(
        select(AccountPatientLink).where(
            AccountPatientLink.patient_id == patient_id
        )
    )
    if link is None:
        return False
    db.delete(link)
    db.flush()
    return True


def get_linkable_patient_user(
    db: Session, *, user_id: int, practice_id: int
) -> User | None:
    """An active ``patient``-role user in ``practice_id`` (the only kind
    of account a patient record may be linked to)."""
    return db.scalar(
        select(User).where(
            User.id == user_id,
            User.practice_id == practice_id,
            User.is_active.is_(True),
            User.role == UserRole.patient,
        )
    )


def list_patients(
    db: Session,
    *,
    practice_id: int,
    query: str | None = None,
    active: bool | None = True,
    assigned_to: int | None = None,
    device_type: DeviceType | None = None,
    phase: MilestoneType | None = None,
    flagged_prom: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[Sequence[Patient], int]:
    stmt = scope_to_practice(select(Patient), Patient, practice_id)
    if active is not None:
        stmt = stmt.where(Patient.is_active.is_(active))
    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            or_(Patient.first_name.ilike(like), Patient.last_name.ilike(like))
        )
    if assigned_to is not None:
        stmt = stmt.where(
            Patient.id.in_(
                select(PatientAssignment.patient_id).where(
                    PatientAssignment.user_id == assigned_to,
                    PatientAssignment.end_date.is_(None),
                )
            )
        )
    if device_type is not None:
        stmt = stmt.where(
            Patient.id.in_(
                select(LimbInvolvement.patient_id)
                .join(
                    Device,
                    Device.involvement_id == LimbInvolvement.id,
                )
                .where(Device.device_type == device_type)
            )
        )
    if phase is not None:
        stmt = stmt.where(
            Patient.id.in_(
                select(RecoveryMilestone.patient_id).where(
                    RecoveryMilestone.milestone_type == phase,
                    RecoveryMilestone.status == MilestoneStatus.in_progress,
                )
            )
        )
    if flagged_prom:
        stmt = stmt.where(
            Patient.id.in_(
                select(PromRecord.patient_id).where(PromRecord.flagged.is_(True))
            )
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Patient.last_name, Patient.first_name)
        .limit(limit)
        .offset(offset)
    ).all()
    return rows, total


def update_patient(
    db: Session, patient: Patient, data: schemas.PatientUpdate
) -> Patient:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(patient, field, value)
    db.flush()
    db.refresh(patient)
    return patient


def set_patient_active(db: Session, patient: Patient, *, active: bool) -> Patient:
    patient.is_active = active
    db.flush()
    db.refresh(patient)
    return patient


def get_assignable_user(
    db: Session, *, user_id: int, practice_id: int
) -> User | None:
    """An active clinician or prosthetist in ``practice_id``."""
    stmt = select(User).where(
        User.id == user_id,
        User.practice_id == practice_id,
        User.is_active.is_(True),
        User.role.in_([UserRole.clinician, UserRole.prosthetist]),
    )
    return db.scalar(stmt)


def list_clinical_staff(db: Session, *, practice_id: int) -> Sequence[User]:
    """Every active clinician / prosthetist in ``practice_id``, for the
    patient-assignment picker. Ordered by email (``User`` has no name)."""
    stmt = (
        select(User)
        .where(
            User.practice_id == practice_id,
            User.is_active.is_(True),
            User.role.in_([UserRole.clinician, UserRole.prosthetist]),
        )
        .order_by(User.email)
    )
    return db.scalars(stmt).all()


def create_assignment(
    db: Session,
    *,
    patient: Patient,
    user: User,
    site_id: int | None,
    start_date: date | None,
    notes: str | None,
) -> PatientAssignment:
    assignment = PatientAssignment(
        patient_id=patient.id,
        user_id=user.id,
        role=AssignmentRole(user.role.value),
        practice_id=patient.practice_id,
        site_id=site_id,
        notes=notes,
    )
    if start_date is not None:
        assignment.start_date = start_date
    db.add(assignment)
    db.flush()
    db.refresh(assignment)
    return assignment


def get_assignment(
    db: Session, *, assignment_id: int, patient_id: int, practice_id: int
) -> PatientAssignment | None:
    stmt = scope_to_practice(
        select(PatientAssignment).where(
            PatientAssignment.id == assignment_id,
            PatientAssignment.patient_id == patient_id,
        ),
        PatientAssignment,
        practice_id,
    )
    return db.scalar(stmt)


def list_assignments(
    db: Session, *, patient_id: int, active: bool | None = None
) -> Sequence[PatientAssignment]:
    stmt = select(PatientAssignment).where(
        PatientAssignment.patient_id == patient_id
    )
    if active is True:
        stmt = stmt.where(PatientAssignment.end_date.is_(None))
    elif active is False:
        stmt = stmt.where(PatientAssignment.end_date.is_not(None))
    return db.scalars(
        stmt.order_by(
            PatientAssignment.start_date.desc(), PatientAssignment.id.desc()
        )
    ).all()


def end_assignment(
    db: Session, assignment: PatientAssignment, *, end_date: date | None
) -> PatientAssignment:
    if assignment.end_date is None:
        assignment.end_date = end_date or date.today()
        db.flush()
        db.refresh(assignment)
    return assignment


# --- limb involvements ---------------------------------------------------


def get_involvement(
    db: Session, *, patient_id: int, involvement_id: int
) -> LimbInvolvement | None:
    return db.scalar(
        select(LimbInvolvement).where(
            LimbInvolvement.id == involvement_id,
            LimbInvolvement.patient_id == patient_id,
        )
    )


def list_involvements(
    db: Session, *, patient_id: int
) -> Sequence[LimbInvolvement]:
    return db.scalars(
        select(LimbInvolvement)
        .where(LimbInvolvement.patient_id == patient_id)
        .order_by(LimbInvolvement.id)
    ).all()


def create_involvement(
    db: Session, *, patient_id: int, data: schemas.InvolvementCreate
) -> LimbInvolvement:
    involvement = LimbInvolvement(**data.model_dump(), patient_id=patient_id)
    db.add(involvement)
    db.flush()
    db.refresh(involvement)
    return involvement


def update_involvement(
    db: Session, involvement: LimbInvolvement, data: schemas.InvolvementUpdate
) -> LimbInvolvement:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(involvement, field, value)
    db.flush()
    db.refresh(involvement)
    return involvement


# --- devices -----------------------------------------------------------


def get_device(
    db: Session, *, involvement_id: int, device_id: int
) -> Device | None:
    stmt = select(Device).where(
        Device.id == device_id,
        Device.involvement_id == involvement_id,
    )
    return db.scalar(stmt)


def list_devices(
    db: Session, *, involvement_id: int, status: DeviceStatus | None = None
) -> Sequence[Device]:
    stmt = select(Device).where(
        Device.involvement_id == involvement_id
    )
    if status is not None:
        stmt = stmt.where(Device.status == status)
    return db.scalars(stmt.order_by(Device.id.desc())).all()


def get_device_for_patient(
    db: Session, *, patient_id: int, device_id: int
) -> Device | None:
    """A device by id, confirmed to belong to one of the patient's
    involvements (used by the milestone / PROM device_id checks)."""
    return db.scalar(
        select(Device)
        .join(
            LimbInvolvement,
            LimbInvolvement.id == Device.involvement_id,
        )
        .where(
            Device.id == device_id,
            LimbInvolvement.patient_id == patient_id,
        )
    )


def list_patient_devices(
    db: Session, *, patient_id: int
) -> Sequence[Device]:
    """Every device across all of a patient's involvements, newest first
    (for the patient-detail overview and the body-map view)."""
    return db.scalars(
        select(Device)
        .join(
            LimbInvolvement,
            LimbInvolvement.id == Device.involvement_id,
        )
        .where(LimbInvolvement.patient_id == patient_id)
        .order_by(Device.id.desc())
    ).all()


def _apply_map_face_rule(device: Device) -> None:
    """Keep ``map_face`` consistent with the position: NULL when there is
    none, defaulting to ``anterior`` when a position is set without one."""
    if device.map_x is None or device.map_y is None:
        device.map_face = None
    elif device.map_face is None:
        device.map_face = MapFace.anterior


def create_device(
    db: Session,
    *,
    involvement_id: int,
    data: schemas.DeviceCreate,
    replaces_device_id: int | None = None,
) -> Device:
    device = Device(
        **data.model_dump(),
        involvement_id=involvement_id,
        replaces_device_id=replaces_device_id,
    )
    _apply_map_face_rule(device)
    db.add(device)
    db.flush()
    db.refresh(device)
    return device


def update_device(
    db: Session, device: Device, data: schemas.DeviceUpdate
) -> Device:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(device, field, value)
    _apply_map_face_rule(device)
    db.flush()
    db.refresh(device)
    return device


def replace_device(
    db: Session, *, old: Device, data: schemas.DeviceCreate
) -> Device:
    """Mark ``old`` replaced and create its successor, linked back to it."""
    old.status = DeviceStatus.replaced
    db.flush()
    return create_device(
        db,
        involvement_id=old.involvement_id,
        data=data,
        replaces_device_id=old.id,
    )


def get_milestone(
    db: Session, *, patient_id: int, milestone_id: int
) -> RecoveryMilestone | None:
    stmt = select(RecoveryMilestone).where(
        RecoveryMilestone.id == milestone_id,
        RecoveryMilestone.patient_id == patient_id,
    )
    return db.scalar(stmt)


def list_milestones(
    db: Session,
    *,
    patient_id: int,
    care_pathway: CarePathway | None = None,
    status: MilestoneStatus | None = None,
) -> Sequence[RecoveryMilestone]:
    stmt = select(RecoveryMilestone).where(
        RecoveryMilestone.patient_id == patient_id
    )
    if care_pathway is not None:
        stmt = stmt.where(RecoveryMilestone.care_pathway == care_pathway)
    if status is not None:
        stmt = stmt.where(RecoveryMilestone.status == status)
    return db.scalars(
        stmt.order_by(
            RecoveryMilestone.order_index,
            RecoveryMilestone.target_date,
            RecoveryMilestone.id,
        )
    ).all()


def create_milestone(
    db: Session, *, patient_id: int, data: schemas.MilestoneCreate
) -> RecoveryMilestone:
    milestone = RecoveryMilestone(**data.model_dump(), patient_id=patient_id)
    db.add(milestone)
    db.flush()
    db.refresh(milestone)
    return milestone


def update_milestone(
    db: Session, milestone: RecoveryMilestone, data: schemas.MilestoneUpdate
) -> RecoveryMilestone:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(milestone, field, value)
    db.flush()
    db.refresh(milestone)
    return milestone


def complete_milestone(
    db: Session, milestone: RecoveryMilestone, *, completed_date: date | None
) -> RecoveryMilestone:
    milestone.status = MilestoneStatus.complete
    milestone.completed_date = completed_date or date.today()
    db.flush()
    db.refresh(milestone)
    return milestone


def patient_has_pathway(
    db: Session, *, patient_id: int, care_pathway: CarePathway
) -> bool:
    stmt = select(RecoveryMilestone.id).where(
        RecoveryMilestone.patient_id == patient_id,
        RecoveryMilestone.care_pathway == care_pathway,
    )
    return db.scalar(stmt) is not None


def apply_pathway(
    db: Session,
    *,
    patient_id: int,
    care_pathway: CarePathway,
    template: Sequence[MilestoneType],
    device_id: int | None,
    involvement_id: int | None,
    start_date: date | None,
    interval_days: int,
) -> Sequence[RecoveryMilestone]:
    start = start_date or date.today()
    created = [
        RecoveryMilestone(
            patient_id=patient_id,
            care_pathway=care_pathway,
            milestone_type=milestone_type,
            order_index=index,
            status=MilestoneStatus.not_started,
            target_date=start + timedelta(days=interval_days * index),
            device_id=device_id,
            involvement_id=involvement_id,
        )
        for index, milestone_type in enumerate(template)
    ]
    db.add_all(created)
    db.flush()
    for milestone in created:
        db.refresh(milestone)
    return created


def get_prom(
    db: Session, *, patient_id: int, prom_id: int
) -> PromRecord | None:
    stmt = select(PromRecord).where(
        PromRecord.id == prom_id, PromRecord.patient_id == patient_id
    )
    return db.scalar(stmt)


def list_proms(
    db: Session,
    *,
    patient_id: int,
    instrument: PromInstrument | None = None,
    flagged: bool | None = None,
) -> Sequence[PromRecord]:
    stmt = select(PromRecord).where(PromRecord.patient_id == patient_id)
    if instrument is not None:
        stmt = stmt.where(PromRecord.instrument == instrument)
    if flagged is not None:
        stmt = stmt.where(PromRecord.flagged.is_(flagged))
    # a single-instrument list is trend data, so oldest first; otherwise newest
    order = (
        PromRecord.recorded_at.asc()
        if instrument is not None
        else PromRecord.recorded_at.desc()
    )
    return db.scalars(stmt.order_by(order, PromRecord.id)).all()


def create_prom(
    db: Session,
    *,
    patient_id: int,
    recorded_by_id: int | None,
    data: schemas.PromCreate,
) -> PromRecord:
    """Create a PROM, deriving score / flag from :mod:`app.proms`.

    Raises ``ValueError`` if the responses fail instrument validation.
    """
    score, flagged, reason = proms.evaluate(data.instrument, data.responses)
    prom = PromRecord(
        patient_id=patient_id,
        recorded_by_id=recorded_by_id,
        instrument=data.instrument,
        responses=data.responses,
        involvement_id=data.involvement_id,
        device_id=data.device_id,
        notes=data.notes,
        score=score,
        flagged=flagged,
        flag_reason=reason,
    )
    if data.recorded_at is not None:
        prom.recorded_at = data.recorded_at
    db.add(prom)
    db.flush()
    db.refresh(prom)
    return prom


def update_prom(
    db: Session, prom: PromRecord, data: schemas.PromUpdate
) -> PromRecord:
    fields = data.model_dump(exclude_unset=True)
    for field, value in fields.items():
        setattr(prom, field, value)
    if "instrument" in fields or "responses" in fields:
        score, flagged, reason = proms.evaluate(prom.instrument, prom.responses)
        prom.score, prom.flagged, prom.flag_reason = score, flagged, reason
    db.flush()
    db.refresh(prom)
    return prom


def get_note(
    db: Session, *, patient_id: int, note_id: int
) -> ClinicalNote | None:
    stmt = select(ClinicalNote).where(
        ClinicalNote.id == note_id, ClinicalNote.patient_id == patient_id
    )
    return db.scalar(stmt)


def list_notes(db: Session, *, patient_id: int) -> Sequence[ClinicalNote]:
    stmt = (
        select(ClinicalNote)
        .where(ClinicalNote.patient_id == patient_id)
        .order_by(ClinicalNote.created_at.desc(), ClinicalNote.id.desc())
    )
    return db.scalars(stmt).all()


def create_note(
    db: Session,
    *,
    patient_id: int,
    author_id: int | None,
    body: str,
    involvement_id: int | None = None,
) -> ClinicalNote:
    note = ClinicalNote(
        patient_id=patient_id,
        author_id=author_id,
        body=body,
        involvement_id=involvement_id,
    )
    db.add(note)
    db.flush()
    db.refresh(note)
    return note


def update_note(
    db: Session,
    note: ClinicalNote,
    *,
    body: str | None = None,
    involvement_id: int | None = None,
    set_involvement: bool = False,
) -> ClinicalNote:
    if body is not None:
        note.body = body
    if set_involvement:
        note.involvement_id = involvement_id
    db.flush()
    db.refresh(note)
    return note


def get_practice_user(
    db: Session, *, practice_id: int, user_id: int
) -> User | None:
    stmt = select(User).where(
        User.id == user_id, User.practice_id == practice_id
    )
    return db.scalar(stmt)


def list_practice_users(
    db: Session,
    *,
    practice_id: int,
    role: UserRole | None = None,
    active: bool | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[Sequence[User], int]:
    stmt = select(User).where(User.practice_id == practice_id)
    if role is not None:
        stmt = stmt.where(User.role == role)
    if active is not None:
        stmt = stmt.where(User.is_active.is_(active))
    if query:
        stmt = stmt.where(User.email.ilike(f"%{query}%"))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(User.email).limit(limit).offset(offset)
    ).all()
    return rows, total


def create_practice_user(
    db: Session,
    *,
    practice_id: int,
    email: str,
    password: str,
    role: UserRole,
    site_id: int | None,
) -> User:
    user = User(
        email=email,
        hashed_password=security.hash_password(password),
        role=role,
        practice_id=practice_id,
        site_id=site_id,
    )
    db.add(user)
    db.flush()
    db.refresh(user)
    return user


def update_user(
    db: Session, user: User, data: schemas.AdminUserUpdate
) -> User:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    db.flush()
    db.refresh(user)
    return user


def set_user_password(db: Session, user: User, *, password: str) -> User:
    user.hashed_password = security.hash_password(password)
    db.flush()
    db.refresh(user)
    return user


def add_user(
    db: Session,
    *,
    email: str,
    password: str,
    role: UserRole,
    practice_id: int | None = None,
    site_id: int | None = None,
) -> User:
    """Build a user and flush (no commit) - for callers that own the txn."""
    user = User(
        email=email,
        hashed_password=security.hash_password(password),
        role=role,
        practice_id=practice_id,
        site_id=site_id,
    )
    db.add(user)
    db.flush()
    db.refresh(user)
    return user


def create_practice(db: Session, data: schemas.PracticeCreate) -> Practice:
    practice = Practice(
        name=data.name, type=data.type, address=data.address
    )
    db.add(practice)
    db.flush()
    db.refresh(practice)
    return practice


def create_site(
    db: Session, *, practice_id: int, data: schemas.SiteInline
) -> Site:
    site = Site(
        practice_id=practice_id,
        name=data.name,
        type=data.type,
        address=data.address,
    )
    db.add(site)
    db.flush()
    db.refresh(site)
    return site


def get_site(db: Session, *, practice_id: int, site_id: int) -> Site | None:
    stmt = select(Site).where(
        Site.id == site_id, Site.practice_id == practice_id
    )
    return db.scalar(stmt)


def list_sites(
    db: Session, *, practice_id: int, type: SiteType | None = None
) -> Sequence[Site]:
    stmt = select(Site).where(Site.practice_id == practice_id)
    if type is not None:
        stmt = stmt.where(Site.type == type)
    return db.scalars(stmt.order_by(Site.name)).all()


def update_site(db: Session, site: Site, data: schemas.SiteUpdate) -> Site:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(site, field, value)
    db.flush()
    db.refresh(site)
    return site


def get_practice(db: Session, practice_id: int) -> Practice | None:
    return db.get(Practice, practice_id)


def list_practices(
    db: Session,
    *,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[Sequence[Practice], int]:
    stmt = select(Practice)
    if query:
        stmt = stmt.where(Practice.name.ilike(f"%{query}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Practice.name).limit(limit).offset(offset)
    ).all()
    return rows, total


def practice_counts(db: Session, practice_id: int) -> dict[str, int]:
    site_count = db.scalar(
        select(func.count()).select_from(Site).where(Site.practice_id == practice_id)
    )
    user_count = db.scalar(
        select(func.count()).select_from(User).where(User.practice_id == practice_id)
    )
    patient_count = db.scalar(
        select(func.count())
        .select_from(Patient)
        .where(Patient.practice_id == practice_id)
    )
    return {
        "site_count": site_count or 0,
        "user_count": user_count or 0,
        "patient_count": patient_count or 0,
    }


def update_practice(
    db: Session, practice: Practice, data: schemas.PracticeUpdate
) -> Practice:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(practice, field, value)
    db.flush()
    db.refresh(practice)
    return practice


# --- audit trail (practice-administrator read-only) -------------------


def list_audit_entries(
    db: Session,
    *,
    practice_id: int,
    actor_id: int | None = None,
    action: AuditAction | None = None,
    entity_type: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[Sequence, int]:
    """Audit rows for one practice, newest first, each paired with the
    actor's current email (``None`` if the user was deleted)."""
    stmt = (
        select(AuditLogEntry, User.email)
        .outerjoin(User, User.id == AuditLogEntry.actor_id)
        .where(AuditLogEntry.practice_id == practice_id)
    )
    if actor_id is not None:
        stmt = stmt.where(AuditLogEntry.actor_id == actor_id)
    if action is not None:
        stmt = stmt.where(AuditLogEntry.action == action)
    if entity_type:
        stmt = stmt.where(AuditLogEntry.entity_type == entity_type)
    if date_from is not None:
        stmt = stmt.where(AuditLogEntry.timestamp >= date_from)
    if date_to is not None:
        stmt = stmt.where(
            AuditLogEntry.timestamp < date_to + timedelta(days=1)
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(
        stmt.order_by(
            AuditLogEntry.timestamp.desc(), AuditLogEntry.id.desc()
        )
        .limit(limit)
        .offset(offset)
    ).all()
    return rows, total


def audit_facets(
    db: Session, *, practice_id: int
) -> tuple[Sequence[str], Sequence]:
    """Distinct entity types and the actors that appear in this practice's
    trail, for the filter dropdowns."""
    entity_types = db.scalars(
        select(AuditLogEntry.entity_type)
        .where(AuditLogEntry.practice_id == practice_id)
        .distinct()
        .order_by(AuditLogEntry.entity_type)
    ).all()
    actors = db.execute(
        select(User.id, User.email)
        .where(
            User.id.in_(
                select(AuditLogEntry.actor_id)
                .where(
                    AuditLogEntry.practice_id == practice_id,
                    AuditLogEntry.actor_id.is_not(None),
                )
                .distinct()
            )
        )
        .order_by(User.email)
    ).all()
    return entity_types, actors


# --- medical-aid reviewer access ------------------------------------


def get_active_reviewer(db: Session, *, user_id: int) -> User | None:
    return db.scalar(
        select(User).where(
            User.id == user_id,
            User.is_active.is_(True),
            User.role == UserRole.medical_aid_reviewer,
        )
    )


def create_review_grant(
    db: Session, *, patient_id: int, reviewer_id: int, granted_by_id: int | None
) -> ReviewGrant:
    grant = ReviewGrant(
        patient_id=patient_id,
        reviewer_id=reviewer_id,
        granted_by_id=granted_by_id,
    )
    db.add(grant)
    db.flush()
    db.refresh(grant)
    return grant


def delete_review_grant(
    db: Session, *, patient_id: int, reviewer_id: int
) -> bool:
    grant = db.scalar(
        select(ReviewGrant).where(
            ReviewGrant.patient_id == patient_id,
            ReviewGrant.reviewer_id == reviewer_id,
        )
    )
    if grant is None:
        return False
    db.delete(grant)
    db.flush()
    return True


def list_review_grants(db: Session, *, patient_id: int) -> Sequence:
    """Grants on one patient, paired with the reviewer's email."""
    return db.execute(
        select(ReviewGrant, User.email)
        .join(User, User.id == ReviewGrant.reviewer_id)
        .where(ReviewGrant.patient_id == patient_id)
        .order_by(User.email)
    ).all()


def _reviewer_access_clause(reviewer: User | None, reviewer_id: int):
    """A patient is visible to a reviewer via EITHER an explicit
    :class:`ReviewGrant` (a treating practice's manual, per-patient
    hand-out - closer to the Phase-4 consent concept) OR an automatic
    match between the reviewer's own ``scheme_name`` and the patient's
    active :class:`MedicalAidMembership` (the requirements doc's actual
    Section 5.12 design: scheme-scoped, cross-practice, no grant
    needed). Both mechanisms coexist rather than one replacing the
    other."""
    granted = exists(
        select(ReviewGrant.id).where(
            ReviewGrant.patient_id == Patient.id,
            ReviewGrant.reviewer_id == reviewer_id,
        )
    )
    if reviewer is None or not reviewer.scheme_name:
        return granted
    scheme_matched = exists(
        select(MedicalAidMembership.id).where(
            MedicalAidMembership.patient_id == Patient.id,
            MedicalAidMembership.scheme_name == reviewer.scheme_name,
        )
    )
    return or_(granted, scheme_matched)


def list_reviewer_patients(
    db: Session, *, reviewer_id: int
) -> Sequence:
    """(Patient, practice_name, involvement_count) for every patient a
    reviewer can see - granted or scheme-matched - by surname."""
    reviewer = db.get(User, reviewer_id)
    involvement_count = (
        select(func.count(LimbInvolvement.id))
        .where(LimbInvolvement.patient_id == Patient.id)
        .scalar_subquery()
    )
    return db.execute(
        select(Patient, Practice.name, involvement_count)
        .join(Practice, Practice.id == Patient.practice_id)
        .where(_reviewer_access_clause(reviewer, reviewer_id))
        .order_by(Patient.last_name, Patient.first_name)
    ).all()


def get_reviewer_patient(
    db: Session, *, reviewer_id: int, patient_id: int
) -> Patient | None:
    """A patient by id, only if this reviewer can see them (granted or
    scheme-matched)."""
    reviewer = db.get(User, reviewer_id)
    return db.scalar(
        select(Patient).where(
            Patient.id == patient_id,
            _reviewer_access_clause(reviewer, reviewer_id),
        )
    )


# --- medical-aid membership -------------------------------------------


def get_membership(db: Session, *, patient_id: int) -> MedicalAidMembership | None:
    return db.scalar(
        select(MedicalAidMembership).where(
            MedicalAidMembership.patient_id == patient_id
        )
    )


def create_membership(
    db: Session, *, patient_id: int, data: schemas.MedicalAidMembershipCreate
) -> MedicalAidMembership:
    membership = MedicalAidMembership(patient_id=patient_id, **data.model_dump())
    db.add(membership)
    db.flush()
    db.refresh(membership)
    return membership


def update_membership(
    db: Session,
    membership: MedicalAidMembership,
    data: schemas.MedicalAidMembershipUpdate,
) -> MedicalAidMembership:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(membership, field, value)
    db.flush()
    db.refresh(membership)
    return membership


def delete_membership(db: Session, membership: MedicalAidMembership) -> None:
    db.delete(membership)
    db.flush()


# --- availability slots ------------------------------------------------


def slot_overlaps(
    db: Session,
    *,
    practitioner_id: int,
    start_time: datetime,
    end_time: datetime,
    exclude_id: int | None = None,
) -> bool:
    # Any existing slot for this practitioner — open, booked, or blocked —
    # represents real time already committed, so all statuses count.
    stmt = select(AvailabilitySlot.id).where(
        AvailabilitySlot.practitioner_id == practitioner_id,
        AvailabilitySlot.start_time < end_time,
        AvailabilitySlot.end_time > start_time,
    )
    if exclude_id is not None:
        stmt = stmt.where(AvailabilitySlot.id != exclude_id)
    return db.scalar(stmt) is not None


def get_slot(
    db: Session, *, practice_id: int, slot_id: int
) -> AvailabilitySlot | None:
    return db.scalar(
        select(AvailabilitySlot).where(
            AvailabilitySlot.id == slot_id,
            AvailabilitySlot.practice_id == practice_id,
        )
    )


def list_slots(
    db: Session,
    *,
    practice_id: int,
    practitioner_id: int | None = None,
    status: SlotStatus | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> Sequence[AvailabilitySlot]:
    stmt = select(AvailabilitySlot).where(
        AvailabilitySlot.practice_id == practice_id
    )
    if practitioner_id is not None:
        stmt = stmt.where(AvailabilitySlot.practitioner_id == practitioner_id)
    if status is not None:
        stmt = stmt.where(AvailabilitySlot.status == status)
    if date_from is not None:
        stmt = stmt.where(AvailabilitySlot.start_time >= date_from)
    if date_to is not None:
        stmt = stmt.where(AvailabilitySlot.start_time < date_to)
    return db.scalars(stmt.order_by(AvailabilitySlot.start_time)).all()


def create_slot(
    db: Session,
    *,
    practitioner_id: int,
    practice_id: int,
    site_id: int | None,
    data: schemas.AvailabilitySlotCreate,
) -> AvailabilitySlot:
    slot = AvailabilitySlot(
        practitioner_id=practitioner_id,
        practice_id=practice_id,
        site_id=site_id,
        **data.model_dump(exclude={"site_id"}),
    )
    db.add(slot)
    db.flush()
    db.refresh(slot)
    return slot


def update_slot(
    db: Session, slot: AvailabilitySlot, data: schemas.AvailabilitySlotUpdate
) -> AvailabilitySlot:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(slot, field, value)
    db.flush()
    db.refresh(slot)
    return slot


# --- appointments --------------------------------------------------------


def book_appointment(
    db: Session, *, slot: AvailabilitySlot, patient_id: int, notes: str | None
) -> Appointment:
    """Book a patient into an open slot. Caller must have already checked
    ``slot.status == SlotStatus.open`` inside the same transaction -
    flipping it to ``booked`` here is what makes the slot unavailable to
    a second booking attempt.

    If the patient has a :class:`MedicalAidMembership`, this also opens
    a ``pending`` :class:`CoverageDetermination` for the visit - a
    self-pay patient's appointment never gets one, which is how the rest
    of the system tells the two apart (Section 5.9)."""
    slot.status = SlotStatus.booked
    appointment = Appointment(
        practice_id=slot.practice_id,
        site_id=slot.site_id,
        patient_id=patient_id,
        practitioner_id=slot.practitioner_id,
        slot_id=slot.id,
        appointment_type=slot.appointment_type,
        scheduled_start=slot.start_time,
        scheduled_end=slot.end_time,
        notes=notes,
    )
    db.add(appointment)
    db.flush()

    membership = get_membership(db, patient_id=patient_id)
    if membership is not None:
        db.add(
            CoverageDetermination(
                appointment_id=appointment.id,
                medical_aid_membership_id=membership.id,
            )
        )
        db.flush()

    db.refresh(appointment)
    return appointment


def get_appointment(
    db: Session, *, practice_id: int, appointment_id: int
) -> Appointment | None:
    return db.scalar(
        select(Appointment).where(
            Appointment.id == appointment_id,
            Appointment.practice_id == practice_id,
        )
    )


def get_patient_appointment(
    db: Session, *, patient_id: int, appointment_id: int
) -> Appointment | None:
    return db.scalar(
        select(Appointment).where(
            Appointment.id == appointment_id,
            Appointment.patient_id == patient_id,
        )
    )


def list_appointments(
    db: Session,
    *,
    practice_id: int,
    patient_id: int | None = None,
    practitioner_id: int | None = None,
    status: AppointmentStatus | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> Sequence[Appointment]:
    stmt = select(Appointment).where(Appointment.practice_id == practice_id)
    if patient_id is not None:
        stmt = stmt.where(Appointment.patient_id == patient_id)
    if practitioner_id is not None:
        stmt = stmt.where(Appointment.practitioner_id == practitioner_id)
    if status is not None:
        stmt = stmt.where(Appointment.status == status)
    if date_from is not None:
        stmt = stmt.where(Appointment.scheduled_start >= date_from)
    if date_to is not None:
        stmt = stmt.where(Appointment.scheduled_start < date_to)
    return db.scalars(stmt.order_by(Appointment.scheduled_start)).all()


def list_patient_appointments(
    db: Session, *, patient_id: int
) -> Sequence[Appointment]:
    return db.scalars(
        select(Appointment)
        .where(Appointment.patient_id == patient_id)
        .order_by(Appointment.scheduled_start)
    ).all()


def cancel_appointment(
    db: Session,
    appointment: Appointment,
    *,
    status: AppointmentStatus,
    reason: str | None,
    late: bool = False,
) -> Appointment:
    """Cancel a booked appointment and release its slot back to ``open``
    so it can be rebooked. ``status`` distinguishes who cancelled
    (``cancelled_by_patient`` / ``cancelled_by_practitioner``)."""
    appointment.status = status
    appointment.cancellation_reason = reason
    appointment.cancelled_at = datetime.now()
    appointment.late_cancellation = late
    if appointment.slot is not None:
        appointment.slot.status = SlotStatus.open
    db.flush()
    db.refresh(appointment)
    return appointment


def mark_no_show(db: Session, appointment: Appointment) -> Appointment:
    appointment.status = AppointmentStatus.no_show
    db.flush()
    db.refresh(appointment)
    return appointment


def reschedule_appointment(
    db: Session, appointment: Appointment, *, new_slot: AvailabilitySlot
) -> tuple[Appointment, Appointment]:
    """Reschedule = one atomic action: release the old slot, take the
    new one, and link the two rows. Not a cancel - ``appointment`` gets
    its own ``rescheduled`` status rather than ``cancelled_by_*``, so it
    doesn't read as an unexplained cancel.

    Coverage carry-over (Section 5.10): an ``approved`` determination
    that hasn't expired moves with the visit - literally re-pointed at
    the new appointment - only if the appointment type is unchanged.
    Otherwise it stays on the old (now ``rescheduled``) appointment as
    history and a fresh ``pending`` one opens for the new appointment,
    re-triggering the coverage flow. A self-pay patient (no existing
    determination) gets neither - there's nothing to carry or retrigger.
    """
    if appointment.slot is not None:
        appointment.slot.status = SlotStatus.open
    new_slot.status = SlotStatus.booked
    new_appointment = Appointment(
        practice_id=new_slot.practice_id,
        site_id=new_slot.site_id,
        patient_id=appointment.patient_id,
        practitioner_id=new_slot.practitioner_id,
        slot_id=new_slot.id,
        appointment_type=new_slot.appointment_type,
        scheduled_start=new_slot.start_time,
        scheduled_end=new_slot.end_time,
        notes=appointment.notes,
        rescheduled_from_id=appointment.id,
    )
    db.add(new_appointment)
    db.flush()

    old_coverage = db.scalar(
        select(CoverageDetermination).where(
            CoverageDetermination.appointment_id == appointment.id
        )
    )
    if old_coverage is not None:
        unexpired = (
            old_coverage.valid_until is None
            or old_coverage.valid_until >= date.today()
        )
        same_type = new_appointment.appointment_type == appointment.appointment_type
        if old_coverage.status == CoverageStatus.approved and unexpired and same_type:
            old_coverage.appointment_id = new_appointment.id
        else:
            db.add(
                CoverageDetermination(
                    appointment_id=new_appointment.id,
                    medical_aid_membership_id=old_coverage.medical_aid_membership_id,
                )
            )
        db.flush()

    appointment.status = AppointmentStatus.rescheduled
    appointment.rescheduled_to_id = new_appointment.id
    db.flush()
    db.refresh(appointment)
    db.refresh(new_appointment)
    return appointment, new_appointment


def get_appointment_coverage(
    db: Session, *, appointment_id: int
) -> CoverageDetermination | None:
    return db.scalar(
        select(CoverageDetermination).where(
            CoverageDetermination.appointment_id == appointment_id
        )
    )


# --- coverage determinations (medical-aid reviewer) ---------------------


def get_reviewer_coverage(
    db: Session, *, reviewer_id: int, coverage_id: int
) -> CoverageDetermination | None:
    """A coverage determination by id, only if this reviewer can see the
    patient it belongs to (granted or scheme-matched)."""
    reviewer = db.get(User, reviewer_id)
    return db.scalar(
        select(CoverageDetermination)
        .join(Appointment, Appointment.id == CoverageDetermination.appointment_id)
        .join(Patient, Patient.id == Appointment.patient_id)
        .where(
            CoverageDetermination.id == coverage_id,
            _reviewer_access_clause(reviewer, reviewer_id),
        )
    )


def list_reviewer_coverage(
    db: Session, *, reviewer_id: int, status: CoverageStatus | None = None
) -> Sequence[CoverageDetermination]:
    """Coverage determinations for every patient this reviewer can see,
    newest first. ``status`` typically narrows this to ``pending`` - the
    reviewer's actual queue."""
    reviewer = db.get(User, reviewer_id)
    stmt = (
        select(CoverageDetermination)
        .join(Appointment, Appointment.id == CoverageDetermination.appointment_id)
        .join(Patient, Patient.id == Appointment.patient_id)
        .where(_reviewer_access_clause(reviewer, reviewer_id))
    )
    if status is not None:
        stmt = stmt.where(CoverageDetermination.status == status)
    return db.scalars(
        stmt.order_by(CoverageDetermination.created_at.desc())
    ).all()


def decide_coverage(
    db: Session,
    coverage: CoverageDetermination,
    *,
    status: CoverageStatus,
    decided_by_id: int,
    authorization_number: str | None = None,
    valid_until: date | None = None,
    notes: str | None = None,
) -> CoverageDetermination:
    coverage.status = status
    coverage.decided_by_id = decided_by_id
    coverage.decided_at = datetime.now()
    coverage.authorization_number = authorization_number
    coverage.valid_until = valid_until
    coverage.notes = notes
    db.flush()
    db.refresh(coverage)
    return coverage


# --- walk-in record claim (patient self-service, requirements 5.11) -----


def _digits_only(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _verification_method_for(contact_value: str) -> VerificationMethod:
    return (
        VerificationMethod.contact_email
        if "@" in contact_value
        else VerificationMethod.contact_phone
    )


def _contact_matches(patient: Patient, contact_value: str) -> bool:
    value = contact_value.strip()
    if "@" in value:
        return (
            patient.contact_email is not None
            and patient.contact_email.strip().lower() == value.lower()
        )
    digits = _digits_only(value)
    return bool(digits) and (
        patient.contact_phone is not None
        and _digits_only(patient.contact_phone) == digits
    )


def claim_walk_in_patient(
    db: Session, *, user: User, identifier: str, contact_value: str
) -> Patient | None:
    """Try to claim an unclaimed walk-in record by identity number, second
    -factor gated by a contact detail already on that record. Every
    attempt is logged as an :class:`AccountLinkRequest` regardless of
    outcome; nothing about whether a candidate exists is revealed to the
    caller beyond "linked" or "not linked" (see the model docstring for
    why - the identity number alone isn't a secret)."""
    identifier = identifier.strip()
    method = _verification_method_for(contact_value)

    unclaimed = ~select(AccountPatientLink.id).where(
        AccountPatientLink.patient_id == Patient.id
    ).exists()
    candidates = db.scalars(
        select(Patient).where(
            or_(
                Patient.national_id == identifier,
                Patient.passport_number == identifier,
            ),
            unclaimed,
            Patient.is_active.is_(True),
        )
    ).all()

    matched = next(
        (p for p in candidates if _contact_matches(p, contact_value)), None
    )

    if matched is not None:
        link_patient(
            db,
            patient_id=matched.id,
            user_id=user.id,
            verification_method=method,
        )
        db.add(
            AccountLinkRequest(
                user_id=user.id,
                patient_id=matched.id,
                verification_method=method,
                status=AccountLinkStatus.verified,
                verified_at=datetime.now(),
            )
        )
    else:
        db.add(
            AccountLinkRequest(
                user_id=user.id,
                patient_id=None,
                verification_method=method,
                status=AccountLinkStatus.rejected,
            )
        )
    db.flush()
    return matched


# --- notifications (requirements Section 5.6) -----------------------


def _delivered_clause(now: datetime):
    """A notification is visible once its ``deliver_at`` has passed
    (``NULL`` = immediate). Future-dated reminders stay hidden."""
    return or_(Notification.deliver_at.is_(None), Notification.deliver_at <= now)


def list_notifications(
    db: Session,
    *,
    user_id: int,
    unread_only: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> tuple[Sequence[Notification], int, int]:
    """One user's delivered notifications, newest first, plus the total
    matching the filter and the unread count (always over everything
    delivered, not just this page)."""
    now = datetime.now()
    base = select(Notification).where(
        Notification.user_id == user_id, _delivered_clause(now)
    )

    unread = (
        db.scalar(
            select(func.count())
            .select_from(base.where(Notification.read_at.is_(None)).subquery())
        )
        or 0
    )

    stmt = base
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return rows, total, unread


def unread_notification_count(db: Session, *, user_id: int) -> int:
    now = datetime.now()
    return (
        db.scalar(
            select(func.count()).select_from(
                select(Notification)
                .where(
                    Notification.user_id == user_id,
                    Notification.read_at.is_(None),
                    _delivered_clause(now),
                )
                .subquery()
            )
        )
        or 0
    )


def get_notification(
    db: Session, *, user_id: int, notification_id: int
) -> Notification | None:
    return db.scalar(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
        )
    )


def mark_notification_read(db: Session, notification: Notification) -> Notification:
    if notification.read_at is None:
        notification.read_at = datetime.now()
        db.flush()
    return notification


def mark_all_notifications_read(db: Session, *, user_id: int) -> int:
    """Mark every delivered, unread notification read. Returns how many."""
    now = datetime.now()
    rows = db.scalars(
        select(Notification).where(
            Notification.user_id == user_id,
            Notification.read_at.is_(None),
            _delivered_clause(now),
        )
    ).all()
    for row in rows:
        row.read_at = now
    if rows:
        db.flush()
    return len(rows)


def scan_milestone_notifications(
    db: Session, *, grace_days: int
) -> dict[str, int]:
    """Raise a ``milestone_due`` notification for every open milestone
    past its target date, and a ``milestone_overdue`` one once it is
    ``grace_days`` past (requirements Section 5.6). One per current
    care-team member; idempotent per (milestone, kind) so re-running is
    safe. Driven by the maintenance pass, not a triggering request."""
    from app import notifications as notify_mod

    today = date.today()
    open_milestones = db.scalars(
        select(RecoveryMilestone).where(
            RecoveryMilestone.status != MilestoneStatus.complete,
            RecoveryMilestone.target_date.isnot(None),
            RecoveryMilestone.target_date <= today,
        )
    ).all()

    due = overdue = 0
    for milestone in open_milestones:
        due += notify_mod.notify_milestone_state(
            db, milestone, kind=NotificationType.milestone_due
        )
        if milestone.target_date <= today - timedelta(days=grace_days):
            overdue += notify_mod.notify_milestone_state(
                db, milestone, kind=NotificationType.milestone_overdue
            )
    db.commit()
    return {"due": due, "overdue": overdue}


def dispatch_due_emails(
    db: Session, *, max_age_days: int, limit: int = 200
) -> dict[str, int]:
    """Send an email for every notification that is due, recent, not yet
    emailed, and whose recipient has an address (requirements Section
    5.6). Idempotent: a successful send stamps ``emailed_at``; a send
    that raises is logged and left unstamped so the next pass retries it.

    Driven by an external scheduler (see the ``/notifications/dispatch
    -due`` endpoint) - there is no in-process worker.
    """
    from app import email as email_sender  # local import: optional feature
    from app import notifications as notify_mod

    now = datetime.now()
    cutoff = now - timedelta(days=max_age_days)
    rows = db.scalars(
        select(Notification)
        .where(
            Notification.emailed_at.is_(None),
            Notification.created_at >= cutoff,
            or_(
                Notification.deliver_at.is_(None),
                Notification.deliver_at <= now,
            ),
        )
        .order_by(Notification.created_at, Notification.id)
        .limit(limit)
    ).all()

    sent = failed = skipped = 0
    for row in rows:
        address = row.user.email if row.user else None
        if not address:
            skipped += 1
            continue
        subject, body = notify_mod.render_email(row)
        try:
            email_sender.send(to=address, subject=subject, body=body)
        except Exception:  # noqa: BLE001 - retried next pass, see docstring
            logger.exception("email send failed for notification %s", row.id)
            failed += 1
            continue
        row.emailed_at = datetime.now()
        db.flush()
        sent += 1

    db.commit()
    return {"sent": sent, "failed": failed, "skipped": skipped}
