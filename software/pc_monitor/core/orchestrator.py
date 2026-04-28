"""Startup-capture orchestrator — PC-side cross-device sequencing.

Implements sequencer.md §4 (happy path) and §7 (error matrix).
Pure logic: no Qt, no filesystem I/O. Caller owns persistence.

Usage:
    cfg  = OrchestratorConfig(arduino_port="COM5", dist_port="COM7")
    ade  = Ade9000Client()
    dist = DistributionClient()
    sess = Orchestrator(cfg, ade, dist, on_progress=print_fn).run()
    # sess is a CaptureSession; write it with session_writer.write_session()
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from core.ade9000_client import Ade9000Client, Ade9000Timeout
from core.capture_parser import CaptureDone, CaptureSample, CaptureStatus
from core.distribution_client import (
    DistCapSample,
    DistCapStatus,
    DistributionClient,
    DistributionTimeout,
    VbusBlockError,
)
from core.sync_probe import SyncResult


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class OrchestratorConfig:
    arduino_port:  str
    dist_port:     str
    pre:           int   = 100
    post:          int   = 400
    trigger_mode:  str   = "manual"   # "manual" | "dip"
    dip_threshold: float = 340.0      # volts; used only when trigger_mode="dip"
    output_dir:    Path  = field(default_factory=lambda: Path("captures"))
    arduino_baud:  int   = 115200
    dist_baud:     int   = 57600


# ---------------------------------------------------------------------------
# Session data (returned by run(), written by session_writer)
# ---------------------------------------------------------------------------

@dataclass
class CaptureSession:
    config:          OrchestratorConfig
    started_at_ns:   int        # time.perf_counter_ns() at session start
    session_id:      str        # "YYYY-MM-DDTHH-MM-SS" used as directory name

    # ADE9000 side
    arduino_samples: list[CaptureSample]
    arduino_done:    CaptureDone
    arduino_sync:    SyncResult
    arduino_port:    str

    # Distribution side
    dist_samples:    list[DistCapSample]   # list of (idx, raw_ints, hex_strs)
    dist_status:     DistCapStatus         # CAP STATUS snapshot from drain phase
    dist_rtt_ms:     float                 # median PING RTT (no proper SYNC yet)
    dist_port:       str

    # Cross-device
    offset_ad_ms:    float   # offset_arduino − dist_rtt_ms/2  (§3.4 approximation)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class OrchestratorError(Exception):
    """Raised when the capture sequence cannot complete cleanly."""


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class Orchestrator:
    """Execute a full startup-capture session across ADE9000 + Distribution.

    Phase numbering mirrors sequencer.md §4:
      0 — connect & handshake
      1 — sync
      2 — arm
      3 — fire (START + trigger)
      4 — drain (poll until both READY)
      5 — read (CAP READ both)

    Filesystem writes are out of scope — use session_writer.write_session().
    """

    # Drain poll interval (seconds between CAP STATUS calls per device).
    _DRAIN_POLL_S = 1.0

    # Per-device drain timeouts. ADE9000 window ≈ 3 s → budget 6 s.
    # Distribution window ≈ 7.5 s → budget 15 s (covers 12 s precharge).
    _ADE_DRAIN_TIMEOUT  = 6.0
    _DIST_DRAIN_TIMEOUT = 15.0

    def __init__(
        self,
        config:      OrchestratorConfig,
        ade:         Ade9000Client,
        dist:        DistributionClient,
        on_progress: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self._cfg  = config
        self._ade  = ade
        self._dist = dist
        self._log  = on_progress or (lambda _phase, _msg: None)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> CaptureSession:
        """Open ports, execute the capture sequence, close ports.

        Returns a CaptureSession on success.
        On any failure, both devices are aborted and ports closed before
        the exception propagates. No partial CSV is written.
        """
        started_at_ns = time.perf_counter_ns()
        session_id    = time.strftime("%Y-%m-%dT%H-%M-%S")

        self._log("CONNECT", f"opening ADE9000 on {self._cfg.arduino_port}")
        self._ade.open(self._cfg.arduino_port, self._cfg.arduino_baud)
        try:
            self._log("CONNECT", f"opening Distribution on {self._cfg.dist_port}")
            self._dist.open(self._cfg.dist_port, self._cfg.dist_baud)
        except Exception:
            self._ade.close()
            raise

        try:
            return self._run_session(started_at_ns, session_id)
        except Exception:
            self._abort_both()
            raise
        finally:
            self._ade.close()
            self._dist.close()

    # ------------------------------------------------------------------
    # Private: session phases
    # ------------------------------------------------------------------

    def _run_session(self, started_at_ns: int, session_id: str) -> CaptureSession:
        cfg = self._cfg

        # Phase 0 — handshake
        self._log("CONNECT", "SET WMODE capture → ADE9000")
        self._ade.set_wmode_capture()
        self._log("CONNECT", "MODE CMD → Distribution")
        self._dist.mode_cmd()
        self._log("CONNECT", "PING → Distribution (verify reachable)")
        self._dist.ping()

        # Phase 1 — sync
        self._log("SYNC", "ADE9000 SYNC ×25")
        arduino_sync = self._ade.sync_probe()
        self._log("SYNC",
                  f"ADE9000 offset={arduino_sync.offset_ms:.1f} ms  "
                  f"RTT_best={arduino_sync.rtt_ms_best:.2f} ms  "
                  f"used {arduino_sync.n_used}/{arduino_sync.n_samples}")
        self._log("SYNC", "Distribution PING ×25")
        dist_rtt_ms    = self._dist.ping_probe()
        dist_offset_ms = dist_rtt_ms / 2.0
        self._log("SYNC", f"Distribution RTT_median={dist_rtt_ms:.2f} ms")

        # Phase 2 — arm
        self._log("ARM", f"CAP SET {cfg.pre} {cfg.post} → ADE9000")
        self._ade.cap_set(cfg.pre, cfg.post)
        if cfg.trigger_mode == "dip":
            self._log("ARM", f"CAP ARM dip {cfg.dip_threshold:.1f} → ADE9000")
            self._ade.cap_arm_dip(cfg.dip_threshold)
        else:
            self._log("ARM", "CAP ARM manual → ADE9000")
            self._ade.cap_arm_manual()
        self._log("ARM", "ARM → Distribution")
        self._dist.arm()

        # Phase 3 — fire
        self._log("FIRE", "START → Distribution")
        try:
            self._dist.start()
        except VbusBlockError as exc:
            # sequencer.md §7: vbus_error → abort ADE9000, no CSV
            self._log("FIRE", "START refused (vbus_error) — aborting ADE9000")
            self._safe_ade_abort()
            raise OrchestratorError(
                "Distribution refused START: VBUS already present"
            ) from exc
        if cfg.trigger_mode == "manual":
            self._log("FIRE", "CAP TRIGGER → ADE9000")
            self._ade.cap_trigger()

        # Phase 4 — drain
        self._log("DRAIN", "waiting for READY on both devices")
        ade_cs, dist_cs = self._drain_both()

        # Phase 5 — read
        self._log("READ", "CAP READ → ADE9000")
        arduino_samples, arduino_done = self._ade.cap_read()
        self._log("READ",
                  f"ADE9000: {len(arduino_samples)} samples  "
                  f"trigger_tick={arduino_done.trigger_tick_ms} ms  "
                  f"period={arduino_done.sample_period_ms} ms")

        self._log("READ", f"CAP READ 0 {dist_cs.samples} → Distribution")
        dist_samples = self._dist.cap_read(0, dist_cs.samples)
        self._log("READ",
                  f"Distribution: {len(dist_samples)} samples  "
                  f"trigger_tick={dist_cs.trigger_tick} ms  "
                  f"period={dist_cs.sample_period_ms} ms")

        return CaptureSession(
            config          = cfg,
            started_at_ns   = started_at_ns,
            session_id      = session_id,
            arduino_samples = arduino_samples,
            arduino_done    = arduino_done,
            arduino_sync    = arduino_sync,
            arduino_port    = cfg.arduino_port,
            dist_samples    = dist_samples,
            dist_status     = dist_cs,
            dist_rtt_ms     = dist_rtt_ms,
            dist_port       = cfg.dist_port,
            offset_ad_ms    = arduino_sync.offset_ms - dist_offset_ms,
        )

    def _drain_both(self) -> tuple[CaptureStatus, DistCapStatus]:
        """Poll both devices until both report READY.

        Returns (ade_final_status, dist_final_status) — the last CAP STATUS
        from each device, captured while state=READY. These carry trigger_tick
        and sample_period_ms needed for session.json.

        Alternates polls to avoid starving either device. Each device has its
        own deadline; the shorter ADE9000 window is checked first each cycle.
        """
        ade_cs:  Optional[CaptureStatus] = None
        dist_cs: Optional[DistCapStatus] = None
        ade_deadline  = time.monotonic() + self._ADE_DRAIN_TIMEOUT
        dist_deadline = time.monotonic() + self._DIST_DRAIN_TIMEOUT

        while ade_cs is None or dist_cs is None:
            if ade_cs is None:
                if time.monotonic() > ade_deadline:
                    raise OrchestratorError(
                        f"ADE9000 did not reach READY within "
                        f"{self._ADE_DRAIN_TIMEOUT:.0f} s"
                    )
                cs = self._ade.cap_status()
                if cs.state == "READY":
                    ade_cs = cs
                    self._log("DRAIN", "ADE9000 READY")
                else:
                    self._log("DRAIN",
                              f"ADE9000 {cs.state}  {cs.filled}/{cs.total}")

            if dist_cs is None:
                if time.monotonic() > dist_deadline:
                    raise OrchestratorError(
                        f"Distribution did not reach READY within "
                        f"{self._DIST_DRAIN_TIMEOUT:.0f} s"
                    )
                cs = self._dist.cap_status()
                if cs.state == "READY":
                    dist_cs = cs
                    self._log("DRAIN", "Distribution READY")
                elif cs.state == "ERROR":
                    raise OrchestratorError("Distribution capture FSM reached ERROR")
                else:
                    self._log("DRAIN",
                              f"Distribution {cs.state}  {cs.samples}")

            if ade_cs is None or dist_cs is None:
                time.sleep(self._DRAIN_POLL_S)

        return ade_cs, dist_cs

    # ------------------------------------------------------------------
    # Private: abort helpers
    # ------------------------------------------------------------------

    def _safe_ade_abort(self) -> None:
        """Best-effort CAP ABORT on ADE9000; errors are swallowed."""
        try:
            self._ade.cap_abort()
        except Exception:
            pass

    def _abort_both(self) -> None:
        """Best-effort abort of both devices on the error path.

        Distribution has no CAP ABORT command yet (sequencer.md §8);
        re-ARM resets its FSM to IDLE. We skip that here — the device
        will recover on next connect. ADE9000 is explicitly aborted.
        """
        self._safe_ade_abort()
