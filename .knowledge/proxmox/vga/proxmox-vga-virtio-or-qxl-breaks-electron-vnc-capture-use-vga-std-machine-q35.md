---
date: "2026-05-02"
project: mOSdat
tags:
  - mosdat
  - proxmox
  - vga
  - vnc
  - electron
topic: Proxmox vga=virtio or qxl breaks Electron VNC capture — use vga=std + machine=q35
kind: lesson
scope: project-shared
category: proxmox/vga
confidence: high
---

## Lesson (2026-05-01 ubuntu2204, 2026-05-02 manjaro/fedora42)
QEMU VNC fails to capture Electron app windows on Linux guests when:
- `vga=virtio` — virtio-gpu uses dmabuf channels that the QEMU VNC server can't read. Framebuffer shows X server output but Electron windows are invisible.
- `vga=qxl` — same family of issue on KDE Wayland; partial capture, intermittent black framebuffer.

## Fix
Set `vga=std` + `machine=q35` on every Linux smoke VM. std is plain Cirrus-style framebuffer; q35 is the modern PCIe chipset. Validated working: ubuntu2204, ubuntu2404, fedora42, manjaro, opensuse, windows10, windows11.

```python
# Apply via Proxmox API on stopped VM
api._request("POST", f".../qemu/{vmid}/config", data={"vga": "std", "machine": "q35"})
```

## Tradeoff
No GPU acceleration in guest. Acceptable for VNC-driven UI tests. If guest needs GPU passthrough for the test itself, a different capture path is needed.

## Takeaway
For headless GUI testing through QEMU VNC, vga=std is the only safe default. Other vga modes silently degrade Electron capture.
