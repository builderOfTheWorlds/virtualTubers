# office.playlist

## Overview

OB-33. The ashiorid_office fallback playlist: what airs when nobody is working.

- **Off hours.** Segment s0 (00:00–06:00 America/New_York, `app/office/clock.py`) has no live
  work.
- **A stall.** The day runner's stall detector (OB-30) saw N idle minutes.

`OfficePlaylist.next_item(now, reason)` serves both cases. It returns one `PlaylistItem`, or
`None` when there is nothing to air. `replay_request_message(item, to)` turns the item into a bus
message the replay pane already handles.

Rotation order:

1. **Ambient scenes.** These come from the pack's ambient pool,
   `campaigns/ashiorid_office/scenes/a0NN-*.yaml`. The pool stays empty until OB-40 writes it.
   A scene with `ambient: false` is skipped. An optional `phases:` (or `phase:`) list limits
   when a scene may air: `off`, `morning`, `build` or `ship`. A scene with no phases can air at
   any time.
2. **Approved office replays.** These come from message-api
   `GET /replays?status=approved`, limited to names that start with `office-` (the prefix
   `role_attribution.episode_name` gives). **Drafts are never offered.** The query asks for
   approved rows only, and every row's `status` is checked again on arrival.

The order is deterministic. Items sort by (ambient before replay, name). The playlist then
walks that list. Each call hands out the first item that sorts after the previous one, and
wraps to the start at the end of the list. As a result:

- a pool of two or more items never plays the same item twice in a row
- a one-item pool repeats that item
- an item that is deleted or rejected mid-rotation does not restart the cycle
- two instances built on the same pool produce the same sequence

## Signature

```python
REASON_OFF_HOURS = "off_hours"; REASON_STALL = "stall"

@dataclass(frozen=True)
class PlaylistItem:
    kind: str            # "ambient" | "replay"
    ref: str             # scene id | episode name
    episode: str         # library name to request
    reason: str          # "off_hours" | "stall"
    picked_at: datetime | None = None
    scene_path: str | None = None   # ambient only

class ReplayLibrary:
    def __init__(self, message_api_url="http://127.0.0.1:8090", *, prefix="office-",
                 refresh_s=300.0, timeout_s=10.0, fetch_json=None): ...
    def approved(self, now=None) -> list[str]

@dataclass
class OfficePlaylist:
    ambient_pool: list[AmbientScene] | None = None     # None: load scenes_dir
    library: ReplayLibrary | None = None               # None: ambient only
    scenes_dir: Path | str = DEFAULT_SCENES_DIR
    ambient_requires_approved: bool = False
    ambient_episode_template: str = "office-ambient-{scene_id}"
    def next_item(self, now, reason, phase=None) -> PlaylistItem | None
    def candidates(self, now, reason, phase=None) -> list[PlaylistItem]
    def reload_ambient(self) -> int

class DayRunnerPlaylist:                               # the OB-30 Playlist contract
    def __init__(self, playlist: OfficePlaylist, *, clock=None, tz=None,
                 speed=None, cast=None, worker_name=None): ...
    def off_hours(self, day, context) -> dict | None      # {"episode", "speed"?, "cast"?, "worker_name"?}
    def stall(self, day, context) -> dict | None
    def resume_live(self, day, context) -> None

def day_runner_playlist(agent_config) -> DayRunnerPlaylist    # "office.playlist:day_runner_playlist"

def load_ambient_pool(scenes_dir=DEFAULT_SCENES_DIR) -> list[AmbientScene]
def replay_request_message(item, to, *, from_="office_playlist", cast=None,
                           correlation_id=None, causation_id=None) -> dict
def replay_request_messages(item, targets, **kwargs) -> list[dict]
```

## Parameters

- `now` (aware `datetime` or `None`): stored on the item as `picked_at`. It also drives the
  library cache: the library is re-fetched when the cached list is older than `refresh_s`.
  With `None`, the library is fetched on every call.
- `reason` (str, required): `off_hours` or `stall`. Any other value raises `ValueError`.
- `phase` (str, optional): a `clock.PHASES` value that filters phase-tagged ambient scenes. For
  `off_hours` it defaults to `"off"`. For `stall` it defaults to no filter; pass the current
  phase to apply one.
- `fetch_json(url, params, timeout) -> dict` (injectable): the HTTP GET. It may raise. The
  default uses httpx.
- `ambient_requires_approved`: set this to `True` when ambient scenes air through
  `replay_request` rather than the campaign runtime. A scene is then offered only after its
  built episode, named `office-ambient-<scene_id>`, is approved in the library. With either
  setting, a library row with that name is not offered a second time as a plain replay.
- `to` / `targets`: worker ids. The control panel sends one `replay_request` per worker, and the
  roundtable also gets a `cast`. See `services/control-panel/panel.py` `play_replay`.

## Return Value

`next_item` returns a `PlaylistItem`, or `None` when both pools are empty. `None` is also
logged at WARN.

`replay_request_message` returns a `message_bus.build_message` envelope:

```json
{"id": "...", "from": "office_playlist", "to": "worker1", "type": "replay_request",
 "payload": {"episode": "office-claude_code-sess-001",
             "playlist": {"kind": "replay", "reason": "stall"},
             "cast": {"tuber_0": "worker1"}},
 "timestamp": "...", "correlation_id": "...", "causation_id": null}
```

`cast` is included only when you pass one. `agent_handlers/replay_relay.handle_replay_request`
reads only `episode` and `cast`, so the extra `playlist` block is harmless.

## Wiring it into the day runner (OB-30)

The day runner loads its playlist from config and calls it with its own
`Playlist` contract (docs/office_day_runner.md). `day_runner_playlist` is that
factory:

```yaml
agent:
  office:
    day_runner:
      playlist: office.playlist:day_runner_playlist
      playlist_options:            # all optional
        message_api_url: http://message-api:8000   # else env MESSAGE_API_URL, else 127.0.0.1:8090
        scenes_dir: /campaigns/ashiorid_office/scenes   # else $OFFICE_PACK_DIR/scenes, else the repo pack
        refresh_s: 300             # library cache
        timeout_s: 10
        prefix: office-
        library: true              # false: ambient pool only (empty under the approved-only rule)
        speed: 1.0                 # copied into every request
        cast: {tuber_0: roundtable}
        worker_name: Ashiorid
```

`DayRunnerPlaylist` maps each call onto `next_item`:

| Runner call | `next_item` reason | Phase filter |
|---|---|---|
| `off_hours(day, ctx)` (00:00, once per s0) | `off_hours` | `off` |
| `stall(day, ctx)` (N idle minutes) | `stall` | the office phase of `now` in `tz` (`clock.PHASES[hour // 6]`) |
| `resume_live(day, ctx)` (day_start) | none | logged only; the pane finishes its episode |

It answers `{"episode": item.episode}` plus `speed` / `cast` / `worker_name`
when configured, or `None` when the pool is empty. The runner then builds and
sends the `replay_request` itself (to `replay_target`, default the CEO worker),
and `agent_handlers.replay_relay.handle_replay_request` writes the replay
pane's request file. `now` comes from the adapter's `clock` (default UTC
now), since the runner's context has no timestamp. `tz` is
`day_runner.tz`, else `agent.office.tz`, else America/New_York.

**Ambient items and the approved-only rule.** The runner can only ask the
replay pane for a library name; it cannot play a scene YAML
(`scene_path`) through the campaign runtime. The adapter therefore forces
`ambient_requires_approved=True` on the playlist it wraps: an ambient scene
is offered only once its built episode (`office-ambient-<scene_id>`) is
approved in the library, and the request names that library episode. An
unbuilt scene, or a draft, is never requested. A library row belonging to an
ambient scene is never offered a second time as a plain replay, even when
the scene itself is filtered out by phase.

For a caller that sends its own messages (not the day runner),
`replay_request_messages(item, WORKER_IDS)` builds one request per worker.
A `replay_stop` should go before each request, as the control panel's Play
does, so that the request takes over from whatever is on air instead of
queuing behind it. Sending that stop is the caller's job; the day runner
does not send one.

## Dependencies

- `message_bus.build_message`
- PyYAML, imported lazily and only for the ambient pool
- httpx, imported lazily and only for the default fetcher
- message-api `GET /replays` (`services/message-api/api.py`)

## Usage Examples

```python
playlist = OfficePlaylist(library=ReplayLibrary("http://127.0.0.1:8090"))
item = playlist.next_item(datetime.now(timezone.utc), REASON_OFF_HOURS)
if item:
    producer.send(replay_request_message(item, "worker1"))
```

```python
# Tests / offline: no library, a hand-built pool
pool = [AmbientScene("coffee-machine", Path("a001-coffee-machine.yaml"))]
OfficePlaylist(ambient_pool=pool).next_item(None, REASON_STALL).ref   # "coffee-machine"
```

```python
# The day runner's adapter, built by hand (tests)
adapter = DayRunnerPlaylist(OfficePlaylist(ambient_pool=[], library=ReplayLibrary(url)),
                            clock=lambda: now, speed=1.0)
adapter.off_hours("2026-09-29", {"worker_id": "tuber_0", "reason": "off_hours"})
# -> {"episode": "office-claude_code-sess-001", "speed": 1.0}
```

## Error Handling

- A bad `reason` raises `ValueError`. So does `replay_request_message` with an empty `to`.
- An unreachable library, or a malformed reply from it, logs a WARN, and the last good list is
  kept. If there never was a good list, only ambient scenes are offered.
- An unreadable or non-mapping ambient YAML, or a duplicate scene id, logs a WARN and the scene
  is skipped. A missing scenes directory gives an empty pool.
- YAML 1.1 reads a bare `off` as `False`. `phases: [off]` is mapped back to `"off"`.
- `DayRunnerPlaylist` with an unknown `tz` raises `ValueError` (the runner's
  `build_day_runner` then logs `plugin_unavailable` and runs without a playlist).
  A non-mapping `playlist_options` logs a WARN and is ignored.

## Changelog

- **v1.0.0** (2026-09-28): Initial version (OB-33). Ambient-first then approved-replay
  rotation, drafts excluded, no back-to-back repeats, library cache with last-good fallback,
  `replay_request` builders. Tests: `tests/test_office_playlist.py`.
- **v1.1.0** (2026-09-28): `DayRunnerPlaylist` + `day_runner_playlist(agent_config)` factory:
  the OB-30 `Playlist` contract (`off_hours` / `stall` / `resume_live`), config via
  `agent.office.day_runner.playlist_options`, message-api URL from config or `MESSAGE_API_URL`.
  Ambient scenes air through the adapter only once their built episode is approved. Fix: an
  ambient scene's library episode no longer comes back as a plain replay when the scene is
  filtered out by phase.
