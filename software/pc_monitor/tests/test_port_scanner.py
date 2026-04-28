"""Run from software/pc_monitor/: `python -m pytest tests/`

Tests for port_scanner. serial.Serial and list_ports are fully mocked —
no real hardware required.
"""
import json
import unittest
from unittest.mock import MagicMock, patch

from core.port_scanner import ScanResult, scan_ports, _probe_ade9000, _probe_dist


# ---------------------------------------------------------------------------
# Fake serial port
# ---------------------------------------------------------------------------

def _make_serial(replies: bytes):
    """Return a mock serial.Serial whose read() delivers `replies` once then b''."""
    s = MagicMock()
    s.__enter__ = lambda self: self
    s.__exit__ = MagicMock(return_value=False)
    s.reset_input_buffer = MagicMock()
    s.write = MagicMock()
    delivered = [False]
    def _read(n):
        if not delivered[0]:
            delivered[0] = True
            return replies
        return b""
    s.read.side_effect = _read
    return s


def _sync_reply() -> bytes:
    return json.dumps({"event": "sync", "seq": 1, "tick_ms": 12345}).encode() + b"\r\n"


def _telemetry_reply() -> bytes:
    return json.dumps({"ts": 1000, "mode": "delta"}).encode() + b"\r\n"


def _status_reply() -> bytes:
    return b"STATUS power=0 vbus=0 mode=CMD trig=0 capture=IDLE\r\n"


def _garbage_reply() -> bytes:
    return b"GARBAGE LINE\r\n"


# ---------------------------------------------------------------------------
# _probe_ade9000
# ---------------------------------------------------------------------------

class TestProbeAde9000(unittest.TestCase):
    def _run(self, replies: bytes) -> bool:
        with patch("core.port_scanner.serial.Serial", return_value=_make_serial(replies)):
            return _probe_ade9000("COM99", timeout=0.2)

    def test_telemetry_reply_returns_true(self):
        self.assertTrue(self._run(_telemetry_reply()))

    def test_sync_reply_returns_true(self):
        self.assertTrue(self._run(_sync_reply()))

    def test_garbage_returns_false(self):
        self.assertFalse(self._run(_garbage_reply()))

    def test_status_reply_returns_false(self):
        self.assertFalse(self._run(_status_reply()))

    def test_empty_reply_returns_false(self):
        self.assertFalse(self._run(b""))

    def test_serial_exception_returns_false(self):
        import serial as _serial
        with patch("core.port_scanner.serial.Serial",
                   side_effect=_serial.SerialException("access denied")):
            self.assertFalse(_probe_ade9000("COM99", timeout=0.1))

    def test_lf_terminated_reply(self):
        reply = json.dumps({"ts": 0, "mode": "delta"}).encode() + b"\n"
        self.assertTrue(self._run(reply))

    def test_multi_line_finds_telemetry(self):
        data = b"NOISE\r\n" + _telemetry_reply()
        self.assertTrue(self._run(data))

    def test_never_writes_to_port(self):
        """ADE9000 probe must be strictly listen-only."""
        mock_s = _make_serial(_telemetry_reply())
        with patch("core.port_scanner.serial.Serial", return_value=mock_s):
            _probe_ade9000("COM99", timeout=0.2)
        mock_s.write.assert_not_called()

    def test_never_writes_even_on_silence(self):
        """Even when nothing is received, no bytes are sent."""
        mock_s = _make_serial(b"")
        with patch("core.port_scanner.serial.Serial", return_value=mock_s):
            _probe_ade9000("COM99", timeout=0.1)
        mock_s.write.assert_not_called()


# ---------------------------------------------------------------------------
# _probe_dist
# ---------------------------------------------------------------------------

class TestProbeDist(unittest.TestCase):
    def _run(self, replies: bytes) -> bool:
        with patch("core.port_scanner.serial.Serial", return_value=_make_serial(replies)):
            return _probe_dist("COM99", timeout=0.2)

    def test_status_reply_returns_true(self):
        self.assertTrue(self._run(_status_reply()))

    def test_status_lowercase_returns_true(self):
        self.assertTrue(self._run(b"status power=1 vbus=0 mode=CMD trig=0 capture=IDLE\r\n"))

    def test_telemetry_returns_false(self):
        self.assertFalse(self._run(_telemetry_reply()))

    def test_garbage_returns_false(self):
        self.assertFalse(self._run(_garbage_reply()))

    def test_empty_returns_false(self):
        self.assertFalse(self._run(b""))

    def test_serial_exception_returns_false(self):
        import serial as _serial
        with patch("core.port_scanner.serial.Serial",
                   side_effect=_serial.SerialException("in use")):
            self.assertFalse(_probe_dist("COM99", timeout=0.1))

    def test_sends_status_command(self):
        mock_s = _make_serial(_status_reply())
        with patch("core.port_scanner.serial.Serial", return_value=mock_s):
            _probe_dist("COM99", timeout=0.2)
        mock_s.write.assert_called_once_with(b"STATUS\r\n")

    def test_pong_before_status_skipped(self):
        data = b"PONG\r\n" + _status_reply()
        self.assertTrue(self._run(data))


# ---------------------------------------------------------------------------
# ScanResult
# ---------------------------------------------------------------------------

class TestScanResult(unittest.TestCase):
    def test_complete_when_both_lists_non_empty(self):
        r = ScanResult(arduino_ports=["COM11"], dist_ports=["COM3"])
        self.assertTrue(r.complete)

    def test_incomplete_when_arduino_missing(self):
        r = ScanResult(arduino_ports=[], dist_ports=["COM3"])
        self.assertFalse(r.complete)

    def test_incomplete_when_dist_missing(self):
        r = ScanResult(arduino_ports=["COM11"], dist_ports=[])
        self.assertFalse(r.complete)

    def test_arduino_port_property_first(self):
        r = ScanResult(arduino_ports=["COM11", "COM12"], dist_ports=[])
        self.assertEqual(r.arduino_port, "COM11")

    def test_dist_port_property_first(self):
        r = ScanResult(arduino_ports=[], dist_ports=["COM3", "COM4"])
        self.assertEqual(r.dist_port, "COM3")

    def test_arduino_port_none_when_empty(self):
        self.assertIsNone(ScanResult().arduino_port)

    def test_dist_port_none_when_empty(self):
        self.assertIsNone(ScanResult().dist_port)


# ---------------------------------------------------------------------------
# scan_ports
# ---------------------------------------------------------------------------

class TestScanPorts(unittest.TestCase):
    def _mock_ports(self, *devices: str):
        ports = [MagicMock(device=d) for d in devices]
        return patch("core.port_scanner.serial.tools.list_ports.comports",
                     return_value=ports)

    def test_both_found(self):
        with self._mock_ports("COM11", "COM3"), \
             patch("core.port_scanner._probe_ade9000",
                   side_effect=lambda p, t: p == "COM11"), \
             patch("core.port_scanner._probe_dist",
                   side_effect=lambda p, t: p == "COM3"):
            result = scan_ports(timeout=0.1)

        self.assertIn("COM11", result.arduino_ports)
        self.assertIn("COM3",  result.dist_ports)
        self.assertTrue(result.complete)

    def test_only_arduino_found(self):
        with self._mock_ports("COM11", "COM3"), \
             patch("core.port_scanner._probe_ade9000",
                   side_effect=lambda p, t: p == "COM11"), \
             patch("core.port_scanner._probe_dist", return_value=False):
            result = scan_ports(timeout=0.1)

        self.assertEqual(result.arduino_ports, ["COM11"])
        self.assertEqual(result.dist_ports, [])
        self.assertFalse(result.complete)

    def test_only_dist_found(self):
        with self._mock_ports("COM11", "COM3"), \
             patch("core.port_scanner._probe_ade9000", return_value=False), \
             patch("core.port_scanner._probe_dist",
                   side_effect=lambda p, t: p == "COM3"):
            result = scan_ports(timeout=0.1)

        self.assertEqual(result.dist_ports, ["COM3"])
        self.assertIsNone(result.arduino_port)

    def test_no_ports_available(self):
        with self._mock_ports():
            result = scan_ports(timeout=0.1)
        self.assertEqual(result.arduino_ports, [])
        self.assertEqual(result.dist_ports, [])
        self.assertFalse(result.complete)

    def test_no_devices_found(self):
        with self._mock_ports("COM1", "COM2"), \
             patch("core.port_scanner._probe_ade9000", return_value=False), \
             patch("core.port_scanner._probe_dist",    return_value=False):
            result = scan_ports(timeout=0.1)
        self.assertFalse(result.complete)

    def test_same_port_not_in_both_lists(self):
        """A port claimed as ADE9000 must not be probed for Distribution."""
        dist_probed = []
        def fake_dist(port, timeout):
            dist_probed.append(port)
            return True

        with self._mock_ports("COM5"), \
             patch("core.port_scanner._probe_ade9000", return_value=True), \
             patch("core.port_scanner._probe_dist", side_effect=fake_dist):
            result = scan_ports(timeout=0.1)

        self.assertIn("COM5", result.arduino_ports)
        self.assertNotIn("COM5", dist_probed)
        self.assertEqual(result.dist_ports, [])

    def test_multiple_ports_of_same_type(self):
        with self._mock_ports("COM1", "COM2", "COM3"), \
             patch("core.port_scanner._probe_ade9000", return_value=True), \
             patch("core.port_scanner._probe_dist", return_value=False):
            result = scan_ports(timeout=0.1)

        self.assertEqual(sorted(result.arduino_ports), ["COM1", "COM2", "COM3"])

    def test_returns_scan_result_instance(self):
        with self._mock_ports(), \
             patch("core.port_scanner._probe_ade9000", return_value=False), \
             patch("core.port_scanner._probe_dist",    return_value=False):
            result = scan_ports(timeout=0.1)
        self.assertIsInstance(result, ScanResult)

    def test_results_are_sorted(self):
        with self._mock_ports("COM9", "COM1", "COM5"), \
             patch("core.port_scanner._probe_ade9000", return_value=True), \
             patch("core.port_scanner._probe_dist", return_value=False):
            result = scan_ports(timeout=0.1)
        self.assertEqual(result.arduino_ports, sorted(result.arduino_ports))


if __name__ == "__main__":
    unittest.main()
