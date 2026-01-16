# Windows 11 Test Scripts

## VM Configuration

| Setting | Value |
|---------|-------|
| VM ID | 105 |
| Name | windows-11 |
| OS | Windows 11 |
| User | jean |
| Password | cb6wist3 |

## Status: TEMPLATE - NEEDS ISO

Windows 11 ISO needs to be downloaded and added to Proxmox storage.

## Windows 11 Requirements

Windows 11 has stricter requirements than Windows 10:
- TPM 2.0 (can be emulated in Proxmox)
- Secure Boot (OVMF/UEFI)
- 4GB+ RAM
- 64GB+ storage

### Proxmox VM Settings

```
BIOS: OVMF (UEFI)
Machine: q35
TPM: Add TPM v2.0 device
EFI Disk: Required
CPU: host (for TPM support)
```

## Prerequisites

Same as Windows 10, plus:
- TPM enabled in VM settings
- May need to bypass hardware checks during install

### Bypass Hardware Checks (if needed)

During install, press Shift+F10 and run:
```
regedit
```
Navigate to `HKEY_LOCAL_MACHINE\SYSTEM\Setup` and create key `LabConfig`:
- `BypassTPMCheck` = 1 (DWORD)
- `BypassSecureBootCheck` = 1 (DWORD)
- `BypassRAMCheck` = 1 (DWORD)

## Testing

Same approach as Windows 10 - verify no regressions.
