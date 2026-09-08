"""Seed the local database with multi-practice sample data.

Isolation bugs only surface with more than one practice and more than one
site, so the fixture is a multi-site hospital network plus a single-site
private practice, with at least one user per role.

Run after ``alembic upgrade head``:

    python -m scripts.seed           # idempotent: creates only what's missing
    python -m scripts.seed --reset   # wipe the seeded tables first, then seed

Practices, sites and users live here; the Phase 1 clinical sample
(patients with devices, milestones, PROMs and notes) lives in
:mod:`scripts.seed_clinical` and is applied at the end of :func:`seed`.
The ``patient`` and ``medical_aid_reviewer`` users are placeholders for
Phase 2.
"""

import argparse
import sys

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app import crud, security
from app.database import SessionLocal, engine
from scripts import seed_clinical
from app.models import (
    AccountLinkRequest,
    AuditLogEntry,
    AvailabilitySlot,
    Notification,
    Practice,
    PracticeType,
    Site,
    SiteType,
    User,
    UserRole,
)

DEV_PASSWORD = "Password123!"

PRACTICES = [
    {
        "name": "Northgate Rehabilitation Network",
        "type": PracticeType.hospital_network,
        "address": "1 Northgate Ave, Johannesburg",
        "sites": [
            {
                "name": "Northgate Main Hospital",
                "type": SiteType.location,
                "address": "1 Northgate Ave, Johannesburg",
            },
            {
                "name": "Rosebank Gait Lab",
                "type": SiteType.department,
                "address": "12 Oxford Rd, Rosebank",
            },
        ],
    },
    {
        "name": "Cape Mobility Clinic",
        "type": PracticeType.private_practice,
        "address": "45 Kloof St, Cape Town",
        "sites": [
            {
                "name": "Cape Mobility Clinic",
                "type": SiteType.location,
                "address": "45 Kloof St, Cape Town",
            },
            {
                "name": "Bellville Satellite Rooms",
                "type": SiteType.department,
                "address": "8 Voortrekker Rd, Bellville",
            },
        ],
    },
    {
        "name": "Sunrise Prosthetics & Orthotics",
        "type": PracticeType.private_practice,
        "address": "21 Marine Dr, Durban",
        "sites": [
            {
                "name": "Sunrise Durban Rooms",
                "type": SiteType.location,
                "address": "21 Marine Dr, Durban",
            },
        ],
    },
]

# (email, role, practice name or None, site name or None)
# Reserved TLDs (.local, .test, .example) fail email validation, so the
# fixture uses plausible domains.
USERS = [
    ("platform.admin@limbitless.co.za", UserRole.platform_administrator, None, None),
    (
        "admin@northgate-rehab.co.za",
        UserRole.practice_administrator,
        "Northgate Rehabilitation Network",
        None,
    ),
    (
        "clinician@northgate-rehab.co.za",
        UserRole.clinician,
        "Northgate Rehabilitation Network",
        "Northgate Main Hospital",
    ),
    (
        "prosthetist@northgate-rehab.co.za",
        UserRole.prosthetist,
        "Northgate Rehabilitation Network",
        "Rosebank Gait Lab",
    ),
    (
        "admin@capemobility.co.za",
        UserRole.practice_administrator,
        "Cape Mobility Clinic",
        None,
    ),
    (
        "clinician@capemobility.co.za",
        UserRole.clinician,
        "Cape Mobility Clinic",
        "Cape Mobility Clinic",
    ),
    ("patient@limbitless.co.za", UserRole.patient, None, None),
    # Practice-bound patient logins, linked to a seeded record in
    # seed_clinical so the self-service portal has something to show.
    (
        "thabo.molefe@patient.limbitless.co.za",
        UserRole.patient,
        "Northgate Rehabilitation Network",
        "Northgate Main Hospital",
    ),
    (
        "refilwe.adams@patient.limbitless.co.za",
        UserRole.patient,
        "Northgate Rehabilitation Network",
        "Northgate Main Hospital",
    ),
    ("reviewer@medscheme.co.za", UserRole.medical_aid_reviewer, None, None),
    # Extra Northgate staff so the practice-admin user list has enough
    # rows to filter and page.
    (
        "nomvula.clinician@northgate-rehab.co.za",
        UserRole.clinician,
        "Northgate Rehabilitation Network",
        "Northgate Main Hospital",
    ),
    (
        "thandi.clinician@northgate-rehab.co.za",
        UserRole.clinician,
        "Northgate Rehabilitation Network",
        "Rosebank Gait Lab",
    ),
    (
        "pieter.prosthetist@northgate-rehab.co.za",
        UserRole.prosthetist,
        "Northgate Rehabilitation Network",
        "Rosebank Gait Lab",
    ),
    (
        "former.clinician@northgate-rehab.co.za",
        UserRole.clinician,
        "Northgate Rehabilitation Network",
        "Northgate Main Hospital",
    ),
    (
        "admin@sunrise-prosthetics.co.za",
        UserRole.practice_administrator,
        "Sunrise Prosthetics & Orthotics",
        None,
    ),
    (
        "clinician@sunrise-prosthetics.co.za",
        UserRole.clinician,
        "Sunrise Prosthetics & Orthotics",
        "Sunrise Durban Rooms",
    ),
]

# Seed users created deactivated, for testing the "Inactive" filter.
INACTIVE_EMAILS = frozenset({"former.clinician@northgate-rehab.co.za"})

# Which scheme a medical-aid reviewer represents (Section 5.12) - gives
# them automatic, cross-practice access to any patient whose active
# MedicalAidMembership.scheme_name matches, alongside the manual
# review_by ReviewGrant a treating practice can still hand out.
REVIEWER_SCHEMES = {"reviewer@medscheme.co.za": "Discovery Health"}


def _get_or_create_practice(db: Session, spec: dict) -> tuple[Practice, bool]:
    practice = db.query(Practice).filter_by(name=spec["name"]).one_or_none()
    if practice is not None:
        return practice, False
    practice = Practice(
        name=spec["name"], type=spec["type"], address=spec["address"]
    )
    db.add(practice)
    db.flush()
    return practice, True


def _get_or_create_site(
    db: Session, practice: Practice, spec: dict
) -> tuple[Site, bool]:
    site = (
        db.query(Site)
        .filter_by(practice_id=practice.id, name=spec["name"])
        .one_or_none()
    )
    if site is not None:
        return site, False
    site = Site(
        name=spec["name"],
        type=spec["type"],
        address=spec["address"],
        practice_id=practice.id,
    )
    db.add(site)
    db.flush()
    return site, True


def _get_or_create_user(
    db: Session,
    email: str,
    role: UserRole,
    practice: Practice | None,
    site: Site | None,
) -> tuple[User, bool]:
    user = crud.get_user_by_email(db, email)
    if user is not None:
        return user, False
    user = User(
        email=email,
        hashed_password=security.hash_password(DEV_PASSWORD),
        role=role,
        practice_id=practice.id if practice else None,
        site_id=site.id if site else None,
        is_active=email not in INACTIVE_EMAILS,
    )
    db.add(user)
    db.flush()
    return user, True


def reset(db: Session) -> None:
    """Delete every row the seed owns, in FK-safe order."""
    seed_clinical.reset_clinical(db)
    for model in (
        Notification,
        AuditLogEntry,
        AvailabilitySlot,
        AccountLinkRequest,
        User,
        Site,
        Practice,
    ):
        db.query(model).delete()
    db.commit()


def seed(db: Session) -> None:
    practices: dict[str, Practice] = {}
    sites: dict[tuple[str, str], Site] = {}
    users_by_email: dict[str, User] = {}
    added = {"practices": 0, "sites": 0, "users": 0}

    for pspec in PRACTICES:
        practice, is_new = _get_or_create_practice(db, pspec)
        practices[practice.name] = practice
        added["practices"] += is_new
        for sspec in pspec["sites"]:
            site, is_new = _get_or_create_site(db, practice, sspec)
            sites[(practice.name, site.name)] = site
            added["sites"] += is_new

    for email, role, practice_name, site_name in USERS:
        practice = practices.get(practice_name) if practice_name else None
        site = sites.get((practice_name, site_name)) if site_name else None
        user, is_new = _get_or_create_user(db, email, role, practice, site)
        users_by_email[email] = user
        added["users"] += is_new

    for email, scheme_name in REVIEWER_SCHEMES.items():
        user = users_by_email.get(email)
        if user is not None and user.scheme_name != scheme_name:
            user.scheme_name = scheme_name

    db.commit()

    clinical = seed_clinical.seed_clinical(db, practices, sites, users_by_email)
    slots_added = seed_clinical.seed_availability(db, users_by_email)
    appointments_added = seed_clinical.seed_appointments(
        db, practices, users_by_email
    )
    notifications_added = seed_clinical.seed_notifications(db, users_by_email)
    _print_summary(
        added, clinical, slots_added, appointments_added, notifications_added
    )


def _print_summary(
    added: dict[str, int],
    clinical: dict[str, int],
    slots_added: int,
    appointments_added: int,
    notifications_added: int,
) -> None:
    print(
        f"Seed complete: +{added['practices']} practices, "
        f"+{added['sites']} sites, +{added['users']} users "
        f"(existing rows left as-is)."
    )
    print(
        f"Clinical sample: +{clinical['patients']} patients, "
        f"+{clinical['involvements']} involvements, +{clinical['devices']} devices, "
        f"+{clinical['milestones']} milestones, +{clinical['proms']} PROMs, "
        f"+{clinical['notes']} notes, +{clinical['audit']} audit entries, "
        f"+{clinical['medical_aid_memberships']} medical-aid memberships."
    )
    print(
        f"+{slots_added} availability slots, +{appointments_added} appointments, "
        f"+{notifications_added} notifications."
    )
    print(f"\nAll seeded users share the password: {DEV_PASSWORD}")
    for email, role, practice_name, _ in USERS:
        print(f"  {email:34} {role.value:23} {practice_name or '(cross-practice)'}")


def _require_schema() -> None:
    if not inspect(engine).has_table("users"):
        sys.exit("No 'users' table - run 'alembic upgrade head' first.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Seed the local dev database.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete the seeded tables before seeding",
    )
    args = parser.parse_args(argv)

    _require_schema()
    db = SessionLocal()
    try:
        if args.reset:
            reset(db)
            print("Reset: cleared audit_log_entries, users, sites, practices.")
        seed(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
