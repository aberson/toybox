-- Phase I Step I1: seed the household-scoped ``transcript_retention_seconds``
-- setting and add an index on ``transcripts.ended_at`` to keep the I2 sweep
-- + filter-on-read fast as the table churns.
--
-- Valid retention presets are ``{60, 180, 300, 600, 900}`` (1m / 3m / 5m / 10m
-- / 15m); the helper at :mod:`toybox.core.transcript_retention` enforces the
-- canonical set. Default is 60 seconds.
--
-- Both statements are idempotent — ``INSERT OR IGNORE`` is a no-op when the
-- key already exists (preserves an operator's chosen value across re-runs)
-- and ``CREATE INDEX IF NOT EXISTS`` short-circuits when the index is
-- already present.
--
-- NOTE: The migration runner at ``db/migrations/__init__.py`` wraps the
-- whole file in a single BEGIN/COMMIT transaction and splits statements
-- via ``sqlite3.complete_statement``; this file MUST NOT contain its
-- own BEGIN/COMMIT/ROLLBACK and every statement must end with ``;``.

INSERT OR IGNORE INTO settings (key, value) VALUES ('transcript_retention_seconds', '60');

CREATE INDEX IF NOT EXISTS idx_transcripts_ended_at ON transcripts(ended_at);
