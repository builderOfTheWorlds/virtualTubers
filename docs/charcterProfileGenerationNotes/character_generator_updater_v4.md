# Character Generator + Updater: Design Plan v4

Status: design of record, ready to build. Written 2026-09-25.
Supersedes v3 (`character_generator_updater_v3.md`), v2, v1, and
`.claude/prompts/character_generator_updater.md`. Those files stay as the
decision trail. The user's inline answers are in `review_4.md` (cited as
r4:N) and in the handover `.claude/prompts/character_generator_updater_handover.md` §6.

Companion files (all in this directory unless a path says otherwise):

| File | What it is |
|---|---|
| `character_v4_decisions.md` | Decision log: every choice, why it was made, and how to change it later |
| `character_v4_build_playbook.md` | How the local model (qwen3.8:27b) builds this, as work packages with test lists |
| `v4_reference_character_profile.sql` | The full `character_profile` schema, validated on Postgres 16 + pgvector |
| `v4_reference_messages.sql` | `messages` changes: added columns, the partition migration, compaction SQL |
| `.claude/prompts/character_v4_plan_validation.py` | Proves the SQL and the loop clock work. **33/33 checks pass** (2026-09-25) |
| `.claude/prompts/character_v4_recall_probe.py` | Proves trajectory recall separates a replayed lead-up from noise using real `nomic-embed-text` vectors. **Passes** (2026-09-25) |
| `.claude/prompts/character_v4_build_status.md` | Build tracker the orchestrator updates after each work package |

Code claims cite `path:line`. Anything not checked says "unverified".

---

## 0. What changed from v3

| Topic | v3 | v4 |
|---|---|---|
| Database | New `characters` DB on mafober, pgvector unknown | `character_profile` in its own pgvector container on mafober CT 101, port 5433. The deploy package is built (`deploy/character-profile-db/`). One owner role plus a read-only `character_reader` role (D-05) |
| Pilot cast | Top 6 by dialogue | harry, ron, hermione (r4:61) |
| Source | `chapters_v2/` then clean | Same, but the pilot only needs **book 1** (17 chapters, 77,597 words), which cuts LLM cleaning time from days to hours (D-16). `chapters_v2/` has no quotes, commas or apostrophes (measured), so speaker tagging depends on stage 1 restoring quotes |
| Start position | Chapter 1, or chapter 2 for a playable Harry (open) | The story runs from book 1 chapter 1 to chapter 17 (user). Each character's baseline is set at their **entry chapter**: Harry ch 2, Ron and Hermione ch 6 (D-07) |
| Fragments | Hidden until recall | **Dormant, then unlocked for good.** A fragment is dormant (hidden) until its lead-up is matched in some later week. From then on it is part of the character's brief in every future week, framed as a feeling (D-08, from the user's answer to handover Q8) |
| Reset reaching workers | Not designed | A `character_refresh` bus message plus a brief cache with a TTL (D-02, user chose push refresh) |
| `messages` table | Not covered | Add a `character` column and a timestamp index. Nightly compaction does all three of A (drop noise), B (archive) and C (daily partitions) (D-03, user: "Do A, B, and C") |
| Jobs | "cli.py reset" | Every operation is its own CLI job with `--dry-run`, `--at`, advisory locks and fixed exit codes. The scheduler itself is next phase (D-13) |
| Backups | Not covered | Nightly `pg_dump` plus a ZFS snapshot on mafober `tank_0`, a separate host job (D-12) |
| Test controls | Revert only | `testctl`: a full set of manipulation commands, including undoing a reset and changing fragments, behind a confirm guard (D-10, user answer Q10) |
| Who builds it | Claude writes specs, qwen implements | qwen3.8:27b orchestrates **and** implements from this plan's work packages. Tests come from fixed test lists, written before the code (D-22) |

## 1. Source pipeline

Stage 0 (split) is built: `utilities/source_pipeline/`, 199/199 chapters in
`sourceworks/chapters_v2/` plus `manifest.json` (18 tests).

**Pilot scope is book 1** (`source.scope.books: [1]`, D-16). The whole
series is only needed for the GM-only `truth` layer about later books. That
is deferred until book 1 works end to end. Widening the scope is a config
change plus re-running stages 1–6.

What the text looks like (measured 2026-09-25 on `1_002_The Vanishing Glass.txt`):

- It is one line with no newlines, no quotes, no commas and no apostrophes.
  Sentence ends are ` .`.
- `said Harry` appears 5 times in the chapter. Unquoted dialogue runs straight
  into narration ("Excuse me Harry said to the plump woman .Hello dear she said").
- Ron first appears at char 6,531 of `1_006`, Hermione at 23,441.

So **stage 1 has to restore quotation marks** before stage 2 can attribute
speakers. The old phase 2 run could not do it:

| Measure (chapter 1_001 of the broken split, 1.05M words) | Value |
|---|---|
| Windows total / ok / error / hallucinated | 1,114 / 191 / 907 / 16 (`sourceworks/chapters_llm_restored/llm_restoration_results.json`) |
| Mean API time per window | 61 s (llama3.1:8b; two runs were competing for one Ollama) |
| Repeated 20-grams, input vs output | 123 vs 89,216 (overlaps joined twice, `sourceworks/phase2_llm_windowing_ollama.py:352`) |

### Stages

| # | Stage | Code (new unless noted) | In | Out | Validation |
|---|---|---|---|---|---|
| 0 | Split | `utilities/source_pipeline/split.py` (built) | flat text + ToC | chapters + manifest | built |
| 1 | Clean | `utilities/source_pipeline/src/cleaner.py`, `clean.py` | chapters_v2 | `sourceworks/chapters_clean/<file>.txt` + `clean_report.json` | word-sequence guard per window; whole-chapter audit (§1.1) |
| 2 | Tag | `src/tagger.py`, `tag.py` | cleaned chapters | `utterances.jsonl`, `scenes.jsonl` | ≥90% speaker accuracy on a 50-line hand check of `1_006` (operator gate) |
| 3 | Cast | `src/cast_rank.py`, `cast.py` | utterances | `cast.json`: ranked slugs, aliases, entry chapter | harry, ron, hermione, hagrid in the book-1 top 6 |
| — | Load | `app/character/generator/load_source.py` | manifest + stage 1–3 outputs | `source_works`, `source_chapters`, `source_utterances`, `source_scenes` | row counts match the files; sha256 matches the manifest |
| 4 | Timeline | `app/character/generator/timeline.py` | chapters in scope | `timeline_events` | every event cites offsets that exist; `pre_story` has no position |
| 5 | Backstory | `app/character/generator/backstory.py` | timeline + passages | `character_backstories` (`believed`, `truth`) | every claim cites evidence; `believed` uses only events before `entry_pos` |
| 6 | Baseline | `app/character/generator/baseline.py` | backstory + passages | `character_baselines` row, `characters.active_baseline_version` | JSON schema; node names pass §10 |
| 7 | Export | `app/character/generator/export.py` | DB | `campaigns/<pack>/cast/<slug>.yaml` | `app/campaign/pack.py` still loads the pack; unknown keys kept |

Stages 1–3 are text-in, text-out: no DB access and no network except the LLM
in stage 1. Stages 4–7 live under `app/character/generator/` because they
write character tables. `utilities/` never imports from `app/`.

### 1.1 Stage 1 rules (fixes the phase 1 and phase 2 defects)

- **Deterministic pass.** Use the contraction table from
  `sourceworks/phase1_deterministic.py:16-66`, minus the ambiguous entries
  `ill well shell hell` (`:36-39`), `id wed shed` (`:51-53`), `lets` (`:65`),
  plus `its were`. Those are left for the LLM. This fixes "rather I'll but
  happy". The old scripts stay untouched as legacy.
- **Windows.** Split a chapter into `core` spans of about 350 words. Each LLM
  call sees `left context + core + right context` (60 words each side), but
  **only the core span is kept**. Joining the cores rebuilds the chapter
  exactly once, so no overlap is duplicated.
- **Guard.** Normalise both sides: lowercase, strip every character that isn't
  `a-z0-9`, split on whitespace. The token sequences must be **identical**.
  This is stricter than the old ±5% word count, and it still allows
  punctuation, quotes, paragraph breaks and apostrophes (`well` → `we'll`
  normalises back to `well`). A failure or a timeout keeps the deterministic
  text for that core span and records why.
- **One run at a time.** A file lock in the output directory, with a 300 s
  timeout per call.
- **Audit per chapter** (`clean_report.json`): input and output word counts
  (must be equal), repeated 20-gram count in and out (output ≤ input + 2),
  guard pass rate, timeout count, and model. The chapter fails the audit if
  the word counts differ.
- **Model.** Configurable (`clean.model`). First calibrate on `1_001` and
  `1_006` with `llama3.1:8b`, `gemma4:12b-it-q4_K_M` and `qwen3.8:27b` (all on
  the gx10 Ollama, checked 2026-09-25). Pick the best guard pass rate that
  finishes book 1 overnight. Record the choice in the decision log.

### 1.2 Stage 2 and 3 rules

- Speaker attribution goes rule-based first: `<quote> said X`, `X said <quote>`,
  and `said X` with inversions, over the cast alias table. The LLM only
  handles leftover lines in context windows, returning JSON `{index, speaker}`.
  Store `method` (`rule|llm|unknown`) and `confidence` per line.
- A scene is a run of text with no location change and no gap longer than N
  sentences. `present[]` is the speakers plus any cast alias named in the scene.
  This is a heuristic, recorded as such.
- Stage 3 ranks by attributed words within scope. **Entry chapter** is the
  first chapter at or after `story_start` where the character has at least 3
  attributed lines. This gives Harry ch 2 and Ron and Hermione ch 6; it is
  checked by a test on the real corpus that skips if the corpus is absent.

## 2. The model in one paragraph

A campaign runs as a weekly time loop on the clock. The week runs Sunday
00:00 to Sunday 00:00 `America/New_York`, and week N is counted from a fixed
epoch Sunday in config (D-01). Each character has a permanent baseline:
identity, personality, objectives, and the `believed` backstory at their
entry chapter. The baseline never drifts, so there is no `profile_diff`, no
trait drift and no carried progress. During a week, everything a character
says, thinks or witnesses is ingested in real time. Each night at 00:00 NY the
day is summarised per character, and the summary becomes that week's
knowledge nodes. At Sunday 00:00 NY the week's knowledge is archived (kept,
never deleted, unreachable by the character). For characters with
`retains_fragments`, the weekly reset creates `fragments_per_week` lossy,
emotional, first-person fragments, each stored with the lead-up beats that
came before it. A new fragment is **dormant**. If a later week's beats replay
its lead-up closely enough, it surfaces as "a feeling surfaces: …", and from
then on it is **unlocked** permanently and carried in every future brief as a
feeling. Characters never know they are in a loop. Every week plays out
differently. LLM output is stored for audit, not replay.

**User's questions, answered:**

- r4:9 "what is profile_diff and trait drift?" v1 wrote a new profile version
  every week. `profile_diff` was the list of changes between versions, and
  trait drift let personality trait strengths move by up to ±0.15 a week. Both
  are removed because the baseline resets fully.
- r4:83 "what capture is this?" It is the step that records what each
  character says, thinks and witnesses into `experience_events`. In v4 that is
  the ingest consumer (Kafka `vtuber.messages` → `character_profile`, §3.3),
  build step WP-16.

## 3. Bus and ingestion

### 3.1 Verified facts

- One topic, `vtuber.messages`. `build_message()` (`app/message_bus.py:33`)
  sets `id` (uuid4, `:35`) and a UTC ISO `timestamp` (`:40`). Dedupe on `id`,
  and derive week and day from that timestamp.
- `MessageConsumer` hardcodes `auto_offset_reset="latest"` and
  `enable_auto_commit=True` (`app/message_bus.py:66-67`). Ingest must not use it.
- `agent_thinking` is published by `InstrumentedLLMClient.complete`
  (`app/agent_metrics.py:240-261`) with `from = worker_id` and payload
  `{"text": ...}`.
- Nothing in `app/campaign/` publishes to the bus. `character_say` and
  `scene_event` don't exist yet.
- Handlers are in `MESSAGE_HANDLERS` (`app/agent.py:1075`). The worker system
  prompt comes from `agent_config.get("system_prompt")` (for example
  `app/agent.py:197`).

### 3.2 Contracts (new; built in `app/character/bus_contracts.py`)

Character agent ids use the form `char:<slug>` (D-04). The live driver's id is
`char-live:<campaign>`.

```json
{"type": "character_say", "from": "char:harry", "to": "broadcast",
 "payload": {"campaign": "hp", "scene_id": "the-boy-who-lived", "character": "harry",
             "addressees": ["ron"], "present": ["harry", "ron", "hermione", "gm"],
             "text": "..."}}

{"type": "scene_event", "from": "char-live:hp", "to": "broadcast",
 "payload": {"campaign": "hp", "scene_id": "...", "kind": "narration|action|scene_start|scene_end|story_end",
             "character": null, "present": ["harry", "ron", "hermione", "gm"], "text": "..."}}

{"type": "character_refresh", "from": "character-updater", "to": "broadcast",
 "payload": {"campaign": "hp", "week": 12, "characters": ["*"],
             "reason": "weekly_reset|revert|testctl"}}
```

`agent_thinking` stays as it is. The live driver wraps one
`InstrumentedLLMClient` per character with `worker_id = "char:<slug>"`, so
each thought is attributed by `from`.

### 3.3 Ingest consumer (`app/character/ingest.py`, process `character-ingest`)

- `KafkaConsumer` with group `character-ingest`, `auto_offset_reset="earliest"`,
  `enable_auto_commit=False`. **Commit offsets after the DB commit**, in
  batches of up to 200 messages or 2 s.
- Allowlist: `agent_thinking`, `character_say`, `scene_event`. Everything
  else is skipped before any parsing beyond `type`.
- Routing is a pure function, `route(msg, agents, slugs, clock) -> list[row]`:
  - `agent_thinking`: `character_agents[from]` gives one row, `visibility='self'`.
    An unmapped `from` (the dev-team workers) is counted and skipped.
  - `character_say` / `scene_event`: one row per slug in
    `present ∪ {character} ∪ addressees` that is a known character,
    `visibility='present'`.
  - `loop_week` and `loop_day` come from the body `timestamp` via `clock.py`.
    A timestamp before the epoch is counted and skipped.
- Insert with `ON CONFLICT (message_id, character_id) DO NOTHING`, which makes
  redelivery harmless (validated).
- A malformed message is logged at ERROR, counted, and skipped. It never stalls
  the partition.
- Each batch updates `ingest_status` (last message ts, counts).
- No embeddings at ingest (D-20). Ingest has to keep up even when Ollama is busy.
- Retention: Kafka's default is 168 h, exactly one week. Raise it to 14 days
  if the broker disk allows (D-11, operator step OP-3). The recovery path is a
  backfill from `messages`: `ingest --backfill-from-messages --since <ts>`,
  read-only on the virtualtubers DB (WP-16, optional flag).

### 3.4 `messages` table (virtualtubers DB, 192.168.1.120:5432)

Verified: schema at `docs/sql/02_create_tables.sql:13-23` and
`services/message-logger/logger.py:19-30`. The only indexes are on `"to"` and
`type`. The logger is the only writer (`logger.py:82-86`, `ON CONFLICT (id)`)
and drops excluded types (`:183`). Nothing reads or prunes it. A second
`postgres` service exists in `docker-compose.yml:820-822` under the
`local-postgres` profile only. Production uses mafober :5432
(`docs/architecture_flow_diagram.md:21`).

Changes (full SQL in `v4_reference_messages.sql`, validated):

- **M1, additive (safe live):** `character TEXT` column, plus indexes on
  `(timestamp)` and `(character, timestamp)`. The logger fills `character`
  using `character_for_message(msg)` from the new `app/bus_attribution.py`:
  `payload.character`, else the `char:<slug>` prefix of `from`, else NULL
  (D-04). The logger Dockerfile gains one `COPY` line.
- **M2, daily partitions (operator-run, logger stopped):**
  `services/message-logger/migrate_partitioned.py` builds
  `messages_new PARTITION BY RANGE (timestamp)` with PK `(id, timestamp)`,
  daily partitions `messages_pYYYYMMDD` (UTC) and a DEFAULT partition, copies
  the rows, and swaps names. It keeps `messages_legacy` until the operator
  drops it. The logger detects the shape (`pg_class.relkind`): it uses
  `ON CONFLICT (id, timestamp)` when partitioned and creates partitions for
  today through today+14 at startup and hourly.
- **Pitfall (validated):** once the DEFAULT partition holds rows for a day, a
  partition for that day can no longer be created. So the logger always
  creates partitions **ahead**, and compaction drains old DEFAULT rows by
  moving them.
- **Three schema copies stay in sync** (`logger.py`,
  `docs/sql/02_create_tables.sql`, `docs/message_logger.md`). A test asserts
  the M1 statements appear in the first two.

Compaction is in §5.

## 4. Characters, backstory, positions

- Pilot cast: harry, ron, hermione. `retains_fragments: true` for harry only
  at first (v2 §2: the main streamed character). This is a config flag per
  character.
- Campaign positions (D-07), all config:
  - `story_start: {book: 1, chapter: 1}`, `story_end: {book: 1, chapter: 17}`
    (user: "start chapter 1 and continue through the first book").
  - Per character, `entry_pos` defaults to the entry chapter from stage 3 and
    can be overridden. The baseline is the character's state just before
    `entry_pos`, stored as `baseline_book/baseline_chapter`.
  - Harry's baseline is a 10-year-old in June 1991 who knows nothing of magic.
    The chapter 1 night (1 Nov 1981) belongs to the GM's narration and the
    `truth` layer.
- Two-layer backstory (v3 §5, unchanged):
  - `believed`: what the character knows at `entry_pos`, and the only layer
    the character sees.
  - `truth`: GM only. It feeds scene generation and later reveals, and is
    never in a brief.
  - Stage 5 filters `believed` to events with a position before `entry_pos`
    whose participants include the character or which the text shows the
    character learned.
- `revealed_book/revealed_chapter` on timeline events records where a reader
  learns of an event. The loop is free play, so reveals are optional.

## 5. Scheduled jobs and maintenance

Every job is a subcommand of one entry point:

```
python services/character-updater/main.py <job> [--campaign hp] [--character <slug>] [--dry-run] [--at <ISO time>] [-v]
```

- The logic lives in `app/character/*.py`. `services/character-updater/`
  only holds the process: argparse, config, exit codes, container (old D1).
- `weekly-reset --character harry --dry-run` is the old D1 `--once --character`
  contract.
- Every run writes a `character_jobs` row. A Postgres advisory lock keyed on
  `(job, campaign)` stops two copies running at once; the second exits 2.
- **Exit codes:** 0 done, 2 nothing to do (already done, locked, or not due),
  3 precondition not met (e.g. ingest lag), 1 failed.
- `--at` replaces "now" everywhere, for tests and catch-up runs. `--dry-run`
  prints each step and writes nothing, not even the jobs row.

| Job | When (NY) | Idempotency key | Steps and notes |
|---|---|---|---|
| `ingest` | always on | Kafka offsets + `(message_id, character_id)` | Long-running. Compose `restart: unless-stopped` keeps it up |
| `daily-maintenance` | 00:00 daily | `daily_summaries (character_id, loop_day)`; compaction is naturally idempotent | 1. **Summaries** for yesterday (NY date), for every active character. Waits up to `ingest.catchup_wait_s` (600) for ingest to pass midnight, then proceeds and marks the summary `partial`. 2. **Compaction** of `messages`, only if ingest lag is under `ingest.max_lag_messages`, else skipped with exit 3 (steps already done stay done) |
| `weekly-reset` | Sunday 00:00, after daily-maintenance | `loop_weeks(campaign, week).reset_steps` | 1. `close_saturday`: make sure Saturday's summaries exist (runs step 1 of daily-maintenance if not). 2. `select_fragments`. 3. `archive`: set `archived_at_week = W` on week W nodes and edges, for every character, in one transaction. 4. `open_next`: insert week W+1, status `open`. 5. `refresh`: publish `character_refresh`. Each step runs under `SELECT … FOR UPDATE` on the week row and records `{completed_at}`. A re-run skips completed steps (validated) |
| `initialize` | manual | stage outputs keyed by sha256 / `(character, version)` | Load source (§1), then stages 4–6 for the configured characters, seed `character_agents` (`char:<slug>`), export cast YAML, open the current week |
| `story-start` | before the story (e.g. Sunday 00:05) | none needed | Enables `char-live:<campaign>` in WorkerControl (`app/worker_control.py:27`), then `docker compose up -d character-live` (or runs the driver inline with `--foreground`) |
| `story-stop` | any time | none needed | Disables `char-live:<campaign>`. The driver finishes the current scene, publishes `scene_event kind=story_end`, and exits 0. The driver also stops by itself at the week's `ends_at` |
| `revert` | manual | none needed | `--character X --to-version N` moves `active_baseline_version`, then publishes `character_refresh` |
| `testctl …` | manual | none needed | §5.2 |
| `status` | manual | none needed | Week, reset steps, ingest lag, last jobs, fragments per character (dormant/unlocked) |
| backup | 00:30 daily, **on mafober** | `backup_runs` | `deploy/character-profile-db/scripts/backup.sh` (§5.1) |

### 5.1 Backups (D-12)

These run on the mafober host, not gx10, because ZFS snapshots need Proxmox
root, and dumping locally keeps the data on `tank_0` without a network copy
(user: "Needs to be on mafober tank_0").

- `pct exec 101 -- docker exec character-profile-db pg_dump -U postgres -Fc character_profile`
  writes to `/tank_0/utilities/character-profile-db-backups/daily/character_profile_<YYYYmmdd_HHMM>.dump`.
  That dataset is created by `install.sh`.
- `zfs snapshot tank_0/utilities/character-profile-db@auto-<YYYYmmdd>`.
- Retention: 14 daily dumps, 8 weekly (Sunday) dumps, 14 snapshots. Prune
  only files and snapshots this script created (by name pattern).
- Each run writes a `backup_runs` row via `psql`.
- `backup.sh --restore-drill` restores the newest dump into
  `character_profile_drill` in the same container, compares row counts of
  every table against the live DB, drops the drill DB, and exits non-zero on
  any mismatch. **Run one before the pilot stores anything** (build step OP-4).
- Scheduling on mafober is a root crontab line (example in §13). The user
  said the scheduler is next phase, so it is documented, not installed.

### 5.2 Test manipulation controls (`testctl`, D-10)

The user wants "a full set of manipulation controls" that won't be used
regularly. Every command:

- supports `--dry-run`
- writes a `character_jobs` row (`job='testctl:<cmd>'`)
- needs `--confirm <database name>` when it deletes anything
- uses `SET LOCAL character.allow_test_mutation = 'on'` (the only way past
  the fragment immutability triggers, validated)

| Command | Effect |
|---|---|
| `reset-undo --week W` | Reverses weekly reset W: un-archive nodes and edges with `archived_at_week=W`, delete fragments with `source_week=W` (cascades to lead-up, links, unlocks, recalls), delete week W+1 if it has no events, clear W's `reset_steps`, set W `open` |
| `fragment add --character X --gist ... [--lead-up-file f.txt]` | Hand-made fragment (for the recall harness) |
| `fragment delete --id` / `unlock --id` / `lock --id` | Change one fragment |
| `week wipe --week W [--character X]` | Delete that week's experience events, summaries and nodes |
| `seed --file fixture.yaml` | Insert synthetic experience events (for replaying a scenario) |
| `clock --show --at <ISO>` | Print the week, day and bounds for a time. Jobs themselves take `--at` |
| `refresh [--character X]` | Publish `character_refresh` |

## 6. Database: `character_profile`

Deploy (built, not yet deployed): `deploy/character-profile-db/`, image
`pgvector/pgvector:0.8.6-pg16-bookworm`, CT 101 at 192.168.1.120:5433,
database `character_profile` owned by role `character_profile`.

**Schema:** `v4_reference_character_profile.sql`, 23 tables. It is
idempotent, and was validated twice-applied on Postgres 16.2 + pgvector
0.6.2 (the pgserver bundle). The build copies it to
`app/character/sql/001_init.sql`, and later changes are new numbered files
(D-25). Highlights:

| Group | Tables |
|---|---|
| Source | `source_works`, `source_chapters`, `source_utterances`, `source_scenes` (GIN on `present`), `timeline_events` |
| Character | `characters` (+ `active_baseline_version` pointer), `character_agents`, `character_backstories`, `character_baselines` |
| Loop | `loop_weeks (campaign, week)` + `reset_steps`, `experience_events` PK `(message_id, character_id)`, `daily_summaries`, `week_knowledge_nodes/edges` (indexed on `(character_id, archived_at_week)` and `(character_id, loop_week)`) |
| Fragments | `memory_fragments`, `fragment_lead_up`, `fragment_links`, `fragment_unlocks`, `fragment_recalls` |
| Ops | `character_jobs`, `character_artifacts`, `ingest_status`, `backup_runs` |

Rules:

- DOUBLE PRECISION only (validated: 2 such columns, no bare DOUBLE).
- The fragment tables (`memory_fragments`, `fragment_lead_up`,
  `fragment_links`, `fragment_unlocks`) are **insert-only**, enforced by
  triggers. Test tools opt out per transaction only.
- Embedding columns are dimension-free `vector` plus `embed_model`, so a model
  change is a re-embed job, not a migration. There is no ANN index in v1:
  about 52 fragments × 8 beats per character per year is tiny, so recall does
  cosine in-process (v3 §7).

**Roles (D-05, the user asked for the options):**

| Option | Roles | For | Against |
|---|---|---|---|
| 1 | `character_profile` owns everything | Already built; simplest; one password | The display panes would get write access if they used it |
| 2 (v3) | `character_ingest` (events, agents) + `character_service` (rest) | Least privilege between ingest and jobs | Cross-owner FKs need extra grants; migrations need an admin role; little real gain because both are our own code on one host |
| **3 (chosen)** | Option 1 + **`character_reader`** (SELECT only, default privileges for future tables) | The tuber screens and any GUI read week memories safely; no change to the built package's ownership | One more password in the stack env |

The `init` service creates `character_reader` from `CHARACTER_READER_PASSWORD`
(WP-01). `001_init.sql` grants SELECT when the role exists (validated,
including tables created later). To move to option 2 later, add the roles and
grants in a new migration and give ingest its own DSN env (`CHARACTER_INGEST_DB_*`,
already read by config, defaulting to the main DSN).

**Local test instances (D-06, user: "sure we can do that"):**

- Unit tests: fakes, no DB.
- Integration tests on the dev PC use the `pgserver` pip package: Postgres 16
  + pgvector in a temp dir, no Docker (validated on Windows; there is no
  aarch64 wheel).
- On gx10: `CHARACTER_DB_DATA_DIR=/home/secus/character-profile-test docker compose -f deploy/character-profile-db/docker-compose.yml -p character-profile-test up -d`
  with `CHARACTER_DB_PORT=5434`, then point `CHARACTER_TEST_DSN` at it.
- Tests pick `CHARACTER_TEST_DSN` if set, else pgserver, else skip.

## 7. Recall

v3 §7 is unchanged in shape: hooks, trajectory, gist, activation, thresholds,
cooldown, harness first. v4 adds the lifecycle and calibrated numbers.

- **Where it runs (D-18):** in the live driver process, which already sees
  every beat because it publishes them. `app/character/recall.py` is pure: it
  takes an injected `embed(texts)` and a fragment loader, so it can move into
  its own bus consumer later without changes.
- **Candidates:** the character's **dormant** fragments only.
- **Signals, per beat, each 0..1:**
  - `hooks`: fraction of the fragment's hook terms (entities, places, objects,
    each with character aliases) found in the beat text, case-insensitive
    whole words.
  - `trajectory`: Smith-Waterman alignment of the character's last `window_beats`
    beats against the fragment's `lead_up`. Match score is `cos − bias`, and
    gaps cost `gap`. The best local score is divided by
    `len(lead_up) × (ref_cosine − bias)` and clipped to 1.
  - `gist`: `clip((cos(beat, gist) − bias) / (ref_cosine − bias), 0, 1)`.
- **Activation** = `w_h·hooks + w_t·trajectory + w_g·gist`. It decays by
  `decay` per beat and spreads `spread` × to `fragment_links` neighbours.
- **Levels:**
  - `unease` injects "something about this feels familiar" once per cooldown.
  - `surface` asks the judge LLM (`judge` profile, strict JSON
    `{echoes: bool, reason}`). On yes: inject "a feeling surfaces: <gist>", and
    insert a `fragment_unlocks` row (permanent, D-08).
  - Every unease or surface event writes a `fragment_recalls` row.
- **Measured calibration** (`character_v4_recall_probe.py`, nomic-embed-text,
  2026-09-25):

  | Measure | Value |
  |---|---|
  | Unrelated beat pairs, cosine | 0.35–0.55 (median 0.43) |
  | Paraphrased beat pairs, cosine | 0.70–0.86 (mean 0.79) |
  | Raw SW score at bias 0.6, gap 0.1: replay / shuffled / unrelated | 0.102 / 0.037 / 0.000 |

  So `bias: 0.6` and `ref_cosine: 0.8` normalise a real replay to about 0.5,
  a shuffled one to about 0.2, and noise to 0. Starting thresholds are
  `unease: 0.35`, `surface: 0.55`. **The recall harness (WP-23) must re-tune
  these on replayed beats before the pilot.**
- Activation state is in memory. A driver restart starts from zero, which is
  acceptable: stories restart weekly.

## 8. Brief, and reaching running workers

**Brief** (`app/character/brief.py`), assembled from the DB, never stored:

1. **Who you are:** identity, personality, speech style (profile).
2. **What you remember of your life so far:** the `believed` layer.
3. **What you want:** objectives.
4. **What you've learned this week:** this week's unarchived node statements,
   newest first, capped.
5. **Feelings you carry:** unlocked fragment gists, framed as instincts, with
   no dates and no mention of weeks (D-08).
6. **Behaviour contract:** stay in character, speak in first person, one line
   at a time, and treat a surfaced feeling as déjà vu. Never state that time
   repeats.

There is no dormant fragment list (the user said the character sees nothing
until a fragment triggers). It is capped at `brief.max_chars`
(6000); drop the oldest week-knowledge lines first.

**Reaching running processes (D-02):**

- The live driver caches each brief for `brief.cache_ttl_s` (300).
- On `character_refresh` it drops the cache and clears per-week state:
  improviser transcript, activation, surfaced-this-week list.
- The TTL is the safety net if a refresh message is missed.
- A worker started after the reset reads the new brief anyway.
- `agent.py` workers don't play characters today. When they do, the same
  handler is added to `MESSAGE_HANDLERS` (Phase 4, hand-applied).

**Live driver (`app/character/live.py`, D-19).** Nothing drives a campaign
with an improviser today: `app/campaign/cli.py:62-67` builds a
`SceneRenderer` with no improviser. The driver:

- loads the pack and builds `CampaignRuntime` (`app/campaign/runtime.py:62`)
- keeps one `LLMImproviser` per cast member (`app/campaign/improviser.py:39`),
  each with its own `InstrumentedLLMClient(worker_id="char:<slug>")`. Its
  `system_prompt` is replaced with the brief, plus "a feeling surfaces: …" for
  fragments surfaced this week. The improviser reads `cast_member.system_prompt`
  (`improviser.py:80-81`). The driver never sets `carry` or `loop`, because
  `improviser.py:106-112` would print "Loop N" into the prompt.
- uses `ObservingRenderer`, a subclass of `SceneRenderer` in the character
  package, which overrides `render_beat` (`renderer.py:60`). It calls the
  parent, then publishes `character_say` (dialogue) or `scene_event`
  (narration/action), feeds the beat to recall, and calls `observe()` on every
  improviser. `app/campaign/` is not modified.
- sets `present` to the pack's players plus the GM (v1 heuristic, one
  function to replace later)
- stops between scenes when `char-live:<campaign>` is disabled or the week
  ends. Then it publishes `story_end`.

## 9. Avatar

- `codec_avatar` renders every worker (`config/workers/coder.yaml:94-97`).
- v1 uses the 8 sliders in `SLIDER_DEFAULTS` (`app/character_schema.py:38-47`)
  plus `accent_color` from `ACCENT_COLORS` (`:33`).
- `characters.avatar_params` defaults to `resolve_params(None)` (`:290`) with
  a per-character `accent_color` from config.
- Export writes it inline into the cast YAML as `character_params`. The pack
  loader ignores unknown keys (`app/campaign/pack.py:29-46`).
- Export also prints a roster snippet for `config/workers/roundtable.yaml`,
  whose tiles accept inline dicts (`app/tile_avatar.py:184`, done last session).
- **TODO (Phase 4):** map appearance text to sliders. The seam is
  `generator/avatar.py: map_appearance(profile) -> dict`, which returns the
  defaults in v1.

## 10. Generator prompts

Prompt templates live in `app/character/prompts/*.md`. Every one:

- writes character-facing text in the **first person** (r4:110)
- asks for strict JSON matching a schema that is validated in code
- includes node-name examples:
  - Good: `knows-wingardium-leviosa`, `lives-in-cupboard-under-stairs`,
    `wants-to-win-house-cup`, `fears-uncle-vernon`, `trusts-hagrid`.
  - Bad, with the reason given in the prompt: `godsley-shelter-abuse`
    (misspelled, not first person), `wingardsium-levia` (misspelled),
    `knows-qui-llusions` (garbled), `pass-third-year-exams` (wrong age for an
    11-year-old in book 1).
- Mechanical check (`app/character/node_names.py`): regex
  `^[a-z0-9]+(-[a-z0-9]+){1,7}$`, a verb-first allowlist
  (`knows|lives|wants|fears|trusts|likes|dislikes|believes|remembers|is|has|can|cannot|owes|suspects|hopes`),
  no word from a banned list. A bad name is rejected and retried once, then
  dropped and logged.
- Age and period sense (no book 3 exams in book 1) is a prompt instruction,
  checked by stage 6 tests on fixtures. It can't be fully mechanical.

## 11. `config/character.yaml`

```yaml
character:
  campaign: hp
  timezone: America/New_York
  loop:
    epoch: "2026-10-04"          # a Sunday; week 1 starts at 00:00 NY on this date (D-01)
    fragments_per_week: 1
  db:                            # every value can be overridden by env CHARACTER_DB_*
    host: 192.168.1.120
    port: 5433
    dbname: character_profile
    user: character_profile      # password only from env CHARACTER_DB_PASSWORD
  messages_db_env_prefix: POSTGRES_   # virtualtubers DB (compaction, backfill)
  source:
    id: harry_potter
    config: utilities/source_pipeline/sources/harry_potter.yaml
    scope: { books: [1] }        # D-16
    story_start: { book: 1, chapter: 1 }
    story_end:   { book: 1, chapter: 17 }
  pack: campaigns/hptest
  characters:
    harry:    { retains_fragments: true, is_main: true, accent_color: GREEN }
    ron:      { accent_color: RED }
    hermione: { accent_color: PURPLE }
    # entry_pos: { book: 1, chapter: 2 }   # optional override of stage 3
  llm:
    base_url: http://192.168.1.23:11434
    profiles:                    # D-15
      summary:   { model: "qwen3.8:27b", temperature: 0.3, num_ctx: 16384, timeout_s: 300, think: false }
      fragment:  { model: "qwen3.8:27b", temperature: 0.5, num_ctx: 16384, timeout_s: 300, think: false }
      generator: { model: "qwen3.8:27b", temperature: 0.4, num_ctx: 32768, timeout_s: 600, think: false }
      judge:     { model: "qwen3.8:27b", temperature: 0.0, num_ctx: 8192,  timeout_s: 60,  think: false }
      clean:     { model: "llama3.1:8b", temperature: 0.0, num_ctx: 4096,  timeout_s: 300, think: false }
    max_retries: 2
  ingest:
    kafka_topic: vtuber.messages
    consumer_group: character-ingest
    types: [agent_thinking, character_say, scene_event]
    batch_max: 200
    batch_wait_s: 2
    catchup_wait_s: 600
    max_lag_messages: 1000
  compaction:                    # D-03
    noisy_types: [status_update]
    noisy_keep_hours: 6
    hot_keep_days: 2
    move_batch: 5000
    partitions_ahead_days: 14
  brief: { max_chars: 6000, cache_ttl_s: 300, week_knowledge_max: 40 }
  recall:
    embeddings: { base_url: http://192.168.1.23:11434/v1, model: nomic-embed-text, dim: 768 }
    lead_up_beats: 8
    window_beats: 12
    bias: 0.6
    ref_cosine: 0.8
    gap: 0.1
    weights: { hooks: 0.3, trajectory: 0.5, gist: 0.2 }
    thresholds: { unease: 0.35, surface: 0.55 }
    decay: 0.85
    spread: 0.3
    cooldown_beats: 40
  reset: { refresh_mode: push }  # push | none (D-02)
  testctl: { enabled: true }
```

`config.py` validates it: types, `epoch` is a Sunday, weights sum to 1, and
threshold order. It returns a frozen dataclass tree. **Every decision in the
log that can be changed later is a key here, or one function named in the log.**

## 12. Open items

All previously open questions have a chosen default (see the decision log).
These are worth a look from the user, and none of them block the build:

- **O-A (D-12):** backups run as a separate job on mafober instead of as step 3
  of daily-maintenance (reason in the log). Confirm.
- **O-B (D-07):** baselines at each character's entry chapter (Harry ch 2)
  rather than all at ch 1. Confirm.
- **O-C (D-03):** hot `messages` keep 2 days; the screens only show minutes
  (user). Weekly memories for the screens come from `character_profile`
  through `character_reader`, as a Phase 4 display.
- **O-D:** stage 1 model choice. It is measured in WP-07 and recorded.
- **O-E (OP-3):** Kafka disk headroom for 14-day retention. Measured by the
  operator step.
- **Housekeeping:** two stale phase 2 runs were still alive on 2026-09-25
  (Python PIDs 688 and 50688, `phase2_llm_windowing_ollama.py`). They compete
  for the gx10 Ollama that the build needs. Stop them (OP-1).

## 13. Build order

The detail per step is in `character_v4_build_playbook.md`. The phases are
gates: don't start a phase until the previous one's gate passes.

| Phase | Steps | Gate |
|---|---|---|
| 0 operator | OP-1 stop stale runs · OP-2 deploy DB (+ reader role after WP-01), firewall 5433 · OP-3 Kafka retention · OP-4 backups + restore drill (after WP-02) · OP-5 optional gx10 test instance | `install.sh --verify` passes; restore drill exits 0 |
| 1 foundation | WP-00 harness on Windows · WP-01 reader role · WP-02 backup.sh · WP-03 config + clock · WP-04 db + migrations · WP-05 stores · WP-06 jobs + CLI + container | full suite green; `main.py status` works against a pgserver DB |
| 2 source + generator | WP-07 clean · WP-08 tag · WP-09 cast · WP-10 load · WP-11 llm/embeddings/prompts/node names · WP-12 timeline · WP-13 backstory · WP-14 baseline + export + `initialize` | book 1 cleaned with audit passing; trio baselines in the DB; `hptest` pack still validates |
| 3 loop runtime | WP-15 contracts + attribution · WP-16 ingest · WP-17 messages M1/M2 + logger · WP-18 compaction · WP-19 summaries + daily job · WP-20 fragments + weekly reset · WP-21 testctl · WP-22 brief · WP-23 recall + harness · WP-24 live driver + story jobs · WP-25 e2e simulated two weeks · WP-26 docs | e2e: week-1 fragment created, week-2 replay unlocks it, reset-undo restores week 1 |
| 4 next phase | scheduler wiring, knowledge display pane via `character_reader`, `agent.py` character handler, appearance → sliders, vLLM embeddings, whole-series truth layer | user decides |
| pilot | 2–3 real weeks with harry `retains_fragments` | fragments and recalls reviewed by the user |

Example crontab for the next phase (gx10, `CRON_TZ=America/New_York`):

```
CRON_TZ=America/New_York
0 0 * * *   cd ~/codeProjects/virtualTubers && docker compose run --rm character-jobs daily-maintenance
2 0 * * 0   cd ~/codeProjects/virtualTubers && docker compose run --rm character-jobs weekly-reset
5 0 * * 0   cd ~/codeProjects/virtualTubers && docker compose run --rm character-jobs story-start
# mafober (root): 30 0 * * *  bash /root/character-profile-db/scripts/backup.sh
```

## Changelog

- v4.0.0 (2026-09-25): v3 plus the r4 answers, handover §2/§4/§6 decisions,
  validated reference SQL, recall calibration, and the qwen build playbook.
