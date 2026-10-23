"""Tests for Card 3's offline verify_recorded resolution
(automation/runners/deferred_verify.py).

Covers: label resolution (found/ambiguous/missing), int step_num
resolution, frame-window filtering via filtered_index.jsonl, first-yes
short-circuit, all-no -> fail, empty-window -> fail.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from automation.runners.deferred_verify import (
    DeferredVerifyError,
    resolve_pending_verifies,
)


BASE = datetime(2026, 1, 1, 12, 0, 0)


def _ts(offset_s: float) -> str:
    return (BASE + timedelta(seconds=offset_s)).isoformat()


def _write_events(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


def _write_filtered_index(path: Path, frames: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for rec in frames:
            f.write(json.dumps(rec) + "\n")


def _make_frame(dir_path: Path, name: str) -> None:
    img = Image.new("RGB", (4, 4), color="white")
    img.save(dir_path / name)


class _FakeArtifacts:
    def __init__(self, filtered_dir, filtered_index_path):
        self.filtered_dir = filtered_dir
        self.filtered_index_path = filtered_index_path


def _make_runner(vlm_verify_side_effect=None, vlm_verify_return=False):
    runner = MagicMock()
    runner._pending_recorded_verifies = []
    if vlm_verify_side_effect is not None:
        runner.vlm.verify.side_effect = vlm_verify_side_effect
    else:
        runner.vlm.verify.return_value = vlm_verify_return
    return runner


@pytest.fixture
def workdir(tmp_path):
    filtered_dir = tmp_path / "filtered"
    filtered_dir.mkdir()
    events_path = tmp_path / "events.jsonl"
    filtered_index_path = tmp_path / "filtered_index.jsonl"
    return {
        "tmp_path": tmp_path,
        "filtered_dir": filtered_dir,
        "events_path": events_path,
        "filtered_index_path": filtered_index_path,
    }


class TestNoPendingVerifies:
    def test_empty_pending_returns_empty_list(self, workdir):
        runner = _make_runner()
        _write_events(workdir["events_path"], [])
        artifacts = _FakeArtifacts(workdir["filtered_dir"], workdir["filtered_index_path"])
        result = resolve_pending_verifies(runner, workdir["events_path"], artifacts)
        assert result == []


class TestLabelResolution:
    def test_label_found_resolves_window(self, workdir):
        runner = _make_runner(vlm_verify_return=True)
        runner._pending_recorded_verifies = [
            {"prompt": "is a toast visible", "between": ["open toast", "close toast"], "step_num": 3}
        ]
        _write_events(workdir["events_path"], [
            {"ts": _ts(0), "event": "step_start", "step_num": 3, "step_label": "3: open toast"},
            {"ts": _ts(1), "event": "step_end", "step_num": 3, "status": "ok"},
            {"ts": _ts(5), "event": "step_start", "step_num": 4, "step_label": "4: close toast"},
            {"ts": _ts(6), "event": "step_end", "step_num": 4, "status": "ok"},
        ])
        _make_frame(workdir["filtered_dir"], "frame_000001.png")
        _write_filtered_index(workdir["filtered_index_path"], [
            {"frame": "frame_000001.png", "ts": _ts(2)},
        ])
        # But the pending verify references labels "open toast"/"close toast",
        # not "3: open toast" — use exact label matches instead below.
        runner._pending_recorded_verifies = [
            {"prompt": "is a toast visible", "between": ["3: open toast", "4: close toast"], "step_num": 3}
        ]
        artifacts = _FakeArtifacts(workdir["filtered_dir"], workdir["filtered_index_path"])
        results = resolve_pending_verifies(runner, workdir["events_path"], artifacts)
        assert len(results) == 1
        assert results[0]["passed"] is True
        assert results[0]["frames_checked"] == 1

    def test_ambiguous_label_raises(self, workdir):
        runner = _make_runner()
        runner._pending_recorded_verifies = [
            {"prompt": "x", "between": ["dup label", 2], "step_num": 1}
        ]
        _write_events(workdir["events_path"], [
            {"ts": _ts(0), "event": "step_start", "step_num": 1, "step_label": "dup label"},
            {"ts": _ts(1), "event": "step_start", "step_num": 5, "step_label": "dup label"},
            {"ts": _ts(2), "event": "step_start", "step_num": 2, "step_label": "s2"},
        ])
        artifacts = _FakeArtifacts(workdir["filtered_dir"], workdir["filtered_index_path"])
        with pytest.raises(DeferredVerifyError, match="ambiguous"):
            resolve_pending_verifies(runner, workdir["events_path"], artifacts)

    def test_missing_label_raises(self, workdir):
        runner = _make_runner()
        runner._pending_recorded_verifies = [
            {"prompt": "x", "between": ["does not exist", 2], "step_num": 1}
        ]
        _write_events(workdir["events_path"], [
            {"ts": _ts(0), "event": "step_start", "step_num": 2, "step_label": "s2"},
        ])
        artifacts = _FakeArtifacts(workdir["filtered_dir"], workdir["filtered_index_path"])
        with pytest.raises(DeferredVerifyError, match="no step found with label"):
            resolve_pending_verifies(runner, workdir["events_path"], artifacts)


class TestStepNumResolution:
    def test_int_step_num_resolves(self, workdir):
        runner = _make_runner(vlm_verify_return=True)
        runner._pending_recorded_verifies = [
            {"prompt": "is a toast visible", "between": [1, 2], "step_num": 1}
        ]
        _write_events(workdir["events_path"], [
            {"ts": _ts(0), "event": "step_start", "step_num": 1, "step_label": "1"},
            {"ts": _ts(1), "event": "step_end", "step_num": 1, "status": "ok"},
            {"ts": _ts(5), "event": "step_start", "step_num": 2, "step_label": "2"},
            {"ts": _ts(6), "event": "step_end", "step_num": 2, "status": "ok"},
        ])
        _make_frame(workdir["filtered_dir"], "frame_000001.png")
        _write_filtered_index(workdir["filtered_index_path"], [
            {"frame": "frame_000001.png", "ts": _ts(3)},
        ])
        artifacts = _FakeArtifacts(workdir["filtered_dir"], workdir["filtered_index_path"])
        results = resolve_pending_verifies(runner, workdir["events_path"], artifacts)
        assert results[0]["passed"] is True

    def test_missing_step_num_raises(self, workdir):
        runner = _make_runner()
        runner._pending_recorded_verifies = [
            {"prompt": "x", "between": [99, 2], "step_num": 1}
        ]
        _write_events(workdir["events_path"], [
            {"ts": _ts(0), "event": "step_start", "step_num": 2, "step_label": "s2"},
        ])
        artifacts = _FakeArtifacts(workdir["filtered_dir"], workdir["filtered_index_path"])
        with pytest.raises(DeferredVerifyError, match="step_num=99"):
            resolve_pending_verifies(runner, workdir["events_path"], artifacts)


class TestFrameWindowFiltering:
    def test_only_frames_inside_window_are_checked(self, workdir):
        seen_frames = []

        def _verify_side_effect(img, prompt):
            seen_frames.append(prompt)
            return False

        runner = _make_runner(vlm_verify_side_effect=_verify_side_effect)
        runner._pending_recorded_verifies = [
            {"prompt": "is a toast visible", "between": [1, 2], "step_num": 1}
        ]
        _write_events(workdir["events_path"], [
            {"ts": _ts(10), "event": "step_start", "step_num": 1, "step_label": "1"},
            {"ts": _ts(20), "event": "step_start", "step_num": 2, "step_label": "2"},
        ])
        for i, off in enumerate([5, 12, 15, 18, 25], start=1):
            name = f"frame_{i:06d}.png"
            _make_frame(workdir["filtered_dir"], name)
        _write_filtered_index(workdir["filtered_index_path"], [
            {"frame": "frame_000001.png", "ts": _ts(5)},   # before window
            {"frame": "frame_000002.png", "ts": _ts(12)},  # inside
            {"frame": "frame_000003.png", "ts": _ts(15)},  # inside
            {"frame": "frame_000004.png", "ts": _ts(18)},  # inside
            {"frame": "frame_000005.png", "ts": _ts(25)},  # after window
        ])
        artifacts = _FakeArtifacts(workdir["filtered_dir"], workdir["filtered_index_path"])
        results = resolve_pending_verifies(runner, workdir["events_path"], artifacts)
        assert results[0]["frames_checked"] == 3
        assert results[0]["passed"] is False

    def test_first_yes_short_circuits(self, workdir):
        call_count = {"n": 0}

        def _verify_side_effect(img, prompt):
            call_count["n"] += 1
            return call_count["n"] == 2  # second frame says yes

        runner = _make_runner(vlm_verify_side_effect=_verify_side_effect)
        runner._pending_recorded_verifies = [
            {"prompt": "is a toast visible", "between": [1, 2], "step_num": 1}
        ]
        _write_events(workdir["events_path"], [
            {"ts": _ts(0), "event": "step_start", "step_num": 1, "step_label": "1"},
            {"ts": _ts(10), "event": "step_start", "step_num": 2, "step_label": "2"},
        ])
        for i in range(1, 5):
            _make_frame(workdir["filtered_dir"], f"frame_{i:06d}.png")
        _write_filtered_index(workdir["filtered_index_path"], [
            {"frame": "frame_000001.png", "ts": _ts(1)},
            {"frame": "frame_000002.png", "ts": _ts(2)},
            {"frame": "frame_000003.png", "ts": _ts(3)},
            {"frame": "frame_000004.png", "ts": _ts(4)},
        ])
        artifacts = _FakeArtifacts(workdir["filtered_dir"], workdir["filtered_index_path"])
        results = resolve_pending_verifies(runner, workdir["events_path"], artifacts)
        assert results[0]["passed"] is True
        assert results[0]["frames_checked"] == 2  # stopped at the 2nd (first yes)

    def test_all_no_fails(self, workdir):
        runner = _make_runner(vlm_verify_return=False)
        runner._pending_recorded_verifies = [
            {"prompt": "is a toast visible", "between": [1, 2], "step_num": 1}
        ]
        _write_events(workdir["events_path"], [
            {"ts": _ts(0), "event": "step_start", "step_num": 1, "step_label": "1"},
            {"ts": _ts(10), "event": "step_start", "step_num": 2, "step_label": "2"},
        ])
        for i in range(1, 3):
            _make_frame(workdir["filtered_dir"], f"frame_{i:06d}.png")
        _write_filtered_index(workdir["filtered_index_path"], [
            {"frame": "frame_000001.png", "ts": _ts(3)},
            {"frame": "frame_000002.png", "ts": _ts(6)},
        ])
        artifacts = _FakeArtifacts(workdir["filtered_dir"], workdir["filtered_index_path"])
        results = resolve_pending_verifies(runner, workdir["events_path"], artifacts)
        assert results[0]["passed"] is False
        assert results[0]["frames_checked"] == 2

    def test_empty_window_fails(self, workdir):
        runner = _make_runner(vlm_verify_return=True)
        runner._pending_recorded_verifies = [
            {"prompt": "is a toast visible", "between": [1, 2], "step_num": 1}
        ]
        _write_events(workdir["events_path"], [
            {"ts": _ts(0), "event": "step_start", "step_num": 1, "step_label": "1"},
            {"ts": _ts(1), "event": "step_start", "step_num": 2, "step_label": "2"},
        ])
        # No frames at all in filtered_index.jsonl within [_ts(0), _ts(1)]
        _write_filtered_index(workdir["filtered_index_path"], [])
        artifacts = _FakeArtifacts(workdir["filtered_dir"], workdir["filtered_index_path"])
        results = resolve_pending_verifies(runner, workdir["events_path"], artifacts)
        assert results[0]["passed"] is False
        assert results[0]["frames_checked"] == 0
        runner.vlm.verify.assert_not_called()


class TestEventEmission:
    def test_emits_verify_recorded_result_event(self, workdir):
        runner = _make_runner(vlm_verify_return=True)
        runner._pending_recorded_verifies = [
            {"prompt": "is a toast visible", "between": [1, 2], "step_num": 1}
        ]
        _write_events(workdir["events_path"], [
            {"ts": _ts(0), "event": "step_start", "step_num": 1, "step_label": "1"},
            {"ts": _ts(5), "event": "step_start", "step_num": 2, "step_label": "2"},
        ])
        _make_frame(workdir["filtered_dir"], "frame_000001.png")
        _write_filtered_index(workdir["filtered_index_path"], [
            {"frame": "frame_000001.png", "ts": _ts(2)},
        ])
        artifacts = _FakeArtifacts(workdir["filtered_dir"], workdir["filtered_index_path"])
        resolve_pending_verifies(runner, workdir["events_path"], artifacts)
        runner._emit.assert_called_once()
        call_args = runner._emit.call_args
        assert call_args[0][0] == "verify_recorded_result"
        assert call_args[1]["step_num"] == 1
        assert call_args[1]["passed"] is True
