# Music engine (`app/music/`, `app/music_director.py`)

## Overview

This module generates emotion-driven background music for the roundtable
show, in real time. Each campaign has one central theme (a motif plus a
chord progression, written in scale degrees). The engine plays that theme
in the emotional colour of the current scene. The mode, tempo, register,
articulation, brightness, density and orchestration all change, while the
motif's contour stays the same. That way viewers always recognise the show's
tune. Every bar played is recorded to Postgres, both as the notes and
parameters and as the aired audio.

The module is standalone. The show never calls into it; it only publishes
the current mood. For the research basis and the full plan, see
`.claude/prompts/background_music_plan.md`.

## Components

| File | Role |
|---|---|
| `music/theme.py` | `Theme` dataclass, `load_theme(path)`, `theme_from_dict`, `theme_to_dict`, `ThemeError` |
| `music/mood_map.py` | GEMS mood → `MoodParams` table, `mood_params(mood, intensity)`, `interpolate`, `blend_moods`, `mood_for_emotion` |
| `music/synth.py` | Vectorised numpy voices (pad, drone, bass, pluck, lead, kick, hat) and a reverb impulse response |
| `music/engine.py` | `MusicEngine`: renders one bar at a time and returns a `Bar` (audio plus the exact notes played) |
| `music/control.py` | Mood sources (Redis GM override, scene-cue file), `MoodResolver` precedence and dwell time, `write_scene_cue()` |
| `music/recorder.py` | `MusicRecorder`: groups bars into mood segments, encodes them (Opus, falling back to WAV) and writes them to Postgres on a background thread |
| `music/music_store.py` | Postgres access for the music tables (`docs/database_schema.md`) |
| `music/cli.py` | Offline `render`, `timeline`, `segments` and `export` |
| `music_director.py` | The live process: mood resolution → engine → `pacat` on the `music` Pulse sink → recorder |

## Signatures

```python
MusicEngine(theme: Theme, mood: str = "neutral", intensity: float = 0.5,
            sample_rate: int = 44100, transition_bars: int = 2, lead_every: int = 2)
MusicEngine.set_mood(mood: str, intensity: float | None = None,
                     transition_bars: int | None = None) -> bool
MusicEngine.render_bar() -> Bar      # Bar.audio: float32 (n, 2); Bar.notes: list[dict]
mood_params(mood: str, intensity: float = 0.5) -> MoodParams
MusicRecorder(store, engine, worker_id="", campaign="", episode="",
              max_segment_s=60.0, audio_format="opus", record_audio=True)
write_scene_cue(mood: str | list[str], scene_id="", intensity=0.5,
                path="/tmp/music/scene_cue.json", theme=None) -> bool
```

## Parameters that matter

- **mood**: one of the 9 GEMS moods (`wonder, transcendence, tenderness,
  nostalgia, peacefulness, power, joyful_activation, tension, sadness`) or
  `neutral`. Unknown values fall back to `neutral`; they never raise.
- **intensity**: 0..1. It scales tempo (±6%), brightness, density and layer
  levels. It never changes the mood's mode.
- **transition_bars** (default 2): the number of bars over which continuous
  parameters glide. Discrete ones (mode, chord extension, key) switch on the
  first downbeat.
- **min_dwell_s** (director, default 20): the minimum time a scene mood
  stays before another scene cue can replace it. A GM override is always
  applied immediately.

## How mood is chosen (precedence)

1. **GM override**: the Redis key `music:{worker_id}:override`. The value
   `{"mood":"tension","intensity":0.7}` holds that mood, `{"mood":"silence"}`
   fades the music out, and `{"mood":"follow"}` (or deleting the key) returns
   to following scenes.
2. **Scene cue**: the file `/tmp/music/scene_cue.json`, written by
   `write_scene_cue()`. The scene's `mood:` list is accepted as-is. A cue
   may also carry `theme` (a campaign name). The director then fades out,
   loads `<themes_dir>/<theme>/music/theme.yaml`, starts a new recording
   session and fades back in.
3. **Hold**: otherwise the current music keeps playing.

## How it's wired into the stream (roundtable only)

```
campaign scene mood: ──► episode builders ──► episode JSON
   (campaigns/*/scenes)   attach event.music      show.music.theme + events[].music
                                                        │
                        replay_pane director: on_scene_start → write_scene_cue()
                                                        ▼
  control panel "Music" card ─► message-api /music/roundtable ─► Redis override
                                                        │
                               music_director.py  ◄─────┘  (resolver: GM > scene > hold)
                                     │ pacat
                                     ▼
       audio_player (voices) ─► Pulse sink "vout"     Pulse sink "music"
                                     │                      │
                                     └──── stream_supervisor ffmpeg ────┘
                          [1:a] voices ──┬───────────────────────► amix ─► AAC ─► Twitch/preview
                                         └─ key ─► sidechaincompress ◄─ [2:a] music
```

- **Config**: the `music:` section in `config/workers/roundtable.yaml`
  sets enabled, theme_name, themes_dir, initial_mood, volume,
  min_dwell_s, transition_bars, record, segment_seconds, audio_format and
  duck.{threshold,ratio,attack_ms,release_ms}. `MUSIC_ENABLED=0` in `.env`
  is a kill switch that needs no config edit. No other worker has the
  section, so their ffmpeg commands are unchanged.
- **startup.sh**: §3.5 creates the `music` null sink when
  `music_director.py --print-enabled` prints `1`. §7.7 runs the director
  in a restart-on-crash loop.
- **Ducking** (`stream_supervisor.music_filter_graph`): the voices go to
  the mix untouched, and a copy of them drives a sidechain compressor on
  the music. With the defaults, a spoken line drops the music by about
  8 dB (measured), and it recovers over 600 ms. `amix normalize=0` keeps
  the voices at their existing level. If the `music` sink is missing, the
  broadcaster streams voices only and logs a WARNING.
- **Themes**: `docker-compose.yml` mounts `campaigns/` read-only at
  `/data/campaigns` in `worker-roundtable`. Replacing
  `campaigns/<name>/music/theme.yaml` takes effect on the next theme load.
  That happens on a director restart, or when a scene cue names that
  campaign after another one.
- **GM control**: in the control panel, the Music card sits under the
  roundtable. It shows now-playing (from the director's
  `music:{worker}:status` heartbeat), a mood dropdown with an intensity
  slider and "Hold mood", and "Follow scene" to clear the override. The
  API is `GET/POST/DELETE /music/{worker_id}` and `GET /music-moods` (see
  docs/message_api.md).
- **Scene cues in episodes**: `event.music = {mood, intensity?,
  scene_id?}` on the first event of each scene with a mood, plus
  `show.music.theme`. The episode validator checks both (`_check_music`,
  see docs/episode_validator.md). Right now only ashiorid's ambient scenes
  carry `mood:`, so spine-only episodes get just the theme header. Add
  `mood:` to spine scenes to get cues there too.

## Usage examples

```bash
cd app
# Audition one mood of the ashiorid theme (writes preview_out/music/…wav)
../.venv/Scripts/python.exe -m music.cli render ../campaigns/ashiorid/music/theme.yaml --mood sadness

# Hear the theme morph across scene changes, and record it to Postgres
../.venv/Scripts/python.exe -m music.cli timeline ../campaigns/ashiorid/music/theme.yaml \
    --cue peacefulness@0 --cue tension:0.8@20 --cue sadness@40 --seconds 60 --record

# Inspect / export what was recorded
../.venv/Scripts/python.exe -m music.cli segments 1
../.venv/Scripts/python.exe -m music.cli export 2 --out tension.ogg
```

```python
# From the show side: publish the scene's mood (never raises)
from music.control import write_scene_cue
write_scene_cue(scene.mood, scene_id=scene.id)
```

```bash
# GM override from anywhere with Redis access
redis-cli SET music:roundtable:override '{"mood":"tension","intensity":0.8}'
redis-cli DEL music:roundtable:override          # back to following scenes
```

## Performance

| Host | Result |
|---|---|
| Dev PC (Windows, Python 3.11, numpy 2.4) | Renders 9–18× faster than real time. Busy moods (joyful, tension) are at the low end. |
| argyre (aarch64, CPU-only), inside the live `worker-roundtable` container with the show running | 4–5× faster than real time. About 0.08–0.10 s of CPU per second of audio, so roughly 10% of one core. |

The live director only has to keep up at 1×. A bar is always rendered one
bar ahead, so a render never falls behind playback.

## Error handling

- `ThemeError` means a malformed theme; the message names the bad field.
- Redis unreachable: `read_override()` returns None, and the music follows
  scene cues.
- Postgres unreachable at startup: the director plays unrecorded and logs
  an ERROR. Failures mid-show are logged, the segment is dropped, and the
  error is counted in `recorder.errors`. The recorder never stalls the audio.
- If `pacat` dies, the director restarts it (3 attempts per bar).
- `music_store.*` functions raise on DB failure. Callers degrade.

## Changelog

- **v1.1.0** (2026-09-27): wired into the stream. Adds the `music:`
  worker config and the `MUSIC_ENABLED` kill switch; the startup.sh
  `music` sink and restart-on-crash director; sidechain ducking in the
  stream_supervisor ffmpeg; scene cues carried through episode JSON and
  published by the roundtable director; theme switching by scene cue; the
  now-playing status heartbeat; message-api `/music` endpoints and the
  control-panel Music card; and a lazy, numpy-free `music/__init__`.
- **v1.0.0** (2026-09-27): theme format, GEMS mood map, numpy synth engine,
  mood resolver, Postgres recording (themes/sessions/segments with
  notes, params and Opus audio), offline CLI and live director.
