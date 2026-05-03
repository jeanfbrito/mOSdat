"""Interactive browser authoring sessions for functional scenarios."""

from __future__ import annotations

import base64
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml
from PIL import Image


def image_payload(image: Image.Image, size: tuple[int, int]) -> dict:
    data = _bmp_bytes(image.convert("RGB"), size)
    return {
        "image": base64.b64encode(data).decode("ascii"),
        "width": size[0],
        "height": size[1],
        "content_type": "image/bmp",
    }


def _bmp_bytes(image: Image.Image, size: tuple[int, int]) -> bytes:
    width, height = size
    pixels = image.tobytes()
    row_stride = ((width * 3 + 3) // 4) * 4
    pixel_size = row_stride * height
    file_size = 54 + pixel_size
    header = bytearray()
    header.extend(b"BM")
    header.extend(file_size.to_bytes(4, "little"))
    header.extend((0).to_bytes(4, "little"))
    header.extend((54).to_bytes(4, "little"))
    header.extend((40).to_bytes(4, "little"))
    header.extend(width.to_bytes(4, "little", signed=True))
    header.extend(height.to_bytes(4, "little", signed=True))
    header.extend((1).to_bytes(2, "little"))
    header.extend((24).to_bytes(2, "little"))
    header.extend((0).to_bytes(4, "little"))
    header.extend(pixel_size.to_bytes(4, "little"))
    header.extend((2835).to_bytes(4, "little", signed=True))
    header.extend((2835).to_bytes(4, "little", signed=True))
    header.extend((0).to_bytes(4, "little"))
    header.extend((0).to_bytes(4, "little"))
    rows = []
    padding = b"\x00" * (row_stride - width * 3)
    for y in range(height - 1, -1, -1):
        start = y * width * 3
        row = pixels[start:start + width * 3]
        bgr = bytearray()
        for i in range(0, len(row), 3):
            r, g, b = row[i], row[i + 1], row[i + 2]
            bgr.extend((b, g, r))
        rows.append(bytes(bgr) + padding)
    return bytes(header) + b"".join(rows)


@dataclass
class AuthorSession:
    id: str
    vm_name: str
    vnc: object
    vlm: object
    injector: object
    draft_steps: list[dict] = field(default_factory=list)
    last_image: Optional[Image.Image] = None
    last_size: Optional[tuple[int, int]] = None
    created_at: float = field(default_factory=time.time)

    def capture(self) -> dict:
        image, size = self.vnc.capture()
        self.last_image = image
        self.last_size = size
        return image_payload(image, size)

    def require_frame(self) -> tuple[Image.Image, tuple[int, int]]:
        if self.last_image is None or self.last_size is None:
            self.capture()
        assert self.last_image is not None and self.last_size is not None
        return self.last_image, self.last_size

    def localize(self, prompt: str) -> dict:
        image, size = self.require_frame()
        x, y = self.vlm.localize(image, prompt, size)
        return {"prompt": prompt, "x": x, "y": y, "width": size[0], "height": size[1]}

    def verify(self, question: str) -> dict:
        image, _ = self.require_frame()
        started = time.perf_counter()
        answer = bool(self.vlm.verify(image, question))
        return {
            "question": question,
            "answer": "yes" if answer else "no",
            "ok": answer,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }

    def run_action(self, action: str, payload: dict) -> dict:
        if not payload.get("confirm"):
            raise ValueError("action requires confirm=true")
        if action == "click":
            self.injector.click(int(payload["x"]), int(payload["y"]))
            step = {"localize": payload.get("prompt", "manual click"), "retries": 1}
        elif action == "type":
            self.injector.type_text(str(payload["text"]))
            step = {"type": str(payload["text"])}
        elif action == "key":
            self.injector.key(str(payload["key"]))
            step = {"key": str(payload["key"])}
        elif action == "shell":
            self.injector.shell(str(payload["cmd"]))
            step = {"shell": str(payload["cmd"])}
        elif action == "wait":
            seconds = int(payload.get("seconds", 1))
            time.sleep(max(0, min(seconds, 10)))
            step = {"wait": seconds}
        elif action == "launch":
            step = {"launch": str(payload["cmd"]), "wait": int(payload.get("wait", 0))}
            self.injector.shell(str(payload["cmd"]))
        else:
            raise ValueError(f"unsupported action: {action}")
        self.draft_steps.append(step)
        return {"action": action, "step": step, "draft_steps": self.draft_steps}

    def append_step(self, step: dict) -> dict:
        self.draft_steps.append(step)
        return {"draft_steps": self.draft_steps}

    def update_steps(self, steps: list[dict]) -> dict:
        self.draft_steps = steps
        return {"draft_steps": self.draft_steps}

    def export_yaml(self, name: str = "authored-scenario") -> str:
        return yaml.safe_dump({"name": name, "steps": self.draft_steps}, sort_keys=False)

    def close(self) -> None:
        close = getattr(self.vnc, "close", None)
        if close:
            close()


class AuthorManager:
    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path
        self.sessions: dict[str, AuthorSession] = {}

    def create_session(self, vm_name: str, model: Optional[str] = None, verify_model: Optional[str] = None) -> dict:
        if not self.config_path:
            raise ValueError("authoring requires mosdat live --config <config.toml>")
        session = self._create_real_session(vm_name, model=model, verify_model=verify_model)
        self.sessions[session.id] = session
        return self.describe(session)

    def describe(self, session: AuthorSession) -> dict:
        return {"session_id": session.id, "vm": session.vm_name, "draft_steps": session.draft_steps}

    def get(self, session_id: str) -> AuthorSession:
        try:
            return self.sessions[session_id]
        except KeyError as exc:
            raise ValueError("unknown author session") from exc

    def close(self, session_id: str) -> dict:
        session = self.get(session_id)
        session.close()
        del self.sessions[session_id]
        return {"closed": session_id}

    def _create_real_session(
        self,
        vm_name: str,
        model: Optional[str] = None,
        verify_model: Optional[str] = None,
    ) -> AuthorSession:
        from automation.config import load_config
        from automation.proxmox.api import ProxmoxAPI
        from automation.transport.ssh import SSHClient
        from automation.transport.vnc import VncClient
        from automation.vlm.client import VLMClient
        from automation.vlm.input import InputInjector

        config = load_config(self.config_path)
        vm = next((candidate for candidate in config.vms if candidate.name == vm_name), None)
        if vm is None:
            raise ValueError(f"unknown VM: {vm_name}")
        proxmox = ProxmoxAPI(config.proxmox)
        vnc = VncClient(proxmox, vmid=vm.vmid)
        enter = getattr(vnc, "__enter__", None)
        if enter:
            vnc = enter()
        ssh = SSHClient(vm.ip, vm.user)
        vlm = VLMClient(
            base_url=config.vlm.base_url,
            model=model or config.vlm.model,
            verify_model=verify_model or config.vlm.verify_model or None,
        )
        return AuthorSession(
            id=uuid.uuid4().hex,
            vm_name=vm.name,
            vnc=vnc,
            vlm=vlm,
            injector=InputInjector(vnc, ssh, vm.is_windows),
        )
