# Open questions / risks

> **Scope:** known v1 risks and their mitigations. Read this when scoping a feature that touches one of these surfaces, or before paving over a design decision that exists *because* of one.

| Item | Risk | Mitigation |
|------|------|------------|
| faster-whisper accuracy on kid speech | Garbled transcripts → bad triggers | Default `small` is fine on adult speech (initial testing); evaluate `medium`/`large-v3` when kids start using; `TOYBOX_TRANSCRIPT_CONFIDENCE_FLOOR` blocks low-confidence transcripts from firing triggers |
| Claude OAuth token refresh failures | Silent offline mid-session | Background refresh task; `capability_reason` enum distinguishes config vs token vs network; parent UI banner per reason |
| Curated NLP coverage of toddler-ese | False negatives | Trigger registry editable in `data/triggers.json`; transcript log lets parent see what was heard but didn't fire; user edits survive package upgrades |
| Single mic in busy household | Multiple kids, parent voices, TV → noisy triggers | VAD gate + confidence threshold + parent has final approval; multi-mic schema in v1.5 |
| Storage growth from transcripts | DB bloat | Vacuum trigger at 100k rows / 100 MB; PIN-gated wipe button |
| Claude vision cost on toy/room ingest | $$ over a large catalog | One-time per item; SHA-256 dedup prevents re-billing on re-upload; offline mode skips vision; parent can decline AI suggestion |
| Persona IP boundaries | "Elmo" specifically can't ship | Library uses archetypes ("friendly red monster"); user can customize names locally |
| WebSocket reconnect during activities | Brief disconnect = stuck step on child app | Server pings every 20s, closes if no pong within 30s. Client reconnects with exponential backoff: 1s → 2s → 4s → 8s → 16s → cap at 30s, jitter ±25%. On reconnect, client refetches the active activity via REST and resubscribes to all prior topics. |
| Children's voice retention privacy | Even local-only audio is sensitive | Audio ephemeral by design; transcripts redactable; PIN-gated wipe in Phase D; mic-hot indicator + mute toggle for trust |
| Child app in browser, not native | Tablet may sleep, lock, etc. | Documented setup: kiosk mode (`chrome --kiosk`), display always-on; v2 native shell |
| CUDA toolkit on Windows for GPU whisper | Setup friction | Default to CPU; `TOYBOX_WHISPER_DEVICE=auto` falls back; CPU `small` runs faster than realtime on a modern machine |
| Mic loop blocked by long Claude call | Missed audio | AI calls in `asyncio.to_thread`; mic loop is independent task with its own ring buffer |
| Anthropic rate limits in mode 5 | Cost spike or 429 spam | `TOYBOX_CLAUDE_MIN_INTERVAL_SEC` throttle; 429 opens breaker for `retry-after`; queued triggers route to offline path |
| Multi-tab parent app race conditions | Two tabs racing approve/dismiss | `If-Match-Version` on every mutation; 409 + state refresh on mismatch; ws state sync keeps tabs aligned |
| Pydantic ↔ TypeScript type drift | API contract decays silently | `pydantic-to-typescript` codegen wired into pre-commit / CI; drift is a check failure |
| Photo-upload path traversal | Arbitrary file write via filename | Server-generated UUID filenames; user filename discarded; static serving whitelisted to `data/images/{toys,personas,rooms}/` |
| First-run model download on no-internet machine | Setup blocked | Documented in How to Run; `--download` script is explicit; offline-clean once cached |
| Family Wi-Fi exposure | Backend on `0.0.0.0` reachable to anyone on the LAN | Default `TOYBOX_HOST=127.0.0.1` (loopback-only); LAN-binding startup guard refuses `0.0.0.0` until parent PIN is set (Phase D step 21); Origin header allow-list enforced on `/ws` + state-changing REST |
| Migration apply failure on startup | DB locked in partial state | Forward-only for v1; abort + preserve DB; operator/recovery.md walks through manual restore from backup |
