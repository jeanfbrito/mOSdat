"""
Configuration loader for the mosdat test framework.

Precedence (highest to lowest):
  1. TOML file values (explicit keys in config.toml)
  2. Environment variables (from .env or shell)
  3. Hardcoded defaults in this module

Copy .env.example to .env at the repo root or next to your config.toml and
fill in required values before running. See .env.example for all supported
variables.
"""
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator


def _resolve_vlm_api_key(raw: Optional[str]) -> str:
    """Resolve a VLM api_key from TOML, supporting `file:` and `env:` indirection.

    - `file:/path/to/key`  → reads file, strips whitespace
    - `env:VAR_NAME`       → reads env var
    - empty/None           → falls back to VLM_API_KEY then OPENAI_API_KEY env
    - literal              → used as-is (discouraged — checks for accidental secrets)
    """
    if not raw:
        return os.environ.get("VLM_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
    raw = raw.strip()
    if raw.startswith("file:"):
        path = Path(raw[5:]).expanduser()
        return path.read_text().strip() if path.exists() else ""
    if raw.startswith("env:"):
        return os.environ.get(raw[4:], "")
    return raw


def _require_env(name: str) -> str:
    """Return the value of a required environment variable or raise clearly."""
    value = os.environ.get(name, "")
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{name}' is not set. "
            f"Copy .env.example to .env and set {name}."
        )
    return value


@dataclass
class AppConfig:
    name: str
    version: str
    binary: str
    args: list[str] = field(default_factory=list)
    process_name: str = ""
    timeout: int = 10
    pass_codes: list[int] = field(default_factory=lambda: [0, 124])
    fail_codes: list[int] = field(default_factory=lambda: [139, 134])

    def __post_init__(self):
        if not self.process_name:
            self.process_name = Path(self.binary).name


@dataclass
class BuildConfig:
    repo_path: Path
    commands: list[str]
    dist_dir: str = "dist"
    checkout: bool = True

    @property
    def dist_path(self) -> Path:
        return self.repo_path / self.dist_dir


@dataclass
class ProxmoxConfig:
    host: str = field(default_factory=lambda: os.environ.get("PROXMOX_HOST", ""))
    port: int = field(default_factory=lambda: int(os.environ.get("PROXMOX_PORT", "8006")))
    user: str = field(default_factory=lambda: os.environ.get("PROXMOX_USER", "root@pam"))
    password: str = field(default_factory=lambda: os.environ.get("PROXMOX_PASSWORD", ""))
    node: str = field(default_factory=lambda: os.environ.get("PROXMOX_NODE", "pve"))
    gpu_pci_address: str = field(default_factory=lambda: os.environ.get("PROXMOX_GPU_PCI", "0000:01:00"))

    @property
    def base_url(self) -> str:
        return f"https://{self.host}:{self.port}/api2/json"


@dataclass
class Package:
    format: str
    install_cmd: str
    uninstall_cmd: str = ""
    app_path: Optional[str] = None
    process_name: Optional[str] = None
    file_glob: str = ""
    # Flatpak-specific fields (all optional; only populated when format == "flatpak")
    appid: Optional[str] = None
    remote: Optional[str] = None
    data_dir: Optional[str] = None
    # J3: per-package version override; substituted into install_cmd as {version}
    version: Optional[str] = None

    def get_file_glob(self, app_name: str, version: str) -> str:
        if self.file_glob:
            return self.file_glob
        return f"{app_name}-*.{self.format}"

    def effective_version(self, app_version: str) -> str:
        """Return per-package version if set, else fall back to global app.version."""
        return self.version if self.version else app_version


@dataclass
class VMConfig:
    name: str
    vmid: int
    ip: str
    packages: list[Package]
    desktop: str
    user: str = ""
    password: str = ""
    os_type: str = "linux"
    temp_dir: Optional[str] = None
    # I4: implicit X11 env injection per VM. "auto" = inject preamble on GUI steps;
    # "off" (default) = no injection, preserving backwards-compatible behaviour.
    x11: str = "off"

    @property
    def is_windows(self) -> bool:
        return self.os_type == "windows"

    @property
    def scenario_subdir(self) -> str:
        """Per-VM subdirectory under shared/scenarios/functional/.

        Concrete VM names (for example ``ubuntu2204`` or ``windows10``) get
        first chance to provide scenario variants. Linux callers also fall back
        to the shared ``linux`` bucket for scenarios that have not diverged.
        """
        return self.name

    @property
    def scenario_fallback_subdirs(self) -> list[str]:
        """Fallback scenario subdirectories after ``scenario_subdir``.

        Windows variants must be explicit. Linux VMs share the historical
        ``linux`` bucket until a per-VM scenario exists.
        """
        if self.is_windows:
            return []
        return ["linux"]

    @property
    def resolved_temp_dir(self) -> str:
        """Resolve temp dir: TMPDIR env var > temp_dir field > OS default."""
        env_val = os.environ.get("TMPDIR")
        if env_val:
            return env_val
        if self.temp_dir:
            return self.temp_dir
        return "C:\\tmp" if self.is_windows else "/tmp"


@dataclass
class TestScenario:
    name: str
    gpu: bool
    script: str


@dataclass
class VLMConfig:
    base_url: str = field(default_factory=lambda: os.environ.get("VLM_BASE_URL", ""))
    model: str = field(default_factory=lambda: os.environ.get("VLM_MODEL", "holo2-4b"))
    verify_model: str = field(default_factory=lambda: os.environ.get("VLM_VERIFY_MODEL", ""))  # general VLM for yes/no state checks; empty → reuse `model`
    expected_model: Optional[str] = None  # H2.3: if set, probe_model() result must match; mismatch → exit 3
    api_key: str = field(default_factory=lambda: os.environ.get("VLM_API_KEY", os.environ.get("OPENAI_API_KEY", "")))  # Bearer key for hosted VLM endpoints (e.g. crof.ai)
    max_tokens_floor: int = field(default_factory=lambda: int(os.environ.get("VLM_MAX_TOKENS_FLOOR", "0")))  # min max_tokens for reasoning models that burn budget on hidden CoT


@dataclass
class FunctionalConfig:
    enabled: bool = False
    workspace_url: str = field(default_factory=lambda: os.environ.get("FUNCTIONAL_WORKSPACE_URL", ""))
    test_user: str = field(default_factory=lambda: os.environ.get("FUNCTIONAL_TEST_USER", ""))
    test_password: str = field(default_factory=lambda: os.environ.get("FUNCTIONAL_TEST_PASSWORD", ""))
    tests_dir: Optional[Path] = None   # directory containing YAML test files


@dataclass
class ReportConfig:
    title: str = "mOSdat Test Report"
    critical_tests: list[str] = field(default_factory=list)
    critical_description: str = ""
    known_issues: dict[str, str] = field(default_factory=dict)


class CursorConfig(BaseModel):
    profile: Literal["instant", "linear", "bezier"] = "bezier"
    duration_ms: int = 1000
    hover_dwell_ms: int = 250
    seed: str = "auto"

    @field_validator("duration_ms")
    @classmethod
    def _validate_duration_ms(cls, value: int) -> int:
        if value < 0 or value > 5000:
            raise ValueError("duration_ms must be between 0 and 5000")
        return value

    @field_validator("hover_dwell_ms")
    @classmethod
    def _validate_hover_dwell_ms(cls, value: int) -> int:
        if value < 0:
            raise ValueError("hover_dwell_ms must be >= 0")
        return value


@dataclass
class ProjectConfig:
    app: AppConfig
    proxmox: ProxmoxConfig
    vms: list[VMConfig]
    tests: list[TestScenario]
    report: ReportConfig
    build: Optional[BuildConfig] = None
    vlm: VLMConfig = field(default_factory=VLMConfig)
    functional: FunctionalConfig = field(default_factory=FunctionalConfig)
    cursor: CursorConfig = Field(default_factory=CursorConfig)
    framework_path: Path = field(default_factory=lambda: Path(__file__).parent.parent)
    results_dir: Path = field(default_factory=Path)
    skip_build: bool = False
    resume: bool = False
    only_vm: Optional[str] = None
    allow_incomplete: bool = False

    def __post_init__(self):
        if not self.results_dir or self.results_dir == Path():
            from datetime import datetime
            date_str = datetime.now().strftime("%Y-%m-%d")
            self.results_dir = self.framework_path / "results" / "smoke" / f"{date_str}_full-matrix-{self.app.version}"

    @property
    def vm_by_name(self) -> dict[str, VMConfig]:
        return {vm.name: vm for vm in self.vms}

    @property
    def dist_path(self) -> Path:
        if self.build:
            return self.build.dist_path
        return Path(".")

    @property
    def tests_path(self) -> Path:
        return self.framework_path / "shared" / "scenarios" / "smoke-linux"

    @property
    def tests_windows_path(self) -> Path:
        return self.framework_path / "shared" / "scenarios" / "smoke-windows"

    @property
    def state_file(self) -> Path:
        return self.results_dir / "state.json"

    @property
    def version(self) -> str:
        return self.app.version


def load_config(config_path: Path) -> ProjectConfig:
    """Load a mosdat TOML config file and return a ProjectConfig."""
    # Load .env from same directory as config, or from framework root
    env_path = Path(config_path).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        framework_env = Path(__file__).parent.parent / ".env"
        if framework_env.exists():
            load_dotenv(framework_env)

    # Load TOML first to check what fields are provided
    with open(config_path, "rb") as f:
        raw = tomllib.load(f)

    # Fail fast on required env vars before touching the filesystem further
    # Only require if TOML doesn't provide them
    px_raw = raw.get("proxmox", {})
    if "host" not in px_raw:
        _require_env("PROXMOX_HOST")
    if "password" not in px_raw:
        _require_env("PROXMOX_PASSWORD")

    # App config
    app_raw = raw["app"]
    app = AppConfig(
        name=app_raw["name"],
        version=app_raw["version"],
        binary=app_raw["binary"],
        args=app_raw.get("args", []),
        process_name=app_raw.get("process_name", ""),
        timeout=app_raw.get("timeout", 10),
        pass_codes=app_raw.get("exit_codes", {}).get("pass", [0, 124]),
        fail_codes=app_raw.get("exit_codes", {}).get("fail", [139, 134]),
    )

    # Proxmox config
    px_raw = raw.get("proxmox", {})
    proxmox = ProxmoxConfig(
        host=px_raw.get("host", os.environ.get("PROXMOX_HOST", "")),
        port=px_raw.get("port", int(os.environ.get("PROXMOX_PORT", "8006"))),
        user=px_raw.get("user", os.environ.get("PROXMOX_USER", "root@pam")),
        password=px_raw.get("password", os.environ.get("PROXMOX_PASSWORD", "")),
        node=px_raw.get("node", os.environ.get("PROXMOX_NODE", "pve")),
        gpu_pci_address=px_raw.get("gpu_pci_address", os.environ.get("PROXMOX_GPU_PCI", "0000:01:00")),
    )

    # Build config (optional)
    build = None
    if "build" in raw:
        b_raw = raw["build"]
        build = BuildConfig(
            repo_path=Path(b_raw["repo_path"]),
            commands=b_raw["commands"],
            dist_dir=b_raw.get("dist_dir", "dist"),
            checkout=b_raw.get("checkout", True),
        )

    # Default VM credentials
    defaults = raw.get("defaults", {})
    default_user = defaults.get("user", os.getenv("DEFAULT_VM_USER", "jean"))
    default_password = os.getenv("MOSDAT_VM_PASSWORD", os.getenv("DEFAULT_VM_PASSWORD", ""))

    # VM configs
    vms: list[VMConfig] = []
    for vm_raw in raw.get("vm", []):
        packages: list[Package] = []
        for pkg_raw in vm_raw.get("package", []):
            pkg = Package(
                format=pkg_raw["format"],
                install_cmd=pkg_raw["install"],
                uninstall_cmd=pkg_raw.get("uninstall", ""),
                app_path=pkg_raw.get("app_path"),
                process_name=pkg_raw.get("process_name"),
                file_glob=pkg_raw.get("file_glob", ""),
                appid=pkg_raw.get("appid"),
                remote=pkg_raw.get("remote"),
                data_dir=pkg_raw.get("data_dir"),
                version=pkg_raw.get("version"),
            )
            # Resolve {app_name} templates
            if "{app_name}" in pkg.uninstall_cmd:
                pkg.uninstall_cmd = pkg.uninstall_cmd.format(app_name=app.name)
            if pkg.app_path and "{app_name}" in pkg.app_path:
                pkg.app_path = pkg.app_path.format(app_name=app.name)
            packages.append(pkg)

        vms.append(VMConfig(
            name=vm_raw["name"],
            vmid=vm_raw["vmid"],
            ip=vm_raw["ip"],
            desktop=vm_raw.get("desktop", ""),
            packages=packages,
            user=vm_raw.get("user", default_user),
            password=vm_raw.get("password", default_password),
            os_type=vm_raw.get("os_type", "linux"),
            x11=vm_raw.get("x11", "off"),
        ))

    # Test scenarios (optional — auto-discover if absent)
    tests: list[TestScenario] = []
    if "test" in raw:
        for t_raw in raw["test"]:
            tests.append(TestScenario(
                name=t_raw["name"],
                gpu=t_raw.get("gpu", False),
                script=t_raw["script"],
            ))
    else:
        tests_dir = Path(__file__).parent.parent / "shared" / "scenarios" / "smoke-linux"
        if tests_dir.exists():
            for script in sorted(tests_dir.glob("test-*.sh")):
                name = script.stem.removeprefix("test-")
                gpu = "gpu" in name
                tests.append(TestScenario(name=name, gpu=gpu, script=script.name))

    # Report config
    rpt_raw = raw.get("report", {})
    known_issues = rpt_raw.get("known_issues", {})
    report = ReportConfig(
        title=rpt_raw.get("title", "mOSdat Test Report"),
        critical_tests=rpt_raw.get("critical_tests", []),
        critical_description=rpt_raw.get("critical_description", ""),
        known_issues=known_issues,
    )

    # VLM config
    vlm_raw = raw.get("vlm", {})
    vlm = VLMConfig(
        base_url=vlm_raw.get("base_url", os.environ.get("VLM_BASE_URL", "")),
        model=vlm_raw.get("model", os.environ.get("VLM_MODEL", "holo2-4b")),
        verify_model=vlm_raw.get("verify_model", os.environ.get("VLM_VERIFY_MODEL", "")),
        expected_model=vlm_raw.get("expected_model") or None,  # H2.3: None → log-only mode
        api_key=_resolve_vlm_api_key(vlm_raw.get("api_key")),
        max_tokens_floor=int(vlm_raw.get("max_tokens_floor", os.environ.get("VLM_MAX_TOKENS_FLOOR", "0"))),
    )

    # Functional test config
    fn_raw = raw.get("functional", {})
    framework_path = Path(__file__).parent.parent
    default_tests_dir = framework_path / "shared" / "scenarios" / "functional"
    fn_enabled = fn_raw.get("enabled", False)
    if fn_enabled:
        # Only require env vars if TOML doesn't provide them
        if "workspace_url" not in fn_raw:
            _require_env("FUNCTIONAL_WORKSPACE_URL")
        if "test_user" not in fn_raw:
            _require_env("FUNCTIONAL_TEST_USER")
        if "test_password" not in fn_raw:
            _require_env("FUNCTIONAL_TEST_PASSWORD")
    functional = FunctionalConfig(
        enabled=fn_enabled,
        workspace_url=fn_raw.get("workspace_url", os.environ.get("FUNCTIONAL_WORKSPACE_URL", "")),
        test_user=fn_raw.get("test_user", os.environ.get("FUNCTIONAL_TEST_USER", "")),
        test_password=fn_raw.get("test_password", os.environ.get("FUNCTIONAL_TEST_PASSWORD", "")),
        tests_dir=Path(fn_raw["tests_dir"]) if "tests_dir" in fn_raw else default_tests_dir,
    )

    # Cursor motion config
    cursor_raw = raw.get("cursor", {})
    cursor = CursorConfig(**cursor_raw)

    return ProjectConfig(
        app=app,
        proxmox=proxmox,
        build=build,
        vms=vms,
        tests=tests,
        report=report,
        vlm=vlm,
        functional=functional,
        cursor=cursor,
    )
