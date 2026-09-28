# ashiorid_office — Technical Build Plan (for subagents)

Version: v1.0, 2026-09-27
Design doc: `.claude/prompts/office_campaign_plan.md` (v0.3). Read it first; it holds the roster,
the clock, the lore direction, and the user's decisions.
Status: plan only. No work package has started.

---

## 0. Decisions this plan relies on

User decisions (from office_campaign_plan.md §8/§9):

| # | Decision |
|---|---|
| U1 | The pack is `campaigns/ashiorid_office/`. The company Ashiorid is one part of the wider Ashiorid world (no simulation-layer twist for now). |
| U2 | The product is **Fraud-Stop**, an enterprise SaaS that banks send transactions through to get a fraud verdict. |
| U3 | Every day is a work day: 7/7, 06:00–00:00 work and 00:00–06:00 off. The weekly loop resets Sunday 00:00 America/New_York. |
| U4 | The corpus v1 collects Hermes and Claude Code conversations from THIS machine only. Other machines come later. |
| U5 | All characters are live agents. They write real code and push it to **Gitea** (not GitHub): `http://192.168.1.120:3300`, owner `gitea_admin`, repo `fraud-stop`, SSH remote `ssh://git@192.168.1.120:2222/gitea_admin/fraud-stop.git`. |
| U6 | All 8 characters have `retains_fragments: true`. The Party Member watches silently and never speaks. |
| U7 | Backstories are written first. Avatars are derived from the backstories. |

Engineering decisions made by the planner. Flag any of these to the user if a work package runs
into trouble with them:

| # | Decision | Why |
|---|---|---|
| E1 | The existing 8 worker containers are reused. Office mode = `docker-compose.office.yml` override + `config/workers/office/*.yaml`. The dev-team show configs are left untouched. | No new infra. Switching back to the old show is one compose flag. |
| E2 | Each worker config gets `agent.role` (the handler family) and a new `agent.office_role` (the persona and lane). Mapping: Tech Lead → `role: manager` (reuses the bug_report / test_passed / task_complete handlers). Engineer → `role: coder` (reuses the coding backend path). Tester → `role: tester`. CEO, Analyst, Marketing, Office Manager and Observer get the new roles `ceo`, `analyst`, `marketing`, `office_manager` and `observer`. | Reuses the proven manager → coder → tester loop instead of rewriting it. |
| E3 | "Everyone writes real code" means every role commits real artifacts to the Fraud-Stop repo, each within its own **lane**. The lanes are listed in the table after E6. | Every agent has real work, no two agents edit the same path, and it fits their jobs. |
| E4 | The corpus utility is a sibling repo, `C:/Users/matt/PycharmProjects/sessionCorpus`. v1 stores to SQLite (`data/corpus.db`) and exports JSONL. Postgres push is a later work package. | U4 scope is local-only. SQLite needs no infrastructure. |
| E5 | Models are local Ollama on gx10 (`http://192.168.1.23:11434`). The final allocation is set by the OB-07 benchmark. | Local-first stack. Hosted models would bill per token, 24/7. |
| E6 | The live-agent brief starts as a stub: cast `system_prompt` + `believed` backstory + today's directive. It is replaced by the v4 `brief.py` once WS-F lands. | Live agents don't block on the memory DB. |

Fraud-Stop repo lanes (E3):

| Role | Lane in the Fraud-Stop repo | Gitea action |
|---|---|---|
| CEO | GitHub issues (the directives) | opens and closes issues |
| Analyst | `docs/requirements/` | PR |
| Tech Lead | `docs/design/`, PR review and merge | reviews, merges |
| Engineer | `src/` | PR per task |
| Tester | `tests/` | PR, CI result comments |
| Marketing | `marketing/` (landing copy, release notes) | PR |
| Office Manager | `CHANGELOG.md`, dependency bumps, stale-branch cleanup ("garbage collection") | PR, branch deletes |
| Party Member | no lane | read-only token, never writes |

---

## 1. Rules for every subagent

Paste these rules into every task context.

- Repo: `C:/Users/matt/PycharmProjects/virtualTubers`, unless the work package says otherwise.
  The host is Windows and the shell is git-bash. Use `.venv/Scripts/python.exe` (Python 3.11).
  Tests import app modules bare, because `tests/conftest.py` puts `app/` on sys.path.
- **Other agents are editing this repo at the same time.** Touch ONLY the files your work package
  owns. If a file outside your list looks broken, report it; don't fix it.
- **Do not commit, push, or stage.** Leave your changes in the working tree. The parent reviews
  and commits.
- Follow CLAUDE.md:
  - structured logging (TRACE/DEBUG/INFO/ERROR)
  - pytest tests under `tests/test_<module>.py`
  - one `docs/<module>.md` per new module, with a changelog v1.0.0
  - never log secrets
- Pre-existing test failures are not yours to fix. Run
  `.venv/Scripts/python.exe -m pytest tests/<your files> -q` and report the before and after
  counts for the files you touched.
- Pass real values. Never leave `<placeholder>` text in code or config.
- Report back with:
  - the files changed
  - the test counts
  - anything left undone, with the reason
  - any decision you made that the plan didn't specify

---

## 2. Waves and work packages

Notation: **Owns** = files the work package may create or modify. **Done when** = the acceptance
check the parent verifies.

### Wave 0: parent only, before dispatch

| ID | Task |
|---|---|
| OB-00 | Create branch `feat/ashiorid-office`. Record the full pytest baseline (the pass/fail/error counts are the reference for every later wave). Create the empty `campaigns/ashiorid_office/{cast,profiles,lore,scenes,music}` directories. |
| OP-1 (user) | In Gitea, create two access tokens and add them to `.env` as `GITEA_TOKEN_OFFICE` and `GITEA_TOKEN_OBSERVER`. The agent never sees the values. `GITEA_TOKEN_OFFICE` needs `write:repository` + `write:issue` on `fraud-stop`. `GITEA_TOKEN_OBSERVER` is read-only. A dedicated bot user per persona is optional (it improves commit and PR attribution); v1 uses `gitea_admin` with per-persona commit author names. The parent creates the `fraud-stop` repo itself, via SSH push-to-create or the Gitea UI. The tokens only block OB-23 live tests and the in-container agents' API calls. |

### Wave 1: foundations (all parallel, no dependencies between them)

**OB-01 — Company lore**

- Owns: `campaigns/ashiorid_office/lore/{company,product,office,org_chart,rituals,rumours,clients}.md`
- Content:
  - company: Ashiorid, the Fraud-Stop product, founding, the 2 previous CEOs (names and how each
    one left)
  - office: floor plan with named rooms, the kitchen, the server closet, the Party Member's desk
  - org chart: taken exactly from design §2
  - rituals: 06:00 standup, lunch, the 00:00 lights-out, and the "no days off" culture framed as
    in-world normality
  - rumours: the spy rumour plus 3–5 others
  - clients: 3–4 banks or prospects, fictional names
- Tie-in: one or two small nods that Ashiorid exists in the wider fantasy world (U1), e.g. a
  client named after a region from `campaigns/ashiorid/lore/`. Nothing more.
- Length: each file 300–900 words, second person neutral, no meta or simulation talk.
- Done when: all 7 files exist and are internally consistent. The parent reads them.

**OB-02 — Office primitives**

- Owns: `app/campaign/primitives.py` (add an office verb set only; don't touch the existing
  verbs), `tests/test_campaign_primitives.py` (append only), `docs/campaign_pack_format.md`
  (primitive list section only)
- Verbs: `assign_task`, `write_spec`, `open_ticket`, `commit`, `run_tests`, `file_bug`,
  `open_pr`, `merge_pr`, `deploy`, `pitch`, `brew_coffee`, `take_out_trash`, `hr_notice`,
  `observe`
- Keep the existing contract: purely narration, deterministic, `PrimitiveError` only.
- Done when: the primitive tests are green and the rest of the suite is unchanged.

**OB-03 — Pack `seats:` support + generic episode seat map**

- Owns: `app/campaign/pack.py`, `app/campaign/validator.py`, `.claude/prompts/build_campaign_episode.py`,
  `tests/test_campaign_pack.py`, `tests/test_campaign_validator.py`, `docs/campaign_pack_format.md`
  (seats section only; coordinate by appending a section, since OB-02 edits a different section)
- Behaviour:
  - `campaign.yaml` gains an optional `seats: {cast_id: tuber_N}`.
  - Validation: N is in 0–7, seats are unique, and every seated id is in the cast.
  - `Pack.seats` is exposed.
  - `build_campaign_episode.py` uses `pack.seats` when it's present. Otherwise it falls back to
    the existing `SPEAKER_TO_WORKER`. Ashiorid output must not change: diff the episode JSON
    before and after.
- Done when: tests pass, and the Ashiorid episode JSON is byte-identical to before.

**OB-04 — sessionCorpus utility v1 (sibling repo)**

- Owns: all of `C:/Users/matt/PycharmProjects/sessionCorpus/` (a new repo, `git init` allowed;
  no commit)
- Layout per CLAUDE.md: `main.py`, `src/`, `tests/`, `docs/`, `README.md`, its own `.venv`,
  `requirements.txt` (pinned), `scripts/install.sh` + `uninstall.sh`, and a `.bat` for Windows
  Task Scheduler.
- Collectors:
  1. **Claude Code**: `~/.claude/projects/*/*.jsonl`. 4 files currently exist.
  2. **Hermes**: `C:/Users/matt/AppData/Local/hermes/state.db`, a SQLite file. Tables:
     - `sessions`: `id`, `source`, `model`, …
     - `messages`: `session_id`, `role`, `content`, `tool_calls`, `tool_name`, `timestamp`, …

     There are currently 82 sessions and 11,893 messages. Open it **read-only**
     (`file:...?mode=ro` URI). Hermes is running.
- Normalised record (a JSON-serialisable superset of virtualTubers `session_log_parser` output):
  - `{source_tool, host, project, session_id, started_at, ended_at, model, events[]}`
  - Each event is `{seq, ts, type: user_message|assistant_text|tool_call, text, tool, input,
    output, error}`.
- Dedupe: `sha256(source_tool + session_id)`. Re-running must be idempotent: sessions that are
  unchanged are skipped, and sessions that grew are updated.
- Redaction at ingest:
  - Port, don't import, the redaction and leak-audit rules from
    `virtualTubers/app/session_log_parser.py`. That file is a different project; don't edit it.
  - Add the local username and hostname to the redact list.
  - A session that fails the audit after redaction is stored with `status=quarantined` and
    excluded from export.
- Store: `data/corpus.db` (SQLite) with tables `sessions` and `events`. Add `data/` to
  `.gitignore`.
- CLI:
  - `main.py collect [--source claude|hermes|all] [--dry-run]`
  - `main.py list [--project X --tool Y --min-events N]`
  - `main.py tag <id> <tag>`
  - `main.py export --out corpus.jsonl [--tag T]`
- Logging: structured logs, plus the Loki push used by CLAUDE.md (fire-and-forget).
- Done when:
  - `collect --dry-run` then `collect` ingest all Claude and Hermes sessions on this machine
  - `export` produces JSONL
  - the leak audit passes on the export
  - tests (fixtures only, no real data in tests) are green

**OB-05 — Office roles + protocol (pure module)**

- Owns: `app/office/__init__.py`, `app/office/roles.py`, `app/office/protocol.py`,
  `tests/test_office_roles.py`, `tests/test_office_protocol.py`, `docs/office_roles.md`,
  `docs/office_protocol.md`
- `roles.py`:
  - `OfficeRole` enum, with the 8 roles.
  - `SEAT` mapping: role → `tuber_N`, per design §2.
  - `HANDLER_ROLE` mapping, per E2.
  - `REPORTS_TO` and `DIRECTS` tables.
  - `can_direct(sender, recipient) -> bool`.
  - `LANES`: role → repo path globs, per E3.
  - `lane_allows(role, path) -> bool`.
- `protocol.py`:
  - Builders and validators for the new message types, each built on `message_bus.build_message`
    and carrying correlation ids:
    - `directive` (CEO → TL/Analyst/Marketing/OM)
    - `functional_plan` (Analyst → TL, cc CEO)
    - `technical_plan` (TL → CEO)
    - `test_request` (Engineer → Tester)
    - `status_report` (up the chain)
    - `phase_change` (the clock, broadcast)
    - `day_start` and `day_end`
  - Validation rejects rank violations with `ProtocolError`.
- Pure module: no Kafka, no LLM, no filesystem.
- Done when: tests cover every allowed and denied edge of the chain.

**OB-06 — Office world clock**

- Owns: `app/office/clock.py`, `tests/test_office_clock.py`, `docs/office_clock.md`
- `office_time(now: datetime, epoch: date, tz="America/New_York") -> OfficeTime`, a frozen
  dataclass with fields:
  - `loop_week`
  - `day_index` (0 = Sunday)
  - `segment` (0–3)
  - `phase` (`off` | `morning` | `build` | `ship`)
  - `is_work_hours`
  - `next_boundary`
- The epoch is a Sunday, per v4 D-01.
- DST: correct in both directions. Boundaries are local wall-clock times.
- Done when: parametrized tests cover segment edges, week rollover, DST spring and fall, and a
  non-Sunday epoch rejected.

**OB-07 — W0 model benchmark (report only)**

- Owns: `.claude/prompts/office_w0_benchmark.py`, `.claude/prompts/office_w0_benchmark.md`
- Against the gx10 Ollama (`qwen3.8:27b`, `qwen3-coder:30b`, plus anything else it lists),
  measure at concurrency 1, 4 and 8:
  - tokens/s
  - time-to-first-token
  - p50/p95 latency for a 150-token in-character line
  - one 800-token code-edit completion
- Don't pull or delete models.
- Deliverable: a recommended allocation. Which roles share which model, the realistic line
  latency, and whether 8 concurrent agents are viable or turns must be serialized.
- Done when: the report has measured numbers. The parent reads it before Wave 3 configs.

### Wave 2: characters and content (needs OB-01; OB-11 also needs OB-02/03)

**OB-10a — Cast bible**

- Owns: `campaigns/ashiorid_office/profiles/_cast_bible.md`
- One agent writes the shared frame so the character agents stay consistent:
  - names, ages and pronouns (Office Manager is female; the others are decided here)
  - tenure timeline, anchored on the Tech Lead's tenure across 2 previous CEOs
  - the 8×8 relationship matrix, one line per directed pair that matters
  - voice and speech-style distinctness
  - the CEO's GM stance
  - the Party Member's observable behaviour (the hidden truth is left undefined, per U6)

**OB-10b — Characters, 4 agents × 2 characters**

- Owns: `campaigns/ashiorid_office/cast/<id>.yaml` + `profiles/<id>.yaml` for that agent's pair
  only. Ids: `ceo`, `tech_lead`, `analyst`, `engineer`, `tester`, `marketing`,
  `office_manager`, `party_member`.
- Pairs: (ceo, analyst), (tech_lead, engineer), (tester, marketing),
  (office_manager, party_member).
- Cast file keys:
  - the pack keys `name`, `archetype`, `voice`, `system_prompt`
  - agent_dnd §6.2 keys: `wants`, `fears`, `speech`, `relationships`, `knowledge`,
    `turn_order_pos`
  - `seat`
  - `voice` taken from design §5 WS-G
- Profile file keys, per design §5 WS-A:
  - `identity`, `appearance` (detailed enough for sliders: face shape, build, age cues, colouring,
    signature accessory), `personality`, `objectives`
  - `backstory.believed`: first person, 600–1200 words
  - `backstory.truth`: GM-only, short; may be left mostly open
  - `backstory_nodes`, `behaviour_contract`
  - `retains_fragments: true`, `is_main: true`
- `system_prompt` rules:
  - It must encode the rank: who they obey and who they direct.
  - It must encode the lane from E3.
  - It must include "never state or imply that time repeats".
  - Party Member: "you never speak; you only observe".

**OB-10c — Profile validator + consistency pass**

- Owns: `scripts/validate_office_profiles.py`, `tests/test_validate_office_profiles.py`,
  `docs/validate_office_profiles.md`
- Checks:
  - required keys
  - node names match `^[a-z0-9]+(-[a-z0-9]+){1,7}$` and start with a verb from the v4
    allowlist
  - relationship targets exist
  - seats are unique
  - voices exist in `config/voices.yaml`
  - backstory tenure agrees with the cast bible
- Run it, report the violations to the parent, and do NOT edit the character files. The parent
  sends fixes back to the owning OB-10b agent.
- **USER REVIEW GATE** after OB-10: the user reads all 8 characters before avatars or agents are
  built on them.

**OB-11 — Pack skeleton**

- Owns: `campaigns/ashiorid_office/campaign.yaml`, `campaigns/ashiorid_office/music/theme.yaml`,
  `campaigns/ashiorid_office/scenes/00-first-standup.yaml`
- `campaign.yaml`:
  - `gm: ceo`
  - `players`: the 7 others
  - `genre: office`
  - `seats`, per design §2
  - `primitives`: the OB-02 office set
  - `ambient: {every: 2}`
  - `theme`
- One hand-written spine scene, the first 06:00 standup, so that `--validate` and `--dry-run`
  pass.
- A music theme with an office lo-fi motif.
- Done when:
  - `PYTHONPATH=app .venv/Scripts/python.exe app/campaign/cli.py --pack campaigns/ashiorid_office --validate`
    prints ok
  - `--dry-run --no-pace` plays the scene
  - `build_campaign_episode.py` produces a valid episode with the seats

**OB-12 — Corpus → content bridge**

- Needs OB-04 and OB-05.
- Owns:
  - `utilities/3LayersWeeklyGeneration/src/source_adapter.py` (add a `CorpusAdapter` class only)
  - `utilities/3LayersWeeklyGeneration/tests/test_source_adapter_corpus.py`
  - `app/office/role_attribution.py`, `tests/test_office_role_attribution.py`,
    `docs/office_role_attribution.md`
- `CorpusAdapter`: reads the sessionCorpus JSONL export and yields
  `SourceNote(kind="work_session")`. Each note has a condensed text: the user asks, the files
  touched, the test outcomes, the final result.
- `role_attribution`: a rule pass that maps each event to an office role, following the design
  §5 WS-D table and using `app/office/roles.py`. Output is a replay episode with `speaker:
  tuber_N`, and `session_log_parser.audit()` is re-run as a hard gate.
- The LLM pass (hand-off lines, Marketing reactions) is included behind a flag, with the LLM
  client injected and mocked in tests.

### Wave 3: avatars, GitHub, live agents (needs the OB-10 review gate, OB-05, OB-06, OB-07)

**OB-20 — Backstory → avatar (`map_appearance`)**

- Owns: `app/character/__init__.py`, `app/character/avatar.py`,
  `scripts/generate_office_avatars.py`, `tests/test_character_avatar.py`,
  `docs/character_avatar.md`
- `map_appearance(profile, llm_client) -> dict`:
  - The LLM reads `appearance` + `personality` and outputs strict JSON: the 8
    `SLIDER_DEFAULTS` keys plus `accent_color` from `ACCENT_COLORS` (`app/character_schema.py`).
  - The output is clamped and validated through `resolve_params`.
  - On failure it retries once, then falls back to the defaults and logs it.
- The script:
  - writes `character_params` into each cast YAML (the pack loader ignores unknown keys)
  - prints the roster snippet for the office roundtable config
  - renders one preview PNG per character via `app/character_preview.py` into `preview_out/office/`
- Done when:
  - the 8 PNGs exist
  - the parent (text-only) confirms the params differ meaningfully between characters
  - the user eyeballs the PNGs

**OB-23 — Gitea push + PR support**

- Owns: `app/git_client.py`, `app/gitea_client.py` (new), `tests/test_git_client.py`,
  `tests/test_gitea_client.py`, `docs/git_client.md`, `docs/gitea_client.md`
- `push()`:
  - works against the Gitea remote: SSH (`ssh://git@192.168.1.120:2222/...`) when a key is
    mounted, or http(s) with the token supplied through a `GIT_ASKPASS` helper
  - the token is never embedded in the remote URL and never logged
  - the local-only no-op stays the default
- `gitea_client.py`, a thin wrapper over the Gitea REST API v1 (server 1.22.3, base
  `http://192.168.1.120:3300/api/v1`, auth header `Authorization: token ...`), using urllib or
  requests, whichever is in `requirements.txt`: `open_issue`, `close_issue`, `open_pr`,
  `review_pr`, `merge_pr`, `delete_branch`, `list_prs`, `create_tag`.
- Unit tests mock HTTP. One opt-in integration test is marked `@pytest.mark.integration` and
  skipped unless `GITEA_TOKEN_OFFICE` is set.

**OB-24 — Fraud-Stop seed repo**

- Owns: all of `C:/Users/matt/PycharmProjects/fraudStop/`, a new repo.
- A deliberately small starting codebase that the agents grow during the loop:
  - Python 3.11, FastAPI
  - `POST /v1/transactions/score` → `{score, verdict, reasons[]}`
  - a rule engine with 3 rules (velocity, amount outlier, country mismatch)
  - an in-memory store
  - pytest, plus a `scripts/ci.sh` the Tester runs (Gitea Actions only if a runner exists; don't assume one)
  - lane directories `docs/requirements`, `docs/design`, `marketing/`, `CHANGELOG.md`
  - `CODEOWNERS` mirroring the E3 lanes
- The parent creates `gitea_admin/fraud-stop` on Gitea, pushes the seed, and tags it `loop-seed`. The agent does not push.
- **Loop semantic:** at every Sunday reset the repo is hard-reset to the `loop-seed` tag, on a
  fresh branch `loop/<week>`. The world resets with it. The previous week's branch is kept as
  an archive (just like archived memory). OB-31 implements the reset step.

**OB-21 — Office agent handlers**

- Owns:
  - `app/agent_handlers/office.py` (new)
  - `app/agent_handlers/__init__.py` (register the new types and idle hooks only)
  - `app/agent_handlers/common.py` (only if a shared helper is truly needed; list the helper in
    the report)
  - `tests/test_agent_handlers_office.py`, `docs/agent_handlers.md` (office section)
- New handlers:
  - `handle_directive` (Analyst, TL, Marketing, OM)
  - `handle_functional_plan` (TL)
  - `handle_technical_plan` (CEO ack)
  - `handle_test_request` (Tester)
  - `handle_status_report` (CEO, TL)
  - `handle_phase_change` (all)
  - `ceo_idle_tick`
  - `office_manager_idle_tick` (coffee, cleanup, branch garbage collection)
  - `observer_idle_tick` (emits gaze/pose only, never text)
- Each handler:
  1. checks rank via `app/office/protocol.py`
  2. builds the persona prompt from the E6 stub brief (cast + profile) through one function,
     `app/office/brief_stub.py`, which is owned here as well
  3. calls the LLM
  4. performs its lane action through `gitea_client` / `git_client`, enforced by
     `lane_allows`
  5. emits the next protocol message
- Existing manager/coder/tester handlers:
  - Reuse them, per E2.
  - Change them only where an `office_role` hook is unavoidable, and list each change.
  - Existing handler tests must stay green.
- Done when: a fake-bus e2e test (following the pattern of the existing multi-agent e2e tests,
  commit 541a9dc) runs the full chain directive → functional_plan → technical_plan →
  task_assignment → commit → test_request → test_passed → status_report → CEO, using fakes for
  the LLM, git and Gitea.

**OB-22 — Office worker configs + compose override**

- Owns:
  - `config/workers/office/*.yaml` (8 files + `roundtable.yaml`)
  - `docker-compose.office.yml`
  - `docs/office_deployment.md`
  - `redeploy.sh` (add an `--office` flag only)
- Configs:
  - Copy the structure of the matching existing worker config.
  - Set `agent.role` / `agent.office_role` per E2.
  - Set the persona name, the `voice.speakers` keys as `tuber_0..7`, and the model per the
    OB-07 report.
  - Engineer: `coding_backend.provider` is chosen per the OB-07 report (likely `aider` or
    `opencode` with qwen3-coder), with `WORKSPACE_PATH` pointing at the Fraud-Stop clone.
- Override:
  - point each service's CONFIG at `config/workers/office/`
  - add a `worker-observer` service for tuber_7, reusing the DISPLAY/stream-key conventions of
    the existing services
  - mount the tokens from `.env` by variable name only
- Done when: `docker compose -f docker-compose.yml -f docker-compose.office.yml config`
  validates locally.

### Wave 4: the running day (needs Wave 3)

**OB-30 — Day runner (CEO-as-GM orchestration)**

- Owns: `app/office/day_runner.py`, `tests/test_office_day_runner.py`, `docs/office_day_runner.md`,
  plus a hook in `ceo_idle_tick`. Coordinate: OB-21 exposes a `day_runner` injection point, and
  OB-30 fills it.
- Driven by `office_time`:
  - at 06:00, emit `day_start`, then the CEO picks today's directive
  - at each segment edge, emit `phase_change`
  - s3 end, 23:45: wrap-up status reports, then `day_end` at 00:00
  - during s0: no live work; hand off to the ambient/replay playlist (OB-33)
- Directive sources:
  1. the arc plan's spine for today (OB-40), when present
  2. otherwise a corpus session tagged `feature` (via OB-12), rewritten as a Fraud-Stop
     directive
  3. otherwise the open Fraud-Stop issues backlog
- Guards:
  - one active directive per day plus at most 2 follow-ups
  - a hard cap on retries (reuse `MAX_BUG_RETRIES`)
  - a stall detector that emits a fallback replay request after N idle minutes

**OB-31 — Weekly reset orchestration (office side)**

- Owns: `app/office/weekly_reset.py`, `tests/test_office_weekly_reset.py`, `docs/office_weekly_reset.md`
- At Sunday 00:00:
  1. Fraud-Stop: archive branch `archive/week-<N>`, reset to the `loop-seed` tag, delete merged
     branches, close issues with the label `loop-<N>`.
  2. Clear the agents' session state.
  3. Emit `character_refresh`.
  4. Call the v4 `weekly-reset` job when it exists (a stub hook until WS-F lands).
- Idempotent through a step ledger file, mirroring v4 `loop_weeks.reset_steps`.
- `--dry-run` supported.

**OB-32 — Live transcript on the roundtable + stream wiring**

- Owns: the office roundtable config (from OB-22; coordinate), `services/control-panel/panel.py`
  (the office mapping only), `app/replay_pane.py` or a new `app/live_pane.py`, and their tests
  and docs.
- Per agent_dnd W2:
  - committed live protocol lines render on the speaker's tile
  - the voice gate and refusal path are unchanged
  - the observer tile shows a gaze-follow idle pose (`gaze.py`)
- Investigate the existing show-log/relay-file convention first, and extend it rather than add
  a parallel path.

**OB-33 — Replay fallback + off-hours playlist**

- Owns:
  - `app/office/playlist.py`, `tests/test_office_playlist.py`
  - `app/revoice.py` (per-role tone hook only)
  - `scripts/build_office_replays.py`
- Builds office episodes from the corpus via OB-12 role attribution, and uploads them as drafts
  to `/replays`.
- The playlist fills s0 and any stall: ambient scenes first, then approved office replays.
- Draft review in the control panel stays the gate.

### Wave 5: arc, memory, end to end

**OB-40 — Campaign arc generation**

- Owns:
  - `utilities/3LayersWeeklyGeneration/config/generation.ashiorid_office.yaml`
  - `campaigns/ashiorid_office/scenes/a0NN-*.yaml` (the ambient pool)
  - `.claude/prompts/run_office_arc.py`
- Content:
  - 28 segments, `plot_0` ring `[16,4,8]`, ring enabled
  - The week is a Fraud-Stop push: a major bank pilot goes live Saturday. The keystone is a
    mid-week production fraud miss or a regulator audit (pick in the config).
  - Secondary threads: Marketing's launch, office life, the spy rumour.
  - About 60 ambient prompts tagged by phase and participants. The s0 ones cover the empty
    office, home life, and Office Manager night shift.
- Runs L1 → L2 → L3 through `services/3layer-generator`, then `pack_gate`.
- Promotion to `scenes/` is a manual parent step after user review.
- Note: forks/events, tethers and the JIT picker are unbuilt 3-layer features (see design §7).
  The arc must not rely on them.

**OB-41 — Memory (v4 track, re-piloted on the office cast)**

- A separate long track run from `docs/charcterProfileGenerationNotes/character_v4_build_playbook.md`
  and `.claude/prompts/character_v4_build_status.md`. Continue WP-02 onward, with these
  overrides:
  - The pilot cast is the 8 office characters, not Harry, Ron and Hermione.
  - The book source stages are replaced by `scripts/load_office_profiles.py`, which loads
    `profiles/*.yaml` into `characters` / `character_backstories` / `character_baselines`.
  - `retains_fragments: true` for all 8. The recall harness (WP-23) is calibrated on office
    beats.
  - The Party Member's experience comes only from `visibility: present` events.
- When `brief.py` lands, swap it in for `app/office/brief_stub.py` (E6).
- This track has its own status file. Keep updating `character_v4_build_status.md`.

**OB-42 — End-to-end on argyre**

Run by the parent, not delegated. Deploy with `./redeploy.sh -y --office`.

1. **Smoke:** one directive with CEO + Tech Lead + Engineer + Tester live. It must produce a
   real merged PR on the Gitea Fraud-Stop repo, and the chain must be audible and visible on the roundtable.
2. **One full 18/6 day**, including s0 playlist hand-off and `day_end`.
3. **Forced weekly reset** (`--at`), then verify the repo reset, the archive branch, and fresh
   agent state.
4. **Full week.** Fragment unlock checks follow once OB-41 reaches WP-23.

---

## 3. Dependency graph

```
Wave1: OB-01  OB-02  OB-03  OB-04  OB-05  OB-06  OB-07        (parallel)
         |      \      /       |      |      |      |
Wave2: OB-10a -> OB-10b(x4) -> OB-10c -> [USER REVIEW]
                 OB-11 (needs 01,02,03)     OB-12 (needs 04,05)
Wave3: OB-20 (needs review)  OB-23  OB-24 (parent pushes to Gitea)
       OB-21 (needs 05,06,23)  OB-22 (needs 07,10,21 names)
Wave4: OB-30 (21,06)  OB-31 (24,06)  OB-32 (22)  OB-33 (12)
Wave5: OB-40 (11,12)  OB-41 (10; long, parallel from Wave 2 on)  OB-42 (all)
```

- OB-41 (the memory DB) can start as soon as the characters pass review. It runs in parallel
  with Waves 3–5.
- Maximum concurrency per wave is 7 agents (Wave 1). Wave 2 is 1, then 4, then 1, plus OB-11
  and OB-12 alongside.

## 4. File-ownership collision check

Shared files and who owns each part:

| File | Owners | Rule |
|---|---|---|
| `docs/campaign_pack_format.md` | OB-02, OB-03 | Each appends its own section; neither rewrites the file. |
| `app/agent_handlers/__init__.py` | OB-21 only | OB-30 does not edit it. |
| Office `roundtable.yaml` | OB-22 creates; OB-32 edits | Sequential waves. |
| `app/revoice.py` | OB-33 only | |
| `utilities/3LayersWeeklyGeneration/src/source_adapter.py` | OB-12 only | |

All other work packages own disjoint paths.

## 5. Parent verification per wave

- Re-run the full pytest suite on a quiet tree and compare with the OB-00 baseline.
- Review the diffs and commit per work package (conventional commits, scope `office`), on
  `feat/ashiorid-office`.
- Headline artifacts are checked by the parent:
  - Wave 1: the corpus row counts, the benchmark numbers
  - Wave 2: the `--validate` output
  - Wave 3: the PNGs, the compose config
  - Wave 4: the fake-bus e2e test
  - Wave 5: the argyre deployment
- Update this file's status table as work packages land.

## 6. Status

| WP | Status |
|---|---|
| OB-00 | done. Branch `feat/ashiorid-office` created. Baseline: 2575 passed, 3 failed, 7 skipped, 1 xfailed. The 3 failures are pre-existing Windows failures in `tests/test_relay_io.py` (umask and concurrent atomic-write tests). |
| OP-1 | todo (user) |
| OB-01 | done (c5d06af). Tenures fixed in lore: TL 8y, OM 5y, Analyst 3y, Engineer 2y, Tester 18mo, CEO 14mo, Party Member 13mo, Marketing 10mo. Former CEOs: Oswin Harrowgate (founder), Delphine Maro-Kest. |
| OB-02 | done (9df6298). 14 office verbs, genre `office`. |
| OB-03 | done (65158e2). `seats:`, `check_seats`, `speaker_map`. Ashiorid and hptest episodes byte-identical before and after. |
| OB-04 | done (sessionCorpus 8261114, separate repo). 3 Claude + 90 Hermes sessions, 0 quarantined. Its redaction fixes (escaped-JSON IPs/passwords, masked tokens) likely also apply to `app/session_log_parser.py`: follow-up. |
| OB-05 | done (999a663). Tester's superior is the Tech Lead only; Engineer→Tester is `test_request` only. Clock messages are sent by `office_clock` or `tuber_0`. |
| OB-06 | done (0252c43). Added `tzdata` to requirements.txt and .venv. |
| OB-07 | done (a19c6bd). Speaking turns must be SERIALIZED. `qwen3-coder:30b` is not installed. `hermes3:70b` is unusable on gx10. Allocation: CEO/TL/Analyst/Marketing on gemma4:26b; Tester/OM on gemma4:12b; Engineer on qwen3.8:27b + aider; llama3.1:8b as fallback; keep_alive ≥30m. OPEN: another client holds qwen3.8:27b at 262k ctx, so a ctx mismatch costs a 30–70 s reload. |
| Wave 1 verify | Full suite: 2939 passed, 3 failed (the same baseline relay_io failures), 7 skipped. |
| OB-10a | done. `profiles/_cast_bible.md`: names, ages, speech styles, relationship matrix, odd details. Names: CEO Graham Ellery, TL Anselm Brody, Analyst Maren Voss, Engineer Theo Palliser, Tester Owen Hask, Marketing Julian Faire, OM Nora Blakeley, Party Member "A. Penhale". |
| OB-10b | done (30f7417, 9b68f20, 47c3c0a, 62f134b; e74cb8c de-duplicates two signature features). All 8 `cast/<id>.yaml` + `profiles/<id>.yaml`. Believed backstories 804–998 words. |
| OB-10c | done. Validator: PASS, 0 errors, 0 warnings on all 8. USER REVIEW GATE passed (user approved the characters 2026-09-28). |
| OB-11 | done (fad4123). `--validate` ok, `--dry-run --no-pace` plays `first-standup`, `build_campaign_episode.py` → 15 events, PASS. Follow-up: the builder drops text-less action beats (coffee, assign_task, observe, open_ticket), so office actions and tuber_7 never reach the episode. |
| OB-12 | done. `CorpusAdapter` in 3Layers `source_adapter.py` (`.jsonl` → work_session notes). `app/office/role_attribution.py` (session → tuber_N episode, audit-gated). |
| OB-20 | done (bc04460). `app/character/avatar.py` map_appearance; params written to cast YAMLs; 8 PNGs in `preview_out/office/` (gitignored). Params came from the offline `--params-file` path (`profiles/_avatar_params.json`) because no LLM is reachable from the cloud container — re-run live on argyre with `--base-url http://192.168.1.23:11434` if wanted. User still to eyeball the PNGs. |
| OB-21 | done (99e710a). `app/agent_handlers/office.py` + `app/office/brief_stub.py`; fake-bus e2e passes. `day_start`/`day_end` unhandled (OB-30); `set_day_runner()` / `issue_directive()` are the OB-30 hooks. Reused manager/coder/tester narration still uses `agent.system_prompt`, so OB-22 must fill it from the cast files. Config schema: `agent.office` block, see docs/agent_handlers.md. |
| OB-22 | done (2ce8bef). `config/workers/office/`, `docker-compose.office.yml` (+ worker-observer, per-seat Fraud-Stop clone volumes), `redeploy.sh --office`, docs/office_deployment.md. Merged compose config validates. |
| OB-23 | done. `git_client` has real push/fetch/tag/reset (ssh or askpass token, never logged); new `app/gitea_client.py`. NOTE: Gitea returns 422 on self-approval. With one shared token, TL approvals must be COMMENT reviews, or each persona needs its own bot user/token. |
| OB-24 | done. Seed repo at `C:/Users/matt/PycharmProjects/fraudStop`, tag `loop-seed` (20a1de1), 24 tests green. Canon from product.md: score 0–1000, APPROVE/REVIEW/DECLINE, keyed on account_id. Not yet on Gitea (see Handoff). |
| OB-30 | done (8371d36). Day runner from the CEO idle tick; arc → corpus → Gitea backlog directive sources; restart-safe state. |
| OB-31 | done (00e3054). Weekly reset with step ledger, `--dry-run`, `--at`. |
| OB-32 | done (b94a00c). Live transcript: `office_line` → roundtable spool → director → `<slot>.live.json`; observer gaze only; control panel `CONTROL_PANEL_SHOW=office`. |
| OB-33 | done (4951938). Playlist, draft-only office replay builder, revoice role tones. |
| Integration | done (adba443 builder action beats, 82f9632 playlist↔day runner + Party Member silence, 6e98d09 wrap_up + week-branch merges + character_refresh + e2e day/reset test). |
| OB-40 | **NEXT.** Needs the 3-layer generator + a local LLM (argyre); not runnable from the cloud container. |
| OB-41 | todo (long v4 memory track). |
| OB-42 | todo — run by the parent on argyre after OP-1 tokens + Gitea repos exist. |


## 7. Handoff: where we are (updated 2026-09-27, session 2)

**Session 2, continued (2026-09-28):** after the user approved the characters, Waves 3 and 4
landed (OB-20, OB-22, OB-30..OB-33) plus two integration batches. Full suite: **4444 passed, 0
failed, 61 skipped, 1 xfailed**. Remaining: OB-40 (arc generation — needs argyre's LLM), OB-41
(v4 memory), OB-42 (end-to-end on argyre, blocked on OP-1 tokens and the `fraud-stop` /
`sessionCorpus` Gitea repos). Deployment prerequisites are in docs/office_deployment.md
(Fraud-Stop clone volumes, `OFFICE_TWITCH_CHANNEL_MAP`, optional `OFFICE_CORPUS_DIR`).

**Session 2 (Linux cloud container, branch `claude/feature-development-progress-79sysa`):**
OB-10b, OB-10c run, OB-11 and OB-21 landed. Linux full suite: **4042 passed, 0 failed, 61
skipped, 1 xfailed** (the Windows relay_io failures don't occur on Linux; service deps from
`services/*/requirements.txt` must be installed into `.venv` for collection). Next: USER REVIEW
GATE on the 8 characters, then OB-20; OB-22 can proceed in parallel.

### Session 1 notes (original handoff)

Branch `feat/ashiorid-office` on `origin` (Gitea `gitea_admin/virtualTubers`, push-mirrored to
GitHub `builderOfTheWorlds/virtualTubers`).

Full suite at handoff: **3972 passed, 3 failed, 63 skipped, 1 xfailed**. The 3 failures are the
baseline Windows `tests/test_relay_io.py` failures, not regressions.

### Repos in play

| Repo | Local path | Remote | State |
|---|---|---|---|
| virtualTubers | `C:/Users/matt/PycharmProjects/virtualTubers` | Gitea `gitea_admin/virtualTubers`, mirrored to GitHub | branch `feat/ashiorid-office` |
| sessionCorpus | `C:/Users/matt/PycharmProjects/sessionCorpus` | **LOCAL ONLY.** Remote `origin` is set to `ssh://git@192.168.1.120:2222/gitea_admin/sessionCorpus.git`, but the push failed: the repo doesn't exist and push-to-create is off. Create it in Gitea, then run `git push -u origin main` | main, committed |
| fraudStop | `C:/Users/matt/PycharmProjects/fraudStop` | **LOCAL ONLY.** Same as sessionCorpus. Create `gitea_admin/fraud-stop` (the name OB-22/OB-31 expect), run `git remote set-url origin ssh://git@192.168.1.120:2222/gitea_admin/fraud-stop.git`, then `git push -u origin main --tags` | main + tag `loop-seed` (20a1de1) |

### Resume here: next actions, in order

1. **OB-10b + OB-11 (parallel).**
   - Dispatch 4 character agents (pairs listed in OB-10b) plus the pack-skeleton agent.
   - Each character agent reads `profiles/_SCHEMA.md`, `profiles/_cast_bible.md`, `lore/*.md`.
2. **OB-10c run.** `.venv/Scripts/python.exe scripts/validate_office_profiles.py`. Send the
   violations back to the owning pair and repeat until it exits 0.
3. **USER REVIEW GATE:** the user reads all 8 characters.
4. **Wave 3:**
   - OB-20 avatars (`map_appearance`)
   - OB-21 office handlers, which must include `brief_stub.py`
   - OB-22 configs + `docker-compose.office.yml`, using the OB-07 model allocation
5. **Wave 4:** OB-30 .. OB-33. **Wave 5:** OB-40 .. OB-42.

### Open items / user actions

- **OP-1:** add `GITEA_TOKEN_OFFICE` and `GITEA_TOKEN_OBSERVER` to `.env` (names are already in
  `.env.example`). Consider one Gitea bot user per persona to avoid the self-approval 422.
- **Model context clash (OB-07):** another client holds `qwen3.8:27b` at 262k ctx. The Engineer
  at 8k forces a 30–70 s reload. Decide: match the 262k ctx, or keep the other client off it
  during 06:00–00:00.
- **GitHub mirror token** (the Gitea push-mirror) **expires 2026-10-03.** Renew it in Gitea →
  repo → Settings → Repository → Mirror Settings. Add mirrors for sessionCorpus and fraudStop
  there too if they are wanted on GitHub.
- **Follow-up:** sessionCorpus found redaction gaps (escaped-JSON IPs/passwords, masked
  `ghp_` tokens) that likely also exist in `app/session_log_parser.py`. Port the fixes and tests.
- The 3 coder worker configs name `qwen2.5:7b-instruct-q4_K_M`, which isn't installed on gx10.
  This affects the existing dev-team show, not the office build.
- `tzdata` was added to requirements.txt. The Docker image needs a rebuild to pick it up.

### Conventions for subagents (unchanged)

§1 rules apply. The parent commits per WP with conventional commits, scope `office`, on
`feat/ashiorid-office`, and re-runs the full suite after each wave against the counts above.
