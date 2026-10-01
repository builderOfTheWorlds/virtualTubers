"""Tests for the ashiorid_office worker configs + compose override (OB-22).

config/workers/office/*.yaml are loaded through message_bus.load_worker_config
(the loader app/agent.py uses) and checked against their sources of truth:
app/office/roles.py (seat, handler family), the cast files (name, prompt,
voice, face), config/voices.yaml, and the OB-07 model allocation. The compose
override is checked as raw YAML (no docker needed): every seat is mounted on
exactly one service with a matching WORKER_ID, and tokens only ever appear as
environment-variable names.
"""
import re
from pathlib import Path

import pytest
import yaml

from character_schema import resolve_params
from message_bus import load_worker_config
from office.roles import HANDLER_ROLE, SEAT, OfficeRole

ROOT = Path(__file__).resolve().parents[1]
OFFICE_DIR = ROOT / "config" / "workers" / "office"
CAST_DIR = ROOT / "campaigns" / "ashiorid_office" / "cast"
BASE_COMPOSE = ROOT / "docker-compose.yml"
OFFICE_COMPOSE = ROOT / "docker-compose.office.yml"
REGISTRY = yaml.safe_load((ROOT / "config" / "voices.yaml").read_text(encoding="utf-8"))["voices"]

ROLES = list(OfficeRole)
SEATS = [f"tuber_{i}" for i in range(8)]
SPEAKING = [r for r in ROLES if r is not OfficeRole.PARTY_MEMBER]
LANE_WRITERS = {OfficeRole.TECH_LEAD, OfficeRole.ANALYST, OfficeRole.ENGINEER,
                OfficeRole.MARKETING, OfficeRole.OFFICE_MANAGER}

# OB-07 (.claude/prompts/office_w0_benchmark.md §3) speaking-model allocation.
PLOT_MODEL = "gemma4:26b"
SUPPORT_MODEL = "gemma4:12b-it-q4_K_M"
EXPECTED_MODEL = {
    OfficeRole.CEO: PLOT_MODEL,
    OfficeRole.TECH_LEAD: PLOT_MODEL,
    OfficeRole.ANALYST: PLOT_MODEL,
    OfficeRole.MARKETING: PLOT_MODEL,
    OfficeRole.ENGINEER: PLOT_MODEL,      # speaks via gemma; codes via qwen (below)
    OfficeRole.TESTER: SUPPORT_MODEL,
    OfficeRole.OFFICE_MANAGER: SUPPORT_MODEL,
    OfficeRole.PARTY_MEMBER: "llama3.1:8b",
}
ENGINEER_CODE_MODEL = "qwen3.8:27b"
FALLBACK_MODEL = "llama3.1:8b"

GITEA = {"base_url": "http://192.168.1.120:3300", "owner": "gitea_admin", "repo": "fraud-stop"}
FRAUD_STOP_REMOTES = {
    "http://192.168.1.120:3300/gitea_admin/fraud-stop.git",
    "ssh://git@192.168.1.120:2222/gitea_admin/fraud-stop.git",
}
ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
# A key that holds (or names) a credential: token, token_env, password, ...
# but not max_tokens / max_context_tokens.
SECRET_KEY_RE = re.compile(r"(?:^|_)(?:token|password|secret|api_key)(?:$|_)", re.IGNORECASE)
CONFIG_MOUNT_RE = re.compile(r"^(.*):/config/worker\.yaml(?::ro)?$")
TOKEN_ENV_NAMES = {"GITEA_TOKEN_OFFICE", "GITEA_TOKEN_OBSERVER"}
# Shapes of real secrets that must never be committed in a config.
SECRET_VALUE_RES = [
    re.compile(r"\b[0-9a-f]{32,}\b"),                 # Gitea/hex tokens
    re.compile(r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{10,}"),
    re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{10,}"),
    re.compile(r"https?://[^/\s:@]+:[^/\s@]+@"),      # user:pass@ in a URL
]


def _config_path(role):
    return OFFICE_DIR / f"{role.value}.yaml"


def _cfg(role):
    return load_worker_config(str(_config_path(role)))


def _cast(role):
    return yaml.safe_load((CAST_DIR / f"{role.value}.yaml").read_text(encoding="utf-8"))


def _minutes(duration):
    m = re.fullmatch(r"(\d+)([smh])", str(duration))
    assert m, f"unparseable keep_alive {duration!r}"
    n, unit = int(m.group(1)), m.group(2)
    return {"s": n / 60, "m": n, "h": n * 60}[unit]


def _walk(node, path=()):
    """Yield (key path, value) for every scalar leaf of a parsed YAML tree."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, path + (str(k),))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, path + (str(i),))
    else:
        yield path, node


def _office_compose():
    return yaml.safe_load(OFFICE_COMPOSE.read_text(encoding="utf-8"))


def _mounted_config(service):
    """The config/workers/office/<file> a compose service mounts at /config/worker.yaml."""
    for vol in service.get("volumes") or []:
        m = isinstance(vol, str) and CONFIG_MOUNT_RE.match(vol)
        if m:
            return m.group(1).split("/")[-1]
    return None


# ── per-seat configs ─────────────────────────────────────────────────────────
def test_office_dir_holds_exactly_the_eight_seats_plus_roundtable():
    names = sorted(p.name for p in OFFICE_DIR.glob("*.yaml"))
    assert names == sorted([f"{r.value}.yaml" for r in ROLES] + ["roundtable.yaml"])


@pytest.mark.parametrize("role", ROLES, ids=lambda r: r.value)
def test_config_roles_and_seat_match_roles_module(role):
    cfg = _cfg(role)
    agent = cfg["agent"]
    assert agent["role"] == HANDLER_ROLE[role]
    assert agent["office_role"] == role.value
    assert cfg["message_bus"]["worker_id"] == SEAT[role]
    assert cfg["world_state"]["worker_id"] == SEAT[role]
    assert _cast(role)["seat"] == SEAT[role]


@pytest.mark.parametrize("role", ROLES, ids=lambda r: r.value)
def test_persona_name_and_prompt_are_copied_from_the_cast(role):
    """OB-21: the reused manager/coder/tester handlers narrate with
    agent.system_prompt, so it must be the character's own prompt."""
    cfg, cast = _cfg(role), _cast(role)
    assert cfg["agent"]["name"] == cast["name"]
    assert cfg["agent"]["system_prompt"] == cast["system_prompt"]
    assert cfg["avatar"]["name"] == cast["name"]
    if role in LANE_WRITERS:
        assert cfg["agent"]["office"]["author_name"] == cast["name"]


@pytest.mark.parametrize("role", ROLES, ids=lambda r: r.value)
def test_voice_is_the_cast_registry_voice_and_every_seat_is_keyed(role):
    cfg = _cfg(role)
    voice = cfg["voice"]
    assert voice["model_path"] == REGISTRY[_cast(role)["voice"]]["model_path"]
    speakers = voice["speakers"]
    assert set(SEATS) <= set(speakers)
    for other in ROLES:
        expected = REGISTRY[_cast(other)["voice"]]["model_path"]
        assert speakers[SEAT[other]]["model_path"] == expected, (role.value, other.value)
        assert voice["speaker_names"][SEAT[other]] == _cast(other)["name"]


@pytest.mark.parametrize("role", ROLES, ids=lambda r: r.value)
def test_llm_model_follows_ob07_allocation(role):
    llm = _cfg(role)["llm"]
    assert llm["provider"] == "ollama"
    assert llm["base_url"] == "http://192.168.1.23:11434"
    assert llm["model"] == EXPECTED_MODEL[role]


@pytest.mark.parametrize("role", SPEAKING, ids=lambda r: r.value)
def test_speaking_seats_record_keep_alive_ctx_and_fallback(role):
    llm = _cfg(role)["llm"]
    assert _minutes(llm["keep_alive"]) >= 30
    assert llm["num_ctx"] == 8192
    assert llm["think"] is False
    assert llm["fallback_model"] == FALLBACK_MODEL


def test_engineer_codes_with_aider_on_qwen_in_the_fraud_stop_clone():
    cfg = _cfg(OfficeRole.ENGINEER)
    cb = cfg["coding_backend"]
    assert cb["provider"] == "aider"
    assert cb["model"] == ENGINEER_CODE_MODEL
    assert cb["workspace"] == cfg["agent"]["office"]["workspace"] == "/data/repos/fraud-stop"


@pytest.mark.parametrize("role", [r for r in ROLES if r is not OfficeRole.ENGINEER],
                         ids=lambda r: r.value)
def test_only_the_engineer_has_a_coding_backend(role):
    assert "coding_backend" not in _cfg(role)


@pytest.mark.parametrize("role", [OfficeRole.ENGINEER, OfficeRole.TESTER], ids=lambda r: r.value)
def test_engineer_and_tester_escalate_to_the_tech_lead_seat(role):
    assert _cfg(role)["agent"]["manager_id"] == SEAT[OfficeRole.TECH_LEAD]


def test_tester_reads_the_engineer_workspace_by_seat():
    workspaces = _cfg(OfficeRole.TESTER)["agent"]["workspaces"]
    assert workspaces == {SEAT[OfficeRole.ENGINEER]: f"/data/repos/{SEAT[OfficeRole.ENGINEER]}"}


@pytest.mark.parametrize("role", ROLES, ids=lambda r: r.value)
def test_office_block_points_at_the_fraud_stop_repo(role):
    office = _cfg(role)["agent"]["office"]
    assert office["pack_dir"] == "/campaigns/ashiorid_office"
    gitea = office["gitea"]
    assert {k: gitea[k] for k in GITEA} == GITEA
    if role in LANE_WRITERS:
        assert office["workspace"] == "/data/repos/fraud-stop"
        assert office["remote_url"] in FRAUD_STOP_REMOTES
        # "auto" -> the current week trunk loop/<W> (agent_handlers.office._base_branch).
        assert office["base_branch"] == "auto"
    else:
        assert "workspace" not in office


@pytest.mark.parametrize("role", ROLES, ids=lambda r: r.value)
def test_gitea_token_is_an_env_var_name_with_the_right_scope(role):
    gitea = _cfg(role)["agent"]["office"]["gitea"]
    assert ENV_NAME_RE.match(gitea["token_env"])
    if role is OfficeRole.PARTY_MEMBER:
        assert gitea["token_env"] == "GITEA_TOKEN_OBSERVER"
        assert gitea["read_only"] is True
    else:
        assert gitea["token_env"] == "GITEA_TOKEN_OFFICE"


@pytest.mark.parametrize("role", ROLES, ids=lambda r: r.value)
def test_only_the_ceo_narrates_phase_changes(role):
    """OB-07: turns must be serialized; a phase_change broadcast would
    otherwise fire seven LLM calls at once. The Party Member never speaks."""
    office = _cfg(role)["agent"]["office"]
    if role is OfficeRole.PARTY_MEMBER:
        assert "narrate_phase_change" not in office
    else:
        assert office["narrate_phase_change"] is (role is OfficeRole.CEO)


@pytest.mark.parametrize("role", ROLES, ids=lambda r: r.value)
def test_avatar_face_matches_the_cast_and_the_shared_geometry(role):
    cfg, cast = _cfg(role), _cast(role)
    block = cfg["avatar"]["codec_avatar"]
    assert cfg["avatar"]["provider"] == "codec_avatar"
    assert block["character_params"] == cast["character_params"]
    resolve_params(block["character_params"], strict=True)
    dev = load_worker_config(str(ROOT / "config" / "workers" / "coder.yaml"))["avatar"]["codec_avatar"]
    for key in ("window_pos", "width", "height", "background"):
        assert block[key] == dev[key], key
    assert cfg["layout"]["preset"] == "tuber_base"


# ── office roundtable ────────────────────────────────────────────────────────
def test_exactly_one_office_director():
    directors = [p.name for p in sorted(OFFICE_DIR.glob("*.yaml"))
                 if (load_worker_config(str(p)).get("agent") or {}).get("role") == "roundtable"]
    assert directors == ["roundtable.yaml"]


def test_roundtable_roster_seats_the_cast_with_their_faces():
    cfg = load_worker_config(str(OFFICE_DIR / "roundtable.yaml"))
    assert cfg["layout"]["preset"] == "roundtable"
    assert cfg["message_bus"]["worker_id"] == "roundtable"
    roster = cfg["roster"]
    assert sorted(roster) == SEATS
    for role in ROLES:
        entry, cast = roster[SEAT[role]], _cast(role)
        assert entry["name"] == cast["name"]
        assert entry["character_params"] == cast["character_params"]
        assert cfg["voice"]["speakers"][SEAT[role]]["model_path"] == \
            REGISTRY[cast["voice"]]["model_path"]


def test_roundtable_music_theme_exists():
    music = load_worker_config(str(OFFICE_DIR / "roundtable.yaml"))["music"]
    assert (ROOT / "campaigns" / music["theme_name"] / "music" / "theme.yaml").is_file()


# ── secrets ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", sorted(OFFICE_DIR.glob("*.yaml")), ids=lambda p: p.name)
def test_office_configs_hold_no_secret_values(path):
    text = path.read_text(encoding="utf-8")
    for pattern in SECRET_VALUE_RES:
        assert not pattern.search(text), f"{path.name}: secret-shaped value {pattern.pattern}"
    for keys, value in _walk(yaml.safe_load(text)):
        if SECRET_KEY_RE.search(keys[-1]):
            assert isinstance(value, str) and ENV_NAME_RE.match(value), \
                f"{path.name}: {'.'.join(keys)} must name an env var, not hold a value"


def test_compose_override_passes_tokens_by_env_var_name_only():
    text = OFFICE_COMPOSE.read_text(encoding="utf-8")
    for pattern in SECRET_VALUE_RES:
        assert not pattern.search(text), pattern.pattern
    for name, svc in _office_compose()["services"].items():
        for key, value in (svc.get("environment") or {}).items():
            if SECRET_KEY_RE.search(key):
                assert value == "" or value == f"${{{key}:-}}", (name, key, value)


# ── compose override ─────────────────────────────────────────────────────────
def test_every_seat_is_mounted_on_exactly_one_service_with_matching_worker_id():
    services = _office_compose()["services"]
    seen = {}
    for name, svc in services.items():
        if not name.startswith("worker-"):
            continue  # e.g. control-panel only gets CONTROL_PANEL_SHOW (OB-32)
        mounted = _mounted_config(svc)
        assert mounted, f"{name} does not mount an office config"
        cfg = load_worker_config(str(OFFICE_DIR / mounted))
        expected_id = cfg["message_bus"]["worker_id"]
        worker_id = (svc.get("environment") or {}).get("WORKER_ID")
        # worker-roundtable keeps the base file's WORKER_ID ("roundtable").
        assert worker_id in (expected_id, None), (name, worker_id, expected_id)
        seen.setdefault(mounted, []).append(name)
    assert sorted(seen) == sorted(p.name for p in OFFICE_DIR.glob("*.yaml"))
    assert all(len(v) == 1 for v in seen.values()), seen


def test_only_the_observer_gets_the_observer_token_and_it_gets_no_write_token():
    services = _office_compose()["services"]
    observer_env = services["worker-observer"]["environment"]
    assert "GITEA_TOKEN_OBSERVER" in observer_env
    assert "GITEA_TOKEN_OFFICE" not in observer_env
    for name, svc in services.items():
        if name != "worker-observer":
            assert "GITEA_TOKEN_OBSERVER" not in (svc.get("environment") or {}), name


def test_observer_follows_stream_worker_conventions_without_collisions():
    base = yaml.safe_load(BASE_COMPOSE.read_text(encoding="utf-8"))["services"]
    env = _office_compose()["services"]["worker-observer"]["environment"]
    base_displays = {s["environment"]["DISPLAY_NUM"] for n, s in base.items()
                     if n.startswith("worker-")}
    assert env["DISPLAY_NUM"] not in base_displays
    assert env["STREAM_KEY"].startswith("${TUBER7_STREAM_KEY")
    assert env["WORKER_ID"] == SEAT[OfficeRole.PARTY_MEMBER]


def test_dev_team_compose_does_not_reference_office_configs():
    assert "config/workers/office" not in BASE_COMPOSE.read_text(encoding="utf-8")
    assert "worker-observer" not in yaml.safe_load(BASE_COMPOSE.read_text(encoding="utf-8"))["services"]


# ── CEO day runner (OB-30 / OB-33) ───────────────────────────────────────────
def _ceo_day_runner():
    return _cfg(OfficeRole.CEO)["agent"]["office"]["day_runner"]


def test_ceo_day_runner_block_is_enabled_and_wired_to_the_office_playlist():
    from office.day_runner import day_runner_enabled, load_factory

    cfg = _cfg(OfficeRole.CEO)["agent"]
    block = _ceo_day_runner()
    assert day_runner_enabled(cfg) and block["enabled"] is True
    assert block["playlist"] == "office.playlist:day_runner_playlist"
    assert callable(load_factory(block["playlist"]))
    assert block["playlist_options"]["scenes_dir"] == "/campaigns/ashiorid_office/scenes"
    assert block["corpus_export"].startswith("/data/corpus/")
    assert block["backlog"] is True
    # Only the CEO runs the day.
    for role in ROLES:
        if role is not OfficeRole.CEO:
            assert "day_runner" not in (_cfg(role)["agent"].get("office") or {}), role


def test_ceo_day_runner_state_lives_on_a_persistent_worker_gm_volume():
    state_dir = str(Path(_ceo_day_runner()["state_path"]).parent.as_posix())
    compose = _office_compose()
    named = set(compose.get("volumes") or {})
    mounts = [v for v in compose["services"]["worker-gm"]["volumes"] if isinstance(v, str)]
    matching = [v for v in mounts if v.split(":")[1] == state_dir]
    assert len(matching) == 1, mounts
    assert matching[0].split(":")[0] in named       # a named volume, not a tmp bind
    assert not matching[0].endswith(":ro")
    env = compose["services"]["worker-gm"]["environment"]
    assert env["MESSAGE_API_URL"] == "http://message-api:8000"


def test_ceo_config_builds_a_day_runner_with_the_real_playlist(monkeypatch):
    from office.day_runner import build_day_runner
    from office.playlist import DayRunnerPlaylist

    monkeypatch.setenv("MESSAGE_API_URL", "http://message-api:8000")
    cfg = _cfg(OfficeRole.CEO)["agent"]
    runner = build_day_runner(cfg, worker_id="tuber_0")
    assert isinstance(runner.playlist, DayRunnerPlaylist)
    assert runner.playlist.playlist.library.url == "http://message-api:8000/replays"
    assert runner.state_path == _ceo_day_runner()["state_path"]
    assert runner.sources["feature"] is not None and runner.sources["backlog"] is not None


# ── batch B: live transcript, week-branch base, roundtable replays ───────────
@pytest.mark.parametrize("role", ROLES, ids=lambda r: r.value)
def test_speaking_seats_opt_into_the_live_transcript(role):
    import live_pane

    agent = _cfg(role)["agent"]
    if role is OfficeRole.PARTY_MEMBER:
        assert "live_transcript" not in agent["office"]
        assert not live_pane.live_transcript_enabled(agent)
    else:
        assert agent["office"]["live_transcript"] is True
        assert live_pane.live_transcript_enabled(agent)


@pytest.mark.parametrize("role", ROLES, ids=lambda r: r.value)
def test_speaking_seats_opt_into_character_say(role):
    """The 7 speaking seats publish every spoken line as a v4 character_say;
    the Party Member never speaks, so his config has no flag."""
    from office import character_say

    agent = _cfg(role)["agent"]
    if role is OfficeRole.PARTY_MEMBER:
        assert "character_say" not in agent["office"]
        assert not character_say.character_say_enabled(agent)
    else:
        assert agent["office"]["character_say"] is True
        assert character_say.character_say_enabled(agent)


@pytest.mark.parametrize("role", sorted(LANE_WRITERS, key=lambda r: r.value), ids=lambda r: r.value)
def test_lane_writers_target_the_current_week_branch(role, monkeypatch):
    from datetime import datetime, timezone

    from agent_handlers import office

    monkeypatch.delenv("OFFICE_EPOCH", raising=False)
    monkeypatch.delenv("OFFICE_TZ", raising=False)

    agent = _cfg(role)["agent"]
    now = datetime(2026, 10, 5, 16, 0, tzinfo=timezone.utc)   # Mon of loop week 2 (v4 numbering)
    assert office._base_branch(agent, now=now) == "loop/2"


def test_ceo_replays_air_on_the_roundtable_with_a_seat_cast():
    block = _ceo_day_runner()
    assert block["replay_target"] == "roundtable"
    roundtable = load_worker_config(str(OFFICE_DIR / "roundtable.yaml"))
    assert block["replay_target"] == roundtable["message_bus"]["worker_id"]
    cast = block["playlist_options"]["cast"]
    speaking_seats = [SEAT[r] for r in SPEAKING]
    assert cast == {seat: seat for seat in speaking_seats}
    assert set(cast.values()) <= set(roundtable["roster"])
    assert SEAT[OfficeRole.PARTY_MEMBER] not in cast


def test_ceo_day_runner_sends_playlist_replays_to_the_roundtable(monkeypatch):
    from office.day_runner import build_day_runner

    class OneReplay:
        def off_hours(self, day, context):
            return {"episode": "office-claude_code-sess-001", "cast": {"tuber_0": "tuber_0"}}

        def stall(self, day, context):
            return None

    class Sink(list):
        def send(self, msg):
            self.append(msg)

    sent = []
    runner = build_day_runner(_cfg(OfficeRole.CEO)["agent"], worker_id="tuber_0",
                              playlist=OneReplay(), state_path=None)
    runner._playlist_call("off_hours", "tuber_0", Sink(), "2026-09-28", sent)
    [msg] = sent
    assert msg["type"] == "replay_request" and msg["to"] == "roundtable"
    assert msg["payload"]["cast"] == {"tuber_0": "tuber_0"}


def test_office_twitch_presence_maps_channels_to_seat_ids():
    """The dev-team TWITCH_CHANNEL_MAP names dev worker ids; office mode swaps
    in OFFICE_TWITCH_CHANNEL_MAP (the same channels -> seat ids)."""
    env = _office_compose()["services"]["twitch-presence"]["environment"]
    assert env == {"TWITCH_CHANNEL_MAP": "${OFFICE_TWITCH_CHANNEL_MAP:-}"}
    base = yaml.safe_load(BASE_COMPOSE.read_text(encoding="utf-8"))
    assert base["services"]["twitch-presence"]["environment"]["TWITCH_CHANNEL_MAP"] == \
        "${TWITCH_CHANNEL_MAP:-}"
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^OFFICE_TWITCH_CHANNEL_MAP=$", example, re.MULTILINE)
