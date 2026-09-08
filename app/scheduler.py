"""Optional in-process scheduler for the notification maintenance pass
(requirements Section 5.6).

Enabled by ``MAINTENANCE_INTERVAL_MINUTES`` > 0. Runs
:func:`app.crud.run_maintenance` on a background thread every N minutes
for as long as the API process is up, so appointment reminders and
milestone due/overdue notifications go out on time without an external
cron.

Multi-worker safe: every worker starts its own scheduler, but each tick
first takes a Postgres advisory lock, so only one process actually runs
the pass. When it is disabled, or when the DB is not Postgres (the test
suite), nothing is scheduled.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import text

from app import config, crud
from app.database import SessionLocal, engine

logger = logging.getLogger(__name__)

# Arbitrary constant key for pg_try_advisory_lock — shared by every
# worker so only one holds it at a time.
_LOCK_KEY = 528_741_003

_scheduler = None  # BackgroundScheduler | None


def _is_postgres() -> bool:
    return engine.url.get_backend_name().startswith("postgresql")


def _tick() -> None:
    """One maintenance pass, guarded by an advisory lock. Never raises —
    a bad tick must not stop the scheduler."""
    db = SessionLocal()
    try:
        if _is_postgres():
            locked = db.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": _LOCK_KEY}
            ).scalar()
            if not locked:
                logger.debug("maintenance tick skipped: lock held elsewhere")
                return
        try:
            result = crud.run_maintenance(
                db,
                grace_days=config.MILESTONE_OVERDUE_GRACE_DAYS,
                max_age_days=config.EMAIL_DISPATCH_MAX_AGE_DAYS,
            )
            if any(result.values()):
                logger.info("maintenance tick: %s", result)
        finally:
            if _is_postgres():
                db.execute(
                    text("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_KEY}
                )
                db.commit()
    except Exception:  # noqa: BLE001 - keep the scheduler alive
        logger.exception("maintenance tick failed")
    finally:
        db.close()


def start_scheduler() -> None:
    """Start the background scheduler if it is configured on. A no-op
    otherwise, and idempotent."""
    global _scheduler
    minutes = config.MAINTENANCE_INTERVAL_MINUTES
    if minutes <= 0 or _scheduler is not None:
        return
    if not _is_postgres():
        logger.info("maintenance scheduler not started: DB is not Postgres")
        return

    from apscheduler.schedulers.background import BackgroundScheduler

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        _tick,
        trigger="interval",
        minutes=minutes,
        id="maintenance",
        max_instances=1,
        coalesce=True,
        # first pass shortly after boot, not a full interval later
        next_run_time=datetime.now() + timedelta(seconds=10),
    )
    _scheduler.start()
    logger.info("maintenance scheduler started: every %s min", minutes)


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
