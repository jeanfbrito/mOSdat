"""CLI subcommand dispatchers that build argv and call sub-CLI ``cli()`` entry points.

Extracted from ``automation.main`` to keep that file under 500 LOC. Each function
takes parsed ``args`` and returns an exit code.
"""

from __future__ import annotations


def cmd_live(args) -> int:
    from automation.live_dashboard import cli as live_cli
    argv = ["--port", str(args.port), "--results", str(args.results),
            "--refresh-ms", str(args.refresh_ms),
            "--warn-after", str(args.warn_after),
            "--stale-after", str(args.stale_after)]
    if args.config:
        argv += ["--config", str(args.config)]
    return live_cli(argv)


def cmd_author(args) -> int:
    from automation.author_cli import cli as author_cli
    argv = ["--url", args.url, args.author_command]
    if args.author_command == "doctor":
        pass
    elif args.author_command == "start":
        argv += ["--vm", args.vm]
        if args.model:
            argv += ["--model", args.model]
        if args.verify_model:
            argv += ["--verify-model", args.verify_model]
    elif args.author_command in {"session", "capture"}:
        argv += ["--session", args.session]
        if args.author_command == "capture" and args.output:
            argv += ["--output", args.output]
    elif args.author_command == "localize":
        argv += ["--session", args.session, "--prompt", args.prompt]
    elif args.author_command == "describe":
        argv += ["--session", args.session, "--x", str(args.x), "--y", str(args.y)]
    elif args.author_command == "verify":
        argv += ["--session", args.session, "--question", args.question]
    elif args.author_command == "action":
        argv += ["--session", args.session, "--kind", args.kind, "--json", args.json]
    elif args.author_command == "click":
        argv += ["--session", args.session, "--x", str(args.x), "--y", str(args.y), "--button", args.button]
        if args.prompt is not None:
            argv += ["--prompt", args.prompt]
    elif args.author_command == "prompt-click":
        argv += ["--session", args.session, "--prompt", args.prompt, "--button", args.button]
    elif args.author_command == "prompt-hover":
        argv += ["--session", args.session, "--prompt", args.prompt]
    elif args.author_command == "prompt-type":
        argv += ["--session", args.session, "--prompt", args.prompt, "--text", args.text, "--button", args.button]
    elif args.author_command == "hover":
        argv += ["--session", args.session, "--x", str(args.x), "--y", str(args.y)]
        if args.prompt is not None:
            argv += ["--prompt", args.prompt]
    elif args.author_command == "type":
        argv += ["--session", args.session, "--text", args.text]
    elif args.author_command == "key":
        argv += ["--session", args.session, "--key", args.key]
    elif args.author_command == "wait":
        argv += ["--session", args.session, "--seconds", str(args.seconds)]
    elif args.author_command == "shell":
        argv += ["--session", args.session, "--cmd", args.cmd]
    elif args.author_command == "launch":
        argv += ["--session", args.session, "--cmd", args.cmd, "--wait", str(args.wait)]
    elif args.author_command in {"validate", "export"}:
        argv += ["--session", args.session, "--name", args.name]
        if args.author_command == "export":
            if args.output:
                argv += ["--output", args.output]
            if getattr(args, "base", None):
                argv += ["--base", args.base]
            if getattr(args, "insert_at_step", None) is not None:
                argv += ["--insert-at-step", str(args.insert_at_step)]
            if getattr(args, "dry_run", False):
                argv += ["--dry-run"]
    elif args.author_command == "step":
        argv += ["--session", args.session]
        if args.step_json is not None:
            argv += ["--json", args.step_json]
        if args.steps_json is not None:
            argv += ["--steps-json", args.steps_json]
    elif args.author_command == "close":
        argv += ["--session", args.session]
    return author_cli(argv)


def cmd_visual(args) -> int:
    from automation.visual import cli as visual_cli
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
    from automation.reporting.dashboard import cli as dashboard_cli
    argv = ["--root", str(args.root)]
    if args.output:
        argv += ["--output", str(args.output)]
    return dashboard_cli(argv)


def cmd_draft(args) -> int:
    from automation.draft import cli as draft_cli
    argv = []
    if args.templates:
        argv += ["--templates"]
    elif args.change_type:
        argv += ["--change-type", args.change_type]
        if args.pr:
            argv += ["--pr", args.pr]
        if args.description:
            argv += ["--description", args.description]
        if args.output:
            argv += ["--output", str(args.output)]
    return draft_cli(argv)


def cmd_confirm(args) -> int:
    from automation.commands.confirm_cmd import cmd_confirm as _cmd
    return _cmd(args)


def cmd_report(args) -> int:
    from automation.commands.report_cmd import cmd_report as _cmd
    return _cmd(args)


def cmd_vlm_cache(args) -> int:
    """I6: vlm-cache stats / clear / prune subcommand handler."""
    from automation.transport.vlm_cache import VLMCache

    cache = VLMCache()

    if args.cache_command == "stats":
        s = cache.stats()
        total = s["hits"] + s["misses"]
        hit_pct = f"{s['hit_rate']*100:.1f}%" if total > 0 else "n/a (no lookups this session)"
        size_kb = s["size_bytes"] / 1024
        print(f"VLM cache stats")
        print(f"  db:       {s['db_path']}")
        print(f"  entries:  {s['entries']}")
        print(f"  size:     {size_kb:.1f} KB")
        print(f"  ttl:      {s['ttl']}s ({s['ttl']//3600}h)")
        print(f"  hits:     {s['hits']}")
        print(f"  misses:   {s['misses']}")
        print(f"  hit rate: {hit_pct}")
        if s["oldest_ts"]:
            import time as _time
            age_h = (_time.time() - s["oldest_ts"]) / 3600
            print(f"  oldest:   {age_h:.1f}h ago")
        return 0

    if args.cache_command == "clear":
        deleted = cache.clear()
        print(f"VLM cache cleared ({deleted} entries removed)")
        return 0

    if args.cache_command == "prune":
        duration_str = args.older_than.strip().lower()
        if duration_str.endswith("d"):
            seconds = int(duration_str[:-1]) * 86400
        elif duration_str.endswith("h"):
            seconds = int(duration_str[:-1]) * 3600
        elif duration_str.endswith("s"):
            seconds = int(duration_str[:-1])
        else:
            print(f"ERROR: unrecognised duration '{args.older_than}'. Use 7d, 48h, or 3600s.")
            return 1
        deleted = cache.prune(older_than=seconds)
        print(f"VLM cache pruned ({deleted} entries older than {args.older_than} removed)")
        return 0

    print(f"Unknown cache command: {args.cache_command}")
    return 1
