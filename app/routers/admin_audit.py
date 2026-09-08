"""Practice-administrator view of the audit trail (requirements Section
5.7). Read-only: the log is append-only and written by every other
endpoint. Scoped to the caller's practice via the ``practice_id`` each
entry carries.
"""

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import crud, schemas
from app.database import get_db
from app.deps import get_request_scope, require_roles
from app.models import AuditAction, UserRole
from app.scoping import RequestScope

router = APIRouter(prefix="/admin/audit", tags=["admin"])

_only_practice_admin = require_roles(UserRole.practice_administrator)


@router.get(
    "",
    response_model=schemas.AuditPage,
    dependencies=[Depends(_only_practice_admin)],
)
def list_audit(
    scope: RequestScope = Depends(get_request_scope),
    db: Session = Depends(get_db),
    actor_id: int | None = Query(default=None),
    action: AuditAction | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    date_from: date | None = Query(
        default=None, description="inclusive, on the entry timestamp"
    ),
    date_to: date | None = Query(default=None, description="inclusive"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> schemas.AuditPage:
    rows, total = crud.list_audit_entries(
        db,
        practice_id=scope.practice_id,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return schemas.AuditPage(
        items=[
            schemas.AuditEntryRead(
                id=entry.id,
                actor_id=entry.actor_id,
                actor_email=email,
                action=entry.action,
                entity_type=entry.entity_type,
                entity_id=entry.entity_id,
                timestamp=entry.timestamp,
            )
            for entry, email in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/facets",
    response_model=schemas.AuditFacets,
    dependencies=[Depends(_only_practice_admin)],
)
def audit_facets(
    scope: RequestScope = Depends(get_request_scope),
    db: Session = Depends(get_db),
) -> schemas.AuditFacets:
    entity_types, actors = crud.audit_facets(
        db, practice_id=scope.practice_id
    )
    return schemas.AuditFacets(
        entity_types=list(entity_types),
        actors=[
            schemas.ActorRef(id=actor_id, email=email)
            for actor_id, email in actors
        ],
    )
