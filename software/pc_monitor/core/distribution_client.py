"""Distribution Board serial client — Qt-free.

Exports:
  CHANNEL_KEYS         — ordered ADS1115 channel identifiers
  DistributionProtocol — pure-logic line parsers (authoritative)
  DistCapStatus        — parsed CAP STATUS reply
  DistCapSample        — one capture sample: (idx, raw_ints, hex_strs)
  DistributionError / VbusBlockError / StartAlreadyOnError / DistributionTimeout
  DistributionClient   — high-level blocking API for the orchestrator
                         (provides sync_probe → SyncResult, mirrors ADE9000)

db_tool.py currently has its own copy of DistributionProtocol; that copy
should be replaced with an import from here in a follow-up cleanup.
"""
from __future__ import annotations

import queue
import re
import time
from dataclasses import dataclass
from typing import Optional

from core.serial_transport import SerialTransport
from core.sync_probe import SyncResult, SyncSample, compute_offset


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
    CMD_CAP_ABORT  = "CAP ABORT"

    @staticmethod
    def cmd_cap_read(offset: int, count: int) -> str:
        return f"CAP READ {offset} {count}"

    @staticmethod
    def cmd_sync(seq: int) -> str:
        return f"SYNC {seq}"

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
    # "SYNC ok" prefix is intentionally NOT required: same RS-485 garble
    # tolerance pattern as _CAP_STATUS_RE — first bytes can be mangled by
    # the half-duplex DE-pin transition, but the seq/tick fields survive
    # and are distinctive enough to key on.
    _SYNC_RE = re.compile(
        r"seq=(?P<seq>\d+)\s+tick=(?P<tick>\d+)",
        re.IGNORECASE,
    )

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

    @staticmethod
    def parse_sync(line: str) -> Optional[tuple[int, int]]:
        """Parse 'SYNC ok seq=<n> tick=<t>' → (seq, tick_ms).

        Tolerant of garbled prefix; matches anywhere in the line.
        """
        m = DistributionProtocol._SYNC_RE.search(line)
        if not m:
            return None
        return int(m.group("seq")), int(m.group("tick"))


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
# High-level blocking client
# ---------------------------------------------------------------------------

def _default_transport() -> SerialTransport:
    # tx_preamble: sacrificial \r\n absorbs leading-byte loss caused by
    # USB-RS485 adapter auto-direction (DE) latency. The FW parser treats
    # an empty line as a no-op, so the preamble has no semantic effect
    # but rescues short commands like "ARM\r\n" from wire corruption.
    return SerialTransport(
        encoding="ascii",
        line_terminator=b"\r\n",
        post_open_flush=False,
        not_open_error_cls=DistributionError,
        tx_preamble=b"\r\n",
    )


class DistributionClient:
    """Blocking API over the Distribution Board RS-485 protocol.

    Methods block until a reply arrives or `timeout` seconds elapse.
    EVT lines seen while waiting for command replies are sidelined into
    an internal buffer; retrieve and clear them with ``take_events()``.
    """

    def __init__(self, _transport: Optional[SerialTransport] = None) -> None:
        self._t = _transport if _transport is not None else _default_transport()
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

    def _try_send_recv_ok_once(
        self, cmd: str, timeout: float
    ) -> tuple[bool, list[str]]:
        """One attempt: send cmd, scan rx for an ack with " OK" tail.

        Returns ``(ok, skipped)``. ``ok`` is True if an OK-tail line was
        seen. ``skipped`` lists every non-EVT, non-OK line received in
        the deadline window — useful for diagnosing RS-485 garble even
        on success (e.g. echo-corrupted prefix lines arriving before OK).
        EVT lines are sidelined to ``self._evt_lines``.
        """
        self._drain()
        self._t.send_line(cmd)
        deadline = time.monotonic() + timeout
        skipped: list[str] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False, skipped
            try:
                line = self._t.rx_queue.get(timeout=min(remaining, 0.1))
            except queue.Empty:
                continue
            if DistributionProtocol.parse_evt(line) is not None:
                self._evt_lines.append(line)
                continue
            if line.upper().rstrip().endswith(" OK"):
                return True, skipped
            skipped.append(line)

    def _send_recv_ok(self, cmd: str, label: str, timeout: float) -> None:
        """Send cmd, expect an ack ending in " OK". Retries once on garbled
        timeout before raising.

        Tolerates two failure modes:
          * Receive-side garble that destroys the " OK" suffix
            (e.g. wire form 'PSk' from "ARM ok" — first attempt fails,
            second attempt usually arrives clean).
          * Send-side garble that makes the FW respond with "ERR unknown"
            because the command was corrupted on the wire by USB-RS485
            DE-pin latency. Combined with the tx_preamble flush in
            SerialTransport, a single retry is sufficient in practice.

        ARM / MODE CMD / PING are idempotent — re-sending is safe.
        On final failure, the error message lists skipped lines from
        both attempts so RS-485 garble diagnosis is not lost.
        """
        ok, skipped1 = self._try_send_recv_ok_once(cmd, timeout)
        if ok:
            return
        ok, skipped2 = self._try_send_recv_ok_once(cmd, timeout)
        if ok:
            return
        raise DistributionTimeout(
            f"{label}: no clean OK reply after 2 attempts ({timeout:.1f}s each); "
            f"attempt1 skipped={skipped1!r} attempt2 skipped={skipped2!r}"
        )

    # -- commands --

    def mode_cmd(self, timeout: float = 2.0) -> None:
        """Switch Distribution board to CMD mode; verify ack.

        Scans for a line whose tail is " OK" — RS-485 TX→RX adapter switching
        can mangle the leading bytes of the "MODE CMD ok" reply (e.g. an
        observed wire form was '=\\x11\\x15\\x1a5D ok'); the trailing " ok"
        survives and is what we key on.  Truly destroyed replies fall through
        to a clean DistributionTimeout instead of a false-fail.
        """
        self._send_recv_ok(
            DistributionProtocol.CMD_MODE_CMD, "MODE CMD", timeout
        )

    def ping(self, timeout: float = 2.0) -> float:
        """Send PING; return round-trip time in ms.

        Scans until PONG is found, skipping garbled lines that can appear
        during RS-485 TX→RX adapter switching (half-duplex settling artefact).
        On timeout, the error message lists every skipped line so we can
        tell "FW never replied" from "FW replied with garbage" — same
        diagnostic instrumentation as _send_recv_ok.
        """
        self._drain()
        t0 = time.perf_counter_ns()
        self._t.send_line(DistributionProtocol.CMD_PING)
        deadline = time.monotonic() + timeout
        skipped: list[str] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DistributionTimeout(
                    f"PING: no PONG after {timeout:.1f}s; skipped={skipped!r}"
                )
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
            skipped.append(line)
            # Garbled / echo line — keep scanning until PONG or timeout.

    def sync_probe(
        self, n: int = 25, best_k: int = 8, probe_timeout: float = 0.5,
        inter_probe_gap_s: float = 0.05,
    ) -> SyncResult:
        """Run N SYNC probes; return clock-offset estimate.

        Mirrors Ade9000Client.sync_probe: recv_ns is recorded immediately
        on dequeue (before parsing) to minimise bias; probes that time out
        or echo back the wrong seq are skipped; remaining samples feed
        compute_offset() which picks the cleanest best_k by RTT.
        EVT lines are sidelined to the event buffer; garbled / unrelated
        lines are skipped (RS-485 half-duplex tolerance).

        ``inter_probe_gap_s`` is a small idle window before each probe.
        Empirically required on RS-485: back-to-back probes on a USB-RS485
        adapter drop ≈80 % of replies because the DE-pin auto-direction
        latency overlaps the next host transmit and garbles the reply
        framing. 50 ms gap → ~96 % success rate on the stock adapter; that
        matches the wider RS-485 timing budget the link already lives on.
        ADE9000 (USB CDC, no DE-pin) does not need this, hence the gap is
        local to DistributionClient.
        """
        samples: list[SyncSample] = []
        for seq in range(1, n + 1):
            if seq > 1 and inter_probe_gap_s > 0:
                time.sleep(inter_probe_gap_s)
            self._drain()
            send_ns = time.perf_counter_ns()
            self._t.send_line(DistributionProtocol.cmd_sync(seq))
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
                if DistributionProtocol.parse_evt(line) is not None:
                    self._evt_lines.append(line)
                    continue
                parsed = DistributionProtocol.parse_sync(line)
                if parsed is None:
                    # Garbled / unrelated line — keep scanning until deadline.
                    continue
                got_seq, tick_ms = parsed
                if got_seq != seq:
                    # Stale reply from a previous probe — skip.
                    continue
                samples.append(SyncSample(
                    seq=seq, send_ns=send_ns, recv_ns=recv_ns, tick_ms=tick_ms,
                ))
                break
        if not samples:
            raise DistributionTimeout("sync_probe: no responses received")
        return compute_offset(samples, best_k)

    def status(self, timeout: float = 2.0) -> dict:
        reply = self._send_recv(DistributionProtocol.CMD_STATUS, timeout)
        parsed = DistributionProtocol.parse_status(reply)
        if parsed is None:
            raise DistributionError(f"bad STATUS reply: {reply!r}")
        return parsed

    def arm(self, timeout: float = 2.0) -> None:
        """Arm the Distribution capture FSM; verify ack.

        Same RS-485 garble tolerance as mode_cmd(): scan for a tail of " OK"
        and skip mangled-prefix lines.  See mode_cmd() for the rationale.
        """
        self._send_recv_ok(DistributionProtocol.CMD_ARM, "ARM", timeout)

    def cap_abort(self, timeout: float = 2.0) -> None:
        """Abort capture: any state → IDLE on the FW side; verify ack.

        Idempotent. Used by the orchestrator's error path to leave the
        FSM in a defined state without rearming. Same garble-tolerant " OK"
        tail policy as ARM / MODE CMD — RS-485 half-duplex switching can
        mangle the leading bytes of "CAP ABORT ok"; the trailing " ok"
        survives and is what we key on.
        """
        self._send_recv_ok(
            DistributionProtocol.CMD_CAP_ABORT, "CAP ABORT", timeout
        )

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
