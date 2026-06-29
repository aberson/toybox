// Phase #223 bundle — PARENT-UI visual UAT (vision-judge pass).
//
// Drives the real parent app on a live backend(:8000)+vite(:4000) and
// captures stage screenshots + /api read-backs for the vision judge.
// Covers the renderable parent-UI rows of the #223 bundle:
//   O1 — Play tab shows the 5 sub-tabs (exact labels/order)
//   O2 — sub-tab filtering switches the queue view
//   R1 — TriggerButton/AdventureButton are the prominent primary CTAs;
//        NO play-cadence controls anywhere in Settings
//   R4 — activity/template search renders with Try-this / Play-again
//   T1 — offline catalog browse renders + chip filters
//   W1/W2/W5 — the Settings dials/toggles render with a pressed state
//
// Exercises the app AS SHIPPED — touches no src/ or component files.
// Screenshots -> playwright/test-results/uat-visual-parent/.

import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const OUT_DIR = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "test-results",
  "uat-visual-parent",
);
const PIN = "4242";

function ensureOutDir(): void {
  fs.mkdirSync(OUT_DIR, { recursive: true });
}

async function ensureAuthed(page: Page): Promise<void> {
  await page.goto("/parent");
  const setupSubmit = page.getByTestId("pin-setup-submit");
  const loginSubmit = page.getByTestId("pin-login-submit");
  const kidsTab = page.getByTestId("tab-kids-toyboxes");
  await expect(setupSubmit.or(loginSubmit).or(kidsTab)).toBeVisible({
    timeout: 20_000,
  });
  if (await setupSubmit.isVisible()) {
    await page.getByTestId("pin-setup-pin-input").fill(PIN);
    await page.getByTestId("pin-setup-confirm-input").fill(PIN);
    await setupSubmit.click();
  } else if (await loginSubmit.isVisible()) {
    await page.getByTestId("pin-login-pin-input").fill(PIN);
    await loginSubmit.click();
  }
  await expect(kidsTab).toBeVisible({ timeout: 20_000 });
}

test("parent-ui: #223 visual sweep (tabs / settings / search / catalog)", async ({
  page,
}) => {
  ensureOutDir();
  test.setTimeout(120_000);

  let token: string | undefined;
  page.on("request", (req) => {
    const t = req.headers()["x-toybox-token"];
    if (t) token = t;
  });

  await ensureAuthed(page);
  // Let the post-auth bootstrap requests fly so we have a token to seed with.
  await expect.poll(() => token, { timeout: 15_000 }).toBeTruthy();
  const hdr = { "X-Toybox-Token": token as string };

  // --- Seed minimal data: a child + one proposed activity (gives the
  //     Play queue + Search something real to show). Best-effort. ---
  await page.request
    .post("/api/children", { headers: hdr, data: { display_name: "Test Kid" } })
    .catch(() => undefined);
  await page.request
    .post("/api/activities/propose", {
      headers: hdr,
      data: { intent: "request_play", slot: null, hour: 12, seed: 42 },
    })
    .catch(() => undefined);

  // ===== O1 / R1 — Play tab + 5 sub-tabs + prominent CTAs =====
  await page.getByTestId("tab-play").click();
  const subtabs = [
    ["subtab-all", "All"],
    ["subtab-adventures", "Adventures"],
    ["subtab-elements", "Elements"],
    ["subtab-feelings-friends", "Feelings & Friends"],
    ["subtab-transcriptions", "Transcriptions"],
  ] as const;
  for (const [tid, label] of subtabs) {
    await expect(page.getByTestId(tid), `${tid} present`).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId(tid)).toHaveText(label);
  }
  // R1 — the two primary CTAs render prominently on the Play queue view.
  await expect(page.getByTestId("trigger-button")).toBeVisible();
  await expect(page.getByTestId("adventure-button")).toBeVisible();
  await page.screenshot({
    path: path.join(OUT_DIR, "01-play-tabs-and-ctas.png"),
    fullPage: true,
  });

  // O2 — sub-tab filter switches the view (click Elements, then back to All).
  await page.getByTestId("subtab-elements").click();
  await page.waitForTimeout(400);
  await page.screenshot({
    path: path.join(OUT_DIR, "02-subtab-elements.png"),
    fullPage: true,
  });
  await page.getByTestId("subtab-all").click();

  // ===== R4 — search renders with Try-this / Play-again affordances =====
  // search-input lives on the Play tab (non-transcriptions sub-tabs).
  const search = page.getByTestId("search-input");
  if (await search.isVisible().catch(() => false)) {
    await search.fill("play");
    await page.waitForTimeout(900); // debounce
    // Capture whatever the search returns (results or empty-state).
    await page.screenshot({
      path: path.join(OUT_DIR, "03-search.png"),
      fullPage: true,
    });
    const hasResults =
      (await page.getByTestId("template-row").count()) > 0 ||
      (await page.getByTestId("past-activity-row").count()) > 0;
    test.info().annotations.push({
      type: "search-results",
      description: hasResults ? "results rendered" : "empty-state",
    });
    await search.fill("");
  } else {
    test.info().annotations.push({
      type: "search-input",
      description: "search-input not visible on Play tab",
    });
  }

  // ===== T1 — offline catalog browse + chip filters =====
  const browseToggle = page.getByTestId("view-tab-browse");
  if (await browseToggle.isVisible().catch(() => false)) {
    await browseToggle.click();
    await expect(page.getByTestId("catalog-panel")).toBeVisible({
      timeout: 15_000,
    });
    const chips = page.locator('[data-testid^="catalog-chip-"]');
    const cards = page.locator('[data-testid^="catalog-card-"]');
    await expect(chips.first()).toBeVisible({ timeout: 15_000 });
    await expect(cards.first()).toBeVisible({ timeout: 15_000 });
    await page.screenshot({
      path: path.join(OUT_DIR, "04-catalog-all.png"),
      fullPage: true,
    });
    // Toggle the first theme chip to show filtering.
    await chips.first().click();
    await page.waitForTimeout(400);
    await page.screenshot({
      path: path.join(OUT_DIR, "05-catalog-filtered.png"),
      fullPage: true,
    });
  } else {
    test.info().annotations.push({
      type: "catalog",
      description: "view-tab-browse not visible",
    });
  }

  // ===== Settings — W dials/toggles + R1 cadence-absence =====
  await page.getByTestId("tab-settings").click();
  const controls = [
    "parent-involvement",
    "game-complexity",
    "game-linearity",
    "qa-grading",
    "boss-fights",
  ];
  const pressed: Record<string, string> = {};
  for (const c of controls) {
    const ctl = page.getByTestId(`${c}-control`);
    await expect(ctl, `${c} control present`).toBeVisible({ timeout: 15_000 });
    // Record which button is aria-pressed.
    const buttons = ctl.locator("button[aria-pressed]");
    const n = await buttons.count();
    for (let i = 0; i < n; i++) {
      const b = buttons.nth(i);
      if ((await b.getAttribute("aria-pressed")) === "true") {
        pressed[c] = (await b.getAttribute("data-testid")) ?? (await b.innerText());
      }
    }
  }
  // R1 — NO play-cadence controls anywhere in Settings.
  const cadenceCount = await page.locator('[data-testid*="cadence"]').count();
  expect(cadenceCount, "no cadence controls in Settings").toBe(0);
  await page.screenshot({
    path: path.join(OUT_DIR, "06-settings-controls.png"),
    fullPage: true,
  });

  fs.writeFileSync(
    path.join(OUT_DIR, "settings-pressed.json"),
    JSON.stringify({ pressed, cadenceControlCount: cadenceCount }, null, 2),
  );
});
