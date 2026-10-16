# API Reference

Base path: `/api/v1`. The generated OpenAPI schema is served at `/api/v1/openapi.json` and a
browsable UI at `/docs`.

**This document lists what is implemented.** Work-item endpoints, the workflow transitions
and comments do not exist yet; they are tracked in
[`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md).

## Authentication

State-changing and reading endpoints require a bearer token obtained from `login`.

```
Authorization: Bearer <token>
```

The token is 43 characters of base64url (256 bits of `secrets` randomness). The server stores
only its SHA-256 digest, so **the token is shown exactly once**, in the login response, and
cannot be recovered. A client that loses it must sign in again.

A session ends when any of the following is true:

| Condition | Result |
| --- | --- |
| No `Authorization` header, or a scheme other than `Bearer`, or an empty token | `401 not_authenticated` |
| Digest unknown, or the session was revoked by logout | `401 invalid_session` |
| Past the absolute lifetime (12 h from login) | `401 session_expired` |
| Idle for longer than the idle window (2 h) | `401 session_expired` |
| The account was deactivated | `401 invalid_session` |

`401` responses carry `WWW-Authenticate: Bearer`. Lifetimes are configuration
(`SESSION_ABSOLUTE_TTL_HOURS`, `SESSION_IDLE_TTL_HOURS`). Both expiries are evaluated with
the database clock. Every authenticated request refreshes the idle window, but the refresh is
written at most once a minute per session, so the idle window can be enforced up to about a
minute late.

### `POST /auth/login`

```json
{ "email": "sam.multi@newtonite.dev", "password": "Newtonite!2026" }
```

`200`:

```json
{
  "token": "…43 characters…",
  "token_type": "Bearer",
  "expires_at": "2026-10-18T09:13:13.688524Z",
  "user": { "id": "…", "email": "sam.multi@newtonite.dev", "display_name": "Sam Alvarez" }
}
```

An unknown email, a wrong password and a deactivated account all return **the same** `401
invalid_credentials` body. The endpoint cannot be used to discover which addresses have
accounts. There is no rate limiting yet — see
[`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md).

### `POST /auth/logout`

Revokes the session the request is using. Immediate for every later request with the same
token. Returns `{"revoked": true}`. Because revocation takes effect at once, calling logout
twice returns `401` the second time.

### `GET /auth/me`

The caller and their role in each team they belong to.

```json
{
  "user": { "id": "…", "email": "sam.multi@newtonite.dev", "display_name": "Sam Alvarez" },
  "memberships": [
    { "team_id": "…", "team_key": "finance",  "team_name": "Finance Operations",  "role": "viewer" },
    { "team_id": "…", "team_key": "platform", "team_name": "Platform Engineering", "role": "member" }
  ]
}
```

This is what the frontend uses to decide what to offer. It is **advisory to the interface
only**: every write is authorized again on the server from stored data.

## Teams

Team fields are prefixed (`team_id`, `team_key`) so a nested team can never be mistaken for the
enclosing resource, and the shape is identical here and inside a membership.

### `GET /teams`

Teams the caller belongs to, with the caller's role. Only ever returns their own teams.

### `GET /teams/{team_id}/members`

The roster. Visible to **any** member of the team, including a `viewer`. Sorted by role, then
name.

```json
[{ "user_id": "…", "email": "…", "display_name": "…", "role": "lead" }]
```

### `PUT /teams/{team_id}/members/{user_id}` — admin only

```json
{ "role": "lead" }
```

Grants or changes a role, creating the membership if the person is not in the team yet.

Refusals:

| Situation | Response |
| --- | --- |
| Caller is not a member of this team | `404 not_found` (indistinguishable from a nonexistent team) |
| Caller is in the team but is not an admin | `403 forbidden` |
| The target shares no team with the caller, or does not exist | `404 not_found` |
| `role` is not one of `viewer`, `member`, `lead`, `admin` | `422 validation_failed` |
| Any field other than `role` is present | `422 validation_failed` |
| The change would leave the team with no admin | `409 last_admin` |

### `DELETE /teams/{team_id}/members/{user_id}` — admin only

`204` with an empty body on success.

| Situation | Response |
| --- | --- |
| Not a member, or not an admin | `404`, `403` as above |
| Last admin of the team | `409 last_admin` |
| Still the assignee of work items in this team | `409 member_has_assigned_items` — reassign first. Removing a member never silently unassigns their work, and never touches the history they created |

## Users

### `GET /users/directory?query=`

People the caller may need to assign work to: only those who share at least one team with
the caller, capped at 50, filtered in the database by `query` (a name or email fragment,
max 100 characters). A caller cannot use it to enumerate accounts outside their own teams.

```json
[{ "id": "…", "email": "…", "display_name": "…" }]
```

## Roles

Roles are **per team**. The same account can be a `member` in one team and a `viewer` in
another; `sam.multi` in the demo data is exactly that case.

| Role | Read | Create · comment · claim · edit · move work | Assign others · approve · close | Manage members |
| --- | --- | --- | --- | --- |
| `viewer` | ✓ | | | |
| `member` | ✓ | ✓ | | |
| `lead` | ✓ | ✓ | ✓ | |
| `admin` | ✓ | ✓ | ✓ | ✓ |

The matrix is defined once, in `backend/app/policy/roles.py`, and tested in
`backend/tests/test_authorization.py`.

## Errors

Every failure, including framework-generated ones such as an unknown route, has the same
shape:

```json
{ "error": { "code": "forbidden", "message": "Your role in this team does not allow 'manage_members'." } }
```

`code` is stable and is what a client should branch on. `message` is written to be shown to a
user and never contains a stack trace, SQL, a table name or a constraint name. `details` is
present only for validation failures and lists the offending fields.

| Status | Codes | Meaning |
| --- | --- | --- |
| `401` | `not_authenticated`, `invalid_session`, `session_expired`, `invalid_credentials` | No usable session — sign in again |
| `403` | `forbidden` | You are in this team, but your role does not permit it |
| `404` | `not_found` | Does not exist, **or** belongs to a team you are not in |
| `409` | `last_admin`, `member_has_assigned_items`, and later `invalid_transition`, `item_already_claimed`, `idempotency_key_reuse` | Conflicts with current state |
| `412` | `stale_version` | Your `If-Match` version is out of date (work items, Phase 4) |
| `422` | `validation_failed` | The request body or a parameter failed validation |
| `500` | `internal_error` | Unexpected. The detail is logged, never returned |

**Why `404` rather than `403` across a team boundary.** `403` would confirm that the resource
exists. A caller who is not in a team is answered exactly as if they had invented the id, and
a test asserts the two responses are byte-identical. `403` is reserved for a caller who is
already inside the team, where the team's existence is not a secret and the message can
actually help.

## Health

| Endpoint | Purpose |
| --- | --- |
| `GET /healthz` | Liveness. Does not touch the database |
| `GET /readyz` | Readiness. Performs a database round trip; `503` when the database is unreachable |
