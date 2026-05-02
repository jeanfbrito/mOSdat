# Issue-confirmation tool — `mosdat confirm`

**Use when**: a teammate asks "does bug X actually happen?" and we need visual + state proof. Or a PR claims to fix a tracked issue and we need a regression test.

## One-command workflow

```bash
# 1. Cache issue context (one-time per issue)
python -c "from automation.issue_fetch import fetch_issue; fetch_issue('<id>')"

# 2. Run confirmation
MOSDAT_CONFIG=examples/rocketchat.toml \
mosdat confirm <id> --vm <vm> --iterations 3 --mode confirm

# 3. Open results/issues/<id>/<run>/report.md
# 4. Paste results/issues/<id>/<run>/comment.md into the GitHub issue
```

## Mode semantics

- `--mode confirm` (default): exit 0 = bug IS visible (CONFIRMED verdict).
- `--mode verify-fix`: exit 0 = bug NOT visible (NOT_REPRODUCED verdict). Use after PR lands.
- `--mode regression`: alias for verify-fix. Used in CI gates.

Exit codes across modes:
- 0 = verdict matches mode expectation
- 1 = verdict opposite (regression / not-reproduced when expected)
- 2 = INTERMITTENT (mixed iterations)
- 3 = INCONCLUSIVE / HARNESS_ERROR (scenario didn't reach buggy code path)
- 4 = tool/VM error

## Authoring a scenario

File: `shared/scenarios/issues/<id>.yaml`

Required fields:

```yaml
kind: bug-confirmation
issue:
  id: "<id>"
  url: "https://github.com/RocketChat/Rocket.Chat.Electron/issues/<id>"
bug_signal: |-
  Is the bug (visual state you're looking for) visible in this screenshot?
precondition_check: |-
  Did we reach the code path where the bug COULD fire?
  (E.g., "Is the app window open?" — must be true before bug_signal is meaningful.)
expected_env:
  os: "<reporter OS, e.g. Fedora 42>"
  install: "<flatpak|snap|appimage|deb|rpm>"
  display_server: "<wayland|x11>"
steps:
  - action: shell
    command: "rm -rf ~/.var/app/chat.rocket.RocketChat && rm -rf ~/.config/Rocket.Chat*"
  - action: launch
    path: "/opt/Rocket.Chat/rocketchat-desktop"
    process: true
    window: true
    must_pass: true
  - action: wait
    duration_ms: 3000
  # More steps as needed
```

**Key detail**: Use `must_pass: false` on verify steps whose target element is overlaid by the bug (e.g., a picker covers the login form). The runner won't exit early; bug_signal and precondition_check fire after all steps.

## Scenario semantics per iteration

After all steps, the runner captures a final screenshot and independently fires two VLM yes/no prompts:

1. **precondition_check** — YES/NO: did the scenario reach the relevant code path?
2. **bug_signal** — YES/NO: is the bug currently visible?

Result mapping:

| precondition_met | bug_visible | Outcome | Interpretation |
|------------------|-------------|---------|---|
| NO | — | INCONCLUSIVE | Scenario failed to exercise the buggy code path. Verdict is meaningless; re-examine the scenario. |
| YES | YES | CONFIRMED | Bug is visible. |
| YES | NO | NOT_VISIBLE | Precondition met but bug not visible. |

Aggregate across N iterations (e.g., 3 runs):

| All CONFIRMED | All NOT_VISIBLE | Mixed | Any INCONCLUSIVE |
|---|---|---|---|
| → verdict: **CONFIRMED** | → verdict: **NOT_REPRODUCED** | → verdict: **INTERMITTENT (k/N)** | → flag warning; use conclusive subset if ≥3, else **HARNESS_ERROR** |

## Known issues / workarounds

### Config glob glitch
```
MOSDAT_CONFIG=examples/rocketchat.toml mosdat confirm <id> ...
```
Default config discovery picks up pyproject.toml first and triggers KeyError. Set the env var as shown. (Will be hardened in a future pass.)

### Test cache pollution
```
mosdat confirm <id> --refresh-issue-context ...
```
Running the full pytest suite can overwrite `shared/scenarios/issues/<id>.context.md` with a mocked GitHub response. Pass `--refresh-issue-context` to re-fetch from real GitHub. (Will be fixed by scoping test mocks to tmp_path only.)

### VLM hallucinations
Multi-iteration aggregation helps. A one-off false positive in 3 runs looks like INTERMITTENT (2/3), which you'll notice and can debug. Refine the VLM prompt if hallucinations are consistent:
- Bad: "Is a dialog visible?"
- Good: "Is a screen-share picker dialog visible with Cancel and Share buttons?"

## Example: confirming issue #3308

```bash
# Cache the GitHub issue context
python -c "from automation.issue_fetch import fetch_issue; fetch_issue('3308')"

# Create scenario at shared/scenarios/issues/3308.yaml (see docs/issue-confirm-tool.md for full example)

# Run confirmation
MOSDAT_CONFIG=examples/rocketchat.toml \
mosdat confirm 3308 --vm fedora42 --iterations 3 --mode confirm

# Check verdict
cat results/issues/3308/*/report.md | head -20

# Paste into issue
cat results/issues/3308/*/comment.md
```

## See also

- User-facing guide: `docs/issue-confirm-tool.md`
- Scenario plan + architecture: `.claude/mytasks/plan-issue-confirm-tool.md`
- Known issues: `docs/KNOWN_ISSUES.md`
- Pytest regression pattern: `tests/issues/test_3308.py`
