"""Runtime configuration, read from the environment.

Same ``os.getenv`` pattern as :mod:`app.database`. Every value has a
local-dev default; ``JWT_SECRET_KEY`` must be overridden anywhere that
isn't a developer's machine.
"""

import os

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-secret-do-not-use-in-production")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

# Browser origins allowed to call the API. Comma-separated; defaults to the
# local Angular dev server (both host spellings).
CORS_ALLOW_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOW_ORIGINS",
        "http://localhost:4200,http://127.0.0.1:4200",
    ).split(",")
    if origin.strip()
]

# --- notification email delivery (requirements Section 5.6) ------------
# EMAIL_BACKEND: "noop" (default - nothing is sent, safe for dev/tests),
# "console" (the message is logged), or "smtp" (real send via SMTP_*).
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "noop")
EMAIL_FROM = os.getenv("EMAIL_FROM", "no-reply@limbitless.example")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_STARTTLS = os.getenv("SMTP_STARTTLS", "true").lower() != "false"

# Shared secret a cron job presents (X-Dispatch-Token header) to run the
# email dispatch pass. Empty -> the dispatch endpoint is disabled (503).
NOTIFICATIONS_DISPATCH_TOKEN = os.getenv("NOTIFICATIONS_DISPATCH_TOKEN", "")
# Don't email notifications older than this many days - stops a backlog
# flood the first time email is switched on.
EMAIL_DISPATCH_MAX_AGE_DAYS = int(os.getenv("EMAIL_DISPATCH_MAX_AGE_DAYS", "7"))

# How many days past its target date an open milestone must be before the
# maintenance pass raises a "milestone overdue" notification (a "due"
# one goes out as soon as the target date passes).
MILESTONE_OVERDUE_GRACE_DAYS = int(os.getenv("MILESTONE_OVERDUE_GRACE_DAYS", "7"))
