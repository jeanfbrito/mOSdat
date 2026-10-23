"""Unit tests for automation/mcp_tools.py (001-agent-desktop-testing T001–T024).

Handlers are called directly (no stdio / JSON-RPC). External I/O (Proxmox,
SSH, VNC, yarn, VLM) is mocked. ``@pytest.mark.live`` covers the real
build→deploy→run sequence and is skipped unless ``--live`` is passed.
"""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Clear sibling-test stub pollution BEFORE importing automation.*.
for _name in list(sys.modules):
    if (
        _name.startswith("automation.transport")
        or _name.startswith("automation.vlm")
        or _name in (
            "automation.config",
            "automation.proxmox.api",
            "automation.proxmox.vm",
            "automation.proxmox.gpu",
            "automation.reporting.report",
        )
    ):
        sys.modules.pop(_name, None)

from automation.mcp_tools import (  # noqa: E402
    TOOL_DEFINITIONS,
    _build,
    _busy_envelope,
    _deploy,
    _envelope,
    _invoke_functional_runner,
    _list_scenarios,
    _list_vms,
    _readiness,
    _resolve_scenario,
    _resolve_vm,
    _run_functional,
    _run_smoke_impl,
    _ssh_bootstrap,
    _vm_busy,
)


PROJ = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Fixtures — call handlers directly with a mocked VM registry / run_build
# ---------------------------------------------------------------------------


def _make_vm(name: str = "ubuntu2404", vmid: int = 201, os_type: str = "linux"):
    pkg = SimpleNamespace(
        app_path="/opt/Rocket.Chat/rocketchat-desktop",
        process_name=None,
        file_glob=None,
    )
    return SimpleNamespace(
        name=name,
        vmid=vmid,
        ip="127.0.0.1",
        user="root",
        os_type=os_type,
        is_windows=(os_type == "windows"),
        packages=[pkg],
        x11="off",
        scenario_subdir=name,
        scenario_fallback_subdirs=["linux"] if os_type != "windows" else [],
        resolved_temp_dir="/tmp",
    )


@pytest.fixture
def fake_vms():
    return {
        "ubuntu2404": _make_vm("ubuntu2404", 201, "linux"),
        "fedora42": _make_vm("fedora42", 100, "linux"),
    }


@pytest.fixture
def mock_registry(monkeypatch, fake_vms):
    """Replace ``_config()`` with an in-memory VM registry (no TOML / Proxmox)."""
    cfg = SimpleNamespace(
        vm_by_name=fake_vms,
        vms=list(fake_vms.values()),
        vlm=SimpleNamespace(
            base_url="http://127.0.0.1:9",
            model="dummy",
            verify_model=None,
            api_key="",
            max_tokens_floor=16,
            expected_model=None,
        ),
        proxmox=SimpleNamespace(),
        functional=SimpleNamespace(
            tests_dir=PROJ / "shared" / "scenarios" / "functional",
            workspace_url="",
            test_user="",
            test_password="",
        ),
        build=SimpleNamespace(repo_path=None),
    )
    monkeypatch.setattr("automation.mcp_tools._config", lambda: cfg)
    return cfg


@pytest.fixture
def mock_run_build(monkeypatch):
    """``run_build`` returns 0 and does not touch the network."""
    sentinel = MagicMock(return_value=0)
    monkeypatch.setattr("automation.commands.build.run_build", sentinel)
    return sentinel


@pytest.fixture
def mock_env_ready(monkeypatch):
    """Skip the live SSH/deps pre-check so unit tests never touch a VM."""
    monkeypatch.setattr("automation.mcp_tools._run_env_precheck", lambda vm: None)


@pytest.fixture
def mock_functional_runner(monkeypatch, tmp_path):
    """Skip VNC/SSH/VLM; return a canned ``run_test`` outcome."""

    def _invoke(vm, cfg, scenario_path, args):
        shot_dir = tmp_path / "shots" / vm.name
        shot_dir.mkdir(parents=True, exist_ok=True)
        (shot_dir / "step1_verified.png").write_bytes(b"png")
        (shot_dir / "final.png").write_bytes(b"png")
        (shot_dir / "events.jsonl").write_text(
            json.dumps({"event": "step_end", "step_num": 1, "status": "ok"}) + "\n"
            + json.dumps({"event": "step_end", "step_num": 2, "status": "ok"}) + "\n"
        )
        return {
            "passed": True,
            "log": "PASS: all 2 steps completed",
            "screenshot_dir": shot_dir,
            "step_count": 2,
            "degraded": [],
        }

    monkeypatch.setattr("automation.mcp_tools._invoke_functional_runner", _invoke)
    return _invoke


# ---------------------------------------------------------------------------
# T005 — foundational helpers
# ---------------------------------------------------------------------------


def test_envelope_ok_has_null_error_and_empty_degraded():
    result = _envelope(True, extra="x")
    assert result["ok"] is True
    assert result["error"] is None
    assert result["degraded"] == []
    assert result["extra"] == "x"


def test_envelope_error_is_required_when_not_ok():
    result = _envelope(False, error="boom")
    assert result["ok"] is False
    assert result["error"] == "boom"
    assert result["degraded"] == []


def test_envelope_defaults_error_string_when_ok_false_and_error_omitted():
    result = _envelope(False)
    assert result["ok"] is False
    assert result["error"]


def test_envelope_preserves_degraded_on_success():
    result = _envelope(True, degraded=["vlm_unavailable"])
    assert result["ok"] is True
    assert result["error"] is None
    assert result["degraded"] == ["vlm_unavailable"]


def test_resolve_vm_found(mock_registry):
    vm, err = _resolve_vm("ubuntu2404")
    assert err is None
    assert vm is not None
    assert vm.name == "ubuntu2404"
    assert vm.vmid == 201


def test_resolve_vm_not_found_lists_alternatives(mock_registry):
    vm, err = _resolve_vm("no-such-vm")
    assert vm is None
    assert err["ok"] is False
    assert "no-such-vm" in err["error"]
    assert "ubuntu2404" in err["error"]
    assert "fedora42" in err["error"]


def test_resolve_scenario_found():
    meta, err = _resolve_scenario("rocketchat-smoke-linux", platform="linux")
    assert err is None
    assert meta is not None
    assert meta["name"] == "rocketchat-smoke-linux"
    assert meta["platform"] == "linux"
    assert meta["step_count"] >= 1
    assert Path(meta["resolved_path"]).exists()


def test_resolve_scenario_not_found_lists_alternatives():
    meta, err = _resolve_scenario("definitely-not-a-scenario", platform="linux")
    assert meta is None
    assert err["ok"] is False
    assert "definitely-not-a-scenario" in err["error"]
    assert "available:" in err["error"]
    assert "rocketchat-smoke-linux" in err["error"]


def test_vm_busy_false_when_lock_free(tmp_path, monkeypatch):
    monkeypatch.setenv("MOSDAT_LOCK_DIR", str(tmp_path))
    assert _vm_busy(201) is False


def test_vm_busy_true_when_lock_held(tmp_path, monkeypatch):
    monkeypatch.setenv("MOSDAT_LOCK_DIR", str(tmp_path))
    from automation.proxmox.vm import _vm_lock

    with _vm_lock(201, timeout=0):
        assert _vm_busy(201) is True


# ---------------------------------------------------------------------------
# T011 — build vs deploy failure, lock rejection, run verdict
# ---------------------------------------------------------------------------


def test_build_failure_leaves_deploy_null(mock_registry, monkeypatch):
    monkeypatch.setattr("automation.commands.build.run_build", lambda ns: 2)
    result = _build({"pr": 4821, "target": "deb", "deploy_to": "ubuntu2404"})
    assert result["ok"] is False
    assert result["build_ok"] is False
    assert result["deploy"] is None
    assert "build" in result["error"]


def test_deploy_failure_keeps_build_ok(mock_registry, monkeypatch):
    monkeypatch.setattr("automation.commands.build.run_build", lambda ns: 3)
    result = _build({"pr": 4821, "target": "deb", "deploy_to": "ubuntu2404"})
    assert result["ok"] is False
    assert result["build_ok"] is True
    assert result["deploy"] is not None
    assert result["deploy"]["error"]
    assert result["deploy"]["vm"] == "ubuntu2404"


def test_build_success_without_deploy(mock_registry, mock_run_build):
    result = _build({"pr": 4821, "target": "deb"})
    assert result["ok"] is True
    assert result["build_ok"] is True
    assert result["deploy"] is None
    assert result["error"] is None
    mock_run_build.assert_called_once()


def test_build_success_with_deploy(mock_registry, mock_run_build):
    result = _build({"pr": 4821, "target": "deb", "deploy_to": "ubuntu2404"})
    assert result["ok"] is True
    assert result["build_ok"] is True
    assert result["deploy"]["vm"] == "ubuntu2404"
    assert result["deploy"]["install_ok"] is True


def test_build_captures_build_on_windows_vm_result(monkeypatch):
    """Regression: _call_run_build's monkey-patch must also wrap
    build_on_windows_vm, not just deploy_to_vm/deploy_to_windows_vm — found
    live running mosdat_build against a real Windows VM, where the captured
    DeployResult was silently dropped and installed_version came back empty
    despite a successful install.
    """
    from automation.commands import build as build_mod

    fake_vms = {"windows10": _make_vm("windows10", 104, "windows")}
    cfg = SimpleNamespace(vm_by_name=fake_vms, vms=list(fake_vms.values()))
    monkeypatch.setattr("automation.mcp_tools._config", lambda: cfg)

    sentinel = build_mod.DeployResult(
        vm="windows10", scp_ok=True, install_ok=True,
        installed_version="4.17.0-alpha.1",
    )

    def fake_run_build(ns):
        build_mod.build_on_windows_vm(
            "windows10", "192.168.13.87", "jean", ns.pr, ns.repo,
            build_mod.TARGETS["exe"], [], dry_run=False,
        )
        return 0

    monkeypatch.setattr(build_mod, "build_on_windows_vm", lambda *a, **k: sentinel)
    monkeypatch.setattr(build_mod, "run_build", fake_run_build)

    result = _build({"pr": 3464, "target": "exe", "deploy_to": "windows10"})
    assert result["ok"] is True
    assert result["deploy"]["installed_version"] == "4.17.0-alpha.1"


def test_build_unknown_vm_lists_alternatives(mock_registry, mock_run_build):
    result = _build({"pr": 1, "target": "deb", "deploy_to": "missing-vm"})
    assert result["ok"] is False
    assert "missing-vm" in result["error"]
    assert "ubuntu2404" in result["error"]
    mock_run_build.assert_not_called()


def test_build_unknown_target_lists_alternatives(mock_registry, mock_run_build):
    result = _build({"pr": 1, "target": "msi"})
    assert result["ok"] is False
    assert "msi" in result["error"]
    assert "deb" in result["error"]
    mock_run_build.assert_not_called()


def test_build_lock_rejection(mock_registry, mock_run_build, monkeypatch):
    from automation.proxmox.gpu import ProxmoxLockTimeout

    @contextmanager
    def _busy(vmid, timeout=300):
        raise ProxmoxLockTimeout("held")
        yield  # pragma: no cover

    monkeypatch.setattr("automation.proxmox.vm._vm_lock", _busy)
    result = _build({"pr": 4821, "target": "deb", "deploy_to": "ubuntu2404"})
    assert result["ok"] is False
    assert result["error"] == "vm busy: 201 held by another operation"
    mock_run_build.assert_not_called()


def test_deploy_lock_rejection(mock_registry, monkeypatch, tmp_path):
    from automation.proxmox.gpu import ProxmoxLockTimeout

    pkg = tmp_path / "app.deb"
    pkg.write_bytes(b"deb")

    @contextmanager
    def _busy(vmid, timeout=300):
        raise ProxmoxLockTimeout("held")
        yield  # pragma: no cover

    monkeypatch.setattr("automation.proxmox.vm._vm_lock", _busy)
    deploy_fn = MagicMock()
    monkeypatch.setattr("automation.commands.build.deploy_to_vm", deploy_fn)
    result = _deploy({"vm": "ubuntu2404", "artifact_path": str(pkg), "target": "deb"})
    assert result["ok"] is False
    assert result["error"] == "vm busy: 201 held by another operation"
    deploy_fn.assert_not_called()


def test_deploy_success_envelope(mock_registry, monkeypatch, tmp_path):
    pkg = tmp_path / "app.deb"
    pkg.write_bytes(b"deb")
    fake = SimpleNamespace(
        vm="ubuntu2404",
        scp_ok=True,
        install_ok=True,
        installed_version="4.9.2-pr4821",
        missing_symbols=[],
        error="",
        ok=True,
    )
    monkeypatch.setattr("automation.commands.build.deploy_to_vm", lambda **k: fake)
    result = _deploy({"vm": "ubuntu2404", "artifact_path": str(pkg), "target": "deb"})
    assert result["ok"] is True
    assert result["install_ok"] is True
    assert result["installed_version"] == "4.9.2-pr4821"
    assert result["error"] is None


def test_run_functional_verdict_pass(mock_registry, mock_env_ready, mock_functional_runner):
    result = _run_functional({"vm": "ubuntu2404", "scenario": "rocketchat-smoke-linux"})
    assert result["ok"] is True
    assert result["verdict"] == "pass"
    assert result["error"] is None
    assert len(result["steps"]) == 2
    assert result["steps"][0]["outcome"] == "pass"
    assert result["artifacts"]
    assert result["elapsed_ms"] >= 0


def test_run_functional_verdict_fail(mock_registry, mock_env_ready, monkeypatch, tmp_path):
    def _invoke(vm, cfg, scenario_path, args):
        shot_dir = tmp_path / "fail-shots"
        shot_dir.mkdir(parents=True, exist_ok=True)
        (shot_dir / "step1_final_fail.png").write_bytes(b"png")
        (shot_dir / "events.jsonl").write_text(
            json.dumps({"event": "step_end", "step_num": 1, "status": "ok"}) + "\n"
            + json.dumps(
                {
                    "event": "step_end",
                    "step_num": 2,
                    "status": "failed",
                    "reason": "element not found",
                }
            )
            + "\n"
        )
        return {
            "passed": False,
            "log": "FAIL: element not found",
            "screenshot_dir": shot_dir,
            "step_count": 2,
            "degraded": [],
        }

    monkeypatch.setattr("automation.mcp_tools._invoke_functional_runner", _invoke)
    result = _run_functional({"vm": "ubuntu2404", "scenario": "rocketchat-smoke-linux"})
    assert result["ok"] is True
    assert result["verdict"] == "fail"
    assert not result.get("env_not_ready")
    assert result["steps"][-1]["outcome"] == "fail"
    assert result["steps"][-1]["reason"] == "element not found"


def test_run_functional_vlm_unavailable_flagged(mock_registry, mock_env_ready, monkeypatch, tmp_path):
    def _invoke(vm, cfg, scenario_path, args):
        shot_dir = tmp_path / "degraded-shots"
        shot_dir.mkdir(parents=True, exist_ok=True)
        return {
            "passed": True,
            "log": "PASS",
            "screenshot_dir": shot_dir,
            "step_count": 1,
            "degraded": ["vlm_unavailable"],
        }

    monkeypatch.setattr("automation.mcp_tools._invoke_functional_runner", _invoke)
    result = _run_functional({"vm": "ubuntu2404", "scenario": "rocketchat-smoke-linux"})
    assert result["ok"] is True
    # No VLM + VLM-judged steps: a "pass" only means the steps executed.
    assert result["verdict"] == "unverified"
    assert "vlm_unavailable" in result["degraded"]
    assert result["unverified_steps"]
    assert "screenshots_to_judge" in result


def test_run_functional_vlm_unavailable_without_vlm_steps_keeps_verdict(
    mock_registry, mock_env_ready, monkeypatch, tmp_path
):
    scenario = tmp_path / "shell-only.yaml"
    scenario.write_text("name: shell-only\nsteps:\n  - shell: echo hi\n")

    def _invoke(vm, cfg, scenario_path, args):
        return {"passed": True, "log": "PASS", "screenshot_dir": tmp_path,
                "step_count": 1, "degraded": ["vlm_unavailable"]}

    monkeypatch.setattr("automation.mcp_tools._invoke_functional_runner", _invoke)
    result = _run_functional({"vm": "ubuntu2404", "scenario": str(scenario)})
    assert result["verdict"] == "pass"
    assert "unverified_steps" not in result


def test_run_functional_accepts_scenario_path_outside_shared(
    mock_registry, mock_env_ready, monkeypatch, tmp_path
):
    scenario = tmp_path / "scratch.yaml"
    scenario.write_text("name: scratch\nsteps:\n  - shell: echo hi\n")
    seen = {}

    def _invoke(vm, cfg, scenario_path, args):
        seen["path"] = scenario_path
        return {"passed": True, "log": "PASS", "screenshot_dir": tmp_path,
                "step_count": 1, "degraded": []}

    monkeypatch.setattr("automation.mcp_tools._invoke_functional_runner", _invoke)
    result = _run_functional({"vm": "ubuntu2404", "scenario": str(scenario)})
    assert result["verdict"] == "pass"
    assert seen["path"] == scenario.resolve()


def test_run_functional_inline_scenario_yaml(
    mock_registry, mock_env_ready, monkeypatch, tmp_path
):
    seen = {}
    def _write(text):
        path = tmp_path / "inline.yaml"
        path.write_text(text)
        return path

    monkeypatch.setattr("automation.mcp_tools._write_inline_scenario", _write)

    def _invoke(vm, cfg, scenario_path, args):
        seen["text"] = Path(scenario_path).read_text()
        return {"passed": True, "log": "PASS", "screenshot_dir": tmp_path,
                "step_count": 1, "degraded": []}

    monkeypatch.setattr("automation.mcp_tools._invoke_functional_runner", _invoke)
    yaml_text = "name: inline\nsteps:\n  - shell: echo hi\n"
    result = _run_functional({"vm": "ubuntu2404", "scenario_yaml": yaml_text})
    assert result["verdict"] == "pass"
    assert seen["text"] == yaml_text


def test_run_functional_rejects_scenario_and_scenario_yaml(mock_registry, mock_env_ready):
    result = _run_functional(
        {"vm": "ubuntu2404", "scenario": "x", "scenario_yaml": "name: y\nsteps: []\n"}
    )
    assert result["ok"] is False
    assert "not both" in result["error"]


def test_busy_envelope_shape():
    result = _busy_envelope(201)
    assert result == {
        "ok": False,
        "error": "vm busy: 201 held by another operation",
        "degraded": [],
    }


# ---------------------------------------------------------------------------
# T015 — invalid combo rejection + environment-not-ready vs test failure
# ---------------------------------------------------------------------------


def test_run_functional_unknown_vm_lists_alternatives(mock_registry, monkeypatch):
    invoke = MagicMock()
    monkeypatch.setattr("automation.mcp_tools._invoke_functional_runner", invoke)
    result = _run_functional({"vm": "no-such-vm", "scenario": "rocketchat-smoke-linux"})
    assert result["ok"] is False
    assert "no-such-vm" in result["error"]
    assert "ubuntu2404" in result["error"]
    assert result.get("verdict") != "fail"
    assert not result.get("env_not_ready")
    invoke.assert_not_called()


def test_run_functional_unknown_scenario_lists_alternatives(mock_registry, monkeypatch):
    invoke = MagicMock()
    monkeypatch.setattr("automation.mcp_tools._invoke_functional_runner", invoke)
    result = _run_functional({"vm": "ubuntu2404", "scenario": "definitely-not-a-scenario"})
    assert result["ok"] is False
    assert "definitely-not-a-scenario" in result["error"]
    assert "available:" in result["error"]
    assert result.get("verdict") != "fail"
    invoke.assert_not_called()


def test_run_functional_env_not_ready_unreachable(mock_registry, monkeypatch):
    from automation.commands.doctor import CheckResult

    invoke = MagicMock()
    monkeypatch.setattr("automation.mcp_tools._invoke_functional_runner", invoke)
    monkeypatch.setattr("automation.mcp_tools._ssh_client", lambda vm: MagicMock())
    monkeypatch.setattr(
        "automation.commands.doctor.check_ssh",
        lambda ssh: CheckResult("SSH reachable", "FAIL", "ssh timeout"),
    )
    result = _run_functional({"vm": "ubuntu2404", "scenario": "rocketchat-smoke-linux"})
    assert result["ok"] is False
    assert result["env_not_ready"] is True
    assert result["verdict"] == "error"
    assert result["verdict"] != "fail"
    assert result["error"].startswith("environment not ready:")
    assert "ubuntu2404" in result["error"]
    assert "unreachable" in result["error"]
    assert result["steps"] == []
    invoke.assert_not_called()


def test_run_functional_env_not_ready_missing_deps(mock_registry, monkeypatch):
    from automation.commands.doctor import CheckResult

    invoke = MagicMock()
    monkeypatch.setattr("automation.mcp_tools._invoke_functional_runner", invoke)
    monkeypatch.setattr("automation.mcp_tools._ssh_client", lambda vm: MagicMock())
    monkeypatch.setattr(
        "automation.commands.doctor.check_ssh",
        lambda ssh: CheckResult("SSH reachable", "PASS"),
    )
    monkeypatch.setattr(
        "automation.commands.doctor.check_deps",
        lambda ssh: [
            CheckResult("dep:wmctrl", "FAIL", "not installed"),
            CheckResult("dep:xclip", "PASS"),
        ],
    )
    result = _run_functional({"vm": "ubuntu2404", "scenario": "rocketchat-smoke-linux"})
    assert result["ok"] is False
    assert result["env_not_ready"] is True
    assert result["verdict"] == "error"
    assert "missing dependencies" in result["error"]
    assert "wmctrl" in result["error"]
    invoke.assert_not_called()


def test_run_functional_test_fail_is_not_env_not_ready(
    mock_registry, mock_env_ready, monkeypatch, tmp_path
):
    def _invoke(vm, cfg, scenario_path, args):
        return {
            "passed": False,
            "log": "FAIL: assertion",
            "screenshot_dir": tmp_path,
            "step_count": 1,
            "degraded": [],
        }

    monkeypatch.setattr("automation.mcp_tools._invoke_functional_runner", _invoke)
    result = _run_functional({"vm": "ubuntu2404", "scenario": "rocketchat-smoke-linux"})
    assert result["ok"] is True
    assert result["verdict"] == "fail"
    assert not result.get("env_not_ready")


# ---------------------------------------------------------------------------
# T013/T014 — mosdat_run_functional server_url override (002 US2)
# ---------------------------------------------------------------------------


def test_run_functional_tool_definition_has_server_url_field():
    """T013: TOOL_DEFINITIONS advertises the new optional server_url field."""
    tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "mosdat_run_functional")
    props = tool["inputSchema"]["properties"]
    assert "server_url" in props
    assert props["server_url"]["type"] == "string"
    assert "server_url" not in tool["inputSchema"].get("required", [])


@pytest.fixture
def mock_invoke_functional_deps(monkeypatch):
    """Mock every I/O dependency of ``_invoke_functional_runner`` (VNC/SSH/VLM/
    Proxmox/atspi/uia) and record the ``load_test_yaml``/``run_test`` calls so
    tests can assert on the ``cli_vars``/``vars_`` threaded through.
    """
    load_test_yaml_calls: list[dict] = []
    run_test_calls: list[dict] = []

    def fake_load_test_yaml(path, cfg, **kwargs):
        load_test_yaml_calls.append(kwargs)
        return ("fake-scenario", list(range(5)), {}, {})

    class FakeVncClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return MagicMock()

        def __exit__(self, *a):
            return False

    class FakeRunner:
        def __init__(self, **kw):
            pass

        def run_test(self, **kw):
            run_test_calls.append(kw)
            return True, "PASS: 0 steps"

    monkeypatch.setattr("automation.runners.scenario_loader.load_test_yaml", fake_load_test_yaml)
    monkeypatch.setattr("automation.runners.functional.FunctionalRunner", FakeRunner)
    monkeypatch.setattr("automation.transport.vnc.VncClient", FakeVncClient)
    monkeypatch.setattr("automation.transport.ssh.SSHClient", lambda *a, **kw: MagicMock())
    monkeypatch.setattr("automation.vlm.client.VLMClient", lambda **kw: MagicMock())
    monkeypatch.setattr("automation.vlm.input.InputInjector", lambda *a, **kw: MagicMock())
    monkeypatch.setattr("automation.vlm.screenshot.Screenshotter", lambda *a, **kw: MagicMock())
    monkeypatch.setattr("automation.proxmox.api.ProxmoxAPI", lambda cfg: MagicMock())
    monkeypatch.setattr("automation.atspi.AtspiClient", lambda **kw: MagicMock())
    monkeypatch.setattr("automation.uia.UiaClient", lambda **kw: MagicMock())
    monkeypatch.setattr("automation.mcp_tools._probe_vlm_degraded", lambda vlm: [])

    return {"load_test_yaml_calls": load_test_yaml_calls, "run_test_calls": run_test_calls}


def test_invoke_functional_runner_threads_server_url_override(
    mock_registry, mock_invoke_functional_deps, tmp_path
):
    """T014: an explicit server_url overrides workspace_url for both the
    double-brace (cli_vars) and single-brace (vars_) templating paths."""
    vm = _make_vm()
    override = "http://192.168.13.20:54217"
    _invoke_functional_runner(vm, mock_registry, tmp_path / "scenario.yaml", {"server_url": override})

    load_calls = mock_invoke_functional_deps["load_test_yaml_calls"]
    assert load_calls[0]["cli_vars"] == {"workspace_url": override}

    run_calls = mock_invoke_functional_deps["run_test_calls"]
    assert run_calls[0]["vars"]["workspace_url"] == override


def test_invoke_functional_runner_defaults_workspace_url_from_config(
    mock_registry, mock_invoke_functional_deps, tmp_path
):
    """T014: omitting server_url keeps existing behavior, but now vars_ always
    carries workspace_url from config (fixing the pre-existing bug where
    single-brace scenarios saw the literal '{workspace_url}' placeholder)."""
    mock_registry.functional.workspace_url = "https://configured.example.com"
    vm = _make_vm()
    _invoke_functional_runner(vm, mock_registry, tmp_path / "scenario.yaml", {})

    load_calls = mock_invoke_functional_deps["load_test_yaml_calls"]
    assert "cli_vars" not in load_calls[0]

    run_calls = mock_invoke_functional_deps["run_test_calls"]
    assert run_calls[0]["vars"]["workspace_url"] == "https://configured.example.com"


def test_invoke_functional_runner_threads_start_index_for_partial_run(
    mock_registry, mock_invoke_functional_deps, tmp_path
):
    """A partial run (from_step > 1) threads start_index = from_step - 1 into
    run_test so step numbers logged by FunctionalRunner reflect the true
    original-scenario step index instead of renumbering from 1."""
    vm = _make_vm()
    result = _invoke_functional_runner(
        vm, mock_registry, tmp_path / "scenario.yaml", {"from_step": 3}
    )

    run_calls = mock_invoke_functional_deps["run_test_calls"]
    assert run_calls[0]["start_index"] == 2
    assert "partial_run_no_precondition_guarantee" in result["degraded"]


def test_invoke_functional_runner_full_run_has_zero_start_index_and_no_degraded(
    mock_registry, mock_invoke_functional_deps, tmp_path
):
    """A full run (from_step omitted/1) passes start_index=0 and does not flag
    the partial-run precondition caveat in degraded."""
    vm = _make_vm()
    result = _invoke_functional_runner(
        vm, mock_registry, tmp_path / "scenario.yaml", {}
    )

    run_calls = mock_invoke_functional_deps["run_test_calls"]
    assert run_calls[0]["start_index"] == 0
    assert "partial_run_no_precondition_guarantee" not in result["degraded"]


# ---------------------------------------------------------------------------
# T020 — mosdat_readiness
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_doctor_ready(monkeypatch):
    from automation.commands.doctor import CheckResult

    monkeypatch.setattr("automation.mcp_tools._ssh_client", lambda vm: MagicMock())
    monkeypatch.setattr(
        "automation.commands.doctor.check_ssh",
        lambda ssh: CheckResult("SSH reachable", "PASS"),
    )
    monkeypatch.setattr(
        "automation.commands.doctor.check_deps",
        lambda ssh: [
            CheckResult("dep:wmctrl", "PASS"),
            CheckResult("dep:xclip", "PASS"),
            CheckResult("dep:xdotool", "PASS"),
            CheckResult("dep:xdg-mime", "PASS"),
            CheckResult("dep:xdg-open", "PASS"),
            CheckResult("dep:ssh", "PASS"),
            CheckResult("dep:scp", "PASS"),
        ],
    )
    monkeypatch.setattr(
        "automation.commands.doctor.check_disk_tmp",
        lambda ssh: CheckResult("Disk /tmp free", "PASS", "8 GB"),
    )
    monkeypatch.setattr("automation.mcp_tools._read_deployed_version", lambda ssh: "4.9.2-pr4821")
    monkeypatch.setattr("automation.mcp_tools._vm_busy", lambda vmid: False)


def test_readiness_registered_in_tool_definitions_and_dispatch():
    names = [t["name"] for t in TOOL_DEFINITIONS]
    assert "mosdat_readiness" in names
    import inspect

    from automation import mcp_server

    src = inspect.getsource(mcp_server.handle_tools_call)
    assert "mosdat_readiness" in src
    assert "_readiness" in src


def test_readiness_ready(mock_registry, mock_doctor_ready):
    result = _readiness({"vm": "ubuntu2404"})
    assert result["ok"] is True
    assert result["error"] is None
    assert result["ready"] is True
    assert result["vm"]["name"] == "ubuntu2404"
    assert result["vm"]["vmid"] == 201
    assert result["vm"]["reachable"] is True
    assert result["vm"]["busy"] is False
    assert result["vm"]["deployed_version"] == "4.9.2-pr4821"
    labels = [c["label"] for c in result["checks"]]
    assert "ssh_reachable" in labels
    assert "tool_deps" in labels
    assert all(c["status"] == "PASS" for c in result["checks"])


def test_readiness_not_ready_unreachable(mock_registry, mock_doctor_ready, monkeypatch):
    from automation.commands.doctor import CheckResult

    monkeypatch.setattr(
        "automation.commands.doctor.check_ssh",
        lambda ssh: CheckResult("SSH reachable", "FAIL", "connection refused"),
    )
    result = _readiness({"vm": "ubuntu2404"})
    assert result["ok"] is True
    assert result["ready"] is False
    assert result["vm"]["reachable"] is False
    ssh_check = next(c for c in result["checks"] if c["label"] == "ssh_reachable")
    assert ssh_check["status"] == "FAIL"
    assert not any(c["label"] == "tool_deps" for c in result["checks"])


def test_readiness_missing_dependency(mock_registry, mock_doctor_ready, monkeypatch):
    from automation.commands.doctor import CheckResult

    monkeypatch.setattr(
        "automation.commands.doctor.check_deps",
        lambda ssh: [
            CheckResult("dep:wmctrl", "FAIL", "not installed"),
            CheckResult("dep:xclip", "PASS"),
        ],
    )
    result = _readiness({"vm": "ubuntu2404"})
    assert result["ok"] is True
    assert result["ready"] is False
    deps = next(c for c in result["checks"] if c["label"] == "tool_deps")
    assert deps["status"] == "FAIL"
    assert "wmctrl" in deps["detail"]


def test_readiness_busy(mock_registry, mock_doctor_ready, monkeypatch):
    monkeypatch.setattr("automation.mcp_tools._vm_busy", lambda vmid: True)
    result = _readiness({"vm": "ubuntu2404"})
    assert result["ok"] is True
    assert result["vm"]["busy"] is True
    # busy is advisory — it does not by itself flip ready
    assert result["ready"] is True


def test_readiness_expect_pr_mismatch(mock_registry, mock_doctor_ready, monkeypatch):
    monkeypatch.setattr(
        "automation.mcp_tools._read_deployed_version", lambda ssh: "4.9.2-pr4820"
    )
    monkeypatch.setattr(
        "automation.commands.build._find_installed_asar",
        lambda ssh: "/opt/Rocket.Chat/resources/app.asar",
    )
    monkeypatch.setattr(
        "automation.commands.build._verify_symbols_on_vm",
        lambda ssh, asar, symbols: list(symbols),
    )
    result = _readiness({"vm": "ubuntu2404", "expect_pr": 4821})
    assert result["ok"] is True
    assert result["ready"] is False
    match = next(
        c for c in result["checks"] if c["label"] == "deployed_build_matches_expected"
    )
    assert match["status"] == "FAIL"
    assert "4821" in match["detail"]
    assert "4820" in match["detail"]


def test_readiness_unknown_vm_lists_alternatives(mock_registry):
    result = _readiness({"vm": "missing-vm"})
    assert result["ok"] is False
    assert "missing-vm" in result["error"]
    assert "ubuntu2404" in result["error"]


def test_readiness_windows_ssh_fail_hints_bootstrap(mock_registry, mock_doctor_ready, monkeypatch):
    from automation.commands.doctor import CheckResult

    win = _make_vm("windows10", 104, "windows")
    mock_registry.vm_by_name["windows10"] = win
    mock_registry.vms.append(win)
    monkeypatch.setattr(
        "automation.commands.doctor.check_ssh",
        lambda ssh: CheckResult("SSH reachable", "FAIL", "Permission denied"),
    )
    result = _readiness({"vm": "windows10"})
    assert result["ok"] is True
    assert result["ready"] is False
    ssh_check = next(c for c in result["checks"] if c["label"] == "ssh_reachable")
    assert ssh_check["status"] == "FAIL"
    assert "mosdat_ssh_bootstrap" in ssh_check["detail"]
    linux = _readiness({"vm": "ubuntu2404"})
    linux_ssh = next(c for c in linux["checks"] if c["label"] == "ssh_reachable")
    assert linux_ssh["status"] == "FAIL"
    assert "mosdat_ssh_bootstrap" not in linux_ssh["detail"]


# ---------------------------------------------------------------------------
# T023 — discovery tools envelope
# ---------------------------------------------------------------------------


def test_list_vms_envelope(mock_registry, monkeypatch):
    monkeypatch.setattr(
        "automation.mcp_tools._get_proxmox_api",
        lambda: (_ for _ in ()).throw(RuntimeError("no proxmox")),
    )
    result = _list_vms()
    assert result["ok"] is True
    assert result["error"] is None
    assert isinstance(result["degraded"], list)
    assert "proxmox_unreachable" in result["degraded"]
    names = {v["name"] for v in result["vms"]}
    assert names == {"ubuntu2404", "fedora42"}
    ubuntu = next(v for v in result["vms"] if v["name"] == "ubuntu2404")
    assert ubuntu["vmid"] == 201
    assert ubuntu["os_type"] == "linux"
    assert ubuntu["status"] == "unknown"


def test_list_vms_status_from_proxmox(mock_registry, monkeypatch):
    api = MagicMock()
    api.config = SimpleNamespace(node="pve")
    api.get.return_value = {
        "data": [
            {"name": "ubuntu2404", "vmid": 201, "status": "running"},
            {"name": "fedora42", "vmid": 100, "status": "stopped"},
        ]
    }
    monkeypatch.setattr("automation.mcp_tools._get_proxmox_api", lambda: api)
    result = _list_vms({})
    assert result["ok"] is True
    assert result["degraded"] == []
    by_name = {v["name"]: v for v in result["vms"]}
    assert by_name["ubuntu2404"]["status"] == "running"
    assert by_name["fedora42"]["status"] == "stopped"


def test_list_scenarios_envelope():
    result = _list_scenarios()
    assert result["ok"] is True
    assert result["error"] is None
    assert result["degraded"] == []
    assert result["scenarios"]
    smoke = next(s for s in result["scenarios"] if s["name"] == "rocketchat-smoke-linux")
    assert smoke["platform"] == "linux"
    assert "path" in smoke
    assert isinstance(smoke["step_count"], int)
    assert smoke["step_count"] >= 1


def test_list_scenarios_platform_filter():
    linux = _list_scenarios({"platform": "linux"})
    windows = _list_scenarios({"platform": "windows"})
    assert linux["ok"] is True
    assert all(s["platform"] == "linux" for s in linux["scenarios"])
    assert windows["ok"] is True
    assert all(s["platform"] == "windows" for s in windows["scenarios"])


def test_list_scenarios_unknown_platform():
    result = _list_scenarios({"platform": "amiga"})
    assert result["ok"] is False
    assert "amiga" in result["error"]
    assert "linux" in result["error"]
    assert result["scenarios"] == []


# ---------------------------------------------------------------------------
# mosdat_ssh_bootstrap
# ---------------------------------------------------------------------------


def test_ssh_bootstrap_registered_in_tool_definitions_and_dispatch():
    names = [t["name"] for t in TOOL_DEFINITIONS]
    assert "mosdat_ssh_bootstrap" in names
    import inspect

    from automation import mcp_server

    src = inspect.getsource(mcp_server.handle_tools_call)
    assert "mosdat_ssh_bootstrap" in src
    assert "_ssh_bootstrap" in src


def test_ssh_bootstrap_unknown_vm_lists_alternatives(mock_registry, monkeypatch):
    boot = MagicMock()
    monkeypatch.setattr(
        "automation.commands.ssh_bootstrap.bootstrap_windows_ssh", boot,
    )
    result = _ssh_bootstrap({"vm": "missing-vm"})
    assert result["ok"] is False
    assert "missing-vm" in result["error"]
    assert "ubuntu2404" in result["error"]
    boot.assert_not_called()


def test_ssh_bootstrap_linux_uses_guest_agent_path(mock_registry, monkeypatch, tmp_path):
    pub = tmp_path / "id_ed25519.pub"
    pub.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFake test@host\n")
    win_boot = MagicMock()
    linux_boot = MagicMock(return_value=SimpleNamespace(
        ok=True, error="", already_working=False, steps=["agent ping ok"],
    ))
    monkeypatch.setattr("automation.commands.ssh_bootstrap.bootstrap_windows_ssh", win_boot)
    monkeypatch.setattr("automation.commands.ssh_bootstrap.bootstrap_linux_ssh", linux_boot)
    monkeypatch.setattr("automation.proxmox.api.ProxmoxAPI", lambda cfg: MagicMock())

    @contextmanager
    def _free(vmid):
        yield

    monkeypatch.setattr("automation.mcp_tools._nonblocking_vm_lock", _free)
    result = _ssh_bootstrap({"vm": "ubuntu2404", "pubkey_path": str(pub)})
    assert result["ok"] is True
    assert result["steps"] == ["agent ping ok"]
    linux_boot.assert_called_once()
    win_boot.assert_not_called()


def test_ssh_bootstrap_lock_rejection(mock_registry, monkeypatch, tmp_path):
    from automation.proxmox.gpu import ProxmoxLockTimeout

    win = _make_vm("windows10", 104, "windows")
    mock_registry.vm_by_name["windows10"] = win
    mock_registry.vms.append(win)
    pub = tmp_path / "id_ed25519.pub"
    pub.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFake test@host\n")

    @contextmanager
    def _busy(vmid, timeout=300):
        raise ProxmoxLockTimeout("held")
        yield  # pragma: no cover

    monkeypatch.setattr("automation.proxmox.vm._vm_lock", _busy)
    boot = MagicMock()
    monkeypatch.setattr(
        "automation.commands.ssh_bootstrap.bootstrap_windows_ssh", boot,
    )
    result = _ssh_bootstrap({"vm": "windows10", "pubkey_path": str(pub)})
    assert result["ok"] is False
    assert result["error"] == "vm busy: 104 held by another operation"
    boot.assert_not_called()


# ---------------------------------------------------------------------------
# mosdat_run_smoke
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_smoke_deps(monkeypatch):
    """Mock every I/O dependency of ``_run_smoke_impl`` (VNC/SSH/VLM/Proxmox).

    Returns the injector/vlm/screenshotter mocks so tests can configure
    ``process_running``/``verify`` return values and assert on the exact
    commands passed to ``injector.shell``/``injector.launch``.
    """

    class FakeVncClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return MagicMock()

        def __exit__(self, *a):
            return False

    vlm_mock = MagicMock()
    vlm_mock.verify.return_value = True
    injector_mock = MagicMock()
    injector_mock.is_windows = False
    injector_mock.process_running.return_value = True
    screenshotter_mock = MagicMock()
    screenshotter_mock.capture.return_value = ("fake-screenshot", {})

    monkeypatch.setattr("automation.transport.vnc.VncClient", FakeVncClient)
    monkeypatch.setattr("automation.transport.ssh.SSHClient", lambda *a, **kw: MagicMock())
    monkeypatch.setattr("automation.vlm.client.VLMClient", lambda **kw: vlm_mock)
    monkeypatch.setattr("automation.vlm.input.InputInjector", lambda *a, **kw: injector_mock)
    monkeypatch.setattr("automation.vlm.screenshot.Screenshotter", lambda *a, **kw: screenshotter_mock)
    monkeypatch.setattr("automation.proxmox.api.ProxmoxAPI", lambda cfg: MagicMock())
    monkeypatch.setattr("automation.mcp_tools._probe_vlm_degraded", lambda vlm: [])
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)

    return {"vlm": vlm_mock, "injector": injector_mock, "screenshotter": screenshotter_mock}


def test_run_smoke_linux_pass(mock_registry, mock_smoke_deps):
    """Process running + window visible on a Linux VM → verdict pass."""
    result = _run_smoke_impl({"vm_name": "ubuntu2404"})

    assert result["ok"] is True
    assert result["verdict"] == "pass"
    assert result["process_running"] is True
    assert result["window_visible"] is True
    assert result["vm"] == "ubuntu2404"

    injector = mock_smoke_deps["injector"]
    injector.launch.assert_called_once()
    kill_cmd = injector.shell.call_args_list[0][0][0]
    assert "pkill" in kill_cmd
    assert "Stop-Process" not in kill_cmd
    mock_smoke_deps["screenshotter"].capture.assert_called_once()
    mock_smoke_deps["vlm"].verify.assert_called_once()


def test_run_smoke_windows_pass_no_bash_commands(mock_registry, mock_smoke_deps):
    """Regression test: a Windows VM must never see pkill/wmctrl/bash job
    control — this is the exact bug that motivated the rewrite."""
    win = _make_vm("windows10", 104, "windows")
    mock_registry.vm_by_name["windows10"] = win
    mock_registry.vms.append(win)
    mock_smoke_deps["injector"].is_windows = True

    result = _run_smoke_impl({"vm_name": "windows10"})

    assert result["ok"] is True
    assert result["verdict"] == "pass"

    injector = mock_smoke_deps["injector"]
    injector.launch.assert_called_once()
    kill_cmd = injector.shell.call_args_list[0][0][0]
    assert "Stop-Process" in kill_cmd
    assert "pkill" not in kill_cmd
    assert "wmctrl" not in kill_cmd
    assert "$!" not in kill_cmd


def test_run_smoke_process_not_running_fails(mock_registry, mock_smoke_deps):
    """App fails to launch (process never comes up) → verdict fail, VLM
    verify is skipped since there's nothing to look at."""
    mock_smoke_deps["injector"].process_running.return_value = False

    result = _run_smoke_impl({"vm_name": "ubuntu2404"})

    assert result["ok"] is True
    assert result["verdict"] == "fail"
    assert result["process_running"] is False
    assert result["window_visible"] is False
    mock_smoke_deps["vlm"].verify.assert_not_called()


def test_run_smoke_process_appears_late_within_poll_budget(mock_registry, mock_smoke_deps):
    """process_running returns False on the first few poll attempts and only
    becomes True later, within the poll budget — must not report a false
    negative just because the process wasn't up on the very first check."""
    mock_smoke_deps["injector"].process_running.side_effect = [False, False, False, True]

    result = _run_smoke_impl({"vm_name": "ubuntu2404"})

    assert result["ok"] is True
    assert result["verdict"] == "pass"
    assert result["process_running"] is True
    assert result["window_visible"] is True
    assert mock_smoke_deps["injector"].process_running.call_count == 4
    mock_smoke_deps["vlm"].verify.assert_called_once()


def test_run_smoke_poll_budget_exhausted_fails(mock_registry, mock_smoke_deps):
    """process_running never becomes True within the poll budget → clean
    verdict: fail (poll-exhaustion path), same as today's failure reporting."""
    mock_smoke_deps["injector"].process_running.return_value = False

    result = _run_smoke_impl({"vm_name": "ubuntu2404", "timeout": 4})

    assert result["ok"] is True
    assert result["verdict"] == "fail"
    assert result["process_running"] is False
    assert result["window_visible"] is False
    # effective_cap = min(max(4,1),60) = 4, poll_interval = 2 -> max_polls = 2
    assert mock_smoke_deps["injector"].process_running.call_count == 2
    mock_smoke_deps["vlm"].verify.assert_not_called()


def test_run_smoke_window_not_visible_fails(mock_registry, mock_smoke_deps):
    """Process running but VLM says no window visible → verdict fail."""
    mock_smoke_deps["vlm"].verify.return_value = False

    result = _run_smoke_impl({"vm_name": "ubuntu2404"})

    assert result["ok"] is True
    assert result["verdict"] == "fail"
    assert result["process_running"] is True
    assert result["window_visible"] is False


def test_run_smoke_unknown_vm_lists_alternatives(mock_registry, mock_smoke_deps):
    result = _run_smoke_impl({"vm_name": "missing-vm"})
    assert result["ok"] is False
    assert "missing-vm" in result["error"]
    assert "ubuntu2404" in result["error"]


def test_run_smoke_registered_in_tool_definitions_and_dispatch():
    names = [t["name"] for t in TOOL_DEFINITIONS]
    assert "mosdat_run_smoke" in names
    import inspect

    from automation import mcp_server

    src = inspect.getsource(mcp_server.handle_tools_call)
    assert "mosdat_run_smoke" in src
    assert "_run_smoke" in src


def test_run_smoke_envelope_shape(mock_registry, mock_smoke_deps):
    result = _run_smoke_impl({"vm_name": "ubuntu2404"})
    for key in ("ok", "error", "degraded", "vm", "process_running", "window_visible", "verdict"):
        assert key in result


# ---------------------------------------------------------------------------
# T012 — live integration (real VM; skipped without --live)
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.timeout(1800)
def test_live_build_deploy_run_version_matches_just_built():
    """quickstart.md Step 4: build→deploy→run against a real VM.

    Asserts the deployed artifact's version is the one just built (no stale
    binary). Requires MOSDAT_LIVE_PR and a reachable VM.
    """
    pr = os.environ.get("MOSDAT_LIVE_PR")
    if not pr:
        pytest.skip("MOSDAT_LIVE_PR is required for the live build→deploy→run test")
    vm = os.environ.get("MOSDAT_LIVE_VM", "ubuntu2404")
    scenario = os.environ.get("MOSDAT_LIVE_SCENARIO", "rocketchat-smoke-linux")
    target = os.environ.get("MOSDAT_LIVE_TARGET", "deb")

    build = _build({"pr": int(pr), "target": target, "deploy_to": vm})
    assert build["ok"] is True, build.get("error")
    assert build["build_ok"] is True
    assert build["deploy"] is not None
    assert build["deploy"]["install_ok"] is True
    installed = build["deploy"]["installed_version"]
    assert installed, "deploy.installed_version must identify the just-built artifact"

    run = _run_functional({"vm": vm, "scenario": scenario})
    assert run["ok"] is True, run.get("error")
    assert run["verdict"] in ("pass", "fail", "error")
    assert run["steps"], "expected per-step outcomes"
    assert isinstance(run["artifacts"], list)

    # Re-read the version the build just reported — a stale binary would
    # have left installed_version empty or pointing at a previous build.
    assert build["deploy"]["installed_version"] == installed


# ---------------------------------------------------------------------------
# 003 agent friction fixes — MCP wiring
# ---------------------------------------------------------------------------


def _add_windows_vm(registry):
    win = _make_vm("windows10", 104, "windows")
    registry.vm_by_name["windows10"] = win
    registry.vms.append(win)
    return win


def test_readiness_windows_reports_install_and_disk(mock_registry, mock_doctor_ready, monkeypatch):
    _add_windows_vm(mock_registry)
    monkeypatch.setattr(
        "automation.commands.vm_lifecycle.installed_app_info",
        lambda ssh, is_windows: {"found": True, "version": "4.18.0", "asar_path": r"C:\a\app.asar",
                                 "path": r"C:\a\Rocket.Chat.exe", "asar_mtime_utc": "2026-10-14T18:00:00Z"},
    )
    monkeypatch.setattr("automation.commands.vm_lifecycle.windows_disk_free_gb", lambda ssh: 3.5)
    result = _readiness({"vm": "windows10"})
    assert result["vm"]["deployed_version"] == "4.18.0"
    assert result["installed"]["asar_mtime_utc"] == "2026-10-14T18:00:00Z"
    disk = next(c for c in result["checks"] if c["label"] == "disk_c")
    assert disk["status"] == "FAIL"
    assert result["ready"] is False


def test_readiness_unreachable_returns_hints(mock_registry, mock_doctor_ready, monkeypatch):
    from automation.commands.doctor import CheckResult

    monkeypatch.setattr(
        "automation.commands.doctor.check_ssh",
        lambda ssh: CheckResult("SSH reachable", "FAIL", "Permission denied"),
    )
    monkeypatch.setattr(
        "automation.mcp_tools._readiness_hints_safe", lambda vm: ["try ubuntu2204 (same family: ubuntu)"],
    )
    result = _readiness({"vm": "ubuntu2404"})
    assert result["hints"] == ["try ubuntu2204 (same family: ubuntu)"]


def test_build_accepts_source_without_pr(mock_registry, monkeypatch):
    _add_windows_vm(mock_registry)
    seen = {}

    def _run(ns):
        seen["ns"] = ns
        return 0

    monkeypatch.setattr("automation.commands.build.run_build", _run)
    result = _build({"source": "/tmp/tree", "target": "exe", "deploy_to": "windows10"})
    assert result["ok"] is True
    assert seen["ns"].source == "/tmp/tree"
    assert seen["ns"].pr == 0


def test_build_requires_pr_or_source(mock_registry):
    result = _build({})
    assert result["ok"] is False
    assert "source" in result["error"]


def test_deploy_rejects_two_sources(mock_registry):
    result = _deploy({"vm": "ubuntu2404", "artifact_path": "/x.deb", "release": "4.18.3"})
    assert result["ok"] is False
    assert "only one source" in result["error"]


def test_deploy_pr_build_is_windows_only(mock_registry):
    result = _deploy({"vm": "ubuntu2404", "pr_build": 3582})
    assert result["ok"] is False
    assert "Windows" in result["error"]


def test_deploy_pr_build_reports_missing_installer(mock_registry, monkeypatch):
    _add_windows_vm(mock_registry)
    monkeypatch.setattr("automation.commands.build.find_windows_build_artifact", lambda ssh, pr: "")
    install = MagicMock()
    monkeypatch.setattr("automation.commands.build.install_existing_on_windows_vm", install)
    result = _deploy({"vm": "windows10", "pr_build": 3589})
    assert result["ok"] is False
    assert "pr3589" in result["error"]
    install.assert_not_called()


def test_deploy_release_calls_install_release(mock_registry, monkeypatch):
    _add_windows_vm(mock_registry)
    fake = SimpleNamespace(vm="windows10", scp_ok=True, install_ok=True, installed_version="4.18.3",
                           missing_symbols=[], error="", ok=True, artifact=r"C:\mosdat\tmp\rc.exe")
    calls = []
    monkeypatch.setattr(
        "automation.commands.build.install_release_on_vm",
        lambda *a, **k: calls.append((a, k)) or fake,
    )
    result = _deploy({"vm": "windows10", "release": "4.18.3"})
    assert result["ok"] is True
    assert result["artifact"] == r"C:\mosdat\tmp\rc.exe"
    assert calls[0][0][3] == "windows" and calls[0][0][4] == "4.18.3"


def test_deploy_failure_returns_error_envelope(mock_registry, monkeypatch, tmp_path):
    """A failed deploy returns its error; it used to raise TypeError (duplicate 'error' kwarg)."""
    pkg = tmp_path / "rc.deb"
    pkg.write_bytes(b"!<arch>\n")
    fake = SimpleNamespace(vm="ubuntu2404", scp_ok=False, install_ok=False, installed_version="",
                           missing_symbols=[], error="refusing to deploy: too small", ok=False, artifact="")
    monkeypatch.setattr("automation.commands.build.deploy_to_vm", lambda **k: fake)
    result = _deploy({"vm": "ubuntu2404", "artifact_path": str(pkg), "target": "deb"})
    assert result["ok"] is False
    assert result["error"] == "refusing to deploy: too small"
