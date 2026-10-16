/**
 * The comment thread.
 *
 * Two things are being checked, and both are the reason this component is worth a test of
 * its own.
 *
 * **The comment appears because the server said so.** The thread is not appended to
 * locally; the mutation succeeds, the queries are invalidated, and the next read is what
 * the user sees.
 *
 * **The `Idempotency-Key` survives a retry.** The key is a mutation *variable*, generated
 * once per submission. This test makes the first attempt fail at the transport layer and
 * asserts that the retry carries the *same* key — which is what makes the retry safe
 * rather than a second comment. Generating the key inside the request function would break
 * exactly this, and no other test would notice.
 */

import { describe, expect, it } from 'vitest'
import type { QueryClient } from '@tanstack/react-query'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { errorResponse, installFetch, jsonResponse, callsTo } from '../../test/fetch'
import { ITEM_ID, NINA, comment, detail, membership, page } from '../../test/fixtures'
import { renderWithClient, testQueryClient } from '../../test/render'
import { CommentThread } from './CommentThread'

const COMMENTS_PATH = `/api/v1/items/${ITEM_ID}/comments`
const ITEM_PATH = `/api/v1/items/${ITEM_ID}`

function clientWithInstantRetries(): QueryClient {
  const client = testQueryClient()
  client.setDefaultOptions({
    queries: { retry: false, gcTime: 0, staleTime: 0 },
    // The component opts into retrying; the delay is removed so the test is not a race
    // against a backoff timer.
    mutations: { retry: false, retryDelay: 0 },
  })
  return client
}

describe('CommentThread', () => {
  it('posts the trimmed body with an idempotency key and shows the server’s copy', async () => {
    const calls = installFetch((request) => {
      if (request.path === COMMENTS_PATH && request.method === 'GET') {
        const posted = callsTo(calls, COMMENTS_PATH, 'POST').length > 0
        return jsonResponse(page(posted ? [comment({ body: 'Looks like a bad deploy.' })] : []))
      }
      if (request.path === COMMENTS_PATH && request.method === 'POST') {
        return jsonResponse(comment({ body: 'Looks like a bad deploy.' }), { status: 201 })
      }
      if (request.path === ITEM_PATH) return jsonResponse(detail())
      return errorResponse(404, 'not_found', 'No stub')
    })
    const user = userEvent.setup()

    renderWithClient(<CommentThread item={detail()} team={membership()} />, clientWithInstantRetries())

    expect(await screen.findByText(/no comments yet/i)).toBeInTheDocument()

    await user.type(screen.getByLabelText(/add a comment/i), '   Looks like a bad deploy.   ')
    await user.click(screen.getByRole('button', { name: /post comment/i }))

    const [post] = callsTo(calls, COMMENTS_PATH, 'POST')
    expect(post?.headers['idempotency-key']).toBeTruthy()
    // The server strips too, but the client should not send whitespace it can see.
    expect(post?.body).toEqual({ body: 'Looks like a bad deploy.' })

    expect(await screen.findByText('Looks like a bad deploy.')).toBeInTheDocument()
  })

  it('reuses the same key when a failed attempt is retried', async () => {
    let attempts = 0
    const calls = installFetch((request) => {
      if (request.path === COMMENTS_PATH && request.method === 'POST') {
        attempts += 1
        if (attempts === 1) throw new TypeError('Failed to fetch')
        return jsonResponse(comment(), { status: 201 })
      }
      if (request.path === COMMENTS_PATH) return jsonResponse(page([]))
      if (request.path === ITEM_PATH) return jsonResponse(detail())
      return errorResponse(404, 'not_found', 'No stub')
    })
    const user = userEvent.setup()

    renderWithClient(<CommentThread item={detail()} team={membership()} />, clientWithInstantRetries())

    await user.type(await screen.findByLabelText(/add a comment/i), 'A note worth retrying')
    await user.click(screen.getByRole('button', { name: /post comment/i }))

    await waitFor(() => expect(callsTo(calls, COMMENTS_PATH, 'POST')).toHaveLength(2))

    const posts = callsTo(calls, COMMENTS_PATH, 'POST')
    const keys = posts.map((post) => post.headers['idempotency-key'])
    // The same logical comment, so the same key: the server can tell a retry from a second
    // note, and the retry cannot produce a duplicate.
    expect(keys[0]).toBeTruthy()
    expect(keys[0]).toBe(keys[1])
  })

  it('refuses an empty comment before sending anything', async () => {
    const calls = installFetch(() => jsonResponse(page([])))
    const user = userEvent.setup()

    renderWithClient(<CommentThread item={detail()} team={membership()} />)

    const submit = await screen.findByRole('button', { name: /post comment/i })
    await user.type(screen.getByLabelText(/add a comment/i), '    ')
    expect(submit).toBeDisabled()
    expect(callsTo(calls, COMMENTS_PATH, 'POST')).toHaveLength(0)
  })

  it('says when the comment is too long rather than letting the server refuse it', async () => {
    installFetch(() => jsonResponse(page([])))
    const user = userEvent.setup()

    renderWithClient(<CommentThread item={detail()} team={membership()} />)

    const box = await screen.findByLabelText(/add a comment/i)
    // `user.type` would be far too slow for 4 001 characters; the component only reads the
    // value, and a paste is what a person would do anyway.
    await user.click(box)
    await user.paste('x'.repeat(4001))

    expect(await screen.findByText(/longer than 4000 characters/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /post comment/i })).toBeDisabled()
  })

  it('gives a viewer the thread but no way to post', async () => {
    installFetch(() => jsonResponse(page([comment()])))

    renderWithClient(<CommentThread item={detail()} team={membership({ role: 'viewer' })} />)

    expect(await screen.findByText('Looks like a bad deploy.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /post comment/i })).not.toBeInTheDocument()
    expect(screen.getByText(/read-only access/i)).toBeInTheDocument()
  })

  it('shows who wrote each comment', async () => {
    installFetch(() =>
      jsonResponse(page([comment({ author: NINA, body: 'Root cause was the deploy.' })])),
    )

    renderWithClient(<CommentThread item={detail()} team={membership()} />)

    expect(await screen.findByText('Nina Member')).toBeInTheDocument()
  })

  it('reports a failure to load the thread', async () => {
    installFetch(() => errorResponse(500, 'internal_error', 'Unexpected failure.'))

    renderWithClient(<CommentThread item={detail()} team={membership()} />)

    expect(await screen.findByRole('alert')).toHaveTextContent(/something went wrong/i)
  })
})
