import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout


class _ToggleLegend(pg.LegendItem):
    """LegendItem that toggles curve visibility on click."""

    def mouseClickEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseClickEvent(event)
        pos = event.pos()
        for sample, label in self.items:
            sr = sample.mapRectToParent(sample.boundingRect())
            lr = label.mapRectToParent(label.boundingRect())
            if sr.contains(pos) or lr.contains(pos):
                item = sample.item
                visible = not item.isVisible()
                item.setVisible(visible)
                opacity = 1.0 if visible else 0.3
                sample.setOpacity(opacity)
                label.setOpacity(opacity)
                event.accept()
                return
        super().mouseClickEvent(event)


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
        # Graph 1 — Voltages + Uavg (~2/3 height), spans both columns
        self.p_volt = self.glw.addPlot(row=0, col=0, colspan=2)
        self.p_volt.setLabel('left', 'Voltage', units='V')
        self.p_volt.showGrid(x=True, y=True, alpha=0.25)

        legend = _ToggleLegend(offset=(10, 5))
        legend.setParentItem(self.p_volt.graphicsItem())

        self.c_uab  = self.p_volt.plot(pen=pen_uab,  name='Uab')
        self.c_ubc  = self.p_volt.plot(pen=pen_ubc,  name='Ubc')
        self.c_uca  = self.p_volt.plot(pen=pen_uca,  name='Uca')
        self.c_uavg = self.p_volt.plot(pen=pen_uavg, name='Uavg')

        legend.addItem(self.c_uab,  'Uab')
        legend.addItem(self.c_ubc,  'Ubc')
        legend.addItem(self.c_uca,  'Uca')
        legend.addItem(self.c_uavg, 'Uavg')

        # Graph 2 — Unbalance (bottom-left, ~1/3 height)
        self.p_unb = self.glw.addPlot(row=1, col=0)
        self.p_unb.setLabel('left',   'Unbalance', units='%', color='#ff922b')
        self.p_unb.setLabel('bottom', 'Time', units='s')
        self.p_unb.showGrid(x=True, y=True, alpha=0.25)
        self.p_unb.setXLink(self.p_volt)
        self.c_unb = self.p_unb.plot(pen=pen_unb)

        # Graph 3 — Frequency (bottom-right, ~1/3 height)
        self.p_freq = self.glw.addPlot(row=1, col=1)
        self.p_freq.setLabel('left',   'Frequency', units='Hz', color='#cc5de8')
        self.p_freq.setLabel('bottom', 'Time', units='s')
        self.p_freq.showGrid(x=True, y=True, alpha=0.25)
        self.p_freq.setXLink(self.p_volt)
        self.c_freq = self.p_freq.plot(pen=pen_freq)

        # Row stretch: voltage plot gets 2, bottom row gets 1
        self.glw.ci.layout.setRowStretchFactor(0, 2)
        self.glw.ci.layout.setRowStretchFactor(1, 1)

    # ------------------------------------------------------------------
    def set_history(self, seconds: float) -> None:
        self._history_s = seconds

    def set_curve_visible(self, key: str, visible: bool) -> None:
        mapping = {
            'uab':  self.c_uab,
            'ubc':  self.c_ubc,
            'uca':  self.c_uca,
            'uavg': self.c_uavg,
        }
        curve = mapping.get(key)
        if curve is not None:
            curve.setVisible(visible)

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
