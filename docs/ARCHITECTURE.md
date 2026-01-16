# Test Framework Architecture

## Overview

This framework tests Rocket.Chat Electron's Wayland/X11 handling across multiple operating systems using Proxmox VMs with optional GPU passthrough.

## Components

```
┌─────────────────────────────────────────────────────────────┐
│                     Host Machine                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              Test Framework Scripts                  │    │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │    │
│  │  │ build.sh │  │ deploy.sh│  │ test.sh          │   │    │
│  │  └────┬─────┘  └────┬─────┘  └────────┬─────────┘   │    │
│  │       │             │                 │             │    │
│  │       ▼             ▼                 ▼             │    │
│  │  ┌──────────────────────────────────────────────┐   │    │
│  │  │            Proxmox API (REST)                │   │    │
│  │  └──────────────────────────────────────────────┘   │    │
│  └─────────────────────────────────────────────────────┘    │
│                            │                                 │
└────────────────────────────┼─────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                    Proxmox VE Server                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  VM 100     │  │  VM 101     │  │  VM 102-105         │  │
│  │  Fedora 42  │  │  Ubuntu     │  │  (Other VMs)        │  │
│  │  + GPU?     │  │  22.04      │  │                     │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              NVIDIA RTX 3060 (VFIO)                 │    │
│  │              Can be attached to any VM              │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

## Data Flow

### Build Phase

```
1. Git checkout specific version
2. yarn build (TypeScript → JavaScript)
3. electron-builder (→ RPM/DEB/AppImage)
4. Package stored in dist/
```

### Deploy Phase

```
1. Get VM IP via Proxmox guest agent API
2. SCP package to VM /tmp/
3. SSH: sudo dnf/apt install package
4. Verify installation
```

### Test Phase

```
1. SSH into VM
2. Set environment variables (WAYLAND_DISPLAY, etc.)
3. Run rocketchat-desktop with timeout
4. Capture exit code and output
5. Parse results (PASS/FAIL/SEGFAULT)
```

## Test Cases

| Test | Environment | Expected (Fixed) |
|------|-------------|------------------|
| wayland-real | Valid Wayland socket | Native Wayland |
| wayland-fake | Non-existent socket | X11 fallback (no crash) |
| wayland-nodisp | No WAYLAND_DISPLAY | X11 fallback |
| x11 | X11 session | X11 |

## Exit Code Interpretation

| Code | Meaning | Result |
|------|---------|--------|
| 0 | Clean exit | PASS |
| 124 | Timeout (app ran N seconds) | PASS |
| 139 | SIGSEGV (segfault) | FAIL |
| 134 | SIGABRT | FAIL |
| 6 | SIGABRT (alternate) | FAIL |
| Other | Unknown | UNKNOWN |

## GPU Passthrough

### Compute Mode (Default)

```
hostpci0=0000:01:00,pcie=1
```

- GPU available for compute/rendering
- VNC console still works
- Recommended for testing

### Primary Mode

```
hostpci0=0000:01:00,pcie=1,x-vga=1
```

- GPU is primary display
- VNC console blank
- Requires physical monitor
- Not recommended for automated testing

## File Organization

```
test-framework/
├── shared/              # Cross-OS utilities
│   ├── config.sh        # Credentials, paths
│   └── proxmox-api.sh   # API helper functions
├── os/
│   └── <os-version>/    # OS-specific scripts
│       ├── README.md    # OS setup instructions
│       ├── config.sh    # OS-specific config (VMID, etc.)
│       ├── build.sh     # Build for this OS
│       ├── deploy.sh    # Deploy to VM
│       ├── test.sh      # Run tests
│       ├── gpu-control.sh
│       └── full-test.sh
├── results/             # Test results by timestamp
└── docs/                # Documentation
```

## Why OS-Specific Scripts?

1. **Package formats differ**: RPM vs DEB vs AppImage vs MSI
2. **Package managers differ**: dnf vs apt vs pacman
3. **Paths differ**: /etc/gdm/ vs /etc/gdm3/
4. **Desktop versions differ**: GNOME 42 vs 46 vs 47
5. **Wayland implementations differ**: Mutter versions vary
6. **Reproducibility**: Each OS folder is a self-contained example
