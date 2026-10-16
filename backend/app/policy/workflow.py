"""The work-item state machine.

    open ──► in_progress ──► awaiting_approval ──► resolved ──► closed
                  ▲                  │
                  └──────────────────┘
                     rejected

`closed` is terminal. Reopening is deliberately not implemented: it would need a
decision about who may do it, whether the version resets and what history it writes,
and guessing would be worse than leaving it out. See `docs/KNOWN_LIMITATIONS.md`.

Two rules matter, and both are enforced here rather than in a route or in the browser:

* **Legality.** Only the transitions in `TRANSITIONS` may happen. `closed → open`, skipping
  approval, and moving backwards without a rejection are all refused.
* **Permission.** Each transition names the permission it needs, so "who may approve" is a
  property of the state machine rather than a check bolted onto one endpoint.

The client is told which transitions are currently *available* (see
`available_transitions`), but that is a convenience for rendering: every transition is
validated again on the server when it is attempted.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import ActivityType, Role, Status
from app.policy.roles import Permission, has_permission


@dataclass(frozen=True, slots=True)
class TransitionRule:
    """One legal state change."""

    source: Status
    target: Status
    permission: Permission
    event: ActivityType
    #: Work cannot be started or submitted without an owner.
    requires_assignee: bool = False


def _rule(
    source: Status,
    target: Status,
    permission: Permission,
    event: ActivityType,
    *,
    requires_assignee: bool = False,
) -> tuple[tuple[Status, Status], TransitionRule]:
    return (source, target), TransitionRule(
        source=source,
        target=target,
        permission=permission,
        event=event,
        requires_assignee=requires_assignee,
    )


TRANSITIONS: dict[tuple[Status, Status], TransitionRule] = dict(
    [
        # Picking up work is a member action, and it needs an owner: the person
        # doing it claims the item first, so the assignment is explicit and
        # recorded rather than implied.
        _rule(
            Status.OPEN,
            Status.IN_PROGRESS,
            Permission.TRANSITION_WORK,
            ActivityType.STATUS_CHANGED,
            requires_assignee=True,
        ),
        # Submitting finished work for review.
        _rule(
            Status.IN_PROGRESS,
            Status.AWAITING_APPROVAL,
            Permission.TRANSITION_WORK,
            ActivityType.STATUS_CHANGED,
        ),
        # Approval is a lead action: it is the control the workflow exists for.
        _rule(
            Status.AWAITING_APPROVAL,
            Status.RESOLVED,
            Permission.APPROVE,
            ActivityType.APPROVED,
        ),
        # Rejection returns the item to the person doing the work, with a reason.
        _rule(
            Status.AWAITING_APPROVAL,
            Status.IN_PROGRESS,
            Permission.APPROVE,
            ActivityType.REJECTED,
        ),
        # Closing is a lead action and is a separate step from resolving, so
        # "done" and "confirmed and filed" stay distinguishable.
        _rule(
            Status.RESOLVED,
            Status.CLOSED,
            Permission.CLOSE_ITEM,
            ActivityType.STATUS_CHANGED,
        ),
    ]
)

TERMINAL_STATUSES: frozenset[Status] = frozenset({Status.CLOSED})


def is_legal(source: Status, target: Status) -> bool:
    return (source, target) in TRANSITIONS


def rule_for(source: Status, target: Status) -> TransitionRule | None:
    return TRANSITIONS.get((source, target))


def available_transitions(
    *,
    status: Status,
    role: Role,
    has_assignee: bool,
) -> tuple[Status, ...]:
    """Every transition this caller could perform right now.

    Returned to clients so the interface does not have to guess at the workflow,
    and so a disabled button matches what the server would actually allow. It is
    advisory: attempting a transition re-checks legality and permission.
    """
    result: list[Status] = []
    for (source, target), rule in TRANSITIONS.items():
        if source != status or not has_permission(role, rule.permission):
            continue
        if rule.requires_assignee and not has_assignee:
            continue
        result.append(target)
    return tuple(result)


__all__ = [
    "TERMINAL_STATUSES",
    "TRANSITIONS",
    "TransitionRule",
    "available_transitions",
    "is_legal",
    "rule_for",
]
