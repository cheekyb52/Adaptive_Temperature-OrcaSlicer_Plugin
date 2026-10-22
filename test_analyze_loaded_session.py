import unittest

from analyze_loaded_session import summarize_rows, band_recovery_seconds


class LoadedAnalysisTests(unittest.TestCase):
    def test_recovery_requires_a_sustained_band_after_sag(self):
        rows=[dict(time_s=i*0.5, temperature_c=209 if i < 5 else 210) for i in range(40)]
        self.assertEqual(band_recovery_seconds(rows, 210), 2.5)
        self.assertIsNone(band_recovery_seconds(rows[:10], 210))

    def rows(self):
        rows = []
        for i in range(110):
            t = i*0.5
            flow = 7.5 if t < 5 or t >= 40 else 15 if t < 10 else 30
            rows.append(dict(stage='loaded_flow', time_s=t, flow_mm3_s=flow,
                             target_c=210, temperature_c=209 if flow == 30 else 210,
                             power_fraction=0.5))
        return rows

    def test_full_flow_is_selected_by_executing_velocity(self):
        r = summarize_rows(self.rows(), 210, 30)
        self.assertEqual(r['full_flow_samples'], 60)
        self.assertEqual(r['maximum_full_flow_sag_c'], 1)
        self.assertEqual(r['observed_full_flow_span_s'], 29.5)
        self.assertTrue(r['tracking_within_5c_in_sampled_full_flow_window'])
        self.assertFalse(r['print_quality_validated'])

    def test_missing_or_insufficient_motion_is_rejected(self):
        rows = self.rows()
        for r in rows:
            r['flow_mm3_s'] = None
        with self.assertRaises(ValueError):
            summarize_rows(rows, 210, 30)

    def test_setpoint_changes_are_not_mislabeled_as_load_tests(self):
        rows = self.rows()
        rows[30]['target_c'] = 220
        with self.assertRaises(ValueError):
            summarize_rows(rows, 210, 30)


if __name__ == '__main__':
    unittest.main()
