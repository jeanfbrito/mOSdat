---
name: mosdat-testing
description: "Use whenever validating a real desktop/Electron app change against a real machine is relevant — testing a Rocket.Chat.Electron PR, checking whether a build actually launches and works on Windows/Linux, driving UI interactions on a VM, or answering \"does this PR/build work\" / \"can you verify this on a real machine\" / \"test this on Windows\". Trigger even if the user doesn't say \"mosdat\" by name — any request to build, deploy, launch, or click through a desktop app on an actual OS (not a simulated/headless check) is mosdat's job. Also trigger for questions about available test VMs, test scenarios, or Proxmox-hosted test machines. Do NOT use for unit/integration tests that run in CI without a real desktop, or for web-app browser testing (use claude-in-chrome for that instead)."
---

# mOSdat — Desktop App Testing & Validation

mOSdat drives real build→deploy→run→verdict loops against real Proxmox VMs (Linux
and Windows) for Electron desktop apps — primarily Rocket.Chat.Electron. It exposes
an MCP server (`mosdat-mcp`, registered globally) with tools whose names all start
`mosdat_*`. If you see those tools available, you have direct access — no shelling
out needed. If they're missing, tell the user to run `claude mcp add mosdat-mcp -s
user -- <path-to-mOSdat>/.venv/bin/mosdat-mcp` from the mOSdat repo (or `pip install
-e .` there first if it's not built yet).

This is real infrastructure — real VMs get built on, real installs happen, real
minutes pass. Don't fire off a build/deploy/run against a guessed VM or PR number;
confirm the target with the user when it's ambiguous, the same way you would before
any action with a real, non-trivial blast radius.

## Do this first: discover, don't guess

Before running anything, if the user hasn't told you exactly which VM and which
scenario/PR they mean, call `mosdat_list_vms` and `mosdat_list_scenarios` to see
what's actually configured — VM names, OS, and available scenarios vary by project
and change over time. Guessing a VM name that used to exist, or picking a scenario
unrelated to what the user actually wants tested, wastes real build time and
produces a result that answers the wrong question.

Then, before committing to an expensive build/deploy/run, call `mosdat_readiness`
on the target VM. It tells you if the VM is even reachable and has what it needs
*before* you spend minutes on a build that's doomed to fail for environmental
reasons that have nothing to do with the code under test (VM powered off, SSH not
authorized yet, stale toolchain, wrong build already installed).

## The core loop

| Goal | Call |
|---|---|
| "Does PR #N work?" (no build deployed yet) | `mosdat_build({pr, target, deploy_to})` then `mosdat_run_functional({vm, scenario})` |
| "Run this scenario again" (build already there) | `mosdat_run_functional({vm, scenario})` directly — skip the build |
| "Is `<vm>` ready for a run?" | `mosdat_readiness({vm})` |
| "What can I even test?" | `mosdat_list_vms({})` + `mosdat_list_scenarios({})` |
| "Test the server side of PR #N" (a Rocket.Chat server PR, not the Electron app) | `mosdat_server_provision({ref})`, poll until `state: "ready"`, then pass its `url` as `server_url` to `mosdat_run_functional` |
| VM is powered off | `mosdat_vm_start({vm_name})`, then poll `mosdat_readiness` until `ready: true` — Windows VMs can take a few minutes to boot before SSH responds |

Builds and runs are genuinely slow: a Linux `.deb` build is a few minutes, a
Windows `.exe` build (now built natively on the VM, not cross-compiled) can take
~5-10 minutes, and a VLM-driven scenario run is tens of seconds to a couple of
minutes. Run these in the background / don't block the conversation waiting —
poll or check back rather than sitting on a single long foreground call if your
environment supports background execution.

## Reading the result — this is the part that actually matters

Every tool returns a JSON envelope: `{"ok": bool, "error": str|null, "degraded":
[...], ...tool-specific fields}`. The fields that determine what you tell the user
are NOT all equally important — read them in this order:

1. **`ok: false`** — the *tool itself* couldn't complete (bad VM/scenario name,
   environment not ready, lock contention, a real crash). `error` names the
   problem and, for an unknown VM/scenario, lists the valid alternatives directly
   — use them instead of re-guessing.
2. **`env_not_ready: true`** (on `mosdat_run_functional`) — the environment,
   not the app, is the problem (VM unreachable, missing dependency). Never
   describe this as "the test failed" — say the environment wasn't ready and
   suggest `mosdat_readiness` to see exactly what's wrong.
3. **`verdict`** (on `mosdat_run_functional`, only meaningful when `ok: true`) —
   `"pass"` / `"fail"` / `"error"`. This is the actual answer to "does it work."
   A `"fail"` here is a real, run-for-real scenario failure — worth reporting
   precisely (which step, per `steps[]`, with its `reason` and screenshot
   `artifact`), not glossed over. It does NOT mean the tooling broke.
4. **`degraded`** (present even when everything else looks fine) — e.g.
   `["vlm_unavailable"]` means visual verification didn't actually run for part
   of the check. Never report a `verdict: "pass"` as a clean, confident pass
   without also surfacing this — a degraded pass is a weaker signal than a
   normal one and the user should know why.
5. For `mosdat_build`: `build_ok` and `deploy.install_ok` are separate — a
   `deploy: null` with `build_ok: false` means the build itself failed and no
   deploy was even attempted (don't blame "deploy" for a build failure).
   `deploy.installed_version` confirms which build actually landed on the VM.

A "vm busy: ... held by another operation" error means a concurrent
build/deploy/run already has that VM locked — wait and retry, don't force it or
assume the VM is broken.

## Tool reference

| Tool | Key params | Notes |
|---|---|---|
| `mosdat_list_vms` | *(none)* | Configured VMs + live Proxmox status |
| `mosdat_list_scenarios` | `platform?` | Runnable scenario names, filterable by `linux`/`windows` |
| `mosdat_readiness` | `vm`, `expect_pr?`, `expect_symbol?` | Go/no-go: SSH, deps, disk, optional deployed-build match, `busy` flag |
| `mosdat_build` | `pr`, `target?` (deb/rpm/appimage/exe, default deb), `deploy_to?`, `verify_symbol?`, `repo?` | Full clone→build→(deploy)→verify-symbol flow. Windows targets now build natively on the VM |
| `mosdat_deploy` | `vm`, `artifact_path`, `target?` | Install an already-built local artifact onto a VM (no clone/build) |
| `mosdat_run_functional` | `vm`, `scenario`, `from_step?`, `until_step?`, `timeout?`, `server_url?` | VLM-driven UI test; returns `verdict`/`steps`/`artifacts`. `server_url` overrides the scenario's configured `workspace_url` for this call only — point it at a `mosdat_server_provision` result (or any other already-running server) instead of the default static workspace |
| `mosdat_run_smoke` | `vm_name`, `timeout?` | Cheap launch + basic UI check, no full scenario |
| `mosdat_vm_start` / `mosdat_vm_stop` / `mosdat_vm_status` | `vm_name` | Proxmox VM power control/status |
| `mosdat_ssh` | `vm_name`, `command` | Arbitrary shell command on a VM — escape hatch, not the normal path |
| `mosdat_ssh_bootstrap` | `vm`, `pubkey_path?` | Windows only. If `mosdat_readiness`/any tool reports SSH unreachable on a Windows VM, call this before troubleshooting further — it installs this host's key via the VNC console (no password needed, as long as the desktop is unlocked) and no-ops if SSH already works |
| `mosdat_server_provision` | `ref` (PR/tag/`develop`) | Idempotent: brings up (or reports the state of an already-running) test Rocket.Chat server for `ref`. `state` is `ready`/`starting`/`failed`; `url` appears once the container's port is known — pass it as `server_url` to `mosdat_run_functional` |
| `mosdat_server_teardown` | `ref` | Tears down the provisioned server for `ref`. Idempotent — `torn_down: false` if none existed, never an error. Never touches a server the user handed you directly as a `server_url` |
| `mosdat_server_list` | *(none)* | Lists all currently provisioned server instances (`ref`, `state`, `url`, `elapsed_ms`) |

## Platform notes

- **Windows targets**: build directly on the target VM over SSH now (no Wine
  dependency on the mosdat host). If a Windows build fails with an error naming
  `engines.node` or a version mismatch, the VM's Node.js is too old for the repo
  being built — that's a VM environment issue to flag to the user, not a code
  problem, and not something to silently "fix" by upgrading system software on
  someone's VM without saying so.
- **Windows SSH not authorized yet**: don't hand-roll a fix — call
  `mosdat_ssh_bootstrap({vm})`. It only proceeds if the VM's desktop is unlocked
  (aborts on a lock/sign-in screen rather than guessing) and is a no-op if SSH
  already works, so it's safe to call speculatively whenever readiness reports
  SSH down on a Windows VM.
- **PR number**: if working inside the Rocket.Chat.Electron repo and the user
  says "this PR"/"my change" without a number, check the current branch/PR via
  `gh pr view` rather than asking — but still confirm the target VM.
- **Symbol verification** (`verify_symbol`/`expect_symbol`): a cheap way to prove
  a specific code change actually made it into the build under test — pass a
  string that should appear in the built app if (and only if) the PR's change is
  present, when the user wants proof beyond "the PR number matched."
