# SPDX-License-Identifier: GPL-3.0-only
"""Adaptive path forecast with time-weighted flow and predictive cooling."""
from bisect import bisect_left, bisect_right
from collections import defaultdict
import hashlib
import math
import re

from predictive_thermal import SensorPredictor, choose_target, response_pair
from burst_model import BurstBudget, burst_settings, sustainable_flow


class PathTimeline:
    """Exact cumulative XY(Z) path length and deposited volume over move phases.

An adaptive distance horizon ends when its traversal time reaches the learned
response horizon. Acceleration/deceleration and zero-flow travel are included.
The final move is integrated fractionally, not counted as a whole segment.
"""
    def __init__(self,moves,selected_lines):
        self.phases=[]
        self.starts=[]
        distance=volume=0.0
        self.start=moves[0].time
        self.end=moves[-1].time+moves[-1].duration
        for move in moves:
            now=move.time
            ratio=move.volume_per_mm if move.line in selected_lines else 0.0
            for dt,v0,v1 in ((move.accel_time,move.start_speed,move.cruise_speed),
                              (move.cruise_time,move.cruise_speed,move.cruise_speed),
                              (move.decel_time,move.cruise_speed,move.end_speed)):
                if dt > 1e-12:
                    speed0=v0 if move.kinematic else 0.0
                    slope=(v1-v0)/dt if move.kinematic else 0.0
                    self.starts.append(now)
                    self.phases.append((now,now+dt,speed0,slope,ratio,distance,volume))
                    length=speed0*dt+0.5*slope*dt*dt
                    distance+=length
                    volume+=length*ratio
                now+=dt
        self.distance_mm,self.volume_mm3=distance,volume

    def cumulative(self,t):
        if t <= self.start:return 0.0,0.0
        if t >= self.end:return self.distance_mm,self.volume_mm3
        i=bisect_right(self.starts,t)-1
        if i<0:return 0.0,0.0
        a,b,v,slope,ratio,distance,volume=self.phases[i]
        dt=max(0,min(t,b)-a)
        length=v*dt+0.5*slope*dt*dt
        return distance+length,volume+length*ratio

    def window(self,t,horizon):
        a=max(self.start,min(t,self.end))
        b=min(self.end,a+horizon)
        if b-a < 1e-9:return {'flow_mm3_s':0.0,'distance_mm':0.0,'duration_s':0.0}
        da,va=self.cumulative(a)
        db,vb=self.cumulative(b)
        return {'flow_mm3_s':max(0,(vb-va)/(b-a)),
                'distance_mm':max(0,db-da),'duration_s':b-a}


def initial_target(lines,first_line,tool,minimum,maximum):
    """Use the latest explicit target for this tool as an uncertain initial state."""
    active=None
    target=None
    for raw in lines[:first_line-1]:
        code=raw.split(';',1)[0].strip()
        tool_match=re.fullmatch(r'T(\d+)(?:\s+.*)?',code,re.I)
        if tool_match:active=int(tool_match[1])
        match=re.match(r'M10[49]\s+(.*)',code,re.I)
        if match:
            params={k.upper():float(v) for k,v in re.findall(r'([ST])\s*([-+\d.]+)',match[1],re.I)}
            if params.get('T',active)==tool and params.get('S',0)>0:
                target=params['S']
        match=re.search(r'SET_HEATER_TEMPERATURE\s+HEATER=(\w+)\s+TARGET=([\d.]+)',code,re.I)
        if match and match[1]==('extruder' if tool==0 else f'extruder{tool}') and float(match[2])>0:
            target=float(match[2])
    if target is None:
        target=maximum
    return target


def schedule_predictive(clean,cfg,analyzer,curve,fences,candidates,binding):
    from flow_scheduler import EditingRefused
    try:
        heating,cooling=response_pair(cfg.get('thermal_response'))
    except (KeyError,TypeError,ValueError) as exc:
        raise EditingRefused(f'Invalid thermal response profile: {exc}') from exc
    profile=cfg['thermal_response']
    from predictive_thermal import response_is_configured
    if not response_is_configured(profile):
        raise EditingRefused('This tool has no calibrated response. Import saved settings or calibrate heating and cooling before enabling adaptive temperature.')
    try:
        burst=burst_settings(cfg,cooling.horizon_s)
    except ValueError as exc:
        raise EditingRefused(str(exc)) from exc
    identity=profile.get('binding')
    if identity and any(cfg.get(k)!=v for k,v in identity.items()):
        raise EditingRefused('The response profile is bound to another printer, tool, material or nozzle; adopt a matching calibration or enter a new candidate response')
    all_groups=defaultdict(list)
    selected=defaultdict(list)
    for move in analyzer.moves:
        all_groups[bisect_right(fences,move.line)].append(move)
    for move in candidates:
        selected[bisect_right(fences,move.line)].append(move)
    lines=clean.splitlines(keepends=True)
    minimum,maximum=cfg['min_temperature_c'],cfg['max_temperature_c']
    events,samples,section_stats=[],[],[]
    horizon=max(heating.horizon_s,cooling.horizon_s)
    prediction_step=max(0.05,min(0.5,min(heating.time_constant_s,cooling.time_constant_s)/4))
    control_period=max(prediction_step,min(2.0,heating.time_constant_s,cooling.time_constant_s),
                       abs(heating.delay_s-cooling.delay_s)+0.1)
    unfunded_total=0.0
    for section,chosen in selected.items():
        first,last=chosen[0].line,chosen[-1].line
        group=[m for m in all_groups[section] if first<=m.line<=last]
        timeline=PathTimeline(group,{m.line for m in chosen})
        # Sustained demand uses the learned heating horizon. Shorter-window
        # flow is diagnostic only: no melt-energy/burst allowance was measured.
        short=max(prediction_step,heating.time_constant_s)
        grid=[]
        count=max(1,math.ceil((timeline.end-timeline.start)/prediction_step))
        for i in range(count):
            t=timeline.start+i*prediction_step
            long_window=timeline.window(t,heating.horizon_s)
            short_window=timeline.window(t,short)
            flow=long_window['flow_mm3_s']
            target=curve.temperature_for_flow(flow)['temperature_target_estimate_c']
            grid.append((target,flow,long_window,short_window))
        def demand(t):
            index=min(len(grid)-1,max(0,int((t-timeline.start)/prediction_step)))
            return grid[index]
        assumed=initial_target(lines,first,cfg['logical_tool'],minimum,maximum)
        predictor=SensorPredictor(heating,cooling,timeline.start,assumed,assumed)
        budget=None if burst is None else BurstBudget(burst['estimated_reserve_capacity_mm3'],burst['max_recharge_rate_mm3_s'])
        starts=[m.time for m in group]
        next_control=timeline.start
        slots=[]
        while next_control<timeline.end-1e-9:
            index=bisect_left(starts,next_control)
            if index>=len(group):break
            move=group[index]
            if not slots or slots[-1].line!=move.line:slots.append(move)
            next_control=move.time+control_period
        for move in slots:
            unfunded=0.0
            if budget is not None:
                while predictor.time_s<move.time-1e-9:
                    a=predictor.time_s
                    dt=min(prediction_step,move.time-a)
                    before=predictor.temperature_c
                    after=predictor.advance(a+dt)
                    flow=timeline.window(a,dt)['flow_mm3_s']
                    unfunded+=budget.advance(flow,sustainable_flow(curve,(before+after)/2),dt)
            current=predictor.advance(move.time)
            unfunded_total+=unfunded
            remaining=max(0,timeline.end-move.time)
            n=max(1,math.ceil(min(horizon,remaining)/prediction_step))
            future=[demand(min(timeline.end-1e-9,move.time+i*prediction_step))[0] for i in range(1,n+1)]
            flow_forecast=None
            if budget is not None:
                flow_forecast=[]
                for i in range(n):
                    a=move.time+i*prediction_step
                    window=timeline.window(a,min(prediction_step,timeline.end-a))
                    flow_forecast.append((window['duration_s'],window['flow_mm3_s']))
            diagnostics={}
            target=choose_target(predictor,future,minimum,maximum,prediction_step,
                budget,flow_forecast,curve,diagnostics)
            reference,flow,long_window,short_window=demand(move.time)
            if not events or events[-1]['timing_section']!=section or target!=predictor.last_command_c:
                predictor.command(move.time,target)
                events.append({'before_source_line':move.line,'modeled_time_s':move.time,
                    'temperature_c':target,'envelope_peak_flow_mm3_s':flow,
                    'forecast_average_flow_mm3_s':long_window['flow_mm3_s'],
                    'short_window_flow_mm3_s':short_window['flow_mm3_s'],
                    'path_lookahead_mm':long_window['distance_mm'],
                    'path_lookahead_time_s':long_window['duration_s'],
                    'predicted_sensor_temperature_c':current,'desired_sensor_temperature_c':reference,
                    'burst_reserve_remaining_mm3':None if budget is None else budget.remaining_mm3,
                    'forecast_unfunded_volume_mm3':diagnostics['forecast_unfunded_volume_mm3'],
                    'logical_tool':cfg['logical_tool'],'timing_section':section})
            samples.append({'time_s':move.time,'predicted_sensor_temperature_c':current,
                'desired_sensor_temperature_c':reference,'target_c':target,
                'flow_mm3_s':flow,'average_flow_mm3_s':long_window['flow_mm3_s'],
                'path_lookahead_mm':long_window['distance_mm'],'timing_section':section})
            samples[-1].update(burst_reserve_remaining_mm3=None if budget is None else budget.remaining_mm3,
                unfunded_volume_since_previous_slot_mm3=unfunded)
        # Include the last control interval when reporting modeled shortfalls.
        if budget is not None:
            while predictor.time_s<timeline.end-1e-9:
                a=predictor.time_s;dt=min(prediction_step,timeline.end-a)
                before=predictor.temperature_c;after=predictor.advance(a+dt)
                unfunded_total+=budget.advance(timeline.window(a,dt)['flow_mm3_s'],
                    sustainable_flow(curve,(before+after)/2),dt)
        section_stats.append({'timing_section':section,'start_s':timeline.start,'end_s':timeline.end,
            'assumed_initial_sensor_c':assumed,'initial_sensor_measured':False,
            'path_mm':timeline.distance_mm,'deposited_volume_mm3':timeline.volume_mm3})
    if not events:raise EditingRefused('No predictive command locations were available')
    insertions={e['before_source_line']:e for e in events}
    newline='\r\n' if '\r\n' in clean else '\n'
    output=[]
    first=True
    public_marker='burst_preset' in cfg
    marker_version='0.5.0' if public_marker else '0.4.0'
    for i,line in enumerate(lines,1):
        if i in insertions:
            e=insertions[i]
            if first:
                header=(f'; ADAPTIVE_TEMPERATURE v{marker_version} {len(events)} temperature commands; hybrid predictive'
                        if public_marker else f'; ORCA_FLOW_TEST v{marker_version} {len(events)} temperature commands; hybrid predictive inspection output, print validation pending')
                output.append(header+newline)
                first=False
            marker='ADAPTIVE_TEMPERATURE_TEMP' if public_marker else 'ORCA_FLOW_TEMP'
            output.append(f'M104 T{cfg["logical_tool"]} S{e["temperature_c"]:.10g} ; {marker} v{marker_version} q={e["envelope_peak_flow_mm3_s"]:.6f} t={e["modeled_time_s"]:.6f}'+newline)
        output.append(line)
    edited=''.join(output)
    return edited,{
        'mode':'experimental_hybrid_predictive','algorithm':'adaptive_path_average_predictive_sensor_response',
        'gcode_modified':True,'ready_for_printing':False,'commands_inserted':len(events),
        'temperature_range_c':[min(e['temperature_c'] for e in events),max(e['temperature_c'] for e in events)],
        'body_peak_planned_flow_mm3_s':max(m.volume_per_mm*m.cruise_speed for m in candidates),
        'operating_flow_limit_mm3_s':cfg['points'][-1]['max_reliable_flow_mm3_s']*(1-cfg['headroom_percent']/100),
        'heating_lookahead_s':heating.horizon_s,'cooling_prediction_horizon_s':cooling.horizon_s,
        'cooling_hold_s':0,'control_period_s':control_period,
        'thermal_response':profile,'burst_capacity_validated':False,
        'burst_policy':burst,'modeled_unfunded_burst_volume_mm3':None if burst is None else unfunded_total,
        'flow_forecast_resolution_s':prediction_step,
        'first_layer_preserved':True,'original_commands_preserved':True,'verified_slice_binding':binding,
        'timing_sections_with_no_guaranteed_initial_lead':len(section_stats),
        'path_lookahead_distance_range_mm':[min(s['path_lookahead_mm'] for s in samples),max(s['path_lookahead_mm'] for s in samples)],
        'source_sha256':hashlib.sha256(clean.encode('utf-8')).hexdigest(),
        'output_sha256':hashlib.sha256(edited.encode('utf-8')).hexdigest(),
        'events':events[:2000],'events_truncated':len(events)>2000,
        'prediction_samples':samples[:4000],'prediction_samples_truncated':len(samples)>4000,
        'sections':section_stats,
        'notes':[
            'Adaptive path windows use acceleration-aware traversal time and fractionally integrated deposition, including zero-flow travel.',
            'A receding-horizon sensor model simulates separate heating/cooling delays and time constants; cooling is predictive, with no fixed hold.',
            'Burst reserve is an unvalidated geometry/slider estimate; flow is integrated in sub-second intervals, not a resolved polymer temperature model.' if burst else 'Short-window flow is diagnostic only. Melt quality, stored melt energy and allowable bursts have not been calibrated.',
            'Burst reserve starts empty at each timing barrier and refills only from spare modeled capacity, capped by the cooling-response horizon. Predicted shortfalls remain reported even when unavoidable.' if burst else 'No burst-reserve guard is enabled; this is the previous hybrid average policy.',
            'The model is initialized from the preceding explicit target, not a sensor measurement. Initial timing and model conditions may be uncertain.',
            'Candidate response fits are extrapolated outside their recorded flow, fan and temperature conditions; inspect and validate before print use.',
            'Original commands and first layer are preserved; predictions never cross timing barriers. Output is restricted to file export.',
        ]}
