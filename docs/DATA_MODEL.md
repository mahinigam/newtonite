# Data Model

The database is the authoritative source of truth. This document describes what is
stored, which rules the database itself enforces, why each index exists, and measured
query behaviour. It reflects migration `0001_initial_schema`.

## Entities

```
users ──< memberships >── teams
  │                        │
  │                        └──< work_items ──< comments
  │                                 │
  └──< auth_sessions                └──< activity_events
  └──< idempotency_keys             (activity_events also carries team_id)
```

| Table | Holds | Notes |
| --- | --- | --- |
| `users` | Accounts and Argon2id password hashes | Unique, lower-cased `email` |
| `teams` | Teams, addressed by a short unique `key` | |
| `memberships` | A user's role **inside one team** | The only place a role exists |
| `work_items` | The operational record | Belongs to exactly one team; carries `version` |
| `comments` | Human notes on an item | Append-only: no edit or delete path |
| `activity_events` | Append-only history | Written in the same transaction as the change |
| `auth_sessions` | Login sessions | Stores only a SHA-256 digest of the token |
| `idempotency_keys` | Retry records for writes | Keyed on `(actor_id, operation, key)` |

Roles are per team, not global: one account can be a `member` in one team and a `viewer`
in another. The seed data contains exactly that case, and a test asserts it.

## Conventions

| Convention | Choice | Reason |
| --- | --- | --- |
| Primary keys | `uuid` defaulting to `gen_random_uuid()` | Ids can be generated and compared without a round trip, and do not leak record counts |
| History keys | `bigint` identity | Append-only rows want a monotonic key; it gives a strict total order for keyset pagination without depending on timestamp precision |
| Timestamps | `timestamptz`, defaulted by the database | Unambiguous across time zones; the server clock is the only clock |
| Enum-like values | `varchar` + named `CHECK` constraint | Adding a status is an additive migration rather than a PostgreSQL type rewrite |
| Optimistic concurrency | `work_items.version` integer, bumped by the update itself | The version comparison and the bump happen in one statement, so two racing writers cannot both succeed |

## Integrity rules and where they are enforced

Application validation produces good error messages. These rules do **not** depend on it.

| Rule | Mechanism | Test |
| --- | --- | --- |
| An assignee must be a member of the item's team | Composite FK `work_items(team_id, assignee_id) → memberships(team_id, user_id)` | `test_schema_constraints.py::test_assignee_from_another_team_is_rejected_by_the_database` |
| An unassigned item is valid | Same FK under `MATCH SIMPLE`, which skips the check when `assignee_id` is null | `test_unassigned_item_is_allowed` |
| At most one role per user per team | `UNIQUE(team_id, user_id)` | `test_one_role_per_user_per_team` |
| History cannot be rewritten | `BEFORE UPDATE OR DELETE` trigger raising `restrict_violation` | `test_activity_history_is_append_only` |
| Status, priority, type, role are from known sets | Named `CHECK` constraints | `test_status_is_constrained`, `test_index_inventory.py` |
| Title is 3–200 characters, description ≤ 20 000, comment 1–4 000 | `CHECK` constraints | `test_title_length_is_enforced` |
| `version` never drops below 1 | `CHECK (version >= 1)` | inventory test |
| An idempotency key is unique per actor and operation | `UNIQUE(actor_id, operation, key)` | inventory test; exercised in Phase 4 |
| Email looks like an email | `CHECK (position('@' in email) > 1)` — defence in depth, not validation | inventory test |

`TRUNCATE` is deliberately still allowed on `activity_events` so test fixtures can reset
the table; ordinary `UPDATE` and `DELETE` are refused.

## Indexes, and the query each one serves

| Index | Serves |
| --- | --- |
| `ix_work_items_team_status_created` on `(team_id, status, created_at DESC, id DESC)` | Team-scoped item lists filtered by status, newest first. The workhorse for the list and dashboard views |
| `ix_work_items_assignee_created` on `(assignee_id, created_at DESC, id DESC)` | "Assigned to me" across the teams the caller belongs to |
| `ix_work_items_unassigned_urgent` on `(team_id, created_at DESC, id DESC) WHERE assignee_id IS NULL AND status = 'open' AND priority IN ('high','urgent')` | The unassigned-urgent queue. Partial so it stays small and only covers rows that queue can return |
| `ix_work_items_search_vector` (GIN) | Full-text search over title + description |
| `ix_activity_events_work_item_id_created_at` on `(work_item_id, created_at DESC, id DESC)` | One item's timeline, newest first |
| `ix_activity_events_team_id_created_at` on `(team_id, created_at DESC, id DESC)` | Team-scoped recent activity, and the authorization check for activity reads |
| `ix_comments_work_item_id_created_at` on `(work_item_id, created_at, id)` | One item's comment thread, oldest first |
| `ix_memberships_user_id` on `(user_id)` | "Which teams does this user belong to" — the prefix the composite unique constraint cannot answer |
| `ix_auth_sessions_user_id` on `(user_id)` | Token lookup and "revoke every session for this user" |

Every list index ends in `id DESC` so the ordering is a total order: two items created in
the same millisecond still have exactly one correct position. That is what makes keyset
pagination stable.

### Why the unassigned-urgent index leads with `team_id`

The dashboard queue is always scoped to one team, so a leading `team_id` lets the index
both filter and order. A cross-team version of that query (no `team_id` predicate) can
still use the index for filtering but has to sort the result, which is the correct
trade-off for a queue that is normally viewed one team at a time.

## Measured behaviour

Data: the seed's default dataset of **3 000 work items**, **11 356 activity events**,
**1 878 comments**, 12 users and 14 memberships, after `ANALYZE`. Timings are a single
local run on the development machine — they are evidence that the indexes are used and
the plans are bounded, not a performance claim.

| Query | Plan | Rows | Time |
| --- | --- | --- | --- |
| Team list, filtered by status, newest first | `Index Scan using ix_work_items_team_status_created` | 25 (limit) | **0.18 ms** |
| "Assigned to me", newest first | `Index Scan using ix_work_items_assignee_created` | 25 (limit) | **0.18 ms** |
| Unassigned high/urgent open items | `Bitmap Index Scan on ix_work_items_unassigned_urgent`, then 25-row top-N sort | 194 matched | **0.66 ms** |
| Deep keyset page (~4 000 rows skipped) | `Index Only Scan using ix_work_items_team_status_created`, `Heap Fetches: 0` | 25 (limit) | **0.39 ms** |
| One item's timeline | `Bitmap Index Scan on ix_activity_events_work_item_id_created_at` | 8 | **0.06 ms** |
| Full-text search for `timeout` | **Sequential scan** (planner's choice), 2 812 rows removed by filter | 140 matched | 1.21 ms |
| The same search with sequential scans discouraged | `Bitmap Index Scan on ix_work_items_search_vector` | 140 matched | 0.28 ms |

The keyset row is the important one: skipping thousands of rows costs the same as reading
the first page, because there is no `OFFSET` to scan through.

### An honest note on full-text search at this size

At 3 000 rows — roughly 50 pages of heap — PostgreSQL prefers a sequential scan for full
text search even when the term matches only 5% of rows, and it is right to: reading 50
pages sequentially is cheaper than a bitmap index scan plus 100 heap block fetches. The
forced comparison above shows the GIN index is correct and about 4× faster in isolation,
but the planner is entitled to ignore it at this scale.

So the accurate statement is: **the GIN index is built for search and is used when the
table is large enough for the planner to prefer it; at the seeded size it does not.**
No claim of faster search is made at 3 000 rows. The design is sized for the brief's
tens-of-thousands-of-items case, and that has not been measured.

## What the schema deliberately does not do

- No soft-delete column: items are resolved or closed, not deleted, and there is no delete
  endpoint. History therefore never dangles.
- No `updated_by` on `work_items`: who changed what is in `activity_events`, which keeps a
  full record instead of only the last editor.
- No cascade from `work_items` to `activity_events`: history is deleted only by deleting
  the whole table, which the application never does.
