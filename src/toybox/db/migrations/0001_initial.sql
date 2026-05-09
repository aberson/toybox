-- v1 schema. Forward-only.

CREATE TABLE personas (
    id                  TEXT PRIMARY KEY,
    display_name        TEXT NOT NULL,
    archetype           TEXT,
    system_prompt       TEXT NOT NULL,
    avatar_image_path   TEXT,
    avatar_image_hash   TEXT,
    behavior_tags       TEXT,
    age_range_min       INTEGER,
    age_range_max       INTEGER,
    language            TEXT NOT NULL DEFAULT 'en',
    source              TEXT,
    default_voice_tone  TEXT,
    created_at          TEXT NOT NULL
);

CREATE TABLE toys (
    id            TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    image_path    TEXT NOT NULL,
    image_hash    TEXT NOT NULL,
    type          TEXT,
    tags          TEXT,
    persona_id    TEXT REFERENCES personas(id) ON DELETE RESTRICT,
    archived      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    last_used_at  TEXT
);

CREATE TABLE children (
    id             TEXT PRIMARY KEY,
    display_name   TEXT NOT NULL,
    birthdate      TEXT,
    pronouns       TEXT,
    reading_level  TEXT,
    interests      TEXT,
    comfort        TEXT,
    banned_themes  TEXT,
    notes          TEXT
);

CREATE TABLE rooms (
    id            TEXT PRIMARY KEY,
    display_name  TEXT,
    image_path    TEXT,
    image_hash    TEXT,
    notes         TEXT
);

CREATE TABLE room_features (
    id       TEXT PRIMARY KEY,
    room_id  TEXT NOT NULL REFERENCES rooms(id) ON DELETE RESTRICT,
    name     TEXT,
    tags     TEXT,
    UNIQUE (room_id, name)
);

CREATE TABLE sessions (
    id          TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    mode        INTEGER,
    mic_id      TEXT
);

CREATE TABLE activities (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE RESTRICT,
    state           TEXT NOT NULL,
    version         INTEGER NOT NULL,
    summary         TEXT,
    persona_id      TEXT REFERENCES personas(id) ON DELETE RESTRICT,
    child_ids       TEXT,
    room_ids        TEXT,
    toy_ids         TEXT,
    intent_source   TEXT,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    ended_at        TEXT
);

CREATE TABLE activity_steps (
    id               TEXT PRIMARY KEY,
    activity_id      TEXT NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    seq              INTEGER NOT NULL,
    body             TEXT NOT NULL,
    sfx              TEXT,
    expected_action  TEXT,
    current          INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE transcripts (
    id                TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES sessions(id) ON DELETE RESTRICT,
    mic_id            TEXT,
    started_at        TEXT,
    ended_at          TEXT,
    text              TEXT,
    confidence        REAL,
    triggered_intent  TEXT
);

CREATE TABLE feedback (
    id            TEXT PRIMARY KEY,
    activity_id   TEXT NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    step_seq      INTEGER,
    kind          TEXT,
    signature     TEXT NOT NULL,
    reason        TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE auth_tokens (
    token_hash           TEXT PRIMARY KEY,
    scope                TEXT NOT NULL,
    child_session_label  TEXT,
    created_at           TEXT NOT NULL,
    expires_at           TEXT NOT NULL,
    last_used_at         TEXT,
    revoked_at           TEXT
);

CREATE TABLE settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

CREATE UNIQUE INDEX idx_toy_image_hash
    ON toys(image_hash)
    WHERE archived = 0;

CREATE UNIQUE INDEX idx_room_image_hash
    ON rooms(image_hash)
    WHERE image_hash IS NOT NULL;

CREATE UNIQUE INDEX idx_persona_avatar_hash
    ON personas(avatar_image_hash)
    WHERE source != 'library' AND avatar_image_hash IS NOT NULL;

-- Seed defaults.
INSERT INTO settings (key, value) VALUES ('listening_mode', '3');
INSERT INTO settings (key, value) VALUES ('claude_call_min_interval_sec', '30');
INSERT INTO settings (key, value) VALUES ('claude_spontaneous_interval_sec', '300');
INSERT INTO settings (key, value) VALUES ('vad_aggressiveness', '2');
INSERT INTO settings (key, value) VALUES ('log_level', 'INFO');
INSERT INTO settings (key, value) VALUES ('mic_enabled', 'true');
INSERT INTO settings (key, value) VALUES ('time_of_day_aware', 'true');
INSERT INTO settings (key, value) VALUES ('image_gen_mode', 'cartoon');
