"""scripts/golive_watchdog.py: every check and remediation path against a fake host.

Nothing here touches docker, the network or the real state dir: `FakeHost` stands in for RealIO
(docker CLI, HTTP, /proc/meminfo, statvfs, the clock) and a tmp_path holds the state/event files.
"""
import importlib.util
import json
import pathlib
import subprocess
import sys
import urllib.error

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "golive_watchdog.py"
spec = importlib.util.spec_from_file_location("golive_watchdog", SCRIPT)
wd = importlib.util.module_from_spec(spec)
sys.modules["golive_watchdog"] = wd         # dataclasses resolves string annotations via sys.modules
spec.loader.exec_module(wd)

GiB = wd.GiB
T0 = 1_800_000_000.0
KEY = "sk-super-secret-vllm-key"
GM = wd.WORKER_BY_ID["tuber_0"].container
RT = wd.WORKER_BY_ID["roundtable"].container
T1 = wd.WORKER_BY_ID["tuber_1"].container

CHANNELS = """probe: lofigirl
channels:
  roundtable: null
  tuber_0: null
  tuber_1: mastermanagerchief
  tuber_2: ten_g_hands
  tuber_3: null
  tuber_4: null
  tuber_5: null
  tuber_6: null
  tuber_7: null
"""


def good_status(**over):
    st = {"mode": "auto", "state": "running", "scene_id": "s1",
          "run": {"active": True, "finished": False, "halted": False, "played": 3},
          "block": {"enabled": True, "index": 2, "of": 28, "phase": "workday", "window": "09-17",
                    "spine_remaining": 1, "carry_over": 0, "ambient_pool": 40, "last_ambient_id": "a1",
                    "spine_played": 2, "ambient_played": 5,
                    "stuck": {"consecutive": 0, "total": 0, "limit": 3, "cooldown_s": 0.0}},
          "result": "status"}
    st.update(over)
    return st


class FakeHost:
    """A healthy stack by default; tests break pieces of it."""

    def __init__(self):
        self.t = T0
        self.containers = {}
        for w in wd.WORKERS:
            self.containers[w.container] = {"status": "running", "started": T0 - 86400, "exit": 0,
                                            "etimes": None, "rss": 1 * GiB, "ps_tiles": []}
        for name in wd.SUPPORT_CONTAINERS:
            self.containers[name] = {"status": "running", "started": T0 - 86400, "exit": 0}
        self.containers[wd.VLLM_CONTAINER] = {"status": "running", "started": T0 - 86400, "exit": 0}
        self.containers["vllm-hermes3-70b"] = {"status": "running", "started": T0 - 5, "exit": 0}
        self.vllm_up = True
        self.vllm_models = [wd.VLLM_MODEL]
        self.vllm_starts_up = True        # does `docker start vllm-agents` bring it up?
        self.ollama = {"models": []}
        self.ollama_up = True
        self.avail = 40 * GiB
        self.disk = 80.0
        self.live = {"lofigirl": True, "mastermanagerchief": True, "ten_g_hands": True}
        self.twitch_down = False
        self.status = good_status()        # what the GM answers; None = silence
        self.run_start_reply = {"result": "started", "mode": "auto", "run": {"active": True}}
        self.lines = [{"type": "table_line", "from": "tuber_0", "to": "roundtable"}]
        self.logs_up = True
        self.calls = []                    # docker argv (without "docker")
        self.http_calls = []               # (method, url, body)
        self.messages = []                 # replies visible in /logs/messages

    # --- clock
    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s

    # --- host
    def meminfo_available(self):
        return self.avail

    def disk_pct(self, path="/"):
        return self.disk

    # --- docker
    def run(self, argv, timeout=60.0):
        assert argv[0] == "docker"
        args = argv[1:]
        self.calls.append(args)
        verb = args[0]
        if verb == "inspect":
            out = []
            for name in args[3:]:
                c = self.containers.get(name)
                if c:
                    started = wd.iso(c["started"]).replace("+00:00", "Z")
                    out.append(f"/{name}|{c['status']}|{started}|{c['exit']}")
            return (0 if len(out) == len(args[3:]) else 1), "\n".join(out) + "\n", ""
        if verb == "stats":
            rows = [f"{n}\t{c['rss'] / GiB:.2f}GiB / 119GiB" for n, c in self.containers.items()
                    if n in wd.WORKER_BY_CONTAINER and c["status"] == "running"]
            rows.append(f"{wd.VLLM_CONTAINER}\t90GiB / 119GiB")
            return 0, "\n".join(rows), ""
        if verb == "exec":
            name, rest = args[1], args[2:]
            c = self.containers[name]
            if c["status"] != "running":
                return 1, "", f"container {name} is not running"
            if rest[0] == "printenv":
                return 0, KEY + "\n", ""
            if rest[0] == "sh":
                ages = c["etimes"] if c["etimes"] is not None else [86000 + int(self.t - T0)]   # None: healthy, ages
                lines = [f"{e} ffmpeg -f x11grab -i :99 out" for e in ages]
                return (0 if lines else 1), "\n".join(lines), ""
            if rest[0] == "ps":
                lines = ["  RSS COMMAND", "12 sh"] + [f"{kb} python3 /app/tile_pane.py --slot {i}"
                                                       for i, kb in enumerate(c["ps_tiles"])]
                return 0, "\n".join(lines), ""
        if verb in ("start", "restart"):
            name = args[1]
            c = self.containers[name]
            c["status"], c["started"], c["exit"] = "running", self.t, 0
            if name == wd.VLLM_CONTAINER:
                self.vllm_up = self.vllm_starts_up
            return 0, name + "\n", ""
        raise AssertionError(f"unexpected docker call {args}")

    def mutating(self):
        return [a for a in self.calls if a[0] in ("start", "restart")]

    # --- http
    def http(self, method, url, headers=None, body=None, timeout=10.0):
        self.http_calls.append((method, url, body))
        if url.startswith("http://127.0.0.1:11434"):
            if not self.ollama_up:
                return 0, "refused"
            return 200, json.dumps(self.ollama) if method == "GET" else "{}"
        if url.endswith("/v1/models"):
            if not self.vllm_up:
                return 0, "connection refused"
            assert headers.get("Authorization") == f"Bearer {KEY}"
            return 200, json.dumps({"data": [{"id": m} for m in self.vllm_models]})
        if url == wd.TWITCH_GQL_URL:
            assert headers["Client-ID"] == wd.TWITCH_CLIENT_ID
            if self.twitch_down:
                return 503, ""
            login = json.loads(body)["query"].split('login:"')[1].split('"')[0]
            if login not in self.live:
                return 200, json.dumps({"data": {"user": None}})
            stream = {"createdAt": "2026-10-24T00:00:00Z"} if self.live[login] else None
            return 200, json.dumps({"data": {"user": {"stream": stream}}})
        if url.endswith("/messages") and method == "POST":
            msg = json.loads(body)
            assert msg["to"] == "tuber_0"
            if msg["type"] == "table_status_request" and self.status is not None:
                self.messages.append(self._reply(self.status))
            elif msg["type"] == "table_run_start" and self.run_start_reply is not None:
                self.messages.append(self._reply(self.run_start_reply))
            return 200, "{}"
        if "/logs/messages" in url:
            if not self.logs_up:
                return 503, ""
            if "worker_id=roundtable" in url:
                return 200, json.dumps({"messages": self.lines})
            return 200, json.dumps({"messages": self.messages})
        raise AssertionError(f"unexpected http {method} {url}")

    @staticmethod
    def _reply(payload):
        return {"from": "tuber_0", "to": "operator", "type": "table_status", "payload": payload}

    def posted(self, type_):
        return [json.loads(b) for m, u, b in self.http_calls
                if m == "POST" and u.endswith("/messages") and json.loads(b)["type"] == type_]


@pytest.fixture
def host():
    return FakeHost()


@pytest.fixture
def cfg(tmp_path):
    ch = tmp_path / "channels.yaml"
    ch.write_text(CHANNELS)
    return wd.Config(state_dir=tmp_path / "state", channels_path=ch, action_gap_s=0.0,
                     vllm_wait_s=30.0, vllm_poll_s=10.0)


class Sink:
    def __init__(self):
        self.alerts = []

    def __call__(self, event):
        self.alerts.append(event)


class Runner:
    """Runs successive passes against one host with a persistent state dir."""

    def __init__(self, host, cfg, dry_run=False):
        self.host, self.cfg, self.dry_run = host, cfg, dry_run
        self.sink = Sink()
        self.lines = []
        self.last = None

    def run(self, advance=120.0):
        self.host.t += advance
        w = wd.Watchdog(self.cfg, io=self.host, dry_run=self.dry_run, alert_sink=self.sink,
                        out=self.lines.append)
        w.run_pass()
        self.last = w
        return w

    def events(self):
        p = self.cfg.state_dir / "events.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def alert_keys(self):
        return [e["kind"] for e in self.sink.alerts]

    def state(self):
        return json.loads((self.cfg.state_dir / "state.json").read_text())


@pytest.fixture
def runner(host, cfg):
    return Runner(host, cfg)


# ------------------------------------------------------------------------------ healthy
def test_healthy_pass_does_nothing(runner, host):
    runner.run()
    assert host.mutating() == []
    assert runner.sink.alerts == []
    assert host.posted("table_run_start") == []
    kinds = [e["kind"] for e in runner.events()]
    assert kinds == ["pass"]
    assert runner.state()["passes"] == 1
    s = runner.last.summary
    assert s["containers"]["tuber_1"] == "running" and s["twitch"]["tuber_1"].startswith("live")
    assert s["twitch"]["tuber_0"].startswith("skipped")          # null login


# ------------------------------------------------------------------------------ 1 Ollama + memory
def test_ollama_loaded_model_is_unloaded_loudly(runner, host):
    host.ollama = {"models": [{"name": "qwen3:30b"}]}
    runner.run()
    posts = [(u, json.loads(b)) for m, u, b in host.http_calls if m == "POST" and "11434" in u]
    assert posts == [("http://127.0.0.1:11434/api/generate", {"model": "qwen3:30b", "keep_alive": 0})]
    assert "ollama:loaded" in runner.alert_keys()
    assert any(e["kind"] == "ollama_unload" for e in runner.events())


def test_ollama_empty_or_down_is_left_alone(runner, host):
    runner.run()
    host.ollama_up = False
    runner.run()
    assert [c for c in host.http_calls if c[0] == "POST" and "11434" in c[1]] == []
    assert runner.sink.alerts == []


def test_memory_between_4_and_8_gib_alerts_only(runner, host):
    host.avail = 6 * GiB
    runner.run()
    assert "memory:low" in runner.alert_keys()
    assert host.mutating() == []


def test_memory_under_4_gib_restarts_largest_worker_never_vllm(runner, host):
    host.avail = 3 * GiB
    host.containers[T1]["rss"] = 30 * GiB
    runner.run()
    assert host.mutating() == [["restart", T1]]
    assert "memory:critical" in runner.alert_keys()
    assert all(wd.VLLM_CONTAINER not in a for a in host.mutating())


def test_memory_restart_is_capped_to_one_per_30_min(runner, host):
    host.avail = 3 * GiB
    host.containers[T1]["rss"] = 30 * GiB
    runner.run()
    host.containers[T1]["rss"] = 30 * GiB
    runner.run()
    assert host.mutating() == [["restart", T1]]
    host.containers[T1]["rss"] = 30 * GiB
    runner.run(advance=1900)
    assert host.mutating() == [["restart", T1], ["restart", T1]]


# ------------------------------------------------------------------------------ 2 vLLM
def test_vllm_down_one_pass_waits_two_passes_starts(runner, host):
    host.vllm_up = False
    host.containers[wd.VLLM_CONTAINER]["status"] = "exited"
    runner.run()
    assert host.mutating() == []
    assert runner.state()["vllm"]["down_passes"] == 1
    runner.run()
    assert ["start", wd.VLLM_CONTAINER] in host.mutating()
    ev = [e for e in runner.events() if e["kind"] == "vllm_recovered"]
    assert ev and ev[0]["waited_s"] == 10.0
    assert runner.state()["vllm"]["down_passes"] == 0


def test_vllm_key_is_never_logged_or_stored(runner, host):
    host.vllm_up = False
    host.containers[wd.VLLM_CONTAINER]["status"] = "exited"
    runner.run()
    host.containers[wd.VLLM_CONTAINER]["status"] = "running"   # key readable again
    host.vllm_up = True
    runner.run()
    blob = (runner.cfg.state_dir / "events.jsonl").read_text() + (runner.cfg.state_dir / "state.json").read_text()
    blob += "\n".join(runner.lines)
    assert KEY not in blob


def test_vllm_start_that_never_answers_alerts(runner, host):
    host.vllm_up = False
    host.vllm_starts_up = False
    host.containers[wd.VLLM_CONTAINER]["status"] = "exited"
    runner.run()
    runner.run()
    assert "vllm:slow_start" in runner.alert_keys()


def test_vllm_not_started_while_another_model_holds_the_port(runner, host):
    host.containers[wd.VLLM_CONTAINER]["status"] = "exited"
    host.vllm_up = False
    orig = host.http

    def occupied(method, url, headers=None, body=None, timeout=10.0):
        if url.endswith("/v1/models"):
            host.http_calls.append((method, url, body))
            return 401, "unauthorized"        # the 70b generation server, other key
        return orig(method, url, headers, body, timeout)

    host.http = occupied
    for _ in range(4):
        runner.run()
    assert host.mutating() == []
    assert ["start", wd.VLLM_CONTAINER] not in host.mutating()
    assert "vllm:port_occupied" in runner.alert_keys()


def test_vllm_running_but_unresponsive_is_not_restarted(runner, host):
    host.vllm_up = False                     # container "running" (still loading, or hung)
    for _ in range(9):
        runner.run()
    assert host.mutating() == []
    assert "vllm:hung" in runner.alert_keys()


def test_vllm_wrong_model_alerts(runner, host):
    host.vllm_models = ["something-else"]
    runner.run()
    assert "vllm:model_missing" in runner.alert_keys()


def test_vllm_start_cap_three_per_hour(runner, host):
    host.vllm_starts_up = False
    for _ in range(12):
        host.containers[wd.VLLM_CONTAINER]["status"] = "exited"
        host.vllm_up = False
        runner.run(advance=300)
    starts = [a for a in host.mutating() if a == ["start", wd.VLLM_CONTAINER]]
    assert len(starts) == 3                   # 12 passes x 5 min = 1 h: capped at 3 per rolling hour


# ------------------------------------------------------------------------------ 3 containers
def test_exited_worker_is_started_and_logged(runner, host):
    host.containers[T1]["status"], host.containers[T1]["exit"] = "exited", 137
    runner.run()
    assert host.mutating() == [["start", T1]]
    acts = [e for e in runner.events() if e["kind"] == "action"]
    assert acts[0]["reason"] == "start-exited" and acts[0]["container"] == T1


def test_all_nine_exited_are_started_in_one_pass(runner, host):
    for w in wd.WORKERS:
        host.containers[w.container]["status"] = "exited"
    runner.run()
    assert sorted(a[1] for a in host.mutating()) == sorted(w.container for w in wd.WORKERS)
    assert all(a[0] == "start" for a in host.mutating())


def test_restart_backoff_is_exponential_and_capped_at_three_per_hour(runner, host):
    def die():
        host.containers[T1]["status"], host.containers[T1]["exit"] = "exited", 1

    die()
    runner.run(advance=300)                                  # start #1
    die()
    runner.run(advance=30)                                   # inside the 60 s backoff: skipped
    assert len(host.mutating()) == 1
    runner.run(advance=60)                                   # 90 s since #1 > 60 s: start #2
    assert len(host.mutating()) == 2
    die()
    runner.run(advance=90)                                   # gap now 120 s: skipped
    assert len(host.mutating()) == 2
    runner.run(advance=60)                                   # 150 s since #2: start #3
    assert len(host.mutating()) == 3
    die()
    runner.run(advance=300)                                  # 4th within the hour: alert only
    assert len(host.mutating()) == 3
    assert f"container:{T1}:budget" in runner.alert_keys()
    die()
    runner.run(advance=3600)                                 # an hour later it may try again
    assert len(host.mutating()) == 4


def test_missing_container_alerts_instead_of_inventing_a_start(runner, host):
    del host.containers[T1]
    runner.run()
    assert f"container:{T1}:missing" in runner.alert_keys()
    assert host.mutating() == []


def test_support_container_down_is_alert_only(runner, host):
    host.containers["virtualtubers-kafka-1"]["status"] = "exited"
    runner.run()
    assert "support:virtualtubers-kafka-1" in runner.alert_keys()
    assert host.mutating() == []


# ------------------------------------------------------------------------------ 4 ffmpeg
def test_ffmpeg_missing_alerts_then_restarts_on_third_pass(runner, host):
    host.containers[T1]["etimes"] = []
    runner.run()
    assert "ffmpeg:tuber_1" in runner.alert_keys() and host.mutating() == []
    runner.run()
    assert host.mutating() == []
    runner.run()
    assert host.mutating() == [["restart", T1]]
    assert runner.state()["containers"][T1]["ffmpeg_bad"] == 0


def test_ffmpeg_crash_loop_etimes_reset_detected(runner, host):
    host.containers[T1]["etimes"] = [500]
    runner.run()                                             # baseline
    for _ in range(3):
        host.containers[T1]["etimes"] = [40]                 # restarted again before every pass
        runner.run()
    assert host.mutating() == [["restart", T1]]
    assert any("crash loop" in a["msg"] for a in runner.sink.alerts)


def test_ffmpeg_steady_etimes_growth_is_healthy(runner, host):
    for i in range(4):
        host.containers[T1]["etimes"] = [1000 + 120 * i]
        runner.run()
    assert runner.sink.alerts == [] and host.mutating() == []


def test_ffmpeg_roundtable_two_processes_ok(runner, host):
    host.containers[RT]["etimes"] = [9000, 8990]
    runner.run()
    assert runner.sink.alerts == []


def test_ffmpeg_restart_is_capped_to_one_per_hour(runner, host):
    host.containers[T1]["etimes"] = []
    for _ in range(9):
        runner.run()
    assert [a for a in host.mutating() if a == ["restart", T1]] == [["restart", T1]]


def test_ffmpeg_not_checked_while_container_is_booting(runner, host):
    host.containers[T1]["started"] = host.t + 100 - 30         # 30 s old at pass time
    host.containers[T1]["etimes"] = []
    runner.run()
    assert runner.last.summary["ffmpeg"]["tuber_1"] == "booting"
    assert runner.sink.alerts == []


# ------------------------------------------------------------------------------ 5 Twitch
def test_twitch_offline_three_passes_with_ffmpeg_alive_restarts_once_per_hour(runner, host):
    host.live["mastermanagerchief"] = False
    runner.run()
    runner.run()
    assert host.mutating() == []
    runner.run()
    assert host.mutating() == [["restart", T1]]
    assert "twitch:tuber_1" in runner.alert_keys()
    for _ in range(6):                                       # still offline: capped for the hour
        host.containers[T1]["started"] = host.t - 86400
        runner.run()
    assert host.mutating() == [["restart", T1]]
    host.containers[T1]["started"] = host.t - 86400
    for _ in range(3):
        runner.run(advance=1200)
    assert host.mutating() == [["restart", T1], ["restart", T1]]


def test_twitch_probe_offline_means_distrust_and_do_nothing(runner, host):
    host.live["lofigirl"] = False
    host.live["mastermanagerchief"] = False
    for _ in range(5):
        runner.run()
    assert host.mutating() == []
    assert runner.sink.alerts == []
    assert any(e["kind"] == "twitch_probe_distrusted" for e in runner.events())
    assert runner.last.summary["twitch"]["status"] == "probe-distrusted"
    assert not runner.state()["containers"][T1].get("twitch_offline")


def test_twitch_api_error_is_not_offline(runner, host):
    host.twitch_down = True
    for _ in range(5):
        runner.run()
    assert host.mutating() == []
    assert runner.last.summary["twitch"]["status"] == "probe-distrusted"


def test_twitch_offline_with_dead_ffmpeg_does_not_count(runner, host):
    host.live["mastermanagerchief"] = False
    host.containers[T1]["etimes"] = []
    runner.run()
    assert runner.state()["containers"][T1].get("twitch_offline", 0) == 0


def test_twitch_live_resets_counter(runner, host):
    host.live["mastermanagerchief"] = False
    runner.run()
    runner.run()
    host.live["mastermanagerchief"] = True
    runner.run()
    host.live["mastermanagerchief"] = False
    runner.run()
    runner.run()
    assert host.mutating() == []


def test_twitch_unknown_login_is_skipped_not_actioned(runner, host):
    del host.live["ten_g_hands"]
    runner.run()
    assert runner.last.summary["twitch"]["tuber_2"].startswith("unknown")
    assert host.mutating() == []


def test_channels_fallback_parser_without_pyyaml(tmp_path, monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "yaml":
            raise ImportError
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    probe, ch = wd.load_channels(wd.DEFAULT_CHANNELS)
    assert probe == "lofigirl"
    assert ch["tuber_1"] == "mastermanagerchief" and ch["roundtable"] is None and ch["tuber_6"] == "the_emperor_secus"
    assert len(ch) == 9


def test_shipped_channels_yaml_has_the_six_known_logins():
    probe, ch = wd.load_channels(wd.DEFAULT_CHANNELS)
    assert {k: v for k, v in ch.items() if v} == {
        "tuber_1": "mastermanagerchief", "tuber_2": "ten_g_hands", "tuber_3": "spam_of_frogs",
        "tuber_4": "braxton_caruso", "tuber_5": "burtyrenoldo", "tuber_6": "the_emperor_secus"}
    assert [k for k, v in ch.items() if not v] == ["roundtable", "tuber_0", "tuber_7"]


# ------------------------------------------------------------------------------ 6 table
def test_table_not_active_sends_run_start_with_backoff(runner, host):
    host.status = good_status(mode="manual", run={"active": False, "finished": False, "halted": False})
    runner.run()
    assert host.posted("table_run_start") == [{"to": "tuber_0", "type": "table_run_start",
                                               "payload": {"from": "next"}}]
    runner.run()                                             # 120 s later: still inside the 240 s backoff
    assert len(host.posted("table_run_start")) == 1
    runner.run(advance=200)
    assert len(host.posted("table_run_start")) == 2
    host.status = good_status()                              # it took
    runner.run()
    assert runner.state()["table"]["run_start"] == {}


def test_table_halted_run_is_restarted_too(runner, host):
    host.status = good_status(run={"active": False, "finished": False, "halted": True})
    runner.run()
    assert len(host.posted("table_run_start")) == 1


def test_table_finished_run_is_never_auto_restarted(runner, host):
    host.status = good_status(run={"active": False, "finished": True, "halted": False})
    runner.run()
    assert host.posted("table_run_start") == []
    assert "table:finished" in runner.alert_keys()


def test_table_check_skipped_when_gm_is_down_or_booting(runner, host):
    host.containers[GM]["status"] = "exited"
    runner.run()
    assert runner.last.summary["table"]["status"] == "skipped"
    assert host.posted("table_status_request") == []
    host.containers[GM].update(status="running", started=host.t + 120 - 20)
    runner.run()
    assert runner.last.summary["table"]["reason"] == "worker-gm booting"


def test_table_no_reply_alerts_after_three_passes(runner, host):
    host.status = None
    runner.run()
    runner.run()
    assert "table:no_reply" not in runner.alert_keys()
    runner.run()
    assert "table:no_reply" in runner.alert_keys()


def test_table_stalled_30_min_alerts_and_restarts_gm_once_per_2h(runner, host):
    runner.run()                                             # records the progress signature
    for _ in range(15):                                      # 30 min of the same signature
        host.lines = []
        runner.run()
    assert host.mutating() == [["restart", GM]]
    assert "table:stalled" in runner.alert_keys()
    for _ in range(25):                                      # sig_ts reset by the restart; stalls again
        host.lines = []
        host.containers[GM]["started"] = host.t - 86400
        runner.run()
    assert host.mutating() == [["restart", GM]]              # 2 h cap holds (50 min later)
    host.containers[GM]["started"] = host.t - 86400
    for _ in range(60):
        host.lines = []
        host.containers[GM]["started"] = host.t - 86400
        runner.run()
    assert host.mutating().count(["restart", GM]) == 2


def test_table_not_stalled_while_lines_are_on_air(runner, host):
    runner.run()
    for _ in range(20):
        runner.run()                                         # same counters, but table_line keeps coming
    assert host.mutating() == []


def test_table_progress_resets_the_stall_clock(runner, host):
    runner.run()
    for i in range(30):
        host.lines = []
        st = good_status()
        st["block"]["ambient_played"] = 5 + i // 5           # a scene finishes every 10 min
        host.status = st
        runner.run()
    assert host.mutating() == []


def test_table_stall_not_judged_when_message_log_unreadable(runner, host):
    runner.run()
    host.logs_up = False
    host.status = good_status()
    for _ in range(20):
        runner.run()
    assert host.mutating() == []


def test_table_stuck_total_growth_is_recorded(runner, host):
    runner.run()
    st = good_status()
    st["block"]["stuck"] = {"consecutive": 1, "total": 2, "limit": 3, "cooldown_s": 0}
    host.status = st
    runner.run()
    grew = [e for e in runner.events() if e["kind"] == "table_stuck_total_grew"]
    assert grew and "0 -> 2" in grew[0]["msg"]
    assert runner.state()["table"]["stuck_total"] == 2


def test_table_stuck_streak_alerts_at_limit(runner, host):
    st = good_status()
    st["block"]["stuck"] = {"consecutive": 3, "total": 9, "limit": 3, "cooldown_s": 300}
    host.status = st
    runner.run()
    assert "table:stuck_streak" in runner.alert_keys()


def test_stall_clock_starts_at_gm_restart_not_before(runner, host):
    runner.run()
    host.lines = []
    for _ in range(14):
        runner.run()                                         # 28 min stagnant
    host.containers[GM].update(status="exited")
    runner.run()                                             # watchdog starts the GM (uptime ~0)
    host.containers[GM]["started"] = host.t - 400            # up for 400 s: past the boot grace
    runner.run()
    assert [a for a in host.mutating() if a[0] == "restart"] == []


# ------------------------------------------------------------------------------ 7 tiles
def test_tile_rss_between_3_and_6_gib_alerts_only(runner, host):
    host.containers[RT]["ps_tiles"] = [500_000, 4 * 1024 * 1024]
    runner.run()
    assert "tiles:rss" in runner.alert_keys() and host.mutating() == []


def test_tile_rss_over_6_gib_restarts_roundtable_capped_two_hours(runner, host):
    host.containers[RT]["ps_tiles"] = [7 * 1024 * 1024]
    runner.run()
    assert host.mutating() == [["restart", RT]]
    assert "tiles:leak" in runner.alert_keys()
    host.containers[RT]["ps_tiles"] = [7 * 1024 * 1024]
    runner.run(advance=1800)
    assert host.mutating() == [["restart", RT]]


def test_tiles_small_are_quiet(runner, host):
    host.containers[RT]["ps_tiles"] = [600_000] * 7
    runner.run()
    assert runner.sink.alerts == [] and runner.last.summary["tiles"]["tiles"] == 7


# ------------------------------------------------------------------------------ 8 disk
def test_disk_over_93_alerts_and_not_below(runner, host):
    host.disk = 93.0
    runner.run()
    assert runner.sink.alerts == []
    host.disk = 94.5
    runner.run()
    assert "disk:full" in runner.alert_keys()


# ------------------------------------------------------------------------------ alerting
def test_alert_dedupe_repeat_and_recovery(runner, host):
    host.disk = 95
    runner.run()
    runner.run()
    runner.run()
    assert runner.alert_keys().count("disk:full") == 1       # deduped inside the repeat window
    runner.run(advance=2000)
    assert runner.alert_keys().count("disk:full") == 2
    host.disk = 50
    runner.run()
    assert any(e["kind"] == "recovered" and "disk:full" in e["msg"] for e in runner.events())
    assert "disk:full" not in runner.state()["alerts"]


def test_alert_sink_failure_does_not_break_the_pass(runner, host):
    def boom(event):
        raise RuntimeError("gateway down")

    runner.sink = boom
    host.disk = 99
    w = runner.run()
    assert any(e["kind"] == "alert_sink_error" for e in runner.events())
    assert w.summary["disk"]["status"] == "alert"


def test_default_alert_sink_is_a_noop():
    assert wd.default_alert_sink({"kind": "x"}) is None


def test_a_crashing_check_is_isolated(runner, host, monkeypatch):
    monkeypatch.setattr(wd.Watchdog, "check_disk", lambda self: 1 / 0)
    runner.run()
    assert "check_error:disk" in runner.alert_keys()
    assert runner.last.summary["containers"]["tuber_1"] == "running"


# ------------------------------------------------------------------------------ safety gate
def gate(host, cfg, **kw):
    return wd.Watchdog(cfg, io=host, out=lambda s: None, **kw)


@pytest.mark.parametrize("args", [
    ["stop", T1], ["rm", "-f", T1], ["kill", T1], ["compose", "down"], ["run", "x"], ["pull", "x"],
    ["restart", "vllm-hermes3-70b"], ["start", "vllm-hermes3-70b"], ["exec", "vllm-hermes3-70b", "ls"],
    ["inspect", "vllm-hermes3-70b"], ["restart", "vllm-agents"], ["restart", "some-other-container"],
    ["exec", "some-other-container", "ls"], ["start", T1, "extra"], [],
])
def test_gate_refuses_anything_outside_the_allow_list(host, cfg, args):
    w = gate(host, cfg)
    with pytest.raises(wd.ForbiddenDockerAction):
        w.docker(args, reason="x", cap=(1, 60))
    assert host.calls == []


def test_gate_requires_reason_and_cap(host, cfg):
    with pytest.raises(wd.ForbiddenDockerAction):
        gate(host, cfg).docker(["restart", T1])


def test_gate_global_rate_limit(runner, host, cfg):
    cfg.global_docker_per_hour = 3
    cfg.memory_cap = cfg.ffmpeg_cap = (99, 3600)
    w = gate(host, cfg)
    w.now = T0
    results = [w.docker(["restart", T1], reason=f"r{i}", cap=(5, 3600)) for i in range(5)]
    assert [r.ok for r in results] == [True, True, True, False, False]
    assert results[3].skipped == "global rate limit"
    assert len(host.mutating()) == 3
    assert "watchdog:global_rate_limit" in w.state["alerts"]


def test_gate_global_limit_window_slides(host, cfg):
    cfg.global_docker_per_hour = 2
    w = gate(host, cfg)
    w.now = T0
    for i in range(2):
        assert w.docker(["restart", T1], reason=f"r{i}", cap=(5, 3600)).ok
    assert not w.docker(["restart", T1], reason="r9", cap=(5, 3600)).ok
    w.now = T0 + 3601
    assert w.docker(["restart", T1], reason="r9", cap=(5, 3600)).ok


def test_gate_action_gap_sleeps_between_mutations(host, cfg):
    cfg.action_gap_s = 2.0
    w = gate(host, cfg)
    t = host.t
    w.docker(["restart", T1], reason="a", cap=(5, 3600))
    assert host.t == t                                       # first action: no wait
    w.docker(["restart", RT], reason="a", cap=(5, 3600))
    assert host.t == t + 2.0


def test_no_watchdog_run_ever_issues_a_forbidden_verb(runner, host):
    # Break everything at once and run many passes: only start/restart/exec/inspect/stats happen.
    for w in wd.WORKERS:
        host.containers[w.container]["status"] = "exited"
    host.vllm_up = False
    host.containers[wd.VLLM_CONTAINER]["status"] = "exited"
    host.avail = 1 * GiB
    host.ollama = {"models": [{"name": "m"}]}
    for _ in range(10):
        runner.run()
    assert {c[0] for c in host.calls} <= {"start", "restart", "exec", "inspect", "stats"}
    assert not any("hermes3" in " ".join(c) for c in host.calls)
    assert not any(c == ["restart", wd.VLLM_CONTAINER] for c in host.calls)


# ------------------------------------------------------------------------------ dry run / pause
def test_dry_run_on_a_dead_host_plans_but_changes_nothing(host, cfg):
    for w in wd.WORKERS:
        host.containers[w.container]["status"] = "exited"
    host.containers[wd.VLLM_CONTAINER]["status"] = "exited"
    host.vllm_up = False
    host.ollama = {"models": [{"name": "qwen3:30b"}]}
    host.avail = 2 * GiB
    host.disk = 99
    r = Runner(host, cfg, dry_run=True)
    r.run()
    assert host.mutating() == []
    # only the read-only Twitch GQL probe POSTs; nothing to Ollama or message-api
    assert [c for c in host.http_calls if c[0] == "POST" and c[1] != wd.TWITCH_GQL_URL] == []
    assert not cfg.state_dir.exists()                        # no state, no event log, no lock
    planned = r.last.planned
    assert sum(p.startswith("docker start virtualtubers-worker-") for p in planned) == 9
    assert any("/api/generate" in p and "keep_alive" in p for p in planned)
    out = "\n".join(r.lines)
    assert "PLAN docker start virtualtubers-worker-gm-1" in out and "planned actions" in out
    assert "disk:full" in out


def test_dry_run_plans_vllm_start_when_counter_is_due(host, cfg):
    Runner(host, cfg).run()                                  # a real healthy pass creates state
    host.containers[wd.VLLM_CONTAINER]["status"] = "exited"
    host.vllm_up = False
    state = json.loads((cfg.state_dir / "state.json").read_text())
    state["vllm"]["down_passes"] = 1
    (cfg.state_dir / "state.json").write_text(json.dumps(state))
    r = Runner(host, cfg, dry_run=True)
    r.run()
    assert "docker start vllm-agents  [vllm-down]" in r.last.planned
    assert host.mutating() == []
    assert json.loads((cfg.state_dir / "state.json").read_text())["vllm"]["down_passes"] == 1


def test_dry_run_table_run_start_is_only_planned(host, cfg):
    host.status = good_status(mode="manual", run={"active": False, "finished": False, "halted": False})
    r = Runner(host, cfg, dry_run=True)
    r.run()
    assert host.posted("table_run_start") == []
    assert any("table_run_start" in p for p in r.last.planned)


def test_pause_file_skips_everything(runner, host, cfg):
    cfg.state_dir.mkdir(parents=True)
    (cfg.state_dir / "PAUSE").write_text("")
    host.containers[T1]["status"] = "exited"
    host.disk = 99
    runner.run()
    assert host.calls == [] and host.http_calls == []
    assert [e["kind"] for e in runner.events()] == ["paused"]
    (cfg.state_dir / "PAUSE").unlink()
    runner.run()
    assert host.mutating() == [["start", T1]]


def test_corrupt_state_file_is_set_aside(runner, host, cfg):
    cfg.state_dir.mkdir(parents=True)
    (cfg.state_dir / "state.json").write_text("{not json")
    runner.run()
    assert (cfg.state_dir / "state.corrupt").exists()
    assert runner.state()["passes"] == 1


def test_event_log_rotates(runner, host, cfg):
    cfg.log_max_bytes = 200
    for _ in range(4):
        runner.run()
    assert (cfg.state_dir / "events.jsonl.1").exists()


# ------------------------------------------------------------------------------ real IO wrappers
def test_realio_run_maps_errors(monkeypatch):
    io = wd.RealIO()

    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("docker", 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert io.run(["docker", "ps"], 1)[0] == 124

    def missing(*a, **k):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(subprocess, "run", missing)
    assert io.run(["docker", "ps"])[0] == 127
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 3, "o", "e"))
    assert io.run(["docker", "ps"]) == (3, "o", "e")


def test_realio_http_maps_errors(monkeypatch):
    io = wd.RealIO()

    def refused(*a, **k):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(wd.urllib.request, "urlopen", refused)
    assert io.http("GET", "http://x")[0] == 0

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": 1}'

    monkeypatch.setattr(wd.urllib.request, "urlopen", lambda *a, **k: Resp())
    assert io.http("GET", "http://x") == (200, '{"ok": 1}')

    def err401(req, timeout=0):
        raise urllib.error.HTTPError("http://x", 401, "no", {}, None)

    monkeypatch.setattr(wd.urllib.request, "urlopen", err401)
    assert io.http("GET", "http://x")[0] == 401


def test_realio_disk_and_meminfo_return_sane_values():
    io = wd.RealIO()
    assert 0 < io.disk_pct("/") <= 100
    assert io.meminfo_available() is None or io.meminfo_available() > 0


def test_parsers():
    assert wd.parse_size("34.5GiB / 119GiB".split("/")[0]) == int(34.5 * GiB)
    assert wd.parse_size("512MiB") == 512 * 1024 ** 2
    assert wd.parse_size("garbage") is None
    assert wd.parse_docker_time("2026-10-21T05:18:35.839506123Z") is not None
    assert wd.parse_docker_time("0001-01-01T00:00:00Z") is None


def test_main_dry_run_uses_injected_io_and_exits_zero(host, cfg, capsys):
    rc = wd.main(["--dry-run", "--state-dir", str(cfg.state_dir), "--channels", str(cfg.channels_path)], io=host)
    assert rc == 0
    assert "planned actions (0)" in capsys.readouterr().out


def test_main_second_instance_is_locked_out(host, cfg, capsys):
    import fcntl
    cfg.state_dir.mkdir(parents=True)
    with open(cfg.state_dir / "lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        rc = wd.main(["--state-dir", str(cfg.state_dir), "--channels", str(cfg.channels_path)], io=host)
    assert rc == 0 and host.calls == []
    assert "still running" in capsys.readouterr().out
