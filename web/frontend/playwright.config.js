import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 30000,
  retries: 1,
  use: {
    baseURL: 'http://localhost:7860',
    viewport: { width: 375, height: 667 },
    actionTimeout: 10000,
  },
  webServer: {
    command: 'npx vite --port 7860',
    port: 7860,
    reuseExistingServer: false,
    timeout: 15000,
    env: {
      VITE_PROXY_TARGET: 'http://localhost:7861',
    },
  },
})
