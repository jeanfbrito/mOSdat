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
    p.add_argument("--output", help="Write YAML/diff to path instead of stdout")
    p.add_argument("--base", metavar="SCENARIO_YAML", help="I12: base scenario to diff against")
    p.add_argument("--insert-at-step", type=int, default=None, metavar="N", help="I12: insert recorded steps BEFORE step N (1-indexed); default: append")
    p.add_argument("--dry-run", action="store_true", help="I12: validate but don't write or emit")
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
    fn_p.add_argument("--cursor-instant", action="store_true",
                      help="Skip human-like cursor motion (instant teleport, fast CI runs)")
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
    fn_p.add_argument("--no-record-session", action="store_false",
                      dest="record_session", default=True,
                      help="Disable VNC frame recording (recording is ON by default)")
    # Back-compat: --record-session is the historical opt-in flag; now a no-op
    # since recording is default-on. Kept hidden so old invocations don't break.
    fn_p.add_argument("--record-session", action="store_true", dest="record_session",
                      help=argparse.SUPPRESS)
    # Recorder + runner now share a LatestFrameBus (frame_bus.py). The recorder
    # is the only VNC producer; the runner consumes the most-recent post-deadline
    # frame from the bus. Higher recorder FPS no longer starves runner captures.
    # Actual raw FPS is still bounded by VNC capture latency (~20–50 ms/frame).
    fn_p.add_argument("--record-fps", type=float, default=30.0, metavar="FPS",
                      help="Recorder capture rate request (default: 30.0). Runner reads from the same bus so contention is gone; actual rate caps at VNC capture latency.")
    fn_p.add_argument("--record-gif", action="store_true",
                      help="Also export recording/session.gif (MP4 is always attempted)")
    fn_p.add_argument("--record-keep-raw", action="store_true",
                      help="Keep recording/raw frames after export")
    # I1: declarative userData pre-staging
    fn_p.add_argument("--inject-config", dest="inject_config", default=None, metavar="JSON",
                      help="I1: JSON object (inline or @path.json) merged into "
                           "config.json before launch. Pins __internal__.migrations.version "
                           "and defaults currentView from --inject-servers.")
    fn_p.add_argument("--inject-servers", dest="inject_servers", default=None, metavar="LIST",
                      help="I1: TITLE=URL,TITLE=URL (or @path.json with {title:url} dict). "
                           "Written as servers.json to both userData dirs before launch.")
    fn_p.add_argument("--inject-app-name", dest="inject_app_name", default=None, metavar="NAME",
                      help="I1: Electron productName for userData path detection "
                           "(default: auto-detect via strings(app.asar)).")
    fn_p.add_argument("--inject-install-path", dest="inject_install_path", default=None, metavar="PATH",
                      help="I1: Override install path (binary or dir) for asar/version "
                           "detection. Default: derived from VM package app_path.")
    fn_p.add_argument("--inject-migrations-version", dest="inject_migrations_version",
                      default=None, metavar="VER",
                      help="I1: pin __internal__.migrations.version explicitly "
                           "(default: auto-detect via <binary> --version).")
    fn_p.add_argument("--no-cache", action="store_true", dest="no_cache",
                      help="I6: disable VLM verify result cache for this run")
    fn_p.add_argument("--config-snapshots", action="store_true", dest="config_snapshots",
                      help="I14: capture config.json after each shell step (overrides yaml; default off)")
    fn_p.add_argument("--var", action="append", dest="vars", default=[], metavar="KEY=VALUE",
                      help="I8: override or set a scenario var (repeatable). "
                           "Format: KEY=VALUE. CLI overrides scenario yaml vars: block. "
                           "Error on duplicate keys within CLI --var flags.")
    fn_p.add_argument("--from-phase", dest="from_phase", default=None, metavar="ID",
                      help="I9: start at the first step of named phase ID "
                           "(overrides --from-step; warns if both given)")
    fn_p.add_argument("--until-phase", dest="until_phase", default=None, metavar="ID",
                      help="I9: stop after the last step of named phase ID "
                           "(overrides --until-step; warns if both given)")

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
    confirm_p.add_argument("--no-record-session", action="store_false",
                           dest="record_session", default=True,
                           help="Disable VNC frame recording (recording is ON by default)")
    # Back-compat hidden alias — recording is default-on now.
    confirm_p.add_argument("--record-session", action="store_true", dest="record_session",
                           help=argparse.SUPPRESS)
    confirm_p.add_argument("--record-fps", type=float, default=30.0, metavar="FPS",
                           help="Recorder capture rate request (default: 30.0). Runner shares the frame bus — see --record-fps in functional subcommand.")
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

    # mosdat build  (I3: PR clone + build + deploy + verify)
    from automation.commands.build import add_build_subparser
    add_build_subparser(sub)

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

    # mosdat preflight
    pf_p = sub.add_parser(
        "preflight",
        help="I2: pre-run health checks for a functional scenario",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example: mosdat preflight examples/rocketchat.toml "
            "--vms ubuntu2204 --test 3325-master-toggle "
            "--expect-symbol isTelephonyEnabled"
        ),
    )
    pf_p.add_argument("config", type=Path, help="Path to mosdat config (TOML)")
    pf_p.add_argument("--vms", required=True, help="Comma-separated VM names")
    pf_p.add_argument("--test", required=True, metavar="NAME",
                      help="Scenario name without .yaml extension")
    pf_p.add_argument("--expect-symbol", dest="expect_symbols", action="append",
                      default=[], metavar="SYM",
                      help="Symbol that must appear in strings <asar> (repeatable)")

    # mosdat replay  (I5: rerun a verify against a cached screenshot)
    replay_p = sub.add_parser("replay", help="Rerun a VLM verify against a cached result-dir screenshot")
    replay_p.add_argument("result_dir", metavar="result-dir",
                          help="Path to VM result dir (contains events.jsonl + screenshots)")
    replay_p.add_argument("--step", type=int, default=None, metavar="N",
                          help="Step number to replay (omit to list available steps)")
    replay_p.add_argument("--verify", metavar="PROMPT", default=None,
                          help="New verify prompt to send to the VLM")
    replay_p.add_argument("--model", metavar="MODEL", default=None,
                          help="VLM model override (default: qwen3.6-35b-a3b-apex-vl)")
    replay_p.add_argument("--all-attempts", action="store_true", dest="all_attempts",
                          help="Rerun against every verify_poll screenshot of that step")
    replay_p.add_argument("--no-cache", action="store_true", dest="no_cache",
                          help="I6: disable VLM verify result cache for this replay")

    # mosdat vlm-cache  (I6: cache management)
    cache_p = sub.add_parser("vlm-cache", help="I6: Manage the VLM verify result cache")
    cache_sub = cache_p.add_subparsers(dest="cache_command", required=True)
    cache_sub.add_parser("stats", help="Show entry count, size, and hit rate")
    cache_sub.add_parser("clear", help="Wipe all cached entries")
    prune_p = cache_sub.add_parser("prune", help="Remove entries older than a duration")
    prune_p.add_argument(
        "--older-than", dest="older_than", default="7d", metavar="DURATION",
        help="Remove entries older than this duration (e.g. 7d, 48h, 3600s). Default: 7d",
    )

    # mosdat lint  (F1a: static scenario analyzer)
    lint_p = sub.add_parser(
        "lint",
        help="F1a: Static analyzer for functional scenario YAML files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example: mosdat lint shared/scenarios/functional/3325-master-toggle.yaml",
    )
    lint_p.add_argument("scenario", help="Path to scenario YAML file")

    # mosdat trace  (F1b: input capability probe)
    trace_p = sub.add_parser(
        "trace",
        help="F1b: Probe input capabilities (menu accelerators, shortcuts, focus) on a VM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example: mosdat trace examples/rocketchat.toml --vms ubuntu2204 --write-manifest",
    )
    trace_p.add_argument("config", type=Path, help="Path to mosdat config (TOML)")
    trace_p.add_argument("--vms", required=True, help="Comma-separated VM names")
    trace_p.add_argument(
        "--write-manifest", action="store_true", dest="write_manifest",
        help="Persist probe results to shared/binary_capabilities/<sha>.json",
    )
    trace_p.add_argument(
        "--probe-hover", dest="probe_hover", default=None, metavar="X,Y[;X2,Y2...]",
        help="Probe hover-required classification at semicolon-separated coord pairs (e.g. '97,380;200,400')",
    )

    # mosdat doctor  (I13: VM + host health checks)
    doctor_p = sub.add_parser("doctor", help="I13: Check VM and host health (SSH, deps, disk, processes)")
    doctor_p.add_argument("config", type=Path, help="Path to mosdat config (TOML)")
    doctor_p.add_argument("--vms", default="", metavar="VM1,VM2",
                          help="Comma-separated VM names to check (default: all VMs in config)")

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

    # mosdat recipes  (F2: platform-constraint corpus + pivot browser)
    recipes_p = sub.add_parser("recipes", help="F2: Browse known platform constraints and workarounds")
    recipes_sub = recipes_p.add_subparsers(dest="recipes_command", required=True)
    recipes_sub.add_parser("list", help="List all recipes (slug + title)")
    show_p = recipes_sub.add_parser("show", help="Show full recipe body")
    show_p.add_argument("slug", help="Recipe slug (e.g. settings-electron-linux)")
    search_p = recipes_sub.add_parser("search", help="Search recipes by title, symptoms, or constraint")
    search_p.add_argument("query", help="Search query (case-insensitive substring)")

    # mosdat routines  (R1/R3/R4: parameterized reusable procedure library + test harness)
    routines_p = sub.add_parser("routines", help="R1: Browse and inspect reusable routine library")
    routines_sub = routines_p.add_subparsers(dest="routines_command", required=True)
    routines_sub.add_parser("list", help="List all routines (name + description + tags)")
    show_r = routines_sub.add_parser("show", help="Show full routine definition as YAML")
    show_r.add_argument("name", help="Routine name (e.g. launch-rocketchat)")
    test_r = routines_sub.add_parser("test", help="R3: Run a routine against a VM with optional fixture")
    test_r.add_argument("name", help="Routine name to test")
    test_r.add_argument("--vms", required=True, metavar="VM",
                        help="VM name to run against (e.g. ubuntu2204)")
    test_r.add_argument("--fixture", default=None, metavar="NAME",
                        help="Fixture slug to apply before running the routine (e.g. rc-killed-userdata-wiped)")
    test_r.add_argument("--with", dest="with_inputs", action="append", default=[],
                        metavar="KEY=VAL",
                        help="Override a routine input (repeatable, e.g. --with url=https://...)")
    test_r.add_argument("--model", default=None, metavar="MODEL",
                        help="Override VLM model for localization")
    test_r.add_argument("--verify-model", dest="verify_model", default=None, metavar="MODEL",
                        help="Override VLM model for verification")
    test_r.add_argument("--save-screenshots", action="store_true", dest="save_screenshots",
                        help="Save step screenshots to results dir")
    test_r.add_argument("--config", default=None, metavar="PATH",
                        help="mosdat config TOML for VM/VLM settings")
    routines_sub.add_parser("fixtures", help="R3: List available fixtures (slug + description + vm_state)")
    explain_r = routines_sub.add_parser(
        "explain",
        help="R4: Show which fallback fires given a capability manifest and inputs (dry run)",
    )
    explain_r.add_argument("name", help="Routine name (e.g. open-settings)")
    explain_r.add_argument(
        "--manifest", metavar="PATH",
        help="Path to capability manifest JSON (default: auto-load latest from shared/binary_capabilities/)",
    )
    explain_r.add_argument(
        "--with", dest="with_inputs", action="append", default=[], metavar="KEY=VAL",
        help="Override or set a routine input (repeatable). Format: KEY=VAL.",
    )
    report_r = routines_sub.add_parser(
        "report",
        help="R6: Coverage report — scenarios, test history, unused/untested routines",
    )
    report_r.add_argument(
        "--format", choices=["md", "json", "tty"], default="md",
        help="Output format: md (default), json, or tty (colorized)",
    )
    routines_sub.add_parser(
        "version",
        help="R7: Print current schema version and supported versions list",
    )

    return parser
