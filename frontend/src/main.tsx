/**
 * Entry point.
 *
 * The query client is created once, outside React, so it survives a re-render and so its
 * error handling (a `401` ends the session) is installed before any request is made.
 */

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'

import { App } from './App'
import { createQueryClient } from './query/client'
import './styles.css'

const queryClient = createQueryClient()

const container = document.getElementById('root')
if (!container) throw new Error('The #root element is missing from index.html')

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
