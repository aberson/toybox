-- Phase L Step L1 — rewards catalog table.
--
-- Picture-rewards are a new content type that fires as an end-of-activity
-- celebration step. The parent uploads a still image, tags it with
-- free-form theme strings, and pins one of six animations to it; the
-- server resolves an eligible reward at advance time and the kiosk
-- renders ``<img>`` with the configured CSS animation.
--
-- See documentation/phase-l-plan.md §"Rewards table" for the full design.
--
-- Columns:
--
--   * ``id`` — opaque PK (e.g. ``reward_<slug>`` or a UUID); chosen by
--     the API layer at create time.
--   * ``display_name`` — parent-facing label rendered in the rewards
--     list (e.g. ``"Treasure Chest"``).
--   * ``image_path`` — relative path to the PNG/JPG under
--     ``data/images/rewards/<id>.<ext>``. Mirrors the existing per-toy
--     image storage convention; the upload pipeline writes the file in
--     the API layer (L2) the same way toy uploads do today.
--   * ``image_hash`` — content hash of the image bytes. Used for
--     duplicate detection at upload time; mirrors ``toys.image_hash``
--     usage but no UNIQUE index here in v1 — the API layer enforces
--     dedup against active rows at confirm time.
--   * ``tags`` — JSON-encoded array of lowercased NFKC-normalized
--     strings. Empty list ``'[]'`` is canonical for "no tags". The API
--     layer (L2) normalizes free-form input before persistence; the
--     resolver (L3) does tag-match against this column to bias picks
--     toward template ``recommended_themes`` ∪ transcript-extracted
--     themes.
--   * ``animation`` — one of the six string members of
--     :class:`toybox.activities.models.Animation` (``shine`` | ``jump``
--     | ``spin`` | ``pulse`` | ``wobble`` | ``float``). No CHECK
--     constraint here: the validator lives in the Pydantic API layer
--     (matching the convention 0016 sets for ``activity_steps.kind``).
--     Forward-only philosophy (invariant 10) means a future animation
--     taxonomy expansion does not need a follow-up migration to alter
--     the constraint.
--   * ``active`` — 1 = visible to the resolver pool; 0 = hidden but
--     not soft-deleted (parent toggle). Mirrors ``toys.active`` (0018).
--     Default 1 so confirm-time uploads are immediately eligible.
--   * ``archived`` — 1 = soft-deleted (parent hit "delete" in the
--     rewards list). Mirrors ``toys.archived`` (0001). The resolver
--     filters on ``active = 1 AND archived = 0``.
--   * ``created_at`` — ISO-8601 UTC timestamp written by the API at
--     INSERT time. Mirrors the rest of the schema.
--   * ``last_used_at`` — ISO-8601 UTC timestamp; updated by the
--     resolver when this reward is selected. NULL until first use.
--     Powers the active-first / recency sort in the parent list.
--
-- Forward-only (invariant 10); no rollback.

CREATE TABLE rewards (
    id            TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    image_path    TEXT NOT NULL,
    image_hash    TEXT NOT NULL,
    tags          TEXT NOT NULL DEFAULT '[]',
    animation     TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1,
    archived      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    last_used_at  TEXT
);
