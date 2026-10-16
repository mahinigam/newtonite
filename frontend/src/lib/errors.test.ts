/**
 * Error explanations.
 *
 * The point of this module is that a refusal reaches the user as an *instruction*. These
 * tests pin the two properties that matter: a stale version says the change was not
 * applied and offers a reload, and a role refusal does not pretend a reload would help.
 */

import { describe, expect, it } from 'vitest'

import { ApiError } from '../api/client'
import { explainError } from './errors'

describe('explainError', () => {
  it('explains a stale version as a conflict with a way forward', () => {
    const explained = explainError(new ApiError(412, 'stale_version', 'Version mismatch.'))

    expect(explained.title).toMatch(/changed this first/i)
    expect(explained.detail).toMatch(/not applied/i)
    expect(explained.recoverableByRefetch).toBe(true)
  })

  it('explains a lost claim race as recoverable', () => {
    const explained = explainError(new ApiError(409, 'item_already_claimed', 'Taken.'))

    expect(explained.recoverableByRefetch).toBe(true)
  })

  it('explains a forbidden action without promising a reload will fix it', () => {
    const explained = explainError(new ApiError(403, 'forbidden', 'Your role does not allow that.'))

    expect(explained.recoverableByRefetch).toBe(false)
    expect(explained.detail).toMatch(/lead or an admin/i)
  })

  it('does not leak the server message for an unexpected code', () => {
    const explained = explainError(new ApiError(500, 'internal_error', 'Something broke.'))

    expect(explained.title).toMatch(/went wrong/i)
    expect(explained.detail).not.toMatch(/traceback|SQL/i)
  })

  it('lists validation fields when the server named them', () => {
    const explained = explainError(
      new ApiError(422, 'validation_failed', 'Invalid.', [
        { field: 'title', message: 'Field required' },
      ]),
    )

    expect(explained.detail).toContain('title')
    expect(explained.detail).toContain('Field required')
  })

  it('handles a plain Error without pretending to know the code', () => {
    const explained = explainError(new Error('boom'))

    expect(explained.title).toMatch(/went wrong/i)
    expect(explained.detail).toBe('boom')
  })

  it('handles a thrown non-error', () => {
    const explained = explainError('something odd')

    expect(explained.title).toMatch(/went wrong/i)
  })
})
