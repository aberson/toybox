# Operator recovery: forgotten parent PIN

The parent PIN gate (`POST /api/auth/parent`) is the only gate on the
parent-scope token; without it nobody can mint child-scope tokens or
manage profiles. There is intentionally **no web UI for resetting the
PIN** — that would itself be an unauthenticated reset path that an
attacker on the LAN could try to script. Reset is a manual operator
step.

## Symptoms

* You enter the PIN at the login screen and the UI keeps showing
  *Wrong PIN. N attempts remaining*.
* After 5 wrong attempts in 5 minutes the UI flips to a 15-minute
  countdown ("PIN locked. Try again in MM:SS").
* You no longer remember the PIN and cannot wait out repeated lockouts.

## What "reset" means

Reset clears the stored argon2id hash from the `settings` table. On the
next request, `GET /api/auth/parent/status` returns `{"pin_set": false}`
and the parent UI re-enters the **first-run setup screen** so you can
choose a fresh PIN. No tokens are revoked automatically (existing
parent-scope tokens still work until they expire on their normal 24h
TTL); the assumption is that if you've lost the PIN, you also can't
issue new tokens until you've re-set it.

## Procedure

Stop the backend first so no new login attempts race with the reset:

```powershell
# Windows
Get-Process | Where-Object { $_.ProcessName -eq "python" -and $_.CommandLine -match "toybox.main" } | Stop-Process
```

```bash
# macOS / Linux (when you eventually run it there)
pkill -f "toybox.main"
```

Then clear the hash row. The DB lives at the path returned by
`toybox.db.resolve_db_path()` (default `data/toybox.db`):

```powershell
# Windows PowerShell
& "C:\Program Files\sqlite-tools\sqlite3.exe" data\toybox.db `
  "DELETE FROM settings WHERE key = 'parent_pin_hash';"
```

```bash
# macOS / Linux
sqlite3 data/toybox.db "DELETE FROM settings WHERE key = 'parent_pin_hash';"
```

Equivalently, if you have a Python shell handy:

```python
from toybox.core.pin import clear_pin_hash
from toybox.db import connect, resolve_db_path

conn = connect(resolve_db_path())
try:
    clear_pin_hash(conn)
finally:
    conn.close()
```

Restart the backend. The next browser load lands on the first-run setup
screen.

## Rate-limit lockout, no PIN reset needed

The 15-minute lockout is **in-memory only**. If you simply waited out
the timer or restarted the backend (which clears the counter), you can
try again without doing anything to the DB. Reset above is only for the
"forgot the PIN entirely" case.

## What this does NOT do

* It does **not** wipe transcripts, activities, child profiles, or any
  other data. Only the `parent_pin_hash` row is touched.
* It does **not** revoke existing parent-scope tokens. If you're worried
  someone may have an old token, revoke them via:

  ```sql
  UPDATE auth_tokens
     SET revoked_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
   WHERE scope = 'parent' AND revoked_at IS NULL;
  ```

* It does **not** disable LAN binding. Once the PIN is set again the
  bind-guard will recognise it on the next backend startup. Until then,
  `TOYBOX_HOST=0.0.0.0` will fail with `code=lan_bind_requires_pin`.

## When to consider this an incident

If you have to run this procedure unexpectedly — for example, you
believe someone else set the PIN — also rotate any OAuth tokens stored
in `secrets.json` and revoke all `auth_tokens` rows. The PIN reset
itself is benign, but losing visibility of who set it suggests a
broader compromise of the host.
