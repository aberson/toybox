-- Seed the household-scoped ``play_cadence_seconds`` setting.
--
-- Valid presets are ``{0, 10, 30, 60}`` where ``0`` means "cadence
-- disabled" (NOT a sentinel for unset — a legitimate in-set value).
-- The helper at :mod:`toybox.core.play_cadence_seconds` enforces the
-- canonical set. Default is 30 seconds (the play-queue tick cadence
-- the autonomous loop uses when proposing the next play step).
--
-- ``INSERT OR IGNORE`` is idempotent — re-running the migration is a
-- no-op when the key already exists, preserving an operator's chosen
-- value across re-runs.
--
-- NOTE: The migration runner at ``db/migrations/__init__.py`` wraps the
-- whole file in a single BEGIN/COMMIT transaction and splits statements
-- via ``sqlite3.complete_statement``; this file MUST NOT contain its
-- own BEGIN/COMMIT/ROLLBACK and every statement must end with ``;``.

INSERT OR IGNORE INTO settings (key, value) VALUES ('play_cadence_seconds', '30');
