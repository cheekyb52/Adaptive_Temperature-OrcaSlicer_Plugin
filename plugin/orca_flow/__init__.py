# SPDX-License-Identifier: GPL-3.0-only
"""Adaptive Temperature: a main Orca tab and opt-in experimental G-code editing."""
import json
import threading
from copy import deepcopy
from .controller import DEFAULT_CONFIG, DEFAULT_RESPONSE, RESOURCES, curve_from_config, runtime, validated_config, reviewed_action

__version__ = '0.5.8'

try:
    import orca
except ModuleNotFoundError as exc:
    if exc.name != 'orca':
        raise
else:
    class FlowWorkspace(orca.pages.PagesPluginCapabilityBase):

        def get_name(self):
            return 'Adaptive Temperature'

        def get_default_config(self):
            return DEFAULT_CONFIG

        def on_load(self):
            # Invalid saved settings must surface instead of silently resetting bounds.
            runtime.set_config(json.loads(self.get_config()) or DEFAULT_CONFIG)

        def get_ui(self):
            from .burst_model import catalog
            from .tool_profiles import fresh_profile, PROFILE_FIELDS
            html=(RESOURCES/'panel.html').read_text(encoding='utf-8')
            schema={'fields':PROFILE_FIELDS,'templates':{str(i):fresh_profile(i,'') for i in range(4)}}
            return html.replace('/*HOTEND_PRESETS*/null',json.dumps(catalog()).replace('<','\\u003c')).replace(
                '/*TOOL_SCHEMA*/null',json.dumps(schema).replace('<','\\u003c')).replace(
                '/*DEFAULT_CONFIG*/null',json.dumps(DEFAULT_CONFIG).replace('<','\\u003c')).replace(
                '/*DEFAULT_RESPONSE*/null',json.dumps(DEFAULT_RESPONSE).replace('<','\\u003c'))

        def reply(self, command, **data):
            self.post_message({'command': command, **data})

        def on_message(self, message):
            if not isinstance(message, dict):
                return
            command = message.get('command')
            try:
                if command == 'refresh':
                    self.reply('state', **runtime.snapshot())
                elif command == 'save':
                    cfg = validated_config(message.get('config'))
                    with runtime.lock:
                        if runtime.job and runtime.job['status'] == 'running':
                            raise ValueError('Wait for the calibration to finish before changing settings')
                        if not self.save_config(json.dumps(cfg, allow_nan=False)):
                            raise RuntimeError('Orca could not save the plugin settings')
                        runtime.set_config(cfg)
                    self.reply('saved', **runtime.snapshot())
                elif command == 'use_latest_response':
                    from .predictive_thermal import profile_from_result
                    state=runtime.snapshot()
                    cfg=state['config']
                    latest=next((r for r in reversed(state['results']) if r.get('transitions') and
                        r.get('physical_tool')==cfg['physical_tool'] and r.get('profile_binding') and
                        all(cfg.get(k)==v for k,v in r['profile_binding'].items())),None)
                    if latest is not None:
                        if latest.get('physical_tool')!=cfg['physical_tool']:
                            raise ValueError('The latest response calibration is for another physical tool')
                        binding=latest.get('profile_binding')
                        if not binding or any(cfg.get(k)!=v for k,v in binding.items()):
                            raise ValueError('The recorded response does not match this setup; review its binding before using it')
                        response=profile_from_result(latest,binding)
                    else:
                        raise ValueError('No matching recorded response is available. Run a response calibration for this tool first.')
                    self.reply('response_profile',profile=response)
                elif command == 'copy_tool_calibration':
                    from .tool_profiles import copy_calibration
                    cfg=validated_config({**message.get('config',{}),'output_mode':'analysis'})
                    profile=copy_calibration(cfg,message.get('source_tool'),message.get('destination_tool'))
                    self.reply('tool_calibration_copy',profile=profile)
                elif command == 'import_settings':
                    cfg=validated_config({**message.get('config',{}),'output_mode':'analysis'})
                    self.reply('imported_settings',config=cfg)
                elif command == 'connect':
                    # The callback is on Orca's UI thread; network I/O never runs there.
                    from .moonraker_connection import connection_config
                    connection=connection_config(message.get('connection',runtime.snapshot()['config']))
                    threading.Thread(target=self.read_printer, args=(connection,), daemon=True).start()
                elif command == 'start':
                    action,point=message.get('action'),message.get('point_index')
                    with runtime.lock:
                        if runtime.job and runtime.job['status']=='running':
                            raise ValueError('A calibration is already running')
                        cfg=validated_config(message.get('config',runtime.config))
                        if action=='Loaded' and 'selected_point' in message:
                            selected=message['selected_point']
                            matches=[i for i,p in enumerate(cfg['points']) if isinstance(selected,dict) and
                                     p['temperature_c']==selected.get('temperature_c') and
                                     p['max_reliable_flow_mm3_s']==selected.get('max_reliable_flow_mm3_s')]
                            if len(matches)!=1:raise ValueError('The selected flow point does not match the current curve')
                            point=matches[0]
                        reviewed_action(cfg,action,point)  # Refuse invalid plans before saving or starting.
                        if not self.save_config(json.dumps(cfg,allow_nan=False)):
                            raise RuntimeError('Orca could not save the calibration settings; no test was started')
                        runtime.set_config(cfg)
                        runtime.start(action,point)
                    self.reply('started', **runtime.snapshot())
                elif command == 'stop':
                    runtime.request_stop()
                    self.reply('state', **runtime.snapshot())
                else:
                    raise ValueError('Unsupported action')
            except Exception as exc:
                self.reply('start_error' if command=='start' else 'error', message=str(exc))

        def read_printer(self, connection):
            try:
                self.reply('printer', sample=runtime.read_printer(connection))
            except Exception as exc:
                self.reply('connection_error', message=str(exc))

        def on_unload(self):
            runtime.request_stop()

    class FlowSliceAnalysis(orca.slicing.SlicingPipelineCapabilityBase):
        def get_name(self):
            return 'Adaptive Temperature — slice analysis'

        def execute(self, ctx):
            if ctx.step != orca.slicing.Step.psGCodePostProcess or not ctx.gcode_path:
                return orca.ExecutionResult.success()
            from .flow_evaluator import analyze_file
            cfg = runtime.snapshot()['config']
            _, report = analyze_file(ctx.gcode_path, {**cfg['motion'], 'logical_tool': cfg['logical_tool'],
                'calibration': {'material': cfg['material'], 'physical_head': cfg['physical_tool'],
                    'hotend': cfg['hotend'], 'hard_test_bounds': {
                        'max_commanded_flow_mm3_s': cfg['max_flow_mm3_s'],
                        'max_temperature_target_c': cfg['max_temperature_c'], 'automatically_expand': False},
                    'heating_response': None, 'cooling_response': None}})
            peak = report['target']['peak_planned_flow_mm3_s']
            try:
                report['curve_preview'] = curve_from_config(cfg).temperature_for_flow(peak)
            except ValueError as exc:
                report['curve_preview'] = {'error': str(exc)}
            report['profile_binding'] = {'material': cfg['material'], 'hotend': cfg['hotend'],
                'physical_tool': cfg['physical_tool'], 'note': 'Manually selected configuration; automatic preset matching is pending.'}
            output_message = 'G-code unchanged.'
            if cfg['output_mode'] == 'temperature_test':
                from .flow_scheduler import edit_working_file, EditingRefused
                try:
                    if getattr(ctx, 'host', 'File') != 'File':
                        raise EditingRefused('Adaptive temperature editing is restricted to file export; upload must use analysis mode')
                    edit = edit_working_file(ctx.gcode_path, cfg)
                    report.update(mode='experimental_temperature_test', gcode_modified=edit['gcode_modified'], edit=edit)
                    report['warnings'][0] = 'Adaptive temperature commands generated for file export.'
                    report['warnings'].extend(edit.get('notes', []))
                    output_message = f'Inserted {edit["commands_inserted"]} M104 commands.'
                except EditingRefused as exc:
                    report['edit'] = {'status': 'refused', 'reason': str(exc), 'gcode_modified': False}
                    with runtime.lock:
                        runtime.report = report
                    # An explicit editing request must not silently export unchanged output.
                    raise RuntimeError(f'Adaptive Temperature editing refused: {exc}') from exc
            with runtime.lock:
                runtime.report = report
            # No host UI call from a slicing worker: Orca can be waiting on it.
            return orca.ExecutionResult.success(
                f'Adaptive Temperature: peak {peak:.1f} mm³/s. Open the Adaptive Temperature tab. {output_message}',
                json.dumps(report, allow_nan=False))

    @orca.plugin
    class AdaptiveTemperaturePlugin(orca.base):
        def register_capabilities(self):
            orca.register_capability(FlowWorkspace)
            orca.register_capability(FlowSliceAnalysis)
