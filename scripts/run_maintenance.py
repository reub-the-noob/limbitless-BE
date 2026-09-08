"""Run one notification maintenance pass and exit (requirements Section
5.6).

    python -m scripts.run_maintenance

Raises milestone due/overdue notifications for slipped milestones, then
sends email for everything now due. Idempotent — safe to run as often as
you like. This is the plain-cron / systemd-timer path; the same work is
also available as the token-gated ``POST /notifications/dispatch-due``
endpoint and, while the API is up, the in-process scheduler
(``MAINTENANCE_INTERVAL_MINUTES``).
"""

import json

from sqlalchemy.orm import Session

from app import config, crud
from app.database import SessionLocal


def main(db: Session | None = None) -> dict[str, int]:
    own = db is None
    db = db or SessionLocal()
    try:
        return crud.run_maintenance(
            db,
            grace_days=config.MILESTONE_OVERDUE_GRACE_DAYS,
            max_age_days=config.EMAIL_DISPATCH_MAX_AGE_DAYS,
        )
    finally:
        if own:
            db.close()


if __name__ == "__main__":
    print(json.dumps(main()))
