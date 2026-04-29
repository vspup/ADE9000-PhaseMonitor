"""Run from software/pc_monitor/: `python -m pytest tests/test_serial_transport.py`

Covers SerialTransport configuration knobs (encoding, line terminator,
post-open flush hack, custom not-open exception class) and the reader
thread's line-splitting behaviour. The pyserial Serial class is replaced
by a _FakeSerial so no real port is required.
"""
import threading
import time
import unittest
from typing import List, Optional
from unittest import mock

from core import serial_transport
from core.serial_transport import SerialTransport


class _FakeSerial:
    """Minimal stand-in for serial.Serial. Drives the reader thread by
    yielding bytes from a scripted script."""

    def __init__(self, port: str, baudrate: int, **kwargs) -> None:
        self.port = port
        self.baudrate = baudrate
        self.kwargs = kwargs
        self.is_open = True
        self.writes: List[bytes] = []
        self.reset_input_buffer_calls = 0
        self._rx_chunks: List[bytes] = []
        self._lock = threading.Lock()

    def write(self, data: bytes) -> None:
        self.writes.append(bytes(data))

    def read(self, n: int) -> bytes:
        with self._lock:
            if not self._rx_chunks:
                return b""
            chunk = self._rx_chunks.pop(0)
            return chunk[:n]

    def reset_input_buffer(self) -> None:
        self.reset_input_buffer_calls += 1

    def close(self) -> None:
        self.is_open = False

    # -- test helpers --

    def feed(self, data: bytes) -> None:
        with self._lock:
            self._rx_chunks.append(data)


def _make_transport(**overrides) -> SerialTransport:
    kw = dict(
        encoding="utf-8",
        line_terminator=b"\n",
        post_open_flush=False,
        not_open_error_cls=RuntimeError,
    )
    kw.update(overrides)
    return SerialTransport(**kw)


def _drain_one_line(t: SerialTransport, fake: _FakeSerial,
                    payload: bytes, timeout: float = 1.0) -> Optional[str]:
    """Feed bytes; wait for the reader to surface one line on rx_queue."""
    fake.feed(payload)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return t.rx_queue.get(timeout=0.05)
        except Exception:
            continue
    return None


class TestSendLine(unittest.TestCase):
    def test_closed_raises_configured_error_class(self):
        class Boom(Exception):
            pass
        t = _make_transport(not_open_error_cls=Boom)
        with self.assertRaises(Boom):
            t.send_line("hello")

    def test_send_line_writes_with_lf(self):
        fake_holder: List[_FakeSerial] = []

        def factory(**kw):
            fs = _FakeSerial(**kw)
            fake_holder.append(fs)
            return fs

        t = _make_transport(encoding="utf-8", line_terminator=b"\n")
        with mock.patch.object(serial_transport.serial, "Serial", side_effect=factory):
            t.open("COM_FAKE", 115200)
            try:
                t.send_line("PING")
            finally:
                t.close()
        self.assertEqual(fake_holder[0].writes, [b"PING\n"])

    def test_send_line_writes_with_crlf_ascii(self):
        fake_holder: List[_FakeSerial] = []

        def factory(**kw):
            fs = _FakeSerial(**kw)
            fake_holder.append(fs)
            return fs

        t = _make_transport(encoding="ascii", line_terminator=b"\r\n")
        with mock.patch.object(serial_transport.serial, "Serial", side_effect=factory):
            t.open("COM_FAKE", 57600)
            try:
                t.send_line("STATUS")
            finally:
                t.close()
        self.assertEqual(fake_holder[0].writes, [b"STATUS\r\n"])

    def test_send_line_strips_trailing_newlines_before_appending(self):
        fake_holder: List[_FakeSerial] = []

        def factory(**kw):
            fs = _FakeSerial(**kw)
            fake_holder.append(fs)
            return fs

        t = _make_transport(line_terminator=b"\r\n")
        with mock.patch.object(serial_transport.serial, "Serial", side_effect=factory):
            t.open("COM_FAKE", 9600)
            try:
                t.send_line("CMD\r\n")
                t.send_line("CMD2\n")
            finally:
                t.close()
        self.assertEqual(fake_holder[0].writes, [b"CMD\r\n", b"CMD2\r\n"])

    def test_tx_preamble_prepended_to_payload(self):
        # Sacrificial preamble bytes go before the real command — used
        # by the Distribution RS-485 client to wake up the USB-RS485
        # adapter's auto-direction control.
        fake_holder: List[_FakeSerial] = []

        def factory(**kw):
            fs = _FakeSerial(**kw)
            fake_holder.append(fs)
            return fs

        t = _make_transport(
            encoding="ascii", line_terminator=b"\r\n", tx_preamble=b"\r\n"
        )
        with mock.patch.object(serial_transport.serial, "Serial", side_effect=factory):
            t.open("COM_FAKE", 57600)
            try:
                t.send_line("ARM")
                t.send_line("PING")
            finally:
                t.close()
        self.assertEqual(
            fake_holder[0].writes, [b"\r\nARM\r\n", b"\r\nPING\r\n"]
        )

    def test_default_tx_preamble_is_empty(self):
        # Without tx_preamble, the wire output is just the command and
        # terminator — preserves prior ADE9000-side behaviour.
        fake_holder: List[_FakeSerial] = []

        def factory(**kw):
            fs = _FakeSerial(**kw)
            fake_holder.append(fs)
            return fs

        t = _make_transport(encoding="utf-8", line_terminator=b"\n")
        with mock.patch.object(serial_transport.serial, "Serial", side_effect=factory):
            t.open("COM_FAKE", 115200)
            try:
                t.send_line("PING")
            finally:
                t.close()
        self.assertEqual(fake_holder[0].writes, [b"PING\n"])


class TestPostOpenFlush(unittest.TestCase):
    def test_flush_writes_newline_and_resets_input(self):
        fake_holder: List[_FakeSerial] = []

        def factory(**kw):
            fs = _FakeSerial(**kw)
            fake_holder.append(fs)
            return fs

        t = _make_transport(post_open_flush=True, line_terminator=b"\n")
        with mock.patch.object(serial_transport.serial, "Serial", side_effect=factory):
            t.open("COM_FAKE", 115200)
            try:
                pass
            finally:
                t.close()
        fs = fake_holder[0]
        self.assertEqual(fs.reset_input_buffer_calls, 1)
        # First write should be the bare \n flush.
        self.assertEqual(fs.writes[0], b"\n")

    def test_no_flush_when_disabled(self):
        fake_holder: List[_FakeSerial] = []

        def factory(**kw):
            fs = _FakeSerial(**kw)
            fake_holder.append(fs)
            return fs

        t = _make_transport(post_open_flush=False)
        with mock.patch.object(serial_transport.serial, "Serial", side_effect=factory):
            t.open("COM_FAKE", 57600)
            try:
                pass
            finally:
                t.close()
        fs = fake_holder[0]
        self.assertEqual(fs.reset_input_buffer_calls, 0)
        self.assertEqual(fs.writes, [])


class TestOpenClose(unittest.TestCase):
    def test_double_open_raises(self):
        def factory(**kw):
            return _FakeSerial(**kw)

        t = _make_transport()
        with mock.patch.object(serial_transport.serial, "Serial", side_effect=factory):
            t.open("COM_FAKE", 9600)
            try:
                with self.assertRaises(RuntimeError):
                    t.open("COM_FAKE", 9600)
            finally:
                t.close()

    def test_is_open_reflects_state(self):
        def factory(**kw):
            return _FakeSerial(**kw)

        t = _make_transport()
        self.assertFalse(t.is_open)
        with mock.patch.object(serial_transport.serial, "Serial", side_effect=factory):
            t.open("COM_FAKE", 9600)
            self.assertTrue(t.is_open)
            t.close()
        self.assertFalse(t.is_open)


class TestReaderLineSplit(unittest.TestCase):
    """The reader thread keeps the same first-of (\\r\\n / \\n / \\r) split
    that the original _Transport implementations used."""

    def _run_with_payload(self, payload: bytes, encoding: str = "utf-8") -> List[str]:
        fake_holder: List[_FakeSerial] = []

        def factory(**kw):
            fs = _FakeSerial(**kw)
            fake_holder.append(fs)
            return fs

        t = _make_transport(encoding=encoding)
        lines: List[str] = []
        with mock.patch.object(serial_transport.serial, "Serial", side_effect=factory):
            t.open("COM_FAKE", 115200)
            try:
                fake_holder[0].feed(payload)
                deadline = time.monotonic() + 1.0
                # Pull whatever has arrived within a short window.
                while time.monotonic() < deadline:
                    try:
                        lines.append(t.rx_queue.get(timeout=0.05))
                    except Exception:
                        if not fake_holder[0]._rx_chunks:
                            # No more data pending; small grace period
                            # for the reader to surface buffered lines.
                            time.sleep(0.05)
                            try:
                                lines.append(t.rx_queue.get_nowait())
                            except Exception:
                                break
            finally:
                t.close()
        return lines

    def test_crlf(self):
        lines = self._run_with_payload(b"PONG\r\nSTATUS power=1 vbus=0 mode=CMD trig=0\r\n")
        self.assertEqual(lines, ["PONG", "STATUS power=1 vbus=0 mode=CMD trig=0"])

    def test_lf_only(self):
        lines = self._run_with_payload(b'{"event":"wmode","wmode":"capture"}\n')
        self.assertEqual(lines, ['{"event":"wmode","wmode":"capture"}'])

    def test_cr_only(self):
        lines = self._run_with_payload(b"a\rb\rc\r")
        self.assertEqual(lines, ["a", "b", "c"])

    def test_empty_lines_are_dropped(self):
        lines = self._run_with_payload(b"PING\r\n\r\nPONG\r\n")
        self.assertEqual(lines, ["PING", "PONG"])

    def test_non_utf8_bytes_are_ignored(self):
        # 0xFF is invalid UTF-8 — must be silently dropped, not crash the reader.
        lines = self._run_with_payload(b"\xffOK\r\n")
        self.assertEqual(lines, ["OK"])


if __name__ == "__main__":
    unittest.main()
