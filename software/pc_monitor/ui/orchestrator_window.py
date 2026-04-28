"""Orchestrator window — startup capture UI.

Layout:
  Devices panel  — Scan button, status indicators, per-device port selectors
  Settings panel — Pre/Post, trigger mode
  Run button     — disabled until both devices found
  Progress log
  Result panel
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import serial.tools.list_ports
from PySide6.QtCore import QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.ade9000_client import Ade9000Client
from core.distribution_client import DistributionClient
from core.orchestrator import CaptureSession, OrchestratorConfig
from core.orchestrator_worker import OrchestratorWorker
from core.port_scanner import ScanResult, scan_ports
from core.session_reader import SessionReadError, read_session
from ui.capture_viewer import CaptureViewDialog
from ui.session_browser import SessionBrowserDialog

# Colour tokens
_GREEN = "#4caf50"
_RED   = "#f44336"
_GREY  = "#888888"
_DOT   = "●"

# Heartbeat: COM-port enumeration tick between sessions, so the indicator
# dots flip red within ~2 s if the user yanks a USB cable. Cheap (no I/O,
# just OS port list); paused while a scan or capture session is running so
# we never race the worker's serial handles.
_HEARTBEAT_MS = 2000


# ---------------------------------------------------------------------------
# Background scanner thread
# ---------------------------------------------------------------------------

class _ScanWorker(QThread):
    finished = Signal(object)   # ScanResult

    def __init__(self, timeout: float, parent=None) -> None:
        super().__init__(parent)
        self._timeout = timeout

    def run(self) -> None:
        self.finished.emit(scan_ports(timeout=self._timeout))


# ---------------------------------------------------------------------------
# Distribution reset thread
# ---------------------------------------------------------------------------

class _ResetWorker(QThread):
    """Re-arm Distribution out-of-band so its FSM goes back to IDLE/ARMED.

    Distribution FW has no CAP ABORT; an explicit ARM is the documented
    way to reset the capture FSM (see orchestrator._abort_both). Runs in a
    QThread because RS-485 timeouts can stall for several seconds when
    the link is genuinely down.
    """

    finished = Signal(bool, str)   # (ok, message)

    def __init__(self, port: str, baudrate: int = 57600, parent=None) -> None:
        super().__init__(parent)
        self._port     = port
        self._baudrate = baudrate

    def run(self) -> None:
        client = DistributionClient()
        try:
            client.open(self._port, self._baudrate)
            try:
                client.mode_cmd()
                client.arm()
            finally:
                client.close()
            self.finished.emit(True, "Distribution re-armed (FSM → IDLE → ARMED)")
        except Exception as exc:
            self.finished.emit(False, str(exc))


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class OrchestratorWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MPS2P Orchestrator")
        self.resize(740, 680)

        self._worker:       Optional[OrchestratorWorker] = None
        self._scan_worker:  Optional[_ScanWorker]        = None
        self._reset_worker: Optional[_ResetWorker]       = None
        self._scan_result:  Optional[ScanResult]         = None
        self._last_session: Optional[CaptureSession]     = None

        self._setup_ui()
        self._populate_all_ports()
        self._update_run_button()

        self._heartbeat = QTimer(self)
        self._heartbeat.setInterval(_HEARTBEAT_MS)
        self._heartbeat.timeout.connect(self._on_heartbeat)
        self._heartbeat.start()
        self._on_heartbeat()   # immediate first tick so dots reflect state at startup

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        vbox = QVBoxLayout(root)
        vbox.setContentsMargins(10, 10, 10, 10)
        vbox.setSpacing(8)

        vbox.addWidget(self._build_devices_group())
        vbox.addWidget(self._build_settings_group())

        run_row = QHBoxLayout()
        self._run_btn = QPushButton("Run Capture")
        self._run_btn.setFixedHeight(38)
        self._run_btn.setToolTip("Start the startup-capture sequence on both devices")
        self._run_btn.clicked.connect(self._on_run)
        run_row.addWidget(self._run_btn, 1)

        self._browse_btn = QPushButton("Browse Sessions…")
        self._browse_btn.setFixedHeight(38)
        self._browse_btn.setToolTip(
            "Open the analysis viewer on a previously captured session"
        )
        self._browse_btn.clicked.connect(self._on_browse_sessions)
        run_row.addWidget(self._browse_btn)
        vbox.addLayout(run_row)

        vbox.addWidget(self._build_progress_group())
        vbox.addWidget(self._build_error_panel())
        vbox.addWidget(self._build_result_group())

    # --- Devices group ---

    def _build_devices_group(self) -> QGroupBox:
        box = QGroupBox("Devices")
        vbox = QVBoxLayout(box)

        # Scan row
        scan_row = QHBoxLayout()
        self._scan_btn = QPushButton("Scan Ports")
        self._scan_btn.setToolTip(
            "Auto-detect ADE9000 (Arduino) and Distribution board\n"
            "by probing all available COM ports"
        )
        self._scan_btn.clicked.connect(self._on_scan)

        self._scan_status = QLabel("Not scanned")
        self._scan_status.setStyleSheet(f"color: {_GREY};")

        scan_row.addWidget(self._scan_btn)
        scan_row.addWidget(self._scan_status, 1)
        vbox.addLayout(scan_row)

        # Device rows
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(10)

        # ADE9000 row
        ard_row = QHBoxLayout()
        self._ard_dot  = QLabel(_DOT)
        self._ard_dot.setStyleSheet(f"color: {_GREY}; font-size: 16px;")
        self._ard_port = QComboBox()
        self._ard_port.setMinimumWidth(120)
        self._ard_port.setToolTip("COM port for ADE9000 Phase Monitor (Arduino Zero, 115200 baud)")
        self._ard_port.currentTextChanged.connect(self._update_run_button)
        ard_row.addWidget(self._ard_dot)
        ard_row.addWidget(self._ard_port, 1)
        form.addRow("ADE9000 (Arduino):", ard_row)

        # Distribution row
        dist_row = QHBoxLayout()
        self._dist_dot  = QLabel(_DOT)
        self._dist_dot.setStyleSheet(f"color: {_GREY}; font-size: 16px;")
        self._dist_port = QComboBox()
        self._dist_port.setMinimumWidth(120)
        self._dist_port.setToolTip("COM port for Distribution Board (STM32G431, 57600 baud)")
        self._dist_port.currentTextChanged.connect(self._update_run_button)
        dist_row.addWidget(self._dist_dot)
        dist_row.addWidget(self._dist_port, 1)
        form.addRow("Distribution board:", dist_row)

        vbox.addLayout(form)
        return box

    # --- Settings group ---

    def _build_settings_group(self) -> QGroupBox:
        box = QGroupBox("Capture Settings")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(10)

        # Pre / Post
        pp_row = QHBoxLayout()
        self._pre_spin = QSpinBox()
        self._pre_spin.setRange(0, 2000)
        self._pre_spin.setValue(100)
        self._pre_spin.setToolTip("Samples to capture before trigger (pre-trigger window)")
        self._post_spin = QSpinBox()
        self._post_spin.setRange(1, 2000)
        self._post_spin.setValue(400)
        self._post_spin.setToolTip("Samples to capture after trigger (post-trigger window)")
        pp_row.addWidget(QLabel("Pre:"))
        pp_row.addWidget(self._pre_spin)
        pp_row.addSpacing(16)
        pp_row.addWidget(QLabel("Post:"))
        pp_row.addWidget(self._post_spin)
        pp_row.addStretch()
        form.addRow("Window:", pp_row)

        # Trigger mode
        trig_row = QHBoxLayout()
        self._radio_manual = QRadioButton("Manual")
        self._radio_manual.setToolTip("Trigger is fired manually by this application")
        self._radio_dip    = QRadioButton("Dip")
        self._radio_dip.setToolTip("ADE9000 triggers automatically on voltage dip")
        self._radio_manual.setChecked(True)
        self._dip_thresh = QDoubleSpinBox()
        self._dip_thresh.setRange(0.0, 500.0)
        self._dip_thresh.setValue(340.0)
        self._dip_thresh.setSuffix(" V")
        self._dip_thresh.setEnabled(False)
        self._dip_thresh.setToolTip("Voltage threshold below which a dip trigger fires (V rms)")
        self._radio_manual.toggled.connect(
            lambda checked: self._dip_thresh.setEnabled(not checked)
        )
        trig_row.addWidget(self._radio_manual)
        trig_row.addWidget(self._radio_dip)
        trig_row.addSpacing(16)
        trig_row.addWidget(QLabel("Threshold:"))
        trig_row.addWidget(self._dip_thresh)
        trig_row.addStretch()
        form.addRow("Trigger mode:", trig_row)

        return box

    # --- Progress group ---

    def _build_progress_group(self) -> QGroupBox:
        box = QGroupBox("Progress")
        vl  = QVBoxLayout(box)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(500)
        mono = QFont("Consolas", 9)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._log.setFont(mono)
        self._log.setMinimumHeight(180)
        vl.addWidget(self._log)
        return box

    # --- Error panel ---

    def _build_error_panel(self) -> QFrame:
        """Persistent error panel — shown after a failed session, hidden on next run.

        Surfaces *which device* refused *which command* (when available from
        OrchestratorError) so the user doesn't have to scrape the log.
        """
        self._error_box = QFrame()
        self._error_box.setVisible(False)
        self._error_box.setFrameShape(QFrame.Shape.StyledPanel)
        self._error_box.setStyleSheet(
            f"QFrame {{ border: 1px solid {_RED}; border-radius: 4px; "
            f"background: rgba(244, 67, 54, 0.08); }}"
        )
        vl = QVBoxLayout(self._error_box)
        vl.setContentsMargins(10, 6, 10, 6)
        vl.setSpacing(2)

        self._error_title = QLabel()
        self._error_title.setStyleSheet(f"color: {_RED}; font-weight: bold;")
        self._error_detail = QLabel()
        self._error_detail.setWordWrap(True)
        self._error_detail.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        vl.addWidget(self._error_title)
        vl.addWidget(self._error_detail)

        # After a FIRE/DRAIN failure the Distribution FSM is stuck in
        # ARMED/CAPTURING (no CAP ABORT in FW yet). Re-ARM resets it to
        # IDLE so the user can hit Run again without a USB reconnect.
        reset_row = QHBoxLayout()
        reset_row.setContentsMargins(0, 4, 0, 0)
        self._reset_btn = QPushButton("Reset Distribution")
        self._reset_btn.setToolTip(
            "Re-ARM Distribution to clear a stuck FSM after a failed capture"
        )
        self._reset_btn.clicked.connect(self._on_reset_distribution)
        reset_row.addStretch()
        reset_row.addWidget(self._reset_btn)
        vl.addLayout(reset_row)
        return self._error_box

    # --- Result group ---

    def _build_result_group(self) -> QGroupBox:
        self._result_box = QGroupBox("Result")
        self._result_box.setVisible(False)
        vl = QVBoxLayout(self._result_box)
        self._result_dir     = QLabel()
        self._result_metrics = QLabel()
        self._result_dir.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        vl.addWidget(self._result_dir)
        vl.addWidget(self._result_metrics)

        self._view_btn = QPushButton("View Plots")
        self._view_btn.setFixedHeight(32)
        self._view_btn.setToolTip("Open voltage / current / ADC charts for this session")
        self._view_btn.clicked.connect(self._on_view_session)
        vl.addWidget(self._view_btn)

        return self._result_box

    # ------------------------------------------------------------------
    # Port list helpers
    # ------------------------------------------------------------------

    def _populate_all_ports(self) -> None:
        """Fill both dropdowns with all available COM ports (pre-scan fallback)."""
        ports = sorted(p.device for p in serial.tools.list_ports.comports())
        for combo in (self._ard_port, self._dist_port):
            cur = combo.currentText()
            combo.clear()
            combo.addItems(ports)
            if cur in ports:
                combo.setCurrentText(cur)

    def _populate_from_scan(self, result: ScanResult) -> None:
        """Fill each dropdown with only the ports of the right device type."""
        self._ard_port.clear()
        self._ard_port.addItems(result.arduino_ports)

        self._dist_port.clear()
        self._dist_port.addItems(result.dist_ports)

    def _set_indicator(self, label: QLabel, found: bool) -> None:
        colour = _GREEN if found else _RED
        label.setStyleSheet(f"color: {colour}; font-size: 16px;")

    def _set_indicator_colour(self, label: QLabel, colour: str) -> None:
        label.setStyleSheet(f"color: {colour}; font-size: 16px;")

    def _update_run_button(self) -> None:
        has_ard  = bool(self._ard_port.currentText())
        has_dist = bool(self._dist_port.currentText())
        self._run_btn.setEnabled(has_ard and has_dist)

    # ------------------------------------------------------------------
    # Heartbeat: cheap port-presence check between sessions
    # ------------------------------------------------------------------

    @Slot()
    def _on_heartbeat(self) -> None:
        """Refresh dot indicators based on whether selected ports still exist.

        Pure OS port enumeration — no serial I/O, so safe to run on a timer.
        Skipped while a worker (scan or capture) holds the ports, to keep
        signal-cause attribution clean (a colour flip during a session would
        otherwise be misleading: the port is open, just not in our list).

        Detects only the cable-yank case; an unresponsive but still-enumerated
        device shows green here. A real PING-based liveness check would require
        opening the port, which would clash with concurrent worker access — not
        worth the complexity for a 2 s tick.
        """
        if self._worker is not None and self._worker.isRunning():
            return
        if self._scan_worker is not None and self._scan_worker.isRunning():
            return

        available = {p.device for p in serial.tools.list_ports.comports()}
        for dot, combo in (
            (self._ard_dot,  self._ard_port),
            (self._dist_dot, self._dist_port),
        ):
            port = combo.currentText()
            if not port:
                self._set_indicator_colour(dot, _GREY)
            elif port in available:
                self._set_indicator_colour(dot, _GREEN)
            else:
                self._set_indicator_colour(dot, _RED)

    # ------------------------------------------------------------------
    # Scan slot
    # ------------------------------------------------------------------

    @Slot()
    def _on_scan(self) -> None:
        self._scan_btn.setEnabled(False)
        self._run_btn.setEnabled(False)
        self._scan_status.setText("Scanning…")
        self._scan_status.setStyleSheet(f"color: {_GREY};")

        self._scan_worker = _ScanWorker(timeout=0.6, parent=self)
        self._scan_worker.finished.connect(self._on_scan_done)
        self._scan_worker.start()

    @Slot(object)
    def _on_scan_done(self, result: ScanResult) -> None:
        self._scan_result = result
        self._scan_btn.setEnabled(True)

        self._populate_from_scan(result)

        self._set_indicator(self._ard_dot,  bool(result.arduino_ports))
        self._set_indicator(self._dist_dot, bool(result.dist_ports))

        parts = []
        if result.arduino_ports:
            parts.append(f"ADE9000 = {', '.join(result.arduino_ports)}")
        if result.dist_ports:
            parts.append(f"Distribution = {', '.join(result.dist_ports)}")

        if result.complete:
            self._scan_status.setText("  |  ".join(parts))
            self._scan_status.setStyleSheet(f"color: {_GREEN};")
        elif parts:
            missing = []
            if not result.arduino_ports:
                missing.append("ADE9000 not found")
            if not result.dist_ports:
                missing.append("Distribution not found")
            self._scan_status.setText("  |  ".join(parts + missing))
            self._scan_status.setStyleSheet(f"color: {_RED};")
        else:
            self._scan_status.setText("No devices found")
            self._scan_status.setStyleSheet(f"color: {_RED};")

        self._update_run_button()

    # ------------------------------------------------------------------
    # Run slot
    # ------------------------------------------------------------------

    @Slot()
    def _on_run(self) -> None:
        ard_port  = self._ard_port.currentText()
        dist_port = self._dist_port.currentText()

        if not ard_port or not dist_port:
            QMessageBox.warning(self, "No port selected",
                                "Both devices must be selected before running.")
            return
        if ard_port == dist_port:
            QMessageBox.warning(self, "Same port",
                                "Arduino and Distribution must use different ports.")
            return

        self._run_btn.setEnabled(False)
        self._scan_btn.setEnabled(False)
        self._log.clear()
        self._result_box.setVisible(False)
        self._error_box.setVisible(False)

        cfg = OrchestratorConfig(
            arduino_port  = ard_port,
            dist_port     = dist_port,
            pre           = self._pre_spin.value(),
            post          = self._post_spin.value(),
            trigger_mode  = "dip" if self._radio_dip.isChecked() else "manual",
            dip_threshold = self._dip_thresh.value(),
        )
        self._worker = OrchestratorWorker(cfg, Ade9000Client(), DistributionClient(),
                                          parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    # ------------------------------------------------------------------
    # Worker callbacks
    # ------------------------------------------------------------------

    @Slot(str, str)
    def _on_progress(self, phase: str, msg: str) -> None:
        self._log.appendPlainText(f"[{phase}] {msg}")

    @Slot(object)
    def _on_done(self, session: CaptureSession) -> None:
        self._last_session = session
        done = session.arduino_done
        ds   = session.dist_status
        out  = Path(session.config.output_dir) / session.session_id

        self._result_dir.setText(f"Session: {out}")
        self._result_metrics.setText(
            f"offset_ad = {session.offset_ad_ms:+.1f} ms  |  "
            f"ADE9000 trigger = {done.trigger_tick_ms} ms  |  "
            f"Dist trigger = {ds.trigger_tick} ms  |  "
            f"Arduino {len(session.arduino_samples)} samples  |  "
            f"Dist {len(session.dist_samples)} samples"
        )
        self._result_box.setVisible(True)
        self._log.appendPlainText("[DONE] session written")
        self._run_btn.setEnabled(True)
        self._scan_btn.setEnabled(True)

    @Slot()
    def _on_view_session(self) -> None:
        if self._last_session is None:
            return
        dlg = CaptureViewDialog(self._last_session, parent=self)
        # The viewer takes over the screen; the main window is hidden until
        # the user clicks ← Back (or closes the viewer any other way).
        dlg.finished.connect(self._on_viewer_closed)
        self.hide()
        dlg.showMaximized()

    @Slot()
    def _on_viewer_closed(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    @Slot()
    def _on_browse_sessions(self) -> None:
        # Default location matches OrchestratorConfig.output_dir (CWD-relative).
        captures_dir = Path("captures")
        dlg = SessionBrowserDialog(captures_dir, parent=self)
        dlg.selected.connect(self._open_session_from_disk)
        dlg.exec()

    @Slot(Path)
    def _open_session_from_disk(self, session_dir: Path) -> None:
        try:
            sess = read_session(session_dir)
        except SessionReadError as exc:
            QMessageBox.warning(
                self,
                "Cannot open session",
                f"{session_dir}\n\n{exc}",
            )
            return
        viewer = CaptureViewDialog(sess, parent=self)
        viewer.finished.connect(self._on_viewer_closed)
        self.hide()
        viewer.showMaximized()

    @Slot()
    def _on_reset_distribution(self) -> None:
        port = self._dist_port.currentText().strip()
        if not port:
            QMessageBox.warning(
                self, "Reset Distribution",
                "Select the Distribution COM port first.",
            )
            return
        self._reset_btn.setEnabled(False)
        self._run_btn.setEnabled(False)
        self._log.appendPlainText(f"[RESET] re-arming Distribution on {port}")
        self._reset_worker = _ResetWorker(port, parent=self)
        self._reset_worker.finished.connect(self._on_reset_done)
        self._reset_worker.start()

    @Slot(bool, str)
    def _on_reset_done(self, ok: bool, msg: str) -> None:
        self._reset_btn.setEnabled(True)
        self._run_btn.setEnabled(True)
        if ok:
            self._log.appendPlainText(f"[RESET] {msg}")
            self._error_box.setVisible(False)
        else:
            self._log.appendPlainText(f"[RESET] failed: {msg}")
            self._error_title.setText("Reset Distribution failed")
            self._error_detail.setText(msg)
            self._error_box.setVisible(True)

    @Slot(str, dict)
    def _on_failed(self, msg: str, info: dict) -> None:
        phase   = info.get("phase",   "")
        device  = info.get("device",  "")
        command = info.get("command", "")

        # Log line: tagged with phase/device when available so it stays
        # greppable even after the panel is dismissed by the next run.
        prefix_parts = ["ERROR"]
        if phase:
            prefix_parts.append(phase)
        if device:
            prefix_parts.append(device)
        prefix = "][".join(prefix_parts)
        cmd_suffix = f" (CMD={command})" if command else ""
        self._log.appendPlainText(f"[{prefix}]{cmd_suffix} {msg}")

        # Persistent panel: title summarises device + command, body is the message.
        if device and command:
            self._error_title.setText(f"{device} failed on {command}")
        elif device:
            self._error_title.setText(f"{device} failed")
        else:
            self._error_title.setText("Capture failed")
        self._error_detail.setText(msg)
        self._error_box.setVisible(True)

        self._run_btn.setEnabled(True)
        self._scan_btn.setEnabled(True)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        for w in (self._worker, self._scan_worker, self._reset_worker):
            if w and w.isRunning():
                w.wait(3000)
        super().closeEvent(event)
