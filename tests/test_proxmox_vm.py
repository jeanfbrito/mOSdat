"""Unit tests for VMOperations snapshot/rollback/list/delete/reset_vm.

ProxmoxAPI HTTP calls are mocked — no network.
Loads vm.py directly by file path to bypass the automation.proxmox.__init__
chain (which imports gpu.py and api.py requiring full installation).
"""

import sys
import types
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PROJ = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Stub the heavy dependencies vm.py pulls in via relative imports
# ---------------------------------------------------------------------------

# Force-replace any stale installed modules with fresh stubs.
# sys.modules may already point to stale egg copies — we overwrite them.
def _force_stub(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m  # always overwrite
    return m


ProxmoxAPIError = type("ProxmoxAPIError", (Exception,), {})
ProxmoxAPI_stub = MagicMock()
_force_stub("automation.proxmox.api",
            ProxmoxAPI=ProxmoxAPI_stub,
            ProxmoxAPIError=ProxmoxAPIError)

_force_stub("automation.config",
            VMConfig=object,
            Package=object,
            ProjectConfig=object,
            ProxmoxConfig=object)

SSHResult_stub = type("SSHResult", (), {
    "returncode": 0, "stdout": "", "stderr": "", "success": True
})
_force_stub("automation.transport.ssh",
            SSHClient=MagicMock(),
            wait_for_ssh=MagicMock(return_value=True),
            SSHResult=SSHResult_stub)
_force_stub("automation.transport", ssh=sys.modules["automation.transport.ssh"])

# Load vm.py directly
_vm_spec = importlib.util.spec_from_file_location(
    "automation.proxmox.vm",
    _PROJ / "automation" / "proxmox" / "vm.py",
)
_vm_mod = importlib.util.module_from_spec(_vm_spec)
_vm_mod.__package__ = "automation.proxmox"
sys.modules["automation.proxmox.vm"] = _vm_mod
_vm_spec.loader.exec_module(_vm_mod)

VMOperations = _vm_mod.VMOperations
VMError = _vm_mod.VMError


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def _make_vm_ops():
    api = MagicMock()
    api.config.node = "pve"
    api.post.return_value = {"data": "UPID:pve:00001234:snap"}
    api.get.return_value = {"data": {"status": "stopped", "exitstatus": "OK"}}
    api.delete.return_value = {"data": "UPID:pve:00001235:del"}

    vm = MagicMock()
    vm.vmid = 105
    vm.name = "test-vm"
    vm.ip = "192.168.1.100"
    vm.user = "root"
    vm.is_windows = False
    vm.resolved_temp_dir = "/tmp/mosdat"

    config = MagicMock()
    config.app.name = "myapp"

    # Patch SSHClient so __init__ doesn't open a connection
    with patch.object(_vm_mod, "SSHClient", MagicMock()):
        ops = VMOperations(api=api, vm=vm, config=config)
    ops.ssh = MagicMock()
    return ops, api


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------

class TestSnapshot:
    def test_posts_to_correct_endpoint(self):
        ops, api = _make_vm_ops()
        with patch.object(ops, "_wait_for_task"):
            ops.snapshot(105, "snap-step-1")
        api.post.assert_called_once_with(
            "/nodes/pve/qemu/105/snapshot",
            data={"snapname": "snap-step-1", "vmstate": 0},
        )

    def test_raises_if_no_upid(self):
        ops, api = _make_vm_ops()
        api.post.return_value = {"data": ""}
        with pytest.raises(VMError, match="no UPID"):
            ops.snapshot(105, "snap-x")

    def test_calls_wait_for_task(self):
        ops, api = _make_vm_ops()
        with patch.object(ops, "_wait_for_task") as mock_wait:
            ops.snapshot(105, "snap-y")
        mock_wait.assert_called_once()


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------

class TestRollback:
    def test_posts_to_correct_endpoint(self):
        ops, api = _make_vm_ops()
        with patch.object(ops, "_wait_for_task"):
            ops.rollback(105, "snap-step-1")
        api.post.assert_called_once_with(
            "/nodes/pve/qemu/105/snapshot/snap-step-1/rollback"
        )

    def test_raises_if_no_upid(self):
        ops, api = _make_vm_ops()
        api.post.return_value = {"data": ""}
        with pytest.raises(VMError, match="no UPID"):
            ops.rollback(105, "snap-x")


# ---------------------------------------------------------------------------
# list_snapshots
# ---------------------------------------------------------------------------

class TestListSnapshots:
    def test_excludes_current_entry(self):
        ops, api = _make_vm_ops()
        api.get.return_value = {"data": [
            {"name": "current"},
            {"name": "snap-step-1"},
            {"name": "snap-step-2"},
        ]}
        assert ops.list_snapshots(105) == ["snap-step-1", "snap-step-2"]

    def test_empty_list(self):
        ops, api = _make_vm_ops()
        api.get.return_value = {"data": [{"name": "current"}]}
        assert ops.list_snapshots(105) == []

    def test_correct_endpoint(self):
        ops, api = _make_vm_ops()
        api.get.return_value = {"data": []}
        ops.list_snapshots(105)
        api.get.assert_called_once_with("/nodes/pve/qemu/105/snapshot")


# ---------------------------------------------------------------------------
# delete_snapshot
# ---------------------------------------------------------------------------

class TestDeleteSnapshot:
    def test_correct_endpoint(self):
        ops, api = _make_vm_ops()
        with patch.object(ops, "_wait_for_task"):
            ops.delete_snapshot(105, "snap-step-1")
        api.delete.assert_called_once_with("/nodes/pve/qemu/105/snapshot/snap-step-1")

    def test_no_upid_skips_wait(self):
        ops, api = _make_vm_ops()
        api.delete.return_value = {"data": ""}
        with patch.object(ops, "_wait_for_task") as mock_wait:
            ops.delete_snapshot(105, "snap-empty")
        mock_wait.assert_not_called()


# ---------------------------------------------------------------------------
# reset_vm
# ---------------------------------------------------------------------------

class TestResetVm:
    def test_posts_to_status_reset(self):
        ops, api = _make_vm_ops()
        ops.reset_vm(log_fn=lambda m: None)
        api.post.assert_called_once_with("/nodes/pve/qemu/105/status/reset")
