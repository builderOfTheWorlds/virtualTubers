# services/twitch-presence/presence.py

## Overview

Watches each worker's Twitch channel and announces arriving viewers to the
worker team, so the agents greet them on stream. It connects to Twitch chat
over IRC **anonymously** (a `justinfan<digits>` nick — no OAuth token, no
Twitch app registration, read-only), requests the *membership* capability so
Twitch sends JOIN/PART events for other users, joins every configured
channel on one connection, and — whenever a viewer JOINs a channel's chat —
POSTs a `viewer_joined` message to `message-api` (`POST /messages`,
docs/message_api.md) addressed to the worker that streams on that channel.
The worker's agent handles it with `handle_viewer_joined` (docs/agent.md):
it **starts a rerun** — a Rerun Theater episode picked at random from the
worker's library, queued for the replay pane exactly like a
`replay_request` — and greets the viewer with an LLM-written in-character
welcome (console + avatar speech bubble) introducing the show. No bus
reply either way. The rerun only actually plays on workers whose layout
includes the replay pane (`LAYOUT_PRESET=replay`, docs/replay_pane.md);
elsewhere the queued request file is simply never picked up.

**What "a viewer starts watching" actually means here** — Twitch has no
per-viewer "started watching" event. The closest real, per-user signal is
the chat JOIN, which the normal Twitch web player fires automatically when
a logged-in viewer opens the stream (they don't have to type anything).
Two caveats:

- Twitch **batches** JOIN/PART notifications — an arrival can show up
  seconds to several minutes late.
- A viewer watching logged-out, or via an embed with no chat connection,
  never appears at all.

A per-`(channel, user)` cooldown stops rejoin flapping — and the mass
re-JOIN Twitch sends after every IRC reconnect — from re-greeting the same
person; a built-in (extendable) ignore list drops the ubiquitous chat bots
(`nightbot`, `streamelements`, …) and the watcher's own nick.

Like the project's other side channels, everything is fire-and-forget: a
down `message-api` logs an error and the watcher keeps following chat; a
dead IRC connection reconnects with capped backoff; a missing
`TWITCH_CHANNEL_MAP` idles with a periodic log line instead of exiting (an
exit under `restart: unless-stopped` would be a restart loop).

## Signature

```python
def parse_channel_map(raw: str | None) -> dict[str, str]

def parse_ignored_users(raw: str | None) -> set[str]

def parse_join(line: str) -> tuple[str, str] | None

class GreetingCooldown:
    def __init__(self, cooldown_s: float, clock=time.monotonic)
    def should_greet(self, channel: str, username: str) -> bool

def post_viewer_joined(api_url: str, worker_id: str, username: str,
                       channel: str, timeout_s: float = 10) -> bool

class PresenceWatcher:
    def __init__(self, channel_map: dict[str, str], api_url: str,
                 cooldown: GreetingCooldown)
    def connect(self) -> socket.socket
    def handle_line(self, sock, line: str) -> bool
    def run_connection(self, sock) -> None
    def run_forever(self) -> None

def main() -> None
```

## Parameters

Environment variables (the whole configuration surface):

- `TWITCH_CHANNEL_MAP` (required to do anything) — comma-separated
  `channel:worker_id` pairs, e.g.
  `mycoderchannel:coder,mymanagerchannel:manager`. Channel names are
  case-insensitive; a leading `#` is tolerated. Malformed entries are
  logged and skipped, never fatal. Unset/empty → the service idles.
- `MESSAGE_API_URL` (optional, default `http://message-api:8000/messages`) —
  where to POST `viewer_joined` messages. The compose default points at the
  in-stack `message-api` service port, not the host-published `8090`.
- `PRESENCE_COOLDOWN_S` (optional, default `3600`) — seconds before the same
  viewer in the same channel is greeted again.
- `PRESENCE_IGNORE_USERS` (optional) — comma-separated usernames to ignore,
  **extending** (never replacing) the built-in `DEFAULT_IGNORED_USERS` bot
  list.

Function/class parameters:

- `raw` (`parse_channel_map` / `parse_ignored_users`) — the raw env string;
  `None`/empty yields an empty map / the default bot set.
- `line` (`parse_join` / `handle_line`) — one decoded IRC line (no CRLF).
  `parse_join` returns `(username, channel)` for a membership JOIN
  (`:user!user@user.tmi.twitch.tv JOIN #channel`), else `None`.
- `cooldown_s` / `clock` (`GreetingCooldown`) — window size and an
  injectable monotonic clock (tests pass a fake).
- `api_url`, `worker_id`, `username`, `channel`, `timeout_s`
  (`post_viewer_joined`) — POST target and the message fields; see Return
  Value for the wire shape.

## Return Value

- `parse_channel_map` — `{channel: worker_id}`, channels lowercased.
- `parse_join` — `(username, channel)` or `None`.
- `GreetingCooldown.should_greet` — `True` exactly when this
  `(channel, username)` hasn't been announced within the window; a `True`
  also records the announcement.
- `post_viewer_joined` — `True` on HTTP success, `False` on any
  network/HTTP error (logged, never raised). The message it sends:

  ```json
  {"to": "<worker_id>", "type": "viewer_joined",
   "payload": {"username": "<viewer>", "channel": "<channel>"}}
  ```

- `PresenceWatcher.handle_line` — `False` only when the server sent
  `RECONNECT` (caller must drop and redial); `True` otherwise. Also answers
  `PING` with `PONG` inline.
- `run_forever` / `main` — never return.

## Dependencies

- Python stdlib only: `socket` (IRC), `urllib.request` (message-api POST),
  `json`, `os`, `time`, `random`.
- Runtime: the `message-api` service (docs/message_api.md) and outbound
  reachability to `irc.chat.twitch.tv:6667`.
- Consumed by: `app/agent.py`'s `handle_viewer_joined` (docs/agent.md).

## Usage Examples

Set the channel map in the Portainer stack env (or `.env` locally) and
redeploy — each channel is the Twitch channel that worker streams to:

```bash
TWITCH_CHANNEL_MAP=mycoderchannel:coder,mymanagerchannel:manager,mytesterchannel:tester
PRESENCE_COOLDOWN_S=3600
```

Watch it work (viewer `phil` opens the coder's stream):

```bash
docker logs virtualtubers-twitch-presence-1
# [twitch-presence] connected as justinfan48214, joined #mycoderchannel,#mymanagerchannel,#mytesterchannel
# [twitch-presence] viewer 'phil' joined #mycoderchannel -> announcing to coder
```

The `viewer_joined` message appears on the Kafka feed pane (highlighted
cyan) and the coder greets phil in its console/avatar bubble.

Test the worker-side greeting without Twitch by injecting the same message
by hand:

```bash
curl -X POST http://localhost:8090/messages \
  -H "Content-Type: application/json" \
  -d '{"to": "coder", "type": "viewer_joined",
       "payload": {"username": "phil", "channel": "mycoderchannel"}}'
```

## Error Handling

- `TWITCH_CHANNEL_MAP` unset/empty — logs and idles (periodic log line
  every 5 minutes); never exits.
- Malformed channel-map entry — logged and skipped; valid entries still run.
- IRC connect failure / connection drop / server `RECONNECT` — reconnects
  with backoff (5s → 15s → 60s → 120s cap; reset after a successful
  connect). A socket silent for longer than `SOCKET_TIMEOUT_S` (7 min —
  Twitch pings ~every 5) times out and reconnects.
- `message-api` unreachable or non-2xx — `post_viewer_joined` logs `ERROR`
  and returns `False`; chat following continues.
- Undecodable bytes on the wire — replaced (`errors="replace"`), never
  fatal.

## Changelog

- v1.0.0 (2026-07-12) — Initial version: anonymous Twitch IRC membership
  watcher, per-user greeting cooldown, bot ignore list, fire-and-forget
  `viewer_joined` POSTs to message-api.
