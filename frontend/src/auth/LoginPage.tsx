/**
 * Signing in.
 *
 * Two details that are deliberate rather than decorative:
 *
 * * The demo accounts are buttons, not documentation. The seed creates one account per
 *   role and the reviewer's job is to see the role boundary, not to type twelve
 *   addresses; a click fills the form, and the password still has to be submitted.
 * * A failed sign-in shows the API's own message. The server answers identically for an
 *   unknown address and a wrong password, so the interface cannot tell the two apart
 *   either — which is the point of that decision on the server.
 */

import { type FormEvent, useState } from 'react'

import { signIn } from '../auth/auth'
import { ErrorState } from '../components/states'

const DEMO_PASSWORD = 'Newtonite!2026'

const DEMO_ACCOUNTS: { email: string; note: string }[] = [
  { email: 'ada.admin@newtonite.dev', note: 'admin of platform and support' },
  { email: 'lena.lead@newtonite.dev', note: 'lead in platform' },
  { email: 'nina.member@newtonite.dev', note: 'member in platform' },
  { email: 'vic.viewer@newtonite.dev', note: 'viewer in platform' },
  { email: 'priya.lead@newtonite.dev', note: 'lead in another team' },
]

export function LoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await signIn(email, password)
    } catch (cause) {
      setError(cause)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login">
      <div className="login__panel">
        <div className="login__brand">
          <span className="app__mark" aria-hidden="true">
            N
          </span>
          <div>
            <h1 className="app__title">Newtonite</h1>
            <p className="app__subtitle">Operations under pressure</p>
          </div>
        </div>

        <form className="login__form" onSubmit={submit}>
          <label className="field">
            <span className="field__label">Email</span>
            <input
              type="email"
              name="email"
              autoComplete="username"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </label>

          <label className="field">
            <span className="field__label">Password</span>
            <input
              type="password"
              name="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>

          {error ? <ErrorState error={error} /> : null}

          <button type="submit" className="button button--primary" disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>

        <div className="login__demo">
          <p className="login__demo-title">Demo accounts (password {DEMO_PASSWORD})</p>
          <ul className="login__demo-list">
            {DEMO_ACCOUNTS.map((account) => (
              <li key={account.email}>
                <button
                  type="button"
                  className="link-button"
                  onClick={() => {
                    setEmail(account.email)
                    setPassword(DEMO_PASSWORD)
                  }}
                >
                  {account.email}
                </button>
                <span className="login__demo-note"> — {account.note}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  )
}
