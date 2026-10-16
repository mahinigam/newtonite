/**
 * A fake `fetch`.
 *
 * The tests exercise real components against a real TanStack Query client; the only thing
 * replaced is the network. Stubbing `fetch` rather than the API module means the tests
 * cover the parts that are actually easy to get wrong — the error envelope, the `ETag`
 * header, the query parameters the list sends — instead of a mock of my own abstraction.
 *
 * Every request is recorded, so a test can assert on what the *server* was asked for:
 * that a filter became a query parameter, that a retry reused the same `Idempotency-Key`,
 * that a conditional write carried `If-Match`.
 */

import { vi } from 'vitest'

export interface RecordedRequest {
  method: string
  url: string
  path: string
  searchParams: URLSearchParams
  headers: Record<string, string>
  /** Parsed JSON body, or `undefined` when there was none. */
  body: unknown
}

export type StubHandler = (request: RecordedRequest) => Response | Promise<Response>

export function jsonResponse(
  body: unknown,
  { status = 200, headers = {} }: { status?: number; headers?: Record<string, string> } = {},
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

export function errorResponse(
  status: number,
  code: string,
  message: string,
  details?: unknown,
): Response {
  return jsonResponse({ error: { code, message, ...(details === undefined ? {} : { details }) } }, {
    status,
  })
}

/** Install the stub and return the list of requests it saw, in order. */
export function installFetch(handler: StubHandler): RecordedRequest[] {
  const calls: RecordedRequest[] = []

  vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === 'string' ? input : input.toString())
    const headers: Record<string, string> = {}
    for (const [key, value] of Object.entries(init?.headers ?? {})) {
      headers[key.toLowerCase()] = String(value)
    }
    const rawBody = init?.body
    const recorded: RecordedRequest = {
      method: (init?.method ?? 'GET').toUpperCase(),
      url: url.toString(),
      path: url.pathname,
      searchParams: url.searchParams,
      headers,
      body: typeof rawBody === 'string' ? JSON.parse(rawBody) : undefined,
    }
    calls.push(recorded)
    return await handler(recorded)
  })

  return calls
}

/** All requests that hit one path, in order. */
export function callsTo(calls: RecordedRequest[], path: string, method?: string): RecordedRequest[] {
  return calls.filter(
    (call) => call.path === path && (method === undefined || call.method === method.toUpperCase()),
  )
}
