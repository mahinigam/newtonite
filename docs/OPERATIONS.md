# Operations

Practical guidance for configuring, running, migrating and diagnosing the Newtonite
application. Commands are written for the repository root unless stated otherwise.

## Configuration

All settings come from environment variables, falling back to `.env` in the repository
root (or `backend/.env`). Copy `.env.example` to `.env` and adjust.

| Variable | Default | Purpose |
| --- | --- | --- |
| `ENVIRONMENT` | `development` | `development`, `test` or `production` |
| `DATABASE_URL` | local dev URL | Primary database used by the API, Alembic and scripts |
| `TEST_DATABASE_URL` | local test URL | Disposable database used **only** by the test suite |
| `SESSION_ABSOLUTE_TTL_HOURS` | `12` | Maximum lifetime of a login session |
| `SESSION_IDLE_TTL_HOURS` | `2` | Session is rejected after this much inactivity |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:5173` | Comma-separated browser origins allowed to call the API |
| `LOG_LEVEL` | `INFO` | Python logging level |

`.env` is gitignored. Never commit real credentials; `.env.example` holds placeholders
only.

## Databases

Two databases are used locally:

| Database | Used by | Notes |
| --- | --- | --- |
| `newtonite` | API, migrations, seed | Development data |
| `newtonite_test` | `pytest` | **Truncated between tests** — never point it at real data |

Either run a local PostgreSQL 16 (see `README.md`) or use
`docker compose up -d db`, which listens on host port **5433** so it cannot collide with
a PostgreSQL already on 5432. Remember to update `DATABASE_URL` and `TEST_DATABASE_URL`
if you use Compose, and to create `newtonite_test` there (the Compose init script does it
on first start).

## Migrations

```bash
cd backend
uv run alembic upgrade head        # apply everything
uv run alembic downgrade base      # revert every revision (destructive)
uv run alembic current             # show the applied revision
uv run alembic check               # fail if the models drifted from the migrations
```

`alembic check` is the guard against the most common migration mistake — editing a model
and forgetting the revision. It also runs as part of the test suite
(`tests/test_migrations.py`).

Migration revisions are hand-reviewed after `--autogenerate`. The
append-only trigger on `activity_events` is written manually because autogenerate cannot
express it.

## Diagnosing problems

| Symptom | Likely cause and fix |
| --- | --- |
| `connection refused` on startup or `/readyz` | PostgreSQL is not running, or `DATABASE_URL` is wrong (`brew services start postgresql@16`, or `docker compose up -d db`) |
| `password authentication failed for user "newtonite"` | The role has not been created, or the password in `.env` differs from the one the role was created with |
| `database "newtonite_test" does not exist` | Create it: `CREATE DATABASE newtonite_test OWNER newtonite;` |
| Tests wipe unexpected data | `TEST_DATABASE_URL` points at a database you care about. Stop, fix the URL, and restore from a backup |
| `alembic check` reports differences | A model changed without a migration (or vice versa). Create a revision and review it |
| `activity_events is append-only` | Something tried to `UPDATE`/`DELETE` history. That is the intended behaviour |
| Port 5432 already in use by Compose | Compose is mapped to 5433 precisely to avoid this; check `.env` |

Logs are written to standard error at the level set by `LOG_LEVEL`. Set `LOG_LEVEL=DEBUG`
(and `echo=True` in `create_database`) to see SQL.

## Safe local reset

> **These commands destroy data.** They are safe only for the local development and test
> databases created by the steps in `README.md`. Never run them against a database that
> holds data you want to keep, and never against a shared or production environment.

Reset the test database schema only (disposable by definition):

```bash
cd backend
uv run alembic downgrade base && uv run alembic upgrade head
```

Rebuild both local schemas from scratch:

```bash
psql -h 127.0.0.1 -U newtonite -d newtonite      -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'
psql -h 127.0.0.1 -U newtonite -d newtonite_test -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'
cd backend && uv run alembic upgrade head
```

Verify which database you are about to affect before running anything destructive:

```bash
psql "$DATABASE_URL_NO_SCHEME" -c 'SELECT current_database(), current_user;'
```

## Secret rotation and session invalidation

If a credential is ever exposed:

1. Rotate it in the source system. Deleting a file from the latest commit does **not**
   remove it from Git history — rotate first, then clean history if required.
2. Revoke affected sessions:
   ```sql
   UPDATE auth_sessions SET revoked_at = now() WHERE revoked_at IS NULL;
   ```
   Revocation takes effect on the next request; sessions are not cached in process.

## Deployment caveats

- The API is designed to run behind a TLS-terminating proxy. Session tokens are bearer
  credentials and must only travel over HTTPS.
- Restrict `CORS_ALLOWED_ORIGINS` to the real frontend origin. It defaults to a single
  localhost origin and does not use wildcards.
- Set a `Content-Security-Policy` such as
  `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self' <api-origin>; frame-ancestors 'none'; base-uri 'self'`.
  This is a deployment responsibility and is deliberately not enforced by the Vite dev
  server.
- Serve the API over HTTPS only, and keep `Authorization` headers out of proxy access
  logs.
- The readiness probe is `/readyz`; use `/healthz` for liveness so a database blip does
  not restart otherwise healthy processes.
