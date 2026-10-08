# HANDOVER: Live Agent Table on vLLM (GM + character agents)

Written 2026-10-07 so a fresh session can start cold. Every fact below was
verified in this session; don't re-verify unless a step depends on it.

## 0. Start here (paste into the new session)

> Read `.claude/prompts/agent_dnd_vllm_HANDOVER.md` (this file), then
> `.claude/prompts/agent_dnd_vllm_build_plan.md` (the plan; §1, §3 and §6
> matter). Don't read the parent specs in full; jump to a section only when
> a WP cites it. Start with §5 "Next actions".

Token-saving rules for the new session:
- The plan is the source of truth. The parent docs are reference only:
  `.claude/prompts/agent_dnd_architecture.md` (602 lines) and
  `docs/charcterProfileGenerationNotes/character_generator_updater_v4.md` (661 lines).
- Don't search GitHub, Gitea or git history for the "missing" character code. That was
  done exhaustively (§3.4): it doesn't exist.
- Grep for exact symbols. Don't open large files whole (`app/agent.py`,
  `docker-compose.yml` ~1090 lines, `app/replay_pane.py`).

## 1. Goal (one paragraph)

Each character on the stream is its own agent: its own process in its own
worker container, its own context and knowledge. All agents share ONE model
served by vLLM. The GM is an agent on the same model with a larger
context (truth layer, every sheet, the arc plan) and the authority to
direct, judge, retake and overrule. Characters talk only through the GM's
turn arbiter over Kafka. The roundtable channel shows the spoken table.
Each character's own channel shows its private reasoning.

## 2. User decisions (final unless the user reopens them)

| # | Decision |
|---|---|
| U1 | Build on the Ashiorid D&D pack first (GM + 4 players: Chadwick, Leena, Sodacan Bob, Vigil). Everything pack-agnostic; `ashiorid_office` (GM + 7 seats, one silent observer) must run on the same code with config only. |
| U2 | One shared vLLM model, picked by benchmarking 2–3 candidates (BF16 baseline, FP8, a small-active MoE). |
| U3 | Knowledge: the minimal v4 subset, i.e. the per-character **brief** from the `character_profile` Postgres. The weekly loop, ingest, summaries and fragments are deferred (plan Phase 8). |
| U4 | Reasoning ON; each seat's reasoning streams to its own Thinking pane. |
| U5 | Targets: SPEAK ≈ 15 s/line, D&D round ≈ 3 min. Provisional; must be proven by test. |
| U6 | Private intent and reasoning go ONLY on that seat's own stream. Never on the roundtable, never in another seat's context. |
| U7 | The GM profile is extensible: the player schema + optional GM-only blocks; adding a block is data, not code. |
| U8 | The story comes from the 3-layer generator's **arc plan** (Layer 1 `arc_plan` + Layer 2 segment `brief`/`tree`), not from the hand-authored `campaigns/ashiorid/scenes/`. The live table replaces Layer 3 (offline dialogue). |
| OPEN | Two-pass round (parallel private THINK, then sequential SPEAK). Working assumption: yes. P1.1 measures both shapes; confirm with the user using the numbers. |
| OPEN | Which generator run is canonical for D&D (§3.6). Resolve in P3.2. |

User working style: autonomous and persistent, with periodic status updates.
Flag any contradiction with the existing specs explicitly ("this broke X,
here's what I changed"). Bulk generation is delegated to local models; the
agent plans, reviews and verifies. Plans come with a rendered diagram.
Prefer routing writes through Postgres/API over rw host mounts. When time is
short, file an issue rather than half-fixing.

## 3. Verified state (2026-10-07)

### 3.1 Repo
- `~/codeProjects/virtualTubers`, branch `claude/feature-development-progress-79sysa`
  (HEAD 26af0d9), identical to `github/` (builderOfTheWorlds/virtualTubers).
  Gitea `origin` (192.168.1.120:3300) is 2 commits behind on this branch; its main isn't synced.
- GitHub branches: main, feat/ashiorid-office and the current branch are all merged into local.
  `feat/campaign-platform` (9 commits, July; primitives engine, episode
  schema validator, generic worker-1..8, campaign_control) was **never merged**.
  It's unrelated to this work and conflicts heavily; ignore it unless the user asks.
- **Uncommitted from this session:**
  - `.claude/prompts/agent_dnd_vllm_build_plan.md`
  - `.claude/prompts/agent_dnd_vllm_HANDOVER.md`
  - `scripts/render_agent_dnd_vllm_plan.py`
  - `docs/agent_dnd_vllm_plan.png`

  Commit them first (§5 step 1).

### 3.2 What exists and gets reused
| Piece | Location / symbol |
|---|---|
| Handler dispatch | `app/agent_handlers/__init__.py`: `MESSAGE_HANDLERS` (dict type→fn), `IDLE_TICK_HOOKS` (role→hook `(worker_id, agent_config, llm_client, producer, state_path=None)`) |
| Seat ↔ container ↔ persona mapping (pattern to copy) | `docker-compose.office.yml` header: worker-gm=tuber_0, worker-manager=tuber_1, worker-coder-native=tuber_2, worker-coder-aider=tuber_3, worker-tester=tuber_4, worker-coder-opencode=tuber_5, worker-coder=tuber_6, worker-observer(new)=tuber_7, worker-roundtable=director |
| Worker LLM client | `app/llm_client.py`: `OllamaClient`, `ClaudeClient`, `build_llm_client(config)`; env `LLM_PROVIDER` / `LLM_BASE_URL` (compose default `http://host.docker.internal:11434`). **No vLLM provider.** |
| vLLM client to port | `utilities/3LayersWeeklyGeneration/src/concurrent_llm.py:248` `OpenAICompatClient` (Bearer, SSE, `complete` / `complete_streaming`); `from_profile()` at :462 |
| Thinking publication | `app/agent_metrics.py:215` `InstrumentedLLMClient`: injects a *prompted* `<thinking>` instruction and publishes `agent_thinking` `{text}`. Needs a native-reasoning path (plan P0.4) |
| Thinking pane | `app/thinking_pane.py` filters `type==agent_thinking AND from==WORKER_ID` |
| Seat layout | `config/layouts/tuber_base.yaml`: radar, knowledge graph, avatar, Thinking, chat list, Kafka feed (already built) |
| Per-scene context + silent-cast rule | `app/campaign/improviser.py`: `LLMImproviser.update_context/observe`, `_silent_cast_ids` (cast whose system_prompt says "you never speak") |
| Roundtable director / relay files | `app/replay_pane.py` (cue/ready/stop relay files via `app/relay_io.py`), `app/tile_pane.py`, `app/voice_gate.py` |
| Office profiles with the §6.2 fields | `campaigns/ashiorid_office/profiles/*.yaml` + `_SCHEMA.md` (seat, wants, fears, speech, relationships, knowledge, turn_order_pos) |
| D&D pack | `campaigns/ashiorid/`: cast `gm, chadwick, Leena, sodacan_bob, Vigil` (name/archetype/voice/avatar/system_prompt only, **no profiles**); lore: amulet-of-wonder, malmont, moonwells, the-bahadur, the-begene-program, the-event |
| Benchmark harness | `utilities/benchmarker/` (`bin/runBenchmark.sh --host vllm --base-url …`), methodology `.claude/prompts/benchmark_methodology_dnd_agents.md`. The last run crashed in `full_round` (pre-fix bug) |

### 3.3 Infra
- GB10, ~121 GiB unified memory. At check time: 25 used / 96 available, Ollama had nothing loaded,
  and no vLLM was running.
- vLLM deployments live in `~/environments/argyreServer/deployments/` (`vllm-qwen3.8-27b`,
  `vllm-hermes3-70b`, `vllm-qwen3-coder`). Images are built; containers are stopped
  (hermes3-70b exited 137 = OOM). qwen3.8-27b compose: host port **8092**,
  image `vllm/vllm-openai:v0.30.0-aarch64-cu129`, `--reasoning-parser qwen3`,
  env-driven `VLLM_MODEL / VLLM_SERVED_NAME / VLLM_GPU_MEM_UTIL / VLLM_MAX_MODEL_LEN`.
- vLLM requires an API key (`VLLM_API_KEY` in the container env). **Never print it.** Read it
  from the env/.env and pass it through by name only.
- Measured: Qwen3.8-27B BF16 on vLLM, thinking ON, small plan prompt, 1024 budget → ~100 s wall
  (~4 tok/s per stream). Thinking OFF ≈ 20 s. This is why U2 benchmarks FP8/MoE.
- **OOM rule:** run one LLM server at a time. Stop Ollama before a vLLM run. Hermes background
  tasks on `provider: auto` can load Ollama `qwen3.8:27b`; that plus vllm-hermes3-70b crashed
  the box on 2026-09-29. Only the user can reroute those tasks, so ask before a long vLLM run.
- Two Postgres instances, never confuse them:
  - APP db: `virtualtubers` at 192.168.1.120:5432 (messages, replay_episodes)
  - GENERATOR db: `generation` at 127.0.0.1:5455 (container `virtualtubers-generator-postgres-1`)
- A third one is planned: `character_profile` at 192.168.1.120:**5433**. **Not deployed (connection refused).**
  Its stack is in `deploy/character-profile-db/` (Portainer on mafober CT 101, needs `install.sh`
  on the Proxmox host first; README there). The user does this step.

### 3.4 Character v4 code status
- Present: `app/character/{__init__,avatar}.py`, `sql/001_init.sql` (all tables, incl.
  characters, character_agents, character_backstories(believed/truth), character_baselines,
  week_knowledge_nodes …), `prompts/{fragment,summary_day}.md`, `config/character.yaml`
  (LLM profiles are Ollama-shaped: qwen3.8:27b think off).
- **Missing (never committed anywhere; searched every GitHub/Gitea branch, stash, dangling
  commit and the local disk):** config.py, db.py, store/*, brief.py, llm.py, clock.py,
  ingest.py, recall.py, live.py, fragments.py, summaries.py, testctl.py, compaction.py,
  shapes.py, node_names.py, `services/character-updater/`. The only unchecked place is the
  user's Windows dev PC; asking costs nothing.
- The tests are written and frozen in `tests/character/` (44 files). They use `tests/character/pending.py`:
  a missing target module means SKIP; `CHARACTER_V4_STRICT=1` makes it a failure. Current run: 3 passed, 41 skipped.
- pgserver has **no aarch64 wheel**, so integration tests on this box need
  `CHARACTER_TEST_DSN` (a throwaway local pgvector container works: the
  `deploy/character-profile-db` compose with `CHARACTER_DB_DATA_DIR=<local dir>`).
- Build convention (D-22): tests first, frozen, then code generated by the local model via
  `tools/qwen_worker` (`runner.py run <spec>`, specs in `tools/qwen_worker/specs/*.yaml`);
  the orchestrator reviews and promotes.

### 3.5 Turn protocol code status
None of it exists: no `app/turns.py`, `app/table/`, table message types, character-agent
handler or live roundtable feed.

### 3.6 Arc-plan data (story source, U8)
- Table `generation_artifacts(pack, kind, segment_id, content jsonb, job_id, updated_at)` in
  the GENERATOR db. `pack` is the run id. `kind ∈ {arc_plan, brief, tree, dialogue}`.
- arc_plan segment fields: `id, order, hours, loop, synopsis, ambient_focus, continuity_in,
  continuity_out, carry_in, carry_out, spine_scenes, plot_path, event_windows, fork, mirror_of`.
- tree = `{node_id: {kind: branch|leaf, depth, order, parent_id, children, summary,
  continuity_in, continuity_out, target_slots, slots:[{slot_id, kind, prompt, participants,
  lore, depends_on, sensitivity}]}}`. A **leaf slot ≈ one scene contract** (plan P3.2).
- Candidate runs: D&D `ashiorid_1_20260913_180158_ce8d` (15 segments with brief + tree;
  participants use names like `Leena, Vigil, chadwick`, so normalise the case) and office
  `ashiorid_office_20260929_135927_9c1b` (4 segments). Some runs have an empty arc_plan
  (`segments: []`, e.g. `output/ashiorid/arc_plan.yaml`, `ashiorid_office_20261001_060858_a5f2`).
  Skip those.
- The `ashiorid_1` packs were archived 2026-09-19 (`1d578c3`); the current pack is
  `campaigns/ashiorid`. A fresh plan_arc run on it may be cleaner. That's the open question.

## 4. Spec conflicts already resolved (don't re-litigate; the plan §1 has them)

- agent_dnd §8 Plans A/B/C (one Ollama model per seat) are replaced by one shared vLLM model.
- agent_dnd §6.2 cast-YAML `knowledge` is now authoring input → DB → brief (runtime).
- v4 D-19 single `character-live` process is dropped; the per-container seat handler replaces it.
- v4 D-15 Ollama-only `llm.py` gets a provider seam, vLLM first.
- Seat count comes from the pack (4 / 7), not a fixed 6.
- `present` for future ingest comes from the arbiter's seat list, not the v4 heuristic.

## 5. Next actions (in order)

### Progress 2026-10-08 (session 2, continued)
- DONE P2.4 `scripts/load_profiles.py --pack` + `app/character/generator/pack_profiles.py`;
  P2.7 migration `002_gm_blocks` (+ T04.1-3 relaxed, user-approved), `app/table/gm_context.py`.
- DONE P2.6 as `app/table/seat_brief.py` (**not** `app/character/brief.py`: that is WP-22, the
  full loop brief with frozen tests, Phase 8). Cap: believed text first (D&D believed ~3k chars).
- DONE P3.1 protocol, P3.2 arc_source+contract (ce8d: 96 contracts, 23 prompt-less spine slots,
  2 empty segments -> regenerate before going live), P3.3 commit_check (+narration, +repeats),
  P3.4 `app/turns.py` (on_commit, prior lines to check), P3.5 `agent_handlers/table.py`,
  P3.6 `agent_handlers/table_gm.py`, P3.7 configs (`config/table/ashiorid.yaml` ->
  `scripts/build_table_configs.py` -> `config/workers/table/*.yaml`, overlay mounts).
- DONE P4.1 `app/table/live_feed.py`: `table_line` from the arbiter -> roundtable live spool.
  **P4.2 already existed** (OB-32 LiveDirector, docs/live_pane.md); only the sender rule differs.
- In-process slice `scripts/table_slice_local.py` (real vLLM + character DB + handlers, memory
  bus): 27B-FP8 1 scene 312 s; MoE 2 scenes 176 s. MoE collapsed into verbatim repetition
  (GM copied the player's line) until the repeats rule + presence_penalty 1.0 were added.
- qwen harness speaks OpenAI/vLLM (`QWEN_WORKER_API=openai`); P2.7/P3.5/P3.6 were generated by
  the local model. Lesson: frozen tests that stub the DB/provider paths let broken code pass
  (3 modules had bugs only there); always review + integration-test the stubbed paths.
- NEXT: the real stack (Kafka/Redis/workers) is DOWN (not by us); bringing it up streams to
  Twitch, so ask the user. Needs VLLM_API_KEY + CHARACTER_DB_PASSWORD in the app .env, an image
  rebuild, and a roundtable config with `agent.live.enabled` + `table_arbiter` for the overlay.
- OPEN (user): P1.3 quality scoring / P1.4 model freeze (MoE fast but repetition-prone; 27B-FP8
  richer but D&D round ~3-5 min); D&D profile borderlines; canonical generator run.

### Progress 2026-10-07 (session 2)
- DONE P0.1–P0.5: `vllm-agents` deployment (argyreServer `e1b2ec6`, `802e148`), VLLMClient +
  native reasoning + `reasoning_budget` (`d0e7d91`, `42138dd`), table overlay, runbook with a
  cold-run record (`docs/vllm_agents.md`). `scripts/vllm_mem_watch.py` = 12 GiB watchdog.
- DONE P1.1/P1.2: results table + findings in `.claude/prompts/benchmark_methodology_dnd_agents.md`
  ("W0 on vLLM"). Winner on speed: Qwen3-30B-A3B-Thinking-2507-FP8 with reasoning budgets
  (D&D round 45–58 s). FP8 27B: 187–241 s. BF16: fails. **Always send reasoning budgets.**
- character_profile DB runs LOCALLY (docker compose project `character-profile-db`, :5433,
  data `~/data/character-profile-db`, creds in gitignored `deploy/character-profile-db/.env`).
  Mafober deploy is later (user).
- Frozen tests + harness specs written: P3.1 protocol, P3.3 commit_check (`tests/table/`).
- OPEN for the user: confirm two-pass (data says yes); P1.3 quality scoring; P1.4 freeze;
  frozen T04.1/T04.2 assert migrate()==["001_init"], which conflicts with P2.7's 002 migration.
- Can't stop Ollama without sudo (it idles with no models loaded). P2 code generation via
  the qwen harness (Ollama-only) needs vLLM DOWN first (memory).

### Original order
1. Commit the 4 session files (§3.1) on the current branch; push to `github` and `origin`.
2. Ask the user to (a) deploy `character_profile` on mafober (P2.1) and (b) confirm no
   Hermes/Ollama background jobs will run during vLLM work. Then continue without waiting.
3. **P0.3** vLLM provider in `app/llm_client.py` (port `OpenAICompatClient`; add
   `complete_stream` → `(reasoning, content)`; key via the env-var name in `llm.api_key_env`).
   Tests first: `tests/test_llm_client.py` with a fake SSE server.
4. **P0.4** native-reasoning path in `InstrumentedLLMClient` (throttled `agent_thinking`).
5. **P0.1/P0.2** `vllm-agents` deployment (copy of qwen3.8-27b; add `--enable-prefix-caching`,
   `--max-num-seqs 16`, `--max-model-len 32768`, start `VLLM_GPU_MEM_UTIL=0.55`) + `docs/vllm_agents.md`
   runbook. Bring it up and `curl :8092/v1/models` (with the key header).
6. **P1.1** fix benchmarker `full_round`, add the `table_round` probe (both one-pass and two-pass shapes,
   N=4 and N=7). Run it on the baseline. Report the numbers with the two-pass question.
7. **P1.2** check that the FP8 and MoE candidates exist (`hf` CLI; aarch64 + vLLM 0.30), pull, and benchmark.
8. In parallel with 6–7: **P2.2–P2.4** (character config/db/store/loader against a local throwaway
   pgvector until mafober is up), **P2.5** D&D profiles drafted by local hermes3:70b *on Ollama*.
   Not at the same time as a vLLM run (OOM rule); sequence them.
9. Then P2.6–P2.7, P3 (slice: GM + Chadwick), P4, P5 … per the plan.

Status check-ins: after each phase, and whenever a gate number misses its target.

## 6. Pitfalls already learned

- Thinking models can spend the whole token budget on reasoning and return empty content
  (`finish_reason=length`). Always budget reasoning + content separately and assert content is non-empty.
- Per-item parallel LLM calls can't build a coherent chain. Spoken lines must be sequential
  with the committed transcript passed by value. Only the private THINK pass may run in parallel.
- Enforce invariants in code (silent cast, knowledge scoping, forbidden leaks), never only in prompt wording.
- Docker images lag HEAD. Grep inside the container for the fix before calling a live bug.
- Host LAN 192.168.1.120 and egress can drop together on a cable unplug and self-recover; retry.
- Don't print secrets (vLLM key, DB passwords); refer to env-var names.
