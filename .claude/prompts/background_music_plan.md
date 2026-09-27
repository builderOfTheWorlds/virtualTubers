# Roundtable background music — plan (draft, awaiting answers)

Status: PLAN ONLY. Nothing implemented. Open questions at the bottom.

## Goal
An emotion-driven background score for the roundtable show. One central theme
(the campaign's "main title") is varied to match the emotional payload of the
current scene. The GM can override it live. It must be cheap to run on the stream host
(aarch64, CPU-only / llvmpipe).

## Research basis (what the literature says)

1. How to represent emotion: use two dimensions, valence and arousal.
   - Russell 1980 (circumplex model).
   - Eerola & Vuoskoski 2011, Psychology of Music 39(1): two dimensions
     (valence, arousal) explain most of the variance in perceived music emotion,
     and handle ambiguous excerpts better than discrete labels.
   - Our packs already tag scenes with GEMS moods (app/campaign/pack.py:324).
     Keep GEMS as the author-facing vocabulary and map each mood to a
     (valence, arousal, tension) point that drives the engine.
2. Which musical features carry which emotion.
   - Gabrielsson & Lindström 2001/2010 (a review of 100+ studies): tempo, mode, pitch
     height, loudness, articulation, rhythm, harmony and timbre.
   - Tempo is the strongest single cue overall (Juslin & Lindström 2010).
   - Mode (major/minor) and harmonic complexity are the main valence cues.
3. CMERS (Livingstone et al. 2010, Computer Music Journal 34(1)) changed the
   emotion of existing pieces in real time using score and performance rules.
   Listeners identified the intended emotion 78% of the time.
   - Key finding: arousal comes from tempo, loudness, articulation, register and
     brightness. Valence needs score-level changes (mode, harmonic complexity).
     Performance tweaks alone moved only arousal.
   - Example rules: happy = +10 BPM, major, +5 dB, +4 semitones, staccato.
     Sad = slower, minor, quieter, lower, legato.
   - This is exactly the "one theme, emotional variations" idea, already validated.
4. Leitmotif systems for games: Mezzo (Brown 2012, AIIDE) maps leitmotifs into
   forms with different harmonic tension to follow story state. Rise of the Tomb
   Raider shipped a real-time generative percussion system built on the same idea.
5. Film-scoring practice: keep underscore sparse under dialogue and out of the
   voice band (roughly 300 Hz to 3 kHz). Duck under speech. Change mood on phrase
   boundaries, never mid-bar.

## Emotion → music parameter table (first cut, tune by ear)

| GEMS mood          | V    | A    | mode           | tempo | register | articulation | density | timbre         |
|--------------------|------|------|----------------|-------|----------|--------------|---------|----------------|
| wonder             | +0.5 | +0.3 | lydian         | 90    | high     | legato       | med     | bright bells   |
| transcendence      | +0.6 | -0.2 | major, sus     | 66    | wide     | very legato  | low     | pad, reverb    |
| tenderness         | +0.6 | -0.5 | major          | 72    | mid      | legato       | low     | soft keys      |
| nostalgia          | +0.1 | -0.4 | major/minor mix| 70    | mid      | legato       | low     | warm, filtered |
| peacefulness       | +0.4 | -0.7 | major, pentatonic | 60 | mid      | legato       | very low| pad            |
| power              | +0.3 | +0.8 | mixolydian     | 120   | low      | marcato      | high    | brass/saw      |
| joyful_activation  | +0.8 | +0.7 | major          | 128   | high     | staccato     | high    | bright, perc   |
| tension            | -0.4 | +0.6 | phrygian/dim   | 100   | low      | staccato     | med     | ostinato, dark |
| sadness            | -0.7 | -0.6 | aeolian        | 58    | low      | legato       | low     | dark, filtered |

The GM also gets an intensity control (0..1). It scales loudness, density and
the number of layers without changing the mood.

## The central theme (what binds everything)

A theme is data, not audio. It is defined once per campaign in
`campaigns/<name>/music/theme.yaml`:
- motif: 4–8 notes written as scale degrees plus rhythm. Because it uses scale
  degrees rather than absolute pitches, swapping the mode reharmonizes it
  automatically, and the melody stays recognizable in every mood.
- progression: chords written as scale degrees (e.g. i–VI–III–VII).
- layers: pad, bass, motif lead, arpeggio, percussion. A mood decides which
  layers play.

The variation compiler turns (theme, mood, intensity) into a playable pattern by
applying the CMERS-style transforms:
- mode swap
- tempo
- transposition
- augmentation or diminution (sad scenes get the motif at half speed)
- articulation
- layer on/off
- filter cutoff

Characters can get their own mini-motifs later, Mezzo-style, but not in v1.

An LLM (local qwen or Claude) can draft theme.yaml from the campaign lore. A
human then approves it by listening. It never writes free-form code at air time,
so there is no risk of a broken pattern live.

## Engine options

A. Strudel, pre-rendered (recommended v1)
   - The compiler emits Strudel code for each (theme × 9 moods × 2–3 intensity
     levels). You can paste any variant into strudel.cc to audition and tweak it.
   - Rendering is offline, at campaign build time, with headless Strudel
     (jebin2/strudel-render: Chromium + OfflineAudioContext, faster than real time)
     into seamless loop stems of 8 or 16 bars, all at a shared bar grid.
   - On air: a small Python `music_player` crossfades between the pre-rendered
     stems on bar boundaries. It uses almost no CPU, is fully deterministic, and
     any GM choice is instantly available because every variant already exists.
   - Downside: it is "generated" per campaign rather than per moment. There is a
     fixed number of variants, though 27+ loops per theme is plenty for a
     roundtable.
   - Chromium is only needed on the build machine, not in the stream container.

B. Strudel live in the container (node + node-web-audio-api, e.g. strudel-node)
   - This is true live-coding: patterns are hot-swapped at runtime.
   - Risks: immature projects (a 1-star repo), CPU cost of Web Audio DSP
     alongside the render workers, adding Node audio to the image, and jitter
     under load. Consider it for phase 2 only if A feels too canned.

C. A native Python engine (the pattern scheduler in Python, synthesis via
   fluidsynth + a small soundfont or numpy)
   - Cheapest truly-live option. We lose Strudel's editor, but the theme and
     compiler stay the same.

Recommendation: A for v1. Keep the compiler's output engine-agnostic (a list of
events plus Strudel code) so B or C can be dropped in later.

## Integration with the existing stack

1. Data path (currently missing): scene `mood` is dropped in
   build_campaign_episode.py. Carry it into the episode JSON:
   - `show.music: {theme: <campaign>}` in the header.
   - A `music` cue per scene (mood, intensity, optional `sting`).
   - Allow it in episode_validator.py.
2. Trigger: `replay_pane.py` `on_scene_start` (around line 841) already acts as
   the per-scene master clock. It will also write the scene's mood cue.
3. GM live override: reuse the console-theme pattern.
   - message-api `POST /music/{worker_id}` → Redis `music:{worker}:mood`.
   - `music_player` polls this key (like theme_watcher.py does).
   - A control-panel card with 9 mood buttons, an intensity slider, and
     "hold / follow script / silence".
   - Precedence: GM override > scene cue > previous mood.
   - Optionally the AI GM character could emit mood tags itself
     (improviser already returns a per-line emotion).
4. Transitions:
   - Quantize to the next bar (or phrase).
   - Crossfade over 2–4 bars.
   - Minimum dwell time (e.g. 20 s) so rapid cues don't whiplash.
   - A 1-bar motif "sting" on scene changes reminds viewers of the theme.
5. Audio mixing:
   - Add a second Pulse null sink `music`, and a second `-f pulse -i music.monitor`
     input in stream_supervisor.build_ffmpeg_cmd.
   - `sidechaincompress` ducks the music under `vout` voice, then `amix`.
   - The tee branch's hard-coded `-map 1:a:0` has to become the filter output
     label.
   - Alternative with no ffmpeg change: `music_player` ducks itself whenever a
     voice_gate seat is held.
6. New files:
   - app/music/theme.py (schema)
   - app/music/compiler.py (theme+mood → events + Strudel code)
   - app/music/mood_map.py
   - app/music_player.py (runtime)
   - scripts/render_music_stems (node, build time)
   - docs + tests per CLAUDE.md
   - campaigns/ashiorid/music/theme.yaml as the first theme

## Phasing
- P0: Spike. Hand-write the ashiorid theme and 3 moods (tension, sadness,
  joyful_activation). Audition in strudel.cc. Render stems headless. Check
  CPU/latency on the gx10.
- P1: Compiler + mood map + stem render for all 9 moods.
- P2: music_player + Pulse sink + ffmpeg ducking + scene cues from episodes.
- P3: GM control-panel card + Redis override + AI-GM mood tags.
- P4 (optional): live engine (B or C) and character leitmotifs.

## Decisions (user answers, 2026-09-27)
1. Scope: roundtable only.
2. Control: a STANDALONE music module, not part of the GM/character code.
   - It watches "current scene mood" and adjusts the music.
   - The GM (human via the control panel, later the AI GM) only publishes mood;
     it never touches audio.
3. Engine: anything that works. Strudel is not required, so there is no AGPL
   dependency.
4. Style: synth/ambient ("computerized"). One theme per campaign.
5. Roundtable only. Music ducks under speech.
6. Host: argyre = 192.168.1.23 (gx10, aarch64). It moved from 192.168.2.170.

## Revised engine choice (supersedes "Engine options" above)
Synth ambient is cheap to synthesize, so generate LIVE in Python rather than
using pre-rendered loops:
- A `music_director` process runs in the roundtable container.
  - The pattern scheduler is Python: theme motif and progression as scale
    degrees, with a mood → parameter map.
  - The synth is numpy block synthesis: detuned saw/sine pads, sub bass,
    plucked arp, filtered noise, a simple lowpass, and a feedback-delay reverb.
  - Output is streamed as raw PCM into `pacat` on a dedicated `music` Pulse sink.
- Mood changes interpolate parameters continuously (tempo, filter cutoff,
  density, register) on bar boundaries. Mode and chord changes land at phrase
  boundaries. The result is a true morph, not a crossfade between loops.
- Expected cost: well under one core at 44.1 kHz, with a 20 ms block and about
  8 voices. To be verified on argyre in P0.
- Mood inputs, highest precedence first:
  - GM override: Redis `music:roundtable:mood`, set via message-api and the
    control panel.
  - Scene cue: written by replay_pane `on_scene_start` from the episode's
    per-scene `music` field.
  - Hold the last mood.
- Ducking: ffmpeg `sidechaincompress` (keyed on vout voice) + `amix`, in
  stream_supervisor.build_ffmpeg_cmd. Only when the music sink is enabled
  (roundtable).
- No new system packages are needed: numpy and pulseaudio-utils are already in
  the image.

## Status (2026-09-27)
DONE (uncommitted):
- P0 and P1: app/music/ (theme, mood_map, synth, engine, control, recorder,
  music_store, cli), plus app/music_director.py.
- campaigns/ashiorid/music/theme.yaml.
- tests/test_music.py: 36 tests, green.
- Docs: docs/music_engine.md, docs/database_schema.md,
  docs/sql/02_create_tables.sql.
- Postgres recording verified live on mafober 192.168.1.120. The CLI test
  wrote music_sessions.id=1 (worker_id=cli-test), 3 opus segments.
- argyre perf: 4-5x realtime inside the live roundtable container, about 10%
  of one core.
TODO (P2/P3):
- startup.sh: add the `music` null sink and launch music_director.py on the
  roundtable only (gate on WORKER_ID / config `music.enabled`).
- stream_supervisor.build_ffmpeg_cmd: add a second pulse input
  music.monitor + sidechaincompress keyed on vout + amix. Fix the tee
  `-map 1:a:0`.
- Scene mood into episode JSON (build_campaign_episode.py drops it) and
  write_scene_cue() in replay_pane on_scene_start.
- message-api POST /music/{worker_id} + control-panel card (Redis override).
- Local .env POSTGRES_HOST is stale (192.168.2.158); the DB is at
  192.168.1.120.

## Revised phasing
- P0 spike:
  - app/music synth + scheduler.
  - Hand-written ashiorid theme.
  - Render 30 s WAVs of 3 moods (tension, sadness, joyful_activation) offline so
    the user can listen.
  - Measure CPU per second of audio on argyre.
- P1: all 9 GEMS moods, intensity control, the transition rules, and tests.
- P2: music_director process, music sink, ffmpeg ducking, and scene mood carried
  into the episode JSON and cued from on_scene_start.
- P3: control-panel card + message-api endpoint + Redis override.
- P4: LLM-drafted themes per campaign; AI-GM mood publishing.
