"""Interactive scenario authoring — VNC viewer + click-to-step recording.

Launch via:
    mosdat functional --record --vms <vm> --output new.yaml --config <cfg>

The window streams the VM's live VNC framebuffer at 500 ms cadence.
Clicking any point:
  1. Pauses the live feed and captures the frame.
  2. Asks the VLM to describe the element under the cursor.
  3. Presents an editable description dialog.
  4. Asks what action to record (click / type / key).
  5. Appends a step dict to self.steps and refreshes the side panel.

Layout:
  ┌────────────────────────────┬─────────────────┐
  │                            │  Step list      │
  │   VNC framebuffer          │  ─────────────  │
  │   (live, polled 500ms)     │  1. localize:   │
  │   click captures coord     │     "..."       │
  │                            │  2. type: "..." │
  │                            │  ...            │
  │                            ├─────────────────┤
  │                            │ [Save YAML]     │
  │                            │ [Reload frame]  │
  │                            │ [Add wait step] │
  └────────────────────────────┴─────────────────┘
"""

import sys
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from PIL import Image

from PyQt6.QtCore import Qt, QTimer, QSize, pyqtSignal
from PyQt6.QtGui import QPixmap, QImage, QPainter, QColor, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QLineEdit,
    QComboBox,
    QInputDialog,
    QMessageBox,
    QTextEdit,
    QStatusBar,
    QFileDialog,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
)

from .vlm.client import VLMClient
from .transport.vnc import VncClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pil_to_qpixmap(img: Image.Image) -> QPixmap:
    """Convert a PIL RGB image to a QPixmap."""
    rgb = img.convert("RGB")
    data = rgb.tobytes("raw", "RGB")
    qimg = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimg)


# ---------------------------------------------------------------------------
# Action dialog
# ---------------------------------------------------------------------------

class _ActionDialog(QDialog):
    """Let the author choose what to do after clicking the element."""

    def __init__(self, description: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Record step action")
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)

        form = QFormLayout()

        # Description (editable)
        self._desc_edit = QLineEdit(description)
        form.addRow("Element description:", self._desc_edit)

        # Action type combo
        self._action_combo = QComboBox()
        self._action_combo.addItems([
            "click only",
            "click then type",
            "click then press key",
        ])
        self._action_combo.currentIndexChanged.connect(self._on_action_changed)
        form.addRow("Action:", self._action_combo)

        # Text/key value
        self._value_label = QLabel("Type text:")
        self._value_edit = QLineEdit()
        self._value_edit.setPlaceholderText("text to type…")
        form.addRow(self._value_label, self._value_edit)

        # Retries
        self._retries_edit = QLineEdit("3")
        form.addRow("Retries:", self._retries_edit)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._on_action_changed(0)

    def _on_action_changed(self, idx: int):
        labels = ["", "Type text:", "Key name:"]
        placeholders = ["", "text to type…", "enter / tab / esc…"]
        self._value_label.setText(labels[idx])
        self._value_edit.setPlaceholderText(placeholders[idx])
        self._value_edit.setEnabled(idx > 0)
        if idx == 0:
            self._value_edit.clear()

    def result_step(self) -> dict:
        """Build a step dict from dialog values."""
        desc = self._desc_edit.text().strip() or "unknown element"
        action = self._action_combo.currentIndex()
        value = self._value_edit.text().strip()
        try:
            retries = int(self._retries_edit.text().strip())
        except ValueError:
            retries = 3

        step: dict = {"localize": desc, "retries": retries}
        if action == 1 and value:
            step["then_type"] = value
        elif action == 2 and value:
            step["then_key"] = value
        return step


# ---------------------------------------------------------------------------
# Framebuffer label (click-aware)
# ---------------------------------------------------------------------------

class _FramebufferLabel(QLabel):
    """A QLabel that emits (image_x, image_y) on left-click, accounting for scaling."""

    clicked = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_size: tuple[int, int] = (1, 1)  # (w, h) of the actual image

    def set_image_size(self, w: int, h: int):
        self._img_size = (w, h)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            lw, lh = self.width(), self.height()
            iw, ih = self._img_size
            if iw == 0 or ih == 0:
                return
            # Qt scales the pixmap to fit the label preserving aspect ratio.
            scale = min(lw / iw, lh / ih)
            rendered_w = int(iw * scale)
            rendered_h = int(ih * scale)
            offset_x = (lw - rendered_w) // 2
            offset_y = (lh - rendered_h) // 2
            px = event.position().x() - offset_x
            py = event.position().y() - offset_y
            if 0 <= px <= rendered_w and 0 <= py <= rendered_h:
                img_x = int(px / scale)
                img_y = int(py / scale)
                self.clicked.emit(img_x, img_y)
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class RecorderWindow(QMainWindow):
    """Live VNC viewer with click-to-record support."""

    def __init__(
        self,
        vnc: VncClient,
        vlm: VLMClient,
        output_path: Optional[Path] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._vnc = vnc
        self._vlm = vlm
        self._output_path = output_path
        self.steps: list[dict] = []
        self._last_frame: Optional[Image.Image] = None
        self._paused = False

        self.setWindowTitle("mosdat recorder")
        self._build_ui()
        self._start_polling()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)

        # Left: framebuffer
        self._fb_label = _FramebufferLabel()
        self._fb_label.setMinimumSize(800, 600)
        self._fb_label.setStyleSheet("background: #111;")
        self._fb_label.clicked.connect(self._on_click)
        root.addWidget(self._fb_label, stretch=3)

        # Right: controls
        right = QVBoxLayout()
        right.setContentsMargins(4, 4, 4, 4)

        right.addWidget(QLabel("<b>Recorded steps</b>"))

        self._steps_view = QTextEdit()
        self._steps_view.setReadOnly(True)
        self._steps_view.setMinimumWidth(260)
        right.addWidget(self._steps_view, stretch=1)

        btn_save = QPushButton("Save YAML")
        btn_save.clicked.connect(self._save_yaml)
        right.addWidget(btn_save)

        btn_reload = QPushButton("Reload frame")
        btn_reload.clicked.connect(self._reload_frame)
        right.addWidget(btn_reload)

        btn_wait = QPushButton("Add wait step")
        btn_wait.clicked.connect(self._add_wait)
        right.addWidget(btn_wait)

        root.addLayout(right, stretch=1)

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("Connecting…")

    # ------------------------------------------------------------------
    # Framebuffer polling
    # ------------------------------------------------------------------

    def _start_polling(self):
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_frame)
        self._timer.start(500)

    def _poll_frame(self):
        if self._paused:
            return
        try:
            img, size = self._vnc.capture()
        except Exception as e:
            logger.warning("VNC capture failed: %s", e)
            self._status.showMessage(f"Capture error: {e}")
            return
        self._last_frame = img
        self._fb_label.set_image_size(*size)
        pix = _pil_to_qpixmap(img)
        self._fb_label.setPixmap(
            pix.scaled(
                self._fb_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        n = len(self.steps)
        w, h = size
        self._status.showMessage(f"Connected · {w}×{h} · {n} step{'s' if n != 1 else ''}")

    def _reload_frame(self):
        """Force an immediate frame refresh and resume polling if paused."""
        self._paused = False
        self._poll_frame()

    # ------------------------------------------------------------------
    # Click handling
    # ------------------------------------------------------------------

    def _on_click(self, img_x: int, img_y: int):
        if self._last_frame is None:
            return

        # Freeze feed so coords stay aligned while dialogs are open
        self._paused = True
        screenshot = self._last_frame.copy()

        # Ask VLM to describe the element
        desc = ""
        try:
            self._status.showMessage("Asking VLM to describe element…")
            QApplication.processEvents()
            desc = self._vlm.describe_element(screenshot, img_x, img_y)
        except Exception as e:
            logger.warning("describe_element failed: %s", e)
            desc = ""

        # Show action dialog
        dlg = _ActionDialog(desc, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            self._paused = False
            return

        step = dlg.result_step()
        self.steps.append(step)
        self._refresh_steps()
        self._paused = False
        self._status.showMessage(f"Step {len(self.steps)} recorded.")

    # ------------------------------------------------------------------
    # Side-panel refresh
    # ------------------------------------------------------------------

    def _refresh_steps(self):
        lines = []
        for i, step in enumerate(self.steps, 1):
            if "wait" in step:
                lines.append(f"{i}. wait: {step['wait']}s")
            else:
                loc = step.get("localize", "?")
                line = f"{i}. localize: \"{loc}\""
                if "then_type" in step:
                    line += f"\n   then_type: \"{step['then_type']}\""
                elif "then_key" in step:
                    line += f"\n   then_key: \"{step['then_key']}\""
                lines.append(line)
        self._steps_view.setPlainText("\n\n".join(lines))

    # ------------------------------------------------------------------
    # Wait step
    # ------------------------------------------------------------------

    def _add_wait(self):
        secs, ok = QInputDialog.getDouble(
            self, "Add wait step", "Seconds to wait:", value=2.0, min=0.1, max=60.0, decimals=1
        )
        if ok:
            self.steps.append({"wait": round(secs, 1)})
            self._refresh_steps()

    # ------------------------------------------------------------------
    # YAML save
    # ------------------------------------------------------------------

    def _save_yaml(self):
        default = str(self._output_path) if self._output_path else ""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save recorded scenario",
            default,
            "YAML files (*.yaml *.yml);;All files (*)",
        )
        if not path:
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        doc = {
            "name": f"Recorded scenario {now}",
            "steps": self.steps,
        }

        # Dump with a header comment
        header = (
            f"# mosdat recorded scenario\n"
            f"# Created: {now}\n"
            f"# Steps: {len(self.steps)}\n"
        )
        body = yaml.dump(doc, allow_unicode=True, sort_keys=False, default_flow_style=False)
        full = header + body

        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(full, encoding="utf-8")
        QMessageBox.information(self, "Saved", f"Scenario written to:\n{out}")
        self._status.showMessage(f"Saved: {out}")
