# Implementation Status

A truthful, phase-by-phase record. "Verified" means the listed command was run and its
result observed in this repository — not that the code looks correct.

**Branch:** `feature/newtonite-operations-platform`
**Last updated:** 2026-10-17 (end of Phase 4)

## Phase summary

| Phase | Scope | Status | Verified by |
| --- | --- | --- | --- |
| 0 | Reconnaissance and design | Complete | Repository inspected, remote confirmed empty, brief and execution plan read |
| 1 | Runnable foundation | Complete | See below |
| 2 | Domain, seed data and constraint tests | Complete | See below |
| 3 | Authentication and team authorization | Complete | See below |
| 4 | Work-item API and the five critical correctness behaviours | Complete | See below |
| 5 | Comments API and activity history API | Not started | — |
| 6 | Frontend vertical slice | Not started | — |
| 7 | Transactional outbox and worker (conditional) | Not started | Gate not yet assessed |
| 8 | Final quality, documentation and reviewer readiness | Not started | — |

## Phase 0 — Reconnaissance and design

**Requirements.** Inspect the repository and remote before changing anything; read
`AGENTS.md`, the test-and-fix skill, the official challenge brief and the university
execution plan; record assumptions, risks and planned architecture.

**Findings.**

- The working directory was a Git repository with **no commits** and only two untracked
  entries: `AGENTS.md` and `.opencode/skills/test-and-fix/SKILL.md`. No existing
  implementation was present, so nothing had to be preserved or reconciled.
- `origin` is `https://github.com/mahinigam/newtonite`. The remote existed, was public and
  contained **no refs** (`git ls-remote` returned nothing; the GitHub API reported the
  repository).
- Both source documents were read locally from `~/Downloads`:
  `Newtonite_Software_Engineering_Challenge.pdf` (6 pages) and
  `GU_CCIO_Advisory_Newtonite_Software_Engineering_Execution_Plan (1).pdf` (8 pages).
  They are not part of the repository.
- Tooling present: Python 3.14 (with 3.12 available), `uv` 0.11.16, Node 25, Docker CLI
  (daemon stopped), PostgreSQL 16.13 via Homebrew (stopped), `gh` (unauthenticated at the
  time of inspection).

**Acceptance criteria.** No existing work lost; plan evidence-based. **Met.**

## Phase 1 — Runnable foundation

**Requirements.** Frontend and backend structure, pinned runtime dependencies,
PostgreSQL integration, environment configuration, migrations and seed mechanism, health
checks, Docker Compose, a first smoke test, and documented local startup.

**Acceptance criteria.** The foundation starts, migrations work, the health check
succeeds, and the smoke test passes from a clean setup.

**What was built.**

- `backend/` — FastAPI application factory, environment-driven settings, async SQLAlchemy
  engine and session management, structured `uv` project pinned to Python 3.12 with a
  committed `uv.lock`.
- `alembic/` — async migration environment; revision `0001_initial_schema` creates users,
  teams, memberships, work items, comments, activity events, auth sessions and
  idempotency keys, with their constraints, indexes and the append-only trigger.
- `docker-compose.yml` — PostgreSQL 16 on host port 5433 with an init script that creates
  the test database, so Compose cannot collide with a local PostgreSQL on 5432.
- `Makefile`, `.env.example`, `frontend/.env.example`, `.gitignore`.
- `.github/workflows/ci.yml` — formatting, lint, strict type check and tests against a
  `postgres:16` service container.
- `docker/initdb/01-test-database.sql`.

**Seed mechanism.** Not included in this phase: `alembic upgrade head` is the only setup
step required, and `make seed` does not exist until the seed script does. This was a
deliberate change from the original phase plan (which grouped seeding into Phase 1) to
avoid documenting a command that cannot run. Seeding lands in Phase 2.

**Verification.**

| Check | Command | Result |
| --- | --- | --- |
| Dependencies install from the lockfile | `uv sync --group dev` | Passed |
| Migrations apply to an empty database | `uv run alembic upgrade head` | Passed — `Running upgrade -> 0001, initial schema` |
| Migrations revert cleanly | `uv run alembic downgrade base` then `upgrade head` | Passed on both databases |
| Models match migrations | `uv run alembic check` | Passed — `No new upgrade operations detected` |
| Backend tests | `uv run pytest -q` | Passed — 12 passed |
| Formatting | `uv run ruff format --check .` | Passed — 26 files already formatted |
| Lint | `uv run ruff check .` | Passed — all checks passed |
| Strict types | `uv run mypy .` | Passed — no issues in 25 source files |
| Health endpoints | `tests/test_health.py` | Passed |
| CI (GitHub Actions) | run `38965759133`, job `Backend (lint, types, tests)` | Passed in 32s |

**Commits.** `a9d3e89` (foundation, pushed to `main` and this branch), `530b573`,
`937c7d1`, `aaedb7f` (CI corrections). Two CI fixes were needed and are recorded rather
than hidden: `setup-uv` publishes exact tags only, so `@v10` did not resolve and had to be
pinned to `v10.3.0`; and `ubuntu-latest` is scheduled to move to Ubuntu 26 on
19 October 2026, inside the delivery window, so the runner image is pinned.

**Initial history.** `main` had no commits, so a documented decision was made to create
the foundation as the first commit on `main` (rather than leaving the default branch
empty while work happened elsewhere), then continue on
`feature/newtonite-operations-platform`. Both branches point at the foundation today, and
the feature branch is merged back at the end. No history was rewritten and no force push
was used.

**Invariants proven at the database layer** (`tests/test_schema_constraints.py`), by
issuing raw SQL rather than going through application code:

- An assignee who is not a member of the item's team is rejected by the composite foreign
  key `work_items(team_id, assignee_id) -> memberships(team_id, user_id)`.
- An unassigned item is still allowed (MATCH SIMPLE skips the check when `assignee_id` is
  null).
- `activity_events` refuses `UPDATE` and `DELETE` via a trigger.
- Title length, status value and one-role-per-user-per-team constraints all hold.

**Corrected during this phase.** The first schema draft carried two unique constraints on
the same membership column pair. One was redundant, and the redundancy surfaced as a
confusing test failure. The constraint on `(team_id, user_id)` is retained because it
both enforces "one role per user per team" and serves as the composite foreign key target;
the second became a plain index on `user_id` for per-user lookups. The migration was
amended rather than adding a revision, because revision `0001` had never been released.
Both local databases were rebuilt from scratch afterwards (they contained no data).

**Remaining gaps in this phase.** No frontend yet; no seed data; no authentication; CI has
not yet run on the remote (first push happens with this phase).

**Known defects.** None known.

## Phase 2 — Domain, integrity and demo data

**Requirements.** A complete data model, seed data that makes the demo meaningful at
scale, and tests that prove the domain rules rather than the happy path.

**Acceptance criteria.** Migrations create the schema; the seed produces a coherent,
reproducible dataset; database-level invariants are proven by tests that bypass the
application; indexes match the query patterns.

**What was built.**

- `backend/scripts/seed.py` — a deterministic, idempotent seed: 4 teams, 12 users with
  documented demo credentials, 3 000 work items by default, plus coherent comments and
  activity history.
- `backend/app/security/passwords.py` — Argon2id hashing, plus a constant-time path for
  the "account does not exist" login case.
- `backend/tests/test_seed.py` — 10 tests covering determinism, idempotency and data
  coherence.
- `backend/tests/test_index_inventory.py` — 16 tests asserting every index and named
  constraint exists with the intended shape.
- `docs/DATA_MODEL.md` — the data model, the rule-to-mechanism table, the index rationale,
  and measured query plans.
- A `make seed` target, and CI steps that migrate and seed a clean database before testing.

**Design details worth recording.**

- *Determinism.* Identifiers come from `uuid5` over a fixed namespace, and each item is
  generated from its own seeded RNG, so the dataset does not change if `--items` changes.
  Timestamps are relative to run time so the dashboard looks current; a test asserts that
  structure (not timestamps) is reproducible.
- *Idempotency of history.* Items are inserted with `ON CONFLICT DO NOTHING`, and activity
  is generated **only for rows actually created**. This is what makes a second run add no
  duplicate history — the failure mode that would otherwise make the audit trail
  untrustworthy.
- *Coherent generated states.* Status and assignment are chosen together, so the seed never
  produces an item the workflow would not allow (an unassigned item awaiting approval).
  Open items are unassigned often enough to make the unassigned-urgent queue meaningful.
- *Honest seeding semantics.* The seed is additive, not converging. Documented in the
  script, the README and `KNOWN_LIMITATIONS.md`, because silently rewriting existing rows
  would contradict the append-only history rule.

**Verification.**

| Check | Command | Result |
| --- | --- | --- |
| Seed on a clean database | `uv run python -m scripts.seed --items 3000` | Passed — 4 teams, 12 users, 14 memberships, 3 000 items, 1 878 comments, 11 356 events in ~1 s |
| Seed is idempotent | `uv run python -m scripts.seed --items 3000` (second run) | Passed — **0 rows** created in every table |
| Backend tests | `uv run pytest -q` | Passed — **33 passed** (was 12) |
| Formatting / lint / types | `ruff format --check`, `ruff check`, `mypy .` | Passed |
| Index plans at 3 000 items | `EXPLAIN (ANALYZE, BUFFERS)` on the five hot queries | See `docs/DATA_MODEL.md` |
| CI (GitHub Actions) | run `38966610036` | Passed — every step green, including *Migrate the development database* and *Seed a small dataset* |

**Commit.** `384fe6a` on `feature/newtonite-operations-platform`. No history was rewritten.

**Measured query behaviour** (3 000 items, after `ANALYZE`): team list 0.18 ms via
`ix_work_items_team_status_created`; "assigned to me" 0.18 ms via
`ix_work_items_assignee_created`; unassigned-urgent 0.66 ms via the partial index; a
deep keyset page using an index-only scan with **0 heap fetches**, 0.39 ms; a single
item's timeline 0.06 ms.

**A finding that is documented rather than hidden.** Full-text search does *not* use its
GIN index at this dataset size: with ~3 000 rows the planner prefers a sequential scan and
is right to. Forcing the index shows it is correct and about 4× faster in isolation. The
claims in the documentation were adjusted to match the measurement: the index is described
as built for search and used when the table is large enough, not as universally faster.

**Also corrected during this phase.** The first draft of the batch insert failed because
SQLAlchemy compiles a bulk insert once against the first row's keys, and the `created`
event omitted `from_value`. Fixed with an explicit event factory so every row carries the
same columns — the kind of bug that would otherwise surface as silent data loss.

**Remaining gaps.** No API endpoints yet, so the seeded data is only reachable through
`psql`. No authentication. No frontend.

**Known defects.** None known.

## Phase 3 — Authentication and team authorization

**Requirements.** Real authentication (not a placeholder), authorization decided on the
server for every protected action, and no client-supplied identity or role ever being trusted.

**Acceptance criteria.** A login issues a credential that expires and can be revoked;
credentials are stored so that a database disclosure does not yield usable sessions;
every team-scoped endpoint authorizes against stored membership; a caller outside a team
cannot confirm that the team's data exists; role restrictions are proven by tests that
attempt the forbidden action.

**What was built.**

- `app/security/tokens.py` — 256-bit opaque tokens, stored only as a SHA-256 digest.
- `app/services/auth.py` — login, session resolution, revocation, session housekeeping.
  Absolute and idle expiry, both evaluated with the database clock.
- `app/policy/roles.py` — the permission matrix, defined once.
- `app/policy/principal.py` — the authenticated caller, carrying their role per team.
- `app/api/deps.py` — `get_principal` and `require_team_permission(...)` dependencies.
- `app/errors.py` — one error envelope with stable codes, installed on the app.
- `app/api/v1/` — `auth` (login, logout, me), `teams` (list, roster, set role, remove
  member) and `users` (scoped directory) routers.
- `scripts/maintenance.py` — operator commands to revoke one account's sessions and purge
  long-expired ones.
- `tests/test_auth.py` (15), `tests/test_authorization.py` (20),
  `tests/test_session_housekeeping.py` (5).

**Design details worth recording.**

- *The token is never stored.* The raw token appears only in the login response. A test reads
  every column of `auth_sessions` and asserts the token appears in none of them.
- *Two lifetime rules.* Absolute (12 h from login) and idle (2 h since last use). Every
  comparison uses PostgreSQL's `now()`, so two API processes cannot disagree; `login` also
  derives `expires_at` from the database clock.
- *Reads do not become writes.* `last_used_at` is refreshed at most once a minute, with the
  condition in the `WHERE` clause. Documented, with its one-minute slack, rather than hidden.
- *Failed logins do not leak which addresses have accounts.* Unknown email, wrong password and
  deactivated account return byte-identical `401 invalid_credentials` responses, and the
  unknown-email path spends a real Argon2id verification so the timing matches. A test asserts
  the bodies are equal.
- *404 rather than 403 across a team boundary.* A caller outside a team gets the same answer
  for a real team id and for a random UUID; a test asserts the two responses are identical.
- *Authorization is a dependency.* A route that needs a permission declares it, so it is not
  possible to perform the work before checking.
- *Roles are advisory to the UI, authoritative on the server.* `/auth/me` returns the caller's
  roles so the interface can decide what to show, and every write is checked again from stored
  memberships.
- *Two invariants that protect a team.* A team keeps at least one admin, and a member who is
  still the assignee of work items cannot be removed — the composite foreign key would refuse
  it anyway, but the API turns that into an instruction ("reassign their work first") instead
  of a raw integrity error. A test reassigns the item and asserts the removal then succeeds.
- *Removing a member does not touch history.* Removing a membership leaves `activity_events`
  exactly as it was; a test counts them before and after.

**Verification.**

| Check | Command | Result |
| --- | --- | --- |
| Backend tests | `uv run pytest -q` | Passed — **68 passed** (was 33) |
| Formatting / lint / types | `ruff format --check`, `ruff check`, `mypy .` | Passed — 50 files formatted, 49 files type-checked |
| End-to-end against the seeded dev database | `uvicorn`, then `curl` login → `/auth/me`, `/teams`, rosters, refusals | Passed — see below |
| CI (GitHub Actions) | run `38968414101` | Passed — every step green, including the 73-test suite |

**End-to-end evidence against the real seeded dataset** (3 000 items, 12 demo accounts, real
Argon2id hashes, the documented password):

- `sam.multi@newtonite.dev` signed in and `/auth/me` returned **two memberships with different
  roles**: `platform: member` and `finance: viewer`.
- A wrong password and an unknown email returned the **same** body:
  `{"error":{"code":"invalid_credentials","message":"Email or password is incorrect."}}`.
- `vic.viewer` (viewer) read the platform roster: `200`.
- `sam.multi` (member) and `lena.lead` (lead) each attempted to change a role: `403 forbidden`.
- `sam.multi` (viewer **in** finance) attempted the same in finance: `403`.
- `ada.admin` (admin of platform, **not** a member of finance) attempted it in finance: `404`.
  Same request, different boundary — this is the 403/404 distinction working.
- An invalid role value: `422`, with the field named in `details`.
- After every refusal, the roster was re-read and was **unchanged**.
- The directory returned 8 people for `sam.multi` — the union of platform and finance, and
  nobody from the teams they are not in.

**Commit.** `16d1b59` on `feature/newtonite-operations-platform`. No history was rewritten.

**Remaining gaps.** No work-item endpoints yet, so the seeded items are still only reachable
through `psql`. No frontend. No login rate limiting (documented in
[`SECURITY.md`](SECURITY.md) with the residual risk).

**Known defects.** None known.

## Phase 4 — Work-item API and the five critical correctness behaviours

**Requirements.** Work-item create, read, update and list endpoints; server-side filtering,
search and cursor pagination; and the behaviours the brief singles out — an atomic claim, a
stale update being rejected rather than silently applied, idempotent creation, server-enforced
workflow transitions with role authorization, and an activity history that cannot disagree
with the state it describes. Plus indexing driven by real query patterns, and evidence
rather than assertion for all of it.

**Acceptance criteria.** Two concurrent claims produce at most one claimant; a stale update
is rejected and changes nothing; a replayed create produces at most one item and a mismatched
payload is refused; cross-team access and forbidden role actions are denied; illegal workflow
transitions are refused; a failed history insert rolls the state change back; and every list
view is filtered and paged in PostgreSQL with a bounded page size.

**What was built.**

- `app/services/work_items.py` — the whole item lifecycle. `list_statement` (pure, so the
  exact SQL can be measured), `create_item` (idempotency in one transaction),
  `claim_item` (one conditional `UPDATE`), `update_item`, `transition_item`, and
  `load_visible_item` (authorizes from the row's team).
- `app/policy/workflow.py` — the transition table: origin, destination, required permission,
  `requires_assignee`, and `available_transitions(status, role, has_assignee)`.
- `app/services/activity.py` — transactional history writes, sharing the caller's session.
- `app/services/cursors.py` — `(created_at, id)` cursors, base64url, opaque.
- `app/api/preconditions.py` — `ETag` construction and `If-Match` parsing (`7`, `"7"`,
  `W/"7"`; absent → `428`).
- `app/schemas/work_items.py` — summary and detail shapes, tri-state edit payload.
- `app/api/v1/items.py` — six routes.
- `alembic/versions/0002_team_created_index.py` — one index, added because of a measurement.
- `scripts/explain_lists.py` — the measuring instrument behind `docs/DATA_MODEL.md`.
- Seven new test files: `test_items_create.py` (17), `test_transitions.py` (16),
  `test_items_update.py` (15), `test_items_list.py` (15), `test_claim.py` (9),
  `test_items_concurrency.py` (4), `test_activity_atomicity.py` (4).

**Design details worth recording.** The reasoning is in `ENGINEERING_DECISIONS.md` decisions
9–14; the short version:

- *Claiming is one `UPDATE` whose affected-row count is the answer.* The eligibility condition
  is inside the statement, so there is no read-then-write window to race. A version
  precondition was considered and rejected as the *weaker* guard — it says nothing about the
  assignee.
- *The `Idempotency-Key` is required, and the key row shares the item's transaction.*
  Concurrent duplicates block on the unique index and then replay, so the loser cannot slip
  a second item in.
- *`If-Match` is required on every edit and transition (`428` when absent), and the comparison
  shares the `UPDATE` that bumps the version.* A stale attempt gets `412` with
  `details.current`.
- *Status is not a patchable field.* `PATCH` refuses it, so closure and approval cannot be
  reached by a generic edit.
- *History is written in the same transaction.* Verified by breaking it on purpose.

**Verification.**

| Check | Command | Result |
| --- | --- | --- |
| Backend tests | `uv run pytest -q` | Passed — **154 passed** in ~33 s (was 73) |
| Two concurrent claims | `tests/test_items_concurrency.py` | Passed — `200`/`409`, one assignee, one `claimed` event. Repeated 10× for flakiness, then checked against a deliberately broken implementation |
| Concurrent creates with one key | same file | Passed — `201`/`200`, same id, exactly one row |
| Stale update rejected | `tests/test_items_update.py` | Passed — `412`, `details.current`, newer value intact, no history written |
| Illegal transition refused | `tests/test_transitions.py` | Passed — all edges, plus role denials, terminal `closed`, and two concurrent approvals |
| Failed history insert rolls back | `tests/test_activity_atomicity.py` | Passed — item byte-identical after a forced trigger failure, for edit, claim and transition |
| Formatting / lint / types | `ruff format --check`, `ruff check`, `mypy .` | Passed — 66 files formatted, 66 type-checked |
| Models match migrations | `uv run alembic check` | Passed — `No new upgrade operations detected` |
| Query plans | `uv run python -m scripts.explain_lists` | All seven shapes use an index; see `docs/DATA_MODEL.md` |
| End-to-end over HTTP | `curl` against the seeded database on a real `uvicorn` process | **49/49 checks passed** — see below |

**End-to-end evidence against the real seeded dataset** (3 000 items, 12 accounts, real
Argon2id logins, real HTTP, a real server process):

- *Idempotency.* A create with a fresh key returned `201`; the identical retry returned `200`
  with the same id and `Idempotent-Replay: true`; the same key with a different body returned
  `409 idempotency_key_reuse`; no key at all returned `400 idempotency_key_required`.
- *Claiming.* A viewer was refused with `403`; a member claimed the item, which set the
  assignee, bumped the version to 2, left the status at `open`, and wrote one `claimed` event.
- *Concurrency, over real HTTP.* Two members POSTed a claim at the same instant: one `200`,
  one `409`, and **exactly one** `claimed` event in the database. The same create sent twice
  concurrently returned `200` and `201` with the same id, and exactly **one** row existed with
  that title.
- *Stale writes.* An edit with no `If-Match` → `428 precondition_required`; an edit from
  version 1 after the item had moved to version 2 → `412 stale_version` with
  `details.current.version = 2`; the same edit from the current version → `200`, version 3.
- *The workflow.* member claims → starts → submits; the member's attempt to approve was `403`
  and their `available_transitions` was empty, while the lead's held exactly the two approval
  moves; the lead approved, then closed, after which `available_transitions` was empty; an
  attempt to reopen a closed item was `409 invalid_transition`.
- *History.* The item's timeline read back from the database as eight correctly attributed
  events — `created`, `claimed`, `title_changed`, `priority_changed`, `status_changed`,
  `status_changed`, `approved`, `status_changed` — with the two approvals attributed to the
  lead and the rest to the member.
- *Listing.* A page was bounded by `limit` and reported `has_more`; page 2 shared **zero** ids
  with page 1; `limit=101` was `422`; full-text search found the item just created; combined
  filters returned only matching rows.
- *The team boundary.* `sam.multi` (viewer in finance) could read a finance item, while the
  platform lead got `404` for the same item on both read and edit, and `sam.multi` was refused
  `403` for claiming in finance where they are only a viewer.
- *Assignee eligibility.* A lead assigning an item to a member of another team got
  `422 invalid_assignee` on both create and edit; a member assigning to a colleague got
  `403 forbidden` naming `assign_others`; after all three refusals the item was unchanged
  (assignee `null`, version 1).

**A correction recorded rather than quietly fixed.** The first draft of the concurrency test
file called `raw_item` without `await` inside a subscript, which raised
`TypeError: 'coroutine' object is not subscriptable` in four tests. The fix was in the test,
not the assertion. Separately, one test asserted the *order* of field-change events from an
assumed order; the implementation emits them in the field-declaration order, which is
deterministic. The test was corrected to the real, documented order — and the ordering
guarantee is now stated in the code comment on `_FIELD_EVENTS`, so it is a documented
property rather than an accident of `dict` iteration.

**How the concurrency and atomicity tests were validated.** A test that passes proves
nothing if it would also pass against the bug it targets. Each of the three load-bearing
tests was therefore checked by deliberately breaking the implementation:

| Test | Deliberate break | Result |
| --- | --- | --- |
| Concurrent claims | `claim_item` rewritten as read-then-write with a widened window | Both claim tests **failed**; all ten racers "won" |
| Concurrent create | duplicate keys made to proceed instead of replaying | The idempotency test **failed** |
| Activity atomicity | the state change committed before the history insert | The rollback test **failed** with its own message ("the claim survived without its history entry") |

`app/services/work_items.py` was restored after each and verified byte-identical with `diff`.

**A measurement that found real work.** The unfiltered team list was a sequential scan plus a
top-N sort (3.19 ms) because the existing composite index leads with `status`. Revision `0002`
adds `ix_work_items_team_created`; the plan became an index scan at 0.53 ms. Separately, the
text-search configuration was being passed as a bound parameter, which under a generic plan
made PostgreSQL re-evaluate `websearch_to_tsquery` for every index entry — 5.47 ms against
0.245 ms with the same plan shape. Both are documented with their numbers in
`docs/DATA_MODEL.md`.

**Remaining gaps.** Comments and the activity read API do not exist yet, so history is written
and inspectable only through `psql`. No frontend. Reopening a closed item is refused by
design. The verification pass added six probe work items to the development database
(3 006 items, 11 377 events in total); they are ordinary rows and the seed remains additive
and idempotent.

**Known defects.** None known.

## How to read this document

- A phase is only "Complete" when every acceptance criterion has been verified with a
  command whose output was observed.
- "Not run" is used rather than implying success. Anything not verified is stated as such.
- Commit SHAs are recorded in the row for each phase once the phase commit is pushed.
