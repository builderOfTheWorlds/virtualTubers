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

```python
from office.playlist import OfficePlaylist, ReplayLibrary, REASON_OFF_HOURS, REASON_STALL, \
    replay_request_messages

playlist = OfficePlaylist(library=ReplayLibrary(os.environ.get("MESSAGE_API_URL",
                                                               "http://127.0.0.1:8090")))

def playlist_callback(now, reason):          # reason: "off_hours" | "stall"
    item = playlist.next_item(now, reason)
    if item is None:
        return []                            # nothing to air: stay on the idle scene
    return replay_request_messages(item, WORKER_IDS)   # the caller sends them
```

A `replay_stop` should go before each request, as the control panel's Play does, so that the
request takes over from whatever is on air instead of queuing behind it. Sending that stop is the
caller's job.

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

## Error Handling

- A bad `reason` raises `ValueError`. So does `replay_request_message` with an empty `to`.
- An unreachable library, or a malformed reply from it, logs a WARN, and the last good list is
  kept. If there never was a good list, only ambient scenes are offered.
- An unreadable or non-mapping ambient YAML, or a duplicate scene id, logs a WARN and the scene
  is skipped. A missing scenes directory gives an empty pool.
- YAML 1.1 reads a bare `off` as `False`. `phases: [off]` is mapped back to `"off"`.

## Changelog

- **v1.0.0** (2026-09-28): Initial version (OB-33). Ambient-first then approved-replay
  rotation, drafts excluded, no back-to-back repeats, library cache with last-good fallback,
  `replay_request` builders. Tests: `tests/test_office_playlist.py`.
