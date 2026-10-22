import math
import unittest

from analyze_loaded_temperature_session import fit_rows


class LoadedTemperatureTests(unittest.TestCase):
    def rows(self):
        out = []
        for i in range(200):
            t = i*0.5
            target = 220 if t < 25 or t >= 60 else 230
            if t < 25:
                temperature = 220
            elif t < 60:
                temperature = 220+10*(1-math.exp(-max(0,t-26)/3))
            else:
                temperature = 230-10*(1-math.exp(-max(0,t-61)/4))
            out.append(dict(stage='loaded_temperature_flow', time_s=t, temperature_c=temperature,
                            target_c=target, flow_mm3_s=15, fan_fraction=0, power_fraction=0.5))
        return out

    def test_heating_and_cooling_are_identified_separately(self):
        result = fit_rows(self.rows(), 15)
        self.assertEqual([t['status'] for t in result['transitions']], ['candidate_fit', 'candidate_fit'])
        self.assertAlmostEqual(result['transitions'][0]['model']['time_constant_s'], 3, delta=0.1)
        self.assertAlmostEqual(result['transitions'][1]['model']['time_constant_s'], 4, delta=0.1)
        self.assertFalse(result['ready_for_automatic_control'])

    def test_variable_or_unknown_flow_cannot_produce_a_constant_load_model(self):
        rows = self.rows()
        for row in rows[40:100]:
            row['flow_mm3_s'] = None
        with self.assertRaises(ValueError):
            fit_rows(rows, 15)

    def test_user_selected_temperature_sequence_is_not_u1_specific(self):
        rows=self.rows()
        for row in rows:
            row['target_c']-=10;row['temperature_c']-=10
        result=fit_rows(rows,15,(210,220,210))
        self.assertEqual(result['transitions'][0]['from_target_c'],210)
        self.assertEqual(result['transitions'][1]['to_target_c'],210)
        self.assertEqual([t['status'] for t in result['transitions']],['candidate_fit','candidate_fit'])

    def test_adaptive_phases_wait_out_initial_drift_and_accept_pid_ripple(self):
        rows=[]
        for i in range(420):
            t=i*.5
            target=230 if t<80 or t>=145 else 240
            if t<80:temperature=230+max(0,45-t)*.05
            elif t<145:temperature=230+10*(1-math.exp(-max(0,t-82)/4))
            else:temperature=240-10*(1-math.exp(-max(0,t-147)/5))
            temperature+=.75*math.sin(t*2*math.pi/4)
            rows.append(dict(stage='loaded_temperature_flow',time_s=t,temperature_c=temperature,
                target_c=target,flow_mm3_s=35,fan_fraction=0,power_fraction=.5))
        stability={'tolerance_percent':.5,'window_s':20}
        result=fit_rows(rows,35,(230,240,230),stability)
        self.assertEqual([r['status'] for r in result['transitions']],['candidate_fit','candidate_fit'])
        self.assertEqual(result['transitions'][0]['model']['stability_tolerance_percent'],.5)
        strict=fit_rows(rows,35,(230,240,230))
        self.assertEqual(strict['transitions'][0]['status'],'not_identified')
        # A larger band never waives continuing thermal drift.
        for row in rows:
            if 59<=row['time_s']<80:row['temperature_c']=230+(row['time_s']-69)*.05
        drifting=fit_rows(rows,35,(230,240,230),stability)
        self.assertEqual(drifting['transitions'][0]['status'],'not_identified')
        self.assertIn('drifting',drifting['transitions'][0]['reason'])

    def test_out_of_band_stable_offset_is_not_accepted(self):
        rows=self.rows()
        for row in rows:row['temperature_c']+=2
        result=fit_rows(rows,15,stability={'tolerance_percent':.5,'window_s':20})
        self.assertTrue(all(r['status']=='not_identified' for r in result['transitions']))


if __name__ == '__main__':
    unittest.main()
