"""
Tests for the roundtable layout preset (roundtable_stream_design.md v1.1 §5/§6).

Unlike tests/test_build_layout.py — which builds synthetic panels/layouts under
tmp_path to exercise the ENGINE — this module points the same engine at the REAL
config/panels + config/layouts and asserts the shipped roundtable preset
resolves the way the design requires. Only the runtime dir is a tmp_path; no
tmux and no pane process is ever started.

conftest.py adds app/ to sys.path, so `import build_layout` works directly.
"""
import pathlib

import pytest
import yaml

import build_layout


ROOT = pathlib.Path(__file__).resolve().parents[1]
PANELS_DIR = str(ROOT / "config" / "panels")
LAYOUTS_DIR = str(ROOT / "config" / "layouts")
TUBER_0 = str(ROOT / "config" / "workers" / "tuber_0.yaml")

SLOTS = [f"tuber_{i}" for i in range(7)]


@pytest.fixture
def built(tmp_path, monkeypatch):
    """Resolve the roundtable preset from tuber_0's real worker config."""
    monkeypatch.delenv("LAYOUT_PRESET", raising=False)
    runtime = tmp_path / "runtime"
    lines, panes = build_layout.build(TUBER_0, PANELS_DIR, LAYOUTS_DIR, str(runtime))
    return {"lines": lines, "panes": panes, "runtime": runtime}


def _by_use(panes, use):
    return [p for p in panes if p.get("use") == use]


# ── Preset selection ──────────────────────────────────────────────────────────
def test_tuber_0_selects_roundtable_preset():
    cfg = yaml.safe_load(pathlib.Path(TUBER_0).read_text(encoding="utf-8"))
    assert build_layout.select_preset(cfg) == "roundtable"
    assert cfg["agent"]["role"] == "roundtable"


# ── Shape ─────────────────────────────────────────────────────────────────────
def test_roundtable_resolves_to_nine_panes(built):
    """8 tiles (7 cast + 1 grid-balancing spare) + the show log. The htop
    "System" strip was removed in v1.2: it is operator telemetry, it read on
    the live broadcast as an extra character sitting in the grid, and every row
    it occupied came out of the tiles."""
    assert len(built["panes"]) == 9


def test_no_system_monitor_strip_on_the_broadcast(built):
    assert _by_use(built["panes"], "htop") == []


# ── The 20% / 80% broadcast geometry (v1.2) ──────────────────────────────────
def test_show_log_is_the_base_pane_so_it_spans_the_full_height(built):
    """The log column is only full-height because it is the BASE pane and every
    split is carved OUT of it (or out of the grid). If a later pane ever
    targets show_log with split: v, the column gets cut short on air."""
    assert built["panes"][0]["id"] == "show_log"
    vertical_splits_off_the_log = [
        p for p in built["panes"][1:]
        if p.get("target") == "show_log" and str(p.get("split", "v")).lower() != "h"
    ]
    assert vertical_splits_off_the_log == []


def test_character_grid_takes_eighty_percent_off_the_log(built):
    """Exactly one pane splits the log, horizontally, at 80% — that single
    split is what makes the log 20% of the width."""
    off_the_log = [p for p in built["panes"][1:] if p.get("target") == "show_log"]
    assert len(off_the_log) == 1
    grid = off_the_log[0]
    assert grid["id"] == "tile_tuber_0"
    assert grid["split"] == "h"
    assert grid["size"] == 80


def test_every_non_base_pane_is_carved_out_of_the_grid(built):
    """Everything except the grid's own split hangs off a tile, never off the
    log — the structural guarantee that the log keeps its full height."""
    for pane in built["panes"][2:]:
        assert pane["target"].startswith("tile_"), pane["id"]


def _cast_tiles(panes):
    """Tiles pinned to a CAST slot (tuber_0..tuber_6).

    The preset also carries one spare tile pinned to the uncast `tuber_7`, which
    exists purely to balance the 4x2 grid — without it tuber_6 absorbs the empty
    cell and renders double width. It is deliberately NOT a cast slot, so every
    roster assertion below filters it out.
    """
    return [t for t in _by_use(panes, "tile") if t.get("slot") in set(SLOTS)]


def test_seven_tiles_pinned_to_each_slot_exactly_once(built):
    tiles = _cast_tiles(built["panes"])
    assert len(tiles) == 7
    slots = [t["slot"] for t in tiles]
    assert sorted(slots) == sorted(SLOTS)        # none missing
    assert len(set(slots)) == 7                  # no duplicates


def test_spare_tile_balances_the_grid_without_being_cast(built):
    """The 8th tile must exist (grid balance) but never be a cast slot."""
    all_tiles = _by_use(built["panes"], "tile")
    spare = [t for t in all_tiles if t.get("slot") not in set(SLOTS)]
    assert len(all_tiles) == 8
    assert len(spare) == 1
    assert spare[0]["slot"] == "tuber_7"


def test_every_tile_has_a_distinct_id(built):
    """Distinct ids are what give each tile its own tmux pane index and its own
    runtime config file — a duplicate would silently collapse two tiles."""
    tiles = _by_use(built["panes"], "tile")
    ids = [t["id"] for t in tiles]
    assert len(set(ids)) == len(ids)
    assert sorted(ids) == sorted([f"tile_{slot}" for slot in SLOTS] + ["tile_spare"])


def test_all_pane_ids_are_distinct(built):
    ids = [p["id"] for p in built["panes"]]
    assert len(set(ids)) == len(ids) == 9


# ── Per-tile command substitution ─────────────────────────────────────────────
@pytest.mark.parametrize("slot", SLOTS)
def test_each_tile_command_carries_its_own_slot(built, slot):
    line = next(l for l in built["lines"]
                if "tile_pane.py" in l and f"--slot {slot} " in l)
    assert f"--slot {slot}" in line
    assert "{slot}" not in line
    assert f"--config {TUBER_0}" in line


def test_tuber_3_tile_command_is_pinned_to_tuber_3(built):
    tile = next(p for p in built["panes"] if p["id"] == "tile_tuber_3")
    assert tile["slot"] == "tuber_3"
    line = next(l for l in built["lines"] if "tile_tuber_3.yaml" in l or
                ("tile_pane.py" in l and "--slot tuber_3 " in l))
    assert "--slot tuber_3" in line


def test_each_tile_gets_exactly_one_send_keys(built):
    tile_lines = [l for l in built["lines"] if "tile_pane.py" in l]
    # 7 cast tiles + the spare grid-balancing tile (tuber_7).
    assert len(tile_lines) == 8
    for slot in SLOTS:
        assert sum(1 for l in tile_lines if f"--slot {slot} " in l) == 1
    assert sum(1 for l in tile_lines if "--slot tuber_7 " in l) == 1


# ── The director / show-log pane (§6.1) ───────────────────────────────────────
def test_exactly_one_replay_pane_is_the_show_log(built):
    replays = _by_use(built["panes"], "replay")
    assert len(replays) == 1
    assert replays[0]["id"] == "show_log"


# ── Breadth-only: no depth panes (§6) ─────────────────────────────────────────
@pytest.mark.parametrize("forbidden", ["filetree", "editor"])
def test_no_depth_panes_on_the_roundtable(built, forbidden):
    assert _by_use(built["panes"], forbidden) == []


# ── Engine contracts ──────────────────────────────────────────────────────────
def test_every_emitted_line_is_a_tmux_command(built):
    assert built["lines"], "engine emitted nothing"
    for line in built["lines"]:
        assert line.startswith("tmux "), f"non-tmux stdout line: {line!r}"


def test_one_runtime_yaml_written_per_pane(built):
    written = sorted(p.name for p in built["runtime"].glob("*.yaml"))
    expected = sorted(f"{p['id']}.yaml" for p in built["panes"])
    assert written == expected
    assert len(written) == 9


def test_tile_runtime_config_records_its_slot(built):
    for slot in SLOTS:
        data = yaml.safe_load(
            (built["runtime"] / f"tile_{slot}.yaml").read_text(encoding="utf-8"))
        assert data["type"] == "tile"
        assert data["slot"] == slot
        assert data["id"] == f"tile_{slot}"
