import pytest
from pathlib import Path
from PIL import Image, ImageDraw
from scripts.generate_layers import generate_backgrounds, generate_overlays, generate_face_layers, generate_expression_layers


class TestProceduralLayers:
    def test_generate_backgrounds(self, tmp_path):
        generate_backgrounds(tmp_path / "background", canvas_size=(512, 512))
        for name in ["bg_dim.png", "bg_pulse.png", "bg_error.png"]:
            fpath = tmp_path / "background" / name
            assert fpath.exists(), f"Missing: {fpath}"
            img = Image.open(fpath)
            assert img.size == (512, 512)
            assert img.mode == "RGBA"

    def test_generate_overlays(self, tmp_path):
        generate_overlays(tmp_path / "overlay", canvas_size=(512, 512))
        expected = [
            "scanline_light.png", "scanline_heavy.png", "crt_bloom.png",
            "holo_flicker.png", "chrom_aberr.png", "glitch_corrupt.png",
            "noise_bands.png", "red_tint.png",
        ]
        for name in expected:
            fpath = tmp_path / "overlay" / name
            assert fpath.exists(), f"Missing: {fpath}"
            img = Image.open(fpath)
            assert img.size == (512, 512)
            assert img.mode == "RGBA"


class TestFaceLayers:
    def test_generate_face_layers(self, tmp_path):
        ref = Image.new("RGB", (512, 512), (180, 140, 120))
        draw = ImageDraw.Draw(ref)
        draw.ellipse([128, 64, 384, 448], fill=(200, 160, 140))
        generate_face_layers(ref, tmp_path, canvas_size=(512, 512))
        for name in ["face_center.png", "face_left15.png", "face_right15.png", "face_up10.png", "face_down10.png"]:
            assert (tmp_path / "face" / name).exists()
            assert Image.open(tmp_path / "face" / name).mode == "RGBA"
        for name in ["hair_center.png", "hair_left.png", "hair_right.png"]:
            assert (tmp_path / "hair" / name).exists()
        for name in ["nose_center.png", "nose_left.png", "nose_right.png"]:
            assert (tmp_path / "nose" / name).exists()


class TestExpressionLayers:
    def test_generate_expression_layers(self, tmp_path):
        generate_expression_layers(tmp_path, canvas_size=(512, 512))
        for direction in ["center", "left", "right", "up", "down"]:
            for state in ["open", "half", "closed"]:
                assert (tmp_path / "eyes" / f"eyes_{direction}_{state}.png").exists()
        for name in ["brows_neutral.png", "brows_raised.png", "brows_furrowed.png", "brows_asymmetric.png"]:
            assert (tmp_path / "eyebrows" / name).exists()
        for name in ["mouth_closed.png", "mouth_slight.png", "mouth_open.png", "mouth_wide.png", "mouth_smile.png", "mouth_glitch.png"]:
            assert (tmp_path / "mouth" / name).exists()
