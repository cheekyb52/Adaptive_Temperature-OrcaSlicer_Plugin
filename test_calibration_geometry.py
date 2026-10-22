# SPDX-License-Identifier: GPL-3.0-only
import os
from pathlib import Path
import subprocess
import unittest


class CalibrationGeometryTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','PowerShell runner is Windows-only')
    def test_actual_geometry_functions_and_active_print_control_guard(self):
        script=Path(__file__).parent/'verify_calibration_geometry.ps1'
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(script)],
            capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('control guards: passed',result.stdout)


if __name__=='__main__':unittest.main()
