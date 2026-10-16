/**
 * A team's whole activity feed.
 *
 * The same rows as an item's timeline, read through a different filter and paged with its
 * own cursor. It is here because it is the view that makes the history feel like a record
 * of the team's work rather than a property of one item — and because keeping it separate
 * from the item list is what "activity paginates independently" means to a reader.
 */

import { useInfiniteQuery } from '@tanstack/react-query'

import { api } from '../../api/client'
import type { Membership } from '../../api/types'
import { keys } from '../../query/client'
import { ActivityList } from '../items/ActivityTimeline'

export function TeamActivityPage({ team }: { team: Membership }) {
  const query = useInfiniteQuery({
    queryKey: keys.teamActivity(team.team_id),
    queryFn: ({ pageParam, signal }) => api.teamActivity(team.team_id, pageParam, 25, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
  })

  return (
    <section className="page">
      <header className="page__header">
        <div>
          <h2 className="page__title">{team.team_name} activity</h2>
          <p className="page__subtitle">
            Everything that has happened in this team, newest first. Read-only.
          </p>
        </div>
      </header>

      <section className="card">
        <ActivityList
          query={query}
          emptyTitle="Nothing has happened yet"
          emptyDetail="Events appear here as work items are created and moved."
        />
      </section>
    </section>
  )
}
