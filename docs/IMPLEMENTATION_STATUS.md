# Implementation Status

A truthful, phase-by-phase record. "Verified" means the listed command was run and its
result observed in this repository — not that the code looks correct.

**Branch:** `feature/newtonite-operations-platform`
**Last updated:** 2026-10-17

## Phase summary

| Phase | Scope | Status | Verified by |
| --- | --- | --- | --- |
| 0 | Reconnaissance and design | Complete | Repository inspected, remote confirmed empty, brief and execution plan read |
| 1 | Runnable foundation | Complete | See below |
| 2 | Domain, seed data and constraint tests | Complete | See below |
| 3 | Authentication and team authorization | Not started | — |
| 4 | Core API and critical correctness behaviours | Not started | — |
| 5 | Workflow, collaboration and activity API | Not started | — |
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

## How to read this document

- A phase is only "Complete" when every acceptance criterion has been verified with a
  command whose output was observed.
- "Not run" is used rather than implying success. Anything not verified is stated as such.
- Commit SHAs are recorded in the row for each phase once the phase commit is pushed.
