// E2E smoke for the child kiosk UI. The kiosk renders a friendly
// idle screen until the parent approves an activity; we cannot drive
// the parent flow from this spec in isolation (the build orchestrator
// runs a coordinated flow elsewhere), so we exercise:
//
//   1. /child loads
//   2. the persona avatar fallback (letter circle) is visible
//   3. the idle hint "Waiting for play to start..." is visible
//
// Component coverage of the active-step + all-done branches lives in
// the vitest store/flow tests; full end-to-end (parent approves →
// child advances → all-done) is the orchestrator's job.

import { expect, test } from "@playwright/test";

test("child UI smoke: idle screen renders before any activity arrives", async ({ page }) => {
  await page.goto("/child");

  // The fallback persona avatar always renders. Give it generous
  // headroom for the first paint so flake doesn't bite.
  await expect(page.getByTestId("persona-avatar")).toBeVisible({
    timeout: 5_000,
  });

  await expect(page.getByTestId("child-idle")).toBeVisible({
    timeout: 5_000,
  });

  // The kiosk must NOT show a next-step button before an activity
  // has been approved upstream.
  await expect(page.getByTestId("next-step-button")).toHaveCount(0);
});
