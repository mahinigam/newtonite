/**
 * The discussion on an item.
 *
 * Newest first, paged by the server with the thread's own cursor — a thread is not a page
 * of items, and the two do not share a position.
 *
 * Posting is server-confirmed and carries an `Idempotency-Key` generated once per
 * submission, exactly as creating an item does. The key is a mutation variable, so the
 * retry policy in `query/client.ts` can safely retry the request: the second attempt
 * replays the first comment instead of posting it twice. That is the visible payoff of the
 * idempotency work on the server, and it is why the button can be clicked twice in a
 * hurry without producing two notes.
 *
 * A viewer may read the thread and not add to it. The server enforces that; the form is
 * simply not rendered, and if it were the server would still refuse.
 */

import { type FormEvent, useState } from 'react'
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'

import { api } from '../../api/client'
import type { Membership, WorkItemDetail } from '../../api/types'
import { Empty, ErrorState, Loading } from '../../components/states'
import { formatDateTime, formatRelative } from '../../lib/format'
import { newIdempotencyKey } from '../../lib/idempotency'
import { can } from '../../lib/permissions'
import { invalidateItemViews, keys } from '../../query/client'

const PAGE_SIZE = 25
const MAX_LENGTH = 4000

export function CommentThread({ item, team }: { item: WorkItemDetail; team: Membership }) {
  const queryClient = useQueryClient()
  const [body, setBody] = useState('')

  const query = useInfiniteQuery({
    queryKey: keys.comments(item.id),
    queryFn: ({ pageParam, signal }) => api.comments(item.id, pageParam, PAGE_SIZE, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
  })

  const post = useMutation({
    // Safe to retry: the key travels with the variables, so both attempts are the same
    // logical comment and the server replays rather than duplicating.
    retry: 2,
    mutationFn: (variables: { body: string; idempotencyKey: string }) =>
      api.addComment(item.id, variables.body, variables.idempotencyKey),
    onSuccess: async () => {
      setBody('')
      // A comment changes no field of the item, so the item itself is not re-read; what it
      // produced — the timeline entry — is.
      await invalidateItemViews(queryClient, item.id, team.team_id)
    },
  })

  const comments = query.data?.pages.flatMap((page) => page.items) ?? []
  const canWrite = can(team.role, 'comment')
  const trimmed = body.trim()
  const tooLong = trimmed.length > MAX_LENGTH

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (trimmed === '' || tooLong) return
    post.mutate({ body: trimmed, idempotencyKey: newIdempotencyKey() })
  }

  return (
    <section className="card thread" aria-label="Comments">
      <h2 className="card__title">Comments</h2>

      {canWrite ? (
        <form className="thread__form" onSubmit={submit}>
          <label className="field">
            <span className="field__label">
              Add a comment ({trimmed.length}/{MAX_LENGTH})
            </span>
            <textarea
              name="body"
              rows={3}
              value={body}
              aria-invalid={tooLong}
              onChange={(event) => setBody(event.target.value)}
            />
          </label>
          {tooLong ? (
            <p className="field__error" role="alert">
              That is longer than {MAX_LENGTH} characters.
            </p>
          ) : null}
          {post.isError ? <ErrorState error={post.error} /> : null}
          <button
            type="submit"
            className="button button--primary"
            disabled={trimmed === '' || tooLong || post.isPending}
          >
            {post.isPending ? 'Posting…' : 'Post comment'}
          </button>
        </form>
      ) : (
        <p className="thread__readonly">
          You have read-only access to this team, so you can read the discussion but not add
          to it.
        </p>
      )}

      {query.isPending ? <Loading label="Loading comments…" /> : null}
      {query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : null}
      {query.isSuccess && comments.length === 0 ? (
        <Empty title="No comments yet" detail="Notes on this item will appear here, newest first." />
      ) : null}

      {comments.length > 0 ? (
        <ol className="thread__list">
          {comments.map((comment) => (
            <li key={comment.id} className="comment">
              <div className="comment__head">
                <span className="comment__author">{comment.author.display_name}</span>
                <time className="comment__time" dateTime={comment.created_at}>
                  {formatRelative(comment.created_at)} · {formatDateTime(comment.created_at)}
                </time>
              </div>
              <p className="comment__body">{comment.body}</p>
            </li>
          ))}
        </ol>
      ) : null}

      {query.hasNextPage ? (
        <button
          type="button"
          className="button button--quiet"
          disabled={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          {query.isFetchingNextPage ? 'Loading…' : 'Show older comments'}
        </button>
      ) : null}
    </section>
  )
}
