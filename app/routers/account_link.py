"""Self-service walk-in-record claim (requirements Section 5.11). A
patient-role login - whether just self-registered or one staff already
created directly - matches an existing unclaimed walk-in
:class:`~app.models.Patient` record by identity number, gated by a
contact detail already on file as the second factor. A login may claim
several records (walk-ins at different practices), so claiming again is
allowed - the record picker lives at ``GET /portal/records``. Only
affects what the claiming patient can see; a *different* practitioner
gaining access is Section 5.12's separate, unbuilt ``ConsentGrant``.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import crud, schemas
from app.database import get_db
from app.deps import get_current_user, require_roles
from app.models import User, UserRole

router = APIRouter(
    prefix="/account-link-requests",
    tags=["account-link"],
    dependencies=[Depends(require_roles(UserRole.patient))],
)

_NO_MATCH = HTTPException(
    status.HTTP_400_BAD_REQUEST,
    detail="We couldn't find a matching record with those details.",
)


@router.post("", response_model=schemas.PortalProfile, status_code=status.HTTP_200_OK)
def claim_walk_in_record(
    data: schemas.AccountLinkClaim,
    caller: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> schemas.PortalProfile:
    patient = crud.claim_walk_in_patient(
        db,
        user=caller,
        identifier=data.identifier,
        contact_value=data.contact_value,
    )
    db.commit()
    if patient is None:
        raise _NO_MATCH

    return schemas.PortalProfile(
        **schemas.PatientRead.model_validate(patient).model_dump(),
        practice_name=patient.practice.name if patient.practice else None,
        site_name=patient.site.name if patient.site else None,
    )
