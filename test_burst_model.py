# SPDX-License-Identifier: GPL-3.0-only
from copy import deepcopy
import unittest

from burst_model import BurstBudget,burst_settings,catalog,sustainable_flow
from flow_temperature_curve import FlowPoint,FlowTemperatureCurve
from predictive_thermal import Response,SensorPredictor,choose_target


class BurstTests(unittest.TestCase):
    def curve(self):
        return FlowTemperatureCurve([FlowPoint(210,30),FlowPoint(245,75)],
            max_temperature_c=245,max_flow_mm3_s=75,flow_headroom_fraction=.1)

    def test_physical_geometry_not_equivalent_heat_transfer_sets_inventory(self):
        cfg={'burst_preset':'e3d-a1-hf-obxidian','burst_aggressiveness':50}
        policy=burst_settings(cfg,10)
        self.assertEqual(policy['geometry']['physical_length_mm'],27.2)
        self.assertGreater(policy['geometry']['effective_length_mm'],27.2)
        pika=burst_settings({'burst_preset':'lukes-lab-pika-u1'},10)
        self.assertAlmostEqual(pika['physical_inventory_proxy_mm3'],85.6280347644)
        self.assertFalse(pika['independently_validated'])

    def test_slider_has_monotonic_bounded_allowance_and_unknown_is_not_guessed(self):
        capacities=[burst_settings({'burst_preset':'lukes-lab-pika-u1','burst_aggressiveness':v},10)['estimated_reserve_capacity_mm3'] for v in (0,50,100)]
        self.assertEqual(capacities[0],0)
        self.assertAlmostEqual(capacities[2],capacities[1]*2)
        for invalid in (True,-1,101,float('nan')):
            with self.assertRaises(ValueError):burst_settings({'burst_aggressiveness':invalid},10)
        with self.assertRaises(ValueError):burst_settings({'burst_preset':'made-up-hotend'},10)
        self.assertEqual(burst_settings({'burst_preset':'none'},10)['estimated_reserve_capacity_mm3'],0)

    def test_unsupported_feedstock_and_multiple_paths_refused(self):
        for row in catalog()['hotends']:
            if row['filament_paths']!=1 or row['filament_diameter_mm']!=1.75:
                with self.subTest(row=row['id']),self.assertRaises(ValueError):
                    burst_settings({'burst_preset':row['id']},10)

    def test_repeated_bursts_share_reserve_and_refill_is_rate_limited(self):
        reserve=BurstBudget(40,4,40)
        self.assertEqual(reserve.advance(50,30,1.5),0)
        self.assertEqual(reserve.remaining_mm3,10)
        self.assertEqual(reserve.advance(50,30,1),10)
        self.assertEqual(reserve.remaining_mm3,0)
        reserve.advance(0,30,2)
        self.assertEqual(reserve.remaining_mm3,8)
        reserve.advance(29,30,2)
        self.assertEqual(reserve.remaining_mm3,8.5)
        reserve.advance(0,30,100)
        self.assertEqual(reserve.remaining_mm3,40)

    def test_forward_curve_obeys_headroom_and_no_cold_credit(self):
        curve=self.curve()
        self.assertEqual(sustainable_flow(curve,209),0)
        self.assertEqual(sustainable_flow(curve,210),27)
        self.assertEqual(sustainable_flow(curve,500),67.5)

    def test_more_reserve_permits_a_lower_target_without_changing_bounds(self):
        model=SensorPredictor(Response(0,.1),Response(0,.1),0,210,210)
        kwargs={'future_demands':[210]*4,'minimum_c':210,'maximum_c':245,'step_s':.25,
                'flow_forecast':[(.25,60)]*4,'curve':self.curve()}
        cautious=choose_target(model,burst_budget=BurstBudget(0,0),**kwargs)
        aggressive=choose_target(model,burst_budget=BurstBudget(43,4,43),**kwargs)
        self.assertLess(aggressive,cautious)
        self.assertTrue(210<=aggressive<=cautious<=245)


if __name__=='__main__':unittest.main()
