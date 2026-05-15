-- Seed the eight Phase K parent-controlled feature flags.
--
-- Two content masters (jokes_enabled, songs_enabled) + four surface
-- flags (play_standalone_enabled, play_embedded_enabled,
-- play_endings_enabled, play_spontaneity_enabled) + two kiosk-affordance
-- flags (clickable_words_enabled, read_me_button_enabled). Defaults are
-- all ``'true'`` except ``play_spontaneity_enabled`` which defaults to
-- ``'false'`` (opt-in — interjections can disrupt flow, parents must
-- explicitly turn them on).
--
-- A surface delivers content only when ``(content_master AND
-- surface_flag)`` are both true. That pairing logic lives in later
-- K-steps (K13/K14/K15); this migration just seeds the rows.
--
-- ``INSERT OR IGNORE`` is idempotent — re-running the migration is a
-- no-op when the key already exists, preserving an operator's chosen
-- value across re-runs.
--
-- NOTE: The migration runner at ``db/migrations/__init__.py`` wraps the
-- whole file in a single BEGIN/COMMIT transaction and splits statements
-- via ``sqlite3.complete_statement``; this file MUST NOT contain its
-- own BEGIN/COMMIT/ROLLBACK and every statement must end with ``;``.

INSERT OR IGNORE INTO settings (key, value) VALUES ('jokes_enabled', 'true');
INSERT OR IGNORE INTO settings (key, value) VALUES ('songs_enabled', 'true');
INSERT OR IGNORE INTO settings (key, value) VALUES ('play_standalone_enabled', 'true');
INSERT OR IGNORE INTO settings (key, value) VALUES ('play_embedded_enabled', 'true');
INSERT OR IGNORE INTO settings (key, value) VALUES ('play_endings_enabled', 'true');
INSERT OR IGNORE INTO settings (key, value) VALUES ('play_spontaneity_enabled', 'false');
INSERT OR IGNORE INTO settings (key, value) VALUES ('clickable_words_enabled', 'true');
INSERT OR IGNORE INTO settings (key, value) VALUES ('read_me_button_enabled', 'true');
