#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

from .config import load_config, ProjectConfig


def cmd_run(args) -> int:
    config = load_config(args.config)
    config.skip_build = args.skip_build
    config.resume = args.resume
    config.only_vm = args.only
    if args.results_dir:
        config.results_dir = args.results_dir

    from .runner import TestRunner
    runner = TestRunner(config)

    try:
        success = runner.run()
        return 0 if success else 1
    except KeyboardInterrupt:
        print("\n[mOSdat] Interrupted by user")
        return 130


def cmd_test(args) -> int:
    config = load_config(args.config)
    config.skip_build = True
    if args.results_dir:
        config.results_dir = args.results_dir

    vm_names = [v.strip() for v in args.vms.split(",")]
    for name in vm_names:
        if name not in config.vm_by_name:
            print(f"[mOSdat] ERROR: Unknown VM '{name}'. Available: {', '.join(config.vm_by_name.keys())}")
            return 1

    config.vms = [config.vm_by_name[name] for name in vm_names]

    from .runner import TestRunner
    runner = TestRunner(config)

    try:
        success = runner.run_quick(args.package)
        return 0 if success else 1
    except KeyboardInterrupt:
        print("\n[mOSdat] Interrupted by user")
        return 130


def cmd_functional(args) -> int:
    from datetime import datetime
    from pathlib import Path as P

    config = load_config(args.config)

    vm_names = [v.strip() for v in args.vms.split(",")]
    for name in vm_names:
        if name not in config.vm_by_name:
            print(f"[mOSdat] ERROR: Unknown VM '{name}'. Available: {', '.join(config.vm_by_name.keys())}")
            return 1

    vms = [config.vm_by_name[name] for name in vm_names]

    # Resolve test file
    tests_dir = config.functional.tests_dir or (config.framework_path / "shared" / "tests-functional")
    test_name = args.test or "rocketchat-smoke"
    test_file = tests_dir / f"{test_name}.yaml"
    if not test_file.exists():
        print(f"[mOSdat] ERROR: Test file not found: {test_file}")
        return 1

    from .holo2_client import Holo2Client
    from .screenshot import Screenshotter
    from .input_injector import InputInjector
    from .functional_runner import FunctionalRunner, load_test_yaml

    holo2 = Holo2Client(
        base_url=config.holo2.base_url,
        model=args.model or config.holo2.model,
    )

    name, steps, vars_ = load_test_yaml(test_file)

    # Merge config vars (workspace_url, test_user, test_password) into template vars
    from datetime import datetime as dt
    vars_.setdefault("workspace_url", config.functional.workspace_url)
    vars_.setdefault("test_user", config.functional.test_user)
    vars_.setdefault("test_password", config.functional.test_password)
    vars_["timestamp"] = dt.now().strftime("%Y%m%d_%H%M%S")


    overall = True
    for vm in vms:
        print(f"\n[mOSdat] --- {vm.name} ---")
        screenshot_dir = None
        if args.screenshots:
            screenshot_dir = P(args.screenshots) / vm.name
        elif args.save_screenshots:
            ts = dt.now().strftime("%Y-%m-%d")
            screenshot_dir = config.framework_path / "results" / f"{ts}_functional" / vm.name

        from .ssh import SSHClient
        from .proxmox import ProxmoxAPI
        ssh = SSHClient(vm.ip, vm.user)
        proxmox = ProxmoxAPI(config.proxmox) if config.proxmox.password else None
        screenshotter = Screenshotter(ssh, vm.is_windows, proxmox=proxmox, vmid=vm.vmid)
        injector = InputInjector(ssh, vm.is_windows)
        runner = FunctionalRunner(
            holo2=holo2,
            screenshotter=screenshotter,
            injector=injector,
            screenshot_dir=screenshot_dir,
            log_fn=lambda msg: print(f"[mOSdat] {msg}"),
        )

        # Inject per-VM app_path (first package with a non-empty app_path)
        vm_vars = dict(vars_)
        for pkg in vm.packages:
            if pkg.app_path:
                vm_vars.setdefault("app_path", pkg.app_path)
                break

        passed, log = runner.run_test(steps, name, vars=vm_vars)
        status = "PASS" if passed else "FAIL"
        print(f"[mOSdat]   Result: {status}")
        if not passed:
            overall = False

    return 0 if overall else 1


def cmd_validate(args) -> int:
    try:
        config = load_config(args.config)
        print(f"[mOSdat] Config OK: {args.config}")
        print(f"  App: {config.app.name} {config.app.version}")
        print(f"  Binary: {config.app.binary}")
        print(f"  Proxmox: {config.proxmox.host}:{config.proxmox.port}")
        print(f"  VMs: {len(config.vms)}")
        for vm in config.vms:
            pkgs = ", ".join(p.format for p in vm.packages)
            print(f"    {vm.name} (VMID {vm.vmid}, {vm.ip}) [{pkgs}]")
        print(f"  Tests: {len(config.tests)}")
        for t in config.tests:
            gpu_label = "GPU" if t.gpu else "no-GPU"
            print(f"    {t.name} ({gpu_label}) -> {t.script}")
        if config.build:
            print(f"  Build: {config.build.repo_path}")
        else:
            print(f"  Build: disabled (pre-built packages only)")
        if config.report.critical_tests:
            print(f"  Critical tests: {', '.join(config.report.critical_tests)}")
        return 0
    except Exception as e:
        print(f"[mOSdat] Config ERROR: {e}")
        return 1


def cmd_list_vms(args) -> int:
    config = load_config(args.config)
    print(f"{'Name':<15} {'VMID':<6} {'IP':<16} {'Desktop':<20} {'Packages'}")
    print(f"{'─'*15} {'─'*6} {'─'*16} {'─'*20} {'─'*20}")
    for vm in config.vms:
        pkgs = ", ".join(p.format for p in vm.packages)
        print(f"{vm.name:<15} {vm.vmid:<6} {vm.ip:<16} {vm.desktop:<20} {pkgs}")
    return 0


def cmd_report(args) -> int:
    from .state import StateManager
    state_file = args.results_dir / "state.json"
    if not state_file.exists():
        print(f"[mOSdat] ERROR: No state.json found in {args.results_dir}")
        return 1

    if args.config:
        config = load_config(args.config)
        config.results_dir = args.results_dir
    else:
        print("[mOSdat] ERROR: --config is required for report regeneration")
        return 1

    state_manager = StateManager(state_file, config.app.version)
    with state_manager:
        from .report import generate_report
        generate_report(state_manager.state, config)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="mosdat",
        description="mOSdat - Multi-OS Desktop App Testing with GPU passthrough",
    )
    sub = parser.add_subparsers(dest="command")

    # mosdat run
    run_p = sub.add_parser("run", help="Full test matrix run",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example: mosdat run examples/rocketchat.toml --only fedora42")
    run_p.add_argument("config", type=Path, help="Path to mosdat config (TOML)")
    run_p.add_argument("--resume", action="store_true", help="Resume from last checkpoint")
    run_p.add_argument("--skip-build", action="store_true", help="Skip build phase")
    run_p.add_argument("--only", metavar="VM", help="Test only one VM")
    run_p.add_argument("--results-dir", type=Path, help="Custom results directory")

    # mosdat test
    test_p = sub.add_parser("test", help="Quick test with pre-built package")
    test_p.add_argument("config", type=Path, help="Path to mosdat config (TOML)")
    test_p.add_argument("package", type=Path, help="Path to .deb/.rpm/.AppImage/.snap")
    test_p.add_argument("--vms", required=True, help="Comma-separated VM names")
    test_p.add_argument("--results-dir", type=Path, help="Custom results directory")

    # mosdat functional
    fn_p = sub.add_parser("functional", help="Run Holo2 VLM functional UI tests")
    fn_p.add_argument("config", type=Path, help="Path to mosdat config (TOML)")
    fn_p.add_argument("--vms", required=True, help="Comma-separated VM names")
    fn_p.add_argument("--test", default="rocketchat-smoke", metavar="NAME",
                      help="Test file name without .yaml (default: rocketchat-smoke)")
    fn_p.add_argument("--model", metavar="MODEL",
                      help="Override Holo2 model (e.g. holo3-35b-a3b)")
    fn_p.add_argument("--save-screenshots", action="store_true",
                      help="Save screenshots on failure to results dir")
    fn_p.add_argument("--screenshots", type=Path, metavar="DIR",
                      help="Save all step screenshots to this directory")

    # mosdat validate
    val_p = sub.add_parser("validate", help="Validate config file")
    val_p.add_argument("config", type=Path, help="Path to mosdat config (TOML)")

    # mosdat list-vms
    list_p = sub.add_parser("list-vms", help="Show configured VMs")
    list_p.add_argument("config", type=Path, help="Path to mosdat config (TOML)")

    # mosdat report
    report_p = sub.add_parser("report", help="Regenerate report from existing results")
    report_p.add_argument("results_dir", type=Path, help="Results directory with state.json")
    report_p.add_argument("--config", type=Path, help="Path to mosdat config (TOML)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    handlers = {
        "run": cmd_run,
        "test": cmd_test,
        "functional": cmd_functional,
        "validate": cmd_validate,
        "list-vms": cmd_list_vms,
        "report": cmd_report,
    }

    try:
        return handlers[args.command](args)
    except Exception as e:
        print(f"\n[mOSdat] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
