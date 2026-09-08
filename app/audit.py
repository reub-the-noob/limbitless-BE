"""Audit logging for access to and changes in patient data (requirements 5.7).

Phase 1's patient / device / milestone endpoints record an entry on every
read and write, either by calling :func:`record` directly or by injecting
the :func:`get_audit_recorder` dependency. It is deliberately explicit
rather than middleware so each entry carries the real action and entity
id and is written inside the request's own transaction.

``entity_type`` is a singular snake_case logical name (``"patient"``,
``"device"``); ``entity_id`` may be ``None`` for a list or
search read that isn't about one row.
"""

from dataclasses import dataclass

from fastapi import Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import AuditAction, AuditLogEntry, User


def record(
    db: Session,
    *,
    actor_id: int | None,
    action: AuditAction,
    entity_type: str,
    entity_id: int | None = None,
    practice_id: int | None = None,
) -> AuditLogEntry:
    """Append one audit entry to the current transaction (no commit)."""
    entry = AuditLogEntry(
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        practice_id=practice_id,
    )
    db.add(entry)
    db.flush()
    return entry


@dataclass
class AuditRecorder:
    """Request-scoped audit helper bound to the caller and their session."""

    db: Session
    actor: User

    def __call__(
        self,
        action: AuditAction,
        entity_type: str,
        entity_id: int | None = None,
        *,
        practice_id: int | None = None,
    ) -> AuditLogEntry:
        return record(
            self.db,
            actor_id=self.actor.id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            practice_id=self.actor.practice_id if practice_id is None else practice_id,
        )


def get_audit_recorder(
    db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
) -> AuditRecorder:
    return AuditRecorder(db=db, actor=actor)
