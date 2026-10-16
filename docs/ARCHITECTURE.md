# Architecture

A modular monolith. One FastAPI process, one PostgreSQL database, and internal boundaries
that are enforced by review rather than by a network.

## Layers

```
HTTP request
    │
    ▼
app/api/            routers: parse input, call a service, choose a status code
    │  deps.py      authentication and the "authorize me against this team" dependency
    ▼
app/services/       business rules: login, sessions, teams, memberships
    │              (the place to look for "what is allowed", independent of HTTP)
    ▼
app/models/         SQLAlchemy ORM: tables, relationships, enum values
    │
    ▼
PostgreSQL          constraints, unique indexes, triggers, the authoritative clock
```

Two supporting modules sit across those layers:

| Module | Responsibility |
| --- | --- |
| `app/policy/` | The role matrix (`roles.py`) and the authenticated caller (`principal.py`). The only place a role is compared. |
| `app/errors.py` | The exception types and the single HTTP error envelope. |

The direction of every arrow is one-way. Routers import services; services do not import
routers. Services are callable from a test, a script or a worker without constructing a
request, which is why the seed script can reuse none of them and the tests can call all of
them.

## Request flow for a protected write

`PUT /api/v1/teams/{team_id}/members/{user_id}` is a representative example.

1. **Transport and shape.** FastAPI validates the path UUIDs and the body against
   `SetRoleRequest`. An unknown field is a `422`, because request models set
   `extra="forbid"` — that is the guard against mass assignment.
2. **Authentication** (`Depends(get_principal)`). The `Authorization` header is parsed, the
   token is hashed, and `auth_sessions` is probed with all validity conditions in one
   `WHERE`: not revoked, not past `expires_at`, `last_used_at` inside the idle window, user
   active. All comparisons use PostgreSQL's `now()`. The caller's memberships are loaded and
   returned as a `Principal`.
3. **Authorization** (`require_team_permission(Permission.MANAGE_MEMBERS)`). The role for
   `{team_id}` is looked up in the principal's memberships. Not a member → `404`. Member but
   insufficient role → `403`.
4. **Business rules** (`services/memberships.set_role`). Is the change legal at all — would
   it remove the last admin, is the target somebody the caller may act on?
5. **Persistence.** A single statement, then commit. The response is built from what the
   database now holds, not from what was requested.

Every step can refuse the request on its own. Nothing decides permissions from the URL alone,
and nothing decides them from the body.

## Where each rule lives

| Rule | Enforced by | Why there |
| --- | --- | --- |
| Request shape, field allow-list | Pydantic schemas | Cheapest place; also produces the field-level error a client needs |
| Passwords, session tokens | `app/security/` | Pure functions, no database, easy to test in isolation |
| Session validity and expiry | SQL predicate in `services/auth.py` | One query, database clock, no read-then-check race |
| Team boundary | `Principal.require_team` | Membership is loaded once per request; a non-member is a 404 |
| Role permissions | `app/policy/roles.py` matrix | One reviewable table instead of comparisons scattered through routers |
| Duplicate memberships, invalid enum values, assignee-in-team | PostgreSQL constraints | Must hold even against a code path nobody has written yet |
| Append-only history | PostgreSQL trigger | Same reason: history is not allowed to have an "edit" path |
| Team keeps an admin | `services/memberships.py` | A policy about people, not about rows |

## Conventions

- **Routers are thin.** A handler validates, calls one service function, and returns a
  schema. If a handler needs a branch that is not about HTTP, the branch belongs in a
  service.
- **Schemas are not models.** Responses are declared explicitly, so a new column is never
  published by accident and a password hash cannot leak into a response.
- **Services raise `AppError` subclasses.** Those carry a status code and a stable `code`,
  but they are plain exceptions — no FastAPI types in the business layer.
- **Errors are registered once.** `register_exception_handlers` in `app/main.py` gives every
  failure, including framework-generated ones, the same JSON shape.
- **Nothing is trusted from the client except the fields it is allowed to set.** Team ids,
  roles, versions, `created_by` and audit fields are always derived on the server.

## Adding an endpoint

1. Add or reuse a schema in `app/schemas/`.
2. Put the rule in `app/services/`, not in the router.
3. Add a route in `app/api/v1/`, declaring `SessionDep` and either `PrincipalDep` or a
   `require_team_permission(...)` dependency.
4. Add a test that proves the refusal as well as the success.
5. Run `make check`.
