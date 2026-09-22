#!/usr/bin/env python3
"""
tests/test_character_preview.py
Covers the agent-facing CLI (app/character_preview.py) as it exists after
switching from ascii_raster.py to the codec pixel pipeline (codec_head.py +
gl_raster.py / pixel_raster.py) — see docs/character_generator.md.

Writes real PNGs via a tmp_path fixture rather than the module's default
preview_out/ directory, so the suite doesn't scatter files into the repo.
"""
import json

import pytest

from character_preview import build_params, main, parse_set


# ── Param building (unchanged by the renderer swap) ────────────────────────
@pytest.mark.parametrize("pair,expected", [
    ("eye_size=0.8", {"eye_size": 0.8}),
    ("accent_color=CYAN", {"accent_color": "CYAN"}),   # bare string, unquoted
    ("build=1", {"build": 1}),
])
def test_parse_set_parses_pairs(pair, expected):
    assert parse_set([pair]) == expected


def test_parse_set_rejects_malformed_pair():
    from character_schema import CharacterParamError
    with pytest.raises(CharacterParamError, match="key=value"):
        parse_set(["eye_size"])


def test_build_params_applies_set_over_preset():
    class Args:
        preset = "chadwick"
        params = None
        set = ["jaw_width=0.05"]

    assert build_params(Args())["jaw_width"] == pytest.approx(0.05)


def test_build_params_set_beats_params_json():
    class Args:
        preset = None
        params = '{"eye_size": 0.2}'
        set = ["eye_size=0.9"]

    assert build_params(Args())["eye_size"] == pytest.approx(0.9)


# ── The CLI, rendering real PNGs ────────────────────────────────────────────
def test_cli_renders_a_single_view(tmp_path, capsys):
    out = tmp_path / "front.png"
    code = main(["--preset", "chadwick", "--view", "front",
                 "--width", "64", "--height", "80", "-o", str(out)])
    assert code == 0
    assert out.exists() and out.stat().st_size > 0
    printed = capsys.readouterr().out
    assert "front" in printed


def test_cli_turntable_renders_every_view(tmp_path, capsys):
    code = main(["--preset", "chadwick", "--width", "48", "--height", "60",
                 "--out-dir", str(tmp_path), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    labels = [f["label"] for f in payload["frames"]]
    assert labels == ["front", "three-quarter", "profile", "rear-quarter"]
    for frame in payload["frames"]:
        assert (tmp_path / f"{frame['label']}.png").exists()
        assert frame["backend"] in ("gpu", "cpu")


def test_cli_json_output_is_parseable(tmp_path, capsys):
    code = main(["--preset", "chadwick", "--view", "front",
                 "--width", "40", "--height", "50",
                 "--out-dir", str(tmp_path), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["params"]["head_width"] > 0
    assert len(payload["frames"]) == 1
    assert payload["frames"][0]["seconds"] > 0


def test_cli_out_rejects_multiple_views(tmp_path, capsys):
    code = main(["--preset", "chadwick", "-o", str(tmp_path / "x.png")])
    captured = capsys.readouterr()
    assert code == 2
    assert "error:" in captured.err


def test_cli_reports_bad_params_without_a_traceback(capsys):
    """An iterating agent will send a bad key; it should get a one-line
    reason and a non-zero exit, not a stack trace."""
    code = main(["--set", "hat_size=0.5"])
    captured = capsys.readouterr()
    assert code == 2
    assert "error:" in captured.err
    assert "Traceback" not in captured.err


def test_cli_list_params_emits_the_schema(capsys):
    assert main(["--list-params"]) == 0
    assert "head_width" in json.loads(capsys.readouterr().out)


def test_cli_cpu_flag_forces_cpu_backend(tmp_path, capsys):
    code = main(["--preset", "chadwick", "--view", "front", "--cpu",
                 "--width", "40", "--height", "50",
                 "--out-dir", str(tmp_path), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["frames"][0]["backend"] == "cpu"


def test_cli_tint_selects_amber(tmp_path, capsys):
    code = main(["--preset", "chadwick", "--view", "front", "--tint", "amber",
                 "--width", "40", "--height", "50",
                 "--out-dir", str(tmp_path)])
    assert code == 0
