"""Tests for the agent-facing author CLI client."""

from __future__ import annotations

import json

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

