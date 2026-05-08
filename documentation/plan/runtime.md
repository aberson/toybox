# Runtime behavior

> **Scope:** listening modes, Claude OAuth lifecycle, capability gate, failure-mode policy, logging, and what data leaves the house. Read this when reasoning about offline degradation, error surfaces, or privacy posture. State machine + generators in [activity-loop.md](activity-loop.md); env vars in [how-to-run.md](how-to-run.md).

## Listening modes

Parent app surfaces a 5-position slider. Mode dictates whether Claude is invoked and at what cadence.

| Mode | Name | Curated NLP | Claude calls |
|------|------|-------------|--------------|
| 5 | Intense | always | every utterance, throttled by `claude_call_min_interval_sec` |
| 4 | High | always | curated triggers + spontaneous every N min (default 5) |
| 3 | Default | always | only on curated triggers |
| 2 | Low | always | only on parent-tap "what should we do?" |
| 1 | Offline | always | never (also engaged automatically when token missing/expired) |

Default = **mode 3** on first run. Mode is stored in `settings`, emitted on the `mode` ws topic so both apps reflect changes instantly.

The capability gate `ai.client.is_capable()` returns `False` when:
- mode == 1
- OAuth token missing
- OAuth token expired and refresh fails
- last N Claude calls returned network errors (circuit breaker open)

Every AI call site reads this; on `False` the call falls back to the offline path or no-ops cleanly.

The gate also publishes a `capability_reason` so the parent UI can distinguish *why* it's offline:

| Reason | Meaning | UI banner |
|--------|---------|-----------|
| `config_mode_1` | Listening mode = 1 by parent choice | none (intentional) |
| `token_missing` | No OAuth file at startup | "Set up Claude in Settings" |
| `token_expired` | Refresh failed or token revoked | "Re-authenticate Claude" |
| `network_error` | Circuit breaker open after N consecutive fails | "Can't reach Claude — retrying" |
| `rate_limited` | Anthropic returned 429 | "Slowing down for a few minutes" |

`capability_reason` is emitted on the `system` ws topic on every flip.

## What leaves the house

Privacy posture for a passively-listening home device used around children.

| Data | Stays local | Sent outbound | Notes |
|------|-------------|---------------|-------|
| Microphone audio | ✅ | ❌ | Ephemeral ring buffer, never written to disk, never sent anywhere |
| Transcripts (text) | ✅ | ❌ | SQLite-only; PIN-gated wipe button |
| Toy / room photos | ✅ (always) | ⚠️ when ingest with vision enabled | Sent to Claude's vision endpoint over OAuth; only at ingest time, never replayed |
| Child profile (name, age, interests) | ✅ (always) | ⚠️ as Claude prompt context (modes 3+) | Sent on activity-generation Claude calls. Free-text fields can be redacted by parent |
| Trigger phrase that fired | ✅ | ⚠️ as Claude prompt context (modes 3+) | Last 60 sec of transcript context |
| Listening mode + curated NLP triggers | ✅ | ❌ | |
| Feedback / anti-signal | ✅ | ⚠️ as Claude prompt addendum | "things this family doesn't like" string |
| OAuth token | local file | ❌ (refresh round-trip excluded) | `~/.toybox/secrets.json` |

**Anthropic-side retention:** governed by Anthropic's published policy at the time of use. Toybox cannot wipe data already received by Anthropic. If retention is a concern, run in mode 1 (offline) — no data leaves the house.

**Mode 1 guarantee:** when `listening_mode=1`, no outbound network calls except OAuth refresh (which can also be disabled by removing `secrets.json`). Pure local operation.

**LAN exposure:** the backend defaults to `127.0.0.1` (loopback-only) so v1 — which has no PIN — is not reachable from the LAN at all. The child kiosk tablet path requires the parent PIN to be set (Phase D step 21); only then does the LAN-binding guard allow `TOYBOX_HOST=0.0.0.0`. See [api.md "LAN binding guard"](api.md#lan-binding-guard).

## Claude OAuth lifecycle

Token managed via the `claude-oauth-auth` subscription flow; storage at `~/.toybox/secrets.json` (Windows: `%USERPROFILE%\.toybox\secrets.json`). On Windows, ACL inheritance is sufficient for single-user home machines; on POSIX, file mode is forced to `0600` at write time.

```
┌────────────┐    on startup     ┌──────────────┐
│ load token │──────────────────►│ check expiry │
└────────────┘                   └──────┬───────┘
                                        │
                  ┌─────────────────────┼────────────────────────┐
                  │                     │                        │
              valid (>5 min)        expiring soon            expired
                  │                  (<5 min)                    │
                  ▼                     ▼                        ▼
          ┌────────────┐        ┌──────────────┐         ┌──────────────┐
          │ is_capable │        │ refresh in   │         │ refresh now  │
          │ = True     │        │ background   │         │ (sync, once) │
          └────────────┘        └──────────────┘         └──────┬───────┘
                                                                │
                                                       success ─┴─ fail
                                                          │       │
                                                          ▼       ▼
                                                   re-arm timer  capability=False
                                                                 reason=token_expired
                                                                 emit system ws
```

- Background refresh task wakes every 60 sec; refreshes any token within 5 min of expiry.
- On Anthropic 401 mid-call, the call fails and the next gate read returns `False` with `reason=token_expired` until refresh succeeds.
- On three consecutive `network_error` outcomes, circuit breaker opens for 60 sec; `reason=network_error`.
- On `429`, breaker opens for the response's `retry-after` (default 120 sec); `reason=rate_limited`. Subsequent triggers route to the offline path.

Parent UI displays current `capability_reason` and an "Re-authenticate" button that re-runs the OAuth flow in a new window.

## Failure modes & error policy

Every external call is wrapped to return a typed `Result[T, ToyboxError]` (using a small Result type, not exceptions across boundaries). Errors carry a stable `code` (string), `message` (user-safe), and optional `detail` (dev-only).

| Site | Failure | Surface |
|------|---------|---------|
| STT (`audio.stt.transcribe`) | model load fails on startup | fatal, exits with clear log; `--check` would have caught it |
| STT | mid-stream decode error | log + drop chunk; no user-visible message |
| Claude (`ai.client.call`) | network/timeout | circuit breaker counts; capability flips to `network_error` |
| Claude | 401 | mark token expired; trigger refresh; capability flips to `token_expired` |
| Claude | 429 | open breaker; capability flips to `rate_limited` |
| Claude | malformed JSON output | log; fall back to offline path for that one suggestion; emit `system` warning |
| Vision (`ai.toy_vision`) | timeout/error | suggestion fields empty; parent fills manually; toast in UI |
| Photo upload | size > limit / bad MIME / bad bytes | 415/413 with explicit code; UI shows the rule that failed |
| DB | constraint violation | 409 with `code=duplicate_*`; UI shows "this exists, view existing?" |
| ws | client disconnect | client auto-reconnect with state resync (full activity refetch on reconnect) |
| Persona library load | malformed JSON | log + skip that persona; startup continues; `--check` warns |
| Migration | apply failure | abort startup; preserve DB at original path; log the failed migration filename + traceback. v1 has no backup; operator must manually copy `data/toybox.db` aside, then either fix the migration source or factory-reset per M5. |

UI errors flow on the `system` ws topic with `{level: "error"|"warn"|"info", code, message, dismissable}`.

## Logging policy

- Logs go to stdout; structured JSON when not a TTY.
- **Transcript text never logged at `INFO` or higher** — only at `DEBUG`. The DEBUG handler is opt-in via `TOYBOX_LOG_LEVEL=DEBUG` and intended for development. A pre-commit hook (`tools/check_no_transcript_in_info.py`) greps for `log\.(info|warning|error).*transcript` and fails the commit on hit. A full ruff plugin was considered but is overkill for one rule; the grep hook is ~15 lines and covers the same ground.
- Children's names appear in transcripts and are PII; logging policy applies the same gate.
- API request bodies containing photos are never logged in full (size only).
- OAuth tokens never logged in any form.
- Failed PIN attempts logged at `WARNING` with attempt count, never the attempted value.
