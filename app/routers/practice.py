"""Read-only lookups a clinician needs about their own practice.

Right now just the clinical-staff roster that populates the patient
"assign staff" picker (requirements Section 4). ``/admin/users`` covers
the same ground but is practice-administrator only, so clinicians and
prosthetists get this narrower, unpaged view instead.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import crud, schemas
from app.database import get_db
from app.deps import get_request_scope, require_roles
from app.models import UserRole
from app.scoping import RequestScope

router = APIRouter(prefix="/practice", tags=["practice"])

READ_ROLES = (
    UserRole.clinician,
    UserRole.prosthetist,
    UserRole.practice_administrator,
)


@router.get(
    "/clinical-staff",
    response_model=list[schemas.ClinicalStaffRead],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def list_clinical_staff(
    scope: RequestScope = Depends(get_request_scope),
    db: Session = Depends(get_db),
) -> list[schemas.ClinicalStaffRead]:
    rows = crud.list_clinical_staff(db, practice_id=scope.practice_id)
    return [
        schemas.ClinicalStaffRead(
            id=user.id,
            email=user.email,
            role=user.role,
            site_id=user.site_id,
            site_name=user.site.name if user.site else None,
        )
        for user in rows
    ]
