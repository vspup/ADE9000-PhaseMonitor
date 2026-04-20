"""Run from software/pc_monitor/: `python -m pytest tests/` (or unittest)."""
import unittest

from core.measurement_mode import MeasurementMode
from core.packet_parser import parse_packet


class TestParseDelta(unittest.TestCase):
    def test_full_delta_packet(self):
        line = ('{"ts":15230,"mode":"delta","uab":401.20,"ubc":398.70,'
                '"uca":403.10,"uavg":401.00,"unb":0.86,"f":50.01,'
                '"state":1,"flags":[]}')
        p = parse_packet(line)
        self.assertIsNotNone(p)
        self.assertEqual(p.ts, 15230)
        self.assertEqual(p.mode, MeasurementMode.MEASURE_DELTA)
        self.assertAlmostEqual(p.uab, 401.20)
        self.assertAlmostEqual(p.uavg, 401.00)
        self.assertEqual(p.flags, [])

    def test_flags_preserved(self):
        line = '{"ts":1,"mode":"delta","flags":["dip","unb"]}'
        p = parse_packet(line)
        self.assertEqual(p.flags, ['dip', 'unb'])


class TestParseWye(unittest.TestCase):
    def test_full_wye_packet(self):
        line = ('{"ts":15430,"mode":"wye","va":231.50,"vb":229.80,'
                '"vc":230.60,"vavg":230.63,"unb":0.37,"f":50.01,'
                '"state":1,"flags":[]}')
        p = parse_packet(line)
        self.assertEqual(p.mode, MeasurementMode.MEASURE_WYE)
        self.assertAlmostEqual(p.va, 231.50)
        self.assertAlmostEqual(p.vavg, 230.63)
        # delta fields default to 0
        self.assertEqual(p.uab, 0.0)


class TestParseCalLn(unittest.TestCase):
    def test_cal_ln_packet(self):
        line = '{"ts":16000,"mode":"cal_ln","va":62.40,"vb":0.00,"vc":0.00,"f":50.01,"state":1,"flags":[]}'
        p = parse_packet(line)
        self.assertEqual(p.mode, MeasurementMode.CALIBRATION_LN)
        self.assertAlmostEqual(p.va, 62.40)
        self.assertEqual(p.unb, 0.0)   # not in cal_ln packets


class TestNonTelemetry(unittest.TestCase):
    def test_status_lines_return_none(self):
        self.assertIsNone(parse_packet('{"status":"ok","event":"pong"}'))
        self.assertIsNone(parse_packet('{"status":"ok","event":"boot","fw":"x","ver":"1"}'))
        self.assertIsNone(parse_packet('{"status":"error","reason":"unknown_cmd"}'))

    def test_cal_events_return_none(self):
        self.assertIsNone(parse_packet('{"status":"ok","event":"cal_rms","phase":"A","vrms":62.394}'))


class TestMalformed(unittest.TestCase):
    def test_empty_string(self):
        self.assertIsNone(parse_packet(''))

    def test_garbage(self):
        self.assertIsNone(parse_packet('not json at all'))

    def test_truncated_json(self):
        self.assertIsNone(parse_packet('{"ts":123,"mode":"delta"'))

    def test_wrong_types(self):
        self.assertIsNone(parse_packet('{"ts":"not_a_number","mode":"delta"}'))

    def test_whitespace_tolerant(self):
        line = '  {"ts":1,"mode":"delta","uab":100.0}  \n'
        p = parse_packet(line)
        self.assertIsNotNone(p)
        self.assertEqual(p.ts, 1)


class TestUnknownMode(unittest.TestCase):
    def test_unknown_mode_falls_back_to_delta(self):
        p = parse_packet('{"ts":1,"mode":"something_new"}')
        self.assertEqual(p.mode, MeasurementMode.MEASURE_DELTA)

    def test_missing_mode_defaults_to_delta(self):
        p = parse_packet('{"ts":1,"uab":100.0}')
        self.assertEqual(p.mode, MeasurementMode.MEASURE_DELTA)


if __name__ == '__main__':
    unittest.main()
