"""Patient self-service portal (requirements Section 4's patient role).

A ``patient``-role login sees only the clinical records it is linked to.
Most people have exactly one, so ``?patient_id=`` is optional and
resolves to that record. Someone seen as a walk-in at more than one
practice (Section 5.11) holds several records on the one login: they
list them at ``GET /portal/records`` and must pass ``?patient_id=`` on
the per-record endpoints. Reads and writes are audited under the
patient's own actor id.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import crud, notifications, proms, schemas
from app.audit import AuditRecorder, get_audit_recorder
from app.database import get_db
from app.deps import get_current_user, require_roles
from app.models import (
    AppointmentStatus,
    AuditAction,
    Patient,
    SlotStatus,
    User,
    UserRole,
)

router = APIRouter(
    prefix="/portal",
    tags=["portal"],
    dependencies=[Depends(require_roles(UserRole.patient))],
)

_NO_RECORD = HTTPException(
    status.HTTP_404_NOT_FOUND,
    detail="No patient record is linked to your account",
)
_AMBIGUOUS = HTTPException(
    status.HTTP_400_BAD_REQUEST,
    detail="Several records are linked to your account; pass ?patient_id=",
)


def get_my_patient(
    patient_id: int | None = Query(
        default=None,
        description="Which linked record to act on. Optional when only "
        "one record is linked; required otherwise.",
    ),
    caller: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Patient:
    """Resolve the caller's target record (see the module docstring)."""
    links = crud.list_linked_patients(db, user_id=caller.id)
    if not links:
        raise _NO_RECORD
    if patient_id is not None:
        match = next((p for p in links if p.id == patient_id), None)
        if match is None:
            raise _NO_RECORD
        return match
    if len(links) == 1:
        return links[0]
    raise _AMBIGUOUS


@router.get("/records", response_model=list[schemas.PortalRecordSummary])
def my_records(
    caller: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[schemas.PortalRecordSummary]:
    """Every clinical record linked to this login - the picker for
    someone with records at more than one practice (Section 5.11).
    Empty list rather than 404 so the frontend can offer the claim
    form."""
    return [
        schemas.PortalRecordSummary(
            patient_id=p.id,
            first_name=p.first_name,
            last_name=p.last_name,
            practice_name=p.practice.name if p.practice else None,
            site_name=p.site.name if p.site else None,
        )
        for p in crud.list_linked_patients(db, user_id=caller.id)
    ]


@router.get("/me", response_model=schemas.PortalProfile)
def read_me(
    recorder: AuditRecorder = Depends(get_audit_recorder),
    patient: Patient = Depends(get_my_patient),
    db: Session = Depends(get_db),
) -> schemas.PortalProfile:
    recorder(
        AuditAction.read,
        "patient",
        patient.id,
        practice_id=patient.practice_id,
    )
    db.commit()
    return schemas.PortalProfile(
        **schemas.PatientRead.model_validate(patient).model_dump(),
        practice_name=patient.practice.name if patient.practice else None,
        site_name=patient.site.name if patient.site else None,
    )


@router.get(
    "/me/involvements", response_model=list[schemas.InvolvementDetail]
)
def my_involvements(
    recorder: AuditRecorder = Depends(get_audit_recorder),
    patient: Patient = Depends(get_my_patient),
    db: Session = Depends(get_db),
) -> list[schemas.InvolvementDetail]:
    out = [
        schemas.InvolvementDetail(
            **schemas.InvolvementRead.model_validate(involvement).model_dump(),
            devices=[
                schemas.DeviceRead.model_validate(device)
                for device in crud.list_devices(
                    db, involvement_id=involvement.id
                )
            ],
        )
        for involvement in crud.list_involvements(db, patient_id=patient.id)
    ]
    recorder(
        AuditAction.read,
        "limb_involvement",
        None,
        practice_id=patient.practice_id,
    )
    db.commit()
    return out


@router.get("/me/milestones", response_model=list[schemas.MilestoneRead])
def my_milestones(
    recorder: AuditRecorder = Depends(get_audit_recorder),
    patient: Patient = Depends(get_my_patient),
    db: Session = Depends(get_db),
) -> list[schemas.MilestoneRead]:
    rows = crud.list_milestones(db, patient_id=patient.id)
    recorder(
        AuditAction.read,
        "recovery_milestone",
        None,
        practice_id=patient.practice_id,
    )
    db.commit()
    return [schemas.MilestoneRead.model_validate(row) for row in rows]


@router.get("/me/proms", response_model=list[schemas.PromRead])
def my_proms(
    recorder: AuditRecorder = Depends(get_audit_recorder),
    patient: Patient = Depends(get_my_patient),
    db: Session = Depends(get_db),
) -> list[schemas.PromRead]:
    rows = crud.list_proms(db, patient_id=patient.id)
    recorder(
        AuditAction.read,
        "prom_record",
        None,
        practice_id=patient.practice_id,
    )
    db.commit()
    return [schemas.PromRead.model_validate(row) for row in rows]


@router.get("/me/instruments", response_model=schemas.PortalInstruments)
def my_instruments(
    patient: Patient = Depends(get_my_patient),
    db: Session = Depends(get_db),
) -> schemas.PortalInstruments:
    kinds = {
        involvement.kind.value
        for involvement in crud.list_involvements(db, patient_id=patient.id)
    }
    if not kinds:
        return schemas.PortalInstruments(
            instruments=list(proms.SPECS.keys())
        )
    seen: list = []
    for kind in kinds:
        for instrument in proms.instruments_for(kind):
            if instrument not in seen:
                seen.append(instrument)
    return schemas.PortalInstruments(instruments=seen)


@router.post(
    "/me/proms",
    response_model=schemas.PromRead,
    status_code=status.HTTP_201_CREATED,
)
def submit_prom(
    data: schemas.PortalPromCreate,
    caller: User = Depends(get_current_user),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    patient: Patient = Depends(get_my_patient),
    db: Session = Depends(get_db),
) -> schemas.PromRead:
    try:
        prom = crud.create_prom(
            db,
            patient_id=patient.id,
            recorded_by_id=caller.id,
            data=schemas.PromCreate(
                instrument=data.instrument,
                responses=data.responses,
                recorded_at=data.recorded_at,
                notes=data.notes,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    recorder(
        AuditAction.create,
        "prom_record",
        prom.id,
        practice_id=patient.practice_id,
    )
    if prom.flagged:
        notifications.notify_prom_flagged(db, prom)
    db.commit()
    return schemas.PromRead.model_validate(prom)


@router.get(
    "/availability", response_model=list[schemas.AvailabilitySlotRead]
)
def browse_availability(
    patient: Patient = Depends(get_my_patient),
    db: Session = Depends(get_db),
    practitioner_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[schemas.AvailabilitySlotRead]:
    """Open slots in the patient's own practice — what they can book
    into. Booking itself lives in the appointments endpoints."""
    rows = crud.list_slots(
        db,
        practice_id=patient.practice_id,
        practitioner_id=practitioner_id,
        status=SlotStatus.open,
        date_from=date_from,
        date_to=date_to,
    )
    return [
        schemas.AvailabilitySlotRead(
            id=slot.id,
            practice_id=slot.practice_id,
            site_id=slot.site_id,
            practitioner_id=slot.practitioner_id,
            practitioner_email=slot.practitioner.email,
            start_time=slot.start_time,
            end_time=slot.end_time,
            appointment_type=slot.appointment_type,
            status=slot.status,
            notes=slot.notes,
            created_at=slot.created_at,
            updated_at=slot.updated_at,
        )
        for slot in rows
    ]


def _appointment_read(appointment) -> schemas.AppointmentRead:
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
    "/appointments",
    response_model=schemas.AppointmentRead,
    status_code=status.HTTP_201_CREATED,
)
def book_appointment(
    data: schemas.AppointmentBook,
    recorder: AuditRecorder = Depends(get_audit_recorder),
    patient: Patient = Depends(get_my_patient),
    db: Session = Depends(get_db),
) -> schemas.AppointmentRead:
    """Book into an open slot in your own practice. Publishing the slot
    already was the confirmation - this just claims it, no approval
    step."""
    slot = crud.get_slot(
        db, practice_id=patient.practice_id, slot_id=data.slot_id
    )
    if slot is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="Slot not found"
        )
    if slot.status != SlotStatus.open:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="This slot is no longer open"
        )
    appointment = crud.book_appointment(
        db, slot=slot, patient_id=patient.id, notes=data.notes
    )
    recorder(
        AuditAction.create,
        "appointment",
        appointment.id,
        practice_id=patient.practice_id,
    )
    # The patient booked this themselves, so no "booked" notification -
    # just the reminder ahead of time.
    notifications.notify_appointment_reminder(db, appointment)
    db.commit()
    return _appointment_read(appointment)


@router.get(
    "/appointments", response_model=list[schemas.AppointmentRead]
)
def my_appointments(
    recorder: AuditRecorder = Depends(get_audit_recorder),
    patient: Patient = Depends(get_my_patient),
    db: Session = Depends(get_db),
) -> list[schemas.AppointmentRead]:
    rows = crud.list_patient_appointments(db, patient_id=patient.id)
    recorder(
        AuditAction.read,
        "appointment",
        None,
        practice_id=patient.practice_id,
    )
    db.commit()
    return [_appointment_read(row) for row in rows]


@router.post(
    "/appointments/{appointment_id}/cancel",
    response_model=schemas.AppointmentRead,
)
def cancel_my_appointment(
    appointment_id: int,
    recorder: AuditRecorder = Depends(get_audit_recorder),
    patient: Patient = Depends(get_my_patient),
    db: Session = Depends(get_db),
) -> schemas.AppointmentRead:
    """Patient cancel (Section 5.10) - no reason required. Cancelling
    inside the practice's notice window is recorded as a late
    cancellation (no fee logic - just the flag)."""
    appointment = crud.get_patient_appointment(
        db, patient_id=patient.id, appointment_id=appointment_id
    )
    if appointment is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="Appointment not found"
        )
    if appointment.status != AppointmentStatus.booked:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="This appointment is not currently booked",
        )
    notice_hours = patient.practice.cancellation_notice_hours
    notice = timedelta(hours=notice_hours)
    late = (appointment.scheduled_start - datetime.now()) < notice
    crud.cancel_appointment(
        db,
        appointment,
        status=AppointmentStatus.cancelled_by_patient,
        reason=None,
        late=late,
    )
    recorder(
        AuditAction.update,
        "appointment",
        appointment.id,
        practice_id=patient.practice_id,
    )
    notifications.supersede_appointment_reminders(db, appointment.id)
    db.commit()
    return _appointment_read(appointment)


@router.post(
    "/appointments/{appointment_id}/reschedule",
    response_model=schemas.RescheduleResult,
)
def reschedule_my_appointment(
    appointment_id: int,
    data: schemas.AppointmentReschedule,
    recorder: AuditRecorder = Depends(get_audit_recorder),
    patient: Patient = Depends(get_my_patient),
    db: Session = Depends(get_db),
) -> schemas.RescheduleResult:
    """Reschedule (Section 5.10) - one atomic action, not a cancel: the
    old slot is released and the new one taken together. No notice-window
    restriction - that's specifically a cancel concept."""
    appointment = crud.get_patient_appointment(
        db, patient_id=patient.id, appointment_id=appointment_id
    )
    if appointment is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="Appointment not found"
        )
    if appointment.status != AppointmentStatus.booked:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="This appointment is not currently booked",
        )
    new_slot = crud.get_slot(
        db, practice_id=patient.practice_id, slot_id=data.new_slot_id
    )
    if new_slot is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="Slot not found"
        )
    if new_slot.status != SlotStatus.open:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="This slot is no longer open"
        )
    previous, new = crud.reschedule_appointment(
        db, appointment, new_slot=new_slot
    )
    recorder(
        AuditAction.update,
        "appointment",
        previous.id,
        practice_id=patient.practice_id,
    )
    recorder(
        AuditAction.create,
        "appointment",
        new.id,
        practice_id=patient.practice_id,
    )
    notifications.notify_appointment_rescheduled(
        db,
        previous=previous,
        new=new,
        recipient_user_id=new.practitioner_id,
    )
    notifications.supersede_appointment_reminders(db, previous.id)
    notifications.notify_appointment_reminder(db, new)
    db.commit()
    return schemas.RescheduleResult(
        previous=_appointment_read(previous), new=_appointment_read(new)
    )
