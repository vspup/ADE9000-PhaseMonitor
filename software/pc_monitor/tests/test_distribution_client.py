"""Run from software/pc_monitor/: `python -m pytest tests/`

Covers DistributionProtocol parsers and DistributionClient high-level API.
All I/O is replaced by _FakeTransport — no serial port required.
"""
import queue
import unittest
from typing import List

from core.distribution_client import (
    DistCapStatus,
    DistributionClient,
    DistributionError,
    DistributionProtocol,
    DistributionTimeout,
    StartAlreadyOnError,
    VbusBlockError,
)
from core.sync_probe import SyncResult


# ---------------------------------------------------------------------------
# Fake transport
# ---------------------------------------------------------------------------

class _FakeTransport:
    """Stub for SerialTransport. Each push_replies() call registers one batch
    of lines to be delivered into rx_queue on the next send_line() call.
    This matches the real flow: _drain() clears stale data, send_line()
    triggers the device reply, _recv() reads from rx_queue."""

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


# ---------------------------------------------------------------------------
# DistributionProtocol — parse_status
# ---------------------------------------------------------------------------

class TestParseStatus(unittest.TestCase):
    def test_valid(self):
        d = DistributionProtocol.parse_status(
            "STATUS power=1 vbus=1 mode=CMD trig=12345"
        )
        self.assertEqual(d["power"], 1)
        self.assertEqual(d["vbus"], 1)
        self.assertEqual(d["mode"], "CMD")
        self.assertEqual(d["trig"], 12345)

    def test_mode_uppercased(self):
        d = DistributionProtocol.parse_status(
            "STATUS power=0 vbus=0 mode=stream trig=0"
        )
        self.assertEqual(d["mode"], "STREAM")

    def test_missing_field_returns_none(self):
        self.assertIsNone(
            DistributionProtocol.parse_status("STATUS power=0 vbus=0")
        )

    def test_garbage_returns_none(self):
        self.assertIsNone(DistributionProtocol.parse_status("PONG"))
        self.assertIsNone(DistributionProtocol.parse_status(""))


# ---------------------------------------------------------------------------
# DistributionProtocol — parse_cap_status
# ---------------------------------------------------------------------------

class TestParseCapStatus(unittest.TestCase):
    _BASE = (
        "CAP STATUS state={state} samples=50 trigger_idx=0 "
        "sample_period_ms=25 channels=8 trigger_tick=99000"
    )

    def test_all_states(self):
        for state in ("IDLE", "ARMED", "CAPTURING", "READY", "ERROR"):
            d = DistributionProtocol.parse_cap_status(self._BASE.format(state=state))
            self.assertIsNotNone(d, state)
            self.assertEqual(d["state"], state)

    def test_all_fields(self):
        d = DistributionProtocol.parse_cap_status(
            "CAP STATUS state=READY samples=300 trigger_idx=50 "
            "sample_period_ms=25 channels=8 trigger_tick=123456"
        )
        self.assertEqual(d["samples"], 300)
        self.assertEqual(d["trigger_idx"], 50)
        self.assertEqual(d["sample_period_ms"], 25)
        self.assertEqual(d["channels"], 8)
        self.assertEqual(d["trigger_tick"], 123456)

    def test_negative_trigger_idx(self):
        d = DistributionProtocol.parse_cap_status(
            "CAP STATUS state=IDLE samples=0 trigger_idx=-1 "
            "sample_period_ms=25 channels=8 trigger_tick=0"
        )
        self.assertEqual(d["trigger_idx"], -1)

    def test_state_lowercased_in_reply(self):
        d = DistributionProtocol.parse_cap_status(
            "CAP STATUS state=ready samples=300 trigger_idx=50 "
            "sample_period_ms=25 channels=8 trigger_tick=1"
        )
        self.assertEqual(d["state"], "READY")

    def test_missing_trigger_tick_defaults_to_zero(self):
        # FW s_cap_tx[96] truncates the line before "trigger_tick=<val>";
        # parser must accept it and default trigger_tick to 0.
        d = DistributionProtocol.parse_cap_status(
            "CAP STATUS state=IDLE samples=0 trigger_idx=0 "
            "sample_period_ms=25 channels=8"
        )
        self.assertIsNotNone(d)
        self.assertEqual(d["trigger_tick"], 0)

    def test_corrupted_prefix_parses(self):
        # "CAP STATUS" may be garbled by RS-485 echo; fields that follow are intact.
        d = DistributionProtocol.parse_cap_status(
            "PT*UTUS state=CAPTURING samples=197 trigger_idx=4 "
            "sample_period_ms=25 channels=8 trigger_tick=984399"
        )
        self.assertIsNotNone(d)
        self.assertEqual(d["state"], "CAPTURING")
        self.assertEqual(d["samples"], 197)
        self.assertEqual(d["trigger_tick"], 984399)

    def test_garbage_returns_none(self):
        self.assertIsNone(DistributionProtocol.parse_cap_status("PONG"))
        self.assertIsNone(DistributionProtocol.parse_cap_status(""))


# ---------------------------------------------------------------------------
# DistributionProtocol — parse_cap_sample
# ---------------------------------------------------------------------------

class TestParseCapSample(unittest.TestCase):
    @staticmethod
    def _line(idx: int, values: List[int]) -> str:
        hexes = [f"{v & 0xFFFF:04X}" for v in values]
        return f"{idx} " + " ".join(hexes)

    def test_all_zero(self):
        idx, ints, hexes = DistributionProtocol.parse_cap_sample(self._line(0, [0] * 8))
        self.assertEqual(idx, 0)
        self.assertEqual(ints, [0] * 8)
        self.assertEqual(hexes, ["0000"] * 8)

    def test_positive_values(self):
        vals = [100, 200, 300, 400, 500, 600, 700, 800]
        idx, ints, _ = DistributionProtocol.parse_cap_sample(self._line(5, vals))
        self.assertEqual(idx, 5)
        self.assertEqual(ints, vals)

    def test_negative_value_sign_extended(self):
        _, ints, hexes = DistributionProtocol.parse_cap_sample(self._line(0, [-1] * 8))
        self.assertEqual(ints, [-1] * 8)
        self.assertEqual(hexes, ["FFFF"] * 8)

    def test_max_positive_int16(self):
        _, ints, _ = DistributionProtocol.parse_cap_sample(self._line(0, [32767] * 8))
        self.assertEqual(ints, [32767] * 8)

    def test_min_negative_int16(self):
        _, ints, _ = DistributionProtocol.parse_cap_sample(self._line(0, [-32768] * 8))
        self.assertEqual(ints, [-32768] * 8)

    def test_hex_output_uppercased(self):
        _, _, hexes = DistributionProtocol.parse_cap_sample(
            "1 00ab 00CD 0000 0000 0000 0000 0000 0000"
        )
        self.assertEqual(hexes[0], "00AB")
        self.assertEqual(hexes[1], "00CD")

    def test_too_few_channels_returns_none(self):
        self.assertIsNone(
            DistributionProtocol.parse_cap_sample("0 0000 0000 0000")
        )

    def test_no_idx_returns_none(self):
        self.assertIsNone(DistributionProtocol.parse_cap_sample(
            "0000 0000 0000 0000 0000 0000 0000 0000"
        ))

    def test_garbage_returns_none(self):
        self.assertIsNone(DistributionProtocol.parse_cap_sample(""))
        self.assertIsNone(DistributionProtocol.parse_cap_sample("PONG"))


# ---------------------------------------------------------------------------
# DistributionProtocol — parse_cap_done
# ---------------------------------------------------------------------------

class TestParseCapDone(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(
            DistributionProtocol.parse_cap_done("CAP READ done count=300"), 300
        )

    def test_case_insensitive(self):
        self.assertEqual(
            DistributionProtocol.parse_cap_done("cap read done count=42"), 42
        )

    def test_zero_count(self):
        self.assertEqual(
            DistributionProtocol.parse_cap_done("CAP READ done count=0"), 0
        )

    def test_no_match_returns_none(self):
        self.assertIsNone(DistributionProtocol.parse_cap_done("PONG"))
        self.assertIsNone(DistributionProtocol.parse_cap_done(""))


# ---------------------------------------------------------------------------
# DistributionProtocol — parse_evt
# ---------------------------------------------------------------------------

class TestParseEvt(unittest.TestCase):
    def test_vbus_block(self):
        self.assertEqual(DistributionProtocol.parse_evt("EVT: vbus_block"), "vbus_block")

    def test_unknown_body_still_returned(self):
        self.assertEqual(
            DistributionProtocol.parse_evt("EVT: something_new"), "something_new"
        )

    def test_case_insensitive_prefix(self):
        self.assertEqual(DistributionProtocol.parse_evt("evt: vbus_block"), "vbus_block")

    def test_non_evt_returns_none(self):
        self.assertIsNone(DistributionProtocol.parse_evt("PONG"))
        self.assertIsNone(DistributionProtocol.parse_evt(
            "STATUS power=0 vbus=0 mode=CMD trig=0"
        ))
        self.assertIsNone(DistributionProtocol.parse_evt(""))


# ---------------------------------------------------------------------------
# DistributionProtocol — cmd_cap_read
# ---------------------------------------------------------------------------

class TestCmdCapRead(unittest.TestCase):
    def test_format(self):
        self.assertEqual(DistributionProtocol.cmd_cap_read(0, 300), "CAP READ 0 300")
        self.assertEqual(DistributionProtocol.cmd_cap_read(100, 50), "CAP READ 100 50")


# ---------------------------------------------------------------------------
# DistributionProtocol — parse_sync
# ---------------------------------------------------------------------------

class TestParseSync(unittest.TestCase):
    def test_clean_reply(self):
        self.assertEqual(
            DistributionProtocol.parse_sync("SYNC ok seq=7 tick=123456"),
            (7, 123456),
        )

    def test_case_insensitive(self):
        self.assertEqual(
            DistributionProtocol.parse_sync("sync ok SEQ=1 TICK=42"),
            (1, 42),
        )

    def test_corrupted_prefix(self):
        # RS-485 half-duplex switching can mangle the first bytes; the
        # seq/tick fields survive and are what the parser keys on.
        self.assertEqual(
            DistributionProtocol.parse_sync("xX@k seq=12 tick=99000"),
            (12, 99000),
        )

    def test_zero_values(self):
        self.assertEqual(
            DistributionProtocol.parse_sync("SYNC ok seq=0 tick=0"),
            (0, 0),
        )

    def test_missing_tick_returns_none(self):
        self.assertIsNone(DistributionProtocol.parse_sync("SYNC ok seq=1"))

    def test_garbage_returns_none(self):
        self.assertIsNone(DistributionProtocol.parse_sync("PONG"))
        self.assertIsNone(DistributionProtocol.parse_sync(""))
        self.assertIsNone(DistributionProtocol.parse_sync("SYNC err_range"))


# ---------------------------------------------------------------------------
# DistributionProtocol — cmd_sync
# ---------------------------------------------------------------------------

class TestCmdSync(unittest.TestCase):
    def test_format(self):
        self.assertEqual(DistributionProtocol.cmd_sync(0),  "SYNC 0")
        self.assertEqual(DistributionProtocol.cmd_sync(42), "SYNC 42")


# ---------------------------------------------------------------------------
# DistributionClient — ping
# ---------------------------------------------------------------------------

class TestPing(unittest.TestCase):
    def test_success_returns_nonnegative_rtt(self):
        t = _FakeTransport()
        t.push_replies("PONG")
        rtt = DistributionClient(t).ping(timeout=1.0)
        self.assertGreaterEqual(rtt, 0.0)
        self.assertEqual(t.sent[-1], "PING")

    def test_wrong_reply_raises(self):
        t = _FakeTransport()
        t.push_replies("GARBLED")
        with self.assertRaises(DistributionError):
            DistributionClient(t).ping(timeout=1.0)

    def test_timeout_raises(self):
        t = _FakeTransport()
        with self.assertRaises(DistributionTimeout):
            DistributionClient(t).ping(timeout=0.05)

    def test_timeout_message_lists_skipped_lines(self):
        # When PING times out, the error must include every non-EVT line
        # that arrived during the wait — needed to tell "FW silent" apart
        # from "FW replied with garbage".
        t = _FakeTransport()
        t.push_replies("tCtCtCERR unknown")
        with self.assertRaises(DistributionTimeout) as cm:
            DistributionClient(t).ping(timeout=0.05)
        self.assertIn("skipped=", str(cm.exception))
        self.assertIn("tCtCtCERR unknown", str(cm.exception))


class TestSyncProbe(unittest.TestCase):
    @staticmethod
    def _reply(seq: int, tick: int = 1000) -> str:
        return f"SYNC ok seq={seq} tick={tick + seq}"

    def test_returns_sync_result(self):
        t = _FakeTransport()
        for seq in range(1, 4):
            t.push_replies(self._reply(seq))
        result = DistributionClient(t).sync_probe(n=3, best_k=2, probe_timeout=1.0)
        self.assertIsInstance(result, SyncResult)
        self.assertEqual(result.n_samples, 3)
        self.assertEqual(result.n_used,    2)

    def test_command_format(self):
        t = _FakeTransport()
        for seq in range(1, 4):
            t.push_replies(self._reply(seq))
        DistributionClient(t).sync_probe(n=3, probe_timeout=1.0)
        self.assertEqual(t.sent, ["SYNC 1", "SYNC 2", "SYNC 3"])

    def test_evt_lines_sidelined(self):
        t = _FakeTransport()
        t.push_replies("EVT: vbus_block tick=100", self._reply(1))
        t.push_replies(self._reply(2))
        client = DistributionClient(t)
        client.sync_probe(n=2, probe_timeout=1.0)
        evts = client.take_events()
        self.assertEqual(len(evts), 1)
        self.assertIn("vbus_block", evts[0])

    def test_stale_seq_skipped(self):
        # Reply seq doesn't match — probe scans past it until the deadline.
        # With only one stale reply per probe the result list is empty.
        t = _FakeTransport()
        t.push_replies("SYNC ok seq=99 tick=1234")   # wrong seq for probe 1
        t.push_replies("SYNC ok seq=99 tick=1235")   # wrong seq for probe 2
        with self.assertRaises(DistributionTimeout):
            DistributionClient(t).sync_probe(n=2, probe_timeout=0.05)

    def test_garbled_reply_skipped(self):
        # A line that doesn't carry seq=/tick= is dropped; probe times out
        # and contributes no sample, so all-garble → DistributionTimeout.
        t = _FakeTransport()
        t.push_replies("PSk")
        t.push_replies("PSk")
        with self.assertRaises(DistributionTimeout):
            DistributionClient(t).sync_probe(n=2, probe_timeout=0.05)

    def test_all_timeout_raises(self):
        t = _FakeTransport()
        with self.assertRaises(DistributionTimeout):
            DistributionClient(t).sync_probe(n=2, probe_timeout=0.05)

    def test_corrupted_prefix_still_parses(self):
        t = _FakeTransport()
        t.push_replies("xX@k seq=1 tick=1001")
        result = DistributionClient(t).sync_probe(n=1, probe_timeout=1.0)
        self.assertEqual(result.n_samples, 1)


# ---------------------------------------------------------------------------
# DistributionClient — mode_cmd
# ---------------------------------------------------------------------------

class TestModeCmd(unittest.TestCase):
    def test_success(self):
        t = _FakeTransport()
        t.push_replies("MODE CMD ok")
        DistributionClient(t).mode_cmd()
        self.assertEqual(t.sent[-1], "MODE CMD")

    def test_recovers_garbled_prefix(self):
        # Wire form actually observed in the field — leading bytes of
        # "MODE CMD ok" mangled by RS-485 TX→RX switching, " ok" intact.
        t = _FakeTransport()
        t.push_replies("=\x11\x15\x1a5D ok")
        DistributionClient(t).mode_cmd()

    def test_skips_evt_lines(self):
        t = _FakeTransport()
        t.push_replies("EVT: vbus_block tick=1234", "MODE CMD ok")
        client = DistributionClient(t)
        client.mode_cmd()
        self.assertEqual(client.take_events(), ["EVT: vbus_block tick=1234"])

    def test_garbled_reply_times_out(self):
        # With Fix B both attempts must fail to reach the timeout —
        # push the destroyed reply twice.
        t = _FakeTransport()
        t.push_replies("PSk")
        t.push_replies("PSk")
        with self.assertRaises(DistributionError):
            DistributionClient(t).mode_cmd(timeout=0.05)


# ---------------------------------------------------------------------------
# DistributionClient — status / arm / start
# ---------------------------------------------------------------------------

class TestStatus(unittest.TestCase):
    def test_success(self):
        t = _FakeTransport()
        t.push_replies("STATUS power=1 vbus=1 mode=CMD trig=9999")
        d = DistributionClient(t).status()
        self.assertEqual(d["power"], 1)
        self.assertEqual(d["mode"], "CMD")
        self.assertEqual(d["trig"], 9999)

    def test_bad_reply_raises(self):
        t = _FakeTransport()
        t.push_replies("ERR: unknown command")
        with self.assertRaises(DistributionError):
            DistributionClient(t).status()


class TestArm(unittest.TestCase):
    def test_success(self):
        t = _FakeTransport()
        t.push_replies("ARM ok")
        DistributionClient(t).arm()
        self.assertEqual(t.sent[-1], "ARM")

    def test_recovers_garbled_prefix(self):
        # Real wire form observed when RS-485 TX→RX adapter mangled the
        # leading bytes of "ARM ok" but the trailing " ok" survived.
        t = _FakeTransport()
        t.push_replies("=\x11\x15\x1a5D ok")
        DistributionClient(t).arm()

    def test_skips_evt_lines(self):
        # An EVT line arriving between the command and the OK is sidelined
        # into the event buffer, not treated as the ack.
        t = _FakeTransport()
        t.push_replies("EVT: vbus_block tick=1234", "ARM ok")
        client = DistributionClient(t)
        client.arm()
        self.assertEqual(client.take_events(), ["EVT: vbus_block tick=1234"])

    def test_garbled_reply_times_out(self):
        # Fully destroyed reply (e.g. "PSk" — neither " OK" nor "ERROR" tail)
        # is honestly reported as a timeout instead of a false error.
        # DistributionTimeout is-a DistributionError, so old expectations hold.
        # With Fix B, the client also retries once before raising — both
        # attempts have to fail to reach this code path.
        t = _FakeTransport()
        t.push_replies("PSk")  # attempt 1
        t.push_replies("PSk")  # attempt 2
        with self.assertRaises(DistributionError):
            DistributionClient(t).arm(timeout=0.05)

    def test_retries_once_on_garbled_first_reply(self):
        # First reply is destroyed garble; client retries the command
        # automatically. If the retry arrives clean, arm() succeeds and
        # no exception propagates.
        t = _FakeTransport()
        t.push_replies("PSk")     # attempt 1: garbled, no " OK" tail
        t.push_replies("ARM ok")  # attempt 2: clean
        DistributionClient(t).arm(timeout=0.05)
        # Both attempts went out on the wire.
        self.assertEqual(t.sent.count("ARM"), 2)

    def test_timeout_message_lists_both_attempts(self):
        # When both attempts fail, the timeout message must surface skipped
        # lines from each — the first hint at a wire problem could be in
        # either attempt's tail.
        t = _FakeTransport()
        t.push_replies("PSk")            # attempt 1
        t.push_replies("ERR unknown")    # attempt 2
        with self.assertRaises(DistributionTimeout) as cm:
            DistributionClient(t).arm(timeout=0.05)
        msg = str(cm.exception)
        self.assertIn("attempt1 skipped=", msg)
        self.assertIn("attempt2 skipped=", msg)
        self.assertIn("PSk", msg)
        self.assertIn("ERR unknown", msg)


class TestCapAbort(unittest.TestCase):
    def test_success(self):
        t = _FakeTransport()
        t.push_replies("CAP ABORT ok")
        DistributionClient(t).cap_abort()
        self.assertEqual(t.sent[-1], "CAP ABORT")

    def test_recovers_garbled_prefix(self):
        # Same RS-485 garble pattern as ARM/MODE CMD: leading bytes mangled,
        # trailing " OK" survives and is what the client keys on.
        t = _FakeTransport()
        t.push_replies("=\x11\x15\x1a5D ok")
        DistributionClient(t).cap_abort()

    def test_skips_evt_lines(self):
        t = _FakeTransport()
        t.push_replies("EVT: vbus_block tick=1234", "CAP ABORT ok")
        client = DistributionClient(t)
        client.cap_abort()
        self.assertEqual(client.take_events(), ["EVT: vbus_block tick=1234"])

    def test_retries_once_on_garbled_first_reply(self):
        t = _FakeTransport()
        t.push_replies("PSk")            # attempt 1: destroyed
        t.push_replies("CAP ABORT ok")   # attempt 2: clean
        DistributionClient(t).cap_abort(timeout=0.05)
        self.assertEqual(t.sent.count("CAP ABORT"), 2)

    def test_garbled_reply_times_out(self):
        t = _FakeTransport()
        t.push_replies("PSk")
        t.push_replies("PSk")
        with self.assertRaises(DistributionError):
            DistributionClient(t).cap_abort(timeout=0.05)


class TestStart(unittest.TestCase):
    def test_success(self):
        t = _FakeTransport()
        t.push_replies("START ok")
        DistributionClient(t).start()

    def test_vbus_error(self):
        t = _FakeTransport()
        t.push_replies("START vbus_error")
        with self.assertRaises(VbusBlockError):
            DistributionClient(t).start()

    def test_already_on(self):
        t = _FakeTransport()
        t.push_replies("START already_on")
        with self.assertRaises(StartAlreadyOnError):
            DistributionClient(t).start()

    def test_generic_error(self):
        t = _FakeTransport()
        t.push_replies("START error")
        with self.assertRaises(DistributionError):
            DistributionClient(t).start()


# ---------------------------------------------------------------------------
# DistributionClient — cap_status
# ---------------------------------------------------------------------------

class TestCapStatus(unittest.TestCase):
    def test_ready(self):
        t = _FakeTransport()
        t.push_replies(
            "CAP STATUS state=READY samples=300 trigger_idx=50 "
            "sample_period_ms=25 channels=8 trigger_tick=88000"
        )
        cs = DistributionClient(t).cap_status()
        self.assertIsInstance(cs, DistCapStatus)
        self.assertEqual(cs.state, "READY")
        self.assertEqual(cs.samples, 300)
        self.assertEqual(cs.trigger_idx, 50)
        self.assertEqual(cs.sample_period_ms, 25)
        self.assertEqual(cs.channels, 8)
        self.assertEqual(cs.trigger_tick, 88000)

    def test_bad_reply_raises(self):
        # Unrecognized reply is skipped by the scan loop; timeout fires instead.
        # DistributionTimeout is-a DistributionError, so assertRaises still passes.
        t = _FakeTransport()
        t.push_replies("ERR: not ready")
        with self.assertRaises(DistributionError):
            DistributionClient(t).cap_status(timeout=0.05)

    def test_corrupted_prefix_still_parses(self):
        # RS-485 half-duplex switching can corrupt the first bytes of the reply,
        # e.g. "CAP STATUS" → "PT*UTUS".  Fields after the prefix are intact.
        t = _FakeTransport()
        t.push_replies(
            "PT*UTUS state=CAPTURING samples=197 trigger_idx=4 "
            "sample_period_ms=25 channels=8 trigger_tick=984399"
        )
        cs = DistributionClient(t).cap_status()
        self.assertEqual(cs.state, "CAPTURING")
        self.assertEqual(cs.samples, 197)
        self.assertEqual(cs.trigger_idx, 4)
        self.assertEqual(cs.trigger_tick, 984399)

    def test_echo_line_skipped(self):
        # RS-485 adapter can echo the sent command as a separate line before
        # the actual reply.  The scan loop must skip it.
        t = _FakeTransport()
        t.push_replies(
            "CAP STATUS",   # echo: no state/samples fields → not parseable
            "CAP STATUS state=READY samples=300 trigger_idx=50 "
            "sample_period_ms=25 channels=8 trigger_tick=1",
        )
        cs = DistributionClient(t).cap_status()
        self.assertEqual(cs.state, "READY")


# ---------------------------------------------------------------------------
# DistributionClient — cap_read
# ---------------------------------------------------------------------------

class TestCapRead(unittest.TestCase):
    @staticmethod
    def _sample_line(idx: int) -> str:
        hexes = [f"{(idx + ch) & 0xFFFF:04X}" for ch in range(8)]
        return f"{idx} " + " ".join(hexes)

    def test_reads_n_samples(self):
        n = 5
        t = _FakeTransport()
        batch = [self._sample_line(i) for i in range(n)]
        batch.append(f"CAP READ done count={n}")
        t.push_replies(*batch)
        samples = DistributionClient(t).cap_read(0, n)
        self.assertEqual(len(samples), n)
        for i, (idx, ints, hexes) in enumerate(samples):
            self.assertEqual(idx, i)
            self.assertEqual(len(ints), 8)
            self.assertEqual(len(hexes), 8)

    def test_command_format(self):
        n = 3
        t = _FakeTransport()
        batch = [self._sample_line(i) for i in range(n)]
        batch.append(f"CAP READ done count={n}")
        t.push_replies(*batch)
        DistributionClient(t).cap_read(10, n)
        self.assertIn("CAP READ 10 3", t.sent)

    def test_count_mismatch_raises(self):
        t = _FakeTransport()
        t.push_replies(
            self._sample_line(0),
            "CAP READ done count=5",
        )
        with self.assertRaises(DistributionError):
            DistributionClient(t).cap_read(0, 5)

    def test_evt_sidelined_not_counted(self):
        n = 2
        t = _FakeTransport()
        t.push_replies(
            self._sample_line(0),
            "EVT: vbus_block",
            self._sample_line(1),
            f"CAP READ done count={n}",
        )
        client = DistributionClient(t)
        samples = client.cap_read(0, n)
        self.assertEqual(len(samples), n)
        evts = client.take_events()
        self.assertEqual(len(evts), 1)
        self.assertIn("vbus_block", evts[0])

    def test_timeout_raises(self):
        t = _FakeTransport()
        t.push_replies(self._sample_line(0))   # no done line
        with self.assertRaises(DistributionTimeout):
            DistributionClient(t).cap_read(0, 300, timeout=0.1)


# ---------------------------------------------------------------------------
# DistributionClient — take_events
# ---------------------------------------------------------------------------

class TestTakeEvents(unittest.TestCase):
    def test_evt_accumulated_during_recv(self):
        t = _FakeTransport()
        t.push_replies("EVT: something", "PONG")
        client = DistributionClient(t)
        client.ping(timeout=1.0)
        evts = client.take_events()
        self.assertEqual(len(evts), 1)
        self.assertIn("something", evts[0])

    def test_take_events_clears_buffer(self):
        t = _FakeTransport()
        t.push_replies("EVT: x", "PONG")
        client = DistributionClient(t)
        client.ping(timeout=1.0)
        client.take_events()
        self.assertEqual(client.take_events(), [])

    def test_multiple_evts_accumulated(self):
        t = _FakeTransport()
        t.push_replies("EVT: first", "EVT: second", "PONG")
        client = DistributionClient(t)
        client.ping(timeout=1.0)
        evts = client.take_events()
        self.assertEqual(len(evts), 2)


if __name__ == "__main__":
    unittest.main()
