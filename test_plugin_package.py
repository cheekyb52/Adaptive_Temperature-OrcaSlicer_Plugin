# SPDX-License-Identifier: GPL-3.0-only
"""Package, host bridge and control-boundary tests. No hardware is operated."""
from copy import deepcopy
import ast
import hashlib
import importlib
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from build_plugin import build, ROOT, PACKAGE
from test_fixtures import write_response_session


class Base:
    def get_config(self):
        return '{}'

    def save_config(self, config):
        return True

    def post_message(self, value):
        self.last_message = value


class PluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.wheel = build()
        with zipfile.ZipFile(cls.wheel) as archive:
            archive.extractall(cls.temp.name)
        sys.path.insert(0, cls.temp.name)
        cls.registered = []
        cls.orca = types.SimpleNamespace(
            script=types.SimpleNamespace(ScriptPluginCapabilityBase=Base),
            pages=types.SimpleNamespace(PagesPluginCapabilityBase=Base),
            slicing=types.SimpleNamespace(SlicingPipelineCapabilityBase=Base,
                Step=types.SimpleNamespace(psGCodePostProcess='post')),
            ExecutionResult=types.SimpleNamespace(success=lambda *args: args),
            plugin=lambda cls: cls, base=Base, register_capability=cls.registered.append)
        cls.host_patch = patch.dict(sys.modules, {'orca': cls.orca})
        cls.host_patch.start()
        cls.package = importlib.import_module(PACKAGE)
        cls.controller = importlib.import_module(PACKAGE+'.controller')

    @classmethod
    def tearDownClass(cls):
        cls.host_patch.stop()
        for key in list(sys.modules):
            if key == PACKAGE or key.startswith(PACKAGE+'.'):
                del sys.modules[key]
        sys.path.remove(cls.temp.name)
        cls.temp.cleanup()

    def config(self):
        cfg = deepcopy(self.controller.DEFAULT_CONFIG)
        from test_tool_profiles import configured
        cfg.update(configured())
        cfg.update(tool_mode='single',output_mode='analysis',tool_binding_confirmed=False,
                   calibration_flow_mm3_s=15,calibration_baseline_c=220,calibration_peak_c=230)
        cfg['moonraker_url'] = 'http://127.0.0.1:7125'
        return cfg

    def test_public_package_excludes_private_evidence_and_identifiers(self):
        with zipfile.ZipFile(self.wheel) as archive:
            self.assertNotIn(f'{PACKAGE}/resources/run_u1_calibration.ps1', archive.namelist())
            self.assertFalse(any('/example-' in name or '/sessions/' in name for name in archive.namelist()))
            for name in archive.namelist():
                content=archive.read(name).decode('utf-8').lower()
                from tools.check_public_release import PATTERNS
                for description, pattern in PATTERNS.items():
                    self.assertIsNone(pattern.search(archive.read(name)), f'{name} contains {description}')
            self.assertIn(f'{PACKAGE}/COPYING',archive.namelist())
            presets=json.loads(archive.read(f'{PACKAGE}/resources/meltcalc/hotends.json'))
            self.assertEqual(len(presets['hotends']),95)
            self.assertEqual(presets['license'],'CC-BY-4.0')
            self.assertIn(f'{PACKAGE}/resources/meltcalc/LICENSE-CONTENT.txt',archive.namelist())
            self.assertIn(f'{PACKAGE}/resources/run_moonraker_calibration.ps1',archive.namelist())
            self.assertIn(f'{PACKAGE}/resources/calibration_logging.ps1',archive.namelist())

    def test_public_defaults_are_unconfigured_and_cannot_edit(self):
        cfg=self.controller.validated_config(self.controller.DEFAULT_CONFIG)
        self.assertEqual(cfg['physical_tool'],0)
        self.assertEqual(cfg['moonraker_url'],'')
        self.assertFalse(cfg['tool_configured'])
        self.assertEqual(cfg['thermal_response']['source'],'unconfigured')
        from create_gcode_edit_demo import demo_gcode
        scheduler=importlib.import_module(PACKAGE+'.flow_scheduler')
        with self.assertRaises(ValueError):
            scheduler.schedule_text(demo_gcode(),{**cfg,'output_mode':'temperature_test','tool_binding_confirmed':True})

    def test_import_keeps_calibration_but_requires_review_and_save(self):
        page=self.package.FlowWorkspace()
        before=self.controller.runtime.snapshot()['config']
        imported={**self.config(),'output_mode':'temperature_test','tool_binding_confirmed':True}
        page.on_message({'command':'import_settings','config':imported})
        self.assertEqual(page.last_message['command'],'imported_settings')
        cfg=page.last_message['config']
        self.assertEqual(cfg['thermal_response'],imported['thermal_response'])
        self.assertEqual(cfg['output_mode'],'analysis')
        self.assertNotIn('tool_binding_confirmed',cfg)
        self.assertEqual(self.controller.runtime.snapshot()['config'],before)

    def test_uncalibrated_settings_can_be_saved_to_prepare_calibration(self):
        cfg=self.config()
        p=deepcopy(self.controller.DEFAULT_CONFIG['thermal_response'])
        p['source']='copied_response_candidate'
        p.pop('configuration_state',None)
        cfg.update(thermal_response=p,tool_configured=True,output_mode='temperature_test',tool_binding_confirmed=True)
        checked=self.controller.validated_config(cfg)
        self.assertFalse(checked['tool_configured'])

    def test_copied_placeholder_cannot_generate_temperature_output(self):
        from create_gcode_edit_demo import demo_gcode
        scheduler=importlib.import_module(PACKAGE+'.flow_scheduler')
        cfg=self.config()
        p=deepcopy(self.controller.DEFAULT_CONFIG['thermal_response'])
        p['source']='copied_response_candidate'
        p.pop('configuration_state',None)
        cfg.update(thermal_response=p,tool_configured=True,output_mode='temperature_test',tool_binding_confirmed=True)
        with self.assertRaisesRegex(ValueError,'no calibrated response'):
            scheduler.schedule_text(demo_gcode(),cfg)

    def test_public_markers_are_idempotent_and_unknown_versions_refused(self):
        from create_gcode_edit_demo import demo_gcode
        scheduler=importlib.import_module(PACKAGE+'.flow_scheduler')
        for planner in ('hybrid_predictive','peak_hold'):
            cfg={**self.config(),'scheduler':planner,'output_mode':'temperature_test','tool_binding_confirmed':True}
            output,report=scheduler.schedule_text(demo_gcode(),cfg)
            self.assertIn('; ADAPTIVE_TEMPERATURE v0.5.0 ',output)
            self.assertEqual(scheduler.strip_generated(output),demo_gcode())
            self.assertEqual(scheduler.schedule_text(output,cfg)[0],output)
            with self.assertRaisesRegex(ValueError,'Unrecognized'):
                scheduler.strip_generated(output.replace('ADAPTIVE_TEMPERATURE_TEMP v0.5.0','ADAPTIVE_TEMPERATURE_TEMP v1.0.0'))

    def test_wheel_internal_imports_are_relative_including_deferred_imports(self):
        with zipfile.ZipFile(self.wheel) as archive:
            modules={Path(name).stem for name in archive.namelist()
                     if name.startswith(PACKAGE+'/') and name.endswith('.py')}
            for name in archive.namelist():
                if not name.startswith(PACKAGE+'/') or not name.endswith('.py'):continue
                for node in ast.walk(ast.parse(archive.read(name).decode('utf-8'))):
                    if isinstance(node,ast.ImportFrom) and node.module in modules:
                        self.assertGreater(node.level,0,f'{name}:{node.lineno} must not import a workspace module')

    def test_clean_interpreter_export_with_old_package_namespace_present(self):
        """No workspace imports; exercise late save, adoption and export calls."""
        from create_gcode_edit_demo import demo_gcode
        program = r'''
import importlib,json,sys,types,zipfile
from pathlib import Path
wheel,folder,entry=sys.argv[1:]
with zipfile.ZipFile(wheel) as archive:
    metadata=archive.read(next(n for n in archive.namelist() if n.endswith('/METADATA'))).decode()
    assert 'Import-Name: '+entry+'\n' in metadata
    archive.extractall(folder)
assert entry!='orca_flow'
old=types.ModuleType('orca_flow');old.__path__=[]
sys.modules['orca_flow']=old
class Base:
    def get_config(self):return '{}'
    def save_config(self,value):return True
    def post_message(self,value):self.last=value
sys.modules['orca']=types.SimpleNamespace(
    pages=types.SimpleNamespace(PagesPluginCapabilityBase=Base),
    slicing=types.SimpleNamespace(SlicingPipelineCapabilityBase=Base,Step=types.SimpleNamespace(psGCodePostProcess='post')),
    ExecutionResult=types.SimpleNamespace(success=lambda *args:args),
    plugin=lambda cls:cls,base=Base,register_capability=lambda cls:None)
sys.path.insert(0,folder)
plugin=importlib.import_module(entry)
page=plugin.FlowWorkspace();page.on_load()
page.on_message({'command':'refresh'})
cfg=json.loads((Path(folder)/'test-settings.json').read_text());cfg.update(output_mode='temperature_test',tool_binding_confirmed=True)
page.on_message({'command':'save','config':cfg})
assert page.last['command']=='saved',page.last
page.on_message({'command':'use_latest_response'})
assert page.last['command']=='error' and 'matching' in page.last['message'],page.last
del sys.modules['orca_flow'] # unloading the previous wheel must not break this version
path=Path(folder)/'working.gcode'
result=plugin.FlowSliceAnalysis().execute(types.SimpleNamespace(step='post',gcode_path=str(path),host='File'))
assert 'Inserted' in result[0],result
assert 'ADAPTIVE_TEMPERATURE_TEMP' in path.read_text()
page.on_message({'command':'refresh'})
assert page.last['report']['edit']['mode']=='experimental_hybrid_predictive'
assert all(not k.startswith('orca_flow.') for k in sys.modules)
print('isolated upgrade/save/adoption/export passed')
'''
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            (folder/'working.gcode').write_text(demo_gcode(),encoding='utf-8')
            (folder/'test-settings.json').write_text(json.dumps(self.config()),encoding='utf-8')
            probe=folder/'check.py';probe.write_text(program,encoding='utf-8')
            result=subprocess.run([sys.executable,'-I',str(probe),str(self.wheel),str(folder),PACKAGE],
                                  capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('export passed',result.stdout)

    def test_curve_headroom_and_hard_caps(self):
        cfg = self.controller.validated_config(self.config())
        curve = self.controller.curve_from_config(cfg)
        self.assertEqual(curve.temperature_for_flow(45)['temperature_target_estimate_c'], 230)
        with self.assertRaises(ValueError):
            curve.temperature_for_flow(75)
        cfg['max_temperature_c'] = 229
        with self.assertRaises(ValueError):
            self.controller.validated_config(cfg)

    def test_invalid_numbers_and_non_monotonic_points_rejected(self):
        for field, value in [('max_temperature_c', float('nan')), ('headroom_percent', 100),
                             ('physical_tool', True), ('logical_tool', 1.5)]:
            cfg = self.config()
            cfg[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.controller.validated_config(cfg)
        cfg = self.config()
        cfg['points'][1]['max_reliable_flow_mm3_s'] = 1
        with self.assertRaises(ValueError):
            self.controller.validated_config(cfg)

    def test_selected_tool_and_user_defined_load_plan_are_independent(self):
        cfg = self.config()
        cfg['physical_tool'] = 3
        plan=self.controller.reviewed_action(cfg,'Loaded',0)
        self.assertEqual(plan['probe']['physical_tool'],3)
        self.assertEqual(plan['probe']['maximum_requested_flow_mm3_s'],30)
        cfg.update(calibration_flow_mm3_s=76)
        with self.assertRaisesRegex(ValueError,'outside'):
            self.controller.reviewed_action(cfg,'LoadedThermal')
        for action in ('HeaterOff', 'Thermal', 'anything'):
            with self.assertRaises(ValueError):
                self.controller.reviewed_action(self.config(), action)

    def test_supported_plan_has_no_xyz_and_is_bounded(self):
        plan = self.controller.reviewed_action(self.config(), 'Loaded', 2)['probe']
        self.assertFalse(plan['xyz_motion'])
        self.assertEqual(plan['physical_tool'], 2)
        self.assertEqual(plan['total_requested_duration_s'], 55)
        self.assertTrue(all(command.startswith('G1 E') for command in plan['commands']))

    def test_stability_percentage_migrates_validates_and_bounds_step(self):
        cfg=self.config()
        cfg.pop('calibration_tolerance_percent',None)
        self.assertEqual(self.controller.validated_config(cfg)['calibration_tolerance_percent'],.5)
        for invalid in (0,True,float('nan'),1.01):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.controller.validated_config({**cfg,'calibration_tolerance_percent':invalid})
        plan=self.controller.reviewed_action({**cfg,'calibration_tolerance_percent':.75},'LoadedThermal')
        self.assertIn('120 s per loaded phase',plan['description'])
        with self.assertRaisesRegex(ValueError,'four times'):
            self.controller.reviewed_action({**cfg,'calibration_peak_c':222,'calibration_tolerance_percent':1},'LoadedThermal')

    def test_reduced_user_bounds_prevent_thermal_run(self):
        cfg = self.config()
        cfg.update(max_temperature_c=220, points=cfg['points'][:2])
        with self.assertRaisesRegex(ValueError, 'outside'):
            self.controller.reviewed_action(cfg, 'LoadedThermal')

    def test_url_cannot_inject_command_or_credentials(self):
        for endpoint in ('file:///etc/passwd', 'http://user:password@printer',
                         'http://printer/path', 'http://printer?x=y'):
            cfg = self.config()
            cfg['moonraker_url'] = endpoint
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                self.controller.validated_config(cfg)

    def test_constructor_and_import_do_not_create_session(self):
        target = Path(self.temp.name)/'uncreated'
        runner = self.controller.Runtime(target)
        self.assertFalse(target.exists())
        self.assertIsNone(runner.snapshot()['job'])

    def test_active_run_blocks_setting_changes(self):
        runner = self.controller.Runtime()
        runner.job = {'status': 'running'}
        with self.assertRaisesRegex(ValueError, 'finish'):
            runner.set_config(self.config())

    def test_start_applies_current_unsaved_test_settings(self):
        runtime=self.controller.runtime;before=runtime.snapshot()['config']
        page=self.package.FlowWorkspace()
        cfg=self.config();cfg.update(calibration_flow_mm3_s=30,calibration_baseline_c=220,calibration_peak_c=230)
        try:
            with patch.object(page,'save_config',return_value=True) as save,patch.object(runtime,'start') as start:
                page.on_message({'command':'start','action':'LoadedThermal','config':cfg})
                self.assertEqual(page.last_message['command'],'started')
                self.assertEqual(json.loads(save.call_args.args[0])['calibration_flow_mm3_s'],30)
                self.assertEqual(runtime.snapshot()['config']['calibration_flow_mm3_s'],30)
                start.assert_called_once_with('LoadedThermal',None)
        finally:runtime.set_config(before)

    def test_invalid_response_flow_is_rejected_before_save_or_start(self):
        runtime=self.controller.runtime;before=runtime.snapshot()['config']
        page=self.package.FlowWorkspace();cfg=self.config();cfg['calibration_flow_mm3_s']=35
        with patch.object(page,'save_config') as save,patch.object(runtime,'start') as start:
            page.on_message({'command':'start','action':'LoadedThermal','config':cfg})
            self.assertEqual(page.last_message['command'],'start_error')
            self.assertIn('34.2',page.last_message['message'])
            save.assert_not_called();start.assert_not_called()
        self.assertEqual(runtime.snapshot()['config'],before)

    def test_start_keeps_selected_point_when_unsaved_rows_are_reordered(self):
        runtime=self.controller.runtime;before=runtime.snapshot()['config']
        page=self.package.FlowWorkspace();cfg=self.config();cfg['points'].reverse()
        selected=deepcopy(cfg['points'][0])
        try:
            with patch.object(page,'save_config',return_value=True),patch.object(runtime,'start') as start:
                page.on_message({'command':'start','action':'Loaded','point_index':0,'selected_point':selected,'config':cfg})
                self.assertEqual(page.last_message['command'],'started')
                start.assert_called_once_with('Loaded',3)
                self.assertEqual(runtime.snapshot()['config']['points'][3],selected)
        finally:runtime.set_config(before)

    def test_failed_settings_save_never_starts_calibration(self):
        runtime=self.controller.runtime;before=runtime.snapshot()['config']
        page=self.package.FlowWorkspace();cfg=self.config();cfg['calibration_flow_mm3_s']=30
        with patch.object(page,'save_config',return_value=False),patch.object(runtime,'start') as start:
            page.on_message({'command':'start','action':'LoadedThermal','config':cfg})
            self.assertEqual(page.last_message['command'],'start_error')
            self.assertIn('no test was started',page.last_message['message'])
            start.assert_not_called()
        self.assertEqual(runtime.snapshot()['config'],before)

    def test_active_calibration_rejects_another_start_before_saving(self):
        runtime=self.controller.runtime;previous=runtime.job
        page=self.package.FlowWorkspace();cfg=self.config()
        try:
            runtime.job={'status':'running'}
            with patch.object(page,'save_config') as save,patch.object(runtime,'start') as start:
                page.on_message({'command':'start','action':'LoadedThermal','config':cfg})
                self.assertEqual(page.last_message['command'],'start_error')
                self.assertIn('already running',page.last_message['message'])
                save.assert_not_called();start.assert_not_called()
        finally:runtime.job=previous

    def test_cleanup_failure_cannot_be_reported_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)/'sessions'/'test'
            session.mkdir(parents=True)
            rows = [{'event': 'session_complete'}, {'event': 'heater_cleanup_blocked'}]
            (session/'events.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
            runner = self.controller.Runtime()
            runner.job = {'status': 'running'}
            with patch.object(self.controller.subprocess, 'Popen') as process:
                process.return_value.wait.return_value = 0
                runner._run(self.config(), {'action': 'Position'}, session)
            self.assertEqual(runner.job['status'], 'failed')
            self.assertIn('Heater shutdown could not be verified', runner.job['error'])
            args = process.call_args.args[0]
            request = json.loads(Path(args[args.index('-RequestFile')+1]).read_text())
            self.assertEqual(request['physical_tool'], 2)
            self.assertEqual(request['action'], 'Position')
            self.assertTrue(args[args.index('-File')+1].endswith('plugin_calibration_entry.ps1'))
            self.assertFalse(process.call_args.kwargs['shell'])

    def test_capabilities_register_and_post_hook_does_not_modify_gcode(self):
        self.package.AdaptiveTemperaturePlugin().register_capabilities()
        self.assertEqual(len(self.registered), 2)
        self.package.FlowWorkspace().on_load()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'part.gcode'
            path.write_text('G90\nM83\nT2\nM204 S1000\n;TYPE:Internal infill\nG1 X100 E10 F6000\n')
            before = hashlib.sha256(path.read_bytes()).digest()
            result = self.package.FlowSliceAnalysis().execute(types.SimpleNamespace(step='post',gcode_path=str(path)))
            self.assertIn('G-code unchanged', result[0])
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)
            report = self.controller.runtime.snapshot()['report']
            self.assertFalse(report['ready_for_temperature_control'])
            self.assertIn('curve_preview', report)

    def test_workspace_is_main_page_and_messages_use_page_bridge(self):
        page = self.package.FlowWorkspace()
        self.assertEqual(page.get_name(), 'Adaptive Temperature')
        self.assertIn('Slice analysis and G-code output', page.get_ui())
        self.assertFalse(hasattr(page, 'window'))
        page.on_message({'command': 'refresh'})
        self.assertEqual(page.last_message['command'], 'state')

    def test_output_does_not_depend_on_removed_confirmation_flags(self):
        from create_gcode_edit_demo import demo_gcode
        cfg=self.config()
        cfg.update(output_mode='temperature_test',tool_binding_confirmed=False,tool_configured=False)
        checked=self.controller.validated_config(cfg)
        self.assertTrue(checked['tool_configured'])
        self.assertNotIn('tool_binding_confirmed',checked)
        scheduler=importlib.import_module(PACKAGE+'.flow_scheduler')
        _,report=scheduler.schedule_text(demo_gcode(),checked)
        self.assertGreater(report['commands_inserted'],0)

    def test_check_uses_unsaved_connection_and_only_get_requests(self):
        calls=[]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append(('GET',self.path))
                if self.path=='/server/info':result={'klippy_state':'ready','moonraker_version':'fixture'}
                elif 'print_stats' in self.path:
                    result={'status':{'print_stats':{'state':'printing'},'virtual_sdcard':{'is_active':True},'pause_resume':{'is_paused':False}}}
                else:result={'eventtime':1,'status':{'toolhead':{'extruder':'extruder3'},'extruder3':{'temperature':22,'target':0,'power':0},'fan':{'speed':0}}}
                body=json.dumps({'result':result}).encode();self.send_response(200);self.end_headers();self.wfile.write(body)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            runner=self.controller.Runtime()
            before=runner.snapshot()['config']
            connection={'moonraker_ip':'127.0.0.1','moonraker_port':server.server_port,'heater_name':'extruder3','fan_name':'fan'}
            sample=runner.read_printer(connection)
            self.assertTrue(sample['connected'])
            self.assertEqual(sample['physical_heater'],'extruder3')
            self.assertEqual(sample['print_state'],'printing')
            self.assertEqual(runner.snapshot()['config'],before)
            self.assertEqual(calls[0],('GET','/server/info'))
            self.assertEqual(len(calls),3)
            self.assertTrue(all(method=='GET' for method,_ in calls))
        finally:server.shutdown();server.server_close();worker.join(3)

    def test_reachable_server_is_reported_even_with_no_ready_tool(self):
        runner=self.controller.Runtime()
        with patch.object(self.controller,'MoonrakerReader') as reader:
            reader.return_value.server_info.return_value={'klippy_state':'shutdown'}
            sample=runner.read_printer({'moonraker_ip':'127.0.0.1'})
            self.assertTrue(sample['connected'])
            self.assertEqual(sample['klippy_state'],'shutdown')
            reader.return_value.sample.assert_not_called()
            reader.return_value.server_info.return_value={'klippy_state':'ready'}
            reader.return_value.sample.side_effect=RuntimeError('Unknown heater')
            sample=runner.read_printer({'moonraker_ip':'127.0.0.1'})
            self.assertTrue(sample['connected'])
            self.assertIn('Unknown heater',sample['telemetry_error'])

    def test_response_adoption_keeps_binding_and_requires_matching_record(self):
        page=self.package.FlowWorkspace()
        runtime=self.controller.runtime
        runtime.set_config(self.config())
        with runtime.lock:runtime.results=[]
        page.on_message({'command':'use_latest_response'})
        self.assertEqual(page.last_message['command'],'error')
        self.assertIn('matching',page.last_message['message'])
        with tempfile.TemporaryDirectory() as directory:
            record=importlib.import_module(PACKAGE+'.analyze_loaded_temperature_session').analyze_session(
                write_response_session(directory))
        record['profile_binding']=deepcopy(self.config()['thermal_response']['binding'])
        with runtime.lock:runtime.results=[record]
        try:
            page.on_message({'command':'use_latest_response'})
            self.assertEqual(page.last_message['command'],'response_profile')
            cfg=self.config();cfg['material']='Different PLA'
            runtime.set_config(cfg)
            page.on_message({'command':'use_latest_response'})
            self.assertEqual(page.last_message['command'],'error')
            self.assertIn('matching',page.last_message['message'])
        finally:
            with runtime.lock:runtime.results=[]
            runtime.set_config(self.config())

    def test_tool_profiles_roundtrip_and_copy_derive_readiness(self):
        cfg=self.config();cfg['tool_mode']='toolchanger'
        profile_module=importlib.import_module(PACKAGE+'.tool_profiles')
        cfg['tool_profiles']['0']=profile_module.fresh_profile(0,cfg['printer_name'])
        checked=self.controller.validated_config(cfg)
        self.assertFalse(checked['tool_profiles']['0']['tool_configured'])
        self.assertEqual(checked['tool_profiles']['2']['heater_name'],'extruder2')
        page=self.package.FlowWorkspace()
        page.on_message({'command':'copy_tool_calibration','config':checked,'source_tool':2,'destination_tool':0})
        self.assertEqual(page.last_message['command'],'tool_calibration_copy')
        copied=page.last_message['profile']
        self.assertEqual(copied['heater_name'],'extruder')
        self.assertEqual(copied['thermal_response']['binding']['physical_tool'],0)
        self.assertTrue(copied['tool_configured'])

    def test_post_hook_edits_export_and_blocks_upload(self):
        from create_gcode_edit_demo import demo_gcode
        cfg = self.config()
        cfg.update(output_mode='temperature_test', tool_binding_confirmed=True)
        self.controller.runtime.set_config(cfg)
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/'working.gcode'
                path.write_text(demo_gcode())
                result = self.package.FlowSliceAnalysis().execute(types.SimpleNamespace(step='post',gcode_path=str(path),host='File'))
                self.assertIn('Inserted', result[0])
                self.assertIn('ADAPTIVE_TEMPERATURE_TEMP', path.read_text())
                self.assertEqual(self.controller.runtime.snapshot()['report']['edit']['mode'],'experimental_hybrid_predictive')
                before = path.read_bytes()
                with self.assertRaisesRegex(RuntimeError, 'restricted to file export'):
                    self.package.FlowSliceAnalysis().execute(types.SimpleNamespace(step='post',gcode_path=str(path),host='Moonraker'))
                self.assertEqual(path.read_bytes(), before)
        finally:
            self.controller.runtime.set_config(self.config())

    def test_packaged_analyzer_replays_synthetic_completed_session(self):
        module = importlib.import_module(PACKAGE+'.analyze_loaded_temperature_session')
        with tempfile.TemporaryDirectory() as directory:
            report = module.analyze_session(write_response_session(directory))
        self.assertEqual(len(report['transitions']), 2)
        self.assertFalse(report['ready_for_automatic_control'])
        self.assertTrue(all(t['status']=='candidate_fit' for t in report['transitions']))
        self.assertAlmostEqual(report['transitions'][0]['model']['time_constant_s'], 3, delta=.1)
        self.assertAlmostEqual(report['transitions'][1]['model']['time_constant_s'], 4, delta=.1)


if __name__ == '__main__':
    unittest.main()
