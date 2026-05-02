# Issue-Confirmation Tool — `mosdat confirm`

## Overview

`mosdat confirm <id>` automates the workflow of visually confirming a bug on a VM, capturing evidence, and generating a shareable report. Given a GitHub issue ID and a YAML scenario that describes repro steps, the tool runs the scenario N times, fires VLM checks to detect the bug, and emits a Markdown report with screenshots, environment deltas, and verdict. Use this to: confirm a reported bug, verify a fix holds, or track regression in CI.

For the full architecture and motivation, see [Plan — `mosdat confirm` issue-bug confirmation harness](.claude/mytasks/plan-issue-confirm-tool.md).

## Three Modes

| Mode | Success criterion | Exit code | Use case |
|------|------------------|-----------|----------|
| `confirm` (default) | Bug IS visible | 0 = CONFIRMED | "Does this bug actually happen?" — run once per reported issue, paste report into the issue. |
| `verify-fix` | Bug NOT visible | 0 = NOT_REPRODUCED | "Does the fix hold?" — run after PR lands to validate the fix. |
| `regression` | Alias for `verify-fix` | 0 = NOT_REPRODUCED | Same as verify-fix; used in CI as a regression gate. |

## Quickstart

### Confirming issue #3308 (screen-share picker on launch)

```bash
# One-time cache of issue context from GitHub
python -c "from automation.issue_fetch import fetch_issue; fetch_issue('3308')"

# Run confirmation (3 iterations on fedora42 VM)
MOSDAT_CONFIG=examples/rocketchat.toml \
mosdat confirm 3308 --vm fedora42 --iterations 3 --mode confirm
```

**Note on `MOSDAT_CONFIG`**: Until the config-discovery glob is hardened, set this env var to skip the pyproject.toml glob that triggers a KeyError. (See [Known Issues](../docs/KNOWN_ISSUES.md).)

Output: `results/issues/3308/<run-id>/report.md` — open in any Markdown viewer. Paste `comment.md` (same dir) into the GitHub issue.

### After a fix lands

```bash
MOSDAT_CONFIG=examples/rocketchat.toml \
mosdat confirm 3308 --vm fedora42 --mode verify-fix --iterations 5
```

Exit 0 = fix verified. Exit 1 = bug still present (regression).

## Authoring a New Bug-Confirmation Scenario

### File location

```
shared/scenarios/issues/<id>.yaml
```

Example: `shared/scenarios/issues/3308.yaml` for issue #3308.

### Minimal YAML skeleton

```yaml
name: "Bug #<id> — <one-line summary>"
description: |-
  <Numbered repro steps from the issue, edited for clarity.>

kind: bug-confirmation  # Required for bug-confirmation scenarios

issue:
  id: "<id>"
  url: "https://github.com/RocketChat/Rocket.Chat.Electron/issues/<id>"
  title: "<will be auto-filled from cached context.md>"

expected_env:
  os: "Fedora 42"
  install: "flatpak"
  display_server: "wayland"  # or "x11"

bug_signal: |-
  Is a screen-share picker dialog visible, with text mentioning
  "share your screen" and Cancel/Share buttons?

precondition_check: |-
  Is the Rocket.Chat main window visible (with login form, server
  form, or chat UI)?

default_vm: fedora42

steps:
  - action: shell
    command: |-
      rm -rf ~/.var/app/chat.rocket.RocketChat
      rm -rf ~/.config/Rocket.Chat*
      rm -rf ~/.local/share/keyrings/
    description: "Clean up session data"

  - action: launch
    path: "/opt/Rocket.Chat/rocketchat-desktop"
    process: true
    window: true
    description: "Launch Rocket.Chat Flatpak"
    must_pass: true

  - action: wait
    duration_ms: 3000
    description: "Wait for window to settle"
```

### Required fields

- **`kind: bug-confirmation`** — Required. Tells the runner to fire `bug_signal` and `precondition_check` at the end instead of failing on verify failures.
- **`issue.{id, url}`** — At minimum. `title` is auto-filled from cached context.
- **`bug_signal`** — VLM yes/no prompt. YES = bug is visible in the current screenshot.
- **`precondition_check`** — VLM yes/no prompt. YES = the scenario reached the relevant code path (guards against false NOT_REPRODUCED).
- **`expected_env`** — Optional. Reporter's environment (helps spot if you're testing on a different setup). Fields: `os`, `install` (flatpak/snap/appimage/deb/rpm), `display_server`, `app_version`.
- **`steps`** — List of launch/wait/shell/interact steps as usual.

### Gotcha: overlay-blocked verify steps

When a bug overlays the UI (e.g., a picker covers the login form), any `verify:` step that checks for the covered element will fail. Mark such steps with `must_pass: false`:

```yaml
- action: launch
  path: "/opt/Rocket.Chat/rocketchat-desktop"
  process: true
  window: true
  verify:
    - localize:
        landmark: "Server URL input field"
  must_pass: false  # Picker overlays it; verify will fail, but that's OK
```

The runner won't exit early on this failure. Bug_signal and precondition_check will fire afterward.

## Reading the Report

After `mosdat confirm <id>` completes:

### Main report: `results/issues/<id>/<run-id>/report.md`

Sections:

1. **Verdict** — one-line summary: ✅ CONFIRMED, ❌ NOT_REPRODUCED, 🟡 INTERMITTENT (k/N), ⚠ HARNESS_ERROR.
2. **Reproduction steps** — numbered list from the scenario.
3. **Verdict detail** — table showing each iteration's precondition_met and bug_visible.
4. **Environment** — delta table (Reporter's env vs This run): ✓ match, ≈ close, ✗ mismatch.
5. **Smoking-gun evidence** — the screenshot where bug_signal fired YES, with VLM verdict quoted.
6. **Per-iteration artifacts** — links to `iter-1/`, `iter-2/`, etc. (events.jsonl, screenshots, vm-state.json).
7. **Reproducer command** — copy-paste command to re-run the same test.

### GitHub comment: `results/issues/<id>/<run-id>/comment.md`

Stripped-down variant (no internal paths, no run-IDs). Suitable for paste-into-issue. Includes verdict, env table, single screenshot link, and reproducer command.

### Example live report

See `results/issues/3308/<latest>/report.md` for a real confirmation of issue #3308.

## Verdict Semantics

Per iteration:
- **CONFIRMED** — precondition_met=YES, bug_visible=YES
- **NOT_VISIBLE** — precondition_met=YES, bug_visible=NO
- **INCONCLUSIVE** — precondition_met=NO (scenario never reached the buggy code path)

Aggregate (across N iterations):
- **CONFIRMED** — all N iterations CONFIRMED
- **NOT_REPRODUCED** — all N iterations NOT_VISIBLE
- **INTERMITTENT (k/N)** — mixed CONFIRMED + NOT_VISIBLE (bug fires in k out of N runs)
- **HARNESS_ERROR** — all or most iterations INCONCLUSIVE (scenario is wrong or VM is misconfigured)

Confidence score:
- 5/5 same outcome → high
- 4/5 → medium
- 3/5 → low (report flags as INTERMITTENT)
- <3 conclusive iterations → HARNESS_ERROR

## CLI Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `<issue-id-or-url>` | string | required | Issue ID (e.g., `3308`) or full GitHub URL |
| `--vm` | string | from scenario | VM to run on (e.g., `fedora42`) |
| `--iterations` | int | 3 (confirm) / 5 (verify-fix) | Number of runs; higher = better confidence |
| `--mode` | string | `confirm` | `confirm` \| `verify-fix` \| `regression` |
| `--scenario` | path | `shared/scenarios/issues/<id>.yaml` | Override scenario file location |
| `--refresh-issue-context` | flag | — | Re-fetch issue from GitHub (skip cache) |
| `--no-state-snapshot` | flag | — | Skip VM state collection (faster, less evidence) |
| `--keep-vm` | flag | — | Leave VM in post-test state for manual inspection |
| `--output` | path | — | Write report to custom path instead of results/ |
| `--html` | flag | — | Also emit report.html |
| `--skip-warmup` | flag | — | Skip VM warmup (faster, less reliable) |
| `--skip-health-probe` | flag | — | Skip VNC/SSH readiness check |
| `--save-screenshots` | flag | — | Force save all screenshots (default: yes if exit != 0) |
| `--click-verify` | flag | — | Use click-diff for VLM verification (advanced) |

## Pytest Regression Integration

Add a regression test per issue under `tests/issues/`:

```python
# tests/issues/test_3308.py
import pytest
from tests.issues.confirmer import confirm_issue

@pytest.mark.issue("3308")
@pytest.mark.live  # requires VM; skipped unless pytest --live
def test_3308_screenshare_picker_should_not_open_on_launch():
    """Regression: picker must NOT fire on first launch (after fix lands)."""
    result = confirm_issue("3308", mode="verify-fix", iterations=3)
    assert result.aggregate == "NOT_REPRODUCED", \
        f"Bug regressed: {result.report_path}"
```

Run all issue regression tests:

```bash
pytest -m issue --live
```

CI gates merge on this passing post-fix.

## Limitations / Gotchas

### pyproject.toml config glob glitch
**Problem**: `automation/issue_confirm.py` config discovery picks up `pyproject.toml` before `examples/*.toml`. pyproject.toml lacks the `[app]` and `[[vm]]` tables, causing KeyError on startup.

**Workaround**: Set `MOSDAT_CONFIG=examples/rocketchat.toml` env var.

**Status**: [Known issue](../docs/KNOWN_ISSUES.md#mosdat-confirm--config-glob-picks-up-projecttoml-before-examplestoml). Will be fixed in a hardening pass.

### Test cache pollution
**Problem**: Running `pytest` can overwrite `shared/scenarios/issues/<id>.context.md` with a mocked GitHub response (e.g., title="New Title").

**Workaround**: Pass `--refresh-issue-context` to re-fetch the cache from real GitHub before the next confirm run.

**Status**: [Known issue](../docs/KNOWN_ISSUES.md#issue-context-cache-file-can-be-overwritten-by-test_issue_fetch). Will be fixed by scoping the test's mock to tmp_path only.

### Multi-iteration flakes
Some bugs are intermittent (fire 2 out of 5 runs). The tool detects this and flags as INTERMITTENT with the pass rate (e.g., "INTERMITTENT (2/5)"). If you suspect a test-time flake (not a real intermittent bug), re-run with `--iterations=10` or check `iter-N/events.jsonl` for timing clues.

### VLM hallucinations
If `bug_signal` fires YES when the bug isn't visible (or vice versa), multi-iteration aggregation helps — a one-off false positive in 3 runs looks like INTERMITTENT, which you'll notice. For reproducible hallucinations, refine the prompt. Example: "Is a screen-share picker dialog visible with Cancel and Share buttons?" is more specific than "Is a dialog visible?"
