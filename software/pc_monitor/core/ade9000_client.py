"""ADE9000 Phase Monitor serial client — Qt-free.

Exports:
  Ade9000Protocol    — pure-logic helpers (command strings, JSON parsing)
  Ade9000Client      — high-level blocking API for the orchestrator
  Ade9000Error / Ade9000ProtocolError / Ade9000Timeout / Ade9000FirmwareError
"""
from __future__ import annotations

import json
import queue
import time
from typing import Optional

from core.capture_parser import CaptureDone, CaptureSample, CaptureStatus, parse_capture_event
from core.serial_transport import SerialTransport
from core.sync_probe import SyncResult, SyncSample, compute_offset


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class Ade9000Error(Exception):
    """Base for ADE9000 protocol and communication errors."""

class Ade9000ProtocolError(Ade9000Error):
    """Unexpected or malformed reply from firmware."""

class Ade9000Timeout(Ade9000Error):
    """No reply received within the timeout window."""

class Ade9000FirmwareError(Ade9000Error):
    """Firmware replied with status=error."""


# ---------------------------------------------------------------------------
# Protocol helpers — pure logic, no I/O
# ---------------------------------------------------------------------------

class Ade9000Protocol:
    """Command strings and pure helpers for the ADE9000 JSON Lines protocol."""

    CMD_SET_WMODE_CAPTURE  = "SET WMODE capture"
    CMD_SET_WMODE_MONITOR  = "SET WMODE monitor"
    CMD_CAP_STATUS        = "CAP STATUS"
    CMD_CAP_READ          = "CAP READ"
    CMD_CAP_TRIGGER       = "CAP TRIGGER"
    CMD_CAP_ABORT         = "CAP ABORT"

    @staticmethod
    def cmd_sync(seq: int) -> str:
        return f"SYNC {seq}"

    @staticmethod
    def cmd_cap_set(pre: int, post: int) -> str:
        return f"CAP SET {pre} {post}"

    @staticmethod
    def cmd_cap_arm_manual() -> str:
        return "CAP ARM manual"

    @staticmethod
    def cmd_cap_arm_dip(threshold_v: float) -> str:
        return f"CAP ARM dip {threshold_v:.1f}"

    @staticmethod
    def parse_json(line: str) -> Optional[dict]:
        """Parse one JSON line → dict, return None on failure."""
        try:
            return json.loads(line.strip())
        except (json.JSONDecodeError, ValueError):
            return None

    @staticmethod
    def is_telemetry(d: dict) -> bool:
        """True for live-stream telemetry packets (contain `ts`).
        Telemetry is suspended in WMODE capture, so hits here are only
        possible in the brief monitor→capture transition window."""
        return "ts" in d

    @staticmethod
    def is_error(d: dict) -> bool:
        return d.get("status") == "error"

    @staticmethod
    def error_reason(d: dict) -> str:
        return d.get("reason", "unknown")


# ---------------------------------------------------------------------------
# High-level blocking client
# ---------------------------------------------------------------------------

def _default_transport() -> SerialTransport:
    return SerialTransport(
        encoding="utf-8",
        line_terminator=b"\n",
        post_open_flush=True,
        not_open_error_cls=Ade9000Error,
    )


class Ade9000Client:
    """Blocking API over the ADE9000 Phase Monitor JSON Lines protocol.

    All methods block until a reply arrives or `timeout` seconds elapse.
    Telemetry packets and unparseable lines seen while waiting for command
    replies are silently skipped.
    """

    def __init__(self, _transport=None) -> None:
        self._t = _transport if _transport is not None else _default_transport()

    def open(self, port: str, baudrate: int = 115200) -> None:
        self._t.open(port, baudrate)

    def close(self) -> None:
        self._t.close()

    @property
    def is_open(self) -> bool:
        return self._t.is_open

    # -- internal primitives --

    def _drain(self) -> None:
        while True:
            try:
                self._t.rx_queue.get_nowait()
            except queue.Empty:
                return

    def _recv_json(self, timeout: float) -> dict:
        """Return next parseable non-telemetry JSON dict within timeout."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Ade9000Timeout(f"no reply after {timeout:.1f}s")
            try:
                line = self._t.rx_queue.get(timeout=min(remaining, 0.1))
            except queue.Empty:
                continue
            d = Ade9000Protocol.parse_json(line)
            if d is None or Ade9000Protocol.is_telemetry(d):
                continue
            return d

    def _send_recv(self, cmd: str, timeout: float = 2.0) -> dict:
        self._drain()
        self._t.send_line(cmd)
        return self._recv_json(timeout)

    def _expect_event(self, cmd: str, event: str, timeout: float = 2.0) -> dict:
        """Send cmd; raise if reply is an error or has the wrong event."""
        d = self._send_recv(cmd, timeout)
        if Ade9000Protocol.is_error(d):
            raise Ade9000FirmwareError(
                f"{cmd!r} → firmware error: {Ade9000Protocol.error_reason(d)}"
            )
        if d.get("event") != event:
            raise Ade9000ProtocolError(
                f"{cmd!r}: expected event={event!r}, got {d!r}"
            )
        return d

    # -- commands --

    def set_wmode_capture(self, timeout: float = 2.0) -> None:
        """Connect handshake: send SET WMODE capture, verify wmode=capture ack."""
        d = self._expect_event(
            Ade9000Protocol.CMD_SET_WMODE_CAPTURE, "wmode", timeout
        )
        if d.get("wmode") != "capture":
            raise Ade9000ProtocolError(
                f"wmode ack carries wmode={d.get('wmode')!r}, expected 'capture'"
            )

    def set_wmode_monitor(self, timeout: float = 2.0) -> None:
        """Switch back to monitor mode (restores autonomous telemetry stream)."""
        d = self._expect_event(
            Ade9000Protocol.CMD_SET_WMODE_MONITOR, "wmode", timeout
        )
        if d.get("wmode") != "monitor":
            raise Ade9000ProtocolError(
                f"wmode ack carries wmode={d.get('wmode')!r}, expected 'monitor'"
            )

    def sync_probe(
        self, n: int = 25, best_k: int = 8, probe_timeout: float = 0.5
    ) -> SyncResult:
        """Run N SYNC probes; return clock-offset estimate.

        recv_ns is recorded immediately on dequeue (before JSON parsing) to
        minimise bias. Probes that time out or return an unexpected seq are
        skipped; the rest are passed to compute_offset().
        """
        samples: list[SyncSample] = []
        seq = 0
        for _ in range(n):
            seq += 1
            self._drain()
            send_ns = time.perf_counter_ns()
            self._t.send_line(Ade9000Protocol.cmd_sync(seq))
            deadline = time.monotonic() + probe_timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    line = self._t.rx_queue.get(timeout=min(remaining, 0.1))
                except queue.Empty:
                    continue
                recv_ns = time.perf_counter_ns()
                d = Ade9000Protocol.parse_json(line)
                if d is None or Ade9000Protocol.is_telemetry(d):
                    continue
                if d.get("event") != "sync" or d.get("seq") != seq:
                    continue
                try:
                    tick_ms = int(d["tick_ms"])
                except (KeyError, ValueError, TypeError):
                    break
                samples.append(SyncSample(
                    seq=seq, send_ns=send_ns, recv_ns=recv_ns, tick_ms=tick_ms,
                ))
                break
        if not samples:
            raise Ade9000Timeout("sync_probe: no responses received")
        return compute_offset(samples, best_k)

    def cap_set(self, pre: int, post: int, timeout: float = 2.0) -> None:
        """Send CAP SET pre post; verify cap_status ack (any state)."""
        self._expect_event(Ade9000Protocol.cmd_cap_set(pre, post), "cap_status", timeout)

    def cap_arm_manual(self, timeout: float = 2.0) -> None:
        """Arm capture for manual trigger; verify state=ARMED in ack."""
        d = self._expect_event(
            Ade9000Protocol.cmd_cap_arm_manual(), "cap_status", timeout
        )
        if d.get("state") != "ARMED":
            raise Ade9000ProtocolError(
                f"cap_arm_manual: expected state=ARMED, got {d.get('state')!r}"
            )

    def cap_arm_dip(self, threshold_v: float, timeout: float = 2.0) -> None:
        """Arm capture for voltage-dip trigger; verify state=ARMED in ack."""
        d = self._expect_event(
            Ade9000Protocol.cmd_cap_arm_dip(threshold_v), "cap_status", timeout
        )
        if d.get("state") != "ARMED":
            raise Ade9000ProtocolError(
                f"cap_arm_dip: expected state=ARMED, got {d.get('state')!r}"
            )

    def cap_trigger(self, timeout: float = 2.0) -> None:
        """Send manual trigger; verify cap_triggered ack."""
        self._expect_event(Ade9000Protocol.CMD_CAP_TRIGGER, "cap_triggered", timeout)

    def cap_abort(self, timeout: float = 2.0) -> None:
        """Abort capture; verify cap_aborted ack."""
        self._expect_event(Ade9000Protocol.CMD_CAP_ABORT, "cap_aborted", timeout)

    def cap_status(self, timeout: float = 2.0) -> CaptureStatus:
        """Query capture FSM state; return typed CaptureStatus."""
        d = self._send_recv(Ade9000Protocol.CMD_CAP_STATUS, timeout)
        if Ade9000Protocol.is_error(d):
            raise Ade9000FirmwareError(
                f"CAP STATUS error: {Ade9000Protocol.error_reason(d)}"
            )
        if d.get("event") != "cap_status":
            raise Ade9000ProtocolError(f"CAP STATUS unexpected reply: {d!r}")
        try:
            return CaptureStatus(
                state   = str(d["state"]),
                filled  = int(d["filled"]),
                pre     = int(d.get("pre",  0)),
                post    = int(d.get("post", 0)),
                total   = int(d["total"]),
                tick_ms = int(d.get("tick_ms", 0)),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise Ade9000ProtocolError(f"CAP STATUS parse error: {exc}") from exc

    def cap_read(
        self, timeout: float = 30.0
    ) -> tuple[list[CaptureSample], CaptureDone]:
        """Send CAP READ; collect all cap_sample lines + cap_done marker.

        Returns (samples, done). Raises Ade9000ProtocolError if the sample
        count in cap_done disagrees with the number of lines received.
        Non-capture lines (telemetry, status) are silently skipped.
        """
        self._drain()
        self._t.send_line(Ade9000Protocol.CMD_CAP_READ)
        samples: list[CaptureSample] = []
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Ade9000Timeout(f"CAP READ timed out after {timeout}s")
            try:
                line = self._t.rx_queue.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                continue
            ev = parse_capture_event(line)
            if isinstance(ev, CaptureSample):
                samples.append(ev)
            elif isinstance(ev, CaptureDone):
                if ev.n != len(samples):
                    raise Ade9000ProtocolError(
                        f"CAP READ count mismatch: expected {ev.n}, got {len(samples)}"
                    )
                return samples, ev
            # telemetry, status events, unparseable lines — skip
