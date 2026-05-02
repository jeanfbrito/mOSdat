"""Unit tests for automation.visual — SSIM computation and capture/check helpers."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from PIL import Image, ImageOps


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _save_rgb(path: Path, color: tuple[int, int, int], size: tuple[int, int] = (64, 64)) -> Path:
    """Save a solid-color PNG and return its path."""
    Image.new("RGB", size, color).save(path)
    return path


def _save_gradient(path: Path, size: tuple[int, int] = (64, 64)) -> Path:
    """Save a simple gradient PNG."""
    img = Image.new("L", size)
    pixels = img.load()
    for y in range(size[1]):
        for x in range(size[0]):
            pixels[x, y] = int((x / size[0]) * 255)
    img.convert("RGB").save(path)
    return path


# ---------------------------------------------------------------------------
# compute_ssim tests
# ---------------------------------------------------------------------------

class TestComputeSsim:
    def test_identical_images_return_near_one(self, tmp_path):
        from automation.visual import compute_ssim

        img = tmp_path / "a.png"
        _save_gradient(img)
        score = compute_ssim(img, img)
        assert score >= 0.99, f"Identical images should score ≥ 0.99, got {score}"

    def test_identical_copy_returns_near_one(self, tmp_path):
        from automation.visual import compute_ssim

        a = tmp_path / "a.png"
        b = tmp_path / "b.png"
        _save_gradient(a)
        import shutil
        shutil.copy2(a, b)
        score = compute_ssim(a, b)
        assert score >= 0.99, f"Copy of image should score ≥ 0.99, got {score}"

    def test_inverted_image_scores_below_threshold(self, tmp_path):
        from automation.visual import compute_ssim

        a = tmp_path / "a.png"
        b = tmp_path / "b.png"
        _save_gradient(a)
        # Invert the gradient to create a maximally different image
        img = Image.open(a).convert("L")
        ImageOps.invert(img).convert("RGB").save(b)
        score = compute_ssim(a, b)
        assert score < 0.95, f"Inverted image should score < 0.95, got {score}"

    def test_different_solid_colors_score_below_threshold(self, tmp_path):
        from automation.visual import compute_ssim

        a = tmp_path / "white.png"
        b = tmp_path / "black.png"
        _save_rgb(a, (255, 255, 255))
        _save_rgb(b, (0, 0, 0))
        score = compute_ssim(a, b)
        assert score < 0.95, f"White vs black should score < 0.95, got {score}"

    def test_slight_jpeg_noise_stays_above_threshold(self, tmp_path):
        """A PNG saved with slight JPEG compression noise should still pass."""
        from automation.visual import compute_ssim
        import io

        a = tmp_path / "a.png"
        b = tmp_path / "b.png"
        _save_gradient(a, size=(128, 128))

        # Re-save via JPEG (lossy) then reload as PNG to simulate noise
        img = Image.open(a)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        buf.seek(0)
        Image.open(buf).save(b, format="PNG")

        score = compute_ssim(a, b)
        assert score >= 0.90, f"JPEG-noisy copy should score ≥ 0.90, got {score}"

    def test_shifted_image_scores_below_threshold(self, tmp_path):
        from automation.visual import compute_ssim
        from PIL import ImageChops

        a = tmp_path / "a.png"
        b = tmp_path / "b.png"
        _save_gradient(a, size=(128, 128))
        img = Image.open(a)
        # Shift by 40 pixels — enough to clearly differ
        shifted = ImageChops.offset(img, 40, 40)
        shifted.save(b)
        score = compute_ssim(a, b)
        assert score < 0.95, f"Shifted image should score < 0.95, got {score}"

    def test_returns_float_in_valid_range(self, tmp_path):
        from automation.visual import compute_ssim

        a = tmp_path / "a.png"
        b = tmp_path / "b.png"
        _save_rgb(a, (128, 64, 32))
        _save_rgb(b, (200, 100, 50))
        score = compute_ssim(a, b)
        assert isinstance(score, float)
        assert -1.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# capture_reference tests
# ---------------------------------------------------------------------------

class TestCaptureReference:
    def test_writes_to_expected_path(self, tmp_path):
        from automation.visual import capture_reference

        shot = tmp_path / "screenshot.png"
        _save_rgb(shot, (100, 100, 100))
        refs_dir = tmp_path / "refs"

        dest = capture_reference("my-scenario", 3, shot, refs_dir)

        expected = refs_dir / "my-scenario" / "step_3.png"
        assert dest == expected
        assert dest.exists(), "Reference file should exist after capture"

    def test_creates_scenario_subdir(self, tmp_path):
        from automation.visual import capture_reference

        shot = tmp_path / "shot.png"
        _save_rgb(shot, (10, 20, 30))
        refs_dir = tmp_path / "refs"

        capture_reference("new-scenario", 0, shot, refs_dir)
        assert (refs_dir / "new-scenario").is_dir()

    def test_content_is_preserved(self, tmp_path):
        from automation.visual import capture_reference, compute_ssim

        shot = tmp_path / "shot.png"
        _save_gradient(shot)
        refs_dir = tmp_path / "refs"

        dest = capture_reference("s", 0, shot, refs_dir)
        score = compute_ssim(shot, dest)
        assert score >= 0.99, "Captured reference should be pixel-identical to source"


# ---------------------------------------------------------------------------
# check_against_reference tests
# ---------------------------------------------------------------------------

class TestCheckAgainstReference:
    def test_identical_image_passes(self, tmp_path):
        from automation.visual import capture_reference, check_against_reference
        import shutil

        refs_dir = tmp_path / "refs"
        shot = tmp_path / "shot.png"
        _save_gradient(shot)
        capture_reference("s", 0, shot, refs_dir)

        # Check with an identical copy
        shot2 = tmp_path / "shot2.png"
        shutil.copy2(shot, shot2)
        passed, score = check_against_reference("s", 0, shot2, refs_dir, threshold=0.95)
        assert passed is True
        assert score >= 0.95

    def test_different_image_fails(self, tmp_path):
        from automation.visual import capture_reference, check_against_reference
        from PIL import ImageOps

        refs_dir = tmp_path / "refs"
        shot = tmp_path / "shot.png"
        _save_gradient(shot)
        capture_reference("s", 0, shot, refs_dir)

        # Check with inverted image
        different = tmp_path / "different.png"
        img = Image.open(shot).convert("L")
        ImageOps.invert(img).convert("RGB").save(different)

        passed, score = check_against_reference("s", 0, different, refs_dir, threshold=0.95)
        assert passed is False
        assert score < 0.95

    def test_missing_reference_raises(self, tmp_path):
        from automation.visual import check_against_reference

        refs_dir = tmp_path / "refs"
        shot = tmp_path / "shot.png"
        _save_rgb(shot, (0, 0, 0))

        with pytest.raises(FileNotFoundError):
            check_against_reference("nonexistent", 0, shot, refs_dir)

    def test_custom_threshold_respected(self, tmp_path):
        from automation.visual import capture_reference, check_against_reference

        refs_dir = tmp_path / "refs"
        shot = tmp_path / "shot.png"
        _save_gradient(shot)
        capture_reference("s", 0, shot, refs_dir)

        # With threshold=0.0 everything passes
        passed, score = check_against_reference("s", 0, shot, refs_dir, threshold=0.0)
        assert passed is True
