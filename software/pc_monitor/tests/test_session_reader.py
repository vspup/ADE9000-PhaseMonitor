"""Run from software/pc_monitor/: `python -m pytest tests/`

Tests for session_reader.read_session() and list_sessions().
Covers the writer→reader round-trip plus directory listing edge cases.
"""
import json
import tempfile
import unittest
from pathlib import Path

from core.capture_parser import CaptureDone, CaptureSample
from core.distribution_client import DistCapStatus
from core.orchestrator import CaptureSession, OrchestratorConfig
from core.session_reader import (
    SessionEntry, SessionReadError, list_sessions, read_session,
)
from core.session_writer import write_session
from core.sync_probe import SyncResult


# ---------------------------------------------------------------------------
# Fixture builder (kept independent from test_session_writer to avoid
# cross-test coupling; small intentional duplication)
# ---------------------------------------------------------------------------

def _session(output_dir: str, *, session_id: str = "2026-01-01T12-00-00",
             n_arduino: int = 5, n_dist: int = 5) -> CaptureSession:
    arduino_samples = [
        CaptureSample(
            i=i - 2, uab=400.0 + i * 0.1, ubc=401.0 + i * 0.2,
            uca=399.5 - i * 0.1, ia=1.1 + i, ib=1.2, ic=1.3,
        )
        for i in range(n_arduino)
    ]
    dist_samples = [
        (i, [i * 10 + ch for ch in range(8)],
            [f"{i*10+ch:04X}" for ch in range(8)])
        for i in range(n_dist)
    ]
    cfg = OrchestratorConfig(
        arduino_port="COM5", dist_port="COM7",
        pre=2, post=3, trigger_mode="manual", output_dir=output_dir,
    )
    return CaptureSession(
        config          = cfg,
        started_at_ns   = 1_234_567_890,
        session_id      = session_id,
        arduino_samples = arduino_samples,
        arduino_done    = CaptureDone(
            n=n_arduino, trigger_tick_ms=42000, sample_period_ms=10,
            pre=2, post=3, trigger_index=0,
        ),
        arduino_sync    = SyncResult(
            offset_ms=123.45, rtt_ms_median=2.0, rtt_ms_best=1.0,
            n_samples=25, n_used=8,
        ),
        arduino_port    = "COM5",
        dist_samples    = dist_samples,
        dist_status     = DistCapStatus(
            state="READY", samples=n_dist, trigger_idx=2,
            sample_period_ms=25, channels=8, trigger_tick=99000,
        ),
        dist_rtt_ms     = 4.0,
        dist_port       = "COM7",
        offset_ad_ms    = 121.45,
    )


# ---------------------------------------------------------------------------
# Round-trip — fields the viewer actually consumes
# ---------------------------------------------------------------------------

class TestRoundTrip(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.orig = _session(self._tmp.name)
        self.paths = write_session(self.orig)
        self.rt   = read_session(self.paths.session_dir)

    def tearDown(self):
        self._tmp.cleanup()

    def test_session_id(self):
        self.assertEqual(self.rt.session_id, self.orig.session_id)

    def test_started_at_ns(self):
        self.assertEqual(self.rt.started_at_ns, self.orig.started_at_ns)

    def test_offset_ad_ms(self):
        self.assertAlmostEqual(self.rt.offset_ad_ms, self.orig.offset_ad_ms)

    def test_arduino_port(self):
        self.assertEqual(self.rt.arduino_port, self.orig.arduino_port)

    def test_dist_port(self):
        self.assertEqual(self.rt.dist_port, self.orig.dist_port)

    def test_arduino_sample_count(self):
        self.assertEqual(len(self.rt.arduino_samples),
                         len(self.orig.arduino_samples))

    def test_arduino_sample_values(self):
        for got, exp in zip(self.rt.arduino_samples, self.orig.arduino_samples):
            self.assertEqual(got.i, exp.i)
            self.assertAlmostEqual(got.uab, exp.uab, places=4)
            self.assertAlmostEqual(got.ubc, exp.ubc, places=4)
            self.assertAlmostEqual(got.uca, exp.uca, places=4)
            self.assertAlmostEqual(got.ia,  exp.ia,  places=4)
            self.assertAlmostEqual(got.ib,  exp.ib,  places=4)
            self.assertAlmostEqual(got.ic,  exp.ic,  places=4)

    def test_dist_sample_count(self):
        self.assertEqual(len(self.rt.dist_samples),
                         len(self.orig.dist_samples))

    def test_dist_sample_idx(self):
        got_idx = [t[0] for t in self.rt.dist_samples]
        exp_idx = [t[0] for t in self.orig.dist_samples]
        self.assertEqual(got_idx, exp_idx)

    def test_dist_sample_raw_ints(self):
        got = [t[1] for t in self.rt.dist_samples]
        exp = [t[1] for t in self.orig.dist_samples]
        self.assertEqual(got, exp)

    def test_dist_sample_hex_strs(self):
        got = [t[2] for t in self.rt.dist_samples]
        exp = [t[2] for t in self.orig.dist_samples]
        self.assertEqual(got, exp)

    def test_arduino_done_sample_period(self):
        self.assertEqual(self.rt.arduino_done.sample_period_ms,
                         self.orig.arduino_done.sample_period_ms)

    def test_arduino_done_trigger_tick(self):
        self.assertEqual(self.rt.arduino_done.trigger_tick_ms,
                         self.orig.arduino_done.trigger_tick_ms)

    def test_arduino_done_n_matches_samples(self):
        self.assertEqual(self.rt.arduino_done.n,
                         len(self.rt.arduino_samples))

    def test_dist_status_trigger_idx(self):
        # The viewer needs this — explicitly verified.
        self.assertEqual(self.rt.dist_status.trigger_idx,
                         self.orig.dist_status.trigger_idx)

    def test_dist_status_sample_period(self):
        self.assertEqual(self.rt.dist_status.sample_period_ms,
                         self.orig.dist_status.sample_period_ms)

    def test_dist_status_channels(self):
        self.assertEqual(self.rt.dist_status.channels,
                         self.orig.dist_status.channels)

    def test_arduino_sync_offset(self):
        self.assertAlmostEqual(self.rt.arduino_sync.offset_ms,
                               self.orig.arduino_sync.offset_ms)

    def test_arduino_sync_rtt_best(self):
        self.assertAlmostEqual(self.rt.arduino_sync.rtt_ms_best,
                               self.orig.arduino_sync.rtt_ms_best)

    def test_dist_rtt_ms(self):
        self.assertAlmostEqual(self.rt.dist_rtt_ms, self.orig.dist_rtt_ms)

    def test_config_pre_post(self):
        self.assertEqual(self.rt.config.pre,  self.orig.config.pre)
        self.assertEqual(self.rt.config.post, self.orig.config.post)

    def test_config_trigger_mode(self):
        self.assertEqual(self.rt.config.trigger_mode,
                         self.orig.config.trigger_mode)


# ---------------------------------------------------------------------------
# Empty session (no samples on either device)
# ---------------------------------------------------------------------------

class TestEmptySession(unittest.TestCase):
    def test_zero_arduino_zero_dist(self):
        with tempfile.TemporaryDirectory() as tmp:
            sess = _session(tmp, n_arduino=0, n_dist=0)
            paths = write_session(sess)
            rt = read_session(paths.session_dir)
            self.assertEqual(rt.arduino_samples, [])
            self.assertEqual(rt.dist_samples, [])
            self.assertEqual(rt.arduino_done.n, 0)


# ---------------------------------------------------------------------------
# Backwards compatibility: old session.json without trigger_idx
# ---------------------------------------------------------------------------

class TestLegacyJson(unittest.TestCase):
    """Sessions written before trigger_idx was added to session.json must
    still be openable (default to 0)."""

    def test_missing_trigger_idx_defaults_to_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            sess = _session(tmp)
            paths = write_session(sess)

            # Strip trigger_idx to simulate a legacy file
            doc = json.loads(paths.session_json.read_text(encoding="utf-8"))
            doc["distribution"].pop("trigger_idx", None)
            paths.session_json.write_text(json.dumps(doc), encoding="utf-8")

            rt = read_session(paths.session_dir)
            self.assertEqual(rt.dist_status.trigger_idx, 0)


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

class TestErrors(unittest.TestCase):
    def test_missing_directory_raises(self):
        with self.assertRaises(SessionReadError):
            read_session(Path("/no/such/directory_xyz"))

    def test_missing_session_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "broken"
            d.mkdir()
            (d / "arduino.csv").write_text("i,uab,ubc,uca,ia,ib,ic\n", encoding="utf-8")
            (d / "distribution.csv").write_text("idx\n", encoding="utf-8")
            with self.assertRaises(SessionReadError):
                read_session(d)

    def test_unparseable_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            sess = _session(tmp)
            paths = write_session(sess)
            paths.session_json.write_text("{ not json", encoding="utf-8")
            with self.assertRaises(SessionReadError):
                read_session(paths.session_dir)


# ---------------------------------------------------------------------------
# list_sessions — directory listing for the browser UI
# ---------------------------------------------------------------------------

class TestListSessions(unittest.TestCase):
    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(list_sessions(Path(tmp)), [])

    def test_nonexistent_dir(self):
        self.assertEqual(list_sessions(Path("/no/such/dir_xyz")), [])

    def test_lists_only_session_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            # one valid session
            sess = _session(tmp, session_id="2026-01-01T10-00-00")
            write_session(sess)
            # an unrelated file
            (tmp_p / "cap_old.csv").write_text("legacy", encoding="utf-8")
            # a non-session dir
            (tmp_p / "scratch").mkdir()
            (tmp_p / "scratch" / "notes.txt").write_text("x", encoding="utf-8")

            entries = list_sessions(tmp_p)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].session_id, "2026-01-01T10-00-00")

    def test_sorted_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            for sid in ("2026-01-01T10-00-00",
                        "2026-04-15T22-30-00",
                        "2025-12-31T23-59-59"):
                write_session(_session(tmp, session_id=sid))
            ids = [e.session_id for e in list_sessions(Path(tmp))]
            self.assertEqual(ids, [
                "2026-04-15T22-30-00",
                "2026-01-01T10-00-00",
                "2025-12-31T23-59-59",
            ])

    def test_entry_carries_sample_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_session(_session(tmp, n_arduino=7, n_dist=3))
            entry = list_sessions(Path(tmp))[0]
            self.assertIsInstance(entry, SessionEntry)
            self.assertEqual(entry.arduino_samples, 7)
            self.assertEqual(entry.dist_samples,    3)


if __name__ == "__main__":
    unittest.main()
