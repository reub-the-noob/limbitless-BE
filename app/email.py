"""Outbound email for notification delivery (requirements Section 5.6).

Deliberately minimal and dependency-free: stdlib :mod:`smtplib` and a
plain-text body. Three backends, chosen by ``config.EMAIL_BACKEND``:

* ``noop`` (default) - nothing is sent. Safe for tests and any machine
  without SMTP configured.
* ``console`` - the message is logged at INFO. Useful for local dev.
* ``smtp`` - a real send via ``config.SMTP_*``.

:func:`send` raises on failure; the caller (the dispatch pass in
:mod:`app.crud`) catches that and leaves ``Notification.emailed_at``
unset so the next pass retries.
"""

import logging
import smtplib
from email.message import EmailMessage

from app import config

logger = logging.getLogger(__name__)


def _send_smtp(message: EmailMessage) -> None:
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
        if config.SMTP_STARTTLS:
            smtp.starttls()
        if config.SMTP_USERNAME:
            smtp.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
        smtp.send_message(message)


def send(*, to: str, subject: str, body: str) -> None:
    """Deliver one plain-text email via the configured backend."""
    backend = config.EMAIL_BACKEND

    if backend == "noop":
        logger.debug("email backend is noop; not sending to %s (%r)", to, subject)
        return

    message = EmailMessage()
    message["From"] = config.EMAIL_FROM
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    if backend == "console":
        logger.info(
            "EMAIL (console backend)\nFrom: %s\nTo: %s\nSubject: %s\n\n%s",
            config.EMAIL_FROM,
            to,
            subject,
            body,
        )
        return

    if backend == "smtp":
        if not config.SMTP_HOST:
            raise RuntimeError("EMAIL_BACKEND=smtp but SMTP_HOST is not set")
        _send_smtp(message)
        return

    raise RuntimeError(f"unknown EMAIL_BACKEND {backend!r}")
