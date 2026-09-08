# Welcome to Limb-itless Project

## Team members

- Siphamandla Malaza
- Jessica Harrison
- Reuben Ellis
- Nkanyiso Shabane

## Branching Strategy

This project follows Trunk-Based Development.

All work is based on a single primary branch (main), which is always kept in a deployable state. Changes are developed in short-lived branches created from main and merged back frequently through pull requests.

This approach promotes:
- Continuous integration and fast feedback
- Reduced merge conflicts
- Smaller, incremental changes

To maintain stability, all changes merged into main must pass automated checks and code review before integration.

## Continuous integration

`.github/workflows/ci.yml` runs on every pull request and on pushes to `main`:

- **pytest** — the full test suite (in-memory SQLite, no services)
- **alembic** — against a Postgres 17 service container: `upgrade head`,
  `alembic check` (fails on model/migration drift), then `downgrade base`
  and `upgrade head` to prove the migrations are reversible

## Running Application
1) Ensure you have installed and activated the virtual environment: 

    In main directory run commands: 
   - python -m venv venv
   - macOS/Linux: `source venv/bin/activate`
   - Windows (Command Prompt): `venv\Scripts\activate`
   - Windows (PowerShell): `venv\Scripts\Activate.ps1`

2) Install the requirements found in requirements.txt

    In main directory run command:
   - pip install -r requirements.txt

3) Configure the database:

    Development uses a local Postgres database. Create the role and database
    once (defaults match `.env.example`):
   - `createuser limbitless --pwprompt` (enter `limbitless`, or your own password)
   - `createdb limbitless --owner limbitless`

    If your credentials differ from the defaults, set `DATABASE_URL` for the
    shell (see `.env.example` for the format):
   - macOS/Linux: `export DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/limbitless`
   - Windows (PowerShell): `$env:DATABASE_URL = "postgresql+psycopg://user:pass@localhost:5432/limbitless"`

    With the defaults in place, `DATABASE_URL` can be left unset.

4) Apply database migrations:

    In main directory run command:
   - `alembic upgrade head`

    Alembic reads the same `DATABASE_URL`. Create a new migration after changing
    a model with `alembic revision --autogenerate -m "short description"`.

5) (optional) Load sample data for local development:

    In main directory run command:
   - `python -m scripts.seed` — two practices (one multi-site) and one user
     per role; safe to re-run
   - `python -m scripts.seed --reset` — wipe the seeded tables first

    All seeded users share the password `Password123!`. The script prints the
    full list of accounts on completion.

6) Start application:

    In main directory run command:
    - fastapi run (for production server)
    - fastapi dev (for dev server)

## Access API
1) To access the interactive API documentation, 
go to http://127.0.0.1:8000/docs. 
   - FastAPI automatically generates this documentation using Swagger UI.
2) You can also access alternative API documentation at http://127.0.0.1:8000/redoc, 
which uses ReDoc.

## Authentication

JWT-based, configured via environment variables (see `.env.example`):
`JWT_SECRET_KEY` (must be a strong random value outside local dev),
`JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS`.

Endpoints:
- `POST /auth/login` — form body `username` (the user's email) + `password`;
  returns an `access_token` / `refresh_token` pair.
- `POST /auth/refresh` — JSON `{ "refresh_token": "..." }`; returns a fresh pair.
- `GET /auth/me` — the authenticated user; send `Authorization: Bearer <access_token>`.

There is no self-service registration yet — users are created by the seed
script (Phase 0) or practice/platform admin endpoints (Phase 1). In Swagger,
the **Authorize** button drives the same `/auth/login` password flow.

### CORS

The browser frontend runs on a different origin, so the API allows the
origins listed in `CORS_ALLOW_ORIGINS` (comma-separated; defaults to the
local Angular dev server, `http://localhost:4200` and
`http://127.0.0.1:4200`). Set it for any other frontend origin.

## Auditing

Access to and changes in patient data are recorded in `audit_log_entries`
(requirements Section 5.7). Endpoints that touch patient data call
`app.audit.record(...)` or inject the `get_audit_recorder` dependency and
call it once per read/write — for example:

```python
recorder(AuditAction.read, "patient", patient_id)
```

Each entry stores the actor, action (`read` / `create` / `update` /
`delete`), `entity_type`, `entity_id`, practice, and timestamp. The table
is append-only. Patient-data endpoints land in Phase 1, so this is a
contract for that work rather than something wired to an endpoint yet.
