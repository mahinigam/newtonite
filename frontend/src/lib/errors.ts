/**
 * Turning API error codes into something a person can act on.
 *
 * The API returns a message that is safe to display, and it is used as the fallback. The
 * codes below get a better one because the *action* differs: a stale version means
 * "reload and try again", a refused transition means "somebody else already moved it",
 * and an authorization failure means "this is not yours to do" rather than "an error
 * occurred".
 *
 * `recoverableByRefetch` is the part the components need: it says whether refreshing the
 * view can possibly help, which decides whether the UI offers a "reload" button or
 * simply reports the refusal.
 */

import { ApiError } from '../api/client'

export interface ExplainedError {
  title: string
  detail: string
  recoverableByRefetch: boolean
}

const EXPLANATIONS: Partial<Record<ApiError['code'], ExplainedError>> = {
  stale_version: {
    title: 'Somebody else changed this first',
    detail:
      'Your copy was out of date, so the change was not applied. The item has been reloaded — review it and try again.',
    recoverableByRefetch: true,
  },
  item_already_claimed: {
    title: 'Already taken',
    detail: 'Somebody claimed this item first. Reload to see who owns it now.',
    recoverableByRefetch: true,
  },
  invalid_transition: {
    title: 'That move is not available',
    detail:
      'The item has moved on since this page was loaded. Reload to see the moves that are legal now.',
    recoverableByRefetch: true,
  },
  forbidden: {
    title: 'Not allowed',
    detail: 'Your role in this team does not permit that. A lead or an admin can do it.',
    recoverableByRefetch: false,
  },
  not_found: {
    title: 'Not found',
    detail: 'This item does not exist, or it belongs to a team you are not in.',
    recoverableByRefetch: false,
  },
  assignee_required: {
    title: 'Assign it first',
    detail: 'This item has to have an owner before the work can start.',
    recoverableByRefetch: false,
  },
  invalid_assignee: {
    title: 'That person is not in this team',
    detail: 'Work can only be assigned to a member of the item\u2019s own team.',
    recoverableByRefetch: false,
  },
  idempotency_key_reuse: {
    title: 'That request was reused',
    detail:
      'This submission reused a key from an earlier, different request. Reload the page and try again.',
    recoverableByRefetch: true,
  },
  idempotency_key_required: {
    title: 'Missing request key',
    detail: 'The request was refused because it could not be made safe to retry.',
    recoverableByRefetch: false,
  },
  network_error: {
    title: 'Cannot reach the server',
    detail: 'The API did not respond. Check that it is running, then try again.',
    recoverableByRefetch: true,
  },
  internal_error: {
    title: 'Something went wrong',
    detail: 'The server reported an unexpected failure. Trying again may work.',
    recoverableByRefetch: true,
  },
}

export function explainError(error: unknown): ExplainedError {
  if (error instanceof ApiError) {
    // Validation is checked first: it has no entry in the table above, and the fields the
    // server named are more useful than any generic sentence.
    if (error.code === 'validation_failed') {
      const fields = error.fieldErrors
      return {
        title: 'That input is not valid',
        detail:
          fields.length > 0
            ? fields.map((field) => `${field.field}: ${field.message}`).join('; ')
            : error.message,
        recoverableByRefetch: false,
      }
    }

    const known = EXPLANATIONS[error.code]
    if (known) return known

    return { title: 'Request refused', detail: error.message, recoverableByRefetch: false }
  }
  if (error instanceof Error) {
    return { title: 'Something went wrong', detail: error.message, recoverableByRefetch: false }
  }
  return {
    title: 'Something went wrong',
    detail: 'An unexpected error occurred.',
    recoverableByRefetch: false,
  }
}
