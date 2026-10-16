"""Database-level invariants.

These tests bypass the application entirely and talk to PostgreSQL directly.
That is the point: they prove the guarantees hold even if application code is
wrong, or a human connects with `psql` and tries the same thing.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support import (
    TEAM_ALPHA,
    USER_ALPHA_MEMBER,
    USER_BETA_MEMBER,
    seed_two_teams,
)


async def _seed(db: AsyncSession) -> None:
    await seed_two_teams(db)


_INSERT_ITEM = text(
    """
    INSERT INTO work_items (team_id, title, type, priority, assignee_id, created_by)
    VALUES (:team, :title, 'task', 'medium', :assignee, :created_by)
    """
)


async def test_assignee_from_the_same_team_is_accepted(db: AsyncSession) -> None:
    await _seed(db)

    await db.execute(
        _INSERT_ITEM,
        {
            "team": TEAM_ALPHA,
            "title": "A valid title",
            "assignee": USER_ALPHA_MEMBER,
            "created_by": USER_ALPHA_MEMBER,
        },
    )
    await db.commit()

    assignee = (await db.execute(text("SELECT assignee_id FROM work_items"))).scalar_one()
    assert str(assignee) == USER_ALPHA_MEMBER


async def test_assignee_from_another_team_is_rejected_by_the_database(
    db: AsyncSession,
) -> None:
    """`work_items(team_id, assignee_id) -> memberships(team_id, user_id)`.

    An assignee who is not a member of the item's team must be impossible, not
    merely discouraged by application validation.
    """
    await _seed(db)

    with pytest.raises(IntegrityError) as excinfo:
        await db.execute(
            _INSERT_ITEM,
            {
                "team": TEAM_ALPHA,
                "title": "A valid title",
                "assignee": USER_BETA_MEMBER,
                "created_by": USER_ALPHA_MEMBER,
            },
        )
        await db.commit()

    assert "fk_work_items_team_id_assignee_id_memberships" in str(excinfo.value)
    await db.rollback()


async def test_unassigned_item_is_allowed(db: AsyncSession) -> None:
    await _seed(db)

    await db.execute(
        _INSERT_ITEM,
        {
            "team": TEAM_ALPHA,
            "title": "A valid title",
            "assignee": None,
            "created_by": USER_ALPHA_MEMBER,
        },
    )
    await db.commit()

    count = (await db.execute(text("SELECT count(*) FROM work_items"))).scalar_one()
    assert count == 1


async def test_title_length_is_enforced(db: AsyncSession) -> None:
    await _seed(db)

    with pytest.raises(IntegrityError) as excinfo:
        await db.execute(
            _INSERT_ITEM,
            {
                "team": TEAM_ALPHA,
                "title": "ab",
                "assignee": None,
                "created_by": USER_ALPHA_MEMBER,
            },
        )
        await db.commit()

    assert "ck_work_items_title_length" in str(excinfo.value)
    await db.rollback()


async def test_status_is_constrained(db: AsyncSession) -> None:
    await _seed(db)

    with pytest.raises(IntegrityError) as excinfo:
        await db.execute(
            text(
                """
                INSERT INTO work_items (team_id, title, type, priority, status, created_by)
                VALUES (:team, 'Some valid title', 'task', 'medium', 'not_a_status', :created_by)
                """
            ),
            {"team": TEAM_ALPHA, "created_by": USER_ALPHA_MEMBER},
        )
        await db.commit()

    assert "ck_work_items_status_is_known" in str(excinfo.value)
    await db.rollback()


async def test_one_role_per_user_per_team(db: AsyncSession) -> None:
    await _seed(db)

    with pytest.raises(IntegrityError) as excinfo:
        await db.execute(
            text("INSERT INTO memberships (user_id, team_id, role) VALUES (:user, :team, 'lead')"),
            {"user": USER_ALPHA_MEMBER, "team": TEAM_ALPHA},
        )
        await db.commit()

    assert "uq_memberships_team_id_user_id" in str(excinfo.value)
    await db.rollback()


async def test_activity_history_is_append_only(db: AsyncSession) -> None:
    """The audit trail cannot be rewritten, even by direct SQL."""
    await _seed(db)
    await db.execute(
        _INSERT_ITEM,
        {
            "team": TEAM_ALPHA,
            "title": "A valid title",
            "assignee": None,
            "created_by": USER_ALPHA_MEMBER,
        },
    )
    await db.execute(
        text(
            """
            INSERT INTO activity_events (work_item_id, team_id, actor_id, event_type, to_value)
            SELECT id, team_id, created_by, 'created', 'open' FROM work_items
            """
        )
    )
    await db.commit()

    with pytest.raises(DBAPIError) as update_error:
        await db.execute(text("UPDATE activity_events SET event_type = 'closed'"))
        await db.commit()
    assert "append-only" in str(update_error.value)
    await db.rollback()

    with pytest.raises(DBAPIError) as delete_error:
        await db.execute(text("DELETE FROM activity_events"))
        await db.commit()
    assert "append-only" in str(delete_error.value)
    await db.rollback()

    surviving = (await db.execute(text("SELECT count(*) FROM activity_events"))).scalar_one()
    assert surviving == 1
