# SPDX-License-Identifier: GPL-3.0-only
from copy import deepcopy
import unittest

from create_gcode_edit_demo import DEMO_CONFIG,demo_gcode
from flow_scheduler import schedule_text,strip_generated
from tool_profiles import fresh_profile,copy_calibration,profile_config
from test_fixtures import response_profile


def configured():
    cfg={**fresh_profile(2,'Example printer'),**deepcopy(DEMO_CONFIG),'printer_name':'Example printer',
         'material':'Example PLA','hotend':'Example hotend / 0.8 mm','tool_configured':True,
         'tool_mode':'toolchanger','tool_count':4,'tool_profiles':{},'scheduler':'hybrid_predictive'}
    cfg['thermal_response']=response_profile()
    cfg.update(burst_preset='lukes-lab-pika-u1',burst_aggressiveness=50)
    return cfg


class ToolProfileTests(unittest.TestCase):
    def test_confirming_a_placeholder_does_not_make_it_copyable(self):
        cfg=configured()
        cfg.update(fresh_profile(2,cfg['printer_name']))
        cfg['tool_configured']=True
        with self.assertRaisesRegex(ValueError,'no calibrated response'):
            copy_calibration(cfg,2,0)

    def test_old_copied_placeholder_does_not_become_calibrated(self):
        cfg=configured()
        cfg['thermal_response']=fresh_profile(2,cfg['printer_name'])['thermal_response']
        cfg['thermal_response']['source']='copied_response_candidate'
        cfg['thermal_response'].pop('configuration_state')  # Existing 0.5.0 copies.
        with self.assertRaisesRegex(ValueError,'no calibrated response'):
            copy_calibration(cfg,2,0)

    def test_response_bound_to_another_nozzle_cannot_be_copied(self):
        cfg=configured()
        cfg['nozzle_diameter_mm']=.4
        with self.assertRaisesRegex(ValueError,'does not match'):
            copy_calibration(cfg,2,0)

    def test_new_tool_never_inherits_another_tools_measurements(self):
        fresh=profile_config(configured(),0)
        self.assertFalse(fresh['tool_configured'])
        self.assertEqual(fresh['thermal_response']['source'],'unconfigured')
        self.assertEqual(fresh['max_flow_mm3_s'],1)

    def test_copy_rebinds_response_preserves_target_mapping_and_is_deep(self):
        cfg=configured()
        cfg['tool_profiles']['3']=fresh_profile(3,'Example printer')
        cfg['tool_profiles']['3']['fan_name']='fan_generic part3'
        copied=copy_calibration(cfg,2,3)
        self.assertEqual(copied['physical_tool'],3)
        self.assertEqual(copied['heater_name'],'extruder3')
        self.assertEqual(copied['fan_name'],'fan_generic part3')
        self.assertEqual(copied['thermal_response']['binding']['physical_tool'],3)
        self.assertEqual(copied['thermal_response']['copied_from_binding']['physical_tool'],2)
        self.assertTrue(copied['tool_configured'])
        copied['points'][0]['temperature_c']=999
        self.assertEqual(cfg['points'][0]['temperature_c'],210)

    def test_missing_used_tool_refuses_whole_export(self):
        cfg=configured()
        text=demo_gcode().replace(';TYPE:Custom','T3\n;TYPE:Internal infill\nG1 X80 Y180 E4 F6000\nG1 X180 Y180 E4 F6000\n;TYPE:Custom',1)
        with self.assertRaisesRegex(ValueError,'nozzle|no calibrated response'):
            schedule_text(text,cfg)

    def test_four_independent_schedules_merge_once_and_are_idempotent(self):
        cfg=configured()
        for tool in (0,1,3):
            record=copy_calibration(cfg,2,tool);record['tool_configured']=True
            cfg['tool_profiles'][str(tool)]=record
        extra=''
        for tool in (0,1,3):
            extra+=f'T{tool}\nM104 T{tool} S230\n;TYPE:Internal infill\nG1 X80 Y180 E4 F6000\nG1 X180 Y180 E4 F6000\n'
        text=demo_gcode().replace(';TYPE:Custom',extra+';TYPE:Custom',1).replace('0.4,0.4,0.8,0.8','0.8,0.8,0.8,0.8')
        output,report=schedule_text(text,cfg)
        self.assertEqual(report['used_logical_tools'],[0,1,2,3])
        self.assertEqual(strip_generated(output),text)
        self.assertEqual(schedule_text(output,cfg)[0],output)
        self.assertEqual(output.count('; ADAPTIVE_TEMPERATURE v'),1)
        self.assertEqual(output.count('ADAPTIVE_TEMPERATURE_TEMP'),report['commands_inserted'])


if __name__=='__main__':unittest.main()
