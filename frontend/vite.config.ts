/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// No dev proxy: the browser talks to `VITE_API_BASE_URL` directly, and the backend lists
// the Vite origin in `CORS_ALLOWED_ORIGINS`. Keeping the two origins distinct means the
// same configuration works in development and against a deployed API.
//
// `strictPort` is deliberate. Silently moving to :5174 would put the browser on an origin
// the backend does not allow, and the failure would look like a broken API rather than a
// port clash.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
  },
  test: {
    environment: 'jsdom',
    globals: false,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    restoreMocks: true,
    unstubGlobals: true,
  },
})
