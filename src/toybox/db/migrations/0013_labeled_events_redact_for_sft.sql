-- Phase E3 Step 1 — operator opt-out for SFT export.
--
-- Adds ``redact_for_sft`` to ``labeled_events`` so an operator can
-- flag individual rows as ineligible for the Phase E SFT corpus when
-- the activity contains PII the automated redactor can't catch (a
-- child name spelled phonetically in transcript_window, a place name
-- the family doesn't want emitted, etc.). The default is ``0`` —
-- "include in SFT corpus" — so the column is a backwards-compatible
-- opt-out, NOT an opt-in. Existing rows are backfilled to ``0`` by
-- the DEFAULT clause; no UPDATE is needed.
--
-- The ``--sft-filter`` CLI in ``toybox.ai.labeled_events`` adds
-- ``redact_for_sft = 0`` to its WHERE clauses; rows with ``1`` are
-- excluded from counts and from the eval_dump export by the same
-- gate.
--
-- NOTE: The migration runner at ``db/migrations/__init__.py`` wraps
-- the whole file in a single BEGIN/COMMIT transaction; this file
-- MUST NOT contain its own BEGIN/COMMIT/ROLLBACK and every statement
-- must end with ``;``.

ALTER TABLE labeled_events
    ADD COLUMN redact_for_sft INTEGER NOT NULL DEFAULT 0;
