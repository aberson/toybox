# Phase iPad-Kiosk — Child kiosk on iPad (PWA, post-v1)

> **ARCHIVED 2026-05-11: phase shipped.** See [plan.md status](../../plan.md#status) for the authoritative completion record. Internal cross-refs in this doc are frozen as of archival.

> **Scope:** iPad-Kiosk build plan — turn `/child` into an installable PWA via Add-to-Home-Screen. Carries the per-step `**Problem:**/**Type:**/**Issue:**/**Flags:**/**Status:**/**Done when:**` shape that `/build-phase` parses, plus the M_iK manual UAT script and Backlog enhancements (Tailscale+HTTPS, Service Worker, Capacitor). Sequenced after Phase D (PIN gate ships LAN binding); independent of Phase E (local model) and Phase F (toy action sprites). Top-level overview is in [../plan.md](../plan.md).

## What this feature does

Turn the existing `/child` React route into an installable iPad app via the Progressive Web App "Add to Home Screen" path. After install, the iPad shows a toybox icon on its home screen; tapping it launches the kiosk full-screen with no Safari chrome, indistinguishable from a native app for the child.

Goal is family-private testing only — no App Store distribution, no Apple Developer account, no Mac/Xcode. The frontend already ships everything a kiosk needs (full-bleed layout, large touch targets, PIN bootstrap, audio SFX with silent fallback); this phase adds the PWA scaffolding that makes Safari treat it as an installable app, plus iPad-specific ergonomics (landscape lock, Wake Lock, safe-area padding, audio unlock).

Network reachability for v1 of this feature is **plain HTTP over LAN** — the iPad joins the home Wi-Fi and connects to `http://<lan-ip>:4000/child`. The Phase D PIN gate already protects LAN binding ([how-to-run.md "LAN trust assumption"](how-to-run.md#run-dev--child-tablet-on-lan-phase-d-and-later-only)). HTTPS-over-Tailscale is in [§"Backlog"](#backlog) below for a follow-up phase.

## Existing context

- **Child kiosk** at [frontend/src/child/App.tsx](../frontend/src/child/App.tsx) renders full-bleed (fixed positioning + flexbox + `clamp()` font sizing). Components: [KioskPinPrompt](../frontend/src/child/components/KioskPinPrompt.tsx), [StepCard](../frontend/src/child/components/StepCard.tsx), [ToyActionSprite](../frontend/src/child/components/ToyActionSprite.tsx) (Phase F), [NextStepButton](../frontend/src/child/components/NextStepButton.tsx), [PersonaAvatar](../frontend/src/child/components/PersonaAvatar.tsx). Single big "Next" button, persona avatar (240px) at the top, step body text with optional 96–128px sprite.
- **Frontend HTML shell** at [frontend/index.html](../frontend/index.html) has only `<meta name="viewport" content="width=device-width, initial-scale=1.0">`. No manifest, no apple-touch-icon, no `apple-mobile-web-app-*` meta tags, no service worker.
- **Audio SFX** at [frontend/src/child/audio/sfx.ts](../frontend/src/child/audio/sfx.ts) preloads `transition.wav` + `success.wav` via HTML5 `Audio` with silent fallback on 404 / autoplay block. iOS Safari requires a user-gesture-triggered `.play()` before it will play any audio — the existing PIN tap is the natural unlock point but it isn't wired today.
- **WebSocket client** at [frontend/src/child/ws.ts](../frontend/src/child/ws.ts) chooses `wss://` on HTTPS pages and `ws://` on HTTP. Plain-HTTP LAN deployment uses `ws://` — works fine on iPadOS Safari.
- **Auth** at boot: [api.ts:196](../frontend/src/child/api.ts#L196) `issueParentToken({pin})` → `POST /api/auth/parent`. Kiosk uses parent token (note in api.ts:72: "A dedicated child/kiosk pairing flow arrives in Phase D Step 20" — still pending, not blocking for this phase).
- **Backend LAN binding** is already PIN-gated ([how-to-run.md "Run dev — child tablet on LAN"](how-to-run.md#run-dev--child-tablet-on-lan-phase-d-and-later-only)). `uv run python -m toybox.main --host 0.0.0.0 --port 8000` works once Phase D step 21 has set the PIN. No backend code changes needed for this phase.
- **Vite dev server** pinned to port 4000, `strictPort: true`, proxies `/api` and `/ws` to `:8000` ([feedback_vite_dev_port memory](../../../../.claude/projects/c--Users-abero-dev/memory/feedback_vite_dev_port.md)). For iPad LAN access, frontend must be served with `--host 0.0.0.0` (existing operator step in [plan/how-to-run.md](how-to-run.md#run-dev--child-tablet-on-lan-phase-d-and-later-only)).
- **Plan format conventions** mature: this doc mirrors [phase-e.md](phase-e.md) and [phase-f-toy-action-sprites.md](archive/phase-f-toy-action-sprites.md) for `/build-phase` compatibility.
- **Operating mode** for autonomous build: per [`feedback_autonomous_build_bundled_ui.md`](../../../../.claude/projects/c--Users-abero-dev/memory/feedback_autonomous_build_bundled_ui.md) the user prefers code-only/code-with-UI runs back-to-back with `--reviewers code`; visual UI verification batches into one human-driven test pass at the end (Step iK5 is that pass).

## Scope

**In:**
- Web App Manifest at `frontend/public/manifest.webmanifest` — `name`, `short_name`, `display: "standalone"`, `orientation: "landscape"`, `start_url: "/child"`, `theme_color: "#fefefe"`, `background_color: "#f4f4f7"` (matching the gradient endpoints in [App.tsx:95](../frontend/src/child/App.tsx#L95) `linear-gradient(180deg, #fefefe 0%, #f4f4f7 100%)`), icon entries
- Apple-touch-icon (180×180) and manifest icons (192×192, 512×512) generated from a single source SVG via a `sharp`-based Node script at `frontend/scripts/generate-icons.mjs`; `sharp` added as a `devDependency`; SVG + PNGs shipped under `frontend/public/icons/`. **Icon visual design:** placeholder is acceptable for v1 — a 512×512 rounded-square with the kiosk gradient + a centered white "T" glyph (or any non-transparent SVG; final design can iterate post-UAT without re-running this phase)
- iOS-specific meta tags in [index.html](../frontend/index.html): `apple-mobile-web-app-capable`, `apple-mobile-web-app-status-bar-style`, `apple-mobile-web-app-title`, `viewport-fit=cover`
- Safe-area-inset CSS padding on the kiosk root so the rounded-corner / camera-notch zones don't clip content
- Screen Wake Lock acquired while an activity is running (`navigator.wakeLock.request('screen')`); released on activity end / unmount / visibility change; graceful no-op on browsers without support (iPadOS <16.4)
- iOS audio unlock: prime `transition.wav` and `success.wav` `Audio` elements with a silent `.play().then(p => p.pause())` on the first user gesture (the PIN-prompt submit tap); idempotent (safe to call multiple times — subsequent calls are harmless no-ops)
- Touch-target audit: confirm [NextStepButton.tsx](../frontend/src/child/components/NextStepButton.tsx) and [KioskPinPrompt.tsx](../frontend/src/child/components/KioskPinPrompt.tsx) have ≥44pt hit areas (likely already true; verify and document)
- Operator install procedure: new `documentation/operator/ipad-setup.md` covering LAN setup recap, Wi-Fi requirements (iPad must be on the same SSID as the home machine — guest networks and isolated SSIDs do NOT work), Add-to-Home-Screen flow on iPadOS, Guided Access configuration (single-app lock, no code change), troubleshooting (audio silent, WS won't connect, sleep-mid-activity)
- Visual UI verification on a real iPad against the home Wi-Fi LAN — golden path (PIN → activity → completion), reconnect after Wi-Fi blip, audio plays after first tap, screen stays awake during activity

**Out:**
- HTTPS / TLS — plain HTTP over LAN only for this phase (Tailscale + Let's Encrypt is in Backlog)
- Service Worker / offline cache — requires HTTPS on the iPad anyway, deferred to Backlog
- Capacitor / native iOS wrapper — would require Mac + Xcode + Apple Developer account, not in scope for "free / private / for testing"
- Push notifications — kiosk model is foreground-only, not needed
- iPhone form factor — kiosk layout is designed for iPad landscape; phone support is out of scope
- Portrait orientation — locked to landscape; vertical kiosk is a future variant if requested
- Dedicated child/kiosk pairing token — kiosk continues to use parent token per [api.ts:72](../frontend/src/child/api.ts#L72) note; that work is tracked separately (was originally "Phase D Step 20" placeholder, now part of v1.5 scope)
- Auto-update / version pinning — Add-to-Home-Screen launches always fetch fresh from Vite/backend; no version stickiness needed
- Token security on shared iPads — kiosk persists the parent token in `localStorage` for kiosk session continuity. On a shared iPad without Guided Access, a child who pinches out of the kiosk could in principle access the cached token. Security boundary for this phase is **Guided Access** (operator-configured, documented in iK5), NOT the token lifecycle. Hardening the token (short expiry, child-scoped pairing token) is the v1.5 dedicated-pairing-flow work referenced above.

## Steps

| # | Step | Reviewers | Done-when (programmatic — manual iPad checks all live in iK5) |
|---|------|-----------|-----------|
| iK1 | PWA manifest + icons + iOS meta tags | `--reviewers code` | `frontend/public/manifest.webmanifest` exists with `display: "standalone"`, `orientation: "landscape"`, `start_url: "/child"`, `theme_color: "#fefefe"`, `background_color: "#f4f4f7"`; `frontend/public/icons/` ships `source.svg`, `apple-touch-icon-180.png`, `icon-192.png`, `icon-512.png`, generated by `frontend/scripts/generate-icons.mjs` using `sharp`; `npm run generate:icons` regenerates all PNGs from the SVG; [index.html](../frontend/index.html) adds `<link rel="manifest">`, `<link rel="apple-touch-icon">`, `<meta name="apple-mobile-web-app-capable" content="yes">`, `<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">`, `<meta name="apple-mobile-web-app-title" content="toybox">`, and updates the viewport to `width=device-width, initial-scale=1.0, viewport-fit=cover`; `cd frontend && npm run build` succeeds, `dist/manifest.webmanifest` and `dist/icons/*` present; manifest passes JSON syntax (`node -e "JSON.parse(require('fs').readFileSync('frontend/public/manifest.webmanifest'))"`); existing kiosk Playwright smoke tests still pass (`cd frontend && npm run test:ui`) — index.html change must not regress the desktop kiosk |
| iK2 | Safe-area-inset padding on kiosk root | `--reviewers code` | Kiosk root in [App.tsx](../frontend/src/child/App.tsx) splits into outer "background-bearing" div (gradient, edge-to-edge) + inner "content-bearing" div (with `padding: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left)`); CSS `env()` defaults to 0 outside iPad — desktop rendering snapshot-equivalent; existing kiosk Playwright smoke tests still pass (`cd frontend && npm run test:ui`); `npm run typecheck && npm run lint && npm run test` clean |
| iK3 | Screen Wake Lock during active activity | `--reviewers code` | New `frontend/src/child/wakeLock.ts` module exposes `acquireWakeLock()` / `releaseWakeLock()` returning a `WakeLockSentinel \| null`; [App.tsx](../frontend/src/child/App.tsx) calls `acquire` when `activityState === "running"` (or `paused`), `release` on `ended`/`completed`/unmount; re-acquires on `visibilitychange` if the sentinel was released by the system (documented Web API behavior when tab hidden); silent no-op when `navigator.wakeLock` is `undefined` (older iPadOS / unsupported browsers); unit tests stub `navigator.wakeLock.request` and assert: (a) acquire on running, (b) release on ended, (c) re-acquire on `visibilitychange: visible`, (d) no-op when API missing, (e) `releaseWakeLock` called twice doesn't throw (idempotency); no console errors when the API is missing; `npm run typecheck && npm run lint && npm run test` clean |
| iK4 | iOS audio unlock + touch-target audit | `--reviewers code` | [sfx.ts](../frontend/src/child/audio/sfx.ts) gains `unlockAudio()` that calls `.play().then(p => p?.pause())` on each preloaded `Audio` element (catches and ignores autoplay-block rejections); idempotent — safe to call multiple times, subsequent calls are harmless no-ops (NO `unlocked` flag guard — iOS autoplay state is per-element and the play-pause idiom is itself a no-op once unlocked); [KioskPinPrompt.tsx](../frontend/src/child/components/KioskPinPrompt.tsx) calls `unlockAudio()` on PIN submit (the first user gesture); touch-target audit: [NextStepButton.tsx](../frontend/src/child/components/NextStepButton.tsx) and PIN keypad buttons each have CSS `min-height` and `min-width` ≥ 44pt with one-line comment `/* ≥44pt per Apple HIG */` next to the relevant CSS; existing silent-fallback behavior preserved when SFX files are 404; unit tests verify `unlockAudio()` calls `.play()` on each preloaded `Audio` element exactly once + handles rejection without throwing + safe to call twice; `npm run typecheck && npm run lint && npm run test` clean |
| iK5 | Operator iPad setup doc + visual verification | `--reviewers code` (operating-mode override — see step subsection) | ✅ DONE 2026-05-10 — closed via Phase G iPad UAT (`documentation/runs/2026-05-10-phase-g-uat.md`). Operator doc + troubleshooting matrix landed during that run (commits `869bb0d` + `1ea0a89`); WS-origin warning log added so future LAN mismatches are diagnosable from the log alone. The original full done-when criteria below are preserved for historical reference. Original done-when: New [documentation/operator/ipad-setup.md](../operator/ipad-setup.md) covers: (1) Prerequisites (parent PIN set, backend bound to `0.0.0.0`, frontend with `--host 0.0.0.0`, find LAN IP via `ipconfig`, **iPad on the same Wi-Fi SSID as the home machine** — guest/isolated networks do NOT work); (2) iPad Add-to-Home-Screen — open Safari, navigate to `http://<lan-ip>:4000/child`, tap Share → Add to Home Screen, name it "toybox"; (3) Guided Access setup — Settings → Accessibility → Guided Access, triple-click side button to lock the iPad to the kiosk; (4) Troubleshooting matrix (audio silent → tap PIN to unlock; WS won't connect → check LAN IP / Origin policy / firewall / SSID; iPad sleeps mid-activity → ensure iPadOS ≥16.4 for Wake Lock, or disable Auto-Lock in Settings → Display); (5) Known limitations (HTTP only — see Backlog Tailscale enhancement; token caching boundary is Guided Access). All rows of the M_iK manual UAT table pass on a real iPad. Verification notes captured in `documentation/runs/<date>-phase-ipad-kiosk-uat.md` including iPadOS version |

**Issues:** Phase iPad-Kiosk umbrella #55 · step iK1 → #56 · step iK2 → #57 · step iK3 → #58 · step iK4 → #59 · step iK5 → #60

### Step iK1: PWA manifest + icons + iOS meta tags

- **Problem:** Add the static PWA scaffolding that lets iPad Safari recognize `/child` as installable. Create `frontend/public/manifest.webmanifest` with `display: "standalone"`, `orientation: "landscape"`, `start_url: "/child"`, `name: "toybox"`, `short_name: "toybox"`, `theme_color: "#fefefe"` (matches the gradient top from [App.tsx:95](../frontend/src/child/App.tsx#L95) — used by iOS Safari for the standalone-mode status bar), `background_color: "#f4f4f7"` (matches the gradient bottom — used during the brief splash before JS hydrates), and icon entries pointing at three PNGs under `frontend/public/icons/` (192×192, 512×512 for manifest; 180×180 for `apple-touch-icon`). Generate the icons from a single source SVG (`frontend/public/icons/source.svg`) via a `sharp`-based Node script at `frontend/scripts/generate-icons.mjs`; add `sharp` to `frontend/package.json` `devDependencies` and an `npm run generate:icons` script that invokes the generator. Update [index.html](../frontend/index.html) to (a) link the manifest, (b) link the apple-touch-icon, (c) add `apple-mobile-web-app-capable=yes` (full-screen mode when launched from home screen), (d) add `apple-mobile-web-app-status-bar-style=black-translucent` (status bar overlays the kiosk gradient cleanly), (e) add `apple-mobile-web-app-title=toybox` (icon label override), (f) extend the viewport meta with `viewport-fit=cover` (allows content to extend behind safe-area insets — Step iK2 then pads back). Vite `public/` is copied to `dist/` verbatim; no vite config changes needed. **Icon visual design:** placeholder is acceptable — a 512×512 rounded-square with the kiosk gradient + a centered white "T" glyph is enough for v1; final design can iterate later without re-running this phase. **Manifest validation** is JSON syntax + structural fields (verified programmatically); browser-devtools "Application" panel inspection happens during the iK5 UAT pass on a real iPad.
- **Type:** code
- **Issue:** #56
- **Flags:** --reviewers code (UI evidence intentionally bundled to iK5 per `feedback_autonomous_build_bundled_ui.md`)
- **Files:** CREATE `frontend/public/manifest.webmanifest`, `frontend/public/icons/source.svg`, `frontend/public/icons/apple-touch-icon-180.png`, `frontend/public/icons/icon-192.png`, `frontend/public/icons/icon-512.png`, `frontend/scripts/generate-icons.mjs`. MODIFY `frontend/index.html`, `frontend/package.json` (add `sharp` devDep + `generate:icons` script).
- **Status:** DONE (2026-05-07)

### Step iK2: Safe-area-inset padding on kiosk root

- **Problem:** With `viewport-fit=cover` (Step iK1), iPad content extends edge-to-edge under the rounded corners and Dynamic Island. Wrap the kiosk root in [App.tsx](../frontend/src/child/App.tsx) so its content area respects `env(safe-area-inset-{top,right,bottom,left})`. The full-bleed gradient background MUST stay edge-to-edge (so corners don't show a color band); only the inner content container is padded — implement by splitting the existing single root `<div>` into an outer "background-bearing" div (the existing gradient) plus an inner "content-bearing" div (with the safe-area padding applied). CSS `env()` evaluates to `0` on desktop browsers and on devices without insets, so existing desktop rendering is unaffected — no media-query gating needed. Real-iPad verification (avatar doesn't clip into the camera notch, Next button doesn't fall under the home indicator) is part of the iK5 UAT pass, not this step's acceptance.
- **Type:** code
- **Issue:** #57
- **Flags:** --reviewers code (UI evidence intentionally bundled to iK5 per `feedback_autonomous_build_bundled_ui.md`)
- **Files:** MODIFY `frontend/src/child/App.tsx` (split background + content; add `env(safe-area-inset-*)` padding to inner content container). MODIFY any associated CSS module if the App uses one (otherwise inline-style is fine).
- **Status:** DONE (2026-05-07)

### Step iK3: Screen Wake Lock during active activity

- **Problem:** iPad Auto-Lock turns the screen off after 2 minutes by default; a child mid-activity loses their kiosk. Use the [Screen Wake Lock API](https://developer.mozilla.org/en-US/docs/Web/API/Screen_Wake_Lock_API) (iPadOS 16.4+) to keep the display on while an activity is `running` or `paused`. New module `frontend/src/child/wakeLock.ts` exports `acquireWakeLock()` and `releaseWakeLock()`; [App.tsx](../frontend/src/child/App.tsx) calls `acquire` in a `useEffect` when activity state enters `running`/`paused` and `release` on transition to terminal states (`ended`, `completed`, `dismissed`) or unmount. Browser releases the sentinel automatically when the page becomes hidden (e.g., user pulls down Control Center); a `visibilitychange` listener re-acquires on return-to-visible. The API is feature-detected: `if (!("wakeLock" in navigator)) return null` — older iPadOS / desktop Safari are silent no-ops, no warning to the user. `releaseWakeLock` is idempotent (safe to call when no sentinel is held). Operator doc (Step iK5) documents the iPadOS ≥16.4 requirement and the Auto-Lock-disable fallback for older iPads.
- **Type:** code
- **Issue:** #58
- **Flags:** --reviewers code (UI evidence intentionally bundled to iK5 per `feedback_autonomous_build_bundled_ui.md`)
- **Files:** CREATE `frontend/src/child/wakeLock.ts` (module). CREATE `frontend/src/child/wakeLock.test.ts` (or co-located per project test convention) — unit tests with stubbed `navigator.wakeLock`. MODIFY `frontend/src/child/App.tsx` (acquire/release call sites + `visibilitychange` listener).
- **Status:** DONE (2026-05-07)
- **File-conflict note:** iK3 modifies `App.tsx`, same file as iK2. NOT parallel-safe with iK2 — sequence iK2 → iK3 (or merge into one bundled run).

### Step iK4: iOS audio unlock + touch-target audit

- **Problem:** iOS Safari blocks `Audio.play()` until a user gesture has triggered at least one `.play()` call. The existing [sfx.ts](../frontend/src/child/audio/sfx.ts) already swallows autoplay-block rejections silently, but the *first* SFX (the transition between step 1 and step 2) won't play until something has unlocked audio. The PIN submit tap is the natural unlock point — it's a real user gesture. Add `unlockAudio()` to sfx.ts that iterates the preloaded `Audio` elements and calls `.play().then(p => p?.pause())` on each, catching and ignoring all rejections; the play-then-immediately-pause sequence is the documented iOS unlock idiom. The function is **idempotent** — safe to call multiple times. Do NOT add an `unlocked` flag guard: the play-pause idiom is itself a no-op once iOS has unlocked the element, and a guard would prevent re-unlock on edge cases (e.g., page reload after token cache, where the kiosk skips PIN entry but audio still needs priming on the first user gesture). Wire it into [KioskPinPrompt.tsx](../frontend/src/child/components/KioskPinPrompt.tsx) on PIN submit (NOT on every digit tap — one call is enough). Same step also audits touch targets: [NextStepButton.tsx](../frontend/src/child/components/NextStepButton.tsx) and the PIN keypad buttons in [KioskPinPrompt.tsx](../frontend/src/child/components/KioskPinPrompt.tsx) must each have a CSS `min-height` and `min-width` of at least 44pt (Apple HIG floor); audit existing values and adjust if any fall short. The audit deliverable is a one-line comment in each affected component naming the floor (`/* ≥44pt per Apple HIG */`) so future edits don't accidentally shrink the tap target. Real-iPad audio-unlock confirmation (subsequent SFX plays without further interaction) is part of the iK5 UAT pass.
- **Type:** code
- **Issue:** #59
- **Flags:** --reviewers code (UI evidence intentionally bundled to iK5 per `feedback_autonomous_build_bundled_ui.md`)
- **Files:** MODIFY `frontend/src/child/sfx.ts` (add `export function unlockAudio(): void` — note: actual path is `child/sfx.ts`, not `child/audio/sfx.ts` as originally written). MODIFY `frontend/src/child/components/KioskPinPrompt.tsx` (call `unlockAudio()` on PIN submit; add `/* ≥44pt per Apple HIG */` comment near keypad button CSS). MODIFY `frontend/src/child/components/NextStepButton.tsx` (add same comment near button CSS; verify existing `min-height` / `min-width`).
- **Status:** DONE (2026-05-07)

### Step iK5: Operator iPad setup doc + visual verification

- **Problem:** Document the end-to-end iPad install + use procedure for a fresh operator, then verify the whole stack works on a real iPad against the real home Wi-Fi LAN. New file `documentation/operator/ipad-setup.md` covers: (1) **Prerequisites** — Phase D PIN set, backend bound to `0.0.0.0`, frontend running with `--host 0.0.0.0`, LAN IP known via `ipconfig`, **iPad on the same Wi-Fi SSID as the home machine** (guest networks, isolated SSIDs with AP isolation, and corporate networks that block client-to-client traffic do NOT work). (2) **Add to Home Screen** — open Safari on iPad, navigate to `http://<lan-ip>:4000/child`, complete PIN entry once to confirm reachability, tap Share button → Add to Home Screen → name "toybox" → Add. The home-screen icon now launches the kiosk in standalone mode (no Safari chrome). **Dev iteration tip:** desktop browser responsive design mode (Safari → Develop → Enter Responsive Design Mode) emulates the iPad viewport and is enough to validate iK1–iK2 layout before pushing to a real iPad. (3) **Guided Access** — Settings → Accessibility → Guided Access → On; set a passcode; in the kiosk, triple-click the side/top button to lock the iPad to the toybox app. Triple-click again + passcode to exit. This is the "true kiosk" mode that prevents the child from swiping out to other apps; no code change required. **Token security boundary is Guided Access** (the kiosk caches the parent token in `localStorage`; without Guided Access, a child can pinch out and the cached token persists). (4) **Troubleshooting matrix** — `audio silent on first transition` → iPad audio unlocks on first PIN tap (Step iK4); if the kiosk skipped PIN entry due to a cached token, tap anywhere first. `WS won't connect` → confirm LAN IP, confirm backend Origin policy includes the LAN IP origin, check iPad firewall / VPN, **confirm iPad is on the same SSID as the home machine** (#9 from review). `iPad sleeps mid-activity` → confirm iPadOS ≥16.4 for Wake Lock support; on older iPads, set Settings → Display & Brightness → Auto-Lock → Never (manual fallback). `add-to-home-screen icon disappears` → the iPad treats the icon as ephemeral if storage is wiped; re-add from Safari. (5) **Known limitations** — plain HTTP only (link to Backlog Tailscale enhancement); no offline support (kiosk requires backend connectivity throughout); token caching boundary is Guided Access, not the token lifecycle. **Visual verification** is the deliverable's other half: run all rows of the M_iK manual UAT table (below). Verification notes captured in `documentation/runs/<date>-phase-ipad-kiosk-uat.md` including iPadOS version (the version determines whether Wake Lock is active or the manual Auto-Lock fallback is needed).
- **Type:** code+ui (`+ui` indicates UI verification scope; `--reviewers code` flag is the deliberate operating-mode override per `feedback_autonomous_build_bundled_ui.md` — runtime UI reviewer is dropped because verification is operator-on-real-iPad, not Playwright)
- **Issue:** #60
- **Flags:** --reviewers code
- **Files:** CREATE `documentation/operator/ipad-setup.md`. CREATE `documentation/runs/<date>-phase-ipad-kiosk-uat.md` (after UAT pass; `<date>` = ISO date of the pass).
- **Status:** ✅ DONE 2026-05-10 — closed via Phase G iPad UAT pass at `documentation/runs/2026-05-10-phase-g-uat.md`. Operator doc + troubleshooting matrix landed during the run (commits `869bb0d` + `1ea0a89`); WS-origin warning log added (`1ea0a89`). The Phase G UAT exercised the full kiosk stack on a real iPad including PIN entry, activity progression, audio unlock, and Wake Lock — same checks iK5 would have run.
- **Depends on:** iK1, iK2, iK3, iK4 — all four must be merged before iK5 verification can run end-to-end.

## Backlog

These are deliberately out of scope for Phase iPad-Kiosk but worth shipping if the LAN-HTTP MVP proves the kiosk-on-iPad value:

### iK-Backlog-1: Tailscale + Let's Encrypt HTTPS for the kiosk

**Problem:** Plain HTTP over LAN works on the home Wi-Fi but has three limitations: (a) no Service Worker / offline cache (browsers require HTTPS for SW registration outside `localhost`); (b) no kiosk access from outside the home network — useful for grandparent demos, traveling, or split-household scenarios; (c) no encryption-in-transit, even on the home Wi-Fi (low-stakes for family-private toybox but trivially fixable). Tailscale solves all three: install Tailscale on the home Windows machine and on the iPad, both join a private mesh, the iPad reaches the home machine at a stable MagicDNS hostname (`toybox.tailnet-name.ts.net`), and `tailscale serve --bg https / 4000` provisions a free Let's Encrypt cert + HTTPS proxy automatically. Implementation: (1) operator install steps (Tailscale on Windows, on iPad, log in to same account, enable MagicDNS); (2) `tailscale serve` config recipe; (3) backend Origin allow-list extended to include the MagicDNS hostname; (4) optional: re-enable `wss://` WebSocket since Tailscale serve will upgrade the proxied WS connection. Decision gate before starting: confirm the operator has a Tailscale account (free tier is sufficient for ≤3 devices) and is comfortable with the dependency.

**Type:** code+ops
**Status:** BACKLOG

### iK-Backlog-2: Service Worker offline shell

**Problem:** Once HTTPS is in place (iK-Backlog-1), register a service worker via `vite-plugin-pwa` to pre-cache the kiosk JS / CSS / icons. Live API + WebSocket calls stay online (kiosk requires backend connectivity for activity progression — no offline play in v1), but the home-screen icon launches instantly even on a flaky LAN, and a backend reboot doesn't blank the screen mid-load. Also enables iPadOS 16.4+ proper PWA push if push notifications ever become a feature (out of scope for v1 — listed for completeness). Decision gate: only start after iK-Backlog-1 ships (HTTPS prerequisite). Scope guard: cache the *shell* only (HTML, JS, CSS, icons); never cache API responses or WS messages.

**Type:** code
**Status:** BACKLOG

### iK-Backlog-3: Capacitor iOS wrapper for App Store / TestFlight distribution

**Problem:** Wrap the existing Vite build in a Capacitor + native iOS WebView shell so the kiosk can ship as a real `.ipa`. Useful only if multi-household distribution becomes a real use case; for the original "free / private / for testing" goal, the PWA path (Phase iPad-Kiosk) is sufficient and the Capacitor route adds material cost (Mac required, Apple Developer account at $99/yr for persistent re-sign or 7-day cycles on the free tier, App Store review process). Implementation if pursued: `npm install @capacitor/core @capacitor/cli @capacitor/ios`, `npx cap init`, `npx cap add ios`, configure backend URL to point at Tailscale or LAN IP, build via Xcode on a Mac, distribute via TestFlight. Decision gate: only consider after a real-use signal that PWA install friction is the limiting factor; until then, the PWA install flow (Add to Home Screen) is faster and cheaper.

**Type:** code+ops
**Status:** BACKLOG

## Risks and open questions

- **iPadOS Wake Lock minimum version.** Wake Lock API requires iPadOS 16.4+. Older iPads (e.g., iPad Air 2 stuck on iPadOS 15) silently no-op and rely on the operator-set Auto-Lock=Never fallback. If the test iPad is older, Step iK3 still ships (graceful degradation), but the operator doc must call out the floor explicitly.
- **iOS audio unlock fragility.** The `.play().then(p => p?.pause())` idiom is the documented unlock pattern but iOS occasionally tightens autoplay rules between major releases. If a future iPadOS breaks the pattern, the kiosk falls back to silent operation (existing behavior — no regression). Re-test after every iPadOS major version bump.
- **Origin allow-list for LAN IP.** The Phase A WebSocket Origin check ([phase-a.md step 8](phase-a.md#step-8-activity-api--ws--auth-scaffolding)) enforces an allow-list. The iPad's request Origin will be `http://<lan-ip>:4000`, not `localhost:4000` — the allow-list must include the LAN-IP origin or the WS upgrade will be rejected with 403. The Phase D PIN gate already conditionally relaxes this when `TOYBOX_HOST=0.0.0.0`; verify during Step iK5 that the actual LAN IP origin passes. If a misconfiguration is found, the fix is in the existing Origin policy, not in this phase's scope.
- **Add-to-Home-Screen vs Safari refresh.** PWAs added to the home screen on iPad don't always pick up frontend code changes between sessions (cached aggressively). For dev iteration during the build, instruct the operator to delete + re-add the icon, or to test in Safari directly until each step passes. The Service Worker (Backlog iK-Backlog-2) would make this worse, not better — another reason to defer it.
- **Battery drain from Wake Lock.** Keeping the screen on for a long activity drains the iPad battery faster than normal Auto-Lock. Document in operator setup that the iPad should be charging during use, or accept the drain.
- **Sprite assets are bandwidth-sensitive over LAN.** Phase F toy action sprites (96–128 px PNGs) load over the LAN per kiosk session. On 2.4 GHz Wi-Fi with multiple devices, the first activity may show a brief sprite load delay. Mitigation lives in Phase F's existing graceful-404 path (sprite is removed from DOM if it fails to load) — no Phase iPad-Kiosk change needed.

## Dependencies and sequencing

- **Hard prerequisite:** Phase D step 21 (parent PIN gate) must be DONE — LAN binding is gated on PIN-set, and the iPad needs LAN binding to reach the backend. Status per [phase-d.md "Step 21"](phase-d.md#step-21-parent-pin-gate-argon2id--rate-limit): DONE 2026-05-03, commit `72f530f`. ✅
- **Soft prerequisite:** Phase D step 22+ (transcript management, live activity polish, metrics) — not blocking for the kiosk-on-iPad path, but the parent UI workflow on the home machine drives kiosk activities, so a fully-finished Phase D is the natural baseline.
- **Independent of Phase E (local model)** — kiosk-on-iPad is a presentation-layer change, model substrate is irrelevant.
- **Independent of Phase F (toy action sprites)** — kiosk renders sprites if they exist, no-ops if they don't; Phase F's 404-graceful path already handles the "no sprites yet" case.
- **Sequencing within Phase iPad-Kiosk:** parallelism map (NOT strictly linear):
  - **iK1 ⇄ iK2 ⇄ iK4** — fully parallel-safe (disjoint files): iK1 touches `index.html` + `public/` + `package.json` + `scripts/`; iK2 touches `App.tsx`; iK4 touches `audio/sfx.ts` + `components/{KioskPinPrompt,NextStepButton}.tsx`.
  - **iK1 ⇄ iK3 ⇄ iK4** — fully parallel-safe (disjoint files).
  - **iK2 ⊥ iK3** — NOT parallel-safe; both modify `frontend/src/child/App.tsx`. Sequence iK2 → iK3 OR bundle them into a single worktree.
  - **iK5 sequential after iK1–iK4** — verification step that closes the phase; depends on all four.
  - **Recommended `/build-phase` dispatch:** parallel worktree group A = {iK1, iK4}; serial worktree B = iK2 → iK3; iK5 last (human-driven UAT). Or simpler: bundle iK1–iK4 in one worktree if `--reviewers code` and parallel infra isn't worth the orchestration overhead. Either is fine; the parallelism declaration above just makes the choice deliberate.

## Manual M_iK — iPad UAT (Step iK5)

```powershell
# Prerequisite check
uv run python -m toybox.tools.session_check  # confirm PIN set, backend healthy

# Backend on LAN
$env:TOYBOX_HOST = "0.0.0.0"
uv run python -m toybox.main --host 0.0.0.0 --port 8000

# Frontend on LAN
cd frontend; npm run dev -- --host 0.0.0.0

# Find LAN IP
ipconfig                   # note IPv4 address under your Wi-Fi adapter
```

What to look for on the iPad (in order):

| Check | Expected |
|-------|----------|
| Settings → General → About → Software Version | Record iPadOS version in run-doc — determines whether Wake Lock (iK3) is active (≥16.4) or the manual Auto-Lock=Never fallback applies |
| Confirm iPad Wi-Fi SSID matches home machine's SSID | Same SSID required; guest networks / AP-isolated SSIDs do NOT work — record SSID name (without password) in run-doc |
| Open `http://<lan-ip>:4000/child` in Safari | PIN prompt renders full-screen, landscape, gradient extends to all corners (iK2) |
| Enter PIN | Activity panel appears, audio unlocks silently (iK4) |
| Trigger activity from parent UI on home machine | Kiosk shows persona avatar + step 1 within ~1 sec |
| Tap Next | Transition SFX plays (iK4 unlock confirmed); step 2 renders |
| Pause activity from parent for ≥3 minutes | Kiosk screen stays awake (iK3 Wake Lock); auto-lock does NOT fire |
| Resume + advance to completion | "All done!" screen renders; success SFX plays |
| Pull down Control Center → close → reopen kiosk tab | WS reconnects (existing behavior); Wake Lock re-acquires (iK3) |
| Toggle iPad Wi-Fi off → on | WS reconnects within ~5 sec; activity state resyncs (existing Phase A behavior) |
| Tap Share → Add to Home Screen → "toybox" → Add | Icon appears on iPad home screen with toybox glyph (iK1) |
| Tap home-screen icon | Kiosk launches full-screen, NO Safari URL bar / chrome (iK1 standalone mode) |
| Rotate iPad to portrait | Kiosk stays landscape (iK1 orientation lock — note: only enforced in standalone-launched mode, NOT in Safari directly; document in iK5 troubleshooting) |
| Settings → Accessibility → Guided Access → enable + lock | Triple-click locks iPad to kiosk; child cannot swipe to other apps |

Verification notes captured at `documentation/runs/<date>-phase-ipad-kiosk-uat.md` per the run-doc convention. **Closed via** `documentation/runs/2026-05-10-phase-g-uat.md` (Phase G iPad UAT exercised the same kiosk stack end-to-end).
