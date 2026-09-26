# Character v4: Decision Log

The companion to `character_generator_updater_v4.md`. It records every choice
the plan makes: where it came from, why it was made, and how to reverse it.

Rules for this file:

- A decision is changed by adding a new entry, e.g. `D-07b`, that supersedes
  the old one. Don't edit the old entry: it is the trail.
- Every entry names its **change seam**: the config key, or the one function
  or file that holds it. Reversing the decision should touch that seam and its
  tests, and nothing else. If a change would need more than that, the seam is
  wrong. Fix the seam first, then log it.
- The source of each decision is either the user's words, a handover or
  review line, or "gap: chosen by the planner". Planner choices are the ones
  most worth a second look.

Status values: **user** (the user decided), **confirmed** (carried from
earlier rounds), **planner** (a gap the planner filled; can be changed
freely).

| ID | Decision | Status |
|---|---|---|
| D-01 | Loop clock: an epoch Sunday in config, NY time | confirmed + planner |
| D-02 | Reset reaches workers by a push refresh plus a brief TTL | user |
| D-03 | `messages` compaction does A, B and C | user |
| D-04 | Filling `messages.character`; agent ids `char:<slug>` | planner |
| D-05 | DB roles: owner plus read-only `character_reader` | planner |
| D-06 | Test databases: pgserver, optional gx10 instance | user + planner |
| D-07 | Story book 1 ch 1–17; baselines at each entry chapter | user + planner |
| D-08 | Fragment lifecycle: dormant, then unlocked for good | user |
| D-09 | Fragments only for `retains_fragments` characters; harry first | confirmed |
| D-10 | Test manipulation controls (`testctl`) | user |
| D-11 | Kafka retention 14 days if disk allows; backfill fallback | user + planner |
| D-12 | Backups on mafober `tank_0`, a separate host job | user + planner |
| D-13 | One CLI for every job; the scheduler waits for the next phase | user + planner |
| D-14 | `character_jobs` / `character_artifacts` in `character_profile` | user |
| D-15 | LLM profiles; qwen3.8:27b with thinking off | planner |
| D-16 | Pilot source scope is book 1 only | planner |
| D-17 | New stage-1 cleaner; the old phase 1/2 scripts are frozen | planner |
| D-18 | Recall runs in the live driver, as a pure module | planner |
| D-19 | New live driver; `app/campaign/` is not modified | planner |
| D-20 | No embeddings at ingest | planner |
| D-21 | Daily summaries become the week's knowledge nodes | planner |
| D-22 | Build by qwen3.8:27b with fixed test lists written first | user + planner |
| D-23 | The new code lives in `app/character/`, imported as `character.*` | planner |
| D-24 | Timezones: store UTC, compute loop time in NY, partitions in UTC | planner |
| D-25 | Schema migrations are numbered SQL files, never edited | planner |
| D-26 | Dependencies: add only `tzdata` and dev-only `pgserver`; no jsonschema | planner |

---

## D-01 Loop clock

- **Decision:** week N starts at 00:00 America/New_York on
  `epoch + 7·(N−1)` days, where `loop.epoch` is a Sunday in config. `loop_day`
  is the NY calendar date. `app/character/clock.py` is the only code that turns
  a timestamp into a week or day.
- **Source:** v2 §2 (Sunday to Sunday NY, clock-driven). The epoch is a planner
  choice.
- **Why:** a counter drifts when a job is missed. A clock can't. DST weeks
  come out 167 h and 169 h, and are validated (`character_v4_plan_validation.py`
  "clock" checks).
- **Alternatives:** a week counter in the DB (rejected: it drifts); ISO weeks
  (rejected: they start on Monday).
- **Change seam:** `character.loop.epoch` and `character.timezone` in
  `config/character.yaml`, and `clock.py`. Changing the epoch on a live
  campaign renumbers weeks, so it needs a one-off `UPDATE` of the `loop_week`
  columns (write that as a numbered migration).

## D-02 Reset reaches running workers by push refresh

- **Decision:** after `weekly-reset` commits, it publishes
  `character_refresh` on `vtuber.messages`. The live driver drops its brief
  cache and per-week state when it sees one. As a safety net, the brief cache
  has a 300 s TTL.
- **Source:** user, handover §6 Q2: "setup push refresh".
- **Why:** restarting interrupts streams. The TTL covers a missed message.
- **Alternatives:** restart containers (simplest, but it interrupts streams);
  TTL only (up to 5 minutes stale).
- **Change seam:** `character.reset.refresh_mode` (`push|none`). The TTL is
  `brief.cache_ttl_s`. For a restart mode later, add a value and one branch in
  `weekly_reset.step_refresh()`.

## D-03 `messages` compaction does A, B and C

- **Decision:**
  - A: delete `compaction.noisy_types` older than `noisy_keep_hours`.
  - B: move rows older than `hot_keep_days` into `messages_archive`.
  - C: daily UTC partitions, so B becomes DETACH/ATTACH.
  - C is an operator migration (M2). Compaction handles both table shapes,
    so A and B work before M2 runs.
- **Source:** user, handover §6 Q3: "Do A, B, and C". The screens only show
  minutes; weekly memories come from `character_profile` (O-C).
- **Why:** the archive is kept, never wiped (the data is shown on screens,
  per the handover §2B). Partitions make the nightly move O(1).
- **Pitfall (validated):** once DEFAULT holds rows for a day, you can't create
  that day's partition. So partitions are created ahead (14 days), and old
  DEFAULT rows are moved in batches.
- **Change seam:** the `character.compaction.*` keys; `app/character/compaction.py`
  (`detect_shape`, `compact_noisy`, `compact_archive`). Rolling back C: leave
  it partitioned. The code already handles either shape.

## D-04 Filling `messages.character`; character agent ids

- **Decision:** `app/bus_attribution.py:character_for_message(msg)` returns
  `payload.character` if it is a string, else the slug from a `from` of
  `char:<slug>`, else None. It is pure: no DB lookup, so the logger doesn't
  depend on `character_profile`. Character agents publish as `char:<slug>`.
  `character_agents` maps ids to characters for ingest.
- **Source:** the user asked for research (handover §6 Q4). Planner.
- **Why:** the map lives in another database. Putting the character into the
  message itself (or its sender id) makes the column self-contained and cheap.
  `agent_thinking` doesn't carry `payload.character`, so the `char:` prefix
  covers it.
- **Alternatives:** the logger queries `character_agents` (rejected: a
  cross-DB dependency on the hot path); fill it later in a batch job (rejected:
  the rows go stale).
- **Change seam:** `character_for_message()`. The only callers are
  `services/message-logger/logger.py` and the ingest router.

## D-05 DB roles

- **Decision:** option 3. `character_profile` owns everything (as built), plus
  `character_reader` (SELECT only, with default privileges covering future
  tables) for the screens and GUIs.
- **Source:** the user asked for the options (handover §6 Q5). The table is
  in plan §6. Planner.
- **Why:** it keeps the built deploy package, and read-only displays can't
  write.
- **Change seam:** a new numbered migration plus a DSN env per process
  (`CHARACTER_INGEST_DB_*` is already read by config and defaults to the main
  DSN). To move to v3's two-role split, add the roles and grants in `00N_roles.sql`.

## D-06 Test databases

- **Decision:**
  - Unit tests use fakes.
  - Integration tests use `CHARACTER_TEST_DSN` if it is set, else the `pgserver`
    pip package (embedded PG16 with pgvector), else they skip.
  - An optional gx10 instance uses the deploy compose with `-p character-profile-test`
    on port 5434.
- **Source:** user, handover §6 Q6: "sure we can do that". pgserver is a
  planner choice.
- **Verified 2026-09-25:** pgserver 0.1.4 publishes **no** Linux aarch64
  wheel (only x86_64, macOS and win_amd64), so on gx10 the integration tests
  need `CHARACTER_TEST_DSN` pointed at the gx10 test instance. The dev PC
  (Windows x86_64) uses pgserver.
- **Change seam:** the `pg_dsn` fixture in `tests/character/conftest.py`.

## D-07 Story range and baseline positions

- **Decision:** the story runs from book 1 ch 1 to ch 17. Each character's
  baseline is their state just before their **entry chapter** (the first
  chapter from `story_start` with at least 3 attributed lines). Expected:
  Harry ch 2, Ron and Hermione ch 6. `entry_pos` can be overridden per
  character.
- **Source:** user, handover §6 Q7: "start chapter 1 and continue through the
  first book". Entry-chapter baselines are a planner choice (open item O-B).
- **Why:** at ch 1 Harry is an infant, and Ron and Hermione aren't in it. A ch 1
  baseline would give a baby and two blank characters.
- **Change seam:** `character.characters.<slug>.entry_pos`, and
  `cast_rank.entry_chapter()` with its `min_lines` argument.

## D-08 Fragment lifecycle: dormant, then permanently unlocked

- **Decision:**
  - A fragment made in week N is **dormant**. It is never in a brief, and it
    is a recall candidate.
  - In the first later week where its lead-up is matched (a `surface` level
    and a judge yes), it **unlocks**: a `fragment_unlocks` row, insert-only.
  - From then on it is in every brief under "Feelings you carry", framed as a
    feeling, and it is no longer a recall candidate.
  - It can stay dormant for any number of weeks.
- **Source:** user, handover §6 Q8 (quoted in plan §0).
- **Change seam:** `fragments.is_unlocked()` and `brief.section_feelings()`.
  The unlock table is separate from `memory_fragments`, so the lifecycle can
  change without touching the fragment rows.

## D-09 Who retains fragments

- **Decision:** only characters with `retains_fragments: true`, with
  `fragments_per_week: 1`. In the pilot that is harry only.
- **Source:** v2 §2 (confirmed).
- **Change seam:** `character.characters.<slug>.retains_fragments` and
  `character.loop.fragments_per_week`.

## D-10 Test manipulation controls

- **Decision:** `main.py testctl <cmd>`, covering `reset-undo`,
  `fragment add|delete|unlock|lock`, `week wipe`, `seed`, `clock`, and
  `refresh`. Deleting needs `--confirm <dbname>`. Fragment edits use the
  per-transaction trigger bypass. The whole group is off when
  `testctl.enabled: false`.
- **Source:** user, handover §6 Q10: "Yes allow for test commands, we need a
  full set of manipulation controls".
- **Change seam:** `app/character/testctl.py`. Each command is one function,
  and the CLI only dispatches to them.

## D-11 Kafka retention

- **Decision:** raise `vtuber.messages` retention to 14 days if the broker disk
  allows (operator step OP-3). Either way, ingest has
  `--backfill-from-messages --since <ts>`, reading the `messages` table
  (the archive included).
- **Source:** user, handover §6 Q11: "Ok if we have the space for it".
- **Change seam:** broker config. Backfill is one function in `ingest.py`.

## D-12 Backups

- **Decision:** a nightly `pg_dump -Fc` plus a ZFS snapshot on mafober
  `tank_0`, by `deploy/character-profile-db/scripts/backup.sh`, run from root's
  crontab on mafober at 00:30 NY. Retention is 14 daily dumps, 8 weekly dumps
  and 14 snapshots. It includes `--restore-drill`.
- **Source:** user, handover §6 Q12: "Needs to be on mafober tank_0". Moving it
  out of daily-maintenance is a planner choice (open item O-A): ZFS needs
  Proxmox root, and gx10 shouldn't hold root SSH to mafober.
- **Change seam:** `backup.sh` and its cron line. daily-maintenance doesn't
  know about backups.

## D-13 One CLI; the scheduler comes later

- **Decision:** `services/character-updater/main.py <job>`. The jobs are:
  - `ingest`, `daily-maintenance`, `weekly-reset`
  - `initialize`, `story-start`, `story-stop`
  - `revert`, `testctl`, `status`

  Every job has `--dry-run` and `--at`, runs under an advisory lock, and uses
  exit codes 0/1/2/3. Plan §13 has an example crontab, but nothing installs it.
- **Source:** r4:35, r4:116, r4:129. User on scheduler choice: "we will work on
  the scheduler in the next phase".
- **Change seam:** any scheduler calls the CLI. Swapping schedulers doesn't
  touch the code.

## D-14 Job tables' home

- **Decision:** `character_jobs` and `character_artifacts` go in
  `character_profile`.
- **Source:** user, handover §6 Q9.

## D-15 LLM profiles

- **Decision:** there are five named profiles: `summary`, `fragment`,
  `generator`, `judge` and `clean`. Each sets its own model, temperature,
  num_ctx, timeout and `think`.
  - Every profile but `clean` uses qwen3.8:27b, with thinking **off**.
  - `clean` starts on llama3.1:8b and is calibrated in WP-07.
  - `app/character/llm.py` calls Ollama `/api/chat` with `format: json` and
    validates the output against a schema, retrying up to `max_retries` times.
- **Source:** planner.
- **Why:** `tools/qwen_worker/ollama_client.py:46-53` records that qwen3
  reasoning eats the whole `num_predict` budget and returns empty content.
- **Change seam:** the `character.llm.profiles.*` keys. Only `llm.py` knows
  about Ollama. For vLLM later, swap in a second client class selected by a
  `provider` key.

## D-16 Pilot scope is book 1

- **Decision:** `source.scope.books: [1]`. Stages 1–6 run on book 1 only.
- **Source:** planner. The trio pilot plays book 1 (D-07), and book 1 is 17
  chapters and 77,597 words (measured), against 199 chapters for the whole
  series.
- **Cost:** the GM `truth` layer has no knowledge from later books.
- **Change seam:** `character.source.scope.books`. Widening it re-runs the
  stages. They are keyed by sha256, so already-done chapters are skipped.

## D-17 New stage-1 cleaner; old scripts frozen

- **Decision:** write a new `utilities/source_pipeline/src/cleaner.py` with
  the fixes from plan §1.1:
  - core-span windows
  - the exact token-sequence guard
  - a reduced contraction table
  - one run at a time
  - a per-chapter audit

  `sourceworks/phase1_deterministic.py` and `phase2_llm_windowing_ollama.py`
  stay as they are, as legacy. `sourceworks/chapters_deterministic/` and
  `chapters_llm_restored/` are not inputs to anything.
- **Source:** handover §8. The choice to make a new module is a planner
  choice: the old scripts hardcode paths, and the old output is contaminated.
- **Change seam:** `utilities/source_pipeline/src/cleaner.py`.

## D-18 Where recall runs

- **Decision:** recall runs inside the live driver (`character-live`).
  `app/character/recall.py` is pure: it takes an injected `embed()` and
  fragments, and holds activation state in an object.
- **Source:** planner. The driver already sees every beat, so there is no
  extra bus hop and no extra process.
- **Change seam:** move `RecallEngine` behind a bus consumer. `recall.py` itself
  doesn't change.

## D-19 New live driver; `app/campaign/` untouched

- **Decision:** `app/character/live.py` drives the campaign:
  - `CampaignRuntime`
  - one `LLMImproviser` per cast member, fed the brief
  - `ObservingRenderer`, a subclass that publishes to the bus and feeds recall

  It is a separate container, `character-live`.
- **Source:** planner. `app/campaign/cli.py:62-67` builds no improviser, and
  keeping `app/campaign/` unchanged keeps the existing 10 campaign test files
  valid.
- **Change seam:** `live.py`. If the campaign CLI gains an improviser later,
  the driver can call it instead.

## D-20 No embeddings at ingest

- **Decision:** ingest stores the text only. Embeddings are computed:
  - at fragment creation (the lead-up beats and the gist)
  - in the live driver, per beat, for recall
- **Source:** planner. Ingest has to keep up while Ollama is busy.
- **Change seam:** `experience_events.embedding` exists (nullable), so a
  back-fill job can add them later.

## D-21 Daily summaries become week knowledge

- **Decision:** each night the `summary` profile turns a character's day of
  `experience_events` into one `daily_summaries` row (a first-person paragraph)
  plus up to 10 `week_knowledge_nodes` (with names checked by
  `node_names.py`) and edges between them. The brief's "What you've learned
  this week" section reads those nodes. The knowledge-graph builder (r4:25) is
  still out of scope; these nodes are its input contract.
- **Source:** planner. It fills the gap between r4:15 (nightly summaries) and
  v2 §4 (week knowledge nodes).
- **Change seam:** `summaries.summarise_day()` and the `summary_day.md`
  prompt template.

## D-22 How the build runs

- **Decision:** qwen3.8:27b builds everything, work package by work package
  (see `character_v4_build_playbook.md`):
  - Each package has a fixed test list. The tests are written first (by qwen
    from the list, reviewed against it) and frozen.
  - Then the code is written until they pass, through `tools/qwen_worker`
    (`runner.py run`, whole files, sandboxed pytest, feedback retries).
  - The orchestrator reviews and promotes, following
    `docs/campaign_module_status.md:69-73`: a behaviour gap becomes a test plus
    a spec note, then a regeneration. Hygiene fixes are done by hand.
- **Source:** user ("all building will be delegated to a local model"). The
  shape is a planner choice.
- **Change seam:** the playbook. The harness itself only gets the two
  preflight and Windows fixes in WP-00.

## D-23 Package location

- **Decision:** the new code lives under `app/character/`, and tests import it
  as `character.*`, because `tests/conftest.py:5` puts `app/` on `sys.path`.
  This matches `from campaign.pack import …` (`app/campaign/improviser.py:6`).
  Tests go in `tests/character/`. `services/character-updater/main.py` does its
  own `sys.path` insert of `app/`.
- **Source:** planner. It follows the house convention.
- **Pitfall:** `app/character_schema.py` already exists. A new
  `app/character/` package doesn't clash with that module name, but never
  create `app/character.py`.

## D-24 Timezones

- **Decision:**
  - Every stored timestamp is TIMESTAMPTZ in UTC.
  - Loop week and loop day are computed in NY by `clock.py`.
  - `messages` partitions are UTC days, because the logger writes UTC ISO
    (`app/message_bus.py:40`).
  - Compaction cutoffs are in UTC.
- **Source:** planner.
- **Change seam:** `clock.py` and `compaction.partition_name()`.

## D-25 Migrations

- **Decision:** the build copies `v4_reference_character_profile.sql` to
  `app/character/sql/001_init.sql`. `db.migrate()` applies the numbered files
  in order and records them in `schema_migrations(version, applied_at, sha256)`.
  It refuses to run if an applied file's sha256 has changed. Later changes are
  new files, `002_*.sql` and up.
- **Source:** planner.
- **Change seam:** `db.migrate()`.

## D-26 Dependencies

- **Decision:** add only these two:
  - `tzdata==2026.4` to `requirements.txt`. Without it,
    `ZoneInfo("America/New_York")` raises in the project `.venv` on Windows
    (verified 2026-09-25).
  - `pgserver==0.1.4; platform_machine != "aarch64"` to a new
    `requirements-dev.txt`. It is test only.

  `psycopg2-binary`, `kafka-python`, `pyyaml` and `httpx` are already in
  `requirements.txt`. numpy is installed in `.venv` (2.4.6) but isn't listed,
  so the character code doesn't use it: recall's cosine and Smith-Waterman
  work on about 8×12 cells and use stdlib `math`. LLM JSON output is checked
  by a small stdlib validator, `app/character/shapes.py`, not by jsonschema,
  which isn't installed.
- **Source:** planner (CLAUDE.md: pin versions, choose dependencies with care).
- **Change seam:** `shapes.py`. To use jsonschema later, swap
  `shapes.validate()`; the callers don't change.
