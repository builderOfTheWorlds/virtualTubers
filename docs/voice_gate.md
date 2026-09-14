# Voice Gate — one voice at a time

**Status:** implemented (`app/voice_gate.py`, wired through `replay.Performer`
at every playback site). **Applies to:** roundtable channel (the only place
voices actually mix) and, harmlessly, every character channel.

## The problem it solves

The roundtable channel captures one Pulse sink (`vout`) that **all seven
tiles AND the director** feed. Nothing used to serialize that shared bus, so
two failure modes produced the "characters talking over each other" heard on
air:

1. **Director double-play (GM line echoed).** The director's ownership test
   was `cast.get(speaker, self_id) == self_id` (replay_pane.py). For the GM's
   own slot — `boss` → `tuber_0`, which the cast maps to the GM tile — that
   is `True` **while the GM tile also owns it**. Two processes paplayed the
   same line into one sink, offset by a few hundred ms: a doubled, smeared
   voice. For every *other* slot the old formula was `False` on the director,
   so only the GM's line doubled — but it's the loudest, most frequent line
   on a roundtable.

2. **Scene-boundary bleed.** The director cues scene N+1 the moment its own
   (silent) render of scene N ends, but the owning tile's *audio* (paplay)
   is slightly longer in reality — process spawn, pulse buffering — so
   scene N+1's audio can start before scene N's fades out. No lock existed
   to say "nobody starts a voice line until the current one is done."

## The fix

**Rule — one voice at a time.** A "gate" is a counting lock built on
`fcntl.flock` over N seat files:

- `acquire()` — before a voice line starts, grab the lowest free seat,
  holding it for the life of that line.
- `release()` — only after the line's audio has **fully finished**
  (`wait_extra` returns), on every exit path (stop, exception, pacer-off).
- **N = 1 (default)** ⇒ seat 0 held for the whole line ⇒ a second voice
  line literally cannot start under it. Overlap becomes impossible, not
  merely unlikely.
- **N > 1 (escape hatch)** ⇒ up to N lines may sound together — deliberate
  duet/edge-case moments (see below).

Seat files are flock locks, so the guarantees that matter for a live show
come free:

- **Cross-process:** tiles and the director are separate processes in the
  GM container; they all lock the same seat files, so the bound holds
  between them.
- **No deadlocks on a crash:** the kernel releases a flock when the holder
  dies. A killed tile frees its seat with zero cleanup. (This is why the
  gate is a *file lock*, not a lock server or a lease.)
- **Auto-degrade, never withhold audio:** acquire is bounded
  (`acquire_timeout_s`, default 90s — covers the longest legal line).
  Timeout ⇒ WARN log + play without a seat. An unwritable gate dir ⇒ WARN +
  play as if the gate didn't exist. The gate can *delay* a line, never
  *delete* one.
- **Reconstructable timeline:** every seat acquire/release is appended to
  `events.jsonl` in the gate dir (monotonic time, seat, holder tag), so an
  audio complaint can be replayed against exactly what held the bus when.

### Container scope (and why the gate isn't fleet-wide by default)

Voices only mix where they share one Pulse sink — inside the **one**
container that owns that sink (the roundtable). Each character channel has
its own container, its own sink, and plays at most its own lines, one at a
time *by construction* (a duet follower is one process performing serial
scenes). The default seat dir is therefore the **container's own /tmp**
(`_default_gate_dir`):

- inside the GM container, the director + all seven tiles share that
  `/tmp` (one container) → the gate arbitrates exactly the processes
  whose voices actually mix,
- two character containers are different `/tmp`s → they can never contend,
  which is correct: there is nothing for them to serialize against.

Both choices are deliberate: not `/data/world-state` (a named volume shared
by **all** workers — a two-seat roundtable line must not be able to wedge a
character channel's line behind the 90s acquire timeout) and not
`$TILE_RELAY_DIR` (a pure-file wire whose exact contents its own test suite
asserts — a `voice_gate` subdirectory would break the relay-purity contract).

Pointing the gate at a shared dir via `VOICE_GATE_DIR` (env) or
`show.audio.gate_dir` is still possible when an operator wants a different
scope.

## The config escape hatch

Layered, each layer degrading cleanly (a typo never stops the show):

```yaml
# in the episode (episode.json), next to show.slots / show.cast:
show:
  slots: ["tuber_0", ..., "tuber_7"]
  cast:  {boss: tuber_0, coder: tuber_1, tester: tuber_2, ...}
  # Deliberate overlap between lines — opt in per-episode:
  audio:
    max_concurrent: 2     # how many voices may sound at once (1..7; default 1)
    line_gap_s: 0.25      # beat of silence between lines (0.0..60; default 0)
```

Resolution order (first sane source wins):

1. **Env** — `VOICE_GATE_CONCURRENT`, `VOICE_GATE_LINE_GAP_S`,
   `VOICE_GATE_ACQUIRE_TIMEOUT_S`, `VOICE_GATE_DIR` (ops override).
2. **Episode `show.audio`** — `max_concurrent`, `line_gap_s`,
   `acquire_timeout_s`, `gate_dir`.
3. **Worker config** `voice.audio.{max_concurrent,line_gap_s}`.
4. **Defaults** — 1 seat, 0s gap, 90s acquire timeout, container-private dir.

Sane bounds are enforced (and validated at upload by
`episode_validator._check_show`): `max_concurrent` is an integer in
1..7 (bools and non-numbers rejected — they would silently become
1/0/garbage), `line_gap_s` a number in 0..60, `acquire_timeout_s` in
5..3600. Upload-side validation and runtime resolution share the same
ranges, so a header that passes upload is guaranteed sane at air time.

### Where each piece lives

| Piece | Location |
|---|---|
| Gate implementation | `app/voice_gate.py` (`VoiceGate`, `Seat`, `resolve_voice_gate`) |
| Acquire → play → hold → release | `app/replay.py` `Performer._perform_scene` (the only place audio starts) |
| One-performer-per-line ownership | `app/replay_pane.py` `perform_director_request` annotation loop |
| Gate construction at each playback site | `app/replay_pane.py` `build_voice_gate`, called from director / follower / solo / `app/tile_pane.py` |
| Upload validation | `app/episode_validator.py` `_check_show` (`show.audio` block) |
| Runtime env overrides | `VOICE_GATE_CONCURRENT`, `VOICE_GATE_LINE_GAP_S`, `VOICE_GATE_ACQUIRE_TIMEOUT_S`, `VOICE_GATE_DIR` |
| Reconstructed timeline | `<gate_dir>/events.jsonl` (acquire/release, time, pid, seat, tag) |

## Verification (2026-09-13)

- 12 new gate tests (`tests/test_voice_gate.py`): serialization at N=1,
  exact N bound at N=2, crash-safety (SIGKILL'd holder still frees the
  seat), degrade paths, event log, full layered resolution incl. env vs
  show vs config, clamps, and bool/garbage rejection.
- Double-play regression tests
  (`tests/test_director_tile_fanout.py`): the director is silent on any
  line a tile plays (the exact `boss→tuber_0` case that was doubled), and
  still owns a line the cast maps to no slot at all (the Ashiorid duet's
  narration case — would be silent on no channel otherwise).
- Full suite: 1302 passed (pre-existing `test_campaign_validator.py`
  failures unchanged — they fail identically without these modules loaded,
  and that subsystem is a separate package).

## Known limitations

- The seat is per **line**, not per **word**: the escape hatch allows N
  lines at once, not arbitrary word-level overlap. That is the intended
  semantics — deliberate duets are scene-paired, not syllable-paired.
- Seat files are not cleaned up (by design — the stable inode IS the
  identity of the seat; unlinking would break holders of a deleted inode).
  A dead container's `/tmp/voice_gate` files disappear with the container,
  which is exactly right.
- The gate is per container by default (see channel scope above). Two
  containers can *technically* each hold seat_000 — but each is a
  separate channel/sink, so there is no audible consequence.