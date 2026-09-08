"""Practice-administrator site management (requirements Section 4).

A practice administrator lists, adds and edits the sites of their own
practice. Sites are never deleted here - a site with staff or patients
attached should stay. Every write is audited.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import crud, schemas
from app.audit import AuditRecorder, get_audit_recorder
from app.database import get_db
from app.deps import get_request_scope, require_roles
from app.models import AuditAction, Site, SiteType, UserRole
from app.scoping import RequestScope

router = APIRouter(prefix="/admin/sites", tags=["admin"])

_only_practice_admin = require_roles(UserRole.practice_administrator)

_SITE_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND, detail="Site not found"
)


def _require_site(db: Session, site_id: int, practice_id: int) -> Site:
    site = crud.get_site(db, practice_id=practice_id, site_id=site_id)
    if site is None:
        raise _SITE_NOT_FOUND
    return site


@router.get(
    "",
    response_model=list[schemas.SiteRead],
    dependencies=[Depends(_only_practice_admin)],
)
def list_sites(
    scope: RequestScope = Depends(get_request_scope),
    db: Session = Depends(get_db),
    type: SiteType | None = Query(default=None),
) -> list[schemas.SiteRead]:
    rows = crud.list_sites(db, practice_id=scope.practice_id, type=type)
    return [schemas.SiteRead.model_validate(row) for row in rows]


@router.post(
    "",
    response_model=schemas.SiteRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_only_practice_admin)],
)
def create_site(
    data: schemas.SiteInline,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.SiteRead:
    site = crud.create_site(db, practice_id=scope.practice_id, data=data)
    recorder(AuditAction.create, "site", site.id)
    db.commit()
    return schemas.SiteRead.model_validate(site)


@router.get(
    "/{site_id}",
    response_model=schemas.SiteRead,
    dependencies=[Depends(_only_practice_admin)],
)
def read_site(
    site_id: int,
    scope: RequestScope = Depends(get_request_scope),
    db: Session = Depends(get_db),
) -> schemas.SiteRead:
    return schemas.SiteRead.model_validate(
        _require_site(db, site_id, scope.practice_id)
    )


@router.patch(
    "/{site_id}",
    response_model=schemas.SiteRead,
    dependencies=[Depends(_only_practice_admin)],
)
def update_site(
    site_id: int,
    data: schemas.SiteUpdate,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.SiteRead:
    site = _require_site(db, site_id, scope.practice_id)
    crud.update_site(db, site, data)
    recorder(AuditAction.update, "site", site.id)
    db.commit()
    return schemas.SiteRead.model_validate(site)
