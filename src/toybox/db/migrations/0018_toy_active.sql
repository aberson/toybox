-- Post-K — per-toy "active" toggle for play-time filtering.
--
-- Use case: child wants to play only cats today; parent deactivates the
-- non-cat toys for the session. Distinct from ``archived`` (soft-delete
-- — toy is gone from the parent UI) and from ``allowed_roles`` (which
-- roles a toy can be cast as).
--
-- Filter contract:
--   * Parent toy list AND image-dedup still see inactive toys
--     (so the parent can re-enable them and so re-uploading the same
--     photo still 409s).
--   * Mention triggers (``triggers/dynamic.py``) and the role-casting
--     pool (``activities/content_resolver.py``) exclude ``active = 0``
--     rows — that's the whole point of the toggle.
--
-- Default 1 = active so every existing row stays in play after the
-- migration. Forward-only (invariant 10).

ALTER TABLE toys ADD COLUMN active INTEGER NOT NULL DEFAULT 1;
