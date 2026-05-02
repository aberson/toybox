import { defineConfig } from "vitest/config";

// Vitest config separated from vite.config.ts so the dev server config
// stays untouched. The only Phase A concern: exclude `playwright/`
// from vitest since the e2e specs use @playwright/test (a different
// runner) and would otherwise be picked up by the default include glob.
export default defineConfig({
  test: {
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    exclude: ["node_modules", "dist", "playwright"],
    environment: "node",
  },
});
