-- Phase R Step R2: seed the household-scoped ``spoken_text_limit``
-- setting. Controls the maximum character count at which the Read Me
-- button truncates the spoken text to a word boundary before passing
-- it to TTS. ``0`` means "off" (no truncation); valid presets are
-- ``{0, 50, 100, 150, 250}`` and the helper at
-- :mod:`toybox.core.spoken_text_limit` enforces the canonical set.
-- Default is 150 characters.
--
-- The INSERT is idempotent — ``INSERT OR IGNORE`` is a no-op when the
-- key already exists (preserves an operator's chosen value across
-- re-runs).
--
-- NOTE: The migration runner at ``db/migrations/__init__.py`` wraps the
-- whole file in a single BEGIN/COMMIT transaction and splits statements
-- via ``sqlite3.complete_statement``; this file MUST NOT contain its
-- own BEGIN/COMMIT/ROLLBACK and every statement must end with ``;``.

INSERT OR IGNORE INTO settings (key, value) VALUES ('spoken_text_limit', '150');
