/**
 * The shell: a header that says who you are and which team you are looking at, and a
 * navigation bar between the team's three views.
 *
 * The team switcher lists only the teams the caller is a member of — which is the same
 * set the server will authorize against, so the interface cannot offer a team that would
 * then 404. It is a convenience, not a boundary: the server decides.
 */

import type { ReactNode } from 'react'

import type { Membership } from '../api/types'
import { navigate } from '../lib/router'
import { roleLabel } from '../lib/format'
import { useSession } from '../auth/session'

export function Layout({
  memberships,
  activeTeamKey,
  onSignOut,
  children,
}: {
  memberships: Membership[]
  activeTeamKey: string | null
  onSignOut: () => void
  children: ReactNode
}) {
  const session = useSession()
  const active = memberships.find((membership) => membership.team_key === activeTeamKey) ?? null

  return (
    <div className="app">
      <header className="app__header">
        <div className="app__brand">
          <span className="app__mark" aria-hidden="true">
            N
          </span>
          <div>
            <h1 className="app__title">Newtonite</h1>
            <p className="app__subtitle">Operations under pressure</p>
          </div>
        </div>

        <div className="app__identity">
          <label className="app__team">
            <span className="visually-hidden">Team</span>
            <select
              value={activeTeamKey ?? ''}
              onChange={(event) => {
                navigate({ name: 'items', teamKey: event.target.value })
              }}
            >
              {memberships.map((membership) => (
                <option key={membership.team_key} value={membership.team_key}>
                  {membership.team_name}
                </option>
              ))}
            </select>
          </label>
          <span className="app__user">
            {session?.user.display_name}
            {active ? <span className="app__role"> · {roleLabel(active.role)}</span> : null}
          </span>
          <button type="button" className="button button--quiet" onClick={onSignOut}>
            Sign out
          </button>
        </div>
      </header>

      {active ? (
        <nav className="app__nav" aria-label="Team views">
          <a href={`#/teams/${active.team_key}`} aria-current={activeTeamKey ? 'page' : undefined}>
            Work items
          </a>
          <a href={`#/teams/${active.team_key}/activity`}>Team activity</a>
        </nav>
      ) : null}

      <main className="app__main">{children}</main>
    </div>
  )
}
