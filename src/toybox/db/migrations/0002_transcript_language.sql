-- Add language column to transcripts. Defaults to 'unknown' to match
-- the STT module's UNKNOWN_LANGUAGE sentinel; existing rows (Step 13
-- ships before any production write path is wired) backfill to the
-- same sentinel.

ALTER TABLE transcripts ADD COLUMN language TEXT NOT NULL DEFAULT 'unknown';
