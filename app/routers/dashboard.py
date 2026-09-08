"""Dashboard endpoint: the clinician / prosthetist caseload summary
(requirements Section 5.5), scoped to the caller's practice.
"""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import dashboard, schemas
from app.audit import AuditRecorder, get_audit_recorder
from app.database import get_db
from app.deps import get_current_user, get_request_scope, require_roles
from app.models import AuditAction, User, UserRole
from app.scoping import RequestScope

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

READ_ROLES = (
    UserRole.clinician,
    UserRole.prosthetist,
    UserRole.practice_administrator,
)


@router.get(
    "",
    response_model=schemas.DashboardSummary,
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def get_dashboard(
    scope: RequestScope = Depends(get_request_scope),
    caller: User = Depends(get_current_user),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
    view: Literal["practice", "mine"] = Query(
        default="practice",
        description="'mine' limits the summary to your assigned patients",
    ),
    upcoming_days: int = Query(default=14, ge=1, le=90),
) -> schemas.DashboardSummary:
    assigned_to = caller.id if view == "mine" else None
    summary = dashboard.build_summary(
        db,
        practice_id=scope.practice_id,
        assigned_to=assigned_to,
        upcoming_days=upcoming_days,
    )
    recorder(AuditAction.read, "dashboard", None)
    db.commit()
    return summary
