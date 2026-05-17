---
date: "2026-05-15"
project: mosdat
topic: 2. **Cold-start launch command** — `nohup /opt/Rocket.Chat/rocketchat-desktop --no-sandbox --disable-gpu
kind: decision
scope: project-shared
confidence: low
---

1. **Toggle persistence via interactive pre-flight** — did not attempt to guess the Redux persistence key in `PersistableValues.ts`. The pragmatic fallback (launch, enable, kill with TERM for graceful Redux flush) is deterministic and reuses the exact localize pattern from `3325-auto-and-single-server.yaml` Phase B. The TERM-first kill loop is the canonical path that triggers Electron's `app.on('quit')` → Redux persist flush.

2. **Cold-start launch command** — `nohup /opt/Rocket.Chat/rocketchat-desktop --no-sandbox --disable-gpu --ozone-platform=x11 "tel:+5511999999999"` with `XAUTHORITY` set. Added `--disable-gpu --ozone-platform=x11` to the cold-start dispatch (missing from the brief's suggested command) because the QEMU VM has no Wayland display; without those flags the cold-started process would segfault (see KNOWN_ISSUES.md Fedora entry for same root cause on Ubuntu/Debian path).

3. **Negative assertion** — `nohup &lt;binary&gt; &lt;url&gt;` always launches the binary, so the negative test asserts behavioral ignoring (no dial pad, no modal, normal login page) rather than "RC does not start." Matching what the PR actually gates: when toggle OFF, `setAsDefaultProtocolClient` was never called and the deep-link handler is unregistered — the URL arg is simply dropped.

4. **`verify_timeout: 30, retries: 5`** for all cold-start verifies per findings.md guidance ("For launch: `verify_timeout: 25-30 + retries: 0` (deterministic)" note overridden here with retries: 5 because cold start on QEMU has high variance, not because the outcome is nondeterministic).
