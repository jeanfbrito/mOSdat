# Windows 10 Test Scripts

## VM Configuration

| Setting | Value |
|---------|-------|
| VM ID | 104 |
| Name | windows-10 |
| OS | Windows 10 22H2 |
| User | jean |
| Password | cb6wist3 |

## Status: TEMPLATE - NOT YET CONFIGURED

Windows testing is fundamentally different from Linux Wayland testing.

## Purpose

The Wayland/X11 crash fix is Linux-specific. Windows testing verifies:
- General app functionality
- No regressions from the fix
- GPU passthrough works

## Prerequisites

### VirtIO Drivers

Windows needs VirtIO drivers for optimal performance:
1. Attach `virtio-win.iso` as secondary CD
2. Install drivers during/after Windows setup

### Guest Agent

Install QEMU Guest Agent from VirtIO ISO:
```
D:\guest-agent\qemu-ga-x64.msi
```

### Enable Auto-login

```
netplwiz
# Uncheck "Users must enter a username and password"
```

Or via registry:
```
reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v AutoAdminLogon /t REG_SZ /d 1 /f
reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v DefaultUserName /t REG_SZ /d jean /f
reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v DefaultPassword /t REG_SZ /d cb6wist3 /f
```

### Enable SSH (Optional)

Windows 10 has built-in OpenSSH:
```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
```

## Package Format

Windows uses NSIS installer (`.exe`) or MSI:
```bash
yarn electron-builder --publish never --win nsis
```

## Testing Approach

Windows tests are different - no Wayland:

1. **Smoke Test**: App launches and shows main window
2. **GPU Test**: App uses GPU acceleration
3. **Crash Test**: No crashes on various scenarios

## Scripts

Windows scripts use PowerShell instead of Bash. Remote execution via:
- SSH (if enabled)
- WinRM
- Proxmox QEMU Guest Agent exec
