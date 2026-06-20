-- Phase X Step X1 — per-room ``room_type`` category + ``active`` toggle.
--
-- Mirrors the toy ``active`` contract (migration 0018): ``active = 0``
-- means "room exists but stay out" — the parent UI still lists it (so
-- they can re-enable it) but every play-time room selector excludes it.
--
-- ``room_type`` is a free-form category label (e.g. "bedroom",
-- "kitchen") the parent can set; nullable, no play-time semantics.
--
-- Filter contract:
--   * Parent room list (``GET /api/rooms``) AND image-dedup still see
--     inactive rooms (so the parent can re-enable them and so
--     re-uploading the same photo still 409s).
--   * The role-resolution / propose-time room pool
--     (``activities/content_resolver.py::resolve_rooms`` and every
--     consumer downstream of it, incl. ``ai/tools.py`` get_room) exclude
--     ``active = 0`` rows — that's the whole point of the toggle.
--
-- Default 1 = active so every existing row stays in play after the
-- migration. Forward-only (invariant 10).

ALTER TABLE rooms ADD COLUMN room_type TEXT;
ALTER TABLE rooms ADD COLUMN active INTEGER NOT NULL DEFAULT 1;
