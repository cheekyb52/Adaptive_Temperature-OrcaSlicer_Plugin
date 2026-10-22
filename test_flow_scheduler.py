# SPDX-License-Identifier: GPL-3.0-only
from copy import deepcopy
from pathlib import Path
import re
import tempfile
import unittest

from create_gcode_edit_demo import DEMO_CONFIG, demo_gcode
from flow_scheduler import EditingRefused, RecordingAnalyzer, edit_working_file, schedule_text, strip_generated


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.cfg = deepcopy(DEMO_CONFIG)
        self.source = demo_gcode()

    def test_original_motion_startup_first_layer_and_shutdown_preserved(self):
        edited, report = schedule_text(self.source, self.cfg)
        self.assertGreater(report['commands_inserted'], 2)
        self.assertEqual(strip_generated(edited), self.source)
        start = self.source.index(';LAYER_CHANGE', self.source.index(';LAYER_CHANGE')+1)
        self.assertEqual(edited[:start], self.source[:start])
        self.assertIn(';TYPE:Custom\nM104 T2 S0\nM140 S0\n; EXECUTABLE_BLOCK_END\n', edited)
        self.assertFalse(report['ready_for_printing'])

    def test_edit_is_idempotent_and_crlf_is_preserved(self):
        source = self.source.replace('\n', '\r\n')
        edited, first = schedule_text(source, self.cfg)
        repeated, second = schedule_text(edited, self.cfg)
        self.assertEqual(edited, repeated)
        self.assertEqual(first['source_sha256'], second['source_sha256'])
        self.assertEqual(strip_generated(edited), source)
        self.assertNotIn('\n', edited.replace('\r\n', ''))

    def test_heat_request_precedes_high_flow_and_cooling_is_held(self):
        analyzer = RecordingAnalyzer({**self.cfg['motion'], 'logical_tool': 2})
        analyzer.read(self.source.splitlines())
        high = [m for m in analyzer.moves if m.deposited_mm3 and m.volume_per_mm*m.cruise_speed > 40]
        _, report = schedule_text(self.source, self.cfg)
        hot = [e for e in report['events'] if e['temperature_c'] > 210]
        self.assertLess(hot[0]['modeled_time_s'], high[0].time)
        self.assertLessEqual(high[0].time-hot[0]['modeled_time_s'], self.cfg['heating_lead_s']+0.01)
        cooled = [e for e in report['events'] if e['modeled_time_s'] > hot[0]['modeled_time_s'] and e['temperature_c'] == 210]
        self.assertTrue(cooled)
        self.assertGreaterEqual(cooled[0]['modeled_time_s'], high[-1].time+high[-1].duration+self.cfg['cooling_hold_s'])

    def test_acceleration_affects_generated_temperature(self):
        _, normal = schedule_text(self.source, self.cfg)
        slow = self.source.replace('M204 S10000', 'M204 S10')
        _, limited = schedule_text(slow, self.cfg)
        self.assertLess(limited['temperature_range_c'][1], normal['temperature_range_c'][1])

    def test_tool_specific_commands_and_macro_boundaries(self):
        source = self.source.replace(';TYPE:Custom\nM104 T2 S0',
            'CUSTOM_TOOL_MACRO\nT1\nG1 X80 Y80 Z0.4 F6000\n;TYPE:Inner wall\nG1 X180 E4 F6000\n;TYPE:Custom\nM104 T2 S0')
        edited, report = schedule_text(source, self.cfg)
        self.assertTrue(all(e['logical_tool'] == 2 for e in report['events']))
        after = edited.split('CUSTOM_TOOL_MACRO')[1]
        self.assertNotIn('ORCA_FLOW_TEMP', after)
        self.assertEqual(strip_generated(edited), source)

    def test_excess_flow_is_refused_without_changing_file(self):
        source = self.source.replace('E20 F6000', 'E40 F6000')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'working.gcode'
            path.write_text(source)
            before = path.read_bytes()
            with self.assertRaisesRegex(EditingRefused, 'operating limit'):
                edit_working_file(path, self.cfg)
            self.assertEqual(path.read_bytes(), before)

    def test_opt_in_and_mapping_are_required(self):
        for field, value in (('output_mode', 'analysis'), ('physical_tool', 3)):
            cfg = {**self.cfg, field: value}
            with self.subTest(field=field), self.assertRaises(EditingRefused):
                schedule_text(self.source, cfg)

    def test_unsupported_arcs_units_numbering_and_missing_layers_refused(self):
        for source in (self.source.replace('G1 X80 Y91 E20', 'G2 X80 Y91 E20', 1),
                       self.source.replace(';LAYER_CHANGE', ';no layer'),
                       self.source+'N10 G1 X0*1\n', self.source+'G20\n'):
            with self.subTest(source=source[-70:]), self.assertRaises(ValueError):
                schedule_text(source, self.cfg)

    def test_unknown_owned_markers_are_not_silently_deleted(self):
        with self.assertRaises(EditingRefused):
            schedule_text(self.source+'M104 T2 S500 ; ORCA_FLOW_TEMP unknown\n', self.cfg)

    def test_actual_slice_material_nozzle_and_mapping_must_match(self):
        for source in (self.source.replace('PLA;PLA;PLA;PLA', 'PLA;PLA;ASA;PLA'),
                       self.source.replace('0.4,0.4,0.8,0.8', '0.4,0.4,0.4,0.8'),
                       self.source.replace('filament_map = 1,2,3,4', 'filament_map = 1,2,1,4'),
                       self.source.replace('filament_type =', 'unknown_type =')):
            with self.subTest(), self.assertRaises(EditingRefused):
                schedule_text(source, self.cfg)


if __name__ == '__main__':
    unittest.main()
