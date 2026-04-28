"""Post-session data viewer — matplotlib embed in QDialog with cursor markers.

Three subplots:
  1. ADE9000 line voltages  Uab / Ubc / Uca  (V)
  2. ADE9000 phase currents Ia  / Ib  / Ic   (A)
  3. Distribution ADC channels  u17_ch0..u18_ch3  (signed int16)

Voltages and currents share the ADE9000 time axis; Distribution has its own.
Both time axes are in ms from each device's own trigger (t = 0).

Two cursor markers (M1, M2) per plot group. Left-click cycles them, right-click
clears the markers in the clicked group. Status bar shows positions and Δt.
Matplotlib's NavigationToolbar provides pan/zoom; clicks are ignored while
pan or zoom mode is active.
"""
from __future__ import annotations

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
    name: str
    axes: list[Axes]
    xs: list[float] = field(default_factory=list)
    lines: list[list[Line2D]] = field(default_factory=list)  # parallel to xs
    next_slot: int = 0


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

        ax_v, ax_i, ax_d = fig.subplots(
            3, 1,
            gridspec_kw={"height_ratios": [2, 1, 2]},
        )
        ax_i.sharex(ax_v)

        # --- subplot 1: voltages ---
        for attr, label, color in _VOLT_SERIES:
            ax_v.plot(
                t_ade,
                [getattr(s, attr) for s in session.arduino_samples],
                label=label, color=color, linewidth=0.9,
            )
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
        for attr, label, color in _CURR_SERIES:
            ax_i.plot(
                t_ade,
                [getattr(s, attr) for s in session.arduino_samples],
                label=label, color=color, linewidth=0.9,
            )
        ax_i.axvline(0, color="red", linestyle="--", linewidth=0.9)
        ax_i.set_ylabel("Current (A)")
        ax_i.set_xlabel("Time from ADE9000 trigger (ms)")
        ax_i.legend(loc="upper right", fontsize=8, ncol=3)
        ax_i.grid(True, alpha=0.3)

        # --- subplot 3: Distribution ---
        if session.dist_samples:
            ch_vals: list[list[int]] = [[] for _ in CHANNEL_KEYS]
            for _, raw_ints, _ in session.dist_samples:
                for k, v in enumerate(raw_ints):
                    ch_vals[k].append(v)
            for i, (key, vals) in enumerate(zip(CHANNEL_KEYS, ch_vals)):
                ax_d.plot(t_dist, vals, label=key, color=f"C{i}", linewidth=0.9)
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
            _MarkerGroup(name="ADE9000",      axes=[ax_v, ax_i]),
            _MarkerGroup(name="Distribution", axes=[ax_d]),
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
        if group is None:
            return
        if event.button == 3:
            self._clear_group(group)
        elif event.button == 1:
            self._place_marker(group, float(event.xdata))
        self._canvas.draw_idle()
        self._update_status()

    def _place_marker(self, group: _MarkerGroup, x: float) -> None:
        slot = group.next_slot
        new_lines = [
            ax.axvline(x, color=_MARKER_COLORS[slot], linewidth=1.1, linestyle=":")
            for ax in group.axes
        ]
        if slot < len(group.xs):
            for line in group.lines[slot]:
                line.remove()
            group.xs[slot]    = x
            group.lines[slot] = new_lines
        else:
            group.xs.append(x)
            group.lines.append(new_lines)
        group.next_slot = (slot + 1) % 2

    def _clear_group(self, group: _MarkerGroup) -> None:
        for line_set in group.lines:
            for line in line_set:
                line.remove()
        group.xs.clear()
        group.lines.clear()
        group.next_slot = 0

    def _update_status(self) -> None:
        segments: list[str] = []
        for g in self._groups:
            if not g.xs:
                continue
            parts = [
                f"M{i + 1}={x:+9.2f} ms"
                for i, x in enumerate(g.xs)
            ]
            if len(g.xs) == 2:
                parts.append(f"Δt={(g.xs[1] - g.xs[0]):+9.2f} ms")
            segments.append(f"{g.name}: " + "  ".join(parts))
        self._status.setText("     |     ".join(segments) if segments else _MARKER_HINT)
