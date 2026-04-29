"""Run from software/pc_monitor/: `python -m pytest tests/`

Tests for OrchestratorWorker. Uses PySide6 QApplication + event loop.
Orchestrator and session_writer are patched so no serial ports or disk I/O
are needed.

Skipped automatically when PySide6 is not installed (e.g. in minimal CI).
"""
import sys
import tempfile
import unittest
from typing import List
from unittest.mock import MagicMock, patch

import pytest
pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication

from core.capture_parser import CaptureDone, CaptureSample, CaptureStatus
from core.distribution_client import DistCapStatus
from core.orchestrator import CaptureSession, OrchestratorConfig
from core.orchestrator_worker import OrchestratorWorker
from core.sync_probe import SyncResult


# ---------------------------------------------------------------------------
# One QCoreApplication for the whole module
# ---------------------------------------------------------------------------

_APP: QCoreApplication | None = None


def _app() -> QCoreApplication:
    global _APP
    if _APP is None:
        _APP = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    return _APP


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_session(output_dir: str) -> CaptureSession:
    cfg = OrchestratorConfig(arduino_port="COM5", dist_port="COM7",
                             output_dir=output_dir)
    return CaptureSession(
        config          = cfg,
        started_at_ns   = 1_000_000_000,
        session_id      = "2026-01-01T12-00-00",
        arduino_samples = [CaptureSample(i=0, uab=400.0, ubc=400.0, uca=400.0,
                                         ia=1.0, ib=1.0, ic=1.0)],
        arduino_done    = CaptureDone(n=1, trigger_tick_ms=42000,
                                      sample_period_ms=10, pre=0, post=1,
                                      trigger_index=0),
        arduino_sync    = SyncResult(offset_ms=100.0, rtt_ms_median=2.0,
                                     rtt_ms_best=1.0, n_samples=25, n_used=8),
        arduino_port    = "COM5",
        dist_samples    = [(0, [0]*8, ["0000"]*8)],
        dist_status     = DistCapStatus(state="READY", samples=1, trigger_idx=0,
                                        sample_period_ms=25, channels=8,
                                        trigger_tick=99000),
        dist_sync       = SyncResult(offset_ms=2.0, rtt_ms_median=4.0,
                                     rtt_ms_best=3.0, n_samples=25, n_used=8),
        dist_port       = "COM7",
        offset_ad_ms    = 98.0,
    )


def _cfg(output_dir: str) -> OrchestratorConfig:
    return OrchestratorConfig(arduino_port="COM5", dist_port="COM7",
                              output_dir=output_dir)


def _run_worker(worker: OrchestratorWorker) -> None:
    """Start worker and block until finished (max 5 s).

    `worker.wait()` blocks the test thread but does not pump its event loop,
    so cross-thread queued signals stay parked until we drain them with
    `processEvents()`. Without this drain the connect() callbacks recording
    `done` / `failed` / `progress` are never invoked and assertions fail.
    """
    app = _app()
    worker.start()
    worker.wait(5000)
    app.processEvents()


# ---------------------------------------------------------------------------
# Happy-path signals
# ---------------------------------------------------------------------------

class TestWorkerHappyPath(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()

    def _patched_worker(self) -> OrchestratorWorker:
        sess = _fake_session(self._tmp.name)
        ade  = MagicMock()
        dist = MagicMock()
        worker = OrchestratorWorker(_cfg(self._tmp.name), ade, dist)

        with patch("core.orchestrator_worker.Orchestrator") as MockOrc, \
             patch("core.orchestrator_worker.write_session") as mock_write:
            MockOrc.return_value.run.return_value = sess
            mock_write.return_value = MagicMock()
            self._mock_orc   = MockOrc
            self._mock_write = mock_write
            # We need the patches live during run(), so we re-patch here.
            # Delegate to a helper that injects the mocks for real.
        return worker, sess

    def test_done_signal_emitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            sess = _fake_session(tmp)
            done_payloads: List[CaptureSession] = []
            failed_payloads: List[tuple]        = []

            ade  = MagicMock()
            dist = MagicMock()
            worker = OrchestratorWorker(_cfg(tmp), ade, dist)
            worker.done.connect(done_payloads.append)
            worker.failed.connect(lambda msg, info: failed_payloads.append((msg, info)))

            with patch("core.orchestrator_worker.Orchestrator") as MockOrc, \
                 patch("core.orchestrator_worker.write_session"):
                MockOrc.return_value.run.return_value = sess
                _run_worker(worker)

            self.assertEqual(len(done_payloads),    1)
            self.assertEqual(len(failed_payloads), 0)
            self.assertIs(done_payloads[0], sess)

    def test_failed_not_emitted_on_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            sess = _fake_session(tmp)
            failed_payloads: List[tuple] = []

            ade  = MagicMock()
            dist = MagicMock()
            worker = OrchestratorWorker(_cfg(tmp), ade, dist)
            worker.failed.connect(lambda msg, info: failed_payloads.append((msg, info)))

            with patch("core.orchestrator_worker.Orchestrator") as MockOrc, \
                 patch("core.orchestrator_worker.write_session"):
                MockOrc.return_value.run.return_value = sess
                _run_worker(worker)

            self.assertEqual(failed_payloads, [])

    def test_progress_signals_forwarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            sess = _fake_session(tmp)
            phases: List[str] = []

            def fake_orc_init(config, ade, dist, on_progress=None):
                m = MagicMock()
                def fake_run():
                    if on_progress:
                        on_progress("CONNECT", "opening")
                        on_progress("SYNC",    "syncing")
                    return sess
                m.run.side_effect = fake_run
                return m

            ade  = MagicMock()
            dist = MagicMock()
            worker = OrchestratorWorker(_cfg(tmp), ade, dist)
            worker.progress.connect(lambda ph, _msg: phases.append(ph))

            with patch("core.orchestrator_worker.Orchestrator",
                       side_effect=fake_orc_init), \
                 patch("core.orchestrator_worker.write_session"):
                _run_worker(worker)

            self.assertIn("CONNECT", phases)
            self.assertIn("SYNC",    phases)

    def test_write_phase_progress_emitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            sess = _fake_session(tmp)
            phases: List[str] = []

            ade  = MagicMock()
            dist = MagicMock()
            worker = OrchestratorWorker(_cfg(tmp), ade, dist)
            worker.progress.connect(lambda ph, _msg: phases.append(ph))

            with patch("core.orchestrator_worker.Orchestrator") as MockOrc, \
                 patch("core.orchestrator_worker.write_session"):
                MockOrc.return_value.run.return_value = sess
                _run_worker(worker)

            self.assertIn("WRITE", phases)


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------

class TestWorkerErrorPath(unittest.TestCase):
    def test_orchestrator_error_emits_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            done_payloads:   List[object] = []
            failed_payloads: List[tuple]  = []

            ade  = MagicMock()
            dist = MagicMock()
            worker = OrchestratorWorker(_cfg(tmp), ade, dist)
            worker.done.connect(done_payloads.append)
            worker.failed.connect(lambda msg, info: failed_payloads.append((msg, info)))

            with patch("core.orchestrator_worker.Orchestrator") as MockOrc:
                MockOrc.return_value.run.side_effect = RuntimeError("port gone")
                _run_worker(worker)

            self.assertEqual(len(done_payloads),    0)
            self.assertEqual(len(failed_payloads), 1)
            msg, info = failed_payloads[0]
            self.assertIn("port gone", msg)
            self.assertEqual(info, {})   # non-OrchestratorError → no structured info

    def test_orchestrator_error_carries_structured_info(self):
        from core.orchestrator import OrchestratorError
        with tempfile.TemporaryDirectory() as tmp:
            failed_payloads: List[tuple] = []

            ade  = MagicMock()
            dist = MagicMock()
            worker = OrchestratorWorker(_cfg(tmp), ade, dist)
            worker.failed.connect(lambda msg, info: failed_payloads.append((msg, info)))

            with patch("core.orchestrator_worker.Orchestrator") as MockOrc:
                MockOrc.return_value.run.side_effect = OrchestratorError(
                    "VBUS already present",
                    phase="FIRE", device="Distribution", command="START",
                )
                _run_worker(worker)

            self.assertEqual(len(failed_payloads), 1)
            msg, info = failed_payloads[0]
            self.assertIn("VBUS", msg)
            self.assertEqual(info["phase"],   "FIRE")
            self.assertEqual(info["device"],  "Distribution")
            self.assertEqual(info["command"], "START")

    def test_write_session_error_emits_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            sess = _fake_session(tmp)
            failed_payloads: List[tuple] = []

            ade  = MagicMock()
            dist = MagicMock()
            worker = OrchestratorWorker(_cfg(tmp), ade, dist)
            worker.failed.connect(lambda msg, info: failed_payloads.append((msg, info)))

            with patch("core.orchestrator_worker.Orchestrator") as MockOrc, \
                 patch("core.orchestrator_worker.write_session",
                        side_effect=OSError("disk full")):
                MockOrc.return_value.run.return_value = sess
                _run_worker(worker)

            self.assertEqual(len(failed_payloads), 1)
            msg, _info = failed_payloads[0]
            self.assertIn("disk full", msg)

    def test_done_not_emitted_on_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            done_payloads: List[object] = []

            ade  = MagicMock()
            dist = MagicMock()
            worker = OrchestratorWorker(_cfg(tmp), ade, dist)
            worker.done.connect(done_payloads.append)

            with patch("core.orchestrator_worker.Orchestrator") as MockOrc:
                MockOrc.return_value.run.side_effect = ValueError("bad config")
                _run_worker(worker)

            self.assertEqual(done_payloads, [])


if __name__ == "__main__":
    unittest.main()
