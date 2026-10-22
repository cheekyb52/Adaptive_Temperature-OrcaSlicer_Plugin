"""Concurrent local telemetry I/O and cleanup checks; no printer connection."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest


@unittest.skipUnless(os.name=='nt','PowerShell live logger is Windows-only')
class CalibrationLoggingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent
        cls.namespace='_calibration_logging_test'
        package=types.ModuleType(cls.namespace)
        package.__path__=[str(root/'plugin/orca_flow'),str(root)]
        sys.modules[cls.namespace]=package
        cls.controller=importlib.import_module(cls.namespace+'.controller')

    @classmethod
    def tearDownClass(cls):
        for name in list(sys.modules):
            if name==cls.namespace or name.startswith(cls.namespace+'.'):del sys.modules[name]

    def test_writer_can_be_tailed_by_real_runtime_snapshot_and_cleanup_survives_log_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            session=Path(directory)
            runtime=self.controller.Runtime(session)
            runtime.job={'status':'running','session':str(session)}
            process=subprocess.Popen(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
                '-File',str(Path(__file__).with_name('verify_calibration_logging.ps1')),
                '-SessionDirectory',str(session),'-Count','1500'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            snapshots=0
            deadline=time.monotonic()+30
            while process.poll() is None and time.monotonic()<deadline:
                path=session/'telemetry.jsonl'
                if path.exists():
                    # Exercise the exact UI snapshot reader while the PS handle
                    # remains open, and independently check read-sharing errors.
                    state=runtime.snapshot()
                    with path.open('rb') as stream:stream.read(256)
                    if state['job'].get('samples'):snapshots+=1
                time.sleep(.003)
            output,error=process.communicate(timeout=10)
            self.assertEqual(process.returncode,0,output+error)
            self.assertGreater(snapshots,1,'Concurrent UI reads were not exercised')
            self.assertIn('cleanup with broken event log: passed',output)
            raw=(session/'telemetry.jsonl').read_bytes()
            self.assertFalse(raw.startswith(b'\xef\xbb\xbf'))
            rows=[json.loads(line) for line in raw.decode('utf-8').splitlines()]
            self.assertEqual([r['index'] for r in rows],list(range(1500)))
            self.assertTrue(rows[0]['label'].endswith('\u00b0'))
            events=[json.loads(line) for line in (session/'events.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(events[-1]['event'],'session_complete')
            self.assertEqual(runtime.snapshot()['job']['samples'][-1]['time_s'],749.5)
