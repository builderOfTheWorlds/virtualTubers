#!/usr/bin/env python3
"""
tests/test_termgl_avatar_character.py
Covers the wiring that puts a generated parametric head into the 3D avatar
pane (app/avatar_providers/termgl_avatar.py's `character_params` block).

termgl is stubbed rather than imported: `tgl.TGL()` talks to a real
terminal and raises OSError under pytest, and these tests are about
CONFIG RESOLUTION AND FALLBACK BEHAVIOUR, not rasterization. What must be
true here is that a configured character produces a head, that no
malformed config can cost a worker its pane, and that workers without a
character are completely unaffected.
"""
import sys
import types

import pytest


@pytest.fixture
def termgl_stub(monkeypatch):
    """Minimal termgl double covering only what __init__ touches."""
    tgl = types.ModuleType("termgl")

    class _Ctx:
        def cull_face(self, *a, **k):
            pass

        def enable(self, *a, **k):
            pass

    class _Setting(int):
        def __or__(self, other):
            return _Setting(1)

    tgl.TGL = lambda w, h: _Ctx()
    tgl.Face = types.SimpleNamespace(BACK=0)
    tgl.Winding = types.SimpleNamespace(CCW=0)
    tgl.Setting = types.SimpleNamespace(
        CULL_FACE=_Setting(1), Z_BUFFER=_Setting(1),
        OUTPUT_BUFFER=_Setting(1), PROGRESSIVE=_Setting(1),
        DOUBLE_CHARS=_Setting(1),
    )
    tgl.camera = lambda m, *a, **k: None
    tgl.scale = tgl.rotate = tgl.translate = lambda m, *a, **k: None
    tgl.PixelShader = type("PixelShader", (), {})
    tgl.PixFmt = lambda *a, **k: None
    tgl.Idx = lambda *a, **k: None
    tgl.Color = types.SimpleNamespace(YELLOW=1, CYAN=2, GREEN=3, RED=4, WHITE=5)
    tgl.FmtFlag = types.SimpleNamespace(BOLD=1)
    tgl.gradient_min = types.SimpleNamespace(char=lambda i: "#")

    monkeypatch.setitem(sys.modules, "termgl", tgl)
    # render3d_common binds termgl at import time, so drop any copy imported
    # by an earlier test to force a re-import against this stub.
    monkeypatch.delitem(sys.modules, "render3d_common", raising=False)
    return tgl


@pytest.fixture
def make_provider(termgl_stub):
    def _make(avatar_config):
        from avatar_providers.termgl_avatar import TermglAvatarProvider
        return TermglAvatarProvider(avatar_config, "KODI-7", "Software Engineer")
    return _make


def test_character_params_builds_a_parametric_head(make_provider):
    provider = make_provider({"termgl_avatar": {"character_params": "chadwick"}})
    assert provider._mesh_kind == "parametric head"
    assert len(provider._mesh) > 100


def test_no_character_params_keeps_the_placeholder_icosahedron(make_provider):
    """Every other worker still runs this provider with no character of its
    own and must be completely unaffected by the generator landing."""
    provider = make_provider({})
    assert provider._mesh_kind == "icosahedron"
    assert len(provider._mesh) == 20
    assert provider._accent_color is None


def test_inline_slider_dict_is_accepted(make_provider):
    provider = make_provider({"termgl_avatar": {"character_params": {"head_width": 0.9}}})
    assert provider._mesh_kind == "parametric head"


@pytest.mark.parametrize("bad", [
    "nosuchpreset",
    42,
])
def test_unusable_character_params_fall_back_instead_of_killing_the_pane(make_provider, bad):
    """A character block that can't be resolved at all must never propagate:
    the registry would catch it and drop the worker all the way back to the
    flat-ASCII BuiltinProvider, losing the 3D pane over one typo."""
    provider = make_provider({"termgl_avatar": {"character_params": bad}})
    assert len(provider._mesh) > 0
    assert "icosahedron" in provider._mesh_kind


@pytest.mark.parametrize("recoverable", [
    {"jaw_width": "wide"},       # non-numeric slider -> default
    {"unknown_slider": 0.5},     # unknown key        -> ignored
    {"eye_size": 99},            # out of range       -> clamped
])
def test_recoverable_character_params_still_build_a_head(make_provider, recoverable):
    """The config read path is deliberately LENIENT (character_schema's
    strict=False): a single bad key degrades that one slider to its default
    rather than throwing the whole generated face away. An agent editing
    sliders gets a face it can look at and correct, not a placeholder."""
    provider = make_provider({"termgl_avatar": {"character_params": recoverable}})
    assert provider._mesh_kind == "parametric head"
    assert len(provider._mesh) > 100


def test_character_accent_color_overrides_the_expression_palette(make_provider):
    provider = make_provider({"termgl_avatar": {
        "character_params": {"preset": "chadwick", "accent_color": "CYAN"}}})
    assert provider._accent_color == "CYAN"


def test_head_gets_its_own_camera_distance(make_provider):
    """A generated head needs more room than the icosahedron, and either
    must remain overridable per worker."""
    from avatar_providers.termgl_avatar import DEFAULT_VIEW_DIST, HEAD_VIEW_DIST

    head = make_provider({"termgl_avatar": {"character_params": "chadwick"}})
    placeholder = make_provider({})

    assert head._view_dist == HEAD_VIEW_DIST
    assert placeholder._view_dist == DEFAULT_VIEW_DIST


def test_view_dist_is_overridable(make_provider):
    provider = make_provider({"termgl_avatar": {
        "character_params": "chadwick", "view_dist": 9.9}})
    assert provider._view_dist == pytest.approx(9.9)


def test_coder_worker_config_wires_chadwick():
    """The flagship character is actually live in the shipped config — this
    is the one assertion that would catch the config and the code drifting
    apart (docs/avatar_3d_design.md §5 step 2).

    2026-09-22: coder.yaml moved from termgl_avatar (character-grid ANSI)
    to codec_avatar (live GPU-first pixel rendering, docs/gl_raster_
    benchmark.md) — this asserts the current wiring, not the original one.
    """
    from pathlib import Path

    import yaml

    from character_schema import resolve_params

    config_path = Path(__file__).resolve().parents[1] / "config" / "workers" / "coder.yaml"
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    avatar = cfg["avatar"]
    assert avatar["provider"] == "codec_avatar"
    params = resolve_params(avatar["codec_avatar"]["character_params"])
    assert params["accent_color"] in ("YELLOW", "CYAN", "GREEN", "RED", "WHITE",
                                      "BLUE", "PURPLE", "BLACK")


def test_tuber_base_workers_all_wired_to_codec_avatar():
    """2026-09-22: all 6 tuber_base-layout workers standardized on
    codec_avatar with distinct accent_color presets (character_schema.py's
    nyx1/oko2/ada3/tess3/max1) — tuber_0.yaml (roundtable layout, GM) is
    deliberately excluded, see its own layout.preset."""
    from pathlib import Path

    import yaml

    from character_schema import resolve_params

    workers_dir = Path(__file__).resolve().parents[1] / "config" / "workers"
    expected = {
        "coder.yaml": "chadwick",
        "coder-native.yaml": "nyx1",
        "coder-opencode.yaml": "oko2",
        "coder-aider.yaml": "ada3",
        "tester.yaml": "tess3",
        "manager.yaml": "max1",
    }
    for filename, preset in expected.items():
        cfg = yaml.safe_load((workers_dir / filename).read_text(encoding="utf-8"))
        avatar = cfg["avatar"]
        assert avatar["provider"] == "codec_avatar", filename
        assert cfg["layout"]["preset"] == "tuber_base", filename
        character_params = avatar["codec_avatar"]["character_params"]
        assert character_params == preset, filename
        # Must actually resolve (schema-valid preset, not just a string).
        resolve_params(character_params)
