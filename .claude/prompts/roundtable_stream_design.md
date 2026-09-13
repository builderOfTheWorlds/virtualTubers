# Roundtable Stream — Design Spec

> Status: **DRAFT v1.1 (2026-09-12) — revised after v1.0 review. No schema or code changes yet.**
> Supersedes v1.0. Changes are listed in §0 Revision Notes; every one of them
> came from a review finding where v1.0 asserted something about the existing
> code that turned out not to be true.
> Companion specs: `ring_composition_spec.md` (story structure), `docs/duet_replay.md`
> (the sync protocol this reuses), `docs/layout_system.md` (the layout engine this
> builds on).
>
> This doc is design-only. After it is approved, the follow-up work is a
> per-module implementation plan in the same directory, one file per buildable
> component (see §11 Work Packages).

---

## 0. Revision notes (v1.0 → v1.1)

Seven findings from the v1.0 review. Four were false claims about existing code;
three were design gaps. Two of the fixes made the build *smaller*, not bigger.

| # | v1.0 said | Reality | v1.1 does |
|---|---|---|---|
| **B1** | "`episode_validator`'s dry-run render already catches any missing-voice failure at upload time" | **False.** `_dry_run` (episode_validator.py:112-132) calls `Performer.perform(script)` with `show=None` and `plan_scenes` — it never imports `tts_client` and never synthesizes. Worse: message-api (docker-compose.yml:317-334) has **no `/data/voices` mount**, so it *cannot* check voice files even if it wanted to | Replaces the single fictional check with a **4-tier validation ladder** (§8), split by where each capability actually lives |
| **B2** | show header carried `"voice": "en_US-kathleen-low"` | Unconsumable. `voice_for` (tts_client.py:250-255) merges `config.speakers[speaker]`, and the real override shape is `model_path: /data/voices/….onnx` (`_piper_local` stats that path). No name→path layer exists anywhere | Adds a **symbolic voice registry** (§7.2), `config/voices.yaml`. Also the enabler for B1 |
| **B3** | N-2 "config-only, no `startup.sh` or `build_layout.py` changes" | Half-true. The layout *engine* needs no change (verified: `resolve_pane` handles arbitrary `use:`, `with:` merges, per-`id` index tracking works). But a panel type is a yaml file **plus a pane program**, and the programs don't exist | N-2 reworded honestly: engine unchanged, one new pane program required (§3) |
| **B4** | §2.1 mapping table | Mechanically broken: `tuber_4` assigned twice, `coder-aider` appeared twice, `tuber_6` appeared twice, 7 rows for 6 workers | Regenerated clean 1:1 (§2.1). Development mode — drastic changes are fine |
| **G1** | No pane for the director process | Real fork in the design | **Resolved:** GM is a 7th character with its own tile. And the director's own `Performer` output **becomes** the show log (§6.1) — this *deletes* the local-JSONL relay and the `tail_bus.py` sibling from v1.0's N-3/WP-4 |
| **G2** | "Rendering is the existing follower path… same as the existing avatar panel" | `avatar.py` resolves its state path from `AGENT_STATE_FILE` env / config / a fixed default (agent_state.py:16-18) and has only `--config` (avatar.py:68) — six instances would render identical faces | Accepted as **MVP placeholder debt** (§5.1). Tiles get a per-tile state path; robust/3D avatars are a separate future track |
| **G3** | "all 6 avatars stay legible at 1080p" | Unsupported by the row budget, and `config/layouts/replay.yaml` carries a comment about this exact trap already burning the project once | Accepted as placeholder debt (§5.1) with an explicit pre-WP-4 geometry spike (§8.5) |

Two v1.0 claims the review **confirmed as correct** and this version keeps
unchanged: the §7.4 backwards-compat contract (`_check_shape` checks only
REQUIRED_KEYS presence and per-event fields, with no unknown-key rejection, so an
optional `show` block passes untouched), and the §14 mid-show-failure row
(`on_scene_start` is fire-and-forget with no ack, so the show genuinely does
continue).

---

## 1. Concept

Seven Twitch channels, one shared performance:

- **One roundtable channel** — hosted by the `tuber_0` (GM) container. It shows a
  dedicated tile per character slot — **including `tuber_0`'s own** — plus a
  shared show log recording all dialogue and actions in order. This is the "main"
  stream: a viewer who only watches this one gets the **full picture and the full
  sound**, because it is the one channel that plays *every* character's voice.
- **Six character channels** — the existing six workers (`tuber_1`…`tuber_6`).
  During a roundtable show each one performs **duet mode** (existing, proven): it
  plays audio only for its own lines, renders the full visuals for every scene, and
  additionally keeps its private-detail panes (filetree, editor/tool output, htop).
  A viewer who only watches one character gets the **full depth** for that
  character, at the cost of the other voices (which are silent on that channel).

Information model: **breadth on the roundtable, depth on the individual channels.**
Some dialogue text appears in more than one place (the character tile AND the
shared log on the roundtable) — that duplication is deliberate and called out in the
§6 layout spec.

The roundtable stream is a platform feature: it broadcasts whatever show the
content generation engine produces. Content generation itself is out of scope here;
this spec covers slots, personas, the 7th channel, the roundtable UI, the show
pipeline, the validation ladder, and the naming migration.

Viewer chat voting, real-time LLM improvisation, frame-accurate multi-channel sync,
and browser/web UI overlays are **explicitly deferred** (§12) — not designed, and
the spec must not paint them in accidentally.

### 1.1 The GM is a character (v1.1 decision)

v1.0 left the GM's on-screen representation open (OPEN-1) and had no home for the
director process (G1). Both are now settled by one rule:

> **`tuber_0` is a character slot like any other.** It has a tile, a voice, an
> avatar, and lines. Its tile uses the *same* tile panel type and the *same*
> render path as `tuber_1`…`tuber_6`, differing only by configuration.

The payoff is maintenance: a bug in tile rendering is fixed once, for all seven.
A per-tile `with:` block is the only thing that distinguishes them (§5), so the
GM can be visually marked as the narrator (border colour, title, a `narrator:
true` style flag) without forking the code path.

The director *process* is a separate concern from the GM *character*, and §6.1
gives it a home that costs no new code.

## 2. Slots vs personas vs workers — the naming model

This is the sharp edge of the redesign. Three distinct concepts that the first
draft of the project conflated:

| Concept | What it is | Who owns it | Example |
|---|---|---|---|
| **Slot** (`tuber_N`) | A stable platform identity: one broadcast channel, one tile position, one bus worker id, one voice slot | The platform, permanent | `tuber_2` |
| **Persona** (character name) | A story role that *fills* a slot for one generated show; a per-show binding of slot → character name → voice → avatar | The generated story (show header) | "Alcinoe" in show X, "Vance" in show Y |
| **Worker** (old) | The 1st-draft concept where `coder`/`manager`/`tester`/… were both the channel identity AND the persona identity | — (being retired) | `manager` = MAX-1 forever |

Rules:

1. **Slots are the stable IDs everywhere platform code speaks of a channel or a
   speaker**: bus `worker_id`s, `cast` keys, `voice.speakers` keys, layout tile ids,
   compose service names, config file names (`config/workers/tuber_0.yaml` …
   `tuber_6.yaml`).
2. **Personas are data on the show**, not platform config. A generated show carries
   a **show header** (§7.1) binding each cast slot to its character name and voice.
   Character names come from the story; they never appear in platform code, layout
   names, or channel names.
3. **`tuber_0` is the GM slot**: it hosts the roundtable channel and acts as
   game-master / director of every roundtable show. It is a slot like any other
   (bus id, voice slot, config file, **and a tile** — §1.1); its *channel* is the
   roundtable rather than a personal-character stream.
4. **Roster**: `tuber_0` (GM) + `tuber_1`…`tuber_6` (character channels) = **7
   channels total**, matching the existing 6 workers plus the new 7th GM container.
   The roundtable renders **7 tiles**.
5. **Recorded-session replays** (the `boss`/`coder` 2-speaker real-session format
   from `session_log_parser`) are **out of this rename's scope** — that path must
   keep working unchanged. The rename applies to the generated-show/slot namespace.
6. **Scope of rule 2** *(new in v1.1 — review finding C3)*: "character names never
   appear in platform code" governs the **generated-show namespace only**. The live
   agents keep their own identities — `config/workers/manager.yaml` carries
   `agent.name: "MAX-1"`, `avatar.name: "MAX-1"`, and a system prompt that says
   "You are MAX-1", and the dev-team handoff mode (kept, OPEN-6) broadcasts that.
   Those are *agent* personas, not *show* personas. The §13 verification grep is
   scoped accordingly.

### 2.1 Current → target identity mapping

*Regenerated in v1.1 — v1.0's table had three collisions (B4).* Six existing
workers, six slots, one new container. Clean 1:1:

| Old worker id (bus) | Old persona | New slot | Channel | DISPLAY_NUM |
|---|---|---|---|---|
| `coder` | KODI-7 | `tuber_1` | existing, retargeted | 99 |
| `coder-native` | NYX-1 | `tuber_2` | existing, retargeted | 102 |
| `coder-opencode` | OKO-2 | `tuber_3` | existing, retargeted | 103 |
| `coder-aider` | ADA-3 | `tuber_4` | existing, retargeted | 104 |
| `tester` | TESS-3 | `tuber_5` | existing, retargeted | 101 |
| `manager` | MAX-1 | `tuber_6` | existing, retargeted | 100 |
| — (new container) | — | `tuber_0` | **roundtable (NEW)** | **105** |

`DISPLAY_NUM` is carried in the table because 99-104 are already taken
(docker-compose.yml) and the GM needs a free one — 105.

The mapping is a planning artifact only; after migration nothing references the
old names.

### 2.2 Migration plan (identity rename)

Executed as one work package (**WP-7**, §11) **after** the roundtable feature is
verified, so the rename never breaks a working show:

1. `config/workers/tuber_N.yaml` replaces `config/workers/<role>.yaml`;
   `voice.speakers` keys become `tuber_1`…`tuber_6` (plus `tuber_0`) on **every**
   worker config; `speaker_names` display names become slot-neutral defaults
   (real names come from show headers).
2. `docker-compose.yml`: service names `worker-<role>` → `worker-tuber<N>`,
   `WORKER_ID` env values updated, compose env key names
   (`CODER_STREAM_KEY` → `TUBER1_STREAM_KEY`, `CODER_LAYOUT_PRESET` →
   `TUBER1_LAYOUT_PRESET`, etc.). `.env` / `.env.example` updated in the same pass.
3. `app/agent.py` role gates: the role-gated handlers branch on `role`
   (`handle_commit_notification` → tester, `handle_bug_report` /
   `handle_test_passed` / `handle_task_complete` / `handle_clarification_request`
   → manager). **Decision: (a)** — keep role-gating, read `role` from the worker
   config (`role: manager|coder|tester|roundtable`), so the handoff protocol keeps
   working under the new names with no code change. The roundtable adds a new
   format on top rather than replacing it. (OPEN-6 resolved.)
4. Existing duet examples (`docs/duet_replay.md`,
   `scripts/send_test_message.ps1`) updated to the new slot ids.
5. The orphan `meta/cast/scenes/render` fixtures
   (`replays/coder/test_worker_roundtable.json`, `replays/coder/sample.json`)
   are **deleted** — 1st-draft test fixtures whose schema no code reads and whose
   concept is fully expressed by §5/§6/§7 (§7.3).

## 3. Architecture overview

```
                          ┌────────────────────────────────────────────┐
                          │              message-api (FastAPI)         │
   operator / scripts ───▶│  POST /messages  ·  POST /replays  ·  GET  │
                          └───────────────────────┬────────────────────┘
                                                  │ Kafka (vtuber.messages)
   ┌──────────────────────────────────────────────▼──────────────────────────────┐
   │                                 Kafka broker                                │
   └──┬──────────┬──────────┬──────────┬──────────┬──────────┬───────────────────┘
      ▼          ▼          ▼          ▼          ▼          ▼
   ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌──────────────────────┐
   │tuber_1 │ │tuber_2 │ │tuber_3 │ │tuber_4 │ │tuber_5 │ │tuber_0 (GM/roundtable)│
   │tuber_6 │ │ agent  │ │ agent  │ │ agent  │ │ agent  │ │ agent                 │
   │ agent  │ │        │ │        │ │        │ │        │ │ replay_pane = DIRECTOR│
   │replay_ │ │replay_ │ │replay_ │ │replay_ │ │replay_ │ │   └ its stdout IS the │
   │pane    │ │pane    │ │pane    │ │pane    │ │pane    │ │      show_log  (§6.1) │
   │(duet   │ │(duet   │ │(duet   │ │(duet   │ │(duet   │ │ tile × 7 (incl. GM)   │
   │follow) │ │follow) │ │follow) │ │follow) │ │follow) │ │ htop                  │
   │Twitch  │ │Twitch  │ │Twitch  │ │Twitch  │ │Twitch  │ │Twitch (roundtable,NEW)│
   └────────┘ └────────┘ └────────┘ └────────┘ └────────┘ └──────────────────────┘
        │          │          │          │          │
        ▼          ▼          ▼          ▼          ▼
   6 × existing Twitch channels              1 × new roundtable Twitch channel

   Shared state:
     Postgres (192.168.1.120:5432):
       replay_episodes  — the show (script + events + speaker overrides + show header)
       voiced_narration — persisted "airing" (per-scene TTS) shared by director + followers
       messages         — the bus log

     Repo config (read-only, mounted into workers AND message-api):
       config/voices.yaml — the symbolic voice registry (§7.2)

     Per-show: the director (tuber_0) publishes replay_invite → every cast slot
     replies replay_ready → director cues scene-by-scene (replay_cue ratchet) →
     replay_end. All 7 channels perform the SAME scenes in lockstep (existing
     protocol, docs/duet_replay.md).
```

What's **new** vs what's **reused**:

- **N-1**: `tuber_0` (GM) — a 7th container service, same worker image, new config
  + layout preset + its own Twitch stream key (user-supplied; §9 config keys).
- **N-2** *(reworded in v1.1 — B3)*: Roundtable layout preset + one new panel type
  (`tile`). The **layout engine needs no change** — verified: `resolve_pane`
  (build_layout.py:146-190) handles an arbitrary `use:` value, the `with:` block
  deep-merges, and the `index_of` tracking assigns a distinct tmux index per
  distinct `id:`. But a panel type is *a yaml file plus a pane program*, and
  `app/tile_pane.py` is new code (WP-3). No `startup.sh` change, no
  `build_layout.py` change; one new program. The platform was built to accept new
  panels — this is that mechanism being used as intended.
- **N-3** *(shrunk in v1.1 — G1)*: A "local tile performer" inside `tuber_0`.
  Reuses the existing follower rendering path (`Performer` +
  `_rebuild_scenes_from_rows`) driven from a **local relay file** — the same
  `REPLAY_CUE_FILE` polling pattern `perform_follower_request`'s `wait_for_scene`
  (replay_pane.py:690-716) already uses — instead of from Kafka. One code path,
  two uses: external follower (over bus) and local tile (over relay). No new bus
  message types. **The show-log half of v1.0's N-3 is gone** — see §6.1.
- **N-4**: Show header (per-show persona→slot→voice binding) — a new optional field
  on the episode object in `replay_episodes.script`, validated by
  `episode_validator` (§8.1).
- **N-5**: **Symbolic voice registry** (`config/voices.yaml`, §7.2) + persona/voice
  resolution from the show header — a per-show override seam in
  `tts_client.voice_for` and `revoice`'s `speaker_names` source. Today's
  `voice.speakers` remains the fallback pool.
- **Reused unchanged**: the 4 duet bus message types (`replay_invite`,
  `replay_ready`, `replay_cue`, `replay_end`), the cue ratchet + fast-forward rule,
  the refusal rule ("duets never degrade"),
  `replay_pane.perform_director_request` / `perform_follower_request`,
  `narration_store` persist/load, `episode_store`, the layered layout engine,
  `stream_supervisor`, `audio_player`, `tail_bus` (**untouched** — v1.0 planned a
  sibling command here; §6.1 removes the need).

## 4. The GM / director (tuber_0)

`tuber_0` is the **director** of every roundtable show, and also a cast member
(§1.1). Concretely:

- It receives the `replay_request` (the operator, or later the generation engine,
  sends it to `"tuber_0"` with a `cast` naming which `tuber_N` slots are in this
  show). `tuber_0` appears in `cast` as a speaker (narrator) like anyone else.
- It prepares the airing exactly as a duet director does today: LLM narration for
  every speaker (using the show header's persona names), TTS for every speaker
  (using the show header's registry-resolved voice), persist to `narration_store`,
  then `replay_invite` each remote cast slot.
- It renders the roundtable channel locally: one `tile` pane per slot in the cast
  performs that slot's scenes (audio + avatar).
- Local tile performance is driven by the **same cue ratchet** the external
  followers use, but the cue source is the director's own scene loop — no Kafka
  round-trip for the GM's own tiles. The director's `Performer.on_scene_start(i)`
  writes `<relay-dir>/<slot>.cue.json` before performing scene `i`; each local
  tile's `wait_for_scene(i)` polls for it, mirroring the external follower path
  exactly.

### 4.1 Who plays what audio (explicit — review finding C4)

This was implicit in v1.0 and is load-bearing, so it is now stated:

- The director's own `Performer` runs with a cast that maps every speaker to some
  `tuber_N` other than itself, so `cast.get(speaker, self_id) == self_id`
  (replay_pane.py:522) is **false for every scene**. The director therefore owns
  nothing and **plays no audio**. That is correct and intended.
- All seven voices on the roundtable channel come from the **seven tiles**, each
  playing only its own owned scenes.
- The GM's own lines are played by the **GM's tile**, exactly like any other
  character — not by the director process.
- Every tile's audio goes to the same Pulse `vout` sink (`audio_player.play_wav`
  → `paplay`, which inherits `PULSE_SINK=vout`), which is what
  `stream_supervisor`'s ffmpeg captures. So all seven voices land on the
  roundtable broadcast with **no change to the broadcast layer**.

## 5. The tile panel (new panel type)

A tile is a self-contained mini-stage for one character slot:

```
┌─────────────────────────┐
│  [avatar  N×N ASCII]    │   ◀ avatar render (MVP placeholder — §5.1)
│  "You there, stop."     │   ◀ the current line (speaker's line, from the scene)
│  ─────────────────────  │
│  status: listening      │   ◀ speaking / listening / idle
└─────────────────────────┘
```

- One tmux pane per slot on the roundtable channel (7 panes; §6).
- A tile is configured with `use: tile`, `id: <slot>`, and a `with:` block that
  pins the slot (`with: {slot: "tuber_2"}`) — the slot is the stable identity;
  the persona display name is resolved from the show header at scene time.
- Rendering is the **existing follower path**: a `Performer` per tile, `owned`
  computed as `cast.get(speaker) == slot`, `audio_player` playback when owned,
  pacing to `target_duration` when not, avatar expression `speaking` when owned
  else `listening`. Same code as the external follower.
- The GM tile is the same panel type with a different `with:` block (§1.1).
- Idle behavior (between shows): each tile shows its slot's default avatar in
  `idle` with a neutral "listening" status — the roundtable channel never goes
  blank.

### 5.1 Avatar rendering: acknowledged MVP placeholder debt

*New in v1.1 — review findings G2 and G3, accepted rather than solved.*

The current avatars are placeholder-grade: a module adopted for MVP testing that
does not work correctly and is slated for replacement (robust 2D, possibly 3D —
approach TBD, out of scope here). This spec therefore **does not** try to make
seven high-fidelity avatars work. It records the two concrete constraints so the
replacement work starts from facts, and so nobody mistakes the placeholder for a
finished feature:

1. **`avatar.py` cannot be reused as-is for tiles.** It resolves its state path
   via `resolve_state_path(agent_config)` = `AGENT_STATE_FILE` env →
   `agent_config["state_file"]` → `/tmp/agent_state.json` (agent_state.py:16-18),
   and its only CLI argument is `--config` (avatar.py:68). Seven instances in one
   container would all read the same file and render seven identical faces.
   **Resolution:** `app/tile_pane.py` owns its own minimal avatar render and its
   own per-tile state path (`/tmp/tiles/<slot>.state.json`). Writing it is cheap
   because `Performer._avatar` already takes a per-instance `state_path`. We do
   **not** patch `avatar.py` for a module that is being replaced.
2. **The row budget is tight and this project has already been burned by it
   once.** `build_layout.py` defaults to 240×67 and startup.sh then resizes tmux
   to the real xterm grid (~60-70 rows at 1080p/font 14). Three tile rows sharing
   the space left after show_log and htop gives roughly 15-16 rows per tile —
   while `config/layouts/replay.yaml` carries a comment stating the avatar pane
   needs ~80% of a column for "~20 content rows + bubble + status bar", a comment
   that exists *because a preset shipped with the avatar cut off and nobody
   noticed*. **Resolution:** tiles get a deliberately small placeholder avatar
   sized to the tile, not the full-pane art, and §8.5 makes a geometry spike a
   gate on WP-4. v1.0's claim that "all 6 avatars stay legible at 1080p" is
   withdrawn — it was never measured.

When avatars are replaced, the tile is the single place to change (§1.1), which
is the main reason the GM is a character rather than a special case.

## 6. The roundtable layout preset (config/layouts/roundtable.yaml)

Seven tiles + the show log + a status strip. Tiles in a 4×2-ish grid with the log
across the bottom:

```
┌──────────┬──────────┬──────────┬──────────┐
│ tuber_0  │ tuber_1  │ tuber_2  │ tuber_3  │  ┐ top row
│ (GM/narr)│          │          │          │  ┘
├──────────┼──────────┼──────────┼──────────┤
│ tuber_4  │ tuber_5  │ tuber_6  │  (spare) │  ┐ second row
│          │          │          │          │  ┘
├──────────┴──────────┴──────────┴──────────┤
│  show_log — the DIRECTOR pane (§6.1):     │
│  all dialogue + actions in order, scrollable
├───────────────────────────────────────────┤
│  htop (thin status strip)                 │
└───────────────────────────────────────────┘
```

- **No `filetree` pane and no `editor` pane** on the roundtable — those are the
  "depth" panes that live on the character channels. The roundtable is breadth.
- The `htop` strip keeps the "live system" feel consistent with the individual
  channels.
- The preset is config-driven: adding or removing a tile is one entry in the
  `panes:` list (OPEN-4).
- Exact geometry (tmux `-p` percentages) is **deliberately not fixed here** —
  §8.5 sets it from a measured spike rather than a guess. The grid above shows
  topology, not final sizing. The 8th cell is spare capacity, not a committed slot.

### 6.1 The show log is the director pane (v1.1 — resolves G1, deletes work)

v1.0 specified the show log as a new pane program tailing a per-airing local
JSONL that the director would write, plus "a small `tail_bus.py` sibling command"
(v1.0 N-3 / WP-4). That is unnecessary.

The director's `Performer` **already** prints exactly the required content:
`perform` walks every scene in order, and `_perform_scene` sets the per-scene
display label via `_resolve_display_name` before `_on_assistant_text` writes
`label + text` to the Performer's `out` (default `sys.stdout`). A pane running
the director process therefore renders, on its own stdout, the full ordered
transcript of the show with correct per-speaker persona names.

> **Decision: the `show_log` pane *is* the director's `replay_pane` process.**

Consequences, all good:

- **The director process has a home** (G1 resolved) — it is a normal tmux pane,
  launched by the normal `send-keys` path, with no `startup.sh` change and no
  headless side-channel.
- **No new pane program for the log**, no per-airing JSONL, no relay-file writer,
  no `tail_bus.py` sibling. `tail_bus.py` is untouched.
- **The log can never drift from the tiles' timing** — it is the same loop that
  fires the cues. v1.0 needed a risk-table row arguing the log lags by at most a
  render cycle; that row is now vacuous and has been dropped from §14.
- Scroll-back comes free from tmux `history-limit`.
- The director owns no scenes (§4.1), so this pane is silent — pure text. Right
  behaviour for a log.

The one thing to get right in WP-4: the director pane must be configured
`use: replay` with a roundtable-appropriate `title`/`border_color`, and its
`Performer` must be constructed with pacing suited to a log (the typed-output
pacer is a per-scene effect that reads fine, but the spike in §8.5 confirms it at
tile scale).

## 7. The show (episode) format

### 7.1 Show header (new optional field on the episode object)

The existing episode object (`source`, `project`, `session_id`, `date`, `events`)
gains one **optional** top-level key:

```json
{
  "show": {
    "title": "The Malvakar Riddle — Act 1",
    "slots": ["tuber_0", "tuber_1", "tuber_2"],
    "persona": {
      "tuber_0": {"name": "The Chronicler", "voice": "narrator_warm"},
      "tuber_1": {"name": "Alcinoe",        "voice": "alto_bright"},
      "tuber_2": {"name": "Vance",          "voice": "tenor_low"}
    }
  },
  "source": "...", "project": "...", "session_id": "...", "date": "...",
  "events": [
    {"type": "assistant_text", "text": "...", "speaker": "tuber_0"},
    {"type": "assistant_text", "text": "...", "speaker": "tuber_1"}
  ]
}
```

- `show.slots` — the roster for this show; slots in the global roster but not in
  `slots` stay as idle tiles.
- `show.persona.<slot>.name` — the character name, displayed on screen and used
  to drive TTS narration phrasing.
- `show.persona.<slot>.voice` — a **symbolic registry name** (§7.2), never a file
  path. *Changed in v1.1 — B2.*
- `speaker` on each event references the **slot** (`tuber_N`), not the persona
  name — the persona name is only display data.
- The `show` block is **optional**: an episode without it behaves exactly as
  today, so recorded-session replays and existing hand-authored episodes keep
  working with zero changes (§7.4).

### 7.2 The symbolic voice registry (`config/voices.yaml`) — new in v1.1

**Why it exists.** Two problems, one fix:

1. v1.0's `"voice": "en_US-kathleen-low"` is unconsumable. `voice_for` merges a
   voice-config *fragment*, and the real per-speaker shape is
   `model_path: /data/voices/en_US-kathleen-low.onnx` — `_piper_local` stats that
   path and raises `TTSError` if it is missing. There is no name→path layer.
   Putting a raw `model_path` in the show header instead would leak container
   filesystem paths into story data, which is the wrong direction.
2. Upload-time validation cannot see voice files at all: message-api has **no
   `/data/voices` mount** (docker-compose.yml:317-334). So "does this voice
   exist?" is unanswerable there — unless the answer is a *config lookup* rather
   than a *file stat*.

A registry solves both. It is a repo-level config file, so message-api can
validate registry membership with no voice files present, while the container
that actually synthesizes resolves the same name to a real backend fragment.

**Shape.** A registry entry is a `voice_cfg` fragment — exactly what `voice_for`
merges — so it is provider-agnostic by construction (`model_path` for piper,
`voice_id` for openai/elevenlabs):

```yaml
# config/voices.yaml — the symbolic voice registry.
# Names are campaign-agnostic: they describe a VOICE, never a character and
# never a file. Story data (show headers) references only these names.
version: 1
voices:
  narrator_warm:   {provider: piper, model_path: /data/voices/en_US-lessac-medium.onnx}
  narrator_plain:  {provider: piper, model_path: /data/voices/en_US-lessac-low.onnx}
  alto_bright:     {provider: piper, model_path: /data/voices/en_US-kathleen-low.onnx}
  alto_warm:       {provider: piper, model_path: /data/voices/en_US-kristin-medium.onnx}
  tenor_low:       {provider: piper, model_path: /data/voices/en_US-danny-low.onnx}
  tenor_high:      {provider: piper, model_path: /data/voices/en_US-ryan-high.onnx}
  baritone_mid:    {provider: piper, model_path: /data/voices/en_US-joe-medium.onnx}
  baritone_soft:   {provider: piper, model_path: /data/voices/en_US-bryce-medium.onnx}
  bass_low:        {provider: piper, model_path: /data/voices/en_US-ryan-low.onnx}
```

Nine entries, matching the nine `.onnx` files actually present in `voices/` —
enough for seven distinct slot voices plus two spares. Verified against
`ls voices/*.onnx`.

**Resolution order.** `voice_for` gains an optional symbolic name:

```
voice_for(speaker, voice_name=None):
    merged = {base voice config, minus "speakers"}
    if voice_name and voice_name in registry:  merged.update(registry[voice_name])
    else:                                      merged.update(config.speakers[speaker] or {})
    return merged
```

So: **show header wins → slot's `voice.speakers` entry → base voice config.**
This preserves today's behaviour exactly when no `voice_name` is passed, which is
what keeps §7.4 true.

**Rules.**

- Story/campaign data may reference **only** registry names. A show header
  carrying a `model_path` is rejected at upload (§8.1) — that is a leak of
  platform detail into story data.
- Names describe a voice, not a character (`alto_bright`, not `alcinoe_voice`) —
  the same registry serves every campaign, and a persona→voice binding belongs in
  the show header where it can change per show.
- The registry is mounted read-only into **every worker and message-api**
  (§9) — the single source of truth both tiers of validation read.
- Adding a voice is: drop the `.onnx` in `voices/`, add one registry line, re-run
  the §8.2 inventory check.

## 7.3 Retiring the orphan `meta/cast/scenes/render` schema

`replays/coder/test_worker_roundtable.json` and `replays/coder/sample.json`
use a schema no code reads. They are **deleted** (§2.2 step 5): roundtable shows
use the standard episode format + the `show` header (§7.1). The 1st-draft concept
they encoded is fully expressed by §5/§6/§7.1.

### 7.4 Backwards-compat contract

*Verified against the real validator: `_check_shape` (episode_validator.py:65-94)
checks only `REQUIRED_KEYS` presence and per-event required fields, with no
unknown-key rejection — so an extra optional `show` key passes untouched.*

- An episode with **no** `show` block: renders exactly as today on a character
  channel (duet, as now) and on the roundtable (tiles for the standard slots,
  persona names falling back to the slot's config default).
- An episode with a `show` block: `show.persona` is authoritative for names and
  voices; slots not in `show.slots` are idle tiles; speakers in `events` that are
  not a valid slot id render in the show log with the raw id (OPEN-2 default).
- The current single-channel/duet behaviour is **the v1.0-era shipping path and
  must keep working through every WP** — the roundtable is additive.

## 8. Validation: nothing unvalidated reaches a live broadcast

*New in v1.1. Replaces v1.0's single false claim (B1) with a ladder of four
tiers. The organising principle: **each check runs in the tier that actually has
the capability to perform it.*** message-api can read config but not voice files;
the GM container can load voices but shouldn't be the first to notice a typo; a
rehearsal can exercise the real code path but must not broadcast.

| Tier | Runs where | Needs | Catches | Gate |
|---|---|---|---|---|
| **V1 static** | message-api, `POST /replays` | repo config only | malformed header, unknown slot, unknown voice **name**, cast/slots incoherence, `model_path` leak | upload rejected, HTTP 400 |
| **V2 inventory** | any TTS-capable container (GM, workers) | `/data/voices` | registry name → file missing / unloadable | boot warning + non-zero CLI exit |
| **V3 readiness** | GM, pre-invite | bus + Postgres | slot down, episode unresolvable, narration store down, voice inventory bad | clean operator error, fast |
| **V4 rehearsal** | GM, broadcast suppressed | everything | anything the above can't — real director path, end to end | show not cleared to air |

### 8.1 V1 — static validation at upload (extends `episode_validator`)

Added as a fifth stage to `validate_episode`, after the leak audit and before the
dry run. Needs **no voice files**, which is what makes it viable in message-api:

1. **Shape**: `show` is a dict; `show.slots` is a non-empty list of strings;
   `show.persona` is a dict.
2. **Slot ids** match `^tuber_[0-9]+$` and are within the configured roster.
3. **Coherence**: every key of `show.persona` is in `show.slots`; every
   `event.speaker` that looks like a slot id is in `show.slots` (a non-slot
   speaker is allowed — OPEN-2 renders it in the log).
4. **Voice names**: every `show.persona.<slot>.voice` is a key in
   `config/voices.yaml`. This is the check v1.0 wrongly believed the dry run was
   doing. A typo'd or unavailable voice is now an HTTP 400 at upload with the
   offending name echoed (a voice name is not a secret — unlike the leak audit,
   which must never echo its match).
5. **No platform leakage**: a persona block containing `model_path`, `voice_id`,
   or `base_url` is rejected — story data must reference the registry only.
6. **Registry integrity** (cheap, same pass): `config/voices.yaml` parses, is
   `version: 1`, and every entry has a `provider`.

Note the *existing* `_dry_run` stays exactly as-is and remains valuable — it
renders the whole episode through `Performer` with pacing off, which is what
catches an episode that crashes the renderer. It simply never had anything to do
with voices.

### 8.2 V2 — voice inventory verification (where the voices live)

A registry name passing V1 proves the *name* is known, not that the *file* is
there. That check has to run where `/data/voices` is mounted.

- New `app/voice_registry.py` with `--verify`: for every registry entry, resolve
  the backend fragment and confirm the asset is usable — for piper, `model_path`
  exists **and** `PiperVoice.load` succeeds (a present-but-corrupt `.onnx` is a
  real failure mode; `_load_local_voice` caches by resolved path, so a verify
  pass also warms the cache). Non-zero exit on any failure, one line per voice.
- **Runs at GM container start**, logged loudly. A missing voice must be visible
  at boot, not at air time. Non-fatal (consistent with startup.sh's existing
  treatment of the Pulse sink) but unmissable.
- Also runnable ad hoc: `docker exec … python3 /app/voice_registry.py --verify`.

Rationale for loud-but-non-fatal: the GM's `prepare_voice` is best-effort and
returns `None` on failure, which `perform_director_request` converts into
`refuse("voice preparation failed or is disabled for this worker")` — a single
bad voice therefore kills the **entire show on every channel** with a message
that does not name the voice. V2 makes that diagnosable before it happens; V4
makes it impossible to reach air.

### 8.3 V3 — cast readiness probe before committing to an airing

Today the director commits to an airing and then waits up to
`REPLAY_READY_TIMEOUT_S` (default 60s, replay_pane.py:77-78) for followers,
after having already paid for LLM narration and TTS for the whole show. With
seven slots the chance that one is down is meaningfully higher, and burning a
full narration pass to discover it is wasteful.

A cheap pre-flight before `prepare_voice`: ask each cast slot to confirm it can
resolve the episode, reach the narration store, and pass V2. Reuses the existing
message plumbing (no new protocol shape needed beyond a probe/ack pair) and fails
in seconds with a specific operator error — "`tuber_3` did not answer" — instead
of a generic `ready_timeout` a minute later.

Strictly an optimisation and a diagnostics win: the §10 refusal contract is
unchanged, and V3 failing means the show refuses exactly as it would have.

### 8.4 V4 — full-dress rehearsal (the actual "cleared to air" gate)

The one tier that exercises the real thing. A `replay_request` variant
(`rehearse: true`) that runs the **complete director path** — narration, TTS,
persistence, invite, cue ratchet, all seven tiles, followers on their own
channels — with broadcast suppressed.

- **Broadcast suppression** uses the existing mechanism: `stream_supervisor`
  already starts/stops ffmpeg from `app/worker_control.py`'s per-worker on/off
  flag (toggled via message-api `/workers/{id}/enable|disable`, no redeploy).
  A rehearsal disables the affected workers' broadcast, airs, re-enables. No new
  suppression machinery.
- **Cost control** uses the existing `fake` TTS provider (`TTS_PROVIDER=fake`,
  tts_client.py:210-225), which writes a silent WAV sized to the line's read
  time — so `target_duration` pacing and the cue/watchdog timings stay realistic
  while costing nothing. A rehearsal can run with `fake` for the fast structural
  pass, then once with the real provider for the voice pass.
- **What only V4 can catch**: the show-header → registry → `voice_for` →
  synthesis chain working end to end with the *real* provider; `plan_scenes`
  producing a scene count and kind sequence that matches what followers rebuild
  (`_rebuild_scenes_from_rows` returns `None` on mismatch and the follower
  refuses); every slot's owned-scene math; the ratchet not tripping a watchdog at
  seven-way fan-out; and the §8.5 geometry actually being legible.
- **Gate**: a show is "cleared to air" only after a clean V4. This is the
  sentence that answers the original requirement — the entire configuration
  passes validation before a live broadcast.

### 8.5 Geometry spike (gate on WP-4)

Before committing tile percentages: build the preset, run it under a 1920×1080
xterm at the production font size, screenshot the frame, and read it. Set the
percentages from what is measured. Deliverable: the numbers in
`config/layouts/roundtable.yaml` plus one screenshot in the WP-4 plan. Cheap, and
it is the check that was skipped the last time this project cut avatar panes off
(§5.1).

## 8.6 Remaining open items

- **OPEN-2 — NPC / overflow speakers**: default is render in the show log only,
  no tile. (Alternative: an "overflow" tile cycling non-slot speakers — defer.)
- **OPEN-3 — min_cast / partial-airing**: default is **refuse** (duet contract:
  no degrading). A configurable `min_cast: N` is a later option; the refusal
  machinery already supports both.
- **OPEN-4 — roster size**: default is 7 tiles (`tuber_0`…`tuber_6`). The preset
  is config-driven, so `tuber_7` is one yaml line + one compose service + one env
  key.

*Resolved since v1.0:* OPEN-1 (GM tile — yes, §1.1), OPEN-5 (mapping — §2.1),
OPEN-6 (handoff protocol — keep, §2.2 step 3).

## 9. Config keys (new env + compose)

*Corrected in v1.1 — review finding C1: v1.0's block omitted `CONFIG_PATH`, the
config mount, the **voices mount**, `DISPLAY_NUM`, `LLM_BASE_URL`, `REDIS_URL`,
`ipc: host` and `shm_size`. The voices mount is critical — the GM does **all** the
TTS for every speaker in the show, so without it there is no audio on any channel.*

```yaml
# docker-compose.yml — new service (shape mirrors worker-manager, lines 213-252)
  worker-gm:
    image: vtube-worker:latest
    pull_policy: never
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      CONFIG_PATH: /config/worker.yaml
      WORKER_ID: tuber_0
      DISPLAY_NUM: 105                      # 99-104 are taken (§2.1)
      LAYOUT_PRESET: ${GM_LAYOUT_PRESET:-roundtable}
      STREAM_RTMP_URL: ${STREAM_RTMP_URL:-rtmp://rtmp-preview:1935/live}
      STREAM_KEY: ${TUBER0_STREAM_KEY:?TUBER0_STREAM_KEY must be set in .env}
      LLM_BASE_URL: ${LLM_BASE_URL:-http://host.docker.internal:11434}
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
      AVATAR_PROVIDER: ${GM_AVATAR_PROVIDER:-}
      KAFKA_BOOTSTRAP_SERVERS: ${KAFKA_BOOTSTRAP_SERVERS:?}
      KAFKA_TOPIC: ${KAFKA_TOPIC:-vtuber.messages}
      REDIS_URL: ${REDIS_URL:-redis://redis:6379}
      POSTGRES_HOST: ${POSTGRES_HOST:?}
      POSTGRES_PORT: ${POSTGRES_PORT:-5432}
      POSTGRES_DB: ${POSTGRES_DB:-virtualtubers}
      POSTGRES_USER: ${POSTGRES_USER:-virtualtubers}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?}
      REPLAY_READY_TIMEOUT_S: ${REPLAY_READY_TIMEOUT_S:-}
      REPLAY_SKIP_LLM: ${REPLAY_SKIP_LLM:-}
      TTS_PROVIDER: ${TTS_PROVIDER:-}
      TTS_BASE_URL: ${TTS_BASE_URL:-}
    volumes:
      - ${REPO_ROOT:-.}/config/workers/tuber_0.yaml:/config/worker.yaml:ro
      - ${REPO_ROOT:-.}/voices:/data/voices:ro        # CRITICAL — GM does all TTS
      - ${REPO_ROOT:-.}/config/voices.yaml:/config/voices.yaml:ro   # registry (§7.2)
      - world-state:/data/world-state
    ipc: host
    shm_size: 256mb
    restart: unless-stopped
    depends_on:
      - rtmp-preview
      - redis
```

**Also required (WP-1/WP-5):** mount the registry into message-api so V1 can read
it — `${REPO_ROOT:-.}/config/voices.yaml:/config/voices.yaml:ro`. message-api
needs the registry but still needs **no** voices mount; that asymmetry is the
whole point of §7.2.

`.env.example` additions:

```
# Roundtable channel (tuber_0 / GM) — NEW
TUBER0_STREAM_KEY=live_xxx...
GM_LAYOUT_PRESET=roundtable

# Existing six — renamed in the identity-migration pass (WP-7)
TUBER1_STREAM_KEY=live_xxx...
TUBER2_STREAM_KEY=live_xxx...
TUBER3_STREAM_KEY=live_xxx...
TUBER4_STREAM_KEY=live_xxx...
TUBER5_STREAM_KEY=live_xxx...
TUBER6_STREAM_KEY=live_xxx...
```

`config/workers/tuber_N.yaml` keeps a `role` key for the handoff protocol
(§2.2 step 3): `role: roundtable` for `tuber_0`, and the §2.1 mapping's role for
the rest. `voice.speakers` keys become `tuber_0`…`tuber_6` on every worker config.

## 10. Refusal rule & degradation (reused)

The roundtable show follows the **duet refusal contract**: it does not degrade.
If any cast slot fails to `replay_ready` within the timeout (worker down,
narration store unavailable, TTS failure, Kafka unreachable), the GM refuses —
the show does not air at all, on any channel. `tuber_0` emits `replay_end`
(reason `aborted` / `ready_timeout`) and an `operator_reply` error. There is no
"air roundtable-only" or "fire on the channels that are up" fallback — a
half-show is worse than no show, and it would break the shared
`voiced_narration` airing's "every channel heard the same audio" invariant. This
is the exact behaviour of `perform_director_request`'s refusal paths today
(replay_pane.py:488-589) and it carries over unchanged.

V3 (§8.3) makes refusals *faster and better-labelled*; it does not relax the
contract. OPEN-3 (configurable `min_cast`) would relax it — deferred.

The roundtable **channel itself** is always up: with no show running, `tuber_0`
shows its seven idle tiles and an idle log. Character-channel uptime does not
affect the GM container; it only affects whether a given show may air.

## 11. Work packages (in build order; to be planned in detail after review)

| WP | Scope | Depends on |
|---|---|---|
| **WP-1 Show header + registry + V1 validation** | §7.1 show block; §7.2 `config/voices.yaml`; §8.1 static validation in `episode_validator` (incl. registry membership + leak-of-platform-detail checks); registry mount into message-api | — |
| **WP-2 Voice resolution + V2 inventory** | §7.2 — `voice_for(speaker, voice_name=None)`; `revoice.speaker_names` sourced from the show header; `Performer` label from the show header; `app/voice_registry.py --verify` (§8.2) wired into GM start | WP-1 |
| **WP-3 Local tile performer** | §5 / N-3 — `app/tile_pane.py`: a `Performer`-driven tile, local cue-file ratchet (same code shape as `perform_follower_request`'s `wait_for_scene`), owned-audio via `audio_player`, per-tile avatar state path (§5.1) | WP-2 |
| **WP-4 Roundtable layout** | §6 — `config/panels/tile.yaml`, `config/layouts/roundtable.yaml` (7 tiles + director/show_log pane + htop); director pane per §6.1. **Gated on the §8.5 geometry spike.** No `tail_bus.py` work, no show_log program | WP-3 |
| **WP-5 GM service + compose + .env** | §9 — 7th container, env keys, both mounts, DISPLAY_NUM 105, Portainer stack update, `worker_control` entry for the GM | WP-4 |
| **WP-6 V3 + V4 + end-to-end** | §8.3 readiness probe; §8.4 rehearsal mode (reusing `worker_control` broadcast suppression + the `fake` TTS provider); then a real generated show through WP-1→WP-5 on the roundtable with all six character channels in duet | WP-1…WP-5 |
| **WP-7 Identity migration** | §2.2 — rename worker ids, compose services, config files, env keys, bus cast keys, `voice.speakers`, `speaker_names`, docs; delete the orphan fixtures | **Only after WP-6 is verified live** |
| **WP-8 (deferred)** | viewer chat voting, real-time LLM improv, frame-accurate sync, web UI overlays, robust/3D avatars (§5.1) | — |

Net change vs v1.0's plan: WP-1 and WP-2 grew (registry + real validation), WP-4
shrank (no show_log program, no JSONL relay), WP-6 grew (V3/V4 both reuse
existing machinery). Roughly a wash, with the validation gap closed.

## 12. Deferred (explicitly out of scope)

- Viewer chat voting to drive scene selection (consistent with the campaign
  module's already-deferred `voting`).
- Real-time LLM improvisation between rounds — the show is always a pre-generated
  episode; the LLM produces only the *text* of each scene's narration,
  precomputed by the director at show start.
- Frame-accurate multi-channel sync — the existing duet protocol is scene-level
  (cues + ratchet + catch-up). Sufficient, and what the roundtable reuses.
- Browser/web UI overlays — the stream is a terminal broadcast.
- Robust / 3D avatar rendering (§5.1) — the tile is the seam it will land in.
- An 8th *character* channel — roster is config-driven (OPEN-4) but the first cut
  ships seven.

## 13. Verification checklist (phase 1)

After all WPs through WP-6 land:

1. **Registry + validation (V1/V2)**
   - [ ] `config/voices.yaml` parses; `python3 /app/voice_registry.py --verify`
         exits 0 in the GM container and lists every voice as loadable.
   - [ ] A show header naming an unknown voice is rejected by `POST /replays`
         with HTTP 400 naming the bad voice — **not** accepted and later refused
         on air (the v1.0 B1 regression test).
   - [ ] A show header carrying a raw `model_path` is rejected (§8.1 rule 5).
   - [ ] A show header whose `event.speaker` names a slot absent from
         `show.slots` is rejected.
2. **Configuration**
   - [ ] `tuber_0` starts with `LAYOUT_PRESET=roundtable`, renders 7 idle tiles +
         show log + htop on display :105, broadcast live on `TUBER0_STREAM_KEY`.
   - [ ] All six character channels still start and show their idle state.
   - [ ] Geometry spike (§8.5) screenshot attached to the WP-4 plan; every tile's
         avatar and current-line text are legible at 1080p.
3. **Show authoring**
   - [ ] A hand-authored 7-slot show (with a `show` header) uploads via
         `POST /replays`, passes all of V1 + the existing dry run, and is listed
         by `GET /replays`.
4. **Rehearsal (V4) before any live air**
   - [ ] `rehearse: true` with `TTS_PROVIDER=fake` completes: all 7 tiles perform,
         all 6 followers ready and ratchet, `replay_end` reason `finished`, and
         **no ffmpeg broadcast ran** on any channel (confirm via
         `worker_control` state + absence of RTMP output, not by assumption).
   - [ ] Same rehearsal with the real TTS provider completes, and the show log
         shows every line in order under the correct persona names.
5. **End-to-end roundtable airing**
   - [ ] `POST /messages` with `{"to":"tuber_0","type":"replay_request","payload":
         {"episode":"...","cast":{"tuber_0":"tuber_0","tuber_1":"tuber_1",…,
         "tuber_6":"tuber_6"}}}` → GM prepares, persists, invites, all six
         `replay_ready`, show airs in lockstep.
   - [ ] Roundtable channel: all 7 tiles animate `speaking` on owned scenes; each
         speaker's voice is **audible on that channel** (not just present in the
         WAV) — confirm on the actual Twitch output.
   - [ ] The GM's own lines play from the GM **tile**, and the director pane plays
         no audio (§4.1).
   - [ ] Each character channel: only its own voice plays; avatar `speaking` on
         owned scenes, `listening` otherwise; private panes render as before.
   - [ ] `replay_end` (`finished`) lands; all channels return to idle.
6. **Refusal path**
   - [ ] Stop `tuber_3`; request the same show → V3 reports `tuber_3` specifically
         and fast, GM refuses, `operator_reply` error, no show airs anywhere.
   - [ ] Remove a voice file, re-run V2 → non-zero exit naming that voice; a show
         needing it refuses with a message that names the voice.
7. **Backwards-compat**
   - [ ] A recorded-session (boss/coder) episode with **no** `show` block still
         uploads and still airs (solo or 2-worker duet) exactly as before.
   - [ ] An existing single-channel duet show still airs unchanged — the v1.0
         shipping path survives (§7.4).
8. **Identity migration (WP-7)**
   - [ ] `grep -rn "MAX-1\|KODI-7\|NYX-1\|OKO-2\|ADA-3\|TESS-3" config/workers/
         docker-compose.yml` returns no matches **in the generated-show
         namespace**; live-agent `agent.name` / `avatar.name` / system prompts are
         out of scope by §2 rule 6.
   - [ ] `voice.speakers` has `tuber_0`…`tuber_6` on every `config/workers/tuber_N.yaml`.
   - [ ] A duet show with the new slot ids works end-to-end on two channels.
9. **No regressions**
   - [ ] `pytest tests/ -q` green — existing suites plus new tests for the show
         header, the registry + V1 checks, `voice_for`'s override precedence, and
         the local tile performer.
   - [ ] `stream_supervisor` redaction still covers the GM's key. Note: it
         redacts the `--stream-key` **value**, not a key *name* pattern
         (stream_supervisor.py:31), so this is name-agnostic and needs no change —
         confirm rather than implement.

## 14. Risks & mitigations

| Risk | Mitigation |
|---|---|
| A voice named in a show header isn't available where TTS runs | **This was v1.0's false-mitigation row.** Now genuinely covered in two places: V1 rejects an unknown registry *name* at upload (no voice files needed — §8.1), V2 verifies every registry entry actually loads where `/data/voices` exists (§8.2), and V4 proves the whole chain before air (§8.4) |
| Persona names collide with a slot's config default and the wrong one displays | `show.persona.<slot>.name` is authoritative when present; the fallback is the slot id (`tuber_2`), so a collision is visible, not silent |
| One bad voice kills the entire show on every channel | True and intended (§10 refusal contract), but previously undiagnosable: `prepare_voice` returns `None` and the refusal message doesn't name the voice. V2's boot check + V3's probe + V4's rehearsal move that discovery off the live path |
| Seven-way fan-out trips a follower watchdog the 2-way duet never hit | The ratchet's per-scene watchdog is bounded by the previous scene's `target_duration` + grace (replay_pane.py:88-91), which does not scale with cast size — but first-cue timeout does matter with 7 slots preparing. V4 rehearsal at full cast is the measurement; `REPLAY_READY_TIMEOUT_S` is already env-tunable per container |
| Tile geometry cuts off avatars at 1080p | Exactly what happened before in `config/layouts/replay.yaml`. §8.5 makes a measured spike a gate on WP-4, and §5.1 sizes tile avatars for a tile rather than reusing full-pane art |
| Avatar placeholder quality shows badly across 7 tiles at once | Accepted, scoped, and documented (§5.1). The tile is the single seam the replacement lands in (§1.1) |
| A new slot is added later but the preset shows 7 tiles | Preset is config-driven (OPEN-4): one yaml entry + one compose service + one env key |
| A character channel goes down mid-show (not at start) | Existing duet behaviour: that follower stops, the show continues — `on_scene_start` is fire-and-forget with no ack (replay.py `perform`), so the ratchet doesn't require it. Verified, unchanged |
| Rename (WP-7) breaks a working show | WP-7 is ordered after WP-1…WP-6 are verified live |

*Dropped from v1.0:* the "show log lags the tiles by a render cycle" row — §6.1
makes the log the director's own pane, so there is no second timeline to drift.

## 15. References

- `docs/duet_replay.md` — the 4-message protocol, cue ratchet, refusal rule, voice
  resolution, and deployment requirements this spec reuses without change.
- `docs/layout_system.md` — the layered config model this spec's panels + preset
  plug into.
- `docs/replay_pane.md` — `perform_director_request` / `perform_follower_request` /
  `perform_request` (the dispatch the GM uses).
- `docs/revoice.md` — `plan_scenes` speaker-override and `speaker_names`.
- `docs/episode_store.md` / `docs/episode_validator.md` — the upload + validation
  path §8.1 extends.
- `docs/stream_supervisor.md` — the ffmpeg/Pulse/broadcast layer the 7th channel
  reuses, and the `worker_control` on/off flag §8.4 borrows for broadcast suppression.
- `docs/tts_client.md` — `voice_for` (gets the registry seam, WP-2) and the `fake`
  provider (§8.4).
- `docs/control_panel.md` — the operator surface that will need the rehearsal control.
- `ring_composition_spec.md` — the story-structure spec (context only, separate
  concern).
