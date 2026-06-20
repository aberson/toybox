-- Phase W Step W3: Q&A answer-grading.
--
-- Two additive changes that extend the Phase R R3 Q&A gate:
--
-- 1. ``activity_steps.expected_answer`` — the canonical answer text for a
--    Q&A step. NULL on the overwhelming majority of steps (non-Q&A
--    content) AND on R3 question steps that opt out of auto-grading. When
--    BOTH ``question`` and ``expected_answer`` are set AND the household
--    ``qa_grading`` dial is not ``"off"``, the advance handler attempts an
--    auto-grade against the recent transcript window before falling back
--    to the R3 parent-tap 409.
--
-- 2. Seed the household-scoped ``qa_grading`` dial. TEXT setting
--    constrained to ``{"off", "lenient", "strict"}`` and defaulting to
--    ``"off"``; the helper at :mod:`toybox.core.qa_grading` enforces the
--    canonical set. With ``"off"`` the R3 parent-tap flow is byte-
--    identical to the pre-W3 behavior.
--
-- The INSERT is idempotent — ``INSERT OR IGNORE`` is a no-op when the key
-- already exists (preserves an operator's chosen value across re-runs).
--
-- NOTE: The migration runner at ``db/migrations/__init__.py`` wraps the
-- whole file in a single BEGIN/COMMIT transaction and splits statements
-- via ``sqlite3.complete_statement``; this file MUST NOT contain its own
-- BEGIN/COMMIT/ROLLBACK and every statement must end with ``;``.

ALTER TABLE activity_steps ADD COLUMN expected_answer TEXT NULL;

INSERT OR IGNORE INTO settings (key, value) VALUES ('qa_grading', 'off');
