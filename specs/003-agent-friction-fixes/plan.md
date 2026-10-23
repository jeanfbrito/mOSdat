# 003 — Agent friction fixes (master plan)

Source: mining of 10 agent sessions that used mosdat (2026-08-21 → 2026-10-20)
plus the 5 mosdat memory files that agents wrote in the Rocket.Chat.Electron
project. Each item names the session that showed it.

## Problem

Agents lose the most time on five things:

1. Tools that report success on a wrong result (`.deb` deploy, `click_at` on a
   Windows toast, a "pass" with the VLM down).
2. Windows commands through `mosdat_ssh` that lose `$vars`, die with the SSH
   session, or hit Defender.
3. Long rebuilds when a built artifact is already on the VM.
4. Lab and Docker states that block a run but give no next step.
5. An MCP server that dies after an SSH timeout.

## Constraints

- The working tree has ~2,500 lines of uncommitted work (DSL `click_at`,
  `poll_shell`, `verify_recorded`, `vm_hygiene`). Build on it. Do not revert it.
- Baseline before this plan: full suite = 27 failed, 43 errors, 1384 passed.
  Those failures come from cross-file test coupling and a missing `numpy`.
  Run alone, the touched test files pass. The gate is: no new failure.
- New behavior is opt-in where it changes an existing default. Exception:
  results that are wrong today (false success) are fixed in place.
- Each MCP tool keeps the `{ok, error, degraded, ...}` envelope.

## Workstreams

Write scopes do not overlap, so A–F run in parallel. The MCP wiring
(`mcp_tools.py`, `mcp_server.py`) is done once, after A–F land (step G).

### A. SSH transport and script mode (items 2, 8)

Files: `automation/transport/ssh.py`, new `automation/transport/remote_script.py`,
`tests/test_remote_script.py`, `tests/test_ssh_*.py`.

- A1. `SSHClient.run` / `scp_*` start `ssh`/`scp` with `stdin=DEVNULL`.
  Root cause of the "MCP disconnects after SSH call" (c8883777): the child
  inherited the MCP server's stdin (the JSON-RPC pipe).
- A2. `run_script(ssh, script, *, is_windows, timeout, detach=False,
  session="ssh")`: upload the script to a temp file with scp, then run it
  with `powershell -NoProfile -ExecutionPolicy Bypass -File` (Windows) or
  `bash` (Linux). No inline quoting, no `-EncodedCommand`.
- A3. `detach=True`: Windows starts the script through `Win32_Process.Create`
  (CIM), which outlives the SSH session; Linux uses `setsid nohup`. Returns
  the remote log path and PID.
- A4. `session="desktop"` (Windows): run the script in the logged-in user's
  session 1 through an InteractiveToken scheduled task, wait, return output
  from a log file.

Done when: unit tests cover the argv built for each mode, and a live run on
windows10 shows `$x` survives, a detached job outlives the SSH call, and a
desktop-session script sees session 1.

### B. Native Windows click (item 1)

Files: `automation/vlm/input.py`, `automation/scenario.py` (`ClickAtCoords`),
`automation/runners/functional_steps.py` (click branch), `automation/commands/lint.py`
if it validates `click_at`, `tests/test_native_click.py`.

- B1. `InputInjector.native_click(x, y, button)`: session-1 task that calls
  `SetProcessDPIAware` + `SetCursorPos` + `mouse_event`. Windows only.
- B2. `click_at: {x, y, native: true}` routes to `native_click`. Default stays
  VNC. On Linux `native` logs a warning and uses VNC.
- B3. Skill + KNOWN_ISSUES: VNC clicks dismiss Windows toasts; use `native: true`.

Done when: unit tests prove routing, and a live run clicks a real toast on
windows10 and Electron logs an activation, not `Notification dismissed`.

### C. Build and deploy truth (items 3, 5, 6, disk)

Files: `automation/commands/build.py`, `tests/test_build_*.py`.

- C1. `validate_artifact(path, target)`: size floor + magic bytes
  (`!<arch>` deb, `\xed\xab\xee\xdb` rpm, ELF AppImage, `MZ` exe). Run in
  `deploy_to_vm` / `deploy_to_windows_vm` before scp, and on cache hits.
- C2. deb deploy: after install, compare `dpkg-deb -f <pkg> Package Version`
  with `dpkg-query -W`. Mismatch → `error`. rpm: same with `rpm -qp`/`rpm -q`.
- C3. `DeployResult.artifact` holds the artifact path (VM path for a native
  Windows build). The MCP result shows it.
- C4. `install_existing_on_windows_vm(vm, artifact_path_on_vm)` and
  `install_release(vm, tag)`: reinstall without a rebuild.
- C5. `build --source <local dir>`: tar the local tree (git ls-files -co
  --exclude-standard), scp to the Windows VM, build there.
- C6. Before a native Windows build, remove old `C:\mosdat-builds\pr*` dirs
  (keep the newest 2) when free space on C: is < 15 GB.

Done when: unit tests cover each, and a live deploy of a 96-byte `.deb`
fails with a clear error.

### D. Server provisioning (item 4)

Files: `automation/commands/rc_server.py`,
`automation/commands/templates/rc-server-compose.yml`, `tests/test_rc_server.py`.

- D1. Docker down → start it (`open -g -a Docker` on macOS), wait up to 120 s;
  if the VM never comes up, quit + restart once. Opt-out `start_docker=False`.
- D2. If the GHCR image is missing, try `docker.io/rocketchat/rocket.chat:<ref>`.
- D3. Compose turns off 2FA (`OVERWRITE_SETTING_Accounts_TwoFactorAuthentication_*`).

Done when: unit tests cover each, and a live `provision 7.10.0` returns ready.

### E. VM lifecycle and readiness (items 7, 10b, 10c)

Files: `automation/commands/vm_lifecycle.py` (new), `automation/commands/ssh_bootstrap.py`
(Linux path), `tests/test_vm_lifecycle.py`.

- E1. `wait_ready(vm, timeout)`: poll SSH until it answers. Used by
  `mosdat_vm_start` with `wait_ready: true`.
- E2. Windows readiness: find the installed `Rocket.Chat.exe`, report version,
  `app.asar` path and mtime, disk free on C:. Fixes "readiness missed the
  asar path" (01a0262c) and answers "is the PR code installed?".
- E3. Readiness failure hints: Proxmox down → plain message; SSH down → name
  other VMs of the same OS.
- E4. Linux ssh-bootstrap through the QEMU guest agent, then fix ubuntu2404.

### F. Functional runs (items 9, 10a)

Files: `automation/runners/` only if needed, `tests/test_mcp_functional_*.py`.

- F1. `scenario` accepts an absolute path to a YAML file outside `shared/`;
  new `scenario_yaml` takes inline YAML (written under `results/`).
- F2. With `vlm_unavailable`, a scenario that has VLM steps returns
  `verdict: "unverified"` and `screenshots_to_judge`.

### G. MCP wiring + docs (after A–F)

Files: `automation/mcp_tools.py`, `automation/mcp_server.py`,
`tests/test_mcp_tools.py`, `AGENTS.md`, `skills/mosdat-testing/SKILL.md`,
`docs/KNOWN_ISSUES.md`, `specs/001-agent-desktop-testing/contracts/mcp-tools.md`.

- `mosdat_ssh`: `script`, `detach`, `session` args.
- `mosdat_vm_start`: `wait_ready`.
- `mosdat_build`: `source`; result carries `artifact`.
- `mosdat_deploy`: `artifact_on_vm`, `release`.
- `mosdat_readiness`: Windows install info + hints.
- `mosdat_run_functional`: path / inline scenario, `unverified`.
- `mosdat_ssh_bootstrap`: Linux VMs.

## Verification

1. Scoped tests per workstream, then the full suite against the baseline list.
2. Live on the lab (up on 2026-10-23): A, B, C1/C2, D, E1–E4, F via the real
   MCP server over stdio.
3. `detect_changes` before any commit.

## Out of scope

Committing or pushing (needs a request). Splitting the earlier WIP into
its own commits (offer it at the end).
