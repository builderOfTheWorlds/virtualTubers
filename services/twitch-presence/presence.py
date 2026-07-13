#!/usr/bin/env python3
"""
presence.py
Twitch viewer-presence watcher: connects to Twitch chat (IRC) anonymously —
no OAuth token or Twitch app needed to read — joins each configured channel,
and whenever a viewer shows up in a channel's chat POSTs a `viewer_joined`
message to message-api (POST /messages), addressed to the worker that streams
on that channel. The worker's agent then greets the viewer on stream
(agent.py `handle_viewer_joined`).

Channel -> worker mapping comes from TWITCH_CHANNEL_MAP, e.g.
`mycoderchannel:coder,mymanagerchannel:manager`. With no map configured the
service idles with a periodic log line instead of exiting, so the container
doesn't restart-loop until the operator sets the env var.

What "a viewer starts watching" means here: Twitch has no per-viewer
"started watching" event. The closest real signal is the chat JOIN — emitted
when a viewer's client connects to the channel's chat (which the normal web
player does automatically). Caveats: Twitch batches JOIN/PART notifications
(they can lag ~10s-10min behind the actual arrival), and a viewer watching
logged-out/embedded without a chat connection never appears. A per-user
cooldown (PRESENCE_COOLDOWN_S) stops rejoin flapping — and the mass re-JOIN
Twitch sends after every reconnect — from re-greeting the same person.
"""
import os
import random
import socket
import time
import urllib.error
import urllib.request
import json

IRC_HOST = "irc.chat.twitch.tv"
IRC_PORT = 6667

DEFAULT_MESSAGE_API_URL = "http://message-api:8000/messages"
DEFAULT_COOLDOWN_S = 3600
IDLE_LOG_INTERVAL_S = 300

# Twitch pings roughly every 5 minutes; a socket silent for longer than this
# is a dead connection — recv times out and we reconnect.
SOCKET_TIMEOUT_S = 420
RECONNECT_BACKOFF_S = [5, 15, 60, 120]

# Ubiquitous channel/service bots — never worth greeting. Extend (not
# replace) via PRESENCE_IGNORE_USERS.
DEFAULT_IGNORED_USERS = {
    "nightbot",
    "streamelements",
    "streamlabs",
    "moobot",
    "fossabot",
    "wizebot",
    "soundalerts",
    "sery_bot",
    "commanderroot",
    "anotherttvviewer",
    "lurxx",
}


def log(msg):
    print(f"[twitch-presence] {msg}", flush=True)


def parse_channel_map(raw):
    """Parse TWITCH_CHANNEL_MAP (`channel:worker_id,channel2:worker2`) into
    {channel: worker_id}. Channels are lowercased with any leading '#'
    stripped (Twitch channel names are case-insensitive; IRC JOIN lines
    arrive lowercase). Malformed entries are skipped with a log line rather
    than failing startup — one typo must not take the whole watcher down.
    """
    channels = {}
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            log(f"WARN ignoring malformed TWITCH_CHANNEL_MAP entry {entry!r} (expected channel:worker_id)")
            continue
        channel, worker_id = entry.split(":", 1)
        channel = channel.strip().lstrip("#").lower()
        worker_id = worker_id.strip()
        if not channel or not worker_id:
            log(f"WARN ignoring malformed TWITCH_CHANNEL_MAP entry {entry!r} (empty channel or worker)")
            continue
        channels[channel] = worker_id
    return channels


def parse_ignored_users(raw):
    """PRESENCE_IGNORE_USERS extends (never replaces) the default bot list."""
    ignored = set(DEFAULT_IGNORED_USERS)
    for name in (raw or "").split(","):
        name = name.strip().lower()
        if name:
            ignored.add(name)
    return ignored


def parse_join(line):
    """Extract (username, channel) from a Twitch IRC membership JOIN line:

        :user!user@user.tmi.twitch.tv JOIN #channel

    Returns None for anything else (PRIVMSG, PART, numerics, the 353 NAMES
    list, ...).
    """
    parts = line.split(" ")
    if len(parts) < 3 or parts[1] != "JOIN":
        return None
    prefix = parts[0]
    if not prefix.startswith(":") or "!" not in prefix:
        return None
    username = prefix[1:].split("!", 1)[0].lower()
    channel = parts[2].lstrip(":").lstrip("#").lower()
    if not username or not channel:
        return None
    return username, channel


class GreetingCooldown:
    """Remembers when each (channel, user) was last announced so rejoin
    flapping — and the mass re-JOIN after every IRC reconnect — doesn't
    re-greet the same viewer. `clock` is injectable for tests."""

    def __init__(self, cooldown_s, clock=time.monotonic):
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._last_seen = {}

    def should_greet(self, channel, username):
        now = self._clock()
        key = (channel, username)
        last = self._last_seen.get(key)
        if last is not None and (now - last) < self.cooldown_s:
            return False
        self._last_seen[key] = now
        return True


def post_viewer_joined(api_url, worker_id, username, channel, timeout_s=10):
    """POST a viewer_joined message to message-api. Best-effort: a down or
    unreachable API logs and returns False — the watcher must keep following
    chat regardless (same fire-and-forget contract as the rest of the
    project's side channels)."""
    body = json.dumps({
        "to": worker_id,
        "type": "viewer_joined",
        "payload": {"username": username, "channel": channel},
    }).encode("utf-8")
    request = urllib.request.Request(
        api_url, data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            response.read()
        return True
    except (urllib.error.URLError, OSError) as exc:
        log(f"ERROR posting viewer_joined for {username!r} -> {worker_id}: {exc}")
        return False


class PresenceWatcher:
    """One anonymous IRC connection covering every configured channel."""

    def __init__(self, channel_map, api_url, cooldown):
        self.channel_map = channel_map
        self.api_url = api_url
        self.cooldown = cooldown
        self.ignored_users = parse_ignored_users(os.environ.get("PRESENCE_IGNORE_USERS"))
        # Anonymous read-only login — any justinfan<digits> nick, no password.
        self.nick = f"justinfan{random.randint(10000, 99999)}"

    def connect(self):
        sock = socket.create_connection((IRC_HOST, IRC_PORT), timeout=SOCKET_TIMEOUT_S)
        # Membership capability = JOIN/PART events for other users; without
        # it Twitch only ever shows our own JOIN.
        sock.sendall(b"CAP REQ :twitch.tv/membership\r\n")
        sock.sendall(f"NICK {self.nick}\r\n".encode("utf-8"))
        joins = ",".join(f"#{channel}" for channel in sorted(self.channel_map))
        sock.sendall(f"JOIN {joins}\r\n".encode("utf-8"))
        log(f"connected as {self.nick}, joined {joins}")
        return sock

    def handle_line(self, sock, line):
        """Returns False when the server asked us to reconnect."""
        if line.startswith("PING"):
            sock.sendall(line.replace("PING", "PONG", 1).encode("utf-8") + b"\r\n")
            return True
        if " RECONNECT" in line:
            log("server sent RECONNECT — reconnecting")
            return False

        joined = parse_join(line)
        if joined is None:
            return True
        username, channel = joined
        worker_id = self.channel_map.get(channel)
        if worker_id is None:
            return True
        if username == self.nick or username in self.ignored_users:
            return True
        if not self.cooldown.should_greet(channel, username):
            return True

        log(f"viewer {username!r} joined #{channel} -> announcing to {worker_id}")
        post_viewer_joined(self.api_url, worker_id, username, channel)
        return True

    def run_connection(self, sock):
        """Read the connection until it dies or asks for a reconnect."""
        buffer = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                log("connection closed by server")
                return
            buffer += chunk
            while b"\r\n" in buffer:
                raw, buffer = buffer.split(b"\r\n", 1)
                line = raw.decode("utf-8", errors="replace")
                if not self.handle_line(sock, line):
                    return

    def run_forever(self):
        attempt = 0
        while True:
            try:
                sock = self.connect()
                attempt = 0  # a successful connect resets the backoff ladder
                try:
                    self.run_connection(sock)
                finally:
                    sock.close()
            except OSError as exc:
                log(f"ERROR connection failed: {exc}")
            backoff = RECONNECT_BACKOFF_S[min(attempt, len(RECONNECT_BACKOFF_S) - 1)]
            attempt += 1
            log(f"reconnecting in {backoff}s")
            time.sleep(backoff)


def main():
    api_url = os.environ.get("MESSAGE_API_URL") or DEFAULT_MESSAGE_API_URL
    cooldown_s = float(os.environ.get("PRESENCE_COOLDOWN_S") or DEFAULT_COOLDOWN_S)
    channel_map = parse_channel_map(os.environ.get("TWITCH_CHANNEL_MAP"))

    if not channel_map:
        # Idle instead of exiting: an exit under `restart: unless-stopped`
        # is a restart loop. The operator sets TWITCH_CHANNEL_MAP in the
        # stack env and redeploys to activate.
        log("TWITCH_CHANNEL_MAP not set (channel:worker_id,...) — idling until configured")
        while True:
            time.sleep(IDLE_LOG_INTERVAL_S)
            log("idle — TWITCH_CHANNEL_MAP still not set")

    log(f"watching {len(channel_map)} channel(s): "
        + ", ".join(f"#{c} -> {w}" for c, w in sorted(channel_map.items())))
    log(f"message-api: {api_url}, greeting cooldown: {cooldown_s:.0f}s")

    watcher = PresenceWatcher(channel_map, api_url, GreetingCooldown(cooldown_s))
    watcher.run_forever()


if __name__ == "__main__":
    main()
