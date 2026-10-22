import json
import math
from pathlib import Path
import tempfile
import unittest

from analyze_thermal_session import analyze_session


class SessionAnalysisTests(unittest.TestCase):
    def write_session(self, path, tool=2, complete=True):
        events = [
            {'event': 'session_start', 'data': {'action': 'Thermal', 'physical_tool': tool}},
            {'event': 'target_step', 'data': {'target_c': 240, 'before_time_s': 0}},
            {'event': 'settled', 'data': {'stage': 'heating_1_240', 'time_s': 100}},
        ]
        if complete:
            events += [{'event': 'session_complete'}, {'event': 'heater_off'}]
        rows = []
        for index in range(-40, 201):
            t = index*0.5
            temp = 235 if t <= 2 else 235+5*(1-math.exp(-(t-2)/5))
            rows.append(dict(time_s=t, temperature_c=temp, target_c=235 if t < 0 else 240,
                             physical_tool=tool, fan_fraction=0, power_fraction=0.2, flow_mm3_s=None))
        for name, content in [('events.jsonl', events), ('telemetry.jsonl', rows)]:
            (path/name).write_text('\n'.join(json.dumps(row) for row in content), encoding='utf-8-sig')

    def test_completed_correct_head_can_produce_candidate(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            self.write_session(p)
            report = analyze_session(p)
            self.assertEqual(report['transition_statuses'], {'candidate_fit': 1})
            self.assertFalse(report['ready_for_automatic_control'])
            self.assertAlmostEqual(report['transitions'][0]['model']['time_constant_s'], 5, delta=0.1)

    def test_incomplete_wrong_or_excluded_sessions_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            self.write_session(p, complete=False)
            with self.assertRaises(ValueError):
                analyze_session(p)
            self.write_session(p, tool=3)
            with self.assertRaises(ValueError):
                analyze_session(p)
            self.write_session(p)
            (p/'EXCLUDED.json').write_text('{}')
            with self.assertRaises(ValueError):
                analyze_session(p)


if __name__ == '__main__':
    unittest.main()
