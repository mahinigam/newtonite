/**
 * Signing in.
 *
 * The sign-in screen is the one place a reviewer sees the authentication design from the
 * outside, so the tests check the two things that are easy to get wrong: a refusal is shown
 * using the API's own wording (the server answers identically for an unknown address and a
 * wrong password, and the interface must not undo that), and a success installs the session
 * with the memberships the server returned.
 */

import { afterEach, describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { sessionStore } from './session'
import { errorResponse, installFetch, jsonResponse } from '../test/fetch'
import { NINA, membership } from '../test/fixtures'
import { renderWithClient } from '../test/render'
import { LoginPage } from './LoginPage'

afterEach(() => {
  sessionStore.clear()
})

function stubLogin({ fail = false } = {}) {
  return installFetch((request) => {
    if (request.path === '/api/v1/auth/login') {
      if (fail) return errorResponse(401, 'invalid_credentials', 'Email or password is incorrect.')
      return jsonResponse({
        token: 'tok_abc',
        token_type: 'Bearer',
        expires_at: '2026-10-18T00:00:00Z',
        user: NINA,
      })
    }
    if (request.path === '/api/v1/auth/me') {
      return jsonResponse({ user: NINA, memberships: [membership()] })
    }
    return errorResponse(404, 'not_found', `No stub for ${request.path}`)
  })
}

describe('LoginPage', () => {
  it('shows the API’s message when the credentials are refused', async () => {
    stubLogin({ fail: true })
    const user = userEvent.setup()

    renderWithClient(<LoginPage />)

    await user.type(screen.getByLabelText(/email/i), 'nina.member@newtonite.dev')
    await user.type(screen.getByLabelText(/password/i), 'wrong')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/email or password is incorrect/i)
    expect(sessionStore.get()).toBeNull()
  })

  it('does not distinguish an unknown account from a wrong password', async () => {
    stubLogin({ fail: true })
    const user = userEvent.setup()

    renderWithClient(<LoginPage />)

    await user.type(screen.getByLabelText(/email/i), 'nobody@newtonite.dev')
    await user.type(screen.getByLabelText(/password/i), 'Newtonite!2026')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/email or password is incorrect/i)
    expect(alert).not.toHaveTextContent(/unknown|not found|no such account/i)
  })

  it('installs the session and the memberships the server returned', async () => {
    const calls = stubLogin()
    const user = userEvent.setup()

    renderWithClient(<LoginPage />)

    await user.type(screen.getByLabelText(/email/i), 'nina.member@newtonite.dev')
    await user.type(screen.getByLabelText(/password/i), 'Newtonite!2026')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => {
      expect(sessionStore.get()?.user.display_name).toBe('Nina Member')
    })
    expect(sessionStore.get()?.memberships).toEqual([membership()])
    // The roles come from `/auth/me`, not from the login response.
    expect(calls.some((call) => call.path === '/api/v1/auth/me')).toBe(true)
  })

  it('fills the form from a demo account without submitting it', async () => {
    const calls = stubLogin()
    const user = userEvent.setup()

    renderWithClient(<LoginPage />)

    await user.click(screen.getByRole('button', { name: 'nina.member@newtonite.dev' }))

    expect(screen.getByLabelText(/email/i)).toHaveValue('nina.member@newtonite.dev')
    expect(screen.getByLabelText(/password/i)).toHaveValue('Newtonite!2026')
    expect(calls).toHaveLength(0)
    expect(sessionStore.get()).toBeNull()
  })

  it('leaves no session behind when the memberships cannot be read', async () => {
    installFetch((request) => {
      if (request.path === '/api/v1/auth/login') {
        return jsonResponse({
          token: 'tok_abc',
          token_type: 'Bearer',
          expires_at: '2026-10-18T00:00:00Z',
          user: NINA,
        })
      }
      return errorResponse(401, 'session_expired', 'Your session has expired.')
    })
    const user = userEvent.setup()

    renderWithClient(<LoginPage />)

    await user.type(screen.getByLabelText(/email/i), 'nina.member@newtonite.dev')
    await user.type(screen.getByLabelText(/password/i), 'Newtonite!2026')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => expect(sessionStore.get()).toBeNull())
  })
})
