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

## What works today (Phase 2 — domain, integrity and demo data)

| Capability | Status |
| --- | --- |
| Backend project, pinned dependencies (`uv`, Python 3.12) | Working |
| Async SQLAlchemy + PostgreSQL wiring, bounded connection pool | Working |
| Alembic migration workflow with a model/migration drift check | Working |
| Schema for users, teams, memberships, work items, comments, activity events, sessions, idempotency keys | Working |
| Database-enforced invariants (team/assignee consistency, append-only history, value sets) | Working, tested |
| Deterministic, idempotent demo seed (~3 000 work items, 12 users, 4 teams) | Working |
| Index inventory matched to query patterns, with measured plans | Working — see [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) |
| Liveness (`/healthz`) and readiness (`/readyz`) endpoints | Working |
| Backend test suite against real PostgreSQL | Working (33 tests) |
| Docker Compose PostgreSQL for reviewers | Working |
| CI: formatting, lint, types, migrate, seed, tests | Working |
| Authentication, work-item API, workflow, frontend | Not yet — see [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md) |

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

> The login endpoint arrives with the authentication phase, so these accounts are in the
> database but not yet reachable over HTTP. See
> [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md).

### 6. Run the API

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
    api/              FastAPI routers (thin HTTP layer)
    models/           SQLAlchemy ORM models and domain enums
    config.py         environment-driven settings
    db.py             async engine and session management
    main.py           application factory
  tests/              integration tests against PostgreSQL
docker/
  initdb/             creates the test database on first Compose start
docs/                 architecture, decisions, status and review material
frontend/             React application (added in a later phase)
```

## Documentation

| Document | Contents |
| --- | --- |
| [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) | Entities, integrity rules, indexes and measured query plans |
| [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md) | Phase-by-phase status with verification evidence |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | Configuration, migrations, diagnosis, safe resets |
| [`ENGINEERING_DECISIONS.md`](ENGINEERING_DECISIONS.md) | Decisions with context, options, trade-offs |
| [`docs/KNOWN_LIMITATIONS.md`](docs/KNOWN_LIMITATIONS.md) | What is not built yet, and why |

## Security notes

- No secrets are committed. `.env.example` contains placeholders only.
- Passwords are hashed with Argon2id and `auth_sessions` stores only a SHA-256 digest of
  a session token, never the token itself. The schema enforces this today; the code that
  uses it lands with the authentication phase — see
  [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md).
- Authorization is enforced on the server; the UI never decides permissions.

Deployment caveats, including the recommended `Content-Security-Policy`, are documented
in [`docs/OPERATIONS.md`](docs/OPERATIONS.md).
