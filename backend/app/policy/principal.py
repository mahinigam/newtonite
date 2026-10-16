"""The authenticated caller.

A `Principal` is what every protected route depends on: the user, the session they
are using, and **their role in each team they belong to**.

Holding the roles for the whole request means a team boundary check is a
dictionary lookup rather than another query, and more importantly it means the
role always comes from the database. A `team_id` or `role` in a request body or
query string is never consulted.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field

from app.errors import NotFound
from app.models.enums import Role
from app.models.user import User
from app.policy.roles import Permission, require_permission


@dataclass(frozen=True, slots=True)
class Principal:
    user: User
    session_id: uuid.UUID
    roles: Mapping[uuid.UUID, Role] = field(default_factory=dict)

    def role_in(self, team_id: uuid.UUID) -> Role | None:
        """The caller's role in `team_id`, or None if they are not a member."""
        return self.roles.get(team_id)

    def require_team(self, team_id: uuid.UUID) -> Role:
        """Return the caller's role, or raise `NotFound`.

        A team the caller is not a member of is reported as missing rather than
        forbidden. Saying "403" would confirm that the team exists, which is
        information a non-member has no business having.
        """
        role = self.roles.get(team_id)
        if role is None:
            raise NotFound("Team not found.")
        return role

    def require_permission(self, team_id: uuid.UUID, permission: Permission) -> Role:
        """Return the caller's role, after checking it grants `permission`."""
        role = self.require_team(team_id)
        require_permission(role, permission)
        return role

    @property
    def team_ids(self) -> tuple[uuid.UUID, ...]:
        """Every team the caller belongs to.

        Used to scope queries such as "activity across my teams" so they can never
        reach a team the caller is not in.
        """
        return tuple(self.roles)


def require_item_access(
    principal: Principal, *, team_id: uuid.UUID, permission: Permission
) -> Role:
    """The caller's role for a work item in `team_id`, or `NotFound`.

    Every item-scoped read and write goes through this, so the three cases have one
    answer each, in one place:

    * the item does not exist — reported by the caller, which did the lookup;
    * it exists but the caller is not in its team — `404`, **not** `403`, because a
      non-member has no business learning that another team's item exists;
    * the caller is in the team but their role is insufficient — `403`, and the message
      names the action that was refused.

    Three routes' worth of `if` blocks that could drift is exactly why this is a
    function rather than a paragraph in each service.
    """
    role = principal.role_in(team_id)
    if role is None:
        raise NotFound("Work item not found.")
    require_permission(role, permission)
    return role


__all__ = ["Principal", "require_item_access"]
