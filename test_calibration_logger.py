import unittest

from calibration_logger import MoonrakerReader


class FakeReader(MoonrakerReader):
    def __init__(self, active=False, missing=False):
        self.calls = []
        self.active = active
        self.missing = missing

    def query(self, objects):
        self.calls.append(objects)
        if objects[0] == 'print_stats':
            return {'status': {} if self.missing else {
                'print_stats': {'state': 'printing' if self.active else 'standby'},
                'virtual_sdcard': {'is_active': self.active},
                'pause_resume': {'is_paused': False}}}
        return {'eventtime': 10, 'status': {
            'extruder3': {'temperature': 22, 'target': 0, 'power': 0, 'state': 'PARKED'},
            'toolhead': {'extruder': 'extruder'},
            'fan_generic e3_fan': {'speed': 0}}}


class LoggerTests(unittest.TestCase):
    def test_state_precedes_temperature_read(self):
        r = FakeReader()
        row = r.sample('extruder3', 'fan_generic e3_fan')
        self.assertEqual(r.calls[0][0], 'print_stats')
        self.assertEqual(r.calls[1][0], 'extruder3')
        self.assertEqual(row['temperature_c'], 22)
        self.assertIsNone(row['flow_mm3_s'])

    def test_active_job_remains_read_only_with_unknown_flow(self):
        r = FakeReader(active=True)
        row = r.sample('extruder3')
        self.assertTrue(row['active_job_or_pause'])
        self.assertIsNone(row['flow_mm3_s'])

    def test_no_further_request_if_state_missing(self):
        r = FakeReader(missing=True)
        with self.assertRaises(RuntimeError):
            r.sample('extruder3')
        self.assertEqual(len(r.calls), 1)


if __name__ == '__main__':
    unittest.main()
