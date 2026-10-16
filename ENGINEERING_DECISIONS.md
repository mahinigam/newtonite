# Engineering Decisions

Decisions are recorded as they are implemented, in the format the brief asks for:
context, options considered, the choice, why, the trade-offs, and how the decision is
verified. Anything not yet implemented is listed separately at the end so this document
never describes hypothetical behaviour as if it existed.

---

## 1. Modular monolith rather than services

**Context.** The brief describes one product with a small, well-understood domain
(users, teams, work items, comments, activity). The assessment values a smaller system
with well-considered behaviour over a large one with incomplete features, and the system
must be modifiable live during review.

**Options considered.**
1. Modular monolith — one FastAPI process with explicit internal boundaries.
2. Separately deployed services for work items, activity and notifications.
3. A serverless function per endpoint.

**Choice.** A modular monolith with layered boundaries: `api/` (HTTP) → `services/`
(business rules) → `models/` (persistence), with `policy/` holding authorization rules.

**Why.** All the interesting invariants in this domain — "one claimant", "history matches
state", "assignee belongs to the team" — are *cross-cutting* and are easiest to keep
correct when they live in one process and one database transaction. Splitting them across
services would turn a single atomic write into a distributed transaction, weakening
exactly the guarantees the brief asks for. There is also no independent scaling need: the
load is one kind of request against one database.

**Trade-offs.**
- Rejected because of the trade-off: independent deployability and per-service scaling
  would be lost. Neither is needed at this scale, and both would cost transactional
  integrity.
- Accepted cost: module boundaries are conventions rather than network boundaries, so they
  must be enforced by review and by keeping routers thin.

**Consequences and verification.** Routers only translate HTTP ↔ service calls. The
layering is visible in `backend/app/`; `docs/ARCHITECTURE.md` explains the flow.

---

## 2. Relational current state plus append-only activity history, not event sourcing

**Context.** Users must be able to see "what happened previously" and "who is responsible
now". Both a current-state view and a history are required, and history must never
disagree with the current state.

**Options considered.**
1. Current-state tables plus an append-only `activity_events` log, written in the same
   transaction as the change.
2. Full event sourcing — events are the source of truth, current state is a projection.
3. Current state only, with history derived from an audit log written asynchronously.

**Choice.** Option 1.

**Why.** Event sourcing is a heavy commitment: every read needs a projection, schema
evolution needs event upcasting, and the reviewer would have to reason about eventual
consistency everywhere. Writing the event in the same transaction as the state change
gives the property that actually matters — *the history is never inconsistent with the
item* — without that cost. Option 3 was rejected outright because a failure between the
two writes would silently produce a state change with no explanation.

**Trade-offs.**
- Rejected: event sourcing's ability to rebuild arbitrary past states and to add new
  read models retroactively.
- Accepted: history is descriptive rather than authoritative, so it cannot be replayed to
  reconstruct state. Queries always read the current-state tables.

**Consequences and verification.** The append-only property is enforced twice: there is no
application code path that updates or deletes an event, and a database trigger refuses
`UPDATE`/`DELETE` on `activity_events` (`alembic/versions/0001_initial_schema.py`).
Verified by `tests/test_schema_constraints.py::test_activity_history_is_append_only`.
The transactional coupling itself is verified in Phase 4
(`test_activity_atomicity`), and is not yet implemented.

---

## 3. Data integrity enforced by PostgreSQL constraints, not only by application code

**Context.** Two rules are easy to state and easy to violate accidentally: "an assignee
must be a member of the item's team", and "audit history is immutable". Both were
explicitly called out in the project guidance.

**Options considered.**
1. Application-level validation only.
2. Database constraints only.
3. Both, with the database as the backstop.

**Choice.** Option 3, and specifically:

- a composite foreign key `work_items(team_id, assignee_id) → memberships(team_id, user_id)`,
  resting on a unique constraint on those membership columns;
- a trigger rejecting `UPDATE`/`DELETE` on `activity_events`;
- `CHECK` constraints for status, type, priority, role, version, title and body lengths.

**Why.** Application validation is the right place for good error messages, but it is the
wrong place to rely on for invariants that must never break. A composite foreign key
expresses "assignee must belong to the team" as a single declarative rule that no code
path — including a future one written by someone else — can bypass. PostgreSQL uses
MATCH SIMPLE semantics, so a null assignee skips the check, which is precisely the
behaviour wanted for unassigned items.

**Trade-offs.**
- Rejected: flexibility to assign cross-team items. That is a product non-goal here.
- Accepted: the composite key requires managing two membership uniqueness/index concepts,
  which is documented on the model to avoid confusion later.
- Accepted: enum-like values are stored as text with `CHECK` constraints rather than as
  PostgreSQL `ENUM` types, so adding a status is an additive migration rather than a type
  rewrite. The trade-off is that the value set lives in two places (Python enum and SQL
  check), which the drift check and constraint tests partly cover.

**Consequences and verification.**
`tests/test_schema_constraints.py` proves each rule with raw SQL that bypasses the
application entirely: cross-team assignee rejected, same-team accepted, unassigned
accepted, history immutable.

---

## 4. Schema as reviewed code, with a drift check

**Context.** The project must be reproducible for a reviewer, and migrations are the only
acceptable way to change the schema.

**Options considered.**
1. `Base.metadata.create_all()` at startup.
2. Alembic with `--autogenerate`, committed and reviewed.
3. Hand-written SQL migration scripts.

**Choice.** Option 2, with revisions read and corrected by hand after generation.

**Why.** Option 1 cannot evolve a database that already has data, and hides changes from
review. Hand-written SQL is more control than this project needs and drifts from the
models. Autogenerate is fast and accurate for tables, columns, indexes and foreign keys;
manual review is still required for anything it cannot express — which is exactly what
happened with the append-only trigger in revision `0001`.

**Trade-offs.**
- Accepted: `alembic check` does not compare `CHECK` constraint bodies, so a check
  constraint changed in a model but not in a migration would not be caught by the drift
  test. Mitigated by the explicit constraint tests in `tests/test_schema_constraints.py`,
  and stated here rather than glossed over.

**Consequences and verification.** `tests/test_migrations.py::test_models_and_migrations_do_not_drift`
runs `alembic check` as part of the suite, so drift fails CI. Round-tripping
(`downgrade base` → `upgrade head`) was verified on both local databases.

---

## 5. Asynchronous stack with a bounded connection pool

**Context.** The brief describes hundreds to a few thousand simultaneous users. FastAPI
supports both synchronous endpoints (run in a worker thread pool) and asynchronous ones.

**Options considered.**
1. Async SQLAlchemy 2.x with asyncpg, async endpoints.
2. Synchronous SQLAlchemy with psycopg3, endpoints run in FastAPI's thread pool.

**Choice.** Option 1.

**Why.** A synchronous endpoint occupies a thread for the whole request, so concurrency is
capped by the thread pool regardless of how little work the request does. With async
endpoints, the limiting factor becomes the database pool, which is sized deliberately
(`pool_size=10`, `max_overflow=20`) instead of implicitly by a thread count. It also makes
the concurrency tests natural: two concurrent requests are two coroutines with two
independent sessions, which is exactly how the claim and idempotency races will be
exercised.

**Trade-offs.**
- Rejected: synchronous code is easier to debug (ordinary tracebacks, no accidental
  blocking). Chosen anyway because the concurrency behaviour is the point of the exercise,
  and correctness here comes from SQL conditions rather than from the driver.
- Accepted: every database call must be awaited; a blocking call inside a coroutine would
  stall the event loop. Strict typing plus a narrow repository/service layer keep this
  contained.
- Accepted: a single process is still one failure domain. Horizontal scaling needs a
  shared pool or an external pooler, which is a stated evolution path rather than a
  current claim.

**Consequences and verification.** The pool configuration is explicit in
`backend/app/db.py`. Tests use a `NullPool` engine so independent concurrent sessions
cannot be handed a connection bound to a different event loop.

---

## 6. Database-backed opaque sessions rather than JWTs

**Context.** The API needs to identify a caller on every request, must be able to end a
session immediately, and must not put a credential in the hands of an attacker who reads
the database.

**Options considered.**

1. Signed stateless JWT in a cookie or `Authorization` header.
2. Opaque random token with the server storing a hash of it.
3. Server-side session with a session id in a cookie.

**Choice.** Option 2: a 256-bit token from `secrets.token_urlsafe(32)`, stored only as its
SHA-256 digest, in an `auth_sessions` row with `expires_at`, `last_used_at` and
`revoked_at`.

**Why.** The brief asks for expiration *and* revocation. A JWT is valid until it expires —
revoking one requires either a denylist (which reintroduces the database lookup the JWT was
meant to avoid) or a key rotation that logs everybody out. Since every protected request
already touches the database, a stateless token buys nothing and costs the ability to sign
someone out immediately. Storing only the digest means a database disclosure exposes
expired hashes rather than live credentials. A hand-rolled cookie session adds CSRF
concerns and a second credential concept for no benefit.

**Trade-offs.**

- Accepted: every authenticated request costs one indexed lookup on `token_hash`. Measured
  as a single index probe, which is the same order as the work the request does anyway.
- Accepted: no cross-service portability. There is one service.
- Rejected: the ability to verify a token without the database.

**Sub-decision: the digest is fast (SHA-256), the password hash is slow (Argon2id).** They
have opposite threat models. A 256-bit random token cannot be brute-forced, so a memory-hard
KDF would only add latency to every request. A human password can be guessed offline, so it
gets Argon2id. Both are in `app/security/`.

**Sub-decision: two clocks, one authority.** Absolute and idle expiry are both evaluated with
PostgreSQL's `now()`, never the application's. With more than one process, an application-side
comparison would grant a session a different lifetime depending on which process answered.
`login` also derives `expires_at` from the database clock, so issuance and validation agree.

**Sub-decision: `last_used_at` is refreshed at most once a minute.** The update is conditional
in SQL (`... WHERE last_used_at < now() - interval '60 seconds'`), so reads do not become
writes and two concurrent requests cannot both decide to update the row. The cost is that the
idle window can be enforced up to a minute late; that is recorded in
`docs/KNOWN_LIMITATIONS.md` rather than left implicit.

**Consequences and verification.** `tests/test_auth.py` covers: unknown email and wrong
password returning byte-identical responses; only a digest stored (and the raw token appearing
in no column); missing, malformed and foreign-scheme headers; unknown and revoked tokens;
absolute expiry; idle expiry; activity refreshing the idle window; two logins producing
independent sessions; and an expired token not being resurrected by signing in again.

---

## 7. One error envelope, and 404 rather than 403 across a team boundary

**Context.** Clients need to distinguish "sign in again", "you may not do that", "that is
gone", "somebody changed this" and "your input is wrong" — without learning anything about
resources they cannot see.

**Options considered.**

1. FastAPI's defaults (`{"detail": ...}`, plus a plain-text 500).
2. A single envelope for every failure, produced by exception handlers.
3. Per-route error shapes.

**Choice.** Option 2, with a fixed mapping:

| Status | Code | Meaning |
| --- | --- | --- |
| 401 | `not_authenticated`, `invalid_session`, `session_expired`, `invalid_credentials` | No usable session |
| 403 | `forbidden` | Inside the team, role does not permit it |
| 404 | `not_found` | Does not exist **or** is in a team you are not in |
| 409 | `invalid_transition`, `item_already_claimed`, `idempotency_key_reuse`, `last_admin`, … | Conflicts with current state |
| 412 | `stale_version` | Your `If-Match` version is out of date |
| 422 | `validation_failed` | Request body rejected |

**Why.** `code` is what the frontend branches on; `message` is written to be shown to a user
without leaking anything. The 403/404 split carries the important policy: a caller **inside**
a team whose role is insufficient gets 403, because they already know the team exists, and the
message helps them understand. A caller outside the team gets 404, indistinguishable from a
nonexistent resource, so the API cannot be used to confirm that another team's work item
exists. `tests/test_authorization.py` asserts the two responses are byte-identical for a
foreign team and a random UUID.

**Trade-offs.**

- Accepted: a stricter envelope than FastAPI's default, so framework-level errors (unknown
  route, wrong method) need an adapter handler to avoid a second shape.
- Accepted: the 404 choice is mildly less helpful for a legitimate user who typed a stale
  team URL — the frontend compensates by only ever offering teams from `/auth/me`.
- 500 responses are always the same generic body; the detail goes to the log, never to the
  client.

**Consequences and verification.** `app/errors.py` defines the types and installs the
handlers; `tests/test_authorization.py` and `tests/test_auth.py` assert the status **and** the
`code` for every case, and that a refused write leaves the stored role unchanged.

---

## 8. Authorization as an explicit permission matrix

**Context.** Four roles, several protected actions, and a rule that the server decides — never
the UI, and never a value from the request.

**Options considered.**

1. Role comparisons inline in route handlers (`if role == "admin"`).
2. A declarative matrix: role → set of permissions, plus a principal that carries the caller's
   roles per team.
3. A policy DSL or external authorization service.

**Choice.** Option 2. `app/policy/roles.py` holds the only role logic; `Principal` carries the
roles, loaded from the database on every request; routes declare the permission they need as a
dependency.

**Why.** The matrix is a single reviewable table, so an auditor can answer "can a member close
the item?" by reading eight lines instead of searching for string comparisons. Making it a
dependency means a route cannot accidentally *perform* an action before checking it, and a
route that forgot its check is conspicuous. Option 3 is infrastructure with no payoff at four
roles.

**Trade-offs.**

- Accepted: for `PUT /teams/{id}/members/{user_id}`, that an admin may only set a role for
  somebody who already shares a team with them. It cannot enumerate strangers, and it means
  adding a brand-new person to a team is not possible through the API (seeded users only) —
  recorded in `docs/KNOWN_LIMITATIONS.md`.
- Accepted: admins currently have no capability a lead lacks except membership management.
  The distinction is real but narrow, and `admin` is the role reserved for it.

**Consequences and verification.** `tests/test_authorization.py`: a viewer cannot escalate
anyone's role, a member cannot promote themselves, a lead cannot manage members, an admin can,
and an admin of one team is answered with 404 for another team's roster. The permission table is
also what the frontend will read via `/auth/me` — advisory only, since every write is checked
again.

---

## Decisions taken, not yet implemented

Listed so the direction is explicit without implying that the behaviour exists.

| Decision | Direction | Implemented in |
| --- | --- | --- |
| Claim concurrency | Single conditional `UPDATE ... WHERE assignee_id IS NULL` that both checks eligibility and assigns; zero rows affected means somebody else won | Phase 4 |
| Stale writes | `If-Match` version precondition evaluated inside the update statement; failure returns `412` with the current representation | Phase 4 |
| Idempotent creation | Key row and work item written in one transaction, unique on `(actor_id, operation, key)`; concurrent duplicates block on the index and replay the stored outcome | Phase 4 |
| Search and pagination | Keyset (cursor) pagination with a stable tie-breaker; PostgreSQL full-text search over a generated `tsvector` with a GIN index | Phase 4 |
| Async notifications | PostgreSQL-backed transactional outbox with a `SKIP LOCKED` worker, bounded backoff, idempotent handling. Conditional on the core slice passing first | Phase 7 |
| Deliberately not built | Email, attachments, SSO, WebSockets, mobile, comment editing, reopen-after-close, rate limiting | See `docs/KNOWN_LIMITATIONS.md` |
