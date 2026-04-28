"""Post-session data viewer — matplotlib embed in QDialog with cursor markers.

Three subplots:
  1. ADE9000 line voltages  Uab / Ubc / Uca  (V)
  2. ADE9000 phase currents Ia  / Ib  / Ic   (A)
  3. Distribution ADC channels  u17_ch0..u18_ch3  (signed int16)

Voltages and currents share the ADE9000 time axis; Distribution has its own.
Both time axes are in ms from each device's own trigger (t = 0).

Two cursor markers (M1, M2) per plot group. Left-click cycles them, right-click
clears the markers in the clicked group. Clicks snap to the nearest sample
on that group's time axis so the readout reflects an actual measurement, not
an interpolated mouse position. The status panel shows per-marker timestamp
plus every series' value at that sample, and Δt when both markers are placed.
Matplotlib's NavigationToolbar provides pan/zoom; clicks are ignored while
pan or zoom mode is active.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Optional

from matplotlib.axes import Axes
from matplotlib.backend_bases import MouseEvent
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

from core.distribution_client import CHANNEL_KEYS
from core.orchestrator import CaptureSession

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
_TRIGGER_STYLE  = dict(color="red", linestyle="--", linewidth=0.9, label="trigger")
_MARKER_COLORS  = ("#00bcd4", "#e91e63")   # M1 cyan, M2 pink
_MARKER_HINT    = (
    "Left-click on a plot to place a marker (cycles M1 ↔ M2). "
    "Right-click clears the markers in that group. Pan/zoom in toolbar."
)


@dataclass
class _MarkerGroup:
    """Markers for one set of axes that share the same X-axis (sample times).

    `times` is the per-sample X array used for snap-to-nearest. `series` is
    the parallel set of (label, unit, values) tuples plotted in this group;
    a marker's Y-readout is just `values[idx]` for each one. `xs[k]` /
    `indices[k]` are the snapped X / sample-index of the k-th placed marker
    (k=0 → M1, k=1 → M2). `lines[k]` is the list of axvlines drawn on each
    axis for that marker, kept so we can remove them on re-place / clear.
    """
    name:      str
    axes:      list[Axes]
    times:     list[float]                      = field(default_factory=list)
    series:    list[tuple[str, str, list[float]]] = field(default_factory=list)
    xs:        list[float]                      = field(default_factory=list)
    indices:   list[int]                        = field(default_factory=list)
    lines:     list[list[Line2D]]               = field(default_factory=list)
    next_slot: int                              = 0


def _snap_index(times: list[float], x: float) -> int:
    """Return the index in `times` whose value is closest to `x`.

    `times` must be sorted ascending. For empty input returns 0 (caller is
    expected to guard on that, but the safety is cheap).
    """
    n = len(times)
    if n == 0:
        return 0
    if x <= times[0]:
        return 0
    if x >= times[-1]:
        return n - 1
    i = bisect.bisect_left(times, x)
    # i is the first index with times[i] >= x; compare against times[i-1].
    if i == 0:
        return 0
    return i if abs(times[i] - x) < abs(times[i - 1] - x) else i - 1


class CaptureViewDialog(QDialog):
    def __init__(self, session: CaptureSession, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(f"Capture — {session.session_id}")

        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(int(screen.width() * 0.9), int(screen.height() * 0.9))

        fig = Figure(tight_layout=True)
        self._canvas  = FigureCanvasQTAgg(fig)
        self._toolbar = NavigationToolbar2QT(self._canvas, self)

        self._status = QLabel(_MARKER_HINT)
        self._status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._status.setWordWrap(True)
        self._status.setStyleSheet("padding: 4px 6px; font-family: monospace;")
        self._status.setAlignment(Qt.AlignTop)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(self._toolbar)
        layout.addWidget(self._canvas, stretch=1)
        layout.addWidget(self._status)

        self._groups: list[_MarkerGroup] = []
        self._draw(fig, session)
        self._canvas.draw()
        self._canvas.mpl_connect("button_press_event", self._on_click)

    # ------------------------------------------------------------------

    def _draw(self, fig: Figure, session: CaptureSession) -> None:
        done = session.arduino_done
        ds   = session.dist_status

        period_ade  = done.sample_period_ms  or 10
        period_dist = ds.sample_period_ms    or 25

        t_ade  = [s.i * period_ade for s in session.arduino_samples]
        t_dist = [
            (idx - ds.trigger_idx) * period_dist
            for idx, _, _ in session.dist_samples
        ]

        # Per-series value lists used both for plotting and for marker Y-readout.
        ade_series: list[tuple[str, str, list[float]]] = []
        for attr, label, _color in _VOLT_SERIES:
            ade_series.append(
                (label, "V", [getattr(s, attr) for s in session.arduino_samples])
            )
        for attr, label, _color in _CURR_SERIES:
            ade_series.append(
                (label, "A", [getattr(s, attr) for s in session.arduino_samples])
            )

        dist_series: list[tuple[str, str, list[float]]] = []
        if session.dist_samples:
            for k, key in enumerate(CHANNEL_KEYS):
                vals = [raw_ints[k] for _, raw_ints, _ in session.dist_samples]
                dist_series.append((key, "", [float(v) for v in vals]))

        ax_v, ax_i, ax_d = fig.subplots(
            3, 1,
            gridspec_kw={"height_ratios": [2, 1, 2]},
        )
        ax_i.sharex(ax_v)

        # --- subplot 1: voltages ---
        for (attr, label, color), (_lbl, _unit, vals) in zip(
            _VOLT_SERIES, ade_series[:3]
        ):
            ax_v.plot(t_ade, vals, label=label, color=color, linewidth=0.9)
        ax_v.axvline(0, **_TRIGGER_STYLE)
        ax_v.set_ylabel("Voltage (V)")
        ax_v.legend(loc="upper right", fontsize=8, ncol=4)
        ax_v.grid(True, alpha=0.3)
        ax_v.set_title(
            f"ADE9000  |  {len(session.arduino_samples)} samples @ {period_ade} ms"
            f"  |  offset_ad = {session.offset_ad_ms:+.1f} ms",
            fontsize=9,
        )

        # --- subplot 2: currents ---
        for (attr, label, color), (_lbl, _unit, vals) in zip(
            _CURR_SERIES, ade_series[3:]
        ):
            ax_i.plot(t_ade, vals, label=label, color=color, linewidth=0.9)
        ax_i.axvline(0, color="red", linestyle="--", linewidth=0.9)
        ax_i.set_ylabel("Current (A)")
        ax_i.set_xlabel("Time from ADE9000 trigger (ms)")
        ax_i.legend(loc="upper right", fontsize=8, ncol=3)
        ax_i.grid(True, alpha=0.3)

        # --- subplot 3: Distribution ---
        for i, (label, _unit, vals) in enumerate(dist_series):
            ax_d.plot(t_dist, vals, label=label, color=f"C{i}", linewidth=0.9)
        ax_d.axvline(0, **_TRIGGER_STYLE)
        ax_d.set_ylabel("ADC (raw int16)")
        ax_d.set_xlabel("Time from Distribution trigger (ms)")
        ax_d.legend(loc="upper right", fontsize=8, ncol=2)
        ax_d.grid(True, alpha=0.3)
        ax_d.set_title(
            f"Distribution  |  {len(session.dist_samples)} samples @ {period_dist} ms",
            fontsize=9,
        )

        self._groups = [
            _MarkerGroup(
                name="ADE9000",
                axes=[ax_v, ax_i],
                times=t_ade,
                series=ade_series,
            ),
            _MarkerGroup(
                name="Distribution",
                axes=[ax_d],
                times=t_dist,
                series=dist_series,
            ),
        ]

    # ------------------------------------------------------------------
    # Marker handling

    def _group_for_axes(self, ax: Axes) -> Optional[_MarkerGroup]:
        for g in self._groups:
            if ax in g.axes:
                return g
        return None

    def _on_click(self, event: MouseEvent) -> None:
        if event.inaxes is None or event.xdata is None:
            return
        # Skip while pan/zoom is engaged so cursor doesn't fight the toolbar.
        if self._toolbar.mode:
            return
        group = self._group_for_axes(event.inaxes)
        if group is None or not group.times:
            return
        if event.button == 3:
            self._clear_group(group)
        elif event.button == 1:
            self._place_marker(group, float(event.xdata))
        self._canvas.draw_idle()
        self._update_status()

    def _place_marker(self, group: _MarkerGroup, x_raw: float) -> None:
        idx     = _snap_index(group.times, x_raw)
        x_snap  = group.times[idx]
        slot    = group.next_slot
        new_lines = [
            ax.axvline(x_snap, color=_MARKER_COLORS[slot],
                       linewidth=1.1, linestyle=":")
            for ax in group.axes
        ]
        if slot < len(group.xs):
            for line in group.lines[slot]:
                line.remove()
            group.xs[slot]      = x_snap
            group.indices[slot] = idx
            group.lines[slot]   = new_lines
        else:
            group.xs.append(x_snap)
            group.indices.append(idx)
            group.lines.append(new_lines)
        group.next_slot = (slot + 1) % 2

    def _clear_group(self, group: _MarkerGroup) -> None:
        for line_set in group.lines:
            for line in line_set:
                line.remove()
        group.xs.clear()
        group.indices.clear()
        group.lines.clear()
        group.next_slot = 0

    def _update_status(self) -> None:
        """Render every group's marker block as a multi-line readout.

        Layout per group:
            <Group> M1: t=±t.tt ms  L1=v u  L2=v u  …
            <Group> M2: t=…
            <Group> Δt=±t.tt ms
        """
        any_markers = False
        lines: list[str] = []
        for g in self._groups:
            if not g.xs:
                continue
            any_markers = True
            for k, (x, idx) in enumerate(zip(g.xs, g.indices)):
                series_parts = [
                    self._format_series_value(label, unit, values, idx)
                    for label, unit, values in g.series
                ]
                lines.append(
                    f"{g.name} M{k + 1}: t={x:+9.2f} ms"
                    + ("  " + "  ".join(series_parts) if series_parts else "")
                )
            if len(g.xs) == 2:
                dt = g.xs[1] - g.xs[0]
                lines.append(f"{g.name} Δt={dt:+9.2f} ms")
        self._status.setText("\n".join(lines) if any_markers else _MARKER_HINT)

    @staticmethod
    def _format_series_value(
        label: str, unit: str, values: list[float], idx: int,
    ) -> str:
        if idx < 0 or idx >= len(values):
            return f"{label}=—"
        v = values[idx]
        # Distribution channels are raw int16: integer formatting reads better
        # than decimals when the unit is empty.
        if unit:
            return f"{label}={v:+8.2f} {unit}"
        return f"{label}={int(round(v)):+6d}"
