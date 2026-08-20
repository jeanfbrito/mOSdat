# VM Setup

← Back to [AGENTS.md](../../AGENTS.md)

## VM Setup (Required Before Testing)

Before testing a VM, ensure:

1. **SSH key access**: `ssh-copy-id <vm-user>@<VM_IP>` (password: use your local secret store).
   **Windows VMs**: `ssh-copy-id` does NOT work against Windows OpenSSH Server — it ships a
   POSIX `sh -c` script the Windows shell can't run. See
   [KNOWN_ISSUES.md](../KNOWN_ISSUES.md#windows-ssh-copy-id-fails-manual-key-install-required)
   for the manual fix.
2. **Passwordless sudo**: `echo "jean ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/jean`
3. **Test dependencies**: `Xvfb`, `weston` installed
   - Fedora: `sudo dnf install -y xorg-x11-server-Xvfb weston`
   - Ubuntu: `sudo apt install -y xvfb weston`
   - openSUSE: `sudo zypper install -y xvfb weston`
   - Manjaro/Arch: `sudo pacman -S xorg-server-xvfb weston`

## ISO Storage Management

ISOs are stored on a CIFS/SMB share. Proxmox only sees ISOs in `template/iso/` subfolder.

| Setting | Value |
|---------|-------|
| Server | 192.168.13.11 |
| Share | isos |
| Username | guest (no password) |
| Required path | `template/iso/` |

**To list ISOs:**
```bash
docker run --rm alpine sh -c "apk add --no-cache samba-client >/dev/null 2>&1 && smbclient //192.168.13.11/isos -N -c 'cd template/iso; ls'"
```

**To move ISO to correct location:**
```bash
docker run --rm alpine sh -c "apk add --no-cache samba-client >/dev/null 2>&1 && smbclient //192.168.13.11/isos -N -c 'rename \"filename.iso\" \"template/iso/filename.iso\"'"
```

**Check Proxmox sees it:**
```bash
mosdat validate examples/rocketchat.toml
mosdat list-vms examples/rocketchat.toml
```

## Adding New OS

1. Add a `[[vm]]` block to the relevant TOML config.
2. Add one or more `[[vm.package]]` entries for supported package formats.
3. Confirm SSH, desktop auto-login, and qemu-guest-agent are working.
4. Run `mosdat validate <config>` and a bounded functional smoke with `--until-step`.
