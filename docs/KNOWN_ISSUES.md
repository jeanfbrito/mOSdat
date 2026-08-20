# Known Issues

## opensuse Leap KDE: xdotool not installed, SSH cannot authorize to :0
- **Status**: Workaround in place
- **Issue**: `xdotool` is not installed on opensuse Leap by default. Additionally,
  the SSH session cannot authorize to the X11 display `:0` — the xauth cookie in
  `/tmp/xauth_*` is not accepted by the SSH user context ("Authorization required,
  but no authorization protocol specified"). All shell-step X11 commands
  (xdotool, xdpyinfo, xset, qdbus) silently fail (exit 127 or connection refused).
- **Workaround**: Use VNC-native input only. In scenario YAML, replace shell-based
  xdotool calls with `key:`, `then_type:`, and `localize:` steps — these inject
  via VNC and bypass the SSH/X11 authorization problem entirely. For KDE lock
  screen dismissal, use `if_visible:` + `localize:` (password field) + `then_type`
  + `then_key: return`.
- **Affects**: `shared/scenarios/functional/rocketchat-smoke-linux-kde.yaml`.
  Any future scenario targeting opensuse Leap KDE must avoid SSH-based X11 input.
  The same likely applies to any KDE X11 VM where the X session is owned by a
  different user/process than the SSH login.
- **Ref**: Discovered during opensuse smoke iteration (iter 1-2, May 2026).

## Fedora 42: injector.launch() fails on GNOME Wayland (no WAYLAND_DISPLAY in gnome-shell proc env)
- **Status**: Workaround in place
- **Issue**: `InputInjector.launch()` reads env from gnome-shell's `/proc/PID/environ`. On
  Fedora 42 / GNOME Wayland, `WAYLAND_DISPLAY` is NOT present in gnome-shell's proc environ
  (it is propagated differently via systemd user session units). The RC wrapper script
  (`/opt/Rocket.Chat/rocketchat-desktop`) checks for `WAYLAND_DISPLAY` to decide between
  Wayland and X11 ozone platforms; without it, forces `--ozone-platform=x11`, which then
  segfaults because there is no Xauthority file in a pure Wayland session.
- **Workaround**: Use the GNOME Activities launcher (Super → type app name → Enter) via VNC
  keyboard input instead of `launch:`. The Activities launcher inherits the full graphical
  session env from gnome-shell, including `WAYLAND_DISPLAY=wayland-0`.
- **Affects**: `shared/scenarios/functional/rocketchat-smoke-fedora.yaml` (implements the
  workaround). Any future scenario targeting Fedora 42 GNOME Wayland that uses `launch:` will
  hit the same issue.
- **Ref**: Discovered during H1.1 fedora42 smoke iteration (iter 1-2).

## Fedora 42: Linux proc comm truncation breaks pkill -x for long process names
- **Status**: Workaround in place
- **Issue**: Linux truncates process comm (name in `/proc/PID/comm`) to 15 characters.
  `rocketchat-desktop.bin` → `rocketchat-desk` (15 chars). `pkill -x rocketchat-desktop.bin`
  produces zero matches; the procps version on Fedora 42 emits a warning and matches nothing.
- **Workaround**: Use `pkill -x rocketchat-desk` (the actual truncated comm name) in cleanup.
- **Affects**: `shared/scenarios/functional/rocketchat-smoke-fedora.yaml` cleanup shell step.
  Note: `rocketchat-smoke-linux.yaml` uses `pkill -x rocketchat-desktop.bin` which works on
  Ubuntu (different procps behaviour or different binary comm name — verify if porting to
  other RPM distros).
- **Ref**: Discovered during H1.1 fedora42 smoke iteration (iter 4).

## Windows: process_running must use _ps_encoded (not raw powershell -Command)
- **Status**: Fixed in input.py
- **Issue**: Windows OpenSSH's default shell strips `$_` before PowerShell sees it.
  A raw `powershell.exe -Command "... | Where-Object {$_.Name -like ...}"` sent over
  SSH arrives as `... | Where-Object {.Name -like ...}` — `.Name` is not recognized,
  matching nothing. Every `process_running` call returned False regardless of whether
  the process was running.
- **Workaround**: Encode all PowerShell scripts via `_ps_encoded()` (base64 UTF-16-LE
  `-EncodedCommand`) so the SSH shell never touches the script body.
- **Affects**: `automation/vlm/input.py` `process_running`. Any new Windows SSH calls
  that use `$_`, `${...}`, or other PS special vars must use `_ps_encoded`.
- **Ref**: Discovered during H1.1 windows10 smoke iteration (iter 1-4).

## Windows: Get-Process Name has no .exe extension
- **Status**: Fixed in input.py
- **Issue**: `Get-Process` returns `Name` without the `.exe` extension. Searching for
  `*Rocket.Chat.exe*` never matches the process named `Rocket.Chat`.
- **Workaround**: Strip `.exe` suffix before building the wildcard pattern.
- **Affects**: `automation/vlm/input.py` `process_running`. Any new process name checks
  on Windows must account for this.
- **Ref**: Discovered during H1.1 windows10 smoke iteration (iter 1-4).

## Windows: os.path.basename fails on backslash paths when running on Linux host
- **Status**: Fixed in functional.py
- **Issue**: `os.path.basename("C:\\Users\\...\\Rocket.Chat.exe")` on Linux returns
  the full string (no `/` to split on). The full Windows path was passed to
  `process_running`, producing a wildcard pattern that never matched any process name.
- **Workaround**: Use `ntpath.basename` when `injector.is_windows` is True.
- **Affects**: `automation/runners/functional.py` launch step basename extraction.
  Any code on the Linux host that parses Windows paths must use `ntpath`, not `os.path`.
- **Ref**: Discovered during H1.1 windows10 smoke iteration (iter 1-4).

## Windows 11: "Let's finish setting up your PC" OOBE dialog blocks app window
- **Status**: Workaround in place
- **Issue**: On first boot (or after VM snapshot restore), Windows 11 shows a
  "Let's finish setting up your PC" setup wizard as a full-screen WebView2 dialog
  (`WebExperienceHostApp.exe` + `msedgewebview2.exe`). This covers any app window
  that launches behind it, causing `process=True, window=False` in launch verify.
- **Workaround**: Kill `WebExperienceHostApp.exe` and `msedgewebview2.exe` in the
  pre-test cleanup shell step before launching the app under test.
- **Affects**: `shared/scenarios/functional/rocketchat-smoke.yaml` cleanup step.
  Any Win11 scenario that launches a GUI app must kill these processes first.
- **Ref**: Discovered during H1.1 windows11 smoke iteration (iter 1-3).

## `mosdat build --target exe` needs Wine to cross-build from macOS — fixed via on-VM native build

- **Status**: RESOLVED — `automation/commands/build.py::build_on_windows_vm()`.
- **Issue**: `electron-builder`'s Windows (NSIS) packaging invokes a Wine-dependent
  step (via its bundled `app-builder` helper) when run from a non-Windows host.
  Modern macOS (Catalina+) dropped 32-bit executable support, which this
  specific Wine invocation needs, so `mosdat build --target exe` always failed
  on the packaging step when run from a Mac, even though the build got as far
  as producing partial `win-arm64` MSI/ZIP output before failing:
  `⨯ macOS Catalina doesn't support 32-bit executables and as result Wine cannot
  run Windows 32-bit applications too`. The `app-builder` binary itself was not
  broken (ran fine standalone) — it's specifically the Wine-dependent packaging
  path that fails.
- **Fix**: `run_build()` now detects a pure-Windows `--deploy` list with
  `--target exe` and builds NATIVELY on the target Windows VM over SSH instead
  of locally — clone, `yarn install`/`build`, `electron-builder --win`, then
  install in place (no SCP needed, no Wine involved at all since it's real
  Windows). See `build_on_windows_vm()`. A mixed Windows+Linux `--deploy` list
  in one call still uses the old host-side build+SCP path for now (out of
  scope for the initial fix — rare in practice).
- **Affects**: Any future PR/branch built with `--target exe` from a
  non-Windows mosdat host. No longer requires Wine installed anywhere.
- **Ref**: Discovered and fixed building PR #3464
  (`fix/windows-notification-quick-reply`) live against `windows10`
  (192.168.13.87), 2026-08-20. See the Node.js entry below for a related gotcha
  hit along the way.

## Windows VMs: stale Node.js breaks Electron builds run natively on the VM (ERR_REQUIRE_ESM)

- **Status**: Fixed on `windows10` by upgrading Node; other Windows VMs (`windows11`) may
  have the same stale version and need the same fix the first time a native on-VM build is
  attempted.
- **Issue**: `windows10` shipped with Node v20.18.1. Rocket.Chat.Electron's current
  `package.json` requires `"engines": {"node": ">=24.11.1"}` and pins `packageManager`
  to `yarn@4.6.0` via corepack. Running `yarn build` under Node 20 fails during Electron's
  postinstall with:
  ```
  Error [ERR_REQUIRE_ESM]: require() of ES Module .../node_modules/electron/node_modules/@electron/get/dist/index.js
  from .../node_modules/electron/install.js not supported.
  ```
  Because `$ErrorActionPreference = "Stop"` in a PowerShell script does **not** stop on a
  non-PowerShell process's non-zero exit code, the script kept going to
  `electron-builder`, which then failed differently and more confusingly: the asar it
  packaged was missing `app/main.js` (`Application entry file "app\main.js" ... was not
  found in this archive`) — a downstream symptom of the incomplete `yarn build`, not a
  separate root cause. Always check `$LASTEXITCODE` after each `yarn`/external-process
  call in a build script, don't rely on `$ErrorActionPreference` alone.
- **Fix — upgrade Node** (no `winget`/`nvm-windows` needed; direct MSI works):
  ```powershell
  Invoke-WebRequest -Uri "https://nodejs.org/dist/v24.19.0/node-v24.19.0-x64.msi" -OutFile "$env:TEMP\node.msi"
  Start-Process msiexec.exe -ArgumentList "/i `"$env:TEMP\node.msi`" /qn /norestart" -Wait
  ```
  (`winget` itself failed with "Failed in attempting to update the source" on this VM —
  don't rely on it; direct nodejs.org MSI download works and needs no source configuration.)
  Then activate the pinned yarn version via corepack — do NOT rely on whatever `corepack`
  downloads by default (it silently gave classic Yarn 1.22.22 the first time, not 4.6.0):
  ```powershell
  corepack enable
  corepack prepare yarn@4.6.0 --activate
  ```
- **Affects**: Any Windows VM used for a native on-VM build (as opposed to mOSdat's normal
  build-on-host-then-deploy flow) of a project whose `engines.node` has moved past what
  was originally imaged onto the VM. Check `node --version` against the target repo's
  `package.json` `engines` field before starting a native Windows build.
- **Ref**: Discovered building PR #3464 (`fix/windows-notification-quick-reply`) natively
  on `windows10` (192.168.13.87) for live validation of the agent-desktop-testing MCP
  tooling, 2026-08-20.

## Windows: ssh-copy-id fails, manual key install required

- **Status**: Workaround documented; use the commands below.
- **Issue**: `ssh-copy-id` sends a POSIX `sh -c 'exec sh -c "cd; umask 077; ..."'` script to
  install the key — Windows OpenSSH Server has no POSIX shell (default shell is
  `powershell.exe` or `cmd.exe`), so the command fails immediately:
  `exec : The term 'exec' is not recognized...`. Additionally, if the SSH user is a member
  of the local `Administrators` group, Windows OpenSSH Server ignores the per-user
  `~/.ssh/authorized_keys` file entirely and only reads
  `%ProgramData%\ssh\administrators_authorized_keys` (with strict ACL requirements) — so
  even manually appending to the per-user file silently does nothing for admin accounts.
- **Symptom before the fix**: `ssh -i <key> user@winvm` returns
  `Permission denied (publickey,password,keyboard-interactive)` for a key that "should"
  work, or — if an `ssh-agent` is running with several unrelated keys loaded and no
  `Host`-specific stanza exists in `~/.ssh/config` for that VM's IP — the client cycles
  through every local identity file and the connection is dropped with
  `Too many authentication failures` before ever reaching the right key or a password
  prompt.
- **Diagnose**: confirm which failure mode you're in before fixing anything:
  ```bash
  # Does the account name even matter, or is auth itself failing?
  ssh -o BatchMode=yes -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519 -o ConnectTimeout=8 \
    <user>@<VM_IP> echo ok
  # "Too many authentication failures" (no password prompt reached) → too many local
  # identities being offered; "Permission denied (publickey,password,...)" cleanly →
  # this specific key genuinely isn't authorized yet (the real fix below).

  # Is the account a local admin? (changes which authorized_keys file applies)
  sshpass -p '<password>' ssh <user>@<VM_IP> "whoami /groups" | grep -i admin
  ```
- **Fix (admin account — e.g. `jean` on the mOSdat Windows VMs)**: install the key into
  `administrators_authorized_keys` with correct ACLs via a base64-encoded PowerShell
  command (avoids all the SSH→PowerShell quoting problems that break inline `-Command`):
  ```bash
  PUBKEY=$(cat ~/.ssh/id_ed25519.pub)
  cat > /tmp/install-key.ps1 <<EOF
  \$key = '$PUBKEY'
  \$path = "\$env:ProgramData\ssh\administrators_authorized_keys"
  Add-Content -Force -Path \$path -Value \$key
  icacls.exe \$path /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"
  Get-Content \$path
  EOF
  ENCODED=$(iconv -f UTF-8 -t UTF-16LE /tmp/install-key.ps1 | base64 | tr -d '\n')
  sshpass -p '<password>' ssh <user>@<VM_IP> "powershell -NoProfile -EncodedCommand $ENCODED"
  ```
  This *appends* to the file — it does not clobber any key already installed by a previous
  session. Verify: `ssh -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519 <user>@<VM_IP> echo ok`
  should now succeed with no password prompt.
- **Fix (non-admin account)**: the normal per-user path works —
  `Add-Content -Force -Path "$env:USERPROFILE\.ssh\authorized_keys" -Value $key` (no
  `administrators_authorized_keys`, no `icacls` ACL step needed).
- **Where the VM password comes from**: `MOSDAT_VM_PASSWORD`/`DEFAULT_VM_PASSWORD` (see
  `.env`) are scenario template variables (`{{ vm_password }}`) for typing a password into
  an on-screen login step — they are NOT read by `SSHClient` (`automation/transport/ssh.py`)
  for SSH authentication itself. There is no automated password-based SSH fallback; the
  VM's actual login password has to come from whoever provisioned it.
- **Affects**: Any Windows VM (`os_type = "windows"` in the TOML config) the first time you
  connect from a new host/key, or after a VM is rebuilt from a fresh image/snapshot.
  `automation/transport/ssh.py`'s `SSHClient` has no `IdentitiesOnly`/`IdentityFile`
  handling of its own — it inherits whatever the system `ssh` resolves, so a host with many
  unrelated local keys and no `~/.ssh/config` entry for the VM's IP is more likely to hit
  the "too many authentication failures" variant even once the right key IS installed
  server-side, if `IdentitiesOnly=yes` isn't set for that host.
- **Ref**: Discovered getting `windows10` (192.168.13.87, PR #3464 live validation) working
  under a fresh SSH client environment, 2026-08-19.

## Issue 3308: screen-share picker IS reproducible on first launch (clean install)

- **Status**: Confirmed on fedora42 VM via mosdat confirm 3308; tracked upstream in PR #3313.
- **Issue**: RC.Electron 4.14.0 Flatpak opens the desktopCapturer screen-share picker
  dialog immediately on launch, before any user interaction or server registration.
- **Repro**: `mosdat confirm 3308 --vm fedora42 --mode confirm` produces a CONFIRMED
  verdict; the bug fires on a clean install with no servers registered. Surprising
  finding vs the issue body, which suggested the bug needed a prior connect+close
  cycle: actually it fires on the very first launch.
- **Env mismatch from reporter**: reporter ran Fedora 43 Wayland Flatpak with
  ozone-platform=wayland; we ran Fedora 42 with the no-GPU x11 fallback. Bug
  reproduced anyway, so the regression is independent of the ozone backend.
- **Scenario**: `shared/scenarios/issues/3308.yaml` (kind: bug-confirmation).
- **Regression test**: `tests/issues/test_3308.py` runs in `pytest -m issue --live`.
  Will turn green once PR #3313 lands and is propagated to Flathub.
- **Ref**: Bisect attempt 2026-05-02 (initial NOT_REPRODUCED was a faulty scenario);
  re-confirmed 2026-05-02 with `mosdat confirm` harness.

## Fedora 42: RC Flatpak session data in ~/.config/Rocket.Chat (development)/, not ~/.var/app/

- **Status**: Documented; cleanup scripts updated
- **Issue**: RC Flatpak on fedora42 writes ALL session data (cookies, localStorage, config.json,
  IndexedDB) to the HOST filesystem at `~/.config/Rocket.Chat (development)/` via Electron's
  `app.getPath('userData')`. The Flatpak manifest has no `filesystem=` restrictions so the
  sandbox passes through `~/.config` directly. The canonical Flatpak data path
  `~/.var/app/chat.rocket.RocketChat/` contains only empty scaffolding (cache/config/data
  dirs, no RC data).
- **Workaround**: Any scenario or cleanup script that needs to wipe RC state must delete
  BOTH `~/.var/app/chat.rocket.RocketChat` AND `~/.config/Rocket.Chat*/` (glob catches
  both `Rocket.Chat` and `Rocket.Chat (development)`). Also wipe
  `~/.local/share/keyrings/` to clear libsecret token cache.
- **Affects**: Any scenario targeting fedora42 Flatpak RC that needs a fresh (unauthenticated)
  session. The `rocketchat-no-screenshare-picker.yaml` scenario implements the correct cleanup.
- **Ref**: Discovered during issue 3308 bisect attempt, 2026-05-02.

## Fedora 42: RC relaunches with X11 when GPU unavailable (--disable-gpu workaround)
- **Status**: Not needed with GNOME Activities launcher workaround
- **Issue**: When launched via SSH with `WAYLAND_DISPLAY` set, Electron probes GPU and on
  failure relaunches itself with `--ozone-platform=x11`, which then fails (no Xauthority).
  Adding `--disable-gpu` prevents the relaunch loop, but the Wayland window still doesn't
  appear in the VNC framebuffer when launched from SSH.
- **Workaround**: GNOME Activities launcher (see above) avoids this entirely — RC launches
  in the graphical session directly and GPU probe follows Wayland path successfully.
- **Affects**: SSH-based launch paths on any no-GPU GNOME Wayland VM.
- **Ref**: Discovered during H1.1 fedora42 smoke iteration (iter 2-3).

## VNC type_text drops shift-required keysyms on Wayland mutter VNC backend
- **Status**: Workaround in place (avoid shift-required canary chars)
- **Issue**: `automation/transport/vnc.py::type_text` sends X11 keysyms (with optional
  Shift_L wrap from the `_SHIFTED` set). On the Proxmox QEMU → Wayland mutter VNC
  path, single-char shifted typing (e.g. `~`, `§`, `!`, `@`) drops the keypress
  silently — no error, just no character produced. Multi-char strings of unmodified
  ASCII (lowercase letters, digits, `.` `,` `/` etc.) work fine.
- **Workaround**: Canary chars and any single-keystroke probe must use no-shift
  ASCII (lowercase letters or digits). Default canary char is `"q"` — not in any RC
  placeholder text and reliably typed. If shift-required input is essential,
  send `injector.key("shift+...")` explicitly rather than `type_text`.
- **Affects**: `automation/runners/functional.py::_check_canary`, any scenario
  step that relies on `type_text` for single shifted chars.
- **Ref**: Discovered during canary-byte hardening, May 2026 (smoke iter 7-10).

## mosdat confirm — config glob picks up pyproject.toml before examples/*.toml
- **Status**: Workaround in place
- **Issue**: `automation/issue_confirm.py` config-discovery does
  `list(project_root.glob("*.toml")) + list(project_root.glob("examples/*.toml"))`,
  but pyproject.toml lacks the `[app]` / `[[vm]]` tables that load_config expects,
  triggering KeyError before any VM action runs.
- **Workaround**: Set `MOSDAT_CONFIG=examples/rocketchat.toml` env var.
- **Affects**: `mosdat confirm` invocations without explicit MOSDAT_CONFIG.
- **Fix**: Filter the glob to TOML files containing `[[vm]]`, or look in
  `examples/` first. One-line change in `automation/issue_confirm.py::run_confirm`.
- **Ref**: Discovered during Phase F live validation, 2026-05-02.

## Issue-context cache file can be overwritten by test_issue_fetch
- **Status**: Workaround in place (use --refresh-issue-context)
- **Issue**: `tests/test_issue_fetch.py::test_cache_miss_fetches_from_github`
  monkeypatches `automation.issue_fetch.CACHE_DIR` to a tmp_path. When
  module reloading from sibling test files (test_issue_confirm fixture)
  rebinds the module reference, the patch may not take effect, and the
  test's mocked GitHub response (title="New Title") gets written to the
  real `shared/scenarios/issues/<id>.context.md` cache.
- **Workaround**: Run `mosdat confirm <id> --refresh-issue-context` once
  to repopulate the cache from real GitHub before the next confirm run.
- **Affects**: `shared/scenarios/issues/<id>.context.md` after running
  the full pytest suite.
- **Fix**: Switch `test_cache_miss_fetches_from_github` to mock
  `_cache_path` instead of `CACHE_DIR` so it can't accidentally write
  outside tmp_path even if the module reference goes stale.
- **Ref**: Observed during Phase F live validation, 2026-05-02.

---

## Rocket.Chat.Electron PR #3325 — isTelephonyEnabled in config.json is silently ignored (Redux-persist)

- **Status**: Resolved — mosdat now uses overridden-settings.json instead.
- **Issue**: Writing `"isTelephonyEnabled": true` (or `false`) as a top-level key in
  `~/.config/Rocket.Chat*/config.json` has no effect at runtime. RC's Redux-persist
  rehydrates feature flags from its own internal namespace (`__internal__`) on startup
  and silently ignores top-level writes. Additionally, if RC is not fully quit when
  the file is written, RC's state-save tick overwrites the file within seconds.
  A prior incorrect diagnosis (commit d510f4b) attributed this to a SIGTRAP crash
  caused by `isTelephonyEnabled` appearing in config.json; that was wrong — the crash
  was a separate upstream issue in the PR3325 branch (missing `src/telephony/main.ts`),
  not caused by the field itself. PR3325 source includes `isTelephonyEnabled` in
  `PersistableValues_4_14_0` with a proper migration; RC logs show no SIGTRAP from
  the field; crashpad dumps were empty.
- **Fix**: Use `overridden-settings.json`. RC's `src/app/main/data.ts` merges this
  file on top of Redux state on every startup, guaranteeing the value regardless of
  prior Redux state. The `launch-rocketchat` routine now writes both files: `config.json`
  contains only state RC owns (servers, window bounds, last URL); `overridden-settings.json`
  contains all boolean flags and feature toggles.
- **Rule**: Always use `overridden-settings.json` for any boolean or string you need
  to guarantee. Never rely on top-level config.json writes for Redux-managed keys.
- **Affects**: `shared/routines/launch-rocketchat.yaml`. Any scenario that pre-stages
  feature flags must use the `launch-rocketchat` routine's inputs, not manual config.json writes.
- **Ref**: Corrected 2026-05-18; see `docs/runbooks/scenario-state-seeding.md`,
  commit d510f4b (the incorrect diagnosis that was reverted by this fix).

## RFB capture used pixel-count completion gate (fixed)

- **Status**: RESOLVED — fixed in `automation/transport/vnc.py`
- **Symptom**: `VncClient.capture()` returned screenshots that were N frames
  behind the real screen state. Post-click popups and transient UI were
  invisible to the VLM, causing widespread localize/verify flakiness (kebab
  click 2026-05-18: popup rendered on VM but absent in capture at 09:35:14).
- **Root cause 1 (stale buffer)**: `_Reader._buf` and any pending WebSocket
  frames from unsolicited server FB updates (cursor moves, dirty regions) were
  not drained before sending a new `FramebufferUpdateRequest`. The stale bytes
  were consumed as if they were the fresh response, leaving the actual response
  buffered for the next capture call.
- **Root cause 2 (wrong completion gate)**: `_grab_framebuffer()` exited its
  read loop when `painted >= W*H`. Overlapping rectangles in one FB update
  double-counted pixels, so the gate fired before all rectangles in the message
  were consumed.
- **Fix**: (a) `capture()` now drains `_reader._buf` and any pending WS frames
  via `recv(timeout=0)` before sending the FBUR. (b) `_grab_framebuffer()` now
  reads exactly one `FramebufferUpdate` message (consuming all its declared
  rectangles) and returns immediately, matching the RFB §7.6.1 one-request →
  one-response contract.
- **Affects**: All scenarios relying on post-action screen state (localize,
  verify, verify_not after click/key). Visible on ubuntu2204 QEMU VNC path.
- **Ref**: Auditor diagnosis 2026-05-18; fixed same session.

## Runner `launch:` step bypasses `shared/routines/launch-rocketchat.yaml`
- **Status**: Workaround in place
- **Issue**: The runner's `launch:` step handler builds its own bare command
  for the target binary; it does NOT invoke the matching routine YAML at
  `shared/routines/launch-rocketchat.yaml`. As a result, accessibility-related
  flags (`ACCESSIBILITY_ENABLED=1`, `--force-renderer-accessibility`,
  `--ozone-platform=x11`) baked into the routine never reach the spawned
  process when a scenario uses `launch: rocket-chat` (or similar).
- **Workaround**: Use an inline `shell:` step that sets the env vars and
  flags explicitly. The Stage 1E/2 smoke scenarios
  (`_smoke-atspi.yaml`, `_smoke-stage2-waitfor.yaml`,
  `_smoke-stage3-controlmaster.yaml`) all do this — copy that block when
  authoring new AT-SPI scenarios.
- **Affects**: `automation/runners/functional_steps.py` (the `step.launch`
  branch in `run_step`). Any scenario that uses `launch:` and relies on
  flags from the routine YAML.
- **Long-term fix**: Teach the `launch:` step to resolve a matching routine
  in `shared/routines/` and invoke it instead of synthesising the command.
- **Ref**: Discovered during Stage 1D wiring, 2026-05-22.

## Canvas/custom-rendered widgets are opaque to AT-SPI
- **Status**: By design (Chromium a11y boundary)
- **Issue**: Chromium only exposes standard HTML elements (buttons, inputs,
  links, headings, frames) to the AT-SPI accessibility bus. Custom Web
  Components and canvas-drawn regions appear as bare containers with no
  children. RC regions known to be opaque: chat message list (virtual
  scroll), emoji picker, video call panel, rich-text editor (composer),
  drag-and-drop file-upload overlay.
- **Workaround**: Keep `localize:` (VLM) for these regions. Document the
  target widget shape in the scenario so future authors know why VLM is
  used instead of `atspi:`.
- **Affects**: Any AT-SPI step targeting one of the listed regions.
- **Ref**: Chromium accessibility tree exposes only standard widgets. See
  `docs/atspi-authoring.md` "Known canvas regions" for the live list.

## `_resolve_in_dict` var substitution is shallow
- **Status**: Workaround in place
- **Issue**: `automation/runners/scenario_loader.py:_resolve_in_dict` walks
  only the top-level keys of `atspi:` / `verify_atspi:` / `wait_for:` dict
  values. `{var}` substitutions DO work for top-level string values like
  `name: "{username}"`, but they are NOT substituted inside list-of-dict
  payloads such as `wait_for.any[].name`. The string `"{username}"` will
  reach the worker verbatim.
- **Workaround**: Pre-resolve vars in the scenario or use literal values
  inside nested condition dicts.
- **Affects**: `automation/runners/scenario_loader.py:resolve_vars` and
  `_resolve_in_dict` helper.
- **Long-term fix**: Deep-recurse `_resolve_in_dict` through lists of dicts
  so the substitution behaviour matches the top-level case.
- **Ref**: Discovered during Stage 1D wiring, 2026-05-22.

## `atspi:` `name_substr: true` requires a non-empty `name`
- **Status**: Workaround in place
- **Issue**: The worker `find` op treats missing `name` + `name_substr: true`
  as malformed — there is no string to substring-match against. Empty
  `name: ""` is equally rejected.
- **Workaround**: Always provide a non-empty `name` when setting
  `name_substr: true`. To match "any node of role X", omit `name_substr`
  entirely (and omit `name`) — the worker will return the first node of
  the requested role.
- **Affects**: `automation/atspi/worker.py:_op_find`. All three step fields
  that route through the find op: `atspi:`, `verify_atspi:`, `wait_for:`
  conditions.
- **Ref**: Discovered during Stage 1D wiring, 2026-05-22.

## Window-state sampler requires a live GUI session DISPLAY
- **Status**: Workaround in place (auto-injection); sampler self-disables on failure
- **Issue**: `wmctrl` and `xdotool` need `DISPLAY` and `XAUTHORITY` to query
  the running session. When `--record-window-state` is on, the sampler now
  auto-injects `DISPLAY=:0` plus the mutter XAUTHORITY
  (`/run/user/1000/.mutter-Xwaylandauth.*`) for each SSH round-trip. This
  works on a logged-in GNOME-X11 session owned by `jean`. Headless CI,
  non-mutter X sessions, and pure-Wayland sessions without an Xwayland
  cookie will not be sampled — the sampler self-disables after the first
  failure and the recording proceeds without the extra metadata.
- **Workaround**: Run with a real graphical login on the VM. For headless
  runs, simply omit `--record-window-state` (default OFF).
- **Affects**: `automation/recording/session_recorder.py:_collect_window_state`.
- **Ref**: Discovered during Stage 3a live verify, 2026-05-22.

## launch-rocketchat: Wayland-only XAUTH path silently failed on Xorg VMs
- **Status**: Fixed (XAUTH fallback chain added)
- **Issue**: `shared/routines/launch-rocketchat.yaml` (the launch shell step)
  resolved `XAUTHORITY` ONLY via `ls /run/user/1000/.mutter-Xwaylandauth.*
  | head -1`. On Xorg-only VMs (e.g. `ubuntu2204@192.168.13.81`) the mutter
  Xwayland auth file does not exist; the lookup returned empty, RC was
  launched with `XAUTHORITY=""`, the binary saw `cannot open display` and
  silently failed to render. Downstream `wait_for {role: frame}` steps then
  timed out at 30s.
- **Workaround**: The shell step now tries mutter Xwayland auth → gdm
  Xauthority (`/run/user/1000/gdm/Xauthority`) → `$HOME/.Xauthority`, and
  only exports `XAUTHORITY` when the resolved file actually exists.
  Scenarios that drove launch inline copied the same fallback chain.
- **Affects**: `shared/routines/launch-rocketchat.yaml` line ~137;
  `shared/scenarios/functional/3325-diagnostics-panel.yaml` inline launch.
- **Ref**: Discovered while wiring `3325-diagnostics-panel` against the
  PR3325 clean redeploy, 2026-05-22.

## Rocket.Chat.Electron #3325 (`feat/telephony-deeplink`) — TypeError on zero-workspaces fresh profile

- **Status**: upstream bug, awaiting PR fix.
- **Issue**: launching Rocket.Chat with an empty `servers.json` (zero workspaces) throws `TypeError: Cannot read properties of null (reading 'url')` at `setupServers` (main.js:6581). RC main process exits silently; no window appears.
- **Workaround**: stage at least one workspace in `servers.json` before launching. mOSdat scenario `tel-qa-014-negative-cases.yaml` step2 (zero-workspaces test) is currently SKIPPED for this reason — re-enable when upstream is fixed.
- **Affects**: `tel-qa-014-negative-cases.yaml` step2 sub-scenario.
- **Ref**: stack at `/opt/Rocket.Chat/resources/app.asar/app/main.js:6581:26 setupServers → select`. Discovered 2026-05-22 on ubuntu2204@192.168.13.81 (RC 4.14.1 PR build).

## Fuselage ToggleSwitch: AT-SPI action_name decoupled from React `checked` state
- **Status**: Workaround in place (probe-by-modal pattern)
- **Issue**: The Telephony master toggle (Fuselage `<ToggleSwitch>`) is
  exposed in the AT-SPI tree as `role: check box, name: "Telephony"`, but
  its single action is always reported as `action_name: "uncheck"`
  regardless of the underlying React `checked` prop. Invoking the action
  toggles state, but neither `verify_atspi` (no state inspector) nor the
  pre-action `action_name` reveals whether the click landed OFF→ON or
  ON→OFF. Blind `atspi:` clicks therefore drift: if the toggle is
  persisted ON from a prior session, the first click flips it OFF, the
  diagnostics panel never renders, and the scenario stalls.
- **Workaround**: Behavioural verification via the one-shot
  "Rocket.Chat now opens phone links" modal, which fires ONLY on
  OFF→ON. Drive the toggle directly from the worker: `find` →
  `do_action` → `wait_for {role: push button, name: "Got it"}` with a
  short timeout. If `Got it` does not appear within 5s, click again (the
  prior click went ON→OFF; this one goes OFF→ON) and re-poll with a
  longer timeout. Implemented in the `drive Telephony ON via worker
  (probe-by-modal)` shell step of `3325-diagnostics-panel.yaml`.
- **Affects**: any scenario that needs to GUARANTEE Telephony is ON via
  AT-SPI (currently `3325-diagnostics-panel.yaml`). Same pattern applies
  to other Fuselage ToggleSwitch instances if they ship one-shot side
  effects.
- **Ref**: Discovered during `3325-diagnostics-panel` live-run iteration,
  2026-05-22.
