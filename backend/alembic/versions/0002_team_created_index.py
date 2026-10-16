"""add the team/created index for the unfiltered team list

Adding one index, and the reason is a measurement rather than a hunch.

`GET /api/v1/teams/{team_id}/items` with no `status` filter is the default view of a
team's work, and it orders by `(created_at DESC, id DESC)` within one team. The existing
`ix_work_items_team_status_created` covers `(team_id, status, created_at DESC, id DESC)`,
and because its second column is `status`, it cannot serve a query that does not constrain
`status` — PostgreSQL has nothing to seek on after `team_id`.

At 3 000 seeded items that showed up as a sequential scan of the whole table plus a top-N
sort, 3.19 ms, against 0.20 ms for the same list filtered by status. That gap is not about
3 000 rows; it is the shape of the plan, which would stay a full scan as the table grows.
`ix_work_items_team_created` gives the planner a `(team_id, created_at DESC, id DESC)`
path, which is also exactly the ordering the keyset cursor walks.

No other index is added. "Assigned to me" is already served by
`ix_work_items_assignee_created` (measured 0.43 ms) and the unassigned-urgent queue by its
partial index, so both are left alone. One index, for one measured gap.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-17

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_work_items_team_created",
        "work_items",
        ["team_id", sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_work_items_team_created", table_name="work_items")
