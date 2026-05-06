// E2E synthetic-audio smoke documentation spec.
//
// This file documents the Playwright assertion shape used by the
// authoritative driver in ``tests/e2e/test_smoke_pipeline.py`` (the
// Python pytest harness boots both backend + frontend subprocesses,
// then drives the browser via the Python ``playwright.async_api``).
// Keeping the assertions duplicated here gives the frontend dev a
// single-language reference for the testid contract; the Python side
// is what actually runs in CI / the build-step orchestrator.
//
// Marked with ``test.skip`` so ``npx playwright test`` from
// ``frontend/`` does not attempt to drive the synthetic-audio path
// without the full Python harness around it. To run the full smoke,
// invoke ``uv run pytest -m slow tests/e2e/test_smoke_pipeline.py``
// from the repo root.

import { expect, test } from "@playwright/test";

test.skip(true, "Smoke is driven by tests/e2e/test_smoke_pipeline.py via Python Playwright");

test("synthetic-audio smoke: WAV -> VAD -> STT -> trigger -> approve -> child", async ({
  browser,
}) => {
  // Documentation only — the Python harness implements this same shape.
  // The flow:
  //
  //   1. Parent loads /parent.
  //   2. Backend's --smoke lifespan plays
  //      tests/fixtures/audio/lets_play_unicorns.wav into the real VAD
  //      + faster-whisper + trigger pipeline.
  //   3. Above-floor transcript ("let's play unicorns") fires the
  //      ``request_play``/``unicorns`` intent through the escalation
  //      dispatcher; offline path persists a `proposed` activity and
  //      emits an ``activity.state`` envelope.
  //   4. SuggestionCard renders on the parent page.
  //   5. Approve → ActivityPanel renders.
  //   6. Second browser context loads /child; reconnect-resync
  //      re-hydrates the activity and PersonaAvatar renders.
  const parentCtx = await browser.newContext();
  const childCtx = await browser.newContext();

  const parentPage = await parentCtx.newPage();
  await parentPage.goto("/parent");
  await expect(parentPage.getByTestId("suggestion-card")).toBeVisible({
    timeout: 30_000,
  });
  await parentPage.getByTestId("approve-button").click();
  await expect(parentPage.getByTestId("activity-panel")).toBeVisible({
    timeout: 15_000,
  });

  const childPage = await childCtx.newPage();
  await childPage.goto("/child");
  await expect(childPage.getByTestId("persona-avatar")).toBeVisible({
    timeout: 15_000,
  });
  // Phase F Step F7: when the activity's current step has
  // ``action_slot`` set AND the activity has at least one toy, the
  // kiosk renders a ``ToyActionSprite`` next to the step body. The
  // smoke runs against a fixture activity whose first step pins a
  // known slot ("looking" by default — see the boredom template).
  // The sprite is best-effort: a 404 (capability disabled or
  // generation pending) hides it gracefully, so we use ``.or()`` with
  // the body-only branch as the fallback assertion path.
  await expect(
    childPage.getByTestId("toy-action-sprite").or(childPage.getByTestId("step-text")),
  ).toBeVisible({ timeout: 15_000 });

  await parentCtx.close();
  await childCtx.close();
});
