"""cmd_report — extracted from automation/main.py."""
from pathlib import Path


def cmd_report(args) -> int:
    # Handle --flakes flag (B5)
    if getattr(args, "flakes", False):
        from automation.reporting.aggregate import flake_leaderboard

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
        from automation.config import load_config
        from automation.state import StateManager
        config = load_config(args.config)
        config.results_dir = args.results_dir
    else:
        print("[mOSdat] ERROR: --config is required for report regeneration")
        return 1

    from automation.state import StateManager
    state_manager = StateManager(state_file, config.app.version)
    with state_manager:
        from automation.reporting.report import generate_report
        generate_report(state_manager.state, config)
    return 0
