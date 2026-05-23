"""Temporary debug test to capture PIL.Image state in full suite.

Placed BEFORE cursor_motion_integration in collection order (alphabetically
'_debug' < 'test_cursor').
"""
import sys
import json
from pathlib import Path

def test_debug_pil_state_early():
    """Check PIL state before cursor_motion_integration tests run."""
    pil_img = sys.modules.get("PIL.Image")
    pil_pkg = sys.modules.get("PIL")
    info = {
        "PIL.Image_in_sys": str(pil_img),
        "PIL.Image.__file__": getattr(pil_img, "__file__", "no __file__") if pil_img else None,
        "PIL.Image.has_new": hasattr(pil_img, "new") if pil_img else False,
        "PIL_pkg.Image_attr": str(getattr(pil_pkg, "Image", "MISSING")),
        "PIL_pkg.Image_has_new": hasattr(getattr(pil_pkg, "Image", None), "new") if pil_pkg else False,
    }
    Path("/tmp/pil_debug_early.json").write_text(json.dumps(info, indent=2))
    assert True
