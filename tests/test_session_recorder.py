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


def _solid_png(path: Path, color: tuple) -> Path:
    """Write an 8x8 RGB PNG filled with a single color value."""
    Image.new("RGB", (8, 8), color=color).save(path)
    return path


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
    """5 identical frames → only first + last kept (hash dedupes middle 3)."""
    rec = _make_recorder(tmp_path)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    rec.raw_dir = raw_dir
    rec.filtered_dir = tmp_path / "filtered"
    rec.index_path = tmp_path / "index.jsonl"

    frames = []
    for i in range(1, 6):
        p = raw_dir / f"frame_{i:06d}.png"
        _solid_png(p, color=(100, 100, 100))
        frames.append(p)

    kept = rec._filter_and_copy(frames)
    assert len(kept) == 2


def test_filter_and_copy_all_distinct(tmp_path):
    """5 frames each with a unique fill color → all 5 kept."""
    rec = _make_recorder(tmp_path)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    rec.raw_dir = raw_dir
    rec.filtered_dir = tmp_path / "filtered"
    rec.index_path = tmp_path / "index.jsonl"

    colors = [(10, 20, 30), (40, 50, 60), (70, 80, 90), (100, 110, 120), (130, 140, 150)]
    frames = []
    for i, c in enumerate(colors, start=1):
        p = raw_dir / f"frame_{i:06d}.png"
        _solid_png(p, color=c)
        frames.append(p)

    kept = rec._filter_and_copy(frames)
    assert len(kept) == 5


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
