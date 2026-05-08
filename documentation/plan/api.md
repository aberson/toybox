# API contract

> **Scope:** REST routes, WebSocket envelope, auth/token model, Origin allow-list, LAN binding guard. Read this when adding a new endpoint, changing a payload shape, or debugging auth/CORS issues. Schema in [data-model.md](data-model.md); listening modes & failure policy in [runtime.md](runtime.md).

**Token transport:** all gated endpoints expect `Authorization: Bearer <token>`. No cookies (sidesteps CSRF on a LAN device). Pre-Phase-D, `/api/auth/parent` returns a token without PIN check — **don't expose toybox to a guest network or untrusted houseguests until Phase D step 21 lands**. The pre-Phase-D auth path is a transitional convenience, not a security boundary.

## REST routes

| Method | Route | Purpose | Body / Query | Response |
|--------|-------|---------|--------------|----------|
| GET | `/api/health` | liveness | — | `{ok, version, claude_capable, capability_reason, mode, mic_enabled}` |
| GET | `/api/metrics` | counters for tuning (parent token) | — | `{trigger_fires_by_intent, claude_calls_by_mode, suggestions_total, suggestions_approved, suggestions_dismissed, avg_stt_confidence, breaker_state, ws_subscribers, mic_device, mic_queue_depth, model_loaded, data_dir_bytes, disk_free_bytes, uptime_sec}` |
| POST | `/api/auth/parent` | issue parent token | `{pin?}` (pre-Phase D: empty body) | `{token, expires_at}` |
| POST | `/api/auth/child/pair` | pair a child kiosk (parent token required) | `{label?}` | `{token, expires_at}` |
| POST | `/api/auth/revoke` | revoke a token (parent token required) | `{token}` | `{ok}` |
| GET | `/api/settings/mode` | current mode | — | `{mode: 1-5}` |
| POST | `/api/settings/mode` | set mode | `{mode: 1-5}` | `{mode}` |
| GET | `/api/settings/pin/status` | PIN setup status | — | `{configured: bool}` |
| POST | `/api/settings/pin` | set/change PIN (gated) | `{old?, new}` | `{ok}` |
| GET | `/api/settings/claude` | OAuth status | — | `{capable, expires_at?}` |
| GET | `/api/toys` | list | `?archived=` | `[Toy]` |
| POST | `/api/toys/photo` | upload + propose | multipart | `{toy: Toy, ai_suggestions}` |
| PATCH | `/api/toys/{id}` | edit | partial Toy | `Toy` |
| DELETE | `/api/toys/{id}` | archive | — | `{ok}` |
| GET | `/api/personas` | list | — | `[Persona]` |
| POST | `/api/personas` | create | `Persona` | `Persona` |
| PATCH | `/api/personas/{id}` | edit | partial | `Persona` |
| DELETE | `/api/personas/{id}` | delete (non-library only) | — | `{ok}` |
| GET | `/api/rooms` | list with features | — | `[Room with features]` |
| POST | `/api/rooms/photos/bulk` | bulk upload + propose (per-file results, partial success allowed) | multipart (multi-file) | `{results: [{filename, status: "ok"\|"error", code?, message?, room?, ai_suggestions?}]}` |
| PATCH | `/api/rooms/{id}` | edit | partial | `Room` |
| GET | `/api/children` | list | — | `[Child]` |
| POST | `/api/children` | create | `Child` | `Child` |
| PATCH | `/api/children/{id}` | edit | partial | `Child` |
| DELETE | `/api/children/{id}` | delete | — | `{ok}` |
| POST | `/api/activities/suggest` | manual trigger (dev / parent solicit) | `{intent, slot?, child_ids?}` | `Activity` |
| POST | `/api/activities/{id}/approve` | start running with selected kid(s) | header: `If-Match-Version: N`; body `{child_ids: [...]}` (required if >1 child profile, else server fills) | `Activity` (409 on version mismatch) |
| POST | `/api/activities/{id}/dismiss` | discard pre-approval | header: `If-Match-Version: N` | `{ok}` (409 on mismatch) |
| POST | `/api/activities/{id}/advance` | next step | header: `If-Match-Version: N` | `Activity` (409 on mismatch) |
| POST | `/api/activities/{id}/pause` | pause | header: `If-Match-Version: N` | `Activity` (409 on mismatch) |
| POST | `/api/activities/{id}/resume` | resume | header: `If-Match-Version: N` | `Activity` (409 on mismatch) |
| POST | `/api/activities/{id}/regenerate` | regenerate from current step | header: `If-Match-Version: N` | `Activity` (409 on mismatch) |
| POST | `/api/activities/{id}/end` | end (with confirm) | header: `If-Match-Version: N`; body `{confirmed: true}` | `Activity` (409 on mismatch) |
| POST | `/api/activities/{id}/feedback` | "didn't work" / "loved it" | `{kind, step_seq?, reason?}` | `{ok}` |
| GET | `/api/transcripts` | list (recent first; ISO `before` cursor) | `?limit=50&before=<iso>` | `TranscriptListResponse{items: [TranscriptRow], next_before?}` |
| GET | `/api/transcripts/search` | case-insensitive substring search (parameterized LIKE) | `?q=<str>&limit=50` | `TranscriptListResponse` |
| DELETE | `/api/transcripts/{id}` | delete one (Phase D Step 22) | — | `{ok}` |
| DELETE | `/api/transcripts` | wipe all (PIN-gated, Phase D Step 22) | — | `{deleted: int}` |
| WS | `/ws` | bidirectional topics | subscribe by topic | streamed events |

## WebSocket envelope

All ws messages share a single envelope:

```json
{
  "topic": "activity.state",
  "type": "transition",
  "payload": { /* topic-specific shape */ },
  "ts": "2026-05-01T12:34:56.789Z"
}
```

### WebSocket authentication

`/ws` requires a session token in the `Sec-WebSocket-Protocol` header (or `?token=` query string for browsers that don't allow custom subprotocols on tablets). Tokens are issued by:

| Endpoint | Recipient | Scope | Lifetime |
|----------|-----------|-------|----------|
| `POST /api/auth/parent` (body: `{pin}`) | parent app after PIN entry | all topics | 24 h sliding |
| `POST /api/auth/child/pair` (body: `{room?}`, parent token required) | child kiosk one-time pairing | `activity.state` for current session only | 30 days, revocable |

Topics by scope:

| Topic | Required scope |
|-------|---------------|
| `transcript` | parent |
| `activity.state` | parent OR matching-session child |
| `mode` | parent OR child |
| `system` | parent (warns + errors) / child (errors only) |
| `metrics` | parent |

Tokens are random 32-byte hex strings; revocation is tracked via `auth_tokens.revoked_at` (single source of truth — no separate `revoked_tokens` table). Lifespan startup deletes rows past `expires_at`; capability check rejects rows with `revoked_at IS NOT NULL`. Pre-Phase D (no PIN), the auth endpoints accept any request and return a token — the gate exists structurally so Phase D step 21 only needs to add PIN verification, not retrofit auth. **For v1, this means the backend must bind loopback-only — see "LAN binding guard" below.**

### Origin header check (defense-in-depth)

`/ws` upgrade and all `POST`/`PATCH`/`DELETE` REST handlers reject requests whose `Origin` header is not in the configured allow-list. Default allow-list:

- `http://localhost:4000`
- `http://127.0.0.1:4000`
- `http://<TOYBOX_LAN_IP>:4000` if `TOYBOX_LAN_IP` env var is set (Phase D LAN-bind path only)

Mitigates DNS rebinding and cross-site websocket hijacking from a phishing tab on the same machine. `GET` requests skip the check (no state change).

### LAN binding guard

The backend refuses to bind any non-loopback host unless `settings.parent_pin_hash` is set. Concretely: at startup, if `TOYBOX_HOST != 127.0.0.1` and `TOYBOX_HOST != localhost` and the PIN is unset, the process logs `code=lan_bind_requires_pin` and exits non-zero. This makes the security invariant a runtime check, not a documentation request — v1 (no PIN, no LAN binding) and post-Phase-D (PIN set, LAN binding allowed) are the only valid states.

### Subscription messages

```json
{ "action": "subscribe", "topics": ["activity.state", "mode", "system"] }
{ "action": "unsubscribe", "topics": ["transcript"] }
```

Server rejects subscriptions to out-of-scope topics with a `system` error message and disconnects after 3 violations.

### Subscriber backpressure

Each subscriber has a single bounded outbound queue (default 100 messages **total across all subscribed topics**, not per-topic). On overflow, oldest messages drop and a `system` notice fires (`code=ws_backpressure_drop`). Mic loop and other publishers never block on slow subscribers.

### Topics (server → client)

| Topic | Payload shape |
|-------|---------------|
| `transcript` | `{id, text, confidence, started_at, ended_at, language}` (per-transcript envelope, schema_version=1) |
| `activity.state` | full `Activity` DTO including `version` |
| `mode` | `{mode: 1-5}` |
| `system` | `{level: "error"\|"warn"\|"info", code, message, dismissable, capability_reason?}` |
| `metrics` | snapshot every 30 sec: same shape as `GET /api/metrics` |
