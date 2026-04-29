"""Run from software/pc_monitor/: `python -m pytest tests/`

Tests for session_writer.write_session().
No serial ports, no orchestrator — builds CaptureSession directly.
All filesystem operations happen in a TemporaryDirectory.
"""
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.capture_parser import CaptureDone, CaptureSample
from core.distribution_client import DistCapStatus
from core.orchestrator import CaptureSession, OrchestratorConfig
from core.session_writer import SCHEMA_VERSION, SessionPaths, write_session
from core.sync_probe import SyncResult


# ---------------------------------------------------------------------------
# Test fixture helpers
# ---------------------------------------------------------------------------

def _cfg(output_dir: str) -> OrchestratorConfig:
    return OrchestratorConfig(
        arduino_port="COM5", dist_port="COM7",
        pre=100, post=400, output_dir=output_dir,
    )


def _session(output_dir: str, *, n_arduino: int = 5, n_dist: int = 5) -> CaptureSession:
    arduino_samples = [
        CaptureSample(
            i=i - 2, uab=400.0 + i * 0.1, ubc=401.0, uca=399.5,
            ia=1.1, ib=1.2, ic=1.3,
        )
        for i in range(n_arduino)
    ]
    dist_samples = [
        (i, [i * 10 + ch for ch in range(8)], [f"{i*10+ch:04X}" for ch in range(8)])
        for i in range(n_dist)
    ]
    return CaptureSession(
        config          = _cfg(output_dir),
        started_at_ns   = 1_000_000_000,
        session_id      = "2026-01-01T12-00-00",
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
        dist_sync       = SyncResult(
            offset_ms=2.0, rtt_ms_median=4.5, rtt_ms_best=3.5,
            n_samples=25, n_used=8,
        ),
        dist_port       = "COM7",
        offset_ad_ms    = 121.45,
    )


# ---------------------------------------------------------------------------
# SessionPaths / directory structure
# ---------------------------------------------------------------------------

class TestSessionPaths(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sess = _session(self._tmp.name)
        self.paths = write_session(self.sess)

    def tearDown(self):
        self._tmp.cleanup()

    def test_returns_session_paths(self):
        self.assertIsInstance(self.paths, SessionPaths)

    def test_session_dir_exists(self):
        self.assertTrue(self.paths.session_dir.is_dir())

    def test_session_dir_name_matches_id(self):
        self.assertEqual(self.paths.session_dir.name, self.sess.session_id)

    def test_all_files_exist(self):
        self.assertTrue(self.paths.arduino_csv.exists())
        self.assertTrue(self.paths.dist_csv.exists())
        self.assertTrue(self.paths.session_json.exists())

    def test_paths_point_inside_session_dir(self):
        self.assertEqual(self.paths.arduino_csv.parent,  self.paths.session_dir)
        self.assertEqual(self.paths.dist_csv.parent,     self.paths.session_dir)
        self.assertEqual(self.paths.session_json.parent, self.paths.session_dir)

    def test_output_dir_created_if_absent(self):
        nested = Path(self._tmp.name) / "deep" / "nested"
        sess = _session(str(nested))
        paths = write_session(sess)
        self.assertTrue(paths.session_dir.is_dir())


# ---------------------------------------------------------------------------
# arduino.csv
# ---------------------------------------------------------------------------

class TestArduinoCsv(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sess  = _session(self._tmp.name)
        self.paths = write_session(self.sess)
        with self.paths.arduino_csv.open(newline="", encoding="utf-8") as f:
            self.rows = list(csv.reader(f))

    def tearDown(self):
        self._tmp.cleanup()

    def test_header_row(self):
        self.assertEqual(self.rows[0], ["i", "uab", "ubc", "uca", "ia", "ib", "ic"])

    def test_row_count_matches_samples(self):
        # header + one row per sample
        self.assertEqual(len(self.rows), 1 + len(self.sess.arduino_samples))

    def test_index_column_values(self):
        indices = [int(r[0]) for r in self.rows[1:]]
        expected = [s.i for s in self.sess.arduino_samples]
        self.assertEqual(indices, expected)

    def test_voltage_column_values(self):
        uab_col = [float(r[1]) for r in self.rows[1:]]
        expected = [s.uab for s in self.sess.arduino_samples]
        for got, exp in zip(uab_col, expected):
            self.assertAlmostEqual(got, exp, places=4)

    def test_empty_samples_writes_header_only(self):
        tmp2 = tempfile.TemporaryDirectory()
        try:
            sess = _session(tmp2.name, n_arduino=0)
            p = write_session(sess)
            with p.arduino_csv.open(newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))
            self.assertEqual(len(rows), 1)
        finally:
            tmp2.cleanup()


# ---------------------------------------------------------------------------
# distribution.csv
# ---------------------------------------------------------------------------

class TestDistCsv(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sess  = _session(self._tmp.name)
        self.paths = write_session(self.sess)
        with self.paths.dist_csv.open(newline="", encoding="utf-8") as f:
            self.rows = list(csv.reader(f))

    def tearDown(self):
        self._tmp.cleanup()

    def test_header_starts_with_idx(self):
        self.assertEqual(self.rows[0][0], "idx")

    def test_header_has_16_channel_columns(self):
        # 8 channels × 2 (raw + hex) = 16 columns + 1 idx = 17 total
        self.assertEqual(len(self.rows[0]), 17)

    def test_channel_header_names(self):
        hdr = self.rows[0]
        self.assertIn("ch0_raw", hdr)
        self.assertIn("ch0_hex", hdr)
        self.assertIn("ch7_raw", hdr)
        self.assertIn("ch7_hex", hdr)

    def test_row_count_matches_samples(self):
        self.assertEqual(len(self.rows), 1 + len(self.sess.dist_samples))

    def test_idx_column(self):
        indices = [int(r[0]) for r in self.rows[1:]]
        expected = [t[0] for t in self.sess.dist_samples]
        self.assertEqual(indices, expected)

    def test_raw_values_preserved(self):
        # First sample, channel 0
        raw_col_idx = self.rows[0].index("ch0_raw")
        got = int(self.rows[1][raw_col_idx])
        _, raw_ints, _ = self.sess.dist_samples[0]
        self.assertEqual(got, raw_ints[0])

    def test_hex_values_preserved(self):
        hex_col_idx = self.rows[0].index("ch0_hex")
        got = self.rows[1][hex_col_idx]
        _, _, hex_strs = self.sess.dist_samples[0]
        self.assertEqual(got, hex_strs[0])


# ---------------------------------------------------------------------------
# session.json
# ---------------------------------------------------------------------------

class TestSessionJson(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sess  = _session(self._tmp.name)
        self.paths = write_session(self.sess)
        self.doc   = json.loads(self.paths.session_json.read_text(encoding="utf-8"))

    def tearDown(self):
        self._tmp.cleanup()

    def test_schema_version(self):
        self.assertEqual(self.doc["schema_version"], SCHEMA_VERSION)

    def test_session_id(self):
        self.assertEqual(self.doc["session_id"], self.sess.session_id)

    def test_started_at_pc_ns(self):
        self.assertEqual(self.doc["started_at_pc_ns"], self.sess.started_at_ns)

    def test_offset_ad_ms(self):
        self.assertAlmostEqual(self.doc["offset_ad_ms"], self.sess.offset_ad_ms)

    def test_arduino_section_keys(self):
        a = self.doc["arduino"]
        for key in ("port", "trigger_mode", "pre", "post",
                    "trigger_tick_ms", "sample_period_ms",
                    "offset_ms", "rtt_ms_best", "n_sync_samples"):
            self.assertIn(key, a, f"missing key: {key!r}")

    def test_arduino_port(self):
        self.assertEqual(self.doc["arduino"]["port"], self.sess.arduino_port)

    def test_arduino_trigger_tick(self):
        self.assertEqual(
            self.doc["arduino"]["trigger_tick_ms"],
            self.sess.arduino_done.trigger_tick_ms,
        )

    def test_arduino_offset_ms(self):
        self.assertAlmostEqual(
            self.doc["arduino"]["offset_ms"],
            self.sess.arduino_sync.offset_ms,
        )

    def test_distribution_section_keys(self):
        d = self.doc["distribution"]
        for key in ("port", "trigger_tick_ms", "sample_period_ms", "channels",
                    "offset_ms", "rtt_ms_best", "n_sync_samples"):
            self.assertIn(key, d, f"missing key: {key!r}")

    def test_distribution_trigger_tick(self):
        self.assertEqual(
            self.doc["distribution"]["trigger_tick_ms"],
            self.sess.dist_status.trigger_tick,
        )

    def test_distribution_offset_ms(self):
        self.assertAlmostEqual(
            self.doc["distribution"]["offset_ms"],
            self.sess.dist_sync.offset_ms,
        )

    def test_distribution_rtt_ms_best(self):
        self.assertAlmostEqual(
            self.doc["distribution"]["rtt_ms_best"],
            self.sess.dist_sync.rtt_ms_best,
        )

    def test_distribution_n_sync_samples(self):
        self.assertEqual(
            self.doc["distribution"]["n_sync_samples"],
            self.sess.dist_sync.n_samples,
        )

    def test_valid_json_structure(self):
        # Re-serialise and parse to catch any non-serialisable types.
        roundtrip = json.loads(json.dumps(self.doc))
        self.assertEqual(roundtrip["session_id"], self.sess.session_id)


# ---------------------------------------------------------------------------
# All-or-nothing atomicity
# ---------------------------------------------------------------------------

class TestAtomicity(unittest.TestCase):
    def test_no_directory_left_on_json_write_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            sess = _session(tmp)
            target = Path(tmp) / sess.session_id

            with patch("core.session_writer._write_session_json",
                       side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    write_session(sess)

            self.assertFalse(target.exists())
            # No .tmp_ remnants either
            remnants = list(Path(tmp).glob(".tmp_*"))
            self.assertEqual(remnants, [])

    def test_no_directory_left_on_csv_write_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            sess = _session(tmp)
            target = Path(tmp) / sess.session_id

            with patch("core.session_writer._write_arduino_csv",
                       side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    write_session(sess)

            self.assertFalse(target.exists())

    def test_session_dir_absent_until_complete(self):
        """Verifies that the target dir only appears after all writes succeed."""
        with tempfile.TemporaryDirectory() as tmp:
            sess = _session(tmp)
            target = Path(tmp) / sess.session_id
            self.assertFalse(target.exists())
            write_session(sess)
            self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main()
