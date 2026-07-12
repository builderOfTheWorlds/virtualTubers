-- Creates this project's tables inside the "virtualtubers" database.
-- Run after 01_create_role_and_database.sql, as the new role:
--
--   psql -h <POSTGRES_HOST> -p <POSTGRES_PORT> -U virtualtubers -d virtualtubers \
--        -f docs/sql/02_create_tables.sql
--
-- Optional in practice: services/message-logger/logger.py and
-- services/log-shipper/shipper.py each run their own CREATE TABLE IF NOT
-- EXISTS on startup. This file mirrors those statements so the full schema
-- exists in one reviewable place. If either service's schema changes,
-- update both places.

CREATE TABLE IF NOT EXISTS messages (
    id          UUID PRIMARY KEY,
    "from"      TEXT NOT NULL,
    "to"        TEXT NOT NULL,
    type        TEXT NOT NULL,
    payload     JSONB NOT NULL,
    timestamp   TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_messages_to ON messages ("to");
CREATE INDEX IF NOT EXISTS idx_messages_type ON messages (type);

-- One row per coding-backend run (typed unpacking of coding_run_report bus
-- messages by message-logger) — the A/B comparison table for the
-- native | opencode | aider coder workers. See docs/coding_backend.md.
CREATE TABLE IF NOT EXISTS coding_backend_runs (
    message_id    UUID PRIMARY KEY,
    worker_id     TEXT NOT NULL,
    backend       TEXT NOT NULL,
    task          TEXT NOT NULL,
    retry_count   INTEGER NOT NULL DEFAULT 0,
    success       BOOLEAN NOT NULL,
    commit_sha    TEXT,
    files_changed INTEGER NOT NULL DEFAULT 0,
    insertions    INTEGER NOT NULL DEFAULT 0,
    deletions     INTEGER NOT NULL DEFAULT 0,
    duration_s    DOUBLE PRECISION NOT NULL DEFAULT 0,
    output        TEXT,
    error         TEXT,
    reported_at   TIMESTAMPTZ NOT NULL,
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_runs_backend ON coding_backend_runs (backend);
CREATE INDEX IF NOT EXISTS idx_runs_worker ON coding_backend_runs (worker_id);

-- Typed unpacking of replay_narration messages: one row per spoken scene
-- from a Rerun Theater airing (see docs/revoice.md). The bus message itself
-- is text-only, but app/narration_store.py has replay_pane.py upsert the
-- WAV bytes + measured duration for the same (message_id, scene_index)
-- directly from the pane after every fresh airing, so a later replay_request
-- with payload.narration: "reuse" can replay it without calling the LLM/TTS
-- again. This insert and that upsert converge on one row set regardless of
-- which lands first.
CREATE TABLE IF NOT EXISTS voiced_narration (
    message_id       UUID NOT NULL,
    worker_id        TEXT NOT NULL,
    episode          TEXT NOT NULL,
    aired_at         TIMESTAMPTZ NOT NULL,
    scene_index      INTEGER NOT NULL,
    scene_kind       TEXT NOT NULL,
    speaker          TEXT NOT NULL,
    text             TEXT NOT NULL,
    audio            BYTEA,
    audio_duration_s DOUBLE PRECISION,
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (message_id, scene_index)
);
CREATE INDEX IF NOT EXISTS idx_voiced_narration_episode ON voiced_narration (episode);
ALTER TABLE voiced_narration ADD COLUMN IF NOT EXISTS audio BYTEA;
ALTER TABLE voiced_narration ADD COLUMN IF NOT EXISTS audio_duration_s DOUBLE PRECISION;

CREATE TABLE IF NOT EXISTS container_logs (
    id             BIGSERIAL PRIMARY KEY,
    container_name TEXT NOT NULL,
    stream         TEXT NOT NULL,
    message        TEXT NOT NULL,
    log_timestamp  TIMESTAMPTZ NOT NULL,
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_container_logs_name ON container_logs (container_name);
CREATE INDEX IF NOT EXISTS idx_container_logs_timestamp ON container_logs (log_timestamp);
