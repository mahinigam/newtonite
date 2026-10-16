/**
 * Idempotency keys for the browser.
 *
 * A key identifies one *logical operation*, not one HTTP attempt. That distinction is the
 * whole point: it must be generated once, when the user acts, and then reused by every
 * retry of that same action. Generating it inside the request function would give each
 * retry a fresh key and turn a network blip into a duplicate work item — which is exactly
 * the failure the API's `Idempotency-Key` requirement exists to prevent.
 *
 * So the key is a mutation *variable*, created at the call site.
 */
export function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  // `randomUUID` needs a secure context. A non-cryptographic fallback is acceptable here
  // because the key is not a secret: it only has to be unique among one actor's own
  // in-flight requests, and the server scopes it to `(actor, operation)`.
  const random = Math.random().toString(36).slice(2)
  return `${Date.now().toString(36)}-${random}`
}
