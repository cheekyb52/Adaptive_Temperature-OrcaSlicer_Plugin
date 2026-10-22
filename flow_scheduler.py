# SPDX-License-Identifier: GPL-3.0-only
"""Experimental, offline-only temperature editing for exported Orca G-code.

No printer connection. Original motion, startup, first layer, existing heater
commands, tool changes and shutdown remain byte-for-byte present. Candidate
lead times are a planning hypothesis, not a validated thermal controller.
"""
from bisect import bisect_right
from collections import defaultdict, deque
import hashlib
import math
from pathlib import Path
import re

from flow_evaluator import Analyzer
from flow_temperature_curve import FlowPoint, FlowTemperatureCurve

VERSION = '0.3.0'
OWN_COMMAND = re.compile(r'^M104 T\d+ S[\d.]+ ; ORCA_FLOW_TEMP v0\.[345]\.0 q=[\d.]+ t=[\d.]+\r?\n?$')
PUBLIC_COMMAND = re.compile(r'^M104 T\d+ S[\d.]+ ; ADAPTIVE_TEMPERATURE_TEMP v0\.5\.0 q=[\d.]+ t=[\d.]+\r?\n?$')
OWN_HEADER = '; ORCA_FLOW_TEST v0.3.0 '


class EditingRefused(ValueError):
    pass


def strip_generated(text):
    kept = []
    for line in text.splitlines(keepends=True):
        if OWN_COMMAND.fullmatch(line) or PUBLIC_COMMAND.fullmatch(line) or line.startswith((OWN_HEADER,'; ORCA_FLOW_TEST v0.4.0 ','; ORCA_FLOW_TEST v0.5.0 ','; ADAPTIVE_TEMPERATURE v0.5.0 ')):
            continue
        if 'ORCA_FLOW_TEMP' in line or 'ORCA_FLOW_TEST' in line or 'ADAPTIVE_TEMPERATURE' in line:
            raise EditingRefused('Unrecognized temperature plugin marker; refusing to stack or remove unknown edits')
        kept.append(line)
    return ''.join(kept)


class RecordingAnalyzer(Analyzer):
    def __init__(self, config):
        super().__init__(config)
        self.fences = []

    def barrier(self, command, invalidate=True):
        self.fences.append(self.lines)
        super().barrier(command, invalidate)


def check_slice_binding(text, cfg):
    """Check resolved exported metadata as well as the user's manual binding."""
    metadata = dict(re.findall(r'^;\s*(filament_type|nozzle_diameter|filament_map|filament_diameter)\s*=\s*([^\r\n]*)', text, re.M))
    tool = cfg['logical_tool']
    def vector(key, separator):
        values = [s.strip().strip('"') for s in metadata.get(key, '').split(separator)]
        if len(values) <= tool or not values[tool]:
            raise EditingRefused(f'Cannot verify {key} for T{tool} in exported slice metadata')
        return values[tool]
    material = vector('filament_type', ';')
    if material.upper() != cfg['filament_type'].upper():
        raise EditingRefused(f'Slice T{tool} is {material}, but the selected curve is {cfg["filament_type"]}')
    nozzle = float(vector('nozzle_diameter', ','))
    if abs(nozzle-cfg['nozzle_diameter_mm']) > 0.0001:
        raise EditingRefused(f'Slice nozzle is {nozzle:g} mm; this curve is bound to {cfg["nozzle_diameter_mm"]:g} mm. Import or select the calibration for this nozzle in Filament curve, then confirm the tool mapping.')
    diameter = float(vector('filament_diameter', ','))
    if abs(diameter-1.75) > 0.0001:
        raise EditingRefused('The initial editor requires 1.75 mm filament')
    if 'filament_map' in metadata and int(vector('filament_map', ',')) != cfg['physical_tool']+1:
        raise EditingRefused('The exported filament map does not match the confirmed physical tool')
    return {'filament_type': material, 'nozzle_diameter_mm': nozzle, 'filament_diameter_mm': diameter}


def schedule_text(text, cfg):
    """Return edited text and an audit report; refuse before any file is changed."""
    if cfg.get('tool_mode')=='toolchanger':
        from multi_tool_scheduler import schedule_toolchanger
        return schedule_toolchanger(text,cfg)
    if cfg.get('output_mode') != 'temperature_test':
        raise EditingRefused('Select adaptive temperature output before editing')
    if cfg.get('physical_tool') != cfg.get('logical_tool'):
        raise EditingRefused('This first editor requires matching logical and physical tool indices; remapped tools need an adapter')
    clean = strip_generated(text)
    if '\x00' in clean or re.search(r'^\s*N\d+\s', clean, re.M):
        raise EditingRefused('Only plain, unnumbered text G-code is supported')
    binding = check_slice_binding(clean, cfg)
    lines = clean.splitlines(keepends=True)
    layer_lines = [i for i, line in enumerate(lines, 1) if line.strip() == ';LAYER_CHANGE']
    if len(layer_lines) < 2:
        raise EditingRefused('At least two Orca ;LAYER_CHANGE markers are needed; the first layer is preserved')
    start_line = layer_lines[1]
    analyzer = RecordingAnalyzer({**cfg['motion'], 'logical_tool': cfg['logical_tool']})
    source_report = analyzer.read(lines)
    if not source_report['supported_motion_only']:
        raise EditingRefused('Unsupported arcs or firmware retraction: disable them for the initial editing test')
    if any('Marlin limits/jerk' in key for key in analyzer.skipped):
        raise EditingRefused('The initial temperature editor supports the Klipper motion model only')
    curve = FlowTemperatureCurve([FlowPoint(**p) for p in cfg['points']],
        max_temperature_c=cfg['max_temperature_c'], max_flow_mm3_s=cfg['max_flow_mm3_s'],
        flow_headroom_fraction=cfg['headroom_percent']/100, interpolation=cfg['interpolation'])
    # Never move an insertion across an opaque macro, existing heater command,
    # custom script boundary or tool change. Keep the first layer intact.
    fences = list(analyzer.fences)+[start_line]
    for i, line in enumerate(lines, 1):
        code = line.split(';', 1)[0].strip().upper()
        if re.match(r'^(?:M104|M109|SET_HEATER_TEMPERATURE|TEMPERATURE_WAIT|M400|G4)(?:\s|$)', code):
            fences.append(i)
        if line.strip() in (';TYPE:Custom', ';TYPE:Unclassified', '; EXECUTABLE_BLOCK_END', '; CONFIG_BLOCK_START'):
            fences.append(i)
    fences = sorted(set(fences))
    groups = defaultdict(list)
    candidates = [m for m in analyzer.moves if m.line > start_line and m.tool == cfg['logical_tool']
                  and m.deposited_mm3 > 0 and m.role.lower() not in ('custom', 'unclassified', '')]
    if not candidates:
        raise EditingRefused(f'No modeled print extrusion after the first layer for logical T{cfg["logical_tool"]}')
    peak = max(m.volume_per_mm*m.cruise_speed for m in candidates)
    capacity = cfg['points'][-1]['max_reliable_flow_mm3_s']*(1-cfg['headroom_percent']/100)
    try:
        curve.temperature_for_flow(peak)
    except ValueError as exc:
        raise EditingRefused(f'Planned body flow {peak:.3f} mm³/s exceeds the curve operating limit {capacity:.3f} mm³/s. Reduce sliced flow or explicitly review headroom; the file was not edited.') from exc
    if cfg.get('scheduler','peak_hold') == 'hybrid_predictive':
        from predictive_scheduler import schedule_predictive
        return schedule_predictive(clean,cfg,analyzer,curve,fences,candidates,binding)
    for move in candidates:
        q = move.volume_per_mm*move.cruise_speed
        temperature = curve.temperature_for_flow(q)['temperature_target_estimate_c']
        temperature = min(cfg['max_temperature_c'], max(cfg['min_temperature_c'], math.ceil(temperature-1e-9)))
        groups[bisect_right(fences, move.line)].append((move, temperature, q))
    heat = float(cfg['heating_lead_s'])
    hold = float(cfg['cooling_hold_s'])
    if not all(math.isfinite(v) and 0 <= v <= 60 for v in (heat, hold)):
        raise EditingRefused('Lead/hold times must be finite and between 0 and 60 seconds')
    insertions = {}
    events = []
    clipped_sections = 0
    for group in groups.values():
        window = deque()
        right = 0
        previous = None
        previous_time = -math.inf
        for move, _, _ in group:
            now = move.time
            while right < len(group) and group[right][0].time <= now+heat:
                candidate = group[right]
                while window and window[-1][1] <= candidate[1]:
                    window.pop()
                window.append(candidate)
                right += 1
            while window and window[0][0].time+window[0][0].duration < now-hold:
                window.popleft()
            _, target, flow = window[0]
            # Heat requests can advance immediately; falling targets need a
            # 2-degree change and 2 s spacing. The past window delays cooling.
            emit = previous is None or target > previous or (previous-target >= 2 and now-previous_time >= 2)
            if not emit:
                continue
            event = {'before_source_line': move.line, 'modeled_time_s': now,
                     'temperature_c': target, 'envelope_peak_flow_mm3_s': flow,
                     'logical_tool': cfg['logical_tool'], 'timing_section': bisect_right(fences, move.line)}
            if previous is None:
                # Boundary lead cannot be borrowed from startup/another tool.
                clipped_sections += 1
            marker,version=('ADAPTIVE_TEMPERATURE_TEMP','0.5.0') if 'burst_preset' in cfg else ('ORCA_FLOW_TEMP',VERSION)
            command = f'M104 T{cfg["logical_tool"]} S{target:.10g} ; {marker} v{version} q={flow:.6f} t={now:.6f}'
            insertions[move.line] = command
            events.append(event)
            previous, previous_time = target, now
    newline = '\r\n' if '\r\n' in clean else '\n'
    output = []
    first = True
    for i, line in enumerate(lines, 1):
        if i in insertions:
            if first:
                header=(f'; ADAPTIVE_TEMPERATURE v0.5.0 {len(events)} temperature commands; peak demand'
                        if 'burst_preset' in cfg else OWN_HEADER+f'{len(events)} temperature commands; experimental inspection output, print validation pending')
                output.append(header+newline)
                first = False
            output.append(insertions[i]+newline)
        output.append(line)
    edited = ''.join(output)
    return edited, {
        'mode': 'experimental_temperature_test', 'gcode_modified': edited != text,
        'ready_for_printing': False, 'commands_inserted': len(events),
        'temperature_range_c': [min(e['temperature_c'] for e in events), max(e['temperature_c'] for e in events)],
        'body_peak_planned_flow_mm3_s': peak, 'operating_flow_limit_mm3_s': capacity,
        'heating_lookahead_s': heat, 'cooling_hold_s': hold,
        'timing_sections_with_no_guaranteed_initial_lead': clipped_sections,
        'first_layer_preserved': True, 'original_commands_preserved': True,
        'verified_slice_binding': binding,
        'source_sha256': hashlib.sha256(clean.encode('utf-8')).hexdigest(),
        'output_sha256': hashlib.sha256(edited.encode('utf-8')).hexdigest(),
        'events': events[:2000], 'events_truncated': len(events) > 2000,
        'notes': [
            'Heating uses future peak demand; cooling is held after high flow, not advanced before it.',
            'Lookahead is clipped at macros, heater commands and tool changes. Initial lead in each section is not guaranteed.',
            'Motion-planner time and candidate sensor-response timing are estimates; queue timing and print quality are unvalidated.',
            'Original startup, first layer, existing heater commands, other tools, travel/feed/E and shutdown are preserved.',
            'Adaptive temperature output supports file export; direct upload requires analysis mode.',
        ],
    }


def edit_working_file(path, cfg):
    path = Path(path)
    before = path.read_bytes()
    edited, report = schedule_text(before.decode('utf-8'), cfg)
    # All validation and planning precedes the first write. This is only the
    # working copy supplied by Orca, never an arbitrary source/profile file.
    if path.read_bytes() != before:
        raise EditingRefused('The working file changed during analysis; no edit applied')
    with path.open('wb') as stream:
        stream.write(edited.encode('utf-8'))
    return report
