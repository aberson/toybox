-- Phase W Step W2: seed the household-scoped ``game_linearity`` dial.
-- TEXT setting constrained to ``{"linear", "nonlinear"}`` and defaulting
-- to ``"nonlinear"``; the helper at :mod:`toybox.core.game_linearity`
-- enforces the canonical set.
--
-- WIRED (not a stub): the propose path reads this value and passes
-- ``linear_only=(value == "linear")`` into the offline generator, which
-- excludes any template containing a branching step when ``linear`` is
-- selected. ``"nonlinear"`` is byte-identical to the pre-W2 behavior.
--
-- The INSERT is idempotent — ``INSERT OR IGNORE`` is a no-op when the
-- key already exists (preserves an operator's chosen value across
-- re-runs).
--
-- NOTE: The migration runner at ``db/migrations/__init__.py`` wraps the
-- whole file in a single BEGIN/COMMIT transaction and splits statements
-- via ``sqlite3.complete_statement``; this file MUST NOT contain its
-- own BEGIN/COMMIT/ROLLBACK and every statement must end with ``;``.

INSERT OR IGNORE INTO settings (key, value) VALUES ('game_linearity', 'nonlinear');
