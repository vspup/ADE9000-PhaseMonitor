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
    """Return True if `data` contains at least one recognisable ADE9000 line."""
    for sep in (b"\r\n", b"\n", b"\r"):
        for chunk in data.split(sep):
            try:
                d = json.loads(chunk.decode("utf-8", errors="ignore").strip())
                if "ts" in d or d.get("event") == "sync":
                    return True
            except (json.JSONDecodeError, ValueError):
                pass
    return False


def _probe_ade9000(port: str, timeout: float) -> bool:
    """Return True if port streams ADE9000 JSON telemetry at 115200.

    Strictly listen-only — no bytes are written.  This makes the probe safe
    to run on a Distribution board port without corrupting its UART state.
    Covers ADE9000 in WMODE monitor (autonomous 5 Hz telemetry).
    """
    try:
        with serial.Serial(
            port=port, baudrate=115200,
            bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE, timeout=0.05,
        ) as s:
            s.reset_input_buffer()
            deadline = time.monotonic() + timeout
            buf = b""
            while time.monotonic() < deadline:
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
                            if line.startswith(_STATUS_PREFIX):
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

    Each port is probed for ADE9000 first (listen-only at 115200), then for
    Distribution board (STATUS at 57600) if the ADE9000 probe found nothing.
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
        if _probe_ade9000(port, timeout):
            with lock:
                arduino_ports.append(port)
            return
        if _probe_dist(port, timeout):
            with lock:
                dist_ports.append(port)

    workers = min(max_workers, max(len(ports), 1))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_probe_port, p) for p in ports]
        for f in as_completed(futures):
            f.result()

    return ScanResult(
        arduino_ports=sorted(arduino_ports),
        dist_ports=sorted(dist_ports),
    )
