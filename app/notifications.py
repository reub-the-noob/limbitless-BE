"""In-app notifications (requirements Section 5.6).

One :class:`~app.models.Notification` row per message per recipient user.
Like :mod:`app.audit`, this is deliberately explicit: routers call a
``notify_*`` helper right after the state change (and its audit entry),
inside the same transaction, so the notification is committed atomically
with the thing it is about.

Email delivery is out of scope for this pass — the section allows
"in-app and/or email … in-app at minimum".

``appointment_reminder`` is time-based: written up front at booking time
with a future ``deliver_at``, and the list endpoint hides it until that
time has passed — so no background worker. A reminder for an appointment
that is later cancelled or rescheduled is superseded (deleted while
still pending). Milestone due / overdue notifications are the doc's
remaining time-based case and are not built yet.

Every helper is a no-op when there is no recipient (e.g. a walk-in
patient with no linked user account) and never raises into the caller's
request — a notification failing to write must not fail the booking or
the PROM it accompanies (Section 5.6 reliability note). The failure is
logged instead.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (
    Appointment,
    CoverageDetermination,
    Notification,
    NotificationType,
    PatientAssignment,
    PromRecord,
    RecoveryMilestone,
)

logger = logging.getLogger(__name__)

# How far ahead of an appointment its reminder is delivered.
REMINDER_LEAD = timedelta(hours=24)


def notify(
    db: Session,
    *,
    user_id: int | None,
    type: NotificationType,
    payload: dict,
    subject_type: str | None = None,
    subject_id: int | None = None,
    deliver_at: datetime | None = None,
) -> Notification | None:
    """Append one notification to the current transaction (no commit).

    Returns ``None`` and does nothing if ``user_id`` is ``None`` or the
    write fails — a notification is never allowed to break the request it
    rides along with.
    """
    if user_id is None:
        return None
    try:
        row = Notification(
            user_id=user_id,
            type=type,
            payload=payload,
            subject_type=subject_type,
            subject_id=subject_id,
            deliver_at=deliver_at,
        )
        db.add(row)
        db.flush()
        return row
    except Exception:  # noqa: BLE001 - see module docstring
        logger.exception("failed to write notification type=%s user=%s", type, user_id)
        return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _appointment_payload(appointment: Appointment) -> dict:
    patient = appointment.patient
    return {
        "appointment_id": appointment.id,
        "appointment_type": appointment.appointment_type.value,
        "scheduled_start": _iso(appointment.scheduled_start),
        "practitioner_email": appointment.practitioner.email,
        "patient_name": f"{patient.first_name} {patient.last_name}",
    }


# --- appointments -----------------------------------------------------


def notify_appointment_booked(
    db: Session, appointment: Appointment, *, booked_by_email: str
) -> None:
    """Staff booked a slot on the patient's behalf (front-desk / phone)."""
    notify(
        db,
        user_id=appointment.patient.user_id,
        type=NotificationType.appointment_booked,
        payload={**_appointment_payload(appointment), "booked_by": booked_by_email},
        subject_type="appointment",
        subject_id=appointment.id,
    )


def notify_appointment_cancelled(
    db: Session, appointment: Appointment, *, reason: str | None
) -> None:
    """A practitioner (or admin acting for them) cancelled — the patient
    is told immediately, with the reason."""
    notify(
        db,
        user_id=appointment.patient.user_id,
        type=NotificationType.appointment_cancelled,
        payload={**_appointment_payload(appointment), "reason": reason},
        subject_type="appointment",
        subject_id=appointment.id,
    )


def notify_appointment_rescheduled(
    db: Session,
    *,
    previous: Appointment,
    new: Appointment,
    recipient_user_id: int | None,
) -> None:
    """One side rescheduled; the other side is notified. The router
    passes the recipient — the patient's user when staff moved it, the
    practitioner when the patient moved it."""
    notify(
        db,
        user_id=recipient_user_id,
        type=NotificationType.appointment_rescheduled,
        payload={
            "previous_appointment_id": previous.id,
            "appointment_id": new.id,
            "old_start": _iso(previous.scheduled_start),
            "new_start": _iso(new.scheduled_start),
            "appointment_type": new.appointment_type.value,
            "practitioner_email": new.practitioner.email,
            "patient_name": f"{new.patient.first_name} {new.patient.last_name}",
        },
        subject_type="appointment",
        subject_id=new.id,
    )


def notify_appointment_reminder(db: Session, appointment: Appointment) -> None:
    """A reminder ahead of a booked appointment (Section 5.6). Written at
    booking time with a future ``deliver_at`` — the list endpoint keeps
    it hidden until then, so no scheduler is needed. Skipped for an
    appointment that is already in the past (e.g. booking into a stale
    slot); a same-day booking just gets a reminder that is due
    immediately."""
    now = datetime.now()
    if appointment.scheduled_start <= now:
        return
    notify(
        db,
        user_id=appointment.patient.user_id,
        type=NotificationType.appointment_reminder,
        payload=_appointment_payload(appointment),
        subject_type="appointment",
        subject_id=appointment.id,
        deliver_at=appointment.scheduled_start - REMINDER_LEAD,
    )


def supersede_appointment_reminders(db: Session, appointment_id: int) -> None:
    """Drop a not-yet-due reminder for an appointment that has been
    cancelled, no-showed or rescheduled. A reminder that has already
    become due (``deliver_at`` in the past) is left alone — by then it is
    a real message the recipient may have seen."""
    now = datetime.now()
    db.execute(
        delete(Notification).where(
            Notification.type == NotificationType.appointment_reminder,
            Notification.subject_type == "appointment",
            Notification.subject_id == appointment_id,
            Notification.read_at.is_(None),
            Notification.deliver_at.isnot(None),
            Notification.deliver_at > now,
        )
    )


# --- coverage --------------------------------------------------------


def notify_coverage_decided(
    db: Session, coverage: CoverageDetermination
) -> None:
    """A medical-aid reviewer approved or denied coverage — the patient
    is told the decision is ready (never the clinical detail)."""
    appointment = coverage.appointment
    notify(
        db,
        user_id=appointment.patient.user_id,
        type=NotificationType.coverage_decided,
        payload={
            "coverage_id": coverage.id,
            "appointment_id": appointment.id,
            "status": coverage.status.value,
            "appointment_type": appointment.appointment_type.value,
            "scheduled_start": _iso(appointment.scheduled_start),
        },
        subject_type="appointment",
        subject_id=appointment.id,
    )


# --- clinical -------------------------------------------------------


def care_team_user_ids(db: Session, patient_id: int) -> list[int]:
    """User ids of the patient's *current* care team (assignments with no
    end_date), de-duped, in a stable order."""
    rows = db.scalars(
        select(PatientAssignment.user_id).where(
            PatientAssignment.patient_id == patient_id,
            PatientAssignment.end_date.is_(None),
        )
    ).all()
    return list(dict.fromkeys(rows))


def notify_prom_flagged(db: Session, prom: PromRecord) -> None:
    """A PROM score crossed its clinical threshold — every clinician
    currently on the patient's care team is notified for attention."""
    patient = prom.patient
    payload = {
        "prom_id": prom.id,
        "patient_id": prom.patient_id,
        "patient_name": f"{patient.first_name} {patient.last_name}",
        "instrument": prom.instrument.value,
        "score": prom.score,
        "flag_reason": prom.flag_reason,
    }
    for user_id in care_team_user_ids(db, prom.patient_id):
        notify(
            db,
            user_id=user_id,
            type=NotificationType.prom_flagged,
            payload=payload,
            subject_type="patient",
            subject_id=prom.patient_id,
        )


def _milestone_payload(milestone: RecoveryMilestone) -> dict:
    patient = milestone.patient
    return {
        "milestone_id": milestone.id,
        "patient_id": milestone.patient_id,
        "patient_name": f"{patient.first_name} {patient.last_name}",
        "milestone_type": milestone.milestone_type.value,
        "target_date": milestone.target_date.isoformat()
        if milestone.target_date
        else None,
    }


def notify_milestone_state(
    db: Session, milestone: RecoveryMilestone, *, kind: NotificationType
) -> int:
    """One ``milestone_due`` / ``milestone_overdue`` notification per
    current care-team member, unless this milestone already has one of
    that kind (``subject_type='recovery_milestone'`` + id). Returns how
    many rows were written. Called by the maintenance pass, not a
    triggering request."""
    already = db.scalar(
        select(Notification.id).where(
            Notification.type == kind,
            Notification.subject_type == "recovery_milestone",
            Notification.subject_id == milestone.id,
        )
    )
    if already is not None:
        return 0
    payload = _milestone_payload(milestone)
    written = 0
    for user_id in care_team_user_ids(db, milestone.patient_id):
        notify(
            db,
            user_id=user_id,
            type=kind,
            payload=payload,
            subject_type="recovery_milestone",
            subject_id=milestone.id,
        )
        written += 1
    return written


def supersede_milestone_notifications(db: Session, milestone_id: int) -> None:
    """Drop unread due/overdue nudges for a milestone that has just been
    completed — the clinician does not need a stale reminder about it."""
    db.execute(
        delete(Notification).where(
            Notification.type.in_(
                (
                    NotificationType.milestone_due,
                    NotificationType.milestone_overdue,
                )
            ),
            Notification.subject_type == "recovery_milestone",
            Notification.subject_id == milestone_id,
            Notification.read_at.is_(None),
        )
    )


def notify_patient_reassigned(
    db: Session, assignment: PatientAssignment
) -> None:
    """A clinician / prosthetist was added to a patient's care team."""
    patient = assignment.patient
    notify(
        db,
        user_id=assignment.user_id,
        type=NotificationType.patient_reassigned,
        payload={
            "assignment_id": assignment.id,
            "patient_id": assignment.patient_id,
            "patient_name": f"{patient.first_name} {patient.last_name}",
            "role": assignment.role.value,
        },
        subject_type="patient",
        subject_id=assignment.patient_id,
    )


# --- email rendering (Section 5.6 email delivery) --------------------


def _when(iso: object) -> str:
    """"2026-09-11T09:00:00" -> "2026-09-11 09:00". Naive, shown as-is."""
    if not isinstance(iso, str) or not iso:
        return "the scheduled time"
    date, _, time = iso.partition("T")
    return f"{date} {time[:5]}".strip()


def render_email(notification) -> tuple[str, str]:
    """Subject + plain-text body for one notification, from its payload
    snapshot. Mirrors the frontend's in-app summary text."""
    p = notification.payload or {}
    kind = notification.type

    if kind is NotificationType.appointment_booked:
        return (
            "Appointment booked",
            f"{p.get('booked_by') or 'Your clinic'} booked you a "
            f"{p.get('appointment_type') or 'visit'} on {_when(p.get('scheduled_start'))}.",
        )
    if kind is NotificationType.appointment_cancelled:
        reason = p.get("reason")
        return (
            "Appointment cancelled",
            f"Your {p.get('appointment_type') or 'visit'} on "
            f"{_when(p.get('scheduled_start'))} was cancelled"
            f"{f' - {reason}' if reason else ''}.",
        )
    if kind is NotificationType.appointment_rescheduled:
        return (
            "Appointment rescheduled",
            f"Your {p.get('appointment_type') or 'visit'} moved from "
            f"{_when(p.get('old_start'))} to {_when(p.get('new_start'))}.",
        )
    if kind is NotificationType.appointment_reminder:
        with_who = (
            f" with {p['practitioner_email']}" if p.get("practitioner_email") else ""
        )
        return (
            "Appointment reminder",
            f"Reminder: {p.get('appointment_type') or 'visit'} on "
            f"{_when(p.get('scheduled_start'))}{with_who}.",
        )
    if kind is NotificationType.coverage_decided:
        return (
            "Medical-aid coverage decision",
            f"Medical-aid coverage for your {p.get('appointment_type') or 'visit'} "
            f"on {_when(p.get('scheduled_start'))} was {p.get('status') or 'decided'}.",
        )
    if kind is NotificationType.prom_flagged:
        return (
            "Flagged outcome measure",
            f"{p.get('patient_name') or 'A patient'} recorded a flagged "
            f"{p.get('instrument') or 'measure'} (score {p.get('score')}).",
        )
    if kind is NotificationType.patient_reassigned:
        return (
            "New patient assigned",
            f"You were added to {p.get('patient_name') or 'a patient'}'s care team "
            f"as {p.get('role') or 'clinician'}.",
        )
    if kind is NotificationType.milestone_due:
        return (
            "Milestone due",
            f"{p.get('patient_name') or 'A patient'}'s milestone "
            f"'{p.get('milestone_type') or 'milestone'}' is due "
            f"(target {p.get('target_date') or 'unset'}).",
        )
    if kind is NotificationType.milestone_overdue:
        return (
            "Milestone overdue",
            f"{p.get('patient_name') or 'A patient'}'s milestone "
            f"'{p.get('milestone_type') or 'milestone'}' is overdue "
            f"(target {p.get('target_date') or 'unset'}).",
        )
    return ("Notification", "You have a new notification.")
