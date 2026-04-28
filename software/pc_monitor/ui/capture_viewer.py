"""Post-session data viewer — matplotlib embed in QDialog.

Opens after a successful capture session. Three subplots:
  1. ADE9000 line voltages  Uab / Ubc / Uca  (V)
  2. ADE9000 phase currents Ia  / Ib  / Ic   (A)
  3. Distribution ADC channels  u17_ch0..u18_ch3  (signed int16)

Both time axes are in ms from each device's own trigger (t = 0).
The title of subplot 1 shows offset_ad_ms — residual cross-device misalignment.
"""
from __future__ import annotations

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout

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
_TRIGGER_STYLE = dict(color="red", linestyle="--", linewidth=0.9, label="trigger")


class CaptureViewDialog(QDialog):
    def __init__(self, session: CaptureSession, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(f"Capture — {session.session_id}")
        self.resize(1100, 780)

        fig = Figure(figsize=(11, 8), tight_layout=True)
        canvas = FigureCanvasQTAgg(fig)
        toolbar = NavigationToolbar2QT(canvas, self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)
        layout.addWidget(toolbar)
        layout.addWidget(canvas)

        self._draw(fig, session)
        canvas.draw()

    # ------------------------------------------------------------------

    def _draw(self, fig: Figure, session: CaptureSession) -> None:
        done = session.arduino_done
        ds   = session.dist_status

        period_ade  = done.sample_period_ms  or 10
        period_dist = ds.sample_period_ms    or 25

        # ms from each device's own trigger
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
