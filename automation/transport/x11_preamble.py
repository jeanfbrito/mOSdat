"""X11 environment preamble injection for SSH shell steps (I4).

When a VM is configured with ``x11 = "auto"`` in the TOML, every shell step
body that references a GUI tool (xdotool, wmctrl, etc.) gets a two-line
preamble injected before the body unless DISPLAY and XAUTHORITY are already
exported in that body.

The preamble is idempotent: calling ``inject`` on an already-injected body
returns the body unchanged.
"""

import re

# Sentinel that marks an already-injected body so we don't double-inject.
_PREAMBLE_SENTINEL = "XAUTH=$(ls /run/user/$(id -u)/.mutter-Xwaylandauth.*"

# The full preamble to prepend.
_PREAMBLE = (
    'XAUTH=$(ls /run/user/$(id -u)/.mutter-Xwaylandauth.*'
    ' /run/user/$(id -u)/gdm/Xauthority 2>/dev/null | head -1)\n'
    'export DISPLAY=:0 XAUTHORITY="$XAUTH"\n'
)

# Regex: shell commands that require an X11 environment.
_GUI_TOKENS_RE = re.compile(
    r"xdotool|wmctrl|xclip|xdg-mime|xdg-open|nohup\s+/opt/.*-desktop|xset",
    re.IGNORECASE,
)

# Regex: body already exports DISPLAY (any form).
_DISPLAY_RE = re.compile(r"export\s+.*\bDISPLAY\b", re.MULTILINE)

# Regex: body already exports XAUTHORITY (any form).
_XAUTHORITY_RE = re.compile(r"export\s+.*\bXAUTHORITY\b", re.MULTILINE)


def should_inject(body: str) -> bool:
    """Return True if the preamble should be prepended to *body*.

    Rules:
    - Body must reference at least one GUI token.
    - Body must NOT already export both DISPLAY and XAUTHORITY.
    - Body must NOT already contain the sentinel (already injected).
    """
    if _PREAMBLE_SENTINEL in body:
        return False  # already injected
    if not _GUI_TOKENS_RE.search(body):
        return False  # no GUI commands — nothing to do
    # Skip if both vars are already exported explicitly
    if _DISPLAY_RE.search(body) and _XAUTHORITY_RE.search(body):
        return False
    return True


def inject(body: str) -> str:
    """Return *body* with the X11 preamble prepended (idempotent)."""
    if not should_inject(body):
        return body
    return _PREAMBLE + body
