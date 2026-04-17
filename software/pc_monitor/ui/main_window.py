import os

import serial.tools.list_ports
from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QToolBar, QComboBox, QPushButton, QLabel,
    QSplitter, QMessageBox,
)

from core.data_buffer import DataBuffer
from core.logger import Logger
from core.measurement_mode import MeasurementMode
from core.packet_parser import parse_packet
from core.serial_reader import SerialReader
from ui.calibration_dialog import CalibrationDialog
from ui.control_panel import ControlPanel
from ui.plot_panel import PlotPanel
from ui.status_bar import StatusBar


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('ADE9000 Phase Monitor')
        self.resize(1280, 820)

        self._buffer       = DataBuffer(maxlen=1200)
        self._reader       = SerialReader()
        self._logger       = Logger()
        self._cal_dlg:     CalibrationDialog | None = None
        self._current_mode: MeasurementMode | None  = None

        self._build_ui()
        self._connect_signals()

        self._plot_timer = QTimer(self)
        self._plot_timer.timeout.connect(self._refresh_plots)
        self._plot_timer.start(100)  # display at 10 Hz

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self._build_toolbar()

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.ctrl  = ControlPanel()
        self.plots = PlotPanel()
        splitter.addWidget(self.ctrl)
        splitter.addWidget(self.plots)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([185, 1095])

        root.addWidget(splitter, stretch=1)

        self.sbar = StatusBar()
        root.addWidget(self.sbar)

        self._refresh_ports()

    def _build_toolbar(self) -> None:
        tb = QToolBar('Main')
        tb.setMovable(False)
        self.addToolBar(tb)

        tb.addWidget(QLabel('  Port: '))

        self.cmb_port = QComboBox()
        self.cmb_port.setMinimumWidth(110)
        tb.addWidget(self.cmb_port)

        btn_refresh = QPushButton('↺')
        btn_refresh.setFixedWidth(28)
        btn_refresh.setToolTip('Refresh port list')
        btn_refresh.clicked.connect(self._refresh_ports)
        tb.addWidget(btn_refresh)

        tb.addSeparator()

        self.btn_connect = QPushButton('Connect')
        self.btn_connect.setMinimumWidth(90)
        tb.addWidget(self.btn_connect)

        tb.addSeparator()

        self.lbl_status = QLabel('●  Disconnected')
        self.lbl_status.setStyleSheet('color: #888888; padding: 0 8px;')
        tb.addWidget(self.lbl_status)

    # ------------------------------------------------------------------
    def _connect_signals(self) -> None:
        self.btn_connect.clicked.connect(self._toggle_connection)

        self._reader.line_received.connect(self._on_line)
        self._reader.error_occurred.connect(self._on_error)
        self._reader.connection_changed.connect(self._on_connection)

        self.ctrl.history_changed.connect(self.plots.set_history)
        self.ctrl.log_start_requested.connect(self._start_log)
        self.ctrl.log_stop_requested.connect(self._stop_log)
        self.ctrl.curve_visibility_changed.connect(self.plots.set_curve_visible)
        self.ctrl.calibration_requested.connect(self._open_calibration)
        self.ctrl.mode_change_requested.connect(self._on_mode_requested)

    # ------------------------------------------------------------------
    def _refresh_ports(self) -> None:
        current = self.cmb_port.currentText()
        ports   = [p.device for p in serial.tools.list_ports.comports()]
        self.cmb_port.clear()
        self.cmb_port.addItems(ports)
        if current in ports:
            self.cmb_port.setCurrentText(current)

    def _toggle_connection(self) -> None:
        if self._reader.isRunning():
            self._reader.stop()
        else:
            port = self.cmb_port.currentText()
            if not port:
                QMessageBox.warning(self, 'No port', 'Select a COM port first.')
                return
            self._buffer.clear()
            self.plots.reset()
            self._reader.configure(port)
            self._reader.start()

    # ------------------------------------------------------------------
    @Slot(str)
    def _on_line(self, line: str) -> None:
        if self._cal_dlg and self._cal_dlg.isVisible():
            self._cal_dlg.handle_firmware_line(line)

        packet = parse_packet(line)
        if packet is None:
            return

        if packet.mode != self._current_mode:
            self._current_mode = packet.mode
            self.ctrl.set_mode(packet.mode)
            self.plots.set_mode(packet.mode)

        self._buffer.append(packet)
        self.sbar.update_packet(packet)
        self.ctrl.update_values(packet)
        if self._logger.active:
            self._logger.write(packet)

    @Slot(str)
    def _on_mode_requested(self, mode_str: str) -> None:
        self._reader.send_command(f'SET MODE {mode_str}')
        mode = MeasurementMode.from_str(mode_str)
        if mode != self._current_mode:
            self._current_mode = mode
            self.ctrl.set_mode(mode)
            self.plots.set_mode(mode)

    @Slot(str)
    def _on_error(self, msg: str) -> None:
        self.lbl_status.setText(f'●  Error: {msg[:50]}')
        self.lbl_status.setStyleSheet('color: #ff6b6b; padding: 0 8px;')

    @Slot(bool)
    def _on_connection(self, connected: bool) -> None:
        if connected:
            self.btn_connect.setText('Disconnect')
            self.lbl_status.setText('●  Connected')
            self.lbl_status.setStyleSheet('color: #51cf66; padding: 0 8px;')
            self.ctrl.btn_calibrate.setEnabled(True)
            self.ctrl.btn_calibrate.setToolTip('')
        else:
            self.btn_connect.setText('Connect')
            self.lbl_status.setText('●  Disconnected')
            self.lbl_status.setStyleSheet('color: #888888; padding: 0 8px;')
            self.ctrl.btn_calibrate.setEnabled(False)
            self.ctrl.btn_calibrate.setToolTip('Connect to device first')
            self._current_mode = None

    # ------------------------------------------------------------------
    @Slot()
    def _open_calibration(self) -> None:
        if self._cal_dlg and self._cal_dlg.isVisible():
            self._cal_dlg.raise_()
            return
        self._cal_dlg = CalibrationDialog(self._reader, parent=self)
        self._cal_dlg.show()

    # ------------------------------------------------------------------
    def _refresh_plots(self) -> None:
        if len(self._buffer) > 0:
            self.plots.update(self._buffer.get_arrays())

    # ------------------------------------------------------------------
    @Slot(str)
    def _start_log(self, directory: str) -> None:
        path = self._logger.start(directory)
        self.ctrl.set_logging(True, os.path.basename(path))

    @Slot()
    def _stop_log(self) -> None:
        self._logger.stop()
        self.ctrl.set_logging(False)

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        self._plot_timer.stop()
        self._reader.stop()
        self._logger.stop()
        event.accept()
