"""Staff-facing appointment management (requirements Section 5.9/5.10).

Booking itself is turning an open :class:`~app.models.AvailabilitySlot`
into a confirmed :class:`~app.models.Appointment` - no separate approval
step (publishing the slot already was the confirmation). This router is
the staff side: front-desk/phone booking on a patient's behalf, the
practice-wide appointment list, and practitioner-initiated cancel /
no-show. The patient's own self-service booking lives in
``app.routers.portal``.

A medical-aid patient's appointment opens a ``CoverageDetermination``
automatically at booking time (see ``crud.book_appointment``); this
router only exposes a read of it (``GET .../coverage``) - the reviewer
who actually decides it works through ``app.routers.review``.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import crud, notifications, schemas
from app.audit import AuditRecorder, get_audit_recorder
from app.database import get_db
from app.deps import get_current_user, get_request_scope, require_roles
from app.models import (
    AppointmentStatus,
    AuditAction,
    SlotStatus,
    User,
    UserRole,
)
from app.scoping import RequestScope

router = APIRouter(prefix="/appointments", tags=["appointments"])

CRUD_ROLES = (UserRole.clinician, UserRole.prosthetist)
READ_ROLES = (*CRUD_ROLES, UserRole.practice_administrator)

_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND, detail="Appointment not found"
)
_PATIENT_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND, detail="Patient not found"
)
_SLOT_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND, detail="Slot not found"
)
_NOT_BOOKED = HTTPException(
    status.HTTP_400_BAD_REQUEST,
    detail="This appointment is not currently booked",
)


def _read(appointment) -> schemas.AppointmentRead:
    patient = appointment.patient
    return schemas.AppointmentRead(
        id=appointment.id,
        practice_id=appointment.practice_id,
        site_id=appointment.site_id,
        patient_id=appointment.patient_id,
        patient_name=f"{patient.first_name} {patient.last_name}",
        practitioner_id=appointment.practitioner_id,
        practitioner_email=appointment.practitioner.email,
        slot_id=appointment.slot_id,
        appointment_type=appointment.appointment_type,
        scheduled_start=appointment.scheduled_start,
        scheduled_end=appointment.scheduled_end,
        status=appointment.status,
        cancellation_reason=appointment.cancellation_reason,
        cancelled_at=appointment.cancelled_at,
        late_cancellation=appointment.late_cancellation,
        rescheduled_to_id=appointment.rescheduled_to_id,
        rescheduled_from_id=appointment.rescheduled_from_id,
        coverage_status=appointment.coverage.status if appointment.coverage else None,
        notes=appointment.notes,
        created_at=appointment.created_at,
        updated_at=appointment.updated_at,
    )


@router.post(
    "",
    response_model=schemas.AppointmentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def book_appointment(
    data: schemas.AppointmentCreate,
    scope: RequestScope = Depends(get_request_scope),
    caller: User = Depends(get_current_user),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.AppointmentRead:
    patient = crud.get_patient(
        db, practice_id=scope.practice_id, patient_id=data.patient_id
    )
    if patient is None:
        raise _PATIENT_NOT_FOUND
    slot = crud.get_slot(db, practice_id=scope.practice_id, slot_id=data.slot_id)
    if slot is None:
        raise _SLOT_NOT_FOUND
    if slot.status != SlotStatus.open:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="This slot is no longer open"
        )
    appointment = crud.book_appointment(
        db, slot=slot, patient_id=patient.id, notes=data.notes
    )
    recorder(AuditAction.create, "appointment", appointment.id)
    notifications.notify_appointment_booked(
        db, appointment, booked_by_email=caller.email
    )
    notifications.notify_appointment_reminder(db, appointment)
    db.commit()
    return _read(appointment)


@router.get(
    "",
    response_model=list[schemas.AppointmentRead],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def list_appointments(
    scope: RequestScope = Depends(get_request_scope),
    db: Session = Depends(get_db),
    patient_id: int | None = Query(default=None),
    practitioner_id: int | None = Query(default=None),
    status_: AppointmentStatus | None = Query(default=None, alias="status"),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
) -> list[schemas.AppointmentRead]:
    rows = crud.list_appointments(
        db,
        practice_id=scope.practice_id,
        patient_id=patient_id,
        practitioner_id=practitioner_id,
        status=status_,
        date_from=date_from,
        date_to=date_to,
    )
    return [_read(row) for row in rows]


@router.get(
    "/{appointment_id}",
    response_model=schemas.AppointmentRead,
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def read_appointment(
    appointment_id: int,
    scope: RequestScope = Depends(get_request_scope),
    db: Session = Depends(get_db),
) -> schemas.AppointmentRead:
    appointment = crud.get_appointment(
        db, practice_id=scope.practice_id, appointment_id=appointment_id
    )
    if appointment is None:
        raise _NOT_FOUND
    return _read(appointment)


def _coverage_read(coverage) -> schemas.CoverageDeterminationRead:
    appointment = coverage.appointment
    patient = appointment.patient
    return schemas.CoverageDeterminationRead(
        id=coverage.id,
        appointment_id=coverage.appointment_id,
        patient_id=patient.id,
        patient_name=f"{patient.first_name} {patient.last_name}",
        practice_name=patient.practice.name if patient.practice else None,
        scheme_name=coverage.membership.scheme_name,
        appointment_type=appointment.appointment_type,
        scheduled_start=appointment.scheduled_start,
        status=coverage.status,
        authorization_number=coverage.authorization_number,
        valid_until=coverage.valid_until,
        decided_by_id=coverage.decided_by_id,
        decided_at=coverage.decided_at,
        notes=coverage.notes,
        created_at=coverage.created_at,
        updated_at=coverage.updated_at,
    )


@router.get(
    "/{appointment_id}/coverage",
    response_model=schemas.CoverageDeterminationRead,
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def read_appointment_coverage(
    appointment_id: int,
    scope: RequestScope = Depends(get_request_scope),
    db: Session = Depends(get_db),
) -> schemas.CoverageDeterminationRead:
    appointment = crud.get_appointment(
        db, practice_id=scope.practice_id, appointment_id=appointment_id
    )
    if appointment is None:
        raise _NOT_FOUND
    coverage = crud.get_appointment_coverage(db, appointment_id=appointment.id)
    if coverage is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="No coverage determination for this appointment "
            "(self-pay, or not booked yet)",
        )
    return _coverage_read(coverage)


@router.post(
    "/{appointment_id}/cancel",
    response_model=schemas.AppointmentRead,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def cancel_appointment(
    appointment_id: int,
    data: schemas.AppointmentCancel,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.AppointmentRead:
    appointment = crud.get_appointment(
        db, practice_id=scope.practice_id, appointment_id=appointment_id
    )
    if appointment is None:
        raise _NOT_FOUND
    if appointment.status != AppointmentStatus.booked:
        raise _NOT_BOOKED
    if not data.reason or not data.reason.strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="A reason is required when a practitioner cancels",
        )
    crud.cancel_appointment(
        db,
        appointment,
        status=AppointmentStatus.cancelled_by_practitioner,
        reason=data.reason,
    )
    recorder(AuditAction.update, "appointment", appointment.id)
    notifications.notify_appointment_cancelled(
        db, appointment, reason=data.reason
    )
    notifications.supersede_appointment_reminders(db, appointment.id)
    db.commit()
    return _read(appointment)


@router.post(
    "/{appointment_id}/reschedule",
    response_model=schemas.RescheduleResult,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def reschedule_appointment(
    appointment_id: int,
    data: schemas.AppointmentReschedule,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.RescheduleResult:
    appointment = crud.get_appointment(
        db, practice_id=scope.practice_id, appointment_id=appointment_id
    )
    if appointment is None:
        raise _NOT_FOUND
    if appointment.status != AppointmentStatus.booked:
        raise _NOT_BOOKED
    new_slot = crud.get_slot(
        db, practice_id=scope.practice_id, slot_id=data.new_slot_id
    )
    if new_slot is None:
        raise _SLOT_NOT_FOUND
    if new_slot.status != SlotStatus.open:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="This slot is no longer open"
        )
    previous, new = crud.reschedule_appointment(
        db, appointment, new_slot=new_slot
    )
    recorder(AuditAction.update, "appointment", previous.id)
    recorder(AuditAction.create, "appointment", new.id)
    notifications.notify_appointment_rescheduled(
        db,
        previous=previous,
        new=new,
        recipient_user_id=new.patient.user_id,
    )
    notifications.supersede_appointment_reminders(db, previous.id)
    notifications.notify_appointment_reminder(db, new)
    db.commit()
    return schemas.RescheduleResult(previous=_read(previous), new=_read(new))


@router.post(
    "/{appointment_id}/no-show",
    response_model=schemas.AppointmentRead,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def mark_no_show(
    appointment_id: int,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.AppointmentRead:
    appointment = crud.get_appointment(
        db, practice_id=scope.practice_id, appointment_id=appointment_id
    )
    if appointment is None:
        raise _NOT_FOUND
    if appointment.status != AppointmentStatus.booked:
        raise _NOT_BOOKED
    crud.mark_no_show(db, appointment)
    recorder(AuditAction.update, "appointment", appointment.id)
    notifications.supersede_appointment_reminders(db, appointment.id)
    db.commit()
    return _read(appointment)
