#!/usr/bin/env python3
"""scripts/golive_watchdog.py: unattended health watchdog for the 7-day ashiorid_office broadcast.

One pass per systemd user-timer tick (every 2 min): CHECK -> bounded REMEDIATE -> LOG.

    .venv/bin/python scripts/golive_watchdog.py --dry-run     # read-only, prints planned actions
    .venv/bin/python scripts/golive_watchdog.py               # one real pass (what the timer runs)

Checks (each wrapped so one crash never stops the others):
  1 Ollama     loaded model -> unload (keep_alive 0); MemAvailable <8 GiB alert, <4 GiB restart
               the largest worker container (never vLLM)
  2 vLLM       GET /v1/models (key read from the container at runtime); down 2 passes -> docker start
  3 containers the 9 broadcast workers must run; exited -> docker start with backoff, 3/hour max
  4 ffmpeg     per worker; missing or crash-looping -> alert, 3 bad passes -> docker restart
  5 Twitch     raw GQL per channel (channels.yaml), distrusted when the lofigirl probe reads offline
  6 table      run must be active/auto (table_run_start with backoff); stalled 30 min -> restart GM
  7 tile RSS   tile_pane in the roundtable: >3 GiB alert, >6 GiB restart roundtable
  8 disk       root fs >93% alert

Safety: every docker call goes through Watchdog.docker(): an allow-list of verbs (start, restart,
exec, inspect, stats), a managed-container allow-list, a hard deny on anything hermes3, per-reason caps
and one global rolling-hour limit. No compose, no stop/rm/kill, never vllm-hermes3-70b, Ollama is only
ever unloaded. `--dry-run` performs no state write, no event-log write and no mutation.
Stdlib only. Pause it with `touch ~/.local/state/office-watchdog/PAUSE` (see RUNBOOK.md).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
GiB = 1024 ** 3
HOUR = 3600
DEFAULT_STATE_DIR = Path(os.environ.get("OFFICE_WATCHDOG_STATE_DIR",
                                        "~/.local/state/office-watchdog")).expanduser()
DEFAULT_CHANNELS = REPO_ROOT / ".claude" / "prompts" / "golive" / "channels.yaml"
TWITCH_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
TWITCH_GQL_URL = "https://gql.twitch.tv/gql"
VLLM_CONTAINER = "vllm-agents"
VLLM_MODEL = "table-agents"
FORBIDDEN_SUBSTRINGS = ("hermes3",)     # vllm-hermes3-70b is never touched, by any verb
READ_VERBS = {"exec", "inspect", "stats"}
MUTATING_VERBS = {"start", "restart"}


@dataclass(frozen=True)
class Worker:
    worker_id: str
    container: str
    display: str


# Order = roundtable first, then GM, then the six characters, then the observer.
WORKERS = (
    Worker("roundtable", "virtualtubers-worker-roundtable-1", ":106"),
    Worker("tuber_0", "virtualtubers-worker-gm-1", ":105"),
    Worker("tuber_1", "virtualtubers-worker-manager-1", ":100"),
    Worker("tuber_2", "virtualtubers-worker-coder-native-1", ":102"),
    Worker("tuber_3", "virtualtubers-worker-coder-aider-1", ":104"),
    Worker("tuber_4", "virtualtubers-worker-tester-1", ":101"),
    Worker("tuber_5", "virtualtubers-worker-coder-opencode-1", ":103"),
    Worker("tuber_6", "virtualtubers-worker-coder-1", ":99"),
    Worker("tuber_7", "virtualtubers-worker-observer-1", ":107"),
)
WORKER_BY_ID = {w.worker_id: w for w in WORKERS}
WORKER_BY_CONTAINER = {w.container: w for w in WORKERS}
# Alert-only (the watchdog never starts these): the table status path depends on them.
SUPPORT_CONTAINERS = ("virtualtubers-message-api-1", "virtualtubers-message-logger-1",
                      "virtualtubers-kafka-1", "virtualtubers-campaign-manager-1")
MANAGED_CONTAINERS = {w.container for w in WORKERS} | {VLLM_CONTAINER}
READABLE_CONTAINERS = MANAGED_CONTAINERS | set(SUPPORT_CONTAINERS)


@dataclass
class Config:
    ollama_url: str = "http://127.0.0.1:11434"
    vllm_url: str = "http://127.0.0.1:8092"
    message_api_url: str = "http://localhost:8090"
    gm_worker: str = "tuber_0"
    roundtable_worker: str = "roundtable"
    channels_path: Path = DEFAULT_CHANNELS
    state_dir: Path = DEFAULT_STATE_DIR
    # memory / disk
    mem_alert_bytes: int = 8 * GiB
    mem_restart_bytes: int = 4 * GiB
    disk_alert_pct: float = 93.0
    # vLLM
    vllm_down_passes: int = 2
    vllm_wait_s: float = 300.0
    vllm_poll_s: float = 10.0
    vllm_hung_alert_passes: int = 8
    # containers / ffmpeg
    grace_s: float = 150.0                 # a freshly (re)started container gets this long to boot
    start_backoff_base_s: float = 60.0     # gap before the n-th start in the hour: base * 2**(n-1)
    start_backoff_max_s: float = 1800.0
    ffmpeg_restart_passes: int = 3
    ffmpeg_reset_tolerance_s: float = 20.0
    # twitch
    probe_login: str = "lofigirl"
    twitch_offline_passes: int = 3
    # table
    table_stuck_s: float = 1800.0
    table_status_timeout_s: float = 10.0
    table_start_timeout_s: float = 45.0
    table_start_backoff_base_s: float = 240.0   # attempt n waits base*2**n: 4, 8, 16... min, cap 1 h
    table_start_backoff_max_s: float = 3600.0
    # tiles
    tile_alert_bytes: int = 3 * GiB
    tile_restart_bytes: int = 6 * GiB
    # bounded remediation: (max actions, window seconds) per (container, reason)
    start_cap: tuple = (3, HOUR)
    vllm_start_cap: tuple = (3, HOUR)
    ffmpeg_cap: tuple = (1, HOUR)
    twitch_cap: tuple = (1, HOUR)
    table_cap: tuple = (1, 2 * HOUR)
    tile_cap: tuple = (1, 2 * HOUR)
    memory_cap: tuple = (1, HOUR // 2)
    global_docker_per_hour: int = 16
    action_gap_s: float = 2.0              # pause between two mutating docker calls
    alert_repeat_s: float = 1800.0
    log_max_bytes: int = 20 * 1024 * 1024


# --------------------------------------------------------------------------------------------
# alert sink (pluggable)
# --------------------------------------------------------------------------------------------
def default_alert_sink(event: dict) -> None:
    """Alerts are always written to the event log by Watchdog.alert(); this hook is the extra
    delivery channel.

    TODO(hermes-gateway): deliver `event` to the operator through a Hermes gateway message once a
    delivery endpoint exists. Deliberately a no-op: no endpoint is invented here.
    """
    return None


# --------------------------------------------------------------------------------------------
# IO (everything that touches the host or the network; tests replace this class)
# --------------------------------------------------------------------------------------------
class RealIO:
    def run(self, argv: list, timeout: float = 60.0) -> tuple:
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "", f"timeout after {timeout:.0f}s"
        except OSError as exc:
            return 127, "", str(exc)
        return p.returncode, p.stdout or "", p.stderr or ""

    def http(self, method: str, url: str, headers: Optional[dict] = None, body: Optional[str] = None,
             timeout: float = 10.0) -> tuple:
        """(status, text). status 0 = no HTTP answer at all (connection error / timeout)."""
        req = urllib.request.Request(url, data=None if body is None else body.encode(),
                                     headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            try:
                text = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                text = ""
            return exc.code, text
        except Exception as exc:  # noqa: BLE001 - URLError, timeout, reset...
            return 0, str(exc)

    def meminfo_available(self) -> Optional[int]:
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError):
            pass
        return None

    def disk_pct(self, path: str = "/") -> Optional[float]:
        """Used percentage the way `df` reports it (used / (used + avail))."""
        try:
            st = os.statvfs(path)
        except OSError:
            return None
        used = st.f_blocks - st.f_bfree
        denom = used + st.f_bavail
        return 100.0 * used / denom if denom else None

    def now(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


# --------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------
class ForbiddenDockerAction(RuntimeError):
    """The watchdog was asked for a docker call outside its allow-list."""


@dataclass
class DockerResult:
    ok: bool
    planned: bool = False
    skipped: str = ""
    rc: int = 0
    out: str = ""
    err: str = ""


@dataclass
class Info:
    status: str                     # running / exited / created / paused / restarting / dead
    started_at: Optional[float]
    uptime: Optional[float]
    exit_code: Optional[int] = None


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def parse_docker_time(s: str) -> Optional[float]:
    s = (s or "").strip()
    if not s or s.startswith("0001-"):
        return None
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)?$", s)
    if not m:
        return None
    base, frac, tz = m.groups()
    tz = "+00:00" if tz in (None, "Z") else tz
    try:
        return datetime.fromisoformat(f"{base}.{(frac or '0')[:6].ljust(6, '0')}{tz}").timestamp()
    except ValueError:
        return None


_SIZE = {"b": 1, "kb": 1000, "kib": 1024, "mb": 1000 ** 2, "mib": 1024 ** 2,
         "gb": 1000 ** 3, "gib": 1024 ** 3, "tb": 1000 ** 4, "tib": 1024 ** 4}


def parse_size(text: str) -> Optional[int]:
    m = re.match(r"\s*([\d.]+)\s*([a-zA-Z]+)", text or "")
    if not m or m.group(2).lower() not in _SIZE:
        return None
    return int(float(m.group(1)) * _SIZE[m.group(2).lower()])


def gib(n: Optional[int]) -> str:
    return "?" if n is None else f"{n / GiB:.1f} GiB"


def load_channels(path: Path, default_probe: str = "lofigirl") -> tuple:
    """(probe_login, {worker_id: login | None}) from channels.yaml. PyYAML when installed, else a
    small parser for the flat `channels:` mapping this file uses."""
    text = Path(path).read_text()
    try:
        import yaml  # type: ignore
        doc = yaml.safe_load(text) or {}
        raw = doc.get("channels") or {}
        probe = doc.get("probe") or default_probe
    except ImportError:
        raw, probe, in_channels = {}, default_probe, False
        for line in text.splitlines():
            line = line.split("#", 1)[0].rstrip()
            if not line.strip():
                continue
            if not line.startswith((" ", "\t")):
                key, _, val = line.partition(":")
                in_channels = key.strip() == "channels"
                if key.strip() == "probe" and val.strip():
                    probe = val.strip().strip("'\"")
                continue
            if in_channels and ":" in line:
                key, _, val = line.strip().partition(":")
                raw[key.strip()] = val.strip().strip("'\"")
    out = {}
    for key, val in raw.items():
        val = None if val is None else str(val).strip()
        out[str(key)] = None if val in (None, "", "null", "~", "None") else val
    return probe, out


# --------------------------------------------------------------------------------------------
# the watchdog
# --------------------------------------------------------------------------------------------
class Watchdog:
    def __init__(self, cfg: Config, io=None, dry_run: bool = False,
                 alert_sink: Callable[[dict], None] = default_alert_sink,
                 out: Callable[[str], None] = print):
        self.cfg, self.io, self.dry_run = cfg, io or RealIO(), dry_run
        self.alert_sink, self.out = alert_sink, out
        self.state_path = Path(cfg.state_dir) / "state.json"
        self.events_path = Path(cfg.state_dir) / "events.jsonl"
        self.pause_path = Path(cfg.state_dir) / "PAUSE"
        self.secrets: list = []
        self.planned: list = []
        self.summary: dict = {}
        self.info: dict = {}            # container -> Info (this pass)
        self.ffmpeg: dict = {}          # worker_id -> {"alive": bool, ...} (this pass)
        self.touched: set = set()
        self._did_action = False
        self.now = self.io.now()
        self.state = self._load_state()

    # ---- state / events ------------------------------------------------------------------
    def _load_state(self) -> dict:
        try:
            state = json.loads(self.state_path.read_text())
            if isinstance(state, dict):
                return state
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            if not self.dry_run:
                try:
                    self.state_path.rename(self.state_path.with_suffix(".corrupt"))
                except OSError:
                    pass
            self.out(f"warn: state file unreadable ({exc}); starting fresh")
        return {"version": 1, "passes": 0, "actions": [], "alerts": {}, "containers": {},
                "vllm": {}, "table": {}}

    def save_state(self) -> None:
        if self.dry_run:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1, sort_keys=True))
        os.replace(tmp, self.state_path)

    def _redact(self, text: str) -> str:
        for secret in self.secrets:
            if secret:
                text = text.replace(secret, "***")
        return text

    def log(self, level: str, kind: str, msg: str, **fields) -> dict:
        event = {"ts": iso(self.now), "level": level, "kind": kind, "msg": msg, **fields}
        line = self._redact(json.dumps(event, default=str))
        self.out(("[dry-run] " if self.dry_run else "") +
                 self._redact(f"{event['ts']} {level.upper():5} {kind}: {msg}"))
        if not self.dry_run:
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if self.events_path.stat().st_size > self.cfg.log_max_bytes:
                    os.replace(self.events_path, self.events_path.with_suffix(".jsonl.1"))
            except OSError:
                pass
            with open(self.events_path, "a") as fh:
                fh.write(line + "\n")
        return event

    def alert(self, key: str, msg: str, **fields) -> None:
        """Alert = an `alert` event in the log + the pluggable sink. A condition that persists is
        re-sent at most every alert_repeat_s; when it stops being asserted it is logged recovered."""
        self.touched.add(key)
        rec = self.state.setdefault("alerts", {}).get(key)
        if rec and self.now - rec["last_sent"] < self.cfg.alert_repeat_s:
            rec["count"] += 1
            return
        count = (rec or {}).get("count", 0) + 1
        self.state["alerts"][key] = {"first": (rec or {}).get("first", self.now),
                                     "last_sent": self.now, "count": count}
        event = self.log("alert", key, msg, **fields)
        try:
            self.alert_sink(event)
        except Exception as exc:  # noqa: BLE001 - a broken sink must not stop the pass
            self.log("warn", "alert_sink_error", str(exc))

    def _recover_stale_alerts(self) -> None:
        for key in list(self.state.get("alerts", {})):
            if key not in self.touched:
                self.log("info", "recovered", f"{key} is no longer asserted")
                del self.state["alerts"][key]

    def cstate(self, container: str) -> dict:
        return self.state.setdefault("containers", {}).setdefault(container, {})

    # ---- the one docker function ----------------------------------------------------------
    def _recent_actions(self, container: Optional[str], reason: Optional[str], window: float) -> list:
        return [a for a in self.state.get("actions", [])
                if self.now - a["ts"] < window
                and (container is None or a["container"] == container)
                and (reason is None or a["reason"] == reason)]

    def docker(self, args: list, *, reason: str = "", cap: Optional[tuple] = None,
               backoff_base_s: float = 0.0, backoff_max_s: float = 0.0,
               timeout: float = 60.0) -> DockerResult:
        """The only place the watchdog runs docker. Read-only verbs (exec/inspect/stats) pass
        through; start/restart are checked against the managed list, the per-(container, reason)
        cap, the optional exponential backoff and the global rolling-hour limit. Anything else
        raises ForbiddenDockerAction."""
        if not args:
            raise ForbiddenDockerAction("empty docker call")
        verb = args[0]
        if any(bad in a for a in args for bad in FORBIDDEN_SUBSTRINGS):
            raise ForbiddenDockerAction(f"refusing docker {' '.join(args)}: hermes3 is off limits")
        if verb in READ_VERBS:
            if verb == "exec" and args[1] not in MANAGED_CONTAINERS:
                raise ForbiddenDockerAction(f"exec into unmanaged container {args[1]!r}")
            rc, out, err = self.io.run(["docker", *args], timeout)
            return DockerResult(rc == 0, rc=rc, out=out, err=err)
        if verb not in MUTATING_VERBS:
            raise ForbiddenDockerAction(f"docker {verb} is not allowed")
        if len(args) != 2:
            raise ForbiddenDockerAction(f"docker {verb} takes exactly one container: {args}")
        container = args[1]
        if container not in MANAGED_CONTAINERS:
            raise ForbiddenDockerAction(f"{container!r} is not a managed container")
        if container == VLLM_CONTAINER and verb != "start":
            raise ForbiddenDockerAction("vLLM may only be started, never restarted")
        if cap is None or not reason:
            raise ForbiddenDockerAction("mutating docker calls need a reason and a cap")

        skip = self._action_blocked(verb, container, reason, cap, backoff_base_s, backoff_max_s)
        if skip:
            self.log("info", "action_skipped", f"docker {verb} {container} [{reason}]: {skip}")
            return DockerResult(False, skipped=skip)
        cmd = f"docker {verb} {container}"
        if self.dry_run:
            self.planned.append(f"{cmd}  [{reason}]")
            self.out(f"[dry-run] PLAN {cmd}  [{reason}]")
            return DockerResult(True, planned=True)
        if self._did_action and self.cfg.action_gap_s:
            self.io.sleep(self.cfg.action_gap_s)
        self._did_action = True
        self.state.setdefault("actions", []).append(
            {"ts": self.now, "verb": verb, "container": container, "reason": reason})
        rc, out, err = self.io.run(["docker", *args], timeout)
        level = "warn" if rc == 0 else "error"
        self.log(level, "action", f"{cmd} [{reason}] rc={rc}", container=container, reason=reason,
                 verb=verb, rc=rc, stderr=err.strip()[:300])
        return DockerResult(rc == 0, rc=rc, out=out, err=err)

    def _action_blocked(self, verb, container, reason, cap, base, maxgap) -> str:
        glob = self._recent_actions(None, None, HOUR)
        if len(glob) >= self.cfg.global_docker_per_hour:
            self.alert("watchdog:global_rate_limit",
                       f"{len(glob)} docker actions in the last hour: global limit "
                       f"{self.cfg.global_docker_per_hour} reached, remediation paused")
            return "global rate limit"
        max_n, window = cap
        recent = self._recent_actions(container, reason, window)
        if len(recent) >= max_n:
            return f"cap {max_n} per {window / 60:.0f} min reached"
        if base and recent:
            n = len(self._recent_actions(container, reason, HOUR))
            gap = min(maxgap or base, base * 2 ** max(n - 1, 0))
            wait = recent[-1]["ts"] + gap - self.now
            if wait > 0:
                return f"backoff, next try in {wait:.0f}s"
        return ""

    # ---- container info -------------------------------------------------------------------
    def refresh_info(self, names) -> None:
        names = list(names)
        fmt = "{{.Name}}|{{.State.Status}}|{{.State.StartedAt}}|{{.State.ExitCode}}"
        res = self.docker(["inspect", "--format", fmt, *names])
        for line in res.out.splitlines():
            parts = line.strip().split("|")
            if len(parts) != 4:
                continue
            name = parts[0].lstrip("/")
            started = parse_docker_time(parts[2])
            running = parts[1] == "running"
            try:
                code = int(parts[3])
            except ValueError:
                code = None
            self.info[name] = Info(parts[1], started,
                                   (self.now - started) if (running and started) else None, code)

    def booting(self, container: str) -> bool:
        i = self.info.get(container)
        return bool(i and i.status == "running" and i.uptime is not None and i.uptime < self.cfg.grace_s)

    # ---- 1. Ollama + memory ---------------------------------------------------------------
    def check_ollama(self) -> dict:
        status, text = self.io.http("GET", self.cfg.ollama_url + "/api/ps")
        if status != 200:
            return {"status": "unreachable"}     # Ollama off is the desired state
        try:
            models = (json.loads(text).get("models")) or []
        except ValueError:
            return {"status": "bad-response"}
        loaded = [m.get("name") or m.get("model") for m in models if m.get("name") or m.get("model")]
        for name in loaded:
            self.alert("ollama:loaded", f"OLLAMA HAS {name} LOADED next to vLLM (host OOM risk): "
                                        "unloading (keep_alive 0)", model=name)
            body = json.dumps({"model": name, "keep_alive": 0})
            if self.dry_run:
                self.planned.append(f"POST {self.cfg.ollama_url}/api/generate {body}")
                self.out(f"[dry-run] PLAN unload ollama model {name}")
                continue
            st, _ = self.io.http("POST", self.cfg.ollama_url + "/api/generate",
                                 {"Content-Type": "application/json"}, body)
            self.log("warn" if st == 200 else "error", "ollama_unload",
                     f"unload {name}: HTTP {st}", model=name, http=st)
        return {"status": "loaded" if loaded else "empty", "loaded": loaded}

    def worker_memory(self) -> dict:
        """container -> bytes (docker stats, workers only)."""
        res = self.docker(["stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}"])
        out: dict = {}
        for line in res.out.splitlines():
            name, _, usage = line.partition("\t")
            if name in WORKER_BY_CONTAINER:
                size = parse_size(usage.split("/")[0])
                if size is not None:
                    out[name] = size
        return out

    def check_memory(self) -> dict:
        avail = self.io.meminfo_available()
        if avail is None:
            return {"status": "unknown"}
        res: dict = {"available": gib(avail)}
        if avail < self.cfg.mem_alert_bytes:
            self.alert("memory:low", f"MemAvailable {gib(avail)} < {gib(self.cfg.mem_alert_bytes)}",
                       available_bytes=avail)
            res["status"] = "low"
        else:
            res["status"] = "ok"
        if avail < self.cfg.mem_restart_bytes:
            usage = self.worker_memory()
            if not usage:
                self.alert("memory:critical", f"MemAvailable {gib(avail)} < "
                           f"{gib(self.cfg.mem_restart_bytes)} and no worker RSS readable")
            else:
                victim = max(usage, key=lambda c: usage[c])
                self.alert("memory:critical", f"MemAvailable {gib(avail)} < "
                           f"{gib(self.cfg.mem_restart_bytes)}: restarting largest worker "
                           f"{victim} ({gib(usage[victim])})", available_bytes=avail)
                r = self.docker(["restart", victim], reason="memory", cap=self.cfg.memory_cap)
                res["restarted"] = victim if r.ok else None
            res["status"] = "critical"
        return res

    # ---- 2. vLLM --------------------------------------------------------------------------
    def vllm_probe(self) -> tuple:
        """(up, detail). Key is read from the container at runtime and never logged."""
        info = self.info.get(VLLM_CONTAINER)
        key = None
        if info and info.status == "running":
            r = self.docker(["exec", VLLM_CONTAINER, "printenv", "VLLM_API_KEY"])
            if r.ok and r.out.strip():
                key = r.out.strip()
                self.secrets.append(key)
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        status, text = self.io.http("GET", self.cfg.vllm_url + "/v1/models", headers, timeout=10)
        if status != 0 and not (info and info.status == "running"):
            # vllm-agents is not running yet something answers on its port (the 70b generation
            # model, 2026-10-21): that is NOT "the table model is up", and it must not be displaced.
            return False, {"http": status, "occupied": True}
        if status == 200:
            try:
                ids = [m.get("id") for m in json.loads(text).get("data", [])]
            except ValueError:
                ids = []
            return True, {"models": ids}
        if status in (401, 403) and not key:
            return True, {"note": "server answers, key unreadable"}
        return False, {"http": status}

    def check_vllm(self) -> dict:
        self.refresh_info([VLLM_CONTAINER])
        v = self.state.setdefault("vllm", {})
        up, detail = self.vllm_probe()
        info = self.info.get(VLLM_CONTAINER)
        res: dict = {"up": up, "container": info.status if info else "missing", **detail}
        if up:
            v["down_passes"] = 0
            if detail.get("models") is not None and VLLM_MODEL not in detail["models"]:
                self.alert("vllm:model_missing", f"vLLM is up but does not serve {VLLM_MODEL!r}: "
                                                 f"{detail['models']}")
            return res
        v["down_passes"] = v.get("down_passes", 0) + 1
        res["down_passes"] = v["down_passes"]
        if info is None:
            self.alert("vllm:container_missing", f"container {VLLM_CONTAINER} does not exist")
            return res
        if v["down_passes"] < self.cfg.vllm_down_passes:
            self.log("warn", "vllm_down", f"vLLM not answering ({v['down_passes']}/"
                                          f"{self.cfg.vllm_down_passes} passes)")
            return res
        if detail.get("occupied"):
            self.alert("vllm:port_occupied", f"{VLLM_CONTAINER} is {info.status} but something else answers on "
                                             f"{self.cfg.vllm_url} (HTTP {detail['http']}): NOT starting it "
                                             "(one 70b-class model at a time; host OOM history)")
            return res
        if info.status == "running":
            # still loading, or hung: `docker start` is a no-op here. Alert only after a long time.
            if v["down_passes"] >= self.cfg.vllm_hung_alert_passes:
                self.alert("vllm:hung", f"vLLM container is running but /v1/models failed for "
                                        f"{v['down_passes']} passes ({detail}); NOT restarting it")
            return res
        self.alert("vllm:down", f"vLLM down {v['down_passes']} passes (container {info.status}): "
                                f"docker start {VLLM_CONTAINER}")
        r = self.docker(["start", VLLM_CONTAINER], reason="vllm-down", cap=self.cfg.vllm_start_cap,
                        backoff_base_s=120, backoff_max_s=1800)
        if r.planned:
            res["start"] = "planned"
        elif r.ok:
            v["last_start"] = self.now
            res["start"] = self._wait_for_vllm()
        else:
            res["start"] = r.skipped or "failed"
            if not r.skipped:
                self.alert("vllm:start_failed", f"docker start {VLLM_CONTAINER} failed: {r.err.strip()[:200]}")
        return res

    def _wait_for_vllm(self) -> str:
        self.refresh_info([VLLM_CONTAINER])          # it is running now: the key can be read again
        waited = 0.0
        while waited < self.cfg.vllm_wait_s:
            self.io.sleep(self.cfg.vllm_poll_s)
            waited += self.cfg.vllm_poll_s
            self.now = self.io.now()
            up, _ = self.vllm_probe()
            if up:
                self.state["vllm"].update(down_passes=0, last_recovery_wait_s=waited)
                self.log("warn", "vllm_recovered", f"vLLM answering again {waited:.0f}s after docker start",
                         waited_s=waited)
                return f"up after {waited:.0f}s"
        self.alert("vllm:slow_start", f"vLLM still not answering {self.cfg.vllm_wait_s:.0f}s after docker start")
        return "started, not answering yet"

    # ---- 3. containers --------------------------------------------------------------------
    def check_containers(self) -> dict:
        self.refresh_info([w.container for w in WORKERS] + list(SUPPORT_CONTAINERS))
        res: dict = {}
        for w in WORKERS:
            info = self.info.get(w.container)
            cs = self.cstate(w.container)
            if info is None:
                res[w.worker_id] = "missing"
                self.alert(f"container:{w.container}:missing",
                           f"{w.container} does not exist (needs `docker compose up -d --no-deps`)")
            elif info.status == "running":
                res[w.worker_id] = "running" if not self.booting(w.container) else "booting"
            elif info.status in ("exited", "created"):
                res[w.worker_id] = info.status
                self.log("warn", "container_down", f"{w.container} is {info.status} "
                                                   f"(exit {info.exit_code})")
                r = self.docker(["start", w.container], reason="start-exited", cap=self.cfg.start_cap,
                                backoff_base_s=self.cfg.start_backoff_base_s,
                                backoff_max_s=self.cfg.start_backoff_max_s)
                if r.skipped and r.skipped.startswith("cap"):
                    self.alert(f"container:{w.container}:budget",
                               f"{w.container} keeps dying: restart budget "
                               f"{self.cfg.start_cap[0]}/hour exhausted, alert only (exit {info.exit_code})")
                elif not r.ok and not r.skipped:
                    self.alert(f"container:{w.container}:start_failed",
                               f"docker start {w.container} failed: {r.err.strip()[:200]}")
                elif r.ok:
                    cs["ffmpeg_prev_min"] = None
            else:
                res[w.worker_id] = info.status
                self.alert(f"container:{w.container}:state",
                           f"{w.container} is {info.status}: not auto-remediated")
        for name in SUPPORT_CONTAINERS:
            info = self.info.get(name)
            if info is None or info.status != "running":
                self.alert(f"support:{name}", f"support container {name} is "
                                              f"{info.status if info else 'missing'} (alert only)")
        return res

    # ---- 4. ffmpeg ------------------------------------------------------------------------
    def ffmpeg_procs(self, container: str) -> Optional[list]:
        r = self.docker(["exec", container, "sh", "-c", "ps -eo etimes,args | grep \"[f]fmpeg\""])
        if r.rc not in (0, 1):
            return None                          # exec itself failed: unknown, not "missing"
        procs = []
        for line in r.out.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and parts[0].isdigit() and os.path.basename(parts[1].split()[0]) == "ffmpeg":
                procs.append(int(parts[0]))
        return procs

    def check_ffmpeg(self) -> dict:
        res: dict = {}
        for w in WORKERS:
            info = self.info.get(w.container)
            if not info or info.status != "running":
                continue
            cs = self.cstate(w.container)
            if self.booting(w.container):
                res[w.worker_id] = "booting"
                cs.update(ffmpeg_bad=0, ffmpeg_prev_min=None)
                continue
            procs = self.ffmpeg_procs(w.container)
            if procs is None:
                res[w.worker_id] = "unknown"
                continue
            bad, why = False, ""
            if not procs:
                bad, why = True, "no ffmpeg process"
            else:
                cur, prev = min(procs), cs.get("ffmpeg_prev_min")
                elapsed = self.now - cs.get("ffmpeg_prev_ts", self.now)
                if prev is not None and cur < prev + elapsed - self.cfg.ffmpeg_reset_tolerance_s:
                    bad, why = True, f"ffmpeg etimes reset ({prev}s -> {cur}s over {elapsed:.0f}s): crash loop"
                cs["ffmpeg_prev_min"], cs["ffmpeg_prev_ts"] = cur, self.now
            if procs:
                self.ffmpeg[w.worker_id] = {"alive": True, "etimes": min(procs)}
            else:
                self.ffmpeg[w.worker_id] = {"alive": False}
                cs["ffmpeg_prev_min"] = None
            if not bad:
                cs["ffmpeg_bad"] = 0
                res[w.worker_id] = f"ok etimes={min(procs)}"
                continue
            cs["ffmpeg_bad"] = cs.get("ffmpeg_bad", 0) + 1
            res[w.worker_id] = f"BAD x{cs['ffmpeg_bad']}: {why}"
            self.alert(f"ffmpeg:{w.worker_id}", f"{w.worker_id} ({w.container}): {why} "
                                                f"[{cs['ffmpeg_bad']} bad pass(es)]")
            if cs["ffmpeg_bad"] >= self.cfg.ffmpeg_restart_passes:
                r = self.docker(["restart", w.container], reason="ffmpeg", cap=self.cfg.ffmpeg_cap)
                if r.ok and not r.planned:
                    cs.update(ffmpeg_bad=0, ffmpeg_prev_min=None, twitch_offline=0)
        return res

    # ---- 5. Twitch ------------------------------------------------------------------------
    def twitch_is_live(self, login: str) -> Optional[bool]:
        """True/False from raw GQL; None when the answer can't be trusted (error, unknown login)."""
        query = 'query{user(login:"%s"){stream{createdAt}}}' % login
        status, text = self.io.http("POST", TWITCH_GQL_URL,
                                    {"Client-ID": TWITCH_CLIENT_ID, "Content-Type": "application/json"},
                                    json.dumps({"query": query}), timeout=15)
        if status != 200:
            return None
        try:
            user = (json.loads(text).get("data") or {}).get("user")
        except (ValueError, AttributeError):
            return None
        if user is None:
            return None
        return bool(user.get("stream"))

    def check_twitch(self) -> dict:
        try:
            probe, channels = load_channels(self.cfg.channels_path, self.cfg.probe_login)
        except (OSError, ValueError) as exc:
            self.alert("twitch:config", f"cannot read {self.cfg.channels_path}: {exc}")
            return {"status": "no-config"}
        live = self.twitch_is_live(probe)
        if not live:
            self.log("warn", "twitch_probe_distrusted",
                     f"probe channel {probe!r} reads {'offline' if live is False else 'unavailable'}: "
                     "the Twitch check is not trustworthy this pass, doing nothing")
            return {"status": "probe-distrusted", "probe": probe}
        res: dict = {"probe": "live"}
        for w in WORKERS:
            login = channels.get(w.worker_id)
            cs = self.cstate(w.container)
            if not login:
                res[w.worker_id] = "skipped (no login)"
                continue
            ff = self.ffmpeg.get(w.worker_id)
            if ff is None:
                res[w.worker_id] = "skipped (container not checked)"
                continue
            is_live = self.twitch_is_live(login)
            if is_live is None:
                res[w.worker_id] = f"unknown ({login})"
                continue
            if is_live:
                cs["twitch_offline"] = 0
                res[w.worker_id] = f"live ({login})"
                continue
            if not ff["alive"]:
                cs["twitch_offline"] = 0     # no ffmpeg: the ffmpeg check owns this
                res[w.worker_id] = f"offline ({login}), ffmpeg dead"
                continue
            cs["twitch_offline"] = cs.get("twitch_offline", 0) + 1
            res[w.worker_id] = f"OFFLINE x{cs['twitch_offline']} ({login}) with ffmpeg alive"
            if cs["twitch_offline"] >= self.cfg.twitch_offline_passes:
                self.alert(f"twitch:{w.worker_id}", f"{login} offline on Twitch for {cs['twitch_offline']} passes "
                                                    f"while ffmpeg in {w.container} is alive")
                r = self.docker(["restart", w.container], reason="twitch-offline", cap=self.cfg.twitch_cap)
                if r.ok and not r.planned:
                    cs.update(twitch_offline=0, ffmpeg_prev_min=None, ffmpeg_bad=0)
        return res

    # ---- 6. table -------------------------------------------------------------------------
    def _table_messages(self, worker: str, since_ts: float, limit: int) -> Optional[list]:
        q = urllib.parse.urlencode({"worker_id": worker, "since": iso(since_ts), "limit": limit})
        st, text = self.io.http("GET", f"{self.cfg.message_api_url}/logs/messages?{q}")
        if st != 200:
            return None
        try:
            return json.loads(text).get("messages", [])
        except ValueError:
            return None

    @staticmethod
    def _payload(raw) -> dict:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                return {}
        return raw if isinstance(raw, dict) else {}

    def table_request(self, type_: str, payload: dict, timeout_s: float, action: bool) -> Optional[dict]:
        """Same protocol as scripts/table_ctl.py: post one operator message to the GM, read the
        GM's table_status reply from message-api's message log. action=True ignores plain status
        replies so a poll by someone else is not mistaken for the answer."""
        since = self.io.now() - 1
        body = json.dumps({"to": self.cfg.gm_worker, "type": type_, "payload": payload})
        st, _ = self.io.http("POST", self.cfg.message_api_url + "/messages",
                             {"Content-Type": "application/json"}, body)
        if st not in (200, 201, 202):
            return None
        deadline = self.io.now() + timeout_s
        while True:
            rows = self._table_messages(self.cfg.gm_worker, since, 200) or []
            replies = [m for m in rows if m.get("type") == "table_status"
                       and m.get("from") == self.cfg.gm_worker and m.get("to") == "operator"
                       and not (action and self._payload(m.get("payload")).get("result") == "status")]
            if replies:
                return self._payload(replies[-1].get("payload"))
            if self.io.now() >= deadline:
                return None
            self.io.sleep(1.0)

    def check_table(self) -> dict:
        gm = WORKER_BY_ID["tuber_0"].container
        info = self.info.get(gm)
        t = self.state.setdefault("table", {})
        if not info or info.status != "running":
            return {"status": "skipped", "reason": "worker-gm not running"}
        if self.booting(gm):
            return {"status": "skipped", "reason": "worker-gm booting"}
        status = self.table_request("table_status_request", {}, self.cfg.table_status_timeout_s, False)
        res: dict = {"reply": status is not None}
        if status is None:
            t["no_reply"] = t.get("no_reply", 0) + 1
            self.log("warn", "table_no_reply", f"GM did not answer table_status_request ({t['no_reply']} in a row)")
            if t["no_reply"] >= 3:
                self.alert("table:no_reply", f"worker-gm is running but did not answer table_status_request "
                                             f"{t['no_reply']} passes in a row")
        else:
            t["no_reply"] = 0
        run = (status or {}).get("run") or {}
        block = (status or {}).get("block") or {}
        if status is not None:
            res.update(mode=status.get("mode"), state=status.get("state"), run_active=run.get("active"),
                       finished=run.get("finished"), halted=run.get("halted"),
                       block={k: block.get(k) for k in ("index", "of", "phase", "window", "spine_remaining",
                                                        "carry_over", "ambient_pool", "last_ambient_id")}
                       if block.get("enabled") else None)
            self._table_stuck_counters(block, t, res)
            sig = json.dumps([block.get("index"), block.get("spine_played"), block.get("ambient_played"),
                              block.get("last_ambient_id"), run.get("played"), status.get("scene_id")])
            if sig != t.get("sig"):
                t["sig"], t["sig_ts"] = sig, self.now
            self._table_run_start(status, run, t, res)
        t.setdefault("sig_ts", self.now)
        self._table_stall(info, t, res)
        return res

    def _table_stuck_counters(self, block: dict, t: dict, res: dict) -> None:
        stuck = block.get("stuck") or {}
        if not stuck:
            return
        total = stuck.get("total")
        res["stuck"] = stuck
        if isinstance(total, (int, float)):
            prev = t.get("stuck_total")
            if prev is not None and total > prev:
                self.log("warn", "table_stuck_total_grew", f"stuck.total {prev} -> {total} "
                         f"(consecutive {stuck.get('consecutive')}/{stuck.get('limit')})",
                         stuck=stuck)
            t["stuck_total"] = total
        if stuck.get("limit") and (stuck.get("consecutive") or 0) >= stuck["limit"]:
            self.alert("table:stuck_streak", f"{stuck.get('consecutive')} stuck scenes in a row "
                                             f"(cooldown {stuck.get('cooldown_s')}s): is the model OK?")

    def _table_run_start(self, status: dict, run: dict, t: dict, res: dict) -> None:
        rs = t.setdefault("run_start", {})
        if run.get("finished"):
            self.alert("table:finished", "table run reports finished: NOT restarting it automatically "
                                         "(that is the operator's call)")
            return
        if status.get("mode") == "auto" and run.get("active"):
            rs.clear()
            return
        res["needs_run_start"] = True
        n = rs.get("attempts", 0)
        if self.now < rs.get("next_ok", 0):
            res["run_start"] = f"backoff {rs['next_ok'] - self.now:.0f}s"
            return
        if n >= 3:
            self.alert("table:run_start", f"table_run_start sent {n} times but the run is still not "
                                          f"active (mode={status.get('mode')})")
        self.log("warn", "table_not_running", f"table mode={status.get('mode')} active={run.get('active')} "
                                              f"halted={run.get('halted')}: sending table_run_start")
        if self.dry_run:
            self.planned.append("POST message-api table_run_start {'from': 'next'} -> tuber_0")
            self.out("[dry-run] PLAN table_run_start from=next")
            res["run_start"] = "planned"
            return
        rs["attempts"] = n + 1
        rs["next_ok"] = self.now + min(self.cfg.table_start_backoff_max_s,
                                       self.cfg.table_start_backoff_base_s * 2 ** n)
        reply = self.table_request("table_run_start", {"from": "next"}, self.cfg.table_start_timeout_s, True)
        result = (reply or {}).get("result", "no reply")
        res["run_start"] = result
        self.log("warn" if result == "started" else "error", "table_run_start",
                 f"table_run_start -> {result} {((reply or {}).get('error') or '')}".strip(),
                 attempt=rs["attempts"])

    def _table_stall(self, info: Info, t: dict, res: dict) -> None:
        window = self.cfg.table_stuck_s
        stagnant = min(self.now - t["sig_ts"], info.uptime if info.uptime is not None else window)
        res["stagnant_s"] = round(stagnant)
        if stagnant < window:
            return
        rows = self._table_messages(self.cfg.roundtable_worker, self.now - window, 500)
        if rows is None:
            res["lines"] = "unknown"        # message log unreadable: don't restart on a blind guess
            return
        lines = sum(1 for m in rows if m.get("type") == "table_line")
        res["lines_30m"] = lines
        if lines:
            t["sig_ts"] = self.now - (window - 60)   # airing, just not advancing: look again soon
            return
        self.alert("table:stalled", f"table stalled {stagnant / 60:.0f} min: block/scene counters "
                                    "unchanged and no table_line on air")
        r = self.docker(["restart", WORKER_BY_ID["tuber_0"].container], reason="table-stalled",
                        cap=self.cfg.table_cap)
        if r.ok and not r.planned:
            t["sig_ts"] = self.now
            self.cstate(WORKER_BY_ID["tuber_0"].container).update(ffmpeg_prev_min=None, ffmpeg_bad=0)

    # ---- 7. tile RSS ----------------------------------------------------------------------
    def check_tiles(self) -> dict:
        rt = WORKER_BY_ID["roundtable"].container
        info = self.info.get(rt)
        if not info or info.status != "running":
            return {"status": "skipped", "reason": "roundtable not running"}
        r = self.docker(["exec", rt, "ps", "-eo", "rss,args"])
        if not r.ok:
            return {"status": "unknown"}
        tiles = []
        for line in r.out.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and parts[0].isdigit() and "tile_pane" in parts[1]:
                tiles.append((int(parts[0]) * 1024, parts[1][:90]))
        if not tiles:
            return {"status": "no-tiles", "tiles": 0}
        big = max(tiles)
        res: dict = {"tiles": len(tiles), "max": gib(big[0])}
        if big[0] > self.cfg.tile_alert_bytes:
            self.alert("tiles:rss", f"tile_pane at {gib(big[0])} (> {gib(self.cfg.tile_alert_bytes)}): {big[1]}")
            res["status"] = "alert"
        else:
            res["status"] = "ok"
        if big[0] > self.cfg.tile_restart_bytes:
            self.alert("tiles:leak", f"tile_pane at {gib(big[0])} > {gib(self.cfg.tile_restart_bytes)}: "
                                     "LEAK REGRESSION, restarting roundtable")
            self.docker(["restart", rt], reason="tile-leak", cap=self.cfg.tile_cap)
            res["status"] = "restart"
        return res

    # ---- 8. disk --------------------------------------------------------------------------
    def check_disk(self) -> dict:
        pct = self.io.disk_pct("/")
        if pct is None:
            return {"status": "unknown"}
        if pct > self.cfg.disk_alert_pct:
            self.alert("disk:full", f"root filesystem at {pct:.1f}% (> {self.cfg.disk_alert_pct:.0f}%)")
        return {"status": "alert" if pct > self.cfg.disk_alert_pct else "ok", "pct": round(pct, 1)}

    # ---- the pass -------------------------------------------------------------------------
    CHECKS = ("ollama", "memory", "vllm", "containers", "ffmpeg", "twitch", "table", "tiles", "disk")

    def run_pass(self) -> int:
        self.now = self.io.now()
        if self.pause_path.exists():
            self.log("warn", "paused", f"{self.pause_path} exists: no checks, no remediation")
            for key in ("ffmpeg_bad", "ffmpeg_prev_min", "twitch_offline"):
                for cs in self.state.get("containers", {}).values():
                    cs.pop(key, None)
            self.state.setdefault("vllm", {})["down_passes"] = 0
            self.touched.update(self.state.get("alerts", {}))   # keep alerts as they are
            self.save_state()
            return 0
        self.state["passes"] = self.state.get("passes", 0) + 1
        for name in self.CHECKS:
            try:
                self.summary[name] = getattr(self, f"check_{name}")()
            except ForbiddenDockerAction:
                raise
            except Exception as exc:  # noqa: BLE001 - never let one check stop the rest
                self.summary[name] = {"error": f"{type(exc).__name__}: {exc}"}
                self.alert(f"check_error:{name}", f"check {name} crashed: {type(exc).__name__}: {exc}")
            self.now = self.io.now()
        self._recover_stale_alerts()
        self.state["last_pass"] = self.now
        self.log("info", "pass", f"pass #{self.state['passes']} done", summary=self.summary,
                 open_alerts=sorted(self.state.get("alerts", {})))
        self.state["actions"] = [a for a in self.state.get("actions", []) if self.now - a["ts"] < 6 * HOUR]
        self.save_state()
        if self.dry_run:
            self.out("\n== summary ==")
            for name, val in self.summary.items():
                self.out(f"{name}: {json.dumps(val, default=str)}")
            self.out(f"\n== planned actions ({len(self.planned)}) ==")
            for p in self.planned:
                self.out(f"  {p}")
        return 0


def build_config(args) -> Config:
    cfg = Config()
    if args.state_dir:
        cfg.state_dir = Path(args.state_dir).expanduser()
    if args.channels:
        cfg.channels_path = Path(args.channels)
    if args.vllm_wait is not None:
        cfg.vllm_wait_s = args.vllm_wait
    return cfg


def main(argv=None, io=None) -> int:
    p = argparse.ArgumentParser(description="Unattended office-broadcast watchdog (one pass).")
    p.add_argument("--dry-run", action="store_true",
                   help="read-only: print planned actions, write no state/log and run no mutating call")
    p.add_argument("--state-dir", help=f"default {DEFAULT_STATE_DIR}")
    p.add_argument("--channels", help=f"default {DEFAULT_CHANNELS}")
    p.add_argument("--vllm-wait", type=float, help="seconds to wait for vLLM after docker start (default 300)")
    args = p.parse_args(argv)
    cfg = build_config(args)
    lock = None
    if not args.dry_run:
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        lock = open(cfg.state_dir / "lock", "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("previous watchdog pass is still running; skipping this tick")
            return 0
    return Watchdog(cfg, io=io, dry_run=args.dry_run).run_pass()


if __name__ == "__main__":
    sys.exit(main())
