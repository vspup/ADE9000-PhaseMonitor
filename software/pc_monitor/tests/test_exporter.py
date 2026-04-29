"""Run from software/pc_monitor/: `python -m pytest tests/`

Pure-logic tests for core.exporter. No Qt, no filesystem, no clipboard.
"""
import unittest

from core.capture_parser import CaptureSample
from core.distribution_client import CHANNEL_KEYS
from core.exporter import slice_arduino, slice_distribution


def _arduino_samples(indices: list[int]) -> list[CaptureSample]:
    return [
        CaptureSample(
            i=i, uab=400.0 + i, ubc=401.0 + i, uca=399.0 + i,
            ia=1.0 + 0.1 * i, ib=2.0, ic=3.0,
        )
        for i in indices
    ]


def _dist_samples(indices: list[int]):
    """Build (idx, raw_ints, hex_strs) tuples — match DistCapSample shape."""
    return [
        (idx, [idx + ch for ch in range(8)],
              [f"{(idx + ch) & 0xFFFF:04X}" for ch in range(8)])
        for idx in indices
    ]


# ---------------------------------------------------------------------------
# slice_arduino
# ---------------------------------------------------------------------------

class TestSliceArduino(unittest.TestCase):
    def test_header(self):
        h, _ = slice_arduino(_arduino_samples([0]), 10, 0.0, 0.0)
        self.assertEqual(h, ["t_ms", "i", "uab", "ubc", "uca", "ia", "ib", "ic"])

    def test_inclusive_window(self):
        # period 10 ms, indices -2..2 → t_ms = -20, -10, 0, 10, 20
        samples = _arduino_samples([-2, -1, 0, 1, 2])
        _, rows = slice_arduino(samples, 10, -10.0, 10.0)
        ts = [r[0] for r in rows]
        self.assertEqual(ts, [-10, 0, 10])

    def test_inclusive_at_boundary(self):
        samples = _arduino_samples([-2, -1, 0, 1, 2])
        _, rows = slice_arduino(samples, 10, -20.0, -20.0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], -20)

    def test_swap_t1_gt_t2(self):
        samples = _arduino_samples([0, 1, 2])
        _, a = slice_arduino(samples, 10, 0.0,  20.0)
        _, b = slice_arduino(samples, 10, 20.0, 0.0)
        self.assertEqual(a, b)

    def test_empty_outside_window(self):
        samples = _arduino_samples([0, 1, 2])
        _, rows = slice_arduino(samples, 10, 100.0, 200.0)
        self.assertEqual(rows, [])

    def test_row_columns_match_sample(self):
        s = CaptureSample(i=5, uab=1.0, ubc=2.0, uca=3.0, ia=0.1, ib=0.2, ic=0.3)
        _, rows = slice_arduino([s], 10, 50.0, 50.0)
        self.assertEqual(rows, [[50, 5, 1.0, 2.0, 3.0, 0.1, 0.2, 0.3]])

    def test_period_other_than_ten(self):
        # Verify t_ms = i * period; not hard-coded to 10.
        samples = _arduino_samples([0, 1])
        _, rows = slice_arduino(samples, 25, 0.0, 25.0)
        self.assertEqual([r[0] for r in rows], [0, 25])


# ---------------------------------------------------------------------------
# slice_distribution
# ---------------------------------------------------------------------------

class TestSliceDistribution(unittest.TestCase):
    def test_header_no_filter(self):
        h, _ = slice_distribution(
            _dist_samples([0]), trigger_idx=0, period_ms=25,
            t1_ms=0.0, t2_ms=0.0,
        )
        # 1 t_ms + 1 idx + 8 channels × (raw+hex) = 18 columns
        self.assertEqual(len(h), 18)
        self.assertEqual(h[:2], ["t_ms", "idx"])
        self.assertIn("u17_ch0_raw", h)
        self.assertIn("u18_ch3_hex", h)

    def test_trigger_offset_applied(self):
        # trigger_idx = 2 means idx=2 sits at t=0
        samples = _dist_samples([0, 1, 2, 3, 4])
        _, rows = slice_distribution(
            samples, trigger_idx=2, period_ms=25,
            t1_ms=-25.0, t2_ms=25.0,
        )
        ts = [r[0] for r in rows]
        # idx=1 → t=-25, idx=2 → t=0, idx=3 → t=25
        self.assertEqual(ts, [-25, 0, 25])

    def test_filter_keeps_only_selected(self):
        samples = _dist_samples([0])
        h, rows = slice_distribution(
            samples, trigger_idx=0, period_ms=25,
            t1_ms=0.0, t2_ms=0.0,
            channel_filter={"u17_ch0", "u18_ch3"},
        )
        # 2 prefix + 2 channels × 2 = 6 columns
        self.assertEqual(len(h), 6)
        self.assertEqual(h, ["t_ms", "idx",
                             "u17_ch0_raw", "u17_ch0_hex",
                             "u18_ch3_raw", "u18_ch3_hex"])
        # raw values are: ch_idx=0 → 0+0=0, ch_idx=7 (u18_ch3) → 0+7=7
        # row layout: [t_ms, idx, u17_ch0_raw, u17_ch0_hex, u18_ch3_raw, u18_ch3_hex]
        self.assertEqual(rows[0][2], 0)
        self.assertEqual(rows[0][4], 7)
        self.assertEqual(rows[0][3], "0000")
        self.assertEqual(rows[0][5], "0007")

    def test_filter_empty_keeps_only_t_idx(self):
        samples = _dist_samples([0])
        h, rows = slice_distribution(
            samples, trigger_idx=0, period_ms=25,
            t1_ms=0.0, t2_ms=0.0,
            channel_filter=set(),
        )
        self.assertEqual(h, ["t_ms", "idx"])
        self.assertEqual(rows[0], [0, 0])

    def test_filter_none_writes_all_channels(self):
        h_none, rows_none = slice_distribution(
            _dist_samples([0]), trigger_idx=0, period_ms=25,
            t1_ms=0.0, t2_ms=0.0, channel_filter=None,
        )
        h_full, rows_full = slice_distribution(
            _dist_samples([0]), trigger_idx=0, period_ms=25,
            t1_ms=0.0, t2_ms=0.0, channel_filter=set(CHANNEL_KEYS),
        )
        self.assertEqual(h_none, h_full)
        self.assertEqual(rows_none, rows_full)

    def test_swap_t1_gt_t2(self):
        samples = _dist_samples([0, 1, 2])
        _, a = slice_distribution(samples, 0, 25, 0.0,  50.0)
        _, b = slice_distribution(samples, 0, 25, 50.0, 0.0)
        self.assertEqual(a, b)

    def test_empty_outside_window(self):
        samples = _dist_samples([0, 1, 2])
        _, rows = slice_distribution(samples, 0, 25, 1000.0, 2000.0)
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
