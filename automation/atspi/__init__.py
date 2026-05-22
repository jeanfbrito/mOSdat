"""AT-SPI driver for mOSdat — coordinate-free Linux GUI automation.

Productized from the live-verified POC (`tests/_debug_atspi_poc*.py`). Drives
Rocket.Chat.Electron on Linux VMs via the AT-SPI2 accessibility bus,
replacing VLM-localize+VNC-click for steps where deterministic role/name
addressing is possible. Host-side: `AtspiClient`; VM-side: `worker.py`.
"""

from automation.atspi.client import AtspiClient, AtspiError

__all__ = ["AtspiClient", "AtspiError"]
