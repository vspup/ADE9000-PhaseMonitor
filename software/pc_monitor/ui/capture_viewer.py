"""Post-session data viewer — three-row matplotlib viewer with full controls.

Layout (top to bottom):
  Header            [← Back]  [Fullscreen]  Title  [Reset View]
  Top bar — row 1   Focus mode (All|V|I|ADC) + Point markers toggle
  Top bar — row 2   ADC channel checkboxes + presets
  V row             [Y controls "Voltage scale"]  [voltage canvas + outside legend]
  I row             [Y controls "Current scale"]  [current canvas + outside legend]
  ADC row           [Y controls "ADC scale"]      [ADC canvas + outside legend]
  X bar — row 1     Time range: X min / X max / Apply X / Auto X
  X bar — row 2     Trigger:    ±W ms window / Centre on trigger
  Marker pane       M1/M2 readouts per group + Δt

Each plot row is a self-contained QWidget (`_PlotRow`) with its own Figure +
Canvas — that way the per-plot Y-controls stand visually next to the plot
they affect, without fighting matplotlib's internal subplot geometry. The
X axes are kept in sync via xlim_changed callbacks (one row drives, others
follow). The matplotlib NavigationToolbar is intentionally absent — all view
control flows through the dedicated X / Y input fields. There is no mouse
zoom or pan — view changes happen only through the spinboxes.

Marker model (left/right click):
  - Group "ADE9000" spans the V + I rows; a left-click on either places the
    same snapped marker on both, and the readout shows V and I together.
  - Group "Distribution" is only the ADC row; clicks place markers there
    alone. Right-click clears the markers in the clicked group.

Legend behaviour:
  - Each plot's legend lives outside the axes (top-right of canvas).
  - Click on a legend entry toggles visibility of the matching line; the
    legend handle dims to alpha 0.35 to signal hidden state.

Signal stylization:
  - Voltages and currents are *primary* (1.6 px linewidth, alpha 1.0).
  - Raw ADC channels are *secondary* (0.7 px linewidth, alpha 0.7) so the
    ADE9000 readings dominate visually when both are on screen.

Trigger highlighting:
  - 2.0 px red dashed vertical at t = 0 with a translucent ±5 ms band and a
    "TRIGGER  t = 0.0 ms" label at the top of the band; a second corner
    label shows the current view span ("view: ±W ms"). Both texts are
    re-anchored on every X- or Y-limit change.

Point markers:
  - Global toggle adds round dot markers to every data point on every line;
    useful when zooming in to see actual sample positions.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Optional

from matplotlib.axes import Axes
from matplotlib.backend_bases import MouseEvent, PickEvent
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

from PySide6.QtCore import Qt, QSignalBlocker, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.distribution_client import CHANNEL_KEYS
from core.orchestrator import CaptureSession


# ---------------------------------------------------------------------------
# Visual tokens
# ---------------------------------------------------------------------------

_VOLT_SERIES = [
    ("uab", "Uab", "tab:blue"),
    ("ubc", "Ubc", "tab:orange"),
    ("uca", "Uca", "tab:green"),
]
_CURR_SERIES = [
    ("ia", "Ia", "tab:blue"),
    ("ib", "Ib", "tab:orange"),
    ("ic", "Ic", "tab:green"),
]

# Signal style priorities — voltages and currents are primary (thick/opaque),
# raw ADC is secondary (thin/translucent) so the ADE9000 series dominates the
# eye when both sets share the screen.
_PRIMARY_STYLE   = dict(linewidth=1.6, alpha=1.0)
_SECONDARY_STYLE = dict(linewidth=0.7, alpha=0.7)

_TRIGGER_LINE_STYLE = dict(color="red", linestyle="--", linewidth=2.0, zorder=3)
_TRIGGER_BAND_HALFWIDTH_MS = 5.0
_TRIGGER_BAND_ALPHA        = 0.10
_TRIGGER_LABEL_KW = dict(
    color="red", fontsize=9, fontweight="bold",
    ha="center", va="bottom",
)
_VIEW_LABEL_KW = dict(
    color="#444", fontsize=8, ha="right", va="top",
)

_MARKER_COLORS = ("#00bcd4", "#e91e63")   # M1 cyan, M2 pink

_HIDDEN_LEGEND_ALPHA = 0.35

_POINT_MARKER_KW = dict(marker="o", markersize=3.0, markeredgewidth=0.0)

_HINT_TEXT = (
    "Left-click on a plot to place a marker (cycles M1 ↔ M2). "
    "Right-click clears the markers in that group. "
    "Click a legend entry to toggle its series."
)

_AUTO_X_PAD_FRAC = 0.02
_AUTO_Y_PAD_FRAC = 0.05


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snap_index(times: list[float], x: float) -> int:
    """Index in ascending `times` whose value is closest to `x`."""
    n = len(times)
    if n == 0:
        return 0
    if x <= times[0]:
        return 0
    if x >= times[-1]:
        return n - 1
    i = bisect.bisect_left(times, x)
    if i == 0:
        return 0
    return i if abs(times[i] - x) < abs(times[i - 1] - x) else i - 1


def _format_value(label: str, unit: str, values: list[float], idx: int) -> str:
    if idx < 0 or idx >= len(values):
        return f"{label}=—"
    v = values[idx]
    if unit:
        return f"{label}={v:+8.2f} {unit}"
    return f"{label}={int(round(v)):+6d}"


def _wrap_columns(items: list[str], per_line: int, indent: str) -> list[str]:
    out: list[str] = []
    for i in range(0, len(items), per_line):
        out.append(indent + "   ".join(items[i:i + per_line]))
    return out


# ---------------------------------------------------------------------------
# Y-axis control strip
# ---------------------------------------------------------------------------

class _YControls(QWidget):
    """Compact Y-axis editor placed left of its plot.

    Carries a bold section title ("Voltage scale" / "Current scale" / "ADC
    scale") so the user can immediately see which plot each strip belongs to.
    Emits `apply_requested(ymin, ymax)` on Apply Y and `auto_requested()` on
    Auto Y. The host widget calls `update_fields(ymin, ymax)` after autoscale
    so the spinboxes reflect the actual view.
    """

    apply_requested = Signal(float, float)
    auto_requested  = Signal()

    def __init__(
        self, *, title: str, unit: str, parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 2, 4, 2)
        v.setSpacing(2)

        head = QLabel(title)
        head.setStyleSheet("font-weight: 600;")
        head.setAlignment(Qt.AlignHCenter)
        v.addWidget(head)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        v.addWidget(sep)

        self._unit = unit
        self._ymax = self._make_spin()
        self._ymin = self._make_spin()

        # Top of plot at top of strip.
        v.addStretch(1)
        v.addWidget(self._field_row("max", self._ymax))
        v.addWidget(self._field_row("min", self._ymin))

        btn_apply = QPushButton("Apply")
        btn_auto  = QPushButton("Auto")
        btn_apply.clicked.connect(self._on_apply)
        btn_auto.clicked.connect(self.auto_requested)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(2)
        btn_row.addWidget(btn_apply)
        btn_row.addWidget(btn_auto)
        v.addLayout(btn_row)
        v.addStretch(1)

        self.setMinimumWidth(112)
        self.setMaximumWidth(132)

    def _field_row(self, label: str, spin: QDoubleSpinBox) -> QWidget:
        w = QWidget(self)
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(3)
        lbl = QLabel(label)
        lbl.setMinimumWidth(24)
        h.addWidget(lbl)
        h.addWidget(spin, stretch=1)
        return w

    def _make_spin(self) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(-1e9, 1e9)
        s.setDecimals(2)
        s.setSingleStep(1.0)
        if self._unit:
            s.setSuffix(f" {self._unit}")
        return s

    def update_fields(self, ymin: float, ymax: float) -> None:
        for spin, val in ((self._ymin, ymin), (self._ymax, ymax)):
            blocker = QSignalBlocker(spin)
            spin.setValue(val)
            del blocker

    def _on_apply(self) -> None:
        self.apply_requested.emit(self._ymin.value(), self._ymax.value())


# ---------------------------------------------------------------------------
# One plot row — Y controls + Figure + Canvas
# ---------------------------------------------------------------------------

class _PlotRow(QWidget):
    """Self-contained plot widget with its own Figure, Canvas and Y controls."""

    xlim_changed_by_user = Signal(float, float)
    clicked              = Signal(int, float)   # button (1=left, 3=right), xdata

    def __init__(
        self,
        *,
        title:           str,
        ylabel:          str,
        unit:            str,
        section_title:   str,
        series:          list[tuple[str, str, list[float]]],
        times:           list[float],
        style:           dict,
        parent:          Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.times    = times
        self.series   = series
        self._unit    = unit
        self._title   = title
        self._lines:           list[Line2D] = []
        self._line_for_label:  dict[str, Line2D] = {}
        self._legend_picks:    dict[int, tuple[Line2D, Line2D]] = {}
        self._trigger_line:    Optional[Line2D] = None
        self._trigger_band              = None
        self._trigger_label             = None
        self._view_label                = None
        self._suppress_next_click       = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.y_controls = _YControls(title=section_title, unit=unit, parent=self)
        self.y_controls.apply_requested.connect(self._on_apply_y)
        self.y_controls.auto_requested.connect(self.auto_y)
        layout.addWidget(self.y_controls)

        self._fig = Figure()
        # Reserve space on the right for the outside legend.
        self._fig.subplots_adjust(left=0.06, right=0.84, top=0.90, bottom=0.18)
        self._canvas = FigureCanvasQTAgg(self._fig)
        self._canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._ax: Axes = self._fig.add_subplot(111)
        layout.addWidget(self._canvas, stretch=1)

        self._draw_initial(ylabel, style)
        self._wire_signals()

    # ---------------- Initial draw ----------------

    def _draw_initial(self, ylabel: str, style: dict) -> None:
        for label, color, values in self.series:
            ln, = self._ax.plot(self.times, values,
                                label=label, color=color, **style)
            self._lines.append(ln)
            self._line_for_label[label] = ln

        self._trigger_line = self._ax.axvline(0, **_TRIGGER_LINE_STYLE)

        ylabel_full = f"{ylabel} ({self._unit})" if self._unit else ylabel
        self._ax.set_ylabel(ylabel_full)
        self._ax.set_xlabel("Time (ms)")
        self._ax.grid(True, alpha=0.3)
        self._ax.set_title(self._title, fontsize=9)

        leg = self._ax.legend(
            loc="upper left", bbox_to_anchor=(1.01, 1.0),
            fontsize=8, frameon=False, borderaxespad=0,
        )
        for legline, line in zip(leg.get_lines(), self._lines):
            legline.set_picker(True)
            legline.set_pickradius(8)
            self._legend_picks[id(legline)] = (legline, line)

        self._refresh_trigger_overlay()
        self._update_y_fields()
        self._canvas.draw_idle()

    def _wire_signals(self) -> None:
        self._ax.callbacks.connect("xlim_changed", self._on_xlim_changed)
        self._ax.callbacks.connect("ylim_changed", self._on_ylim_changed)
        self._canvas.mpl_connect("button_press_event", self._on_button_press)
        self._canvas.mpl_connect("pick_event",         self._on_legend_pick)

    # ---------------- X-axis ----------------

    def _on_xlim_changed(self, ax: Axes) -> None:
        xmin, xmax = ax.get_xlim()
        self.xlim_changed_by_user.emit(xmin, xmax)

    def set_xlim_quiet(self, xmin: float, xmax: float) -> None:
        # Re-emission is suppressed at the dialog level via `_x_sync_in_progress`.
        self._ax.set_xlim(xmin, xmax)
        self._refresh_trigger_overlay()
        self._canvas.draw_idle()

    def auto_x_range(self) -> tuple[float, float]:
        if not self.times:
            return (0.0, 1.0)
        xmin, xmax = self.times[0], self.times[-1]
        span = xmax - xmin or 1.0
        pad = span * _AUTO_X_PAD_FRAC
        return xmin - pad, xmax + pad

    # ---------------- Y-axis ----------------

    def auto_y(self) -> None:
        """Autoscale Y from currently-visible series only."""
        all_visible_values: list[float] = []
        for line, (_label, _color, values) in zip(self._lines, self.series):
            if line.get_visible():
                all_visible_values.extend(values)
        if not all_visible_values:
            return
        ymin = min(all_visible_values)
        ymax = max(all_visible_values)
        span = ymax - ymin or 1.0
        pad = span * _AUTO_Y_PAD_FRAC
        self._ax.set_ylim(ymin - pad, ymax + pad)
        self._refresh_trigger_overlay()
        self._update_y_fields()
        self._canvas.draw_idle()

    def _on_apply_y(self, ymin: float, ymax: float) -> None:
        if ymax <= ymin:
            return
        self._ax.set_ylim(ymin, ymax)
        self._refresh_trigger_overlay()
        self._canvas.draw_idle()

    def _on_ylim_changed(self, _ax: Axes) -> None:
        self._update_y_fields()
        self._refresh_trigger_overlay()

    def _update_y_fields(self) -> None:
        ymin, ymax = self._ax.get_ylim()
        self.y_controls.update_fields(ymin, ymax)

    # ---------------- Trigger overlay ----------------

    def _refresh_trigger_overlay(self) -> None:
        for attr in ("_trigger_band", "_trigger_label", "_view_label"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.remove()
                except (ValueError, AttributeError):
                    pass
                setattr(self, attr, None)

        self._trigger_band = self._ax.axvspan(
            -_TRIGGER_BAND_HALFWIDTH_MS, _TRIGGER_BAND_HALFWIDTH_MS,
            color="red", alpha=_TRIGGER_BAND_ALPHA, zorder=0,
        )
        ymin, ymax = self._ax.get_ylim()
        self._trigger_label = self._ax.text(
            0, ymax - (ymax - ymin) * 0.04,
            "TRIGGER  t = 0.0 ms",
            **_TRIGGER_LABEL_KW,
        )
        xmin, xmax = self._ax.get_xlim()
        span_ms   = xmax - xmin
        half_ms   = span_ms / 2.0
        # Show window relative to trigger when zero is roughly centered, else
        # show plain min…max — same data, more useful framing for the user.
        if abs(xmin + xmax) < 0.05 * (abs(xmin) + abs(xmax) + 1.0):
            text = f"view: ±{half_ms:.1f} ms  ({span_ms:.1f} ms span)"
        else:
            text = f"view: {xmin:+.1f} … {xmax:+.1f} ms  ({span_ms:.1f} ms)"
        self._view_label = self._ax.text(
            0.995, 0.965, text,
            transform=self._ax.transAxes, **_VIEW_LABEL_KW,
        )

    # ---------------- Point markers ----------------

    def set_point_markers(self, on: bool) -> None:
        for line in self._lines:
            if on:
                line.set_marker(_POINT_MARKER_KW["marker"])
                line.set_markersize(_POINT_MARKER_KW["markersize"])
                line.set_markeredgewidth(_POINT_MARKER_KW["markeredgewidth"])
            else:
                line.set_marker("")
        self._canvas.draw_idle()

    # ---------------- Mouse: marker click + legend pick ----------------

    def _on_button_press(self, event: MouseEvent) -> None:
        if event.inaxes is not self._ax or event.xdata is None:
            return
        # A pick_event for the legend fires *before* button_press_event for
        # the same physical click; if the legend swallowed it, suppress the
        # marker placement that would otherwise also fire.
        if self._suppress_next_click:
            self._suppress_next_click = False
            return
        if event.button in (1, 3):
            self.clicked.emit(int(event.button), float(event.xdata))

    def _on_legend_pick(self, event: PickEvent) -> None:
        artist = event.artist
        record = self._legend_picks.get(id(artist))
        if record is None:
            return
        legline, dataline = record
        new_visible = not dataline.get_visible()
        dataline.set_visible(new_visible)
        legline.set_alpha(1.0 if new_visible else _HIDDEN_LEGEND_ALPHA)
        # The matching button_press_event will fire next; tell _on_button_press
        # to ignore it so the legend toggle doesn't also drop a marker.
        self._suppress_next_click = True
        self._canvas.draw_idle()

    # ---------------- Series visibility ----------------

    def set_series_visible(self, label: str, visible: bool) -> None:
        line = self._line_for_label.get(label)
        if line is None:
            return
        line.set_visible(visible)
        for legline, dataline in self._legend_picks.values():
            if dataline is line:
                legline.set_alpha(1.0 if visible else _HIDDEN_LEGEND_ALPHA)
                break
        self._canvas.draw_idle()

    # ---------------- Marker drawing (called by dialog) ----------------

    def draw_marker(self, slot: int, x_snap: float) -> Line2D:
        return self._ax.axvline(
            x_snap, color=_MARKER_COLORS[slot],
            linewidth=1.1, linestyle=":", zorder=4,
        )

    def remove_marker(self, line: Line2D) -> None:
        try:
            line.remove()
        except (ValueError, AttributeError):
            pass

    def redraw(self) -> None:
        self._canvas.draw_idle()


# ---------------------------------------------------------------------------
# Marker model
# ---------------------------------------------------------------------------

@dataclass
class _MarkerGroup:
    """Two-cursor marker state for a set of plot rows that share a time axis."""
    name:    str
    rows:    list[_PlotRow]
    times:   list[float]
    series:  list[tuple[str, str, list[float]]] = field(default_factory=list)
    columns_per_line: int = 4
    xs:        list[float]                       = field(default_factory=list)
    indices:   list[int]                         = field(default_factory=list)
    line_sets: list[list[tuple[_PlotRow, Line2D]]] = field(default_factory=list)
    next_slot: int = 0


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------

class CaptureViewDialog(QDialog):
    def __init__(self, session: CaptureSession, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(f"Capture — {session.session_id} · Analysis Mode")
        # Caller is expected to call showMaximized(); the resize hint is a
        # safe fallback for show() during isolated tests.
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(int(screen.width() * 0.95), int(screen.height() * 0.95))

        self._session_id         = session.session_id
        self._x_sync_in_progress = False
        self._point_markers_on   = False

        self._build_data(session)
        self._setup_ui()
        self._wire_x_sync()
        self._compute_initial_xlim()

    # ---------------- Data prep ----------------

    def _build_data(self, session: CaptureSession) -> None:
        done = session.arduino_done
        ds   = session.dist_status
        period_ade  = done.sample_period_ms or 10
        period_dist = ds.sample_period_ms   or 25

        self._t_ade  = [s.i * period_ade for s in session.arduino_samples]
        self._t_dist = [
            (idx - ds.trigger_idx) * period_dist
            for idx, _, _ in session.dist_samples
        ]

        self._volt_series: list[tuple[str, str, list[float]]] = [
            (label, color,
             [getattr(s, attr) for s in session.arduino_samples])
            for attr, label, color in _VOLT_SERIES
        ]
        self._curr_series: list[tuple[str, str, list[float]]] = [
            (label, color,
             [getattr(s, attr) for s in session.arduino_samples])
            for attr, label, color in _CURR_SERIES
        ]
        self._dist_series: list[tuple[str, str, list[float]]] = []
        if session.dist_samples:
            for ch_idx, key in enumerate(CHANNEL_KEYS):
                vals = [float(raw_ints[ch_idx])
                        for _, raw_ints, _ in session.dist_samples]
                self._dist_series.append((key, f"C{ch_idx}", vals))

        self._session_meta = (
            f"ADE9000  {len(session.arduino_samples)} samples @ {period_ade} ms"
            f"  |  offset_ad = {session.offset_ad_ms:+.1f} ms"
            f"  ||  Distribution  {len(session.dist_samples)} samples @ {period_dist} ms"
        )

    # ---------------- Layout ----------------

    def _setup_ui(self) -> None:
        main = QVBoxLayout(self)
        main.setContentsMargins(6, 6, 6, 6)
        main.setSpacing(4)

        main.addWidget(self._build_header())
        main.addWidget(self._build_top_bar())

        self._row_v = _PlotRow(
            title="ADE9000 voltages", ylabel="Voltage", unit="V",
            section_title="Voltage scale",
            series=self._volt_series, times=self._t_ade,
            style=_PRIMARY_STYLE, parent=self,
        )
        self._row_i = _PlotRow(
            title="ADE9000 currents", ylabel="Current", unit="A",
            section_title="Current scale",
            series=self._curr_series, times=self._t_ade,
            style=_PRIMARY_STYLE, parent=self,
        )
        self._row_d = _PlotRow(
            title=f"Distribution ADC  ({self._session_meta})",
            ylabel="ADC", unit="raw int16",
            section_title="ADC scale",
            series=self._dist_series, times=self._t_dist,
            style=_SECONDARY_STYLE, parent=self,
        )

        main.addWidget(self._row_v, stretch=2)
        main.addWidget(self._row_i, stretch=1)
        main.addWidget(self._row_d, stretch=2)

        main.addWidget(self._build_x_bar())
        main.addWidget(self._build_status_panel())

        # Marker groups straddle rows.
        ade_marker_series = (
            [(label, "V", values) for label, _color, values in self._volt_series]
            + [(label, "A", values) for label, _color, values in self._curr_series]
        )
        dist_marker_series = [
            (label, "", values) for label, _color, values in self._dist_series
        ]
        self._mgroups = [
            _MarkerGroup(
                name="ADE9000",
                rows=[self._row_v, self._row_i],
                times=self._t_ade,
                series=ade_marker_series,
                columns_per_line=3,
            ),
            _MarkerGroup(
                name="Distribution",
                rows=[self._row_d],
                times=self._t_dist,
                series=dist_marker_series,
                columns_per_line=4,
            ),
        ]

    # ---- Header (Back / Fullscreen / title / Reset View) ----

    def _build_header(self) -> QWidget:
        bar = QFrame()
        bar.setFrameShape(QFrame.Shape.StyledPanel)
        bar.setStyleSheet(
            "QFrame { background: #2b3340; }"
            "QLabel { color: #e8eaed; }"
            "QPushButton { color: #e8eaed; background: #3a4150; "
            "  border: 1px solid #4a5160; padding: 4px 12px; border-radius: 3px; }"
            "QPushButton:hover { background: #4a5160; }"
            "QPushButton:checked { background: #1976d2; border-color: #1976d2; }"
        )
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 5, 8, 5)
        h.setSpacing(8)

        self._back_btn = QPushButton("← Back")
        self._back_btn.setToolTip("Close the viewer and return to the main window (Esc)")
        self._back_btn.clicked.connect(self._on_back)
        h.addWidget(self._back_btn)

        self._fullscreen_btn = QPushButton("Fullscreen")
        self._fullscreen_btn.setCheckable(True)
        self._fullscreen_btn.setShortcut("F11")
        self._fullscreen_btn.setToolTip("Toggle full-screen (F11)")
        self._fullscreen_btn.toggled.connect(self._on_fullscreen_toggled)
        h.addWidget(self._fullscreen_btn)

        h.addStretch(1)
        title = QLabel(f"Capture — {self._session_id}  ·  Analysis Mode")
        title.setStyleSheet("font-weight: 600; font-size: 14px; color: #e8eaed;")
        h.addWidget(title)
        h.addStretch(1)

        self._reset_btn = QPushButton("Reset View")
        self._reset_btn.setToolTip("Auto-scale X and Y on every plot")
        self._reset_btn.clicked.connect(self._on_reset_view)
        h.addWidget(self._reset_btn)
        return bar

    # ---- Top bar (split into two rows) ----

    def _build_top_bar(self) -> QWidget:
        bar = QFrame()
        bar.setFrameShape(QFrame.Shape.StyledPanel)
        v = QVBoxLayout(bar)
        v.setContentsMargins(6, 4, 6, 4)
        v.setSpacing(3)
        v.addLayout(self._build_focus_row())
        v.addLayout(self._build_channels_row())
        return bar

    def _build_focus_row(self) -> QHBoxLayout:
        h = QHBoxLayout()
        h.setSpacing(6)

        head = QLabel("Focus:")
        head.setStyleSheet("font-weight: 600;")
        h.addWidget(head)
        self._focus_btns = QButtonGroup(self)
        self._focus_btns.setExclusive(True)
        for i, name in enumerate(("All", "V", "I", "ADC")):
            btn = QToolButton()
            btn.setText(name)
            btn.setCheckable(True)
            if i == 0:
                btn.setChecked(True)
            self._focus_btns.addButton(btn, i)
            h.addWidget(btn)
        self._focus_btns.idClicked.connect(self._on_focus_changed)

        h.addSpacing(16)
        self._point_markers_cb = QCheckBox("Point markers")
        self._point_markers_cb.setChecked(self._point_markers_on)
        self._point_markers_cb.setToolTip(
            "Highlight every sample as a small dot on every line")
        self._point_markers_cb.toggled.connect(self._on_point_markers_toggled)
        h.addWidget(self._point_markers_cb)

        h.addStretch(1)
        return h

    def _build_channels_row(self) -> QHBoxLayout:
        h = QHBoxLayout()
        h.setSpacing(4)

        head = QLabel("ADC channels:")
        head.setStyleSheet("font-weight: 600;")
        h.addWidget(head)
        self._ch_checks: dict[str, QCheckBox] = {}
        for key in CHANNEL_KEYS:
            cb = QCheckBox(key)
            cb.setChecked(True)
            cb.toggled.connect(
                lambda v, k=key: self._row_d.set_series_visible(k, v)
            )
            self._ch_checks[key] = cb
            h.addWidget(cb)

        h.addSpacing(8)
        for label, fn in (
            ("All",  self._adc_preset_all),
            ("u17",  self._adc_preset_u17),
            ("u18",  self._adc_preset_u18),
            ("None", self._adc_preset_none),
        ):
            btn = QToolButton()
            btn.setText(label)
            btn.clicked.connect(fn)
            h.addWidget(btn)

        h.addStretch(1)
        return h

    # ---- X bar (split into time-range + trigger rows) ----

    def _build_x_bar(self) -> QWidget:
        bar = QFrame()
        bar.setFrameShape(QFrame.Shape.StyledPanel)
        v = QVBoxLayout(bar)
        v.setContentsMargins(6, 4, 6, 4)
        v.setSpacing(3)
        v.addLayout(self._build_time_range_row())
        v.addLayout(self._build_trigger_row())
        return bar

    def _build_time_range_row(self) -> QHBoxLayout:
        h = QHBoxLayout()
        h.setSpacing(6)

        head = QLabel("Time range:")
        head.setStyleSheet("font-weight: 600;")
        head.setMinimumWidth(90)
        h.addWidget(head)

        h.addWidget(QLabel("X min"))
        self._xmin_spin = self._make_x_spin()
        h.addWidget(self._xmin_spin)
        h.addWidget(QLabel("X max"))
        self._xmax_spin = self._make_x_spin()
        h.addWidget(self._xmax_spin)

        btn_apply = QPushButton("Apply X")
        btn_apply.clicked.connect(self._on_apply_x)
        h.addWidget(btn_apply)
        btn_auto = QPushButton("Auto X")
        btn_auto.clicked.connect(self._on_auto_x)
        h.addWidget(btn_auto)
        h.addStretch(1)
        return h

    def _build_trigger_row(self) -> QHBoxLayout:
        h = QHBoxLayout()
        h.setSpacing(6)

        head = QLabel("Trigger:")
        head.setStyleSheet("font-weight: 600;")
        head.setMinimumWidth(90)
        h.addWidget(head)

        h.addWidget(QLabel("± half-window"))
        self._trig_window_spin = QDoubleSpinBox()
        self._trig_window_spin.setRange(0.5, 100000.0)
        self._trig_window_spin.setDecimals(1)
        self._trig_window_spin.setValue(50.0)
        self._trig_window_spin.setSuffix(" ms")
        h.addWidget(self._trig_window_spin)

        btn_trig = QPushButton("Centre on trigger")
        btn_trig.setToolTip("Centre X on the trigger with the given half-width")
        btn_trig.clicked.connect(self._on_trigger_window)
        h.addWidget(btn_trig)
        h.addStretch(1)
        return h

    @staticmethod
    def _make_x_spin() -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(-1e9, 1e9)
        s.setDecimals(2)
        s.setSingleStep(10.0)
        s.setSuffix(" ms")
        return s

    # ---- Status panel (markers only) ----

    def _build_status_panel(self) -> QWidget:
        box = QFrame()
        box.setFrameShape(QFrame.Shape.StyledPanel)
        # Reserve a stable height so the plots above do not shift up/down
        # when markers are placed or cleared.
        box.setFixedHeight(150)
        h = QHBoxLayout(box)
        h.setContentsMargins(6, 4, 6, 4)
        h.setSpacing(8)

        markers_head = QLabel("Markers")
        markers_head.setStyleSheet("font-weight: 600;")
        markers_head.setAlignment(Qt.AlignTop)
        h.addWidget(markers_head)

        self._marker_lbl = QLabel(_HINT_TEXT)
        # No hardcoded text colour — let the active Qt palette drive it so
        # the text stays readable on both light and dark themes.
        self._marker_lbl.setStyleSheet(
            "font-family: 'Consolas','Cascadia Mono',monospace;"
        )
        self._marker_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._marker_lbl.setWordWrap(True)
        self._marker_lbl.setAlignment(Qt.AlignTop)
        h.addWidget(self._marker_lbl, stretch=1)
        return box

    # ---------------- X-axis sync ----------------

    def _wire_x_sync(self) -> None:
        for row in self._all_rows():
            row.xlim_changed_by_user.connect(self._on_row_xlim_changed)
            row.clicked.connect(
                lambda btn, x, r=row: self._on_row_clicked(r, btn, x)
            )

    def _all_rows(self) -> list[_PlotRow]:
        return [self._row_v, self._row_i, self._row_d]

    def _on_row_xlim_changed(self, xmin: float, xmax: float) -> None:
        if self._x_sync_in_progress:
            return
        self._x_sync_in_progress = True
        try:
            for row in self._all_rows():
                cur = row._ax.get_xlim()
                if cur != (xmin, xmax):
                    row.set_xlim_quiet(xmin, xmax)
            self._update_x_fields(xmin, xmax)
        finally:
            self._x_sync_in_progress = False

    def _set_all_xlim(self, xmin: float, xmax: float) -> None:
        if xmax <= xmin:
            return
        self._x_sync_in_progress = True
        try:
            for row in self._all_rows():
                row.set_xlim_quiet(xmin, xmax)
            self._update_x_fields(xmin, xmax)
        finally:
            self._x_sync_in_progress = False

    def _update_x_fields(self, xmin: float, xmax: float) -> None:
        for spin, val in ((self._xmin_spin, xmin), (self._xmax_spin, xmax)):
            blocker = QSignalBlocker(spin)
            spin.setValue(val)
            del blocker

    def _compute_initial_xlim(self) -> None:
        ends: list[float] = []
        for row in self._all_rows():
            if row.times:
                ends.append(row.times[0])
                ends.append(row.times[-1])
        if not ends:
            return
        xmin, xmax = min(ends), max(ends)
        span = xmax - xmin or 1.0
        pad = span * _AUTO_X_PAD_FRAC
        self._set_all_xlim(xmin - pad, xmax + pad)

    # ---------------- X actions ----------------

    def _on_apply_x(self) -> None:
        self._set_all_xlim(self._xmin_spin.value(), self._xmax_spin.value())

    def _on_auto_x(self) -> None:
        self._compute_initial_xlim()

    def _on_trigger_window(self) -> None:
        w = self._trig_window_spin.value()
        self._set_all_xlim(-w, +w)

    def _on_reset_view(self) -> None:
        self._compute_initial_xlim()
        for row in self._all_rows():
            row.auto_y()

    # ---------------- Header actions ----------------

    def _on_back(self) -> None:
        self.close()

    def _on_fullscreen_toggled(self, on: bool) -> None:
        # Maximized = title bar visible, OS chrome intact;
        # FullScreen   = covers the whole display (kiosk-style).
        if on:
            self.showFullScreen()
        else:
            self.showMaximized()

    # ---------------- Focus mode ----------------

    def _on_focus_changed(self, idx: int) -> None:
        # 0=All, 1=V, 2=I, 3=ADC
        self._row_v.setVisible(idx in (0, 1))
        self._row_i.setVisible(idx in (0, 2))
        self._row_d.setVisible(idx in (0, 3))

    # ---------------- Global toggles ----------------

    def _on_point_markers_toggled(self, on: bool) -> None:
        self._point_markers_on = on
        for row in self._all_rows():
            row.set_point_markers(on)

    # ---------------- ADC channel filter presets ----------------

    def _adc_preset(self, mask: dict[str, bool]) -> None:
        for key, on in mask.items():
            cb = self._ch_checks[key]
            blocker = QSignalBlocker(cb)
            cb.setChecked(on)
            del blocker
            self._row_d.set_series_visible(key, on)

    def _adc_preset_all(self) -> None:
        self._adc_preset({k: True for k in CHANNEL_KEYS})

    def _adc_preset_u17(self) -> None:
        self._adc_preset({k: k.startswith("u17_") for k in CHANNEL_KEYS})

    def _adc_preset_u18(self) -> None:
        self._adc_preset({k: k.startswith("u18_") for k in CHANNEL_KEYS})

    def _adc_preset_none(self) -> None:
        self._adc_preset({k: False for k in CHANNEL_KEYS})

    # ---------------- Markers ----------------

    def _on_row_clicked(self, row: _PlotRow, button: int, x_raw: float) -> None:
        group = next((g for g in self._mgroups if row in g.rows), None)
        if group is None or not group.times:
            return
        if button == 3:
            self._clear_group(group)
        elif button == 1:
            self._place_marker(group, x_raw)
        self._update_marker_label()

    def _place_marker(self, group: _MarkerGroup, x_raw: float) -> None:
        idx    = _snap_index(group.times, x_raw)
        x_snap = group.times[idx]
        slot   = group.next_slot
        new_lines = [(r, r.draw_marker(slot, x_snap)) for r in group.rows]

        if slot < len(group.xs):
            for r, ln in group.line_sets[slot]:
                r.remove_marker(ln)
            group.xs[slot]        = x_snap
            group.indices[slot]   = idx
            group.line_sets[slot] = new_lines
        else:
            group.xs.append(x_snap)
            group.indices.append(idx)
            group.line_sets.append(new_lines)

        for r in group.rows:
            r.redraw()
        group.next_slot = (slot + 1) % 2

    def _clear_group(self, group: _MarkerGroup) -> None:
        for line_set in group.line_sets:
            for r, ln in line_set:
                r.remove_marker(ln)
        group.xs.clear()
        group.indices.clear()
        group.line_sets.clear()
        group.next_slot = 0
        for r in group.rows:
            r.redraw()

    def _update_marker_label(self) -> None:
        out_lines: list[str] = []
        any_markers = False
        for g in self._mgroups:
            if not g.xs:
                continue
            any_markers = True
            for k, (x, idx) in enumerate(zip(g.xs, g.indices)):
                head = f"  {g.name:<13}M{k + 1}   t = {x:+9.2f} ms"
                out_lines.append(head)
                vals = [_format_value(label, unit, values, idx)
                        for (label, unit, values) in g.series]
                out_lines.extend(
                    _wrap_columns(vals, g.columns_per_line, " " * 23)
                )
            if len(g.xs) == 2:
                dt = g.xs[1] - g.xs[0]
                out_lines.append(
                    f"  {g.name:<13}Δt   = {dt:+9.2f} ms"
                )
            out_lines.append("")
        text = "\n".join(out_lines).rstrip() if any_markers else _HINT_TEXT
        self._marker_lbl.setText(text)
