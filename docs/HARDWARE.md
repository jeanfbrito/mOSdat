# Hardware Specifications

This document describes the hardware used in this test environment.

---

## Overview

```
┌─────────────────────────────────────────────────────────────┐
│           Host Machine (rocketlauncher)                      │
│           Ubuntu 24.04 / Xeon E5-2680 v4                    │
│           Runs: test scripts, builds                         │
│                            │                                 │
│                       Network                                │
│                     192.168.13.x                             │
│                            │                                 │
├────────────────────────────┼─────────────────────────────────┤
│           Proxmox Server (192.168.13.85)                     │
│           Proxmox VE 8.x / i7-12700H                        │
│           Runs: VMs, GPU passthrough                         │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                  │
│  │ VM 100   │  │ VM 101   │  │ VM 102+  │                  │
│  │ Fedora   │  │ Ubuntu   │  │ Others   │                  │
│  └──────────┘  └──────────┘  └──────────┘                  │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              NVIDIA RTX 3060 (VFIO)                   │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## Host Machine (Script Runner)

The machine that runs the test framework scripts.

| Component | Specification |
|-----------|---------------|
| **Hostname** | rocketlauncher |
| **OS** | Ubuntu 24.04.3 LTS (Noble Numbat) |
| **Kernel** | 6.8.0-90-generic |
| **CPU** | Intel Xeon E5-2680 v4 @ 2.40GHz |
| **Cores** | 14 cores / 28 threads |
| **RAM** | 32 GB |
| **Storage** | 1 TB (LVM) |
| **GPU** | NVIDIA GeForce GTX 970 (local, not used for tests) |
| **Network** | 192.168.13.x |

### Purpose

- Runs test framework scripts
- Builds Rocket.Chat Electron packages
- Communicates with Proxmox API
- Transfers packages to VMs via SSH

### Software Requirements

- Docker (for SSH operations)
- Node.js 18+ and Yarn
- curl, jq
- Git

---

## Proxmox Server (VM Host)

The hypervisor running test VMs.

| Component | Specification |
|-----------|---------------|
| **IP Address** | 192.168.13.85 |
| **OS** | Proxmox VE 8.x |
| **CPU** | Intel Core i7-12700H (14 cores / 20 threads) |
| **RAM** | 32 GB DDR4 |
| **Storage** | 1 TB NVMe SSD |
| **GPU** | NVIDIA GeForce RTX 3060 12GB (passthrough) |
| **Network** | 1 Gbps Ethernet |

### Purpose

- Hosts test VMs
- Provides GPU passthrough for GPU-specific tests
- Exposes REST API for automation

### VFIO Configuration

| Setting | Value |
|---------|-------|
| **IOMMU** | `intel_iommu=on iommu=pt` |
| **GPU Driver** | vfio-pci |
| **GPU PCI Address** | 0000:01:00 |
| **GPU PCI IDs** | 10de:2504 (VGA), 10de:228e (Audio) |

---

## Virtual Machines

All VMs run on the Proxmox server.

| VM ID | OS | vCPU | RAM | Disk | Status |
|:-----:|-----|:----:|:---:|:----:|--------|
| 100 | Fedora 42 | 8 | 8 GB | 64 GB | ✅ Complete |
| 101 | Ubuntu 22.04 | 8 | 8 GB | 64 GB | ✅ Complete |
| 102 | Ubuntu 24.04 | 8 | 8 GB | 64 GB | 📋 Template |
| 103 | Arch Linux | 8 | 8 GB | 64 GB | 📋 Template |
| 104 | Windows 10 | 8 | 8 GB | 64 GB | 📋 Template |
| 105 | Windows 11 | 8 | 8 GB | 64 GB | 📋 Template |

### VM Configuration

All Linux VMs share these settings:

| Setting | Value |
|---------|-------|
| **BIOS** | OVMF (UEFI) |
| **Machine** | q35 |
| **SCSI** | virtio-scsi-single |
| **Network** | virtio, bridge=vmbr0 |
| **Display** | VirtIO-GPU |
| **Guest Agent** | Enabled |

### VM Requirements

- QEMU Guest Agent installed
- SSH server enabled
- Auto-login configured (for Wayland session)

---

## Network

| Host | IP Address | Role |
|------|------------|------|
| rocketlauncher | 192.168.13.x | Script runner |
| Proxmox | 192.168.13.85 | VM host |
| mushu | 192.168.13.11 | NAS (ISOs) |
| VMs | DHCP | Test targets |

---

## Storage

### ISO Storage (mushu)

| ISO | Size | Purpose |
|-----|------|---------|
| Fedora-Workstation-Live-42-1.1.x86_64.iso | ~2 GB | Fedora 42 install |
| ubuntu-22.04.2-desktop-amd64.iso | ~4 GB | Ubuntu 22.04 install |
| ubuntu-24.04.3-desktop-amd64.iso | ~5 GB | Ubuntu 24.04 install |
| virtio-win-0.1.190-1.iso | ~500 MB | Windows drivers |
| Win10_22H2_EnglishInternational_x64v1.iso | ~5 GB | Windows 10 install |
