-- Seed the default row for the ``neural_voice_enabled`` parent feature flag.
-- Forward-only; INSERT OR IGNORE is idempotent (re-run is a no-op when
-- the operator has already set a value). Value mirrors
-- NEURAL_VOICE_ENABLED_DEFAULT in src/toybox/core/neural_voice_enabled.py.
--
-- The migration runner wraps the whole file in one BEGIN/COMMIT and
-- splits on sqlite3.complete_statement; this file MUST NOT contain its
-- own BEGIN/COMMIT/ROLLBACK and every statement must end with ``;``.

INSERT OR IGNORE INTO settings (key, value) VALUES ('neural_voice_enabled', 'true');
