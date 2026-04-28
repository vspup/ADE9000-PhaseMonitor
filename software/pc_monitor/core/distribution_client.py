"""Distribution Board serial client — Qt-free.

Exports:
  CHANNEL_KEYS         — ordered ADS1115 channel identifiers
  DistributionProtocol — pure-logic line parsers (authoritative)
  DistCapStatus        — parsed CAP STATUS reply
  DistCapSample        — one capture sample: (idx, raw_ints, hex_strs)
  DistributionError / VbusBlockError / StartAlreadyOnError / DistributionTimeout
  DistributionClient   — high-level blocking API for the orchestrator

db_tool.py currently has its own copy of DistributionProtocol; that copy
should be replaced with an import from here in a follow-up cleanup.
"""
from __future__ import annotations

import queue
import re
import threading
import time
from dataclasses import dataclass
from statistics import median
from typing import Optional

import serial


CHANNEL_KEYS: list[str] = [
    "u17_ch0", "u17_ch1", "u17_ch2", "u17_ch3",
    "u18_ch0", "u18_ch1", "u18_ch2", "u18_ch3",
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class DistributionError(Exception):
    """Base for Distribution-side protocol and communication errors."""

class VbusBlockError(DistributionError):
    """START refused: VBUS already present."""

class StartAlreadyOnError(DistributionError):
    """START refused: power already on."""

class DistributionTimeout(DistributionError):
    """No reply received within the timeout window."""


# ---------------------------------------------------------------------------
# Protocol parsers — authoritative copy, pure logic, no I/O
# ---------------------------------------------------------------------------

class DistributionProtocol:
    """Pure-logic parsers for the Distribution Board text protocol.

    Authoritative implementation. db_tool.py has a duplicate that will be
    replaced with an import from here in a follow-up cleanup PR.
    """

    CMD_PING       = "PING"
    CMD_STATUS     = "STATUS"
    CMD_START      = "START"
    CMD_ARM        = "ARM"
    CMD_MODE_CMD   = "MODE CMD"
    CMD_EVENTS_ON  = "EVENTS ON"
    CMD_EVENTS_OFF = "EVENTS OFF"
    CMD_CAP_STATUS = "CAP STATUS"

    @staticmethod
    def cmd_cap_read(offset: int, count: int) -> str:
        return f"CAP READ {offset} {count}"

    _STATUS_RE = re.compile(
        r"STATUS\s+power=(?P<power>\d+)\s+vbus=(?P<vbus>\d+)"
        r"\s+mode=(?P<mode>\w+)\s+trig=(?P<trig>\d+)",
        re.IGNORECASE,
    )
    _CAP_STATUS_RE = re.compile(
        # "CAP STATUS" prefix is intentionally NOT required:
        # RS-485 TX→RX adapter switching can corrupt the first bytes of the
        # reply, turning "CAP STATUS" into garbled characters.  The data fields
        # that follow are received intact and are distinctive enough to match.
        r"state=(?P<state>\w+)"
        r"\s+samples=(?P<samples>\d+)"
        r"\s+trigger_idx=(?P<trigger_idx>-?\d+)"
        r"\s+sample_period_ms=(?P<sample_period_ms>\d+)"
        r"\s+channels=(?P<channels>\d+)"
        r"(?:\s+trigger_tick=(?P<trigger_tick>\d+))?",
        re.IGNORECASE,
    )
    _CAP_SAMPLE_RE = re.compile(
        r"^(?P<idx>\d+)"
        + r"".join(rf"\s+(?P<ch{i}>[0-9A-Fa-f]{{4}})" for i in range(8))
        + r"\s*$"
    )
    _CAP_DONE_RE   = re.compile(r"CAP\s+READ\s+done\s+count=(?P<count>\d+)", re.IGNORECASE)
    _EVT_PREFIX_RE = re.compile(r"^EVT:\s*(?P<body>.*)$", re.IGNORECASE)

    @staticmethod
    def parse_status(line: str) -> Optional[dict]:
        m = DistributionProtocol._STATUS_RE.search(line)
        if not m:
            return None
        return {
            "power": int(m.group("power")),
            "vbus":  int(m.group("vbus")),
            "mode":  m.group("mode").upper(),
            "trig":  int(m.group("trig")),
        }

    @staticmethod
    def parse_cap_status(line: str) -> Optional[dict]:
        m = DistributionProtocol._CAP_STATUS_RE.search(line)
        if not m:
            return None
        return {
            "state":            m.group("state").upper(),
            "samples":          int(m.group("samples")),
            "trigger_idx":      int(m.group("trigger_idx")),
            "sample_period_ms": int(m.group("sample_period_ms")),
            "channels":         int(m.group("channels")),
            "trigger_tick":     int(m.group("trigger_tick") or 0),
        }

    @staticmethod
    def parse_cap_sample(line: str) -> Optional[tuple[int, list[int], list[str]]]:
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
        m = DistributionProtocol._CAP_DONE_RE.search(line)
        return int(m.group("count")) if m else None

    @staticmethod
    def parse_evt(line: str) -> Optional[str]:
        m = DistributionProtocol._EVT_PREFIX_RE.match(line)
        return m.group("body").strip() if m else None


# ---------------------------------------------------------------------------
# Parsed data types
# ---------------------------------------------------------------------------

@dataclass
class DistCapStatus:
    state:            str   # IDLE | ARMED | CAPTURING | READY | ERROR
    samples:          int
    trigger_idx:      int
    sample_period_ms: int
    channels:         int
    trigger_tick:     int


DistCapSample = tuple[int, list[int], list[str]]   # (idx, raw_ints, hex_strs)


# ---------------------------------------------------------------------------
# Internal transport — background reader thread
# ---------------------------------------------------------------------------

class _Transport:
    ENCODING = "ascii"

    def __init__(self) -> None:
        self._port:   Optional[serial.Serial] = None
        self._thread: Optional[threading.Thread] = None
        self._stop  = threading.Event()
        self.rx_queue: queue.Queue[str] = queue.Queue()

    def open(self, port: str, baudrate: int = 57600) -> None:
        if self._port and self._port.is_open:
            raise RuntimeError("already open")
        self._stop.clear()
        self._port = serial.Serial(
            port=port, baudrate=baudrate,
            bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE, timeout=0.1,
        )
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
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
            raise DistributionError("port not open")
        self._port.write((line.rstrip("\r\n") + "\r\n").encode(self.ENCODING))

    def _reader(self) -> None:
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = self._port.read(256)
            except serial.SerialException:
                break
            if not chunk:
                continue
            buf += chunk
            while True:
                best_i, best_sep = len(buf), b""
                for sep in (b"\r\n", b"\n", b"\r"):
                    i = buf.find(sep)
                    if 0 <= i < best_i:
                        best_i, best_sep = i, sep
                if not best_sep:
                    break
                line = buf[:best_i].decode(self.ENCODING, errors="ignore").strip()
                buf = buf[best_i + len(best_sep):]
                if line:
                    self.rx_queue.put(line)


# ---------------------------------------------------------------------------
# High-level blocking client
# ---------------------------------------------------------------------------

class DistributionClient:
    """Blocking API over the Distribution Board RS-485 protocol.

    Methods block until a reply arrives or `timeout` seconds elapse.
    EVT lines seen while waiting for command replies are sidelined into
    an internal buffer; retrieve and clear them with ``take_events()``.
    """

    def __init__(self, _transport: Optional[_Transport] = None) -> None:
        self._t = _transport if _transport is not None else _Transport()
        self._evt_lines: list[str] = []

    def open(self, port: str, baudrate: int = 57600) -> None:
        self._t.open(port, baudrate)

    def close(self) -> None:
        self._t.close()

    @property
    def is_open(self) -> bool:
        return self._t.is_open

    def take_events(self) -> list[str]:
        """Return collected EVT lines and clear the internal buffer."""
        evts = self._evt_lines[:]
        self._evt_lines.clear()
        return evts

    # -- internal primitives --

    def _drain(self) -> None:
        """Discard all currently queued lines (clear stale data before a command)."""
        while True:
            try:
                self._t.rx_queue.get_nowait()
            except queue.Empty:
                return

    def _recv(self, timeout: float) -> str:
        """Return next non-EVT line; EVT lines are sidelined to the event buffer."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DistributionTimeout(f"no reply after {timeout:.1f}s")
            try:
                line = self._t.rx_queue.get(timeout=min(remaining, 0.1))
            except queue.Empty:
                continue
            if DistributionProtocol.parse_evt(line) is not None:
                self._evt_lines.append(line)
                continue
            return line

    def _send_recv(self, cmd: str, timeout: float = 2.0) -> str:
        self._drain()
        self._t.send_line(cmd)
        return self._recv(timeout)

    # -- commands --

    def mode_cmd(self, timeout: float = 2.0) -> None:
        """Switch Distribution board to CMD mode; verify ack."""
        reply = self._send_recv(DistributionProtocol.CMD_MODE_CMD, timeout)
        if "MODE CMD OK" not in reply.upper():
            raise DistributionError(f"MODE CMD failed: {reply!r}")

    def ping(self, timeout: float = 2.0) -> float:
        """Send PING; return round-trip time in ms.

        Scans until PONG is found, skipping garbled lines that can appear
        during RS-485 TX→RX adapter switching (half-duplex settling artefact).
        """
        self._drain()
        t0 = time.perf_counter_ns()
        self._t.send_line(DistributionProtocol.CMD_PING)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DistributionTimeout(f"PING: no PONG after {timeout:.1f}s")
            try:
                line = self._t.rx_queue.get(timeout=min(remaining, 0.1))
            except queue.Empty:
                continue
            upper = line.upper()
            if DistributionProtocol.parse_evt(line) is not None:
                self._evt_lines.append(line)
                continue
            if "PONG" in upper:
                return (time.perf_counter_ns() - t0) / 1e6
            # Garbled / echo line — keep scanning until PONG or timeout.

    def ping_probe(self, n: int = 25, timeout: float = 1.0) -> float:
        """Send N PING probes; return median RTT in ms."""
        rtts: list[float] = []
        for _ in range(n):
            try:
                rtts.append(self.ping(timeout))
            except DistributionTimeout:
                pass
        if not rtts:
            raise DistributionTimeout("ping_probe: no responses received")
        return median(rtts)

    def status(self, timeout: float = 2.0) -> dict:
        reply = self._send_recv(DistributionProtocol.CMD_STATUS, timeout)
        parsed = DistributionProtocol.parse_status(reply)
        if parsed is None:
            raise DistributionError(f"bad STATUS reply: {reply!r}")
        return parsed

    def arm(self, timeout: float = 2.0) -> None:
        reply = self._send_recv(DistributionProtocol.CMD_ARM, timeout)
        if "ARM OK" not in reply.upper():
            raise DistributionError(f"ARM failed: {reply!r}")

    def start(self, timeout: float = 5.0) -> None:
        """Send START. Raises VbusBlockError / StartAlreadyOnError on refusal."""
        reply = self._send_recv(DistributionProtocol.CMD_START, timeout)
        upper = reply.upper()
        if "START OK" in upper:
            return
        if "VBUS_ERROR" in upper:
            raise VbusBlockError(f"START vbus_error: {reply!r}")
        if "ALREADY_ON" in upper:
            raise StartAlreadyOnError(f"START already_on: {reply!r}")
        raise DistributionError(f"START failed: {reply!r}")

    def cap_status(self, timeout: float = 2.0) -> DistCapStatus:
        """Query capture FSM state; return typed DistCapStatus.

        Scans lines until one parses, skipping RS-485 echo / garbled lines.
        Raises DistributionTimeout if no valid reply arrives within timeout.
        """
        self._drain()
        self._t.send_line(DistributionProtocol.CMD_CAP_STATUS)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DistributionTimeout(
                    f"CAP STATUS: no valid reply after {timeout:.1f}s"
                )
            try:
                line = self._t.rx_queue.get(timeout=min(remaining, 0.1))
            except queue.Empty:
                continue
            if DistributionProtocol.parse_evt(line) is not None:
                self._evt_lines.append(line)
                continue
            parsed = DistributionProtocol.parse_cap_status(line)
            if parsed is not None:
                return DistCapStatus(**parsed)
            # Garbled / echo line — keep scanning.

    def cap_read(self, offset: int, count: int, timeout: float = 30.0) -> list[DistCapSample]:
        """Send CAP READ; collect and return all samples up to the done marker."""
        self._drain()
        self._t.send_line(DistributionProtocol.cmd_cap_read(offset, count))
        samples: list[DistCapSample] = []
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DistributionTimeout(f"CAP READ timed out after {timeout}s")
            try:
                line = self._t.rx_queue.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                continue
            done_count = DistributionProtocol.parse_cap_done(line)
            if done_count is not None:
                if done_count != len(samples):
                    raise DistributionError(
                        f"CAP READ count mismatch: expected {done_count}, got {len(samples)}"
                    )
                return samples
            sample = DistributionProtocol.parse_cap_sample(line)
            if sample is not None:
                samples.append(sample)
            elif DistributionProtocol.parse_evt(line) is not None:
                # EVT during CAP READ is a FW contract violation — sidelined, not fatal
                self._evt_lines.append(line)
