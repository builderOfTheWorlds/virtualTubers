# Handover: character generator + updater planning

> **DONE 2026-09-25.** The v4 plan is written. At the user's request it is in
> the docs dir, not `.claude/prompts/`:
> `docs/charcterProfileGenerationNotes/character_generator_updater_v4.md`,
> plus `character_v4_decisions.md`, `character_v4_build_playbook.md`, the two
> `v4_reference_*.sql` files, and the tracker
> `.claude/prompts/character_v4_build_status.md`.
> The §6 answers are folded in as decisions D-01 to D-26. Old copies are
> marked superseded. The next step is the build (playbook, starting at WP-00).

Written 2026-09-24 at the end of a Claude session (token budget ran out). It is
self-contained: a model with no memory of that session can finish the plan
from this file plus the files it names.

## 0. The job

Write `.claude/prompts/character_generator_updater_v4.md`, the one canonical
design plan: v3 plus every decision in §2, the fixes in §4, the outline in §5,
and the open items in §6. Then put this line at the top of each old copy listed
in §1: `> Superseded by .claude/prompts/character_generator_updater_v4.md`.

Rules:
- Planning only. Don't run, deploy, commit, or stop anything. Don't run
  `sourceworks/phase2_llm_windowing_ollama.py`.
- Don't edit the user's inline comments in review files. They are the decision
  trail (plain unindented lines under the reviewer's bullets).
- Cite `path:line` for every claim about code, and open the file first. §3 lists
  facts already verified on 2026-09-24. For anything else, grep; if you can't
  verify it, write "unverified". Don't invent functions, tables, or config keys.
- Items marked "user's call" in §6 go to the user as questions. Don't decide them.
- Match the existing plans' style: short sections, a decisions table, an
  "Open items" list, a numbered "Build order". Plain language.

Reading order: this file, then v3, v2 §2 and §4, `review_4.md`,
`deploy/character-profile-db/README.md`.

## 1. File map

| File | What it is |
|---|---|
| `docs/charcterProfileGenerationNotes/character_generator_updater_v3.md` | Newest design (2026-09-24 00:01). The base for v4. |
| `docs/charcterProfileGenerationNotes/character_generator_updater_v2.md` | §2 decisions table (all confirmed), §4 data model, §6 reset steps, user's answers to O1–O5. |
| `docs/charcterProfileGenerationNotes/review_4.md` | This round's answers (line numbers below cite it as r4:N). |
| `docs/charcterProfileGenerationNotes/review_2.md`, `review_3.md`, `character_generator_updater_review.md` | Earlier rounds. |
| `.claude/prompts/character_generator_updater.md` | STALE v1 model, edited 2026-09-24 00:22. Only D1 and D3 (lines ~492–505) carry forward. Mark superseded. |
| `docs/charcterProfileGenerationNotes/character_generator_updater.md`, `..._v2.md`, `..._v3.md` | Mark superseded once v4 exists. |
| `deploy/character-profile-db/` | The database deploy package built this session (§7). |
| `../projectManager/mafober_summary.md` | mafober host details and lessons learned. |

## 2. Decision ledger

### A. Loop model
- Weekly time loop. The week runs Sunday 00:00 to Sunday 00:00
  America/New_York and comes from the clock, not a counter. The day rolls over
  at 00:00 NY. (v2 §2)
- The baseline is permanent and known every week: identity, personality,
  objectives, and the generated backstory. It never drifts, so there is no
  profile_diff, no trait drift, no weekly profile versions, and no objective
  progress across weeks. (r4:7, r4:9, v2 §2)
- Knowledge learned during a week is archived at the reset: kept, never
  deleted, unreachable by the character. (r4:7, r4:12)
- Fragments are lossy, emotional, first-person gists, not transcripts. Exact
  memories would make the character retake the same steps. (r4:5)
  - Created at the weekly maintenance from the week's daily summaries.
  - Stored with their lead-up ("prefix") sequence, and hidden until recall
    matches that lead-up. (r4:12)
  - Immutable and permanent; they can chain.
  - Only for characters with `retains_fragments: true` (default false).
    `fragments_per_week` starts at 1. (v2 §2)
- Each night at 00:00 NY writes a daily summary per character. The weekly
  maintenance analyses the 7 summaries. (r4:15)
- Every week plays out differently. LLM output is stored for audit, not replay.
  Characters don't know they're in a loop; loop awareness is future work.
  (r4:28, v2 §2)

### B. Processes and scheduling
- Every operation is its own scriptable command that a scheduler can call, each
  with `--dry-run`: initialize, start, end/stop, daily maintenance, weekly
  reset, backup. (r4:35, r4:116, r4:129)
- Updater: the logic goes in `app/character/updater.py`, the process in
  `services/character-updater/`, and the scheduler calls
  `--once --character <slug> [--dry-run]`. (old plan D1)
- "Capture" is the experience ingest: a real-time Kafka→Postgres consumer
  process. (r4:22, r4:83)
- The knowledge graph builder is a separate process and out of scope. The
  generator only emits its data contract. (r4:25)
- The story and the character reset are scheduled separately. A programmed
  story ends gracefully at its end; otherwise a stop script ends it. The live
  Ashiorid campaign will be timed to end at Sunday 00:00. (r4:18, r4:64)
- A reset has to reach workers that are still running, in v1. Nothing restarts
  workers on a schedule today. (review finding, accepted at r4:35)
- The nightly maintenance compacts the bus log (the `messages` table) rather
  than wiping it, because the data is shown on the tuber screens. (r4:76, plus
  the user's reply to a follow-up question: "consider the nightly more like a
  compaction")
- Database backups are a scheduled job. (r4:47)
- An undo/revert command, for testing. (r4:125)

### C. Data and infrastructure
- Character DB: a new pgvector Postgres on mafober, deployed as a Portainer
  stack in CT 101 (192.168.1.120, host port 5433). The database is
  `character_profile`, owned by its own role `character_profile`. The
  package is already built (§7). (user's reply to a follow-up question; r4:43,
  r4:57; r4:57's "192.168.1.20" was resolved to mafober)
- `generator-postgres` (alpine, on gx10, 127.0.0.1:5455) stays for the 3-layer
  generator. Its backups to mafober come later. (r4:120)
- `character_jobs` and `character_artifacts` are standalone tables (old D3) in
  `character_profile`. (r4:132; confirm, §6)
- Bus: v3's new `character_say` and `scene_event` contracts, plus the existing
  `agent_thinking`. (r4:74)
- `messages` gains a character column and a timestamp index. (r4:80)
- Use DOUBLE PRECISION, never DOUBLE, in all DDL. (r4:88)
- Add indexes to the node and edge tables, and use `config/character.yaml`.
  (v2 §2)

### D. Characters, source, prompts
- Pilot cast: harry, ron, hermione. This replaces v3's top 6. (r4:61)
- Source: Harry Potter from `sourceworks/chapters_v2/`, not
  `chapters_deterministic/` (§3). Keep v3's two-layer backstory
  (`believed`/`truth`) and a per-campaign `start_pos`.
- The cast speaks in first person. (r4:110)
- Generator prompts include positive and negative node-name examples. (r4:112)

### E. Avatar
- Renderer: `codec_avatar`, which every worker runs. This replaces r4:95's
  "termgl" (user's reply to a follow-up question).
- v1 uses only the 8 shape sliders and `accent_color`. (r4:100)
- TODO: map appearance to sliders through `SLIDER_DEFAULTS` and
  `resolve_params`. (r4:93)
- Roundtable tiles must accept generated slider dicts. Done this session (§7).

### Answers the v4 should state (the user asked)
- r4:9 "what is profile_diff and trait drift?" v1 wrote a new profile version
  every week. `profile_diff` was the list of changes between versions, and
  trait drift meant personality trait strengths moving by up to ±0.15 a week.
  Both are removed, because the baseline resets fully.
- r4:83 "what capture is this?" It is the step that records what each
  character says, thinks, and witnesses into `experience_events`. In v4 that
  is the ingest consumer (Kafka `vtuber.messages` → `character_profile`), and
  it has to be built (build order step 3).

## 3. Verified facts (2026-09-24)

Bus
- There is one Kafka topic, `vtuber.messages`. `build_message()` at
  `app/message_bus.py:33` sets `id` (uuid4) and a UTC ISO `timestamp` in the
  message body. Dedupe on `id`, and derive `loop_day`/`loop_week` from that
  timestamp.
- The shared `MessageConsumer` reads from the latest offset and auto-commits,
  which is wrong for ingest (v3 §3). The ingest consumer needs the earliest
  offset and a manual commit after the DB insert.
- `agent_thinking` is published (`app/agent_metrics.py:259`). Nothing in
  `app/campaign/` publishes to the bus. `character_say` and `scene_event`
  don't exist yet.
- Kafka retention is the default 168 h, exactly one loop week. v3 O2 says to
  raise it to at least 14 days.
- Message handlers are in `app/agent.py:1075` (`MESSAGE_HANDLERS`). The system
  prompt comes from the worker YAML. There is no brief loader and no
  reset/refresh message.

`messages` table (virtualtubers DB, 192.168.1.120:5432, per
`docs/architecture_flow_diagram.md:21`)
- Schema at `docs/sql/02_create_tables.sql:14-23`: `id` UUID PK, `"from"`,
  `"to"`, `type`, `payload` JSONB, `timestamp` TIMESTAMPTZ, `ingested_at`. The
  only indexes are on `"to"` and `type`.
- Three schema copies must stay in sync: `services/message-logger/logger.py`
  (runs CREATE TABLE at startup), `docs/sql/02_create_tables.sql`, and
  `docs/message_logger.md`.
- Only message-logger writes it: `INSERT ... ON CONFLICT (id) DO NOTHING` at
  `logger.py:82-86`, and it drops any type an operator excludes (`:183`).
- No code reads `FROM messages`, and nothing prunes it. `app/log_prune.py` and
  `services/log-shipper` prune only `container_logs`.
- `docker-compose.yml:820` also defines a `postgres` service. It wasn't
  inspected, so check it before saying where `messages` lives in each
  environment.

Workers and campaign
- `WorkerControl` (`app/worker_control.py:27`) is a per-worker on/off flag in
  Redis. message-api exposes enable/disable at
  `services/message-api/api.py:131,136`. Start and stop jobs can use it.
- `CampaignRuntime.reset()` (`app/campaign/runtime.py:151`) is called only by
  tests. `run()` stops when `state.finished` is set or the scene budget runs
  out.
- `app/campaign/cli.py` has `--pack`, `--scene`, `--dry-run`, `--state-file`,
  `--resume`, `--max-scenes` and a few more flags. There is no stop command.
- The repo has no scheduler. `redeploy.sh` restarts workers only on a deploy.

Avatar
- `app/character_schema.py`:
  - `ACCENT_COLORS` (`:33`): BLACK RED GREEN YELLOW BLUE PURPLE CYAN WHITE.
  - `SLIDER_DEFAULTS` (`:38`): head_width, head_taper, eye_size, eye_spacing,
    jaw_width, nose_length, ear_size, build, each 0..1.
  - `resolve_params(raw, strict)` (`:290`) takes a preset name, a slider dict,
    or `{"preset": name, ...overrides}`.
  - There is no `SLIDERS`.
- Channels read `avatar.codec_avatar.character_params`
  (`config/workers/coder.yaml:106`). No worker uses `termgl_avatar`. The head
  can't show hair, eye colour, glasses, or scars.

Source text
- `sourceworks/chapters_v2/` is correct: 199/199 chapters plus
  `manifest.json`, from `utilities/source_pipeline/` (18 tests).
- `chapters_deterministic/` (174 files) and `chapters_llm_restored/` come from
  the old, broken split. `chapters_deterministic/1_001` has 1,001,970 words,
  which is the whole series.
- Phase 1 also has a contraction bug: "rather ill but happy" became "rather
  I'll but happy". Never restore these from a dictionary: well, ill, id, lets,
  hell, wed, shed, shell, its, were (v3 §1).
- Ron and Hermione first appear in chapter `1_006`. Harry is an infant in
  `1_001`.

Infrastructure
- mafober is the Proxmox host at 192.168.1.117. CT 101 is a privileged LXC
  running Portainer (https://192.168.1.120:9443).
- New storage on mafober follows `mafober_summary.md` lesson 8: ZFS dataset,
  then chown, then `pct set 101 -mpN`, then a CT reboot.
- Probed from the dev PC: 192.168.1.120:5432 answers, but 5433 and other
  unused ports time out. A firewall rule may be needed.
- `postgres:16-alpine` has no pgvector. Never reuse an alpine data directory
  under a Debian image, because text collations differ.
- `pgvector/pgvector:0.8.6-pg16-bookworm` exists for amd64 and arm64.

## 4. Fixes v4 must contain

1. DDL:
   - Use DOUBLE PRECISION.
   - Index `(character_id, archived_at_week)` and `(character_id, loop_week)`
     on the week knowledge nodes and edges (v2 §4).
2. Clock-driven, idempotent reset:
   - `loop_weeks(week, starts_at, ends_at, status, reset_steps JSONB)`.
   - Re-running a completed step does nothing (v2 §6).
3. Ingest consumer:
   - Its own consumer: earliest offset, manual commit after the insert.
   - Allowlist `[agent_thinking, character_say, scene_event]`.
   - `character_agents` maps agent ids to characters.
   - A scene's `present[]` decides which characters experience it.
   - `agent_thinking` is visible only to its author.
4. Reset reaches running workers: after the reset commits, the weekly job
   restarts the character workers or pushes a refresh (§6, Q2).
5. Source:
   - Re-run phase 1 on `chapters_v2/` into a new directory, with the
     contraction fix.
   - Fix phase 2 before any long run (§8).
6. Brief contents:
   - The baseline, including the `believed` backstory.
   - The current week's knowledge.
   - The behaviour contract.
   - No fragment list. A recalled fragment is injected as
     "a feeling surfaces: <gist>".
7. Prompts: first person, with node-name examples.
   - Bad: `godsley-shelter-abuse` and `wingardsium-levia` (misspelled),
     `knows-qui-llusions` (garbled), `pass-third-year-exams` (wrong age for an
     11-year-old).
   - Good, in the spirit of: `knows-wingardium-leviosa`,
     `lives-in-cupboard-under-stairs`, `wants-to-win-house-cup`.
8. Avatar: 8 sliders plus `accent_color` only. The cast export writes inline
   slider dicts. The appearance mapping stays a TODO.
9. Revert: moving the baseline version pointer (v2 §4), plus a test-only
   reset undo (§6, Q10).
10. `messages`: add a `character` column and a `timestamp` index with
    `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, in all 3 schema copies. Add
    the nightly compaction.

## 5. v4 outline

Keep these section numbers. `deploy/character-profile-db/README.md` and its
`docker-compose.yml` already cite v4 §5 (backups) and §6 (database).

0. What changed from v3 (table)
1. Source pipeline: v3 §1, plus fix 5
2. The model in one paragraph: §2A
3. Bus and ingestion: v3 §3, plus fixes 3 and 10
4. Characters and backstory: v3 §5, the trio pilot, `start_pos` (Q7)
5. Scheduled jobs and maintenance. One table: job | when (NY) | command |
   idempotency key | notes.
   - `ingest`: long-running; the compose restart policy keeps it up.
   - `daily-maintenance`, 00:00 daily, in this order:
     1. Daily summaries for the previous day.
     2. `messages` compaction, only once ingest has caught up.
     3. Backup: `pg_dump -Fc character_profile` plus a ZFS snapshot, with
        retention, and a restore drill before the pilot.
   - `weekly-reset`, Sunday 00:00, after `daily-maintenance`:
     1. Close Saturday.
     2. Select fragments.
     3. Archive the week.
     4. Open week W+1.
     5. Refresh or restart the workers.
   - `initialize`: load the source, generate baselines, export the cast YAML.
   - `story-start` / `story-stop`: WorkerControl enable/disable plus the
     campaign. Stop ends the story gracefully.
   - `revert`: fix 9.
6. Database: `character_profile` on mafober (§7). Tables from v2 §4 and
   v3 §4, plus `character_jobs`/`character_artifacts`. Roles (Q5) and
   indexes.
7. Recall: v3 §7, unchanged.
8. Brief, and reaching running workers: fixes 4 and 6.
9. Avatar: §2E, fix 8.
10. Generator prompts: fix 7.
11. `config/character.yaml`: merge v2 §8 and v3 §8. `db` points at
    192.168.1.120:5433 / `character_profile`.
12. Open items: §6.
13. Build order:
    0. The user deploys the DB, sets up backups, and runs a restore drill.
    1. Fix source phases 1 and 2; audit.
    2. Schema, `store.py`, revert, tests.
    3. Bus contracts, ingest consumer, `messages` columns.
    4. Pipeline stages 2–6 for the trio; cast export.
    5. Daily summariser and the `daily-maintenance` job.
    6. Weekly reset, worker refresh, undo.
    7. Scheduler wiring, start/stop scripts.
    8. Recall harness, then wire recall into workers.
    9. Pilot for 2–3 real weeks.

## 6. Open questions (user's call)

1. Scheduler: which one?
   - Host cron on gx10 (`CRON_TZ=America/New_York`) calling
     `docker compose run --rm` one-shots.
   - A scheduler container.
   - systemd timers.
   we will work on the scheduler in the next phase
2. Worker refresh at the reset:
   - Restarting is simplest, but it interrupts live streams. Is that fine
     given the story ends at Sunday 00:00?
   - Or a push-refresh bus message instead.setup push refresh 
3. `messages` compaction: which approach?
   - (a) Delete noisy types (`status_update` heartbeats) after N hours, and
     keep conversation types for N days.
   - (b) Move rows older than N days to `messages_archive`.
   - (c) Daily partitions. This needs PK `(id, timestamp)` and a change to
     `logger.py`'s `ON CONFLICT (id)`.
     Do A, B, and C

   Also: how many days do the tuber screens need?
   They will only be able to display a few minutes of messages in the stream, they would need memories of whats done for the entire week that add up until reset, 
4. What fills `messages.character`? For example, `payload.speaker` on
   `character_say`, or an agent→character map. That map lives in a different
   database (`character_profile`).
   I'm not sure, we need to research this
5. Roles: keep one role, `character_profile` (as built), or use v3's two
   (`character_ingest`, `character_service`)? Add a read-only role for
   displays?
   I'm not sure lets elaborate ont he options for this
6. Also run a local test instance on gx10? r4:51 and r4:120 ask for "local"
   databases. The deploy compose runs on any Docker host when
   `CHARACTER_DB_DATA_DIR` is set.
  sure we can do that
7. Trio pilot `start_pos`: book 1 chapter 1 (v3), chapter 2 (playable Harry),
   or chapter 6 (where Ron and Hermione first appear)?
   start chapter 1 and continue through the first book
8. r4:7 says fragments are "added to the backstory" next week, and r4:12 says
   they are unlocked by their intro sequence. Confirm that fragments stay
   hidden until recall (the v3 model).
   Lets consider that the memory can occur and be preserved from week n in an inactive state, then if in week n+1 the leadup sequence is met and the knowledge unlocks, that knowledge is now permanently unlocked, similarly if the leadup sequence is not matched until n+m it will stay in its inactive state until weke n+m when it becomes opermanently unlocked. 
9. Confirm `character_jobs`/`character_artifacts` go in `character_profile`. 
OK lets have it in this character_p[rofile db]
10. Undo for fragments: allow a test-only command, even though fragments are
    immutable?
    Yes allow for test commands, we need a full set of manipulation controls, we wont plan to use them regularly
11. The external Kafka broker's retention: raise it to at least 14 days?
  Ok if we have the space for it we can do that
12. Backup destination and retention?
Needs to be on mafober tank_0

## 7. Done this session (all uncommitted)

- `deploy/character-profile-db/`:
  - `docker-compose.yml`: pgvector 0.8.6-pg16-bookworm, plus a one-shot
    `init` service that creates the role, the database and `vector`.
  - `.env.example` and `README.md`.
  - `scripts/install.sh`: ZFS dataset, chown 999:999, `pct set` mount;
    `--dry-run`, `--reboot-ct`, `--verify`.
  - `scripts/uninstall.sh`: keeps the data unless `--purge-data` is given and
    the dataset name is typed to confirm.
  - `tests/run_tests.sh` with mocked `pct`/`zfs`; 51/51 pass.
  - `tests/test_character_profile_db_scripts.py`: pytest wrapper, passes.
  - `docker compose config` validates, and a missing password is rejected.
  - Not deployed or started: the user deploys it.
- Roundtable tiles take inline slider dicts: `app/tile_avatar.py`
  `resolve_slot_character_params`, with tests in `tests/test_tile_avatar.py`
  and `tests/test_roundtable_layout.py`, and a comment in
  `config/workers/roundtable.yaml`. The touched test files pass (88).
- Full suite: 2551 passed, 42 failed, 36 errors. The failures are in files this
  session didn't touch: `tests/test_control_panel.py` can't import fastapi in
  the root `.venv`, and `services/3layer-generator/tests/test_service_runner.py`
  errors at setup. Nobody has checked whether they also fail on a clean tree.
- Not done yet:
  - A CHANGELOG.md entry.
  - `docs/project_structure.md` (add `deploy/`).
  - A root README link to `deploy/character-profile-db/README.md`.

## 8. Source cleaning phase 2: stop, then fix

- Two runs of `sourceworks/phase2_llm_windowing_ollama.py` were still going at
  about 10:50 on 2026-09-24:
  - Python PIDs 688/50688, writing `phase2_run_v3.log`.
  - Python PIDs 50728/20804, writing `phase2_run_v2.log`.

  Both work on chapter 1 against the same Ollama (192.168.1.23:11434,
  llama3.1:8b) and write to the same output directory, so they time each other
  out.
- Their output is unusable:
  - The input is the broken `chapters_deterministic/1_001` (1M words).
  - The saved results show 1,105 of 1,114 windows timed out.
  - `reconstruct_from_windows` (`:352`) joins overlapping windows with a
    space, so every 100-word overlap is duplicated: 88,907 repeated 20-word
    sequences in the output, against 97 in the input.
- Fixes:
  - Read the phase-1 output of `chapters_v2/`.
  - Drop each window's read-only overlap before joining.
  - Run one job at a time, with a 300–600 s timeout.
  - A timeout keeps the deterministic text.
  - Before scaling up, audit the output: word count and the repeated-20-gram
    count, compared with the input.
- `.claude/prompts/INDEX.md` still names this run as the next action and says
  174 chapters. Update it.
