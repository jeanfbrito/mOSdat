"""Prompt constants and image encoding helpers for the VLM client."""

import base64
from io import BytesIO

from PIL import Image

# Models specialized for localization that hallucinate on yes/no verify prompts.
_LOC_PREFIXES = ("holo2", "ui-tars", "osatlas", "seeclick", "cogvlm-cogagent")

_LOCALIZE_PROMPT = (
    "Localize an element on the GUI image according to the provided target "
    "and output a click position.\n"
    " * You must output a valid JSON following the format: "
    '{"x": <integer 0-1000>, "y": <integer 0-1000>}\n'
    "Your target is:"
)

_VERIFY_PROMPT = "Look at this screenshot carefully. Answer only 'yes' or 'no': "

_BOUNDS_CLARIFICATION = (
    "The element is on the visible screen, not off-screen. Coordinates must "
    "fit within the image bounds.\n\n"
)

_DESCRIBE_ELEMENT_PROMPT = (
    "You are writing a reusable UI target prompt for automation. "
    "Describe only the stable element at the center of this image. "
    "Use durable visual semantics: role, visible static label text, icon shape, color, and container. "
    "Do not mention time, date, timestamps, message order, temporary text, usernames, file names, "
    "or position relative to transient neighbors. "
    "If surrounding context is needed, use stable container or section names only. "
    "Return 6-14 words. Output only the reusable target prompt."
)


def _encode_image(img: Image.Image) -> str:
    """Encode a PIL image as base64 PNG."""
    buf = BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()
