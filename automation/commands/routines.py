"""mosdat routines — browse, inspect, and test the routine library (R1/R3/R4/R7).

Subcommands:
    mosdat routines list                              # name + description + tags
    mosdat routines show <name>                       # full YAML
    mosdat routines test <name>                       # run against a VM (R3)
    mosdat routines fixtures                          # list available fixtures (R3)
    mosdat routines explain <name> [--manifest PATH]  # R4: which fallback fires
      [--with KEY=VAL ...]
    mosdat routines version                           # R7: current + supported schema versions
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

from automation.routines import CURRENT_SCHEMA_VERSION, SUPPORTED_SCHEMA_VERSIONS
from automation.routines.loader import list_routines, load_routine, routines_dir
from automation.routines.fixtures import list_fixtures


def _cmd_list(args) -> int:  # noqa: ARG001
    routines = list_routines()
    if not routines:
        print("[routines] No routines found.")
        return 0
    col = max(len(r.name) for r in routines) + 2
    for r in routines:
        tags = f"  [{', '.join(r.tags)}]" if r.tags else ""
        print(f"{r.name:<{col}} {r.description}{tags}")
    return 0


def _cmd_show(args) -> int:
    slug = args.name
    try:
        routine = load_routine(slug)
    except FileNotFoundError:
        print(f"[routines] No routine named {slug!r}.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[routines] Error loading routine {slug!r}: {exc}", file=sys.stderr)
        return 1

    # Dump as YAML (round-trip via model dict for clarity)
    data = routine.model_dump(exclude_defaults=False)
    print(yaml.dump(data, default_flow_style=False, sort_keys=False), end="")
    return 0


def _cmd_test(args) -> int:
    from automation.routines.harness import run_routine_test

    # Parse --with KEY=VAL pairs
    with_inputs: dict = {}
    for pair in getattr(args, "with_inputs", None) or []:
        if "=" not in pair:
            print(f"[routines] --with must be KEY=VAL, got: {pair!r}", file=sys.stderr)
            return 2
        k, v = pair.split("=", 1)
        with_inputs[k.strip()] = v.strip()

    return run_routine_test(
        routine_name=args.name,
        vm_name=args.vms,
        fixture_name=getattr(args, "fixture", None),
        model=getattr(args, "model", None),
        verify_model=getattr(args, "verify_model", None),
        with_inputs=with_inputs or None,
        config_path=getattr(args, "config", None),
    )


def _cmd_fixtures(args) -> int:  # noqa: ARG001
    fixtures = list_fixtures()
    if not fixtures:
        print("[routines] No fixtures found.")
        return 0
    col = max(len(f.name) for f in fixtures) + 2
    for fx in fixtures:
        vm_keys = ", ".join(
            f"{k}={v!r}" for k, v in (fx.vm_state or {}).items()
            if k != "config"
        )
        has_config = "config" in (fx.vm_state or {})
        summary = vm_keys
        if has_config:
            summary = (summary + ", config=<dict>").lstrip(", ")
        print(f"{fx.name:<{col}} {fx.description}  [{summary}]")
    return 0


def _cmd_explain(args) -> int:
    """R4: show which fallback (if any) would fire given a manifest + inputs."""
    slug = args.name

    # Load routine
    try:
        routine = load_routine(slug)
    except FileNotFoundError:
        print(f"[routines] No routine named {slug!r}.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[routines] Error loading routine {slug!r}: {exc}", file=sys.stderr)
        return 1

    # Load manifest
    manifest: dict | None = None
    if getattr(args, "manifest", None):
        manifest_path = Path(args.manifest)
        if not manifest_path.exists():
            print(f"[routines] Manifest file not found: {manifest_path}", file=sys.stderr)
            return 1
        try:
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as exc:
            print(f"[routines] Error loading manifest: {exc}", file=sys.stderr)
            return 1
    else:
        # Try auto-load
        from automation.routines.runner import _load_default_manifest
        manifest = _load_default_manifest()

    # Parse --with KEY=VAL overrides
    inputs: dict = {}
    for kv in getattr(args, "with_inputs", []) or []:
        if "=" not in kv:
            print(f"[routines] --with value must be KEY=VAL (got: {kv!r})", file=sys.stderr)
            return 1
        k, v = kv.split("=", 1)
        inputs[k] = v

    # Resolve inputs against routine schema
    from automation.routines.runner import _resolve_inputs, _select_steps
    try:
        resolved_inputs = _resolve_inputs(routine, inputs, {})
    except ValueError as exc:
        print(f"[routines] Input error: {exc}", file=sys.stderr)
        return 1

    # Select steps
    main_steps, fallback_used = _select_steps(routine, manifest, resolved_inputs, {})

    # --- Output ---
    print(f"Routine:  {routine.name}")
    if manifest:
        sha = manifest.get("asar_sha", "<unknown>")
        print(f"Manifest: asar_sha={sha}")
    else:
        print("Manifest: none (fallbacks skipped; using main steps)")

    if fallback_used:
        print(f"Fallback: ACTIVE  when={fallback_used!r}")
    else:
        print("Fallback: none (using main steps)")

    print(f"\nExpanded steps ({len(main_steps)}):")
    for i, step in enumerate(main_steps, 1):
        # Show one-line summary per step
        key = next(
            (k for k in ("shell", "verify", "verify_not", "localize", "wait", "launch", "routine")
             if k in step),
            None,
        )
        if key:
            val = step[key]
            val_str = str(val)[:80] + ("..." if len(str(val)) > 80 else "")
            print(f"  {i:2d}. {key}: {val_str}")
        else:
            keys_str = ", ".join(sorted(step.keys()))
            print(f"  {i:2d}. {{{keys_str}}}")

    return 0


def _cmd_version(args) -> int:  # noqa: ARG001
    """R7: print current schema version and the full supported-versions list."""
    print(f"current:   {CURRENT_SCHEMA_VERSION}")
    print(f"supported: {', '.join(SUPPORTED_SCHEMA_VERSIONS)}")
    return 0


def _cmd_report(args) -> int:
    from automation.routines.coverage import build_report, render_markdown, render_json, render_tty
    fmt = getattr(args, "format", "md") or "md"
    rows = build_report()
    if fmt == "json":
        print(render_json(rows), end="")
    elif fmt == "tty":
        print(render_tty(rows), end="")
    else:
        print(render_markdown(rows), end="")
    return 0


def run_routines(args) -> int:
    sub = getattr(args, "routines_command", None)
    if sub == "list":
        return _cmd_list(args)
    if sub == "show":
        return _cmd_show(args)
    if sub == "test":
        return _cmd_test(args)
    if sub == "fixtures":
        return _cmd_fixtures(args)
    if sub == "explain":
        return _cmd_explain(args)
    if sub == "report":
        return _cmd_report(args)
    if sub == "version":
        return _cmd_version(args)
    print(
        "[routines] No subcommand given. Use: list | show <name> | test <name> | fixtures | explain <name> | report | version",
        file=sys.stderr,
    )
    return 1
