-- Phase R Step R3: optional Q&A gating on activity steps.
--
-- ``question`` holds the text of a question the child must answer
-- before advancing past this step (NULL on the overwhelming majority
-- of steps — non-Q&A content is unaffected).
--
-- ``question_approved`` records the parent's resolution: NULL when
-- the question has not yet been resolved (pending), 1 when approved
-- ("Good answer"), 2 when skipped. The advance endpoint gates on
-- this column: if question IS NOT NULL AND question_approved IS NULL,
-- it returns 409 {"detail": "question_pending"}.
ALTER TABLE activity_steps ADD COLUMN question TEXT NULL;
ALTER TABLE activity_steps ADD COLUMN question_approved INTEGER NULL;
