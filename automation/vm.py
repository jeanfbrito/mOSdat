from pathlib import Path
from typing import Optional

from .config import VMConfig, Package, ProjectConfig
from .proxmox import ProxmoxAPI
from .ssh import SSHClient, wait_for_ssh


class VMError(Exception):
    pass


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

        if self.vm.is_windows:
            parts = [
                f"$env:APP_PATH='{app_path}';",
                f"$env:PROCESS_NAME='{app.process_name}';",
                f"$env:TEST_TIMEOUT='{app.timeout}';",
            ]
            if app.args:
                parts.append(f"$env:APP_ARGS='{' '.join(app.args)}';")
            return " ".join(parts)
        else:
            parts = [
                f"APP_PATH={app_path}",
                f"PROCESS_NAME={app.process_name}",
                f"TEST_TIMEOUT={app.timeout}",
            ]
            if app.args:
                parts.append(f"APP_ARGS='{' '.join(app.args)}'")
            return " ".join(parts)

    @property
    def _tmp_dir(self) -> str:
        return "C:\\tmp" if self.vm.is_windows else "/tmp"

    @property
    def _tests_dir(self) -> str:
        return "C:\\tmp\\tests" if self.vm.is_windows else "/tmp/tests"

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
            self.ssh.run(f"if (Test-Path {self._tests_dir}) {{ Remove-Item -Recurse -Force {self._tests_dir} }}")
            self.ssh.run(f"New-Item -ItemType Directory -Force -Path {self._tests_dir}")
            result = self.ssh.scp_dir_to(self.config.tests_windows_path, f"{self._tmp_dir}\\")
            if not result.success:
                raise VMError(f"Failed to transfer tests: {result.stderr}")
            return True
        else:
            result = self.ssh.run("rm -rf /tmp/tests")
            if not result.success:
                log_fn(f"  Warning: Could not clean /tmp/tests: {result.stderr}")

            result = self.ssh.scp_dir_to(self.config.tests_path, "/tmp/")
            if not result.success:
                raise VMError(f"Failed to transfer tests: {result.stderr}")

            result = self.ssh.run("chmod +x /tmp/tests/*.sh")
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

    def _run_tests(self, package: Package, pkg_filename: str, gpu: bool, log_fn=print) -> tuple[int, str]:
        label = "GPU" if gpu else "without-GPU"
        log_fn(f"Running {label} tests for {package.format} on {self.vm.name}...")

        env = self._app_env(package, pkg_filename)

        if self.vm.is_windows:
            cmd = f"powershell.exe -ExecutionPolicy Bypass -Command \"{env} & '{self._tests_dir}\\run-all.ps1'\""
        else:
            script = "/tmp/tests/run-gpu-tests.sh" if gpu else "/tmp/tests/run-all.sh"
            cmd = f"{env} {script}"

        result = self.ssh.run(cmd, timeout=600, stream_output=True)
        return result.returncode, result.stdout

    def run_tests_no_gpu(self, package: Package, pkg_filename: str, log_fn=print) -> tuple[int, str]:
        return self._run_tests(package, pkg_filename, gpu=False, log_fn=log_fn)

    def run_tests_gpu(self, package: Package, pkg_filename: str, log_fn=print) -> tuple[int, str]:
        return self._run_tests(package, pkg_filename, gpu=True, log_fn=log_fn)

    def cleanup_package(self, package: Package, log_fn=print) -> None:
        log_fn(f"Cleaning up {package.format} from {self.vm.name}...")

        if package.uninstall_cmd:
            cmd = package.uninstall_cmd.format(app_name=self.config.app.name)
        else:
            cmd = "true" if not self.vm.is_windows else "echo 'no cleanup'"

        self.ssh.run(cmd, timeout=60)
