---
date: "2026-04-14"
project: mosdat
topic: Discovered llama-swap proxy (not vLLM) at 192.168.13.62:5001 — uses GGUF models
kind: insight
scope: project-shared
confidence: medium
---

5. Problem Solving:
   - Confirmed SSH works on Win11 after setup script ran
   - Discovered llama-swap proxy (not vLLM) at 192.168.13.62:5001 — uses GGUF models
   - Confirmed SSH-only approach for screenshots and input injection works on Windows
   - Holo2 localization API tested successfully: screenshot → `{"x":508,"y":607}`
   - Current blocker: screenshot.py `_PS_CAPTURE` script needs fix — verify file was actually saved before reporting "OK:"
   - User raised valid question: Proxmox VNC API might be better than SSH BitBlt for screenshots
