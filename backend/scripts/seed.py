"""Deterministic demo data.

What is deterministic
---------------------
Identifiers, teams, users, per-team roles, and the distribution of work-item
types, priorities, statuses and assignees are all derived from fixed seeds. Two
runs against the same empty database produce the same dataset. Timestamps are
relative to the moment the script runs, so the dashboard looks current rather
than historical.

Idempotency
-----------
Every row has a deterministic primary key and is inserted with
``ON CONFLICT DO NOTHING``. Running the script twice creates nothing the second
time, and - importantly - generates no duplicate activity events, because events
are only built for items that were actually created.

Seeding is additive, not converging: editing the generator does not rewrite rows
that already exist. To regenerate from scratch, rebuild the schema (see
``docs/OPERATIONS.md``) and run the script again.

Usage
-----
    uv run python -m scripts.seed                 # 3000 work items
    uv run python -m scripts.seed --items 200     # smaller dataset
"""

from __future__ import annotations

import argparse
import asyncio
import random
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import get_settings
from app.db import Database, create_database
from app.models import ActivityEvent, Comment, Membership, Team, User, WorkItem
from app.models.enums import ActivityType, Priority, Role, Status, WorkItemType
from app.security.passwords import hash_password
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

# --------------------------------------------------------------------------- #
# Deterministic identity
# --------------------------------------------------------------------------- #

# A fixed namespace makes every generated identifier stable across runs and
# machines. Changing it would orphan previously seeded rows.
NAMESPACE = uuid.UUID("6f1d0f6e-6f5f-4a5e-9f5a-1c2b3d4e5f60")


def stable_id(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"{kind}:{key}")


# --------------------------------------------------------------------------- #
# Demo fixtures
# --------------------------------------------------------------------------- #

# Documented development-only password. Every demo account uses it so a reviewer
# can log in as any role. It is a placeholder, not a secret.
DEMO_PASSWORD = "Newtonite!2026"

TEAMS: tuple[tuple[str, str, str], ...] = (
    ("platform", "Platform Engineering", "Production incidents and platform reliability"),
    ("support", "Customer Support", "Customer-reported issues and escalations"),
    ("finance", "Finance Operations", "Payment investigations and reconciliations"),
    ("compliance", "Compliance", "Regulatory and policy requests"),
)

# (email, display name, [(team key, role), ...])
#
# Two deliberate properties:
#   * more than one team has a lead, so approval authority is team-scoped;
#   * sam.multi holds a *different role in each of two teams*, which is the case
#     the role model has to get right.
USERS: tuple[tuple[str, str, tuple[tuple[str, Role], ...]], ...] = (
    (
        "ada.admin@newtonite.dev",
        "Ada Okafor",
        (("platform", Role.ADMIN), ("support", Role.ADMIN)),
    ),
    ("lena.lead@newtonite.dev", "Lena Fischer", (("platform", Role.LEAD),)),
    ("marcus.lead@newtonite.dev", "Marcus Bell", (("support", Role.LEAD),)),
    ("priya.lead@newtonite.dev", "Priya Nair", (("finance", Role.LEAD),)),
    ("omar.lead@newtonite.dev", "Omar Haddad", (("compliance", Role.LEAD),)),
    ("nina.member@newtonite.dev", "Nina Petrova", (("platform", Role.MEMBER),)),
    ("noah.member@newtonite.dev", "Noah Kim", (("platform", Role.MEMBER),)),
    ("tara.member@newtonite.dev", "Tara Singh", (("support", Role.MEMBER),)),
    ("ivan.member@newtonite.dev", "Ivan Cruz", (("finance", Role.MEMBER),)),
    ("grace.member@newtonite.dev", "Grace Mwangi", (("compliance", Role.MEMBER),)),
    (
        "sam.multi@newtonite.dev",
        "Sam Alvarez",
        (("platform", Role.MEMBER), ("finance", Role.VIEWER)),
    ),
    ("vic.viewer@newtonite.dev", "Vic Achebe", (("platform", Role.VIEWER),)),
)

# A viewer is read-only, so seeded items are never assigned to one. The database
# would allow it (a viewer is still a team member); the seed simply reflects how
# the workflow is meant to be used.
_ASSIGNABLE_ROLES = (Role.MEMBER.value, Role.LEAD.value, Role.ADMIN.value)

TITLE_TEMPLATES: dict[WorkItemType, tuple[str, ...]] = {
    WorkItemType.INCIDENT: (
        "Elevated error rate on {service}",
        "Intermittent timeouts in {service}",
        "{service} latency above threshold",
        "Failed deploy for {service}",
    ),
    WorkItemType.CUSTOMER_ISSUE: (
        "Customer reports incorrect totals in {service}",
        "Escalation: {account} cannot complete checkout",
        "{account} requests a data export",
        "Onboarding blocked for {account}",
    ),
    WorkItemType.PAYMENT: (
        "Payment {reference} not settled",
        "Duplicate charge for {account}",
        "Refund mismatch on {reference}",
        "Gateway reconciliation gap in {service}",
    ),
    WorkItemType.COMPLIANCE: (
        "Access review required for {service}",
        "Retention policy exception on {account}",
        "Quarterly attestation for {service}",
        "Vendor risk assessment for {account}",
    ),
    WorkItemType.TASK: (
        "Update runbook for {service}",
        "Rotate credentials for {service}",
        "Backfill missing records in {account}",
        "Review alert thresholds for {service}",
    ),
}

_SERVICES = ("billing-api", "checkout", "ledger", "notifications", "search", "identity")
_ACCOUNTS = ("Northwind", "Acme", "Globex", "Initech", "Umbrella", "Soylent")

DESCRIPTION_TEMPLATES: tuple[str, ...] = (
    "Reported by operations. Impact and scope still being confirmed.",
    "Detected by monitoring and raised for investigation.",
    "Raised during the daily review. Needs an owner and a decision.",
    "Follow-up from a previous item. See the related history for context.",
)

COMMENT_TEMPLATES: tuple[str, ...] = (
    "Picked this up. Investigating the most likely cause first.",
    "Reproduced in staging. Narrowing down the failing component.",
    "Waiting on an external team before this can move forward.",
    "Mitigation applied. Monitoring to confirm the fix holds.",
    "Confirmed with the requester that the outcome is acceptable.",
    "Escalating: this is affecting more than the original reporter.",
)


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #


def _weighted_choice(rng: random.Random, options: Sequence[tuple[Any, int]]) -> Any:
    values = [value for value, _ in options]
    weights = [weight for _, weight in options]
    return rng.choices(values, weights=weights, k=1)[0]


def _item_plan(rng: random.Random) -> dict[str, Any]:
    """Choose the state distribution for one work item.

    Status and assignment are chosen together so the seed never produces an item
    the workflow would not allow - for example an unassigned item awaiting
    approval.
    """
    status = _weighted_choice(
        rng,
        (
            (Status.OPEN, 35),
            (Status.IN_PROGRESS, 25),
            (Status.AWAITING_APPROVAL, 15),
            (Status.RESOLVED, 15),
            (Status.CLOSED, 10),
        ),
    )
    priority = _weighted_choice(
        rng,
        ((Priority.LOW, 30), (Priority.MEDIUM, 40), (Priority.HIGH, 20), (Priority.URGENT, 10)),
    )
    # Anything past "open" has an owner. Open items are unassigned often enough
    # to make the "unassigned urgent" queue meaningful.
    assigned = rng.random() < 0.35 if status is Status.OPEN else True

    return {"status": status, "priority": priority, "assigned": assigned}


def _status_chain(status: Status) -> tuple[Status, ...]:
    """The statuses an item passes through on its way to `status`."""
    chains: dict[Status, tuple[Status, ...]] = {
        Status.OPEN: (),
        Status.IN_PROGRESS: (Status.IN_PROGRESS,),
        Status.AWAITING_APPROVAL: (Status.IN_PROGRESS, Status.AWAITING_APPROVAL),
        Status.RESOLVED: (Status.IN_PROGRESS, Status.AWAITING_APPROVAL, Status.RESOLVED),
        Status.CLOSED: (
            Status.IN_PROGRESS,
            Status.AWAITING_APPROVAL,
            Status.RESOLVED,
            Status.CLOSED,
        ),
    }
    return chains[status]


def _team_key_of(team_id: uuid.UUID) -> str:
    for key, _, _ in TEAMS:
        if stable_id("team", key) == team_id:
            return key
    raise KeyError(f"unknown seeded team id {team_id}")


def _event_for_transition(previous: Status, new: Status) -> ActivityType:
    if new is Status.RESOLVED:
        return ActivityType.APPROVED
    if previous is Status.AWAITING_APPROVAL:
        return ActivityType.REJECTED
    return ActivityType.STATUS_CHANGED


def _event(
    *,
    work_item_id: uuid.UUID,
    team_id: uuid.UUID,
    actor_id: uuid.UUID,
    event_type: ActivityType,
    created_at: datetime,
    from_value: str | None = None,
    to_value: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one activity row with every column present.

    Bulk inserts are compiled once against the first row's keys, so all rows must
    carry the same set of columns; optional values are passed explicitly as None
    rather than being omitted.
    """
    return {
        "work_item_id": work_item_id,
        "team_id": team_id,
        "actor_id": actor_id,
        "event_type": event_type.value,
        "from_value": from_value,
        "to_value": to_value,
        "meta": meta if meta is not None else {},
        "created_at": created_at,
    }


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #


async def seed(
    session: AsyncSession,
    *,
    item_count: int,
    password: str = DEMO_PASSWORD,
) -> dict[str, int]:
    """Insert demo data. Returns the number of rows created in this run."""
    created = {
        "teams": 0,
        "users": 0,
        "memberships": 0,
        "work_items": 0,
        "comments": 0,
        "activity_events": 0,
    }

    now = datetime.now(UTC)
    password_hash = hash_password(password)

    created["teams"] = await _insert_if_absent(
        session,
        Team,
        [
            {
                "id": stable_id("team", key),
                "key": key,
                "name": name,
                "description": description,
            }
            for key, name, description in TEAMS
        ],
    )

    created["users"] = await _insert_if_absent(
        session,
        User,
        [
            {
                "id": stable_id("user", email),
                "email": email,
                "display_name": display_name,
                "password_hash": password_hash,
                "is_active": True,
            }
            for email, display_name, _ in USERS
        ],
    )

    created["memberships"] = await _insert_if_absent(
        session,
        Membership,
        [
            {
                "id": stable_id("membership", f"{email}:{team_key}"),
                "user_id": stable_id("user", email),
                "team_id": stable_id("team", team_key),
                "role": role.value,
            }
            for email, _, memberships in USERS
            for team_key, role in memberships
        ],
    )

    members_by_team: dict[str, list[uuid.UUID]] = {}
    for email, _, memberships in USERS:
        for team_key, role in memberships:
            if role.value in _ASSIGNABLE_ROLES:
                members_by_team.setdefault(team_key, []).append(stable_id("user", email))

    # ---- work items -------------------------------------------------------
    planned: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for index in range(item_count):
        # A per-item generator keeps every item stable when `--items` changes.
        rng = random.Random(f"newtonite-seed-item-{index}")
        team_key, _, _ = TEAMS[index % len(TEAMS)]
        plan = _item_plan(rng)

        created_by = rng.choice(members_by_team[team_key])
        assignee = rng.choice(members_by_team[team_key]) if plan["assigned"] else None

        created_at = now - timedelta(days=rng.uniform(0, 120), hours=rng.uniform(0, 24))
        updated_at = min(created_at + timedelta(hours=rng.uniform(0.5, 72)), now)
        due_at = created_at + timedelta(days=rng.uniform(-5, 30)) if rng.random() < 0.6 else None

        item_type = rng.choice(list(WorkItemType))
        title = rng.choice(TITLE_TEMPLATES[item_type]).format(
            service=rng.choice(_SERVICES),
            account=rng.choice(_ACCOUNTS),
            reference=f"REF-{rng.randint(10000, 99999)}",
        )

        row = {
            "id": stable_id("work_item", str(index)),
            "team_id": stable_id("team", team_key),
            "title": title,
            "description": rng.choice(DESCRIPTION_TEMPLATES),
            "type": item_type.value,
            "priority": plan["priority"].value,
            "status": plan["status"].value,
            "assignee_id": assignee,
            "created_by": created_by,
            "due_at": due_at,
            "version": 2 + len(_status_chain(plan["status"])) + int(assignee is not None),
            "created_at": created_at,
            "updated_at": updated_at,
        }
        planned.append((row, plan))

    fresh_items = await _insert_new_work_items(session, planned)
    created["work_items"] = len(fresh_items)

    # ---- history and comments, for newly created items only ---------------
    events: list[dict[str, Any]] = []
    comments: list[dict[str, Any]] = []

    for row, plan in fresh_items:
        team_key = _team_key_of(row["team_id"])
        team_members = members_by_team[team_key]
        rng = random.Random(f"newtonite-seed-history-{row['id']}")

        cursor = row["created_at"]
        actor = row["created_by"]
        events.append(
            _event(
                work_item_id=row["id"],
                team_id=row["team_id"],
                actor_id=actor,
                event_type=ActivityType.CREATED,
                to_value=Status.OPEN.value,
                created_at=cursor,
            )
        )

        if row["assignee_id"] is not None:
            cursor = min(cursor + timedelta(minutes=rng.randint(5, 240)), now)
            self_claimed = row["assignee_id"] == actor
            events.append(
                _event(
                    work_item_id=row["id"],
                    team_id=row["team_id"],
                    actor_id=row["assignee_id"] if self_claimed else actor,
                    event_type=(ActivityType.CLAIMED if self_claimed else ActivityType.ASSIGNED),
                    to_value=str(row["assignee_id"]),
                    created_at=cursor,
                )
            )
            actor = row["assignee_id"]

        previous = Status.OPEN
        for step in _status_chain(plan["status"]):
            cursor = min(cursor + timedelta(minutes=rng.randint(30, 1440)), now)
            # Approval is a lead or admin action, so attribute approval to
            # somebody other than the assignee where the team allows it.
            approver = actor
            if step is Status.RESOLVED:
                approver = next((user_id for user_id in team_members if user_id != actor), actor)
            events.append(
                _event(
                    work_item_id=row["id"],
                    team_id=row["team_id"],
                    actor_id=approver,
                    event_type=_event_for_transition(previous, step),
                    from_value=previous.value,
                    to_value=step.value,
                    created_at=cursor,
                )
            )
            previous = step
            actor = approver

        for comment_index in range(rng.randint(1, 3) if rng.random() < 0.3 else 0):
            cursor = min(cursor + timedelta(minutes=rng.randint(20, 600)), now)
            comment_id = stable_id("comment", f"{row['id']}:{comment_index}")
            author = rng.choice(team_members)
            comments.append(
                {
                    "id": comment_id,
                    "work_item_id": row["id"],
                    "author_id": author,
                    "body": rng.choice(COMMENT_TEMPLATES),
                    "created_at": cursor,
                }
            )
            events.append(
                _event(
                    work_item_id=row["id"],
                    team_id=row["team_id"],
                    actor_id=author,
                    event_type=ActivityType.COMMENTED,
                    meta={"comment_id": str(comment_id)},
                    created_at=cursor,
                )
            )

    created["comments"] = await _insert_if_absent(session, Comment, comments)
    created["activity_events"] = await _insert_activity(session, events)

    await session.commit()
    return created


# --------------------------------------------------------------------------- #
# Insert helpers
# --------------------------------------------------------------------------- #


async def _insert_if_absent(
    session: AsyncSession,
    model: Any,
    rows: Sequence[dict[str, Any]],
    *,
    chunk_size: int = 500,
) -> int:
    """Insert `rows`, skipping any that conflict with existing rows.

    No conflict target is named: "insert unless something already claims this
    row" is the intent, and every seed row has a deterministic primary key plus
    natural keys (team key, user email, membership pair).
    """
    created = 0
    for start in range(0, len(rows), chunk_size):
        statement = (
            pg_insert(model.__table__).on_conflict_do_nothing().returning(model.__table__.c.id)
        )
        result = await session.execute(statement, rows[start : start + chunk_size])
        created += len(result.scalars().all())
    return created


async def _insert_new_work_items(
    session: AsyncSession,
    planned: Sequence[tuple[dict[str, Any], dict[str, Any]]],
    *,
    chunk_size: int = 500,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Insert only items whose deterministic id is absent, and return those.

    Returning only genuinely new rows is what makes a second seed run produce no
    duplicate history.
    """
    inserted: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for start in range(0, len(planned), chunk_size):
        chunk = planned[start : start + chunk_size]
        existing = set(
            (
                await session.execute(
                    select(WorkItem.id).where(WorkItem.id.in_([row["id"] for row, _ in chunk]))
                )
            )
            .scalars()
            .all()
        )
        fresh = [(row, plan) for row, plan in chunk if row["id"] not in existing]
        if not fresh:
            continue
        statement = pg_insert(WorkItem).on_conflict_do_nothing(index_elements=["id"])
        await session.execute(statement, [row for row, _ in fresh])
        inserted.extend(fresh)
    return inserted


async def _insert_activity(
    session: AsyncSession, rows: Sequence[dict[str, Any]], *, chunk_size: int = 1000
) -> int:
    """Insert activity events.

    Events are only built for items created in this run, so a plain insert is
    correct here: there is no natural uniqueness to conflict on, and the
    append-only trigger forbids updating or deleting history.
    """
    for start in range(0, len(rows), chunk_size):
        await session.execute(pg_insert(ActivityEvent), rows[start : start + chunk_size])
    return len(rows)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


async def run(item_count: int, database_url: str | None = None) -> dict[str, int]:
    database: Database = create_database(database_url or get_settings().database_url)
    try:
        async with database.session_factory() as session:
            return await seed(session, item_count=item_count)
    finally:
        await database.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed deterministic demo data.")
    parser.add_argument(
        "--items",
        type=int,
        default=3000,
        help="number of work items to create (default: 3000)",
    )
    args = parser.parse_args(argv)

    if args.items < 0:
        parser.error("--items must not be negative")

    counts = asyncio.run(run(args.items))

    print("Seed complete. Rows created in this run:")
    for table, count in counts.items():
        print(f"  {table:<16} {count:>6}")
    if counts["work_items"] < args.items:
        print(
            f"\n{args.items - counts['work_items']} work item(s) already existed and were left "
            "untouched (seeding is additive, not converging)."
        )

    print(f"\nDemo accounts - development only. Password for all: {DEMO_PASSWORD}")
    for email, display_name, memberships in USERS:
        roles = ", ".join(f"{team_key}:{role.value}" for team_key, role in memberships)
        print(f"  {email:<30} {display_name:<16} {roles}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
