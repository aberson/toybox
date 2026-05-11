-- Phase H Step H4: promote ``banned_themes`` from a per-child column on
-- ``children`` to a single household-scoped key in ``settings``.
--
-- Runtime behavior is unchanged: the escalation pipeline already
-- UNION-aggregated ``banned_themes`` across every child before sending
-- the prompt to Claude (see ``content_resolver.aggregate_child_constraints``
-- + escalation.py:870), so this migration formalizes that semantic in
-- the data model rather than altering it.
--
-- Three logical steps below:
--   1. Snapshot non-empty per-child values into a TEMP table.
--   2. Union / normalise (trim + lowercase) / dedupe / sort / re-join
--      via a recursive CTE; ``INSERT OR REPLACE`` into ``settings``
--      only when the resulting string is non-empty.
--   3. Rebuild ``children`` without the ``banned_themes`` column.
--      ``PRAGMA foreign_keys=OFF/ON`` brackets the rebuild for
--      documentation only — SQLite ignores ``PRAGMA foreign_keys`` inside
--      a transaction, and the migration runner wraps this whole file in
--      a BEGIN/COMMIT, so these pragmas are effectively no-ops. The
--      rebuild is safe today because no migration declares
--      ``REFERENCES children`` (verified — ``rg "REFERENCES children"
--      src/toybox/db/migrations/`` returns no matches). If a future
--      migration adds a children-pointing FK, this CREATE-AS / DROP /
--      RENAME idiom needs to either run outside the wrapping transaction
--      or preserve FK rows manually — the pragmas alone will NOT save
--      referential integrity.
--
-- NOTE: The migration runner at ``db/migrations/__init__.py`` wraps the
-- whole file in a single BEGIN/COMMIT transaction and splits statements
-- via ``sqlite3.complete_statement``; this file MUST NOT contain its
-- own BEGIN/COMMIT/ROLLBACK and every statement must end with ``;``.

CREATE TEMP TABLE _bt_snapshot AS
SELECT banned_themes
FROM children
WHERE banned_themes IS NOT NULL AND TRIM(banned_themes) != '';

INSERT OR REPLACE INTO settings (key, value)
SELECT 'banned_themes_global', value
FROM (
    WITH RECURSIVE split(remaining, token) AS (
        SELECT banned_themes || ',', '' FROM _bt_snapshot
        UNION ALL
        SELECT
            SUBSTR(remaining, INSTR(remaining, ',') + 1),
            SUBSTR(remaining, 1, INSTR(remaining, ',') - 1)
        FROM split
        WHERE remaining != ''
    ),
    tokens AS (
        SELECT DISTINCT LOWER(TRIM(token)) AS theme
        FROM split
        WHERE TRIM(token) != ''
    )
    SELECT GROUP_CONCAT(theme, ', ') AS value
    FROM (SELECT theme FROM tokens ORDER BY theme)
)
WHERE value IS NOT NULL;

-- Rebuild ``children`` without ``banned_themes``. SQLite < 3.35 does not
-- support ``ALTER TABLE ... DROP COLUMN`` portably for our supported
-- targets, so the CREATE-AS / DROP / RENAME idiom is the safest path.
PRAGMA foreign_keys=OFF;

CREATE TABLE children_new (
    id             TEXT PRIMARY KEY,
    display_name   TEXT NOT NULL,
    birthdate      TEXT,
    pronouns       TEXT,
    reading_level  TEXT,
    interests      TEXT,
    comfort        TEXT,
    notes          TEXT
);

INSERT INTO children_new (
    id, display_name, birthdate, pronouns,
    reading_level, interests, comfort, notes
)
SELECT
    id, display_name, birthdate, pronouns,
    reading_level, interests, comfort, notes
FROM children;

DROP TABLE children;

ALTER TABLE children_new RENAME TO children;

PRAGMA foreign_keys=ON;

DROP TABLE _bt_snapshot;
