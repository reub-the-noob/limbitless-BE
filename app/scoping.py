"""Practice / site query scoping.

Tenant isolation (requirements Section 6) is enforced structurally rather
than re-implemented per endpoint: clinical models carry ``practice_id`` /
``site_id`` via the mixins in :mod:`app.models`, and every query for those
rows is passed through the helpers here.

R-05 adds the auth dependency that builds a :class:`RequestScope` from the
caller's token. Until then these take an explicit id so they can be unit
tested and composed into the CRUD layer as it lands.
"""

from dataclasses import dataclass

from sqlalchemy import Select


@dataclass(frozen=True)
class RequestScope:
    """The tenant boundary a request is confined to."""

    practice_id: int
    site_id: int | None = None


def scope_to_practice(stmt: Select, model: type, practice_id: int) -> Select:
    """Restrict ``stmt`` to rows of ``model`` belonging to one practice."""
    return stmt.where(model.practice_id == practice_id)


def scope_to_site(stmt: Select, model: type, site_id: int) -> Select:
    """Restrict ``stmt`` to rows of ``model`` belonging to one site."""
    return stmt.where(model.site_id == site_id)


def apply_scope(stmt: Select, model: type, scope: RequestScope) -> Select:
    """Apply a :class:`RequestScope`: always by practice, by site when set."""
    stmt = scope_to_practice(stmt, model, scope.practice_id)
    if scope.site_id is not None:
        stmt = scope_to_site(stmt, model, scope.site_id)
    return stmt
