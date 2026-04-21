"""Standalone window for WORK_MODE_CAPTURE: arm trigger → wait → read → plot → save.

Deliberately separate from the MONITOR MainWindow: different work mode,
different lifecycle, different user task. Shares only SerialReader and
capture_parser from core/.
"""
import csv
import json
import time
from typing import List, Optional

import pyqtgraph as pg
import serial.tools.list_ports
from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QPushButton, QRadioButton, QSpinBox, QToolBar,
    QVBoxLayout, QWidget,
)

from core.capture_parser import (
    CaptureDone, CaptureSample, CaptureStatus, parse_capture_event,
)
from core.serial_reader import SerialReader
from core.sync_probe import SyncResult, SyncSample, compute_offset


class CaptureWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle('ADE9000 Capture')
        self.resize(1200, 780)

        self._reader = SerialReader()
        self._wmode_confirmed = False
        self._wmode_timeout = QTimer(self)
        self._wmode_timeout.setSingleShot(True)
        self._wmode_timeout.timeout.connect(self._on_wmode_timeout)

        self._poll = QTimer(self)
        self._poll.timeout.connect(lambda: self._reader.send_command('CAP STATUS'))

        self._samples: List[CaptureSample] = []
        self._last_done: Optional[CaptureDone] = None

        # Sync probe state
        self._sync_active       = False
        self._sync_n            = 25
        self._sync_best_k       = 8
        self._sync_seq          = 0
        self._sync_pending_seq: Optional[int] = None
        self._sync_pending_send_ns = 0
        self._sync_samples: List[SyncSample] = []
        self._sync_result: Optional[SyncResult] = None
        # Baseline from the first successful sync in this session — subsequent
        # syncs are reported as drift relative to it, so numbers stay readable.
        self._sync_baseline_offset_ms: Optional[float] = None
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.timeout.connect(self._sync_probe_timeout)

        self._build_ui()
        self._wire()
        self._refresh_ports()
        self._set_controls_enabled(False)

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        tb = QToolBar()
        tb.setMovable(False)
        self.addToolBar(tb)

        tb.addWidget(QLabel('  Port: '))
        self.cmb_port = QComboBox()
        self.cmb_port.setMinimumWidth(110)
        tb.addWidget(self.cmb_port)

        btn_refresh = QPushButton('↺')
        btn_refresh.setFixedWidth(28)
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

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)

        # -------- left control column --------
        left = QVBoxLayout()
        left.setSpacing(10)

        split_box = QGroupBox('Pre / Post split')
        sp_lay = QHBoxLayout(split_box)
        self.spn_pre = QSpinBox()
        self.spn_pre.setRange(1, 499)
        self.spn_pre.setValue(100)
        self.spn_post = QSpinBox()
        self.spn_post.setRange(1, 499)
        self.spn_post.setValue(400)
        sp_lay.addWidget(QLabel('Pre:'));  sp_lay.addWidget(self.spn_pre)
        sp_lay.addWidget(QLabel('Post:')); sp_lay.addWidget(self.spn_post)
        left.addWidget(split_box)

        trig_box = QGroupBox('Trigger')
        tb_lay = QVBoxLayout(trig_box)
        self.rb_manual = QRadioButton('Manual')
        self.rb_dip    = QRadioButton('Voltage dip')
        self.rb_manual.setChecked(True)
        tb_lay.addWidget(self.rb_manual)
        tb_lay.addWidget(self.rb_dip)

        dip_row = QHBoxLayout()
        dip_row.addWidget(QLabel('Threshold, V:'))
        self.spn_dip = QDoubleSpinBox()
        self.spn_dip.setRange(1.0, 500.0)
        self.spn_dip.setDecimals(1)
        self.spn_dip.setValue(340.0)
        dip_row.addWidget(self.spn_dip)
        tb_lay.addLayout(dip_row)
        left.addWidget(trig_box)

        act_box = QGroupBox('Actions')
        act_lay = QVBoxLayout(act_box)
        self.btn_arm      = QPushButton('Arm')
        self.btn_trigger  = QPushButton('Trigger now')
        self.btn_abort    = QPushButton('Abort')
        self.btn_sync     = QPushButton('Sync clock')
        self.btn_save_csv = QPushButton('Save CSV…')
        for b in (self.btn_arm, self.btn_trigger, self.btn_abort,
                  self.btn_sync, self.btn_save_csv):
            act_lay.addWidget(b)
        left.addWidget(act_box)

        sync_box = QGroupBox('Clock sync')
        sync_lay = QVBoxLayout(sync_box)
        self.lbl_sync = QLabel('not synced')
        self.lbl_sync.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_sync.setStyleSheet('color: #888888;')
        sync_lay.addWidget(self.lbl_sync)
        left.addWidget(sync_box)

        stat_box = QGroupBox('State')
        stat_lay = QVBoxLayout(stat_box)
        self.lbl_state = QLabel('IDLE')
        self.lbl_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_state.setStyleSheet('font-weight: bold; font-size: 14pt;')
        self.lbl_fill = QLabel('0 / 0')
        self.lbl_fill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        stat_lay.addWidget(self.lbl_state)
        stat_lay.addWidget(self.lbl_fill)
        left.addWidget(stat_box)

        left.addStretch()

        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setFixedWidth(220)
        root.addWidget(left_widget)

        # -------- plots --------
        plots = QVBoxLayout()
        self.p_v = pg.PlotWidget(title='Voltages (V)')
        self.p_i = pg.PlotWidget(title='Currents (A)')
        for p in (self.p_v, self.p_i):
            p.showGrid(x=True, y=True, alpha=0.3)
            p.setLabel('bottom', 'Sample index (i=0 trigger)')
        self.p_v.addLegend(offset=(10, 10))
        self.p_i.addLegend(offset=(10, 10))

        colors = {'a': '#ff6b6b', 'b': '#51cf66', 'c': '#4dabf7'}
        self.c_uab = self.p_v.plot([], [], pen=pg.mkPen(colors['a'], width=2), name='Uab/Va')
        self.c_ubc = self.p_v.plot([], [], pen=pg.mkPen(colors['b'], width=2), name='Ubc/Vb')
        self.c_uca = self.p_v.plot([], [], pen=pg.mkPen(colors['c'], width=2), name='Uca/Vc')
        self.c_ia  = self.p_i.plot([], [], pen=pg.mkPen(colors['a'], width=2), name='Ia')
        self.c_ib  = self.p_i.plot([], [], pen=pg.mkPen(colors['b'], width=2), name='Ib')
        self.c_ic  = self.p_i.plot([], [], pen=pg.mkPen(colors['c'], width=2), name='Ic')
        for p in (self.p_v, self.p_i):
            p.addLine(x=0, pen=pg.mkPen('#ffd43b', width=1, style=Qt.PenStyle.DashLine))
            p.setXRange(-100, 400, padding=0)

        plots_w = QWidget()
        plots_w.setLayout(plots)
        plots.addWidget(self.p_v)
        plots.addWidget(self.p_i)
        root.addWidget(plots_w, stretch=1)

    # ------------------------------------------------------------------
    def _wire(self) -> None:
        self.btn_connect.clicked.connect(self._toggle_connection)
        self.btn_arm.clicked.connect(self._on_arm)
        self.btn_trigger.clicked.connect(lambda: self._reader.send_command('CAP TRIGGER'))
        self.btn_abort.clicked.connect(self._on_abort)
        self.btn_sync.clicked.connect(lambda: self._start_sync_probe())
        self.btn_save_csv.clicked.connect(self._on_save_csv)

        self._reader.line_received.connect(self._on_line)
        self._reader.error_occurred.connect(self._on_error)
        self._reader.connection_changed.connect(self._on_connection)

    # ------------------------------------------------------------------
    def _refresh_ports(self) -> None:
        current = self.cmb_port.currentText()
        ports = [p.device for p in serial.tools.list_ports.comports()]
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
            self._reader.configure(port)
            self._reader.start()

    @Slot(bool)
    def _on_connection(self, connected: bool) -> None:
        if connected:
            self.btn_connect.setText('Disconnect')
            self._wmode_confirmed = False
            self.lbl_status.setText('●  Initializing CAPTURE…')
            self.lbl_status.setStyleSheet('color: #ffd43b; padding: 0 8px;')
            self._reader.send_command('SET WMODE capture')
            self._wmode_timeout.start(2000)
        else:
            self._wmode_timeout.stop()
            self._poll.stop()
            self._wmode_confirmed = False
            self._sync_baseline_offset_ms = None
            self.btn_connect.setText('Connect')
            self.lbl_status.setText('●  Disconnected')
            self.lbl_status.setStyleSheet('color: #888888; padding: 0 8px;')
            self._set_controls_enabled(False)
            self.lbl_sync.setText('not synced')
            self.lbl_sync.setStyleSheet('color: #888888;')

    @Slot(str)
    def _on_error(self, msg: str) -> None:
        self.lbl_status.setText(f'●  Error: {msg[:50]}')
        self.lbl_status.setStyleSheet('color: #ff6b6b; padding: 0 8px;')

    @Slot()
    def _on_wmode_timeout(self) -> None:
        if self._wmode_confirmed:
            return
        self.lbl_status.setText('●  Init error: no wmode ack')
        self.lbl_status.setStyleSheet('color: #ff6b6b; padding: 0 8px;')
        self._reader.stop()
        QMessageBox.critical(self, 'Initialization failed',
                             'Firmware did not acknowledge SET WMODE capture.')

    # ------------------------------------------------------------------
    @Slot(str)
    def _on_line(self, line: str) -> None:
        # Handshake: intercept wmode ack before anything else.
        if not self._wmode_confirmed:
            try:
                d = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                return
            if d.get('event') == 'wmode':
                self._wmode_timeout.stop()
                if d.get('status') == 'ok' and d.get('wmode') == 'capture':
                    self._wmode_confirmed = True
                    self.lbl_status.setText('●  Connected (CAPTURE)')
                    self.lbl_status.setStyleSheet('color: #51cf66; padding: 0 8px;')
                    self._set_controls_enabled(True)
                    # Auto-sync after handshake — gives a valid offset for CSV
                    # metadata without the user having to click anything.
                    self._start_sync_probe()
                else:
                    self._on_wmode_timeout()
            return

        # Intercept sync responses before capture-event parsing.
        if self._sync_active and '"event":"sync"' in line:
            self._on_sync_line(line)
            return

        ev = parse_capture_event(line)
        if isinstance(ev, CaptureStatus):
            self._on_cap_status(ev)
        elif isinstance(ev, CaptureSample):
            self._samples.append(ev)
        elif isinstance(ev, CaptureDone):
            self._on_cap_done(ev)

    def _on_cap_status(self, ev: CaptureStatus) -> None:
        self.lbl_state.setText(ev.state)
        self.lbl_fill.setText(f'{ev.filled} / {ev.total}')
        if ev.state == 'READY':
            self._poll.stop()
            self._samples.clear()
            self._reader.send_command('CAP READ')

    def _on_cap_done(self, ev: CaptureDone) -> None:
        if len(self._samples) != ev.n:
            QMessageBox.warning(self, 'Capture',
                                f'Expected {ev.n} samples, got {len(self._samples)}.')
        self._last_done = ev
        self._plot_samples()
        self.lbl_state.setText('IDLE')

    def _plot_samples(self) -> None:
        xs = [s.i for s in self._samples]
        self.c_uab.setData(xs, [s.uab for s in self._samples])
        self.c_ubc.setData(xs, [s.ubc for s in self._samples])
        self.c_uca.setData(xs, [s.uca for s in self._samples])
        self.c_ia .setData(xs, [s.ia  for s in self._samples])
        self.c_ib .setData(xs, [s.ib  for s in self._samples])
        self.c_ic .setData(xs, [s.ic  for s in self._samples])

    # ------------------------------------------------------------------
    def _on_arm(self) -> None:
        pre, post = self.spn_pre.value(), self.spn_post.value()
        if pre + post > 500:
            QMessageBox.warning(self, 'Bad split',
                                f'pre+post must be ≤ 500 (got {pre + post}).')
            return
        # Push the split before arming; firmware echoes cap_status back.
        self._reader.send_command(f'CAP SET {pre} {post}')

        if self.rb_manual.isChecked():
            cmd = 'CAP ARM manual'
        else:
            cmd = f'CAP ARM dip {self.spn_dip.value():.1f}'
        self._samples.clear()
        self._reader.send_command(cmd)
        self._poll.start(500)

    def _on_abort(self) -> None:
        self._reader.send_command('CAP ABORT')
        self._poll.stop()

    def _on_save_csv(self) -> None:
        if not self._samples:
            QMessageBox.information(self, 'Save CSV', 'No samples to save.')
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Save capture', 'capture.csv', 'CSV (*.csv)')
        if not path:
            return
        with open(path, 'w', newline='', encoding='utf-8') as f:
            if self._sync_result is not None:
                r = self._sync_result
                jitter = r.rtt_ms_median - r.rtt_ms_best
                f.write(
                    f'# sync_offset_ms={r.offset_ms:.3f} '
                    f'rtt_ms_median={r.rtt_ms_median:.3f} '
                    f'rtt_ms_best={r.rtt_ms_best:.3f} '
                    f'rtt_ms_jitter={jitter:.3f} '
                    f'n_used={r.n_used} n_samples={r.n_samples}\n'
                )
            if self._last_done is not None:
                d = self._last_done
                f.write(
                    f'# trigger_tick_ms={d.trigger_tick_ms} '
                    f'sample_period_ms={d.sample_period_ms} '
                    f'pre={d.pre} post={d.post} '
                    f'trigger_index={d.trigger_index}\n'
                )
            w = csv.writer(f)
            w.writerow(['i', 'uab', 'ubc', 'uca', 'ia', 'ib', 'ic'])
            for s in self._samples:
                w.writerow([s.i, s.uab, s.ubc, s.uca, s.ia, s.ib, s.ic])

    # ------------------------------------------------------------------
    def _set_controls_enabled(self, ok: bool) -> None:
        for b in (self.btn_arm, self.btn_trigger, self.btn_abort,
                  self.btn_sync, self.btn_save_csv,
                  self.rb_manual, self.rb_dip, self.spn_dip,
                  self.spn_pre, self.spn_post):
            b.setEnabled(ok)

    # ------------------------------------------------------------------
    # Clock-sync probe
    def _start_sync_probe(self) -> None:
        if self._sync_active or not self._wmode_confirmed:
            return
        self._sync_active = True
        self._sync_samples.clear()
        self._sync_result = None
        self.lbl_sync.setText('syncing…')
        self.lbl_sync.setStyleSheet('color: #ffd43b;')
        self._sync_send_next()

    def _sync_send_next(self) -> None:
        if len(self._sync_samples) >= self._sync_n:
            self._finish_sync_probe()
            return
        self._sync_seq += 1
        self._sync_pending_seq     = self._sync_seq
        self._sync_pending_send_ns = time.perf_counter_ns()
        self._reader.send_command(f'SYNC {self._sync_seq}')
        self._sync_timer.start(500)  # abort the probe if no reply

    def _on_sync_line(self, line: str) -> None:
        recv_ns = time.perf_counter_ns()
        try:
            d = json.loads(line)
            seq     = int(d['seq'])
            tick_ms = int(d['tick_ms'])
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            return
        if seq != self._sync_pending_seq:
            return
        self._sync_timer.stop()
        self._sync_samples.append(SyncSample(
            seq     = seq,
            send_ns = self._sync_pending_send_ns,
            recv_ns = recv_ns,
            tick_ms = tick_ms,
        ))
        self._sync_pending_seq = None
        self._sync_send_next()

    def _sync_probe_timeout(self) -> None:
        # Single reply lost — move on. If too many are lost we still
        # finish with what we have.
        if not self._sync_active:
            return
        self._sync_pending_seq = None
        self._sync_send_next()

    def _finish_sync_probe(self) -> None:
        self._sync_active = False
        self._sync_timer.stop()
        if not self._sync_samples:
            self.lbl_sync.setText('sync failed')
            self.lbl_sync.setStyleSheet('color: #ff6b6b;')
            return
        self._sync_result = compute_offset(self._sync_samples, self._sync_best_k)
        r = self._sync_result
        jitter_ms = r.rtt_ms_median - r.rtt_ms_best

        if self._sync_baseline_offset_ms is None:
            self._sync_baseline_offset_ms = r.offset_ms
            drift_line = 'baseline set (drift 0.00 ms)'
        else:
            drift = r.offset_ms - self._sync_baseline_offset_ms
            drift_line = f'drift {drift:+.2f} ms'

        self.lbl_sync.setText(
            f'{drift_line}\n'
            f'RTT best {r.rtt_ms_best:.2f} / med {r.rtt_ms_median:.2f} ms\n'
            f'jitter {jitter_ms:.2f} ms\n'
            f'used {r.n_used}/{r.n_samples}'
        )
        self.lbl_sync.setStyleSheet('color: #51cf66;')

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802
        self._sync_timer.stop()
        self._poll.stop()
        self._reader.stop()
        event.accept()
