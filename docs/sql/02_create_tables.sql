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
