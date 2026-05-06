"""Tests for the agent-facing author CLI client."""

from __future__ import annotations

import json
import pytest

from automation import author_cli


def test_author_cli_start_posts_session(monkeypatch, capsys):
    calls = []

    def fake_request(base_url, method, path, payload=None, query=None):
        calls.append((base_url, method, path, payload, query))
        return {"session_id": "session-1", "vm": "ubuntu2404"}

    monkeypatch.setattr(author_cli, "_request_json", fake_request)

    status = author_cli.cli([
        "--url", "http://dash:8082",
        "start",
        "--vm", "ubuntu2404",
    ])

    assert status == 0
    assert calls == [("http://dash:8082", "POST", "/api/author/session", {"vm": "ubuntu2404"}, None)]
    assert json.loads(capsys.readouterr().out)["session_id"] == "session-1"


def test_author_cli_action_merges_payload(monkeypatch, capsys):
    calls = []

    def fake_request(base_url, method, path, payload=None, query=None):
        calls.append((base_url, method, path, payload, query))
        return {"action": "click"}

    monkeypatch.setattr(author_cli, "_request_json", fake_request)

    status = author_cli.cli([
        "action",
        "--session", "session-1",
        "--kind", "click",
        "--json", '{"x":5,"y":6,"button":"right"}',
    ])

    assert status == 0
    assert calls[0][1:4] == (
        "POST",
        "/api/author/action",
        {"x": 5, "y": 6, "button": "right", "session_id": "session-1", "action": "click", "confirm": True},
    )
    assert json.loads(capsys.readouterr().out)["action"] == "click"


def test_author_cli_click_posts_typed_action(monkeypatch, capsys):
    calls = []

    def fake_request(base_url, method, path, payload=None, query=None):
        calls.append((base_url, method, path, payload, query))
        return {"action": "click"}

    monkeypatch.setattr(author_cli, "_request_json", fake_request)

    status = author_cli.cli([
        "click",
        "--session", "session-1",
        "--x", "5",
        "--y", "6",
        "--button", "right",
        "--prompt", "context menu",
    ])

    assert status == 0
    assert calls[0][1:4] == (
        "POST",
        "/api/author/action",
        {"x": 5, "y": 6, "button": "right", "prompt": "context menu", "session_id": "session-1", "action": "click", "confirm": True},
    )
    assert json.loads(capsys.readouterr().out)["action"] == "click"


def test_author_cli_type_posts_typed_action(monkeypatch, capsys):
    calls = []

    def fake_request(base_url, method, path, payload=None, query=None):
        calls.append((base_url, method, path, payload, query))
        return {"action": "type"}

    monkeypatch.setattr(author_cli, "_request_json", fake_request)

    status = author_cli.cli(["type", "--session", "session-1", "--text", "hello"])

    assert status == 0
    assert calls[0][1:4] == (
        "POST",
        "/api/author/action",
        {"text": "hello", "session_id": "session-1", "action": "type", "confirm": True},
    )
    assert json.loads(capsys.readouterr().out)["action"] == "type"


def test_author_cli_launch_posts_typed_action(monkeypatch, capsys):
    calls = []

    def fake_request(base_url, method, path, payload=None, query=None):
        calls.append((base_url, method, path, payload, query))
        return {"action": "launch"}

    monkeypatch.setattr(author_cli, "_request_json", fake_request)

    status = author_cli.cli(["launch", "--session", "session-1", "--cmd", "rocketchat", "--wait", "5"])

    assert status == 0
    assert calls[0][1:4] == (
        "POST",
        "/api/author/action",
        {"cmd": "rocketchat", "wait": 5, "session_id": "session-1", "action": "launch", "confirm": True},
    )
    assert json.loads(capsys.readouterr().out)["action"] == "launch"


def test_author_cli_export_prints_yaml_json(monkeypatch, capsys):
    calls = []

    def fake_request(base_url, path, query=None):
        calls.append((base_url, path, query))
        return {"yaml": "name: demo\nsteps: []\n"}

    monkeypatch.setattr(author_cli, "_request_text", fake_request)

    status = author_cli.cli(["export", "--session", "session-1", "--name", "demo"])

    assert status == 0
    assert calls == [("http://127.0.0.1:8080", "/api/author/export", {"session": "session-1", "name": "demo"})]
    assert json.loads(capsys.readouterr().out)["yaml"].startswith("name: demo")


def test_author_cli_step_appends_step(monkeypatch, capsys):
    calls = []

    def fake_request(base_url, method, path, payload=None, query=None):
        calls.append((base_url, method, path, payload, query))
        return {"draft_steps": [payload["step"]]}

    monkeypatch.setattr(author_cli, "_request_json", fake_request)

    status = author_cli.cli(["step", "--session", "session-1", "--json", '{"key":"escape"}'])

    assert status == 0
    assert calls == [
        (
            "http://127.0.0.1:8080",
            "POST",
            "/api/author/step",
            {"session_id": "session-1", "step": {"key": "escape"}},
            None,
        )
    ]
    assert json.loads(capsys.readouterr().out)["draft_steps"] == [{"key": "escape"}]


def test_author_cli_step_replaces_steps(monkeypatch, capsys):
    calls = []

    def fake_request(base_url, method, path, payload=None, query=None):
        calls.append((base_url, method, path, payload, query))
        return {"draft_steps": payload["steps"]}

    monkeypatch.setattr(author_cli, "_request_json", fake_request)

    status = author_cli.cli(["step", "--session", "session-1", "--steps-json", '[{"key":"escape"},{"wait":1}]'])

    assert status == 0
    assert calls == [
        (
            "http://127.0.0.1:8080",
            "POST",
            "/api/author/step",
            {"session_id": "session-1", "steps": [{"key": "escape"}, {"wait": 1}]},
            None,
        )
    ]
    assert json.loads(capsys.readouterr().out)["draft_steps"] == [{"key": "escape"}, {"wait": 1}]


def test_author_cli_close_posts_session(monkeypatch, capsys):
    calls = []

    def fake_request(base_url, method, path, payload=None, query=None):
        calls.append((base_url, method, path, payload, query))
        return {"closed": "session-1"}

    monkeypatch.setattr(author_cli, "_request_json", fake_request)

    status = author_cli.cli(["close", "--session", "session-1"])

    assert status == 0
    assert calls == [
        (
            "http://127.0.0.1:8080",
            "POST",
            "/api/author/close",
            {"session_id": "session-1"},
            None,
        )
    ]
    assert json.loads(capsys.readouterr().out)["closed"] == "session-1"


def test_author_cli_action_rejects_invalid_json(capsys):
    with pytest.raises(SystemExit) as exc:
        author_cli.cli(["action", "--session", "session-1", "--kind", "click", "--json", "not-json"])

    assert exc.value.code == 2
    assert "invalid JSON" in capsys.readouterr().err

