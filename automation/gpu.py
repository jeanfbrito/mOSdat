import fcntl
import logging
import os
import time
from contextlib import contextmanager
from typing import Optional

from .config import ProxmoxConfig, VMConfig
from .proxmox import ProxmoxAPI
from .ssh import SSHClient, wait_for_ssh

logger = logging.getLogger(__name__)

_LOCK_DIR_PRIMARY = "/var/lock"
_LOCK_DIR_FALLBACK = "/tmp"
_LOCK_STALE_SECONDS = 30 * 60  # 30 minutes
_LOCK_TIMEOUT_SECONDS = 35 * 60  # 35 minutes (> stale TTL)


def _gpu_lock_path(pci_address: str) -> str:
    safe = pci_address.replace(":", "_").replace("/", "_")
    name = f"mosdat-gpu-{safe}.lock"
    primary = os.path.join(_LOCK_DIR_PRIMARY, name)
    try:
        # Probe writability without creating the file permanently
        fd = os.open(primary, os.O_CREAT | os.O_WRONLY, 0o644)
        os.close(fd)
        return primary
    except OSError:
        return os.path.join(_LOCK_DIR_FALLBACK, name)


@contextmanager
def gpu_lock(pci_address: str, timeout: int = _LOCK_TIMEOUT_SECONDS):
    """Exclusive per-PCI-address lock using fcntl.flock.

    Stale-lock detection: if the lock file mtime is older than
    _LOCK_STALE_SECONDS and we cannot acquire within that window we log a
    warning and force-acquire (LOCK_EX without LOCK_NB after the warning).

    The context manager guarantees release via finally, so SIGINT / Ctrl-C
    is handled correctly — Python's KeyboardInterrupt unwinds the with-block
    and the finally clause runs.
    """
    lock_path = _gpu_lock_path(pci_address)
    logger.debug("GPU lock path: %s", lock_path)

    lock_fd = open(lock_path, "w")
    try:
        deadline = time.monotonic() + timeout
        acquired = False

        # Check for stale lock before blocking
        try:
            mtime = os.path.getmtime(lock_path)
            age = time.time() - mtime
            if age > _LOCK_STALE_SECONDS:
                logger.warning(
                    "GPU lock file %s is %.0f s old (> %d s stale TTL) — "
                    "previous holder may have crashed; force-acquiring.",
                    lock_path, age, _LOCK_STALE_SECONDS,
                )
        except OSError:
            pass

        # Try non-blocking first; fall back to polling so we respect timeout
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Could not acquire GPU lock for {pci_address} "
                        f"within {timeout}s"
                    )
                time.sleep(1)

        # Stamp mtime so stale detection works for the next waiter
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()

        yield lock_path

    finally:
        if acquired:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


class GPUError(Exception):
    pass


class GPUManager:
    def __init__(self, api: ProxmoxAPI, config: ProxmoxConfig, vms: list[VMConfig] | None = None):
        self.api = api
        self.config = config
        self.vms = vms or []

    def find_current_owner(self) -> Optional[int]:
        for vm in self.vms:
            if self.api.has_gpu_attached(vm.vmid):
                return vm.vmid
        return None

    def detach_from_current(self, log_fn=print) -> Optional[int]:
        pci = self.config.gpu_pci_address
        with gpu_lock(pci):
            current_owner = self.find_current_owner()
            if current_owner is None:
                log_fn("GPU not attached to any VM")
                return None

            log_fn(f"Detaching GPU from VM {current_owner}")

            status = self.api.get_vm_status(current_owner)
            if status == "running":
                log_fn(f"  Stopping VM {current_owner}...")
                self.api.stop_vm(current_owner)
                if not self.api.wait_for_status(current_owner, "stopped", timeout=60):
                    raise GPUError(f"VM {current_owner} did not stop in time")

            log_fn(f"  Removing GPU config from VM {current_owner}")
            self.api.detach_gpu(current_owner)

            log_fn(f"  Starting VM {current_owner} without GPU...")
            self.api.start_vm(current_owner)

            return current_owner

    def attach_to_vm(self, vmid: int, log_fn=print) -> str:
        """Attach GPU to vmid.

        Critical section (held under gpu_lock):
          - find_current_owner()
          - detach from current owner (stop VM, detach_gpu, restart)
          - stop target VM if running
          - attach_gpu to target VM
          - start target VM
          - wait_for_ip (GPU online verification)
        """
        pci = self.config.gpu_pci_address
        with gpu_lock(pci):
            log_fn(f"Attaching GPU to VM {vmid}")

            if self.api.has_gpu_attached(vmid):
                log_fn(f"  GPU already attached to VM {vmid}")
                status = self.api.get_vm_status(vmid)
                if status != "running":
                    log_fn(f"  Starting VM {vmid}...")
                    self.api.start_vm(vmid)
                ip = self.api.wait_for_ip(vmid, timeout=120)
                if not ip:
                    raise GPUError(f"Could not get IP for VM {vmid}")
                return ip

            current_owner = self.find_current_owner()
            if current_owner is not None:
                log_fn(f"Detaching GPU from VM {current_owner}")

                status = self.api.get_vm_status(current_owner)
                if status == "running":
                    log_fn(f"  Stopping VM {current_owner}...")
                    self.api.stop_vm(current_owner)
                    if not self.api.wait_for_status(current_owner, "stopped", timeout=60):
                        raise GPUError(f"VM {current_owner} did not stop in time")

                log_fn(f"  Removing GPU config from VM {current_owner}")
                self.api.detach_gpu(current_owner)

                log_fn(f"  Starting VM {current_owner} without GPU...")
                self.api.start_vm(current_owner)

                time.sleep(2)

            status = self.api.get_vm_status(vmid)
            if status == "running":
                log_fn(f"  Stopping VM {vmid} to attach GPU...")
                self.api.stop_vm(vmid)
                if not self.api.wait_for_status(vmid, "stopped", timeout=60):
                    raise GPUError(f"VM {vmid} did not stop in time")

            log_fn(f"  Adding GPU config to VM {vmid}")
            self.api.attach_gpu(vmid, pci)

            log_fn(f"  Starting VM {vmid} with GPU...")
            self.api.start_vm(vmid)

            log_fn(f"  Waiting for VM {vmid} to get IP...")
            ip = self.api.wait_for_ip(vmid, timeout=120)
            if not ip:
                raise GPUError(f"Could not get IP for VM {vmid}")

            log_fn(f"  VM {vmid} IP: {ip}")
            return ip

    def verify_gpu_visible(self, vm: VMConfig, log_fn=print) -> bool:
        log_fn(f"  Verifying GPU visible in VM {vm.name}...")

        if not wait_for_ssh(vm.ip, vm.user, timeout=60):
            log_fn(f"  ERROR: Cannot SSH to VM {vm.name}")
            return False

        ssh = SSHClient(vm.ip, vm.user)
        result = ssh.run("lspci | grep -i nvidia", timeout=30)

        if result.success and "NVIDIA" in result.stdout:
            log_fn(f"  GPU visible: {result.stdout.strip()}")
            return True

        log_fn(f"  WARNING: GPU not visible in VM {vm.name}")
        return False
