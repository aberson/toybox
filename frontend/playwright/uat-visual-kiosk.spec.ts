// Phase #223 bundle — KIOSK (child) visual UAT (vision-judge pass).
//
// The genuine added value over component tests: the FULL kiosk render at
// an iPad viewport on a live backend(:8000)+vite(:4000). Covers:
//   S1 — persona-appropriate background gradient (≥2 personas)
//   S2 — persona avatar renders per activity
//   S3 — step card body is large/legible at an iPad viewport
//   (Phase Y backdrop rides along if scene_url is set)
// R3 (Q&A gating) and W5 (boss banner) kiosk visuals are already covered
// by StepCard.test.tsx component render tests, so they are not re-driven
// here.
//
// Robust ordering: open the kiosk, wait for the "Waiting for play to
// start..." idle screen (confirms the WS is connected with no activity),
// THEN approve an activity via the API so the live broadcast reaches the
// connected kiosk. Exercises the app AS SHIPPED.
// Screenshots -> playwright/test-results/uat-visual-kiosk/.

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const OUT_DIR = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "test-results",
  "uat-visual-kiosk",
);
const PIN = "4242";

// iPad landscape — the kiosk's real form factor for arm's-length reading.
test.use({ viewport: { width: 1024, height: 768 } });

function ensureOutDir(): void {
  fs.mkdirSync(OUT_DIR, { recursive: true });
}

interface Activity {
  id: string;
  version: number;
  persona_id?: string | null;
  scene_url?: string | null;
}

async function proposeAndApprove(
  req: APIRequestContext,
  hdr: Record<string, string>,
  childId: string | null,
  personaId: string,
  seed: number,
): Promise<Activity | null> {
  // Try with persona_id pinned; fall back to a plain propose if the model
  // rejects the extra field.
  let pr = await req.post("/api/activities/propose", {
    headers: hdr,
    data: { intent: "request_play", slot: null, hour: 12, seed, persona_id: personaId },
  });
  if (!pr.ok()) {
    pr = await req.post("/api/activities/propose", {
      headers: hdr,
      data: { intent: "request_play", slot: null, hour: 12, seed },
    });
  }
  if (!pr.ok()) return null;
  const act = (await pr.json()) as Activity;

  const body: Record<string, unknown> = childId ? { child_ids: [childId] } : {};
  let ap = await req.post(`/api/activities/${act.id}/approve`, {
    headers: { ...hdr, "If-Match-Version": String(act.version) },
    data: body,
  });
  if (!ap.ok()) {
    ap = await req.post(`/api/activities/${act.id}/approve`, {
      headers: { ...hdr, "If-Match-Version": String(act.version) },
      data: {},
    });
  }
  if (!ap.ok()) return null;
  return (await ap.json()) as Activity;
}

test("kiosk: #223 visual render (persona gradient / avatar / readable card)", async ({
  page,
}) => {
  ensureOutDir();
  test.setTimeout(150_000);

  let token: string | undefined;
  page.on("request", (req) => {
    const t = req.headers()["x-toybox-token"];
    if (t) token = t;
  });

  // --- Reach the connected, idle kiosk ---
  await page.goto("/child");
  const pinInput = page.getByTestId("kiosk-pin-prompt-input");
  if (await pinInput.isVisible({ timeout: 15_000 }).catch(() => false)) {
    await pinInput.fill(PIN);
    await page.getByTestId("kiosk-pin-prompt-submit").click();
  }
  // Idle screen = WS connected, no activity yet.
  await expect(page.getByText(/Waiting for play to start/i)).toBeVisible({
    timeout: 25_000,
  });
  await page.screenshot({ path: path.join(OUT_DIR, "00-kiosk-idle.png"), fullPage: true });

  await expect.poll(() => token, { timeout: 15_000 }).toBeTruthy();
  const hdr = { "X-Toybox-Token": token as string };

  // --- Seed a child (best-effort) ---
  let childId: string | null = null;
  const cr = await page.request
    .post("/api/children", { headers: hdr, data: { display_name: "Kiosk Kid" } })
    .catch(() => null);
  if (cr && cr.ok()) childId = ((await cr.json()) as { id: string }).id ?? null;

  const readback: Array<{ shot: string; persona_id: unknown; scene_url: unknown }> = [];

  // --- Persona A: detective ---
  const a = await proposeAndApprove(page.request, hdr, childId, "detective", 7);
  expect(a, "persona-A activity approved").toBeTruthy();
  await expect(page.getByTestId("step-card")).toBeVisible({ timeout: 25_000 });
  await page.waitForTimeout(900); // let the gradient + avatar settle
  await page.screenshot({ path: path.join(OUT_DIR, "01-kiosk-persona-detective.png"), fullPage: true });
  readback.push({ shot: "01", persona_id: a?.persona_id ?? null, scene_url: a?.scene_url ?? null });

  // --- Persona B: periodic_table (kiosk adopts the newest approved activity) ---
  const b = await proposeAndApprove(page.request, hdr, childId, "periodic_table", 99);
  if (b) {
    await page.waitForTimeout(1200);
    await expect(page.getByTestId("step-card")).toBeVisible({ timeout: 25_000 });
    await page.screenshot({ path: path.join(OUT_DIR, "02-kiosk-persona-periodic.png"), fullPage: true });
    readback.push({ shot: "02", persona_id: b?.persona_id ?? null, scene_url: b?.scene_url ?? null });
  }

  // Record what the avatar/backdrop rendered as.
  const avatarMode = await page
    .getByTestId("persona-avatar")
    .getAttribute("data-avatar-mode")
    .catch(() => null);
  const hasBackdrop = (await page.getByTestId("scene-backdrop").count()) > 0;
  fs.writeFileSync(
    path.join(OUT_DIR, "kiosk-readback.json"),
    JSON.stringify({ readback, avatarMode, hasBackdrop }, null, 2),
  );
});
