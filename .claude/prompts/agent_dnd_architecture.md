# Live Agent D&D — Character Agents + GM Agent on the Bus (Design Spec)

> Status: **DRAFT v1.0 (2026-09-20) — design only. No code changes, no schema changes.**
> Supersedes nothing; it is additive. It deliberately changes what the
> roundtable *is* (live table, not replay device), which partially re-scopes
> `.claude/prompts/roundtable_stream_design.md` §1 ("Content generation
> itself is out of scope here") and `docs/duet_replay.md`. Both remain valid
> as the **offline fallback path** (§10).
>
> Benchmarking of model allocations is **explicitly deferred to a separate
> conversation** — everything that conversation needs is captured in §13 so
> it can resume cold.

---

## 0. Revision notes

v1.0 — initial. All code references verified against the repo on 2026-09-20
(see §2 audit table). Anything here that turns out to misstate existing code
must be corrected against code, not docs.

---

## 1. Concept

Today one brain writes everything. The 3-layer generator
(`services/3layer-generator` → `plan_arc` / `plan_segment` /
`generate_segment_dialogue`) and the single `LLMImproviser`
(`app/campaign/improviser.py:39`) produce every character's dialogue from one
context that sees the whole pack, the whole lore set, and the whole arc carry
state. Characters therefore cannot be **surprised**, cannot hold **secrets**,
cannot misunderstand, and cannot react to information they "shouldn't" have.
That is a real quality defect, not a theatrical one.

This design splits the story into the shape of a real D&D table:

| Role | Knows | What it does |
|---|---|---|
| **GM agent** (tuber_0, one brain) | entire world, cast, arc, secrets, canon contract | narrates, directs beats, adjudicates turns, owns retakes, advances the spine |
| **Character agents** (tuber_1…tuber_6, six brains) | their own sheet + only the lore unlocked for them + committed transcript of the scene | act, speak, "yes, and" the direction, pursue their own instincts |

Information model (extends roundtable spec v1.1 §1):

- **Roundtable channel = the table.** It renders the WORDS on the bus:
  who said what, in round order, with the voice gate arbitrating audio.
  A viewer here gets the full picture — the players at the table.
- **Six character channels = the heads.** Each streams its own agent's
  tmux workspace: the agent's reasoning/thinking pane, its turn
  assignments as they arrive, its committed line after the LLM call.
  This is live, unique, behind-the-scenes content — not a duet-replay
  of the same episode (which is what it is today).
- **Offline generator = the prep.** Arc planning, segment planning, the
  take library, spine chains — all of it survives unchanged. It becomes
  the GM's campaign prep, not the dialogue engine. What goes live is only
  the dialogue layer (today Layer 3).

The roundtable spec's own line — "breadth on the roundtable, depth on the
individual channels" — becomes *literally true*: depth is no longer the
same content with a different tile; it is a different agent's mind.

---

## 2. Current-state audit (code-verified, 2026-09-20)

What already exists and what this design reuses. Verify against code before
trusting anything below the line.

| Existing thing | Where | Reuse decision |
|---|---|---|
| Kafka bus, one topic `vtuber.messages`, message-api is sole publisher from outside, message-logger sole archive | `services/message-api`, `services/message-logger`, `app/message_bus.py` | **Reuse as transport + audit log.** New message types in §4. Bus stays single-topic; ordering lives in the arbiter, not the bus (§4.5 invariants) |
| Per-worker containers, each with tmux workspace, voice config, stream stack | `docker-compose.yml`, `app/agent.py` | **Reuse.** `worker-gm` (tuber_0, display :105) hosts the GM agent + turn arbiter; `tuber_1…tuber_6` each host one character agent. `app/agent.py`'s dev-team role loop stays; the new handlers are added alongside it |
| Voice gate — one voice at a time, seat files, `show.audio` escape hatch | `app/voice_gate.py`, `docs/voice_gate.md`, roundtable spec §16 | **Reuse unchanged.** It already enforces "one speaker, in order" at the audio layer and is exactly the turn property at the rendering layer |
| Director / tile relay / show-log rendering (roundtable) | `app/replay_pane.py`, `app/tile_pane.py`, `.claude/prompts/roundtable_stream_design.md` | **Reuse the rendering + seats.** The feed source changes from a replayed episode to the live bus transcript (§7.1) |
| Bounded re-delegation loop (manager retries ≤ 3) | `app/agent.py:37` (`MAX_BUG_RETRIES`) | **Reuse the PATTERN** as the GM's retake authority (§5.2) |
| Per-scene knowledge scoping: `scene.lore` closed list fed to the prompt; rolling transcript window | `app/campaign/improviser.py:54` (`update_context`), `observe()` | **Reuse the mechanism, re-scope it per character** (§6). Today it scopes one shared brain; it becomes the per-character context builder |
| Cast data: `name`, `archetype`, `system_prompt` per cast member | `app/campaign/pack.py` (`CastMember`), `campaigns/<pack>/cast/*.yaml` | **Extend** with sheet + knowledge fields (§6.2) |
| World-state Redis (flags, log-filter — NOT story state today) | `app/worker_control.py:22-24`, `voice_gate.py:289-291` | **Reuse the channel** (already a named volume shared by all workers) for scene/round state §5.3 |
| Episode store (Postgres `replay_episodes`), upload validator with shape/name/leak-audit/dry-run ladder | `app/episode_store.py`, `app/episode_validator.py` | **Reuse as the record + fallback target.** A resolved live scene writes a session record here (§7.3); the old replay path then plays it back exactly as today |
| Two-tier voice registry + refusal contract (show doesn't air if any voice fails) | `app/voice_registry.py`, `config/voices.yaml`, `docs/voice_gate.md` | **Reuse unchanged** — same speaker/cast machinery, live or replayed |
| Offline 3-layer generator (arc → segment → dialogue) | `services/3layer-generator`, `utilities/3LayersWeeklyGeneration` | **Surfaces change** (§9.1): arc/segment + take library remain the GM's prep and the ambient bank; the live path replaces the improvised layer only when a scene goes live |
| Neutral-take coverage guard (no dead-air guarantee) | `generate_segment_dialogue.py:79` (`assert_neutral_take_coverage`) | **Replaced in live mode** by the stuck-detection + GM over-rule of §9.2 (the live equivalent guarantee) |

Hardware fact (verified 2026-09-20, needed by §13): the box is one
**NVIDIA GB10 with ~128 GB unified memory** (MemTotal ≈ 127.5 GB; CPU
memory and GPU share it, which makes co-resident small models practical).
Ollama is local, `http://localhost:11434`. Roster on disk:
`hermes3:70b` (Q4_0, 40.0 GB), `qwen3.8:27b` (Q4_K_M, 17.7 GB),
`qwen3-coder:30b` (18.6 GB), `gemma4:26b` (18.0 GB), `gemma4:12b`
(7.6 GB), `llama3.1:8b` (4.9 GB), `llama3` (4.7 GB). Currently loaded:
`qwen3.8:27b` (18.5 GB resident). System currently used ≈ 78 GB — this is
the real headroom constraint for §8, and why §13 is deferred and
mandatory before build-out.

---

## 3. Roles and identities

### 3.1 Three identities per seat (extends roundtable spec §2)

Slot (`tuber_N`) stays the stable platform ID. Persona (character name) is
per-show data. New: **agent instance = the LLM context that occupies a seat
for a live show**, bound to a persona and loaded from an Ollama model per
§8.

| Seat | Persona (example) | Agent brain | Knows |
|---|---|---|---|
| `tuber_0` | "The Game Master" | GM agent — `hermes3:70b` | whole pack, all lore, all sheets, the arc spine, the scene contract |
| `tuber_1…tuber_6` | cast names | character agents — `gemma4:12b` (or `qwen3.8:27b` for two leads — §8 Plan A) | own sheet + unlocked lore + committed scene transcript only |

Rule (unchanged from roundtable spec v1.1 §1.1): the GM is one seats like
any other — its own voice, tile, channel, and **own agent**. The GM agent
is the only one that may reference lore it alone holds; that asymmetry is
the entire point.

### 3.2 Where the agents run

- **GM agent + turn arbiter** live in `worker-gm` (tuber_0). The arbiter is
  a plain process inside that container (new module, e.g. `app/turns.py`),
  NOT a new container. It is the single authority on ordering (§4).
- **Character agents** live in their own existing worker containers
  (`worker-tuber_1…6`), as a new agent role added to `app/agent.py`'s
  handler dispatch. Each container already has: tmux workspace (→ its
  channel), voice config, Kafka producer/consumer, display, ffmpeg.

---

## 4. Turn protocol (the core new mechanism)

### 4.1 The state machine

A live scene is a finite state machine run **exclusively by the arbiter in
`worker-gm`**:

```
                 +----------+
 (operator starts)  | LOADED   | scene contract + cast + scoped lore loaded
      v              +----+-----+
                   +----v-----+
        +-------->  | DIRECTING|   GM agent writes the direction (what happens,
        ^            +----+----+   what each character is expected to engage)
        |             v         |   GM line -> bus as scene_direction
       +--------+----+---------v--------+
       |        |  ROUND N  (N = 1..max_rounds)   |
       |   +----+----+   |            +-------+     |
       |   | char_1 turn|  v           |char_6 turn|   each in round order:
       |   +----------+  |            +-----------+   agent gets committed
       |        |        |      commit to transcript when it responds
       |        v        v            v
       |   +----+--------------------------------------------------+
       +--  | ADJUDICATING   |  GM agent reads the 6 replies,       |
            +----+-----------+  checks against scene contract:
                 v                pass -> resolve  |  fail -> retake
            +----+-----------+  (redirect + specific reason, max_retries per char)
            | SCENE_RESOLVED |  commit session record -> episode store
            +---------+------+  state -> LOADED for next scene (or session ends)
```

The GM is the narrator AND the referee: it writes the direction, it judges
the replies, it owns the retake, and if retakes exhaust it **over-rules with a
committed line of its own** so the scene can never stall.

### 4.2 New message types (Kafka, `vtuber.messages`)

All extend the existing envelope `{to, from, type, payload, ts, scene, turn}`.
The `scene` + `turn` ids are assigned by the arbiter so the bus log is
orderable by audit tools even though ordering authority is NOT the bus.

| type | from → to | payload (JSON, exact shape) |
|---|---|---|
| `scene_start` | arbiter → all | `{"scene_id","contract":{...},"round":1,"max_rounds":4,"turn":"gm_direction"}` |
| `scene_direction` | arbiter (GM agent) → all | `{"scene_id","round":0,"speaker":"gm","text":"...","expects":["tuber_1","tuber_2","tuber_3"],"must_resolve":"the vault door opens"}` |
| `turn_assignment` | arbiter → one seat | `{"scene_id","round":N,"seat":"tuber_k","order_pos":i,"committed_transcript":[...],"instruction":"...","deadline_s":45,"max_retries":2}` |
| `character_reply` | seat → arbiter | `{"scene_id","round":N,"seat":"tuber_k","text":"...","took":true,"reason":""}` |
| `retake` | arbiter → one seat | `{"scene_id","round":N,"seat":"tuber_k","reason":"you revealed the vault key; your sheet does not carry it (forbidden_leak)","retry":1,"max":2}` |
| `gm_overrule` | arbiter → all | `{"scene_id","round":N,"seat":"tuber_k","committed_text":"...","note":"auto-overruled after 2 retakes; line is canon, transcript is updated"}` |
| `adjudication` | arbiter (GM agent) → all | `{"scene_id","round":N,"verdict":"pass|reject","notes":"..."}` |
| `scene_resolve` | arbiter → all | `{"scene_id","resolved":true,"state_delta":{...},"record_id":"<episode_store id>"}` |
| `scene_stuck` | arbiter → operator | `{"scene_id","reason":"...","state":{...}}` (dead-air alarm, §9.2) |
| `operator_override` | message-api → arbiter | `{"scene_id","action":"accept_all|skip_scene|abort_session","note":"..."}` |

`operator_override` is the live escape hatch: the retake/abort controls that
the control-panel (§7.4) sends through message-api, which is already the
sole external publisher.

### 4.3 The committed-transcript rule (non-negotiable)

This is the single invariant that prevents the known multi-agent failure mode
(each agent hallucinating the neighbors' turns and consuming each other's
content — the same defect that makes per-item LLM calls incoherent in the
generator pipeline):

> **A character agent's LLM call receives, and is allowed to assume, ONLY:
> (a) its character sheet, (b) its unlocked lore for this scene, (c) the
> scene contract (canon goal — framed as fiction, never "the scene must end
> with X"), (d) the committed transcript: the GM direction and every reply
> already committed in this round, in order. It does NOT see the other
> pending replies. When all six have committed, round N+1 begins.**

`turn_assignment.committed_transcript` is therefore passed by value by the
arbiter (not "go read the bus yourself"). This makes each reply a
sequential, context-grounded call — the exact property the generator work
proved is required, achieved here through an arbiter rather than a loop.

### 4.4 Turn timing

Per turn: `deadline_s` (default 45s) wall-clock, enforced by the arbiter, not
the agent. On expiry: retake (counts against max_retries); on max_retries
exhausted: `gm_overrule`. All six of a round are sequential in turn, so a
round's worst case is bounded: `max_rounds × (6 × deadline_s) + directions`
— with defaults: 4 × (6×45s + 2×2 GM calls) ≈ 18 min hard worst case,
expected ~2–4 min with 12b replies and 70b directions. The benchmark
conversation (§13/§8) must confirm this against measured tok/s before the
values are frozen.

### 4.5 Invariants

1. **One arbiter.** Ordering authority exists in exactly one process
   (`worker-gm`). The bus is at-least-once and out-of-order by nature; it is
   transport + audit, never the source of order. Never encode turn order in
   the bus payload; assign order in the arbiter.
2. **Commit before next.** A reply is "committed" only when the arbiter has
   validated it (see §5.3) and written it to the session log. Until then
   other agents cannot reference it.
3. **No agent talks to an agent directly.** All coordination flows through
   the arbiter; direct seat→seat messages are ignored by design (and rejected
   by the arbiter with a `retake` reason). This keeps the graph a star, not a
   mesh.
4. **The scene contract is data, not prose.** It is the pass/fail object the
   GM's adjudication checks against (§5.3). Free-text "please end on
   something" is explicitly banned from the protocol.

---

## 5. Goals and "yes, and"

### 5.1 Two layers of goal (both required)

- **Character layer (standing, in every character agent's system prompt):**
  "You are <name>. You act from your own sheet: wants, fears, speech,
  relationships. Stay in character. Push the scene forward or complicate it
  with something true to your character. Never break meta. Never speak for
  another character. Follow the GM's direction with 'yes, and' — you commit
  to what the GM describes and add your character's angle to it."
- **GM layer (per scene, in the scene contract):** the canon goal
  (`must_resolve`), the expected beats, and the forbidden leaks. The GM
  agent is told to steer replies toward canon — and it is the **only** agent
  with the authority to say "no, it resolves this way" (§4.1 overrule).

That pairing is what makes "yes, and" work mechanically: the character has
momentum (instinct) and the GM has destination (canon); the bounded retake
loop is the negotiation between them.

### 5.2 The retake pattern (copied from the existing bounded loop)

The `MAX_BUG_RETRIES` pattern in `app/agent.py:37` — bounded re-delegation —
is reused verbatim in shape: reply → GM judges → `retake` with a **specific
reason string** (a model given "again" improves; a model given a specific
reason fixes) → ≤ `max_retries` (default 2) → `gm_overrule` commits a
canon line. The overrule line is **written into the transcript as that
character's committed reply**, so the scene continues with canon intact and
the audit log shows exactly what happened (the "auto-overruled" record is
itself good meta-content for the channel, §7.2).

### 5.3 Scene contract + commit validation (a small validator, not vibes)

```yaml
scene_contract:
  scene_id: ashiorid-009-the-vault
  canon_goal: The party opens the vault door and finds the sigil broken.
  must_resolve:
    - vault_door_opened
    - sigil_broken_revealed
  expected_beats:
    - Leena identifies the lock mechanism (her sheet: locksmith)
    - Vance hesitates about opening a sealed door (his sheet: superstitious)
  forbidden_leaks:
    - tuber_2 must reveal nothing about Malmont's true purpose (lore not unlocked)
  max_rounds: 4
  max_retries_per_turn: 2
```

Commit validation = cheap, local checks (NOT an LLM gate), run by the
arbiter before a reply is committed: (a) no other character's name used in
first-person action ("I said…", "you said…"); (b) no `forbidden_leaks` stem
content quoted verbatim (string check); (c) length/line count within budget;
(d) not empty / not meta. Failures → `retake`. This keeps the hot path fast;
the judgment-quality check ("did it actually hit canon?") is the GM agent's
LlM call at adjudication.

### 5.4 Scene state in Redis (world-state)

The `world-state` channel (already a named volume + Redis URL shared by all
workers, `app/worker_control.py:22-24`) stores: current `scene_id`, round
number, whose turn, committed-transcript pointer, `overrule_count`, and the
per-scene `state_delta` the GM writes at resolve (e.g. `vault_door: opened`).
This is the seed of a **live world state** the GM's later scene contracts
reference — the offline equivalent is the arc `carry` in the pack.

---

## 6. Knowledge scoping (the actual quality win)

### 6.1 The mechanism already exists — re-scope it per character

`LLMImproviser.update_context()` (`app/campaign/improviser.py:54`) already
scopes a scene by a closed `scene.lore` list and feeds a per-scene rolling
transcript (`observe()`, `app/campaign/improviser.py:63`). Today it's applied
to one shared brain. In this design it becomes the **per-character context
builder**: one call per character-agent LLM call, with that character's
scoped context. This is the 80% of the quality difference (agents that
genuinely don't know what others don't) and costs a context-builder, not new
infra — the same `Vocabulary`/`pack` loading code path the generator already
runs (`campaign.pack.load_pack`, `vocabulary.Vocabulary.from_config_and_pack`).

### 6.2 Cast sheet — `cast/<id>.yaml` fields (extension, not replacement)

```yaml
id: leena
name: Leena
archetype: veteran locksmith, pragmatic
# --- existing ---
system_prompt: >
  Spoke to people like a door. Short sentences. Never wastes a word.
# --- NEW (v1.0 design) ---
agent_model: gemma4:12b        # resolved via §8 allocation; overridable per show
wants: [get her guild contract paid, prove she's not a thief]
fears: [being locked out, owing a debt she can't name]
speech: [short, clipped, first-person, never compliments]
relationships:
  - {who: vance, attitude: "trusts him less than she trusts the lockpick"}
knowledge:                     # closed list of lore stems UNLOCKED for this character, this scene
  - lock-mechanics
  - malmont-tavern
  # NOT: malmont-true-purpose, malvakar-seal-origin
turn_order_pos: 2              # position within a round (per show; see §4.2 turn_assignment)
```

The `knowledge` list is the per-character `scene.lore` equivalent, and the
arbiter enforces it: a `turn_assignment` built for `tuber_2` carries ONLY
`leena.knowledge` stems' lore text, plus the committed transcript (4.3). The
GM agent's context carries the full pack, all sheets, and the full `carry`.

### 6.3 Unlocks and reveals (the "GM leaks the plot" moment)

The arbiter tracks which `knowledge` stems are unlocked per character. When
the GM's direction commits a reveal (e.g. "Malmont steps out and says…"), the
`state_delta` at resolve lists newly-unlocked stems per seat; the next scene's
`turn_assignment` for that seat carries them. A character discovering
information it didn't have — and reacting to it "live" — is content that is
**impossible to generate well offline** and is exactly the content this
design is for.

---

## 7. Streaming model change (roundtable + six channels)

### 7.1 Roundtable becomes the live table

Currently: `worker-gm` renders a **replayed episode** (tiles + shared
show-log, `app/replay_pane.py` / `app/tile_pane.py`), cue-driven, with the
voice gate arbitrating the shared `vout` sink. The show-log IS the committed
transcript's rendering.

Design: the show-log feed source changes from the replay cue file to the
**live Kafka transcript** (`scene_direction`, `character_reply` in commit
order), with the existing voice gate and tile relay unchanged. Concretely:
the director process (`replay_pane.py` as today) reads the arbiter's commit
stream (via relay file, same convention — never raw Kafka polling — to keep
the `TILE_RELAY_DIR` local-file contract that the tile fan-out was built on,
roundtable spec §"tiles are driven by local relay FILES, never Kafka") and
hands lines to the tiles + voice path. The `cast`/voice registry/refusal
contract machinery is identical (§4.2 seat ids are the cast keys).

Fallback: if the live feed is down or stuck for `scene_stuck` threshold, the
roundtable falls back to the **recorded episode path** (today's behavior) for
that slot — the two paths are mutually exclusive per session and share all
rendering code.

### 7.2 Character channels show the agent's head

Each `worker-tuber_N` runs its character agent in tmux (new pane set:
**Sheet** (character sheet + unlocked lore, read-only), **Inbox** (turn
assignments + committed transcript, live-scrolling as the arbiter commits),
**Reasoning** (the LLM's visible thinking / draft lines, if the model +
provider is configured to stream reasoning; otherwise: the draft-line
before commit + the final committed line diff), **Tool** (the agent's
local scratch — its "dice roll" equivalent, e.g. the arbiter's
commit-validation result for that seat). This is the "behind the scenes"
content and it's live and unique per seat, not a duet replay of shared
content. The existing ffmpeg/X/Tmux stack per container is unchanged;
only the pane set (new `config/layouts/character_agent.yaml`, one per role
family) and the agent's tmux usage (`app/agent.py` already drives tmux via
`app/tmux_control.py`) change.

The GM channel (tuber_0) additionally shows: the **arbiter's state panel**
(round N/M, whose turn, turn deadline countdown, overrule counter) — the
"referee's table" the other seats' channels are missing.

### 7.3 Session → record → episode (reuses the store)

At `scene_resolve`, the arbiter assembles the canonical session record
(scene, direction, turns in order, overrules, `state_delta`) and writes it
through message-api (sole writer path, `app/episode_store.py:55`) to
`replay_episodes`. The existing upload validator
(`app/episode_validator.py` — shape, name, leak-audit, dry-run) is applied at
record time, not just at hand-upload time. From that point on, the recorded
scene is airable on the legacy replay path exactly like any hand-authored
episode: the old stack is the fallback and the archive, both.

### 7.4 Operator surface (control-panel / message-api)

New endpoints (thin, delegate to the arbiter via `operator_override`):

- `GET  /live/session` — current scene, round, turn, committed transcript.
- `GET  /live/turns/{scene_id}` — full turn log incl. overrules.
- `POST /live/session/accept_all` — GM accepts all pending replies (skips
  adjudication retakes; useful for pacing).
- `POST /live/session/skip` — resolve early with the last valid state.
- `POST /live/session/abort` — stop the session, keep the record.

These mirror the existing `replay_invite`/`replay_stop` pattern
(`services/message-api`, `app/agent.py:955-1049`) and are gated the same
way (message-api is the only external publisher — preserved).

### 7.5 Voice + audio

No change to the voice gate, the per-slot voice registry
(`config/voices.yaml`, `app/voice_registry.py`), or the refusal contract.
The voice gate's "one line at a time" is precisely the audio-layer form of
§4's turn order (which is why the two agree: the arbiter commits in order,
the voice gate plays in order). `show.audio.max_concurrent` remains the
escape hatch for deliberate overlap on the roundtable.

---

## 8. VRAM / model allocation (provisional, benchmark pending)

Budget reality (verified 2026-09-20): one GB10, ~128 GB unified memory,
~78 GB currently used system-wide → **model headroom must be measured, not
assumed** (see §13). The GB10's unified memory is what makes all three
plans feasible on one card; this is NOT a discrete multi-GPU story.

| Plan | Allocation | Model GB | Notes |
|---|---|---|---|
| **A — tiered (recommended start)** | GM: `hermes3:70b` (40.0) · 2 leads: `qwen3.8:27b`×2 (35.4) · 4 support: `gemma4:12b`×4 (30.4) | **105.8** | "GM is smartest, leads mid, rest lean" dynamic. Headroom tight once system + KV cache are counted — the §13 benchmark must confirm it, else drop to B |
| **B — balanced (safest)** | GM: `hermes3:70b` (40.0) · 6: `gemma4:12b`×6 (45.6) | **85.6** | Max headroom, uniform character quality (fine by design: characters SHOULD be the weaker brains) |
| **C — everyone top, swap per turn** | GM: 70b, all seats: 70b; sequential turns mean ~1 resident at a time, swap on turn handoff | **40.0** | Uniform top-brain, swap latency per line + no "GM smarter" tier. Fallback if A/B both under-perform on character quality |

Tuning knobs the benchmarks must measure before any plan is frozen:
per-model decode tok/s at the **real** context (8k–16k, not the 262k spec),
KV-cache GB at that context (the quiet killer of Plan A's headroom),
latency-per-character-line, and **time-per-full-round** (the real "is this
live?" number). §13 is the deferral; this table is the decision to be
made **after** that benchmark, recorded here so it isn't lost.

---

## 9. What changes, what survives, what's replaced

### 9.1 Survives (unchanged)

- Kafka bus, single topic, message-api sole-publisher, message-logger archive.
- Offline generator: **arc planning**, **segment planning**, the take
  library, spine chains, ambient bank — becomes the GM's prep and the
  fallback bank (offline path is the fallback, not deleted).
- Voice gate, voice registry, refusal contract, per-slot voice mapping.
- Tile rendering, relay-file convention, `config/layouts/roundtable.yaml`.
- Episode store + upload validator (applies NOW to live records too).
- `app/agent.py` dev-team role loop (the new agent role is added alongside,
  not replacing).

### 9.2 Replaced / superseded in live mode

- The single-brain improvised layer (today `Layer 3` dialogue) → the live
  turn loop (§4). The improvised path remains for offline/batch and fallback.
- `assert_neutral_take_coverage` (no-dead-air guarantee, offline) → **stuck
  detection + GM over-rule** (live): per-turn `deadline_s`, `max_retries`,
  then `gm_overrule` commits a canon line so the scene never stalls
  (§4.2/§5.2). The `scene_stuck` alarm is the operator-level dead-air guard.
- Roundtable as replay device → roundtable as live table (with replay as
  fallback), §7.1.

### 9.3 New artifacts

- `app/turns.py` (arbiter FSM, §4) — the single new authority module.
- `scene_contract` (data, §5.3) + per-seat `turn_assignment` builder.
- Per-character context builder reusing `campaign.pack` + `vocabulary`
  (§6.1).
- Extended `cast/<id>.yaml` schema (§6.2).
- `config/layouts/character_agent.yaml` (per-seat agent workspace, §7.2).
- Operator live endpoints (§7.4).

---

## 10. Build order (vertical slice first — never build the 6-player version blind)

| WP | Deliverable | Done-when |
|---|---|---|
| **W0 (gate)** | §13 benchmark run → allocation decision (A/B/C) + latency budget | measured tok/s, line latency, round time, KV GB at 8k/16k context; plan chosen |
| **W1** | Turn protocol vertical slice: GM agent + ONE character agent, scripted 3-beat contract in `worker-gm`, `app/turns.py` state machine, `turn_assignment`/`character_reply`/`retake`/`gm_overrule` over Kafka, committed-transcript rule verified | a 3-beat scene runs live, retake fires at least once on purpose, no stall, audit log shows the full turn order |
| **W2** | Roundtable renders the live transcript (show-log feed → commit stream, relay-file convention preserved); voice gate + refusal path unchanged | the live scene is audible + visible on the roundtable channel; fallback to a recorded episode works |
| **W3** | Scale to full party (6 character agents) on the §8 allocation; turn-order table + `turn_order_pos` live | a 6-seat scene runs under the latency budget; per-seat character channels show the live workspace |
| **W4** | Full knowledge scoping (cast sheets §6.2, per-seat `knowledge` lists, unlock/reveal §6.3) on a real pack scene | two characters with different `knowledge` lists react to the SAME reveal differently; forbidden-leak check fires on a seeded bad reply |
| **W5** | Session record → episode store + validator at record time; operator live endpoints (§7.4); session-records fall back to the legacy replay path | an operator can accept_all/skip/abort; the recorded scene plays in the old replay path exactly like a hand-authored episode |

---

## 11. Open questions (deferred, to be resolved before W0 closes)

1. **Reasoning visibility (§7.2):** which of `hermes3:70b` / `qwen3.8:27b` /
   `gemma4:12b` expose a visible thinking/reasoning stream through Ollama?
   If none, the "Reasoning" pane falls back to draft-line + committed diff.
   (Affects how "behind the scenes" reads on stream.)
2. **Turn order per show:** `turn_order_pos` is per-show data (§6.2) — is the
   round-table's spoken round order fixed per pack, per show, or GM-chosen
   per scene? (Protocol supports any; decide the default.)
3. **Overrule visibility:** is the `gm_overrule` "auto-overruled" line
   rendered with a distinct style on the roundtable (recommended, good
   meta-content) or invisibly committed? (Leans distinct.)
4. **Operator mid-turn abort:** does `POST /live/session/abort` commit a
   partial scene record or discard it? (Protocol is silent; decide before W5.)

---

## 12. Trust and security notes

- The arbiter holds the committed transcript in `worker-gm` (privileged) and
  passes scoped copies to seats — knowledge scoping is enforced by
  construction (§4.3, §6.1), not by prompt-phrase.
- `operator_override` flows through message-api (sole external publisher) so
  the existing external-publisher boundary is preserved; seats never take
  operator input directly.
- No seat gets write access to host campaign files — all reads go through
  `campaign.pack.load_pack` / the Postgres materialized pack (the
  `load_pack_for_job` seam in `services/3layer-generator/runner.py:85`),
  preserving the existing no-rw-mount rule.
- Leaks: the existing upload validator's leak-audit runs at record time
  (§7.3); the per-seat `forbidden_leaks` check runs at commit time (§5.3).

---

## 13. Benchmark deferral (resumable cold, separate conversation)

Everything the benchmark conversation needs, captured here so it doesn't
depend on this doc being open in the same session.

**Hardware (verified 2026-09-20):** one NVIDIA GB10, ~128 GB **unified**
memory (system `MemTotal` ≈ 127.5 GB; `free -h` ≈ 78 GB used / 42 GB
available at capture time — re-measure, it moves). Ollama at
`http://localhost:11434`. GPU util 94% at capture (a job was running —
benchmark must run with that job quiesced).

**Available models (name, quant, size):** `hermes3:70b` (Q4_0, 40.0 GB),
`qwen3.8:27b` (Q4_K_M, 17.7 GB), `qwen3-coder:30b` (18.6 GB),
`gemma4:26b` (18.0 GB), `gemma4:12b` (Q4_K_M, 7.6 GB), `llama3.1:8b`
(4.9 GB), `llama3` (4.7 GB).

**Measure (per model, then per plan):**
1. prefill tok/s and decode tok/s **at the context we'll actually use**
   (8k and 16k via `num_ctx`, not the advertised 262k), streaming.
2. latency-to-complete a ~45-word in-character line (the "feels live" unit).
3. **time-per-full-round**: GM direction (70b) + 6 character replies in turn
   (per the plan's per-seat model) + GM adjudication (70b), using real
   prompt sizes (system + scene lore + committed transcript).
4. **concurrency** (A vs C): 2–3 simultaneous `complete` calls (70b + 1–2
   smalls) — on unified CPU+GPU memory this is where KV-cache pressure and
   CPU offload bite; serial-vs-parallel wall time decides if co-resident
   smalls are fast or resident-in-name.
5. **KV-cache GB** per model at 16k context (subtracts from the §8 budget —
   the thing that silently kills Plan A if ignored).

**Produce:** a table (models × {ctx, prefill, decode, line-latency, KV-GB}), a
time-per-round per plan, and a one-line recommendation among A / B / C.
Then update §8's allocation to the measured choice and close W0.

**Constraint from §8:** model budget must fit within (128 GB unified − system
residue − per-model KV at 16k × number of co-resident models), and the
time-per-round must stay under the §4.4 worst case. The benchmark exists to
pick A/B/C and to set the `num_ctx` / per-turn `deadline_s` values that
§4.2/§4.4 marked "frozen after benchmark."

---

## Appendix — files this design touches (for the implementer)

Add / extend (do not modify offline generator's Layer 1/2):
- `app/turns.py` (new — arbiter FSM, §4)
- `app/agent.py` (new agent role + handlers for the §4.2 message types; §5.2
  reuses the `MAX_BUG_RETRIES` bounded-loop shape at `agent.py:37`)
- `app/campaign/improviser.py` (extract the per-scene context builder for
  per-character reuse, §6.1 — do not change the offline call sites)
- `app/campaign/pack.py` (`CastMember` schema extension, §6.2)
- `config/layouts/character_agent.yaml` (new, §7.2)
- `services/message-api` (§7.4 live endpoints)
- `app/episode_validator.py` (apply at record time, §7.3)
- `services/message-logger` (already archives; confirm it ingests the new
  message types without filter changes)

Unchanged by design (§9.1): `app/voice_gate.py`, `app/voice_registry.py`,
`app/replay_pane.py` (feed source swap is additive, §7.1),
`config/voices.yaml`, `config/layouts/roundtable.yaml`, the offline
generator's `plan_arc` / `plan_segment`.
