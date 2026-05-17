#!/usr/bin/env python3
import argparse
import os
import signal
import sys
import urllib.request
import urllib.error
import time as _time
from pathlib import Path

from automation.config import load_config


def cmd_run(args) -> int:
    config = load_config(args.config)
    config.skip_build = args.skip_build
    config.resume = args.resume
    config.only_vm = args.only
    config.allow_incomplete = args.allow_incomplete
    if args.results_dir:
        config.results_dir = args.results_dir

    from automation.runners.smoke import TestRunner
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

    from automation.runners.smoke import TestRunner
    runner = TestRunner(config)

    try:
        success = runner.run_quick(args.package)
        return 0 if success else 1
    except KeyboardInterrupt:
        print("\n[mOSdat] Interrupted by user")
        return 130



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


def _resolve_phases(phases, total_steps, from_phase_id, until_phase_id, from_step_arg, until_step_arg):
    """I9: Resolve --from-phase / --until-phase to integer step bounds.

    Returns (from_step, until_step, phase_log_line) where phase_log_line is
    None when no phase flags are used.

    Raises SystemExit(1) on unknown phase id.
    """
    if from_phase_id is None and until_phase_id is None:
        return from_step_arg, until_step_arg, None

    if not phases:
        print(f"[mOSdat] ERROR: --from-phase/--until-phase used but scenario has no phases: block")
        raise SystemExit(1)

    phase_by_id = {p.id: p for p in phases}

    if from_phase_id and from_phase_id not in phase_by_id:
        ids = ", ".join(p.id for p in phases)
        print(f"[mOSdat] ERROR: --from-phase {from_phase_id!r} not declared in scenario phases ({ids})")
        raise SystemExit(1)

    if until_phase_id and until_phase_id not in phase_by_id:
        ids = ", ".join(p.id for p in phases)
        print(f"[mOSdat] ERROR: --until-phase {until_phase_id!r} not declared in scenario phases ({ids})")
        raise SystemExit(1)

    # Compute last step for each phase
    phase_list = list(phases)
    phase_last_step = {}
    for idx, phase in enumerate(phase_list):
        if idx + 1 < len(phase_list):
            phase_last_step[phase.id] = phase_list[idx + 1].from_step - 1
        else:
            phase_last_step[phase.id] = total_steps

    new_from = from_step_arg
    new_until = until_step_arg

    if from_phase_id:
        if from_step_arg != 1:
            print(f"[mOSdat] WARNING: --from-phase {from_phase_id!r} overrides --from-step {from_step_arg}")
        new_from = phase_by_id[from_phase_id].from_step

    if until_phase_id:
        if until_step_arg is not None:
            print(f"[mOSdat] WARNING: --until-phase {until_phase_id!r} overrides --until-step {until_step_arg}")
        new_until = phase_last_step[until_phase_id]

    # Build log line: list all phases that overlap [new_from, new_until]
    effective_until = new_until if new_until is not None else total_steps
    parts = []
    for phase in phase_list:
        p_first = phase.from_step
        p_last = phase_last_step[phase.id]
        if p_first <= effective_until and p_last >= new_from:
            parts.append(f"{phase.id} ({phase.name}) [steps {p_first}..{p_last}]")
    log_line = "[phases] running " + ", ".join(parts) if parts else None

    return new_from, new_until, log_line


def cmd_functional(args) -> int:
    if getattr(args, "record", False):
        from automation.commands.record_cmd import cmd_record
        return cmd_record(args)

    # I6: honour --no-cache flag before any VLM calls are made
    if getattr(args, "no_cache", False):
        from automation.vlm.client import set_cache_enabled
        set_cache_enabled(False)

    from pathlib import Path as P

    config = load_config(args.config)
    if getattr(args, "cursor_instant", False):
        config.cursor.profile = "instant"

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

    from automation.vlm.client import VLMClient
    from automation.vlm.screenshot import Screenshotter
    from automation.vlm.input import InputInjector
    from automation.recording import SessionRecorder
    from automation.runners.functional import FunctionalRunner, load_test_yaml

    vlm = VLMClient(
        base_url=config.vlm.base_url,
        model=args.model or config.vlm.model,
        verify_model=args.verify_model or config.vlm.verify_model or None,
        api_key=config.vlm.api_key,
        max_tokens_floor=config.vlm.max_tokens_floor,
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

    # I8: parse --var KEY=VALUE flags into a dict; error on duplicate keys
    cli_var_list = getattr(args, "vars", []) or []
    cli_vars: dict[str, str] = {}
    for kv in cli_var_list:
        if "=" not in kv:
            print(f"[mOSdat] ERROR: --var value must be KEY=VALUE (got: {kv!r})")
            return 1
        k, v = kv.split("=", 1)
        if k in cli_vars:
            print(f"[mOSdat] ERROR: duplicate --var key: {k!r}")
            return 1
        cli_vars[k] = v

    name, steps, vars_, yaml_checkpoints = load_test_yaml(test_file, cli_vars=cli_vars)

    # C2: resolve checkpoint config (YAML wins unless --no-checkpoints overrides)
    checkpoint_config = dict(yaml_checkpoints)
    if getattr(args, "no_checkpoints", False):
        checkpoint_config["enabled"] = False

    # I9: load phases from YAML (re-parse for PhaseDef objects)
    _yaml_phases = None
    _yaml_config_snapshots = False
    try:
        import yaml as _yaml
        _raw_data = _yaml.safe_load(test_file.read_text())
        _raw_phases = _raw_data.get("phases")
        if _raw_phases:
            from automation.scenario import PhaseDef
            _yaml_phases = [PhaseDef.model_validate(p) for p in _raw_phases]
        # I14: opt-in config.json snapshots
        _yaml_config_snapshots = bool(_raw_data.get("report_config_snapshots", False))
    except Exception:
        pass  # phases optional; any parse error falls through to phase-flag error below

    # I9: resolve phase flags → integer step bounds
    total_steps = len(steps)
    _from_phase = getattr(args, "from_phase", None)
    _until_phase = getattr(args, "until_phase", None)
    from_step, until_step, _phase_log = _resolve_phases(
        _yaml_phases, total_steps,
        _from_phase, _until_phase,
        args.from_step, args.until_step,
    )
    if _phase_log:
        print(f"[mOSdat] {_phase_log}")

    # B4: step slicing
    if until_step is None:
        until_step = total_steps
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
    from automation.proxmox.api import ProxmoxAPI
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

            from automation.transport.ssh import SSHClient
            from automation.transport.vnc import VncClient
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
                recorder = None
                # C2: vm_ops needed only when checkpoints are enabled
                from automation.proxmox.vm import VMOperations
                _vm_ops_for_ckpt = None
                if checkpoint_config.get("enabled"):
                    _vm_ops_for_ckpt = VMOperations(proxmox, vm, config)
                try:
                    if getattr(args, "record_session", False):
                        recorder = SessionRecorder(
                            screenshotter=screenshotter,
                            recording_dir=screenshot_dir / "recording",
                            fps=float(getattr(args, "record_fps", 10.0)),
                            diff_threshold=float(getattr(args, "record_diff_threshold", 3.0)),
                            keep_raw=bool(getattr(args, "record_keep_raw", False)),
                            log_fn=lambda msg: print(f"[mOSdat] {msg}"),
                        )
                        recorder.start()
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
                        x11_mode=getattr(vm, "x11", "off"),
                        app_process_name=config.app.process_name,
                    )
                    # I14: enable config snapshots if scenario opts in OR --config-snapshots flag set
                    runner._config_snapshots = (
                        _yaml_config_snapshots
                        or bool(getattr(args, "config_snapshots", False))
                    )

                    # I1: declarative userData pre-staging (BEFORE first scenario step)
                    _inject_cfg = getattr(args, "inject_config", None)
                    _inject_srv = getattr(args, "inject_servers", None)
                    if _inject_cfg is not None or _inject_srv is not None:
                        from automation.setup.inject_config import (
                            inject as _inject,
                            parse_inject_config,
                            parse_inject_servers,
                        )
                        try:
                            cfg_dict = parse_inject_config(_inject_cfg) if _inject_cfg else {}
                            srv_dict = parse_inject_servers(_inject_srv) if _inject_srv else {}
                        except Exception as parse_err:
                            print(f"[mOSdat] ERROR: --inject-* parse failed: {parse_err}")
                            return 4
                        install_path = (
                            getattr(args, "inject_install_path", None)
                            or vm_vars.get("app_path")
                        )
                        if not install_path:
                            print(
                                "[mOSdat] ERROR: --inject-config/--inject-servers requires "
                                "an install path (no app_path on VM packages; "
                                "pass --inject-install-path)"
                            )
                            return 4
                        try:
                            _inject(
                                ssh,
                                install_path=install_path,
                                config=cfg_dict,
                                servers=srv_dict,
                                app_name=getattr(args, "inject_app_name", None),
                                migrations_version=getattr(args, "inject_migrations_version", None),
                                log_fn=lambda msg: print(f"[mOSdat] {msg}"),
                            )
                        except Exception as inj_err:
                            print(f"[mOSdat] ERROR: inject failed: {inj_err}")
                            return 4

                    # B7: VM health probe before scenario start
                    if not getattr(args, "skip_health_probe", False):
                        print("[mOSdat]   Probing VM health...")
                        healthy = runner._probe_vm_health()
                        if not healthy:
                            print("[mOSdat]   WARNING: VM appears frozen — attempting reset...")
                            from automation.proxmox.vm import VMOperations
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
                        from automation.reporting.report import generate_html_report
                        report_path = generate_html_report(screenshot_dir)
                        print(f"[mOSdat] Report: file://{report_path.absolute()}")
                    except Exception as e:
                        print(f"[mOSdat] WARN: report generation failed: {e}")
                finally:
                    if recorder is not None:
                        artifacts = recorder.stop_and_export(
                            make_gif=bool(getattr(args, "record_gif", False))
                        )
                        print(
                            f"[mOSdat] Recording: raw={artifacts.raw_frames} filtered={artifacts.filtered_frames}"
                        )
                        if artifacts.mp4_path:
                            print(f"[mOSdat] Recording MP4: {artifacts.mp4_path}")
                        if artifacts.gif_path:
                            print(f"[mOSdat] Recording GIF: {artifacts.gif_path}")
                        if artifacts.warning:
                            print(f"[mOSdat] Recording WARN: {artifacts.warning}")

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
            from automation.notify import notify
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


def main() -> int:
    from automation.commands.parser import build_parser
    from automation.commands.dispatchers import (
        cmd_author,
        cmd_confirm,
        cmd_dashboard,
        cmd_draft,
        cmd_live,
        cmd_report,
        cmd_visual,
        cmd_vlm_cache,
    )
    from automation.commands.preflight import run_preflight
    from automation.commands.replay import run_replay
    from automation.commands.build import run_build
    from automation.commands.doctor import run_doctor
    from automation.commands.recipes import run_recipes
    from automation.commands.routines import run_routines
    from automation.commands.lint import run_lint
    from automation.commands.trace import run_trace

    def cmd_preflight(args) -> int:
        vms = [v.strip() for v in args.vms.split(",")]
        return run_preflight(
            config_path=str(args.config),
            scenario_name=args.test,
            vms=vms,
            expected_symbols=args.expect_symbols or [],
        )

    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    handlers = {
        "run": cmd_run,
        "test": cmd_test,
        "functional": cmd_functional,
        "confirm": cmd_confirm,
        "validate": cmd_validate,
        "list-vms": cmd_list_vms,
        "report": cmd_report,
        "live": cmd_live,
        "author": cmd_author,
        "draft": cmd_draft,
        "visual": cmd_visual,
        "dashboard": cmd_dashboard,
        "preflight": cmd_preflight,
        "replay": run_replay,
        "build": run_build,
        "doctor": run_doctor,
        "vlm-cache": cmd_vlm_cache,
        "recipes": run_recipes,
        "routines": run_routines,
        "lint": run_lint,
        "trace": run_trace,
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

