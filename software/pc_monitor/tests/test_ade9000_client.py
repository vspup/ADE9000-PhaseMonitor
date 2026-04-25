"""Run from software/pc_monitor/: `python -m pytest tests/`

Covers Ade9000Protocol helpers and Ade9000Client high-level API.
All I/O replaced by _FakeTransport — no serial port required.
"""
import json
import queue
import time
import unittest
from typing import List

from core.ade9000_client import (
    Ade9000Client,
    Ade9000Error,
    Ade9000FirmwareError,
    Ade9000Protocol,
    Ade9000ProtocolError,
    Ade9000Timeout,
)
from core.capture_parser import CaptureDone, CaptureSample, CaptureStatus


# ---------------------------------------------------------------------------
# Fake transport
# ---------------------------------------------------------------------------

class _FakeTransport:
    """Same push-on-send pattern as in test_distribution_client.py."""

    def __init__(self) -> None:
        self.rx_queue: queue.Queue = queue.Queue()
        self.sent: List[str] = []
        self._batches: List[List[str]] = []
        self._open = True

    @property
    def is_open(self) -> bool:
        return self._open

    def send_line(self, line: str) -> None:
        self.sent.append(line)
        if self._batches:
            for r in self._batches.pop(0):
                self.rx_queue.put(r)

    def push_replies(self, *lines: str) -> None:
        self._batches.append(list(lines))


def _j(**kw) -> str:
    return json.dumps(kw)


# ---------------------------------------------------------------------------
# Ade9000Protocol — helpers
# ---------------------------------------------------------------------------

class TestAde9000Protocol(unittest.TestCase):
    def test_cmd_sync(self):
        self.assertEqual(Ade9000Protocol.cmd_sync(7), "SYNC 7")

    def test_cmd_cap_set(self):
        self.assertEqual(Ade9000Protocol.cmd_cap_set(100, 400), "CAP SET 100 400")

    def test_cmd_cap_arm_manual(self):
        self.assertEqual(Ade9000Protocol.cmd_cap_arm_manual(), "CAP ARM manual")

    def test_cmd_cap_arm_dip(self):
        self.assertEqual(Ade9000Protocol.cmd_cap_arm_dip(340.0), "CAP ARM dip 340.0")
        self.assertEqual(Ade9000Protocol.cmd_cap_arm_dip(196.5), "CAP ARM dip 196.5")

    def test_parse_json_valid(self):
        d = Ade9000Protocol.parse_json('{"event":"pong"}')
        self.assertEqual(d["event"], "pong")

    def test_parse_json_invalid(self):
        self.assertIsNone(Ade9000Protocol.parse_json("not json"))
        self.assertIsNone(Ade9000Protocol.parse_json(""))

    def test_is_telemetry(self):
        self.assertTrue(Ade9000Protocol.is_telemetry({"ts": 1234, "mode": "delta"}))
        self.assertFalse(Ade9000Protocol.is_telemetry({"event": "pong"}))

    def test_is_error(self):
        self.assertTrue(Ade9000Protocol.is_error({"status": "error", "reason": "not_armed"}))
        self.assertFalse(Ade9000Protocol.is_error({"status": "ok", "event": "pong"}))

    def test_error_reason(self):
        self.assertEqual(Ade9000Protocol.error_reason({"reason": "cap_busy"}), "cap_busy")
        self.assertEqual(Ade9000Protocol.error_reason({}), "unknown")


# ---------------------------------------------------------------------------
# Ade9000Client — set_wmode_capture
# ---------------------------------------------------------------------------

class TestSetWmodeCapture(unittest.TestCase):
    def test_success(self):
        t = _FakeTransport()
        t.push_replies(_j(status="ok", event="wmode", wmode="capture"))
        Ade9000Client(t).set_wmode_capture()
        self.assertEqual(t.sent[-1], "SET WMODE capture")

    def test_wrong_wmode_raises(self):
        t = _FakeTransport()
        t.push_replies(_j(status="ok", event="wmode", wmode="monitor"))
        with self.assertRaises(Ade9000ProtocolError):
            Ade9000Client(t).set_wmode_capture()

    def test_firmware_error_raises(self):
        t = _FakeTransport()
        t.push_replies(_j(status="error", reason="bad_wmode"))
        with self.assertRaises(Ade9000FirmwareError):
            Ade9000Client(t).set_wmode_capture()

    def test_timeout_raises(self):
        t = _FakeTransport()
        with self.assertRaises(Ade9000Timeout):
            Ade9000Client(t).set_wmode_capture(timeout=0.05)

    def test_telemetry_skipped_before_ack(self):
        t = _FakeTransport()
        t.push_replies(
            _j(ts=1000, mode="delta", uab=400.0, ubc=400.0, uca=400.0,
               f=50.0, state=1, flags=[]),
            _j(status="ok", event="wmode", wmode="capture"),
        )
        Ade9000Client(t).set_wmode_capture()


# ---------------------------------------------------------------------------
# Ade9000Client — sync_probe
# ---------------------------------------------------------------------------

class TestSyncProbe(unittest.TestCase):
    def _sync_reply(self, seq: int, tick_ms: int = 10000) -> str:
        return _j(status="ok", event="sync", seq=seq, tick_ms=tick_ms)

    def test_single_probe(self):
        t = _FakeTransport()
        t.push_replies(self._sync_reply(1))
        result = Ade9000Client(t).sync_probe(n=1, best_k=1)
        self.assertIsNotNone(result)
        self.assertEqual(result.n_samples, 1)
        self.assertEqual(result.n_used, 1)

    def test_multiple_probes(self):
        t = _FakeTransport()
        for i in range(3):
            t.push_replies(self._sync_reply(i + 1))
        result = Ade9000Client(t).sync_probe(n=3, best_k=2)
        self.assertEqual(result.n_samples, 3)
        self.assertEqual(result.n_used, 2)

    def test_seq_mismatch_skipped(self):
        t = _FakeTransport()
        # Reply has wrong seq (99) — probe 1 times out, probe 2 succeeds.
        t.push_replies(self._sync_reply(99))    # wrong seq for probe 1
        t.push_replies(self._sync_reply(2))     # correct for probe 2
        result = Ade9000Client(t).sync_probe(n=2, best_k=1, probe_timeout=0.05)
        self.assertEqual(result.n_samples, 1)

    def test_all_timeout_raises(self):
        t = _FakeTransport()
        with self.assertRaises(Ade9000Timeout):
            Ade9000Client(t).sync_probe(n=2, probe_timeout=0.05)

    def test_telemetry_skipped_during_probe(self):
        t = _FakeTransport()
        t.push_replies(
            _j(ts=1000, mode="delta", uab=400.0, ubc=400.0, uca=400.0,
               f=50.0, state=1, flags=[]),
            self._sync_reply(1),
        )
        result = Ade9000Client(t).sync_probe(n=1, best_k=1)
        self.assertEqual(result.n_samples, 1)


# ---------------------------------------------------------------------------
# Ade9000Client — cap_set
# ---------------------------------------------------------------------------

class TestCapSet(unittest.TestCase):
    def test_success(self):
        t = _FakeTransport()
        t.push_replies(_j(status="ok", event="cap_status",
                          state="IDLE", filled=0, pre=100, post=400, total=500))
        Ade9000Client(t).cap_set(100, 400)
        self.assertIn("CAP SET 100 400", t.sent)

    def test_firmware_error_raises(self):
        t = _FakeTransport()
        t.push_replies(_j(status="error", reason="bad_split"))
        with self.assertRaises(Ade9000FirmwareError):
            Ade9000Client(t).cap_set(300, 300)


# ---------------------------------------------------------------------------
# Ade9000Client — cap_arm_manual / cap_arm_dip
# ---------------------------------------------------------------------------

class TestCapArm(unittest.TestCase):
    def _armed_status(self) -> str:
        return _j(status="ok", event="cap_status",
                  state="ARMED", filled=0, pre=100, post=400, total=500)

    def test_arm_manual_success(self):
        t = _FakeTransport()
        t.push_replies(self._armed_status())
        Ade9000Client(t).cap_arm_manual()
        self.assertIn("CAP ARM manual", t.sent)

    def test_arm_manual_not_armed_raises(self):
        t = _FakeTransport()
        t.push_replies(_j(status="ok", event="cap_status",
                          state="IDLE", filled=0, pre=100, post=400, total=500))
        with self.assertRaises(Ade9000ProtocolError):
            Ade9000Client(t).cap_arm_manual()

    def test_arm_dip_success(self):
        t = _FakeTransport()
        t.push_replies(self._armed_status())
        Ade9000Client(t).cap_arm_dip(340.0)
        self.assertIn("CAP ARM dip 340.0", t.sent)

    def test_arm_dip_firmware_error_raises(self):
        t = _FakeTransport()
        t.push_replies(_j(status="error", reason="missing_threshold"))
        with self.assertRaises(Ade9000FirmwareError):
            Ade9000Client(t).cap_arm_dip(0.0)


# ---------------------------------------------------------------------------
# Ade9000Client — cap_trigger / cap_abort
# ---------------------------------------------------------------------------

class TestCapTriggerAbort(unittest.TestCase):
    def test_trigger_success(self):
        t = _FakeTransport()
        t.push_replies(_j(status="ok", event="cap_triggered"))
        Ade9000Client(t).cap_trigger()
        self.assertIn("CAP TRIGGER", t.sent)

    def test_trigger_firmware_error_raises(self):
        t = _FakeTransport()
        t.push_replies(_j(status="error", reason="not_armed"))
        with self.assertRaises(Ade9000FirmwareError):
            Ade9000Client(t).cap_trigger()

    def test_abort_success(self):
        t = _FakeTransport()
        t.push_replies(_j(status="ok", event="cap_aborted"))
        Ade9000Client(t).cap_abort()
        self.assertIn("CAP ABORT", t.sent)


# ---------------------------------------------------------------------------
# Ade9000Client — cap_status
# ---------------------------------------------------------------------------

class TestCapStatus(unittest.TestCase):
    def test_armed(self):
        t = _FakeTransport()
        t.push_replies(_j(status="ok", event="cap_status",
                          state="ARMED", filled=47, pre=100, post=400,
                          total=500, tick_ms=12345))
        cs = Ade9000Client(t).cap_status()
        self.assertIsInstance(cs, CaptureStatus)
        self.assertEqual(cs.state, "ARMED")
        self.assertEqual(cs.filled, 47)
        self.assertEqual(cs.pre, 100)
        self.assertEqual(cs.post, 400)
        self.assertEqual(cs.total, 500)
        self.assertEqual(cs.tick_ms, 12345)

    def test_firmware_error_raises(self):
        t = _FakeTransport()
        t.push_replies(_j(status="error", reason="not_in_capture_mode"))
        with self.assertRaises(Ade9000FirmwareError):
            Ade9000Client(t).cap_status()

    def test_wrong_event_raises(self):
        t = _FakeTransport()
        t.push_replies(_j(status="ok", event="pong"))
        with self.assertRaises(Ade9000ProtocolError):
            Ade9000Client(t).cap_status()


# ---------------------------------------------------------------------------
# Ade9000Client — cap_read
# ---------------------------------------------------------------------------

class TestCapRead(unittest.TestCase):
    @staticmethod
    def _sample(i: int) -> str:
        return _j(event="cap_sample", i=i,
                  uab=400.0, ubc=400.0, uca=400.0,
                  ia=1.0, ib=1.0, ic=1.0)

    @staticmethod
    def _done(n: int, trigger_tick_ms: int = 42000) -> str:
        return _j(status="ok", event="cap_done", n=n,
                  trigger_tick_ms=trigger_tick_ms, sample_period_ms=10,
                  pre=100, post=200, trigger_index=0)

    def test_reads_n_samples(self):
        n = 5
        t = _FakeTransport()
        batch = [self._sample(i - 2) for i in range(n)]
        batch.append(self._done(n))
        t.push_replies(*batch)
        samples, done = Ade9000Client(t).cap_read()
        self.assertEqual(len(samples), n)
        self.assertEqual(done.n, n)
        self.assertEqual(done.trigger_tick_ms, 42000)
        self.assertEqual(done.sample_period_ms, 10)
        self.assertIsInstance(samples[0], CaptureSample)

    def test_sample_index_range(self):
        pre, post = 2, 3
        n = pre + post
        t = _FakeTransport()
        batch = [self._sample(i - pre) for i in range(n)]
        batch.append(self._done(n))
        t.push_replies(*batch)
        samples, _ = Ade9000Client(t).cap_read()
        indices = [s.i for s in samples]
        self.assertEqual(indices, list(range(-pre, post)))

    def test_count_mismatch_raises(self):
        t = _FakeTransport()
        t.push_replies(
            self._sample(0),
            self._done(5),   # claims 5 but only 1 sample sent
        )
        with self.assertRaises(Ade9000ProtocolError):
            Ade9000Client(t).cap_read()

    def test_non_capture_lines_skipped(self):
        n = 2
        t = _FakeTransport()
        t.push_replies(
            _j(ts=9000, mode="delta", uab=400.0, ubc=400.0, uca=400.0,
               f=50.0, state=1, flags=[]),   # stray telemetry — skip
            self._sample(-1),
            _j(status="ok", event="cap_status",
               state="CAPTURING", filled=10, pre=100, post=200, total=300),
            self._sample(0),
            self._done(n),
        )
        samples, done = Ade9000Client(t).cap_read()
        self.assertEqual(len(samples), n)
        self.assertEqual(done.n, n)

    def test_timeout_raises(self):
        t = _FakeTransport()
        t.push_replies(self._sample(0))   # no done line
        with self.assertRaises(Ade9000Timeout):
            Ade9000Client(t).cap_read(timeout=0.1)


if __name__ == "__main__":
    unittest.main()
