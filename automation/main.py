#!/usr/bin/env python3
import argparse
import os
import signal
import subprocess
import sys
import urllib.request
import urllib.error
import time as _time
from pathlib import Path

from .config import load_config
from .state import StateManager
from .issue_confirm import ConfirmInvocation, run_confirm


def cmd_run(args) -> int:
    config = load_config(args.config)
    config.skip_build = args.skip_build
    config.resume = args.resume
    config.only_vm = args.only
    config.allow_incomplete = args.allow_incomplete
    if args.results_dir:
        config.results_dir = args.results_dir

    from .runners.smoke import TestRunner
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

    from .runners.smoke import TestRunner
    runner = TestRunner(config)

    try:
        success = runner.run_quick(args.package)
        return 0 if success else 1
    except KeyboardInterrupt:
        print("\n[mOSdat] Interrupted by user")
        return 130


def cmd_record(args) -> int:
    """C3: Interactive scenario authoring — open VNC viewer, capture clicks, generate YAML."""
    config = load_config(args.config)

    vm_names = [v.strip() for v in args.vms.split(",")]
    if len(vm_names) != 1:
        print("[mOSdat] ERROR: --record requires exactly one VM (--vms <single-vm>)")
        return 1
    vm_name = vm_names[0]
    if vm_name not in config.vm_by_name:
        print(f"[mOSdat] ERROR: Unknown VM '{vm_name}'. Available: {', '.join(config.vm_by_name.keys())}")
        return 1
    vm = config.vm_by_name[vm_name]

    if not config.proxmox.password:
        print("[mOSdat] ERROR: Proxmox password required for VNC. Set MOSDAT_PROXMOX_PASSWORD or [proxmox].password.")
        return 1

    from .vlm.client import VLMClient
    from .transport.vnc import VncClient
    from .proxmox.api import ProxmoxAPI

    vlm = VLMClient(
        base_url=config.vlm.base_url,
        model=args.model or config.vlm.model,
        verify_model=args.verify_model or config.vlm.verify_model or None,
    )
    proxmox = ProxmoxAPI(config.proxmox)

    output_path = Path(args.output) if args.output else None

    # QApplication must be created here, not at import time (keeps headless imports safe).
    from PyQt6.QtWidgets import QApplication
    from .record import RecorderWindow

    app = QApplication(sys.argv)

    with VncClient(proxmox, vmid=vm.vmid) as vnc:
        window = RecorderWindow(vnc, vlm, output_path=output_path)
        window.resize(1200, 700)
        window.show()
        return app.exec()


def _preflight_workspace(url: str, timeout: int = 10, retries: int = 2) -> tuple[bool, str]:
    """P1: HEAD probe a workspace URL; on failure retry with short backoff.

    Returns (ok, detail).
    Treats 200-499 as alive. 5xx, connection error, timeout = dead.
    """
    if not url:
        return True, "no workspace_url configured"

    probe_url = url
    if "://" not in probe_url:
        probe_url = "https://" + probe_url
    probe_url = probe_url.rstrip("/") + "/api/info"

    last_err = "unknown"
    for attempt in range(retries + 1):
        if attempt > 0:
            _time.sleep(2)
        t0 = _time.perf_counter()
        try:
            req = urllib.request.Request(probe_url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                elapsed = _time.perf_counter() - t0
                if 200 <= r.status < 500:
                    return True, f"HTTP {r.status} in {elapsed*1000:.0f}ms"
                last_err = f"HTTP {r.status} in {elapsed*1000:.0f}ms"
        except urllib.error.HTTPError as e:
            elapsed = _time.perf_counter() - t0
            if 200 <= e.code < 500:
                return True, f"HTTP {e.code} in {elapsed*1000:.0f}ms"
            last_err = f"HTTP {e.code} in {elapsed*1000:.0f}ms"
        except Exception as e:
            elapsed = _time.perf_counter() - t0
            last_err = f"{type(e).__name__}: {str(e)[:80]} in {elapsed*1000:.0f}ms"
    return False, last_err


def _warmup_vlm(vlm) -> bool:
    """G1: Trigger VLM endpoint load before the scenario loop.

    llama-swap returns 502 while swapping models; the failover wrapper retries
    with backoff, consuming ~90s on a cold endpoint. Running a tiny warmup call
    here absorbs that wait outside the scenario step budget.

    Returns True if warmup succeeded, False if it timed out or errored (non-fatal).
    """
    from PIL import Image as _Image

    print("[mOSdat] Warming up VLM endpoint (model swap may take 30-60s)...")
    t0 = _time.perf_counter()
    try:
        img = _Image.new("RGB", (64, 64), (0, 0, 0))
        result = vlm.verify(img, "is the screen visible")
        elapsed = _time.perf_counter() - t0
        print(f"[mOSdat]   Warmup OK in {elapsed:.1f}s (answer={'yes' if result else 'no'})")
        return True
    except Exception as e:
        elapsed = _time.perf_counter() - t0
        print(f"[mOSdat]   Warmup failed after {elapsed:.1f}s: {e}")
        print("[mOSdat]   Proceeding anyway — scenario may take longer for first VLM call")
        return False


class RuntimeWatchdogTimeout(Exception):
    pass


def _watchdog_handler(signum, frame):
    raise RuntimeWatchdogTimeout()


def cmd_functional(args) -> int:
    if getattr(args, "record", False):
        return cmd_record(args)

    from pathlib import Path as P

    config = load_config(args.config)

    vm_names = [v.strip() for v in args.vms.split(",")]
    for name in vm_names:
        if name not in config.vm_by_name:
            print(f"[mOSdat] ERROR: Unknown VM '{name}'. Available: {', '.join(config.vm_by_name.keys())}")
            return 1

    vms = [config.vm_by_name[name] for name in vm_names]

    # Resolve test file
    tests_dir = config.functional.tests_dir or (config.framework_path / "shared" / "scenarios" / "functional")
    test_name = args.test or "rocketchat-smoke"
    test_file = tests_dir / f"{test_name}.yaml"
    if not test_file.exists():
        print(f"[mOSdat] ERROR: Test file not found: {test_file}")
        return 1

    from .vlm.client import VLMClient
    from .vlm.screenshot import Screenshotter
    from .vlm.input import InputInjector
    from .runners.functional import FunctionalRunner, load_test_yaml

    vlm = VLMClient(
        base_url=config.vlm.base_url,
        model=args.model or config.vlm.model,
        verify_model=args.verify_model or config.vlm.verify_model or None,
    )

    # G1: warm up VLM endpoint before per-VM loop so model swap doesn't consume step budget
    if not getattr(args, "skip_warmup", False):
        _warmup_vlm(vlm)

    # P1: preflight workspace URL before per-VM loop to fail fast on dead server
    if not getattr(args, "skip_workspace_check", False):
        workspace_url = getattr(config.functional, "workspace_url", None)
        if workspace_url:
            print(f"[mOSdat] Pre-flight workspace check: {workspace_url}")
            ok, detail = _preflight_workspace(workspace_url)
            if ok:
                print(f"[mOSdat]   Workspace OK ({detail})")
            else:
                print(f"[mOSdat]   ERROR: workspace unreachable — {detail}")
                print("[mOSdat]   Fix the workspace server (or pass --skip-workspace-check) and retry.")
                return 2

    # H2.3: probe VLM model identity before per-VM loop
    if not getattr(args, "skip_model_check", False):
        try:
            models = vlm.list_models()
            expected = config.vlm.expected_model
            if expected:
                if expected not in models:
                    print(f"[mOSdat] ERROR: configured VLM model {expected!r} not in endpoint catalog ({len(models)} models). Available: {models[:5]}{'...' if len(models) > 5 else ''}")
                    print("[mOSdat]   Check llama-swap config or pass --skip-model-check to bypass.")
                    return 3
                print(f"[mOSdat] VLM model identity OK: {expected!r} present in catalog of {len(models)} models")
            else:
                print(f"[mOSdat] VLM model probe (no enforcement): catalog of {len(models)} models")
        except Exception as probe_err:
            print(f"[mOSdat] WARNING: VLM model probe failed: {probe_err}")
            print("[mOSdat]   Proceeding — pass --skip-model-check to suppress this warning.")

    name, steps, vars_, yaml_checkpoints = load_test_yaml(test_file)

    # C2: resolve checkpoint config (YAML wins unless --no-checkpoints overrides)
    checkpoint_config = dict(yaml_checkpoints)
    if getattr(args, "no_checkpoints", False):
        checkpoint_config["enabled"] = False

    # B4: step slicing
    total_steps = len(steps)
    from_step = args.from_step
    until_step = args.until_step if args.until_step is not None else total_steps
    if from_step < 1 or from_step > total_steps:
        print(f"[mOSdat] ERROR: --from-step {from_step} out of range (scenario has {total_steps} steps)")
        return 1
    if until_step < from_step or until_step > total_steps:
        print(f"[mOSdat] ERROR: --until-step {until_step} out of range (from_step={from_step}, total={total_steps})")
        return 1
    steps = steps[from_step - 1 : until_step]
    partial_run = (from_step > 1 or until_step < total_steps)

    # Merge config vars (workspace_url, test_user, test_password) into template vars
    from datetime import datetime as dt
    vars_.setdefault("workspace_url", config.functional.workspace_url)
    vars_.setdefault("test_user", config.functional.test_user)
    vars_.setdefault("test_password", config.functional.test_password)
    vars_.setdefault("vm_password", os.environ.get("DEFAULT_VM_PASSWORD", os.environ.get("MOSDAT_VM_PASSWORD", "")))
    vars_["timestamp"] = dt.now().strftime("%Y%m%d_%H%M%S")

    if not config.proxmox.password:
        print("[mOSdat] ERROR: Proxmox password required for VNC screenshots. Set MOSDAT_PROXMOX_PASSWORD or [proxmox].password in config.")
        return 1
    from .proxmox.api import ProxmoxAPI
    proxmox = ProxmoxAPI(config.proxmox)

    if args.timeout > 0:
        signal.signal(signal.SIGALRM, _watchdog_handler)
        signal.alarm(args.timeout)
    overall = True
    try:
        for vm in vms:
            print(f"\n[mOSdat] --- {vm.name} ---")
            if args.screenshots:
                screenshot_dir = P(args.screenshots) / vm.name
            elif args.save_screenshots:
                ts = dt.now().strftime("%Y-%m-%d_%H%M%S")
                screenshot_dir = config.framework_path / "results" / "functional" / f"{ts}_functional" / vm.name
            else:
                # A6: screenshot_dir is mandatory; default to a timestamped results dir.
                ts = dt.now().strftime("%Y-%m-%d_%H%M%S")
                screenshot_dir = config.framework_path / "results" / "functional" / f"{ts}_functional" / vm.name

            from .transport.ssh import SSHClient
            from .transport.vnc import VncClient
            ssh = SSHClient(vm.ip, vm.user)

            # Inject per-VM app_path (first package with a non-empty app_path)
            vm_vars = dict(vars_)
            for pkg in vm.packages:
                if pkg.app_path:
                    vm_vars.setdefault("app_path", pkg.app_path)
                    # Resolve {file} in app_path by globbing the VM's temp dir.
                    # AppImage packages use app_path="/tmp/{file}" where {file} is
                    # the actual filename matched by file_glob. Glob via SSH so the
                    # runner gets a concrete path (e.g. /tmp/rocketchat-4.x-linux-x86_64.AppImage).
                    if "{file}" in vm_vars.get("app_path", "") and pkg.file_glob:
                        try:
                            temp_dir = vm.resolved_temp_dir
                            result = ssh.run(f"ls {temp_dir}/{pkg.file_glob} 2>/dev/null | head -1")
                            resolved_file = result.stdout.strip()
                            if resolved_file:
                                import os as _os
                                vm_vars["app_path"] = vm_vars["app_path"].replace(
                                    "{file}", _os.path.basename(resolved_file)
                                )
                        except Exception:
                            pass  # leave {file} unresolved; launch step will fail with a clear message
                    break

            with VncClient(proxmox, vmid=vm.vmid) as vnc:
                screenshotter = Screenshotter(vnc)
                injector = InputInjector(vnc, ssh, vm.is_windows)
                # C2: vm_ops needed only when checkpoints are enabled
                from .proxmox.vm import VMOperations
                _vm_ops_for_ckpt = None
                if checkpoint_config.get("enabled"):
                    _vm_ops_for_ckpt = VMOperations(proxmox, vm, config)
                runner = FunctionalRunner(
                    vlm=vlm,
                    screenshotter=screenshotter,
                    injector=injector,
                    screenshot_dir=screenshot_dir,
                    log_fn=lambda msg: print(f"[mOSdat] {msg}"),
                    popup_sweep=getattr(args, "popup_sweep", False),
                    checkpoint_config=checkpoint_config,
                    vm_ops=_vm_ops_for_ckpt,
                    vmid=vm.vmid if checkpoint_config.get("enabled") else None,
                    click_verify_override=getattr(args, "click_verify", "auto"),
                    canary_override=getattr(args, "canary_override", "auto"),
                )

                # B7: VM health probe before scenario start
                if not getattr(args, "skip_health_probe", False):
                    print("[mOSdat]   Probing VM health...")
                    healthy = runner._probe_vm_health()
                    if not healthy:
                        print("[mOSdat]   WARNING: VM appears frozen — attempting reset...")
                        from .proxmox.vm import VMOperations
                        vm_ops = VMOperations(proxmox, vm, config)
                        vm_ops.reset_vm(log_fn=lambda msg: print(f"[mOSdat] {msg}"))
                        # Wait for VM to come back
                        import time as _time
                        _time.sleep(15)
                        healthy2 = runner._probe_vm_health()
                        if not healthy2:
                            runner._emit("vm_health_failed")
                            print(f"[mOSdat] ERROR: VM still frozen after reset — aborting scenario for {vm.name}")
                            overall = False
                            continue

                passed, log = runner.run_test(steps, name, vars=vm_vars)
                # B4: partial run note
                if partial_run and until_step < total_steps:
                    remaining = total_steps - until_step
                    print(f"[mOSdat]   Halted at step {until_step}; {remaining} remaining steps NOT executed. "
                          f"VM left in current state for inspection. SSH: {vm.ip}")

                # B1: Generate HTML report for this run
                try:
                    from .reporting.report import generate_html_report
                    report_path = generate_html_report(screenshot_dir)
                    print(f"[mOSdat] Report: file://{report_path.absolute()}")
                except Exception as e:
                    print(f"[mOSdat] WARN: report generation failed: {e}")

            status = "PASS" if passed else "FAIL"
            print(f"[mOSdat]   Result: {status}")
            if not passed:
                overall = False

    except RuntimeWatchdogTimeout:
        print(f"[mOSdat] WATCHDOG: scenario exceeded {args.timeout}s — terminating")
        return 5
    finally:
        signal.alarm(0)

    # H2.1: notify on failure when NOTIFY_WEBHOOK is configured
    if not overall and os.environ.get("NOTIFY_WEBHOOK"):
        run_label = os.environ.get("MOSDAT_RUN_LABEL", "mosdat functional")
        report_url = os.environ.get("MOSDAT_REPORT_URL", "")
        try:
            from .notify import notify
            notify(run_label=run_label, status="fail", report_url=report_url)
        except Exception as _notify_err:
            print(f"[mOSdat] WARN: notification failed: {_notify_err}")

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
            print("  Build: disabled (pre-built packages only)")
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
    # Handle --flakes flag (B5)
    if getattr(args, "flakes", False):
        from .reporting.aggregate import flake_leaderboard

        results_root = args.root if hasattr(args, "root") and args.root else args.results_dir
        output_path = args.output if hasattr(args, "output") and args.output else (results_root / "functional" / "flake-leaderboard.md")

        # If output is "-", write to stdout; otherwise write to file
        markdown = flake_leaderboard(results_root)
        if output_path == "-" or str(output_path) == "-":
            print(markdown)
        else:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(markdown, encoding="utf-8")
            print(f"[mOSdat] Flakiness leaderboard written: {output_path.absolute()}")
        return 0

    # Original report regeneration logic
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
        from .reporting.report import generate_report
        generate_report(state_manager.state, config)
    return 0


def _git_rev_short() -> str | None:
    """Return short git HEAD rev, or None on failure (e.g. git not available)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
        return None
    except Exception:
        return None


def cmd_confirm(args) -> int:
    iterations = args.iterations or (3 if args.mode == "confirm" else 5)
    repro = " ".join([
        "mosdat", "confirm", args.issue,
        "--vm", args.vm,
        "--iterations", str(iterations),
        "--mode", args.mode,
    ])
    git_rev = _git_rev_short()
    inv = ConfirmInvocation(
        issue_id_or_url=args.issue,
        vm_name=args.vm,
        iterations=iterations,
        mode=args.mode,
        scenario_path=args.scenario,
        output_dir=args.output,
        refresh_issue_context=args.refresh_issue_context,
        emit_html=args.html,
        skip_state_snapshot=args.no_state_snapshot,
        repro_command=repro,
        git_rev=git_rev,
    )
    artifacts = run_confirm(inv)
    print(f"[mosdat] verdict: {artifacts.verdict}")
    print(f"[mosdat] report:  {artifacts.report_md_path}")
    print(f"[mosdat] comment: {artifacts.comment_md_path}")
    return artifacts.exit_code


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
    run_p.add_argument("--allow-incomplete", action="store_true",
                       help="Skip completion validation (dev runs with partial matrix)")

    # mosdat test
    test_p = sub.add_parser("test", help="Quick test with pre-built package")
    test_p.add_argument("config", type=Path, help="Path to mosdat config (TOML)")
    test_p.add_argument("package", type=Path, help="Path to .deb/.rpm/.AppImage/.snap")
    test_p.add_argument("--vms", required=True, help="Comma-separated VM names")
    test_p.add_argument("--results-dir", type=Path, help="Custom results directory")

    # mosdat functional
    fn_p = sub.add_parser("functional", help="Run VLM functional UI tests")
    fn_p.add_argument("config", type=Path, help="Path to mosdat config (TOML)")
    fn_p.add_argument("--vms", required=True, help="Comma-separated VM names")
    fn_p.add_argument("--test", default="rocketchat-smoke", metavar="NAME",
                      help="Test file name without .yaml (default: rocketchat-smoke)")
    fn_p.add_argument("--model", metavar="MODEL",
                      help="Override VLM for element localization (default: holo2-4b)")
    fn_p.add_argument("--verify-model", dest="verify_model", metavar="MODEL",
                      help="Override VLM for yes/no state verification (default: same as --model; "
                           "recommend qwen3-vl-abliterated — localization models hallucinate on yes/no)")
    fn_p.add_argument("--save-screenshots", action="store_true",
                      help="Save screenshots on failure to results dir")
    fn_p.add_argument("--screenshots", type=Path, metavar="DIR",
                      help="Save all step screenshots to this directory")
    fn_p.add_argument("--popup-sweep", action="store_true", dest="popup_sweep",
                      help="B3: sweep modal popups/dialogs before each localize step")
    fn_p.add_argument("--from-step", type=int, default=1, dest="from_step", metavar="N",
                      help="B4: start from step N (1-indexed, default: 1)")
    fn_p.add_argument("--until-step", type=int, default=None, dest="until_step", metavar="N",
                      help="B4: stop after step N (inclusive, default: last step)")
    fn_p.add_argument("--skip-health-probe", action="store_true", dest="skip_health_probe",
                      help="B7: skip VM health probe before scenario start")
    fn_p.add_argument("--no-checkpoints", action="store_true", dest="no_checkpoints",
                      help="C2: disable Proxmox snapshot checkpoints even if YAML enables them")
    fn_p.add_argument("--skip-warmup", action="store_true", dest="skip_warmup",
                      help="Skip VLM warmup phase (faster startup; first scenario step may be slow if endpoint cold)")
    fn_p.add_argument("--skip-workspace-check", action="store_true", dest="skip_workspace_check",
                      help="Skip workspace URL preflight (faster startup; scenario will burn VLM budget if server is down)")
    fn_p.add_argument("--skip-model-check", action="store_true", dest="skip_model_check",
                      help="H2.3: skip VLM model identity check (bypass expected_model enforcement)")
    fn_p.add_argument("--timeout", type=int, default=900, metavar="SECONDS",
                      help="I8: wall-clock watchdog — abort with exit 5 if scenario exceeds N seconds "
                           "(default: 900; 0 = disabled; POSIX/Linux only — uses SIGALRM)")
    fn_p.add_argument("--record", action="store_true",
                      help="C3: interactive authoring mode — open VNC viewer, capture clicks, generate YAML")
    fn_p.add_argument("--output", type=str, default=None,
                      help="(--record) path to write the generated YAML")
    fn_p.add_argument("--click-verify",
                      choices=["auto", "off", "yesno", "diff", "diff+yesno"],
                      default="auto", dest="click_verify",
                      help="Override scenario's verify_click mode globally (A/B testing)")
    fn_p.add_argument("--canary", choices=["auto", "off", "on"],
                      default="auto", dest="canary_override",
                      help="Override scenario's canary setting globally (A/B testing)")

    # mosdat confirm
    confirm_p = sub.add_parser(
        "confirm",
        help="Confirm or verify-fix a tracked GitHub issue via a bug-confirmation scenario",
    )
    confirm_p.add_argument(
        "issue",
        help="Issue id or URL (e.g. 3308 or https://github.com/.../issues/3308)",
    )
    confirm_p.add_argument("--vm", required=True, help="VM name (e.g. fedora42)")
    confirm_p.add_argument(
        "--iterations", type=int, default=None,
        help="Repeat count (default: 3 for confirm, 5 for verify-fix)",
    )
    confirm_p.add_argument(
        "--mode", choices=["confirm", "verify-fix", "regression"], default="confirm",
    )
    confirm_p.add_argument(
        "--scenario", type=Path, default=None,
        help="Override scenario YAML path",
    )
    confirm_p.add_argument(
        "--output", type=Path, default=None,
        help="Override output directory",
    )
    confirm_p.add_argument(
        "--refresh-issue-context", action="store_true", dest="refresh_issue_context",
        help="Bypass cached issue body and re-fetch",
    )
    confirm_p.add_argument(
        "--html", action="store_true",
        help="Also emit HTML report",
    )
    confirm_p.add_argument(
        "--no-state-snapshot", action="store_true", dest="no_state_snapshot",
        help="Skip VM-side state collection",
    )
    confirm_p.set_defaults(func=cmd_confirm)

    # mosdat validate
    val_p = sub.add_parser("validate", help="Validate config file")
    val_p.add_argument("config", type=Path, help="Path to mosdat config (TOML)")

    # mosdat list-vms
    list_p = sub.add_parser("list-vms", help="Show configured VMs")
    list_p.add_argument("config", type=Path, help="Path to mosdat config (TOML)")

    # mosdat report
    report_p = sub.add_parser("report", help="Regenerate report from existing results or generate flakiness leaderboard")
    report_p.add_argument("results_dir", type=Path, help="Results directory with state.json (or root for --flakes)")
    report_p.add_argument("--config", type=Path, help="Path to mosdat config (TOML)")
    report_p.add_argument("--flakes", action="store_true", help="B5: Generate flakiness leaderboard from functional runs")
    report_p.add_argument("--output", type=Path, help="Output path for flakiness leaderboard (default: <results_dir>/functional/flake-leaderboard.md, or - for stdout)")
    report_p.add_argument("--root", type=Path, help="Root path for flakiness leaderboard search (defaults to results_dir)")

    # mosdat dashboard  (H2.2: trend dashboard — appended after report block)
    dash_p = sub.add_parser("dashboard", help="H2.2: Generate static HTML trend dashboard from functional runs")
    dash_p.add_argument("--root", type=Path, default=Path("results"),
                        help="Root results directory (must contain functional/ subdir). Default: results/")
    dash_p.add_argument("--output", type=Path, default=None,
                        help="Output HTML path. Default: <root>/functional/dashboard.html")

    # mosdat live  (live event-stream dashboard — appended after L4 visual block)
    live_p = sub.add_parser("live", help="Live event-stream dashboard — stream smoke test events in real time")
    live_p.add_argument("--port", type=int, default=8080,
                        help="HTTP port to listen on (default: 8080)")
    live_p.add_argument("--results", type=Path, default=Path("results"), metavar="DIR",
                        help="Results root directory to watch (default: results/)")
    live_p.add_argument("--refresh-ms", type=int, default=500, dest="refresh_ms",
                        help="Poll interval in ms (default: 500)")

    # mosdat visual  (L4: visual regression — DO NOT reorder; L7 appends after this block)
    visual_p = sub.add_parser("visual", help="Visual regression: capture or check step screenshots via SSIM")
    visual_group = visual_p.add_mutually_exclusive_group(required=True)
    visual_group.add_argument("--capture", metavar="SCENARIO_DIR",
                              help="Capture *.png files in SCENARIO_DIR as references (sorted, 0-indexed)")
    visual_group.add_argument("--check", metavar="SCENARIO_DIR",
                              help="Check *.png files in SCENARIO_DIR against stored references")
    visual_p.add_argument("--refs-dir", metavar="DIR", default=None,
                          help="Root dir for reference images (default: shared/references/)")
    visual_p.add_argument("--threshold", type=float, default=0.95, metavar="T",
                          help="SSIM threshold [0,1] below which a check fails (default: 0.95)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    def cmd_live(args) -> int:
        from .live_dashboard import cli as live_cli
        argv = ["--port", str(args.port), "--results", str(args.results),
                "--refresh-ms", str(args.refresh_ms)]
        return live_cli(argv)

    def cmd_visual(args) -> int:
        from .visual import cli as visual_cli
        argv = []
        if args.capture:
            argv += ["--capture", args.capture]
        elif args.check:
            argv += ["--check", args.check]
        if args.refs_dir:
            argv += ["--refs-dir", args.refs_dir]
        argv += ["--threshold", str(args.threshold)]
        return visual_cli(argv)

    def cmd_dashboard(args) -> int:
        from .reporting.dashboard import cli as dashboard_cli
        argv = ["--root", str(args.root)]
        if args.output:
            argv += ["--output", str(args.output)]
        return dashboard_cli(argv)

    handlers = {
        "run": cmd_run,
        "test": cmd_test,
        "functional": cmd_functional,
        "confirm": cmd_confirm,
        "validate": cmd_validate,
        "list-vms": cmd_list_vms,
        "report": cmd_report,
        "live": cmd_live,
        "visual": cmd_visual,
        "dashboard": cmd_dashboard,
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
