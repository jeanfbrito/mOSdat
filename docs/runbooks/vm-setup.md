# VM Setup

← Back to [AGENTS.md](../../AGENTS.md)

## VM Setup (Required Before Testing)

Before testing a VM, ensure:

1. **SSH key access**: `ssh-copy-id jean@<VM_IP>` (password: see .env)
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
TICKET=$(curl -k -s -d "username=root@pam&password=cb6wist3" "https://192.168.13.85:8006/api2/json/access/ticket" | jq -r '.data.ticket')
curl -k -s -b "PVEAuthCookie=$TICKET" "https://192.168.13.85:8006/api2/json/nodes/pve/storage/mushu-isos/content" | jq -r '.data[] | .volid'
```

## Adding New OS

1. Copy existing OS folder
2. Update `config.sh` (VMID, package format)
3. Modify `deploy.sh` for package manager
4. Update `README.md`
