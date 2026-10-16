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

The transactional coupling itself is verified by `tests/test_activity_atomicity.py`, which
installs a trigger that makes *every* insert into `activity_events` fail and then attempts an
edit, a claim and a transition. In each case the response is a generic `500`, and the item is
byte-identical to what it was before — no state change survived its missing history entry.
The test also checks that the failure body leaks no SQL, table name, trigger name or
traceback, and that removing the trigger restores normal behaviour, which shows the failures
came from the trigger rather than from something else.

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

## 9. Claiming is one conditional `UPDATE`, not a read-then-write

**Context.** Two people can claim the same unassigned item at the same moment. The brief
asks for at most one successful claimant. The obvious implementation is also the wrong one:
read the item, check it is unassigned, then write. Between the check and the write, another
request can do the same thing, and both succeed.

**Options considered.**

1. Read the row, check `assignee_id IS NULL` in application code, then `UPDATE`.
2. `SELECT … FOR UPDATE` to lock the row, then check and update.
3. A single `UPDATE … WHERE id = :id AND assignee_id IS NULL AND status = 'open'` whose
   affected-row count *is* the answer.
4. Compare-and-set on a version number, as `PATCH` uses.

**Choice.** Option 3, plus a follow-up read to explain a loss.

**Why.** Option 1 is the bug. Option 2 works but serializes every claim attempt on a row
lock and still needs the same predicate written twice. Option 3 makes the eligibility
condition and the assignment the same statement, so PostgreSQL decides the race inside the
row lock it already holds for the update — there is no window and no separate policy to keep
in sync with the write. Zero rows affected means "you lost", and the follow-up read is used
only to choose a *specific* reason (`item_already_claimed` versus `invalid_transition`)
rather than to make the decision.

Option 4 was rejected for a subtle reason: a version precondition is the *weaker* guard
here. Two claims can carry the same valid version and both pass it; the version says nothing
about who the assignee is. Using `If-Match` for claiming would look rigorous while being
less correct, so the endpoint deliberately asks for no `If-Match` at all.

**Trade-offs.**

- Accepted: a lost race needs a second query to produce a helpful error. That query runs
  only on the losing path, and it cannot change the outcome.
- Accepted: the eligibility rule lives in the SQL predicate as well as in the workflow
  table. They are asserted against each other by tests (`invalid_transition` for a
  non-open item is produced from the same rule set).
- Rejected: pessimistic locking, which would make concurrent claims queue rather than
  letting the database reject the loser immediately.

**Consequences and verification.** `tests/test_items_concurrency.py` fires two, then ten,
genuinely concurrent HTTP requests at one item — real sessions on their own connections, so
the race is real rather than simulated. Exactly one `200` and the rest `409`, one assignee
in the database, and exactly one `claimed` event.

The test's ability to detect the bug was checked by *breaking the implementation on
purpose*: `claim_item` was temporarily rewritten as the read-then-write version with the race
window widened. Both concurrency tests then failed (ten of ten claims succeeded). The
original file was restored and verified byte-identical.

---

## 10. Idempotent creation with a required `Idempotency-Key`

**Context.** A client that times out cannot tell whether its create succeeded. Retrying is
the right thing to do, and must not produce a second item. Double-clicking is the same
problem in a smaller package.

**Options considered.**

1. Nothing — the client accepts possible duplicates.
2. Deduplicate on a content hash of the request.
3. A client-supplied `Idempotency-Key`, unique per caller and operation.
4. A server-assigned request id returned to the client after the fact.

**Choice.** Option 3. The key is **required**, and scoped to `(actor_id, operation, key)`.

**Why.** Option 2 cannot distinguish "the same request sent twice" from "two genuinely
identical items", which is a real thing in this domain (two identical refund requests for
the same customer are plausible). Option 4 cannot help the retry that never received the
first response, which is the case that matters.

Scoping by actor and operation means two people who happen to choose the same key do not
collide, and the same key used for a different operation is not a conflict. A fingerprint of
the canonical JSON body (`sort_keys`, compact separators) is stored alongside the key, so
reusing a key with a *different* payload is `409 idempotency_key_reuse` rather than
silently returning an unrelated item.

**How the race is closed.** The key row and the work item are inserted in **one
transaction**, with `UNIQUE (actor_id, operation, key)`. A concurrent duplicate does not take
a lock and wait politely — it blocks on the unique index until the first transaction
commits or rolls back, then sees either a committed key (and replays) or nothing (and
proceeds). The insert is attempted inside a savepoint (`session.begin_nested()`) so the
`IntegrityError` does not poison the surrounding transaction.

**Why the key is required rather than optional.** If it were optional, every client that
forgot it would silently be a client without retry safety, and the API could not tell a
retry from a genuine second item. Refusing with `400 idempotency_key_required` makes the
choice explicit and impossible to overlook by accident.

**Trade-offs.**

- Accepted: one extra row per create, and a table that needs periodic pruning (documented in
  `docs/OPERATIONS.md`; the rows are a retry window, not history).
- Accepted: the stored response body is a minimal provenance record rather than a full
  cached response — a replay returns a **fresh read** of the item, so it can never return a
  stale representation.
- Accepted: a client that reuses one key for two different items gets a `409`. That is the
  intended contract, and the message says how to fix it ("use a new key").
- Rejected: a time-window-based deduplication, which needs a heuristic and still has an
  arbitrary boundary.

**Consequences and verification.** `tests/test_items_create.py` (17 tests) and
`tests/test_items_concurrency.py`: two concurrent identical creates produce one `201` and one
`200` with the **same id** and exactly one row; a mismatched payload is `409`; a missing key
is `400`; a key that was rejected *before* anything was written can be reused successfully,
so a failed attempt does not burn the key. As with claiming, the test was checked against a
deliberately broken implementation (duplicate keys no longer replayed) and failed as
expected.

---

## 11. Preconditions are required (428) and stale writes are refused (412)

**Context.** Two people edit the same item. Without a guard, the second save silently
overwrites the first person's change, and the first person's edit disappears with no
explanation. The brief asks explicitly for stale updates to be rejected rather than silently
applied.

**Options considered.**

1. Last write wins, with no guard.
2. Compare the incoming `updated_at` with the stored one.
3. Require `If-Match` with the item's version; refuse a mismatch with `412`.
4. Make the precondition optional, and only check it when the client happens to send one.

**Choice.** Option 3, on every update and every transition. A missing header is
`428 precondition_required`; a mismatch is `412 stale_version`.

**Why.** Option 1 is the failure the brief names. Option 2 compares a *timestamp*, which is
both coarser and ambiguous when two writes land in the same millisecond; a monotonic integer
version is unambiguous. Option 4 was rejected because a guard that can be omitted is a guard
that will be omitted by exactly the client that most needs it — and an API cannot then claim
to prevent lost updates. Requiring `If-Match` keeps the claim honest: an edit that did not
demonstrate it had seen the current version is not applied at all.

The comparison and `version = version + 1` happen in the **same `UPDATE`**, so the check
cannot be separated from the write by another transaction.

**Trade-offs.**

- Accepted: it is more work for a client — it must read first, keep the `ETag`, and handle a
  `412` by refreshing. That is the point, and `details.current` carries the current
  representation so the recovery step needs no extra round trip.
- Accepted: `428` is a less familiar status than `400`. It is the correct one (RFC 6585) and
  the message explains what to send.
- Accepted: a request that changes nothing returns `200` without bumping the version or
  writing history, so an idempotent-feeling save from a form does not fill the timeline.

**Consequences and verification.** `tests/test_items_update.py` (15) and
`tests/test_transitions.py` (16) cover: `428` with no header; `412` with `details.current`
for a stale one; a successful edit bumping the version and the `ETag`; per-field events; a
no-op writing nothing; malformed `If-Match` values (`banana`, `"nope"`, `0`, `-3`) rejected as
`422`; and two concurrent editors both sending `If-Match: "1"`, where exactly one `200` and
one `412` result and only one history entry is written.

---

## 12. The workflow is a server-side state machine, and status is not a patchable field

**Context.** Items move through `open → in_progress → awaiting_approval → resolved → closed`,
with approval and closure reserved to leads. A generic `PATCH` that accepted `status` would
be a way around those rules: anyone who can edit an item could set it to `closed`.

**Options considered.**

1. Put `status` in the update payload and validate the value.
2. A dedicated transition endpoint that applies the state machine.
3. Derive status from other fields (for example, "closed when `closed_at` is set").
4. A workflow engine or state-machine library.

**Choice.** Option 2: `POST /items/{id}/transitions` with `{"to": …}`, and `status` is
**not** an accepted field on `PATCH`. Each transition declares its origin, destination,
required permission, and whether an assignee is required.

**Why.** Option 1 makes it impossible to say "who may do this" per edge, because the
permission depends on the *transition*, not on the field: a member may move work forward but
may not approve it. Option 3 spreads the state machine across every writer. Option 4 is
infrastructure for a state machine with fewer than ten edges.

Because the rules are one table (`app/policy/workflow.py`), `available_transitions(status,
role, has_assignee)` is computed from the same source that validates the request, so the
statuses offered in an item response and the transitions the server accepts cannot disagree.
Each edge also records a specific event type — `approved`, `rejected`, `status_changed` —
rather than an anonymous "status changed", so a timeline can be read without decoding
`from_value` and `to_value`.

**Rejection is a first-class outcome.** `awaiting_approval → in_progress` is legal for a lead
and is recorded as `rejected` with the reviewer's note. Sending work back is not a delete, and
the assignee keeps the item — a rejection returns the work, it does not orphan it.

**Trade-offs.**

- Accepted: two endpoints (`PATCH` and `transitions`) instead of one, because the rules
  genuinely differ. `PATCH` refuses `status`, so the split cannot be bypassed.
- Accepted: `closed` is terminal. Reopening needs decisions about who may do it, whether the
  version resets, and what history it writes; guessing would be worse than deferring, so the
  API refuses and a closed item's `available_transitions` is empty. Recorded in
  `docs/KNOWN_LIMITATIONS.md`.
- Accepted: any member may advance any item in their team, not only their own. That is what
  lets a small team cover for one another, and changing *ownership* remains restricted. The
  behaviour is asserted in a test so it is a decision rather than an accident.

**Consequences and verification.** `tests/test_transitions.py`: the whole journey
`open → closed` with each step attributed to the right actor in the history; a member refused
at approval and at closure (`403`); `assignee_required` when starting unowned work; an illegal
jump naming the reachable statuses; `closed` terminal for all four targets; stale `412`;
missing `428`; cross-team `404`; an unknown target `422`; and two leads approving
concurrently, where one wins and one gets `412` with a single `approved` event.

---

## 13. Keyset pagination, with a total order

**Context.** Team item lists must be filtered, searched and paged on the server, and the
brief asks that the browser never be sent the whole dataset.

**Options considered.**

1. `OFFSET`/`LIMIT` pagination.
2. Keyset (cursor) pagination on `(created_at DESC, id DESC)`.
3. Return everything and let the client page.

**Choice.** Option 2, with `limit` bounded to 1–100 (default 25) and a `next_cursor` string.

**Why.** Option 1 has a correctness problem, not just a performance one: if an item is
created between two page requests, every later page shifts by one and the reader sees an item
twice and misses another. Keyset paging resumes from a *position* rather than a row count, so
that cannot happen. The tie-breaker on `id` makes the ordering *total* — without it, two items
created in the same millisecond could be ordered differently per query, and a page could
repeat or skip rows even with no concurrent writes. Option 3 fails the requirement outright.

The cursor is base64url of `(created_at, id)` and is deliberately **opaque but unsigned**: it
carries no secret and no permission decision, so nothing is gained by signing it and nothing
is lost by a client tampering with it. Authorization is re-derived from the row on every
request, and a malformed cursor is a `422` rather than a silent restart from the beginning
(which would quietly repeat a page).

**Trade-offs.**

- Accepted: the cursor cannot jump to an arbitrary page number, and a client that loses its
  cursor starts again. Suits an operational list; a "page 7 of 20" control is not offered.
- Accepted: the expanded `OR`/`AND` cursor predicate is applied as a filter after the index
  seek, so a deeper page walks past the index entries it skips rather than seeking directly
  to its position. Measured and documented in `docs/DATA_MODEL.md`; it remains strictly
  better than `OFFSET`, which would additionally read those rows' heap tuples.
- Rejected: a total row count alongside each page, which costs a full count on every request
  for a number nobody acts on. `has_more` answers the question a client actually asks.

**Consequences and verification.** `tests/test_items_list.py` (15 tests): filters combine and
are applied in the database; a full walk visits every item exactly once; **items created
between page 1 and page 2 neither duplicate nor skip** (the property `OFFSET` cannot offer);
twelve items sharing one `created_at` still page correctly through the `id` tie-breaker;
`limit` `0`, `101` and `-1` are `422` while `100` is accepted; malformed cursors are `422`;
search matches title and description and cannot cross a team boundary; and the list payload
omits `description`, which only the detail view carries.

---

## 14. Index and query decisions come from measurements, not taste

**Context.** The brief asks for indexes based on actual query patterns and forbids claiming
scale that has not been measured. Adding an index is easy and adding the wrong ones is
worse than adding none: they cost write throughput and can be ignored by the planner anyway.

**Choice.** Three practices, applied to the work-item API:

1. The list query is built by a **pure function** (`list_statement`), separate from the code
   that executes it, so the exact statement the API runs can be compiled and `EXPLAIN`ed.
2. `scripts/explain_lists.py` prints the plans for the real query shapes against the seeded
   database. The numbers in `docs/DATA_MODEL.md` come from that script, not from estimates.
3. An index is added only where a measurement shows a plan that would not improve with
   scale, and the measurement is recorded next to the index.

**What the measurements actually changed.**

- **An index was added, and it was needed.** The default team view (no `status` filter) was a
  sequential scan of the whole table plus a top-N sort: 3.19 ms against 0.32 ms for the same
  list narrowed by status. The cause was structural — `ix_work_items_team_status_created`
  leads with `team_id` but its next column is `status`, so an unfiltered list has nothing to
  walk in order and would have stayed a full scan at any size. Revision `0002` adds
  `ix_work_items_team_created`. Measured after: **0.53 ms**.
- **A silent 20× penalty was found and removed.** The text-search configuration was passed to
  `websearch_to_tsquery` as a *bound parameter*. The plan still showed a `Bitmap Index Scan`,
  which looks like success, but the index condition was the function call itself: PostgreSQL
  could not evaluate the tsquery once and instead re-evaluated it **for every index entry**.
  Measured under a forced generic plan: **5.47 ms against 0.245 ms** with the same plan
  shape. `SEARCH_CONFIG` is now a literal `'english'::regconfig` — a fixed internal constant,
  never user input — which lets the tsquery be folded once. The search term itself remains a
  bound parameter.
- **A partial index was justified rather than assumed.** The unassigned-urgent partial index
  is used for the shape it was built for (0.31 ms) and is *not* used when the same queue is
  asked without constraining `status` (0.82 ms via the general team index). Both rows are in
  `docs/DATA_MODEL.md`, because reporting only the flattering one would be misleading.
- **A previously wrong claim was corrected.** An earlier revision of `docs/DATA_MODEL.md`
  stated that full-text search did not use its GIN index at the seeded size. Re-measuring
  against the shape the API actually issues showed that it does. The document was corrected
  to match the plan rather than left alone for consistency.

**Trade-offs.**

- Accepted: one more index to maintain on write, justified by a measured 6× difference on the
  most common read.
- Accepted: the measurement script is committed even though it is not part of the
  application, because an unreproducible number is not evidence.
- Rejected: speculative indexes on every filterable column. `status`, `priority`, `type` and
  `assignee_id` are all queried, but only the combinations with a measured plan got an index;
  the rest are served by filtering rows the existing indexes already narrowed.
- No claim is made about the brief's tens-of-thousands-of-items target. Nothing has been
  load-tested, and `docs/KNOWN_LIMITATIONS.md` says so.

**Consequences and verification.** `uv run python -m scripts.explain_lists` reproduces every
number in `docs/DATA_MODEL.md`. `tests/test_index_inventory.py` asserts each index exists with
the intended definition, including the one added in `0002`.

---

## Decisions taken, not yet implemented

Listed so the direction is explicit without implying that the behaviour exists.

| Decision | Direction | Implemented in |
| --- | --- | --- |
| Async notifications | PostgreSQL-backed transactional outbox with a `SKIP LOCKED` worker, bounded backoff, idempotent handling. Conditional on the core slice passing its gates first | Phase 7, not yet started |
| Deliberately not built | Email, attachments, SSO, WebSockets, mobile, comment editing, reopen-after-close, rate limiting | See `docs/KNOWN_LIMITATIONS.md` |

