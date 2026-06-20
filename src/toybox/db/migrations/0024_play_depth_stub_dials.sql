-- Phase W Step W1: seed the household-scoped ``parent_involvement`` and
-- ``game_complexity`` dials. Both are TEXT settings constrained to
-- ``{"low", "medium", "high"}`` and default to ``"medium"``; the helpers
-- at :mod:`toybox.core.parent_involvement` and
-- :mod:`toybox.core.game_complexity` enforce the canonical set.
--
-- TRUE STUBS: these rows persist a value but nothing reads them yet. A
-- later phase consumes them to tune parent participation + activity
-- complexity. Wired to no behavior in this step.
--
-- The INSERTs are idempotent — ``INSERT OR IGNORE`` is a no-op when the
-- key already exists (preserves an operator's chosen value across
-- re-runs).
--
-- NOTE: The migration runner at ``db/migrations/__init__.py`` wraps the
-- whole file in a single BEGIN/COMMIT transaction and splits statements
-- via ``sqlite3.complete_statement``; this file MUST NOT contain its
-- own BEGIN/COMMIT/ROLLBACK and every statement must end with ``;``.

INSERT OR IGNORE INTO settings (key, value) VALUES ('parent_involvement', 'medium');
INSERT OR IGNORE INTO settings (key, value) VALUES ('game_complexity', 'medium');
