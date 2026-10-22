import unittest

from flow_temperature_curve import FlowPoint, FlowOutsideEnvelope, FlowTemperatureCurve


class CurveTests(unittest.TestCase):
    def curve(self, headroom=0, interpolation='linear'):
        # Synthetic flow points; these are not hardware performance ratings.
        return FlowTemperatureCurve([FlowPoint(210, 10), FlowPoint(220, 20),
                                     FlowPoint(230, 40), FlowPoint(245, 75)],
                                    max_temperature_c=245, max_flow_mm3_s=75,
                                    flow_headroom_fraction=headroom, interpolation=interpolation)

    def test_interpolation_uses_measured_brackets(self):
        r = self.curve().temperature_for_flow(30)
        self.assertEqual(r['temperature_target_estimate_c'], 225)
        self.assertTrue(r['interpolated'])
        self.assertFalse(r['ready_for_automatic_control'])

    def test_exact_anchor_and_minimum_temperature_floor(self):
        self.assertEqual(self.curve().temperature_for_flow(75)['temperature_target_estimate_c'], 245)
        self.assertEqual(self.curve().temperature_for_flow(1)['temperature_target_estimate_c'], 210)

    def test_no_extrapolation(self):
        with self.assertRaises(FlowOutsideEnvelope):
            self.curve().temperature_for_flow(75.01)

    def test_headroom_reduces_operating_capacity(self):
        r = self.curve(0.1).temperature_for_flow(67.5)
        self.assertEqual(r['temperature_target_estimate_c'], 245)
        with self.assertRaises(FlowOutsideEnvelope):
            self.curve(0.1).temperature_for_flow(75)

    def test_conservative_upper_anchor_mode(self):
        r = self.curve(interpolation='upper_anchor').temperature_for_flow(30)
        self.assertEqual(r['temperature_target_estimate_c'], 230)
        self.assertFalse(r['interpolated'])

    def test_bad_measurements_not_silently_fixed(self):
        for points in ([FlowPoint(210, 20), FlowPoint(220, 10)],
                       [FlowPoint(210, 20), FlowPoint(210, 30)],
                       [FlowPoint(210, 20), FlowPoint(250, 76)]):
            with self.assertRaises(ValueError):
                FlowTemperatureCurve(points, max_temperature_c=245, max_flow_mm3_s=75,
                                     flow_headroom_fraction=0.1)

    def test_flat_capacity_region(self):
        c = FlowTemperatureCurve([FlowPoint(210, 10), FlowPoint(220, 10), FlowPoint(230, 20)],
                                 max_temperature_c=245, max_flow_mm3_s=75, flow_headroom_fraction=0)
        self.assertEqual(c.temperature_for_flow(15)['temperature_target_estimate_c'], 225)


if __name__ == '__main__':
    unittest.main()
