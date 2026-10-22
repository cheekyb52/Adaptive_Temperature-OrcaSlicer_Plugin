# SPDX-License-Identifier: GPL-3.0-only
"""UI-independent settings, offline plans and the Windows Moonraker adapter.

Nothing connects, heats, moves or creates a process on import or construction.
Hardware actions are only entered through an explicit UI action.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import os
import re
from pathlib import Path
import subprocess
import threading
import uuid

from .flow_temperature_curve import FlowPoint, FlowTemperatureCurve
from .static_probe import loaded_response_probe
from .calibration_logger import MoonrakerReader
from .analyze_loaded_session import analyze_session as analyze_load
from .analyze_loaded_temperature_session import analyze_session as analyze_temperature
from .tool_profiles import PROFILE_FIELDS, profile_config, fresh_profile
from .configuration_defaults import default_configuration
from .moonraker_connection import connection_config

RESOURCES = Path(__file__).parent/'resources'
DEFAULT_CONFIG=default_configuration()
DEFAULT_RESPONSE=deepcopy(DEFAULT_CONFIG['thermal_response'])


def finite(value, name):
    if isinstance(value, bool):
        raise ValueError(f'{name} must be a finite number')
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number')
    return value


def validated_config(raw,_profile=False):
    if not isinstance(raw, dict):
        raise ValueError('Settings must be an object')
    cfg = deepcopy(DEFAULT_CONFIG)
    cfg.update({key: deepcopy(value) for key, value in raw.items() if key in cfg})
    if cfg['tool_mode'] not in ('single','toolchanger') or isinstance(cfg['tool_count'],bool) or not isinstance(cfg['tool_count'],int) or not 1<=cfg['tool_count']<=4:
        raise ValueError('Choose single tool or a toolchanger with one to four tools')
    if not isinstance(cfg['calibration_origin'],dict):raise ValueError('Invalid calibration provenance')
    if not isinstance(cfg['heater_name'],str) or not re.fullmatch(r'extruder(?:[1-9]\d*)?',cfg['heater_name']):
        raise ValueError('Use a Klipper extruder heater name, such as extruder or extruder2')
    if not isinstance(cfg['fan_name'],str) or not re.fullmatch(r'fan|fan_generic [A-Za-z0-9_]+',cfg['fan_name']):
        raise ValueError('Use fan or fan_generic followed by its configured name')
    if cfg['tool_change_style'] not in ('active_only','t_command','u1_macro'):
        raise ValueError('Unsupported tool selection style')
    if cfg['positioning_order'] not in ('y_then_x','x_then_y'):
        raise ValueError('Choose Y first or X first for calibration positioning')
    for key in ('calibration_flow_mm3_s','calibration_baseline_c','calibration_peak_c'):
        cfg[key]=finite(cfg[key],key)
        if cfg[key]<=0:raise ValueError('Calibration flow and temperatures must be positive')
    cfg['calibration_tolerance_percent']=finite(cfg['calibration_tolerance_percent'],'stability tolerance')
    if not 0.05<=cfg['calibration_tolerance_percent']<=1:
        raise ValueError('Stability tolerance must be between 0.05 and 1 percent of target temperature')
    for key in ('printer_name', 'material', 'filament_type', 'hotend', 'moonraker_url'):
        if not isinstance(cfg[key], str) or len(cfg[key]) > 512:
            raise ValueError(f'Invalid {key}')
        cfg[key] = cfg[key].strip()
    cfg.update(connection_config(raw))
    for key in ('physical_tool', 'logical_tool'):
        value = finite(cfg[key], key)
        if value != int(value) or not 0 <= value <= 31:
            raise ValueError(f'{key} must be an integer from 0 to 31')
        cfg[key] = int(value)
    if cfg['output_mode'] not in ('analysis', 'temperature_test'):
        raise ValueError('Invalid output mode')
    if cfg['scheduler'] not in ('hybrid_predictive','peak_hold'):
        raise ValueError('Unknown temperature scheduler')
    from .predictive_thermal import response_pair, response_is_configured
    response_pair(cfg['thermal_response'])
    from .burst_model import burst_settings
    burst_settings(cfg,response_pair(cfg['thermal_response'])[1].horizon_s)
    # Readiness is derived from calibration data, never a manual checkbox.
    binding=cfg['thermal_response'].get('binding',{})
    cfg['tool_configured']=response_is_configured(cfg['thermal_response']) and all(cfg.get(k)==v for k,v in binding.items())
    cfg['nozzle_diameter_mm'] = finite(cfg['nozzle_diameter_mm'], 'nozzle diameter')
    if cfg['nozzle_diameter_mm'] <= 0:
        raise ValueError('Nozzle diameter must be positive')
    for key in ('heating_lead_s', 'cooling_hold_s'):
        cfg[key] = finite(cfg[key], key)
        if not 0 <= cfg[key] <= 60:
            raise ValueError('Lead/hold times must be between 0 and 60 seconds')
    for key in ('min_temperature_c', 'max_temperature_c', 'max_flow_mm3_s', 'headroom_percent'):
        cfg[key] = finite(cfg[key], key)
    if not 0 < cfg['min_temperature_c'] < cfg['max_temperature_c'] or not 0 <= cfg['headroom_percent'] < 100:
        raise ValueError('Check the temperature range and flow headroom')
    if not isinstance(cfg['points'], list) or not 2 <= len(cfg['points']) <= 12:
        raise ValueError('Use between 2 and 12 flow points')
    cfg['points'] = [{'temperature_c': finite(p['temperature_c'], 'temperature'),
                      'max_reliable_flow_mm3_s': finite(p['max_reliable_flow_mm3_s'], 'flow')}
                     for p in cfg['points']]
    cfg['points'].sort(key=lambda p: p['temperature_c'])
    if cfg['points'][0]['temperature_c'] != cfg['min_temperature_c']:
        raise ValueError('The first curve point must match the minimum printing temperature')
    curve_from_config(cfg)  # Checks monotonic points, finite ceilings and interpolation.
    from .flow_evaluator import options
    if not isinstance(cfg['motion'], dict):
        raise ValueError('Motion settings must be an object')
    cfg['motion'] = {key: finite(cfg['motion'].get(key, default), key)
                     for key, default in DEFAULT_CONFIG['motion'].items()}
    options(cfg['motion'])
    if not _profile:
        bank=cfg['tool_profiles']
        if not isinstance(bank,dict) or len(bank)>4:raise ValueError('Use at most four tool calibration records')
        normalized={}
        for key,profile in bank.items():
            if not isinstance(key,str) or not key.isdigit() or not 0<=int(key)<4 or not isinstance(profile,dict):
                raise ValueError('Invalid tool profile slot')
            candidate={**cfg,**profile,'tool_mode':'single','tool_profiles':{},'output_mode':'analysis'}
            candidate=validated_config(candidate,True)
            if candidate['physical_tool']!=int(key):raise ValueError('Tool record does not match its physical slot')
            normalized[key]={k:deepcopy(candidate[k]) for k in PROFILE_FIELDS}
        if cfg['tool_mode']=='toolchanger':
            if not 0<=cfg['physical_tool']<cfg['tool_count']:raise ValueError('Selected tool is outside the toolchanger slots')
            normalized[str(cfg['physical_tool'])]={k:deepcopy(cfg[k]) for k in PROFILE_FIELDS}
        cfg['tool_profiles']=normalized
    return cfg


def curve_from_config(cfg):
    return FlowTemperatureCurve([FlowPoint(**p) for p in cfg['points']],
        max_temperature_c=cfg['max_temperature_c'], max_flow_mm3_s=cfg['max_flow_mm3_s'],
        flow_headroom_fraction=cfg['headroom_percent']/100, interpolation=cfg['interpolation'])


def reviewed_action(cfg, action, point_index=None):
    """Prepare an explicit, bounded run for the selected Moonraker tool."""
    cfg = validated_config(cfg)
    if not cfg['moonraker_url']:
        raise ValueError('Enter the Moonraker IP address and port first')
    if action == 'Position':
        first,second=('Y','X') if cfg['positioning_order']=='y_then_x' else ('X','Y')
        return {'action': action, 'description': f"Home if needed, select T{cfg['physical_tool']} using the configured mapping, verify Z clearance, move {first} to travel center, then {second}, then Z100 mm. Each axis leg completes before the next begins."}
    if action == 'Loaded':
        if isinstance(point_index, bool) or not isinstance(point_index, int) or not 0 <= point_index < len(cfg['points']):
            raise ValueError('Select a flow point')
        point = cfg['points'][point_index]
        t, q = point['temperature_c'], point['max_reliable_flow_mm3_s']
        probe=loaded_response_probe(q,t)
        probe['physical_tool']=cfg['physical_tool']
        return {'action': action, 'description': f"T{cfg['physical_tool']} at {t:g} °C; 25% flow 5 s, 50% 5 s, full {q:g} mm³/s 30 s, 25% 15 s; then recovery and heater off.",
                'probe':probe}
    if action == 'LoadedThermal':
        low,high,flow=cfg['calibration_baseline_c'],cfg['calibration_peak_c'],cfg['calibration_flow_mm3_s']
        if not cfg['min_temperature_c']<=low<high<=cfg['max_temperature_c'] or high-low<2 or flow>cfg['max_flow_mm3_s']:
            raise ValueError('The response test is outside the selected tool bounds; use a temperature step of at least 2 C')
        from .burst_model import sustainable_flow
        limit=sustainable_flow(curve_from_config(cfg),low)
        if flow>limit:
            raise ValueError(f'Response test flow {flow:g} mm³/s exceeds the {limit:g} mm³/s limit at {low:g} °C with {cfg["headroom_percent"]:g}% headroom')
        tolerance=cfg['calibration_tolerance_percent']
        if high-low<4*high*tolerance/100:
            raise ValueError('Use a temperature step at least four times the stability tolerance band, or lower the tolerance')
        return {'action': action, 'description': f"T{cfg['physical_tool']}: constant {flow:g} mm³/s; stabilize at {low:g} °C, heat to {high:g} °C, cool to {low:g} °C. ±{tolerance:g}% of target for 20 s, drift ≤0.03 °C/s; maximum 120 s per loaded phase, then recovery and heater off."}
    raise ValueError('Unsupported calibration action')


class Runtime:
    def __init__(self, data_dir=None):
        self.lock = threading.RLock()
        self.config = deepcopy(DEFAULT_CONFIG)
        self.job = None
        self.report = None
        self.results = []
        self.worker = None
        # Stay inside the installed plugin tree, an Orca-owned filesystem root.
        self.data_dir = Path(data_dir) if data_dir else Path(__file__).parent/'sessions'

    def snapshot(self):
        with self.lock:
            result = {'config': deepcopy(self.config), 'job': deepcopy(self.job),
                      'report': deepcopy(self.report), 'results': deepcopy(self.results)}
        if result['job'] and result['job'].get('session'):
            path = Path(result['job']['session'])/'telemetry.jsonl'
            if path.exists():
                try:
                    # Read only the tail; a final line may be mid-write.
                    with path.open('rb') as stream:
                        stream.seek(max(0, path.stat().st_size-1500000))
                        lines = stream.read().decode('utf-8-sig', errors='replace').splitlines()
                    samples = []
                    for line in lines:
                        try:
                            row = json.loads(line.lstrip('\ufeff'))
                            samples.append({k: row.get(k) for k in ('time_s', 'stage', 'response_phase', 'temperature_c', 'target_c', 'power_fraction', 'flow_mm3_s')})
                        except (ValueError, TypeError):
                            continue
                    result['job']['samples'] = samples[-1200:]
                except OSError:
                    pass
        return result

    def set_config(self, cfg):
        validated = validated_config(cfg)
        with self.lock:
            if self.job and self.job['status'] == 'running':
                raise ValueError('Wait for the calibration to finish before changing settings')
            self.config = validated

    def read_printer(self, connection=None):
        with self.lock:
            cfg = connection_config(deepcopy(self.config) if connection is None else connection)
        if not cfg['moonraker_url']:raise ValueError('Enter the printer IP address')
        reader = MoonrakerReader(cfg['moonraker_url'])
        try:info=reader.server_info()
        except Exception as exc:
            raise RuntimeError(f"Could not reach Moonraker at {cfg['moonraker_url']}: {exc}") from exc
        sample={'connected':True,'moonraker_url':cfg['moonraker_url'],
                'klippy_state':info['klippy_state'],'moonraker_version':info.get('moonraker_version')}
        if info['klippy_state']=='ready':
            try:sample.update(reader.sample(cfg['heater_name'],cfg['fan_name']))
            except Exception as exc:sample['telemetry_error']=str(exc)
        return sample

    def start(self, action, point_index=None):
        with self.lock:
            if self.job and self.job['status'] == 'running':
                raise ValueError('A calibration is already running')
            if os.name != 'nt':
                raise ValueError('Live Moonraker calibration requires Windows PowerShell in this build; offline features work on other platforms')
            cfg = deepcopy(self.config)
            plan = reviewed_action(cfg, action, point_index)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
            session = self.data_dir/f'{stamp}-{uuid.uuid4().hex[:8]}-{action}'
            session.mkdir(parents=True, exist_ok=False)
            self.job = {'status': 'running', 'action': action, 'description': plan['description'],
                        'session': str(session), 'physical_tool':cfg['physical_tool'],'stop_requested': False}
            self.worker = threading.Thread(target=self._run, args=(cfg, plan, session), daemon=True)
            try:
                self.worker.start()
            except BaseException:
                self.job['status'] = 'failed'
                self.job['error'] = 'Worker could not start; no printer command was sent'
                raise

    def request_stop(self):
        with self.lock:
            if self.job and self.job['status'] == 'running':
                (Path(self.job['session'])/'STOP').touch(exist_ok=True)
                self.job['stop_requested'] = True

    def _run(self, cfg, plan, session):
        try:
            script = RESOURCES/'plugin_calibration_entry.ps1'
            shell = Path(os.environ.get('SystemRoot', r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
            # Orca saves exact process targets. A random session path in the
            # command line would require a different grant on every retry.
            request = session.parent/'job-request.json'
            request.write_text(json.dumps({'action': plan['action'], 'physical_tool':cfg['physical_tool'],
                'heater_name':cfg['heater_name'],'fan_name':cfg['fan_name'],'tool_change_style':cfg['tool_change_style'],
                'positioning_order':cfg['positioning_order'],
                'min_temperature_c':cfg['min_temperature_c'],'max_temperature_c':cfg['max_temperature_c'],
                'max_flow_mm3_s':cfg['max_flow_mm3_s'],'flow_mm3_s':cfg['calibration_flow_mm3_s'],
                'baseline_c':cfg['calibration_baseline_c'],'peak_c':cfg['calibration_peak_c'],
                'stability_tolerance_percent':cfg['calibration_tolerance_percent'],
                'printer_url': cfg['moonraker_url'], 'session': str(session)}, allow_nan=False), encoding='utf-8')
            args = [str(shell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                    '-File', str(script), '-RequestFile', str(request)]
            if plan['action'] == 'Loaded':
                probe = session/'input-plan.json'
                probe.write_text(json.dumps(plan['probe'], allow_nan=False), encoding='utf-8')
            # File output avoids a pipe deadlock; never terminate a control process.
            with (session/'runner.log').open('w', encoding='utf-8') as output:
                process = subprocess.Popen(args, stdout=output, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), shell=False)
                code = process.wait()
            from .analyze_thermal_session import read_jsonl
            try:
                events = read_jsonl(session/'events.jsonl')
            except (OSError, ValueError) as exc:
                if code:
                    raise RuntimeError(f'Calibration runner exited with code {code}; no readable event record; inspect runner.log and printer state') from exc
                raise RuntimeError('Calibration event record could not be read; inspect runner.log and printer state') from exc
            labels={'session_error':'Calibration failed','heater_cleanup_blocked':'Heater shutdown could not be verified',
                    'state_restore_blocked':'G-code state restoration failed'}
            failures=[f"{labels[e['event']]}: {e.get('data','No details recorded')}" for e in events if e.get('event') in labels]
            if failures:
                raise RuntimeError('; '.join(failures))
            if code:
                raise RuntimeError(f'Calibration runner exited with code {code}; inspect runner.log and printer state')
            if not any(e['event'] == 'session_complete' for e in events):
                raise RuntimeError('Runner did not record a completed session')
            result = None
            if plan['action'] == 'Loaded':
                result = analyze_load(session)
            elif plan['action'] == 'LoadedThermal':
                result = analyze_temperature(session)
            if result is not None:
                result['profile_binding'] = {k:cfg[k] for k in DEFAULT_RESPONSE['binding']}
                (session/'analysis.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
                with self.lock:
                    self.results.append(result)
            with self.lock:
                self.job.update(status='complete', result=result)
        except Exception as exc:
            with self.lock:
                self.job.update(status='failed', error=str(exc))
                if isinstance(exc, PermissionError):
                    self.job['error'] += '; if Orca requested a permission, grant it and explicitly retry the action'


runtime = Runtime()
