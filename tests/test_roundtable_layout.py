"""
Tests for the roundtable layout preset (roundtable_stream_design.md v1.1 §5/§6,
updated v1.3 — see config/layouts/roundtable.yaml header for the "why").

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


# ── Shape (v1.3: pure tile grid — no show log, no system strip) ──────────────
def test_roundtable_resolves_to_eight_panes(built):
    """8 tiles only: 7 cast + 1 grid-balancing spare. v1.2's left-side show-log
    column was removed — it left the grid columns too narrow to read (the
    tuber_1/tuber_5 regression) — and the htop "System" strip was removed
    earlier still (operator telemetry that read as an extra character)."""
    assert len(built["panes"]) == 8


def test_no_system_monitor_strip_on_the_broadcast(built):
    assert _by_use(built["panes"], "htop") == []


def test_no_show_log_pane_on_the_broadcast(built):
    """The director's transcript is no longer given a pane on this channel —
    every pixel is a character tile."""
    assert _by_use(built["panes"], "replay") == []


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
    assert len(set(ids)) == len(ids) == 8


# ── Even 4x2 grid geometry (v1.3 — the tuber_1/tuber_5 narrowness fix) ───────
def test_base_pane_is_a_tile_not_a_sidebar(built):
    """The base pane must be a character tile now — a base pane that is
    anything else (a log, a status strip) eats into the grid's own even-split
    math (see the layout file's header for the 75/66/50 derivation)."""
    assert built["panes"][0]["id"] == "tile_tuber_0"
    assert built["panes"][0]["use"] == "tile"


def test_rows_are_split_exactly_in_half(built):
    """The only vertical (stacked) split is the one carving the bottom row off
    the base — and it must be a flat 50, otherwise the two rows of tiles are
    uneven heights."""
    vertical_splits = [p for p in built["panes"][1:]
                       if str(p.get("split", "v")).lower() != "h"]
    assert len(vertical_splits) == 1
    assert vertical_splits[0]["id"] == "tile_tuber_4"
    assert vertical_splits[0]["size"] == 50


def test_each_row_is_cut_into_four_equal_columns(built):
    """tmux split-window -p sizes the NEW pane as a percentage of its target's
    CURRENT size, so four even columns need shrinking splits (75, 66, 50), not
    a flat 25 each time — see the layout file header for the derivation. This
    is the actual fix for tiles rendering too narrow to read: the columns were
    always even, but of a base that used to be narrowed by a sidebar."""
    by_id = {p["id"]: p for p in built["panes"]}
    top_row = [by_id["tile_tuber_1"], by_id["tile_tuber_2"], by_id["tile_tuber_3"]]
    bottom_row = [by_id["tile_tuber_5"], by_id["tile_tuber_6"], by_id["tile_spare"]]
    for row, targets in (
        (top_row, ["tile_tuber_0", "tile_tuber_1", "tile_tuber_2"]),
        (bottom_row, ["tile_tuber_4", "tile_tuber_5", "tile_tuber_6"]),
    ):
        sizes = [p["size"] for p in row]
        assert sizes == [75, 66, 50]
        for pane, target in zip(row, targets):
            assert pane["split"] == "h"
            assert pane["target"] == target


def test_no_pane_targets_a_removed_show_log(built):
    """Regression guard: nothing in the shipped preset may reference the old
    show_log pane id — that pane no longer exists."""
    for pane in built["panes"]:
        assert pane.get("target") != "show_log"


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
    assert len(written) == 8


def test_tile_runtime_config_records_its_slot(built):
    for slot in SLOTS:
        data = yaml.safe_load(
            (built["runtime"] / f"tile_{slot}.yaml").read_text(encoding="utf-8"))
        assert data["type"] == "tile"
        assert data["slot"] == slot
        assert data["id"] == f"tile_{slot}"
