import json

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QDoubleSpinBox, QGroupBox,
    QGridLayout, QTextEdit,
)


_STYLE_VALUE = 'font-size: 22px; font-weight: bold; color: #51cf66; font-family: monospace;'
_STYLE_GAIN  = 'font-family: monospace; font-size: 12px; color: #74c0fc;'
_STYLE_NOTE  = 'color: #ffd43b; font-size: 11px;'
_STYLE_LOG   = 'font-family: monospace; font-size: 10px; background: #1a1a2e;'


class CalibrationDialog(QDialog):
    """
    Step-by-step voltage gain calibration dialog.

    Communicates with firmware via serial commands (CAL START / PHASE / READ /
    APPLY / SAVE / EXIT).  Firmware responses are routed here from MainWindow
    via handle_firmware_line().
    """

    def __init__(self, reader, parent=None):
        super().__init__(parent)
        self._reader = reader
        self._active_phase: str | None = None

        self.setWindowTitle('Voltage Calibration')
        self.setMinimumWidth(400)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)

        self._gains: dict[str, float] = {'A': 1.0, 'B': 1.0, 'C': 1.0}

        self._build_ui()
        self._send('CAL START')

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # Wiring note
        note = QLabel(
            'Connect one phase at a time to the input terminal,\n'
            'Neutral to the reference terminal (L-N measurement).'
        )
        note.setStyleSheet(_STYLE_NOTE)
        note.setWordWrap(True)
        root.addWidget(note)

        # ── 1. Phase selection ──────────────────────────────────────────
        grp1 = QGroupBox('1 · Select phase')
        row1 = QHBoxLayout(grp1)
        self._phase_btns: dict[str, QPushButton] = {}
        for ph in ('A', 'B', 'C'):
            btn = QPushButton(f'Phase {ph}')
            btn.setCheckable(True)
            btn.setMinimumWidth(80)
            btn.clicked.connect(lambda _, p=ph: self._select_phase(p))
            self._phase_btns[ph] = btn
            row1.addWidget(btn)
        row1.addStretch()
        root.addWidget(grp1)

        # ── 2. Read measured voltage ────────────────────────────────────
        grp2 = QGroupBox('2 · Read firmware RMS')
        lay2 = QHBoxLayout(grp2)
        self._btn_read = QPushButton('Read Voltage')
        self._btn_read.setMinimumWidth(110)
        self._btn_read.clicked.connect(self._read_voltage)
        self._lbl_measured = QLabel('— V')
        self._lbl_measured.setStyleSheet(_STYLE_VALUE)
        lay2.addWidget(self._btn_read)
        lay2.addStretch()
        lay2.addWidget(self._lbl_measured)
        root.addWidget(grp2)

        # ── 3. Enter voltmeter reading and apply ────────────────────────
        grp3 = QGroupBox('3 · Enter voltmeter reading → Apply')
        lay3 = QHBoxLayout(grp3)
        self._spin = QDoubleSpinBox()
        self._spin.setRange(1.0, 999.9)
        self._spin.setDecimals(2)
        self._spin.setSuffix(' V')
        self._spin.setValue(230.0)
        self._spin.setMinimumWidth(110)
        self._btn_apply = QPushButton('Apply Correction')
        self._btn_apply.setMinimumWidth(130)
        self._btn_apply.clicked.connect(self._apply)
        lay3.addWidget(self._spin)
        lay3.addStretch()
        lay3.addWidget(self._btn_apply)
        root.addWidget(grp3)

        # ── Current gains ───────────────────────────────────────────────
        grp4 = QGroupBox('Stored gains (after Apply)')
        grid = QGridLayout(grp4)
        self._gain_labels: dict[str, QLabel] = {}
        for col, ph in enumerate(('A', 'B', 'C')):
            grid.addWidget(QLabel(f'Phase {ph}'), 0, col * 2, Qt.AlignmentFlag.AlignRight)
            lbl = QLabel('1.000000')
            lbl.setStyleSheet(_STYLE_GAIN)
            grid.addWidget(lbl, 0, col * 2 + 1)
            self._gain_labels[ph] = lbl
        root.addWidget(grp4)

        # ── Log ─────────────────────────────────────────────────────────
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFixedHeight(90)
        self._log.setStyleSheet(_STYLE_LOG)
        root.addWidget(self._log)

        # ── Bottom buttons ───────────────────────────────────────────────
        bot = QHBoxLayout()
        self._btn_save = QPushButton('💾  Save to device')
        self._btn_save.clicked.connect(self._save)
        self._btn_exit = QPushButton('Exit calibration')
        self._btn_exit.clicked.connect(self._exit_cal)
        bot.addWidget(self._btn_save)
        bot.addStretch()
        bot.addWidget(self._btn_exit)
        root.addLayout(bot)

    # ------------------------------------------------------------------
    def _send(self, cmd: str) -> None:
        self._reader.send_command(cmd)
        self._log.append(f'→ {cmd}')

    def _select_phase(self, phase: str) -> None:
        self._active_phase = phase
        for ph, btn in self._phase_btns.items():
            btn.setChecked(ph == phase)
        self._lbl_measured.setText('— V')
        self._send(f'CAL PHASE {phase}')

    def _read_voltage(self) -> None:
        if not self._active_phase:
            self._log.append('⚠  Select a phase first')
            return
        self._send('CAL READ')

    def _apply(self) -> None:
        if not self._active_phase:
            self._log.append('⚠  Select a phase first')
            return
        v = self._spin.value()
        self._send(f'CAL APPLY {v:.2f}')

    def _save(self) -> None:
        self._send('CAL SAVE')

    def _exit_cal(self) -> None:
        self._send('CAL EXIT')
        self.accept()

    # ------------------------------------------------------------------
    @Slot(str)
    def handle_firmware_line(self, line: str) -> None:
        """Route a raw JSON line from firmware to the dialog."""
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            return

        event  = d.get('event', '')
        status = d.get('status', '')

        if event == 'cal_rms':
            vrms  = float(d.get('vrms', 0.0))
            phase = d.get('phase', '')
            self._lbl_measured.setText(f'{vrms:.3f} V')
            self._spin.setValue(round(vrms, 2))
            self._log.append(f'← {phase}: measured {vrms:.3f} V')

        elif event == 'cal_applied':
            phase = d.get('phase', '')
            gain  = float(d.get('gain', 1.0))
            reg   = int(d.get('reg', 0))
            self._gains[phase] = gain
            if phase in self._gain_labels:
                self._gain_labels[phase].setText(f'{gain:.6f}')
            self._log.append(f'← {phase} gain={gain:.6f}  reg={reg}')

        elif event == 'cal_phase':
            phase = d.get('phase', '')
            self._log.append(f'← Phase {phase} selected, gain reset to 1.0')

        elif event == 'cal_saved':
            self._log.append('← ✓ Saved to device flash')

        elif event == 'cal_exit':
            self._log.append('← Calibration mode exited')

        elif status == 'error':
            reason = d.get('reason', '?')
            self._log.append(f'← ✗ Error: {reason}')

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        self._send('CAL EXIT')
        super().closeEvent(event)
