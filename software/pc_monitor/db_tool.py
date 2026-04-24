"""
Distribution Tools — bring-up diagnostic tool for Distribution Board (RS-485/USB-Serial).

Target firmware: mps2p-FW-db-v3 / DistributionBoard_V3_G431.

Wire protocol (UART1, CRLF-terminated):
  Commands:  PING | STATUS | START | ARM | MODE CMD | MODE STREAM
             STREAM ON (alias for MODE STREAM) | STREAM OFF (alias for MODE CMD)
             EVENTS ON | EVENTS OFF            (Phase 5 async event toggle)
  Replies:
    PONG
    STATUS power=<0|1> vbus=<0|1> mode=<CMD|STREAM> trig=<tick>
    START ok | START already_on | START vbus_error | START error
    MODE CMD ok | MODE STREAM ok | ARM ok
    EVENTS ON ok | EVENTS OFF ok | ERR ...
  Stream (only while mode=STREAM, ~2 Hz from HighVoltageADC_Task):
    U17: CH0=%.2fV | CH1=%.2fV | CH2=%.2fV | CH3=%.2fV || U18: CH0=...
  Async events (only while EVENTS ON, suppressed during CAP READ window):
    EVT: vbus_block                   — RequestPowerOn refused (VBUS > 10 V)
    EVT: events_dropped=<N>           — flushed AFTER "CAP READ done" if N>0

Layers:
  SerialTransport      — raw serial I/O, background reader thread
  DistributionProtocol — command encoding, response/stream parsing (pure functions)
  MainApp              — Tkinter UI
"""

from __future__ import annotations

import csv
import json
import os
import queue
import re
import threading
import time
import tkinter as tk
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Optional

import serial
import serial.tools.list_ports

try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import (
        FigureCanvasTkAgg, NavigationToolbar2Tk,
    )
    _MPL_OK = True
except ImportError:
    _MPL_OK = False


# ---------------------------------------------------------------------------
# Scaling constants
# ---------------------------------------------------------------------------
# Matches HighVoltageADC_Task in mps2p-FW-db-v3/.../input_voltage_ads1115.c:
#   real_voltage = raw_int16 * (FS / 32768) * HV_SCALE
# where FS = 4.096 V (ADS1115 PGA=±4.096 V) and HV_SCALE folds the
# 9.98 MΩ : 10 kΩ divider + board calibration into one float.
HV_SCALE = 1957.8628
ADS_LSB_V = 4.096 / 32768.0
VOLTS_PER_COUNT = ADS_LSB_V * HV_SCALE  # ≈ 0.2447 V / count


# ---------------------------------------------------------------------------
# Persistent config (channel mapping)
# ---------------------------------------------------------------------------
CONFIG_PATH = Path(__file__).resolve().parent / "db_tool_config.json"

POINT_LABELS = ["V1_A", "V2_A", "V3_A", "V1_B", "V2_B", "V3_B"]

# Default physical-point → ADS-channel mapping. Working assumption for the
# current board revision; edit via "Channel mapping…" dialog and the choice
# is persisted in db_tool_config.json.
DEFAULT_CHANNEL_MAPPING: dict[str, str] = {
    "V1_A": "u17_ch0",
    "V2_A": "u17_ch1",
    "V3_A": "u17_ch2",
    "V1_B": "u18_ch0",
    "V2_B": "u18_ch1",
    "V3_B": "u18_ch2",
}


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {"channel_mapping": dict(DEFAULT_CHANNEL_MAPPING)}
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"channel_mapping": dict(DEFAULT_CHANNEL_MAPPING)}
    # Backfill any missing points with defaults so a partial config still works.
    mapping = dict(DEFAULT_CHANNEL_MAPPING)
    mapping.update(cfg.get("channel_mapping", {}))
    cfg["channel_mapping"] = mapping
    return cfg


def save_config(cfg: dict) -> None:
    try:
        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        # Non-fatal: config just won't persist this run.
        pass


# ---------------------------------------------------------------------------
# Transport Layer
# ---------------------------------------------------------------------------

class SerialTransport:
    """Raw serial open/close/send/receive with a background reader thread."""

    ENCODING = "ascii"
    LINE_TERM = "\r\n"

    def __init__(self, rx_queue: queue.Queue) -> None:
        self._port: Optional[serial.Serial] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.rx_queue = rx_queue

    def open(self, port: str, baudrate: int = 115200) -> None:
        if self._port and self._port.is_open:
            raise RuntimeError("Port already open")
        self._port = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.1,
        )
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._port and self._port.is_open:
            self._port.close()
        self._port = None

    @property
    def is_open(self) -> bool:
        return self._port is not None and self._port.is_open

    def send_line(self, line: str) -> None:
        if not self.is_open:
            raise RuntimeError("Port not open")
        raw = (line.rstrip("\r\n") + self.LINE_TERM).encode(self.ENCODING)
        self._port.write(raw)

    def _reader_loop(self) -> None:
        buf = b""
        while not self._stop_event.is_set():
            try:
                chunk = self._port.read(256)
            except serial.SerialException:
                self.rx_queue.put(("error", "Serial read error — device disconnected"))
                break
            if not chunk:
                continue
            buf += chunk
            # Split on either CR or LF so CRLF / LF / CR all work.
            while True:
                idx = -1
                for sep in (b"\r\n", b"\n", b"\r"):
                    i = buf.find(sep)
                    if i != -1 and (idx == -1 or i < idx):
                        idx = i
                        sep_len = len(sep)
                if idx == -1:
                    break
                line, buf = buf[:idx], buf[idx + sep_len:]
                text = line.decode(self.ENCODING, errors="ignore").strip()
                if text:
                    self.rx_queue.put(("rx", text))


# ---------------------------------------------------------------------------
# Protocol Layer
# ---------------------------------------------------------------------------

CHANNEL_KEYS = [
    "u17_ch0", "u17_ch1", "u17_ch2", "u17_ch3",
    "u18_ch0", "u18_ch1", "u18_ch2", "u18_ch3",
]


class DistributionProtocol:
    """Command strings and pure parsers for Distribution Board text protocol."""

    CMD_PING = "PING"
    CMD_STATUS = "STATUS"
    CMD_START = "START"
    CMD_ARM = "ARM"
    CMD_MODE_CMD = "MODE CMD"
    CMD_MODE_STREAM = "MODE STREAM"
    CMD_STREAM_ON = "STREAM ON"
    CMD_STREAM_OFF = "STREAM OFF"
    CMD_CAP_STATUS = "CAP STATUS"
    CMD_EVENTS_ON = "EVENTS ON"
    CMD_EVENTS_OFF = "EVENTS OFF"

    @staticmethod
    def cmd_cap_read(offset: int, count: int) -> str:
        return f"CAP READ {offset} {count}"

    _STREAM_RE = re.compile(
        r"U17:\s*CH0=(?P<u17_ch0>-?\d+(?:\.\d+)?)V"
        r"\s*\|\s*CH1=(?P<u17_ch1>-?\d+(?:\.\d+)?)V"
        r"\s*\|\s*CH2=(?P<u17_ch2>-?\d+(?:\.\d+)?)V"
        r"\s*\|\s*CH3=(?P<u17_ch3>-?\d+(?:\.\d+)?)V"
        r"\s*\|\|\s*U18:\s*CH0=(?P<u18_ch0>-?\d+(?:\.\d+)?)V"
        r"\s*\|\s*CH1=(?P<u18_ch1>-?\d+(?:\.\d+)?)V"
        r"\s*\|\s*CH2=(?P<u18_ch2>-?\d+(?:\.\d+)?)V"
        r"\s*\|\s*CH3=(?P<u18_ch3>-?\d+(?:\.\d+)?)V",
        re.IGNORECASE,
    )

    _STATUS_RE = re.compile(
        r"STATUS\s+power=(?P<power>\d+)\s+vbus=(?P<vbus>\d+)"
        r"\s+mode=(?P<mode>\w+)\s+trig=(?P<trig>\d+)",
        re.IGNORECASE,
    )

    _CAP_STATUS_RE = re.compile(
        r"CAP\s+STATUS\s+state=(?P<state>\w+)"
        r"\s+samples=(?P<samples>\d+)"
        r"\s+trigger_idx=(?P<trigger_idx>-?\d+)"
        r"\s+sample_period_ms=(?P<sample_period_ms>\d+)"
        r"\s+channels=(?P<channels>\d+)"
        r"\s+trigger_tick=(?P<trigger_tick>\d+)",
        re.IGNORECASE,
    )

    _CAP_SAMPLE_RE = re.compile(
        r"^(?P<idx>\d+)"
        + r"".join(rf"\s+(?P<ch{i}>[0-9A-Fa-f]{{4}})" for i in range(8))
        + r"\s*$"
    )

    _CAP_DONE_RE = re.compile(
        r"CAP\s+READ\s+done\s+count=(?P<count>\d+)",
        re.IGNORECASE,
    )

    # Phase 5 async events. Generic prefix is matched first; specific bodies
    # are classified against the body regexes below for future-proof handling
    # (unknown body → still logged as EVT, not swallowed).
    _EVT_PREFIX_RE = re.compile(r"^EVT:\s*(?P<body>.*)$", re.IGNORECASE)
    _EVT_VBUS_BLOCK_RE = re.compile(r"^vbus_block\s*$", re.IGNORECASE)
    _EVT_EVENTS_DROPPED_RE = re.compile(
        r"^events_dropped=(?P<n>\d+)\s*$", re.IGNORECASE
    )

    @staticmethod
    def parse_stream(line: str) -> Optional[dict[str, float]]:
        """Return {channel_key: volts} if line is a valid stream sample, else None."""
        m = DistributionProtocol._STREAM_RE.search(line)
        if not m:
            return None
        try:
            return {k: float(m.group(k)) for k in CHANNEL_KEYS}
        except ValueError:
            return None

    @staticmethod
    def parse_status(line: str) -> Optional[dict]:
        """Return dict with power/vbus/mode/trig if line is a STATUS reply, else None."""
        m = DistributionProtocol._STATUS_RE.search(line)
        if not m:
            return None
        return {
            "power": int(m.group("power")),
            "vbus": int(m.group("vbus")),
            "mode": m.group("mode").upper(),
            "trig": int(m.group("trig")),
        }

    @staticmethod
    def parse_cap_status(line: str) -> Optional[dict]:
        """Parse `CAP STATUS state=... samples=... trigger_idx=... sample_period_ms=... channels=... trigger_tick=...`."""
        m = DistributionProtocol._CAP_STATUS_RE.search(line)
        if not m:
            return None
        return {
            "state": m.group("state").upper(),
            "samples": int(m.group("samples")),
            "trigger_idx": int(m.group("trigger_idx")),
            "sample_period_ms": int(m.group("sample_period_ms")),
            "channels": int(m.group("channels")),
            "trigger_tick": int(m.group("trigger_tick")),
        }

    @staticmethod
    def parse_cap_sample(line: str) -> Optional[tuple[int, list[int], list[str]]]:
        """Parse `NNNNNN HHHH HHHH ...x8` → (idx, [int16]*8, [hex_uppercase]*8)."""
        m = DistributionProtocol._CAP_SAMPLE_RE.match(line)
        if not m:
            return None
        idx = int(m.group("idx"))
        hex_vals = [m.group(f"ch{i}").upper() for i in range(8)]
        int_vals = []
        for h in hex_vals:
            v = int(h, 16)
            if v >= 0x8000:
                v -= 0x10000
            int_vals.append(v)
        return idx, int_vals, hex_vals

    @staticmethod
    def parse_cap_done(line: str) -> Optional[int]:
        """Return sample count if line is `CAP READ done count=N`, else None."""
        m = DistributionProtocol._CAP_DONE_RE.search(line)
        return int(m.group("count")) if m else None

    @staticmethod
    def parse_evt(line: str) -> Optional[str]:
        """Return the trimmed body if `line` is an `EVT: <body>` notification,
        else None. Unknown bodies still return a string — only the prefix is
        required to match (forward compatibility with future EVT kinds)."""
        m = DistributionProtocol._EVT_PREFIX_RE.match(line)
        return m.group("body").strip() if m else None

    @staticmethod
    def parse_evt_events_dropped(body: str) -> Optional[int]:
        """Given an EVT body (already stripped of the `EVT: ` prefix), return
        the drop count if it matches `events_dropped=<N>`, else None."""
        m = DistributionProtocol._EVT_EVENTS_DROPPED_RE.match(body)
        return int(m.group("n")) if m else None


# ---------------------------------------------------------------------------
# Capture CSV loader + plot window
# ---------------------------------------------------------------------------

def load_capture_csv(path: Path) -> tuple[list[int], list[list[int]]]:
    """Read a capture CSV produced by `_save_capture_csv`.

    Returns (indices, raw_matrix) where raw_matrix[i][ch] is the signed int16
    count for sample i, channel ch (ch ∈ 0..7 in the U17.ch0..3, U18.ch0..3
    order used by the firmware). `ch*_hex` columns are ignored — `ch*_raw`
    is authoritative (the hex is only for byte-exact replay)."""
    indices: list[int] = []
    raw_matrix: list[list[int]] = []
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                idx = int(row["idx"])
                raws = [int(row[f"ch{i}_raw"]) for i in range(8)]
            except (KeyError, ValueError) as exc:
                raise ValueError(f"Bad row in {path.name}: {exc}") from exc
            indices.append(idx)
            raw_matrix.append(raws)
    return indices, raw_matrix


class ChannelMappingDialog(tk.Toplevel):
    """Modal dialog: assign each logical point (V1_A..V3_B) to an ADS channel."""

    def __init__(self, parent: tk.Tk, current_mapping: dict[str, str]) -> None:
        super().__init__(parent)
        self.title("Channel mapping")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.result: Optional[dict[str, str]] = None

        self._vars: dict[str, tk.StringVar] = {}
        options = list(CHANNEL_KEYS)

        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)

        ttk.Label(
            body,
            text="Assign physical measurement points to ADS1115 channels.\n"
                 "Derived: Module1 = V1 − V2, Module2 = V2 − V3, Total = V1 − V3.",
            foreground="#424242",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        for i, label in enumerate(POINT_LABELS):
            ttk.Label(body, text=label + ":").grid(
                row=i + 1, column=0, sticky="w", padx=(0, 8), pady=2
            )
            var = tk.StringVar(value=current_mapping.get(label, options[0]))
            self._vars[label] = var
            ttk.Combobox(
                body, textvariable=var, values=options, state="readonly", width=12,
            ).grid(row=i + 1, column=1, sticky="w", pady=2)

        btns = ttk.Frame(self, padding=(10, 0, 10, 10))
        btns.pack(fill="x")
        ttk.Button(btns, text="Reset to defaults",
                   command=self._on_reset).pack(side="left")
        ttk.Button(btns, text="Cancel", command=self._on_cancel).pack(side="right")
        ttk.Button(btns, text="Save", command=self._on_save).pack(side="right", padx=6)

        self.bind("<Escape>", lambda _e: self._on_cancel())
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def _on_reset(self) -> None:
        for label, var in self._vars.items():
            var.set(DEFAULT_CHANNEL_MAPPING[label])

    def _on_save(self) -> None:
        mapping = {label: var.get() for label, var in self._vars.items()}
        # Duplicates are allowed (e.g. V3_A == V3_B if the board only wires
        # five distinct points); we just warn, not block.
        used = list(mapping.values())
        if len(set(used)) < len(used):
            if not messagebox.askyesno(
                "Duplicate channels",
                "Two or more points map to the same ADS channel.\n"
                "That's fine if the board is wired that way — continue?",
                parent=self,
            ):
                return
        self.result = mapping
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()


_DERIVED_TRACES: tuple[tuple[str, tuple[str, str]], ...] = (
    ("Module1_A (V1_A−V2_A)", ("V1_A", "V2_A")),
    ("Module2_A (V2_A−V3_A)", ("V2_A", "V3_A")),
    ("Total_A   (V1_A−V3_A)", ("V1_A", "V3_A")),
    ("Module1_B (V1_B−V2_B)", ("V1_B", "V2_B")),
    ("Module2_B (V2_B−V3_B)", ("V2_B", "V3_B")),
    ("Total_B   (V1_B−V3_B)", ("V1_B", "V3_B")),
)


class CapturePlotWindow(tk.Toplevel):
    """Toplevel window: visualise one capture CSV.

    Three view modes, all drawn from the same raw int16 data:

    * **Raw**      — 8 ADS channels in counts (u17_ch0..u18_ch3).
    * **Voltage**  — same 8 channels scaled to volts via VOLTS_PER_COUNT.
    * **Derived**  — 6 per-module voltages: Module1 = V1−V2, Module2 = V2−V3,
      Total = V1−V3, computed per channel group A / B using the current
      physical-point → ADS-channel mapping.
    """

    def __init__(
        self,
        parent: tk.Tk,
        csv_path: Path,
        indices: list[int],
        raw_matrix: list[list[int]],
        channel_mapping: dict[str, str],
        sample_period_ms: int = 25,
        trigger_idx: Optional[int] = None,
    ) -> None:
        super().__init__(parent)
        self.title(f"Capture plot — {csv_path.name}")
        self.geometry("1000x650")

        self._indices = indices
        self._raw = raw_matrix
        self._mapping = channel_mapping
        self._csv_path = csv_path
        self._sample_period_ms = sample_period_ms
        self._trigger_idx = trigger_idx

        self._mode_var = tk.StringVar(value="voltage")
        self._trace_vars: dict[str, tk.BooleanVar] = {}
        self._xaxis_var = tk.StringVar(value="samples")  # "samples" or "ms"

        self._build_ui()
        self._rebuild_traces()

    # -- UI --------------------------------------------------------------

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=6)
        top.pack(fill="x")

        ttk.Label(top, text="Mode:").pack(side="left", padx=(0, 4))
        for label, value in (
            ("Raw (counts)",         "raw"),
            ("Voltage (V)",          "voltage"),
            ("Derived modules (V)",  "derived"),
        ):
            ttk.Radiobutton(
                top, text=label, variable=self._mode_var, value=value,
                command=self._on_mode_changed,
            ).pack(side="left", padx=2)

        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=8)

        ttk.Label(top, text="X axis:").pack(side="left", padx=(0, 4))
        ttk.Radiobutton(
            top, text="samples", variable=self._xaxis_var, value="samples",
            command=self._redraw,
        ).pack(side="left")
        ttk.Radiobutton(
            top, text="ms", variable=self._xaxis_var, value="ms",
            command=self._redraw,
        ).pack(side="left")

        ttk.Label(top, text="  period (ms):").pack(side="left")
        self._period_var = tk.StringVar(value=str(self._sample_period_ms))
        period_entry = ttk.Entry(top, textvariable=self._period_var, width=6)
        period_entry.pack(side="left")
        period_entry.bind("<FocusOut>", lambda _e: self._on_period_changed())
        period_entry.bind("<Return>", lambda _e: self._on_period_changed())

        ttk.Label(top, text="  trigger idx:").pack(side="left")
        self._trig_var = tk.StringVar(
            value="" if self._trigger_idx is None else str(self._trigger_idx)
        )
        trig_entry = ttk.Entry(top, textvariable=self._trig_var, width=8)
        trig_entry.pack(side="left")
        trig_entry.bind("<FocusOut>", lambda _e: self._on_trigger_changed())
        trig_entry.bind("<Return>", lambda _e: self._on_trigger_changed())

        # Trace checkboxes in a scrollable-ish row below.
        self._traces_frame = ttk.LabelFrame(self, text="Traces", padding=4)
        self._traces_frame.pack(fill="x", padx=6, pady=(0, 4))

        # Plot area
        plot_frame = ttk.Frame(self)
        plot_frame.pack(fill="both", expand=True, padx=6, pady=6)

        self._fig = Figure(figsize=(9, 5), dpi=90)
        self._ax = self._fig.add_subplot(111)
        self._ax.grid(True, alpha=0.3)
        self._canvas = FigureCanvasTkAgg(self._fig, master=plot_frame)
        self._canvas.get_tk_widget().pack(fill="both", expand=True)
        NavigationToolbar2Tk(self._canvas, plot_frame)

    # -- trace set management -------------------------------------------

    def _on_mode_changed(self) -> None:
        self._rebuild_traces()

    def _on_period_changed(self) -> None:
        try:
            v = int(self._period_var.get())
            if v > 0:
                self._sample_period_ms = v
        except ValueError:
            pass
        self._redraw()

    def _on_trigger_changed(self) -> None:
        txt = self._trig_var.get().strip()
        if not txt:
            self._trigger_idx = None
        else:
            try:
                self._trigger_idx = int(txt)
            except ValueError:
                self._trigger_idx = None
        self._redraw()

    def _current_trace_labels(self) -> list[str]:
        mode = self._mode_var.get()
        if mode in ("raw", "voltage"):
            return [k.upper().replace("_", " ") for k in CHANNEL_KEYS]
        return [label for label, _pts in _DERIVED_TRACES]

    def _rebuild_traces(self) -> None:
        for child in self._traces_frame.winfo_children():
            child.destroy()
        self._trace_vars.clear()
        labels = self._current_trace_labels()
        for i, label in enumerate(labels):
            var = tk.BooleanVar(value=True)
            self._trace_vars[label] = var
            ttk.Checkbutton(
                self._traces_frame, text=label, variable=var, command=self._redraw,
            ).grid(row=i // 4, column=i % 4, sticky="w", padx=4, pady=2)
        self._redraw()

    # -- data selection -------------------------------------------------

    def _series_for_mode(self) -> list[tuple[str, list[float]]]:
        """Build (label, y-values) pairs for the currently selected mode."""
        mode = self._mode_var.get()
        n = len(self._raw)
        if n == 0:
            return []

        if mode == "raw":
            labels = [k.upper().replace("_", " ") for k in CHANNEL_KEYS]
            return [
                (labels[ch], [self._raw[i][ch] for i in range(n)])
                for ch in range(8)
            ]
        if mode == "voltage":
            labels = [k.upper().replace("_", " ") for k in CHANNEL_KEYS]
            return [
                (labels[ch], [self._raw[i][ch] * VOLTS_PER_COUNT for i in range(n)])
                for ch in range(8)
            ]
        # derived
        # Build per-point voltage series first.
        point_series: dict[str, list[float]] = {}
        for point, ads_key in self._mapping.items():
            try:
                ch = CHANNEL_KEYS.index(ads_key)
            except ValueError:
                continue
            point_series[point] = [
                self._raw[i][ch] * VOLTS_PER_COUNT for i in range(n)
            ]
        result: list[tuple[str, list[float]]] = []
        for label, (hi, lo) in _DERIVED_TRACES:
            a, b = point_series.get(hi), point_series.get(lo)
            if a is None or b is None:
                continue
            result.append((label, [av - bv for av, bv in zip(a, b)]))
        return result

    # -- draw -----------------------------------------------------------

    def _redraw(self) -> None:
        self._ax.clear()
        self._ax.grid(True, alpha=0.3)

        series = self._series_for_mode()
        xs_samples = list(range(len(self._raw)))
        use_ms = self._xaxis_var.get() == "ms" and self._sample_period_ms > 0
        xs = (
            [i * self._sample_period_ms for i in xs_samples]
            if use_ms else xs_samples
        )

        any_drawn = False
        for label, ys in series:
            var = self._trace_vars.get(label)
            if var is not None and not var.get():
                continue
            self._ax.plot(xs, ys, label=label, linewidth=1.1)
            any_drawn = True

        if self._trigger_idx is not None and 0 <= self._trigger_idx < len(xs):
            x_trig = xs[self._trigger_idx]
            self._ax.axvline(x_trig, color="red", linestyle="--",
                             linewidth=1.0, alpha=0.7, label="trigger")

        mode = self._mode_var.get()
        self._ax.set_xlabel("Time, ms" if use_ms else "Sample index")
        if mode == "raw":
            self._ax.set_ylabel("ADC counts (int16)")
        else:
            self._ax.set_ylabel("Voltage, V")
        self._ax.set_title(
            f"{self._csv_path.name}   ({len(self._raw)} samples, "
            f"period = {self._sample_period_ms} ms)"
        )
        if any_drawn:
            self._ax.legend(loc="best", fontsize=8, ncol=2)
        self._canvas.draw_idle()


# ---------------------------------------------------------------------------
# UI Layer
# ---------------------------------------------------------------------------

CHANNEL_LABELS = [
    ("U17 CH0", "u17_ch0"),
    ("U17 CH1", "u17_ch1"),
    ("U17 CH2", "u17_ch2"),
    ("U17 CH3", "u17_ch3"),
    ("U18 CH0", "u18_ch0"),
    ("U18 CH1", "u18_ch1"),
    ("U18 CH2", "u18_ch2"),
    ("U18 CH3", "u18_ch3"),
]

BAUDRATES = [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]

PLOT_HISTORY = 200
PLOT_REFRESH_MS = 100

PLOT_SELECTIONS = [
    "All channels",
    "U17 only",
    "U18 only",
    "U17 CH0",
    "U17 CH1",
    "U17 CH2",
    "U17 CH3",
    "U18 CH0",
    "U18 CH1",
    "U18 CH2",
    "U18 CH3",
]


class MainApp(tk.Tk):
    """Main Tkinter application window."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Distribution Tools")
        self.geometry("1100x750")
        self.resizable(True, True)

        self.rx_queue: queue.Queue = queue.Queue()
        self.transport = SerialTransport(self.rx_queue)
        self.proto = DistributionProtocol()

        self._history: dict[str, deque[float]] = {
            k: deque(maxlen=PLOT_HISTORY) for k in CHANNEL_KEYS
        }
        self._plot_dirty = False

        # Capture state (populated from CAP STATUS / CAP READ traffic).
        self._cap_state: Optional[str] = None
        self._cap_samples_count: int = 0
        self._cap_samples_total: int = 0  # from last CAP STATUS (trigger_idx not used for fill)
        self._cap_rx_buffer: list[tuple[int, list[int], list[str]]] = []
        self._cap_reading: bool = False
        self._cap_poll_after_id: Optional[str] = None
        self._last_capture_csv: Optional[Path] = None
        self._captures_dir = Path(__file__).resolve().parent / "captures"

        # Persistent config (channel mapping for derived-voltage plots).
        self._config = load_config()
        self._channel_mapping: dict[str, str] = self._config["channel_mapping"]

        self._build_ui()
        self._refresh_ports()
        self._poll_queue()
        if _MPL_OK:
            self._schedule_plot_refresh()

    # -- UI construction -----------------------------------------------------

    def _build_ui(self) -> None:
        pad = {"padx": 6, "pady": 4}

        # ====================================================================
        # Top-level grid:
        #   row 0 : connection bar        (col 0-1, fixed height)
        #   row 1 : commands | plot       (col 0 fixed, col 1 expands)
        #   row 2 : voltages + status     (col 0-1, full width, fixed height)
        #   row 3 : capture bar           (col 0-1, full width, fixed height)
        #   row 4 : log                   (col 0-1, full width, expands)
        # ====================================================================

        # --- row 0: connection bar (full width) -----------------------------
        conn_frame = ttk.LabelFrame(self, text="Connection")
        conn_frame.grid(row=0, column=0, columnspan=2, sticky="ew", **pad)

        ttk.Label(conn_frame, text="Port:").grid(row=0, column=0, **pad)
        self._port_var = tk.StringVar()
        self._port_cb = ttk.Combobox(conn_frame, textvariable=self._port_var, width=14)
        self._port_cb.grid(row=0, column=1, **pad)

        ttk.Button(conn_frame, text="Refresh", command=self._refresh_ports).grid(
            row=0, column=2, **pad
        )

        ttk.Label(conn_frame, text="Baud:").grid(row=0, column=3, **pad)
        self._baud_var = tk.StringVar(value="57600")
        ttk.Combobox(
            conn_frame, textvariable=self._baud_var, values=BAUDRATES, width=10
        ).grid(row=0, column=4, **pad)

        self._conn_btn = ttk.Button(
            conn_frame, text="Connect", command=self._toggle_connection
        )
        self._conn_btn.grid(row=0, column=5, **pad)

        self._conn_status = tk.Label(
            conn_frame, text=" Disconnected ",
            fg="white", bg="#c62828", font=("TkDefaultFont", 9, "bold"),
        )
        self._conn_status.grid(row=0, column=6, **pad)

        ttk.Label(conn_frame, text="Mode:").grid(row=0, column=7, **pad)
        self._mode_indicator = tk.Label(
            conn_frame, text="  —  ",
            fg="white", bg="#757575", font=("TkDefaultFont", 9, "bold"), width=10,
        )
        self._mode_indicator.grid(row=0, column=8, **pad)

        ttk.Label(conn_frame, text="Capture:").grid(row=0, column=9, **pad)
        self._cap_indicator = tk.Label(
            conn_frame, text="  —  ",
            fg="white", bg="#757575", font=("TkDefaultFont", 9, "bold"), width=22,
        )
        self._cap_indicator.grid(row=0, column=10, **pad)

        # --- row 1 col 0: left command panel (fixed width) ------------------
        cmd_frame = ttk.LabelFrame(self, text="Commands")
        cmd_frame.grid(row=1, column=0, sticky="ns", **pad)

        commands = [
            ("PING", self._cmd_ping),
            ("STATUS", self._cmd_status),
            ("START", self._cmd_start),
            ("ARM", self._cmd_arm),
            ("MODE CMD", self._cmd_mode_cmd),
            ("MODE STREAM", self._cmd_mode_stream),
            ("STREAM ON", self._cmd_stream_on),
            ("STREAM OFF", self._cmd_stream_off),
            ("EVENTS ON", self._cmd_events_on),
            ("EVENTS OFF", self._cmd_events_off),
        ]
        for i, (label, cb) in enumerate(commands):
            ttk.Button(cmd_frame, text=label, command=cb, width=18).grid(
                row=i, column=0, **pad, sticky="ew"
            )

        ttk.Separator(cmd_frame, orient="horizontal").grid(
            row=len(commands), column=0, sticky="ew", pady=6
        )
        ttk.Button(cmd_frame, text="Clear plot", command=self._clear_history).grid(
            row=len(commands) + 1, column=0, **pad, sticky="ew"
        )

        # --- row 1 col 1: main plot area (expands) --------------------------
        plot_frame = ttk.LabelFrame(self, text="Real-time plot")
        plot_frame.grid(row=1, column=1, sticky="nsew", **pad)

        top_bar = ttk.Frame(plot_frame)
        top_bar.pack(fill="x", padx=4, pady=2)
        ttk.Label(top_bar, text="Show:").pack(side="left", padx=(0, 4))
        self._plot_sel_var = tk.StringVar(value=PLOT_SELECTIONS[0])
        ttk.Combobox(
            top_bar, textvariable=self._plot_sel_var,
            values=PLOT_SELECTIONS, state="readonly", width=16,
        ).pack(side="left")

        if _MPL_OK:
            self._fig = Figure(figsize=(8, 4), dpi=90)
            self._ax = self._fig.add_subplot(111)
            self._ax.set_xlabel("Sample (oldest → newest)")
            self._ax.set_ylabel("Voltage, V")
            self._ax.grid(True, alpha=0.3)
            self._lines: dict[str, object] = {}
            for k in CHANNEL_KEYS:
                (ln,) = self._ax.plot([], [], label=k.upper().replace("_", " "),
                                      linewidth=1.2)
                self._lines[k] = ln
            self._legend = self._ax.legend(loc="upper left", fontsize=8, ncol=4)
            self._canvas = FigureCanvasTkAgg(self._fig, master=plot_frame)
            self._canvas.get_tk_widget().pack(fill="both", expand=True, padx=4, pady=4)
        else:
            ttk.Label(
                plot_frame,
                text="matplotlib not installed — plot disabled.\n"
                     "Install with: pip install matplotlib",
                foreground="orange",
            ).pack(padx=8, pady=12)

        # --- row 2: bottom bar — voltages + status, full width --------------
        bottom = ttk.Frame(self)
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew", **pad)
        bottom.columnconfigure(0, weight=1)
        bottom.columnconfigure(1, weight=1)

        volt_frame = ttk.LabelFrame(bottom, text="Voltages")
        volt_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        self._volt_vars: dict[str, tk.StringVar] = {}
        # Arrange 8 channels in 2 rows × 4 columns for a compact bottom strip.
        for idx, (label, key) in enumerate(CHANNEL_LABELS):
            r, c = divmod(idx, 4)
            cell = ttk.Frame(volt_frame)
            cell.grid(row=r, column=c, sticky="ew", padx=6, pady=3)
            ttk.Label(cell, text=label + ":").pack(side="left")
            var = tk.StringVar(value="—")
            self._volt_vars[key] = var
            ttk.Label(cell, textvariable=var, width=8, anchor="e",
                      font=("Courier", 10)).pack(side="left", padx=(4, 2))
            ttk.Label(cell, text="V").pack(side="left")
        for c in range(4):
            volt_frame.columnconfigure(c, weight=1)

        status_frame = ttk.LabelFrame(bottom, text="Board status")
        status_frame.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        self._status_vars = {
            "power": tk.StringVar(value="—"),
            "vbus": tk.StringVar(value="—"),
            "mode": tk.StringVar(value="—"),
            "trig": tk.StringVar(value="—"),
            "last_reply": tk.StringVar(value="—"),
        }
        fields = [
            ("Power:", "power"),
            ("VBUS:", "vbus"),
            ("Mode:", "mode"),
            ("Trig tick:", "trig"),
            ("Last reply:", "last_reply"),
        ]
        # 2 fields per row to keep the bottom bar shallow.
        for i, (label, key) in enumerate(fields):
            r, c = divmod(i, 2)
            ttk.Label(status_frame, text=label).grid(
                row=r, column=c * 2, sticky="w", padx=6, pady=3
            )
            ttk.Label(status_frame, textvariable=self._status_vars[key],
                      anchor="w", width=18).grid(
                row=r, column=c * 2 + 1, sticky="w", padx=(0, 6), pady=3
            )

        # --- row 3: capture bar (horizontal, full width) --------------------
        cap_bar = ttk.LabelFrame(self, text="Capture")
        cap_bar.grid(row=3, column=0, columnspan=2, sticky="ew", **pad)

        ttk.Label(cap_bar, text="offset:").grid(row=0, column=0, padx=(6, 2), pady=4)
        self._cap_offset_var = tk.StringVar(value="0")
        ttk.Entry(cap_bar, textvariable=self._cap_offset_var, width=8).grid(
            row=0, column=1, padx=(0, 10), pady=4
        )
        ttk.Label(cap_bar, text="count:").grid(row=0, column=2, padx=(0, 2), pady=4)
        self._cap_count_var = tk.StringVar(value="300")
        ttk.Entry(cap_bar, textvariable=self._cap_count_var, width=8).grid(
            row=0, column=3, padx=(0, 10), pady=4
        )
        ttk.Button(cap_bar, text="CAP STATUS", command=self._cmd_cap_status).grid(
            row=0, column=4, padx=4, pady=4
        )
        ttk.Button(cap_bar, text="CAP READ", command=self._cmd_cap_read).grid(
            row=0, column=5, padx=4, pady=4
        )
        self._open_csv_btn = ttk.Button(
            cap_bar, text="Open last CSV",
            command=self._cmd_open_last_csv, state="disabled",
        )
        self._open_csv_btn.grid(row=0, column=6, padx=4, pady=4)

        ttk.Separator(cap_bar, orient="vertical").grid(
            row=0, column=7, sticky="ns", padx=6, pady=2
        )
        ttk.Button(cap_bar, text="Plot CSV…",
                   command=self._cmd_plot_csv).grid(row=0, column=8, padx=4, pady=4)
        ttk.Button(cap_bar, text="Channel mapping…",
                   command=self._cmd_edit_mapping).grid(row=0, column=9, padx=4, pady=4)

        # --- row 4: log (full width, expands) -------------------------------
        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.grid(row=4, column=0, columnspan=2, sticky="nsew", **pad)

        # Header bar above the log: holds the Clear button so it is clearly
        # visible next to the log title, not hidden below the text widget.
        log_header = ttk.Frame(log_frame)
        log_header.pack(fill="x", padx=4, pady=(2, 0))
        ttk.Button(log_header, text="Clear log window",
                   command=self._clear_log).pack(side="right")

        self._log = scrolledtext.ScrolledText(
            log_frame, height=8, width=100, state="disabled", font=("Courier", 9)
        )
        self._log.pack(fill="both", expand=True, padx=4, pady=4)

        for tag, color in (
            ("TX",  "#0050a0"),
            ("RX",  "#202020"),
            ("STR", "#606060"),
            ("ERR", "#c00000"),
            ("SYS", "#6a1b9a"),
            ("EVT", "#e65100"),  # Phase 5 async events — visible but not alarming
        ):
            self._log.tag_configure(tag, foreground=color)

        # --- layout weights -------------------------------------------------
        # Columns: left panel fixed, plot area takes all extra horizontal space.
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        # Rows: plot dominates vertically; log also expands but less.
        self.rowconfigure(0, weight=0)
        self.rowconfigure(1, weight=4)  # plot — >= 60% of vertical space
        self.rowconfigure(2, weight=0)  # bottom bar — fixed height
        self.rowconfigure(3, weight=0)  # capture bar — fixed height
        self.rowconfigure(4, weight=1)  # log — remaining space

    # -- connection management -----------------------------------------------

    def _refresh_ports(self) -> None:
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self._port_cb["values"] = ports
        if ports and not self._port_var.get():
            self._port_var.set(ports[0])

    def _toggle_connection(self) -> None:
        if self.transport.is_open:
            self._disconnect()
        else:
            self._connect()

    def _connect(self) -> None:
        port = self._port_var.get().strip()
        if not port:
            messagebox.showerror("Error", "Select a COM port first.")
            return
        try:
            baud = int(self._baud_var.get())
            self.transport.open(port, baud)
        except Exception as exc:
            messagebox.showerror("Connection failed", str(exc))
            return
        self._conn_btn.configure(text="Disconnect")
        self._conn_status.configure(
            text=f" Connected: {port} ", fg="white", bg="#2e7d32"
        )
        self._log_message("SYS", f"Connected to {port} @ {baud} baud")

    def _disconnect(self) -> None:
        self._cancel_cap_polling()
        self._cap_reading = False
        self._cap_rx_buffer = []
        self._cap_state = None
        self._cap_samples_count = 0
        self._cap_samples_total = 0
        self._set_cap_indicator(None)
        self.transport.close()
        self._conn_btn.configure(text="Connect")
        self._conn_status.configure(text=" Disconnected ", fg="white", bg="#c62828")
        self._set_mode_indicator(None)
        self._log_message("SYS", "Disconnected")

    # -- command senders -----------------------------------------------------

    def _send(self, cmd: str) -> None:
        if not self.transport.is_open:
            messagebox.showwarning("Not connected", "Connect to a port first.")
            return
        try:
            self.transport.send_line(cmd)
            self._log_message("TX", cmd)
        except Exception as exc:
            self._log_message("ERR", str(exc))

    def _cmd_ping(self) -> None:          self._send(DistributionProtocol.CMD_PING)
    def _cmd_status(self) -> None:        self._send(DistributionProtocol.CMD_STATUS)
    def _cmd_arm(self) -> None:           self._send(DistributionProtocol.CMD_ARM)
    def _cmd_mode_cmd(self) -> None:      self._send(DistributionProtocol.CMD_MODE_CMD)
    def _cmd_mode_stream(self) -> None:   self._send(DistributionProtocol.CMD_MODE_STREAM)
    def _cmd_stream_on(self) -> None:     self._send(DistributionProtocol.CMD_STREAM_ON)
    def _cmd_stream_off(self) -> None:    self._send(DistributionProtocol.CMD_STREAM_OFF)
    def _cmd_events_on(self) -> None:     self._send(DistributionProtocol.CMD_EVENTS_ON)
    def _cmd_events_off(self) -> None:    self._send(DistributionProtocol.CMD_EVENTS_OFF)

    def _cmd_start(self) -> None:
        if not messagebox.askyesno(
            "Confirm START",
            "Send START command to the Distribution Board?\n\nThis will activate power output.",
        ):
            return
        self._send(DistributionProtocol.CMD_START)

    def _cmd_cap_status(self) -> None:
        self._send(DistributionProtocol.CMD_CAP_STATUS)

    def _cmd_cap_read(self) -> None:
        try:
            offset = int(self._cap_offset_var.get().strip())
            count = int(self._cap_count_var.get().strip())
        except ValueError:
            messagebox.showerror("Bad input", "offset and count must be integers.")
            return
        if offset < 0 or count <= 0:
            messagebox.showerror("Bad input", "offset must be ≥ 0 and count must be > 0.")
            return
        # Reset RX buffer and arm the streaming parser.
        self._cap_rx_buffer = []
        self._cap_reading = True
        self._send(DistributionProtocol.cmd_cap_read(offset, count))

    def _cmd_open_last_csv(self) -> None:
        if not self._last_capture_csv or not self._last_capture_csv.exists():
            messagebox.showinfo("No capture", "No capture CSV has been saved yet.")
            return
        try:
            os.startfile(str(self._last_capture_csv))  # Windows-only; tool targets Win.
        except OSError as exc:
            messagebox.showerror("Open failed", str(exc))

    def _cmd_plot_csv(self) -> None:
        if not _MPL_OK:
            messagebox.showerror(
                "matplotlib missing",
                "matplotlib is not installed. Install it with:\n\n  pip install matplotlib",
            )
            return
        initial_dir = (
            self._last_capture_csv.parent
            if self._last_capture_csv and self._last_capture_csv.exists()
            else self._captures_dir
        )
        initial_dir.mkdir(parents=True, exist_ok=True)
        initial_file = (
            self._last_capture_csv.name
            if self._last_capture_csv and self._last_capture_csv.exists() else ""
        )
        path_str = filedialog.askopenfilename(
            parent=self,
            title="Open capture CSV",
            initialdir=str(initial_dir),
            initialfile=initial_file,
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            indices, raw_matrix = load_capture_csv(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Load failed", f"Could not read {path.name}:\n{exc}")
            return
        if not raw_matrix:
            messagebox.showwarning("Empty CSV", f"{path.name} has no sample rows.")
            return
        CapturePlotWindow(
            self,
            csv_path=path,
            indices=indices,
            raw_matrix=raw_matrix,
            channel_mapping=self._channel_mapping,
            sample_period_ms=25,
        )

    def _cmd_edit_mapping(self) -> None:
        dlg = ChannelMappingDialog(self, self._channel_mapping)
        self.wait_window(dlg)
        if dlg.result is None:
            return
        self._channel_mapping = dlg.result
        self._config["channel_mapping"] = dict(self._channel_mapping)
        save_config(self._config)
        self._log_message(
            "SYS",
            "Channel mapping updated: "
            + ", ".join(f"{k}={v}" for k, v in self._channel_mapping.items()),
        )

    # -- queue polling -------------------------------------------------------

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.rx_queue.get_nowait()
                if kind == "rx":
                    self._handle_rx(payload)
                elif kind == "error":
                    self._log_message("ERR", payload)
                    self._disconnect()
        except queue.Empty:
            pass
        self.after(50, self._poll_queue)

    def _handle_rx(self, raw: str) -> None:
        # Phase 5 async events: handled FIRST so they are never mistaken for
        # sample lines inside a CAP READ window, and so we can catch a
        # protocol violation (FW guarantees no EVT lines between the first
        # and last line of a CAP READ response).
        evt_body = self.proto.parse_evt(raw)
        if evt_body is not None:
            self._handle_evt(evt_body, raw)
            return

        # Capture-read payload comes first while a CAP READ is in flight —
        # 300 sample rows would otherwise spam every other branch and the log.
        if self._cap_reading:
            sample = self.proto.parse_cap_sample(raw)
            if sample is not None:
                self._cap_rx_buffer.append(sample)
                return
            done_count = self.proto.parse_cap_done(raw)
            if done_count is not None:
                self._finish_cap_read(done_count, raw)
                return
            # Anything else during CAP READ falls through to normal handling.

        cap_status = self.proto.parse_cap_status(raw)
        if cap_status is not None:
            self._log_message("RX", raw)
            self._apply_cap_status(cap_status)
            return

        stream = self.proto.parse_stream(raw)
        if stream is not None:
            self._log_message("STR", raw)
            self._update_voltages(stream)
            self._append_history(stream)
            # Arrival of a stream line proves board is in STREAM mode.
            self._set_mode_indicator("STREAM")
            return

        self._log_message("RX", raw)

        status = self.proto.parse_status(raw)
        if status is not None:
            self._apply_status(status)
            return

        # Track mode via "MODE STREAM ok" / "MODE CMD ok" acknowledgements,
        # and arm capture polling on ARM/START acks.
        upper = raw.upper()
        if "MODE STREAM OK" in upper:
            self._set_mode_indicator("STREAM")
        elif "MODE CMD OK" in upper:
            self._set_mode_indicator("CMD")
        if "ARM OK" in upper or "START OK" in upper:
            self._trigger_cap_polling()

        # Other short replies: remember as "last reply" for quick feedback.
        self._status_vars["last_reply"].set(raw[:40])

    # -- async event handling (Phase 5) --------------------------------------

    def _handle_evt(self, body: str, raw: str) -> None:
        """Log an "EVT: <body>" line and flag any contract violation.

        Contract: the FW never emits an EVT line between the first data line
        and the `CAP READ done` line of a CAP READ response. Seeing one here
        while `self._cap_reading` is True is a regression — suppression logic
        in `rs485_u1.c` should have counted it into `events_dropped=<N>`
        instead. Log it loudly but still process the EVT normally (don't
        drop it silently)."""
        self._log_message("EVT", raw)
        if self._cap_reading:
            self._log_message(
                "ERR",
                f"CONTRACT VIOLATION: EVT line during CAP READ window ({body!r})",
            )
        # Known bodies — classified for future UI hooks. Today we only log.
        dropped = DistributionProtocol.parse_evt_events_dropped(body)
        if dropped is not None:
            self._status_vars["last_reply"].set(f"events_dropped={dropped}")
            return
        if DistributionProtocol._EVT_VBUS_BLOCK_RE.match(body):
            self._status_vars["last_reply"].set("EVT: vbus_block")
            return
        # Unknown body — already logged with EVT tag above; nothing else to do.

    # -- status / voltage updates --------------------------------------------

    def _apply_status(self, info: dict) -> None:
        self._status_vars["power"].set("ON" if info["power"] else "OFF")
        self._status_vars["vbus"].set("present" if info["vbus"] else "absent")
        self._status_vars["mode"].set(info["mode"])
        self._status_vars["trig"].set(str(info["trig"]))
        self._status_vars["last_reply"].set("STATUS ok")
        self._set_mode_indicator(info["mode"])

    def _set_mode_indicator(self, mode: Optional[str]) -> None:
        if mode == "STREAM":
            self._mode_indicator.configure(text=" STREAM ", bg="#1565c0")
        elif mode == "CMD":
            self._mode_indicator.configure(text="  CMD   ", bg="#455a64")
        else:
            self._mode_indicator.configure(text="  —  ", bg="#757575")

    # -- capture status / polling / CSV ---------------------------------------

    # States that keep the CAP STATUS poll running; anything else stops it.
    _CAP_ACTIVE_STATES = frozenset({"ARMED", "CAPTURING"})

    def _apply_cap_status(self, info: dict) -> None:
        self._cap_state = info["state"]
        self._cap_samples_count = info["samples"]
        # `samples` is what's currently filled. Total buffer size isn't in the
        # message, so keep whatever total we last saw (or infer from count).
        self._cap_samples_total = max(self._cap_samples_total, info["samples"])
        self._set_cap_indicator(self._cap_state, info["samples"], self._cap_samples_total)
        self._status_vars["last_reply"].set(f"CAP {self._cap_state} {info['samples']}")
        if self._cap_state in self._CAP_ACTIVE_STATES:
            self._trigger_cap_polling()
        else:
            self._cancel_cap_polling()

    def _set_cap_indicator(
        self, state: Optional[str], filled: int = 0, total: int = 0
    ) -> None:
        color_map = {
            "IDLE":      "#757575",
            "ARMED":     "#ef6c00",
            "CAPTURING": "#1565c0",
            "READY":     "#2e7d32",
            "ERROR":     "#c62828",
        }
        if not state:
            self._cap_indicator.configure(text="  —  ", bg="#757575")
            return
        fill_txt = f" ({filled}/{total})" if total else f" ({filled})" if filled else ""
        self._cap_indicator.configure(
            text=f" {state}{fill_txt} ", bg=color_map.get(state, "#424242")
        )

    def _trigger_cap_polling(self) -> None:
        if self._cap_poll_after_id is not None:
            return  # already scheduled
        self._cap_poll_after_id = self.after(1000, self._poll_cap_status)

    def _poll_cap_status(self) -> None:
        self._cap_poll_after_id = None
        if not self.transport.is_open:
            return
        # Send a CAP STATUS ping; the response handler will reschedule us
        # only if the capture is still ARMED/CAPTURING.
        try:
            self.transport.send_line(DistributionProtocol.CMD_CAP_STATUS)
            self._log_message("TX", DistributionProtocol.CMD_CAP_STATUS + "  (auto)")
        except Exception as exc:
            self._log_message("ERR", f"auto-poll: {exc}")
            return
        # Reschedule eagerly so we keep polling even if the reply is late or
        # malformed; _apply_cap_status will cancel once state leaves ACTIVE.
        if self._cap_state in self._CAP_ACTIVE_STATES:
            self._cap_poll_after_id = self.after(1000, self._poll_cap_status)

    def _cancel_cap_polling(self) -> None:
        if self._cap_poll_after_id is not None:
            self.after_cancel(self._cap_poll_after_id)
            self._cap_poll_after_id = None

    def _finish_cap_read(self, expected: int, raw_done_line: str) -> None:
        self._cap_reading = False
        samples = self._cap_rx_buffer
        self._cap_rx_buffer = []
        if expected != len(samples):
            self._log_message(
                "ERR",
                f"CAP READ size mismatch: expected {expected}, got {len(samples)}",
            )
        if not samples:
            self._log_message("RX", raw_done_line)
            return
        try:
            path = self._save_capture_csv(samples)
        except OSError as exc:
            self._log_message("ERR", f"CSV save failed: {exc}")
            return
        self._last_capture_csv = path
        self._open_csv_btn.configure(state="normal")
        rel = self._relpath_for_log(path)
        self._log_message(
            "SYS", f"CAP READ done: {len(samples)} samples → {rel}"
        )

    def _save_capture_csv(
        self, samples: list[tuple[int, list[int], list[str]]]
    ) -> Path:
        self._captures_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self._captures_dir / f"cap_{stamp}.csv"
        header = ["idx"]
        for i in range(8):
            header += [f"ch{i}_raw", f"ch{i}_hex"]
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            for idx, raws, hexes in samples:
                row = [idx]
                for r, h in zip(raws, hexes):
                    row += [r, h]
                writer.writerow(row)
        return path

    def _relpath_for_log(self, path: Path) -> str:
        try:
            return str(path.relative_to(Path(__file__).resolve().parent))
        except ValueError:
            return str(path)

    def _update_voltages(self, channels: dict[str, float]) -> None:
        for key, var in self._volt_vars.items():
            val = channels.get(key)
            var.set(f"{val:.2f}" if isinstance(val, (int, float)) else "—")

    def _append_history(self, channels: dict[str, float]) -> None:
        for k in CHANNEL_KEYS:
            v = channels.get(k)
            if isinstance(v, (int, float)):
                self._history[k].append(float(v))
        self._plot_dirty = True

    def _clear_history(self) -> None:
        for dq in self._history.values():
            dq.clear()
        self._plot_dirty = True

    # -- plotting ------------------------------------------------------------

    def _schedule_plot_refresh(self) -> None:
        if self._plot_dirty:
            self._redraw_plot()
            self._plot_dirty = False
        self.after(PLOT_REFRESH_MS, self._schedule_plot_refresh)

    def _channels_to_show(self) -> list[str]:
        sel = self._plot_sel_var.get()
        if sel == "All channels":
            return CHANNEL_KEYS
        if sel == "U17 only":
            return [k for k in CHANNEL_KEYS if k.startswith("u17_")]
        if sel == "U18 only":
            return [k for k in CHANNEL_KEYS if k.startswith("u18_")]
        # Specific channel: "U17 CH0" -> "u17_ch0"
        key = sel.lower().replace(" ", "_")
        return [key] if key in CHANNEL_KEYS else CHANNEL_KEYS

    def _redraw_plot(self) -> None:
        if not _MPL_OK:
            return

        visible_keys = set(self._channels_to_show())
        any_data = False
        x_max = 0
        y_min, y_max = float("inf"), float("-inf")

        for k in CHANNEL_KEYS:
            ln = self._lines[k]
            dq = self._history[k]
            if k in visible_keys and dq:
                ys = list(dq)
                xs = range(len(ys))
                ln.set_data(xs, ys)
                ln.set_visible(True)
                any_data = True
                x_max = max(x_max, len(ys) - 1)
                y_min = min(y_min, min(ys))
                y_max = max(y_max, max(ys))
            else:
                ln.set_data([], [])
                ln.set_visible(False)

        if any_data:
            self._ax.set_xlim(0, max(x_max, 1))
            if y_max > y_min:
                margin = (y_max - y_min) * 0.05 or 1.0
                self._ax.set_ylim(y_min - margin, y_max + margin)
        self._canvas.draw_idle()

    # -- log helpers ---------------------------------------------------------

    def _log_message(self, tag: str, message: str) -> None:
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] [{tag:>3}]  {message}\n"
        self._log.configure(state="normal")
        # Apply the color tag to the whole line if it's a known category.
        self._log.insert("end", line, tag if tag in ("TX", "RX", "STR", "ERR", "SYS", "EVT") else ())
        self._log.see("end")
        self._log.configure(state="disabled")

    def _clear_log(self) -> None:
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

    # -- window close --------------------------------------------------------

    def on_close(self) -> None:
        if self.transport.is_open:
            self.transport.close()
        self.destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = MainApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
