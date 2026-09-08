from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import config, scheduler
from app.routers import (
    account_link,
    admin_audit,
    admin_sites,
    admin_users,
    appointments,
    assignments,
    auth,
    availability,
    dashboard,
    devices,
    involvements,
    medical_aid,
    milestones,
    notes,
    notifications,
    patient_report,
    patients,
    platform,
    portal,
    practice,
    proms,
    reports,
    review,
)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    scheduler.start_scheduler()
    try:
        yield
    finally:
        scheduler.stop_scheduler()


app = FastAPI(title="Limb-itless API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(account_link.router)
app.include_router(patients.router)
app.include_router(assignments.router)
app.include_router(involvements.router)
app.include_router(devices.router)
app.include_router(devices.overview_router)
app.include_router(milestones.router)
app.include_router(proms.router)
app.include_router(notes.router)
app.include_router(notifications.router)
app.include_router(patient_report.router)
app.include_router(medical_aid.router)
app.include_router(availability.router)
app.include_router(appointments.router)
app.include_router(dashboard.router)
app.include_router(reports.router)
app.include_router(portal.router)
app.include_router(review.router)
app.include_router(practice.router)
app.include_router(admin_users.router)
app.include_router(admin_sites.router)
app.include_router(admin_audit.router)
app.include_router(platform.router)


@app.get("/")
def read_root() -> dict[str, str]:
    return {"status": "ok"}
