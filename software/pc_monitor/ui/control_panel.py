from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton,
    QSpinBox, QGroupBox, QFileDialog, QGridLayout, QCheckBox,
)
from PySide6.QtCore import Signal

from core.packet_parser import Packet

_VAL_STYLE  = 'font-family: monospace; font-size: 13px; font-weight: bold; color: {color};'
_KEY_STYLE  = 'font-family: monospace; font-size: 10px; color: #888888;'

_COLORS = {
    'uab':  '#ff6b6b',
    'ubc':  '#51cf66',
    'uca':  '#74c0fc',
    'uavg': '#ffd43b',
    'unb':  '#ff922b',
    'f':    '#cc5de8',
}


class ControlPanel(QWidget):
    history_changed          = Signal(float)        # seconds
    log_start_requested      = Signal(str)          # directory path
    log_stop_requested       = Signal()
    curve_visibility_changed = Signal(str, bool)    # key, visible
    calibration_requested    = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(185)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        layout.addWidget(self._build_values_group())
        layout.addWidget(self._build_display_group())
        layout.addWidget(self._build_logging_group())

        self.btn_calibrate = QPushButton('Calibrate…')
        self.btn_calibrate.setEnabled(False)
        self.btn_calibrate.setToolTip('Connect to device first')
        self.btn_calibrate.clicked.connect(self.calibration_requested)
        layout.addWidget(self.btn_calibrate)

        layout.addStretch()

    # ------------------------------------------------------------------
    def _build_values_group(self) -> QGroupBox:
        grp = QGroupBox('Current Values')
        grid = QGridLayout(grp)
        grid.setVerticalSpacing(2)
        grid.setHorizontalSpacing(4)

        # key, display name, unit, has checkbox
        rows = [
            ('uab',  'Uab',  'V',  True),
            ('ubc',  'Ubc',  'V',  True),
            ('uca',  'Uca',  'V',  True),
            ('uavg', 'Uavg', 'V',  True),
            ('unb',  'Unb',  '%',  False),
            ('f',    'Freq', 'Hz', False),
        ]
        self._val_labels: dict[str, QLabel] = {}

        for i, (key, name, unit, has_cb) in enumerate(rows):
            col = 0
            if has_cb:
                cb = QCheckBox()
                cb.setChecked(True)
                cb.setFixedWidth(18)
                cb.toggled.connect(lambda checked, k=key: self.curve_visibility_changed.emit(k, checked))
                grid.addWidget(cb, i, col)
            col += 1

            key_lbl = QLabel(f'{name}:')
            key_lbl.setStyleSheet(_KEY_STYLE)

            val_lbl = QLabel('—')
            val_lbl.setStyleSheet(_VAL_STYLE.format(color=_COLORS[key]))

            unit_lbl = QLabel(unit)
            unit_lbl.setStyleSheet(_KEY_STYLE)

            grid.addWidget(key_lbl,  i, col)
            grid.addWidget(val_lbl,  i, col + 1)
            grid.addWidget(unit_lbl, i, col + 2)

            self._val_labels[key] = val_lbl

        return grp

    def update_values(self, p: Packet) -> None:
        self._val_labels['uab'].setText(f'{p.uab:.1f}')
        self._val_labels['ubc'].setText(f'{p.ubc:.1f}')
        self._val_labels['uca'].setText(f'{p.uca:.1f}')
        self._val_labels['uavg'].setText(f'{p.uavg:.1f}')
        self._val_labels['unb'].setText(f'{p.unb:.2f}')
        self._val_labels['f'].setText(f'{p.f:.2f}' if p.f > 0 else '—')

    # ------------------------------------------------------------------
    def _build_display_group(self) -> QGroupBox:
        grp = QGroupBox('Display')
        lay = QVBoxLayout(grp)

        lay.addWidget(QLabel('History window:'))
        self.spin_history = QSpinBox()
        self.spin_history.setRange(5, 300)
        self.spin_history.setValue(60)
        self.spin_history.setSuffix(' s')
        self.spin_history.valueChanged.connect(
            lambda v: self.history_changed.emit(float(v))
        )
        lay.addWidget(self.spin_history)
        return grp

    def _build_logging_group(self) -> QGroupBox:
        grp = QGroupBox('Logging')
        lay = QVBoxLayout(grp)

        self.btn_start_log = QPushButton('Start Logging')
        self.btn_stop_log  = QPushButton('Stop Logging')
        self.btn_stop_log.setEnabled(False)

        self.lbl_log = QLabel('Not logging')
        self.lbl_log.setWordWrap(True)
        self.lbl_log.setStyleSheet('font-size: 10px; color: #888888;')

        self.btn_start_log.clicked.connect(self._on_start)
        self.btn_stop_log.clicked.connect(self.log_stop_requested)

        lay.addWidget(self.btn_start_log)
        lay.addWidget(self.btn_stop_log)
        lay.addWidget(self.lbl_log)
        return grp

    # ------------------------------------------------------------------
    def _on_start(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, 'Select log folder')
        if directory:
            self.log_start_requested.emit(directory)

    def set_logging(self, active: bool, filename: str = '') -> None:
        self.btn_start_log.setEnabled(not active)
        self.btn_stop_log.setEnabled(active)
        if active:
            self.lbl_log.setText(filename)
            self.lbl_log.setStyleSheet('font-size: 10px; color: #51cf66;')
        else:
            self.lbl_log.setText('Not logging')
            self.lbl_log.setStyleSheet('font-size: 10px; color: #888888;')
