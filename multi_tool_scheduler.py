# SPDX-License-Identifier: GPL-3.0-only
"""Combine independently planned tool schedules against one unchanged source."""
from collections import defaultdict
import hashlib

from flow_scheduler import EditingRefused, RecordingAnalyzer, schedule_text, strip_generated
from tool_profiles import profile_config


def schedule_toolchanger(text,cfg):
    if cfg.get('output_mode')!='temperature_test':
        raise EditingRefused('Select adaptive temperature output before editing')
    clean=strip_generated(text)
    analyzer=RecordingAnalyzer({**cfg['motion'],'logical_tool':cfg['logical_tool']})
    analyzer.read(clean.splitlines(keepends=True))
    layer_lines=[i for i,line in enumerate(clean.splitlines(),1) if line.strip()==';LAYER_CHANGE']
    if len(layer_lines)<2:raise EditingRefused('At least two layers are required')
    used=sorted({m.tool for m in analyzer.moves if m.line>layer_lines[1] and m.deposited_mm3>0 and m.role.lower() not in ('custom','unclassified','')})
    if not used:raise EditingRefused('No print-body deposition tools found')
    reports={}
    insertions=defaultdict(list)
    for tool in used:
        matches=[i for i in range(cfg['tool_count']) if profile_config(cfg,i)['logical_tool']==tool]
        if len(matches)!=1:raise EditingRefused(f'Logical T{tool} needs exactly one configured tool profile')
        local=profile_config(cfg,matches[0])
        _,report=schedule_text(clean,local)
        if report.get('events_truncated'):raise EditingRefused('Too many commands to combine without a complete per-tool event list')
        reports[str(tool)]=report
        for event in report['events']:insertions[event['before_source_line']].append(event)
    newline='\r\n' if '\r\n' in clean else '\n'
    output=[]
    events=sorted((e for report in reports.values() for e in report['events']),key=lambda e:e['before_source_line'])
    first=True
    for i,line in enumerate(clean.splitlines(keepends=True),1):
        for event in insertions.get(i,[]):
            if first:
                output.append(f'; ADAPTIVE_TEMPERATURE v0.5.0 {len(events)} temperature commands; independent toolchanger schedules'+newline)
                first=False
            output.append(f'M104 T{event["logical_tool"]} S{event["temperature_c"]:.10g} ; ADAPTIVE_TEMPERATURE_TEMP v0.5.0 q={event["envelope_peak_flow_mm3_s"]:.6f} t={event["modeled_time_s"]:.6f}'+newline)
        output.append(line)
    edited=''.join(output)
    return edited,{'mode':'experimental_toolchanger_predictive','gcode_modified':True,'ready_for_printing':False,
        'commands_inserted':len(events),'temperature_range_c':[min(e['temperature_c'] for e in events),max(e['temperature_c'] for e in events)],
        'used_logical_tools':used,'tool_reports':reports,'events':events,'events_truncated':False,
        'first_layer_preserved':True,'original_commands_preserved':True,
        'source_sha256':hashlib.sha256(clean.encode()).hexdigest(),'output_sha256':hashlib.sha256(edited.encode()).hexdigest(),
        'notes':['Each used tool has independent curve, response and burst reserve. Predictions do not cross tool-change/timing barriers.',
                 'Copied calibration is inherited data, not an independent measurement of the destination hotend.',
                 'Logical and physical indices must match in this release. Missing calibration data for a used tool refuses the entire export.']}
