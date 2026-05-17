-- Phase L Step L1 — drop three deprecated Phase K play-surface flags.
--
-- Phase L re-frames jokes and songs from spontaneous interjections into
-- per-activity reward types. The three surface flags that gated the
-- old behavior become dead settings — keeping them would be confusing
-- in any parent-debug surface and risks a future caller silently
-- branching on a flag the production code path no longer consults.
--
-- Rows removed (each seeded by 0015):
--
--   * ``play_embedded_enabled``     — Phase K K14 mid-activity picker
--   * ``play_endings_enabled``      — Phase K K3 ending_step interjection
--   * ``play_spontaneity_enabled``  — Phase K K15 advance-time interjection
--
-- The actual surface code is L5's territory; this migration only deletes
-- the rows. Production callers in L5 will be edited to ignore / remove
-- references to these keys.
--
-- If the three rows do not exist (e.g. a fresh DB where Phase K's 0015
-- seeded them but they were already deleted by hand, or a DB that
-- skipped 0015 entirely — not possible in production but harmless
-- here), the DELETE is a no-op. Idempotent.
--
-- Forward-only (invariant 10); no rollback.

DELETE FROM settings WHERE key IN (
    'play_embedded_enabled',
    'play_endings_enabled',
    'play_spontaneity_enabled'
);
