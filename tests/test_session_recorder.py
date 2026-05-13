"""Unit tests for automation/recording/session_recorder.py.

All tests use unittest.mock only — no real ffmpeg, no real VNC, no network.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from PIL import Image

_PROJ = Path(__file__).parent.parent


def _load_session_recorder():
    """Load SessionRecorder module directly to avoid broken __init__ chain."""
    path = _PROJ / "automation" / "recording" / "session_recorder.py"
    spec = importlib.util.spec_from_file_location(
        "automation.recording.session_recorder", path
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "automation.recording"
    sys.modules["automation.recording.session_recorder"] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_session_recorder()
SessionRecorder = _mod.SessionRecorder


def _make_recorder(tmp_path: Path, **kwargs) -> SessionRecorder:
    """Build a SessionRecorder with a Mock screenshotter; does not start capture."""
    screenshotter = Mock()
    return SessionRecorder(
        screenshotter=screenshotter,
        recording_dir=tmp_path / "recording",
        **kwargs,
    )


def _solid_png(path: Path, color: int) -> Path:
    """Write a 64x64 grayscale PNG filled with a single color value."""
    Image.new("L", (64, 64), color=color).save(path)
    return path


# ---------------------------------------------------------------------------
# _mean_abs_diff
# ---------------------------------------------------------------------------


def test_mean_abs_diff_identical(tmp_path):
    img_a = Image.new("L", (64, 64), color=128)
    img_b = Image.new("L", (64, 64), color=128)
    result = SessionRecorder._mean_abs_diff(img_a, img_b)
    assert result == 0.0


def test_mean_abs_diff_max_contrast(tmp_path):
    img_black = Image.new("L", (64, 64), color=0)
    img_white = Image.new("L", (64, 64), color=255)
    result = SessionRecorder._mean_abs_diff(img_black, img_white)
    assert result > 250.0


def test_mean_abs_diff_size_mismatch(tmp_path):
    img_a = Image.new("L", (64, 64), color=0)
    img_b = Image.new("L", (32, 32), color=0)
    result = SessionRecorder._mean_abs_diff(img_a, img_b)
    assert result == 255.0


# ---------------------------------------------------------------------------
# _has_ffmpeg
# ---------------------------------------------------------------------------


def test_has_ffmpeg_present():
    with patch("subprocess.run", return_value=Mock(returncode=0)):
        assert SessionRecorder._has_ffmpeg() is True


def test_has_ffmpeg_missing():
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert SessionRecorder._has_ffmpeg() is False


# ---------------------------------------------------------------------------
# _filter_and_copy
# ---------------------------------------------------------------------------


def test_filter_and_copy_identical_sequence(tmp_path):
    """5 identical frames → only first + last kept (exactly 2)."""
    rec = _make_recorder(tmp_path, diff_threshold=3.0)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    rec.raw_dir = raw_dir
    rec.filtered_dir = tmp_path / "filtered"
    rec.index_path = tmp_path / "index.jsonl"

    frames = []
    for i in range(1, 6):
        p = raw_dir / f"frame_{i:06d}.png"
        _solid_png(p, color=100)
        frames.append(p)

    kept = rec._filter_and_copy(frames)
    assert len(kept) == 2


def test_filter_and_copy_contrast_jump(tmp_path):
    """2 black, 1 white (middle), 2 black → ≥3 kept (first, white, last)."""
    rec = _make_recorder(tmp_path, diff_threshold=10.0)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    rec.raw_dir = raw_dir
    rec.filtered_dir = tmp_path / "filtered"
    rec.index_path = tmp_path / "index.jsonl"

    colors = [0, 0, 255, 0, 0]
    frames = []
    for i, c in enumerate(colors, start=1):
        p = raw_dir / f"frame_{i:06d}.png"
        _solid_png(p, color=c)
        frames.append(p)

    kept = rec._filter_and_copy(frames)
    assert len(kept) >= 3


# ---------------------------------------------------------------------------
# _load_index_timestamps
# ---------------------------------------------------------------------------


def test_load_index_timestamps_skips_malformed(tmp_path):
    rec = _make_recorder(tmp_path)
    rec.recording_dir = tmp_path
    index = tmp_path / "index.jsonl"
    rec.index_path = index

    lines = [
        # valid
        json.dumps({"frame": "frame_000001.png", "ts": "2026-05-12T10:00:00"}) + "\n",
        # malformed JSON
        "not valid json\n",
        # missing fields
        json.dumps({"frame": "frame_000002.png"}) + "\n",
    ]
    index.write_text("".join(lines), encoding="utf-8")

    result = rec._load_index_timestamps()
    assert len(result) == 1
    assert "frame_000001.png" in result
