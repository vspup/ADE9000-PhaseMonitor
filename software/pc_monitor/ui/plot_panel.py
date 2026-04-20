import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout

from core.measurement_mode import MeasurementMode

_DELTA = MeasurementMode.MEASURE_DELTA
_WYE   = MeasurementMode.MEASURE_WYE
_CAL   = MeasurementMode.CALIBRATION_LN

# Curves that belong to each mode group
_DELTA_CURVES = ('uab', 'ubc', 'uca', 'uavg')
_WYE_CURVES   = ('va',  'vb',  'vc',  'vavg')
_CAL_CURVES   = ('va',  'vb',  'vc')          # vavg excluded in cal mode


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
        self._current_mode: MeasurementMode = _DELTA
        self._current_visible: bool = True

        # Per-curve checkbox visibility (from control panel)
        self._cb_visible: dict[str, bool] = {k: True for k in _DELTA_CURVES + _WYE_CURVES}

        self._setup_plots()
        self.set_mode(_DELTA)

    # ------------------------------------------------------------------
    def _setup_plots(self) -> None:
        pen_ab   = pg.mkPen('#ff6b6b', width=2)
        pen_bc   = pg.mkPen('#51cf66', width=2)
        pen_ca   = pg.mkPen('#74c0fc', width=2)
        pen_avg  = pg.mkPen('#ffd43b', width=2, style=Qt.PenStyle.DashLine)
        pen_unb  = pg.mkPen('#ff922b', width=2)
        pen_freq = pg.mkPen('#cc5de8', width=2)
        pen_ia   = pg.mkPen('#ff6b6b', width=2)
        pen_ib   = pg.mkPen('#51cf66', width=2)
        pen_ic   = pg.mkPen('#74c0fc', width=2)
        pen_iavg = pg.mkPen('#ffd43b', width=2, style=Qt.PenStyle.DashLine)

        # Voltage plot (row 0)
        self.p_volt = self.glw.addPlot(row=0, col=0, colspan=2)
        self.p_volt.setLabel('left', 'Voltage', units='V')
        self.p_volt.showGrid(x=True, y=True, alpha=0.25)

        # Delta curves
        self.c_uab  = self.p_volt.plot(pen=pen_ab)
        self.c_ubc  = self.p_volt.plot(pen=pen_bc)
        self.c_uca  = self.p_volt.plot(pen=pen_ca)
        self.c_uavg = self.p_volt.plot(pen=pen_avg)

        # Wye curves
        self.c_va   = self.p_volt.plot(pen=pen_ab)
        self.c_vb   = self.p_volt.plot(pen=pen_bc)
        self.c_vc   = self.p_volt.plot(pen=pen_ca)
        self.c_vavg = self.p_volt.plot(pen=pen_avg)

        self._legend_delta = _ToggleLegend(offset=(10, 5))
        self._legend_delta.setParentItem(self.p_volt.graphicsItem())
        self._legend_delta.addItem(self.c_uab,  'Uab')
        self._legend_delta.addItem(self.c_ubc,  'Ubc')
        self._legend_delta.addItem(self.c_uca,  'Uca')
        self._legend_delta.addItem(self.c_uavg, 'Uavg')

        self._legend_wye = _ToggleLegend(offset=(10, 5))
        self._legend_wye.setParentItem(self.p_volt.graphicsItem())
        self._legend_wye.addItem(self.c_va,   'Va')
        self._legend_wye.addItem(self.c_vb,   'Vb')
        self._legend_wye.addItem(self.c_vc,   'Vc')
        self._legend_wye.addItem(self.c_vavg, 'Vavg')

        # Current plot (row 1) — always same curves: Ia, Ib, Ic, Iavg
        self.p_curr = self.glw.addPlot(row=1, col=0, colspan=2)
        self.p_curr.setLabel('left', 'Current', units='A')
        self.p_curr.showGrid(x=True, y=True, alpha=0.25)
        self.p_curr.setXLink(self.p_volt)

        self.c_ia   = self.p_curr.plot(pen=pen_ia)
        self.c_ib   = self.p_curr.plot(pen=pen_ib)
        self.c_ic   = self.p_curr.plot(pen=pen_ic)
        self.c_iavg = self.p_curr.plot(pen=pen_iavg)

        self._legend_curr = _ToggleLegend(offset=(10, 5))
        self._legend_curr.setParentItem(self.p_curr.graphicsItem())
        self._legend_curr.addItem(self.c_ia,   'Ia')
        self._legend_curr.addItem(self.c_ib,   'Ib')
        self._legend_curr.addItem(self.c_ic,   'Ic')
        self._legend_curr.addItem(self.c_iavg, 'Iavg')

        # Unbalance plot (row 2, left)
        self.p_unb = self.glw.addPlot(row=2, col=0)
        self.p_unb.setLabel('left',   'Unbalance', units='%', color='#ff922b')
        self.p_unb.setLabel('bottom', 'Time', units='s')
        self.p_unb.showGrid(x=True, y=True, alpha=0.25)
        self.p_unb.setXLink(self.p_volt)
        self.c_unb = self.p_unb.plot(pen=pen_unb)

        # Frequency plot (row 2, right)
        self.p_freq = self.glw.addPlot(row=2, col=1)
        self.p_freq.setLabel('left',   'Frequency', units='Hz', color='#cc5de8')
        self.p_freq.setLabel('bottom', 'Time', units='s')
        self.p_freq.showGrid(x=True, y=True, alpha=0.25)
        self.p_freq.setXLink(self.p_volt)
        self.c_freq = self.p_freq.plot(pen=pen_freq)

        self._apply_row_stretch()

    def _apply_row_stretch(self) -> None:
        layout = self.glw.ci.layout
        if self._current_visible:
            # voltage : current : bottom = 2 : 2 : 1
            layout.setRowStretchFactor(0, 2)
            layout.setRowStretchFactor(1, 2)
            layout.setRowStretchFactor(2, 1)
        else:
            # voltage fills the current row's space; bottom stays fixed proportion.
            layout.setRowStretchFactor(0, 4)
            layout.setRowStretchFactor(1, 0)
            layout.setRowStretchFactor(2, 1)

    # ------------------------------------------------------------------
    def set_history(self, seconds: float) -> None:
        self._history_s = seconds

    def set_mode(self, mode: MeasurementMode) -> None:
        self._current_mode = mode
        self._apply_visibility()

        self._legend_delta.setVisible(mode == _DELTA)
        self._legend_wye.setVisible(mode in (_WYE, _CAL))

        # Unbalance plot is hidden in cal mode (firmware doesn't send unb)
        self.p_unb.setVisible(mode != _CAL)

    def set_current_plot_visible(self, visible: bool) -> None:
        """Show/hide the current graph. Voltage graph stretches to fill when hidden."""
        self._current_visible = visible
        self.p_curr.setVisible(visible)
        self._apply_row_stretch()

    def _apply_visibility(self) -> None:
        mode = self._current_mode

        def show(key: str, curve) -> None:
            in_mode = (
                (mode == _DELTA and key in _DELTA_CURVES) or
                (mode == _WYE   and key in _WYE_CURVES)   or
                (mode == _CAL   and key in _CAL_CURVES)
            )
            curve.setVisible(in_mode and self._cb_visible.get(key, True))

        show('uab',  self.c_uab)
        show('ubc',  self.c_ubc)
        show('uca',  self.c_uca)
        show('uavg', self.c_uavg)
        show('va',   self.c_va)
        show('vb',   self.c_vb)
        show('vc',   self.c_vc)
        show('vavg', self.c_vavg)

    def set_curve_visible(self, key: str, visible: bool) -> None:
        self._cb_visible[key] = visible
        self._apply_visibility()

    # ------------------------------------------------------------------
    def update(self, arrays: dict) -> None:
        ts = arrays.get('ts')
        if ts is None or len(ts) == 0:
            return

        if self._t0 is None:
            self._t0 = ts[0]

        t = (ts - self._t0) / 1000.0

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

        self.c_va.setData(t, data['va'])
        self.c_vb.setData(t, data['vb'])
        self.c_vc.setData(t, data['vc'])
        self.c_vavg.setData(t, data['vavg'])

        self.c_ia.setData(t, data['ia'])
        self.c_ib.setData(t, data['ib'])
        self.c_ic.setData(t, data['ic'])
        self.c_iavg.setData(t, data['iavg'])

        self.c_unb.setData(t, data['unb'])

        freq  = data['f']
        valid = freq > 0
        if np.any(valid):
            self.c_freq.setData(t[valid], freq[valid])

    def reset(self) -> None:
        self._t0 = None
        for c in (self.c_uab, self.c_ubc, self.c_uca, self.c_uavg,
                  self.c_va, self.c_vb, self.c_vc, self.c_vavg,
                  self.c_ia, self.c_ib, self.c_ic, self.c_iavg,
                  self.c_unb, self.c_freq):
            c.setData([], [])
