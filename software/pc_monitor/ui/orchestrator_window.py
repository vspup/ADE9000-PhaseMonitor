"""Orchestrator window — standalone PySide6 UI for startup capture.

Runs Orchestrator + session_writer via OrchestratorWorker (background QThread).

Layout:
  Configuration panel  — port selectors with Scan button, pre/post, trigger mode
  Run button
  Progress log         — forwarded from Orchestrator.on_progress
  Result panel         — session dir + key metrics (visible after done)
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


# ---------------------------------------------------------------------------
# Background scanner thread
# ---------------------------------------------------------------------------

class _ScanWorker(QThread):
    finished = Signal(object)   # ScanResult

    def __init__(self, timeout: float, parent=None) -> None:
        super().__init__(parent)
        self._timeout = timeout

    def run(self) -> None:
        result = scan_ports(timeout=self._timeout)
        self.finished.emit(result)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class OrchestratorWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MPS2P Orchestrator")
        self.resize(720, 660)

        self._worker:      Optional[OrchestratorWorker] = None
        self._scan_worker: Optional[_ScanWorker]        = None
        self._setup_ui()
        self._refresh_ports()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        layout.addWidget(self._build_config_group())

        self._run_btn = QPushButton("Run Capture")
        self._run_btn.setFixedHeight(36)
        self._run_btn.clicked.connect(self._on_run)
        layout.addWidget(self._run_btn)

        layout.addWidget(self._build_progress_group())
        layout.addWidget(self._build_result_group())

    def _build_config_group(self) -> QGroupBox:
        box = QGroupBox("Configuration")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Arduino port + Scan button (shared)
        row_ard = QHBoxLayout()
        self._ard_port = QComboBox()
        self._ard_port.setMinimumWidth(150)
        self._scan_btn = QPushButton("Scan")
        self._scan_btn.setToolTip("Auto-detect ADE9000 and Distribution ports")
        self._scan_btn.clicked.connect(self._on_scan)
        self._scan_status = QLabel("")
        row_ard.addWidget(self._ard_port, 1)
        row_ard.addWidget(self._scan_btn)
        row_ard.addWidget(self._scan_status)
        form.addRow("Arduino port:", row_ard)

        # Distribution port + Refresh (list only)
        row_dist = QHBoxLayout()
        self._dist_port = QComboBox()
        self._dist_port.setMinimumWidth(150)
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self._refresh_ports)
        row_dist.addWidget(self._dist_port, 1)
        row_dist.addWidget(self._refresh_btn)
        form.addRow("Distribution port:", row_dist)

        # Pre / Post
        row_pp = QHBoxLayout()
        self._pre_spin = QSpinBox()
        self._pre_spin.setRange(0, 2000)
        self._pre_spin.setValue(100)
        self._post_spin = QSpinBox()
        self._post_spin.setRange(1, 2000)
        self._post_spin.setValue(400)
        row_pp.addWidget(QLabel("Pre:"))
        row_pp.addWidget(self._pre_spin)
        row_pp.addSpacing(12)
        row_pp.addWidget(QLabel("Post:"))
        row_pp.addWidget(self._post_spin)
        row_pp.addStretch()
        form.addRow("Window:", row_pp)

        # Trigger mode
        row_trig = QHBoxLayout()
        self._radio_manual = QRadioButton("Manual")
        self._radio_dip    = QRadioButton("Dip")
        self._radio_manual.setChecked(True)
        self._dip_thresh = QDoubleSpinBox()
        self._dip_thresh.setRange(0.0, 500.0)
        self._dip_thresh.setValue(340.0)
        self._dip_thresh.setSuffix(" V")
        self._dip_thresh.setEnabled(False)
        self._radio_manual.toggled.connect(
            lambda checked: self._dip_thresh.setEnabled(not checked)
        )
        row_trig.addWidget(self._radio_manual)
        row_trig.addWidget(self._radio_dip)
        row_trig.addSpacing(12)
        row_trig.addWidget(QLabel("Threshold:"))
        row_trig.addWidget(self._dip_thresh)
        row_trig.addStretch()
        form.addRow("Trigger mode:", row_trig)

        return box

    def _build_progress_group(self) -> QGroupBox:
        box = QGroupBox("Progress")
        vl  = QVBoxLayout(box)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(500)
        mono = QFont("Consolas", 9)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._log.setFont(mono)
        self._log.setMinimumHeight(200)
        vl.addWidget(self._log)
        return box

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
    # Slot handlers
    # ------------------------------------------------------------------

    @Slot()
    def _refresh_ports(self) -> None:
        ports = sorted(p.device for p in serial.tools.list_ports.comports())
        for combo in (self._ard_port, self._dist_port):
            current = combo.currentText()
            combo.clear()
            combo.addItems(ports)
            if current in ports:
                combo.setCurrentText(current)

    @Slot()
    def _on_scan(self) -> None:
        self._scan_btn.setEnabled(False)
        self._refresh_btn.setEnabled(False)
        self._run_btn.setEnabled(False)
        self._scan_status.setText("Scanning...")

        self._scan_worker = _ScanWorker(timeout=0.5, parent=self)
        self._scan_worker.finished.connect(self._on_scan_done)
        self._scan_worker.start()

    @Slot(object)
    def _on_scan_done(self, result: ScanResult) -> None:
        self._scan_btn.setEnabled(True)
        self._refresh_btn.setEnabled(True)
        self._run_btn.setEnabled(True)

        # Repopulate lists with current ports
        self._refresh_ports()

        found = []
        if result.arduino_port:
            self._ard_port.setCurrentText(result.arduino_port)
            found.append(f"ADE9000={result.arduino_port}")
        if result.dist_port:
            self._dist_port.setCurrentText(result.dist_port)
            found.append(f"Dist={result.dist_port}")

        if found:
            self._scan_status.setText("  ".join(found))
        else:
            self._scan_status.setText("Nothing found")

    @Slot()
    def _on_run(self) -> None:
        ard_port  = self._ard_port.currentText()
        dist_port = self._dist_port.currentText()

        if not ard_port or not dist_port:
            QMessageBox.warning(self, "No port selected",
                                "Select both Arduino and Distribution ports.")
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
        ade  = Ade9000Client()
        dist = DistributionClient()

        self._worker = OrchestratorWorker(cfg, ade, dist, parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

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
            f"ADE9000 trigger tick = {done.trigger_tick_ms} ms  |  "
            f"Dist trigger tick = {ds.trigger_tick} ms  |  "
            f"Arduino samples = {len(session.arduino_samples)}  |  "
            f"Dist samples = {len(session.dist_samples)}"
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
