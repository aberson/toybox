// E2E smoke for the parent UI. Drives the path described in the issue:
//   1. load /parent
//   2. click TriggerButton
//   3. SuggestionCard appears
//   4. click approve
//   5. ActivityPanel renders
//   6. mic-hot indicator visible
//
// The build-step orchestrator runs this as the UI evidence step, not
// the per-PR test suite.

import { expect, test } from "@playwright/test";

test("parent UI smoke: trigger -> approve -> activity panel", async ({ page }) => {
  await page.goto("/parent");

  // Bootstrap fires async (token issue + health fetch + ws open). The
  // mic indicator is rendered from the start, but Playwright's default
  // visibility check can race the React first paint. Give it room.
  await expect(page.getByTestId("mic-indicator")).toBeVisible({
    timeout: 5_000,
  });

  const triggerBtn = page.getByTestId("trigger-button");
  await expect(triggerBtn).toBeVisible();
  await triggerBtn.click();

  const card = page.getByTestId("suggestion-card");
  await expect(card).toBeVisible({ timeout: 10_000 });

  await page.getByTestId("approve-button").click();

  await expect(page.getByTestId("activity-panel")).toBeVisible({
    timeout: 10_000,
  });

  // Mic indicator should still be visible on the activity-panel screen.
  await expect(page.getByTestId("mic-indicator")).toBeVisible();
});
