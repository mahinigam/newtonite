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
app/services/       business rules: login, sessions, teams, memberships, work items,
    │              activity, cursors (the place to look for "what is allowed",
    │              independent of HTTP)
    ▼
app/models/         SQLAlchemy ORM: tables, relationships, enum values
    │
    ▼
PostgreSQL          constraints, unique indexes, triggers, the authoritative clock
```

Two supporting modules sit across those layers:

| Module | Responsibility |
| --- | --- |
| `app/policy/` | The role matrix (`roles.py`), the authenticated caller (`principal.py`), and the work-item state machine (`workflow.py`). The only place a role is compared and the only place a transition is declared legal. |
| `app/errors.py` | The exception types and the single HTTP error envelope. |
| `app/api/preconditions.py` | `ETag` construction and `If-Match` parsing — the HTTP spelling of an optimistic-concurrency check, in one place. |

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

## Request flow for a work-item mutation

`PATCH /api/v1/items/{item_id}` is the harder case, because the team is not in the URL — the
item id is, and an id is what a client actually holds.

1. **Transport and shape.** FastAPI validates the body against `UpdateWorkItemRequest`, whose
   fields are all optional *and* tri-state: a field that is absent means "no opinion", while an
   explicit `null` means "clear it". `model_fields_set` is what distinguishes them, so an
   empty patch and a clearing patch are different requests.
2. **Precondition.** The `If-Match` header is parsed to a version integer. Absent → `428`.
   Unparseable → `422`. This happens before any database read, so a request that cannot be
   applied never touches the row.
3. **Authentication**, as above.
4. **Authorization from the row, not the request.** The service loads the item and derives the
   team **from the item's own `team_id`**, then checks membership against that. An item in
   another team is indistinguishable from a missing one: `404`, with no existence leak. The
   path never carries a team id for a write, so there is nothing to lie about.
5. **Business rules.** The workflow module is consulted for role-dependent field rules: moving
   work to or from *somebody else* needs `assign others`, while taking an unassigned item is a
   claim. `status` is not a field of this request at all.
6. **Persistence, once.** The version comparison and `version = version + 1` are in the same
   `UPDATE`. Field-change events are inserted in the same transaction through
   `services/activity.py`, which shares the session. Either the state change and its history
   both land, or neither does.
7. **The response is the row.** Read back after the update and serialized, with a fresh `ETag`.
   Nothing is echoed from the request.

`POST /items/{item_id}/claim` is the same path with one deliberate difference: it takes no
precondition, because its guard is the `UPDATE`'s own `assignee_id IS NULL` predicate rather
than a version. A version check would be weaker — it says nothing about who owns the item.

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
| Which transitions exist, and who may make them | `app/policy/workflow.py` table | The offered transitions and the refusal come from one source, so they cannot disagree |
| A stale update is refused | `If-Match` compared **inside** the `UPDATE` in `services/work_items.py` | A separate read-then-check is exactly the race being prevented |
| One winner per claim | SQL predicate `assignee_id IS NULL AND status = 'open'` inside the `UPDATE` | The database decides the race while it holds the row lock; no window exists |
| One item per retried create | Unique `(actor_id, operation, key)` on `idempotency_keys`, written in the item's transaction | A concurrent duplicate blocks on the index and then replays the stored outcome |
| History cannot diverge from state | One session, one transaction, `services/activity.py` | Proven by breaking the insert on purpose (`tests/test_activity_atomicity.py`) |

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
