# SPDX-License-Identifier: GPL-3.0-only
"""Fit candidate setpoint response while executing a constant extrusion load."""
import argparse
import json
from pathlib import Path

from analyze_thermal_session import read_jsonl
from thermal_identification import InsufficientExperiment, fit_temperature_step


def fit_rows(rows, flow, targets=(220,230,220), stability=None):
    raw = [r for r in rows if r['stage'] == 'loaded_temperature_flow']
    tolerance = max(0.25, flow*0.03)
    active = [r for r in raw if r.get('flow_mm3_s') is not None and abs(r['flow_mm3_s']-flow) <= tolerance]
    if len(active) < 60:
        raise ValueError('Insufficient constant-flow motion telemetry')
    active_raw = [r for r in raw if active[0]['time_s'] <= r['time_s'] <= active[-1]['time_s']]
    if len(active)/len(active_raw) < 0.9:
        raise ValueError('Flow interruptions are too frequent for isolated step identification')
    groups = []
    for row in active:
        if not groups or groups[-1][0]['target_c'] != row['target_c']:
            groups.append([])
        groups[-1].append(row)
    if [g[0]['target_c'] for g in groups] != list(targets):
        raise ValueError('Observed targets do not match the recorded loaded response sequence')
    transitions = []
    for before_group, after_group in zip(groups, groups[1:]):
        first_after = after_group[0]['time_s']
        previous_target = before_group[-1]['target_c']
        previous_raw = [r for r in raw if r['time_s'] < first_after and r['target_c'] == previous_target]
        t0 = previous_raw[-1]['time_s']
        before = [r for r in before_group if t0-21 <= r['time_s'] < t0]
        after = [r for r in after_group if r['time_s'] > t0]
        result = {
            'from_target_c': previous_target, 'to_target_c': after_group[0]['target_c'],
            'target_transition_observation_bracket_s': [t0, first_after],
            'time_uncertainty_note': 'The actual command lies between observations; sample cadence and sensor filtering limit inferred dead time.',
            'constant_flow_sample_count': len(before)+len(after),
        }
        try:
            options = {} if stability is None else {'settling_tolerance_percent':stability['tolerance_percent'],
                                                    'settling_window_s':stability['window_s']}
            result.update(status='candidate_fit', model=fit_temperature_step(before+after, t0, **options))
        except InsufficientExperiment as exc:
            result.update(status='not_identified', reason=str(exc))
        transitions.append(result)
    return {
        'commanded_flow_mm3_s': flow,
        'constant_flow_samples': len(active),
        'active_interval_flow_sample_fraction': len(active)/len(active_raw),
        'transitions': transitions,
        'ready_for_automatic_control': False,
    }


def analyze_session(directory):
    directory = Path(directory)
    events = read_jsonl(directory/'events.jsonl')
    rows = read_jsonl(directory/'telemetry.jsonl')
    if any(e['event'] == 'session_error' for e in events):
        raise ValueError('Session aborted')
    if not any(e['event'] == 'session_complete' for e in events) or not any(e['event'] == 'heater_off' for e in events):
        raise ValueError('Session/cleanup incomplete')
    tools={r.get('physical_tool') for r in rows}
    if len(tools)!=1 or next(iter(tools)) is None:
        raise ValueError('A response session must contain exactly one physical tool')
    start=next((e['data'] for e in events if e['event']=='session_start'),{})
    if 'physical_tool' in start and start['physical_tool']!=next(iter(tools)):
        raise ValueError('Recorded physical tool does not match the session request')
    if len(set(r['fan_fraction'] for r in rows)) != 1:
        raise ValueError('Part cooling changed')
    sequence = next(e['data'] for e in events if e['event'] == 'loaded_temperature_sequence')
    targets=[p['target_c'] for p in sequence.get('phases',[])] or [220,230,220]
    result = fit_rows(rows, sequence['commanded_flow_mm3_s'],targets,sequence.get('stability'))
    result['protocol']=sequence.get('protocol','fixed_duration_v1')
    if 'stability' in sequence:result['stability']=sequence['stability']
    result.update(source_session=str(directory.resolve()), physical_tool=next(iter(tools)),
                  fan_fraction=rows[0]['fan_fraction'], samples=len(rows),
                  scope='Candidate loaded sensor-response model at the tested flow and fan conditions only.')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = analyze_session(args.session)
    with Path(args.output).open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
