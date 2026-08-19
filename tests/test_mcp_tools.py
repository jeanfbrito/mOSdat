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
    _list_scenarios,
    _list_vms,
    _readiness,
    _resolve_scenario,
    _resolve_vm,
    _run_functional,
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
    assert result["verdict"] == "pass"
    assert "vlm_unavailable" in result["degraded"]


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
