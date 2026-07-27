"""Tests for app/primitives.py — the campaign-platform rendering engine.

The contract under test (CONTRACT.md §2, docs/primitives.md): a two-layer
config merge that mirrors build_layout.deep_merge exactly, `extends`
resolution with positional behavior merging and cycle detection, screen-time
estimation that must match today's (pre-campaign-platform)
replay.estimate_event_seconds formulas to the microsecond, and five behavior
implementations (type/print/diff/image/pause) that reproduce the coder
show's exact glyphs/colors/rates when driven by config/campaigns/coder/
primitives.yaml.

Fakes are hand-written and duck-typed (no unittest.mock, matching this
repo's app/ test convention) — FakePacer/FakePalette stand in for
replay.Pacer/replay.Palette without importing replay.py at all, so these
tests stay independent of the parallel replay.py rework (CONTRACT.md §6).
"""
import io
import logging
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import primitives  # noqa: E402
from primitives import (  # noqa: E402
    BEHAVIORS, PrimitiveError, RenderContext,
    estimate_entry_seconds, estimate_scene_seconds, load_primitives,
    perform_entry, render_summary, resolve_recipe,
)

ROOT = Path(__file__).resolve().parents[1]
REAL_BASE_PATH = ROOT / "config" / "primitives.yaml"
REAL_CAMPAIGNS_DIR = ROOT / "config" / "campaigns"


# ── fakes ──────────────────────────────────────────────────────────────────

class FakePacer:
    """Duck-typed stand-in for replay.Pacer: records every sleep, and
    type_out writes the whole string at once (test speed) while still
    recording the requested cps for assertions."""

    def __init__(self):
        self.sleeps = []
        self.typed = []

    def check_stop(self):
        pass

    def sleep(self, seconds):
        self.sleeps.append(seconds)

    def type_out(self, write, text, cps):
        self.typed.append((text, cps))
        write(text)


class FakePalette:
    """Duck-typed stand-in for replay.Palette — distinct short tokens
    instead of real ANSI escapes so assertions stay readable."""

    def __init__(self):
        self.reset = "<r>"
        self.dim = "<dim>"
        self.bold = "<b>"
        self.cyan = "<cyan>"
        self.green = "<green>"
        self.yellow = "<yellow>"
        self.red = "<red>"
        self.magenta = "<magenta>"


def make_ctx(max_output_lines=24):
    """A RenderContext wired to an in-memory multi-sink writer and an
    avatar-call recorder, both inspectable after perform_entry runs."""
    sinks = {"theater": io.StringIO(), "notes": io.StringIO()}
    avatar_calls = []

    def write(text, *, target=None):
        sinks.setdefault(target or "theater", io.StringIO()).write(text)

    def avatar(expression, action="", bubble=None):
        avatar_calls.append((expression, action, bubble))

    ctx = RenderContext(write, FakePacer(), FakePalette(), avatar, max_output_lines=max_output_lines)
    return ctx, sinks, avatar_calls


# ── fixtures: an isolated base+campaign layer pair under tmp_path ──────────

@pytest.fixture
def layers(tmp_path):
    base = tmp_path / "primitives.yaml"
    campaigns = tmp_path / "campaigns"
    campaigns.mkdir()
    base.write_text(yaml.safe_dump({
        "type_text": {
            "target": "theater",
            "behaviors": [
                {"behavior": "type", "field": "payload.text", "rate_cps": 30, "style": {"fg": "white"}},
            ],
        },
        "beat": {"behaviors": [{"behavior": "pause", "seconds": 1.5}]},
    }), encoding="utf-8")
    return {"base": base, "campaigns": campaigns}


def write_campaign(campaigns_dir, name, data):
    campaign_dir = campaigns_dir / name
    campaign_dir.mkdir(parents=True, exist_ok=True)
    (campaign_dir / "primitives.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


# ── BEHAVIORS constant ──────────────────────────────────────────────────────

def test_behaviors_constant_is_exactly_the_five_frozen_kinds():
    """BEHAVIORS is part of the frozen API (CONTRACT.md §2) — a regression
    here means someone silently added a sixth behavior in code instead of
    reaching for a config-only primitive, which the design doc explicitly
    asks contributors to avoid."""
    assert BEHAVIORS == {"type", "print", "diff", "image", "pause"}


# ── two-layer merge ──────────────────────────────────────────────────────────

def test_campaign_can_add_a_new_primitive_without_touching_shared_ones(layers):
    write_campaign(layers["campaigns"], "demo", {
        "roll_dice": {"target": "notes", "behaviors": [{"behavior": "pause", "seconds": 2.0}]},
    })
    table = load_primitives("demo", base_path=layers["base"], campaigns_dir=layers["campaigns"])
    assert table["type_text"]["behaviors"][0]["rate_cps"] == 30  # shared primitive untouched
    assert table["roll_dice"]["target"] == "notes"


def test_campaign_missing_file_is_fine_shared_only(layers):
    """No config/campaigns/<campaign>/primitives.yaml at all must not raise
    — a brand-new campaign with only shared primitives is a valid state."""
    table = load_primitives("nobody-has-made-this-yet",
                            base_path=layers["base"], campaigns_dir=layers["campaigns"])
    assert set(table) == {"type_text", "beat"}


def test_missing_base_file_raises_primitive_error(tmp_path):
    """Unlike the campaign layer, the shared base has no sensible default —
    a missing config/primitives.yaml is a real deployment error."""
    with pytest.raises(PrimitiveError):
        load_primitives("coder", base_path=tmp_path / "nope.yaml", campaigns_dir=tmp_path)


def test_campaign_extends_overrides_one_field_and_inherits_the_rest(layers):
    """The design doc's stated use case: 'override a single field of a
    shared one (rate_cps for a faster-talking campaign)' via extends,
    without redefining the whole recipe."""
    write_campaign(layers["campaigns"], "demo", {
        "loud_text": {"extends": "type_text", "behaviors": [{"rate_cps": 90}]},
    })
    table = load_primitives("demo", base_path=layers["base"], campaigns_dir=layers["campaigns"])
    loud = table["loud_text"]
    assert loud["target"] == "theater"                       # inherited
    assert loud["behaviors"][0]["behavior"] == "type"          # inherited
    assert loud["behaviors"][0]["field"] == "payload.text"     # inherited
    assert loud["behaviors"][0]["rate_cps"] == 90               # overridden
    assert loud["behaviors"][0]["style"] == {"fg": "white"}    # inherited (untouched dict)


def test_campaign_redefining_shared_primitive_without_extends_logs_warning(layers, caplog):
    """'A campaign redefining a shared primitive outright is allowed but
    logged — silent overrides are confusing' (design doc). Declaring the
    same top-level key without `extends` must warn AND still take effect."""
    write_campaign(layers["campaigns"], "demo", {
        "type_text": {"target": "theater",
                     "behaviors": [{"behavior": "type", "field": "payload.text", "rate_cps": 999}]},
    })
    with caplog.at_level(logging.WARNING, logger="primitives"):
        table = load_primitives("demo", base_path=layers["base"], campaigns_dir=layers["campaigns"])
    assert any("type_text" in record.message for record in caplog.records)
    assert table["type_text"]["behaviors"][0]["rate_cps"] == 999
    assert "style" not in table["type_text"]["behaviors"][0]  # whole list replaced, not merged


def test_campaign_extends_same_named_primitive_does_not_warn(layers, caplog):
    """An explicit `extends` on a same-named key is intentional
    specialization, not an accidental shadow — must not warn."""
    write_campaign(layers["campaigns"], "demo", {
        "beat": {"extends": "beat", "behaviors": [{"seconds": 3.0}]},
    })
    with caplog.at_level(logging.WARNING, logger="primitives"):
        table = load_primitives("demo", base_path=layers["base"], campaigns_dir=layers["campaigns"])
    assert not any("beat" in record.message for record in caplog.records)
    assert table["beat"]["behaviors"][0]["seconds"] == 3.0


# ── extends: deeper chains + cycle detection ────────────────────────────────

def test_extends_resolves_a_three_level_chain(layers):
    write_campaign(layers["campaigns"], "demo", {
        "short_beat": {"extends": "beat", "behaviors": [{"seconds": 0.5}]},
        "tiny_beat": {"extends": "short_beat", "behaviors": [{"seconds": 0.1}]},
    })
    table = load_primitives("demo", base_path=layers["base"], campaigns_dir=layers["campaigns"])
    assert table["tiny_beat"]["behaviors"][0]["behavior"] == "pause"  # inherited from the root
    assert table["tiny_beat"]["behaviors"][0]["seconds"] == 0.1        # final override wins


def test_extends_cycle_raises_primitive_error(layers):
    write_campaign(layers["campaigns"], "demo", {
        "a": {"extends": "b", "behaviors": [{"seconds": 1.0}]},
        "b": {"extends": "a", "behaviors": [{"seconds": 2.0}]},
    })
    with pytest.raises(PrimitiveError, match="cycle"):
        load_primitives("demo", base_path=layers["base"], campaigns_dir=layers["campaigns"])


def test_extends_unknown_parent_raises_primitive_error(layers):
    write_campaign(layers["campaigns"], "demo", {
        "orphan": {"extends": "does_not_exist", "behaviors": [{"seconds": 1.0}]},
    })
    with pytest.raises(PrimitiveError):
        load_primitives("demo", base_path=layers["base"], campaigns_dir=layers["campaigns"])


def test_extends_behaviors_merge_positionally_appends_extra_child_entries(layers):
    """CONTRACT.md §2: extra child behaviors[] entries append past the
    parent's length rather than being dropped or erroring."""
    write_campaign(layers["campaigns"], "demo", {
        "two_step": {
            "extends": "beat",
            "behaviors": [{"seconds": 0.2}, {"behavior": "pause", "seconds": 9.0}],
        },
    })
    table = load_primitives("demo", base_path=layers["base"], campaigns_dir=layers["campaigns"])
    behaviors = table["two_step"]["behaviors"]
    assert len(behaviors) == 2
    assert behaviors[0]["seconds"] == 0.2      # merged onto parent's single behavior
    assert behaviors[1]["seconds"] == 9.0      # appended, parent had no second entry


def test_resolve_recipe_raises_on_unknown_name(layers):
    table = load_primitives("nope", base_path=layers["base"], campaigns_dir=layers["campaigns"])
    with pytest.raises(PrimitiveError):
        resolve_recipe(table, "does_not_exist")


# ── the seven-row coder fidelity table ──────────────────────────────────────
# Loads the REAL shipped config (config/primitives.yaml + config/campaigns/
# coder/primitives.yaml), not a fixture, so this test guards the actual
# files this deliverable ships, matching CONTRACT.md §2's per-event formulas
# (derived from app/replay.py's estimate_event_seconds) to within 1e-6.

@pytest.fixture(scope="module")
def coder_primitives():
    return load_primitives("coder", base_path=REAL_BASE_PATH, campaigns_dir=REAL_CAMPAIGNS_DIR)


def test_fidelity_boss_message_matches_dialogue_cps_times_two_formula(coder_primitives):
    text = "Please fix the flaky test and let me know when it's done, thanks."
    entry = {"primitive": "show_boss_message", "payload": {"text": text}}
    expected = len(text) / 90 + 0.8  # DIALOGUE_CPS * 2, EVENT_PAUSE_S
    assert estimate_entry_seconds(entry, coder_primitives) == pytest.approx(expected, abs=1e-6)


def test_fidelity_coder_line_matches_dialogue_cps_formula(coder_primitives):
    text = "On it boss, let's get started on this right away."
    entry = {"primitive": "show_coder_line", "payload": {"name": "KODI-7", "text": text}}
    expected = len(text) / 45 + 0.8  # DIALOGUE_CPS, EVENT_PAUSE_S
    assert estimate_entry_seconds(entry, coder_primitives) == pytest.approx(expected, abs=1e-6)


def test_fidelity_show_command_matches_code_cps_and_output_lines_formula(coder_primitives):
    command = "pytest -x tests/test_agent.py"
    output = "\n".join(f"line {i}" for i in range(5))
    entry = {"primitive": "show_command", "payload": {"command": command, "output": output}}
    expected = 0.8 + len(command) / 130 + 5 / 18  # CODE_CPS, OUTPUT_LINES_PER_S, EVENT_PAUSE_S
    assert estimate_entry_seconds(entry, coder_primitives) == pytest.approx(expected, abs=1e-6)


def test_fidelity_show_diff_matches_edit_old_scroll_new_type_formula(coder_primitives):
    old_lines = ["fps: 30", "quality: low"]
    new_lines = ["fps: 60", "quality: high", "vsync: true"]
    hunks = [f"- {line}" for line in old_lines] + [f"+ {line}" for line in new_lines]
    entry = {"primitive": "show_diff", "payload": {"file": "config/worker.yaml", "hunks": hunks}}
    expected = 0.8 + len(old_lines) / 18 + sum(len(line) for line in new_lines) / 130
    assert estimate_entry_seconds(entry, coder_primitives) == pytest.approx(expected, abs=1e-6)


def test_fidelity_show_write_matches_code_cps_times_two_formula(coder_primitives):
    content = "line one\nline two\nline three"
    entry = {"primitive": "show_write", "payload": {"file": "app/new_module.py", "content": content}}
    expected = 0.8 + sum(len(line) for line in content.splitlines()) / 260  # CODE_CPS * 2
    assert estimate_entry_seconds(entry, coder_primitives) == pytest.approx(expected, abs=1e-6)


def test_fidelity_show_read_is_flat_event_pause_plus_tool_beat(coder_primitives):
    entry = {"primitive": "show_read", "payload": {"file": "app/agent.py"}}
    expected = 0.8 + 0.5  # EVENT_PAUSE_S + TOOL_BEAT_S
    assert estimate_entry_seconds(entry, coder_primitives) == pytest.approx(expected, abs=1e-6)


def test_fidelity_show_tool_generic_is_flat_event_pause_plus_tool_beat(coder_primitives):
    entry = {"primitive": "show_tool", "payload": {"tool": "Agent", "summary": "delegate a subtask"}}
    expected = 0.8 + 0.5
    assert estimate_entry_seconds(entry, coder_primitives) == pytest.approx(expected, abs=1e-6)


def test_fidelity_show_command_truncates_output_like_replay_truncate_lines(coder_primitives):
    """Output beyond MAX_OUTPUT_LINES (24) must not add to the estimate —
    same cap replay._truncate_lines applies today."""
    output = "\n".join(f"line {i}" for i in range(60))
    entry = {"primitive": "show_command", "payload": {"command": "ls", "output": output}}
    expected = 0.8 + len("ls") / 130 + 24 / 18  # capped at MAX_OUTPUT_LINES
    assert estimate_entry_seconds(entry, coder_primitives) == pytest.approx(expected, abs=1e-6)


def test_fidelity_error_flag_never_changes_the_estimate(coder_primitives):
    """Today's estimate_event_seconds ignores event["error"] entirely — the
    red '✗ that didn't work' line is instant. on_error must stay free."""
    ok = {"primitive": "show_command", "payload": {"command": "ls", "output": ""}}
    failed = {"primitive": "show_command", "payload": {"command": "ls", "output": "", "error": True}}
    assert estimate_entry_seconds(ok, coder_primitives) == estimate_entry_seconds(failed, coder_primitives)


# ── sequence vs parallel scene estimation ───────────────────────────────────

@pytest.fixture
def pause_primitives(layers):
    write_campaign(layers["campaigns"], "demo", {
        "pause_a": {"behaviors": [{"behavior": "pause", "seconds": 2.0}]},
        "pause_b": {"behaviors": [{"behavior": "pause", "seconds": 5.0}]},
    })
    return load_primitives("demo", base_path=layers["base"], campaigns_dir=layers["campaigns"])


def test_estimate_scene_seconds_sums_under_sequence_mode(pause_primitives):
    scene = {"mode": "sequence", "render": [{"primitive": "pause_a"}, {"primitive": "pause_b"}]}
    assert estimate_scene_seconds(scene, pause_primitives) == pytest.approx(7.0)


def test_estimate_scene_seconds_maxes_under_parallel_mode(pause_primitives):
    scene = {"mode": "parallel", "render": [{"primitive": "pause_a"}, {"primitive": "pause_b"}]}
    assert estimate_scene_seconds(scene, pause_primitives) == pytest.approx(5.0)


def test_estimate_scene_seconds_defaults_to_sequence_when_mode_absent(pause_primitives):
    scene = {"render": [{"primitive": "pause_a"}, {"primitive": "pause_b"}]}
    assert estimate_scene_seconds(scene, pause_primitives) == pytest.approx(7.0)


def test_estimate_scene_seconds_divides_by_speed(pause_primitives):
    scene = {"mode": "sequence", "render": [{"primitive": "pause_a"}, {"primitive": "pause_b"}]}
    assert estimate_scene_seconds(scene, pause_primitives, speed=2.0) == pytest.approx(3.5)


def test_estimate_scene_seconds_empty_render_is_zero(pause_primitives):
    assert estimate_scene_seconds({"render": []}, pause_primitives) == 0.0
    assert estimate_scene_seconds({}, pause_primitives) == 0.0


# ── missing field: renders nothing, costs 0 ─────────────────────────────────

def test_missing_field_costs_zero_seconds(layers):
    table = load_primitives("nobody", base_path=layers["base"], campaigns_dir=layers["campaigns"])
    entry = {"primitive": "type_text", "payload": {}}  # no "text" key at all
    assert estimate_entry_seconds(entry, table) == 0.0


def test_missing_field_renders_nothing(layers):
    table = load_primitives("nobody", base_path=layers["base"], campaigns_dir=layers["campaigns"])
    ctx, sinks, _ = make_ctx()
    entry = {"primitive": "type_text", "payload": {}}
    perform_entry(entry, table, ctx)
    assert sinks["theater"].getvalue() == ""


# ── render_summary ───────────────────────────────────────────────────────────

def test_render_summary_describes_each_render_entry(coder_primitives):
    scene = {"render": [
        {"primitive": "show_command", "payload": {"command": "ls -la config/", "output": ""}},
        {"primitive": "show_diff", "payload": {"file": "config/worker.yaml", "hunks": []}},
    ]}
    summary = render_summary(scene, coder_primitives)
    assert "show_command" in summary
    assert "ls -la config/" in summary
    assert "show_diff" in summary


def test_render_summary_never_raises_on_malformed_entries(coder_primitives):
    scene = {"render": [None, {"primitive": "show_command"}, "not a dict"]}
    # must not raise -- render_summary feeds narration prompts and can never
    # be the reason a scene fails to voice
    render_summary(scene, coder_primitives)


def test_render_summary_empty_for_no_render_entries(coder_primitives):
    assert render_summary({"render": []}, coder_primitives) == ""
    assert render_summary({}, coder_primitives) == ""


# ── behavior rendering: type ─────────────────────────────────────────────────

def test_type_behavior_renders_prefix_every_line_boss_style(coder_primitives):
    ctx, sinks, avatar_calls = make_ctx()
    entry = {"primitive": "show_boss_message", "payload": {"text": "line one\nline two"}}
    perform_entry(entry, coder_primitives, ctx)
    text = sinks["theater"].getvalue()
    assert "┌─ BOSS" in text
    assert "└─" in text
    assert "<cyan>│ <r>line one" in text
    assert "<cyan>│ <r>line two" in text
    assert avatar_calls[0][0] == "thinking"


def test_type_behavior_coder_line_uses_header_once_and_continuation_indent(coder_primitives):
    ctx, sinks, avatar_calls = make_ctx()
    entry = {"primitive": "show_coder_line", "payload": {"name": "KODI-7", "text": "hello\nworld"}}
    perform_entry(entry, coder_primitives, ctx)
    text = sinks["theater"].getvalue()
    assert "<green><b>KODI-7 ▸<r>" in text
    assert "hello" in text
    assert "  world" in text  # continuation_prefix, no header repeated
    assert text.count("▸") == 1
    assert avatar_calls[0] == ("speaking", "talking to the stream", "hello\nworld")


def test_type_behavior_truncates_long_content_with_dim_footer(layers):
    table = load_primitives("nobody", base_path=layers["base"], campaigns_dir=layers["campaigns"])
    ctx, sinks, _ = make_ctx(max_output_lines=3)
    entry = {"primitive": "type_text", "payload": {"text": "\n".join(f"l{i}" for i in range(10))}}
    perform_entry(entry, table, ctx)
    text = sinks["theater"].getvalue()
    assert "l2" in text and "l3" not in text
    assert "<dim>… (7 more lines)<r>" in text


# ── behavior rendering: print ─────────────────────────────────────────────────

def test_print_behavior_paces_output_and_truncates(coder_primitives):
    ctx, sinks, _ = make_ctx()
    output = "\n".join(f"line{i}" for i in range(30))
    entry = {"primitive": "show_command", "payload": {"command": "ls", "output": output}}
    perform_entry(entry, coder_primitives, ctx)
    text = sinks["theater"].getvalue()
    assert "line23" in text and "line24" not in text
    assert "<dim>… (6 more lines)<r>" in text
    # every displayed output line paced at 1/OUTPUT_LINES_PER_S
    assert ctx.pacer.sleeps.count(pytest.approx(1 / 18)) == 24


def test_print_behavior_literal_heading_is_instant_and_free(coder_primitives):
    """The '⋯ reading {file}' heading is a literal `text:` behavior — it
    must render without ever touching the pacer (matches today's
    self._line() direct write, no pacing)."""
    ctx, sinks, avatar_calls = make_ctx()
    entry = {"primitive": "show_read", "payload": {"file": "app/agent.py"}}
    perform_entry(entry, coder_primitives, ctx)
    text = sinks["theater"].getvalue()
    assert "⋯ reading app/agent.py" in text
    assert ctx.pacer.sleeps == [0.5, 0.8]  # only the two pause behaviors slept
    assert avatar_calls[0] == ("focused", "reading app/agent.py", None)


# ── behavior rendering: diff ─────────────────────────────────────────────────

def test_diff_behavior_scrolls_removed_and_types_added_independently(coder_primitives):
    ctx, sinks, avatar_calls = make_ctx()
    hunks = ["- old line", "+ new line"]
    entry = {"primitive": "show_diff", "payload": {"file": "config/worker.yaml", "hunks": hunks}}
    perform_entry(entry, coder_primitives, ctx)
    text = sinks["theater"].getvalue()
    assert "✎ editing config/worker.yaml" in text
    assert "<red>- old line<r>" in text
    assert "<green>+ <r>" in text and "new line" in text
    assert avatar_calls[0][0] == "focused"


def test_diff_behavior_truncates_removed_and_added_independently(coder_primitives):
    hunks = ([f"- old{i}" for i in range(30)] + [f"+ new{i}" for i in range(30)])
    ctx, sinks, _ = make_ctx()
    entry = {"primitive": "show_diff", "payload": {"file": "x", "hunks": hunks}}
    perform_entry(entry, coder_primitives, ctx)
    text = sinks["theater"].getvalue()
    assert text.count("more lines") == 2  # one footer for removed, one for added
    assert "old23" in text and "old24" not in text
    assert "new23" in text and "new24" not in text


def test_show_command_on_error_renders_red_line_and_frustrated_avatar(coder_primitives):
    ctx, sinks, avatar_calls = make_ctx()
    entry = {"primitive": "show_command", "payload": {"command": "false", "output": "", "error": True}}
    perform_entry(entry, coder_primitives, ctx)
    text = sinks["theater"].getvalue()
    assert "<red>✗ that didn't work<r>" in text
    assert avatar_calls[-1][0] == "frustrated"


def test_show_command_without_error_never_renders_error_line(coder_primitives):
    ctx, sinks, avatar_calls = make_ctx()
    entry = {"primitive": "show_command", "payload": {"command": "true", "output": ""}}
    perform_entry(entry, coder_primitives, ctx)
    assert "that didn't work" not in sinks["theater"].getvalue()
    assert all(call[0] != "frustrated" for call in avatar_calls)


# ── behavior rendering: pause ─────────────────────────────────────────────────

def test_pause_behavior_sleeps_and_renders_nothing(layers):
    table = load_primitives("nobody", base_path=layers["base"], campaigns_dir=layers["campaigns"])
    ctx, sinks, _ = make_ctx()
    perform_entry({"primitive": "beat", "payload": {}}, table, ctx)
    assert ctx.pacer.sleeps == [1.5]
    assert sinks["theater"].getvalue() == ""


# ── behavior rendering: image ─────────────────────────────────────────────────

def test_image_behavior_falls_back_to_placeholder_without_pillow(monkeypatch, layers):
    monkeypatch.setattr(primitives, "Image", None)
    table = load_primitives("nobody", base_path=layers["base"], campaigns_dir=layers["campaigns"])
    write_campaign(layers["campaigns"], "nobody", {
        "show_image": {"behaviors": [{"behavior": "image", "field": "payload.image", "hold_s": 4.0}]},
    })
    table = load_primitives("nobody", base_path=layers["base"], campaigns_dir=layers["campaigns"])
    ctx, sinks, _ = make_ctx()
    entry = {"primitive": "show_image", "payload": {"image": "idra_tavern.png"}}
    perform_entry(entry, table, ctx)
    text = sinks["theater"].getvalue()
    assert "idra_tavern.png" in text
    assert "┌" in text and "└" in text
    assert ctx.pacer.sleeps == [4.0]


def test_image_behavior_basename_only_never_leaks_path_components(monkeypatch, layers):
    monkeypatch.setattr(primitives, "Image", None)
    write_campaign(layers["campaigns"], "nobody", {
        "show_image": {"behaviors": [{"behavior": "image", "field": "payload.image", "hold_s": 1.0}]},
    })
    table = load_primitives("nobody", base_path=layers["base"], campaigns_dir=layers["campaigns"])
    ctx, sinks, _ = make_ctx()
    entry = {"primitive": "show_image", "payload": {"image": "../../secrets/leaked.png"}}
    perform_entry(entry, table, ctx)
    text = sinks["theater"].getvalue()
    assert "leaked.png" in text
    assert ".." not in text


class FakePilImage:
    """Minimal duck-typed stand-in for a PIL.Image instance — exercises the
    ANSI half-block path without an actual Pillow dependency (which must
    stay optional per CONTRACT.md's soft-import requirement)."""

    def __init__(self, w=2, h=2):
        self.size = (w, h)
        self._pixels = {(x, y): (10 * x, 20 * y, 30) for x in range(w) for y in range(h)}

    def convert(self, mode):
        return self

    def thumbnail(self, size):
        pass

    def getpixel(self, xy):
        return self._pixels[xy]


class FakePilModule:
    def __init__(self, image):
        self._image = image

    def open(self, path):
        return self._image


def test_image_behavior_renders_ansi_half_blocks_when_pillow_available(monkeypatch, layers):
    monkeypatch.setattr(primitives, "Image", FakePilModule(FakePilImage()))
    write_campaign(layers["campaigns"], "nobody", {
        "show_image": {"behaviors": [{"behavior": "image", "field": "payload.image", "hold_s": 2.0}]},
    })
    table = load_primitives("nobody", base_path=layers["base"], campaigns_dir=layers["campaigns"])
    ctx, sinks, _ = make_ctx()
    entry = {"primitive": "show_image", "payload": {"image": "map.png"}}
    perform_entry(entry, table, ctx)
    text = sinks["theater"].getvalue()
    assert "\x1b[38;2;" in text  # true-color ANSI foreground escape
    assert "▀" in text
    assert "map.png" not in text  # no placeholder box when the real image rendered
    assert ctx.pacer.sleeps == [2.0]


def test_image_behavior_missing_field_still_holds_but_renders_nothing(layers):
    write_campaign(layers["campaigns"], "nobody", {
        "show_image": {"behaviors": [{"behavior": "image", "field": "payload.image", "hold_s": 3.0}]},
    })
    table = load_primitives("nobody", base_path=layers["base"], campaigns_dir=layers["campaigns"])
    ctx, sinks, _ = make_ctx()
    entry = {"primitive": "show_image", "payload": {}}
    perform_entry(entry, table, ctx)
    assert sinks["theater"].getvalue() == ""
    assert ctx.pacer.sleeps == [3.0]


# ── dnd campaign config: extends genuinely exercised ────────────────────────

def test_dnd_display_map_extends_shared_show_image():
    table = load_primitives("dnd", base_path=REAL_BASE_PATH, campaigns_dir=REAL_CAMPAIGNS_DIR)
    display_map = table["display_map"]
    assert display_map["behaviors"][0]["behavior"] == "image"       # inherited
    assert display_map["behaviors"][0]["field"] == "payload.image"  # inherited
    assert display_map["behaviors"][0]["hold_s"] == 20               # overridden (base show_image is 10)


def test_dnd_scene_heading_extends_shared_type_text():
    table = load_primitives("dnd", base_path=REAL_BASE_PATH, campaigns_dir=REAL_CAMPAIGNS_DIR)
    heading = table["scene_heading"]
    assert heading["behaviors"][0]["behavior"] == "type"            # inherited
    assert heading["behaviors"][0]["field"] == "payload.text"       # inherited
    assert heading["behaviors"][0]["rate_cps"] == 18                 # overridden (base type_text is 30)
    assert heading["behaviors"][0]["style"] == {"fg": "yellow", "bold": True}


def test_dnd_open_inventory_is_a_standalone_new_primitive():
    table = load_primitives("dnd", base_path=REAL_BASE_PATH, campaigns_dir=REAL_CAMPAIGNS_DIR)
    assert table["open_inventory"]["target"] == "notes"


def test_dnd_estimate_scene_seconds_for_a_parallel_map_plus_caption_scene():
    """The design doc's 'map plus caption' scene — two render entries under
    mode: parallel, no pre-declared combination needed."""
    table = load_primitives("dnd", base_path=REAL_BASE_PATH, campaigns_dir=REAL_CAMPAIGNS_DIR)
    scene = {
        "mode": "parallel",
        "render": [
            {"primitive": "display_map", "payload": {"image": "idra_tavern.png"}},
            {"primitive": "type_text", "target": "notes",
             "payload": {"text": "The Rusted Tankard — Idra, dusk"}},
        ],
    }
    caption_seconds = len("The Rusted Tankard — Idra, dusk") / 30  # shared type_text's rate_cps
    expected = max(20.0, caption_seconds)  # display_map's overridden hold_s
    assert estimate_scene_seconds(scene, table) == pytest.approx(expected, abs=1e-6)
