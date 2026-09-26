-- v4 reference schema for the character_profile database.
--
-- Plan: docs/charcterProfileGenerationNotes/character_generator_updater_v4.md §6.
-- Validated by .claude/prompts/character_v4_plan_validation.py against a real
-- Postgres 16 + pgvector.
--
-- The build copies this file to app/character/sql/001_init.sql (WP-04). After that the
-- copy under app/character/sql/ is the source of truth. Later schema
-- changes are NEW numbered files (002_*.sql, ...), never edits to 001.
--
-- Rules:
--   * Every statement is idempotent (IF NOT EXISTS / OR REPLACE / DROP IF EXISTS).
--   * DOUBLE PRECISION, never DOUBLE.
--   * IDs are TEXT (uuid4 strings) unless noted.
--   * Embedding columns are dimension-free `vector` + embed_model/embed_dim,
--     so changing the embedding model is a re-embed job, not a migration.
--     v1 has no ANN index; recall does cosine in-process (plan §7).
--   * Story positions are (book, chapter[, char_offset]) integer columns so
--     "before position X" is a plain tuple comparison.

CREATE EXTENSION IF NOT EXISTS vector;

-- ── Source material ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS source_works (
    id             TEXT PRIMARY KEY,                 -- e.g. 'harry_potter'
    title          TEXT NOT NULL,
    config_sha256  TEXT NOT NULL,
    loaded_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS source_chapters (
    source_id          TEXT NOT NULL REFERENCES source_works(id),
    number             INTEGER NOT NULL,             -- global chapter number (manifest.json "number")
    book               INTEGER NOT NULL,
    book_chapter       INTEGER NOT NULL,
    title              TEXT NOT NULL,
    story_date         TEXT,
    body_offset_start  INTEGER NOT NULL,             -- offsets into the ORIGINAL flat text
    body_offset_end    INTEGER NOT NULL,
    original_text      TEXT NOT NULL,
    cleaned_text       TEXT,                         -- NULL until stage 1 has run
    clean_report       JSONB,                        -- stage 1 audit numbers
    sha256             TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, number)
);
CREATE INDEX IF NOT EXISTS idx_source_chapters_book
    ON source_chapters (source_id, book, book_chapter);

CREATE TABLE IF NOT EXISTS source_utterances (       -- stage 2: one row per quoted line
    id            BIGSERIAL PRIMARY KEY,
    source_id     TEXT NOT NULL,
    chapter       INTEGER NOT NULL,
    offset_start  INTEGER NOT NULL,                  -- offsets into cleaned_text
    offset_end    INTEGER NOT NULL,
    text          TEXT NOT NULL,
    speaker       TEXT,                              -- canonical cast key, NULL = unattributed
    method        TEXT NOT NULL CHECK (method IN ('rule', 'llm', 'unknown')),
    confidence    DOUBLE PRECISION NOT NULL DEFAULT 0,
    FOREIGN KEY (source_id, chapter) REFERENCES source_chapters (source_id, number)
);
CREATE INDEX IF NOT EXISTS idx_source_utterances_speaker
    ON source_utterances (source_id, speaker);

CREATE TABLE IF NOT EXISTS source_scenes (           -- stage 2: scene segmentation
    id            BIGSERIAL PRIMARY KEY,
    source_id     TEXT NOT NULL,
    chapter       INTEGER NOT NULL,
    offset_start  INTEGER NOT NULL,
    offset_end    INTEGER NOT NULL,
    speakers      TEXT[] NOT NULL DEFAULT '{}',
    present       TEXT[] NOT NULL DEFAULT '{}',
    location      TEXT,
    FOREIGN KEY (source_id, chapter) REFERENCES source_chapters (source_id, number)
);
CREATE INDEX IF NOT EXISTS idx_source_scenes_present
    ON source_scenes USING GIN (present);

CREATE TABLE IF NOT EXISTS timeline_events (         -- stage 4
    id                TEXT PRIMARY KEY,
    source_id         TEXT NOT NULL REFERENCES source_works(id),
    era               TEXT NOT NULL CHECK (era IN ('pre_story', 'story')),
    book              INTEGER,                       -- story position of the event (NULL for pre_story)
    chapter           INTEGER,
    char_offset       INTEGER,
    summary           TEXT NOT NULL,
    participants      TEXT[] NOT NULL DEFAULT '{}',
    evidence          JSONB NOT NULL DEFAULT '[]',   -- [{chapter, offset_start, offset_end}]
    revealed_book     INTEGER,                       -- where a reader first learns of it
    revealed_chapter  INTEGER,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_timeline_events_participants
    ON timeline_events USING GIN (participants);
CREATE INDEX IF NOT EXISTS idx_timeline_events_pos
    ON timeline_events (source_id, book, chapter);

-- ── Characters and baselines ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS characters (
    id                       TEXT PRIMARY KEY,
    slug                     TEXT NOT NULL UNIQUE,   -- 'harry'
    name                     TEXT NOT NULL,
    campaign                 TEXT NOT NULL,
    status                   TEXT NOT NULL DEFAULT 'draft'
                                 CHECK (status IN ('draft', 'active', 'retired')),
    retains_fragments        BOOLEAN NOT NULL DEFAULT FALSE,
    is_main                  BOOLEAN NOT NULL DEFAULT FALSE,
    aliases                  TEXT[] NOT NULL DEFAULT '{}',
    avatar_params            JSONB,                  -- 8 sliders + accent_color (plan §9)
    active_baseline_version  INTEGER,                -- pointer; revert = move it (checked in code)
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS character_agents (        -- bus agent id -> character
    agent_id      TEXT PRIMARY KEY,
    character_id  TEXT NOT NULL REFERENCES characters(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS character_backstories (
    character_id  TEXT NOT NULL REFERENCES characters(id),
    version       INTEGER NOT NULL,
    layer         TEXT NOT NULL CHECK (layer IN ('believed', 'truth')),
    content       JSONB NOT NULL,
    evidence      JSONB NOT NULL DEFAULT '[]',
    llm_model     TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (character_id, version, layer)
);

CREATE TABLE IF NOT EXISTS character_baselines (     -- versioned, never modified by the loop
    character_id      TEXT NOT NULL REFERENCES characters(id),
    version           INTEGER NOT NULL,
    profile           JSONB NOT NULL,
    backstory_nodes   JSONB NOT NULL DEFAULT '[]',   -- permanent knowledge contract for the graph process
    baseline_book     INTEGER NOT NULL,              -- the story position this baseline represents
    baseline_chapter  INTEGER NOT NULL,
    source_id         TEXT,
    change_notes      TEXT,
    created_by        TEXT NOT NULL DEFAULT 'generator',
    llm_model         TEXT,
    raw_response      TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (character_id, version)
);

-- ── The loop ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS loop_weeks (
    campaign     TEXT NOT NULL,
    week         INTEGER NOT NULL,                   -- derived from the clock (plan §2)
    starts_at    TIMESTAMPTZ NOT NULL,
    ends_at      TIMESTAMPTZ NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open'
                     CHECK (status IN ('open', 'closing', 'closed')),
    reset_steps  JSONB NOT NULL DEFAULT '{}',        -- step key -> {completed_at, ...}
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign, week),
    CHECK (ends_at > starts_at)
);

CREATE TABLE IF NOT EXISTS experience_events (       -- written only by the ingest consumer
    message_id   TEXT NOT NULL,                      -- bus message id (dedupe key)
    character_id TEXT NOT NULL REFERENCES characters(id),
    msg_type     TEXT NOT NULL,
    from_agent   TEXT NOT NULL,
    scene_id     TEXT,
    visibility   TEXT NOT NULL CHECK (visibility IN ('self', 'present')),
    text         TEXT NOT NULL DEFAULT '',
    payload      JSONB NOT NULL,
    ts           TIMESTAMPTZ NOT NULL,               -- from the message body, not Kafka
    loop_week    INTEGER NOT NULL,
    loop_day     DATE NOT NULL,                      -- America/New_York calendar day
    embedding    vector,
    embed_model  TEXT,
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (message_id, character_id)           -- one message can reach several characters
);
CREATE INDEX IF NOT EXISTS idx_experience_char_week_day
    ON experience_events (character_id, loop_week, loop_day, ts);
CREATE INDEX IF NOT EXISTS idx_experience_ts
    ON experience_events (ts);

CREATE TABLE IF NOT EXISTS daily_summaries (
    character_id  TEXT NOT NULL REFERENCES characters(id),
    loop_day      DATE NOT NULL,
    loop_week     INTEGER NOT NULL,
    summary       JSONB NOT NULL,
    event_count   INTEGER NOT NULL,
    llm_model     TEXT,
    raw_response  TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (character_id, loop_day)
);

-- ── Fragments (insert-only; see trigger below) ──────────────────────────────

CREATE TABLE IF NOT EXISTS memory_fragments (
    id                   TEXT PRIMARY KEY,
    character_id         TEXT NOT NULL REFERENCES characters(id),
    source_week          INTEGER NOT NULL,
    gist                 TEXT NOT NULL,              -- lossy, emotional, first person
    hooks                JSONB NOT NULL DEFAULT '{}',-- {entities, places, objects, tone, weekday, hour}
    anchor_event_id      TEXT,
    source_event_ids     TEXT[] NOT NULL DEFAULT '{}',
    parent_fragment_ids  TEXT[] NOT NULL DEFAULT '{}',
    gist_embedding       vector,
    embed_model          TEXT,
    embed_dim            INTEGER,
    rank_rationale       TEXT,
    llm_model            TEXT,
    raw_response         TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_memory_fragments_character
    ON memory_fragments (character_id);
CREATE INDEX IF NOT EXISTS idx_memory_fragments_hooks
    ON memory_fragments USING GIN (hooks);

-- week_knowledge_nodes references memory_fragments, so it is created after it.
CREATE TABLE IF NOT EXISTS week_knowledge_nodes (
    id                     TEXT PRIMARY KEY,
    character_id           TEXT NOT NULL REFERENCES characters(id),
    loop_week              INTEGER NOT NULL,
    loop_day               DATE,
    archived_at_week       INTEGER,                  -- NULL = reachable by the character
    name                   TEXT NOT NULL,            -- kebab-case, validated (plan §10)
    kind                   TEXT NOT NULL,
    statement              TEXT NOT NULL,            -- first person
    source_event_ids       TEXT[] NOT NULL DEFAULT '{}',
    grew_from_fragment_id  TEXT REFERENCES memory_fragments(id),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (character_id, loop_week, name)
);
CREATE INDEX IF NOT EXISTS idx_wk_nodes_char_archived
    ON week_knowledge_nodes (character_id, archived_at_week);
CREATE INDEX IF NOT EXISTS idx_wk_nodes_char_week
    ON week_knowledge_nodes (character_id, loop_week);

CREATE TABLE IF NOT EXISTS week_knowledge_edges (    -- graph-builder contract; v1 writes none
    id                TEXT PRIMARY KEY,
    character_id      TEXT NOT NULL REFERENCES characters(id),
    loop_week         INTEGER NOT NULL,
    archived_at_week  INTEGER,
    src_node_id       TEXT NOT NULL REFERENCES week_knowledge_nodes(id),
    dst_node_id       TEXT NOT NULL REFERENCES week_knowledge_nodes(id),
    relation          TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_wk_edges_char_archived
    ON week_knowledge_edges (character_id, archived_at_week);
CREATE INDEX IF NOT EXISTS idx_wk_edges_char_week
    ON week_knowledge_edges (character_id, loop_week);

CREATE TABLE IF NOT EXISTS fragment_lead_up (        -- the beats before the moment, in order
    fragment_id  TEXT NOT NULL REFERENCES memory_fragments(id) ON DELETE CASCADE,
    position     SMALLINT NOT NULL,                  -- 0 = earliest
    event_id     TEXT,
    text         TEXT NOT NULL,
    embedding    vector,
    PRIMARY KEY (fragment_id, position)
);

CREATE TABLE IF NOT EXISTS fragment_links (
    from_fragment_id  TEXT NOT NULL REFERENCES memory_fragments(id) ON DELETE CASCADE,
    to_fragment_id    TEXT NOT NULL REFERENCES memory_fragments(id) ON DELETE CASCADE,
    relation          TEXT NOT NULL CHECK (relation IN ('grew_from', 'co_occurred', 'evokes')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (from_fragment_id, to_fragment_id, relation)
);

CREATE TABLE IF NOT EXISTS fragment_unlocks (        -- dormant -> unlocked, permanent (decision D-08)
    fragment_id    TEXT PRIMARY KEY REFERENCES memory_fragments(id) ON DELETE CASCADE,
    character_id   TEXT NOT NULL REFERENCES characters(id),
    unlocked_week  INTEGER NOT NULL,
    unlocked_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    recall_id      TEXT
);

CREATE TABLE IF NOT EXISTS fragment_recalls (        -- audit of every unease/surface event
    id             TEXT PRIMARY KEY,
    fragment_id    TEXT NOT NULL REFERENCES memory_fragments(id) ON DELETE CASCADE,
    character_id   TEXT NOT NULL REFERENCES characters(id),
    loop_week      INTEGER NOT NULL,
    ts             TIMESTAMPTZ NOT NULL,
    beat_event_id  TEXT,
    level          TEXT NOT NULL CHECK (level IN ('unease', 'surface')),
    activation     DOUBLE PRECISION NOT NULL,
    signals        JSONB NOT NULL DEFAULT '{}',      -- {hooks, trajectory, gist}
    judge_verdict  BOOLEAN,
    judge_reason   TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_fragment_recalls_char_week
    ON fragment_recalls (character_id, loop_week);

-- Insert-only enforcement. Test tools (plan §5 testctl) opt out per transaction
-- with:  SET LOCAL character.allow_test_mutation = 'on';
CREATE OR REPLACE FUNCTION forbid_fragment_mutation() RETURNS trigger AS $$
BEGIN
    IF coalesce(current_setting('character.allow_test_mutation', true), '') = 'on' THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION '% on % is forbidden: fragment data is immutable', TG_OP, TG_TABLE_NAME;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_memory_fragments_immutable ON memory_fragments;
CREATE TRIGGER trg_memory_fragments_immutable
    BEFORE UPDATE OR DELETE ON memory_fragments
    FOR EACH ROW EXECUTE FUNCTION forbid_fragment_mutation();

DROP TRIGGER IF EXISTS trg_fragment_lead_up_immutable ON fragment_lead_up;
CREATE TRIGGER trg_fragment_lead_up_immutable
    BEFORE UPDATE OR DELETE ON fragment_lead_up
    FOR EACH ROW EXECUTE FUNCTION forbid_fragment_mutation();

DROP TRIGGER IF EXISTS trg_fragment_links_immutable ON fragment_links;
CREATE TRIGGER trg_fragment_links_immutable
    BEFORE UPDATE OR DELETE ON fragment_links
    FOR EACH ROW EXECUTE FUNCTION forbid_fragment_mutation();

DROP TRIGGER IF EXISTS trg_fragment_unlocks_immutable ON fragment_unlocks;
CREATE TRIGGER trg_fragment_unlocks_immutable
    BEFORE UPDATE OR DELETE ON fragment_unlocks
    FOR EACH ROW EXECUTE FUNCTION forbid_fragment_mutation();

-- ── Jobs, artifacts, ops ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS character_jobs (          -- every CLI job run, plus queued generation work
    id            TEXT PRIMARY KEY,
    job           TEXT NOT NULL,                     -- 'daily-maintenance', 'weekly-reset', 'generate', ...
    character_id  TEXT,
    status        TEXT NOT NULL
                      CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled', 'skipped')),
    dry_run       BOOLEAN NOT NULL DEFAULT FALSE,
    params        JSONB NOT NULL DEFAULT '{}',
    result        JSONB,
    error         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,
    heartbeat_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_character_jobs_job_created
    ON character_jobs (job, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_character_jobs_status_created
    ON character_jobs (status, created_at);

CREATE TABLE IF NOT EXISTS character_artifacts (     -- LLM outputs kept for audit (not replay)
    id            BIGSERIAL PRIMARY KEY,
    job_id        TEXT REFERENCES character_jobs(id),
    character_id  TEXT,
    kind          TEXT NOT NULL,
    content       JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_character_artifacts_char_kind
    ON character_artifacts (character_id, kind);

CREATE TABLE IF NOT EXISTS ingest_status (           -- heartbeat from the ingest consumer
    consumer_group     TEXT PRIMARY KEY,
    last_message_ts    TIMESTAMPTZ,                  -- body timestamp of the newest committed message
    last_committed_at  TIMESTAMPTZ,
    messages_seen      BIGINT NOT NULL DEFAULT 0,
    rows_written       BIGINT NOT NULL DEFAULT 0,
    unmapped           BIGINT NOT NULL DEFAULT 0,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS backup_runs (             -- written by deploy/character-profile-db/scripts/backup.sh
    id           BIGSERIAL PRIMARY KEY,
    kind         TEXT NOT NULL,                      -- 'daily', 'weekly', 'manual', 'restore-drill'
    started_at   TIMESTAMPTZ NOT NULL,
    finished_at  TIMESTAMPTZ,
    status       TEXT NOT NULL CHECK (status IN ('running', 'ok', 'failed')),
    path         TEXT,
    bytes        BIGINT,
    detail       TEXT
);

-- ── Read-only display role (decision D-05) ──────────────────────────────────
-- The role itself is created by the deploy stack's init service (it needs
-- CREATEROLE). This block only grants, and is a no-op when the role is absent.
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'character_reader') THEN
        EXECUTE 'GRANT USAGE ON SCHEMA public TO character_reader';
        EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA public TO character_reader';
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO character_reader';
    END IF;
END
$$;
