-- Phase C step 15 — activity-quality telemetry & eval scaffold.
--
-- Every activity generation (offline OR Claude) writes a ``labeled_events``
-- row before returning the activity. Parent-signal endpoints
-- (thumbs-up / dismiss-before-start / end-early) update the row in
-- place by ``activity_id``. The async judge sampler fills
-- ``judge_scores_json`` and ``judge_run_at`` on the 1-in-N sampled rows.
--
-- The schema deliberately keeps ``parent_signal`` and ``judge_scores_json``
-- in separate columns: the judge is a cost-saving proxy and ``parent_signal``
-- is the only real label. Phase E step 27 (first SFT iteration) reads
-- this table filtered by ``safety>=4 AND mean_quality>=3.5 AND
-- parent_signal != -1`` and exports via ``eval_dump`` — the column
-- shape supports that query without migration.

CREATE TABLE labeled_events (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id           TEXT NOT NULL,
    generated_at          TEXT NOT NULL,
    generator_path        TEXT NOT NULL CHECK (generator_path IN ('claude','offline','local')),
    inputs_chatml_json    TEXT NOT NULL,
    activity_json         TEXT NOT NULL,
    parent_signal         REAL,
    parent_signal_set_at  TEXT,
    ended_at_step         INTEGER,
    judge_scores_json     TEXT,
    judge_run_at          TEXT
);

-- One row per activity. ``activity_id`` is logically a foreign key to
-- ``activities.id`` but we don't declare it as such — Phase E exports
-- may run after the source activity is purged for retention, and the
-- labeled_events row is the load-bearing artifact that must outlive
-- the activity row.
CREATE UNIQUE INDEX idx_labeled_events_activity_id
    ON labeled_events(activity_id);

-- Export queries (eval_dump --since <ISO>) filter by generated_at and
-- group by generator_path; the composite index keeps the export scan
-- bounded even after months of writes.
CREATE INDEX idx_labeled_events_generated_at
    ON labeled_events(generated_at, generator_path);
