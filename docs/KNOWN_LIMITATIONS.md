# Known Limitations

This document states what the project does **not** do, why, and what it would take to
address each item. It is updated every phase and reflects the code as pushed.

Last updated: 2026-10-17 (end of Phase 1).

## Not built yet, by plan

These are features the project intends to build but has not reached. Until they exist,
nothing in the repository claims they work.

| Area | Status | Planned phase |
| --- | --- | --- |
| Authentication (login, sessions, logout) | Not implemented. The `auth_sessions` table exists. | 3 |
| Team and role authorization on endpoints | Not implemented | 3 |
| Work-item create/read/update endpoints | Not implemented | 4 |
| Atomic claim, stale-write rejection, idempotent creation | Not implemented | 4 |
| Workflow transitions and approvals | Not implemented | 5 |
| Comments and activity API | Not implemented | 5 |
| Frontend application | Not implemented | 6 |
| Seed data | Not implemented; `alembic upgrade head` is the only setup step | 2 |
| Transactional outbox and worker | Not implemented; conditional on the core slice passing its gates | 7 |

## Deliberately excluded from scope

Each of these is a conscious decision, not an oversight. The brief allows a smaller system
with well-considered behaviour, and the guidance explicitly asks for several of these to be
deferred.

| Excluded | Why |
| --- | --- |
| Email or push notification delivery | Requires an external provider and credentials. The interesting engineering problem — reliable, retry-safe secondary work — is addressed by the transactional outbox writing durable events instead. |
| File attachments | Object storage, virus scanning, size quotas and access control are a project of their own, and none of the brief's critical behaviours depend on them. |
| SSO / OIDC / SAML | Not required by the brief, which explicitly allows seeded demo users with a simple login. An external identity provider would add configuration the reviewer cannot run offline. |
| Real-time updates (WebSockets) | The brief says polling or refetching is sufficient. WebSockets would add a stateful connection layer and a second source of truth without improving any tested invariant. |
| Mobile applications | Out of scope for a browser-based internal tool. |
| Reopening closed items | Deliberately not implemented. Reopening must define who may do it, whether it resets the version, and what history it produces. Rather than guess, closing is terminal for now and the decision is recorded in `ENGINEERING_DECISIONS.md`. |
| Deleting work items and comments | The guidance prefers resolving to deleting. Both are append-only, and history must not silently disappear. |
| Editing or deleting comments | A comment is evidence of what somebody said. Editing it would make the activity timeline unreliable. |
| Login rate limiting and account lockout | Not implemented. The mitigation today is a generic error message that does not reveal whether an email exists. See the threat model for the residual risk. |
| Password change, password reset, MFA | There is no self-registration and no credential lifecycle in the demo scope. |

## Known technical limitations

| Limitation | Impact | How it would be addressed |
| --- | --- | --- |
| `alembic check` does not compare `CHECK` constraint bodies | A check constraint altered in a model but not in a migration would not be caught automatically | Explicit constraint tests cover the important ones; a schema-dump diff would close the gap |
| Enum-like values live in both a Python enum and a SQL `CHECK` | Adding a value needs a coordinated change, and a mismatch is only caught by tests | A test asserts each value set against the database; a generated constraint would remove the duplication |
| The test suite truncates a real database | Tests cannot run in parallel against the same database, and a misconfigured `TEST_DATABASE_URL` would destroy data | Documented prominently in `README.md` and `docs/OPERATIONS.md`; a per-worker schema or database would allow parallelism |
| Single API process, no shared connection pool | Horizontal scaling needs an external pooler (for example PgBouncer) | Stated as an evolution path, not a current capability |
| No measurements at the target scale | The brief's figures (tens of thousands of items, thousands of concurrent users) are design expectations. No load test has been run, and no performance claim is made | Seed a few thousand items, inspect query plans with `EXPLAIN ANALYZE`, then load test |
| No observability beyond structured logs | Diagnosing a production incident would rely on logs alone | Add request IDs, metrics and tracing when the deployment target is known |

## Honest statement of scale

The application has not been load-tested. It is designed so that the parts that usually
break under concurrency — claims, version checks, duplicate submissions — are enforced by
PostgreSQL rather than by application timing, and so that list views never load a full
table into memory. That is a design argument, not a measurement, and it is presented as
such.
