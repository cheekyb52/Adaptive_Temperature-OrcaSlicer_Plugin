import os
import json
import math
from pathlib import Path
import subprocess
import tempfile
import unittest


class ResponseProtocolTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','PowerShell runner is Windows-only')
    def test_flow_load_resplitting_and_fractional_static_durations(self):
        from static_probe import loaded_response_probe
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            fixture=root/'fixtures.json';report=root/'report.json'
            fixture.write_text(json.dumps([loaded_response_probe(q,240) for q in (2,2.01,16,16.01,23,50,99.99,100)]),encoding='utf-8')
            result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
                '-File',str(Path(__file__).with_name('audit_runner_rounding.ps1')),
                '-FixtureFile',str(fixture),'-OutputFile',str(report)],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            data=json.loads(report.read_text(encoding='utf-8-sig'))
            self.assertFalse(data['failures'])
            self.assertEqual(data['max_loaded_volume_difference_mm3'],0)
            self.assertEqual(data['static_duration_cases'],32)

    @unittest.skipUnless(os.name=='nt','PowerShell runner is Windows-only')
    def test_response_segments_for_fractional_flows_and_firmware_limits(self):
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
            '-File',str(Path(__file__).with_name('verify_response_segments.ps1'))],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('3 firmware distance limits: passed',result.stdout)

    @unittest.skipUnless(os.name=='nt','PowerShell runner is Windows-only')
    def test_actual_protocol_in_offline_printer_simulator(self):
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
            '-File',str(Path(__file__).with_name('verify_calibration_response.ps1'))],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('timeout and stop: passed',result.stdout)
        from analyze_loaded_temperature_session import fit_rows
        rows=json.loads(next(line.removeprefix('SIMULATION:') for line in result.stdout.splitlines() if line.startswith('SIMULATION:')))
        report=fit_rows(rows,10*math.pi*(1.75/2)**2,(230,240,230),{'tolerance_percent':.5,'window_s':20})
        self.assertEqual([t['status'] for t in report['transitions']],['candidate_fit','candidate_fit'],report)
