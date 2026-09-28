"""Tests for app/character/avatar.py and scripts/generate_office_avatars.py (OB-20)."""
import importlib.util
import json
import pathlib

import pytest
import yaml

from character.avatar import (
    MAX_ATTEMPTS,
    REQUIRED_KEYS,
    AvatarResponseError,
    ReplayLLMClient,
    build_user_prompt,
    map_appearance,
    map_appearance_result,
    parse_response,
)
from character_schema import ACCENT_COLORS, PARAM_DEFAULTS, SLIDER_DEFAULTS

REPO = pathlib.Path(__file__).resolve().parents[1]
PACK = REPO / "campaigns" / "ashiorid_office"

GOOD = {
    "head_width": 0.8, "head_taper": 0.1, "eye_size": 0.4, "eye_spacing": 0.6,
    "jaw_width": 0.9, "nose_length": 0.3, "ear_size": 0.5, "build": 0.85,
    "accent_color": "BLUE",
}

PROFILE = {
    "id": "ceo",
    "identity": {"full_name": "Graham Ellery", "age": 47, "pronouns": "he/him"},
    "appearance": "Square face with a heavy jaw.",
    "personality": {"traits": ["decisive"], "work_style": "delegates"},
    "backstory": {"believed": "SHOULD NOT APPEAR IN PROMPT"},
}


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "generate_office_avatars", REPO / "scripts" / "generate_office_avatars.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RaisingClient:
    def __init__(self):
        self.calls = 0

    def complete(self, system_prompt, messages):
        self.calls += 1
        raise RuntimeError("connection refused")


# ---- parse_response ---------------------------------------------------------

@pytest.mark.parametrize("text", [
    json.dumps(GOOD),
    "```json\n" + json.dumps(GOOD) + "\n```",
    "Here you go: " + json.dumps(GOOD) + " Hope that helps.",
])
def test_parse_response_accepts_json_variants_returns_params(text):
    assert parse_response(text) == GOOD


def test_parse_response_clamps_out_of_range_sliders():
    raw = dict(GOOD, head_width=1.7, build=-0.4, accent_color="red")
    params = parse_response(json.dumps(raw))
    assert params["head_width"] == 1.0
    assert params["build"] == 0.0
    assert params["accent_color"] == "RED"


def test_parse_response_drops_extra_keys():
    params = parse_response(json.dumps(dict(GOOD, hair="curly")))
    assert set(params) == set(PARAM_DEFAULTS)


def test_parse_response_rounds_to_two_places():
    assert parse_response(json.dumps(dict(GOOD, eye_size=0.123456)))["eye_size"] == 0.12


@pytest.mark.parametrize("text", [
    "no json here",
    "[1, 2, 3]",
    "{not valid json}",
    json.dumps({k: v for k, v in GOOD.items() if k != "jaw_width"}),
    json.dumps(dict(GOOD, eye_size="big")),
    json.dumps(dict(GOOD, eye_size=True)),
    json.dumps(dict(GOOD, eye_size=None)),
    json.dumps(dict(GOOD, accent_color="ORANGE")),
])
def test_parse_response_bad_output_raises(text):
    with pytest.raises(AvatarResponseError):
        parse_response(text)


def test_parse_response_non_text_raises():
    with pytest.raises(AvatarResponseError):
        parse_response(None)


# ---- map_appearance ---------------------------------------------------------

def test_map_appearance_good_first_reply_returns_params():
    client = ReplayLLMClient(GOOD)
    result = map_appearance_result(PROFILE, client)
    assert result.params == GOOD
    assert (result.source, result.attempts, client.calls) == ("llm", 1, 1)


def test_map_appearance_bad_then_good_retries_once():
    client = ReplayLLMClient(["sorry, I can't", GOOD])
    result = map_appearance_result(PROFILE, client)
    assert result.params == GOOD
    assert result.attempts == 2
    assert len(result.errors) == 1


def test_map_appearance_retry_feeds_error_back():
    seen = []

    class Recorder(ReplayLLMClient):
        def complete(self, system_prompt, messages):
            seen.append(list(messages))
            return super().complete(system_prompt, messages)

    map_appearance(PROFILE, Recorder(["garbage", GOOD]))
    assert len(seen[0]) == 1
    assert len(seen[1]) == 3
    assert seen[1][1] == {"role": "assistant", "content": "garbage"}
    assert "not usable" in seen[1][2]["content"]


def test_map_appearance_bad_twice_falls_back_to_defaults(caplog):
    client = ReplayLLMClient(["garbage", "still garbage", GOOD])
    with caplog.at_level("ERROR"):
        result = map_appearance_result(PROFILE, client)
    assert result.params == PARAM_DEFAULTS
    assert result.source == "default"
    assert client.calls == MAX_ATTEMPTS == 2
    assert "source=default" in caplog.text


def test_map_appearance_client_exception_falls_back_without_raising():
    client = RaisingClient()
    assert map_appearance(PROFILE, client) == PARAM_DEFAULTS
    assert client.calls == 2


def test_map_appearance_fallback_is_a_copy():
    params = map_appearance(PROFILE, ReplayLLMClient("x"))
    params["eye_size"] = 0.99
    assert PARAM_DEFAULTS["eye_size"] == SLIDER_DEFAULTS["eye_size"]


def test_build_user_prompt_includes_appearance_not_backstory():
    prompt = build_user_prompt(PROFILE)
    assert "Graham Ellery" in prompt and "Age: 47" in prompt
    assert "heavy jaw" in prompt and "- decisive" in prompt
    assert "SHOULD NOT APPEAR" not in prompt


def test_replay_client_serializes_dicts_and_repeats_last():
    client = ReplayLLMClient([GOOD])
    assert json.loads(client.complete("s", [])) == GOOD
    assert json.loads(client.complete("s", [])) == GOOD
    with pytest.raises(ValueError):
        ReplayLLMClient([])


# ---- script: cast YAML editing ----------------------------------------------

CAST_TEXT = """name: "Theo"
archetype: "builder"  # a comment that must survive
voice: tenor_high
avatar: null
system_prompt: |
  You are Theo.
  Second line.
seat: tuber_3
"""


def test_write_character_params_inserts_after_avatar_and_preserves_rest(tmp_path):
    script = _load_script()
    path = tmp_path / "engineer.yaml"
    path.write_text(CAST_TEXT, encoding="utf-8")
    script.write_character_params(path, GOOD, "params-file")
    text = path.read_text(encoding="utf-8")
    assert "# a comment that must survive" in text
    assert text.index("character_params:") == text.index("avatar: null") + len("avatar: null\n")
    data = yaml.safe_load(text)
    assert data["character_params"] == GOOD
    assert data["avatar"] is None
    assert data["system_prompt"] == "You are Theo.\nSecond line.\n"


def test_write_character_params_rerun_replaces_block(tmp_path):
    script = _load_script()
    path = tmp_path / "engineer.yaml"
    path.write_text(CAST_TEXT, encoding="utf-8")
    script.write_character_params(path, GOOD, "params-file")
    newer = dict(GOOD, eye_size=0.1, accent_color="RED")
    script.write_character_params(path, newer, "llm")
    text = path.read_text(encoding="utf-8")
    assert text.count("character_params:") == 1
    assert yaml.safe_load(text)["character_params"] == newer


def test_write_character_params_without_avatar_appends(tmp_path):
    script = _load_script()
    path = tmp_path / "x.yaml"
    path.write_text('name: "X"\n', encoding="utf-8")
    script.write_character_params(path, GOOD, "llm")
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == {"name": "X", "character_params": GOOD}


def test_roster_snippet_is_valid_yaml_matching_params():
    script = _load_script()
    rows = [{"id": "ceo", "name": "Graham Ellery", "seat": "tuber_0", "params": GOOD}]
    data = yaml.safe_load(script.roster_snippet(rows))
    assert data == {"roster": {"tuber_0": {"name": "Graham Ellery", "character_params": GOOD}}}


def test_main_params_file_no_render_writes_cast(tmp_path):
    script = _load_script()
    pack = tmp_path / "pack"
    (pack / "cast").mkdir(parents=True)
    (pack / "profiles").mkdir()
    (pack / "campaign.yaml").write_text("seats:\n  ceo: tuber_0\n  tester: tuber_4\n")
    for cid in ("ceo", "tester"):
        (pack / "cast" / f"{cid}.yaml").write_text(f'name: "{cid}"\navatar: null\n')
        (pack / "profiles" / f"{cid}.yaml").write_text(yaml.safe_dump(dict(PROFILE, id=cid)))
    replies = {"_comment": "ignored", "ceo": GOOD, "tester": "not json"}
    params_file = tmp_path / "p.json"
    params_file.write_text(json.dumps(replies))
    rc = script.main(["--pack", str(pack), "--params-file", str(params_file), "--no-render"])
    assert rc == 1  # tester fell back
    ceo = yaml.safe_load((pack / "cast" / "ceo.yaml").read_text())
    tester_text = (pack / "cast" / "tester.yaml").read_text()
    assert ceo["character_params"] == GOOD
    assert "(source: default)" in tester_text
    assert yaml.safe_load(tester_text)["character_params"] == PARAM_DEFAULTS


# ---- the real office pack ----------------------------------------------------

def _office_params():
    out = {}
    for path in sorted((PACK / "cast").glob("*.yaml")):
        out[path.stem] = yaml.safe_load(path.read_text(encoding="utf-8")).get("character_params")
    return out


def test_office_cast_params_are_complete_and_distinct():
    params = _office_params()
    assert len(params) == 8
    for cid, p in params.items():
        assert p is not None, cid
        assert set(p) == set(REQUIRED_KEYS)
        assert p["accent_color"] in ACCENT_COLORS
    colors = [p["accent_color"] for p in params.values()]
    assert len(set(colors)) == 8
    ids = list(params)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            dist = sum((params[a][k] - params[b][k]) ** 2 for k in SLIDER_DEFAULTS) ** 0.5
            assert dist > 0.3, (a, b, dist)


def test_office_params_file_matches_cast():
    replies = json.loads((PACK / "profiles" / "_avatar_params.json").read_text(encoding="utf-8"))
    params = _office_params()
    for cid, reply in replies.items():
        if cid.startswith("_"):
            continue
        assert parse_response(json.dumps(reply)) == params[cid]


def test_build_live_client_applies_overrides(tmp_path, monkeypatch):
    import llm_client

    script = _load_script()
    captured = {}
    monkeypatch.setattr(llm_client, "build_llm_client", lambda cfg: captured.setdefault("cfg", cfg))
    cfg = tmp_path / "w.yaml"
    cfg.write_text("llm:\n  provider: ollama\n  base_url: http://a:1\n  model: m1\n"
                   "  temperature: 0.7\n  max_tokens: 1024\n")
    script.build_live_client(cfg, "http://192.168.1.23:11434", "m2")
    llm = captured["cfg"]["llm"]
    assert llm["base_url"] == "http://192.168.1.23:11434"
    assert llm["model"] == "m2"
    assert llm["temperature"] == 0.3 and llm["max_tokens"] == 400
