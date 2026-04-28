"""Session browser dialog — pick a previous capture and re-open it.

Reads the captures directory via core.session_reader.list_sessions() and
emits the chosen Path through the `selected` signal so the caller can wire
it into the same CaptureViewDialog flow used after a fresh DONE.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from core.session_reader import SessionEntry, list_sessions


class SessionBrowserDialog(QDialog):
    """Modal picker for previously written sessions.

    Emits `selected(Path)` on accept. The caller calls `read_session()` and
    opens `CaptureViewDialog` themselves (keeps this widget Qt-only).
    """

    selected = Signal(Path)

    def __init__(self, captures_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Session Browser")
        self.resize(640, 420)
        self._captures_dir = Path(captures_dir)

        self._build_ui()
        self._reload()

    # ---------------- UI ----------------

    def _build_ui(self) -> None:
        vl = QVBoxLayout(self)
        vl.setContentsMargins(10, 10, 10, 10)
        vl.setSpacing(8)

        # Header row with location and refresh
        hdr = QHBoxLayout()
        self._dir_label = QLabel()
        self._dir_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setToolTip("Re-read the captures directory")
        self._refresh_btn.clicked.connect(self._reload)
        hdr.addWidget(QLabel("Location:"))
        hdr.addWidget(self._dir_label, 1)
        hdr.addWidget(self._refresh_btn)
        vl.addLayout(hdr)

        # List of sessions
        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(self._on_double_clicked)
        self._list.itemSelectionChanged.connect(self._update_open_btn)
        vl.addWidget(self._list, 1)

        # Status line — shown when no sessions exist
        self._status = QLabel()
        self._status.setStyleSheet("color: #888;")
        vl.addWidget(self._status)

        # OK / Cancel
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._open_btn = self._buttons.button(QDialogButtonBox.StandardButton.Open)
        self._open_btn.setText("Open")
        self._open_btn.setEnabled(False)
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)
        vl.addWidget(self._buttons)

    # ---------------- Behaviour ----------------

    def _reload(self) -> None:
        self._dir_label.setText(str(self._captures_dir.resolve())
                                if self._captures_dir.exists()
                                else f"{self._captures_dir} (does not exist)")
        self._list.clear()
        entries = list_sessions(self._captures_dir)
        for e in entries:
            item = QListWidgetItem(_format_entry(e))
            item.setData(Qt.ItemDataRole.UserRole, e.session_dir)
            self._list.addItem(item)

        if not entries:
            self._status.setText("No sessions found in this directory.")
        else:
            self._status.setText(f"{len(entries)} session(s).")
        self._update_open_btn()

    def _update_open_btn(self) -> None:
        self._open_btn.setEnabled(self._list.currentItem() is not None)

    def _on_double_clicked(self, _item: QListWidgetItem) -> None:
        self._on_accept()

    def _on_accept(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        path: Optional[Path] = item.data(Qt.ItemDataRole.UserRole)
        if path is None:
            return
        self.selected.emit(Path(path))
        self.accept()


def _format_entry(e: SessionEntry) -> str:
    """Single-line label: timestamp + sample counts."""
    when = _format_timestamp(e.session_id)
    return (
        f"{e.session_id}    {when}    "
        f"ADE9000: {e.arduino_samples} samples    "
        f"Distribution: {e.dist_samples} samples"
    )


def _format_timestamp(session_id: str) -> str:
    """Render '2026-04-28T18-17-52' as a friendly local string.

    Falls back to empty when the id doesn't match the expected pattern
    (e.g. a renamed directory) so the row is still usable.
    """
    try:
        dt = datetime.strptime(session_id, "%Y-%m-%dT%H-%M-%S")
    except ValueError:
        return ""
    return dt.strftime("%a %d %b %Y, %H:%M:%S")
