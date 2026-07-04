import { defineConfig } from "vitest/config";

// Vitest config separated from vite.config.ts so the dev server config
// stays untouched. The only Phase A concern: exclude `playwright/`
// from vitest since the e2e specs use @playwright/test (a different
// runner) and would otherwise be picked up by the default include glob.
export default defineConfig({
  test: {
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    exclude: ["node_modules", "dist", "playwright"],
    // Per-test environment: pure-reducer .test.ts files default to node
    // (no DOM needed); Step 18's .test.tsx files mount React components
    // and need a DOM. ws.test.ts files instantiate CloseEvent (not a
    // Node global on Node 20) so route them to happy-dom as well.
    // clip-audio.test.ts (Phase Z Z5) needs a real `window` object to
    // stage its window.Audio fake on — same happy-dom routing.
    environment: "node",
    environmentMatchGlobs: [
      ["src/**/*.test.tsx", "happy-dom"],
      ["src/**/ws.test.ts", "happy-dom"],
      ["src/**/clip-audio.test.ts", "happy-dom"],
    ],
  },
});
