"""The shared maintenance pass (`crud.run_maintenance`), the
`scripts.run_maintenance` one-shot, and the optional in-process
scheduler (`app.scheduler`)."""

import pytest
from sqlalchemy.orm import Session

from app import config, crud, scheduler
from scripts import run_maintenance

_KEYS = {
    "milestones_due",
    "milestones_overdue",
    "emails_sent",
    "emails_failed",
    "emails_skipped",
}


def test_run_maintenance_returns_the_merged_counts(db: Session) -> None:
    result = crud.run_maintenance(db, grace_days=7, max_age_days=7)
    assert set(result) == _KEYS
    assert all(v == 0 for v in result.values())


def test_run_maintenance_endpoint_goes_through_the_shared_pass(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[bool] = []
    real = crud.run_maintenance

    def spy(*args, **kwargs):
        calls.append(True)
        return real(*args, **kwargs)

    monkeypatch.setattr(crud, "run_maintenance", spy)
    monkeypatch.setattr(config, "NOTIFICATIONS_DISPATCH_TOKEN", "secret")

    resp = client.post(
        "/notifications/dispatch-due", headers={"X-Dispatch-Token": "secret"}
    )
    assert resp.status_code == 200
    assert resp.json().keys() >= _KEYS
    assert calls == [True]


def test_run_maintenance_script_main(db: Session) -> None:
    assert set(run_maintenance.main(db)) == _KEYS


def test_scheduler_is_a_noop_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "MAINTENANCE_INTERVAL_MINUTES", 0)
    scheduler.start_scheduler()
    assert scheduler._scheduler is None
    scheduler.stop_scheduler()  # safe even though nothing started


def test_scheduler_does_not_start_on_a_non_postgres_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "MAINTENANCE_INTERVAL_MINUTES", 5)
    monkeypatch.setattr(scheduler, "_is_postgres", lambda: False)
    scheduler.start_scheduler()
    assert scheduler._scheduler is None


def test_scheduler_tick_runs_the_pass_without_raising(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scheduler, "SessionLocal", lambda: db)
    monkeypatch.setattr(scheduler, "_is_postgres", lambda: False)
    scheduler._tick()  # must swallow everything and not raise
