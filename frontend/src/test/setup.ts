/**
 * Test setup.
 *
 * `@testing-library/jest-dom` adds the DOM matchers. The explicit `cleanup` is required
 * because `globals: false` is set in `vite.config.ts`: without the Vitest globals, the
 * testing library cannot find `afterEach` to register its own cleanup, and every render
 * would stay in `document.body` for the next test to trip over. Doing it here keeps the
 * global surface at zero while still isolating each test.
 *
 * Nothing else is global — each test imports what it needs from `vitest`, so a test file
 * can be read without wondering where a matcher came from.
 */

import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(() => {
  cleanup()
})
