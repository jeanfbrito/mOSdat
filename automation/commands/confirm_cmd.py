"""cmd_confirm and _git_rev_short — extracted from automation/main.py."""
import subprocess


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
    from automation.issue_confirm import ConfirmInvocation, run_confirm

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
        record_session=args.record_session,
        record_fps=args.record_fps,
        record_gif=args.record_gif,
        record_keep_raw=args.record_keep_raw,
        repro_command=repro,
        git_rev=git_rev,
    )
    artifacts = run_confirm(inv)
    print(f"[mosdat] verdict: {artifacts.verdict}")
    print(f"[mosdat] report:  {artifacts.report_md_path}")
    print(f"[mosdat] comment: {artifacts.comment_md_path}")
    return artifacts.exit_code
