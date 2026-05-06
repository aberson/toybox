-- Phase F Step F3 — toy_actions table for per-toy/per-slot pixel-art sprite jobs.
--
-- See documentation/toy-action-sprites-plan.md §F3 + §"New components"
-- §storage/toy_actions.py. One row per (toy_id, slot) pair; the worker
-- (F4) upserts the row through the four states the parent UI grid
-- renders. Status values are the string members of
-- toybox.image_gen.models.ToyActionStatus:
--
--   queued      -- worker accepted the job, not yet started
--   running     -- pipeline currently generating
--   done        -- PNG written; image_path populated
--   failed      -- generation raised; error_msg populated
--   superseded  -- a regen request preempted this row mid-flight
--
-- The 10 slot keys are pinned in toybox.image_gen.models.ACTION_SLOTS.
-- The storage layer (storage/toy_actions.py) synthesizes a "not_started"
-- placeholder row for any slot missing from this table so the UI grid
-- always renders 10 cells.
--
-- ON DELETE CASCADE on the toys FK is a defensive backstop for
-- hard-delete code paths; the production toy-archive flow uses
-- toys.archived = 1 (soft-archive) and calls
-- storage.toy_actions.delete_for_toy_archived to clear DB rows
-- explicitly. The PNG files on disk are intentionally LEFT in place
-- per plan §Out: cleanup is operator-driven, not automatic.

CREATE TABLE toy_actions (
    toy_id      TEXT NOT NULL,
    slot        TEXT NOT NULL,
    status      TEXT NOT NULL,
    image_path  TEXT,
    seed        INTEGER,
    error_msg   TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (toy_id, slot),
    FOREIGN KEY (toy_id) REFERENCES toys(id) ON DELETE CASCADE
);

CREATE INDEX idx_toy_actions_status ON toy_actions(status);
CREATE INDEX idx_toy_actions_toy_id_status ON toy_actions(toy_id, status);
