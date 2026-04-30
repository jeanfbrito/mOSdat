# Triage

← Back to [AGENTS.md](../../AGENTS.md)

## Exit Code Interpretation

| Code | Meaning | Result |
|------|---------|--------|
| 0 | Clean exit | PASS |
| 124 | Timeout (app ran 10s) | PASS (didn't crash) |
| 139 | SIGSEGV | FAIL (the bug) |
| 134 | SIGABRT | FAIL |

## Interpreting Test Output

```
Summary: PASS=3 FAIL=0 EXPECTED=2
```

- **PASS**: Test scenario worked correctly
- **FAIL**: Unexpected crash (exit 139 = SIGSEGV = THE BUG)
- **EXPECTED**: Acceptable failure (e.g., no-display test crashes because no display)
- **SKIP**: Test not applicable (e.g., Weston+NVIDIA incompatibility)

**Critical test**: `wayland-fake` - if this shows PASS, the crash fix works.

## Agent Delegation Pattern (for AI agents)

When running tests in parallel, delegate to background agents:

```
# Wave 3: Fire 5 parallel agents for WITHOUT GPU tests
delegate_task(category="quick", run_in_background=true, prompt="
  VM: Fedora 42 (192.168.13.80)
  Packages: RPM, AppImage
  Commands: [copy from matrix-run.md runbook]
  Save to: results/.../fedora42-*-no-gpu.log
")
# Repeat for each VM...

# Wait for all to complete, then Wave 4-8: Sequential GPU tests
# GPU tests CANNOT be parallelized - only one GPU
```
