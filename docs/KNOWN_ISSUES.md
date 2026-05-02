# Known Issues

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
