"""Practitioner-published availability (requirements Section 5.9).

A practitioner publishes slots for patients to book into; publishing the
slot *is* the confirmation, so there's no separate approval step here.
Booking itself (turning an open slot into an appointment) lives in the
appointments router once that lands — this is the practitioner-facing
publish/block side only, plus the practice-scoped read used by staff.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import crud, schemas
from app.database import get_db
from app.deps import get_current_user, get_request_scope, require_roles
from app.models import SlotStatus, User, UserRole
from app.scoping import RequestScope

router = APIRouter(prefix="/availability", tags=["availability"])

CRUD_ROLES = (UserRole.clinician, UserRole.prosthetist)
READ_ROLES = (*CRUD_ROLES, UserRole.practice_administrator)

_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND, detail="Slot not found"
)


def _read(slot) -> schemas.AvailabilitySlotRead:
    return schemas.AvailabilitySlotRead(
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


@router.post(
    "",
    response_model=schemas.AvailabilitySlotRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def create_slot(
    data: schemas.AvailabilitySlotCreate,
    caller: User = Depends(get_current_user),
    scope: RequestScope = Depends(get_request_scope),
    db: Session = Depends(get_db),
) -> schemas.AvailabilitySlotRead:
    site_id = data.site_id if data.site_id is not None else caller.site_id
    if site_id is not None and not crud.site_belongs_to_practice(
        db, site_id=site_id, practice_id=scope.practice_id
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="site_id does not belong to your practice",
        )
    if crud.slot_overlaps(
        db,
        practitioner_id=caller.id,
        start_time=data.start_time,
        end_time=data.end_time,
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="This overlaps a slot you already have",
        )
    slot = crud.create_slot(
        db,
        practitioner_id=caller.id,
        practice_id=scope.practice_id,
        site_id=site_id,
        data=data,
    )
    db.commit()
    return _read(slot)


@router.get(
    "",
    response_model=list[schemas.AvailabilitySlotRead],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def list_slots(
    scope: RequestScope = Depends(get_request_scope),
    db: Session = Depends(get_db),
    practitioner_id: int | None = Query(default=None),
    status_: SlotStatus | None = Query(default=None, alias="status"),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
) -> list[schemas.AvailabilitySlotRead]:
    rows = crud.list_slots(
        db,
        practice_id=scope.practice_id,
        practitioner_id=practitioner_id,
        status=status_,
        date_from=date_from,
        date_to=date_to,
    )
    return [_read(row) for row in rows]


@router.get(
    "/{slot_id}",
    response_model=schemas.AvailabilitySlotRead,
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def read_slot(
    slot_id: int,
    scope: RequestScope = Depends(get_request_scope),
    db: Session = Depends(get_db),
) -> schemas.AvailabilitySlotRead:
    slot = crud.get_slot(db, practice_id=scope.practice_id, slot_id=slot_id)
    if slot is None:
        raise _NOT_FOUND
    return _read(slot)


@router.patch(
    "/{slot_id}",
    response_model=schemas.AvailabilitySlotRead,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def update_slot(
    slot_id: int,
    data: schemas.AvailabilitySlotUpdate,
    caller: User = Depends(get_current_user),
    scope: RequestScope = Depends(get_request_scope),
    db: Session = Depends(get_db),
) -> schemas.AvailabilitySlotRead:
    slot = crud.get_slot(db, practice_id=scope.practice_id, slot_id=slot_id)
    if slot is None:
        raise _NOT_FOUND
    if slot.practitioner_id != caller.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="You can only manage your own availability",
        )
    if slot.status == SlotStatus.booked:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="This slot is booked; cancel the appointment first",
        )

    fields = data.model_dump(exclude_unset=True)
    new_start = fields.get("start_time", slot.start_time)
    new_end = fields.get("end_time", slot.end_time)
    if new_end <= new_start:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="end_time must be after start_time",
        )
    if ("start_time" in fields or "end_time" in fields) and crud.slot_overlaps(
        db,
        practitioner_id=caller.id,
        start_time=new_start,
        end_time=new_end,
        exclude_id=slot.id,
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="This overlaps a slot you already have",
        )

    crud.update_slot(db, slot, data)
    db.commit()
    return _read(slot)
