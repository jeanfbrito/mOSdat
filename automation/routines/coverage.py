"""R6: routine coverage analysis — scenario references, test history, and report.

find_scenario_references() — static YAML scan for `- routine:` calls
find_last_test_results()   — parse results/routines-test-history.jsonl
build_report()             — combine loader + references + results into CoverageRow list
"""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from automation.routines.loader import list_routines, load_routine, routines_dir


# ---------------------------------------------------------------------------
# Project root
# ---------------------------------------------------------------------------

def _project_root() -> Path:
    """Return project root (parent of automation/)."""
    return Path(__file__).parent.parent.parent


def _test_history_path() -> Path:
    return _project_root() / "results" / "routines-test-history.jsonl"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ScenarioRef:
    path: str           # relative path from project root
    scenario_name: str  # value of `name:` field in the YAML
    line_approx: int    # 1-based line where `routine:` key was found (best-effort)


@dataclass
class TestResult:
    routine: str
    vm: str
    fixture: str
    exit_code: int
    duration_ms: int
    timestamp: str


@dataclass
class CoverageRow:
    name: str
    definition_path: str       # relative path to shared/routines/<name>.yaml
    tags: list[str]
    scenarios_using: list[ScenarioRef]
    last_test_result: Optional[TestResult]
    status: str  # 'unused' | 'untested' | 'failing' | 'ok'


# ---------------------------------------------------------------------------
# Scenario scanning
# ---------------------------------------------------------------------------

def _scan_steps_for_routines(
    steps: object,
    scenario_name: str,
    rel_path: str,
    raw_lines: list[str],
    result: dict[str, list[ScenarioRef]],
    _visited: set[str],
) -> None:
    """Recursively walk step lists, extracting `routine:` references."""
    if not isinstance(steps, list):
        return
    for step in steps:
        if not isinstance(step, dict):
            continue

        # Short form: `- routine: name-here`
        # Long form:  `- routine: {name: ..., with: {...}}`
        routine_val = step.get("routine")
        if routine_val is not None:
            if isinstance(routine_val, str):
                routine_name = routine_val.strip()
            elif isinstance(routine_val, dict):
                routine_name = (routine_val.get("name") or "").strip()
            else:
                routine_name = ""
            if routine_name:
                # Best-effort line search
                line_approx = 1
                for i, ln in enumerate(raw_lines, 1):
                    stripped = ln.strip()
                    if stripped.startswith("routine:") or stripped.startswith("- routine:"):
                        if routine_name in ln:
                            line_approx = i
                            break
                result.setdefault(routine_name, []).append(
                    ScenarioRef(
                        path=rel_path,
                        scenario_name=scenario_name,
                        line_approx=line_approx,
                    )
                )

                # Recurse into nested routine body (avoid infinite loops)
                if routine_name not in _visited:
                    _visited.add(routine_name)
                    try:
                        nested = load_routine(routine_name)
                        all_nested: list = (
                            list(nested.preconditions)
                            + list(nested.steps)
                            + list(nested.postconditions)
                        )
                        for fb in nested.fallbacks:
                            all_nested.extend(fb.steps)
                        _scan_steps_for_routines(
                            all_nested, scenario_name, rel_path, raw_lines, result, _visited
                        )
                    except Exception:
                        pass

        # Recurse into any list-valued sub-keys (e.g. nested steps in custom keys)
        for v in step.values():
            if isinstance(v, list):
                _scan_steps_for_routines(
                    v, scenario_name, rel_path, raw_lines, result, _visited
                )


def find_scenario_references(
    scenarios_root: Optional[Path] = None,
) -> dict[str, list[ScenarioRef]]:
    """Scan all functional + issues scenario YAML files for `- routine:` calls.

    Returns mapping routine_name -> [ScenarioRef, ...].
    Pure static analysis — no steps are executed.
    """
    if scenarios_root is None:
        scenarios_root = _project_root() / "shared" / "scenarios"

    result: dict[str, list[ScenarioRef]] = {}

    globs = [
        scenarios_root / "functional",
        scenarios_root / "issues",
    ]

    for base in globs:
        if not base.is_dir():
            continue
        for yaml_path in sorted(base.glob("*.yaml")):
            try:
                raw_text = yaml_path.read_text(encoding="utf-8")
                data = yaml.safe_load(raw_text)
            except Exception as exc:
                warnings.warn(
                    f"[coverage] Skipping unreadable scenario {yaml_path.name}: {exc}",
                    stacklevel=2,
                )
                continue
            if not isinstance(data, dict):
                continue
            scenario_name = data.get("name") or yaml_path.stem
            raw_lines = raw_text.splitlines()
            rel_path = str(yaml_path.relative_to(_project_root()))
            steps = data.get("steps", [])
            _visited: set[str] = set()
            _scan_steps_for_routines(
                steps, scenario_name, rel_path, raw_lines, result, _visited
            )

    return result


# ---------------------------------------------------------------------------
# Test history
# ---------------------------------------------------------------------------

def find_last_test_results(
    history_path: Optional[Path] = None,
) -> dict[str, TestResult]:
    """Parse results/routines-test-history.jsonl; return most-recent per routine.

    Returns empty dict if file does not exist or is empty.
    """
    if history_path is None:
        history_path = _test_history_path()

    if not history_path.exists():
        return {}

    # Keep only most-recent entry per routine (last wins, file is append-only)
    latest: dict[str, TestResult] = {}
    try:
        for raw_line in history_path.read_text(encoding="utf-8").splitlines():
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                obj = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            routine = obj.get("routine", "")
            if not routine:
                continue
            latest[routine] = TestResult(
                routine=routine,
                vm=obj.get("vm", ""),
                fixture=obj.get("fixture", "") or "",
                exit_code=int(obj.get("exit_code", -1)),
                duration_ms=int(obj.get("duration_ms", 0)),
                timestamp=obj.get("timestamp", ""),
            )
    except Exception as exc:
        warnings.warn(f"[coverage] Error reading history file: {exc}", stacklevel=2)
        return {}
    return latest


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

def build_report(
    scenarios_root: Optional[Path] = None,
    history_path: Optional[Path] = None,
) -> list[CoverageRow]:
    """Build a coverage row for every known routine."""
    refs = find_scenario_references(scenarios_root=scenarios_root)
    results = find_last_test_results(history_path=history_path)

    rdir = routines_dir()
    rows: list[CoverageRow] = []

    for routine in list_routines():
        name = routine.name
        def_path_abs = rdir / f"{name}.yaml"
        try:
            def_path = str(def_path_abs.relative_to(_project_root()))
        except ValueError:
            def_path = str(def_path_abs)

        scenarios_using = refs.get(name, [])
        last_result = results.get(name)

        if not scenarios_using:
            status = "unused"
        elif last_result is None:
            status = "untested"
        elif last_result.exit_code != 0:
            status = "failing"
        else:
            status = "ok"

        rows.append(CoverageRow(
            name=name,
            definition_path=def_path,
            tags=list(routine.tags),
            scenarios_using=scenarios_using,
            last_test_result=last_result,
            status=status,
        ))

    return rows


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _format_scenarios(refs: list[ScenarioRef], max_inline: int = 2) -> str:
    if not refs:
        return "(none)"
    if len(refs) <= max_inline:
        return ", ".join(r.scenario_name[:40] for r in refs)
    return f"(used by {len(refs)} scenarios)"


def _format_last_test(result: Optional[TestResult]) -> str:
    if result is None:
        return "never tested"
    icon = "✓" if result.exit_code == 0 else "✗"
    ts = result.timestamp[:19] if result.timestamp else "?"
    vm_fx = result.vm
    if result.fixture:
        vm_fx = f"{result.vm}/{result.fixture}"
    return f"{vm_fx} {ts} {icon}"


def render_markdown(rows: list[CoverageRow]) -> str:
    """Render coverage rows as a GitHub-flavored markdown table."""
    header = "| Routine | Tags | Scenarios | Last test | Status |"
    sep    = "|---|---|---|---|---|"
    lines = [header, sep]
    for row in rows:
        tags_str = ", ".join(row.tags) if row.tags else "-"
        scen_str = _format_scenarios(row.scenarios_using)
        test_str = _format_last_test(row.last_test_result)
        lines.append(
            f"| {row.name} | {tags_str} | {scen_str} | {test_str} | {row.status} |"
        )

    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1

    total = len(rows)
    summary_parts = [f"Total: {total}"]
    for s in ("ok", "untested", "unused", "failing"):
        summary_parts.append(f"{s}: {counts.get(s, 0)}")
    lines.append("")
    lines.append("**" + ", ".join(summary_parts) + "**")
    return "\n".join(lines) + "\n"


def render_json(rows: list[CoverageRow]) -> str:
    """Render coverage rows as JSON array."""
    out = []
    for row in rows:
        last = None
        if row.last_test_result:
            r = row.last_test_result
            last = {
                "vm": r.vm,
                "fixture": r.fixture,
                "exit_code": r.exit_code,
                "duration_ms": r.duration_ms,
                "timestamp": r.timestamp,
            }
        out.append({
            "name": row.name,
            "definition_path": row.definition_path,
            "tags": row.tags,
            "scenarios_using": [
                {"path": s.path, "scenario_name": s.scenario_name, "line_approx": s.line_approx}
                for s in row.scenarios_using
            ],
            "last_test_result": last,
            "status": row.status,
        })
    return json.dumps(out, indent=2)


_STATUS_COLORS = {
    "ok":       "\033[32m",   # green
    "untested": "\033[33m",   # yellow
    "unused":   "\033[90m",   # dark grey
    "failing":  "\033[31m",   # red
}
_RESET = "\033[0m"


def render_tty(rows: list[CoverageRow]) -> str:
    """Render human-readable colorized output."""
    lines = []
    col = max((len(r.name) for r in rows), default=10) + 2
    for row in rows:
        color = _STATUS_COLORS.get(row.status, "")
        tags_str = f"[{', '.join(row.tags)}]" if row.tags else ""
        scen_str = _format_scenarios(row.scenarios_using)
        test_str = _format_last_test(row.last_test_result)
        status_str = f"{color}{row.status}{_RESET}"
        lines.append(
            f"{row.name:<{col}} {status_str:<20}  {tags_str:<25}  "
            f"scenarios: {scen_str}  |  last: {test_str}"
        )

    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1

    total = len(rows)
    summary_parts = [f"Total: {total}"]
    for s in ("ok", "untested", "unused", "failing"):
        c = _STATUS_COLORS.get(s, "")
        summary_parts.append(f"{c}{s}: {counts.get(s, 0)}{_RESET}")
    lines.append("")
    lines.append("  ".join(summary_parts))
    return "\n".join(lines) + "\n"
