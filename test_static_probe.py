import unittest

from static_probe import extrusion_probe, first_load_probe, loaded_response_probe
from thermal_identification import CalibrationBounds


class StaticProbeTests(unittest.TestCase):
    def test_flow_load_rounding_sweep_2_through_100(self):
        area=3.141592653589793*(1.75/2)**2
        for tenths in range(20,1001):
            flow=tenths/10
            plan=loaded_response_probe(flow,240)
            offset=0
            for phase in plan['phases']:
                duration=0
                for line in plan['commands'][offset:offset+phase['command_count']]:
                    _,e,f=line.split();distance,feed=float(e[1:]),float(f[1:])
                    self.assertGreater(distance,0)
                    self.assertLessEqual(feed/60*area,phase['requested_flow_mm3_s']+1e-10)
                    duration+=distance/(feed/60)
                self.assertAlmostEqual(duration,phase['duration_s'],delta=.001)
                offset+=phase['command_count']

    def test_loaded_flow_steps_are_bounded_and_match_timeline(self):
        for temperature, flow in ((210, 30), (220, 38), (230, 50)):
            p = loaded_response_probe(flow, temperature)
            self.assertEqual(p['total_requested_duration_s'], 55)
            self.assertEqual(p['phases'][2]['requested_flow_mm3_s'], flow)
            self.assertEqual(p['phases'][2]['duration_s'], 30)
            self.assertEqual(p['phases'][3]['start_offset_s'], 40)
            for phase in p['phases']:
                self.assertLessEqual(phase['commanded_flow_mm3_s'], flow)
            for command in p['commands']:
                self.assertRegex(command, r'^G1 E[0-9.]+ F[0-9.]+$')
                self.assertLessEqual(float(command.split()[1][1:]), 100)

    def test_maximum_flow_conversion_and_chunks(self):
        p = extrusion_probe(75, 245, 20, CalibrationBounds(75, 245))
        self.assertAlmostEqual(p['filament_feed_mm_s'], 31.181, delta=0.01)
        self.assertLessEqual(p['commanded_cruise_flow_mm3_s'], 75)
        for command in p['commands']:
            self.assertTrue(command.startswith('G1 E'))
            self.assertNotIn('X', command)
            self.assertNotIn('Y', command)
            self.assertNotIn('Z', command)
            self.assertLessEqual(float(command.split()[1][1:]), 200)
        self.assertFalse(p['xyz_motion'])

    def test_first_probe_scales_to_flow_bound(self):
        p = first_load_probe(75, 245)
        self.assertEqual(p['requested_flow_mm3_s'], 3.75)
        self.assertEqual(p['temperature_target_c'], 245)
        smaller = first_load_probe(10, 220)
        self.assertEqual(smaller['requested_flow_mm3_s'], 0.5)

    def test_ceiling_and_firmware_limit_enforced(self):
        bounds = CalibrationBounds(75, 245)
        for flow, temp in ((76, 245), (75, 246)):
            with self.assertRaises(ValueError):
                extrusion_probe(flow, temp, 20, bounds)
        with self.assertRaises(ValueError):
            extrusion_probe(75, 245, 20, bounds, max_extrude_only_velocity=10)


if __name__ == '__main__':
    unittest.main()
