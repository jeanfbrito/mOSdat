"""Tests for mosdat replay (I5).

Covers:
- _load_events: reads events.jsonl correctly
- _available_steps: extracts step numbers + first questions
- _find_screenshots: path discovery, last-only vs all-attempts
- run_replay: prompt forwarding, VLM mock, exit codes
"""

from __future__ import annotations

# Clear sibling-test stub pollution BEFORE importing automation.*.
# test_negative et al. stub PIL / openai / automation.vlm.* / automation.transport.*
# at module import time during pytest collection.
import sys as _sys
for _name in list(_sys.modules):
    if (
        _name.startswith("automation.vlm")
        or _name.startswith("automation.transport")
        or _name in (
            "openai",
            "httpx",
            "automation.config",
            "automation.commands.replay",
            "automation.proxmox.api",
            "automation.proxmox.vm",
            "automation.reporting.report",
        )
    ):
        _sys.modules.pop(_name, None)

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from automation.commands.replay import (
    _available_steps,
    _find_screenshots,
    _load_events,
    run_replay,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _write_events(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def _make_result_dir(tmp_path: Path, events: list[dict], screenshots: list[str] | None = None) -> Path:
    result_dir = tmp_path / "ubuntu2204"
    result_dir.mkdir()
    _write_events(result_dir / "events.jsonl", events)
    for name in (screenshots or []):
        (result_dir / name).write_bytes(b"PNG_PLACEHOLDER")
    return result_dir


SAMPLE_EVENTS = [
    {"event": "step_start", "step_num": 5, "label": "", "kind": "verify"},
    {"event": "vlm_verify", "step_num": 5, "attempt": 1, "question": "First question step 5", "answer": "yes", "latency_ms": 100, "kind": "verify"},
    {"event": "step_end", "step_num": 5, "status": "ok"},
    {"event": "step_start", "step_num": 8, "label": "", "kind": "key"},
    {"event": "vlm_verify", "step_num": 8, "attempt": 1, "question": "Modal is visible", "answer": "yes", "latency_ms": 200, "kind": "verify"},
    {"event": "vlm_verify", "step_num": 8, "attempt": 2, "question": "Modal is visible", "answer": "no", "latency_ms": 150, "kind": "verify"},
    {"event": "step_end", "step_num": 8, "status": "ok"},
]


# ── Unit: _load_events ────────────────────────────────────────────────────────

def test_load_events_reads_all_lines(tmp_path):
    result_dir = tmp_path / "run"
    result_dir.mkdir()
    _write_events(result_dir / "events.jsonl", SAMPLE_EVENTS)
    events = _load_events(result_dir)
    assert len(events) == len(SAMPLE_EVENTS)
    assert events[0]["event"] == "step_start"


def test_load_events_missing_file_raises(tmp_path):
    result_dir = tmp_path / "run"
    result_dir.mkdir()
    with pytest.raises(FileNotFoundError):
        _load_events(result_dir)


# ── Unit: _available_steps ────────────────────────────────────────────────────

def test_available_steps_deduplicates_and_sorts():
    steps = _available_steps(SAMPLE_EVENTS)
    nums = [s["step_num"] for s in steps]
    assert nums == [5, 8]


def test_available_steps_first_question_only():
    steps = _available_steps(SAMPLE_EVENTS)
    step8 = next(s for s in steps if s["step_num"] == 8)
    # Should be the question from the first vlm_verify event for step 8
    assert step8["question"] == "Modal is visible"


def test_available_steps_empty_when_no_verify_events():
    events = [{"event": "step_start", "step_num": 1, "kind": "shell"}]
    assert _available_steps(events) == []


# ── Unit: _find_screenshots ───────────────────────────────────────────────────

def test_find_screenshots_returns_last_by_default(tmp_path):
    result_dir = tmp_path / "run"
    result_dir.mkdir()
    names = [
        "190233_step8_verify_poll.png",
        "190306_step8_verify_poll.png",
        "190350_step8_verify_poll.png",
    ]
    for n in names:
        (result_dir / n).touch()
    found = _find_screenshots(result_dir, step_num=8, all_attempts=False)
    assert len(found) == 1
    assert found[0].name == "190350_step8_verify_poll.png"


def test_find_screenshots_all_attempts_returns_all(tmp_path):
    result_dir = tmp_path / "run"
    result_dir.mkdir()
    names = [
        "190233_step8_verify_poll.png",
        "190306_step8_verify_poll.png",
    ]
    for n in names:
        (result_dir / n).touch()
    found = _find_screenshots(result_dir, step_num=8, all_attempts=True)
    assert len(found) == 2


def test_find_screenshots_no_match_returns_empty(tmp_path):
    result_dir = tmp_path / "run"
    result_dir.mkdir()
    (result_dir / "190233_step5_verify_poll.png").touch()
    found = _find_screenshots(result_dir, step_num=8, all_attempts=False)
    assert found == []


def test_find_screenshots_ignores_verified_files(tmp_path):
    """Files named *_verified.png must NOT be selected — only *_verify_poll.png."""
    result_dir = tmp_path / "run"
    result_dir.mkdir()
    (result_dir / "190233_step8_verified.png").touch()
    (result_dir / "190306_step8_verify_poll.png").touch()
    found = _find_screenshots(result_dir, step_num=8, all_attempts=True)
    assert len(found) == 1
    assert found[0].name == "190306_step8_verify_poll.png"


# ── Integration: run_replay ────────────────────────────────────────────────────

def _make_args(result_dir, step=None, verify=None, model=None, all_attempts=False):
    return SimpleNamespace(
        result_dir=str(result_dir),
        step=step,
        verify=verify,
        model=model,
        all_attempts=all_attempts,
    )


def _make_fake_png(tmp_path: Path, name: str) -> Path:
    """Write a minimal valid 1x1 white PNG."""
    import struct, zlib
    def _chunk(name_bytes, data):
        c = struct.pack(">I", len(data)) + name_bytes + data
        return c + struct.pack(">I", zlib.crc32(name_bytes + data) & 0xffffffff)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
        + _chunk(b"IEND", b"")
    )
    p = tmp_path / name
    p.write_bytes(png)
    return p


@patch.dict("os.environ", {"VLM_BASE_URL": "http://fake-vlm/v1"})
def test_run_replay_yes_verdict_exits_0(tmp_path):
    result_dir = _make_result_dir(tmp_path, SAMPLE_EVENTS)
    _make_fake_png(result_dir, "190306_step8_verify_poll.png")

    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = "yes"

    with patch("automation.commands.replay._build_vlm") as mock_build:
        mock_vlm = MagicMock()
        mock_vlm.verify_model = "test-model"
        mock_vlm._call_with_failover.return_value = mock_resp
        mock_build.return_value = mock_vlm

        args = _make_args(result_dir, step=8, verify="Modal is visible")
        rc = run_replay(args)

    assert rc == 0


@patch.dict("os.environ", {"VLM_BASE_URL": "http://fake-vlm/v1"})
def test_run_replay_no_verdict_exits_1(tmp_path):
    result_dir = _make_result_dir(tmp_path, SAMPLE_EVENTS)
    _make_fake_png(result_dir, "190306_step8_verify_poll.png")

    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = "no, the modal is not visible"

    with patch("automation.commands.replay._build_vlm") as mock_build:
        mock_vlm = MagicMock()
        mock_vlm.verify_model = "test-model"
        mock_vlm._call_with_failover.return_value = mock_resp
        mock_build.return_value = mock_vlm

        args = _make_args(result_dir, step=8, verify="Modal is visible")
        rc = run_replay(args)

    assert rc == 1


@patch.dict("os.environ", {"VLM_BASE_URL": "http://fake-vlm/v1"})
def test_run_replay_uses_supplied_prompt(tmp_path):
    result_dir = _make_result_dir(tmp_path, SAMPLE_EVENTS)
    _make_fake_png(result_dir, "190306_step8_verify_poll.png")

    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = "yes"

    captured_calls = []

    def fake_call(method, **kwargs):
        captured_calls.append(kwargs)
        return mock_resp

    with patch("automation.commands.replay._build_vlm") as mock_build:
        mock_vlm = MagicMock()
        mock_vlm.verify_model = "test-model"
        mock_vlm._call_with_failover.side_effect = fake_call
        mock_build.return_value = mock_vlm

        new_prompt = "new custom prompt for iteration"
        args = _make_args(result_dir, step=8, verify=new_prompt)
        run_replay(args)

    assert captured_calls, "VLM was never called"
    # The prompt must appear in the messages content text
    messages = captured_calls[0]["messages"]
    text_parts = [p["text"] for m in messages for p in m.get("content", []) if p.get("type") == "text"]
    assert any(new_prompt in t for t in text_parts)


@patch.dict("os.environ", {"VLM_BASE_URL": "http://fake-vlm/v1"})
def test_run_replay_falls_back_to_original_question(tmp_path):
    """When --verify is omitted, the prompt from events.jsonl is used."""
    result_dir = _make_result_dir(tmp_path, SAMPLE_EVENTS)
    _make_fake_png(result_dir, "190306_step8_verify_poll.png")

    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = "yes"

    captured_calls = []

    def fake_call(method, **kwargs):
        captured_calls.append(kwargs)
        return mock_resp

    with patch("automation.commands.replay._build_vlm") as mock_build:
        mock_vlm = MagicMock()
        mock_vlm.verify_model = "test-model"
        mock_vlm._call_with_failover.side_effect = fake_call
        mock_build.return_value = mock_vlm

        args = _make_args(result_dir, step=8, verify=None)
        rc = run_replay(args)

    assert rc == 0
    messages = captured_calls[0]["messages"]
    text_parts = [p["text"] for m in messages for p in m.get("content", []) if p.get("type") == "text"]
    assert any("Modal is visible" in t for t in text_parts)


def test_run_replay_no_step_lists_steps(tmp_path, capsys):
    result_dir = _make_result_dir(tmp_path, SAMPLE_EVENTS)
    args = _make_args(result_dir, step=None)
    rc = run_replay(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "5" in out
    assert "8" in out
    assert "First question step 5" in out


def test_run_replay_missing_result_dir_exits_2(tmp_path):
    args = _make_args(tmp_path / "nonexistent")
    rc = run_replay(args)
    assert rc == 2


def test_run_replay_missing_screenshot_exits_2(tmp_path):
    result_dir = _make_result_dir(tmp_path, SAMPLE_EVENTS)
    # No screenshot files written
    with patch.dict("os.environ", {"VLM_BASE_URL": "http://fake-vlm/v1"}):
        args = _make_args(result_dir, step=8, verify="something")
        rc = run_replay(args)
    assert rc == 2


def test_run_replay_missing_vlm_url_exits_2(tmp_path):
    result_dir = _make_result_dir(tmp_path, SAMPLE_EVENTS)
    (result_dir / "190306_step8_verify_poll.png").touch()
    with patch.dict("os.environ", {}, clear=True):
        # Ensure VLM_BASE_URL is absent
        import os
        os.environ.pop("VLM_BASE_URL", None)
        args = _make_args(result_dir, step=8, verify="something")
        rc = run_replay(args)
    assert rc == 2


@patch.dict("os.environ", {"VLM_BASE_URL": "http://fake-vlm/v1"})
def test_run_replay_all_attempts_calls_vlm_per_screenshot(tmp_path):
    result_dir = _make_result_dir(tmp_path, SAMPLE_EVENTS)
    _make_fake_png(result_dir, "190233_step8_verify_poll.png")
    _make_fake_png(result_dir, "190306_step8_verify_poll.png")

    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = "yes"

    with patch("automation.commands.replay._build_vlm") as mock_build:
        mock_vlm = MagicMock()
        mock_vlm.verify_model = "test-model"
        mock_vlm._call_with_failover.return_value = mock_resp
        mock_build.return_value = mock_vlm

        args = _make_args(result_dir, step=8, verify="Modal", all_attempts=True)
        rc = run_replay(args)

    assert rc == 0
    assert mock_vlm._call_with_failover.call_count == 2
