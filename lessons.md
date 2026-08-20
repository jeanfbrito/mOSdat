# mOSdat lessons

Project-specific quirks and design decisions captured during sessions. Cross-project rules go in `~/.claude/lessons.md`.

## Multi-OS smoke playbook

These are mosdat-specific patterns; every supported OS needs them addressed in its scenario YAML or VM config.

### VM hardware (Proxmox)
- **All Linux smoke VMs need `vga=std`, `machine=q35`.** virtio-gpu / qxl break VNC framebuffer capture under Electron compositing — RC window mapped in X but invisible in VNC. Apply via Proxmox API: `PUT /nodes/<n>/qemu/<id>/config {"vga":"std"}`. Confirmed needed on ubuntu2204, ubuntu2404, fedora42, manjaro, opensuse. Windows VMs already use std.
- **VM autologin must be configured per-distro.** mosdat does NOT drive SDDM/GDM login. KDE: `/etc/sddm.conf.d/autologin.conf`. GNOME: gdm settings. Pre-configure once per VM.
- **mosdat does NOT auto-deploy packages.** Pre-stage AppImage/RPM/DEB/EXE in `/tmp/` (or wherever `app_path` points). `{file}` placeholder in `app_path` is resolved via SSH glob at runtime.

### Cleanup script (YAML step 1)
- **Use `pkill -x <truncated-comm>` not `pkill -f <fullname>`.** `-f` matches script's own cmdline → self-kill. `-x` matches truncated-to-15-chars `comm`. RC binary `rocketchat-desktop.bin` (24 chars) → use `rocketchat-desk` (15).
- **Per-DE credential paths to wipe**: gnome-keyring `~/.local/share/keyrings/default` (no extension!), KWallet `~/.local/share/kwalletd/*.kwl`, KDE kscreenlocker config in `~/.config/kscreenlockerrc`.
- **Kill `gnome-keyring-daemon` BEFORE rm-ing keyring files.** Daemon caches in memory; otherwise it rewrites the deleted file on next libsecret access.

### Per-OS scenario forks needed
- **GNOME Wayland (fedora42, ubuntu2404)**: SSH-launched Electron probes GPU, relaunches itself with `--ozone-platform=x11`, fails (no Xauthority). Use Super-key GNOME Activities launcher via VNC instead of direct binary launch.
- **KDE Wayland/X11 (manjaro, opensuse)**: kscreenlocker re-engages mid-test; `pkill -x kscreenlocker_greet` is more reliable than DPMS commands. xset/xdotool either no-op (Wayland) or fail xauth-cookie-mismatched. Drive everything via VNC RFB events. Unlock screen via VNC-typed password using `{vm_password}` template var.
- **Windows 10/11**: PowerShell over OpenSSH strips `$_` and `${...}` — wrap in `_ps_encoded` (base64 EncodedCommand). Use `ntpath.basename` not `os.path.basename` to split exec paths on Linux host. Win11 OOBE WebView2 dialog blocks RC launch on first boot — `pkill WebExperienceHostApp.exe + msedgewebview2.exe` in cleanup.
- **Fedora 42**: update banners need `if_visible:` dismissal guards before navigation steps.

## Framework design rules
- **A4 `precheck_click` is opt-in and narrow-use.** Default-on is too strict — VLM yes/no on small input-field crops false-rejects valid clicks. Enable only on steps where mis-click silently swallows credentials AND there's no `verify_input` / `verify_not` net (e.g. password-then-Enter-without-typed-feedback). Login forms with verify_input should rely on retry loop instead.
- **`launch: wait` is the launch_verify polling budget, not a sleep.** Step 3 needs `wait: 30` for Electron paint. Each VLM verify call ~15-20s; budget needs 2× call duration to allow at least one retry-on-loading-screen.

## Open loops / known limitations
- **GPU passthrough exclusivity not enforced framework-side.** Multiple parallel mosdat invocations could race on GPU attach. C2 snapshot checkpoints help but don't lock. See task #43 for fix.
- **Visual regression is opt-in only.** SSIM-diff against reference screenshots not yet integrated. See task #42.

## Routine engine (R1+)

### Routine input serialization (Cost: ~30min diagnostic + 2 retries)
**What happened**: `launch-rocketchat` routine silently failed on every call. RC process exited shortly after launch, scenario stalled at first verify with `process_not_running`. No clear error in the runner log.
**Root cause**: `automation/routines/runner.py:135` did `render_vars = {**parent_vars, **{k: str(v) for k, v in resolved_inputs.items()}}`. `str(v)` coerced a list-of-dicts `servers` input to its Python `repr()` (single quotes). Then jinja `{{ servers | tojson }}` JSON-encoded the **string** → `"[{'title': 'Workspace', ...}]"` (double-encoded shell-broken payload). `json.loads` returned a string, downstream `s[0]['url']` raised TypeError, config-writer step exited 1, RC launched without config and died.
**What solved it**: Pass native types: `render_vars = {**parent_vars, **resolved_inputs}`. Same fix in `automation/runners/var_subst.py:81` — only coerce scalars to str, leave lists/dicts native.
**Rule**: When routing user values through a Jinja env, NEVER blanket-`str()` complex types. Jinja prints scalars cleanly via `{{ x }}` and handles lists/dicts correctly via filters like `tojson`. Coercion-on-entry breaks filter semantics.

## Test isolation (the 23-failure investigation)

### sys.modules pop without re-import = stub trap (Cost: ~2h triage)
**What happened**: Full pytest suite reported 23 failures; each file passed in isolation. Tests failed with `AttributeError: module 'PIL.Image' has no attribute 'new'` and `ValueError: cannot determine region size; use 4-item box`.
**Root cause**: Three-file chain. `tests/test_build_cmd.py` popped every `PIL*` entry at module top. `tests/test_chaos_infra.py` collected next, saw PIL.Image absent, installed a `types.ModuleType("PIL.Image")` stub (with `Image = object`). `tests/test_cursor_motion_integration.py` then bound local `Image` via `from PIL import Image` → STUB. `Image.new` did not exist. Multiple pop+reimport cycles also produced distinct PIL.Image module instances with different `Image` classes → cross-module `isinstance(crop, Image.Image)` returned False inside `composite.paste(...)` → "needs 4-item box".
**What solved it**:
1. Stop popping `PIL` from sys.modules in `test_build_cmd / test_doctor / test_inject_config / test_replay / test_x11_preamble`.
2. Add `_PIL_WAS_REAL` guard to `test_if_visible` so it only stubs PIL when never loaded.
3. conftest `pytest_collection_finish` targets-re-imports `automation.transport.ssh` + `automation.setup.capability` when stubs are detected.
4. conftest reorders `test_negative / test_concurrent_safety / test_proxmox_vm / test_build_cmd` LAST.
**Rule**: NEVER pop a real library module from sys.modules unless immediately re-imported. The hole between pop and re-import is when a sibling installs a destructive stub. Real PIL lives in the venv; it never needs stubbing.

### Discovery: Multiple PIL.Image module instances break isinstance
**Context**: Even after fixing the stub install, runner_features tests still failed with `paste()` "needs 4-item box".
**Insight**: `sys.modules.pop("PIL.Image")` then `import PIL.Image` creates a NEW module object — old name-bindings still reference the OLD. Each has its own `Image` class. PIL's paste does `isinstance(im, Image.Image)`; cross-module isinstance is False → fall through to color-fill path → 4-item-box error.
**Implication**: Module identity is process-global. Pop+reimport patterns create copies; downstream cross-module isinstance silently switches semantics.

### Multiple pytest_collection_modifyitems hooks in conftest: last def wins
**What happened**: Added a `pytest_collection_modifyitems` hook to reorder files. Hook never fired.
**Root cause**: conftest.py already had a second `pytest_collection_modifyitems` 200 lines later for `--live` marker skipping. Python module-level: second `def` overrides first by name. pytest only registers the latter.
**Rule**: One named function per pytest hook per conftest.py. Factor multi-concern logic into helpers; call from a single dispatcher.

## Agent-facing MCP tooling (001-agent-desktop-testing)

### Discovery: a partial MCP server already existed before the feature was planned
**Context**: Planning "expose mosdat to Claude agents via MCP" assumed greenfield. A finder pass over `automation/` turned up `mcp_server.py` + `mcp_tools.py` (hand-rolled JSON-RPC/stdio, 10 tools, `mosdat-mcp` entry point) already wired and working.
**Insight**: The real work was closing gaps in an existing surface (missing `mosdat_readiness`, `mosdat_build` only doing a host-side yarn build instead of the full clone→build→deploy flow, inconsistent response shapes) — not building a new server.
**Implication**: Before scoping any "add X to this project" feature, grep for the thing you're about to build. A plan written against an assumed-absent capability produces the wrong task list even when every individual task is well-formed.

### Ordinary functional runs return `(passed, log)`, not `BugConfirmationResult` (Cost: caught before it shipped, ~0 rework)
**What happened**: `data-model.md` was drafted assuming `mosdat_run_functional`'s underlying call returned `BugConfirmationResult` (verdict enum `BUG_CONFIRMED`/`BUG_NOT_VISIBLE`/`INCONCLUSIVE`), by analogy with the bug-confirmation workflow.
**Root cause**: `BugConfirmationResult` is produced only by `_run_bug_confirmation_scenario` (the `mosdat confirm` path). Ordinary scenario runs go through `FunctionalRunner.run_test()`, which returns a plain `tuple[bool, str]` (`passed, log`).
**What solved it**: A task explicitly instructed the builder to verify the real return type against `functional_cmd.py`/`functional.py` *before* writing the handler, rather than trusting the analogy. It found the mismatch immediately and corrected the data model before any code was written against the wrong assumption.
**Rule**: When a design references "the return type of X" by analogy to a similarly-named type elsewhere in the same codebase, make the builder's first task step "read the actual call site and confirm," not "assume and proceed." Naming similarity across a bug-confirmation path and a general-execution path is not a type guarantee.

### `mosdat_list_vms` used to list every Proxmox VM, not just configured ones
**What happened**: The original `mosdat_list_vms` queried the Proxmox API directly for every QEMU guest on the node, while `mosdat_vm_start`/`stop`/`status` only ever operate on VMs present in the mosdat TOML config (`cfg.vm_by_name`) — so a VM the list tool happily showed could be rejected as "unknown" by the very next tool call.
**Rule**: When a discovery/list tool and its sibling action tools scope differently, that's a latent bug, not a feature — align the list to what the actions can actually operate on. Fixed by iterating `cfg.vms` instead of the raw Proxmox API response (with live status cross-referenced by vmid, degrading gracefully to `degraded: ["proxmox_unreachable"]` instead of raising if Proxmox itself is down).

### `_call_run_build`'s capture-wrapper must be updated for every new build.py deploy-path function (Cost: ~15min + 1 live rebuild cycle to catch)
**What happened**: `mcp_tools.py::_call_run_build` monkey-patches `build.py`'s `deploy_to_vm`/`deploy_to_windows_vm` to capture their `DeployResult` (since `run_build()` itself only returns an exit code). A later session added a new `build_on_windows_vm()` deploy path in `build.py` — correct in isolation, all its own tests passing — but nobody updated the wrapper list. The bug was invisible to every mocked unit test (they replace `run_build` wholesale, so the capture mechanism never actually runs) and only surfaced when a REAL `mosdat_build` call against a real Windows VM came back with `installed_version: ""` despite a genuinely successful install.
**Root cause**: Two sequential builder sessions each modified one side of an implicit contract (build.py's set of deploy functions vs. mcp_tools.py's wrapper list) without either being told the other side existed.
**Rule**: When a wrapper/registry pattern exists (a list of functions to monkey-patch, intercept, or register), any brief that adds a new function matching that pattern must explicitly say "and update the wrapper/registry at `<location>` too." Don't rely on the second session noticing on its own. Separately: a mocked-wholesale unit test of the outer function can never catch a bug in that function's own interception logic — only a test that lets the real function run (with just the leaf call mocked) or a live integration run exercises it.

### GitNexus impact/detect_changes has two known false-negative/false-positive modes
**Discovery**: (1) `impact()`/`detect_changes()` miss calls made through a module-attribute reference (`from X import Y as mod; mod.func(...)`) rather than a direct name import — `run_build`'s real callers (CLI dispatch, the MCP tool, 6 test files) showed as `impactedCount: 0, risk: LOW` until cross-checked with a plain grep. (2) After a large insertion earlier in a file, GitNexus's line-based symbol mapping flags untouched functions further down as "touched" purely from line-shift — confirmed by checking that no diff hunk actually referenced those symbols.
**Implication**: For genuinely high-stakes impact checks (core, actively-tested, multi-consumer functions), grep for real call sites as a cross-check rather than trusting a LOW-risk verdict outright, and verify a "touched" flag against actual diff hunks before treating every listed symbol as a real edit.

## Windows VM remote build/test (Rocket.Chat.Electron via mosdat)

- **`ssh-copy-id` fails against Windows OpenSSH Server** — it sends a POSIX `sh -c` install script; Windows has no POSIX shell. Admin accounts additionally get their per-user `authorized_keys` ignored entirely — keys must go in `%ProgramData%\ssh\administrators_authorized_keys` with `icacls` locked to `Administrators:F`/`SYSTEM:F`. See `docs/KNOWN_ISSUES.md`.
- **A Windows VM's `yarn.ps1` (or any corepack-installed shim) needs `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` first** — the default policy blocks all `.ps1` scripts, including ones a legitimate package manager just installed.
- **`electron-builder --win` cross-built from macOS needs Wine, and Wine's 32-bit dependency is broken on Catalina+.** The fix that actually works is building natively ON the target Windows VM over SSH (clone/yarn/electron-builder there), not fixing Wine on the host.
- **A Windows VM's Node.js can be stale relative to what a repo's `package.json` now requires** — `engines.node`/`packageManager` (corepack yarn pin) mismatches surface as a confusing `ERR_REQUIRE_ESM` mid-build, not a version-mismatch error. Check `node --version` against `engines.node` before building, and fail with an explicit message rather than let the build run 5+ minutes into a doomed failure.
- **`$ErrorActionPreference = "Stop"` does NOT stop a PowerShell script on a non-PowerShell process's non-zero exit code.** A failed `yarn build` silently fell through to a much more confusing `electron-builder` failure (missing `app.asar` entry file) before this was caught. Always check `$LASTEXITCODE` explicitly after every external command (yarn/git/node) in a script sent over SSH.
- **Base64-encoding a full PowerShell script (`-EncodedCommand`, UTF-16LE) sidesteps SSH→shell→PowerShell nested-quoting entirely** — inline `-Command "..."` with nested quotes breaks unpredictably across that chain; a single self-contained encoded script never does.
- **When SSH itself is the thing that's broken and no password is known, drive the Proxmox VNC console directly instead** — `automation.transport.vnc.VncClient` (the same primitive the functional runner uses) can screenshot, click, and type on any VM's console with zero SSH/network dependency. Screenshot first to confirm the desktop is actually unlocked (a lock/login screen still needs the password). Use "Run as administrator" + click through the UAC prompt (both need a screenshot each, coordinates aren't fixed) — a plain Start-menu-launched PowerShell gets a UAC-filtered token even for an Administrators-group account and can't write to `%ProgramData%\ssh\`. Type raw multi-line commands, not a base64 blob — there's no shell-quoting problem over VNC, only keystroke-injection reliability, so shorter/readable commands are both sufficient and easier to verify from the screenshot. Confirmed working end-to-end fixing `windows11`'s SSH key with an unknown password, 2026-08-20.

## Recording / VNC capture

### Frame-diff filter: max-pixel not mean (Cost: ~45min)
**What happened**: GIFs recorded by `mosdat functional --record-gif` looked empty. raw=103, filtered=7. Cursor motion frames dropped.
**Root cause**: `mean_abs_diff` on 64×64 grayscale thumbnails with default threshold 3.0. A cursor moving 1-2 pixels on a 1280×720 frame is sub-pixel on the 64×64 thumb; bilinear blurs to ~0.06 intensity mean — way below threshold.
**What solved it**: Switch metric to `max_abs_diff` (`ImageStat.Stat(diff).extrema[0][1]`); bump thumb to 256×256 (cursor lands as 3-6px sprite, max diff ~100+); default threshold 1.0 (drop only pixel-identical frames).
**Rule**: When filtering for "did anything visible change", use MAX or count-of-changed-pixels, not MEAN. Mean averages sparse motion into the noise floor.
