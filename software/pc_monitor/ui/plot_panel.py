import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout


class PlotPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.glw = pg.GraphicsLayoutWidget()
        layout.addWidget(self.glw)

        self._history_s: float = 60.0
        self._t0: float | None = None

        self._setup_plots()

    # ------------------------------------------------------------------
    def _setup_plots(self) -> None:
        pen_uab  = pg.mkPen('#ff6b6b', width=2)
        pen_ubc  = pg.mkPen('#51cf66', width=2)
        pen_uca  = pg.mkPen('#74c0fc', width=2)
        pen_uavg = pg.mkPen('#ffd43b', width=2, style=Qt.PenStyle.DashLine)
        pen_unb  = pg.mkPen('#ff922b', width=2)
        pen_freq = pg.mkPen('#cc5de8', width=2)
        pen_thr  = pg.mkPen('#ff4444', width=1, style=Qt.PenStyle.DashLine)

        # Graph 1 — Voltages + Uavg (~2/3 height)
        self.p_volt = self.glw.addPlot(row=0, col=0)
        self.p_volt.setLabel('left', 'Voltage', units='V')
        self.p_volt.showGrid(x=True, y=True, alpha=0.25)
        self.p_volt.addLegend(offset=(10, 5))
        self.c_uab  = self.p_volt.plot(pen=pen_uab,  name='Uab')
        self.c_ubc  = self.p_volt.plot(pen=pen_ubc,  name='Ubc')
        self.c_uca  = self.p_volt.plot(pen=pen_uca,  name='Uca')
        self.c_uavg = self.p_volt.plot(pen=pen_uavg, name='Uavg')

        # Graph 2 — Unbalance (left) + Frequency (right axis, ~1/3 height)
        self.p_unb = self.glw.addPlot(row=1, col=0)
        self.p_unb.setLabel('left',   'Unbalance', units='%',
                            color='#ff922b')
        self.p_unb.setLabel('bottom', 'Time', units='s')
        self.p_unb.showGrid(x=True, y=True, alpha=0.25)
        self.p_unb.setXLink(self.p_volt)
        self.c_unb     = self.p_unb.plot(pen=pen_unb, name='Unb%')
        self.c_unb_thr = self.p_unb.addLine(y=10.0, pen=pen_thr)

        # Secondary ViewBox for frequency on the right axis
        self._vb_freq = pg.ViewBox()
        self.p_unb.showAxis('right')
        self.p_unb.scene().addItem(self._vb_freq)
        self.p_unb.getAxis('right').linkToView(self._vb_freq)
        self.p_unb.getAxis('right').setLabel('Frequency', units='Hz',
                                              color='#cc5de8')
        self._vb_freq.setXLink(self.p_unb)

        self.c_freq     = pg.PlotDataItem(pen=pen_freq, name='Freq')
        self.c_freq_nom = pg.InfiniteLine(pos=50.0, angle=0, pen=pen_thr)
        self._vb_freq.addItem(self.c_freq)
        self._vb_freq.addItem(self.c_freq_nom)

        # Keep secondary ViewBox geometry in sync with the main plot
        self.p_unb.vb.sigResized.connect(self._sync_freq_vb)
        self._sync_freq_vb()

        # Row stretch: voltage plot gets 2, bottom plot gets 1
        self.glw.ci.layout.setRowStretchFactor(0, 2)
        self.glw.ci.layout.setRowStretchFactor(1, 1)

    def _sync_freq_vb(self) -> None:
        self._vb_freq.setGeometry(self.p_unb.vb.sceneBoundingRect())
        self._vb_freq.linkedViewChanged(self.p_unb.vb, self._vb_freq.XAxis)

    # ------------------------------------------------------------------
    def set_history(self, seconds: float) -> None:
        self._history_s = seconds

    def set_unb_threshold(self, value: float) -> None:
        self.c_unb_thr.setValue(value)

    def set_freq_nominal(self, value: float) -> None:
        self.c_freq_nom.setValue(value)

    # ------------------------------------------------------------------
    def update(self, arrays: dict) -> None:
        ts = arrays.get('ts')
        if ts is None or len(ts) == 0:
            return

        if self._t0 is None:
            self._t0 = ts[0]

        t = (ts - self._t0) / 1000.0  # ms → s

        if len(t) > 0:
            t_min = t[-1] - self._history_s
            mask  = t >= t_min
            t     = t[mask]
            data  = {k: v[mask] for k, v in arrays.items() if k != 'ts'}
        else:
            data = {k: v for k, v in arrays.items() if k != 'ts'}

        self.c_uab.setData(t, data['uab'])
        self.c_ubc.setData(t, data['ubc'])
        self.c_uca.setData(t, data['uca'])
        self.c_uavg.setData(t, data['uavg'])
        self.c_unb.setData(t, data['unb'])

        freq  = data['f']
        valid = freq > 0
        if np.any(valid):
            self.c_freq.setData(t[valid], freq[valid])

    def reset(self) -> None:
        self._t0 = None
        for c in (self.c_uab, self.c_ubc, self.c_uca,
                  self.c_uavg, self.c_unb, self.c_freq):
            c.setData([], [])
