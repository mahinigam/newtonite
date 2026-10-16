/**
 * The moves an item can make, and the claim button.
 *
 * Both are **server-confirmed**: the button sends the request, waits for the server's
 * answer, and only then does the interface change. There is no optimistic update here on
 * purpose. A claim that optimistically succeeded and then came back `409` would have to be
 * rolled back in front of the user, and a transition that optimistically advanced the
 * status and then came back `412` would briefly show a workflow state that never existed.
 * For operations whose whole point is that exactly one caller wins, showing the answer
 * before it is known is the wrong trade.
 *
 * The transitions offered are `available_transitions` from the item itself, so the buttons
 * and the server's rules cannot disagree.
 *
 * The `ETag` is the item's version in HTTP form, and it comes from the response *header*,
 * not the body — which is why it is threaded through as its own value rather than read off
 * the item. Every conditional write sends it back as `If-Match`.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '../../api/client'
import type { Membership, Status, WorkItemDetail } from '../../api/types'
import { InlineError } from '../../components/states'
import { transitionLabel } from '../../lib/format'
import { can } from '../../lib/permissions'
import { invalidateItemViews, putItem, refreshItem } from '../../query/client'

export function ItemActions({
  item,
  etag,
  team,
  onReload,
}: {
  item: WorkItemDetail
  etag: string | null
  team: Membership
  onReload: () => void
}) {
  const queryClient = useQueryClient()
  const [note, setNote] = useState('')
  const [error, setError] = useState<unknown>(null)

  /**
   * Accept the server's answer, then re-read what depends on it.
   *
   * The write's response is the new state of the item, so it goes straight into the cache;
   * the timeline, the discussion and the team's lists are re-read because they describe the
   * same item.
   */
  const accept = async (updated: WorkItemDetail, updatedEtag: string | null) => {
    putItem(queryClient, { item: updated, etag: updatedEtag })
    await invalidateItemViews(queryClient, updated.id, team.team_id)
  }

  /** A refused write leaves the local copy known-stale, so re-read it before the user retries. */
  const reject = async (cause: unknown) => {
    setError(cause)
    await refreshItem(queryClient, item.id)
    await invalidateItemViews(queryClient, item.id, team.team_id)
  }

  const claim = useMutation({
    mutationFn: () => api.claimItem(item.id),
    onSuccess: (updated) => accept(updated, etag),
    onError: reject,
  })

  const transition = useMutation({
    mutationFn: (variables: { to: Status; note: string | null }) => {
      if (etag === null) throw new Error('This item has no version to condition on.')
      return api.transitionItem(item.id, variables.to, variables.note, etag)
    },
    onSuccess: (response) => accept(response.data, response.etag),
    onError: reject,
  })

  const busy = claim.isPending || transition.isPending
  const canClaim = can(team.role, 'claim_item') && item.assignee === null
  const canMove = can(team.role, 'transition_work') && etag !== null
  const awaitingApproval = item.status === 'awaiting_approval'
  const noMoves = !canClaim && (!canMove || item.available_transitions.length === 0)

  return (
    <section className="card actions" aria-label="Item actions">
      <h2 className="card__title">Actions</h2>

      {error ? (
        <InlineError error={error} onReload={onReload} onDismiss={() => setError(null)} />
      ) : null}

      {claim.isSuccess ? (
        <p className="notice notice--ok">
          The server confirmed the claim
          {claim.data.assignee ? ` — now owned by ${claim.data.assignee.display_name}` : ''}.
        </p>
      ) : null}

      <div className="actions__row">
        {canClaim ? (
          <button
            type="button"
            className="button button--primary"
            disabled={busy}
            onClick={() => {
              setError(null)
              claim.mutate()
            }}
          >
            {claim.isPending ? 'Claiming…' : 'Claim this item'}
          </button>
        ) : null}

        {canMove
          ? item.available_transitions.map((target) => (
              <button
                key={target}
                type="button"
                className="button"
                disabled={busy}
                onClick={() => {
                  setError(null)
                  transition.mutate({ to: target, note: note.trim() === '' ? null : note.trim() })
                }}
              >
                {transitionLabel(target)}
              </button>
            ))
          : null}

        {noMoves ? (
          <p className="actions__none">
            {item.status === 'closed'
              ? 'This item is closed. There is nothing further to do.'
              : item.assignee === null
                ? 'Nobody has claimed this item yet.'
                : 'No moves are available to you on this item right now.'}
          </p>
        ) : null}
      </div>

      {awaitingApproval && canMove ? (
        <label className="field">
          <span className="field__label">
            Note (optional — a rejection records why the work is coming back)
          </span>
          <input
            name="note"
            maxLength={2000}
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
        </label>
      ) : null}

      <p className="actions__hint">
        Every move is a conditional write: it carries the version this page was loaded with,
        and the server refuses it if somebody else has moved the item since.
      </p>
    </section>
  )
}
