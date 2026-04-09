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
