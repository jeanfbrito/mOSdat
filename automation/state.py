import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .config import VMConfig


class TestStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Phase(str, Enum):
    BUILD = "build"
    DEPLOY = "deploy"
    TEST_NO_GPU = "test_no_gpu"
    TEST_GPU = "test_gpu"
    REPORT = "report"
    DONE = "done"


@dataclass
class TestResult:
    vm_name: str
    package_format: str
    gpu: bool
    status: TestStatus = TestStatus.PENDING
    exit_code: Optional[int] = None
    log_file: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error_message: Optional[str] = None

    @property
    def key(self) -> str:
        gpu_suffix = "gpu" if self.gpu else "no-gpu"
        return f"{self.vm_name}-{self.package_format}-{gpu_suffix}"


@dataclass
class State:
    version: str
    phase: Phase = Phase.BUILD
    current_vm: Optional[str] = None
    current_package: Optional[str] = None
    gpu_attached_to: Optional[int] = None
    results: dict[str, TestResult] = field(default_factory=dict)
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_updated: str = field(default_factory=lambda: datetime.now().isoformat())

    def mark_test_started(self, vm_name: str, package_format: str, gpu: bool) -> TestResult:
        result = TestResult(
            vm_name=vm_name,
            package_format=package_format,
            gpu=gpu,
            status=TestStatus.RUNNING,
            started_at=datetime.now().isoformat(),
        )
        self.results[result.key] = result
        self.current_vm = vm_name
        self.current_package = package_format
        self.last_updated = datetime.now().isoformat()
        return result

    def mark_test_finished(
        self,
        vm_name: str,
        package_format: str,
        gpu: bool,
        status: TestStatus,
        exit_code: Optional[int] = None,
        log_file: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> TestResult:
        key = f"{vm_name}-{package_format}-{'gpu' if gpu else 'no-gpu'}"
        if key not in self.results:
            self.results[key] = TestResult(vm_name=vm_name, package_format=package_format, gpu=gpu)
        
        result = self.results[key]
        result.status = status
        result.exit_code = exit_code
        result.log_file = log_file
        result.error_message = error_message
        result.finished_at = datetime.now().isoformat()
        self.last_updated = datetime.now().isoformat()
        return result

    def is_test_completed(self, vm_name: str, package_format: str, gpu: bool) -> bool:
        key = f"{vm_name}-{package_format}-{'gpu' if gpu else 'no-gpu'}"
        if key not in self.results:
            return False
        return self.results[key].status in (TestStatus.PASSED, TestStatus.FAILED, TestStatus.SKIPPED)

    def get_pending_tests(self, gpu: bool, vms: list | None = None) -> list[tuple[str, str]]:
        pending: list[tuple[str, str]] = []
        if vms is None:
            return pending
        for vm in vms:
            for pkg in vm.packages:
                if not self.is_test_completed(vm.name, pkg.format, gpu):
                    pending.append((vm.name, pkg.format))
        return pending

    def to_dict(self) -> dict:
        data = asdict(self)
        data["phase"] = self.phase.value
        data["results"] = {k: asdict(v) for k, v in self.results.items()}
        for key in data["results"]:
            data["results"][key]["status"] = data["results"][key]["status"].value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "State":
        data["phase"] = Phase(data["phase"])
        results = {}
        for key, result_data in data.get("results", {}).items():
            result_data["status"] = TestStatus(result_data["status"])
            results[key] = TestResult(**result_data)
        data["results"] = results
        return cls(**data)


class StateManager:
    def __init__(self, state_file: Path, version: str):
        self.state_file = state_file
        self.version = version
        self._state: Optional[State] = None

    def load(self) -> State:
        if self.state_file.exists():
            with open(self.state_file) as f:
                data = json.load(f)
                if data.get("version") == self.version:
                    self._state = State.from_dict(data)
                    return self._state
        self._state = State(version=self.version)
        return self._state

    def save(self) -> None:
        if self._state is None:
            return
        self._state.last_updated = datetime.now().isoformat()
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump(self._state.to_dict(), f, indent=2)

    @property
    def state(self) -> State:
        if self._state is None:
            self._state = self.load()
        return self._state

    def __enter__(self) -> "StateManager":
        self.load()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.save()


class CompletionError(Exception):
    """Raised when the test matrix is incomplete and --allow-incomplete was not set."""


def validate_completion(
    state: "State",
    vms: "list[VMConfig]",
    skip_validation: bool = False,
) -> "list[tuple[str, str, str]]":
    """Return missing (vm_name, package_format, gpu_mode) cells.

    gpu_mode is the string "gpu" or "no-gpu" to match TestResult.key conventions.
    An empty list means the matrix is fully covered.

    Args:
        state: Current test State.
        vms: List of VMConfig objects defining the expected matrix.
        skip_validation: If True, always return [] (for --allow-incomplete dev runs).
    """
    if skip_validation:
        return []

    missing: list[tuple[str, str, str]] = []
    for vm in vms:
        for pkg in vm.packages:
            for gpu in (False, True):
                gpu_mode = "gpu" if gpu else "no-gpu"
                key = f"{vm.name}-{pkg.format}-{gpu_mode}"
                result = state.results.get(key)
                if result is None or result.status not in (
                    TestStatus.PASSED,
                    TestStatus.FAILED,
                    TestStatus.SKIPPED,
                ):
                    missing.append((vm.name, pkg.format, gpu_mode))
    return missing
