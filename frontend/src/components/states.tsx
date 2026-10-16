/**
 * The four states every server-backed view has, as components.
 *
 * Loading, empty, error and success are the states a reviewer is asked to check, and
 * writing them once means no view can quietly omit one. The error component takes the
 * `unknown` the query handed it and explains it through `lib/errors.ts`, so the wording
 * for a stale version is the same on every screen.
 */

import type { ReactNode } from 'react'

import { explainError } from '../lib/errors'

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <p className="state state--loading" role="status">
      {label}
    </p>
  )
}

export function Empty({ title, detail }: { title: string; detail?: ReactNode }) {
  return (
    <div className="state state--empty">
      <p className="state__title">{title}</p>
      {detail ? <p className="state__detail">{detail}</p> : null}
    </div>
  )
}

export function ErrorState({
  error,
  onRetry,
  retryLabel = 'Try again',
}: {
  error: unknown
  onRetry?: () => void
  retryLabel?: string
}) {
  const explained = explainError(error)
  return (
    <div className="state state--error" role="alert">
      <p className="state__title">{explained.title}</p>
      <p className="state__detail">{explained.detail}</p>
      {onRetry ? (
        <button type="button" className="button" onClick={onRetry}>
          {retryLabel}
        </button>
      ) : null}
    </div>
  )
}

/**
 * A refusal that does not replace the view.
 *
 * A failed transition should not blank the item — the item is still there, the move just
 * did not happen. This renders the explanation inline, next to the control that failed,
 * with a reload offered only when reloading could actually change the answer.
 */
export function InlineError({
  error,
  onReload,
  onDismiss,
}: {
  error: unknown
  onReload?: () => void
  onDismiss?: () => void
}) {
  const explained = explainError(error)
  return (
    <div className="notice notice--error" role="alert">
      <div>
        <strong>{explained.title}.</strong> {explained.detail}
      </div>
      <div className="notice__actions">
        {explained.recoverableByRefetch && onReload ? (
          <button type="button" className="button button--small" onClick={onReload}>
            Reload the item
          </button>
        ) : null}
        {onDismiss ? (
          <button type="button" className="button button--small button--quiet" onClick={onDismiss}>
            Dismiss
          </button>
        ) : null}
      </div>
    </div>
  )
}
