# Frontend

The React client for the operations API: what it does, how it decides things, and what it
deliberately does not do. It is a single-page application with three views and no server-side
rendering, because the API is the only source of truth and the client's job is to show it.

```
frontend/
  src/
    api/          the HTTP client and the response types
    auth/         the session store, sign-in and sign-out
    components/   the shell and the four view states
    features/     one directory per view: items, activity
    lib/          formatting, error explanations, the router, the role matrix
    query/        the TanStack Query client, its keys and its invalidation helpers
    test/         the fake network, fixtures and render helpers
```

## The three views

| Route | View |
| --- | --- |
| `#/teams/:teamKey` | The team's work items: filters, search, cursor paging, create |
| `#/teams/:teamKey/items/:itemId` | One item: fields, available moves, discussion, history |
| `#/teams/:teamKey/activity` | The team's activity feed |

Routing is a hash router of about sixty lines (`src/lib/router.ts`). It is a pure
`parseHash` function plus a `useSyncExternalStore` subscription, which means the routing rules
are tested without a DOM. A routing library was not used because there is one nested route and
no data loaders: the two things a router library is actually for.

Hash routing rather than the History API because it needs no server rewrite rule, so the built
`dist/` can be served by any static file server.

## Server state is the only state

Every list, item, comment and event on screen came from a response. Nothing is derived from a
locally maintained copy, and nothing is filtered or sorted in the browser.

- **Filtering, searching and paging are query parameters.** Changing a filter changes the
  query *key*, which is a new request. The browser never holds a whole team's items, so
  "show me every unassigned urgent item" is correct rather than "…that happens to be in the
  page I already loaded".
- **Paging is the server's cursor.** `next_cursor` is handed back verbatim; `has_more` decides
  whether the "Load more" control exists at all.
- **Mutations are server-confirmed.** No optimistic update is used for claim, approve, close or
  edit. The request is sent, the server answers, and the answer is what appears. For operations
  whose entire point is that exactly one caller wins, showing a result before it is known is the
  wrong trade — a `409` or `412` would have to be rolled back in front of the user.
- **A successful write is adopted, not re-fetched.** The mutation's response *is* the new state,
  so it goes into the item's cache entry directly. That entry holds the item **and its `ETag`**
  together, so the version a conditional write is conditioned on is always the version of the
  item it is writing to. A refused write (`409`, `412`) re-reads the item instead, because the
  local copy is then known to be stale.
- **A `401` from any request ends the session.** Handled once, in the query cache's `onError`,
  so an expired token returns the user to the sign-in screen rather than leaving a page showing
  an error it can never recover from.

## Retries, and why only two mutations retry

Retrying a write is only safe when the server can recognise the retry. The default for
mutations is therefore **no retry**, and exactly two opt in:

| Mutation | Retries | Why |
| --- | --- | --- |
| Create a work item | Yes (2) | Carries an `Idempotency-Key`; a retry replays the first item |
| Post a comment | Yes (2) | Same mechanism, same guarantee |
| Claim | No | A retry after a success would come back `409` — the item is now owned |
| Transition | No | A retry after a success would come back `412` — the version moved |
| Edit | No | Same |

The key is a **mutation variable**, generated once when the user acts
(`src/lib/idempotency.ts`), not inside the request function. That is the whole reason the retry
is safe: every attempt is the same logical operation. Generating it per attempt would turn a
network blip into a duplicate work item, and `CommentThread.test.tsx` asserts the key is
identical across a retry specifically because that is the property a refactor would break
silently.

Queries retry only transport and `5xx` faults. A `4xx` will not improve on its own, and retrying
an authentication failure just delays the sign-in screen.

## The role matrix is advisory here

`src/lib/permissions.ts` mirrors `backend/app/policy/roles.py` so the interface does not offer a
control the server will refuse. It is **not** the boundary. Every write is authorized again on
the server, and the moves an item offers come from the server's own `available_transitions`,
computed for the caller's role. A client that ignored the matrix entirely would still be
correctly refused — it would simply be a worse interface.

Two examples of the interface rendering the server's decision rather than making its own:

- A viewer gets no claim button, no transition buttons and a read-only detail panel, because the
  server would answer `403` for each of them.
- The assignee picker is the team's roster (`/teams/{id}/members`), not a global directory,
  because work can only be assigned to a member of the item's own team — a rule the database
  enforces with a composite foreign key.

## Errors are explained, not printed

`src/lib/errors.ts` maps the API's stable `code` onto a title, a sentence, and whether reloading
could possibly help. The distinction matters:

- `412 stale_version` → "Somebody else changed this first… the change was not applied", with a
  **Reload the item** button, because the item on screen is genuinely out of date.
- `403 forbidden` → "Your role in this team does not permit that", with **no** reload button,
  because reloading changes nothing.
- `409 item_already_claimed` → "Already taken", with a reload, because the item now has an owner
  the page is not showing.

The server's own message is the fallback, never the primary source: a code is stable, a message
is prose.

## Tests

`npm run test` runs 55 tests in seven files. They render real components against a real
TanStack Query client and a **stubbed `fetch`** (`src/test/fetch.ts`) rather than mocking the API
module, so the error envelope, the `ETag` header and the query parameters are all covered. Every
request is recorded, which is how the tests assert on what the *server* was asked for: that a
filter became a query parameter, that a conditional write carried `If-Match`, that a retry
reused its key.

| File | Covers |
| --- | --- |
| `api/client.test.ts` | The error envelope, `ETag` capture, the bearer header, filter parameter shapes |
| `lib/router.test.ts` | Route parsing, including the shapes that must be refused |
| `lib/errors.test.ts` | Conflict wording, and that a role refusal does not promise a reload |
| `auth/LoginPage.test.tsx` | Refusal wording, session installation, the demo-account buttons |
| `features/items/ItemListPage.test.tsx` | Loading, empty, error and retry; filters as server queries; cursor paging |
| `features/items/ItemDetailPage.test.tsx` | The role boundary, server-confirmed claim, `409` and `412` handling, `If-Match` |
| `features/items/CommentThread.test.tsx` | Idempotency-key reuse across a retry, length limits, viewer read-only |

What the frontend tests do **not** prove is the wire format — they stub the network. That is
covered separately by a contract check that sends the same requests to a running API; see the
Phase 6 section of `IMPLEMENTATION_STATUS.md`.

## Known frontend limitations

- **A page reload signs the user out.** Deliberate: the token is in memory only. See
  `SECURITY.md`.
- **No optimistic updates anywhere.** Chosen, not missing.
- **No member-management UI.** The API supports it (admin only); the interface does not, because
  the vertical slice is about work items.
- **No offline support and no service worker.** The API is authoritative; a cached page would be
  a page showing data the server has moved past.
- **The item list has no sort control.** The order is the server's `(created_at, id)` descending
  keyset order, which is the order a keyset cursor requires.
