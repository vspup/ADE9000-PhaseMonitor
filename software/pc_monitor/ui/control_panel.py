from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QGroupBox, QFileDialog, QCheckBox, QComboBox,
)
from PySide6.QtCore import Signal

from core.measurement_mode import MeasurementMode
from core.packet_parser import Packet

_VAL_STYLE = 'font-family: monospace; font-size: 13px; font-weight: bold; color: {color};'
_KEY_STYLE = 'font-family: monospace; font-size: 10px; color: #888888;'

_COLORS = {
    'uab':  '#ff6b6b',
    'ubc':  '#51cf66',
    'uca':  '#74c0fc',
    'uavg': '#ffd43b',
    'va':   '#ff6b6b',
    'vb':   '#51cf66',
    'vc':   '#74c0fc',
    'vavg': '#ffd43b',
    'unb':  '#ff922b',
    'f':    '#cc5de8',
    'ia':   '#ff6b6b',
    'ib':   '#51cf66',
    'ic':   '#74c0fc',
    'iavg': '#ffd43b',
    'iunb': '#ff922b',
}

_DELTA = MeasurementMode.MEASURE_DELTA
_WYE   = MeasurementMode.MEASURE_WYE
_CAL   = MeasurementMode.CALIBRATION_LN

_ALL_MODES = frozenset([_DELTA, _WYE, _CAL])

_ROWS = [
    # (key, label, unit, has_cb, visible_in)
    ('uab',  'Uab',  'V',  True,  frozenset([_DELTA])),
    ('ubc',  'Ubc',  'V',  True,  frozenset([_DELTA])),
    ('uca',  'Uca',  'V',  True,  frozenset([_DELTA])),
    ('uavg', 'Uavg', 'V',  True,  frozenset([_DELTA])),
    ('va',   'Va',   'V',  True,  frozenset([_WYE, _CAL])),
    ('vb',   'Vb',   'V',  True,  frozenset([_WYE, _CAL])),
    ('vc',   'Vc',   'V',  True,  frozenset([_WYE, _CAL])),
    ('vavg', 'Vavg', 'V',  True,  frozenset([_WYE])),
    ('unb',  'Unb',  '%',  False, frozenset([_DELTA, _WYE])),
    ('f',    'Freq', 'Hz', False, frozenset([_DELTA, _WYE, _CAL])),
]

# Current block — mode-independent; always shown.
_CURR_ROWS = [
    ('ia',   'Ia',   'A'),
    ('ib',   'Ib',   'A'),
    ('ic',   'Ic',   'A'),
    ('iavg', 'Iavg', 'A'),
    ('iunb', 'Iunb', '%'),
]


class ControlPanel(QWidget):
    history_changed          = Signal(float)
    log_start_requested      = Signal(str)
    log_stop_requested       = Signal()
    curve_visibility_changed = Signal(str, bool)   # key, visible
    calibration_requested    = Signal()
    mode_change_requested    = Signal(str)          # 'delta' or 'wye'
    current_plot_visibility_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(190)
        self._current_mode = _DELTA

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        layout.addWidget(self._build_mode_group())
        layout.addWidget(self._build_values_group())
        layout.addWidget(self._build_current_group())
        layout.addWidget(self._build_display_group())
        layout.addWidget(self._build_logging_group())

        self.btn_calibrate = QPushButton('Calibrate…')
        self.btn_calibrate.setEnabled(False)
        self.btn_calibrate.setToolTip('Connect to device first')
        self.btn_calibrate.clicked.connect(self.calibration_requested)
        layout.addWidget(self.btn_calibrate)

        layout.addStretch()

        self.set_mode(_DELTA)

    # ------------------------------------------------------------------
    def _build_mode_group(self) -> QGroupBox:
        grp = QGroupBox('Mode')
        lay = QVBoxLayout(grp)
        lay.setContentsMargins(6, 6, 6, 4)
        lay.setSpacing(4)

        self.cmb_mode = QComboBox()
        self.cmb_mode.addItem('Delta (L-L)', _DELTA)
        self.cmb_mode.addItem('Wye (L-N)',   _WYE)
        self.cmb_mode.currentIndexChanged.connect(self._on_mode_combo)
        lay.addWidget(self.cmb_mode)

        self.lbl_mode_indicator = QLabel('● DELTA')
        self.lbl_mode_indicator.setStyleSheet('font-size: 10px; color: #74c0fc;')
        lay.addWidget(self.lbl_mode_indicator)

        return grp

    def _on_mode_combo(self, index: int) -> None:
        mode = self.cmb_mode.itemData(index)
        if mode == _DELTA:
            self.mode_change_requested.emit('delta')
        elif mode == _WYE:
            self.mode_change_requested.emit('wye')

    # ------------------------------------------------------------------
    def _build_values_group(self) -> QGroupBox:
        grp = QGroupBox('Current Values')
        lay = QVBoxLayout(grp)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(1)

        self._val_labels:  dict[str, QLabel]  = {}
        self._row_widgets: dict[str, QWidget] = {}

        for key, name, unit, has_cb, _ in _ROWS:
            row_w = QWidget()
            row_lay = QHBoxLayout(row_w)
            row_lay.setContentsMargins(2, 0, 2, 0)
            row_lay.setSpacing(3)

            if has_cb:
                cb = QCheckBox()
                cb.setChecked(True)
                cb.setFixedWidth(18)
                cb.toggled.connect(lambda checked, k=key: self.curve_visibility_changed.emit(k, checked))
                row_lay.addWidget(cb)
            else:
                sp = QWidget()
                sp.setFixedWidth(18)
                row_lay.addWidget(sp)

            key_lbl = QLabel(f'{name}:')
            key_lbl.setStyleSheet(_KEY_STYLE)
            key_lbl.setFixedWidth(34)

            val_lbl = QLabel('—')
            val_lbl.setStyleSheet(_VAL_STYLE.format(color=_COLORS[key]))

            unit_lbl = QLabel(unit)
            unit_lbl.setStyleSheet(_KEY_STYLE)

            row_lay.addWidget(key_lbl)
            row_lay.addWidget(val_lbl, stretch=1)
            row_lay.addWidget(unit_lbl)

            self._val_labels[key]  = val_lbl
            self._row_widgets[key] = row_w
            lay.addWidget(row_w)

        return grp

    # ------------------------------------------------------------------
    def _build_current_group(self) -> QGroupBox:
        grp = QGroupBox('Current')
        lay = QVBoxLayout(grp)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(1)

        for key, name, unit in _CURR_ROWS:
            row_w = QWidget()
            row_lay = QHBoxLayout(row_w)
            row_lay.setContentsMargins(2, 0, 2, 0)
            row_lay.setSpacing(3)

            sp = QWidget()
            sp.setFixedWidth(18)
            row_lay.addWidget(sp)

            key_lbl = QLabel(f'{name}:')
            key_lbl.setStyleSheet(_KEY_STYLE)
            key_lbl.setFixedWidth(34)

            val_lbl = QLabel('—')
            val_lbl.setStyleSheet(_VAL_STYLE.format(color=_COLORS[key]))

            unit_lbl = QLabel(unit)
            unit_lbl.setStyleSheet(_KEY_STYLE)

            row_lay.addWidget(key_lbl)
            row_lay.addWidget(val_lbl, stretch=1)
            row_lay.addWidget(unit_lbl)

            self._val_labels[key] = val_lbl
            lay.addWidget(row_w)

        return grp

    # ------------------------------------------------------------------
    def set_mode(self, mode: MeasurementMode) -> None:
        self._current_mode = mode

        for key, _, _, _, visible_in in _ROWS:
            self._row_widgets[key].setVisible(mode in visible_in)

        if mode == _DELTA:
            self.lbl_mode_indicator.setText('● DELTA')
            self.lbl_mode_indicator.setStyleSheet('font-size: 10px; color: #74c0fc;')
            if self.cmb_mode.currentIndex() != 0:
                self.cmb_mode.blockSignals(True)
                self.cmb_mode.setCurrentIndex(0)
                self.cmb_mode.blockSignals(False)
        elif mode == _WYE:
            self.lbl_mode_indicator.setText('● WYE')
            self.lbl_mode_indicator.setStyleSheet('font-size: 10px; color: #51cf66;')
            if self.cmb_mode.currentIndex() != 1:
                self.cmb_mode.blockSignals(True)
                self.cmb_mode.setCurrentIndex(1)
                self.cmb_mode.blockSignals(False)
        elif mode == _CAL:
            self.lbl_mode_indicator.setText('● CAL L-N')
            self.lbl_mode_indicator.setStyleSheet('font-size: 10px; color: #ffd43b;')

    def update_values(self, p: Packet) -> None:
        mode = p.mode
        if mode == _DELTA:
            self._val_labels['uab'].setText(f'{p.uab:.1f}')
            self._val_labels['ubc'].setText(f'{p.ubc:.1f}')
            self._val_labels['uca'].setText(f'{p.uca:.1f}')
            self._val_labels['uavg'].setText(f'{p.uavg:.1f}')
        elif mode in (_WYE, _CAL):
            self._val_labels['va'].setText(f'{p.va:.1f}')
            self._val_labels['vb'].setText(f'{p.vb:.1f}')
            self._val_labels['vc'].setText(f'{p.vc:.1f}')
            if mode == _WYE:
                self._val_labels['vavg'].setText(f'{p.vavg:.1f}')
        self._val_labels['unb'].setText(f'{p.unb:.2f}' if mode != _CAL else '—')
        self._val_labels['f'].setText(f'{p.f:.2f}' if p.f > 0 else '—')

        self._val_labels['ia'].setText(f'{p.ia:.3f}')
        self._val_labels['ib'].setText(f'{p.ib:.3f}')
        self._val_labels['ic'].setText(f'{p.ic:.3f}')
        self._val_labels['iavg'].setText(f'{p.iavg:.3f}')
        self._val_labels['iunb'].setText(f'{p.iunb:.2f}')

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

        self.cb_show_current = QCheckBox('Show Current Graph')
        self.cb_show_current.setChecked(True)
        self.cb_show_current.toggled.connect(self.current_plot_visibility_changed)
        lay.addWidget(self.cb_show_current)
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
