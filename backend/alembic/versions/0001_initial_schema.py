"""initial schema

Creates the seven domain tables, their integrity constraints and their indexes.

Generated with `alembic revision --autogenerate` from `app.models`, then
reviewed and extended by hand with the append-only trigger on
`activity_events` (which autogenerate cannot express).

Revision ID: 0001
Revises:
Create Date: 2026-10-17

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Guards the audit trail in the database itself rather than only in application
# code: an ordinary UPDATE or DELETE against activity_events is refused even if
# someone connects directly and tries it.
APPEND_ONLY_FUNCTION = "newtonite_activity_events_append_only"


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "description", sa.String(length=500), server_default=sa.text("''"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_teams")),
        sa.UniqueConstraint("key", name=op.f("uq_teams_key")),
    )
    op.create_table(
        "users",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("position('@' in email) > 1", name=op.f("ck_users_email_has_at_sign")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_auth_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_auth_sessions_token_hash")),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"], unique=False)
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("resource_id", sa.UUID(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('in_progress', 'completed')",
            name=op.f("ck_idempotency_keys_status_is_known"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_idempotency_keys_actor_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_keys")),
        sa.UniqueConstraint("actor_id", "operation", "key", name="uq_idempotency_keys_scope"),
    )
    op.create_table(
        "memberships",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "role IN ('viewer', 'member', 'lead', 'admin')",
            name=op.f("ck_memberships_role_is_known"),
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name=op.f("fk_memberships_team_id_teams"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_memberships_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memberships")),
        sa.UniqueConstraint("team_id", "user_id", name="uq_memberships_team_id_user_id"),
    )
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"], unique=False)
    op.create_table(
        "work_items",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'open'"), nullable=False),
        sa.Column("assignee_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('english', coalesce(title, '') || ' ' || coalesce(description, ''))",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            "priority IN ('low', 'medium', 'high', 'urgent')",
            name=op.f("ck_work_items_priority_is_known"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'in_progress', 'awaiting_approval', 'resolved', 'closed')",
            name=op.f("ck_work_items_status_is_known"),
        ),
        sa.CheckConstraint(
            "type IN ('incident', 'customer_issue', 'payment', 'compliance', 'task')",
            name=op.f("ck_work_items_type_is_known"),
        ),
        sa.CheckConstraint(
            "char_length(description) <= 20000", name=op.f("ck_work_items_description_length")
        ),
        sa.CheckConstraint(
            "char_length(title) BETWEEN 3 AND 200", name=op.f("ck_work_items_title_length")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_work_items_version_positive")),
        sa.ForeignKeyConstraint(
            ["assignee_id"],
            ["users.id"],
            name=op.f("fk_work_items_assignee_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_work_items_created_by_users"),
            ondelete="RESTRICT",
        ),
        # "An assignee must belong to the item's team", enforced by the database
        # via the unique (team_id, user_id) key on memberships.
        sa.ForeignKeyConstraint(
            ["team_id", "assignee_id"],
            ["memberships.team_id", "memberships.user_id"],
            name="fk_work_items_team_id_assignee_id_memberships",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name=op.f("fk_work_items_team_id_teams"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_work_items")),
    )
    op.create_index(
        "ix_work_items_assignee_created",
        "work_items",
        ["assignee_id", sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_index(
        "ix_work_items_search_vector",
        "work_items",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_work_items_team_status_created",
        "work_items",
        ["team_id", "status", sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_index(
        "ix_work_items_unassigned_urgent",
        "work_items",
        ["team_id", sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
        unique=False,
        postgresql_where=sa.text(
            "assignee_id IS NULL AND status = 'open' AND priority IN ('high','urgent')"
        ),
    )
    op.create_table(
        "activity_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("work_item_id", sa.UUID(), nullable=False),
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("from_value", sa.Text(), nullable=True),
        sa.Column("to_value", sa.Text(), nullable=True),
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('created', 'claimed', 'assigned', 'unassigned', 'status_changed', "
            "'approved', 'rejected', 'priority_changed', 'due_date_changed', 'type_changed', "
            "'title_changed', 'description_changed', 'commented')",
            name=op.f("ck_activity_events_event_type_is_known"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_activity_events_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name=op.f("fk_activity_events_team_id_teams"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name=op.f("fk_activity_events_work_item_id_work_items"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_activity_events")),
    )
    op.create_index(
        "ix_activity_events_team_id_created_at",
        "activity_events",
        ["team_id", sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_index(
        "ix_activity_events_work_item_id_created_at",
        "activity_events",
        ["work_item_id", sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_table(
        "comments",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("work_item_id", sa.UUID(), nullable=False),
        sa.Column("author_id", sa.UUID(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(btrim(body)) BETWEEN 1 AND 4000", name=op.f("ck_comments_body_length")
        ),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name=op.f("fk_comments_author_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name=op.f("fk_comments_work_item_id_work_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comments")),
    )
    op.create_index(
        "ix_comments_work_item_id_created_at", "comments", ["work_item_id", "created_at", "id"]
    )

    # History is append-only. TRUNCATE is intentionally still allowed so test
    # fixtures can reset the table between cases.
    op.execute(
        f"""
        CREATE FUNCTION {APPEND_ONLY_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'activity_events is append-only (% attempted)', TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_activity_events_append_only
        BEFORE UPDATE OR DELETE ON activity_events
        FOR EACH ROW EXECUTE FUNCTION {APPEND_ONLY_FUNCTION}();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_activity_events_append_only ON activity_events")
    op.execute(f"DROP FUNCTION IF EXISTS {APPEND_ONLY_FUNCTION}()")
    op.drop_index("ix_comments_work_item_id_created_at", table_name="comments")
    op.drop_table("comments")
    op.drop_index("ix_activity_events_work_item_id_created_at", table_name="activity_events")
    op.drop_index("ix_activity_events_team_id_created_at", table_name="activity_events")
    op.drop_table("activity_events")
    op.drop_index(
        "ix_work_items_unassigned_urgent",
        table_name="work_items",
        postgresql_where=sa.text(
            "assignee_id IS NULL AND status = 'open' AND priority IN ('high','urgent')"
        ),
    )
    op.drop_index("ix_work_items_team_status_created", table_name="work_items")
    op.drop_index("ix_work_items_search_vector", table_name="work_items", postgresql_using="gin")
    op.drop_index("ix_work_items_assignee_created", table_name="work_items")
    op.drop_table("work_items")
    op.drop_index("ix_memberships_user_id", table_name="memberships")
    op.drop_table("memberships")
    op.drop_table("idempotency_keys")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_table("users")
    op.drop_table("teams")
