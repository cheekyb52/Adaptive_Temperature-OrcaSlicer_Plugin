# SPDX-License-Identifier: GPL-3.0-only
from copy import deepcopy
import math
import unittest

from create_gcode_edit_demo import DEMO_CONFIG, demo_gcode
from flow_evaluator import Move
from flow_scheduler import schedule_text, strip_generated
from predictive_scheduler import PathTimeline
from predictive_thermal import Response, SensorPredictor, choose_target, response_pair, profile_from_result
from test_fixtures import response_profile


def profile():
    return response_profile()


class PredictiveTests(unittest.TestCase):
    def test_heating_delay_and_response_are_separate(self):
        heat,cool=Response(2,3),Response(4,5)
        model=SensorPredictor(heat,cool,0,220,220)
        model.command(0,230)
        self.assertEqual(model.advance(2),220)
        self.assertAlmostEqual(model.advance(5),230-10/math.e)

    def test_cooling_uses_its_own_delay_and_time_constant(self):
        model=SensorPredictor(Response(1,2),Response(3,4),0,230,230)
        model.command(0,220)
        self.assertEqual(model.advance(3),230)
        self.assertAlmostEqual(model.advance(7),220+10/math.e)

    def test_cooling_is_requested_before_forecast_demand_drop(self):
        model=SensorPredictor(Response(1,1.5),Response(2.7,2.5),0,230,230)
        held=choose_target(model,[230]*24,210,245)
        anticipatory=choose_target(model,[230]*12+[210]*12,210,245)
        self.assertEqual(held,230)
        self.assertLess(anticipatory,230)
        self.assertGreaterEqual(anticipatory,210)
        # This command is issued at t=0, before the demand fall at t=6 s.
        model.command(0,anticipatory)
        self.assertLess(model.advance(6),230)

    def test_heating_anticipates_rising_demand_and_targets_are_bounded(self):
        model=SensorPredictor(Response(1.8,1.5),Response(2.7,2.5),0,210,210)
        self.assertGreater(choose_target(model,[210]*12+[230]*12,210,245),210)
        self.assertEqual(choose_target(model,[300]*24,210,245),245)
        self.assertEqual(choose_target(model,[100]*24,210,245),210)

    def test_learned_horizon_and_invalid_responses(self):
        heat,cool=response_pair(profile())
        self.assertAlmostEqual(heat.horizon_s,6.4935984103309865)
        self.assertAlmostEqual(cool.horizon_s,10.489330683884977)
        for delay,tau in ((-1,1),(1,0),(float('nan'),1),(1,float('inf'))):
            with self.subTest(delay=delay,tau=tau),self.assertRaises(ValueError):
                Response(delay,tau)
        invalid=profile()
        invalid['heating']['delay_s']=True
        with self.assertRaises(ValueError):response_pair(invalid)

    def test_recorded_fits_can_describe_another_hotend_without_brand_rules(self):
        result={'commanded_flow_mm3_s':8,'fan_fraction':.5,'transitions':[
            {'status':'candidate_fit','from_target_c':200,'to_target_c':215,
             'model':{'delay_s':3,'time_constant_s':4}},
            {'status':'candidate_fit','from_target_c':215,'to_target_c':200,
             'model':{'delay_s':5,'time_constant_s':9}}]}
        binding={'printer_name':'Another printer','physical_tool':0,'hotend':'Another hotend'}
        adopted=profile_from_result(result,binding)
        heating,cooling=response_pair(adopted)
        self.assertEqual(adopted['binding'],binding)
        self.assertEqual(adopted['observed_conditions']['flow_mm3_s'],8)
        self.assertFalse(adopted['independently_validated'])
        self.assertGreater(heating.horizon_s, response_pair(profile())[0].horizon_s)
        self.assertGreater(cooling.horizon_s, heating.horizon_s)
        result['transitions'].pop()
        with self.assertRaises(ValueError):profile_from_result(result,binding)

    def move(self,line=1,time=0,volume=2):
        move=Move(line,2,'Inner wall',(20,0,0),2,20,10,10,10,0,volume)
        move.start_speed=move.end_speed=0
        move.cruise_speed=10
        move.accel_time=move.cruise_time=move.decel_time=1
        move.time=time
        return move

    def test_adaptive_path_integrates_fractional_acceleration_and_travel(self):
        timeline=PathTimeline([self.move(),self.move(2,3,0)],{1})
        first=timeline.window(0,1)
        self.assertAlmostEqual(first['distance_mm'],5)
        self.assertAlmostEqual(first['flow_mm3_s'],.5)
        full=timeline.window(0,6)
        self.assertAlmostEqual(full['distance_mm'],40)
        self.assertAlmostEqual(full['flow_mm3_s'],2/6)
        self.assertEqual(timeline.window(3,3)['flow_mm3_s'],0)
        tail=timeline.window(5.5,6)
        self.assertAlmostEqual(tail['duration_s'],.5)

    def test_same_response_horizon_collects_more_path_at_higher_speed(self):
        def timeline(speed):
            m=self.move()
            m.length=speed*20
            m.delta=(m.length,0,0)
            m.start_speed=m.end_speed=m.cruise_speed=speed
            m.accel_time=m.decel_time=0
            m.cruise_time=20
            return PathTimeline([m],{1})
        horizon=response_pair(profile())[0].horizon_s
        slow,fast=timeline(50).window(0,horizon),timeline(200).window(0,horizon)
        self.assertAlmostEqual(fast['distance_mm']/slow['distance_mm'],4)
        self.assertAlmostEqual(fast['duration_s'],horizon)

    def test_hybrid_export_is_idempotent_and_preserves_original_commands(self):
        cfg=deepcopy(DEMO_CONFIG)
        cfg.update(scheduler='hybrid_predictive',thermal_response=profile(),
                   printer_name='Example printer',material='Example PLA',hotend='Example hotend / 0.8 mm')
        source=demo_gcode()
        edited,report=schedule_text(source,cfg)
        again,_=schedule_text(edited,cfg)
        self.assertEqual(edited,again)
        self.assertEqual(strip_generated(edited),source)
        self.assertEqual(report['cooling_hold_s'],0)
        self.assertEqual(report['algorithm'],'adaptive_path_average_predictive_sensor_response')
        self.assertFalse(report['burst_capacity_validated'])
        self.assertFalse(report['ready_for_printing'])
        self.assertTrue(report['prediction_samples'])

    def test_response_bound_to_other_setup_cannot_silently_apply(self):
        cfg=deepcopy(DEMO_CONFIG)
        cfg.update(scheduler='hybrid_predictive',thermal_response=profile(),printer_name='Other printer')
        with self.assertRaisesRegex(ValueError,'bound to another'):
            schedule_text(demo_gcode(),cfg)

    def test_average_mode_still_refuses_flow_above_absolute_envelope(self):
        cfg=deepcopy(DEMO_CONFIG)
        cfg.update(profile()['binding'])
        cfg.update(scheduler='hybrid_predictive',thermal_response=profile())
        cfg['headroom_percent']=90
        with self.assertRaisesRegex(ValueError,'operating limit'):
            schedule_text(demo_gcode(),cfg)


if __name__=='__main__':unittest.main()
