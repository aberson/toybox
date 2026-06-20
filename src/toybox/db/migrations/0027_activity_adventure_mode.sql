-- Phase W Step W4 — dynamic adventure mode flag on activities.
--
-- ``adventure`` marks an activity whose steps are GENERATED beat-by-beat
-- by the adventure engine (:mod:`toybox.activities.adventure`) as the
-- child advances, instead of being read from a fixed branching template.
--
--   * 0 (default) — ordinary template-driven activity. Every row in the
--     catalog before this migration ran has 0 here, so propose/advance
--     behave EXACTLY as before for them.
--   * 1 — adventure activity. ``_do_propose`` seeds the first beat and
--     ``post_advance`` generates each subsequent beat (capped at
--     :data:`toybox.activities.adventure.MAX_ADVENTURE_BEATS`, after which
--     the normal reward/terminal/end path runs).
--
-- No CHECK constraint (matches the 0016 ``activity_steps.kind`` /
-- 0020 ``activities.reward_type`` convention — validation lives in the
-- Pydantic API layer). Forward-only (invariant 10); no rollback.

ALTER TABLE activities ADD COLUMN adventure INTEGER NOT NULL DEFAULT 0;
