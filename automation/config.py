import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


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
    host: str = "192.168.13.85"
    port: int = 8006
    user: str = "root@pam"
    password: str = ""
    node: str = "pve"
    gpu_pci_address: str = "0000:01:00"

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

    def get_file_glob(self, app_name: str, version: str) -> str:
        if self.file_glob:
            return self.file_glob
        return f"{app_name}-*.{self.format}"


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

    @property
    def is_windows(self) -> bool:
        return self.os_type == "windows"


@dataclass
class TestScenario:
    name: str
    gpu: bool
    script: str


@dataclass
class Holo2Config:
    base_url: str = "http://192.168.13.62:5001/v1"
    model: str = "qwen3-vl-abliterated"  # handles both localize (bbox) and verify honestly
    verify_model: str = ""  # general VLM for yes/no state checks; empty → reuse `model`


@dataclass
class FunctionalConfig:
    enabled: bool = False
    workspace_url: str = ""
    test_user: str = ""
    test_password: str = ""
    tests_dir: Optional[Path] = None   # directory containing YAML test files


@dataclass
class ReportConfig:
    title: str = "mOSdat Test Report"
    critical_tests: list[str] = field(default_factory=list)
    critical_description: str = ""
    known_issues: dict[str, str] = field(default_factory=dict)


@dataclass
class ProjectConfig:
    app: AppConfig
    proxmox: ProxmoxConfig
    vms: list[VMConfig]
    tests: list[TestScenario]
    report: ReportConfig
    build: Optional[BuildConfig] = None
    holo2: Holo2Config = field(default_factory=Holo2Config)
    functional: FunctionalConfig = field(default_factory=FunctionalConfig)
    framework_path: Path = field(default_factory=lambda: Path(__file__).parent.parent)
    results_dir: Path = field(default_factory=Path)
    skip_build: bool = False
    resume: bool = False
    only_vm: Optional[str] = None

    def __post_init__(self):
        if not self.results_dir or self.results_dir == Path():
            from datetime import datetime
            date_str = datetime.now().strftime("%Y-%m-%d")
            self.results_dir = self.framework_path / "results" / f"{date_str}_full-matrix-{self.app.version}"

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
        return self.framework_path / "shared" / "tests"

    @property
    def tests_windows_path(self) -> Path:
        return self.framework_path / "shared" / "tests-windows"

    @property
    def state_file(self) -> Path:
        return self.results_dir / "state.json"

    @property
    def version(self) -> str:
        return self.app.version


def load_config(config_path: Path) -> ProjectConfig:
    """Load a mosdat TOML config file and return a ProjectConfig."""
    # Load .env from same directory as config, or from framework root
    env_path = config_path.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        framework_env = Path(__file__).parent.parent / ".env"
        if framework_env.exists():
            load_dotenv(framework_env)

    with open(config_path, "rb") as f:
        raw = tomllib.load(f)

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
        host=px_raw.get("host", os.getenv("PROXMOX_HOST", "192.168.13.85")),
        port=px_raw.get("port", int(os.getenv("PROXMOX_PORT", "8006"))),
        user=px_raw.get("user", os.getenv("PROXMOX_USER", "root@pam")),
        password=os.getenv("MOSDAT_PROXMOX_PASSWORD", os.getenv("PROXMOX_PASSWORD", "")),
        node=px_raw.get("node", os.getenv("PROXMOX_NODE", "pve")),
        gpu_pci_address=px_raw.get("gpu_pci_address", "0000:01:00"),
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
        tests_dir = Path(__file__).parent.parent / "shared" / "tests"
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

    # Holo2 config
    h2_raw = raw.get("holo2", {})
    holo2 = Holo2Config(
        base_url=h2_raw.get("base_url", "http://192.168.13.62:5001/v1"),
        model=h2_raw.get("model", "holo2-4b"),
    )

    # Functional test config
    fn_raw = raw.get("functional", {})
    framework_path = Path(__file__).parent.parent
    default_tests_dir = framework_path / "shared" / "tests-functional"
    functional = FunctionalConfig(
        enabled=fn_raw.get("enabled", False),
        workspace_url=fn_raw.get("workspace_url", ""),
        test_user=fn_raw.get("test_user", os.getenv("MOSDAT_TEST_USER", "")),
        test_password=fn_raw.get("test_password", os.getenv("MOSDAT_TEST_PASSWORD", "")),
        tests_dir=Path(fn_raw["tests_dir"]) if "tests_dir" in fn_raw else default_tests_dir,
    )

    return ProjectConfig(
        app=app,
        proxmox=proxmox,
        build=build,
        vms=vms,
        tests=tests,
        report=report,
        holo2=holo2,
        functional=functional,
    )
