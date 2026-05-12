-- Seed the household-scoped ``play_target_depth`` setting.
--
-- Valid presets are ``{1, 3, 5}``; the helper at
-- :mod:`toybox.core.play_target_depth` enforces the canonical set.
-- Default is 3 (the "medium" branching depth target the autonomous
-- play queue aims for when proposing next steps).
--
-- ``INSERT OR IGNORE`` is idempotent — re-running the migration is a
-- no-op when the key already exists, preserving an operator's chosen
-- value across re-runs.
--
-- NOTE: The migration runner at ``db/migrations/__init__.py`` wraps the
-- whole file in a single BEGIN/COMMIT transaction and splits statements
-- via ``sqlite3.complete_statement``; this file MUST NOT contain its
-- own BEGIN/COMMIT/ROLLBACK and every statement must end with ``;``.

INSERT OR IGNORE INTO settings (key, value) VALUES ('play_target_depth', '3');
