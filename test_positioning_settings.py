"""Check staged positioning settings without building or installing a release."""
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys
import types
import tempfile
import unittest
from unittest.mock import patch


class PositioningSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).parent
        cls.namespace='_staged_positioning_test'
        package=types.ModuleType(cls.namespace)
        package.__path__=[str(root/'plugin/orca_flow'),str(root)]
        sys.modules[cls.namespace]=package
        cls.controller=importlib.import_module(cls.namespace+'.controller')

    @classmethod
    def tearDownClass(cls):
        for name in list(sys.modules):
            if name==cls.namespace or name.startswith(cls.namespace+'.'):del sys.modules[name]

    def config(self):
        return {**deepcopy(self.controller.DEFAULT_CONFIG),'moonraker_url':'http://127.0.0.1:7125'}

    def test_default_and_legacy_settings_select_y_first(self):
        cfg=self.config()
        cfg.pop('positioning_order')
        self.assertEqual(self.controller.validated_config(cfg)['positioning_order'],'y_then_x')

    def test_each_axis_priority_is_saved_and_described(self):
        for order,description in (('y_then_x','move Y to travel center, then X'),('x_then_y','move X to travel center, then Y')):
            cfg=self.controller.validated_config({**self.config(),'positioning_order':order})
            self.assertEqual(cfg['positioning_order'],order)
            self.assertIn(description,self.controller.reviewed_action(cfg,'Position')['description'])

    def test_unknown_priority_is_rejected(self):
        for value in ('diagonal','',None,True):
            with self.subTest(value=value),self.assertRaises(ValueError):
                self.controller.validated_config({**self.config(),'positioning_order':value})

    def test_selected_priority_reaches_runner_request_without_starting_process(self):
        cfg={**self.config(),'positioning_order':'x_then_y'}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            session=root/'offline-session';session.mkdir()
            runtime=self.controller.Runtime(root)
            runtime.job={'status':'running'}
            with patch.object(self.controller.subprocess,'Popen',side_effect=RuntimeError('Offline fixture; do not launch')):
                runtime._run(cfg,{'action':'Position'},session)
            request=json.loads((root/'job-request.json').read_text(encoding='utf-8'))
            self.assertEqual(request['positioning_order'],'x_then_y')
            self.assertEqual(runtime.job['status'],'failed')

    def test_runner_failure_shows_recorded_cause_and_cleanup_failures(self):
        cases=[(1,[{'event':'session_error','data':'Invalid response extrusion segment'},{'event':'heater_off','data':{}}],
                'Calibration failed: Invalid response extrusion segment'),
               (0,[{'event':'heater_cleanup_blocked','data':'Printer not idle'}],
                'Heater shutdown could not be verified: Printer not idle')]
        for code,events,expected in cases:
            with self.subTest(code=code),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);session=root/'offline-session';session.mkdir()
                (session/'events.jsonl').write_text('\n'.join(json.dumps(e) for e in events),encoding='utf-8')
                runtime=self.controller.Runtime(root);runtime.job={'status':'running'}
                process=types.SimpleNamespace(wait=lambda:code)
                with patch.object(self.controller.subprocess,'Popen',return_value=process):
                    runtime._run(self.config(),{'action':'Position'},session)
                self.assertEqual(runtime.job['status'],'failed')
                self.assertIn(expected,runtime.job['error'])

    def test_nonzero_runner_exit_without_events_retains_diagnostic_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);session=root/'offline-session';session.mkdir()
            runtime=self.controller.Runtime(root);runtime.job={'status':'running'}
            with patch.object(self.controller.subprocess,'Popen',return_value=types.SimpleNamespace(wait=lambda:1)):
                runtime._run(self.config(),{'action':'Position'},session)
            self.assertEqual(runtime.job['status'],'failed')
            self.assertIn('code 1; no readable event record',runtime.job['error'])
