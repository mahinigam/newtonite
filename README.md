# Newtonite — Operations Under Pressure

Internal web application for creating, assigning, tracking, investigating, approving
and resolving operational work across teams.

The system is being built as one coherent vertical slice: a team-scoped work-item API
with a server-enforced workflow, an append-only activity history, and explicit handling
of concurrent and retried requests.

> **Current status.** This repository is under active construction and the README is
> kept in step with what actually runs. See
> [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md) for the honest
> phase-by-phase state, and [`docs/KNOWN_LIMITATIONS.md`](docs/KNOWN_LIMITATIONS.md) for
> what is deliberately missing.

## What works today (Phase 4 — work-item API and the correctness behaviours)

| Capability | Status |
| --- | --- |
| Backend project, pinned dependencies (`uv`, Python 3.12) | Working |
| Async SQLAlchemy + PostgreSQL wiring, bounded connection pool | Working |
| Alembic migration workflow with a model/migration drift check | Working |
| Schema for users, teams, memberships, work items, comments, activity events, sessions, idempotency keys | Working |
| Database-enforced invariants (team/assignee consistency, append-only history, value sets) | Working, tested |
| Deterministic, idempotent demo seed (~3 000 work items, 12 users, 4 teams) | Working |
| Indexes matched to query patterns, with measured plans | Working — see [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) |
| Login, logout and session validation (Argon2id, hashed tokens, absolute + idle expiry, revocation) | Working |
| Per-team authorization with an explicit role matrix, enforced on the server | Working |
| Teams, membership and roster endpoints; admin-only role management | Working |
| One error envelope: `400` / `401` / `403` / `404` / `409` / `412` / `422` / `428` with stable codes | Working |
| Work-item create, read, edit and list, all authorized against team membership | Working |
| Server-side filtering (`status`, `priority`, `type`, `assignee=me\|none`), full-text search, keyset pagination | Working |
| Atomic claim — two concurrent claims produce at most one owner | Working, tested concurrently |
| Stale-update rejection: `If-Match` required (`428`), stale writes refused (`412`) | Working, tested |
| Idempotent creation via a required `Idempotency-Key`, safe under retry and concurrency | Working, tested concurrently |
| Server-enforced workflow with per-transition role permissions; `status` not patchable | Working |
| Activity history written in the same transaction as every mutation | Working — reads arrive in Phase 5 |
| Liveness (`/healthz`) and readiness (`/readyz`) endpoints | Working |
| Backend test suite against real PostgreSQL | Working (154 tests) |
| Docker Compose PostgreSQL for reviewers | Working |
| CI: formatting, lint, types, migrate, seed, tests | Working |
| Comments API, activity read API, frontend | Not yet — see [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md) |

## Technology stack

| Layer | Choice |
| --- | --- |
| API | Python 3.12, FastAPI, Pydantic v2 |
| Persistence | PostgreSQL 16, SQLAlchemy 2.x (async), asyncpg |
| Migrations | Alembic |
| Tests | pytest, pytest-asyncio, HTTPX (ASGI transport), real PostgreSQL |
| Quality | ruff (format + lint), mypy (strict) |
| Frontend | React + TypeScript (Vite, TanStack Query) — added in a later phase |

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) — installs Python 3.12 and the locked dependencies.
- PostgreSQL 16 — either a local server or the bundled Docker Compose service.
- Docker (only if you prefer the Compose database).

## Setup

### 1. Configuration

```bash
cp .env.example .env
```

`.env` is gitignored. The values in `.env.example` are **local development placeholders**,
not secrets. Adjust `DATABASE_URL` if your PostgreSQL differs.

### 2. Start PostgreSQL

Either a local server:

```bash
brew services start postgresql@16
```

```sql
CREATE ROLE newtonite LOGIN PASSWORD 'newtonite_dev_pw';
CREATE DATABASE newtonite OWNER newtonite;
CREATE DATABASE newtonite_test OWNER newtonite;
```

Or the bundled Compose service, which listens on **host port 5433** so it never collides
with a PostgreSQL already using 5432:

```bash
docker compose up -d db
# then point .env at it:
# DATABASE_URL=postgresql+asyncpg://newtonite:newtonite_dev_pw@127.0.0.1:5433/newtonite
# TEST_DATABASE_URL=postgresql+asyncpg://newtonite:newtonite_dev_pw@127.0.0.1:5433/newtonite_test
```

`newtonite_test` is required. The integration suite **truncates** its tables between
tests, so it must never point at a database holding data you care about.

### 3. Install dependencies and migrate

```bash
cd backend
uv sync --group dev
uv run alembic upgrade head
```

Equivalently, from the repository root: `make setup` and `make migrate`.

### 4. Load demo data

```bash
cd backend
uv run python -m scripts.seed            # 3000 work items (default)
uv run python -m scripts.seed --items 200  # smaller dataset
```

or `make seed` (add `ITEMS=200` for a smaller set).

The seed is **idempotent**: a second run creates nothing, and importantly generates no
duplicate activity history. Identifiers, teams, roles and the item distribution are
derived from fixed seeds, so the dataset is the same on every machine. Timestamps are
relative to the moment it runs, so the dashboard looks current.

Seeding is additive rather than converging — editing the generator does not rewrite rows
that already exist. To regenerate from scratch, rebuild the schema
(see [`docs/OPERATIONS.md`](docs/OPERATIONS.md)) and run it again.

### 5. Log in as a demo user

All demo accounts use the same development-only password:

| Email | Password | Teams and roles |
| --- | --- | --- |
| `ada.admin@newtonite.dev` | `Newtonite!2026` | platform: admin, support: admin |
| `lena.lead@newtonite.dev` | `Newtonite!2026` | platform: lead |
| `marcus.lead@newtonite.dev` | `Newtonite!2026` | support: lead |
| `priya.lead@newtonite.dev` | `Newtonite!2026` | finance: lead |
| `omar.lead@newtonite.dev` | `Newtonite!2026` | compliance: lead |
| `nina.member@newtonite.dev` | `Newtonite!2026` | platform: member |
| `noah.member@newtonite.dev` | `Newtonite!2026` | platform: member |
| `tara.member@newtonite.dev` | `Newtonite!2026` | support: member |
| `ivan.member@newtonite.dev` | `Newtonite!2026` | finance: member |
| `grace.member@newtonite.dev` | `Newtonite!2026` | compliance: member |
| `sam.multi@newtonite.dev` | `Newtonite!2026` | platform: member, finance: viewer |
| `vic.viewer@newtonite.dev` | `Newtonite!2026` | platform: viewer |

These are **development placeholders, not secrets**, and they exist so a reviewer can
exercise every role without creating accounts. `sam.multi` deliberately holds a different
role in each of two teams — the case a per-team role model has to get right.

Try it without a client:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"sam.multi@newtonite.dev","password":"Newtonite!2026"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')

curl -s http://localhost:8000/api/v1/auth/me    -H "Authorization: Bearer $TOKEN"
curl -s http://localhost:8000/api/v1/teams      -H "Authorization: Bearer $TOKEN"
```

`/auth/me` returns the caller's role in each team, which is how the frontend knows what to
offer. It is advisory to the interface only — every write is authorized again on the server.
The full endpoint list is in [`docs/API.md`](docs/API.md).

### 6. Create, claim and move a work item

The three headers that carry the correctness guarantees, in one sequence. Everything below
runs against a seeded database; replace `<TEAM_ID>` with a team from `/auth/me`.

```bash
# Create — Idempotency-Key is required, so a retry cannot make a second item.
ITEM=$(curl -s -X POST "http://localhost:8000/api/v1/teams/<TEAM_ID>/items" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{"title":"Refund the duplicate charge","type":"payment","priority":"urgent"}')
echo "$ITEM"                       # 201, with an ETag
ID=$(echo "$ITEM" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')

# Claim — atomic. No If-Match: the race is decided by assignee_id IS NULL inside the UPDATE.
curl -s -X POST "http://localhost:8000/api/v1/items/$ID/claim" \
  -H "Authorization: Bearer $TOKEN"

# Edit — If-Match is required. Send the ETag you last saw, or get 428 / 412.
curl -s -X PATCH "http://localhost:8000/api/v1/items/$ID" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -H 'If-Match: "2"' -d '{"priority":"high"}'

# Move it. There is no endpoint that writes status directly.
curl -s -X POST "http://localhost:8000/api/v1/items/$ID/transitions" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -H 'If-Match: "3"' -d '{"to":"in_progress"}'

# List, filtered and paged on the server.
curl -s "http://localhost:8000/api/v1/teams/<TEAM_ID>/items?assignee=none&priority=urgent&limit=5" \
  -H "Authorization: Bearer $TOKEN"
```

### 7. Run the API

```bash
cd backend
uv run uvicorn app.main:app --reload --port 8000
```

or `make dev-api`.

| URL | Purpose |
| --- | --- |
| http://localhost:8000/healthz | Liveness |
| http://localhost:8000/readyz | Readiness (performs a database round trip) |
| http://localhost:8000/docs | OpenAPI UI |
| http://localhost:8000/api/v1/openapi.json | OpenAPI schema |

## Tests and quality checks

```bash
cd backend
uv run pytest -q             # integration tests against real PostgreSQL
uv run ruff format --check . # formatting
uv run ruff check .          # lint
uv run mypy .                # strict static types
```

From the repository root: `make test`, `make lint`, `make typecheck`, or `make check` for
all of them.

The suite runs against `TEST_DATABASE_URL` and applies migrations to head itself, so no
manual preparation is needed beyond creating the empty `newtonite_test` database.

## Repository structure

```
backend/
  alembic/            migration environment and revisions
  app/
    api/              FastAPI routers (thin HTTP layer) and dependencies
    models/           SQLAlchemy ORM models and domain enums
    policy/           the role matrix, the authenticated principal and the workflow
    schemas/          request and response models, separate from the ORM
    security/         password hashing and session tokens
    services/         business rules, testable without a request
    config.py         environment-driven settings
    db.py             async engine and session management
    errors.py         error types and the HTTP error envelope
    main.py           application factory
  scripts/            the demo seed, maintenance commands and the query-plan tool
  tests/              integration tests against PostgreSQL
docker/
  initdb/             creates the test database on first Compose start
docs/                 architecture, API, decisions, status and review material
frontend/             React application (added in a later phase)
```

## Documentation

| Document | Contents |
| --- | --- |
| [`docs/API.md`](docs/API.md) | Endpoints, authentication, error codes and worked examples |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Layers, request flow and where each rule lives |
| [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) | Entities, integrity rules, indexes and measured query plans |
| [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md) | Phase-by-phase status with verification evidence |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | Configuration, migrations, diagnosis, safe resets |
| [`ENGINEERING_DECISIONS.md`](ENGINEERING_DECISIONS.md) | Decisions with context, options, trade-offs |
| [`docs/KNOWN_LIMITATIONS.md`](docs/KNOWN_LIMITATIONS.md) | What is not built yet, and why |

## Security notes

- No secrets are committed. `.env.example` contains placeholders only.
- Passwords are hashed with Argon2id. Session tokens are 256 bits of randomness and are
  stored **only** as a SHA-256 digest, so a database disclosure does not yield usable
  sessions. A fast digest is used here deliberately, because a random token cannot be brute
  forced; passwords get a memory-hard KDF because they can be.
- Sessions have both an absolute lifetime (12 h) and an idle lifetime (2 h), and logout
  revokes immediately. Expiry is evaluated with the database clock, not the application's.
- Authorization is enforced on the server, per team, from stored memberships. A `team_id`,
  `role` or `user_id` in a request body is never trusted — unknown fields are rejected.
- A resource in a team the caller does not belong to returns `404`, not `403`, so responses
  cannot be used to confirm that another team's data exists.
- Not yet built and documented as such: login rate limiting, password reset, and MFA. See
  [`docs/KNOWN_LIMITATIONS.md`](docs/KNOWN_LIMITATIONS.md) for the residual risk.

Deployment caveats, including the recommended `Content-Security-Policy`, are documented
in [`docs/OPERATIONS.md`](docs/OPERATIONS.md).
