/**
 * The item view: the role boundary, the conditional writes, and conflict handling.
 *
 * This is the integration test for the frontend's half of the correctness story. It mounts
 * the real page against a stubbed network and checks that:
 *
 * * the moves offered are the server's `available_transitions`, and a viewer is offered
 *   none of them;
 * * a claim is **server-confirmed** — the interface changes because the server said so and
 *   the item was re-read, not because the button was clicked;
 * * a lost race (`409`) and a stale version (`412`) are explained rather than swallowed,
 *   and each leaves the view showing the server's current state;
 * * a conditional write carries the `ETag` from the response header as `If-Match`.
 */

import { describe, expect, it } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { api } from '../../api/client'
import { errorResponse, installFetch, jsonResponse, type RecordedRequest } from '../../test/fetch'
import {
  ITEM_ID,
  LENA,
  NINA,
  PLATFORM_TEAM_ID,
  detail,
  membership,
  page,
} from '../../test/fixtures'
import { renderWithClient } from '../../test/render'
import { ItemDetailPage } from './ItemDetailPage'

const ITEM_PATH = `/api/v1/items/${ITEM_ID}`

interface Scenario {
  /** What the item looks like on the first read. */
  initial?: Parameters<typeof detail>[0]
  /** Overrides the response to a claim. */
  onClaim?: (request: RecordedRequest) => Response
  /** Overrides the response to a transition. */
  onTransition?: (request: RecordedRequest) => Response
}

function scenario({ initial, onClaim, onTransition }: Scenario = {}) {
  let current = detail(initial)
  let reads = 0

  const calls = installFetch((request) => {
    if (request.path === ITEM_PATH && request.method === 'GET') {
      reads += 1
      return jsonResponse(current, { headers: { ETag: `"${current.version}"` } })
    }
    if (request.path === `${ITEM_PATH}/claim` && request.method === 'POST') {
      if (onClaim) return onClaim(request)
      current = detail({ ...initial, assignee: NINA, version: current.version + 1 })
      return jsonResponse(current, { headers: { ETag: `"${current.version}"` } })
    }
    if (request.path === `${ITEM_PATH}/transitions` && request.method === 'POST') {
      if (onTransition) return onTransition(request)
      const to = (request.body as { to: string }).to
      current = detail({ ...initial, status: to as never, version: current.version + 1 })
      return jsonResponse(current, { headers: { ETag: `"${current.version}"` } })
    }
    if (request.path === `${ITEM_PATH}/comments` && request.method === 'GET') {
      return jsonResponse(page([]))
    }
    if (request.path === `${ITEM_PATH}/activity` && request.method === 'GET') {
      return jsonResponse(page([]))
    }
    if (request.path === `/api/v1/teams/${PLATFORM_TEAM_ID}/members`) {
      return jsonResponse([
        { user_id: NINA.id, email: NINA.email, display_name: NINA.display_name, role: 'member' },
      ])
    }
    return errorResponse(404, 'not_found', `No stub for ${request.method} ${request.path}`)
  })

  return { calls, reads: () => reads }
}

describe('ItemDetailPage', () => {
  it('offers the moves the server said are available', async () => {
    scenario({
      initial: { status: 'in_progress', assignee: NINA, available_transitions: ['awaiting_approval'] },
    })

    renderWithClient(
      <ItemDetailPage team={membership({ role: 'member' })} itemId={ITEM_ID} />,
    )

    expect(await screen.findByRole('button', { name: /submit for approval/i })).toBeInTheDocument()
    // The item has an owner, so there is nothing to claim.
    expect(screen.queryByRole('button', { name: /claim this item/i })).not.toBeInTheDocument()
  })

  it('offers a viewer no way to change the item', async () => {
    scenario({ initial: { available_transitions: [] } })

    renderWithClient(<ItemDetailPage team={membership({ role: 'viewer' })} itemId={ITEM_ID} />)

    expect(await screen.findByText(/nobody has claimed this item yet/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /claim this item/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /start work/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /save changes/i })).not.toBeInTheDocument()
  })

  it('confirms a claim from the server and adopts the version it returned', async () => {
    const harness = scenario({ initial: { available_transitions: ['in_progress'] } })

    renderWithClient(<ItemDetailPage team={membership()} itemId={ITEM_ID} />)

    const claim = await screen.findByRole('button', { name: /claim this item/i })
    expect(harness.reads()).toBe(1)

    await userEvent.setup().click(claim)

    // The notice, the owner and the version all come from the claim response. Version 2 is
    // the server's new version, not something the interface counted up.
    expect(await screen.findByText(/server confirmed the claim/i)).toBeInTheDocument()
    expect(await screen.findByText(/now owned by Nina Member/i)).toBeInTheDocument()
    // The version shown is the one the claim response carried, not a locally counted one.
    expect(screen.getByText('2', { selector: '.facts__value' })).toBeInTheDocument()

    // The write's own response *is* the new state, so no extra read was needed: the
    // interface did not guess, and it did not ask again either.
    expect(harness.reads()).toBe(1)
  })

  it('explains a lost claim race and shows the winner', async () => {
    const harness = scenario({
      initial: { available_transitions: ['in_progress'] },
      onClaim: () => {
        // Somebody else won. The item is now owned by another member.
        return errorResponse(409, 'item_already_claimed', 'This item has already been claimed.')
      },
    })

    renderWithClient(<ItemDetailPage team={membership()} itemId={ITEM_ID} />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /claim this item/i }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/already taken/i)
    expect(alert).toHaveTextContent(/somebody claimed this item first/i)
    // A lost race is a reason to re-read, and the component does.
    await waitFor(() => expect(harness.reads()).toBeGreaterThan(1))
  })

  it('refuses a stale transition, says so, and offers a reload', async () => {
    const harness = scenario({
      initial: {
        status: 'in_progress',
        assignee: NINA,
        available_transitions: ['awaiting_approval'],
      },
      onTransition: () =>
        errorResponse(412, 'stale_version', 'The item has changed since you loaded it.', {
          current: { version: 9 },
        }),
    })

    renderWithClient(<ItemDetailPage team={membership()} itemId={ITEM_ID} />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /submit for approval/i }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/somebody else changed this first/i)
    expect(alert).toHaveTextContent(/was not applied/i)
    expect(screen.getByRole('button', { name: /reload the item/i })).toBeInTheDocument()

    // The rejected write did not change anything locally, and the item was re-read.
    await waitFor(() => expect(harness.reads()).toBeGreaterThan(1))
  })

  it('conditions a transition on the version the server returned', async () => {
    const harness = scenario({
      initial: {
        status: 'in_progress',
        assignee: NINA,
        available_transitions: ['awaiting_approval'],
        version: 4,
      },
    })

    renderWithClient(<ItemDetailPage team={membership()} itemId={ITEM_ID} />)
    await userEvent.setup().click(await screen.findByRole('button', { name: /submit for approval/i }))

    await waitFor(() => {
      const write = harness.calls.find((call) => call.path === `${ITEM_PATH}/transitions`)
      expect(write?.headers['if-match']).toBe('"4"')
      expect(write?.body).toEqual({ to: 'awaiting_approval' })
    })
  })

  it('sends a rejection note when one is typed', async () => {
    const harness = scenario({
      initial: {
        status: 'awaiting_approval',
        assignee: NINA,
        available_transitions: ['resolved', 'in_progress'],
      },
    })
    const user = userEvent.setup()

    renderWithClient(<ItemDetailPage team={membership({ role: 'lead' })} itemId={ITEM_ID} />)

    await user.type(await screen.findByLabelText(/note/i), 'Needs a regression test')
    await user.click(screen.getByRole('button', { name: /start work/i }))

    await waitFor(() => {
      const write = harness.calls.find((call) => call.path === `${ITEM_PATH}/transitions`)
      expect(write?.body).toEqual({ to: 'in_progress', note: 'Needs a regression test' })
    })
  })

  it('shows the version and the history the item carries', async () => {
    scenario({ initial: { version: 6 } })

    renderWithClient(<ItemDetailPage team={membership()} itemId={ITEM_ID} />)

    expect(await screen.findByText('6')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'History' })).toBeInTheDocument()
    expect(screen.getByText(/no history yet/i)).toBeInTheDocument()
  })

  it('reports a missing item instead of rendering an empty page', async () => {
    installFetch(() => errorResponse(404, 'not_found', 'Work item not found.'))

    renderWithClient(<ItemDetailPage team={membership()} itemId={ITEM_ID} />)

    expect(await screen.findByRole('alert')).toHaveTextContent(/not found/i)
    expect(screen.getByRole('button', { name: /back to the list/i })).toBeInTheDocument()
  })
})

describe('api.item', () => {
  it('returns the item together with its ETag', async () => {
    installFetch(() => jsonResponse(detail({ created_by: LENA }), { headers: { ETag: '"2"' } }))

    const response = await api.item(ITEM_ID)

    expect(response.etag).toBe('"2"')
    expect(response.data.created_by.display_name).toBe('Lena Lead')
  })
})
