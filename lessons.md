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

## Recording / VNC capture

### Frame-diff filter: max-pixel not mean (Cost: ~45min)
**What happened**: GIFs recorded by `mosdat functional --record-gif` looked empty. raw=103, filtered=7. Cursor motion frames dropped.
**Root cause**: `mean_abs_diff` on 64×64 grayscale thumbnails with default threshold 3.0. A cursor moving 1-2 pixels on a 1280×720 frame is sub-pixel on the 64×64 thumb; bilinear blurs to ~0.06 intensity mean — way below threshold.
**What solved it**: Switch metric to `max_abs_diff` (`ImageStat.Stat(diff).extrema[0][1]`); bump thumb to 256×256 (cursor lands as 3-6px sprite, max diff ~100+); default threshold 1.0 (drop only pixel-identical frames).
**Rule**: When filtering for "did anything visible change", use MAX or count-of-changed-pixels, not MEAN. Mean averages sparse motion into the noise floor.
