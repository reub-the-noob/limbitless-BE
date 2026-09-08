"""In-app notification inbox (requirements Section 5.6).

Every authenticated user has one — a notification is addressed to a
``User``, not a practice, so there is no role gate and no practice
scope: ``crud`` filters strictly on ``user_id``. Reads are not audited
(a user seeing their own inbox is not access to patient data).

Notifications are *written* elsewhere — the routers that make the change
being announced call an ``app.notifications.notify_*`` helper. This
router only lists them and marks them read.
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import config, crud, schemas
from app.database import get_db
from app.deps import get_current_user
from app.models import User

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=schemas.NotificationPage)
def list_my_notifications(
    caller: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    unread: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> schemas.NotificationPage:
    rows, total, unread_count = crud.list_notifications(
        db,
        user_id=caller.id,
        unread_only=unread,
        limit=limit,
        offset=offset,
    )
    return schemas.NotificationPage(
        items=[schemas.NotificationRead.model_validate(row) for row in rows],
        total=total,
        unread=unread_count,
        limit=limit,
        offset=offset,
    )


@router.get("/unread-count", response_model=schemas.UnreadCount)
def unread_count(
    caller: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> schemas.UnreadCount:
    return schemas.UnreadCount(
        unread=crud.unread_notification_count(db, user_id=caller.id)
    )


@router.post("/{notification_id}/read", response_model=schemas.NotificationRead)
def mark_read(
    notification_id: int,
    caller: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> schemas.NotificationRead:
    notification = crud.get_notification(
        db, user_id=caller.id, notification_id=notification_id
    )
    if notification is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="Notification not found"
        )
    crud.mark_notification_read(db, notification)
    db.commit()
    return schemas.NotificationRead.model_validate(notification)


@router.post("/read-all", response_model=schemas.UnreadCount)
def mark_all_read(
    caller: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> schemas.UnreadCount:
    crud.mark_all_notifications_read(db, user_id=caller.id)
    db.commit()
    return schemas.UnreadCount(unread=0)


@router.post("/dispatch-due", response_model=schemas.NotificationMaintenanceResult)
def dispatch_due(
    x_dispatch_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> schemas.NotificationMaintenanceResult:
    """One notification maintenance tick (Section 5.6), meant to be hit
    by a scheduler / cron:

    1. raise ``milestone_due`` / ``milestone_overdue`` notifications for
       open milestones past their target date;
    2. send email for every notification now due (including any just
       raised in step 1).

    Authenticated by a shared ``X-Dispatch-Token`` header, not a JWT.
    Disabled (503) unless ``NOTIFICATIONS_DISPATCH_TOKEN`` is set."""
    expected = config.NOTIFICATIONS_DISPATCH_TOKEN
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Notification dispatch is not configured",
        )
    if x_dispatch_token != expected:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="Invalid dispatch token"
        )
    milestones = crud.scan_milestone_notifications(
        db, grace_days=config.MILESTONE_OVERDUE_GRACE_DAYS
    )
    emails = crud.dispatch_due_emails(
        db, max_age_days=config.EMAIL_DISPATCH_MAX_AGE_DAYS
    )
    return schemas.NotificationMaintenanceResult(
        milestones_due=milestones["due"],
        milestones_overdue=milestones["overdue"],
        emails_sent=emails["sent"],
        emails_failed=emails["failed"],
        emails_skipped=emails["skipped"],
    )
