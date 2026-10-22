import math
import unittest

from thermal_identification import CalibrationBounds, InsufficientExperiment, fit_temperature_step, suggest_next_probe


def samples(start=230, end=240, delay=3, tau=8, duration=100):
    out = []
    for i in range(-100, duration*5+1):
        t = i/5
        temp = start if t <= delay else start+(end-start)*(1-math.exp(-(t-delay)/tau))
        out.append(dict(time_s=t, temperature_c=temp, target_c=start if t < 0 else end,
                        flow_mm3_s=5, fan_fraction=0.5, power_fraction=0.4))
    return out


class IdentificationTests(unittest.TestCase):
    def test_identifies_heating(self):
        model = fit_temperature_step(samples(), 0)
        self.assertEqual(model['direction'], 'heating')
        self.assertAlmostEqual(model['delay_s'], 3, delta=0.1)
        self.assertAlmostEqual(model['time_constant_s'], 8, delta=0.1)
        self.assertFalse(model['ready_for_control'])

    def test_different_hotend_cooling(self):
        model = fit_temperature_step(samples(260, 250, delay=5, tau=20, duration=240), 0)
        self.assertEqual(model['direction'], 'cooling')
        self.assertAlmostEqual(model['delay_s'], 5, delta=0.1)
        self.assertAlmostEqual(model['time_constant_s'], 20, delta=0.1)

    def test_unsettled_tail_is_rejected(self):
        with self.assertRaises(InsufficientExperiment):
            fit_temperature_step(samples(duration=15), 0)

    def test_confounded_flow_rejected(self):
        data = samples()
        data[150]['flow_mm3_s'] = 30
        with self.assertRaises(InsufficientExperiment):
            fit_temperature_step(data, 0)

    def test_missing_telemetry_rejected(self):
        data = samples()
        data[200]['temperature_c'] = float('nan')
        with self.assertRaises(InsufficientExperiment):
            fit_temperature_step(data, 0)

    def test_probe_duration_adapts_to_response(self):
        fast = fit_temperature_step(samples(delay=1, tau=4), 0)
        slow = fit_temperature_step(samples(delay=5, tau=20, duration=240), 0)
        a = suggest_next_probe(fast, 220, 245, 75)
        b = suggest_next_probe(slow, 220, 245, 75)
        self.assertGreater(b['provisional_observation_s'], a['provisional_observation_s'])
        self.assertFalse(b['executes_hardware_actions'])
        self.assertEqual(a['target_temperature_c'], 230)

    def test_probe_cannot_exceed_bounds(self):
        model = fit_temperature_step(samples(), 0)
        with self.assertRaises(ValueError):
            suggest_next_probe(model, 235, 245, 75)

    def test_user_ceiling_pair_is_inclusive(self):
        bounds = CalibrationBounds(75, 245)
        self.assertTrue(bounds.validate_probe(245, 75))
        self.assertTrue(bounds.validate_probe(230, 5))
        for temperature, flow in ((245.01, 75), (245, 75.01), (230, -1), (float('nan'), 5)):
            with self.assertRaises(ValueError):
                bounds.validate_probe(temperature, flow)

    def test_reverse_probe_respects_flow_ceiling(self):
        model = fit_temperature_step(samples(), 0)
        with self.assertRaises(ValueError):
            suggest_next_probe(model, 220, 245, 4)

    def test_invalid_bounds_rejected(self):
        for flow, temp in ((0, 245), (75, float('inf')), (-75, 245)):
            with self.assertRaises(ValueError):
                CalibrationBounds(flow, temp)


if __name__ == '__main__':
    unittest.main()
