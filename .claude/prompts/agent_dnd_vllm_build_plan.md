# Live Agent Table on vLLM: Build Plan (GM agent + character agents)

> Status: PLAN v1.0, 2026-10-07. Nothing in this plan has been built yet.
> Diagram: `docs/agent_dnd_vllm_plan.png` (renderer: `scripts/render_agent_dnd_vllm_plan.py`).
> Parent design: `.claude/prompts/agent_dnd_architecture.md` (DRAFT v1.0, 2026-09-20).
> This plan amends it; §1 lists every place it does.
> Knowledge design: `docs/charcterProfileGenerationNotes/character_generator_updater_v4.md`
> and `character_v4_decisions.md`.

**Goal:** each character on the stream is its own agent, with its own context
and its own knowledge, running on one shared model served by vLLM. The GM is
one more agent on the same model, with the full world knowledge, and it runs
the table: it narrates, directs, judges replies and keeps the story on its
spine.

**User decisions (2026-10-07):**

| # | Decision |
|---|---|
| U1 | Build on the Ashiorid D&D pack first (GM + 4 players). Keep everything pack-agnostic so `ashiorid_office` (GM + 7 seats) runs on the same code. |
| U2 | Pick the shared model by benchmarking 2–3 candidates on vLLM, FP8 and smaller ones included. Nothing is frozen until that runs. |
| U3 | Knowledge, first build: the minimal v4 subset. That is the per-character brief (identity, believed backstory, objectives, known lore) served from the `character_profile` Postgres. The weekly loop, ingest, summaries and fragments come later (Phase 8). |
| U4 | Each character's own stream shows that agent's reasoning ("inner thoughts"). Reasoning stays ON, and the latency budget has to absorb it. |
| U5 | Latency target: SPEAK ≈ 15 s per line, ≈ 3 min per D&D round. Accepted provisionally; **must be validated by test** (P1.4 + P5.1). |
| U6 | Private intent and reasoning appear ONLY on that seat's own stream: never on the roundtable, never in another seat's context, never in the session record's spoken transcript (audit log only). |
| U7 | The GM gets a **pluggable, extensible profile**: the same profile schema as the players plus GM-only blocks (`truth`, `style`, `table_rules`, …). The GM context builder takes whatever blocks exist; adding a block is data, not code. |
| U8 | The story comes from the **3-layer generator's arc plan** (Layer 1 `arc_plan` + Layer 2 segment `tree`/`brief`), NOT from the hand-authored `campaigns/ashiorid/scenes/`. Layer 3 (offline dialogue) is what the live table replaces. |

---

## 0. What already exists (verified 2026-10-07)

| Piece | Where | State |
|---|---|---|
| Kafka bus `vtuber.messages`, message-api, message-logger | `services/`, `app/message_bus.py` | running |
| Worker containers mapped to seats tuber_0..7 with personas | `docker-compose.office.yml` | working (office show) |
| Handler dispatch, per-role idle hooks | `app/agent_handlers/__init__.py` (`MESSAGE_HANDLERS`, `IDLE_TICK_HOOKS`) | working |
| Per-character tmux layout: radar, knowledge graph, avatar, Thinking, chats, Kafka feed | `config/layouts/tuber_base.yaml`, `app/thinking_pane.py`, `app/knowledge_graph_pane.py` | built |
| `agent_thinking` bus message, shown on the Thinking pane | `app/agent_metrics.py:215` (`InstrumentedLLMClient`) | built, but it parses *prompted* `<thinking>` tags, not native reasoning |
| Per-scene context building (scene lore, rolling transcript) | `app/campaign/improviser.py` (`update_context`, `observe`) | working, but built for one shared brain |
| Silent-cast rule enforced in code | `improviser.py` `_silent_cast_ids` | working; reuse it |
| Roundtable director, tiles, voice gate, relay-file contract | `app/replay_pane.py`, `app/tile_pane.py`, `app/voice_gate.py`, `app/relay_io.py` | working (replay mode) |
| OpenAI-compatible streaming client (Bearer auth, SSE) | `utilities/3LayersWeeklyGeneration/src/concurrent_llm.py:248` (`OpenAICompatClient`) | working against vLLM, but not used by the workers |
| Worker LLM client | `app/llm_client.py` | **Ollama and Claude only.** No vLLM. |
| vLLM deployments | `~/environments/argyreServer/deployments/vllm-{qwen3.8-27b,hermes3-70b,qwen3-coder}` | images built; **all stopped** (hermes3-70b: exit 137, OOM) |
| Benchmark harness with a vLLM adapter | `utilities/benchmarker/` | built. The last run crashed in `full_round`; W0 is still open |
| Character DB schema | `app/character/sql/001_init.sql` | written; **DB not deployed** (192.168.1.120:5433 refuses connections) |
| Character v4 runtime modules (config, db, store, brief, llm, …) | `app/character/*.py` | **not built.** Their tests are written and frozen in `tests/character/` (41 skip as "code pending"). The code isn't on any GitHub or Gitea branch, stash or dangling commit |
| Office profiles with the `agent_dnd §6.2` fields (wants, fears, knowledge, seat) | `campaigns/ashiorid_office/profiles/*.yaml`, `_SCHEMA.md` | authored (8) |
| D&D pack | `campaigns/ashiorid/` (cast: gm, Chadwick, Leena, Sodacan Bob, Vigil; 6 lore files; 109 scenes) | cast files only have `system_prompt`. **No profiles, no believed/truth split** |
| Turn arbiter, table protocol, character-agent handler | `app/turns.py` etc. | **do not exist** |

---

## 1. Changes to the existing specs (flagged; this plan supersedes these lines)

| Spec line | Was | Now | Why |
|---|---|---|---|
| agent_dnd §8 Plans A/B/C | one Ollama model loaded per seat; GM on a bigger model | **one vLLM server, one model.** Every agent (GM included) is a separate *context* sent as its own request to the same server. The GM differs by its context (truth layer, full pack, all sheets) and its authority, not by its weights | U2 plus the user's "multiple agents utilizing a model in vllm". It also avoids the 70b co-residency that OOM-crashed the box |
| agent_dnd §2 / §13 | Ollama `:11434`, `options.think` | vLLM OpenAI v1 at `:8092`, `--reasoning-parser`. The reasoning arrives as a separate `reasoning_content` stream | user's stack |
| agent_dnd §3.1 / §4.4 | 6 character seats | the seat count comes from the pack: D&D = 4, office = 7 speaking + 1 silent observer | U1 |
| agent_dnd §4.3 (committed transcript) | each seat makes one call, strictly in turn | **two passes per round: (a) THINK runs in parallel, (b) SPEAK runs in turn.** After the GM direction commits, every seat gets the same committed transcript and reasons in parallel about its private intent. vLLM batches these calls, and they fill the inner-thoughts stream. Then each seat speaks in turn order with one short call that sees its own intent plus the transcript committed so far, other seats' lines included. **The spoken-line invariant is unchanged**: no line is written without the committed lines before it. Only private, never-committed intent runs in parallel | U4 makes reasoning mandatory; strictly sequential reasoning ×N seats breaks the live budget, and batching only helps calls that run in parallel. **Needs your OK; see Q1** |
| agent_dnd §6.2 | the cast YAML `knowledge:` list is the runtime source | the cast/profile YAML is the **authoring input**. A loader writes it into `character_profile`, and the runtime reads the **brief** from the DB (v4 §8) | there must be one source of truth; the brief is already designed and has frozen tests |
| agent_dnd §6.3 (scene unlocks) | unlocks become part of the arbiter's `state_delta` | same, stored in Redis world-state for the session and added to the brief's "What you know" for later turns. They are persisted to the DB in Phase 8 | knowledge is visible at once, with no nightly lag |
| v4 D-19 (`app/character/live.py`, a single `character-live` container) | one process plays every cast member | **dropped.** The character-agent handler in each worker container plays its seat. The brief and recall code stay as libraries | it conflicts with one agent per container. v4 §13 already listed the `agent.py` character handler as the real target |
| v4 D-15 (`app/character/llm.py`, Ollama only) | Ollama `/api/chat` | provider seam: the vLLM client comes first | the seam was planned in D-15 |
| v4 §8 brief sections 4–5 (week knowledge, feelings) | filled by the loop | empty until Phase 8. Section 4 is replaced by "What you know" (knowledge stems + session unlocks) | U3 |

---

## 2. Target architecture

```
                    one vLLM server  (vllm-agents, :8092, one model, prefix caching)
                         ^      ^       ^       ^       ^
          full context   |      | scoped context (brief + unlocks + committed transcript)
     +-------------------+--+  ++-------++-------++-------++----------+
     | worker-gm (tuber_0)   |  | tuber_1 | tuber_2 | tuber_3 | tuber_4  |  (one character agent each)
     |  GM agent             |  | Thinking pane = reasoning_content stream (agent_thinking)
     |  turn arbiter (FSM)   |  +----+----+----+----+---------+----------+
     +---+-----------+-------+       |  character_reply / think_done
         |           | scene_direction, turn_assignment, retake, overrule
         v           v               v
   Redis world-state     Kafka vtuber.messages (transport + audit only)
   (scene, round,                    |
    turn, unlocks)                   v
         |              roundtable worker: relay file -> tiles + voice gate (the TABLE)
         v
   character_profile Postgres (192.168.1.120:5433): characters, baselines, believed/truth backstory
```

Rules carried over unchanged: the arbiter is the only authority on order;
nothing is committed before validation; agents never message each other
directly; the scene contract is data. Knowledge scoping is enforced when the
context is built, not by prompt wording.

---

## 3. Phases

Each phase lists its work packages (WP), the files they touch and a
"done when". Detailed per-WP TDD specs (test list first, then code) are
written at execution time in the `tools/qwen_worker/specs/` house format
(D-22), and bulk code generation is delegated to the local model. The
orchestrator reviews and promotes.

### Phase 0: vLLM serving + client (prerequisite for everything)

| WP | Deliverable | Files | Done when |
|---|---|---|---|
| P0.1 | `vllm-agents` deployment: a copy of `vllm-qwen3.8-27b`, parameterised by `VLLM_MODEL` / `VLLM_SERVED_NAME`; `--enable-prefix-caching`; `--max-num-seqs 16`; `--max-model-len 32768`; `--gpu-memory-utilization` set from the W0 KV measurement (start at 0.55, which leaves room for `tts-gpu` + workers); API key from env only | `~/environments/argyreServer/deployments/vllm-agents/{docker-compose.yml,.env.example}` | `curl :8092/v1/models` lists the model; `free -g` headroom ≥ 15 GB with the full show stack running |
| P0.2 | Operator runbook: **stop Ollama and every other vLLM before starting** (the OOM history); one-line health check | `docs/vllm_agents.md` | the runbook is followed once from cold, and the output is pasted into the doc |
| P0.3 | `llm.provider: vllm` in the worker client: port `OpenAICompatClient` into `app/llm_client.py` (`VLLMClient`); `complete()` returns content; a new `complete_stream(system, messages, on_reasoning, on_content)` returns `(reasoning, content)`; key read from the env var named by `llm.api_key_env` | `app/llm_client.py`, `tests/test_llm_client.py` | unit tests with a fake SSE server: reasoning and content split, auth header present, the key never appears in logs or repr |
| P0.4 | `InstrumentedLLMClient`: when the wrapped client returns native reasoning, publish it as `agent_thinking` (stream throttled chunks, ≤ 1 msg / 500 ms) and skip the `<thinking>` prompt injection; the Ollama path is unchanged | `app/agent_metrics.py`, `tests/test_agent_metrics.py` | the Thinking pane shows live reasoning from a vLLM call in one worker container |
| P0.5 | Compose wiring: `LLM_PROVIDER`, `LLM_BASE_URL=http://host.docker.internal:8092`, `LLM_MODEL`, `VLLM_API_KEY` passed through for the table workers (overlay `docker-compose.table.yml`, same pattern as `docker-compose.office.yml`) | `docker-compose.table.yml` | `docker compose -f … -f docker-compose.table.yml config` validates; one worker completes a call |

### Phase 1: W0 model benchmark on vLLM (gate; closes agent_dnd §13)

| WP | Deliverable | Done when |
|---|---|---|
| P1.1 | Fix the `full_round` crash in `utilities/benchmarker` and add a `table_round` probe matching §1's two-pass round: GM direction → N parallel THINK calls → N sequential SPEAK calls → GM adjudication, at N=4 and N=7, with real prompt sizes (brief ≤ 6000 chars + transcript) | the probe runs end to end against the current qwen3.8-27b |
| P1.2 | Candidates (check availability with `hf` before pulling; aarch64 + vLLM v0.30 support):<br>1. `Qwen/Qwen3.8-27B` BF16, the baseline you already have (measured ≈ 100 s for a short plan prompt with thinking on)<br>2. an FP8 build of the same model (≈ half the weight bytes, so roughly 2× decode on a bandwidth-bound GB10)<br>3. a small-active-parameter MoE reasoning model in FP8 (e.g. a Qwen3 30B-A3B "thinking" build): far faster decode, at some risk to quality | each candidate has: decode tok/s at 16k ctx, reasoning tokens per THINK, SPEAK latency, `table_round` wall time at N=4/7, KV GB from `vllm:kv_cache_usage_perc` |
| P1.3 | Quality pass: the same 3-scene D&D script on each candidate; you score in-character voice, "yes, and" behaviour and leaks (10-line rubric) | a scored table in `.claude/prompts/benchmark_methodology_dnd_agents.md` |
| P1.4 | Freeze the model, `max_tokens` for THINK/SPEAK, `deadline_s`, and the reasoning budget; update agent_dnd §8 | proposed gate: SPEAK p50 ≤ 15 s, THINK p95 ≤ 60 s, D&D round (N=4) ≤ 3 min, office round (N=7) ≤ 5 min. **You confirm or change these numbers** |

### Phase 2: character knowledge, minimal v4 (can run in parallel with Phase 1)

| WP | Deliverable | Files | Done when |
|---|---|---|---|
| P2.1 | Deploy `character_profile` DB (Portainer stack on mafober, existing `deploy/character-profile-db`; operator step, follow its README / `install.sh`) | — | `pg_isready -h 192.168.1.120 -p 5433` ok |
| P2.2 | `character.config`, `character.db` (numbered migrations + advisory lock) | `app/character/{config,db}.py` | the frozen tests `test_character_config.py` and `test_character_db.py` pass with `CHARACTER_TEST_DSN` set |
| P2.3 | `character.store.characters` (characters, baselines, backstories, `character_agents`) | `app/character/store/characters.py` | `test_character_store_characters.py` passes |
| P2.4 | Profile loader, **pack-agnostic**: reads `campaigns/<pack>/profiles/*.yaml` (schema `_SCHEMA.md`), writes the baseline + `believed` + `truth`; idempotent and versioned | `scripts/load_profiles.py` (generalises the planned `load_office_profiles.py`) | `test_character_load_office_profiles.py` passes, plus a D&D fixture test |
| P2.5 | **Author D&D profiles**: 4 players + GM in `campaigns/ashiorid/profiles/` (same schema as office). Per character: `believed` from what that character could know, `truth` (GM-only) from `lore/*.md` + the scene spine, `knowledge:` stems. Draft with local hermes3:70b; you review | `campaigns/ashiorid/profiles/*.yaml` | 5 profiles load; for each player, a grep shows no `truth`-only stem in its `believed`/`knowledge` |
| P2.6 | `character.brief`, reduced: sections 1 who you are, 2 believed backstory, 3 objectives, 4' **what you know** (knowledge stems' lore text + session unlocks passed in by the caller), 6 behaviour contract (in character, first person, "yes, and" the GM, never speak for another character). Cap `max_chars` 6000 | `app/character/brief.py` | the frozen brief tests that don't need the loop pass (order, cap, never-contains-truth, scrub); the loop-only tests are marked deferred to Phase 8 with a note in `pending.py` |
| P2.7 | GM context builder (U7): GM profile = the player schema + optional GM-only blocks (`truth`, `style`, `table_rules`, `secrets`, …) stored as a JSONB `gm_blocks` column on the baseline (new migration `002_gm_blocks.sql`; never edit 001). The builder renders every present block in config order (`config/table/<pack>.yaml: gm_blocks_order`) + all player sheets + the current arc segment + the scene contract. Adding a block = YAML + config, no code | `app/table/gm_context.py`, `app/character/sql/002_gm_blocks.sql` | a test shows the GM context contains truth stems and an extra custom block; a player brief contains neither |

### Phase 3: turn protocol vertical slice (agent_dnd W1): GM + ONE character

| WP | Deliverable | Files | Done when |
|---|---|---|---|
| P3.1 | Table protocol: message builders + validation for `scene_start, scene_direction, think_request, think_done, turn_assignment, character_reply, retake, gm_overrule, adjudication, scene_resolve, scene_stuck, operator_override` (agent_dnd §4.2 + the two THINK types) | `app/table/protocol.py` | round-trip tests for every type; unknown type rejected |
| P3.2 | Scene contract builder from the **arc plan** (U8): read `generation_artifacts` (generator PG 127.0.0.1:5455, `kind in (arc_plan, brief, tree)`) for a given generator run; walk segment → tree leaf → slot in `order`; one slot = one scene contract: `canon_goal` ← slot `prompt`, `participants` ← seats, `lore` ← GM context + unlock candidates, `continuity_in/out` ← leaf/segment, `must_resolve` ← leaf `continuity_out`. Pack-agnostic; read-only DB access | `app/table/contract.py`, `app/table/arc_source.py` | builds contracts from the D&D run `ashiorid_1_20260913_180158_ce8d` (15 segments) and the office run `ashiorid_office_20260929_135927_9c1b` |
| P3.3 | Commit validator: cheap local checks (speaks for another character, forbidden-leak stems, length, empty/meta), reusing `_silent_cast_ids` | `app/table/commit_check.py` | each rule has a failing-input test |
| P3.4 | **Arbiter FSM** (`LOADED → DIRECTING → THINK → SPEAK(seat…) → ADJUDICATING → RESOLVED`), deadlines, ≤ 2 retakes then `gm_overrule`, state in Redis world-state; the only ordering authority | `app/turns.py` | a fake-clock FSM test covers pass, retake, overrule and timeout; the state survives a worker restart |
| P3.5 | Character-agent handler: on `think_request` → brief + transcript → streamed reasoning (Thinking pane) → `think_done` with private intent (kept in that seat's local state, never on the roundtable). On `turn_assignment` → SPEAK call → `character_reply`. Registered as role `table_seat` | `app/agent_handlers/table.py`, `app/agent_handlers/__init__.py` | a handler test with fakes; the seat never reads the bus for other seats' lines (only `committed_transcript` by value) |
| P3.6 | GM agent: direction call, adjudication call (JSON verdict), overrule line; role `table_gm` idle hook drives the arbiter | `app/agent_handlers/table_gm.py` | — |
| P3.7 | Slice config: `config/table/ashiorid.yaml` (pack, seats → cast ids, turn order), `config/workers/table/*.yaml` | — | **live run:** a 3-beat scene runs GM + Chadwick against vLLM, a retake fires once on purpose, nothing stalls, and the message-logger shows the full turn order |

### Phase 4: the roundtable shows the live table (agent_dnd W2)

| WP | Deliverable | Done when |
|---|---|---|
| P4.1 | The arbiter writes committed lines (direction, replies, overrules, styled differently) to a relay file via `relay_io` (never raw Kafka in the pane) | relay file tests |
| P4.2 | `replay_pane.py` gets a `live` feed source next to the replay cue file; voice gate, tiles and refusal contract unchanged | the slice scene is visible and audible on the roundtable channel |
| P4.3 | Fallback: on `scene_stuck` past a threshold, the roundtable plays a recorded episode | forced-stall test |

### Phase 5: full table (agent_dnd W3)

| WP | Deliverable | Done when |
|---|---|---|
| P5.1 | D&D: GM + 4 seats; `table_round` latency inside the Phase 1 gate on the live stack (TTS + ffmpeg running) | a 4-seat scene airs live; each seat's channel shows its own reasoning, its Inbox and its line |
| P5.2 | Seat-channel panes: reuse `tuber_base.yaml`; the knowledge-graph pane reads that seat's brief stems + unlocks; the Kafka feed shows committed table lines | screenshot check of all seat channels |
| P5.3 | Office: GM (CEO) + 7 seats (the observer is silent, enforced by code) through `config/table/ashiorid_office.yaml`, **no code changes** | the pack-agnostic claim is proven: an office scene runs on the same code |

### Phase 6: knowledge scoping proof (agent_dnd W4)

| WP | Deliverable | Done when |
|---|---|---|
| P6.1 | Reveal → unlock: the GM's `state_delta` lists newly unlocked stems per seat; later turns' briefs carry them | two players with different `knowledge` react differently to the SAME reveal (transcript kept as evidence) |
| P6.2 | Forbidden leak: seed a bad reply → the commit validator retakes it | the audit log shows the retake reason |

### Phase 7: records + operator controls (agent_dnd W5)

| WP | Deliverable | Done when |
|---|---|---|
| P7.1 | At `scene_resolve`, a session record → `replay_episodes` through message-api, with `episode_validator` run at record time | a recorded live scene plays on the legacy replay path |
| P7.2 | `/live/session`, `/live/turns/{id}`, `accept_all`, `skip`, `abort` (message-api → `operator_override`) + control-panel buttons | each control works once, live |

### Phase 8: later (not in this build): the v4 loop

Ingest (`experience_events` from table messages, with `present` taken from the
arbiter's seat list, not the v4 heuristic), nightly summaries → week
knowledge, Sunday reset, fragments + recall, persisting session unlocks to
the DB. Restore the deferred brief tests. Scheduler.

---

## 4. Order and parallelism

```
P0 ──> P1 (benchmark gate) ──────────────┐
  └──> P2 (knowledge; P2.1 + P2.5 need you) ┴──> P3 slice ──> P4 ──> P5 ──> P6 ──> P7
```

Phase 2 doesn't depend on the model choice. Phase 3 can start on the
baseline model and switch models via config after Phase 1 freezes. Only
the deadlines depend on Phase 1.

## 5. Risks

| Risk | Mitigation |
|---|---|
| GB10 OOM (a 70b vLLM next to Ollama already crashed the box) | P0.2 runbook: one LLM server at a time; `gpu-memory-utilization` set from the measured headroom with TTS running; Hermes background tasks set to `provider: auto` can load Ollama models, so the operator has to route those away before a show |
| Reasoning makes turns too slow even with batching | the two-pass round + per-pass token caps; P1 includes an FP8 and a MoE candidate; the arbiter's deadline → overrule means a scene never stalls |
| Small/fast model breaks character or leaks | the knowledge scoping is structural (a player's context never contains truth); the commit validator; the GM retake loop; P1.3 quality scoring |
| D&D profile authoring is the long pole | P2.5 drafted by the local model; you review only the believed/truth split |
| Spec drift between the agent_dnd doc, v4 and this plan | §1 is the reconciliation; both parent docs get a header pointing here when Phase 0 starts |

## 6. Open questions

1. **Two-pass round (§1, row 4): STILL OPEN.** OK to add the parallel THINK pass? Without it, reasoning runs strictly in turn: N seats × reasoning time per round. Working assumption: two-pass; P1.1 measures both shapes so the decision rests on data.
2. ~~Latency numbers~~: answered (U5).
3. ~~Private intent visibility~~: answered (U6).
4. ~~GM profile~~: answered (U7).
5. ~~Story source~~: answered (U8). New sub-question: **which generator run is canonical for D&D?** Candidates: `ashiorid_1_20260913_180158_ce8d` (15 segments, briefs + trees) or a fresh `plan_arc` run on the current `campaigns/ashiorid` pack. Resolve in P3.2.
