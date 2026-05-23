"""Windows UIA driver for mOSdat — coordinate-free Windows GUI automation.

Analog of `automation/atspi/` (Linux GNOME AT-SPI2 bus). Drives
Rocket.Chat.Electron on Windows VMs via the Microsoft UI Automation tree
using the pywinauto backend, replacing VLM-localize+VNC-click for steps
where deterministic role/name addressing is possible.

Host-side: `UiaClient` (pure-stdlib; talks SSH to the VM).
VM-side: `worker.py` (one-shot, imports pywinauto/comtypes/pywin32 — only
runs on the Windows VM).

The op-batch JSON protocol mirrors the AT-SPI driver one-for-one: `find`,
`do_action`, `verify`, `tree_dump`, `get_at_point`, `wait_for`. Result
shapes are interchangeable so the runner-side dispatch stays identical
across OSes (see `automation/runners/functional_steps.py`).

Key architectural difference vs the Linux driver:
  * On Linux, pointer-mode cursor motion is generated host-side via VNC
    button events (`InputInjector._position_cursor` → `vnc.click`).
  * On Windows, pywinauto can move the OS cursor directly from inside the
    worker (`pywinauto.mouse.move(...)` / `mouse.click(...)`). So the
    Windows pointer-mode click is a SINGLE op-batch — find + move + verify
    + click happen on the VM in one round-trip. No host-side input
    injector required. The client still accepts the `input_injector=`
    kwarg for symmetry with `AtspiClient` callers but ignores it.
"""

from automation.uia.client import UiaClient, UiaError

__all__ = ["UiaClient", "UiaError"]
