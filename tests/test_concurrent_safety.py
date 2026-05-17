"""Tests for H3.3 concurrent-run safety — VM and GPU advisory locks.

Loads vm.py and gpu.py directly by file path (same pattern as
test_proxmox_vm.py) to avoid the full automation package import chain.
Uses MOSDAT_LOCK_DIR env var to redirect lock files into tmp_path.
"""

import fcntl
import os
import sys
import time
import types
import importlib.util
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PROJ = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Stub heavy dependencies (same pattern as test_proxmox_vm.py)
# ---------------------------------------------------------------------------

# Save originals of any modules we stub so we can restore them at module
# teardown — prevents stub pollution from bleeding into sibling test files
# (e.g. test_preflight needs the real automation.transport.ssh).
_STUBBED_ORIGINALS: dict[str, object] = {}

def _force_stub(name, **attrs):
    import importlib.util
    real = sys.modules.get(name)
    # Save the original module (or sentinel for "not present") on first stub.
    if name not in _STUBBED_ORIGINALS:
        _STUBBED_ORIGINALS[name] = real  # may be None
    m = types.ModuleType(name)
    # Preserve __path__ for packages so child imports (e.g.
    # automation.transport.vlm_cache) still resolve when this stub overlays
    # a real package. We can't import the real package (heavy deps) so we
    # ask the import system to locate it on disk without executing its
    # __init__.py.
    path = getattr(real, "__path__", None) if real is not None else None
    if path is None:
        try:
            spec = importlib.util.find_spec(name)
            if spec is not None and spec.submodule_search_locations:
                path = list(spec.submodule_search_locations)
        except Exception:
            path = None
    if path is not None:
        m.__path__ = path
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
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

# Load gpu.py directly
_gpu_spec = importlib.util.spec_from_file_location(
    "automation.proxmox.gpu",
    _PROJ / "automation" / "proxmox" / "gpu.py",
)
_gpu_mod = importlib.util.module_from_spec(_gpu_spec)
_gpu_mod.__package__ = "automation.proxmox"
sys.modules["automation.proxmox.gpu"] = _gpu_mod
_gpu_spec.loader.exec_module(_gpu_mod)

# Load vm.py directly (imports ProxmoxLockTimeout from gpu)
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
ProxmoxLockTimeout = _gpu_mod.ProxmoxLockTimeout
_vm_lock = _vm_mod._vm_lock
_host_gpu_lock = _gpu_mod._host_gpu_lock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vm_ops(lock_dir: str):
    """Build a VMOperations instance with lock dir redirected to lock_dir."""
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

    with patch.object(_vm_mod, "SSHClient", MagicMock()):
        ops = VMOperations(api=api, vm=vm, config=config)
    ops.ssh = MagicMock()
    return ops, api


# ---------------------------------------------------------------------------
# VM lock tests
# ---------------------------------------------------------------------------

class TestVmLockConcurrency:
    def test_two_threads_serialize_on_same_vmid(self, tmp_path, monkeypatch):
        """Second thread blocks on snapshot until first releases the lock."""
        monkeypatch.setenv("MOSDAT_LOCK_DIR", str(tmp_path))
        results = []
        barrier = threading.Event()

        def slow_snapshot(ops):
            """Snapshot that signals mid-way so thread 2 can attempt to start."""
            orig_wait = ops._wait_for_task

            def patched_wait(upid):
                barrier.set()       # signal: we're inside the lock
                time.sleep(0.3)     # hold lock briefly
                orig_wait(upid)

            with patch.object(ops, "_wait_for_task", patched_wait):
                ops.snapshot(105, "snap-t1")
            results.append("t1-done")

        def second_snapshot(ops):
            barrier.wait(timeout=2)  # wait until t1 holds the lock
            ops.snapshot(105, "snap-t2")
            results.append("t2-done")

        ops1, _ = _make_vm_ops(str(tmp_path))
        ops2, _ = _make_vm_ops(str(tmp_path))
        # Both share the same _wait_for_task mock behaviour (patched on ops1)
        ops2._wait_for_task = MagicMock()

        t1 = threading.Thread(target=slow_snapshot, args=(ops1,))
        t2 = threading.Thread(target=second_snapshot, args=(ops2,))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert results == ["t1-done", "t2-done"], f"Unexpected order: {results}"

    def test_lock_released_on_exception(self, tmp_path, monkeypatch):
        """Lock must be released even when the locked body raises."""
        monkeypatch.setenv("MOSDAT_LOCK_DIR", str(tmp_path))
        ops, api = _make_vm_ops(str(tmp_path))

        def raising_wait(upid):
            raise RuntimeError("simulated failure")

        with patch.object(ops, "_wait_for_task", raising_wait):
            with pytest.raises(RuntimeError, match="simulated failure"):
                ops.snapshot(105, "snap-err")

        # Lock must be free now — acquire immediately without timeout
        lock_path = os.path.join(str(tmp_path), "mosdat-vm-105.lock")
        fd = open(lock_path, "w")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            fd.close()

    def test_timeout_raises_proxmox_lock_timeout(self, tmp_path, monkeypatch):
        """ProxmoxLockTimeout raised when lock is externally held."""
        monkeypatch.setenv("MOSDAT_LOCK_DIR", str(tmp_path))
        lock_path = os.path.join(str(tmp_path), "mosdat-vm-105.lock")

        # Hold lock externally
        holder_fd = open(lock_path, "w")
        fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with pytest.raises(ProxmoxLockTimeout, match="VMID 105"):
                with _vm_lock(105, timeout=2):
                    pass
        finally:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            holder_fd.close()

    def test_timeout_hint_contains_lsof(self, tmp_path, monkeypatch):
        """Timeout error message includes lsof hint."""
        monkeypatch.setenv("MOSDAT_LOCK_DIR", str(tmp_path))
        lock_path = os.path.join(str(tmp_path), "mosdat-vm-200.lock")

        holder_fd = open(lock_path, "w")
        fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with pytest.raises(ProxmoxLockTimeout, match="lsof"):
                with _vm_lock(200, timeout=1):
                    pass
        finally:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            holder_fd.close()


# ---------------------------------------------------------------------------
# GPU host lock tests
# ---------------------------------------------------------------------------

class TestGpuHostLockConcurrency:
    def test_gpu_lock_contention(self, tmp_path, monkeypatch):
        """Two threads competing for the same host GPU lock serialize."""
        monkeypatch.setenv("MOSDAT_LOCK_DIR", str(tmp_path))
        order = []
        barrier = threading.Event()

        def hold_then_release():
            with _host_gpu_lock("pve", timeout=5):
                barrier.set()
                time.sleep(0.3)
                order.append("first")

        def wait_then_acquire():
            barrier.wait(timeout=2)
            with _host_gpu_lock("pve", timeout=5):
                order.append("second")

        t1 = threading.Thread(target=hold_then_release)
        t2 = threading.Thread(target=wait_then_acquire)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert order == ["first", "second"], f"Lock not serializing: {order}"

    def test_gpu_lock_timeout_raises(self, tmp_path, monkeypatch):
        """ProxmoxLockTimeout raised when GPU host lock is externally held."""
        monkeypatch.setenv("MOSDAT_LOCK_DIR", str(tmp_path))
        lock_path = os.path.join(str(tmp_path), "mosdat-gpu-pve.lock")

        holder_fd = open(lock_path, "w")
        fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with pytest.raises(ProxmoxLockTimeout, match="pve"):
                with _host_gpu_lock("pve", timeout=1):
                    pass
        finally:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            holder_fd.close()

    def test_gpu_lock_released_on_exception(self, tmp_path, monkeypatch):
        """GPU host lock released even when body raises."""
        monkeypatch.setenv("MOSDAT_LOCK_DIR", str(tmp_path))
        lock_path = os.path.join(str(tmp_path), "mosdat-gpu-pve.lock")

        with pytest.raises(ValueError, match="boom"):
            with _host_gpu_lock("pve", timeout=5):
                raise ValueError("boom")

        # Must be re-acquirable immediately
        fd = open(lock_path, "w")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            fd.close()

    def test_pci_gpu_lock_timeout_raises(self, tmp_path, monkeypatch):
        """ProxmoxLockTimeout raised when GPU PCI address lock is externally held."""
        pci_address = "0000:01:00"
        safe_pci = pci_address.replace(":", "_")
        lock_path = os.path.join(str(tmp_path), f"mosdat-gpu-{safe_pci}.lock")

        # Mock _gpu_lock_path to return our test lock path
        monkeypatch.setattr(_gpu_mod, "_gpu_lock_path", lambda pci: lock_path)

        holder_fd = open(lock_path, "w")
        fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with pytest.raises(ProxmoxLockTimeout, match="0000:01:00"):
                with _gpu_mod.gpu_lock(pci_address, timeout=1):
                    pass
        finally:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            holder_fd.close()

    def test_pci_gpu_lock_timeout_hint_contains_lsof(self, tmp_path, monkeypatch):
        """PCI GPU lock timeout error message includes lsof hint."""
        pci_address = "0000:02:00"
        safe_pci = pci_address.replace(":", "_")
        lock_path = os.path.join(str(tmp_path), f"mosdat-gpu-{safe_pci}.lock")

        # Mock _gpu_lock_path to return our test lock path
        monkeypatch.setattr(_gpu_mod, "_gpu_lock_path", lambda pci: lock_path)

        holder_fd = open(lock_path, "w")
        fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with pytest.raises(ProxmoxLockTimeout, match="lsof"):
                with _gpu_mod.gpu_lock(pci_address, timeout=1):
                    pass
        finally:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            holder_fd.close()

    def test_attach_to_vm_uses_host_lock(self, tmp_path, monkeypatch):
        """GPUManager.attach_to_vm acquires the host GPU lock."""
        monkeypatch.setenv("MOSDAT_LOCK_DIR", str(tmp_path))

        api = MagicMock()
        api.config.node = "pve"
        api.has_gpu_attached.return_value = True
        api.get_vm_status.return_value = "running"
        api.wait_for_ip.return_value = "10.0.0.5"

        config = MagicMock()
        config.gpu_pci_address = "0000:01:00.0"

        gm = _gpu_mod.GPUManager(api=api, config=config, vms=[])

        lock_acquired = []

        original = _gpu_mod._host_gpu_lock

        def spy_lock(host, timeout=300):
            lock_acquired.append(host)
            return original(host, timeout=timeout)

        with patch.object(_gpu_mod, "_host_gpu_lock", spy_lock):
            gm.attach_to_vm(101, log_fn=lambda m: None)

        assert lock_acquired == ["pve"], "attach_to_vm did not acquire host GPU lock"


# ---------------------------------------------------------------------------
# Restore stubbed modules at module teardown so sibling test files
# (test_preflight, test_doctor, ...) can import the real automation.transport
# submodules.
# ---------------------------------------------------------------------------

def teardown_module(module):
    for _name, _orig in _STUBBED_ORIGINALS.items():
        if _orig is None:
            sys.modules.pop(_name, None)
        else:
            sys.modules[_name] = _orig
