"""P3.7: scripts/build_table_configs.py writes config/workers/table/<slug>.yaml.

Each table worker config = its SLOT config (voice, avatar, layout, stream stay
the slot's) + agent.role/name/system_prompt from the pack cast + agent.table +
llm from config/table/<pack>.yaml. The committed files must equal a fresh
build (drift test), like tests/test_office_configs.py does for the office.
"""
import importlib.util
import pathlib

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "build_table_configs.py"
TABLE_CFG = REPO / "config" / "table" / "ashiorid.yaml"
OUT = REPO / "config" / "workers" / "table"


def _mod():
    spec = importlib.util.spec_from_file_location("build_table_configs", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _built(tmp_path):
    mod = _mod()
    written = mod.build(TABLE_CFG, tmp_path)
    return mod, {p.stem: yaml.safe_load(p.read_text()) for p in written}


def test_one_config_per_seat(tmp_path):
    _, cfgs = _built(tmp_path)
    assert set(cfgs) == {"gm", "chadwick", "Leena", "Vigil", "sodacan_bob", "roundtable"}


def test_gm_config_owns_the_table(tmp_path):
    _, cfgs = _built(tmp_path)
    agent = cfgs["gm"]["agent"]
    assert agent["role"] == "table_gm" and agent["tick_rate_ms"] == 500
    t = agent["table"]
    assert t["seats"] == ["tuber_1"]                       # slice: active players only
    assert t["gm_seat"] == "tuber_0" and t["gm_slug"] == "gm"
    assert t["seat_names"]["tuber_1"] == "Chadwick" and t["seat_names"]["gm"] == "GM"
    assert t["seat_slugs"]["tuber_1"] == "chadwick"
    # contracts name participants by cast id in any case (P3.2): every variant maps
    assert t["seat_of"]["chadwick"] == "tuber_1" and t["seat_of"]["leena"] == "tuber_2"
    for key in ("direct", "adjudicate", "overrule", "contracts_fixture", "gm_blocks_order"):
        assert key in t, key


def test_seat_config_is_a_table_seat_with_its_slug(tmp_path):
    _, cfgs = _built(tmp_path)
    agent = cfgs["chadwick"]["agent"]
    assert agent["role"] == "table_seat" and agent["name"] == "Chadwick"
    assert "half-orc paladin" in agent["system_prompt"]
    t = agent["table"]
    assert t["character_slug"] == "chadwick" and t["pack_dir"] == "/data/campaigns/ashiorid"
    assert t["think"]["reasoning_budget"] == 256 and t["speak"]["max_tokens"] == 160
    assert "seats" not in t                                 # seats never get the table plan


def test_slot_identity_kept_and_llm_points_at_vllm(tmp_path):
    _, cfgs = _built(tmp_path)
    base = yaml.safe_load((REPO / "config" / "workers" / "manager.yaml").read_text())
    for key in ("voice", "avatar", "stream", "layout"):
        if key in base:
            assert cfgs["chadwick"][key] == base[key], key
    assert cfgs["chadwick"]["llm"]["provider"] == "vllm"
    assert cfgs["chadwick"]["llm"]["api_key_env"] == "VLLM_API_KEY"
    assert "api_key" not in cfgs["chadwick"]["llm"]


def test_committed_configs_match_a_fresh_build(tmp_path):
    mod, _ = _built(tmp_path)
    for fresh in sorted(tmp_path.glob("*.yaml")):
        committed = OUT / fresh.name
        assert committed.is_file(), f"run scripts/build_table_configs.py ({fresh.name} missing)"
        assert committed.read_text() == fresh.read_text(), f"{fresh.name} drifted; rebuild"



def test_roundtable_has_live_feed_from_the_arbiter_and_cast_voices(tmp_path):
    _, cfgs = _built(tmp_path)
    rt = cfgs["roundtable"]
    assert rt["agent"]["role"] == "roundtable"
    assert rt["agent"]["live"]["enabled"] is True and rt["agent"]["live"]["table_arbiter"] == "tuber_0"
    assert rt["voice"]["speakers"]["tuber_1"]["model_path"].endswith("ryan-high.onnx")
    assert rt["voice"]["speaker_names"]["tuber_1"] == "Chadwick"
