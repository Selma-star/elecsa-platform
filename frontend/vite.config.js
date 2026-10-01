import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Local dev only - forwards /api/* to the backend running on
    // localhost:4000, so the frontend code can use relative "/api/..."
    // URLs everywhere, identically to how Caddy proxies them in
    // production. No "dev URL" vs "prod URL" to keep in sync.
    proxy: {
      '/api': 'http://localhost:4000',
    },
  },
})
