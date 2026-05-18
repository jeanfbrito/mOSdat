"""Phase C — Issue-confirm orchestrator.

Top-level entry point: run_confirm(ConfirmInvocation) -> ConfirmRunArtifacts.

Loads a bug-confirmation scenario YAML, drives N iterations through
FunctionalRunner in bug-confirmation mode, collects VM state per iteration,
calls the report renderer, writes everything to results/issues/<id>/<run-id>/,
and returns the right exit code.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

Mode = Literal["confirm", "verify-fix", "regression"]


@dataclass
class ConfirmInvocation:
    issue_id_or_url: str
    vm_name: str
    iterations: int
    mode: Mode
    scenario_path: Optional[Path] = None       # override default
    output_dir: Optional[Path] = None          # override default
    refresh_issue_context: bool = False
    emit_html: bool = False
    skip_state_snapshot: bool = False
    record_session: bool = False
    record_fps: float = 10.0
    record_gif: bool = False
    record_keep_raw: bool = False
    repro_command: str = ""                    # the original CLI string for reporter footer
    git_rev: Optional[str] = None


@dataclass
class ConfirmRunArtifacts:
    run_id: str
    output_dir: Path
    report_md_path: Path
    comment_md_path: Path
    summary_json_path: Path
    iter_dirs: list[Path]
    verdict: str   # one of the AggregateVerdict literals from issue_report
    exit_code: int


# ---------------------------------------------------------------------------
# Exit-code mapping
# ---------------------------------------------------------------------------

def _exit_code(verdict: str, mode: Mode) -> int:
    """Map aggregate verdict + mode to exit code per plan §7.

    0  — verdict matches mode expectation
    1  — verdict opposite
    2  — INTERMITTENT
    3  — INCONCLUSIVE / HARNESS_ERROR
    """
    if verdict == "INTERMITTENT":
        return 2
    if verdict in ("INCONCLUSIVE", "HARNESS_ERROR"):
        return 3

    # mode expectation:
    #   confirm      → success = CONFIRMED
    #   verify-fix   → success = NOT_REPRODUCED
    #   regression   → success = NOT_REPRODUCED (alias)
    if mode == "confirm":
        return 0 if verdict == "CONFIRMED" else 1
    else:  # verify-fix / regression
        return 0 if verdict == "NOT_REPRODUCED" else 1


# ---------------------------------------------------------------------------
# Screenshot rename helper
# ---------------------------------------------------------------------------

def _copy_screenshot(src: Optional[Path], dest: Path) -> None:
    """Copy screenshot to dest with stable name, skipping if src is None or missing."""
    if src is None or not src.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


# ---------------------------------------------------------------------------
# ActualEnv from VmState
# ---------------------------------------------------------------------------

def _actual_env_from_vm_state(vm_state, vm_name: str):
    """Build an ActualEnv from VmState. Imports lazily to stay hermetic in tests."""
    from automation.reporting.issue_report import ActualEnv

    if vm_state is None:
        return ActualEnv(vm_name=vm_name)

    return ActualEnv(
        ozone=vm_state.ozone_backend,
        display_server=vm_state.display_server,
        install=vm_state.install_method,
        app_version=vm_state.app_version,
        os=vm_state.os_pretty_name,
        vm_name=vm_name,
        process_cmdline=vm_state.process_cmdline,
        config_view=vm_state.config_view,
    )


# ---------------------------------------------------------------------------
# Runner construction helper  (extracted so cmd_functional can share it)
# ---------------------------------------------------------------------------

def _build_runner_for_vm(vm, config, screenshot_dir: Path, log_fn=None):
    """Construct a FunctionalRunner for the given VMConfig + ProjectConfig.

    This mirrors the runner-construction block in cmd_functional without
    duplicating it verbatim.  cmd_functional continues to do its own inline
    construction (so its behaviour is unchanged); cmd_confirm calls this helper.
    """
    import os
    from datetime import datetime as dt

    from automation.vlm.client import VLMClient
    from automation.vlm.screenshot import Screenshotter
    from automation.vlm.input import InputInjector
    from automation.runners.functional import FunctionalRunner
    from automation.transport.ssh import SSHClient
    from automation.transport.vnc import VncClient
    from automation.proxmox.api import ProxmoxAPI

    if log_fn is None:
        log_fn = lambda msg: print(f"[mosdat] {msg}")

    vlm = VLMClient(
        base_url=config.vlm.base_url,
        model=config.vlm.model,
        verify_model=config.vlm.verify_model or None,
        api_key=config.vlm.api_key,
        max_tokens_floor=config.vlm.max_tokens_floor,
    )

    proxmox = ProxmoxAPI(config.proxmox)
    vnc = VncClient(proxmox, vmid=vm.vmid)

    ssh = SSHClient(vm.ip, vm.user)

    screenshotter = Screenshotter(vnc)
    injector = InputInjector(vnc, ssh, vm.is_windows)

    runner = FunctionalRunner(
        vlm=vlm,
        screenshotter=screenshotter,
        injector=injector,
        screenshot_dir=screenshot_dir,
        log_fn=log_fn,
    )

    return runner, ssh, vnc


# ---------------------------------------------------------------------------
# run_scenario_via_runner — thin wrapper (monkeypatched in tests)
# ---------------------------------------------------------------------------

def run_scenario_via_runner(runner, scenario, vars_: dict):
    """Call runner._run_bug_confirmation_scenario with the scenario's data.

    Kept as a module-level function so tests can monkeypatch it without
    touching FunctionalRunner internals.
    """
    from automation.runners.functional import load_test_yaml
    import yaml

    # Re-use _run_bug_confirmation_scenario which was added in Phase A
    return runner._run_bug_confirmation_scenario(
        steps=_steps_from_scenario(scenario),
        name=scenario.name,
        bug_signal=scenario.bug_signal,
        precondition_check=scenario.precondition_check,
        vars=vars_,
    )


def _steps_from_scenario(scenario):
    """Convert ScenarioModel steps to FunctionalStep list via load_test_yaml.

    We can't call load_test_yaml (it takes a file path).  Instead we rebuild
    steps directly: ScenarioModel already holds parsed step data, but they are
    Pydantic objects, not FunctionalStep objects.  We re-parse via the runner's
    _parse_step helper.
    """
    from automation.runners.functional import _parse_step
    steps = []
    for step in scenario.steps:
        raw = step.model_dump(exclude_none=True, exclude_defaults=False)
        # pydantic model_dump includes all fields; _parse_step expects a subset
        steps.append(_parse_step(raw))
    return steps


# ---------------------------------------------------------------------------
# run_confirm — top-level orchestrator
# ---------------------------------------------------------------------------

def run_confirm(inv: ConfirmInvocation) -> ConfirmRunArtifacts:
    """Top-level orchestrator. Side-effecting (writes to disk).

    Loads scenario + issue context, drives N iterations through
    FunctionalRunner in bug-confirmation mode, collects VM state per iter,
    renders the report, writes everything, returns artifacts.

    Exit code mapping per plan §7:
      0  — verdict matches mode expectation
      1  — verdict opposite
      2  — INTERMITTENT
      3  — INCONCLUSIVE / HARNESS_ERROR
      4  — runner / VM error before scenario could complete
    """
    from automation.issue_fetch import fetch_issue
    from automation.scenario import ScenarioModel
    from automation.reporting.issue_report import render, ActualEnv
    import yaml

    # ------------------------------------------------------------------
    # 1. Resolve issue context
    # ------------------------------------------------------------------
    print(f"[mosdat] Fetching issue context for {inv.issue_id_or_url!r}...")
    issue_ctx = fetch_issue(inv.issue_id_or_url, refresh=inv.refresh_issue_context)
    issue_id = issue_ctx.id

    # ------------------------------------------------------------------
    # 2. Resolve scenario path
    # ------------------------------------------------------------------
    if inv.scenario_path:
        scenario_path = inv.scenario_path
    else:
        # Default: shared/scenarios/issues/<id>.yaml relative to project root
        project_root = Path(__file__).resolve().parent.parent
        scenario_path = project_root / "shared" / "scenarios" / "issues" / f"{issue_id}.yaml"

    if not scenario_path.exists():
        print(f"[mosdat] ERROR: scenario file not found: {scenario_path}")
        # Return a minimal artifact set with exit code 4
        run_id = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        dummy_dir = Path(f"/tmp/mosdat-missing-{issue_id}-{run_id}")
        return ConfirmRunArtifacts(
            run_id=run_id,
            output_dir=dummy_dir,
            report_md_path=dummy_dir / "report.md",
            comment_md_path=dummy_dir / "comment.md",
            summary_json_path=dummy_dir / "summary.json",
            iter_dirs=[],
            verdict="HARNESS_ERROR",
            exit_code=4,
        )

    # Load and validate scenario
    with open(scenario_path) as f:
        raw_yaml = yaml.safe_load(f)
    scenario = ScenarioModel.model_validate(raw_yaml)

    # ------------------------------------------------------------------
    # 3. Run ID
    # ------------------------------------------------------------------
    run_id = datetime.now().strftime("%Y-%m-%d_%H%M%S")

    # ------------------------------------------------------------------
    # 4. Output directory
    # ------------------------------------------------------------------
    if inv.output_dir:
        output_dir = inv.output_dir
    else:
        project_root = Path(__file__).resolve().parent.parent
        output_dir = project_root / "results" / "issues" / issue_id / f"{run_id}_{inv.mode}"

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"[mosdat] Output directory: {output_dir}")

    # ------------------------------------------------------------------
    # 5. Load config + resolve VM
    # ------------------------------------------------------------------
    # We need a config to build the runner.  Look for a default config file.
    from automation.config import load_config
    _project_root = Path(__file__).resolve().parent.parent
    _config_candidates = list(_project_root.glob("*.toml")) + list(_project_root.glob("examples/*.toml"))
    if not _config_candidates:
        raise RuntimeError(
            "No TOML config found. Pass a config via MOSDAT_CONFIG env or place "
            "a *.toml in the project root."
        )
    import os as _os
    config_path = Path(_os.environ.get("MOSDAT_CONFIG", str(_config_candidates[0])))
    config = load_config(config_path)

    if inv.vm_name not in config.vm_by_name:
        raise RuntimeError(
            f"VM '{inv.vm_name}' not found in config. "
            f"Available: {', '.join(config.vm_by_name)}"
        )
    vm = config.vm_by_name[inv.vm_name]

    # Determine install method for vm_state
    install_method = "rpm"
    if scenario.expected_env and scenario.expected_env.install:
        install_method = scenario.expected_env.install
    else:
        for pkg in vm.packages:
            if pkg.format:
                install_method = pkg.format
                break

    # ------------------------------------------------------------------
    # 6. Per-iteration loop
    # ------------------------------------------------------------------
    all_iter_results = []
    iter_dirs: list[Path] = []
    collected_vm_states = []

    for n in range(1, inv.iterations + 1):
        iter_dir = output_dir / f"iter-{n}"
        iter_dir.mkdir(parents=True, exist_ok=True)
        iter_dirs.append(iter_dir)

        print(f"\n[mosdat] --- Iteration {n}/{inv.iterations} ---")

        iter_result = None
        vm_state = None
        vnc_ctx = None
        recorder = None

        try:
            # Build runner with iter_dir as screenshot dir
            runner, ssh, vnc_ctx = _build_runner_for_vm(
                vm, config, iter_dir,
                log_fn=lambda msg: print(f"[mosdat] {msg}"),
            )

            # Open VNC context manager manually so we can keep connection for vm_state
            vnc_ctx.__enter__()
            if inv.record_session:
                from automation.recording import SessionRecorder

                recorder = SessionRecorder(
                    screenshotter=runner.screenshotter,
                    recording_dir=iter_dir / "recording",
                    fps=inv.record_fps,
                    keep_raw=inv.record_keep_raw,
                    log_fn=lambda msg: print(f"[mosdat] {msg}"),
                )
                recorder.start()

            # Drive the scenario
            iter_result = run_scenario_via_runner(runner, scenario, {})

            # Collect VM state while SSH is still open
            if not inv.skip_state_snapshot:
                try:
                    from automation.runners.vm_state import collect as collect_vm_state
                    vm_state = collect_vm_state(ssh, install_method)
                    (iter_dir / "vm-state.json").write_text(
                        vm_state.to_json(), encoding="utf-8"
                    )
                    print(f"[mosdat]   vm-state.json written")
                except Exception as e:
                    print(f"[mosdat]   WARNING: vm_state collection failed: {e}")

        except Exception as exc:
            print(f"[mosdat]   ERROR in iter {n}: {exc}")
            # Record an INCONCLUSIVE iter rather than aborting
            from automation.runners.functional import BugConfirmationResult
            iter_result = BugConfirmationResult(
                precondition_met=False,
                bug_visible=False,
                bug_signal_screenshot=None,
                precondition_screenshot=None,
                final_screenshot=iter_dir / "final.png",  # may not exist
                step_failures=[{"step_num": 0, "kind": "exception",
                                "attempt": 0, "reason": "exception",
                                "error_text": str(exc)}],
                elapsed_ms=0,
            )

        finally:
            if recorder is not None:
                artifacts = recorder.stop_and_export(make_gif=inv.record_gif)
                print(f"[mosdat]   recording raw={artifacts.raw_frames} filtered={artifacts.filtered_frames}")
                if artifacts.mp4_path:
                    print(f"[mosdat]   recording mp4: {artifacts.mp4_path}")
                if artifacts.gif_path:
                    print(f"[mosdat]   recording gif: {artifacts.gif_path}")
                if artifacts.warning:
                    print(f"[mosdat]   recording warn: {artifacts.warning}")
            # Close VNC context if we entered it
            if vnc_ctx is not None:
                try:
                    vnc_ctx.__exit__(None, None, None)
                except Exception:
                    pass

        all_iter_results.append(iter_result)
        collected_vm_states.append(vm_state)

        # Copy screenshots to stable names for the renderer
        if iter_result is not None:
            _copy_screenshot(iter_result.bug_signal_screenshot, iter_dir / "bug-signal.png")
            _copy_screenshot(iter_result.precondition_screenshot, iter_dir / "precondition.png")
            _copy_screenshot(iter_result.final_screenshot, iter_dir / "final.png")

        print(f"[mosdat]   iter-{n} verdict: {iter_result.verdict if iter_result else 'ERROR'}")

    # ------------------------------------------------------------------
    # 7. Aggregate + report
    # ------------------------------------------------------------------
    # Build ActualEnv from the most-populated VmState (prefer iter-1)
    best_state = None
    for st in collected_vm_states:
        if st is not None:
            best_state = st
            break
    if best_state is None and collected_vm_states:
        # Try last iter
        for st in reversed(collected_vm_states):
            if st is not None:
                best_state = st
                break

    actual_env = _actual_env_from_vm_state(best_state, inv.vm_name)

    rendered = render(
        issue=issue_ctx,
        scenario=scenario,
        mode=inv.mode,
        iterations=all_iter_results,
        actual_env=actual_env,
        run_id=run_id,
        vm_name=inv.vm_name,
        git_rev=inv.git_rev,
        repro_command=inv.repro_command,
    )

    # ------------------------------------------------------------------
    # 8. Write report files
    # ------------------------------------------------------------------
    report_md_path = output_dir / "report.md"
    comment_md_path = output_dir / "comment.md"
    summary_json_path = output_dir / "summary.json"

    report_md_path.write_text(rendered.report_md, encoding="utf-8")
    comment_md_path.write_text(rendered.comment_md, encoding="utf-8")
    summary_json_path.write_text(rendered.summary_json, encoding="utf-8")

    # Optional HTML: wrap in <pre> (no new dep, deferred for v1)
    if inv.emit_html:
        # TODO: use markdown library if it becomes a dep; for v1, simple <pre> wrap
        html_path = output_dir / "report.html"
        html_content = (
            "<!DOCTYPE html><html><body><pre>"
            + rendered.report_md.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            + "</pre></body></html>"
        )
        html_path.write_text(html_content, encoding="utf-8")
        print(f"[mosdat] report.html: {html_path}")

    # ------------------------------------------------------------------
    # 9. Exit code
    # ------------------------------------------------------------------
    exit_code = _exit_code(rendered.verdict, inv.mode)

    return ConfirmRunArtifacts(
        run_id=run_id,
        output_dir=output_dir,
        report_md_path=report_md_path,
        comment_md_path=comment_md_path,
        summary_json_path=summary_json_path,
        iter_dirs=iter_dirs,
        verdict=rendered.verdict,
        exit_code=exit_code,
    )
