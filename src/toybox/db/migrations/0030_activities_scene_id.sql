-- Phase Y Step Y5 — per-activity kiosk scene-backdrop id.
--
-- ``scene_id`` records the resolved scene-backdrop for an activity (the
-- full-bleed illustrated PNG the kiosk composes the step card + sprites
-- over). Resolved at propose time by
-- ``activities/content_resolver.py::resolve_scene_id`` via the chain:
--   template scene_id  ->  child-interest match  ->  DEFAULT_SCENE_ID.
--
-- Nullable so every existing (pre-Phase-Y) row stays valid — the wire
-- serializer maps a NULL/absent ``scene_id`` to ``scene_url = null`` and the
-- kiosk renders no backdrop (the prior flat-gradient look). When set, the
-- value is a member of ``toybox.activities.scene_catalog.SCENE_IDS`` and the
-- on-disk PNG lives at ``data/images/scenes/<scene_id>.png`` (served via the
-- existing ``/api/static/images`` mount).
--
-- Forward-only (invariant 10).

ALTER TABLE activities ADD COLUMN scene_id TEXT;
