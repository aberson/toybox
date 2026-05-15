-- Phase K Step K1 — persona role weights + voice profile + spontaneity rates.
--
-- See documentation/phase-k-plan.md §5 ("Data model — migrations 0014 + 0015").
-- Three new columns on the ``personas`` table:
--
--   role_weights        JSON object of role-name -> 0.0..2.0 float biases
--                       used by the slot-fill engine (K4) when picking which
--                       toy fills each role slot. ``{}`` = uniform pick.
--   voice_profile       JSON ``{rate: 0.5..2.0, pitch: 0.0..2.0,
--                       voice_name?: str}`` consumed by the kiosk TTS layer
--                       (K8). NULL = system default voice settings.
--   spontaneity_rates   JSON ``{jokes: 0.0..1.0, songs: 0.0..1.0}`` per-content
--                       max-rate input for the advance-time spontaneity roll
--                       (K15). The default ``{0.0, 0.0}`` means a persona
--                       NEVER triggers an emergent interjection until the
--                       operator (or K1's built-in persona JSON edits) set
--                       a non-zero value.
--
-- Numeric ranges are NOT enforced at the SQL layer — Pydantic models in
-- :mod:`toybox.personas.models` (RoleWeights / VoiceProfile / SpontaneityRates)
-- validate at the API + loader boundaries. Keeping the SQL permissive
-- preserves forward compatibility if v2 widens the bounds.
--
-- Forward-only per invariant 10 (no rollback path). Existing rows take the
-- column defaults: ``role_weights = '{}'``, ``voice_profile IS NULL``,
-- ``spontaneity_rates = '{"jokes":0.0,"songs":0.0}'``. This is the
-- "custom persona" default per phase-k-plan §5 table — ``{}`` uniform
-- pick, no voice override, never interjects. K1 separately edits the four
-- built-in persona JSONs in ``src/toybox/personas/library/`` with their
-- non-default values; the loader UPSERT path picks those up on next
-- startup and overwrites the default-backfilled values for the library
-- personas. User-created (custom) personas keep the defaults.
--
-- NOTE: The migration runner at ``db/migrations/__init__.py`` wraps the
-- whole file in a single BEGIN/COMMIT transaction and splits statements
-- via ``sqlite3.complete_statement``; this file MUST NOT contain its
-- own BEGIN/COMMIT/ROLLBACK and every statement must end with ``;``.

ALTER TABLE personas ADD COLUMN role_weights TEXT NOT NULL DEFAULT '{}';

ALTER TABLE personas ADD COLUMN voice_profile TEXT;

ALTER TABLE personas ADD COLUMN spontaneity_rates TEXT NOT NULL DEFAULT '{"jokes":0.0,"songs":0.0}';
