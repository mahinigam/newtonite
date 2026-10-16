"""The role matrix.

This module is the single source of truth for *who may do what inside a team*.
Route handlers ask it questions; they never compare role strings themselves, and
they never trust a role supplied by a client.

Roles are per team. The same person can be a `member` in one team and a `viewer`
in another, which is why every check takes the role for the team in question
rather than a global user role.

| Role | Can do |
| --- | --- |
| `viewer` | Read the team's items, comments and activity |
| `member` | Everything above, plus create items, comment, claim, edit detail, move work on |
| `lead` | Everything above, plus assign to others, approve or reject, and close resolved items |
| `admin` | Everything above, plus manage team members and roles |

Reading is not restricted by role *within* a team, but membership of the team is
mandatory. A non-member is answered with 404, not 403, so the API does not confirm
the existence of another team's item.
"""

from __future__ import annotations

from enum import StrEnum

from app.errors import Forbidden
from app.models.enums import Role


class Permission(StrEnum):
    VIEW_TEAM = "view_team"
    CREATE_ITEM = "create_item"
    COMMENT = "comment"
    CLAIM_ITEM = "claim_item"
    EDIT_ITEM = "edit_item"
    TRANSITION_WORK = "transition_work"
    ASSIGN_OTHERS = "assign_others"
    APPROVE = "approve"
    CLOSE_ITEM = "close_item"
    MANAGE_MEMBERS = "manage_members"


_VIEWER: frozenset[Permission] = frozenset({Permission.VIEW_TEAM})

_MEMBER: frozenset[Permission] = _VIEWER | {
    Permission.CREATE_ITEM,
    Permission.COMMENT,
    Permission.CLAIM_ITEM,
    Permission.EDIT_ITEM,
    Permission.TRANSITION_WORK,
}

_LEAD: frozenset[Permission] = _MEMBER | {
    Permission.ASSIGN_OTHERS,
    Permission.APPROVE,
    Permission.CLOSE_ITEM,
}

_ADMIN: frozenset[Permission] = _LEAD | {Permission.MANAGE_MEMBERS}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: _VIEWER,
    Role.MEMBER: _MEMBER,
    Role.LEAD: _LEAD,
    Role.ADMIN: _ADMIN,
}

# Ascending authority, for "at least this role" checks and for the last-admin rule.
ROLE_RANK: dict[Role, int] = {
    Role.VIEWER: 0,
    Role.MEMBER: 1,
    Role.LEAD: 2,
    Role.ADMIN: 3,
}


def parse_role(value: str) -> Role:
    """Convert a stored role string to a `Role`, refusing anything unknown.

    The database has a CHECK constraint on this column, so an unknown value means
    the row was written outside the application.
    """
    try:
        return Role(value)
    except ValueError as exc:  # pragma: no cover - prevented by a CHECK constraint
        raise Forbidden("Unknown role on this membership.") from exc


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]


def require_permission(role: Role, permission: Permission) -> None:
    """Raise `Forbidden` unless `role` grants `permission`.

    The error deliberately says which action was refused but not what role the
    caller would need, so the message cannot be used to map out the matrix.
    """
    if not has_permission(role, permission):
        raise Forbidden(f"Your role in this team does not allow '{permission.value}'.")


def at_least(role: Role, minimum: Role) -> bool:
    return ROLE_RANK[role] >= ROLE_RANK[minimum]


def require_at_least(role: Role, minimum: Role) -> None:
    if not at_least(role, minimum):
        raise Forbidden()


__all__ = [
    "ROLE_PERMISSIONS",
    "ROLE_RANK",
    "Permission",
    "at_least",
    "has_permission",
    "parse_role",
    "require_at_least",
    "require_permission",
]
