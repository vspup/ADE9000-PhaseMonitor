"""Shared line-oriented serial transport for the orchestrator clients.

A single class consolidates the previously duplicated `_Transport` from
``ade9000_client.py`` and ``distribution_client.py``. Behaviour is
parameterised so each client keeps its own wire-format quirks:

* ``encoding``           — ``"utf-8"`` for ADE9000 JSON, ``"ascii"`` for
                           Distribution text protocol.
* ``line_terminator``    — ``b"\\n"`` for ADE9000, ``b"\\r\\n"`` for the
                           RS-485 bridge.
* ``post_open_flush``    — ADE9000 needs a stray ``\\n`` + 100 ms settle
                           + ``reset_input_buffer()`` after open to
                           discard framing-error garbage left by a
                           preceding cross-baud port-scanner probe.
* ``not_open_error_cls`` — exception type raised by ``send_line`` when
                           the port is closed; lets each client surface
                           its native error class to callers.
* ``tx_preamble``        — sacrificial bytes prepended to every
                           ``send_line`` payload. Used by the
                           Distribution RS-485 client to wake up the
                           USB-RS485 adapter's auto-direction control:
                           if DE asserts late, the leading bytes of a
                           transmission are dropped or corrupted on the
                           wire. A ``b"\\r\\n"`` preamble is treated by
                           the FW as an empty line (no-op) and absorbs
                           the loss instead of the real command.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Optional

import serial


class SerialTransport:
    """Background-reader serial transport, line-framed.

    The reader thread drains the OS receive buffer in 256-byte chunks,
    splits on the first of ``\\r\\n`` / ``\\n`` / ``\\r``, and pushes
    non-empty decoded lines onto ``rx_queue``. Decoding errors are
    suppressed (``errors="ignore"``) so a single bad byte never wedges
    the queue.
    """

    def __init__(
        self,
        *,
        encoding: str,
        line_terminator: bytes,
        post_open_flush: bool,
        not_open_error_cls: type[Exception] = RuntimeError,
        tx_preamble: bytes = b"",
    ) -> None:
        self._encoding = encoding
        self._line_terminator = line_terminator
        self._post_open_flush = post_open_flush
        self._not_open_error_cls = not_open_error_cls
        self._tx_preamble = tx_preamble

        self._port:   Optional[serial.Serial] = None
        self._thread: Optional[threading.Thread] = None
        self._stop  = threading.Event()
        self.rx_queue: queue.Queue[str] = queue.Queue()

    def open(self, port: str, baudrate: int) -> None:
        if self._port and self._port.is_open:
            raise RuntimeError("already open")
        self._stop.clear()
        self._port = serial.Serial(
            port=port, baudrate=baudrate,
            bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE, timeout=0.1,
        )
        if self._post_open_flush:
            # Discard framing-error bytes a preceding cross-baud port
            # scanner may have left in the firmware's RX buffer. A bare
            # \n nudges the FW to drain its line state as an empty
            # "unknown_cmd" (silently ignored on this side).
            self._port.write(b"\n")
            time.sleep(0.1)
            self._port.reset_input_buffer()
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
            raise self._not_open_error_cls("port not open")
        body = line.rstrip("\r\n").encode(self._encoding) + self._line_terminator
        self._port.write(self._tx_preamble + body)

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
                line = buf[:best_i].decode(self._encoding, errors="ignore").strip()
                buf = buf[best_i + len(best_sep):]
                if line:
                    self.rx_queue.put(line)
