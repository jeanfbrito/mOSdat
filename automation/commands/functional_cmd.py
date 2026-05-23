"""cmd_functional and its helpers — extracted from automation/main.py."""
import os
import signal
import time as _time
import urllib.request
import urllib.error
from pathlib import Path


class RuntimeWatchdogTimeout(Exception):
    pass


def _watchdog_handler(signum, frame):
    raise RuntimeWatchdogTimeout()


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


def cmd_functional(args) -> int:
    from automation.commands.record_cmd import cmd_record
    if getattr(args, "record", False):
        return cmd_record(args)

    from pathlib import Path as P

    # I6: honour --no-cache flag before any VLM calls are made
    if getattr(args, "no_cache", False):
        from automation.vlm.client import set_cache_enabled
        set_cache_enabled(False)

    from automation.config import load_config
    config = load_config(args.config)

    vm_names = [v.strip() for v in args.vms.split(",")]
    for name in vm_names:
        if name not in config.vm_by_name:
            print(f"[mOSdat] ERROR: Unknown VM '{name}'. Available: {', '.join(config.vm_by_name.keys())}")
            return 1

    vms = [config.vm_by_name[name] for name in vm_names]

    # Resolve test file — try per-OS subdir of first VM first, then root fallback.
    # All VMs in a single invocation share the same scenario file; subdir is picked
    # from vms[0] (mosdat dispatches per-platform anyway in real runs).
    tests_dir = config.functional.tests_dir or (config.framework_path / "shared" / "scenarios" / "functional")
    test_name = args.test or "rocketchat-smoke"
    from automation.runners.scenario_loader import resolve_test_path, ScenarioNotFoundError
    try:
        test_file = resolve_test_path(test_name, tests_dir, subdir=vms[0].scenario_subdir)
    except ScenarioNotFoundError as exc:
        print(f"[mOSdat] ERROR: {exc}")
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
    # NB: don't slice `steps` yet — we re-load per-VM inside the loop with
    # `platform=vm.os_type` so routine expansion picks the per-OS variant
    # (e.g. shared/routines/windows/<slug>.yaml). Slicing is reapplied to
    # the per-VM step list using the bounds validated above.
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

            # Stage 4: re-load steps with platform=vm.os_type so routine calls
            # (`shared/routines/<platform>/<slug>.yaml`) resolve per-OS. Bounds
            # already validated against the platform-less load above; we just
            # reapply the slice. Same yaml_checkpoints/vars semantics.
            _, vm_steps, _, _ = load_test_yaml(test_file, platform=vm.os_type)
            vm_steps = vm_steps[from_step - 1 : until_step]

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
                # Stage 1D / Stage 4: per-OS coordinate-free driver. Both
                # clients share the same per-VM persistent SSHClient (one
                # ControlMaster multiplex for AT-SPI on Linux, one for UIA
                # on Windows). Shell steps keep the non-persistent `ssh` to
                # avoid holding the master across heavyweight remote work.
                from automation.atspi import AtspiClient
                from automation.uia import UiaClient
                ssh_atspi = SSHClient(vm.ip, vm.user, persistent=True)
                atspi_client = None
                uia_client = None
                if vm.is_windows:
                    uia_client = UiaClient(ssh=ssh_atspi)
                else:
                    # Linux + any unrecognized os_type fall back to AT-SPI;
                    # macOS is not yet supported (both None would dispatch-fail
                    # loudly via _require_a11y on any atspi: / verify_atspi:
                    # / wait_for: step).
                    atspi_client = AtspiClient(ssh=ssh_atspi)
                recorder = None
                # C2: vm_ops needed only when checkpoints are enabled
                from automation.proxmox.vm import VMOperations
                _vm_ops_for_ckpt = None
                if checkpoint_config.get("enabled"):
                    _vm_ops_for_ckpt = VMOperations(proxmox, vm, config)
                try:
                    if getattr(args, "record_session", False):
                        # Stage 3a: opt-in window/cursor state sampling.
                        # wmctrl/xdotool are Linux-only — skip on Windows VMs.
                        _record_ws = bool(
                            getattr(args, "record_window_state", False)
                        ) and not vm.is_windows
                        recorder = SessionRecorder(
                            screenshotter=screenshotter,
                            recording_dir=screenshot_dir / "recording",
                            fps=float(getattr(args, "record_fps", 10.0)),
                            keep_raw=bool(getattr(args, "record_keep_raw", False)),
                            log_fn=lambda msg: print(f"[mOSdat] {msg}"),
                            ssh=ssh if _record_ws else None,
                            record_window_state=_record_ws,
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
                        app_process_name=config.app.process_name,
                        atspi=atspi_client,
                        uia=uia_client,
                    )

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

                    passed, log = runner.run_test(vm_steps, name, vars=vm_vars)
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
                    # Stage 3c / Stage 4: tear down the persistent a11y
                    # ControlMaster (shared by AT-SPI on Linux, UIA on
                    # Windows). Best-effort — VNC/recorder cleanup follows.
                    if ssh_atspi is not None:
                        try:
                            ssh_atspi.close_persistent()
                        except Exception:
                            pass
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
