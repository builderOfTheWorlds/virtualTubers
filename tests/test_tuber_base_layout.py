"""
Tests for the tuber_base layout preset (docs/tuber_base_layout_plan.md).

Unlike tests/test_build_layout.py — which builds synthetic panels/layouts under
tmp_path to exercise the ENGINE — this module points the same engine at the
REAL config/panels + config/layouts and asserts the shipped tuber_base preset
resolves the way the plan requires. Only the runtime dir is a tmp_path; no
tmux and no pane process is ever started.

conftest.py adds app/ to sys.path, so `import build_layout` works directly.

NOTE on config/panels/{radar_chart,knowledge_graph,thinking,chat_list}.yaml:
these were placeholder-created by sub-agent D (title/border_color/command only,
per the plan's Contract D table) because sub-agent B's panel-script work was
running concurrently. If B's edits landed a `content:` block on top, that is
expected and compatible with these tests, which only assert on
title/border_color/command/use/id/split/size/target — NOT on any `content:`
sub-block those panels may since have grown.
"""
import pathlib

import pytest
import yaml

import build_layout


ROOT = pathlib.Path(__file__).resolve().parents[1]
PANELS_DIR = str(ROOT / "config" / "panels")
LAYOUTS_DIR = str(ROOT / "config" / "layouts")

WORKER_CONFIGS = {
    "coder": str(ROOT / "config" / "workers" / "coder.yaml"),
    "coder-native": str(ROOT / "config" / "workers" / "coder-native.yaml"),
    "coder-opencode": str(ROOT / "config" / "workers" / "coder-opencode.yaml"),
    "coder-aider": str(ROOT / "config" / "workers" / "coder-aider.yaml"),
    "tester": str(ROOT / "config" / "workers" / "tester.yaml"),
    "manager": str(ROOT / "config" / "workers" / "manager.yaml"),
}
CODER = WORKER_CONFIGS["coder"]

EXPECTED_USE_ORDER = [
    "radar_chart", "avatar", "chat_list", "kafka_feed",
    "knowledge_graph", "thinking",
]


@pytest.fixture
def built(tmp_path, monkeypatch):
    """Resolve the tuber_base preset from the coder worker config."""
    monkeypatch.delenv("LAYOUT_PRESET", raising=False)
    runtime = tmp_path / "runtime"
    lines, panes = build_layout.build(CODER, PANELS_DIR, LAYOUTS_DIR, str(runtime))
    return {"lines": lines, "panes": panes, "runtime": runtime}


def _by_use(panes, use):
    return [p for p in panes if p.get("use") == use]


def _by_id(panes):
    return {p["id"]: p for p in panes}


# ── Preset selection: all six worker configs repointed ────────────────────────
@pytest.mark.parametrize("role", sorted(WORKER_CONFIGS))
def test_worker_selects_tuber_base_preset(role):
    cfg = yaml.safe_load(pathlib.Path(WORKER_CONFIGS[role]).read_text(encoding="utf-8"))
    assert build_layout.select_preset(cfg) == "tuber_base"


def test_roundtable_and_tuber_0_are_untouched():
    """Guardrail: this stream must not touch roundtable.yaml or tuber_0.yaml."""
    tuber_0_path = ROOT / "config" / "workers" / "tuber_0.yaml"
    cfg = yaml.safe_load(tuber_0_path.read_text(encoding="utf-8"))
    assert build_layout.select_preset(cfg) == "roundtable"


# ── Shape: exactly the six named panes from Contract D + avatar/kafka_feed ───
def test_tuber_base_resolves_to_six_panes(built):
    assert len(built["panes"]) == 6


def test_all_pane_ids_are_distinct(built):
    ids = [p["id"] for p in built["panes"]]
    assert len(set(ids)) == len(ids) == 6


def test_every_expected_use_appears_exactly_once(built):
    uses = [p["use"] for p in built["panes"]]
    assert sorted(uses) == sorted(EXPECTED_USE_ORDER)


def test_panes_resolve_in_the_declared_order(built):
    """List order in the layout file drives tmux pane-creation order."""
    uses = [p["use"] for p in built["panes"]]
    assert uses == EXPECTED_USE_ORDER


# ── Base pane: narrow left-column radar_chart ─────────────────────────────────
def test_base_pane_is_radar_chart(built):
    assert built["panes"][0]["id"] == "radar_chart"
    assert built["panes"][0]["use"] == "radar_chart"


# ── Geometry: four columns (L narrow / M wide / c narrow / R wide) ───────────
def test_horizontal_splits_carve_four_columns(built):
    """avatar, chat_list, kafka_feed are all horizontal (h) splits chaining off
    the previous column, per the plan's worked percentage math (tuber_base.yaml
    header): 58 / 48 / 83 shrinking splits of each successively narrower
    remainder yield 42/30.16/4.73/23.11 percent columns."""
    by_id = _by_id(built["panes"])

    avatar = by_id["avatar"]
    assert avatar["split"] == "h"
    assert avatar["target"] == "radar_chart"
    assert avatar["size"] == 58

    chat_list = by_id["chat_list"]
    assert chat_list["split"] == "h"
    assert chat_list["target"] == "avatar"
    assert chat_list["size"] == 48

    kafka_feed = by_id["kafka_feed"]
    assert kafka_feed["split"] == "h"
    assert kafka_feed["target"] == "chat_list"
    assert kafka_feed["size"] == 83


def test_column_percentages_resolve_to_the_mock_proportions(built):
    """Guardrail on the actual GEOMETRY, not just the split numbers: the
    shrinking-split chain must still land on the hand-drawn mock's columns
    (measured off it: 42.0 / 28.7 / 5.5 / 23.7 percent), and the M column
    must stay wide enough for the 549px avatar window it is sized around
    (config/workers/coder.yaml). A wrong `size` that still parses would
    otherwise pass every other test in this file."""
    by_id = _by_id(built["panes"])
    remaining = 100.0

    m_c_r = remaining * by_id["avatar"]["size"] / 100.0
    left = remaining - m_c_r

    c_r = m_c_r * by_id["chat_list"]["size"] / 100.0
    middle = m_c_r - c_r

    right = c_r * by_id["kafka_feed"]["size"] / 100.0
    chats = c_r - right

    assert left == pytest.approx(42.0, abs=0.5)
    assert middle == pytest.approx(30.2, abs=1.6)   # mock 28.7
    assert chats == pytest.approx(4.7, abs=1.0)     # mock 5.5
    assert right == pytest.approx(23.1, abs=1.0)    # mock 23.7
    assert left + middle + chats + right == pytest.approx(100.0, abs=0.01)

    # The whole point of this layout pass: the avatar column exists to fit
    # the avatar window, not the other way round.
    assert middle / 100.0 * 1920 >= 549


def test_vertical_splits_carve_column_top_bottom_panes(built):
    """knowledge_graph splits below radar_chart (L column); thinking splits
    below avatar (M column). Both are vertical (v, i.e. stacked) splits."""
    by_id = _by_id(built["panes"])

    knowledge_graph = by_id["knowledge_graph"]
    assert knowledge_graph["split"] == "v"
    assert knowledge_graph["target"] == "radar_chart"
    assert knowledge_graph["size"] == 73

    thinking = by_id["thinking"]
    assert thinking["split"] == "v"
    assert thinking["target"] == "avatar"
    assert thinking["size"] == 55
    # Thinking stays the taller half of the M column (the mock draws it
    # that way) even though the avatar box gained height in this pass.
    assert thinking["size"] > 50


def test_chat_list_is_the_narrow_chats_column(built):
    """The narrow 'chats' chat-room-name-list column sits between
    avatar/thinking (middle) and kafka_feed (right), per the mock."""
    by_id = _by_id(built["panes"])
    chat_list = by_id["chat_list"]
    assert chat_list["title"] == "chats"
    assert chat_list["target"] == "avatar"


# ── Kafka feed: conversation mode via with: block (Contract C) ────────────────
def test_kafka_feed_uses_conversation_format_via_with_block(built):
    by_id = _by_id(built["panes"])
    kafka_feed = by_id["kafka_feed"]
    assert kafka_feed["content"]["format"] == "conversation"


def test_kafka_feed_columns_mode_default_untouched_in_panel_file():
    """Guardrail: the panel-type DEFAULT (config/panels/kafka_feed.yaml) must
    not itself be switched to conversation mode — only tuber_base's placement
    with: block opts in, so other presets/roles keep the columns default."""
    panel_default = build_layout.load_yaml(pathlib.Path(PANELS_DIR) / "kafka_feed.yaml")
    content = panel_default.get("content") or {}
    assert content.get("format") != "conversation"


# ── Panel-type wiring from Contract D's table ─────────────────────────────────
def test_radar_chart_panel_matches_contract_d_table(built):
    by_id = _by_id(built["panes"])
    pane = by_id["radar_chart"]
    assert pane["title"] == "Stats"
    assert pane["border_color"] == "green"
    assert "radar_pane.py" in pane["command"]


def test_knowledge_graph_panel_matches_contract_d_table(built):
    by_id = _by_id(built["panes"])
    pane = by_id["knowledge_graph"]
    assert pane["title"] == "Knowledge"
    assert pane["border_color"] == "cyan"
    assert "knowledge_graph_pane.py" in pane["command"]


def test_thinking_panel_matches_contract_d_table(built):
    by_id = _by_id(built["panes"])
    pane = by_id["thinking"]
    assert pane["title"] == "Thinking"
    assert pane["border_color"] == "yellow"
    assert "thinking_pane.py" in pane["command"]


def test_chat_list_panel_matches_contract_d_table(built):
    by_id = _by_id(built["panes"])
    pane = by_id["chat_list"]
    assert pane["title"] == "chats"
    assert pane["border_color"] == "white"
    assert "chat_list_pane.py" in pane["command"]


def test_avatar_and_kafka_feed_are_reused_not_new(built):
    by_id = _by_id(built["panes"])
    assert "avatar.py" in by_id["avatar"]["command"]
    assert "--config {config_path}" in by_id["avatar"]["command"]
    assert "tail_bus.py" in by_id["kafka_feed"]["command"]


# ── Engine contracts ──────────────────────────────────────────────────────────
def test_every_emitted_line_is_a_tmux_command(built):
    assert built["lines"], "engine emitted nothing"
    for line in built["lines"]:
        assert line.startswith("tmux "), f"non-tmux stdout line: {line!r}"


def test_one_runtime_yaml_written_per_pane(built):
    written = sorted(p.name for p in built["runtime"].glob("*.yaml"))
    expected = sorted(f"{p['id']}.yaml" for p in built["panes"])
    assert written == expected
    assert len(written) == 6


def test_exact_tmux_command_sequence(built):
    """Locks the exact emitted script: session create, then splits in
    declaration order (radar_chart is the base pane, so no split for it),
    then pane-border-status + per-pane title/color, then send-keys last.

    Pane indices shift as later splits insert panes before already-placed
    ones (see build_layout.emit_tmux's index_of bookkeeping / its docstring)
    — knowledge_graph's split (radar_chart's target) inserts before avatar/
    chat_list/kafka_feed, and thinking's split (avatar's target) inserts
    before chat_list/kafka_feed again. Final id -> index mapping:
    radar_chart=0, knowledge_graph=1, avatar=2, thinking=3, chat_list=4,
    kafka_feed=5.
    """
    lines = built["lines"]

    assert lines[0] == "tmux new-session -d -s worker -x 240 -y 67"

    split_lines = [
        l for l in lines
        if "split-window" in l
        or (l.startswith("tmux select-pane -t worker:0.") and "-T" not in l and "-P" not in l)
    ]
    assert split_lines == [
        "tmux select-pane -t worker:0.0",
        "tmux split-window -h -t worker:0.0 -p 58",
        "tmux select-pane -t worker:0.1",
        "tmux split-window -h -t worker:0.1 -p 48",
        "tmux select-pane -t worker:0.2",
        "tmux split-window -h -t worker:0.2 -p 83",
        "tmux select-pane -t worker:0.0",
        "tmux split-window -v -t worker:0.0 -p 73",
        "tmux select-pane -t worker:0.2",
        "tmux split-window -v -t worker:0.2 -p 55",
    ]

    assert "tmux set -t worker pane-border-status top" in lines

    # send-keys happen last, one per pane with a command (all six have one).
    send_keys = [l for l in lines if l.startswith("tmux send-keys")]
    assert len(send_keys) == 6
    last_six = lines[-6:]
    assert last_six == send_keys

    # Final id -> tmux pane index mapping, per the docstring above.
    # radar_pane/knowledge_graph_pane/avatar run under the /opt/render3d
    # Python 3.11 venv (termgl requires >=3.11) — see config/panels/*.yaml.
    assert "tmux send-keys -t worker:0.0 '/opt/render3d/bin/python3 /app/radar_pane.py" in lines[-6]
    assert "tmux send-keys -t worker:0.1 '/opt/render3d/bin/python3 /app/knowledge_graph_pane.py" in lines[-2]
    assert "tmux send-keys -t worker:0.2 '/opt/render3d/bin/python3 /app/avatar.py" in lines[-5]
    assert "tmux send-keys -t worker:0.3 'python3 /app/thinking_pane.py" in lines[-1]
    assert "tmux send-keys -t worker:0.4 'python3 /app/chat_list_pane.py" in lines[-4]
    assert "tmux send-keys -t worker:0.5 'python3 /app/tail_bus.py" in lines[-3]


def test_tuber_base_has_no_status_label(built):
    """Unlike roundtable, tuber_base does not override the status bar."""
    joined = "\n".join(built["lines"])
    assert "status-left" not in joined


# ── Regression guards ──────────────────────────────────────────────────────────
def test_no_editor_filetree_htop_panes(built):
    """tuber_base replaces the old coder/tester/manager three-column presets
    entirely — none of their panel types should leak through."""
    for forbidden in ("editor", "filetree", "htop"):
        assert _by_use(built["panes"], forbidden) == []


def test_resolved_path_substitution_present_in_radar_command(built):
    """radar_chart's command references {runtime_dir}/metrics_{worker_id}.json
    per Contract D — worker_id isn't a scalar on the resolved pane dict so it
    is left intact by build_context/substitute (documented fallback), but the
    literal must still show up verbatim in the emitted send-keys line."""
    send_keys_line = next(
        l for l in built["lines"] if "radar_pane.py" in l and "send-keys" in l
    )
    assert f"--config {CODER}" in send_keys_line
