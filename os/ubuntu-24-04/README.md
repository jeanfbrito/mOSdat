# Ubuntu 24.04 Test Scripts

## VM Configuration

| Setting | Value |
|---------|-------|
| VM ID | 102 |
| Name | ubuntu-24.04 |
| OS | Ubuntu 24.04.3 LTS |
| Desktop | GNOME 46 / Wayland |
| User | jean |
| IP | 192.168.13.82 |
| RAM | 8 GB |
| CPU | 8 cores |
| Disk | 64 GB |

## Status: CONFIGURED AND TESTED

All three package formats (DEB, AppImage, Snap) have been tested and pass the Wayland fix validation.

## Test Results (2026-01-19)

| Package | x11 | wayland | wayland-fake | fallback | no-display |
|---------|:---:|:-------:|:------------:|:--------:|:----------:|
| DEB | PASS | SKIP | PASS | PASS | EXPECTED |
| AppImage | PASS | PASS | PASS | PASS | PASS |
| Snap | PASS | PASS | PASS | PASS | EXPECTED |

Legend: PASS = Works | SKIP = Weston limitation | EXPECTED = Acceptable crash (no display)

## Scripts

| Script | Purpose |
|--------|---------|
| `build.sh` | Build Rocket.Chat from current git checkout |
| `deploy.sh` | Transfer and install DEB on VM |
| `gpu-control.sh` | Attach/detach GPU from VM |

## Usage

### Full Test Matrix (Python runner — canonical)

```bash
mosdat run examples/rocketchat.toml --only ubuntu2404
```

### Test Specific Package

```bash
# Pre-built package via Python runner (canonical)
mosdat test examples/rocketchat.toml /path/to/rocketchat-*.deb --vms ubuntu2404

# AppImage
mosdat test examples/rocketchat.toml /path/to/rocketchat-*.AppImage --vms ubuntu2404

# Snap — deploy manually, then run tests
scp /path/to/*.snap jean@192.168.13.82:/tmp/
ssh jean@192.168.13.82 "sudo snap install --dangerous /tmp/*.snap"
ssh jean@192.168.13.82 "APP_PATH=/snap/bin/rocketchat-desktop /tmp/tests/run-all.sh"
```

## Ubuntu 24.04 Specific Notes

- Uses GNOME 46 with improved Wayland support
- GDM3 config path: `/etc/gdm3/custom.conf`
- Default session is Wayland (unlike 22.04 which defaulted to X11)
- Snap packages work well with Wayland on 24.04
