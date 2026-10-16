# AGENTS.md — Newtonite Operations Under Pressure

## Mission

Build a small, reliable operational work-management application for the Newtonite Software Engineering Challenge. Prioritize correctness, security, understandable engineering decisions, and a reproducible reviewer experience over feature count or architectural novelty.

The assessment explicitly values meaningful behaviour beyond CRUD: concurrent usage, authorization, workflow enforcement, reliable history, stale updates, duplicate-request handling, server-side filtering/pagination, tests, and clear documentation.

## First: inspect before changing

- Read this file and inspect the repository structure, `README.md`, package manifests/lockfiles, migrations, tests, and environment examples before editing.
- Treat the codebase as it actually exists. The intended stack is React + TypeScript, FastAPI, SQLAlchemy, and PostgreSQL, but do not rewrite an existing coherent implementation just to match that preference.
- Discover and use the repository's existing commands and package manager. Do not invent test, lint, build, or migration commands; if a command is unavailable, say so.
- Check `git status` before work. Preserve the user's uncommitted changes. Never discard, reset, overwrite, or revert unrelated work.
- If requirements or current behaviour are ambiguous, inspect nearby code and tests first. Ask a focused question rather than guessing when a decision could change core domain behaviour.

## Architecture principles

- Prefer a modular monolith with clear boundaries over microservices. Do not add Redis, Kafka, a separate search engine, Kubernetes, WebSockets, or other infrastructure unless a concrete requirement justifies it.
- Keep HTTP handlers thin. Put business rules in testable application/service functions and database operations in a clear persistence layer.
- Keep request/response schemas separate from database models where the current stack supports that distinction.
- PostgreSQL is the authoritative source of truth. The browser must not make final decisions about permissions, workflow legality, or concurrent updates.
- Use explicit migrations for schema changes. Do not silently rely on runtime table creation in place of the migration workflow.
- Prefer straightforward, typed, readable code over generic frameworks, unnecessary abstraction, or clever one-liners.

## Domain model and invariants

The core domain contains users, teams, per-team memberships/roles, work items, comments, and an activity history. Idempotency records and a transactional outbox may be included when their behaviours are actually implemented.

Protect these invariants:

1. **Team boundary:** every item read, write, comment, activity request, claim, and transition must be authorized against the authenticated user's membership in that item's team. Never trust a client-provided team ID as proof of access.
2. **Role boundary:** role checks happen on the server for every protected action. Hiding or disabling a UI control is not authorization.
3. **Workflow boundary:** status changes go through explicit, server-enforced transition rules. Do not allow a generic update endpoint to bypass approval or closure rules.
4. **Concurrency boundary:** claim operations must be atomic. Updates based on stale versions must be rejected rather than silently overwriting newer data. Prefer conditional database updates or appropriate locking/transactions, not read-then-write checks vulnerable to races.
5. **History boundary:** every successful important mutation (at least status, assignee, priority, and other meaningful operational changes) writes an activity event in the same database transaction as the state change. If either write fails, both roll back.
6. **Idempotency boundary:** where idempotency is implemented, the key's scope, payload fingerprint, stored result, and uniqueness constraints must prevent duplicate logical operations under retries and concurrent requests. Reusing a key with a different payload must not silently return an unrelated result.
7. **Audit boundary:** activity events are append-only through normal application flows. Do not add ordinary edit/delete behaviour for historical events.
8. **Data integrity:** use foreign keys, unique constraints, NOT NULL constraints, and suitable check/composite constraints where they genuinely protect domain invariants. Validate that an assignee is eligible for the item's team.
9. **No silent destructive actions:** prefer closing/resolving an item to deleting it. Any delete/reopen behaviour needs an explicit authorization and history rule.

Follow the state machine and role matrix already documented in the project. If they have not yet been decided, propose them explicitly and update `ENGINEERING_DECISIONS.md`; do not scatter inconsistent rules across routes and components.

## API and error handling

- Validate all incoming data at the API boundary and return stable, non-sensitive error responses.
- Keep HTTP semantics consistent with the existing API. For conditional updates, use the current version check or ETag/`If-Match` design consistently; do not mix incompatible schemes without a reason.
- Use an explicit command/endpoint for important domain operations such as claiming and workflow transitions when that helps centralize business rules.
- Distinguish authentication, authorization, validation, not-found, conflict, stale-version, and unexpected-server errors. Never expose stack traces, SQL, secrets, or internal implementation details to clients.
- A mutation should return the authoritative server result. After a conflict or rejected optimistic update, refresh/reconcile client state rather than forcing stale local data onto the server.
- Never report an operation as successful before the server confirms it.

## Frontend principles

- Treat server state as authoritative. Use the project's existing server-state solution (TanStack Query if present) for fetching, caching, invalidation, and refetching.
- Always represent loading, empty, success, and error states clearly. Explain recoverable conflicts in user language and provide a way to refresh/review the latest item.
- Prefer server-side filtering and pagination. Never load the entire work-item or activity dataset to filter/sort it in the browser.
- Use optimistic updates selectively. For claim/approval/closure, prefer server-confirmed updates unless rollback and reconciliation are implemented and tested.
- Prioritize a clean, accessible, responsive, operational UI over decorative animation or complex styling.

## Performance and scalability

- Use bounded, server-side pagination with deterministic ordering and a tie-breaker. Apply filters and search in the database.
- Add indexes based on actual query patterns, especially team-scoped item lists and per-item activity history. Avoid speculative indexes on every field.
- Return only the fields needed by each view and paginate activity independently from item lists.
- Do not claim production-scale capacity without measurements. Use seed data and query plans to demonstrate sensible behaviour where practical.

## Testing expectations

Test the dangerous invariants, not just happy-path page rendering. At minimum, aim to cover the behaviours supported by the current implementation:

- two concurrent claims result in at most one successful claimant;
- stale-version updates are rejected and do not change persisted data;
- retrying creation with the same idempotency key creates at most one item, and mismatched payload reuse is rejected;
- unauthorized cross-team reads/writes and forbidden role actions are denied;
- invalid workflow transitions are rejected;
- a failed activity insert rolls back the corresponding item mutation;
- if a worker/outbox exists, duplicate processing is harmless and retry exhaustion is observable.

Prefer integration tests against PostgreSQL for transaction and concurrency guarantees. Do not substitute mocks or SQLite for a test that is intended to prove PostgreSQL locking/concurrency semantics. Keep tests deterministic and isolated; use separate database sessions/connections when testing races.

Never weaken, delete, skip, or rewrite an assertion merely to make a failing test pass. Change a test only when the intended requirement is demonstrably wrong or the test itself is defective, and explain that change.

## Security and configuration

- Do not commit secrets, real credentials, access tokens, private keys, or production data. Update `.env.example` with placeholders only.
- Store passwords using a suitable password-hashing algorithm; never store plaintext passwords.
- Validate and normalize untrusted input at the boundary. Avoid mass assignment: allow-list editable fields and keep `team_id`, `created_by`, `version`, audit fields, and protected workflow fields under server control.
- Check authorization before returning resource data, including comments and activity.
- Keep dependencies minimal and avoid running destructive commands or database resets against any environment that may contain user data.

## Scope discipline

- Deliver a complete, explainable vertical slice before optional breadth.
- Do not add email delivery, SSO, attachments, WebSockets, elaborate admin tooling, or external infrastructure unless the core system is already reliable and there is time to test the addition.
- Avoid speculative abstractions and unnecessary dependencies. Every added component should solve a stated problem and have a clear way to verify it.
- Keep `README.md`, `ENGINEERING_DECISIONS.md`, setup commands, seed/demo credentials, tests, and known limitations aligned with actual behaviour. Never document a feature or passing test that has not been verified.

## Working agreement for coding agents

1. State a brief plan for non-trivial changes.
2. Make the smallest coherent change that fixes the underlying problem.
3. Add/update a focused regression test for bug fixes and domain-rule changes.
4. Run the focused test first, then relevant broader tests and configured lint/type/build checks.
5. Inspect the diff for unrelated edits, accidental secrets, weakened tests, and inconsistent documentation.
6. Report exactly what changed, commands actually run, outcomes, and remaining uncertainties. Do not claim success for checks that were not run.
