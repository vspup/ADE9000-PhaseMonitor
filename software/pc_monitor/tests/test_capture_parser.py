"""Run from software/pc_monitor/: `python -m pytest tests/`."""
import unittest

from core.capture_parser import (
    CaptureDone,
    CaptureSample,
    CaptureStatus,
    parse_capture_event,
)
from core.packet_parser import parse_packet


class TestCaptureStatus(unittest.TestCase):
    def test_full_status(self):
        line = ('{"status":"ok","event":"cap_status","state":"ARMED",'
                '"filled":47,"pre":100,"post":200,"total":500}')
        e = parse_capture_event(line)
        self.assertIsInstance(e, CaptureStatus)
        self.assertEqual(e.state, "ARMED")
        self.assertEqual(e.filled, 47)
        self.assertEqual(e.pre, 100)
        self.assertEqual(e.post, 200)
        self.assertEqual(e.total, 500)

    def test_all_four_states(self):
        for s in ("IDLE", "ARMED", "TRIGGERED", "READY"):
            line = (f'{{"event":"cap_status","state":"{s}","filled":0,'
                    f'"pre":100,"post":200,"total":500}}')
            e = parse_capture_event(line)
            self.assertEqual(e.state, s)

    def test_missing_pre_post_defaults_to_zero(self):
        # Older firmware w/o pre/post fields — parser tolerates.
        line = '{"event":"cap_status","state":"IDLE","filled":0,"total":300}'
        e = parse_capture_event(line)
        self.assertEqual(e.pre, 0)
        self.assertEqual(e.post, 0)


class TestCaptureSample(unittest.TestCase):
    def test_pre_trigger_sample(self):
        line = ('{"event":"cap_sample","i":-100,"uab":401.20,"ubc":398.70,'
                '"uca":403.10,"ia":1.234,"ib":1.251,"ic":1.220}')
        e = parse_capture_event(line)
        self.assertIsInstance(e, CaptureSample)
        self.assertEqual(e.i, -100)
        self.assertAlmostEqual(e.uab, 401.20)
        self.assertAlmostEqual(e.ic, 1.220)

    def test_trigger_moment(self):
        line = '{"event":"cap_sample","i":0,"uab":340.0,"ubc":400.0,"uca":400.0,"ia":1.0,"ib":1.0,"ic":1.0}'
        e = parse_capture_event(line)
        self.assertEqual(e.i, 0)

    def test_post_trigger_sample(self):
        line = '{"event":"cap_sample","i":199,"uab":400.0,"ubc":400.0,"uca":400.0,"ia":0,"ib":0,"ic":0}'
        e = parse_capture_event(line)
        self.assertEqual(e.i, 199)

    def test_missing_index_is_invalid(self):
        line = '{"event":"cap_sample","uab":400.0}'
        self.assertIsNone(parse_capture_event(line))


class TestCaptureDone(unittest.TestCase):
    def test_done(self):
        e = parse_capture_event('{"status":"ok","event":"cap_done","n":300}')
        self.assertIsInstance(e, CaptureDone)
        self.assertEqual(e.n, 300)


class TestNonCaptureLines(unittest.TestCase):
    def test_telemetry_returns_none(self):
        line = '{"ts":15230,"mode":"delta","uab":401.20,"f":50.01,"state":1,"flags":[]}'
        self.assertIsNone(parse_capture_event(line))

    def test_other_events_return_none(self):
        self.assertIsNone(parse_capture_event('{"status":"ok","event":"pong"}'))
        self.assertIsNone(parse_capture_event(
            '{"status":"ok","event":"wmode","wmode":"capture"}'))
        self.assertIsNone(parse_capture_event(
            '{"status":"error","reason":"not_armed"}'))

    def test_cap_triggered_marker_returns_none(self):
        # Intentional — markers carry no structured payload.
        self.assertIsNone(parse_capture_event('{"status":"ok","event":"cap_triggered"}'))
        self.assertIsNone(parse_capture_event('{"status":"ok","event":"cap_aborted"}'))


class TestMalformed(unittest.TestCase):
    def test_empty(self):
        self.assertIsNone(parse_capture_event(''))

    def test_garbage(self):
        self.assertIsNone(parse_capture_event('not json'))

    def test_truncated(self):
        self.assertIsNone(parse_capture_event('{"event":"cap_sample","i":0'))

    def test_wrong_type(self):
        self.assertIsNone(parse_capture_event(
            '{"event":"cap_sample","i":"zero","uab":0,"ubc":0,"uca":0,"ia":0,"ib":0,"ic":0}'))

    def test_whitespace_tolerant(self):
        line = '  {"event":"cap_done","n":42}  \n'
        e = parse_capture_event(line)
        self.assertEqual(e.n, 42)


class TestPacketParserRegression(unittest.TestCase):
    """Ensure capture events don't leak into the telemetry parser."""

    def test_cap_sample_not_a_packet(self):
        line = ('{"event":"cap_sample","i":-100,"uab":401.2,"ubc":398.7,'
                '"uca":403.1,"ia":1.2,"ib":1.2,"ic":1.2}')
        self.assertIsNone(parse_packet(line))

    def test_cap_status_not_a_packet(self):
        self.assertIsNone(parse_packet(
            '{"status":"ok","event":"cap_status","state":"ARMED","filled":0,"pre":100,"post":200,"total":500}'))

    def test_cap_done_not_a_packet(self):
        self.assertIsNone(parse_packet('{"status":"ok","event":"cap_done","n":300}'))


if __name__ == '__main__':
    unittest.main()
