"""Reports endpoint: practice-level rollups (requirements Section 5.6),
scoped to the caller's practice. Same reader roles as the dashboard.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import reports, schemas
from app.audit import AuditRecorder, get_audit_recorder
from app.database import get_db
from app.deps import get_request_scope, require_roles
from app.models import AuditAction, UserRole
from app.scoping import RequestScope

router = APIRouter(prefix="/reports", tags=["reports"])

READ_ROLES = (
    UserRole.clinician,
    UserRole.prosthetist,
    UserRole.practice_administrator,
)


@router.get(
    "/summary",
    response_model=schemas.ReportSummary,
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def get_report_summary(
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
    since_days: int = Query(
        default=30,
        ge=1,
        le=365,
        description="window for the 'new' / 'in period' figures",
    ),
) -> schemas.ReportSummary:
    summary = reports.build_report(
        db, practice_id=scope.practice_id, since_days=since_days
    )
    recorder(AuditAction.read, "report", None)
    db.commit()
    return summary
