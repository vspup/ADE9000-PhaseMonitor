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
from PySide6.QtCore import QThread, Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
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

# Colour tokens
_GREEN = "#4caf50"
_RED   = "#f44336"
_GREY  = "#888888"
_DOT   = "●"


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
# Main window
# ---------------------------------------------------------------------------

class OrchestratorWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MPS2P Orchestrator")
        self.resize(740, 680)

        self._worker:      Optional[OrchestratorWorker] = None
        self._scan_worker: Optional[_ScanWorker]        = None
        self._scan_result: Optional[ScanResult]         = None

        self._setup_ui()
        self._populate_all_ports()
        self._update_run_button()

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

        self._run_btn = QPushButton("Run Capture")
        self._run_btn.setFixedHeight(38)
        self._run_btn.setToolTip("Start the startup-capture sequence on both devices")
        self._run_btn.clicked.connect(self._on_run)
        vbox.addWidget(self._run_btn)

        vbox.addWidget(self._build_progress_group())
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

    def _update_run_button(self) -> None:
        has_ard  = bool(self._ard_port.currentText())
        has_dist = bool(self._dist_port.currentText())
        self._run_btn.setEnabled(has_ard and has_dist)

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

    @Slot(str)
    def _on_failed(self, msg: str) -> None:
        self._log.appendPlainText(f"[ERROR] {msg}")
        self._run_btn.setEnabled(True)
        self._scan_btn.setEnabled(True)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        for w in (self._worker, self._scan_worker):
            if w and w.isRunning():
                w.wait(3000)
        super().closeEvent(event)
