-- Phase F Step F6 — add action_slot column to activity_steps.
--
-- See documentation/plan/phase-f-toy-action-sprites.md §F6 + §"New components"
-- §"Action vocabulary". The generator (offline + Claude single-shot)
-- emits one of the 10 fixed action slot keys per step; the kiosk
-- StepCard renders a per-toy sprite next to the body text when the
-- slot is set AND the activity has at least one toy with a sprite for
-- that slot (F7).
--
-- Old rows (pre-F6) default NULL → kiosk renders no sprite (current
-- pre-F6 behavior). The 10 valid values are pinned in
-- toybox.image_gen.models.ACTION_SLOTS; enforcement is at the
-- Pydantic + generator + template-loader layer rather than a CHECK
-- constraint so the offline-fallback path can degrade gracefully if
-- a future slot vocabulary diverges from on-disk rows. The column
-- has very low cardinality (10 values + NULL) so no index is needed.

ALTER TABLE activity_steps ADD COLUMN action_slot TEXT;
