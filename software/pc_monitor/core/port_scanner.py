"""Automatic COM-port discovery for ADE9000 and Distribution board.

Probes all available serial ports in parallel and identifies devices by
their protocol responses. No firmware changes required.

ADE9000 probe  — 115200 baud, listens for autonomous JSON telemetry
                 ({"ts":...} or {"event":"sync"}).  No bytes written —
                 harmless on any port, including Distribution board.
Distribution   — 57600 baud, sends "STATUS", expects "STATUS power=..."

Usage:
    result = scan_ports()
    # result.arduino_ports — list of ports that look like ADE9000
    # result.dist_ports    — list of ports that look like Distribution
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

import serial
import serial.tools.list_ports


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ScanResult:
    arduino_ports: list[str] = field(default_factory=list)
    dist_ports:    list[str] = field(default_factory=list)

    @property
    def arduino_port(self) -> Optional[str]:
        return self.arduino_ports[0] if self.arduino_ports else None

    @property
    def dist_port(self) -> Optional[str]:
        return self.dist_ports[0] if self.dist_ports else None

    @property
    def complete(self) -> bool:
        return bool(self.arduino_ports) and bool(self.dist_ports)


# ---------------------------------------------------------------------------
# Internal probes
# ---------------------------------------------------------------------------

def _is_ade9000_json(data: bytes) -> bool:
    """Return True if `data` contains at least one recognisable ADE9000 JSON line.

    Matches telemetry packets ("ts" key) and any event dict ("event" key),
    which includes sync, wmode, cap_status, cap_triggered, etc.
    Distribution board speaks plain text — never emits JSON — so any valid
    JSON object with these keys is conclusively ADE9000.
    """
    for sep in (b"\r\n", b"\n", b"\r"):
        for chunk in data.split(sep):
            try:
                d = json.loads(chunk.decode("utf-8", errors="ignore").strip())
                if isinstance(d, dict) and ("ts" in d or "event" in d):
                    return True
            except (json.JSONDecodeError, ValueError):
                pass
    return False


def _probe_ade9000(port: str, timeout: float) -> bool:
    """Return True if port looks like ADE9000 Phase Monitor at 115200.

    Two-phase probe:
      Phase 1 (first half of timeout): listen-only.  Detects ADE9000 in
        WMODE monitor by its autonomous 5 Hz JSON telemetry.  Safe on any
        port — no bytes are written.
      Phase 2 (second half): send "SET WMODE monitor", listen for JSON ack.
        Detects ADE9000 in WMODE capture (silent after a session) or IDLE
        (firmware default at boot).  Side-effect: leaves ADE9000 in monitor
        mode, ready for the next scan or session.

    On a Distribution port "SET WMODE monitor" arrives as framing errors
    (wrong baud rate 115200 vs 57600); the STM32 UART discards them without
    emitting JSON, so the probe still returns False and the Distribution probe
    runs next without interference.
    """
    half = timeout / 2.0
    try:
        with serial.Serial(
            port=port, baudrate=115200,
            bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE, timeout=0.05,
        ) as s:
            s.reset_input_buffer()
            buf = b""

            # Phase 1: listen-only
            deadline1 = time.monotonic() + half
            while time.monotonic() < deadline1:
                chunk = s.read(256)
                if chunk:
                    buf += chunk
                    if _is_ade9000_json(buf):
                        return True

            # Phase 2: wake ADE9000 from IDLE / WMODE capture.
            # Prepend \n to flush any garbage bytes that _probe_dist (57600 baud)
            # may have left in the firmware's receive buffer via framing errors.
            s.write(b"\nSET WMODE monitor\n")
            deadline2 = time.monotonic() + half
            while time.monotonic() < deadline2:
                chunk = s.read(256)
                if chunk:
                    buf += chunk
                    if _is_ade9000_json(buf):
                        return True
    except (serial.SerialException, OSError):
        pass
    return False


_STATUS_PREFIX = "STATUS POWER="


def _probe_dist(port: str, timeout: float) -> bool:
    """Return True if port responds like a Distribution board at 57600."""
    try:
        with serial.Serial(
            port=port, baudrate=57600,
            bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE, timeout=0.05,
        ) as s:
            s.reset_input_buffer()
            s.write(b"STATUS\r\n")
            deadline = time.monotonic() + timeout
            buf = b""
            while time.monotonic() < deadline:
                chunk = s.read(256)
                if chunk:
                    buf += chunk
                    for sep in (b"\r\n", b"\n", b"\r"):
                        while sep in buf:
                            line_b, buf = buf.split(sep, 1)
                            line = line_b.decode("ascii", errors="ignore").strip().upper()
                            if _STATUS_PREFIX in line:
                                return True
    except (serial.SerialException, OSError):
        pass
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_ports(
    timeout: float = 0.6,
    max_workers: int = 8,
) -> ScanResult:
    """Probe all available COM ports; return lists of matching ports per device.

    Probe order per port: Distribution first, then ADE9000.

    Distribution probe (57600, sends "STATUS\\r\\n") runs first because:
      - It is purely text-based and causes only harmless framing errors on an
        ADE9000 port (Arduino SAMD21 UART clears FERR flags in its ISR and
        continues receiving — no side effects).
      - ADE9000 probe phase 2 sends "SET WMODE monitor\\n" at 115200.  On a
        Distribution port this triggers framing errors that kill the STM32
        HAL receive interrupt (no ErrorCallback re-arm), making the UART deaf.
        Running ADE9000 probe only on ports NOT already claimed as Distribution
        prevents this damage entirely.

    Probes run in parallel across ports.

    Args:
        timeout: per-probe timeout in seconds.
        max_workers: max parallel threads.

    Returns:
        ScanResult with sorted lists of ports for each device type.
    """
    ports = [p.device for p in serial.tools.list_ports.comports()]
    arduino_ports: list[str] = []
    dist_ports:    list[str] = []
    lock = threading.Lock()

    def _probe_port(port: str) -> None:
        if _probe_dist(port, timeout):
            with lock:
                dist_ports.append(port)
            return
        if _probe_ade9000(port, timeout):
            with lock:
                arduino_ports.append(port)

    workers = min(max_workers, max(len(ports), 1))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_probe_port, p) for p in ports]
        for f in as_completed(futures):
            f.result()

    return ScanResult(
        arduino_ports=sorted(arduino_ports),
        dist_ports=sorted(dist_ports),
    )
