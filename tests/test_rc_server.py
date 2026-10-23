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
    monkeypatch.setattr(mod, "_service_running", lambda project, service: False)
    result = provision_server("pr-3464", timeout=0.05)
    assert result.state == "failed"
    assert "did not become ready" in result.error
    assert "not running" in result.error
    compose.assert_called_once()
    pull.assert_called_once()

    # Same timeout with the container still booting: "starting", not "failed".
    monkeypatch.setattr(mod, "_service_running", lambda project, service: True)
    monkeypatch.setattr(mod, "_list_managed_projects", lambda: [])
    result = provision_server("pr-3464", timeout=0.05)
    assert result.state == "starting"
    assert result.url
    assert result.error == ""


def test_daemon_not_running_skips_pull_and_up(monkeypatch):
    pull = MagicMock()
    compose = MagicMock()
    monkeypatch.setattr(mod, "_docker_daemon_running", lambda: False)
    monkeypatch.setattr(mod, "_docker_pull", pull)
    monkeypatch.setattr(mod, "_compose_up", compose)
    result = provision_server("develop", start_docker=False)
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


def test_startup_budget_starts_after_compose_up(monkeypatch):
    # A slow image pull must not use up the /livez budget: timeout covers polling.
    clock = {"t": 0.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(
        mod.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s),
    )
    _path_a_ok(monkeypatch, probe=False)

    def _slow_pull(image):
        clock["t"] += 200
        return True, ""

    monkeypatch.setattr(mod, "_docker_pull", _slow_pull)
    probes = iter([False, False, True])
    monkeypatch.setattr(mod, "probe_ready", lambda *a, **k: next(probes))
    result = provision_server("pr-3464", timeout=180)
    assert result.state == "ready"


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


# ---------------------------------------------------------------------------
# D1 — ensure_docker / start_docker
# ---------------------------------------------------------------------------


class _FakeClock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def _patch_docker_timeline(monkeypatch, *, up_at, run_rc=0):
    """Fake clock; the daemon answers from ``up_at`` on (None = never).

    Records every ``_run`` command. ``run_rc`` is the exit code of each command.
    """
    clock = _FakeClock()
    calls = []
    monkeypatch.setattr(mod.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(mod.time, "sleep", clock.sleep)
    monkeypatch.setattr(mod.sys, "platform", "darwin")
    monkeypatch.setattr(
        mod,
        "_docker_daemon_running",
        lambda: up_at is not None and clock.t >= up_at,
    )

    def _fake_run(cmd, timeout, env=None):
        calls.append(cmd)
        return run_rc, "", ""

    monkeypatch.setattr(mod, "_run", _fake_run)
    return clock, calls


OPEN_DOCKER = ["open", "-g", "-a", "Docker"]
QUIT_DOCKER = ["osascript", "-e", 'quit app "Docker"']
PKILL_DOCKER = ["pkill", "-f", "com.docker.backend"]


def test_ensure_docker_already_running_is_noop(monkeypatch):
    _clock, calls = _patch_docker_timeline(monkeypatch, up_at=0.0)
    assert mod.ensure_docker() == (True, "")
    assert calls == []


def test_ensure_docker_starts_on_first_try(monkeypatch):
    clock, calls = _patch_docker_timeline(monkeypatch, up_at=10.0)
    assert mod.ensure_docker(timeout=120) == (True, "")
    assert calls == [OPEN_DOCKER]
    assert clock.t == 12.0  # polls at 0, 3, 6, 9, 12; up at 10


def test_ensure_docker_quits_and_restarts_when_vm_hangs(monkeypatch):
    # Daemon stays down through the first half of the budget (60 s), then
    # answers 1 s into the wait after the restart (t=65 + 3 + 3 = 71).
    _clock, calls = _patch_docker_timeline(monkeypatch, up_at=70.0)
    assert mod.ensure_docker(timeout=120) == (True, "")
    assert calls == [OPEN_DOCKER, QUIT_DOCKER, PKILL_DOCKER, OPEN_DOCKER]


def test_ensure_docker_gives_up_after_restart(monkeypatch):
    clock, calls = _patch_docker_timeline(monkeypatch, up_at=None)
    ok, detail = mod.ensure_docker(timeout=120)
    assert ok is False
    assert detail.startswith(DAEMON_ERROR)
    assert "auto-start tried:" in detail
    assert "opened Docker, no daemon after 60s" in detail
    assert "quit and reopened Docker" in detail
    assert "no daemon after 55s more" in detail
    assert calls == [OPEN_DOCKER, QUIT_DOCKER, PKILL_DOCKER, OPEN_DOCKER]
    assert clock.t >= 120


def test_ensure_docker_reports_missing_app(monkeypatch):
    _clock, calls = _patch_docker_timeline(monkeypatch, up_at=None, run_rc=1)
    ok, detail = mod.ensure_docker()
    assert ok is False
    assert detail.startswith(DAEMON_ERROR)
    assert "open failed" in detail
    assert calls == [OPEN_DOCKER]


def test_ensure_docker_does_not_autostart_off_macos(monkeypatch):
    monkeypatch.setattr(mod.sys, "platform", "linux")
    monkeypatch.setattr(mod, "_docker_daemon_running", lambda: False)
    run = MagicMock()
    monkeypatch.setattr(mod, "_run", run)
    assert mod.ensure_docker() == (False, DAEMON_ERROR)
    run.assert_not_called()


def test_provision_daemon_down_calls_ensure_docker(monkeypatch):
    _path_a_ok(monkeypatch, port=54217, probe=True)
    monkeypatch.setattr(mod, "_docker_daemon_running", lambda: False)
    ensure = MagicMock(return_value=(True, ""))
    monkeypatch.setattr(mod, "ensure_docker", ensure)
    result = provision_server("pr-3464")
    ensure.assert_called_once_with()
    assert result.state == "ready"


def test_provision_ensure_docker_failure_is_reported(monkeypatch):
    monkeypatch.setattr(mod, "_docker_daemon_running", lambda: False)
    detail = f"{DAEMON_ERROR} (auto-start tried: opened Docker, no daemon after 60s)"
    monkeypatch.setattr(mod, "ensure_docker", lambda *a, **k: (False, detail))
    pull = MagicMock()
    monkeypatch.setattr(mod, "_docker_pull", pull)
    result = provision_server("develop")
    assert result.state == "failed"
    assert result.error == detail
    pull.assert_not_called()


def test_provision_no_start_docker_skips_autostart(monkeypatch):
    monkeypatch.setattr(mod, "_docker_daemon_running", lambda: False)
    ensure = MagicMock()
    monkeypatch.setattr(mod, "ensure_docker", ensure)
    result = provision_server("develop", start_docker=False)
    assert result.error == DAEMON_ERROR
    ensure.assert_not_called()


def test_server_provision_no_start_docker_flag(monkeypatch):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    add_server_provision_subparser(sub)
    assert parser.parse_args(["server-provision", "3464"]).start_docker is True
    args = parser.parse_args(["server-provision", "3464", "--no-start-docker"])
    assert args.start_docker is False

    seen = {}

    def _fake_provision(*a, **k):
        seen.update(k)
        return mod.ProvisionResult(ref="pr-3464", state="ready", url="http://x:1")

    monkeypatch.setattr(mod, "provision_server", _fake_provision)
    assert run_server_provision(args) == 0
    assert seen["start_docker"] is False


def test_run_server_provision_docker_start_failure_exits_5(monkeypatch):
    monkeypatch.setattr(
        mod,
        "provision_server",
        lambda *a, **k: mod.ProvisionResult(
            ref="develop",
            state="failed",
            error=f"{DAEMON_ERROR} (auto-start tried: opened Docker)",
        ),
    )
    assert run_server_provision(SimpleNamespace(
        ref="develop", dry_run=False, timeout=180,
    )) == 5


# ---------------------------------------------------------------------------
# D2 — image fallback (GHCR first, then Docker Hub for non-PR refs)
# ---------------------------------------------------------------------------

GHCR_DEVELOP = f"{IMAGE_REPO}:develop"
HUB_DEVELOP = "docker.io/rocketchat/rocket.chat:develop"
MANIFEST_UNKNOWN = "Error response from daemon: manifest unknown"


def _pull_script(monkeypatch, outcomes):
    """Map each image to ``(ok, detail)``; return the list of pulls in order."""
    pulled = []

    def _pull(image):
        pulled.append(image)
        return outcomes[image]

    monkeypatch.setattr(mod, "_docker_pull", _pull)
    return pulled


def test_image_candidates_order_and_pr_exclusion():
    assert mod._image_candidates("develop") == [GHCR_DEVELOP, HUB_DEVELOP]
    assert mod._image_candidates("6.15.0") == [
        f"{IMAGE_REPO}:6.15.0",
        "docker.io/rocketchat/rocket.chat:6.15.0",
    ]
    assert mod._image_candidates("3464") == [f"{IMAGE_REPO}:pr-3464"]


def test_fallback_ghcr_present_skips_docker_hub(monkeypatch):
    _pull, compose = _path_a_ok(monkeypatch)
    pulled = _pull_script(monkeypatch, {GHCR_DEVELOP: (True, "")})
    result = provision_server("develop")
    assert result.state == "ready"
    assert pulled == [GHCR_DEVELOP]
    assert compose.call_args.kwargs["env"]["RC_IMAGE"] == GHCR_DEVELOP


def test_fallback_ghcr_missing_pulls_docker_hub(monkeypatch):
    _pull, compose = _path_a_ok(monkeypatch)
    pulled = _pull_script(
        monkeypatch,
        {GHCR_DEVELOP: (False, MANIFEST_UNKNOWN), HUB_DEVELOP: (True, "")},
    )
    result = provision_server("develop")
    assert result.state == "ready"
    assert pulled == [GHCR_DEVELOP, HUB_DEVELOP]
    assert compose.call_args.kwargs["env"]["RC_IMAGE"] == HUB_DEVELOP


def test_fallback_pr_ref_never_tries_docker_hub(monkeypatch):
    _pull, compose = _path_a_ok(monkeypatch)
    pr_ghcr = f"{IMAGE_REPO}:pr-9999"
    pulled = _pull_script(monkeypatch, {pr_ghcr: (False, MANIFEST_UNKNOWN)})
    result = provision_server("pr-9999")
    assert result.state == "failed"
    assert pulled == [pr_ghcr]
    assert "no published server image for 'pr-9999'" in result.error
    assert "docker.io" not in result.error
    compose.assert_not_called()


def test_fallback_both_missing_names_both_repos(monkeypatch):
    _pull, compose = _path_a_ok(monkeypatch)
    pulled = _pull_script(
        monkeypatch,
        {GHCR_DEVELOP: (False, MANIFEST_UNKNOWN), HUB_DEVELOP: (False, MANIFEST_UNKNOWN)},
    )
    result = provision_server("develop")
    assert result.state == "failed"
    assert result.error.startswith("no published server image for 'develop'")
    assert "cannot be tested this way" in result.error
    assert GHCR_DEVELOP in result.error
    assert HUB_DEVELOP in result.error
    assert pulled == [GHCR_DEVELOP, HUB_DEVELOP]
    compose.assert_not_called()


def test_fallback_ghcr_auth_error_does_not_try_docker_hub(monkeypatch):
    _pull, compose = _path_a_ok(monkeypatch)
    pulled = _pull_script(
        monkeypatch,
        {GHCR_DEVELOP: (False, "unauthorized: authentication required")},
    )
    result = provision_server("develop")
    assert result.state == "failed"
    assert f"failed to pull {GHCR_DEVELOP}" in result.error
    assert pulled == [GHCR_DEVELOP]
    compose.assert_not_called()


# ---------------------------------------------------------------------------
# D3 — compose turns off email 2FA
# ---------------------------------------------------------------------------


def test_compose_template_turns_off_two_factor_auth():
    text = mod.COMPOSE_TEMPLATE.read_text(encoding="utf-8")
    assert 'OVERWRITE_SETTING_Accounts_TwoFactorAuthentication_Enabled: "false"' in text
    assert (
        'OVERWRITE_SETTING_Accounts_TwoFactorAuthentication_By_Email_Enabled: "false"'
        in text
    )


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
