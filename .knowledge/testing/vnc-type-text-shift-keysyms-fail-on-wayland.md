# VNC type_text fails silently on shift-required keysyms (Wayland mutter VNC backend)

**Context**: Implementing canary-byte typing in functional runner. Tried `§` (U+00A7), then `~` (Shift+grave). Both produced no output — placeholder remained visible in screenshot, cursor present, no typed char committed.

**Insight**: Proxmox VNC stack feeds keysyms to QEMU → guest OS Wayland mutter. For unmodified ASCII (lowercase letters, digits, `.` `,` `/` etc.) keysym-only is honored. For shift-required ASCII (`~`, `!`, `@`, `#`, ...) and Latin-1 (`§`) the keysym-only path drops the keypress without error. Multi-char strings like `"rocketchat.jeanbrito.com"` still work because they only contain unmodified ASCII.

`automation/transport/vnc.py:type_text` DOES wrap shift around uppercase + `_SHIFTED` set — but the wrap is unreliable on this backend. Single-char shifted typing is the worst case.

**Implication**:
- Canary chars / probe keystrokes must be **no-shift ASCII** (lowercase letters, digits). Default canary char is `"q"` — not in any RC placeholder text, no shift required, lowercase letter shape clearly visible to VLM.
- When debugging "typing not appearing on screen" symptoms on a Wayland VM: first hypothesis is shift-required keysym, not VLM blindness or focus loss.
- If shift-required chars are essential, send `Shift_L` down + keysym + `Shift_L` up explicitly via `injector.key("shift+...")` rather than relying on `type_text` shift wrap.

**Affects**: `automation/runners/functional.py` (`_check_canary`), `shared/scenarios/functional/*.yaml` (canary_char field), any scenario step that types single shifted chars.
