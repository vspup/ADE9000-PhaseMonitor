"""Run from software/pc_monitor/: `python -m pytest tests/`

Tests for Orchestrator. Both clients are replaced by fakes — no serial ports,
no filesystem. session_writer is out of scope here.
"""
import time
import unittest
from typing import List, Optional
from unittest.mock import MagicMock

from core.ade9000_client import Ade9000FirmwareError, Ade9000Timeout
from core.capture_parser import CaptureDone, CaptureSample, CaptureStatus
from core.distribution_client import (
    DistCapStatus,
    DistributionError,
    DistributionTimeout,
    VbusBlockError,
)
from core.orchestrator import (
    CaptureSession,
    Orchestrator,
    OrchestratorConfig,
    OrchestratorError,
)
from core.sync_probe import SyncResult


# ---------------------------------------------------------------------------
# Fake clients
# ---------------------------------------------------------------------------

def _sync_result(offset_ms: float = 100.0) -> SyncResult:
    return SyncResult(
        offset_ms=offset_ms, rtt_ms_median=2.0,
        rtt_ms_best=1.0, n_samples=25, n_used=8,
    )


def _cap_status_ready(
    state: str = "READY", filled: int = 300, total: int = 500,
    pre: int = 100, post: int = 400, tick_ms: int = 42000,
) -> CaptureStatus:
    return CaptureStatus(
        state=state, filled=filled, pre=pre, post=post,
        total=total, tick_ms=tick_ms,
    )


def _dist_cap_status(
    state: str = "READY", samples: int = 300,
    trigger_tick: int = 99000, sample_period_ms: int = 25,
) -> DistCapStatus:
    return DistCapStatus(
        state=state, samples=samples,
        trigger_idx=50, sample_period_ms=sample_period_ms,
        channels=8, trigger_tick=trigger_tick,
    )


def _arduino_samples(n: int = 5) -> List[CaptureSample]:
    return [
        CaptureSample(i=i - 2, uab=400.0, ubc=400.0, uca=400.0,
                      ia=1.0, ib=1.0, ic=1.0)
        for i in range(n)
    ]


def _arduino_done(n: int = 5) -> CaptureDone:
    return CaptureDone(
        n=n, trigger_tick_ms=42000, sample_period_ms=10,
        pre=2, post=3, trigger_index=0,
    )


def _dist_samples(n: int = 5):
    return [(i, [0] * 8, ["0000"] * 8) for i in range(n)]


def _make_fake_ade(
    *,
    wmode_capture_ok: bool = True,
    sync_result: Optional[SyncResult] = None,
    cap_set_ok: bool = True,
    arm_result: Optional[CaptureStatus] = None,   # None → default ARMED
    trigger_ok: bool = True,
    cap_status_sequence: Optional[List[CaptureStatus]] = None,
    cap_read_result=None,
    abort_ok: bool = True,
):
    ade = MagicMock()
    ade.open.return_value = None
    ade.close.return_value = None
    ade.is_open = True

    if wmode_capture_ok:
        ade.set_wmode_capture.return_value = None
    else:
        ade.set_wmode_capture.side_effect = Ade9000FirmwareError("bad_wmode")

    ade.sync_probe.return_value = sync_result or _sync_result()
    ade.cap_set.return_value = None

    if arm_result is None:
        arm_result = CaptureStatus(
            state="ARMED", filled=0, pre=100, post=400, total=500, tick_ms=0
        )
    ade.cap_arm_manual.return_value = arm_result
    ade.cap_arm_dip.return_value = arm_result

    if trigger_ok:
        ade.cap_trigger.return_value = None
    else:
        ade.cap_trigger.side_effect = Ade9000FirmwareError("not_armed")

    statuses = cap_status_sequence or [_cap_status_ready()]
    ade.cap_status.side_effect = statuses

    if cap_read_result is None:
        samples = _arduino_samples()
        done    = _arduino_done(len(samples))
        cap_read_result = (samples, done)
    ade.cap_read.return_value = cap_read_result

    ade.cap_abort.return_value = None
    return ade


def _make_fake_dist(
    *,
    ping_ok: bool = True,
    sync_result: Optional[SyncResult] = None,
    arm_ok: bool = True,
    start_result=None,   # None → ok, "vbus_error" → VbusBlockError
    cap_status_sequence: Optional[List[DistCapStatus]] = None,
    cap_read_result=None,
):
    dist = MagicMock()
    dist.open.return_value = None
    dist.close.return_value = None
    dist.is_open = True

    if ping_ok:
        dist.ping.return_value = 1.5
    else:
        dist.ping.side_effect = DistributionError("no reply")

    dist.sync_probe.return_value = sync_result or _sync_result(offset_ms=0.0)

    if arm_ok:
        dist.arm.return_value = None
    else:
        dist.arm.side_effect = DistributionError("ARM failed")

    if start_result == "vbus_error":
        dist.start.side_effect = VbusBlockError("vbus_error")
    else:
        dist.start.return_value = None

    statuses = cap_status_sequence or [_dist_cap_status()]
    dist.cap_status.side_effect = statuses

    if cap_read_result is None:
        cap_read_result = _dist_samples()
    dist.cap_read.return_value = cap_read_result

    return dist


def _cfg(**kw) -> OrchestratorConfig:
    defaults = dict(arduino_port="COM5", dist_port="COM7")
    defaults.update(kw)
    return OrchestratorConfig(**defaults)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestHappyPath(unittest.TestCase):
    def _run(self, **cfg_kw) -> CaptureSession:
        ade  = _make_fake_ade()
        dist = _make_fake_dist()
        cfg  = _cfg(**cfg_kw)
        return Orchestrator(cfg, ade, dist).run()

    def test_returns_capture_session(self):
        sess = self._run()
        self.assertIsInstance(sess, CaptureSession)

    def test_session_id_format(self):
        sess = self._run()
        self.assertRegex(sess.session_id, r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}")

    def test_arduino_samples_present(self):
        sess = self._run()
        self.assertGreater(len(sess.arduino_samples), 0)
        self.assertIsInstance(sess.arduino_samples[0], CaptureSample)

    def test_dist_samples_present(self):
        sess = self._run()
        self.assertGreater(len(sess.dist_samples), 0)

    def test_offset_ad_computed(self):
        ade  = _make_fake_ade(sync_result=_sync_result(offset_ms=500.0))
        dist = _make_fake_dist(sync_result=_sync_result(offset_ms=2.0))
        sess = Orchestrator(_cfg(), ade, dist).run()
        self.assertAlmostEqual(sess.offset_ad_ms, 498.0)   # 500 − 2

    def test_dist_sync_stored_on_session(self):
        ade  = _make_fake_ade()
        dist_sync = _sync_result(offset_ms=42.0)
        dist = _make_fake_dist(sync_result=dist_sync)
        sess = Orchestrator(_cfg(), ade, dist).run()
        self.assertIs(sess.dist_sync, dist_sync)

    def test_ports_closed_after_success(self):
        ade  = _make_fake_ade()
        dist = _make_fake_dist()
        Orchestrator(_cfg(), ade, dist).run()
        ade.close.assert_called_once()
        dist.close.assert_called_once()

    def test_config_stored_on_session(self):
        cfg  = _cfg(pre=50, post=250)
        ade  = _make_fake_ade()
        dist = _make_fake_dist()
        sess = Orchestrator(cfg, ade, dist).run()
        self.assertIs(sess.config, cfg)


# ---------------------------------------------------------------------------
# Command sequencing
# ---------------------------------------------------------------------------

class TestSequencing(unittest.TestCase):
    def test_manual_trigger_order(self):
        log = []

        ade  = _make_fake_ade()
        dist = _make_fake_dist()

        # Intercept key calls into a shared ordered log.
        for obj, name in [
            (ade.cap_arm_manual, "ade.cap_arm_manual"),
            (dist.arm,           "dist.arm"),
            (dist.start,         "dist.start"),
            (ade.cap_trigger,    "ade.cap_trigger"),
        ]:
            captured = name   # capture loop variable
            orig = obj.side_effect
            def _make_side_effect(n, o):
                def _se(*a, **kw):
                    log.append(n)
                    return None if o is None else o(*a, **kw)
                return _se
            obj.side_effect = _make_side_effect(captured, orig)

        Orchestrator(_cfg(trigger_mode="manual"), ade, dist).run()

        # ADE9000 armed before Distribution armed, armed before started,
        # Distribution started before ADE9000 triggered (sequencer.md §4).
        self.assertLess(log.index("ade.cap_arm_manual"), log.index("dist.arm"))
        self.assertLess(log.index("dist.arm"),           log.index("dist.start"))
        self.assertLess(log.index("dist.start"),         log.index("ade.cap_trigger"))

    def test_dip_mode_no_cap_trigger(self):
        ade  = _make_fake_ade()
        dist = _make_fake_dist()
        Orchestrator(_cfg(trigger_mode="dip"), ade, dist).run()
        ade.cap_arm_dip.assert_called_once()
        ade.cap_trigger.assert_not_called()

    def test_cap_set_uses_config_pre_post(self):
        ade  = _make_fake_ade()
        dist = _make_fake_dist()
        Orchestrator(_cfg(pre=50, post=250), ade, dist).run()
        ade.cap_set.assert_called_once_with(50, 250)

    def test_dist_cap_read_uses_samples_from_drain_status(self):
        dist_cs = _dist_cap_status(samples=42)
        ade  = _make_fake_ade()
        dist = _make_fake_dist(cap_status_sequence=[dist_cs])
        Orchestrator(_cfg(), ade, dist).run()
        dist.cap_read.assert_called_once_with(0, 42)


# ---------------------------------------------------------------------------
# Drain phase
# ---------------------------------------------------------------------------

class TestDrain(unittest.TestCase):
    def test_drain_polls_until_ready(self):
        capturing = CaptureStatus(
            state="CAPTURING", filled=100, pre=100, post=400, total=500, tick_ms=0
        )
        # ADE9000: CAPTURING × 2, then READY
        ade  = _make_fake_ade(cap_status_sequence=[
            capturing, capturing, _cap_status_ready()
        ])
        dist = _make_fake_dist()
        Orchestrator(_cfg(), ade, dist).run()
        self.assertEqual(ade.cap_status.call_count, 3)

    def test_dist_error_state_raises(self):
        ade  = _make_fake_ade()
        dist = _make_fake_dist(cap_status_sequence=[_dist_cap_status(state="ERROR")])
        with self.assertRaises(OrchestratorError):
            Orchestrator(_cfg(), ade, dist).run()

    def test_ade_drain_timeout_raises(self):
        always_capturing = CaptureStatus(
            state="CAPTURING", filled=0, pre=100, post=400, total=500, tick_ms=0
        )
        ade = _make_fake_ade(cap_status_sequence=[always_capturing] * 100)
        dist = _make_fake_dist()
        orc = Orchestrator(_cfg(), ade, dist)
        orc._ADE_DRAIN_TIMEOUT  = 0.05
        orc._DRAIN_POLL_S       = 0.01
        with self.assertRaises(OrchestratorError) as cm:
            orc.run()
        self.assertIn("ADE9000", str(cm.exception))

    def test_dist_drain_timeout_raises(self):
        always_capturing = _dist_cap_status(state="CAPTURING")
        ade  = _make_fake_ade()
        dist = _make_fake_dist(cap_status_sequence=[always_capturing] * 100)
        orc  = Orchestrator(_cfg(), ade, dist)
        orc._DIST_DRAIN_TIMEOUT = 0.05
        orc._DRAIN_POLL_S       = 0.01
        with self.assertRaises(OrchestratorError) as cm:
            orc.run()
        self.assertIn("Distribution", str(cm.exception))

    def test_dist_cap_status_isolated_failure_recovers(self):
        """One blip → retry → success. Must not abort the session."""
        ade  = _make_fake_ade()
        dist = _make_fake_dist()
        # First call raises, second returns READY → drain succeeds.
        dist.cap_status.side_effect = [
            DistributionTimeout("simulated RS-485 garble"),
            _dist_cap_status(state="READY"),
        ]
        orc = Orchestrator(_cfg(), ade, dist)
        orc._DRAIN_POLL_S = 0.01
        orc.run()   # no exception
        self.assertEqual(dist.cap_status.call_count, 2)

    def test_dist_cap_status_consecutive_failures_raise(self):
        """Three consecutive failures → escalate with structured info."""
        ade  = _make_fake_ade()
        dist = _make_fake_dist()
        dist.cap_status.side_effect = [
            DistributionTimeout("garble 1"),
            DistributionTimeout("garble 2"),
            DistributionTimeout("garble 3"),
        ]
        orc = Orchestrator(_cfg(), ade, dist)
        orc._DRAIN_POLL_S = 0.01
        with self.assertRaises(OrchestratorError) as cm:
            orc.run()
        self.assertEqual(cm.exception.phase,   "DRAIN")
        self.assertEqual(cm.exception.device,  "Distribution")
        self.assertEqual(cm.exception.command, "CAP STATUS")
        self.assertIn("unresponsive", str(cm.exception))

    def test_dist_cap_status_failure_counter_resets_on_success(self):
        """fail, fail, success, fail, fail, success → never hits 3 in a row."""
        ade  = _make_fake_ade()
        dist = _make_fake_dist()
        dist.cap_status.side_effect = [
            DistributionTimeout("blip 1"),
            DistributionTimeout("blip 2"),
            _dist_cap_status(state="CAPTURING"),
            DistributionTimeout("blip 3"),
            DistributionTimeout("blip 4"),
            _dist_cap_status(state="READY"),
        ]
        orc = Orchestrator(_cfg(), ade, dist)
        orc._DRAIN_POLL_S = 0.01
        orc.run()   # no exception
        self.assertEqual(dist.cap_status.call_count, 6)


# ---------------------------------------------------------------------------
# Error handling (sequencer.md §7)
# ---------------------------------------------------------------------------

class TestErrorHandling(unittest.TestCase):
    def test_vbus_error_raises_orchestrator_error(self):
        ade  = _make_fake_ade()
        dist = _make_fake_dist(start_result="vbus_error")
        with self.assertRaises(OrchestratorError) as cm:
            Orchestrator(_cfg(), ade, dist).run()
        self.assertIn("VBUS", str(cm.exception))

    def test_vbus_error_aborts_ade9000(self):
        ade  = _make_fake_ade()
        dist = _make_fake_dist(start_result="vbus_error")
        with self.assertRaises(OrchestratorError):
            Orchestrator(_cfg(), ade, dist).run()
        ade.cap_abort.assert_called()

    def test_ports_closed_on_error(self):
        ade  = _make_fake_ade(wmode_capture_ok=False)
        dist = _make_fake_dist()
        with self.assertRaises(Exception):
            Orchestrator(_cfg(), ade, dist).run()
        ade.close.assert_called_once()
        dist.close.assert_called_once()

    def test_dist_open_failure_closes_ade(self):
        ade  = _make_fake_ade()
        dist = _make_fake_dist()
        dist.open.side_effect = OSError("port not found")
        with self.assertRaises(OSError):
            Orchestrator(_cfg(), ade, dist).run()
        ade.close.assert_called_once()

    def test_ade_connect_failure_raises(self):
        ade  = _make_fake_ade(wmode_capture_ok=False)
        dist = _make_fake_dist()
        with self.assertRaises(Exception):
            Orchestrator(_cfg(), ade, dist).run()

    def test_abort_swallows_abort_errors(self):
        """If cap_abort itself fails, the original error still propagates cleanly."""
        ade  = _make_fake_ade(wmode_capture_ok=False)
        ade.cap_abort.side_effect = Exception("abort failed")
        dist = _make_fake_dist()
        with self.assertRaises(Exception):
            Orchestrator(_cfg(), ade, dist).run()


# ---------------------------------------------------------------------------
# Progress callbacks
# ---------------------------------------------------------------------------

class TestProgressCallback(unittest.TestCase):
    def test_progress_called_for_all_phases(self):
        phases = []
        def on_progress(phase, msg):
            phases.append(phase)

        ade  = _make_fake_ade()
        dist = _make_fake_dist()
        Orchestrator(_cfg(), ade, dist, on_progress=on_progress).run()

        for expected in ("CONNECT", "SYNC", "ARM", "FIRE", "DRAIN", "READ"):
            self.assertIn(expected, phases, f"missing phase {expected!r}")

    def test_no_callback_does_not_raise(self):
        ade  = _make_fake_ade()
        dist = _make_fake_dist()
        Orchestrator(_cfg(), ade, dist, on_progress=None).run()


if __name__ == "__main__":
    unittest.main()
