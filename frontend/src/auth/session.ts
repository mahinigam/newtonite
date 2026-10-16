/**
 * The session, held in memory only.
 *
 * A plain store rather than a React context, for two reasons: the HTTP client needs to
 * clear the session from outside React (any `401` ends it), and a store is directly
 * testable without rendering anything.
 *
 * **The token is never written to `localStorage` or `sessionStorage`.** Web storage is
 * readable by any script on the page, so an XSS bug becomes a stolen credential that
 * outlives the tab. Keeping it in memory means a refresh signs the user out — an
 * acceptable cost for an internal tool, and the trade-off is stated in
 * `docs/SECURITY.md` rather than left implicit.
 */

import { useSyncExternalStore } from 'react'

import { setToken } from '../api/client'
import type { Membership, UserRef } from '../api/types'

export interface Session {
  user: UserRef
  memberships: Membership[]
  expiresAt: string
}

type Listener = () => void

let current: Session | null = null
const listeners = new Set<Listener>()

function emit(): void {
  for (const listener of listeners) listener()
}

export const sessionStore = {
  get(): Session | null {
    return current
  },

  subscribe(listener: Listener): () => void {
    listeners.add(listener)
    return () => {
      listeners.delete(listener)
    }
  },

  /** Adopt a token and the caller it belongs to. */
  start(token: string, session: Session): void {
    setToken(token)
    current = session
    emit()
  },

  /**
   * Forget the session. Called by sign-out and by any `401`, so an expired or revoked
   * token cannot leave the interface looking signed in.
   */
  clear(): void {
    setToken(null)
    current = null
    emit()
  },
}

export function useSession(): Session | null {
  return useSyncExternalStore(sessionStore.subscribe, sessionStore.get, sessionStore.get)
}

/** The caller's role in one team, or `null` when they are not a member of it. */
export function roleIn(session: Session | null, teamId: string): Membership['role'] | null {
  return session?.memberships.find((membership) => membership.team_id === teamId)?.role ?? null
}
