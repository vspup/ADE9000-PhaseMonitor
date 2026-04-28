"""Automatic COM-port discovery for ADE9000 and Distribution board.

Probes all available serial ports in parallel and identifies devices by
their protocol responses. No firmware changes required.

ADE9000 probe  — 115200 baud, sends "SYNC 1", expects JSON {"event":"sync",...}
Distribution probe — 57600 baud, sends "STATUS", expects "STATUS power=..."

Usage:
    result = scan_ports()
    print(result.arduino_port, result.dist_port)
"""
from __future__ import annotations

import json
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

import serial
import serial.tools.list_ports


@dataclass
class ScanResult:
    arduino_port: Optional[str]
    dist_port:    Optional[str]

    @property
    def complete(self) -> bool:
        return self.arduino_port is not None and self.dist_port is not None


# ---------------------------------------------------------------------------
# Internal probes — each opens the port, sends one command, reads reply
# ---------------------------------------------------------------------------

def _probe_ade9000(port: str, timeout: float) -> bool:
    """Return True if port responds like an ADE9000 at 115200."""
    try:
        with serial.Serial(
            port=port, baudrate=115200,
            bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE, timeout=0.05,
        ) as s:
            s.reset_input_buffer()
            s.write(b"SYNC 1\n")
            deadline = time.monotonic() + timeout
            buf = b""
            while time.monotonic() < deadline:
                chunk = s.read(256)
                if chunk:
                    buf += chunk
                    for sep in (b"\r\n", b"\n", b"\r"):
                        while sep in buf:
                            line_b, buf = buf.split(sep, 1)
                            try:
                                d = json.loads(line_b.decode("utf-8", errors="ignore").strip())
                                if d.get("event") == "sync" or "ts" in d:
                                    return True
                            except (json.JSONDecodeError, ValueError):
                                pass
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
    timeout: float = 0.5,
    max_workers: int = 8,
) -> ScanResult:
    """Probe all available COM ports and identify ADE9000 + Distribution.

    Each port is tried for both devices in sequence (ADE9000 first, then
    Distribution). Probes run in parallel across ports using a thread pool.

    Args:
        timeout: per-probe timeout in seconds (applied to each device type).
        max_workers: max parallel threads (one per port).

    Returns:
        ScanResult with the first matching port for each device type.
    """
    ports = [p.device for p in serial.tools.list_ports.comports()]
    arduino_port: Optional[str] = None
    dist_port:    Optional[str] = None
    lock = threading.Lock()

    def _probe_port(port: str) -> None:
        nonlocal arduino_port, dist_port

        # ADE9000 first (115200)
        if arduino_port is None and _probe_ade9000(port, timeout):
            with lock:
                if arduino_port is None:
                    arduino_port = port
            return  # port claimed — skip Distribution probe

        # Distribution (57600)
        if dist_port is None and _probe_dist(port, timeout):
            with lock:
                if dist_port is None:
                    dist_port = port

    workers = min(max_workers, max(len(ports), 1))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_probe_port, p) for p in ports]
        for f in as_completed(futures):
            f.result()   # re-raise any unexpected exception

    return ScanResult(arduino_port=arduino_port, dist_port=dist_port)
