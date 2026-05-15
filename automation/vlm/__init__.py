"""VLM (vision-language model) client + input + screenshot facade.

Lazy attribute access avoids circular imports when test fixtures load
``automation.vlm.client`` via importlib spec hackery before the package
is fully initialized.
"""

from __future__ import annotations

__all__ = [
    "VLMClient",
    "VLMError",
    "InputInjector",
    "Screenshotter",
    "ScreenshotError",
]


def __getattr__(name: str):
    if name in ("VLMClient", "VLMError"):
        from .client import VLMClient, VLMError
        return {"VLMClient": VLMClient, "VLMError": VLMError}[name]
    if name == "InputInjector":
        from .input import InputInjector
        return InputInjector
    if name in ("Screenshotter", "ScreenshotError"):
        from .screenshot import Screenshotter, ScreenshotError
        return {"Screenshotter": Screenshotter, "ScreenshotError": ScreenshotError}[name]
    raise AttributeError(f"module 'automation.vlm' has no attribute {name!r}")
