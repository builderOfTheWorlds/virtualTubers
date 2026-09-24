#!/usr/bin/env python3
"""
console_theme.py
Makes the tuber console's color scheme configurable from a worker's YAML
config AND switchable live via message-api's /console-theme endpoints — the
same "config default, Redis override, fail open on read" shape as
worker_control.py and log_filter_control.py.

Theme data (config/themes/gogh_themes.json) is a straight scrape of every
scheme from https://github.com/Gogh-Co/Gogh's data/themes.json (1247 schemes,
the same list the gogh.website picker offers) — 16 ANSI colors plus
background/foreground/cursor per theme. "Builtin Solarized Dark" is Gogh's
name for the scheme startup.sh shipped with directly (2026-09-24 UI pass);
DEFAULT_THEME_NAME points at it so an unconfigured worker keeps that look.

Two consumers:
  * startup.sh (boot): `console_theme.py --config <path> --xterm-args`
    prints shell-quoted `-xrm 'XTerm*colorN: #rrggbb'` args, eval'd straight
    into the xterm launch command (same eval-a-generated-script pattern as
    build_layout.py's emit_tmux).
  * theme_watcher.py (live): resolves the CURRENT theme (Redis first) on a
    poll loop and, on change, prints an OSC escape sequence that repaints a
    already-running xterm in place — no restart, no reconnect.
"""
import argparse
import json
import logging
import os
import shlex
import sys
from pathlib import Path

import redis

log = logging.getLogger("console_theme")

THEMES_PATH = Path(__file__).resolve().parent.parent / "config" / "themes" / "gogh_themes.json"

#: Gogh's name for the exact scheme startup.sh used directly before this
#: module existed (2026-09-24 Solarized Dark UI pass) — kept as the fallback
#: so an unconfigured worker's look does not change under this refactor.
DEFAULT_THEME_NAME = "Builtin Solarized Dark"

KEY_PREFIX = "console"
KEY_SUFFIX = "theme"

_CACHE = None


def load_themes(path=THEMES_PATH):
    """Return {name: theme_dict} for every scheme in the Gogh dump.

    Cached at module scope — this file is ~460KB and both consumers above
    call the loader once per process, not once per pane.
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    with open(path, "r", encoding="utf-8") as f:
        themes = json.load(f)
    _CACHE = {t["name"]: t for t in themes}
    return _CACHE


def get_theme(name, themes=None, default=DEFAULT_THEME_NAME):
    """Look up a theme by name, case-insensitively, falling back to
    `default` (and logging a warning) if `name` doesn't match anything —
    a typo'd theme name must never crash a worker's boot or an API call.
    Raises KeyError only if `default` itself isn't a valid theme name
    (i.e. a genuinely corrupt theme file), never for a bad `name`."""
    themes = themes if themes is not None else load_themes()
    if name in themes:
        return themes[name]
    lowered = {k.lower(): v for k, v in themes.items()}
    if name and name.lower() in lowered:
        return lowered[name.lower()]
    if name:
        log.warning("get_theme(%r) not found; falling back to %r", name, default)
    return themes[default]


def theme_exists(name, themes=None):
    """Strict, case-insensitive membership check — unlike get_theme, this
    does NOT fall back, so callers (the API's set endpoint) can tell a typo
    apart from a legitimate lookup."""
    themes = themes if themes is not None else load_themes()
    if name in themes:
        return True
    return bool(name) and name.lower() in {k.lower() for k in themes}


# ── config-file default ──────────────────────────────────────────────────────
def resolve_config_theme_name(config):
    """`console.theme` in a worker YAML config, or None if unset."""
    return (config or {}).get("console", {}).get("theme")


# ── live override (Redis) ────────────────────────────────────────────────────
def resolve_redis_url(config=None, env_name="REDIS_URL", default="redis://redis:6379"):
    config_value = (config or {}).get("world_state", {}).get("redis_url")
    return os.environ.get(env_name) or config_value or default


class ConsoleThemeControl:
    """Redis-backed active-theme override, one key per worker
    (console:{worker_id}:theme). Reads fail open to `default` — a
    control-plane outage must never blank a live avatar pane's colors.
    Writes do NOT fail open; set_theme raises on redis.RedisError so the
    API layer can tell the operator the switch didn't take effect."""

    def __init__(self, redis_url, socket_timeout=2):
        self._client = redis.Redis.from_url(
            redis_url,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_timeout,
            decode_responses=True,
        )

    @classmethod
    def from_config(cls, config=None):
        return cls(resolve_redis_url(config))

    def _key(self, worker_id):
        return f"{KEY_PREFIX}:{worker_id}:{KEY_SUFFIX}"

    def get_theme_name(self, worker_id, default=None):
        try:
            value = self._client.get(self._key(worker_id))
        except redis.RedisError as exc:
            log.warning("redis unreachable, failing open to %r for %s: %s",
                        default, worker_id, exc)
            return default
        return value if value else default

    def set_theme_name(self, worker_id, theme_name):
        self._client.set(self._key(worker_id), theme_name)
        return theme_name

    def clear_theme_name(self, worker_id):
        """Drop the override so the worker falls back to its config default."""
        self._client.delete(self._key(worker_id))


def resolve_active_theme_name(worker_id, config=None, control=None):
    """Precedence: Redis override -> config `console.theme` -> DEFAULT_THEME_NAME.

    `control=None` is a valid, common case (startup.sh's one-shot boot-time
    resolution has no reason to open a Redis connection just to immediately
    fall back) — it simply skips the Redis tier.
    """
    if control is not None:
        redis_value = control.get_theme_name(worker_id)
        if redis_value:
            return redis_value
    config_value = resolve_config_theme_name(config)
    if config_value:
        return config_value
    return DEFAULT_THEME_NAME


# ── xterm rendering (boot-time -xrm args) ────────────────────────────────────
#: color_01..color_16 in the Gogh dump map onto xterm's colorN in order.
_XTERM_COLOR_COUNT = 16


def xterm_xrm_pairs(theme):
    """[("XTerm*background", "#rrggbb"), ("XTerm*colorN", "#rrggbb"), ...]
    in the exact order startup.sh's xterm invocation wants them."""
    pairs = [
        ("XTerm*background", theme["background"]),
        ("XTerm*foreground", theme["foreground"]),
        ("XTerm*cursorColor", theme["cursor"]),
    ]
    for i, color in enumerate(theme["colors"][:_XTERM_COLOR_COUNT]):
        pairs.append((f"XTerm*color{i}", color))
    return pairs


def xterm_xrm_shell_args(theme):
    """Shell-quoted `-xrm 'XTerm*colorN: #rrggbb' \\` lines, one per line,
    ready to interpolate into startup.sh's xterm command via `eval`."""
    lines = []
    for key, value in xterm_xrm_pairs(theme):
        lines.append(f"    -xrm {shlex.quote(f'{key}: {value}')} \\")
    return "\n".join(lines)


# ── live re-theme (OSC escape sequences) ─────────────────────────────────────
#: OSC 10/11/12 set foreground/background/cursor; OSC 4 sets one indexed
#: color. All four are ST-terminated (\x1b\\) rather than BEL (\x07) —
#: matches what xterm itself emits and is unambiguous inside a shell
#: printf string. xterm honors these on an already-open window with no
#: restart, which is the entire point of doing this instead of relaunching.
def osc_sequence(theme):
    parts = [
        f"\x1b]10;{theme['foreground']}\x1b\\",
        f"\x1b]11;{theme['background']}\x1b\\",
        f"\x1b]12;{theme['cursor']}\x1b\\",
    ]
    for i, color in enumerate(theme["colors"][:_XTERM_COLOR_COUNT]):
        parts.append(f"\x1b]4;{i};{color}\x1b\\")
    return "".join(parts)


def _main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="worker YAML config path (for console.theme)")
    parser.add_argument("--theme", help="theme name override (skips config lookup)")
    parser.add_argument("--xterm-args", action="store_true",
                         help="print -xrm args for startup.sh's xterm launch")
    parser.add_argument("--osc", action="store_true",
                         help="print a raw OSC escape sequence for a live retheme")
    parser.add_argument("--name", action="store_true",
                         help="print only the resolved theme's name")
    parser.add_argument("--list", action="store_true", help="print every theme name")
    args = parser.parse_args()

    if args.list:
        for name in sorted(load_themes()):
            print(name)
        return

    config = None
    if args.config:
        import yaml
        with open(args.config, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

    name = args.theme or resolve_config_theme_name(config) or DEFAULT_THEME_NAME
    theme = get_theme(name)

    if args.xterm_args:
        print(xterm_xrm_shell_args(theme))
    elif args.osc:
        sys.stdout.write(osc_sequence(theme))
        sys.stdout.flush()
    elif args.name:
        print(theme["name"])
    else:
        print(json.dumps(theme, indent=2))


if __name__ == "__main__":
    _main()
