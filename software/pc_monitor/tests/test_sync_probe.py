"""Run from software/pc_monitor/: `python -m pytest tests/`."""
import unittest

from core.sync_probe import SyncSample, compute_offset


def make_sample(seq: int, send_ns: int, rtt_ns: int, device_offset_ms: float = 0.0) -> SyncSample:
    """Synthesize a sample as if device millis() == PC monotonic ms + offset.
    tick_ms is taken at the midpoint of the RTT, with a fixed offset added."""
    recv_ns = send_ns + rtt_ns
    mid_ms  = (send_ns + recv_ns) / 2.0 / 1e6
    return SyncSample(
        seq     = seq,
        send_ns = send_ns,
        recv_ns = recv_ns,
        tick_ms = int(round(mid_ms + device_offset_ms)),
    )


class TestSyncSample(unittest.TestCase):
    def test_rtt_ms(self):
        s = SyncSample(seq=1, send_ns=1_000_000, recv_ns=3_000_000, tick_ms=100)
        self.assertAlmostEqual(s.rtt_ms, 2.0)

    def test_offset_ms_no_skew(self):
        s = make_sample(1, send_ns=1_000_000, rtt_ns=2_000_000, device_offset_ms=0.0)
        self.assertAlmostEqual(s.offset_ms, 0.0, places=3)

    def test_offset_ms_with_skew(self):
        s = make_sample(1, send_ns=10_000_000, rtt_ns=2_000_000, device_offset_ms=500.0)
        # Midpoint 11ms, tick_ms 511ms → offset 500ms.
        self.assertAlmostEqual(s.offset_ms, 500.0, places=3)


class TestComputeOffset(unittest.TestCase):
    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            compute_offset([])

    def test_clean_channel(self):
        samples = [
            make_sample(i, send_ns=i * 10_000_000, rtt_ns=1_000_000, device_offset_ms=250.0)
            for i in range(1, 11)
        ]
        r = compute_offset(samples, best_k=5)
        self.assertAlmostEqual(r.offset_ms, 250.0, places=0)
        self.assertEqual(r.n_samples, 10)
        self.assertEqual(r.n_used, 5)
        self.assertAlmostEqual(r.rtt_ms_best, 1.0, places=3)
        self.assertAlmostEqual(r.rtt_ms_median, 1.0, places=3)

    def test_rejects_outlier_rtts(self):
        # 8 clean (RTT=1ms) + 2 huge-RTT outliers; best_k=8 should pick clean ones.
        samples = [
            make_sample(i, send_ns=i * 10_000_000, rtt_ns=1_000_000, device_offset_ms=100.0)
            for i in range(1, 9)
        ]
        samples += [
            make_sample(9,  send_ns=90_000_000,  rtt_ns=50_000_000, device_offset_ms=100.0),
            make_sample(10, send_ns=100_000_000, rtt_ns=60_000_000, device_offset_ms=100.0),
        ]
        r = compute_offset(samples, best_k=8)
        self.assertEqual(r.n_used, 8)
        self.assertAlmostEqual(r.offset_ms, 100.0, places=0)
        self.assertAlmostEqual(r.rtt_ms_best, 1.0, places=3)
        # Median across all 10 (includes outliers at 50/60ms) is roughly 1.0
        # since 8 of 10 are 1ms.
        self.assertAlmostEqual(r.rtt_ms_median, 1.0, places=3)

    def test_best_k_larger_than_samples(self):
        samples = [make_sample(1, 0, 1_000_000, 42.0)]
        r = compute_offset(samples, best_k=8)
        self.assertEqual(r.n_used, 1)
        self.assertAlmostEqual(r.offset_ms, 42.0, places=0)


if __name__ == '__main__':
    unittest.main()
