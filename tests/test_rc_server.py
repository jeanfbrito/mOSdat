"""Unit tests for ``mosdat server-provision`` / ``provision_server``.

No real Docker or network. Docker/HTTP helpers are patched on
``automation.commands.rc_server``. ``@pytest.mark.live`` covers a real
``develop`` provision and is skipped unless ``--live`` is passed.
"""

from __future__ import annotations

import argparse
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from automation.commands import rc_server as mod
from automation.commands.rc_server import (
    DAEMON_ERROR,
    DEFAULT_STARTUP_TIMEOUT_S,
    IMAGE_REPO,
    add_server_list_subparser,
    add_server_provision_subparser,
    add_server_teardown_subparser,
    list_instances,
    normalize_ref,
    project_name,
    provision_server,
    resolve_image,
    run_server_list,
    run_server_provision,
    run_server_teardown,
    teardown_server,
)
from automation.mcp_tools import (
    TOOL_DEFINITIONS,
    _server_list,
    _server_provision,
    _server_teardown,
)


# ---------------------------------------------------------------------------
# T003 / T004 — ref / image / project naming
# ---------------------------------------------------------------------------


def test_normalize_resolve_project_pr_number():
    assert normalize_ref("3464") == "pr-3464"
    assert resolve_image("3464") == f"{IMAGE_REPO}:pr-3464"
    assert project_name("3464") == "mosdat-rc-pr-3464"


@pytest.mark.parametrize("ref", ["pr-3464", "develop", "6.15.0"])
def test_normalize_passthrough(ref):
    assert normalize_ref(ref) == ref


def test_project_name_release_tag():
    assert project_name("6.15.0") == "mosdat-rc-6-15-0"
    assert project_name("pr-3464") == "mosdat-rc-pr-3464"


# ---------------------------------------------------------------------------
# T007 — mocked primitives
# ---------------------------------------------------------------------------


def test_is_no_image_error_manifest_unknown_not_auth():
    assert mod._is_no_image_error(
        "Error response from daemon: manifest unknown"
    ) is True
    assert mod._is_no_image_error(
        "manifest for ghcr.io/rocketchat/rocket.chat:pr-1 not found"
    ) is True
    assert mod._is_no_image_error(
        "unauthorized: authentication required"
    ) is False


def test_discover_host_port_parses_ipv4_and_ipv6(monkeypatch):
    monkeypatch.setattr(mod, "_run", lambda *a, **k: (0, "0.0.0.0:54217\n", ""))
    assert mod._discover_host_port("p", "rocketchat", 3000) == 54217
    monkeypatch.setattr(mod, "_run", lambda *a, **k: (0, "[::]:54218\n", ""))
    assert mod._discover_host_port("p", "rocketchat", 3000) == 54218
    monkeypatch.setattr(mod, "_run", lambda *a, **k: (1, "", "error"))
    assert mod._discover_host_port("p", "rocketchat", 3000) is None


def test_compose_down_passes_placeholder_env_for_substitution(monkeypatch):
    # Live-verified bug: `down` still needs ${RC_IMAGE}/${ROOT_URL}/${HOST_PORT}
    # to resolve to something non-empty to pass compose file validation, even
    # though it removes containers via existing project/service labels, not
    # these values — without them, real `docker compose down` fails with
    # "service 'rocketchat' has neither an image nor a build context".
    captured = {}

    def _fake_run(cmd, timeout, env=None):
        captured["env"] = env
        return 0, "", ""

    monkeypatch.setattr(mod, "_run", _fake_run)
    ok, detail = mod._compose_down("mosdat-rc-develop", mod.COMPOSE_TEMPLATE)
    assert ok is True
    assert detail == ""
    env = captured["env"]
    assert env is not None
    assert env["RC_IMAGE"]
    assert env["ROOT_URL"]
    assert env["HOST_PORT"]


def test_list_managed_projects_filters_and_unique(monkeypatch):
    payload = (
        '[{"Name":"mosdat-rc-pr-3464"},'
        '{"Name":"other"},'
        '{"name":"mosdat-rc-pr-3464"},'
        '{"Name":"mosdat-rc-develop"}]'
    )
    monkeypatch.setattr(mod, "_run", lambda *a, **k: (0, payload, ""))
    assert mod._list_managed_projects() == [
        "mosdat-rc-pr-3464",
        "mosdat-rc-develop",
    ]
    monkeypatch.setattr(mod, "_run", lambda *a, **k: (0, "not-json", ""))
    assert mod._list_managed_projects() == []


def test_probe_ready_200_and_exception(monkeypatch):
    class _Resp:
        status_code = 200

    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _Resp())
    assert mod.probe_ready("http://192.0.2.10:1") is True

    def _boom(*a, **k):
        raise mod.requests.RequestException("down")

    monkeypatch.setattr(mod.requests, "get", _boom)
    assert mod.probe_ready("http://192.0.2.10:1") is False


def test_docker_daemon_running_swallows_missing_binary(monkeypatch):
    def _missing(*a, **k):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(mod.subprocess, "run", _missing)
    assert mod._docker_daemon_running() is False


# ---------------------------------------------------------------------------
# T008 / T011 / T015 / T016 / T017 — provision_server paths
# ---------------------------------------------------------------------------


def _path_a_ok(monkeypatch, *, port=54217, probe=True):
    pull = MagicMock(return_value=(True, ""))
    compose = MagicMock(return_value=(True, ""))
    monkeypatch.setattr(mod, "_docker_daemon_running", lambda: True)
    monkeypatch.setattr(mod, "_list_managed_projects", lambda: [])
    monkeypatch.setattr(mod, "_docker_pull", pull)
    monkeypatch.setattr(mod, "_compose_up", compose)
    monkeypatch.setattr(mod, "_pick_free_port", lambda: port)
    monkeypatch.setattr(mod, "probe_ready", lambda *a, **k: probe)
    monkeypatch.setattr(mod, "_advertise_host", lambda: "192.0.2.10")
    return pull, compose


def test_provision_happy_path_ready(monkeypatch):
    _path_a_ok(monkeypatch, port=54217, probe=True)
    result = provision_server("pr-3464")
    assert result.state == "ready"
    assert result.url == "http://192.0.2.10:54217"
    assert result.error == ""
    assert result.ref == "pr-3464"
    assert isinstance(result.elapsed_ms, int)


def test_provision_compose_up_env_has_host_port_and_root_url(monkeypatch):
    pull, compose = _path_a_ok(monkeypatch, port=54217, probe=True)
    result = provision_server("pr-3464")
    assert result.state == "ready"
    compose.assert_called_once()
    _args, kwargs = compose.call_args
    call_args = compose.call_args[0]
    env = call_args[2] if len(call_args) > 2 else kwargs.get("env")
    assert env["HOST_PORT"] == "54217"
    assert env["ROOT_URL"] == "http://192.0.2.10:54217"
    assert env["ROOT_URL"] == result.url


def test_path_b_still_starting_does_not_pull_or_up(monkeypatch):
    pull = MagicMock()
    compose = MagicMock()
    monkeypatch.setattr(mod, "_docker_daemon_running", lambda: True)
    monkeypatch.setattr(
        mod, "_list_managed_projects", lambda: [project_name("pr-3464")],
    )
    monkeypatch.setattr(mod, "_docker_pull", pull)
    monkeypatch.setattr(mod, "_compose_up", compose)
    monkeypatch.setattr(mod, "_discover_host_port", lambda *a, **k: 54217)
    monkeypatch.setattr(mod, "probe_ready", lambda *a, **k: False)
    monkeypatch.setattr(mod, "_advertise_host", lambda: "192.0.2.10")
    result = provision_server("pr-3464")
    assert result.state == "starting"
    assert result.url == "http://192.0.2.10:54217"
    pull.assert_not_called()
    compose.assert_not_called()


def test_startup_timeout_path_a(monkeypatch):
    pull, compose = _path_a_ok(monkeypatch, port=54217, probe=False)
    clock = {"t": 0.0}

    def _monotonic():
        return clock["t"]

    def _sleep(s):
        clock["t"] += s

    monkeypatch.setattr(mod.time, "monotonic", _monotonic)
    monkeypatch.setattr(mod.time, "sleep", _sleep)
    result = provision_server("pr-3464", timeout=0.05)
    assert result.state == "failed"
    assert "timeout" in result.error or "livez" in result.error
    assert "did not become ready" in result.error
    compose.assert_called_once()
    pull.assert_called_once()


def test_daemon_not_running_skips_pull_and_up(monkeypatch):
    pull = MagicMock()
    compose = MagicMock()
    monkeypatch.setattr(mod, "_docker_daemon_running", lambda: False)
    monkeypatch.setattr(mod, "_docker_pull", pull)
    monkeypatch.setattr(mod, "_compose_up", compose)
    result = provision_server("develop")
    assert result.state == "failed"
    assert result.error == DAEMON_ERROR
    assert "Docker daemon is not running — start Docker Desktop first" in result.error
    pull.assert_not_called()
    compose.assert_not_called()


def test_no_image_fr002_skips_compose(monkeypatch):
    compose = MagicMock()
    monkeypatch.setattr(mod, "_docker_daemon_running", lambda: True)
    monkeypatch.setattr(mod, "_list_managed_projects", lambda: [])
    monkeypatch.setattr(
        mod,
        "_docker_pull",
        lambda image: (False, "Error response from daemon: manifest unknown"),
    )
    monkeypatch.setattr(mod, "_compose_up", compose)
    result = provision_server("pr-9999")
    assert result.state == "failed"
    assert "no published server image for 'pr-9999'" in result.error
    assert "cannot be tested this way" in result.error
    compose.assert_not_called()


def test_other_pull_error_distinct_from_no_image(monkeypatch):
    compose = MagicMock()
    monkeypatch.setattr(mod, "_docker_daemon_running", lambda: True)
    monkeypatch.setattr(mod, "_list_managed_projects", lambda: [])
    monkeypatch.setattr(
        mod,
        "_docker_pull",
        lambda image: (False, "unauthorized: authentication required"),
    )
    monkeypatch.setattr(mod, "_compose_up", compose)
    result = provision_server("develop")
    assert result.state == "failed"
    assert "no published server image" not in result.error
    assert "failed to pull" in result.error
    assert "ghcr.io/rocketchat/rocket.chat:develop" in result.error
    compose.assert_not_called()


def test_two_refs_get_distinct_urls(monkeypatch):
    monkeypatch.setattr(mod, "_docker_daemon_running", lambda: True)
    monkeypatch.setattr(mod, "_list_managed_projects", lambda: [])
    monkeypatch.setattr(mod, "_docker_pull", lambda image: (True, ""))
    monkeypatch.setattr(mod, "_compose_up", lambda *a, **k: (True, ""))
    monkeypatch.setattr(
        mod, "_pick_free_port", MagicMock(side_effect=[54217, 54218]),
    )
    monkeypatch.setattr(mod, "probe_ready", lambda *a, **k: True)
    monkeypatch.setattr(mod, "_advertise_host", lambda: "192.0.2.10")
    a = provision_server("pr-3464")
    b = provision_server("develop")
    assert a.state == "ready" and b.state == "ready"
    assert a.url == "http://192.0.2.10:54217"
    assert b.url == "http://192.0.2.10:54218"
    assert a.url != b.url


def test_dry_run_skips_docker(monkeypatch):
    daemon = MagicMock()
    pull = MagicMock()
    compose = MagicMock()
    monkeypatch.setattr(mod, "_docker_daemon_running", daemon)
    monkeypatch.setattr(mod, "_docker_pull", pull)
    monkeypatch.setattr(mod, "_compose_up", compose)
    result = provision_server("develop", dry_run=True)
    assert result.state == "starting"
    assert result.url is None
    daemon.assert_not_called()
    pull.assert_not_called()
    compose.assert_not_called()


def test_empty_ref_is_invalid():
    result = provision_server("   ")
    assert result.state == "failed"
    assert "invalid" in result.error.lower()


# ---------------------------------------------------------------------------
# CLI + MCP wiring
# ---------------------------------------------------------------------------


def test_add_server_provision_subparser():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    add_server_provision_subparser(sub)
    args = parser.parse_args(["server-provision", "3464", "--dry-run"])
    assert args.command == "server-provision"
    assert args.ref == "3464"
    assert args.dry_run is True
    assert args.timeout == DEFAULT_STARTUP_TIMEOUT_S


def test_run_server_provision_exit_codes(monkeypatch):
    monkeypatch.setattr(
        mod,
        "provision_server",
        lambda *a, **k: mod.ProvisionResult(
            ref="develop", state="ready", url="http://192.0.2.10:1",
        ),
    )
    assert run_server_provision(SimpleNamespace(
        ref="develop", dry_run=False, timeout=180,
    )) == 0

    monkeypatch.setattr(
        mod,
        "provision_server",
        lambda *a, **k: mod.ProvisionResult(
            ref="pr-1", state="failed",
            error=mod._no_image_error("pr-1"),
        ),
    )
    assert run_server_provision(SimpleNamespace(
        ref="pr-1", dry_run=False, timeout=180,
    )) == 1

    monkeypatch.setattr(
        mod,
        "provision_server",
        lambda *a, **k: mod.ProvisionResult(
            ref="develop", state="failed", error=DAEMON_ERROR,
        ),
    )
    assert run_server_provision(SimpleNamespace(
        ref="develop", dry_run=False, timeout=180,
    )) == 5


def test_server_provision_mcp_envelope(monkeypatch):
    monkeypatch.setattr(
        "automation.commands.rc_server.provision_server",
        lambda *a, **k: mod.ProvisionResult(
            ref="develop",
            state="ready",
            url="http://192.0.2.10:54217",
            elapsed_ms=12,
        ),
    )
    result = _server_provision({"ref": "develop"})
    for key in ("ok", "error", "degraded", "ref", "state", "url", "elapsed_ms"):
        assert key in result
    assert result["ok"] is True
    assert result["error"] is None
    assert result["degraded"] == []
    assert result["ref"] == "develop"
    assert result["state"] == "ready"
    assert result["url"] == "http://192.0.2.10:54217"
    assert result["elapsed_ms"] == 12


def test_server_provision_registered_in_tool_definitions_and_dispatch():
    names = [t["name"] for t in TOOL_DEFINITIONS]
    assert "mosdat_server_provision" in names
    from automation import mcp_server

    assert "mosdat_server_provision" in inspect.getsource(_server_provision)
    assert "mosdat_server_provision" in inspect.getsource(mcp_server.handle_tools_call)
    assert "_server_provision" in inspect.getsource(mcp_server.handle_tools_call)


# ---------------------------------------------------------------------------
# T019-T022 — teardown_server / list_instances
# ---------------------------------------------------------------------------


def test_teardown_existing_instance_torn_down_true(monkeypatch):
    monkeypatch.setattr(mod, "_docker_daemon_running", lambda: True)
    monkeypatch.setattr(mod, "_list_managed_projects", lambda: ["mosdat-rc-pr-3464"])
    compose_down = MagicMock(return_value=(True, ""))
    monkeypatch.setattr(mod, "_compose_down", compose_down)

    result = teardown_server("3464")

    assert result.ref == "pr-3464"
    assert result.torn_down is True
    assert result.error == ""
    compose_down.assert_called_once_with("mosdat-rc-pr-3464", mod.COMPOSE_TEMPLATE)


def test_teardown_nonexistent_reference_not_an_error(monkeypatch):
    monkeypatch.setattr(mod, "_docker_daemon_running", lambda: True)
    monkeypatch.setattr(mod, "_list_managed_projects", lambda: [])
    compose_down = MagicMock(return_value=(True, ""))
    monkeypatch.setattr(mod, "_compose_down", compose_down)

    result = teardown_server("pr-9999")

    assert result.ref == "pr-9999"
    assert result.torn_down is False
    assert result.error == ""
    compose_down.assert_not_called()


def test_teardown_compose_down_failure_is_a_real_error(monkeypatch):
    monkeypatch.setattr(mod, "_docker_daemon_running", lambda: True)
    monkeypatch.setattr(mod, "_list_managed_projects", lambda: ["mosdat-rc-pr-3464"])
    monkeypatch.setattr(mod, "_compose_down", lambda *a, **k: (False, "boom"))

    result = teardown_server("3464")

    assert result.torn_down is False
    assert "boom" in result.error


def test_teardown_daemon_not_running(monkeypatch):
    monkeypatch.setattr(mod, "_docker_daemon_running", lambda: False)
    compose_down = MagicMock()
    monkeypatch.setattr(mod, "_compose_down", compose_down)

    result = teardown_server("develop")

    assert result.torn_down is False
    assert result.error == DAEMON_ERROR
    compose_down.assert_not_called()


def test_list_instances_zero_one_multiple(monkeypatch):
    monkeypatch.setattr(mod, "_docker_daemon_running", lambda: True)
    monkeypatch.setattr(mod, "_list_managed_projects", lambda: [])
    assert list_instances() == []

    monkeypatch.setattr(mod, "_list_managed_projects", lambda: ["mosdat-rc-develop"])
    monkeypatch.setattr(mod, "_discover_host_port", lambda *a, **k: 54217)
    monkeypatch.setattr(mod, "_advertise_host", lambda: "192.168.13.20")
    monkeypatch.setattr(mod, "probe_ready", lambda url, timeout=5: True)
    instances = list_instances()
    assert len(instances) == 1
    assert instances[0].ref == "develop"
    assert instances[0].state == "ready"
    assert instances[0].url == "http://192.168.13.20:54217"

    monkeypatch.setattr(
        mod,
        "_list_managed_projects",
        lambda: ["mosdat-rc-develop", "mosdat-rc-pr-3464"],
    )
    monkeypatch.setattr(mod, "probe_ready", lambda url, timeout=5: False)
    instances = list_instances()
    assert len(instances) == 2
    refs = {i.ref for i in instances}
    assert refs == {"develop", "pr-3464"}
    assert all(i.state == "starting" for i in instances)


def test_list_instances_daemon_not_running(monkeypatch):
    monkeypatch.setattr(mod, "_docker_daemon_running", lambda: False)
    with pytest.raises(RuntimeError, match=DAEMON_ERROR):
        list_instances()


# ---------------------------------------------------------------------------
# CLI + MCP wiring — teardown / list
# ---------------------------------------------------------------------------


def test_add_server_teardown_subparser():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    add_server_teardown_subparser(sub)
    args = parser.parse_args(["server-teardown", "3464"])
    assert args.command == "server-teardown"
    assert args.ref == "3464"


def test_add_server_list_subparser():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    add_server_list_subparser(sub)
    args = parser.parse_args(["server-list"])
    assert args.command == "server-list"


def test_run_server_teardown_exit_codes(monkeypatch):
    monkeypatch.setattr(
        mod, "teardown_server",
        lambda ref: mod.TeardownResult(ref="develop", torn_down=True),
    )
    assert run_server_teardown(SimpleNamespace(ref="develop")) == 0

    monkeypatch.setattr(
        mod, "teardown_server",
        lambda ref: mod.TeardownResult(ref="pr-1", torn_down=False),
    )
    assert run_server_teardown(SimpleNamespace(ref="pr-1")) == 0

    monkeypatch.setattr(
        mod, "teardown_server",
        lambda ref: mod.TeardownResult(ref="develop", torn_down=False, error=DAEMON_ERROR),
    )
    assert run_server_teardown(SimpleNamespace(ref="develop")) == 5

    monkeypatch.setattr(
        mod, "teardown_server",
        lambda ref: mod.TeardownResult(ref="develop", torn_down=False, error="compose down failed"),
    )
    assert run_server_teardown(SimpleNamespace(ref="develop")) == 2

    assert run_server_teardown(SimpleNamespace(ref="")) == 5


def test_run_server_list_exit_codes(monkeypatch):
    monkeypatch.setattr(mod, "list_instances", lambda: [])
    assert run_server_list(SimpleNamespace()) == 0

    monkeypatch.setattr(
        mod, "list_instances",
        lambda: [mod.ListInstance(ref="develop", state="ready", url="http://x:1")],
    )
    assert run_server_list(SimpleNamespace()) == 0

    def _raise():
        raise RuntimeError(DAEMON_ERROR)

    monkeypatch.setattr(mod, "list_instances", _raise)
    assert run_server_list(SimpleNamespace()) == 5


def test_server_teardown_mcp_envelope(monkeypatch):
    monkeypatch.setattr(
        "automation.commands.rc_server.teardown_server",
        lambda ref: mod.TeardownResult(ref="develop", torn_down=True),
    )
    result = _server_teardown({"ref": "develop"})
    for key in ("ok", "error", "degraded", "ref", "torn_down"):
        assert key in result
    assert result["ok"] is True
    assert result["error"] is None
    assert result["ref"] == "develop"
    assert result["torn_down"] is True

    monkeypatch.setattr(
        "automation.commands.rc_server.teardown_server",
        lambda ref: mod.TeardownResult(ref="develop", torn_down=False),
    )
    result = _server_teardown({"ref": "develop"})
    assert result["ok"] is True
    assert result["torn_down"] is False

    result = _server_teardown({})
    assert result["ok"] is False


def test_server_list_mcp_envelope(monkeypatch):
    monkeypatch.setattr(
        "automation.commands.rc_server.list_instances",
        lambda: [
            mod.ListInstance(ref="pr-3464", state="ready", url="http://192.168.13.20:54217", elapsed_ms=0),
        ],
    )
    result = _server_list({})
    for key in ("ok", "error", "degraded", "instances"):
        assert key in result
    assert result["ok"] is True
    assert result["error"] is None
    assert result["instances"] == [
        {"ref": "pr-3464", "state": "ready", "url": "http://192.168.13.20:54217", "elapsed_ms": 0},
    ]

    def _raise():
        raise RuntimeError(DAEMON_ERROR)

    monkeypatch.setattr("automation.commands.rc_server.list_instances", _raise)
    result = _server_list({})
    assert result["ok"] is False
    assert result["error"] == DAEMON_ERROR


def test_server_teardown_list_registered_in_tool_definitions_and_dispatch():
    names = [t["name"] for t in TOOL_DEFINITIONS]
    assert "mosdat_server_teardown" in names
    assert "mosdat_server_list" in names
    from automation import mcp_server

    assert "mosdat_server_teardown" in inspect.getsource(mcp_server.handle_tools_call)
    assert "_server_teardown" in inspect.getsource(mcp_server.handle_tools_call)
    assert "mosdat_server_list" in inspect.getsource(mcp_server.handle_tools_call)
    assert "_server_list" in inspect.getsource(mcp_server.handle_tools_call)


@pytest.mark.live
@pytest.mark.timeout(600)
def test_live_provision_develop_livez_answers():
    """quickstart.md Step 1: provision develop against real Docker."""
    from automation.commands.rc_server import (
        _compose_down, _docker_daemon_running, COMPOSE_TEMPLATE,
        probe_ready, project_name, provision_server,
    )
    if not _docker_daemon_running():
        pytest.skip("Docker daemon is not running")
    ref = "develop"
    result = provision_server(ref, dry_run=False, timeout=180)
    try:
        assert result.state == "ready", result.error
        assert result.url
        assert probe_ready(result.url) is True
    finally:
        _compose_down(project_name(ref), COMPOSE_TEMPLATE)
