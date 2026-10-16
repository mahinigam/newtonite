/**
 * One work item: its fields, the moves available, its discussion and its history.
 *
 * The detail query returns the item **and** the `ETag`, because every conditional write
 * needs the version and the version is in the response header rather than the body. They
 * are kept together in the cache under one key, so the version a write is conditioned on is
 * always the version of the item it is writing to — a page cannot end up sending an old
 * version with new data.
 *
 * After a successful write the mutation puts the server's own response into that cache
 * entry, so the next write uses the version the server just produced rather than the one
 * this page happened to load with. That is why there is no local "current etag" state here:
 * there is exactly one copy of the item, and the server wrote it.
 *
 * A stale version is not hidden. When a write comes back `412`, the item is re-read and the
 * explanation is shown next to the control that failed: the server's copy is the truth, and
 * the interface's job is to show it and say what happened.
 */

import { useInfiniteQuery, useQuery } from '@tanstack/react-query'

import { api } from '../../api/client'
import type { Membership } from '../../api/types'
import { ErrorState, Loading } from '../../components/states'
import { formatDate, formatDateTime, priorityLabel, statusLabel, typeLabel } from '../../lib/format'
import { navigate } from '../../lib/router'
import { keys } from '../../query/client'
import { ActivityList } from './ActivityTimeline'
import { CommentThread } from './CommentThread'
import { ItemActions } from './ItemActions'
import { ItemEditForm } from './ItemEditForm'

export function ItemDetailPage({ team, itemId }: { team: Membership; itemId: string }) {
  const query = useQuery({
    queryKey: keys.item(itemId),
    queryFn: async ({ signal }) => {
      const response = await api.item(itemId, signal)
      return { item: response.data, etag: response.etag }
    },
  })

  const activity = useInfiniteQuery({
    queryKey: keys.itemActivity(itemId),
    queryFn: ({ pageParam, signal }) => api.itemActivity(itemId, pageParam, 25, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
  })

  if (query.isPending) return <Loading label="Loading the item…" />

  if (query.isError) {
    return (
      <section className="page">
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        <button
          type="button"
          className="button button--quiet"
          onClick={() => navigate({ name: 'items', teamKey: team.team_key })}
        >
          Back to the list
        </button>
      </section>
    )
  }

  const snapshot = query.data
  if (!snapshot) return null
  const { item, etag } = snapshot
  const reload = () => void query.refetch()

  return (
    <section className="page">
      <nav className="breadcrumb" aria-label="Breadcrumb">
        <a href={`#/teams/${team.team_key}`}>{team.team_name}</a>
        <span aria-hidden="true"> / </span>
        <span>{item.title}</span>
      </nav>

      <header className="page__header">
        <div>
          <h2 className="page__title">{item.title}</h2>
          <p className="page__subtitle">
            <span className={`badge badge--status-${item.status}`}>{statusLabel(item.status)}</span>{' '}
            <span className={`badge badge--priority-${item.priority}`}>
              {priorityLabel(item.priority)}
            </span>{' '}
            <span className="item-row__type">{typeLabel(item.type)}</span>
          </p>
        </div>
        <button type="button" className="button button--quiet" onClick={reload} disabled={query.isFetching}>
          {query.isFetching ? 'Refreshing…' : 'Refresh'}
        </button>
      </header>

      <dl className="facts">
        <div>
          <dt>Assignee</dt>
          <dd>{item.assignee ? item.assignee.display_name : 'Unassigned'}</dd>
        </div>
        <div>
          <dt>Created by</dt>
          <dd>{item.created_by.display_name}</dd>
        </div>
        <div>
          <dt>Due</dt>
          <dd>{item.due_at ? formatDate(item.due_at) : 'No due date'}</dd>
        </div>
        <div>
          <dt>Version</dt>
          <dd>
            <span className="facts__value">{item.version}</span>
            <span className="facts__hint"> · conditional writes use this</span>
          </dd>
        </div>
        <div>
          <dt>Created</dt>
          <dd>{formatDateTime(item.created_at)}</dd>
        </div>
        <div>
          <dt>Updated</dt>
          <dd>{formatDateTime(item.updated_at)}</dd>
        </div>
      </dl>

      <ItemActions item={item} etag={etag} team={team} onReload={reload} />
      <ItemEditForm item={item} etag={etag} team={team} onReload={reload} />
      <CommentThread item={item} team={team} />

      <section className="card" aria-label="History">
        <h2 className="card__title">History</h2>
        <p className="card__hint">
          Append-only. Every change above wrote its own entry, in the same transaction as the
          change itself.
        </p>
        <ActivityList
          query={activity}
          emptyTitle="No history yet"
          emptyDetail="Events appear here as the item is worked on."
        />
      </section>
    </section>
  )
}
