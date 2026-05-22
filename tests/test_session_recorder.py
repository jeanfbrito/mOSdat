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


# ---------------------------------------------------------------------------
# Stage 3a: window-state sampler
# ---------------------------------------------------------------------------


class _FakeSSHResult:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    @property
    def success(self) -> bool:
        return self.returncode == 0


def _sampler_stdout() -> str:
    """Build a fake wmctrl/xdotool output blob matching the sampler script."""
    return (
        "<<<ACTIVE>>>\n"
        "Rocket.Chat — Workspace\n"
        "<<<WINDOWS>>>\n"
        "0x04200007  0 ubuntu2204 Rocket.Chat — Workspace\n"
        "0x04400001  0 ubuntu2204 Top Bar\n"
        "<<<CURSOR>>>\n"
        "X=512\n"
        "Y=384\n"
        "SCREEN=0\n"
        "WINDOW=12345\n"
        "<<<END>>>\n"
    )


def test_parse_window_state_full_blob():
    parsed = SessionRecorder._parse_window_state(_sampler_stdout())
    assert parsed["active_window"] == "Rocket.Chat — Workspace"
    assert parsed["open_windows"] == [
        "Rocket.Chat — Workspace",
        "Top Bar",
    ]
    assert parsed["cursor_x"] == 512
    assert parsed["cursor_y"] == 384


def test_parse_window_state_empty_blob_returns_empty():
    blob = (
        "<<<ACTIVE>>>\n"
        "<<<WINDOWS>>>\n"
        "<<<CURSOR>>>\n"
        "<<<END>>>\n"
    )
    assert SessionRecorder._parse_window_state(blob) == {}


def test_parse_window_state_caps_open_windows_at_20():
    lines = ["<<<ACTIVE>>>", "Some Window", "<<<WINDOWS>>>"]
    for i in range(30):
        lines.append(f"0xdead{i:04x}  0 host Title {i}")
    lines += ["<<<CURSOR>>>", "X=0", "Y=0", "<<<END>>>"]
    parsed = SessionRecorder._parse_window_state("\n".join(lines))
    assert len(parsed["open_windows"]) == 20
    assert parsed["open_windows"][0] == "Title 0"
    assert parsed["open_windows"][-1] == "Title 19"


def test_append_index_no_sampler_keeps_legacy_schema(tmp_path):
    """When record_window_state=False, index.jsonl lines stay {frame, ts}."""
    rec = _make_recorder(tmp_path, record_window_state=False)
    rec.recording_dir.mkdir(parents=True, exist_ok=True)
    rec.index_path = tmp_path / "index.jsonl"
    rec._append_index("frame_000001.png")

    line = rec.index_path.read_text(encoding="utf-8").strip()
    obj = json.loads(line)
    assert set(obj.keys()) == {"frame", "ts"}
    assert obj["frame"] == "frame_000001.png"


def test_append_index_with_sampler_carries_metadata(tmp_path):
    """When sampler has produced a sample, _append_index bundles it in."""
    fake_ssh = Mock()
    fake_ssh.run.return_value = _FakeSSHResult(0, _sampler_stdout())
    rec = _make_recorder(
        tmp_path,
        ssh=fake_ssh,
        record_window_state=True,
    )
    rec.recording_dir.mkdir(parents=True, exist_ok=True)
    rec.index_path = tmp_path / "index.jsonl"
    # Simulate a sample produced by the sampler thread.
    sample = SessionRecorder._parse_window_state(_sampler_stdout())
    with rec._sample_lock:
        rec._latest_sample = sample

    rec._append_index("frame_000001.png")
    line = rec.index_path.read_text(encoding="utf-8").strip()
    obj = json.loads(line)

    # Legacy fields preserved.
    assert obj["frame"] == "frame_000001.png"
    assert isinstance(obj["ts"], str)
    # Stage 3a additions.
    assert obj["active_window"] == "Rocket.Chat — Workspace"
    assert obj["open_windows"] == ["Rocket.Chat — Workspace", "Top Bar"]
    assert obj["cursor_x"] == 512
    assert obj["cursor_y"] == 384
    assert isinstance(obj["timestamp_ns"], int)
    assert obj["timestamp_ns"] > 0


def test_append_index_sampler_enabled_no_sample_yet(tmp_path):
    """Sampler ON but no sample published: line carries timestamp_ns only."""
    fake_ssh = Mock()
    rec = _make_recorder(
        tmp_path,
        ssh=fake_ssh,
        record_window_state=True,
    )
    rec.recording_dir.mkdir(parents=True, exist_ok=True)
    rec.index_path = tmp_path / "index.jsonl"

    rec._append_index("frame_000001.png")
    obj = json.loads(rec.index_path.read_text(encoding="utf-8").strip())
    assert obj["frame"] == "frame_000001.png"
    assert "timestamp_ns" in obj
    # No sample → none of the optional metadata keys.
    for key in ("active_window", "open_windows", "cursor_x", "cursor_y"):
        assert key not in obj


def test_collect_window_state_disables_on_ssh_failure(tmp_path):
    fake_ssh = Mock()
    fake_ssh.run.return_value = _FakeSSHResult(127, stderr="wmctrl: command not found")
    rec = _make_recorder(
        tmp_path,
        ssh=fake_ssh,
        record_window_state=True,
    )
    assert rec._sampler_disabled is False
    result = rec._collect_window_state()
    assert result is None
    assert rec._sampler_disabled is True  # subsequent ticks short-circuit


def test_collect_window_state_disables_on_empty_output(tmp_path):
    fake_ssh = Mock()
    # rc=0 (markers print fine) but neither tool produced anything.
    fake_ssh.run.return_value = _FakeSSHResult(
        0,
        "<<<ACTIVE>>>\n<<<WINDOWS>>>\n<<<CURSOR>>>\n<<<END>>>\n",
    )
    rec = _make_recorder(
        tmp_path,
        ssh=fake_ssh,
        record_window_state=True,
    )
    assert rec._collect_window_state() is None
    assert rec._sampler_disabled is True


def test_record_window_state_ignored_without_ssh(tmp_path):
    """`record_window_state=True` but no ssh handle → silently degrade."""
    rec = _make_recorder(tmp_path, ssh=None, record_window_state=True)
    # Constructor must flip the flag off so _append_index stays legacy.
    assert rec._record_window_state is False


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
