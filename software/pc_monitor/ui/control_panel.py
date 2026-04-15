from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton,
    QSpinBox, QGroupBox, QFileDialog,
)
from PySide6.QtCore import Signal


class ControlPanel(QWidget):
    history_changed      = Signal(float)   # seconds
    log_start_requested  = Signal(str)     # directory path
    log_stop_requested   = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(185)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        layout.addWidget(self._build_display_group())
        layout.addWidget(self._build_logging_group())
        layout.addStretch()

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
