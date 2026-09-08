"""Clinical note endpoints and the combined patient timeline.

Notes are free text scoped to a patient. The timeline merges milestones,
PROMs and notes into one chronological feed (requirements Section 5.3).
All routes resolve the patient within the caller's practice first (404
otherwise) and are audited. Clinicians and prosthetists write notes;
practice administrators read (Section 4).
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import crud, schemas
from app.audit import AuditRecorder, get_audit_recorder
from app.database import get_db
from app.deps import get_current_user, get_request_scope, require_roles
from app.models import (
    AuditAction,
    ClinicalNote,
    Patient,
    PromRecord,
    RecoveryMilestone,
    User,
    UserRole,
)
from app.scoping import RequestScope

router = APIRouter(prefix="/patients/{patient_id}", tags=["clinical-notes"])

CRUD_ROLES = (UserRole.clinician, UserRole.prosthetist)
READ_ROLES = (*CRUD_ROLES, UserRole.practice_administrator)

_PATIENT_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND, detail="Patient not found"
)
_NOTE_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND, detail="Note not found"
)


def _require_patient(db: Session, patient_id: int, practice_id: int) -> Patient:
    patient = crud.get_patient(db, practice_id=practice_id, patient_id=patient_id)
    if patient is None:
        raise _PATIENT_NOT_FOUND
    return patient


def _require_note(db: Session, patient_id: int, note_id: int) -> ClinicalNote:
    note = crud.get_note(db, patient_id=patient_id, note_id=note_id)
    if note is None:
        raise _NOTE_NOT_FOUND
    return note


def _check_involvement(
    db: Session, involvement_id: int | None, patient_id: int
) -> None:
    if involvement_id is not None and (
        crud.get_involvement(
            db, patient_id=patient_id, involvement_id=involvement_id
        )
        is None
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="involvement_id does not belong to this patient",
        )


# --- notes ----------------------------------------------------------------


@router.post(
    "/notes",
    response_model=schemas.NoteRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def create_note(
    patient_id: int,
    data: schemas.NoteCreate,
    scope: RequestScope = Depends(get_request_scope),
    caller: User = Depends(get_current_user),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.NoteRead:
    _require_patient(db, patient_id, scope.practice_id)
    _check_involvement(db, data.involvement_id, patient_id)
    note = crud.create_note(
        db,
        patient_id=patient_id,
        author_id=caller.id,
        body=data.body,
        involvement_id=data.involvement_id,
    )
    recorder(AuditAction.create, "clinical_note", note.id)
    db.commit()
    return schemas.NoteRead.model_validate(note)


@router.get(
    "/notes",
    response_model=list[schemas.NoteRead],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def list_notes(
    patient_id: int,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> list[schemas.NoteRead]:
    _require_patient(db, patient_id, scope.practice_id)
    rows = crud.list_notes(db, patient_id=patient_id)
    recorder(AuditAction.read, "clinical_note", None)
    db.commit()
    return [schemas.NoteRead.model_validate(row) for row in rows]


@router.get(
    "/notes/{note_id}",
    response_model=schemas.NoteRead,
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def read_note(
    patient_id: int,
    note_id: int,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.NoteRead:
    _require_patient(db, patient_id, scope.practice_id)
    note = _require_note(db, patient_id, note_id)
    recorder(AuditAction.read, "clinical_note", note.id)
    db.commit()
    return schemas.NoteRead.model_validate(note)


@router.patch(
    "/notes/{note_id}",
    response_model=schemas.NoteRead,
    dependencies=[Depends(require_roles(*CRUD_ROLES))],
)
def update_note(
    patient_id: int,
    note_id: int,
    data: schemas.NoteUpdate,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.NoteRead:
    _require_patient(db, patient_id, scope.practice_id)
    note = _require_note(db, patient_id, note_id)
    fields = data.model_dump(exclude_unset=True)
    if "involvement_id" in fields:
        _check_involvement(db, data.involvement_id, patient_id)
    crud.update_note(
        db,
        note,
        body=data.body,
        involvement_id=data.involvement_id,
        set_involvement="involvement_id" in fields,
    )
    recorder(AuditAction.update, "clinical_note", note.id)
    db.commit()
    return schemas.NoteRead.model_validate(note)


# --- timeline -----------------------------------------------------------


def _at_midnight(value) -> datetime:
    return datetime.combine(value, datetime.min.time())


def _humanise(value: str) -> str:
    return value.replace("_", " ")


def _milestone_event(m: RecoveryMilestone) -> schemas.TimelineEvent:
    occurred = m.completed_date or m.target_date
    at = _at_midnight(occurred) if occurred is not None else m.created_at
    return schemas.TimelineEvent(
        kind="milestone",
        occurred_at=at,
        ref_id=m.id,
        title=f"{_humanise(m.milestone_type.value)} — {_humanise(m.status.value)}",
        milestone=schemas.MilestoneRead.model_validate(m),
    )


def _prom_event(p: PromRecord) -> schemas.TimelineEvent:
    value = "—" if p.score is None else f"{p.score:g}"
    title = f"{_humanise(p.instrument.value)}: {value}"
    if p.flagged:
        title += " (flagged)"
    return schemas.TimelineEvent(
        kind="prom",
        occurred_at=p.recorded_at,
        ref_id=p.id,
        title=title,
        prom=schemas.PromRead.model_validate(p),
    )


def _note_event(n: ClinicalNote) -> schemas.TimelineEvent:
    snippet = n.body.strip().splitlines()[0][:80] if n.body.strip() else "Note"
    return schemas.TimelineEvent(
        kind="note",
        occurred_at=n.created_at,
        ref_id=n.id,
        title=snippet,
        note=schemas.NoteRead.model_validate(n),
    )


@router.get(
    "/timeline",
    response_model=list[schemas.TimelineEvent],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def patient_timeline(
    patient_id: int,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[schemas.TimelineEvent]:
    _require_patient(db, patient_id, scope.practice_id)

    events: list[schemas.TimelineEvent] = []
    events += [
        _milestone_event(m)
        for m in crud.list_milestones(db, patient_id=patient_id)
    ]
    events += [_prom_event(p) for p in crud.list_proms(db, patient_id=patient_id)]
    events += [_note_event(n) for n in crud.list_notes(db, patient_id=patient_id)]
    events.sort(key=lambda e: e.occurred_at, reverse=True)

    recorder(AuditAction.read, "patient_timeline", patient_id)
    db.commit()
    return events[:limit]
