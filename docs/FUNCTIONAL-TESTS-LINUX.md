# Running functional smoke tests on Linux VMs

Notes captured while porting `rocketchat-smoke.yaml` (Windows-only) to
Linux. The same pattern applies to any Electron / GUI app added later.

## Core constraint: no SSH-launched apps on Wayland

The Windows flow SSHs in and does `Start-Process ...`. The naive Linux
equivalent is `DISPLAY=:0 nohup <app>`. This **works on X11** (Ubuntu
22.04 GNOME X11) but **fails on Wayland** (Ubuntu 24.04 GNOME Wayland,
Fedora, etc.) for these reasons:

1. An SSH shell has no `WAYLAND_DISPLAY`, `DBUS_SESSION_BUS_ADDRESS`, or
   `XAUTHORITY` — it lives in a separate PAM session.
2. Even with those env vars exported, Electron tries `--ozone-platform=wayland`
   first; when GPU init fails in a VM without VirGL it **self-relaunches
   into X11 mode** ("GPU features unavailable, disabling GPU and
   relaunching with X11"). That second process has no xauth and dies.
3. `wmctrl -a '<title>'` can't focus a Wayland-native window — there is
   no cross-app window control API on Wayland.
4. `systemd-run --user --scope` segfaults for this VM image. Even when
   it runs, it doesn't solve (2).

Working around each of these piecemeal ends up brittle and
distro-specific. The stable answer is to stop launching apps from SSH.

## The pattern: drive the desktop launcher with VNC + AT-SPI/VLM

Instead, do what a human does:

1. `Escape` — close any stale overlay (Activities, Kickoff, modal popup).
2. `Super` — open the desktop's app launcher. Every mainstream Linux DE
   binds Super to *something* that accepts a typed query:
   - GNOME → Activities overview with search bar
   - KDE → Kickoff menu
   - XFCE → whisker menu
3. type `"Rocket"` (or the app's display name — matches `.desktop`
   `Name=` field).
4. `Enter` — launches the top-ranked result.
5. VLM `verify` — confirm the app is the foreground window.

Clicks/keys flow through the Proxmox VNC WebSocket (RFB protocol) so they are independent of X11 vs Wayland, guest-side tools, or xauth. For accessible Linux widgets, prefer AT-SPI role/name selectors (`atspi:`, `verify_atspi:`, `wait_for:`) before VLM localization; keep VLM `localize` for canvas-like or inaccessible regions.

## Pre-test cleanup (bash)

```bash
pkill -9 -f rocketchat-desktop || true
pkill -9 -f Rocket.Chat       || true
# Ubuntu's update-manager / update-notifier pops a modal over RC and
# steals clicks — nuke it before starting.
pkill -f update-manager  || true
pkill -f update-notifier || true
sleep 2
rm -rf "$HOME/.config/Rocket.Chat" "$HOME/.config/Rocket.Chat (development)"
```

## Functional runner changes that made this work

- `FunctionalRunner.run_step` now handles **standalone `key` / `type` /
  `wait` steps** (no `localize` / `launch` / `shell`). This is what
  lets the YAML emit a `Super → type "Rocket" → Enter` sequence.
- 1.5 s pause after `key_pre` covers the time the launcher takes to
  fully paint before typing lands. 0.8 s after `type` lets GNOME's
  search ranker settle before `Enter`.
- `atspi:`, `verify_atspi:`, and `wait_for:` steps use Linux accessibility semantics for role/name targeting when widgets expose them. Discovery is via `mosdat atspi-dump`.

(`automation/runners/functional.py`, in the "standalone key/type steps"
branch.)

## qwen3.6 parser learnings

Switching the VLM from `qwen3-vl-abliterated` to
`qwen3.6-35b-a3b-uncensored-vl` required two changes to
`automation/vlm/client.py`:

1. `qwen3.6` wraps its answer in a list:
   `[{"bbox_2d": [x1, y1, x2, y2], "label": "…"}]`. Parser now unwraps
   a top-level list and understands `bbox_2d` (center of the box).
2. `bbox_2d` values are on the **0-1000 normalized grid**, same as
   `qwen3-vl`, not pixel space. Auto-detect: if any coord is >1000,
   treat as pixel; else normalized. Localize output divides by 1000
   and multiplies by screen size only for normalized coords.

qwen3.6 occasionally emits a malformed coord dict
(`{"x": [...], "<no y>"}`). The existing retry loop absorbs this —
observed ~1 retry per 5 successful runs.

## Smart retype on retry

`functional_runner.run_step` now asks the VLM, before re-typing on a
retry, whether the target text is **already visible in an input
field**. If yes, skip the type. Prevents double-fills when the field
typed fine but the downstream transition (server 502, slow login) was
what actually failed.

## Adding a new Linux DE / OS

1. Add a VM block to `examples/rocketchat.toml` (mirror `ubuntu2404`).
   Set `desktop = "<DE name>"` and the `[[vm.package]]` entries for the
   formats you care about. For a deb install set
   `app_path = "/opt/Rocket.Chat/rocketchat-desktop"`.
2. Install the app once on the VM so a `.desktop` file lands under
   `/usr/share/applications/`. Confirm it's indexed by the DE's
   launcher (Super → type → Enter finds it).
3. If the DE uses a different launcher key, change `key_pre` / `key` in
   the launch step of `rocketchat-smoke-linux.yaml`. Examples:
   - GNOME / KDE / XFCE default → `super`
   - Some XFCE setups → `ctrl+alt+space` (xfce4-appfinder)
   - i3 / tiling WMs → whatever `dmenu` is bound to
4. If the DE pops distro-specific nagware (update manager, welcome
   tour, telemetry dialog), kill or close it in the cleanup shell
   step. Keep these kills per-distro — don't pollute the generic step.

## What *doesn't* need per-distro changes

- VNC click/keyboard/screenshot path (Proxmox RFB) — works on every
  Linux GUI regardless of display server.
- AT-SPI selectors when widgets expose stable role/name metadata.
- qwen3.6 localize/verify prompts — the prompts describe app UI
  elements, not OS chrome, so they transfer as-is.
- VLM model swap via `[vlm]` in the TOML.
- Cleanup bash (pkill + `rm -rf ~/.config/<App>`).

## Known gotchas

- **First boot after install**: GNOME's `gnome-initial-setup` may run
  and block the Super launcher. Complete it once per VM image.
- **Software Updater modal**: Ubuntu pops this on a schedule; add
  `pkill -f update-manager` to cleanup, or disable via
  `gsettings set com.ubuntu.update-notifier no-show-notifications true`.
- **Super-key sticking**: if a prior test crashed mid-overview, Super
  may be in an inverted toggle state. The `key_pre: escape` at the top
  of the launch step neutralizes this.
- **Wayland focus is one-way**: once RC is foreground the test is
  fine, but if a notification steals focus mid-run there is no way to
  force-focus back. Keep verify_not clauses strict so stolen-focus
  failures are caught immediately, not silently tolerated.
