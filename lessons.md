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
