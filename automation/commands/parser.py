"""Argparse wiring for the ``mosdat`` CLI.

Extracted from ``automation.main`` to keep that file under 500 LOC.
``build_parser()`` returns the configured ``argparse.ArgumentParser`` used
by ``automation.main:main`` and exercised by ``tests/test_help_drift.py``.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def _add_author_subparser(sub) -> None:
    """Wire the 'author' subcommand and all its sub-subcommands."""
    author_p = sub.add_parser("author", help="Agent client for the live authoring API")
    author_p.add_argument("--url", default="http://127.0.0.1:8080", help="Live dashboard URL")
    s = author_p.add_subparsers(dest="author_command", required=True)
    s.add_parser("vms", help="List configured VMs and Proxmox power state")
    s.add_parser("doctor", help="Check author dashboard readiness")
    p = s.add_parser("start", help="Create an authoring session")
    p.add_argument("--vm", required=True); p.add_argument("--model"); p.add_argument("--verify-model")
    p = s.add_parser("session", help="Show authoring session state")
    p.add_argument("--session", required=True)
    p = s.add_parser("capture", help="Capture the current VNC screen")
    p.add_argument("--session", required=True); p.add_argument("--output", help="Write captured BMP image bytes to path")
    p = s.add_parser("localize", help="Find a prompt on the current screen")
    p.add_argument("--session", required=True); p.add_argument("--prompt", required=True)
    p = s.add_parser("describe", help="Describe a clicked screenshot point as a localize prompt")
    p.add_argument("--session", required=True); p.add_argument("--x", required=True, type=int); p.add_argument("--y", required=True, type=int)
    p = s.add_parser("verify", help="Ask the VLM a yes/no screen question")
    p.add_argument("--session", required=True); p.add_argument("--question", required=True)
    p = s.add_parser("action", help="Run a confirmed authoring action")
    p.add_argument("--session", required=True)
    p.add_argument("--kind", required=True, choices=["hover", "click", "type", "key", "shell", "wait", "launch"])
    p.add_argument("--json", default="{}", help="Action payload JSON")
    p = s.add_parser("click", help="Run a confirmed click action")
    p.add_argument("--session", required=True); p.add_argument("--x", required=True, type=int); p.add_argument("--y", required=True, type=int)
    p.add_argument("--button", choices=["left", "right"], default="left"); p.add_argument("--prompt")
    p = s.add_parser("prompt-click", help="Localize a prompt and click its center")
    p.add_argument("--session", required=True); p.add_argument("--prompt", required=True)
    p.add_argument("--button", choices=["left", "right"], default="left")
    p = s.add_parser("prompt-hover", help="Localize a prompt and hover its center")
    p.add_argument("--session", required=True); p.add_argument("--prompt", required=True)
    p = s.add_parser("prompt-type", help="Localize a prompt, click it, then type text")
    p.add_argument("--session", required=True); p.add_argument("--prompt", required=True)
    p.add_argument("--text", required=True); p.add_argument("--button", choices=["left", "right"], default="left")
    p = s.add_parser("hover", help="Run a confirmed hover action")
    p.add_argument("--session", required=True); p.add_argument("--x", required=True, type=int); p.add_argument("--y", required=True, type=int); p.add_argument("--prompt")
    p = s.add_parser("type", help="Run a confirmed text input action")
    p.add_argument("--session", required=True); p.add_argument("--text", required=True)
    p = s.add_parser("key", help="Run a confirmed key press action")
    p.add_argument("--session", required=True); p.add_argument("--key", required=True)
    p = s.add_parser("wait", help="Append and run a bounded wait action")
    p.add_argument("--session", required=True); p.add_argument("--seconds", type=int, default=1)
    p = s.add_parser("shell", help="Run a confirmed shell action")
    p.add_argument("--session", required=True); p.add_argument("--cmd", required=True)
    p = s.add_parser("launch", help="Run a confirmed launch action")
    p.add_argument("--session", required=True); p.add_argument("--cmd", required=True); p.add_argument("--wait", type=int, default=0)
    p = s.add_parser("validate", help="Validate current draft scenario")
    p.add_argument("--session", required=True); p.add_argument("--name", default="authored-scenario")
    p = s.add_parser("export", help="Export current draft scenario YAML")
    p.add_argument("--session", required=True); p.add_argument("--name", default="authored-scenario")
    p.add_argument("--output", help="Write YAML to path instead of embedding it in JSON; use - for stdout JSON")
    p = s.add_parser("step", help="Append or replace draft scenario steps")
    p.add_argument("--session", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--json", dest="step_json", help="Step JSON object to append")
    g.add_argument("--steps-json", help="Full steps JSON array to replace draft")
    p = s.add_parser("close", help="Close an authoring session")
    p.add_argument("--session", required=True)


def build_parser() -> argparse.ArgumentParser:
    """Return the configured ``mosdat`` argparse parser."""
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
    fn_p.add_argument("--record-session", action="store_true",
                      help="Record VNC frames during run and export change-filtered replay artifacts")
    fn_p.add_argument("--record-fps", type=float, default=10.0, metavar="FPS",
                      help="Recorder capture rate (default: 10.0)")
    fn_p.add_argument("--record-diff-threshold", type=float, default=3.0, metavar="VALUE",
                      help="Mean grayscale diff threshold for frame retention (default: 3.0)")
    fn_p.add_argument("--record-gif", action="store_true",
                      help="Also export recording/session.gif (MP4 is always attempted)")
    fn_p.add_argument("--record-keep-raw", action="store_true",
                      help="Keep recording/raw frames after export")

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
    confirm_p.add_argument("--record-session", action="store_true",
                           help="Record VNC frames during each iteration and export change-filtered replay artifacts")
    confirm_p.add_argument("--record-fps", type=float, default=10.0, metavar="FPS",
                           help="Recorder capture rate (default: 10.0)")
    confirm_p.add_argument("--record-diff-threshold", type=float, default=3.0, metavar="VALUE",
                           help="Mean grayscale diff threshold for frame retention (default: 3.0)")
    confirm_p.add_argument("--record-gif", action="store_true",
                           help="Also export recording/session.gif (MP4 is always attempted)")
    confirm_p.add_argument("--record-keep-raw", action="store_true",
                           help="Keep recording/raw frames after export")

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
    live_p.add_argument("--warn-after", type=int, default=90, dest="warn_after",
                        help="Mark VM warning/stale after N seconds without events (default: 90)")
    live_p.add_argument("--stale-after", type=int, default=180, dest="stale_after",
                        help="Mark VM stale after N seconds without events (default: 180)")
    live_p.add_argument("--config", type=Path, default=None,
                        help="mosdat config path; enables browser authoring sessions")

    # mosdat author
    _add_author_subparser(sub)

    # mosdat draft  (scenario authoring — generate YAML from change descriptions)
    draft_p = sub.add_parser("draft", help="Generate functional test scenario YAML from change descriptions")
    draft_p.add_argument("--change-type", choices=[
        "ui", "persistence", "protocol_handler", "keyboard_shortcut",
        "settings", "bug_fix", "de", "autostart",
    ], help="Type of change to generate a scenario for")
    draft_p.add_argument("--pr", default="", help="PR number (e.g. 3325)")
    draft_p.add_argument("--description", default="unnamed", help="Short description of the change")
    draft_p.add_argument("--output", type=Path, default=None, help="Write YAML to path instead of stdout")
    draft_p.add_argument("--templates", action="store_true", help="Write all templates to disk and exit")

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

    return parser
