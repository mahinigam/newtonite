"""The deterministic demo dataset.

The seed script is part of the reviewer experience, so it is tested like
production code: it must create a coherent dataset, and running it twice must be
harmless.
"""

from __future__ import annotations

from app.models.enums import Role, Status
from app.security.passwords import verify_password
from scripts.seed import DEMO_PASSWORD, USERS, seed
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ITEM_COUNT = 40


async def test_seed_creates_the_documented_fixtures(db: AsyncSession) -> None:
    counts = await seed(db, item_count=ITEM_COUNT)

    assert counts["teams"] == 4
    assert counts["users"] == len(USERS)
    assert counts["memberships"] == sum(len(memberships) for _, _, memberships in USERS)
    assert counts["work_items"] == ITEM_COUNT
    # History is generated for every item, so there must be at least one event
    # per item; comments are optional.
    assert counts["activity_events"] >= ITEM_COUNT


async def test_seed_is_idempotent(db: AsyncSession) -> None:
    """A second run must create nothing at all.

    This is the property that matters: a reviewer re-running the documented
    command must not end up with duplicate items, and above all not with
    duplicated or contradictory activity history.
    """
    await seed(db, item_count=ITEM_COUNT)
    before = await _counts(db)

    second = await seed(db, item_count=ITEM_COUNT)
    after = await _counts(db)

    assert second == {
        "teams": 0,
        "users": 0,
        "memberships": 0,
        "work_items": 0,
        "comments": 0,
        "activity_events": 0,
    }
    assert after == before


async def test_seed_is_deterministic(db: AsyncSession) -> None:
    await seed(db, item_count=ITEM_COUNT)
    first = await db.execute(text("SELECT id, title, status, priority FROM work_items ORDER BY id"))
    first_rows = [tuple(row) for row in first]

    for table in ("activity_events", "comments", "work_items", "memberships", "users", "teams"):
        await db.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))

    await seed(db, item_count=ITEM_COUNT)
    second = await db.execute(
        text("SELECT id, title, status, priority FROM work_items ORDER BY id")
    )

    assert [tuple(row) for row in second] == first_rows


async def test_seeded_items_never_violate_the_workflow(db: AsyncSession) -> None:
    """Generated data must be a state the workflow would actually allow.

    In particular an item past ``open`` always has an owner, which is the rule the
    transition endpoints will enforce later.
    """
    await seed(db, item_count=ITEM_COUNT)

    unowned = (
        await db.execute(
            text("SELECT count(*) FROM work_items WHERE status <> 'open' AND assignee_id IS NULL")
        )
    ).scalar_one()

    assert unowned == 0


async def test_seeded_assignees_belong_to_the_items_team(db: AsyncSession) -> None:
    """Belt and braces: the database already enforces this, so this checks the
    generator, not PostgreSQL."""
    await seed(db, item_count=ITEM_COUNT)

    orphans = (
        await db.execute(
            text(
                """
                SELECT count(*) FROM work_items w
                LEFT JOIN memberships m
                  ON m.team_id = w.team_id AND m.user_id = w.assignee_id
                WHERE w.assignee_id IS NOT NULL AND m.id IS NULL
                """
            )
        )
    ).scalar_one()

    assert orphans == 0


async def test_seeded_history_is_ordered_and_attributed(db: AsyncSession) -> None:
    await seed(db, item_count=ITEM_COUNT)

    # Every event references an existing actor in the item's own team.
    cross_team_events = (
        await db.execute(
            text(
                """
                SELECT count(*) FROM activity_events e
                LEFT JOIN memberships m
                  ON m.team_id = e.team_id AND m.user_id = e.actor_id
                WHERE m.id IS NULL
                """
            )
        )
    ).scalar_one()
    assert cross_team_events == 0

    # A 'created' event exists for every item, and it is the earliest event.
    missing_created = (
        await db.execute(
            text(
                """
                SELECT count(*) FROM work_items w
                WHERE NOT EXISTS (
                  SELECT 1 FROM activity_events e
                  WHERE e.work_item_id = w.id AND e.event_type = 'created'
                )
                """
            )
        )
    ).scalar_one()
    assert missing_created == 0

    out_of_order = (
        await db.execute(
            text(
                """
                SELECT count(*) FROM (
                  SELECT work_item_id
                  FROM activity_events
                  GROUP BY work_item_id
                  HAVING min(created_at) FILTER (WHERE event_type = 'created') <> min(created_at)
                ) AS offenders
                """
            )
        )
    ).scalar_one()
    assert out_of_order == 0


async def test_awaiting_approval_items_have_an_approval_pending_history(
    db: AsyncSession,
) -> None:
    await seed(db, item_count=ITEM_COUNT)

    # An item awaiting approval must have passed through in_progress.
    without_progress = (
        await db.execute(
            text(
                """
                SELECT count(*) FROM work_items w
                WHERE w.status = 'awaiting_approval'
                  AND NOT EXISTS (
                    SELECT 1 FROM activity_events e
                    WHERE e.work_item_id = w.id AND e.to_value = 'in_progress'
                  )
                """
            )
        )
    ).scalar_one()

    assert without_progress == 0
    assert Status.AWAITING_APPROVAL.value == "awaiting_approval"


async def test_demo_accounts_share_a_working_password(db: AsyncSession) -> None:
    await seed(db, item_count=1)

    result = await db.execute(text("SELECT password_hash FROM users"))
    hashes = [row.password_hash for row in result]

    assert hashes
    assert all(h.startswith("$argon2id$") for h in hashes)
    assert all(verify_password(DEMO_PASSWORD, h) for h in hashes)
    assert not any(verify_password("wrong-password", h) for h in hashes)


async def test_one_account_holds_different_roles_in_different_teams(
    db: AsyncSession,
) -> None:
    """The role model must be per team, not global."""
    await seed(db, item_count=1)

    roles = (
        await db.execute(
            text(
                """
                SELECT t.key, m.role
                FROM memberships m
                JOIN teams t ON t.id = m.team_id
                JOIN users u ON u.id = m.user_id
                WHERE u.email = 'sam.multi@newtonite.dev'
                ORDER BY t.key
                """
            )
        )
    ).all()

    assert [(row.key, row.role) for row in roles] == [
        ("finance", Role.VIEWER.value),
        ("platform", Role.MEMBER.value),
    ]


async def _counts(db: AsyncSession) -> dict[str, int]:
    tables = ("teams", "users", "memberships", "work_items", "comments", "activity_events")
    return {
        table: (await db.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
        for table in tables
    }
