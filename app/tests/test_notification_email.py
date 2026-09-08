"""Notification email delivery (requirements Section 5.6): the render /
send layer, the dispatch pass, and the token-gated dispatch endpoint."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app import config, crud, email, security
from app import notifications as notify_mod
from app.models import Notification, NotificationType, User
from app.tests.conftest import World


def auth(user: User) -> dict[str, str]:
    token = security.create_access_token(
        user.id,
        role=user.role.value,
        practice_id=user.practice_id,
        site_id=user.site_id,
    )
    return {"Authorization": f"Bearer {token}"}


def _notif(db: Session, user_id: int, **kw) -> Notification:
    now = datetime.now()
    row = Notification(
        user_id=user_id,
        type=kw.get("type", NotificationType.appointment_cancelled),
        payload=kw.get(
            "payload",
            {"appointment_type": "review", "scheduled_start": "2026-09-11T09:00:00"},
        ),
        deliver_at=kw.get("deliver_at"),
        emailed_at=kw.get("emailed_at"),
        created_at=kw.get("created_at", now),
    )
    db.add(row)
    db.commit()
    return row


# --- render / send --------------------------------------------------


def test_render_email_uses_the_payload() -> None:
    row = Notification(
        type=NotificationType.appointment_cancelled,
        payload={
            "appointment_type": "fitting",
            "scheduled_start": "2026-09-11T09:00:00",
            "reason": "Clinic closed",
        },
    )
    subject, body = notify_mod.render_email(row)
    assert subject == "Appointment cancelled"
    assert "fitting" in body and "Clinic closed" in body and "2026-09-11 09:00" in body


def test_send_noop_backend_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "EMAIL_BACKEND", "noop")
    email.send(to="a@b.co", subject="s", body="b")  # must not raise


def test_send_console_backend_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(config, "EMAIL_BACKEND", "console")
    with caplog.at_level("INFO"):
        email.send(to="pat@example.co.za", subject="Hi", body="Body text")
    assert "pat@example.co.za" in caplog.text and "Body text" in caplog.text


def test_send_smtp_backend_builds_and_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            sent.append(("starttls",))

        def login(self, u, p):
            sent.append(("login", u))

        def send_message(self, msg):
            sent.append(("send", msg["To"], msg["Subject"]))

    monkeypatch.setattr(config, "EMAIL_BACKEND", "smtp")
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.example.co.za")
    monkeypatch.setattr(config, "SMTP_USERNAME", "u")
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)

    email.send(to="pat@example.co.za", subject="Hi", body="Body")
    assert ("send", "pat@example.co.za", "Hi") in sent
    assert ("starttls",) in sent and ("login", "u") in sent


def test_send_smtp_without_host_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "EMAIL_BACKEND", "smtp")
    monkeypatch.setattr(config, "SMTP_HOST", "")
    with pytest.raises(RuntimeError):
        email.send(to="a@b.co", subject="s", body="b")


# --- dispatch pass ------------------------------------------------


def test_dispatch_sends_due_notifications_and_stamps_them(
    db: Session, world: World
) -> None:
    a = _notif(db, world.clinician_a.id)
    b = _notif(db, world.prosthetist_a.id)

    result = crud.dispatch_due_emails(db, max_age_days=7)

    assert result == {"sent": 2, "failed": 0, "skipped": 0}
    db.refresh(a)
    db.refresh(b)
    assert a.emailed_at is not None and b.emailed_at is not None

    # second pass has nothing to do
    assert crud.dispatch_due_emails(db, max_age_days=7)["sent"] == 0


def test_dispatch_skips_future_reminders_and_old_rows(
    db: Session, world: World
) -> None:
    _notif(
        db,
        world.clinician_a.id,
        type=NotificationType.appointment_reminder,
        deliver_at=datetime.now() + timedelta(hours=6),
    )
    _notif(
        db,
        world.clinician_a.id,
        created_at=datetime.now() - timedelta(days=30),
    )

    assert crud.dispatch_due_emails(db, max_age_days=7)["sent"] == 0


def test_dispatch_retries_a_failed_send(
    db: Session, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _notif(db, world.clinician_a.id)

    boom = {"n": 0}

    def flaky(**_kw):
        boom["n"] += 1
        if boom["n"] == 1:
            raise RuntimeError("smtp down")

    monkeypatch.setattr("app.email.send", flaky)

    first = crud.dispatch_due_emails(db, max_age_days=7)
    assert first == {"sent": 0, "failed": 1, "skipped": 0}
    db.refresh(row)
    assert row.emailed_at is None  # left unstamped -> will retry

    second = crud.dispatch_due_emails(db, max_age_days=7)
    assert second["sent"] == 1
    db.refresh(row)
    assert row.emailed_at is not None


# --- endpoint --------------------------------------------------


def test_dispatch_endpoint_disabled_without_a_token(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "NOTIFICATIONS_DISPATCH_TOKEN", "")
    resp = client.post("/notifications/dispatch-due")
    assert resp.status_code == 503


def test_dispatch_endpoint_rejects_a_bad_token(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "NOTIFICATIONS_DISPATCH_TOKEN", "secret")
    resp = client.post(
        "/notifications/dispatch-due", headers={"X-Dispatch-Token": "wrong"}
    )
    assert resp.status_code == 401


def test_dispatch_endpoint_runs_with_the_right_token(
    client, db: Session, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "NOTIFICATIONS_DISPATCH_TOKEN", "secret")
    _notif(db, world.clinician_a.id)

    resp = client.post(
        "/notifications/dispatch-due", headers={"X-Dispatch-Token": "secret"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["emails_sent"] == 1
    assert body["emails_failed"] == 0
