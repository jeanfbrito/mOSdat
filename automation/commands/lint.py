"""``mosdat lint <scenario>`` — static YAML scenario analyzer.

Reads a scenario YAML file and prints WARN/INFO messages for known anti-patterns
and footguns. Exits 0 if no WARN, 1 if any WARN.

Checks:
  1. key/then_key combo validation (main key must be in _KEYSYMS, _MODIFIERS, or single char)
  2. Hardcoded xdotool coordinates (coord drift risk)
  3. VLM-fragile transient localize targets (kebab, dropdown, menu, popup)
  4. Settings-nav-to-toggle pattern without subsequent UI verification before kill+relaunch
  5. Heredoc inside YAML literal scalar (breaks YAML portability)
  6. tel:/callto: dispatch without XAUTHORITY env set nearby
  7. config.json write missing __internal__.migrations.version
  8. schema validation via the SAME loader path the real functional runner
     uses (``load_test_yaml`` -> ``ScenarioModel.model_validate``) — a
     scenario that fails this is one the runner will reject at load time
     (exit code 4), so lint must not bless it as clean.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
from pathlib import Path
from typing import Optional

# Import the keysym tables from VNC transport for authoritative validation.
# Imported lazily to avoid pulling in Crypto/websockets at import time.
_KEYSYMS: Optional[dict] = None
_MODIFIERS: Optional[dict] = None


def _load_keysyms() -> tuple[dict, dict]:
    global _KEYSYMS, _MODIFIERS
    if _KEYSYMS is None:
        try:
            from automation.transport.vnc import _KEYSYMS as K, _MODIFIERS as M
            _KEYSYMS = K
            _MODIFIERS = M
        except ImportError:
            # Minimal fallback for unit tests that don't have the full dep tree.
            _KEYSYMS = {
                "enter": 0xff0d, "return": 0xff0d, "tab": 0xff09,
                "escape": 0xff1b, "esc": 0xff1b, "backspace": 0xff08,
                "delete": 0xffff, "home": 0xff50, "end": 0xff57,
                "pgup": 0xff55, "pgdn": 0xff56,
                "up": 0xff52, "down": 0xff54, "left": 0xff51, "right": 0xff53,
                "insert": 0xff63, "space": 0x0020,
                "f1": 0xffbe, "f2": 0xffbf, "f3": 0xffc0, "f4": 0xffc1,
                "f5": 0xffc2, "f6": 0xffc3, "f7": 0xffc4, "f8": 0xffc5,
                "f9": 0xffc6, "f10": 0xffc7, "f11": 0xffc8, "f12": 0xffc9,
            }
            _MODIFIERS = {
                "shift": 0xffe1,
                "ctrl": 0xffe3, "control": 0xffe3,
                "alt": 0xffe9,
                "meta": 0xffe7,
                "super": 0xffeb, "win": 0xffeb,
            }
    return _KEYSYMS, _MODIFIERS


# ---------------------------------------------------------------------------
# Diagnostic result
# ---------------------------------------------------------------------------

class LintDiag:
    """A single lint finding."""
    __slots__ = ("severity", "file", "line", "rule", "message")

    def __init__(self, severity: str, file: str, line: int, rule: str, message: str):
        self.severity = severity  # "WARN" or "INFO"
        self.file = file
        self.line = line
        self.rule = rule
        self.message = message

    def __str__(self) -> str:
        return f"{self.severity} {self.file}:{self.line} [{self.rule}] {self.message}"


# ---------------------------------------------------------------------------
# Check 1 — key combo validation
# ---------------------------------------------------------------------------

_KEY_FIELDS = ("key", "then_key", "then_key_pre", "key_pre")

_COMMA_NAMES = {"comma", ","}  # accepted aliases for the comma key


def _is_valid_key_combo(combo: str) -> bool:
    """Return True if the key combo is valid per VNC keysym tables."""
    keysyms, modifiers = _load_keysyms()
    parts = [p.strip() for p in combo.split("+")]
    if not parts:
        return False
    main = parts[-1].lower()
    mods = [p.lower() for p in parts[:-1]]

    # All modifiers must be in _MODIFIERS
    for m in mods:
        if m not in modifiers:
            return False

    # Main key: in _KEYSYMS, in _MODIFIERS, single char, or named comma aliases
    if main in keysyms:
        return True
    if main in modifiers:
        return True
    if main in _COMMA_NAMES:
        return True
    if len(main) == 1:
        return True
    return False


def check_key_combos(lines: list[str], filename: str) -> list[LintDiag]:
    """Check all key: / then_key: / then_key_pre: combos in raw YAML lines."""
    diags: list[LintDiag] = []
    key_re = re.compile(
        r'^\s*(?:key|then_key|then_key_pre|key_pre)\s*:\s*["\']?([^"\'#\n]+?)["\']?\s*(?:#.*)?$'
    )
    for i, line in enumerate(lines, start=1):
        m = key_re.match(line)
        if not m:
            continue
        combo = m.group(1).strip()
        if not combo:
            continue
        if not _is_valid_key_combo(combo):
            diags.append(LintDiag(
                "WARN", filename, i, "key-combo",
                f"unrecognised key combo {combo!r} — "
                "split on '+', check main key is in _KEYSYMS, _MODIFIERS, or single char"
            ))
    return diags


# ---------------------------------------------------------------------------
# Check 2 — hardcoded xdotool coordinates
# ---------------------------------------------------------------------------

_XDOTOOL_COORD_RE = re.compile(r"xdotool\s+mousemove\s+\d+\s+\d+")


def check_xdotool_coords(lines: list[str], filename: str) -> list[LintDiag]:
    diags: list[LintDiag] = []
    for i, line in enumerate(lines, start=1):
        if _XDOTOOL_COORD_RE.search(line):
            diags.append(LintDiag(
                "WARN", filename, i, "coord-drift",
                "hardcoded xdotool mousemove coords — coord drift risk; "
                "use VNC native or VLM localize"
            ))
    return diags


# ---------------------------------------------------------------------------
# Check 3 — VLM-fragile transient localize targets
# ---------------------------------------------------------------------------

_TRANSIENT_RE = re.compile(
    r"localize\s*:\s*.*(kebab|dropdown|menu|popup)",
    re.IGNORECASE,
)


def check_transient_localize(lines: list[str], filename: str) -> list[LintDiag]:
    diags: list[LintDiag] = []
    for i, line in enumerate(lines, start=1):
        if _TRANSIENT_RE.search(line):
            diags.append(LintDiag(
                "WARN", filename, i, "transient-localize",
                "VLM hallucinates transient popups; consider pre-stage or stable target"
            ))
    return diags


# ---------------------------------------------------------------------------
# Check 4 — Settings-nav-to-toggle without post-toggle verify before kill+relaunch
# ---------------------------------------------------------------------------

_SETTINGS_NAV_RE = re.compile(r"localize\s*:.*Settings", re.IGNORECASE)
_GENERAL_NAV_RE = re.compile(r"localize\s*:.*General", re.IGNORECASE)
_TOGGLE_LOCALIZE_RE = re.compile(r"localize\s*:\s*", re.IGNORECASE)
_VERIFY_RE = re.compile(r"^\s*verify\s*:")
_KILL_RELAUNCH_RE = re.compile(r"pkill|kill.*Rocket|nohup.*/opt/", re.IGNORECASE)
_CLICK_RE = re.compile(r"^\s*click\s*:")


def check_settings_toggle_pattern(lines: list[str], filename: str) -> list[LintDiag]:
    """Detect Settings nav → click → General → click → toggle localize → click
    with NO subsequent verify before a kill+relaunch."""
    diags: list[LintDiag] = []
    n = len(lines)

    # Simple state machine scanning over lines
    state = 0  # 0=idle 1=saw Settings nav 2=saw General 3=saw toggle localize 4=saw click
    toggle_line = 0

    for i, line in enumerate(lines, start=1):
        if state == 0:
            if _SETTINGS_NAV_RE.search(line):
                state = 1
        elif state == 1:
            if _GENERAL_NAV_RE.search(line):
                state = 2
            elif _SETTINGS_NAV_RE.search(line):
                pass  # stay in state 1
        elif state == 2:
            if _TOGGLE_LOCALIZE_RE.search(line):
                state = 3
                toggle_line = i
        elif state == 3:
            if _CLICK_RE.search(line):
                state = 4
        elif state == 4:
            if _VERIFY_RE.search(line):
                # Verify found — pattern OK, reset
                state = 0
            elif _KILL_RELAUNCH_RE.search(line):
                diags.append(LintDiag(
                    "WARN", filename, toggle_line, "settings-toggle-no-verify",
                    "if state is persisted, prefer --inject-config pre-stage; "
                    "no UI verify of toggle mutation found before kill+relaunch"
                ))
                state = 0

    return diags


# ---------------------------------------------------------------------------
# Check 5 — heredoc inside YAML literal scalar
# ---------------------------------------------------------------------------

_HEREDOC_RE = re.compile(r"cat\s+>\s+\S+\s+<<'?\w+'?")


def check_heredoc_in_yaml(lines: list[str], filename: str) -> list[LintDiag]:
    diags: list[LintDiag] = []
    for i, line in enumerate(lines, start=1):
        if _HEREDOC_RE.search(line):
            diags.append(LintDiag(
                "WARN", filename, i, "heredoc-in-yaml",
                "heredoc breaks YAML; use printf '%s\\n' pattern"
            ))
    return diags


# ---------------------------------------------------------------------------
# Check 6 — tel:/callto: dispatch without XAUTHORITY
# ---------------------------------------------------------------------------

_TEL_DISPATCH_RE = re.compile(r'nohup\s.*["\']tel:', re.IGNORECASE)
_XAUTHORITY_RE = re.compile(r"XAUTHORITY=")


def check_tel_dispatch_xauthority(lines: list[str], filename: str) -> list[LintDiag]:
    """Warn if a tel: nohup dispatch line isn't preceded by XAUTHORITY= within 5 lines."""
    diags: list[LintDiag] = []
    for i, line in enumerate(lines, start=1):
        if _TEL_DISPATCH_RE.search(line):
            window_start = max(0, i - 6)  # 5 lines before (0-indexed)
            preceding = lines[window_start : i - 1]
            if not any(_XAUTHORITY_RE.search(pl) for pl in preceding):
                diags.append(LintDiag(
                    "WARN", filename, i, "tel-dispatch-xauth",
                    "second-instance IPC silently fails without XAUTHORITY; "
                    "set XAUTHORITY= within 5 lines before tel: dispatch"
                ))
    return diags


# ---------------------------------------------------------------------------
# Check 7 — config.json write without migrations version
# ---------------------------------------------------------------------------

_CONFIG_JSON_WRITE_RE = re.compile(r"config\.json")
_MIGRATIONS_VERSION_RE = re.compile(r"__internal__.*migrations.*version|migrations.*version.*__internal__", re.IGNORECASE)


def check_config_json_migrations(lines: list[str], filename: str) -> list[LintDiag]:
    """Warn if a config.json write block doesn't include migrations.version.

    Scans a ±20-line window around each config.json write for the migrations key.
    """
    diags: list[LintDiag] = []
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        if _CONFIG_JSON_WRITE_RE.search(line) and (">" in line or "write" in line.lower() or "printf" in line.lower()):
            # Scan ±20 lines around the write line for migrations version
            window_start = max(0, i - 20)
            window_end = min(n, i + 20)
            block = lines[window_start:window_end]
            if not any(_MIGRATIONS_VERSION_RE.search(bl) for bl in block):
                diags.append(LintDiag(
                    "WARN", filename, i + 1, "config-json-no-migrations",
                    "config.json write without __internal__.migrations.version — "
                    "RC re-runs migrations and resets state"
                ))
        i += 1
    return diags


# ---------------------------------------------------------------------------
# Check 8 — schema validation via the real runner's loader path
# ---------------------------------------------------------------------------

def check_schema_validation(
    scenario_path: Path, filename: str, already_explained: bool = False
) -> list[LintDiag]:
    """Validate the scenario the SAME way the real functional runner does.

    ``load_test_yaml`` expands imports/routines, renders vars, then calls
    ``ScenarioModel.model_validate`` and — on failure — prints to stderr and
    calls ``sys.exit(4)``. We call the real loader (unmodified) and turn that
    exit into a WARN diag so lint no longer blesses scenarios the runner
    would reject outright.

    ``already_explained``: when ``check_step_type_discriminator`` already
    produced a clear one-line explanation for a no-type-match step, we skip
    dumping the raw (often ~20-line, mostly-redundant) pydantic union-mismatch
    cascade here and just report that schema validation failed.
    """
    diags: list[LintDiag] = []
    try:
        from automation.runners.scenario_loader import load_test_yaml
    except ImportError as exc:
        diags.append(LintDiag(
            "INFO", filename, 0, "schema-validation-unavailable",
            f"could not import scenario_loader for schema validation: {exc}",
        ))
        return diags

    stderr_buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr_buf):
            load_test_yaml(scenario_path)
    except SystemExit as exc:
        if exc.code not in (0, None):
            message = (
                f"scenario fails real-runner schema validation "
                f"(load_test_yaml exit code {exc.code})"
            )
            if already_explained:
                message += " — see step-no-type-match finding(s) above for details"
            else:
                detail = stderr_buf.getvalue().strip()
                if detail:
                    message += f":\n{detail}"
            diags.append(LintDiag("WARN", filename, 0, "schema-validation", message))
    except Exception as exc:  # noqa: BLE001 - any loader failure is a real problem
        diags.append(LintDiag(
            "WARN", filename, 0, "schema-validation",
            f"scenario fails real-runner validation: {exc}",
        ))
    return diags


# ---------------------------------------------------------------------------
# Check 9 — step dict matches no recognized step-type discriminator
# ---------------------------------------------------------------------------
#
# When a step dict has none of the fields below, it matches ZERO members of
# the AnyStep union and pydantic dumps a full cascade of "field required"
# errors for every union member (one real problem, ~20+ noisy lines). We
# detect that shape cheaply and surface ONE clear message instead.

_STEP_TYPE_FIELDS = {
    "checkpoint", "import", "shell", "launch", "if_visible", "popup_sweep",
    "atspi", "verify_atspi", "wait_for", "localize", "accept_any", "verify",
    "wait", "key", "then_key", "then_key_pre", "key_pre", "then_type", "type",
    "click_at", "poll_shell", "verify_recorded",
}

# Keys that are valid on a raw (pre-expansion) step dict but are resolved
# away by import/routine expansion before schema validation runs — not
# themselves step-type discriminators, but not errors either.
_PRE_EXPANSION_ONLY_FIELDS = {"routine", "with"}


def _walk_raw_steps(steps: list) -> list[tuple[int, dict]]:
    """Flatten a raw steps list (including nested ``then:`` blocks) into
    ``(index, step_dict)`` pairs, skipping non-dict entries."""
    out: list[tuple[int, dict]] = []
    for idx, raw in enumerate(steps):
        if not isinstance(raw, dict):
            continue
        out.append((idx, raw))
        nested = raw.get("then")
        if isinstance(nested, list):
            out.extend(_walk_raw_steps(nested))
    return out


def check_step_type_discriminator(raw_data: dict, filename: str) -> list[LintDiag]:
    diags: list[LintDiag] = []
    for key in ("steps", "cleanup"):
        steps = raw_data.get(key)
        if not isinstance(steps, list):
            continue
        for idx, raw in _walk_raw_steps(steps):
            if any(f in raw for f in _STEP_TYPE_FIELDS):
                continue
            if any(f in raw for f in _PRE_EXPANSION_ONLY_FIELDS):
                continue
            fields = sorted(raw.keys())
            diags.append(LintDiag(
                "WARN", filename, 0, "step-no-type-match",
                f"{key}[{idx}]: no recognized step type — got fields {fields}, "
                "expected one of: " + ", ".join(sorted(_STEP_TYPE_FIELDS))
            ))
    return diags


# ---------------------------------------------------------------------------
# Optional: consult capability manifest if scenario has requires_capabilities:
# ---------------------------------------------------------------------------

def _check_capability_manifest(raw_data: dict, filename: str) -> list[LintDiag]:
    """If scenario declares requires_capabilities: try to load manifest and warn on gaps."""
    diags: list[LintDiag] = []
    requires = raw_data.get("requires_capabilities")
    if not requires:
        return diags

    try:
        from automation.setup.capability import load_manifest
    except ImportError:
        return diags

    asar_sha = requires.get("asar_sha")
    if not asar_sha:
        return diags

    manifest = load_manifest(asar_sha)
    if manifest is None:
        diags.append(LintDiag(
            "INFO", filename, 0, "capability-manifest-missing",
            f"no capability manifest for asar_sha={asar_sha!r}; "
            "run: mosdat trace <toml> --vms <vm> --write-manifest"
        ))
        return diags

    # Check each required accelerator
    required_accels = requires.get("accelerators", {})
    manifest_accels = manifest.get("accelerators", {})
    for key, expected in required_accels.items():
        actual = manifest_accels.get(key)
        if actual and actual != expected:
            diags.append(LintDiag(
                "WARN", filename, 0, "capability-mismatch",
                f"scenario expects {key}={expected!r} but manifest says {actual!r}"
            ))

    return diags


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_lint(args) -> int:
    """Entry point for ``mosdat lint``. Returns 0 (no WARN) or 1 (WARN found)."""
    scenario_arg = args.scenario
    scenario_path = Path(scenario_arg)

    if not scenario_path.exists():
        print(f"[lint] ERROR: scenario not found: {scenario_path}", file=sys.stderr)
        return 2

    filename = str(scenario_path)

    with open(scenario_path, encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()

    # Parse the raw YAML for checks that need structured data (capability
    # manifest, step-shape check). Schema validation (check 8) goes through
    # the real runner's loader below and is FATAL — a step-type mismatch or
    # any other ScenarioModel validation failure here means the runner would
    # reject this scenario outright (exit code 4); lint must not bless it.
    raw_data: dict = {}
    try:
        import yaml as _yaml
        raw_data = _yaml.safe_load(content) or {}
    except Exception as exc:
        print(f"INFO {filename}:0 [yaml-parse] YAML parse warning: {exc}")

    all_diags: list[LintDiag] = []
    all_diags += check_key_combos(lines, filename)
    all_diags += check_xdotool_coords(lines, filename)
    all_diags += check_transient_localize(lines, filename)
    all_diags += check_settings_toggle_pattern(lines, filename)
    all_diags += check_heredoc_in_yaml(lines, filename)
    all_diags += check_tel_dispatch_xauthority(lines, filename)
    all_diags += check_config_json_migrations(lines, filename)
    step_type_diags = check_step_type_discriminator(raw_data, filename)
    all_diags += step_type_diags
    all_diags += check_schema_validation(
        scenario_path, filename, already_explained=bool(step_type_diags)
    )
    all_diags += _check_capability_manifest(raw_data, filename)

    if not all_diags:
        print(f"[lint] {filename}: OK (no warnings)")
        return 0

    for d in sorted(all_diags, key=lambda x: x.line):
        print(d)

    warns = [d for d in all_diags if d.severity == "WARN"]
    print(f"\n[lint] {len(warns)} warning(s), {len(all_diags) - len(warns)} info(s)")
    return 1 if warns else 0
