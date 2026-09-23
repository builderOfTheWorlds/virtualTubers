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
import character_schema
import tile_avatar


ROOT = pathlib.Path(__file__).resolve().parents[1]
PANELS_DIR = str(ROOT / "config" / "panels")
LAYOUTS_DIR = str(ROOT / "config" / "layouts")
WORKERS_DIR = ROOT / "config" / "workers"
ROUNDTABLE_CONFIG = str(WORKERS_DIR / "roundtable.yaml")

SLOTS = [f"tuber_{i}" for i in range(8)]


@pytest.fixture
def built(tmp_path, monkeypatch):
    """Resolve the roundtable preset from the show host's real worker config."""
    monkeypatch.delenv("LAYOUT_PRESET", raising=False)
    runtime = tmp_path / "runtime"
    lines, panes = build_layout.build(ROUNDTABLE_CONFIG, PANELS_DIR, LAYOUTS_DIR, str(runtime))
    return {"lines": lines, "panes": panes, "runtime": runtime}


def _by_use(panes, use):
    return [p for p in panes if p.get("use") == use]


# ── Preset selection ──────────────────────────────────────────────────────────
def test_roundtable_worker_selects_roundtable_preset():
    cfg = yaml.safe_load(pathlib.Path(ROUNDTABLE_CONFIG).read_text(encoding="utf-8"))
    assert build_layout.select_preset(cfg) == "roundtable"
    assert cfg["agent"]["role"] == "roundtable"


# ── Exactly one director (the silent-failure guard) ─────────────────────────
def test_exactly_one_worker_config_is_the_roundtable_director():
    """app/replay_pane.py's _resolve_local_tiles directs when TILE_RELAY_DIR is
    set AND the worker's layout preset or agent role names the roundtable. The
    role side of that gate must hold for EXACTLY one config in config/workers/.

    Zero directors is the dangerous case because it fails SILENTLY: the tile
    panes still run, nothing ever polls the replay request file, and no error
    appears anywhere — the show simply never airs (config/layouts/
    roundtable.yaml's header documents the live incident). Two directors is the
    other half: both would race each other writing the same relay files.

    This is the guard for the GM/show split — config/workers/tuber_0.yaml is
    now an ordinary tuber_base character channel and must NOT carry the role.
    """
    directors = []
    for path in sorted(WORKERS_DIR.glob("*.yaml")):
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if ((cfg.get("agent") or {}).get("role")) == "roundtable":
            directors.append(path.name)
    assert directors == ["roundtable.yaml"], (
        f"expected exactly one roundtable director config, found {directors}")


# ── Shape (v1.3: pure tile grid — no show log, no system strip) ──────────────
def test_roundtable_resolves_to_eight_panes(built):
    """8 tiles, one per cast slot (tuber_0..tuber_7 — ROSTER_SIZE=8). v1.2's
    left-side show-log column was removed — it left the grid columns too
    narrow to read (the tuber_1/tuber_5 regression) — and the htop "System"
    strip was removed earlier still (operator telemetry that read as an
    extra character)."""
    assert len(built["panes"]) == 8


def test_no_system_monitor_strip_on_the_broadcast(built):
    assert _by_use(built["panes"], "htop") == []


def test_no_show_log_pane_on_the_broadcast(built):
    """The director's transcript is no longer given a pane on this channel —
    every pixel is a character tile."""
    assert _by_use(built["panes"], "replay") == []


def _cast_tiles(panes):
    """Tiles pinned to a CAST slot (tuber_0..tuber_7 — all 8 are cast now)."""
    return [t for t in _by_use(panes, "tile") if t.get("slot") in set(SLOTS)]


def test_eight_tiles_pinned_to_each_slot_exactly_once(built):
    tiles = _cast_tiles(built["panes"])
    assert len(tiles) == 8
    slots = [t["slot"] for t in tiles]
    assert sorted(slots) == sorted(SLOTS)        # none missing
    assert len(set(slots)) == 8                  # no duplicates


def test_every_tile_has_a_distinct_id(built):
    """Distinct ids are what give each tile its own tmux pane index and its own
    runtime config file — a duplicate would silently collapse two tiles."""
    tiles = _by_use(built["panes"], "tile")
    ids = [t["id"] for t in tiles]
    assert len(set(ids)) == len(ids)
    assert sorted(ids) == sorted([f"tile_{slot}" for slot in SLOTS])


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
    bottom_row = [by_id["tile_tuber_5"], by_id["tile_tuber_6"], by_id["tile_tuber_7"]]
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
    assert f"--config {ROUNDTABLE_CONFIG}" in line


def test_tuber_3_tile_command_is_pinned_to_tuber_3(built):
    tile = next(p for p in built["panes"] if p["id"] == "tile_tuber_3")
    assert tile["slot"] == "tuber_3"
    line = next(l for l in built["lines"] if "tile_tuber_3.yaml" in l or
                ("tile_pane.py" in l and "--slot tuber_3 " in l))
    assert "--slot tuber_3" in line


def test_each_tile_gets_exactly_one_send_keys(built):
    tile_lines = [l for l in built["lines"] if "tile_pane.py" in l]
    # 8 cast tiles (tuber_0..tuber_7).
    assert len(tile_lines) == 8
    for slot in SLOTS:
        assert sum(1 for l in tile_lines if f"--slot {slot} " in l) == 1


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


# ── Character names + colors on the real shipped roster (v1.4) ───────────────
def test_every_tile_title_matches_the_roundtable_configs_real_roster(built):
    """The tiles must display the SAME roster the show host's worker config
    carries — this locks the two together so an operator adding/renaming a
    character in tuber_0.yaml's `roster:` sees it reflected here without any
    other file changing."""
    cfg = yaml.safe_load(pathlib.Path(ROUNDTABLE_CONFIG).read_text(encoding="utf-8"))
    roster = cfg.get("roster") or {}
    by_id = {p["id"]: p for p in built["panes"]}

    for slot in SLOTS:
        tile = by_id[f"tile_{slot}"]
        if slot in roster:
            # An entry is either a bare title string or a mapping carrying the
            # slot's 3D preset alongside `name` — compare against the name.
            entry = roster[slot]
            expected = entry["name"] if isinstance(entry, dict) else entry
            assert tile["title"] == expected
        elif slot == "tuber_0":
            assert tile["title"] == "Game Master"
        else:
            assert tile["title"] == "Offline"


def test_every_cast_tile_shares_one_active_color(built):
    """Every ACTIVE character (cast or GM) renders in the same light-blue —
    the design ask to normalize colors instead of the old per-slot rainbow."""
    tiles = _cast_tiles(built["panes"])
    cfg = yaml.safe_load(pathlib.Path(ROUNDTABLE_CONFIG).read_text(encoding="utf-8"))
    roster = cfg.get("roster") or {}
    active_colors = {t["border_color"] for t in tiles if t["slot"] == "tuber_0" or t["slot"] in roster}
    assert active_colors == {"colour117"}


def test_uncast_tiles_render_grey_not_the_active_color(built):
    """A slot with no character assigned (tuber_4 today) must read visibly
    different from the active cast — grey, not the light-blue used for
    everyone actually on air."""
    cfg = yaml.safe_load(pathlib.Path(ROUNDTABLE_CONFIG).read_text(encoding="utf-8"))
    roster = cfg.get("roster") or {}
    by_id = {p["id"]: p for p in built["panes"]}
    uncast_cast_slots = [s for s in SLOTS if s != "tuber_0" and s not in roster]
    for slot in uncast_cast_slots:
        assert by_id[f"tile_{slot}"]["border_color"] == "colour240"


# ── Status bar label (v1.4) ────────────────────────────────────────────────────
def test_roundtable_sets_a_static_status_bar_label(built):
    """The tmux status bar's default '[worker] 0:python3*' segment is
    meaningless to a viewer — the roundtable preset overrides it, and blanks
    the window-list segment that would otherwise concatenate straight onto
    the label with no separator."""
    joined = "\n".join(built["lines"])
    assert "tmux set -t worker status-left 'virtualTubers_roundtable'" in joined
    assert "tmux set -t worker window-status-format ''" in joined
    # Cosmetic only: the underlying session keeps its real name everywhere.
    assert built["lines"][0] == "tmux new-session -d -s worker -x 240 -y 67"


# ── Roster casting: the shipped config must actually wire up 3D heads ─────────
# A roster whose entries are plain strings is valid YAML, resolves correct tile
# titles, and renders a perfectly normal-looking show — with every tile silently
# on the 3-row ASCII fallback, because nothing carries a character_params. That
# failure is invisible to every other test here, so assert the real shipped
# config casts each slot it claims to cast.
def test_every_cast_slot_in_the_shipped_roster_has_a_3d_preset():
    cfg = yaml.safe_load(pathlib.Path(ROUNDTABLE_CONFIG).read_text(encoding="utf-8"))
    roster = cfg.get("roster") or {}
    assert roster, "roundtable.yaml must cast its slots"
    for slot, entry in roster.items():
        preset = tile_avatar.resolve_slot_character_params(cfg, slot)
        assert preset, (
            f"roster slot {slot} ({entry!r}) resolves no character_params, so its "
            "tile would fall back to the ASCII face on air"
        )
        assert preset in character_schema.PRESETS, (
            f"roster slot {slot} names preset {preset!r}, absent from PRESETS"
        )


def test_uncast_slots_resolve_no_preset():
    """The complement: an uncast slot must NOT get a pixel window."""
    cfg = yaml.safe_load(pathlib.Path(ROUNDTABLE_CONFIG).read_text(encoding="utf-8"))
    roster = cfg.get("roster") or {}
    for slot in SLOTS:
        if slot not in roster:
            assert tile_avatar.resolve_slot_character_params(cfg, slot) is None
