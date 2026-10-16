/**
 * Rendering helpers.
 *
 * Each test gets its own `QueryClient` with retries off and no cache reuse, so one test
 * cannot pass because of data another test left behind, and a deliberate error is not
 * quietly retried into a different code path.
 *
 * The client is the *production* client factory where the behaviour under test is the
 * production behaviour (the `401` handling, the retry policy); where a test wants to
 * assert on a single failure, it passes its own.
 */

import type { ReactElement, ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, type RenderResult } from '@testing-library/react'

export function testQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  })
}

export function renderWithClient(
  ui: ReactElement,
  client: QueryClient = testQueryClient(),
): RenderResult & { client: QueryClient } {
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
  return Object.assign(render(ui, { wrapper: Wrapper }), { client })
}
