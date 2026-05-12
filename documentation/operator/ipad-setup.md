# Operator setup: toybox kiosk on iPad

End-to-end install procedure for putting the toybox child kiosk onto a real
iPad over the home Wi-Fi LAN. Read once on first install; subsequent iPads
can lean on the Add-to-Home-Screen + Guided Access summary plus the
troubleshooting matrix.

## Prerequisites

The kiosk speaks plain HTTP to the home machine over LAN — no proxy, no
relay, no cloud. Everything below assumes you've already followed the
generic LAN-tablet bring-up at
[`../plan/how-to-run.md#run-dev--child-tablet-on-lan-phase-d-and-later-only`](../plan/how-to-run.md#run-dev--child-tablet-on-lan-phase-d-and-later-only)
and confirmed the kiosk loads at `http://<lan-ip>:4000/child` from any
laptop on the same Wi-Fi.

Before touching the iPad:

1. **Phase D PIN is set.** LAN binding is gated on a parent PIN being in
   place. Confirm by hitting `GET /api/auth/parent/status` from the home
   machine and seeing `{"pin_set": true}`. If not, complete first-run PIN
   setup in the parent UI first.
2. **Backend is bound to `0.0.0.0` AND `TOYBOX_LAN_IP` is exported in
   the same shell.** Default `localhost` binding is unreachable from the
   iPad. Independent of binding, the WS Origin allow-list defaults to
   loopback only, so the iPad's `http://<lan-ip>:4000` Origin gets
   rejected with HTTP 403 on the WS handshake (you'll see
   `"WebSocket /ws" 403 — connection rejected (403 Forbidden)` in the
   backend log on every kiosk page load). Set `TOYBOX_LAN_IP` in the
   same PowerShell session **before** launching the backend — the
   allow-list is computed at request time from the process's
   environment.

   ```powershell
   $env:TOYBOX_LAN_IP = "192.168.x.x"   # your LAN IPv4 from ipconfig
   uv run python -m toybox.main --host 0.0.0.0 --port 8000
   ```
3. **Frontend dev server is running with `--host 0.0.0.0`.** Without it,
   Vite only listens on localhost and the iPad gets connection-refused.

   ```powershell
   cd frontend; npm run dev -- --host 0.0.0.0
   ```
4. **Find the home machine's LAN IP.** Use the IPv4 address under the
   Wi-Fi adapter — not the Ethernet adapter, not a virtual switch (Hyper-V
   / Docker / WSL each add their own).

   ```powershell
   ipconfig
   ```
5. **iPad is on the same Wi-Fi SSID as the home machine.** This is direct
   LAN connectivity — there is no proxy or relay in v1. Guest networks,
   AP-isolated SSIDs, and corporate networks that block client-to-client
   traffic do NOT work. If your home router has a separate "guest" SSID,
   the iPad must be on the main one. HTTPS-over-Tailscale (which would
   make WAN access work) is on the backlog — see "Known limitations"
   below.

## Add to Home Screen

Once the kiosk is reachable from a laptop on the LAN, install it on the
iPad as a home-screen app:

1. Open Safari on the iPad.
2. Navigate to `http://<lan-ip>:4000/child` using the IPv4 address from
   the prereqs.
3. Complete PIN entry once. This both confirms LAN reachability end-to-end
   and primes iOS audio unlock for later transitions (see iK4 and the
   troubleshooting matrix).
4. Tap the Share button in the Safari toolbar.
5. Scroll the share sheet → Add to Home Screen.
6. Name the icon "toybox" and tap Add.

The home-screen icon launches the kiosk in standalone mode — no Safari chrome — and is the only mode where the iK1 landscape lock is enforced.

**Dev iteration tip.** You don't need a real iPad in front of you to
validate iK1–iK2 layout work. Open desktop Safari → Develop → Enter
Responsive Design Mode and pick an iPad preset. The viewport, orientation
controls, and touch-event simulation are close enough to catch layout
regressions before pushing to a real device. Audio unlock and Guided
Access are the parts that genuinely require hardware.

## Guided Access

1. On the iPad: Settings → Accessibility → Guided Access → toggle On.
2. Set a passcode.
3. Open the toybox home-screen icon so the kiosk is foregrounded.
4. Triple-click the side button. Guided Access starts; the kiosk is now
   the only app the child can reach.
5. To exit: triple-click the side/top button again, then enter the
   passcode.

**Token security boundary is Guided Access.** The kiosk caches the parent token in `localStorage` so the child doesn't have to re-enter the PIN. Guided Access prevents pinch-out into another app where the cached token is still reachable. Hardening the cache itself (short-expiry tokens, dedicated child-pairing scope) is v1.5 work, out of scope for the kiosk MVP.

## Troubleshooting

| Symptom | What to check |
|---|---|
| `audio silent on first transition` | iPad audio unlocks on the first PIN tap (iK4). If a cached token caused the kiosk to skip the PIN screen, audio will be silent on the first transition; subsequent transitions recover after the child's first interaction (any tap is a sufficient user-gesture for iOS to relax autoplay). To prime audio explicitly, sign out and re-enter the PIN once. |
| `WS won't connect` / `parent approve doesn't sync to kiosk` / backend log shows `"WebSocket /ws" 403 — connection rejected (403 Forbidden)` on every kiosk page load | The kiosk loaded over HTTP fine, but the WS handshake failed because `TOYBOX_LAN_IP` was not set when the backend started, so the iPad's `http://<lan-ip>:4000` Origin isn't in the allow-list. PowerShell does NOT inherit env vars across sessions — set it in the same shell that launches the backend: `$env:TOYBOX_LAN_IP = "192.168.x.x"; uv run python -m toybox.main --host 0.0.0.0 --port 8000`. Also confirm the LAN IP in the URL bar matches `ipconfig`, the iPad isn't on a VPN / content filter that rewrites hostnames, and **iPad and home machine are on the same SSID** — guest networks and AP isolation silently drop client-to-client traffic. |
| `iPad sleeps mid-activity` | Confirm iPadOS is ≥16.4 — Wake Lock (iK3) requires it and silently no-ops on older versions. On older iPads, set Settings → Display & Brightness → Auto-Lock → Never as the manual fallback. |
| `add-to-home-screen icon disappears` | iPad treats the home-screen icon as ephemeral — it can be evicted when storage is wiped, when Safari data is cleared, or after some iPadOS major upgrades. Re-add from Safari using the steps above. |

## Known limitations

- **Plain HTTP only.** The kiosk speaks `http://` over the LAN. There's
  no TLS, no Service Worker (browsers block SW registration on HTTP
  outside `localhost`), and no out-of-home access. The follow-up
  enhancement is Tailscale + Let's Encrypt — see
  [`../plan/archive/phase-ipad-kiosk.md#ik-backlog-1-tailscale--lets-encrypt-https-for-the-kiosk`](../plan/archive/phase-ipad-kiosk.md#ik-backlog-1-tailscale--lets-encrypt-https-for-the-kiosk).
- **No offline support.** The kiosk requires backend connectivity for the entire activity; a flaky LAN will stall it.
- **Token-caching boundary is Guided Access**, not the token lifecycle — see the Guided Access section above.

## Verification

The verification half of Step iK5 is a real-iPad UAT pass against the
14-row M_iK manual table in
[`../plan/archive/phase-ipad-kiosk.md#step-ik5-operator-ipad-setup-doc--visual-verification`](../plan/archive/phase-ipad-kiosk.md#step-ik5-operator-ipad-setup-doc--visual-verification).
Run all rows in order on the real device, capture findings (including the
iPadOS version — it determines whether iK3 Wake Lock is active or the
manual Auto-Lock fallback applies) in a run-doc at
`../runs/<date>-phase-ipad-kiosk-uat.md`. A clean pass on every row is
the gate that closes Phase iPad-Kiosk; this doc and that run-doc together
are the iK5 deliverable.
