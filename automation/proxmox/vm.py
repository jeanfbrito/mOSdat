import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from ..config import VMConfig, Package, ProjectConfig
from .api import ProxmoxAPI, ProxmoxAPIError
from .gpu import ProxmoxLockTimeout  # noqa: F401 — re-exported for callers
from ..transport.ssh import SSHClient, wait_for_ssh

# Lock directory — override with MOSDAT_LOCK_DIR env var (e.g. in tests)
_LOCK_DIR = os.environ.get("MOSDAT_LOCK_DIR", "/tmp")


class VMError(Exception):
    pass


@contextmanager
def _vm_lock(vmid: int, timeout: int = 300):
    """Exclusive per-VMID advisory lock using fcntl.flock (LOCK_EX|LOCK_NB).

    Polls every 1 s until *timeout* seconds elapse, then raises
    ProxmoxLockTimeout.  Lock file lives in ``_LOCK_DIR``
    (default /tmp, overridable via MOSDAT_LOCK_DIR).

    Usage::

        with _vm_lock(vmid):
            # snapshot / rollback / reset_vm / delete_snapshot
    """
    lock_dir = os.environ.get("MOSDAT_LOCK_DIR", _LOCK_DIR)
    lock_path = os.path.join(lock_dir, f"mosdat-vm-{vmid}.lock")
    lock_fd = open(lock_path, "w")
    acquired = False
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ProxmoxLockTimeout(
                        f"Could not acquire VM lock for VMID {vmid} within {timeout}s; "
                        f"another mosdat process holds the lock — check "
                        f"`lsof {lock_path}`"
                    )
                time.sleep(1)
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
        yield lock_path
    finally:
        if acquired:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


class VMOperations:
    def __init__(self, api: ProxmoxAPI, vm: VMConfig, config: ProjectConfig):
        self.api = api
        self.vm = vm
        self.config = config
        self.ssh = SSHClient(vm.ip, vm.user)

    def _app_env(self, package: Package, pkg_filename: str) -> str:
        """Build environment variable prefix for test scripts."""
        app = self.config.app
        if package.app_path:
            app_path = package.app_path.format(file=pkg_filename)
        else:
            app_path = app.binary

        process_name = package.process_name or app.process_name

        if self.vm.is_windows:
            parts = [
                f"$env:APP_PATH='{app_path}';",
                f"$env:PROCESS_NAME='{process_name}';",
                f"$env:TEST_TIMEOUT='{app.timeout}';",
            ]
            if app.args:
                parts.append(f"$env:APP_ARGS='{' '.join(app.args)}';")
            return " ".join(parts)
        else:
            parts = [
                f"APP_PATH={app_path}",
                f"PROCESS_NAME={process_name}",
                f"TEST_TIMEOUT={app.timeout}",
            ]
            if app.args:
                parts.append(f"APP_ARGS='{' '.join(app.args)}'")
            return " ".join(parts)

    @property
    def _tmp_dir(self) -> str:
        return self.vm.resolved_temp_dir

    @property
    def _tests_dir(self) -> str:
        tmp = self.vm.resolved_temp_dir
        return f"{tmp}\\tests" if self.vm.is_windows else f"{tmp}/tests"

    def ensure_running(self, log_fn=print) -> bool:
        status = self.api.get_vm_status(self.vm.vmid)
        if status != "running":
            log_fn(f"Starting VM {self.vm.name} (VMID {self.vm.vmid})...")
            self.api.start_vm(self.vm.vmid)
            if not self.api.wait_for_status(self.vm.vmid, "running", timeout=60):
                raise VMError(f"VM {self.vm.name} did not start")

        log_fn(f"Waiting for SSH on {self.vm.ip}...")
        timeout = 180 if self.vm.is_windows else 120
        if not wait_for_ssh(self.vm.ip, self.vm.user, timeout=timeout):
            raise VMError(f"Cannot SSH to VM {self.vm.name} at {self.vm.ip}")

        return True

    def transfer_tests(self, log_fn=print) -> bool:
        log_fn(f"Transferring test scripts to {self.vm.name}...")

        if self.vm.is_windows:
            self.ssh.run(
                f"if (Test-Path '{self._tests_dir}') {{ Remove-Item -Recurse -Force '{self._tests_dir}' }}"
            )
            # scp -r src dest: when dest doesn't exist, creates dest with contents of src
            result = self.ssh.scp_dir_to(
                self.config.tests_windows_path, f"{self._tmp_dir}\\tests"
            )
            if not result.success:
                raise VMError(f"Failed to transfer tests: {result.stderr}")
            return True
        else:
            result = self.ssh.run(f"rm -rf {self._tests_dir}")
            if not result.success:
                log_fn(f"  Warning: Could not clean {self._tests_dir}: {result.stderr}")

            # scp -r without trailing slash creates remote dir with contents
            result = self.ssh.scp_dir_to(self.config.tests_path, f"{self._tests_dir}")
            if not result.success:
                raise VMError(f"Failed to transfer tests: {result.stderr}")

            result = self.ssh.run(f"chmod +x {self._tests_dir}/*.sh")
            return result.success

    def find_package_file(self, package: Package) -> Optional[Path]:
        pattern = package.get_file_glob(self.config.app.name, self.config.version)
        matches = list(self.config.dist_path.glob(pattern))
        if not matches:
            return None
        return matches[0]

    def deploy_package(self, package: Package, log_fn=print) -> str:
        pkg_file = self.find_package_file(package)
        if not pkg_file:
            raise VMError(f"No {package.format} package found matching pattern")

        log_fn(f"Deploying {pkg_file.name} to {self.vm.name}...")

        dest = f"{self._tmp_dir}\\" if self.vm.is_windows else "/tmp/"
        result = self.ssh.scp_to(pkg_file, dest)
        if not result.success:
            raise VMError(f"Failed to transfer package: {result.stderr}")

        install_cmd = package.install_cmd.format(file=pkg_file.name)
        log_fn(f"  Installing: {install_cmd}")
        result = self.ssh.run(install_cmd, timeout=120)
        if not result.success:
            raise VMError(f"Installation failed: {result.stderr}")

        return pkg_file.name

    def _run_tests(
        self, package: Package, pkg_filename: str, gpu: bool, log_fn=print
    ) -> tuple[int, str]:
        label = "GPU" if gpu else "without-GPU"
        log_fn(f"Running {label} tests for {package.format} on {self.vm.name}...")

        env = self._app_env(package, pkg_filename)

        if self.vm.is_windows:
            import base64

            ps_script = f"$ProgressPreference='SilentlyContinue'; {env} & '{self._tests_dir}\\run-all.ps1'"
            encoded = base64.b64encode(ps_script.encode("utf-16-le")).decode("ascii")
            cmd = f"powershell.exe -ExecutionPolicy Bypass -EncodedCommand {encoded}"
        else:
            script = (
                f"{self._tests_dir}/run-gpu-tests.sh"
                if gpu
                else f"{self._tests_dir}/run-all.sh"
            )
            cmd = f"{env} {script}"

        result = self.ssh.run(cmd, timeout=600, stream_output=True)
        return result.returncode, result.stdout

    def run_tests_no_gpu(
        self, package: Package, pkg_filename: str, log_fn=print
    ) -> tuple[int, str]:
        return self._run_tests(package, pkg_filename, gpu=False, log_fn=log_fn)

    def run_tests_gpu(
        self, package: Package, pkg_filename: str, log_fn=print
    ) -> tuple[int, str]:
        return self._run_tests(package, pkg_filename, gpu=True, log_fn=log_fn)

    def reset_vm(self, log_fn=print) -> None:
        """Hard-reset the VM (equivalent to pressing the reset button).

        Uses POST /nodes/{node}/qemu/{vmid}/status/reset.
        Non-idempotent — no retry.
        """
        with _vm_lock(self.vm.vmid):
            endpoint = f"/nodes/{self.api.config.node}/qemu/{self.vm.vmid}/status/reset"
            self.api.post(endpoint)
            log_fn(f"VM {self.vm.name} reset issued")

    # ---- C2: Snapshot / checkpoint operations ----

    def _wait_for_task(self, upid: str, timeout: int = 120) -> None:
        """Poll /nodes/{node}/tasks/{upid}/status until status==stopped.

        Raises VMError if the task fails or times out.
        Sleeps 2 s between polls (snapshot ops are slow — no busy-wait).
        """
        node = self.api.config.node
        endpoint = f"/nodes/{node}/tasks/{upid}/status"
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(2)
            try:
                data = self.api.get(endpoint).get("data", {})
            except ProxmoxAPIError:
                # Transient — keep polling
                continue
            status = data.get("status", "")
            if status == "stopped":
                exitstatus = data.get("exitstatus", "")
                if exitstatus and exitstatus != "OK":
                    raise VMError(
                        f"Proxmox task {upid} failed: exitstatus={exitstatus}"
                    )
                return
        raise VMError(f"Proxmox task {upid} did not complete within {timeout}s")

    def snapshot(self, vmid: int, name: str) -> None:
        """Create a disk-only snapshot (vmstate=0) and wait for completion."""
        with _vm_lock(vmid):
            endpoint = f"/nodes/{self.api.config.node}/qemu/{vmid}/snapshot"
            response = self.api.post(endpoint, data={"snapname": name, "vmstate": 0})
            upid = response.get("data", "")
            if not upid:
                raise VMError(f"snapshot: no UPID returned for VM {vmid} snap '{name}'")
            self._wait_for_task(upid)

    def rollback(self, vmid: int, name: str) -> None:
        """Rollback VM to a named snapshot and wait for completion."""
        with _vm_lock(vmid):
            endpoint = (
                f"/nodes/{self.api.config.node}/qemu/{vmid}/snapshot/{name}/rollback"
            )
            response = self.api.post(endpoint)
            upid = response.get("data", "")
            if not upid:
                raise VMError(f"rollback: no UPID returned for VM {vmid} snap '{name}'")
            self._wait_for_task(upid)

    def delete_snapshot(self, vmid: int, name: str) -> None:
        """Delete a named snapshot and wait for completion."""
        with _vm_lock(vmid):
            endpoint = f"/nodes/{self.api.config.node}/qemu/{vmid}/snapshot/{name}"
            response = self.api.delete(endpoint)
            upid = response.get("data", "")
            if upid:
                self._wait_for_task(upid)

    def list_snapshots(self, vmid: int) -> list:
        """Return list of snapshot names, excluding the always-present 'current' entry."""
        endpoint = f"/nodes/{self.api.config.node}/qemu/{vmid}/snapshot"
        data = self.api.get(endpoint).get("data", [])
        return [s["name"] for s in data if s.get("name") != "current"]

    def cleanup_package(self, package: Package, log_fn=print) -> None:
        log_fn(f"Cleaning up {package.format} from {self.vm.name}...")

        if package.uninstall_cmd:
            cmd = package.uninstall_cmd.format(app_name=self.config.app.name)
        else:
            cmd = "true" if not self.vm.is_windows else "echo 'no cleanup'"

        self.ssh.run(cmd, timeout=60)
