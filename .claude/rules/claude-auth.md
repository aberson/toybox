---
description: Toybox calls Anthropic's Messages API directly via urllib + OAuth bearer. No anthropic SDK, no API key. Re-adding either is a regression.
paths:
  - "src/toybox/ai/**"
  - "tests/**/ai/**"
---

# Claude auth: OAuth bearer + urllib only

Toybox authenticates Claude calls via subscription OAuth, not an API key, and calls the Messages API directly with `urllib.request` rather than via the `anthropic` SDK.

## What this looks like in code

`src/toybox/ai/client.py` defines `_post_messages` which does:

```python
req = urllib.request.Request(
    "https://api.anthropic.com/v1/messages",
    method="POST",
    headers={
        "Authorization": f"Bearer {oauth_access_token}",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    },
    data=json.dumps(payload).encode(),
)
```

Every new call site must mirror this pattern. There is no `api_key=` code path anywhere in the codebase.

## Don't re-add the SDK

The `anthropic` Python SDK was added during M2.5 (commit `32e96f4`) when a missing dep surfaced, then removed at commit `5bbdefb`. Re-adding it is a regression:

- The user only has a Claude subscription, not an API key — the SDK's `api_key=` constructor doesn't help.
- The wire format for `/v1/messages` is stable across SDK versions; the indirection adds 5 transitive deps with no value at this layer.

If a new feature genuinely needs an SDK-only capability (managed agents, tool runner, citations), surface that to the user as a tradeoff before adding the dep. The OAuth-direct design is intentional, not an accident.

## OAuth token plumbing

- **Source of truth:** `~/.toybox/secrets.json`.
- **Bridged from:** `~/.claude/.credentials.json` (Claude CLI's token store) via `scripts/uat/bridge_claude_creds.py`.
- **Rotation:** CLI tokens rotate roughly daily — re-run the bridge at the start of any longer session.
- **Inspect:** `uv run python -m toybox.ai --check`.

## See also

Workspace memory pointer: `feedback_prefer_oauth_for_claude_code` (general subprocess design preference).
