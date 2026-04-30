import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from .config import ProjectConfig, VMConfig, Package
from .gpu import GPUManager
from .proxmox import ProxmoxAPI
from .state import StateManager, TestStatus, Phase, CompletionError, validate_completion
from .vm import VMOperations


LogFn = Callable[[str], None]


def log(msg: str) -> None:
    print(f"[mOSdat] {msg}")
    sys.stdout.flush()


class TestRunner:
    def __init__(self, config: ProjectConfig):
        self.config = config
        self.api = ProxmoxAPI(config.proxmox)
        self.gpu = GPUManager(self.api, config.proxmox, config.vms)
        self.state_manager = StateManager(config.state_file, config.version)
        self._pkg_filenames: dict[str, str] = {}

    @property
    def state(self):
        return self.state_manager.state

    def get_vms_to_test(self) -> list[VMConfig]:
        if self.config.only_vm:
            vm = self.config.vm_by_name.get(self.config.only_vm)
            if not vm:
                available = ", ".join(self.config.vm_by_name.keys())
                raise ValueError(f"Unknown VM: {self.config.only_vm}. Available: {available}")
            return [vm]
        return self.config.vms

    def build_packages(self) -> bool:
        if self.config.skip_build:
            log("Skipping build (--skip-build)")
            return True

        if not self.config.build:
            log("No [build] section in config, skipping build")
            return True

        if self.state.phase != Phase.BUILD:
            log("Build already completed, skipping")
            return True

        build = self.config.build
        log(f"Building packages for {self.config.version}...")

        repo = build.repo_path
        if not repo.exists():
            log(f"ERROR: Repository not found at {repo}")
            return False

        if build.checkout:
            checkout_cmd = f"cd {repo} && git fetch --tags && git checkout {self.config.version}"
            log(f"  Checking out {self.config.version}...")
            result = subprocess.run(checkout_cmd, shell=True, capture_output=False)
            if result.returncode != 0:
                log("ERROR: Git checkout failed")
                return False

        for cmd in build.commands:
            log(f"  Running: {cmd}")
            result = subprocess.run(f"cd {repo} && {cmd}", shell=True, capture_output=False)
            if result.returncode != 0:
                log("ERROR: Build step failed")
                return False

        self.state.phase = Phase.DEPLOY
        self.state_manager.save()
        log("Build completed successfully")
        return True

    def run_vm_tests_no_gpu(self, vm: VMConfig) -> dict[str, bool]:
        results: dict[str, bool] = {}
        vm_ops = VMOperations(self.api, vm, self.config)

        log(f"\n{'='*60}")
        log(f"Testing {vm.name} (WITHOUT GPU)")
        log(f"{'='*60}")

        try:
            vm_ops.ensure_running(log)
            vm_ops.transfer_tests(log)
        except Exception as e:
            log(f"ERROR: VM setup failed: {e}")
            for pkg in vm.packages:
                self.state.mark_test_finished(vm.name, pkg.format, False, TestStatus.FAILED, error_message=str(e))
            self.state_manager.save()
            return results

        for pkg in vm.packages:
            if self.state.is_test_completed(vm.name, pkg.format, gpu=False):
                log(f"  Skipping {pkg.format} (already completed)")
                results[pkg.format] = True
                continue

            log(f"\n--- {vm.name} / {pkg.format} (no-gpu) ---")
            self.state.mark_test_started(vm.name, pkg.format, gpu=False)
            self.state_manager.save()

            try:
                pkg_filename = vm_ops.deploy_package(pkg, log)
                self._pkg_filenames[f"{vm.name}-{pkg.format}"] = pkg_filename

                log_file = self.config.results_dir / f"{vm.name}-{pkg.format}-no-gpu.log"
                log_file.parent.mkdir(parents=True, exist_ok=True)

                exit_code, output = vm_ops.run_tests_no_gpu(pkg, pkg_filename, log)

                with open(log_file, "w") as f:
                    f.write(output)

                passed = exit_code == 0
                status = TestStatus.PASSED if passed else TestStatus.FAILED
                self.state.mark_test_finished(vm.name, pkg.format, False, status, exit_code, str(log_file))
                results[pkg.format] = passed

                log(f"  Result: {'PASS' if passed else 'FAIL'} (exit {exit_code})")

            except Exception as e:
                log(f"  ERROR: {e}")
                self.state.mark_test_finished(vm.name, pkg.format, False, TestStatus.FAILED, error_message=str(e))
                results[pkg.format] = False

            self.state_manager.save()

        return results

    def run_vm_tests_gpu(self, vm: VMConfig) -> dict[str, bool]:
        results: dict[str, bool] = {}

        log(f"\n{'='*60}")
        log(f"Testing {vm.name} (WITH GPU)")
        log(f"{'='*60}")

        try:
            ip = self.gpu.attach_to_vm(vm.vmid, log)
            if not self.gpu.verify_gpu_visible(vm, log):
                log("WARNING: GPU not visible, tests may fail")
        except Exception as e:
            log(f"ERROR: GPU attachment failed: {e}")
            for pkg in vm.packages:
                self.state.mark_test_finished(vm.name, pkg.format, True, TestStatus.FAILED, error_message=str(e))
            self.state_manager.save()
            return results

        vm_ops = VMOperations(self.api, vm, self.config)

        try:
            vm_ops.transfer_tests(log)
        except Exception as e:
            log(f"ERROR: Test transfer failed: {e}")
            return results

        for pkg in vm.packages:
            if self.state.is_test_completed(vm.name, pkg.format, gpu=True):
                log(f"  Skipping {pkg.format} GPU tests (already completed)")
                results[pkg.format] = True
                continue

            log(f"\n--- {vm.name} / {pkg.format} (gpu) ---")
            self.state.mark_test_started(vm.name, pkg.format, gpu=True)
            self.state_manager.save()

            try:
                pkg_key = f"{vm.name}-{pkg.format}"
                if pkg_key in self._pkg_filenames:
                    pkg_filename = self._pkg_filenames[pkg_key]
                else:
                    pkg_filename = vm_ops.deploy_package(pkg, log)
                    self._pkg_filenames[pkg_key] = pkg_filename

                log_file = self.config.results_dir / f"{vm.name}-{pkg.format}-gpu.log"

                exit_code, output = vm_ops.run_tests_gpu(pkg, pkg_filename, log)

                with open(log_file, "w") as f:
                    f.write(output)

                passed = exit_code == 0
                status = TestStatus.PASSED if passed else TestStatus.FAILED
                self.state.mark_test_finished(vm.name, pkg.format, True, status, exit_code, str(log_file))
                results[pkg.format] = passed

                log(f"  Result: {'PASS' if passed else 'FAIL'} (exit {exit_code})")

            except Exception as e:
                log(f"  ERROR: {e}")
                self.state.mark_test_finished(vm.name, pkg.format, True, TestStatus.FAILED, error_message=str(e))
                results[pkg.format] = False

            self.state_manager.save()

        return results

    def run_all_no_gpu_tests(self) -> dict[str, dict[str, bool]]:
        log("\n" + "="*70)
        log("PHASE: WITHOUT GPU TESTS")
        log("="*70)

        self.state.phase = Phase.TEST_NO_GPU
        self.state_manager.save()

        all_results: dict[str, dict[str, bool]] = {}
        for vm in self.get_vms_to_test():
            all_results[vm.name] = self.run_vm_tests_no_gpu(vm)

        return all_results

    def run_all_gpu_tests(self) -> dict[str, dict[str, bool]]:
        log("\n" + "="*70)
        log("PHASE: GPU TESTS (sequential - one GPU)")
        log("="*70)

        self.state.phase = Phase.TEST_GPU
        self.state_manager.save()

        all_results: dict[str, dict[str, bool]] = {}
        for vm in self.get_vms_to_test():
            all_results[vm.name] = self.run_vm_tests_gpu(vm)

        try:
            log("\nDetaching GPU from last VM...")
            self.gpu.detach_from_current(log)
            self.state.gpu_attached_to = None
            self.state_manager.save()
        except Exception as e:
            log(f"Warning: Could not detach GPU: {e}")

        return all_results

    def run(self) -> bool:
        log(f"mOSdat Test Runner - {self.config.app.name} {self.config.version}")
        log(f"Results directory: {self.config.results_dir}")

        self.config.results_dir.mkdir(parents=True, exist_ok=True)

        with self.state_manager:
            if self.config.resume:
                log(f"Resuming from phase: {self.state.phase.value}")

            if self.state.phase == Phase.BUILD:
                if not self.build_packages():
                    return False

            if self.state.phase in (Phase.BUILD, Phase.DEPLOY, Phase.TEST_NO_GPU):
                self.run_all_no_gpu_tests()

            if self.state.phase in (Phase.TEST_NO_GPU, Phase.TEST_GPU):
                self.run_all_gpu_tests()

            self.state.phase = Phase.REPORT
            self.state_manager.save()

            from .report import generate_report
            generate_report(self.state, self.config)

            missing = validate_completion(
                self.state,
                self.get_vms_to_test(),
                skip_validation=self.config.allow_incomplete,
            )
            if missing:
                lines = "\n".join(
                    f"  - {vm} / {pkg} / {gpu_mode}"
                    for vm, pkg, gpu_mode in missing
                )
                raise CompletionError(
                    f"Test matrix incomplete — {len(missing)} cell(s) not covered:\n{lines}"
                )

            self.state.phase = Phase.DONE
            self.state_manager.save()

        log("\n" + "="*70)
        log("TEST RUN COMPLETE")
        log(f"Results: {self.config.results_dir}")
        log("="*70)

        return True

    def run_quick(self, package_path: Path) -> bool:
        """Quick mode: deploy a pre-built package to selected VMs and test."""
        if not package_path.exists():
            log(f"ERROR: Package not found: {package_path}")
            return False

        log(f"mOSdat Quick Test - {self.config.app.name}")
        log(f"Package: {package_path.name}")
        log(f"VMs: {', '.join(vm.name for vm in self.config.vms)}")

        self.config.results_dir.mkdir(parents=True, exist_ok=True)

        # Copy package to a temp dist location
        dist_dir = self.config.results_dir / "dist"
        dist_dir.mkdir(exist_ok=True)
        shutil.copy2(package_path, dist_dir / package_path.name)

        with self.state_manager:
            self.state.phase = Phase.TEST_NO_GPU
            self.state_manager.save()

            for vm in self.config.vms:
                vm_ops = VMOperations(self.api, vm, self.config)
                try:
                    vm_ops.ensure_running(log)
                    vm_ops.transfer_tests(log)
                except Exception as e:
                    log(f"ERROR: VM {vm.name} setup failed: {e}")
                    continue

                # Find matching package format
                for pkg in vm.packages:
                    if package_path.name.endswith(f".{pkg.format}") or \
                       (pkg.format == "appimage" and ".AppImage" in package_path.name):
                        log(f"\n--- {vm.name} / {pkg.format} ---")
                        try:
                            # SCP the package directly
                            from .ssh import SSHClient
                            ssh = SSHClient(vm.ip, vm.user)
                            ssh.scp_to(package_path, "/tmp/")

                            install_cmd = pkg.install_cmd.format(file=package_path.name)
                            ssh.run(install_cmd, timeout=120)

                            exit_code, output = vm_ops.run_tests_no_gpu(pkg, package_path.name, log)

                            log_file = self.config.results_dir / f"{vm.name}-{pkg.format}-quick.log"
                            with open(log_file, "w") as f:
                                f.write(output)

                            log(f"  Result: {'PASS' if exit_code == 0 else 'FAIL'} (exit {exit_code})")
                        except Exception as e:
                            log(f"  ERROR: {e}")
                        break

            self.state.phase = Phase.DONE
            self.state_manager.save()

        return True
