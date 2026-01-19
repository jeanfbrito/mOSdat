# Storage Configuration

## ISO Storage (mushu-isos)

Proxmox uses a CIFS/SMB share for ISO storage.

### Connection Details

| Setting | Value |
|---------|-------|
| Server | 192.168.13.11 |
| Share | isos |
| Username | guest |
| Password | (none) |
| Mount Point | /mnt/pve/mushu-isos |

### Accessing the Share

**From Linux:**
```bash
# Mount temporarily
sudo mount -t cifs //192.168.13.11/isos /mnt/mushu-isos -o guest

# Or use smbclient
smbclient //192.168.13.11/isos -N -c "ls"
```

**From Windows:**
```
\\192.168.13.11\isos
```

### ISO Location

ISOs must be placed in the `template/iso/` subdirectory:

```
//192.168.13.11/isos/
└── template/
    └── iso/
        ├── Fedora-Workstation-Live-42-1.1.x86_64.iso
        ├── ubuntu-22.04.2-desktop-amd64.iso
        ├── ubuntu-24.04.3-desktop-amd64.iso
        └── Leap-16.0-offline-installer-x86_64.install.iso  <-- PUT HERE
```

### Refreshing Storage in Proxmox

After adding new ISOs, Proxmox should auto-detect them. If not:

1. Web UI: Datacenter → Storage → mushu-isos → Click storage to refresh
2. Or wait ~60 seconds for auto-refresh

### Local Storage Alternative

For faster access, upload ISOs directly to Proxmox local storage:

1. Proxmox Web UI → pve → local (pve) → ISO Images → Upload
2. Path on server: `/var/lib/vz/template/iso/`
