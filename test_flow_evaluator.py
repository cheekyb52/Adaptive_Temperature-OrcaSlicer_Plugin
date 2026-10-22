import hashlib
import math
from pathlib import Path
import tempfile
import unittest

from flow_evaluator import Analyzer, Move, analyze_file, plan_moves


class FlowTests(unittest.TestCase):
    def evaluate(self, code, **config):
        analyzer = Analyzer({'logical_tool': 3, **config})
        report = analyzer.read(code.splitlines())
        return analyzer, report

    def test_short_move_triangular_profile(self):
        move = Move(1, 3, 'test', (1, 0, 0), 0.1, 1, 100, 1000, 1000, 0, 1)
        plan_moves([move])
        self.assertAlmostEqual(move.cruise_speed, math.sqrt(1000))
        self.assertAlmostEqual(move.duration, 2/math.sqrt(1000))
        self.assertGreater(move.duration, move.length/move.speed)

    def test_minimum_cruise_ratio(self):
        move = Move(1, 3, 'test', (1, 0, 0), 0.1, 1, 100, 1000, 500, 0, 1)
        plan_moves([move])
        self.assertAlmostEqual(move.cruise_speed, math.sqrt(500))
        cruise_distance = move.cruise_time*move.cruise_speed
        self.assertAlmostEqual(cruise_distance, 0.5)

    def test_cornering_speed_changes_timing(self):
        def duration(scv):
            jd = scv**2*(math.sqrt(2)-1)/1000
            moves = [Move(1, 3, '', (20, 0, 0), 1, 20, 100, 1000, 1000, jd, 1),
                     Move(2, 3, '', (0, 20, 0), 1, 20, 100, 1000, 1000, jd, 1)]
            plan_moves(moves)
            return sum(m.duration for m in moves), moves[1].start_speed
        slow, vslow = duration(2)
        fast, vfast = duration(15)
        self.assertGreater(slow, fast)
        self.assertAlmostEqual(vslow, 2)
        self.assertAlmostEqual(vfast, 15)

    def test_collinear_moves_keep_speed(self):
        moves = [Move(i, 3, '', (100, 0, 0), 1, 100, 100, 1000, 1000, 0, 1) for i in range(3)]
        plan_moves(moves)
        self.assertAlmostEqual(moves[1].start_speed, 100)
        self.assertAlmostEqual(moves[1].end_speed, 100)

    def test_relative_absolute_e_and_retractions(self):
        code = 'T3\nG90\nG1 X0 Y0 Z0 F6000\nM83\nG1 E-1\nG1 E1\nG1 X100 E2\nM82\nG92 E10\nG1 X200 E12'
        a, r = self.evaluate(code)
        self.assertEqual(r['target']['deposition_moves'], 2)
        self.assertAlmostEqual(r['target']['deposited_volume_mm3'], 4*math.pi*(1.75/2)**2)

    def test_gcode_acceleration_and_speed_overrides(self):
        code = 'T3\nG1 X0 Y0 Z0 F6000\nM83\nSET_VELOCITY_LIMIT ACCEL=2000 SQUARE_CORNER_VELOCITY=12 MINIMUM_CRUISE_RATIO=0.2\nG1 X100 E1\nM204 S500\nM220 S50\nM221 S200\nG1 X200 E1'
        a, r = self.evaluate(code)
        self.assertEqual([m.accel for m in a.moves], [2000, 500])
        self.assertEqual([m.speed for m in a.moves], [100, 50])
        self.assertAlmostEqual(a.moves[1].deposited_mm3, 2*a.moves[0].deposited_mm3)

    def test_legacy_accel_to_decel(self):
        a, _ = self.evaluate('T3\nG1 X0 Y0 Z0 F6000\nM83\nSET_VELOCITY_LIMIT ACCEL=1000 ACCEL_TO_DECEL=500\nG1 X1 E1')
        self.assertAlmostEqual(a.moves[0].pseudo_accel, 500)
        self.assertAlmostEqual(a.moves[0].cruise_speed, math.sqrt(500))

    def test_unknown_macros_break_timing_and_position(self):
        a, r = self.evaluate('T3\nG1 X0 Y0 Z0 F6000\nM83\nG1 X100 E1\nHIDDEN_MOVE\nG1 X200 E1 F6000\nG1 X300 E1')
        self.assertEqual(r['target']['deposition_moves'], 2)
        self.assertIn('HIDDEN_MOVE', r['unknown_timing_barriers'])
        self.assertEqual(a.moves[0].end_speed, 0)

    def test_arc_is_explicitly_unsupported(self):
        _, r = self.evaluate('T3\nG2 X10 Y10 I5 E1')
        self.assertFalse(r['supported_motion_only'])
        self.assertFalse(r['ready_for_temperature_control'])

    def test_dwell_and_high_flow_duration(self):
        a, r = self.evaluate('T3\nG1 X0 Y0 Z0 F6000\nM83\nG4 P1000\nG1 X100 E10', high_flow_threshold_mm3_s=10)
        self.assertAlmostEqual(a.moves[0].time, 1)
        self.assertGreater(r['target']['time_above_threshold_s'], 0.5)
        self.assertLess(r['target']['time_above_threshold_s'], a.moves[0].duration)

    def test_analysis_does_not_modify_input(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'sample.gcode'
            p.write_text('M104 S245 T3\nT3\nG1 X0 Y0 Z0 F6000\nM83\nG1 X100 E1')
            before = hashlib.sha256(p.read_bytes()).hexdigest()
            _, r = analyze_file(p)
            self.assertEqual(before, hashlib.sha256(p.read_bytes()).hexdigest())
            self.assertEqual(r['temperature_commands_preserved']['M104'], 1)

    def test_invalid_configuration_and_units_fail(self):
        for config in ({'max_accel_mm_s2': 0}, {'minimum_cruise_ratio': 1}, {'max_velocity_mm_s': float('nan')}):
            with self.assertRaises(ValueError):
                Analyzer(config)
        with self.assertRaises(ValueError):
            self.evaluate('G20')


if __name__ == '__main__':
    unittest.main()
