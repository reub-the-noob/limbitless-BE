"""A per-patient progress report (requirements Section 5.5): milestones
met, current PROM trends, viewable on screen and exportable (the FE
prints it) for sharing with a receiving clinician at handover. Same
reader roles as the patient timeline.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import crud, reports, schemas
from app.audit import AuditRecorder, get_audit_recorder
from app.database import get_db
from app.deps import get_request_scope, require_roles
from app.models import AuditAction, UserRole
from app.scoping import RequestScope

router = APIRouter(prefix="/patients/{patient_id}", tags=["patient-report"])

READ_ROLES = (
    UserRole.clinician,
    UserRole.prosthetist,
    UserRole.practice_administrator,
)

_PATIENT_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND, detail="Patient not found"
)


@router.get(
    "/report",
    response_model=schemas.PatientReport,
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def patient_report(
    patient_id: int,
    scope: RequestScope = Depends(get_request_scope),
    recorder: AuditRecorder = Depends(get_audit_recorder),
    db: Session = Depends(get_db),
) -> schemas.PatientReport:
    patient = crud.get_patient(
        db, practice_id=scope.practice_id, patient_id=patient_id
    )
    if patient is None:
        raise _PATIENT_NOT_FOUND
    report = reports.build_patient_report(db, patient=patient)
    recorder(AuditAction.read, "patient_report", patient_id)
    db.commit()
    return report
