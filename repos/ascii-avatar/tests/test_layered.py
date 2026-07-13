import pytest
from pathlib import Path
from PIL import Image

from avatar.frames.layered import (
    LAYER_DEFS,
    STATE_FRAME_MAP,
    PARALLAX_OFFSETS,
    LayerCompositor,
    CANVAS_SIZE,
    FrameAtlasBuilder,
    FRAME_RATES,
)


@pytest.fixture
def mock_assets(tmp_path):
    """Create minimal 32x32 RGBA PNGs for every layer variant in LAYER_DEFS.

    Images have a distinct bright pixel at (0, 0) so that parallax shifts
    (which move layers horizontally) produce visibly different composites
    for left vs right head angles.
    """
    for layer_name, layer_def in LAYER_DEFS.items():
        for variant in layer_def["variants"]:
            file_path = tmp_path / variant["file"]
            file_path.parent.mkdir(parents=True, exist_ok=True)
            img = Image.new("RGBA", (32, 32), (128, 128, 128, 200))
            # Place a bright opaque pixel at the left edge to make shifts detectable
            img.putpixel((0, 16), (255, 255, 255, 255))
            img.save(file_path)
    return tmp_path


class TestLayerCompositor:
    def test_load_layers(self, mock_assets):
        compositor = LayerCompositor(mock_assets, canvas_size=(32, 32))
        assert len(compositor.layers) == 8
        for layer_name in LAYER_DEFS:
            assert layer_name in compositor.layers
            expected_variants = {v["name"] for v in LAYER_DEFS[layer_name]["variants"]}
            assert set(compositor.layers[layer_name].keys()) == expected_variants

    def test_composite_single_combo(self, mock_assets):
        compositor = LayerCompositor(mock_assets, canvas_size=(32, 32))
        combo = {
            "background": "dim",
            "hair": "center",
            "face": "center",
            "eyes": "center_open",
            "eyebrows": "neutral",
            "nose": "center",
            "mouth": "closed",
            "overlay": "scanline_light",
        }
        result = compositor.composite(combo, "center")
        assert isinstance(result, Image.Image)
        assert result.mode == "RGB"
        assert result.size == (32, 32)

    def test_parallax_shifts_differ_by_head_angle(self, mock_assets):
        compositor = LayerCompositor(mock_assets, canvas_size=(32, 32))
        combo = {
            "background": "dim",
            "hair": "center",
            "face": "center",
            "eyes": "center_open",
            "eyebrows": "neutral",
            "nose": "center",
            "mouth": "closed",
            "overlay": "scanline_light",
        }
        img_left = compositor.composite(combo, "left")
        img_right = compositor.composite(combo, "right")
        assert list(img_left.getdata()) != list(img_right.getdata())


class TestLayerConfig:
    def test_layer_defs_has_all_layers(self):
        expected = ["background", "hair", "face", "eyes", "eyebrows", "nose", "mouth", "overlay"]
        assert list(LAYER_DEFS.keys()) == expected

    def test_each_layer_has_variants(self):
        for name, layer in LAYER_DEFS.items():
            assert len(layer["variants"]) >= 1, f"{name} needs at least 1 variant"

    def test_parallax_offsets_match_layers(self):
        for name in LAYER_DEFS:
            assert name in PARALLAX_OFFSETS, f"Missing parallax offset for {name}"

    def test_state_frame_map_has_all_states(self):
        for state in ["idle", "thinking", "speaking", "listening", "error"]:
            assert state in STATE_FRAME_MAP, f"Missing state: {state}"

    def test_state_frame_map_references_valid_variants(self):
        for state, combos in STATE_FRAME_MAP.items():
            assert len(combos) >= 1, f"{state} needs at least 1 frame combo"
            for combo in combos:
                for layer_name, variant_name in combo.items():
                    assert layer_name in LAYER_DEFS, f"Unknown layer: {layer_name}"
                    valid = [v["name"] for v in LAYER_DEFS[layer_name]["variants"]]
                    assert variant_name in valid, (
                        f"{state}: {layer_name}={variant_name} not in {valid}"
                    )


@pytest.fixture
def atlas_assets(tmp_path):
    """Create minimal 16x16 RGBA PNGs for every layer variant for atlas tests."""
    for layer_name, layer_def in LAYER_DEFS.items():
        for variant in layer_def["variants"]:
            file_path = tmp_path / variant["file"]
            file_path.parent.mkdir(parents=True, exist_ok=True)
            img = Image.new("RGBA", (16, 16), (100, 120, 140, 200))
            img.putpixel((0, 0), (255, 0, 255, 255))
            img.save(file_path)
    return tmp_path


class TestFrameAtlasBuilder:
    def test_build_produces_all_states(self, atlas_assets, tmp_path):
        cache_dir = tmp_path / "cache"
        builder = FrameAtlasBuilder(
            assets_dir=atlas_assets,
            cache_dir=cache_dir,
            pixel_width=16,
            pixel_height=16,
            max_colors=8,
        )
        frames, rates = builder.build()
        for state in ["idle", "thinking", "speaking", "listening", "error"]:
            assert state in frames, f"Missing state: {state}"
            assert len(frames[state]) >= 1, f"State {state} has no frames"
            assert isinstance(frames[state][0], str), f"Frame for {state} is not a string"

    def test_build_produces_micro_events(self, atlas_assets, tmp_path):
        cache_dir = tmp_path / "cache"
        builder = FrameAtlasBuilder(
            assets_dir=atlas_assets,
            cache_dir=cache_dir,
            pixel_width=16,
            pixel_height=16,
            max_colors=8,
        )
        frames, _ = builder.build()
        for event in ["blink", "glitch", "flicker"]:
            assert event in frames, f"Missing micro-event: {event}"

    def test_cache_hit_skips_rebuild(self, atlas_assets, tmp_path):
        cache_dir = tmp_path / "cache"
        builder = FrameAtlasBuilder(
            assets_dir=atlas_assets,
            cache_dir=cache_dir,
            pixel_width=16,
            pixel_height=16,
            max_colors=8,
        )
        frames1, _ = builder.build()
        frames2, _ = builder.build()
        assert {k: len(v) for k, v in frames1.items()} == {k: len(v) for k, v in frames2.items()}

    def test_rates_match_spec(self, atlas_assets, tmp_path):
        cache_dir = tmp_path / "cache"
        builder = FrameAtlasBuilder(
            assets_dir=atlas_assets,
            cache_dir=cache_dir,
            pixel_width=16,
            pixel_height=16,
            max_colors=8,
        )
        _, rates = builder.build()
        expected = {"idle": 0.8, "thinking": 0.15, "speaking": 0.1, "listening": 0.4, "error": 0.2}
        for state, rate in expected.items():
            assert rates[state] == rate, f"Rate mismatch for {state}: {rates[state]} != {rate}"


class TestEndToEnd:
    @pytest.fixture
    def real_assets(self):
        """Use the actual generated assets if they exist."""
        from avatar.frames.layered import ASSETS_DIR
        if not ASSETS_DIR.exists():
            pytest.skip("Assets not generated yet — run scripts/generate_layers.py")
        return ASSETS_DIR

    @pytest.mark.integration
    def test_full_pipeline_with_real_assets(self, real_assets, tmp_path):
        from avatar.frames.layered import FrameAtlasBuilder
        builder = FrameAtlasBuilder(
            assets_dir=real_assets,
            cache_dir=tmp_path / "cache",
            pixel_width=256,
            pixel_height=256,
        )
        frames, rates = builder.build()

        # All states present
        for state in ["idle", "thinking", "speaking", "listening", "error"]:
            assert state in frames
            assert len(frames[state]) >= 1
            # Each frame is a sixel string (starts with ESC P)
            assert frames[state][0].startswith("\033P") or frames[state][0].startswith("\033Ptmux")

        # Micro-events present
        for event in ["blink", "glitch", "flicker"]:
            assert event in frames

        # Rates match spec
        assert rates["idle"] == pytest.approx(0.8)
        assert rates["speaking"] == pytest.approx(0.1)

    @pytest.mark.integration
    def test_renderer_accepts_layered_frames(self, real_assets, tmp_path):
        """Verify the AvatarRenderer can use layered frames."""
        from unittest.mock import patch
        from avatar.frames.layered import FrameAtlasBuilder

        builder = FrameAtlasBuilder(
            assets_dir=real_assets,
            cache_dir=tmp_path / "cache",
            pixel_width=256,
            pixel_height=256,
        )
        frames, rates = builder.build()

        # Patch load_frame_set to return our frames
        with patch("avatar.renderer.load_frame_set", return_value=(frames, rates)):
            from avatar.renderer import AvatarRenderer
            from avatar.state_machine import AvatarState

            class FakeTerminal:
                width = 80
                height = 24
                number_of_colors = 256
                home = "HOME"
                def clear(self): return ""
                def hidden_cursor(self): return self
                def __enter__(self): return self
                def __exit__(self, *a): pass

            renderer = AvatarRenderer(terminal=FakeTerminal(), frame_set="layered2d")
            assert renderer._is_sixel is True

            for state in AvatarState:
                frame = renderer.get_current_frame(state, frame_index=0)
                assert isinstance(frame, str)
                assert len(frame) > 0
