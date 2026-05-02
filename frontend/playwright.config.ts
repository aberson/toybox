// Minimal Playwright config used by the build-step orchestrator's
// UI-evidence pass. Vitest runs the unit tests; Playwright drives the
// /parent route end-to-end against a live backend on :8000 + vite on
// :4000. Step 9 doesn't ship CI for this; the orchestrator pulls the
// trace via `npx playwright test`.

import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./playwright",
  retries: 0,
  reporter: "list",
  use: {
    baseURL: "http://localhost:4000",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { browserName: "chromium" },
    },
  ],
});
