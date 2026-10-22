# SPDX-License-Identifier: GPL-3.0-only
"""Fit candidates from an explicitly completed, no-flow response session.

Usage: python analyze_thermal_session.py SESSION_DIRECTORY --output report.json
Raw evidence is never modified. Rejects aborted/excluded/wrong-head sessions.
"""
import argparse
from collections import Counter
import json
from pathlib import Path

from thermal_identification import InsufficientExperiment, fit_temperature_step


def read_jsonl(path):
    return [json.loads(line.lstrip('\ufeff')) for line in Path(path).read_text(encoding='utf-8-sig').splitlines() if line.strip()]


def analyze_session(directory, expected_tool=2):
    directory = Path(directory)
    if (directory/'EXCLUDED.json').exists():
        raise ValueError('Session is explicitly excluded from calibration')
    events = read_jsonl(directory/'events.jsonl')
    rows = read_jsonl(directory/'telemetry.jsonl')
    if any(e['event'] == 'session_error' for e in events):
        raise ValueError('Aborted session: do not fit it as a completed calibration')
    start = next((e for e in events if e['event'] == 'session_start'), None)
    if not start or start['data']['action'] != 'Thermal' or start['data'].get('physical_tool') != expected_tool:
        raise ValueError('Expected a no-flow thermal session on the selected physical tool')
    if not any(e['event'] == 'session_complete' for e in events):
        raise ValueError('Session has not completed')
    if not any(e['event'] == 'heater_off' for e in events):
        raise ValueError('Heater cleanup has not been recorded')
    if any(r.get('physical_tool') != expected_tool for r in rows):
        raise ValueError('Mixed or incorrect tool telemetry')
    transitions = []
    for i, event in enumerate(events):
        if event['event'] != 'target_step':
            continue
        step = event['data']
        subsequent = events[i+1:]
        settled = next((e for e in subsequent if e['event'] in ('settled', 'target_step')), None)
        if not settled or settled['event'] != 'settled':
            continue
        t0, t1 = step['before_time_s'], settled['data']['time_s']
        before = [r for r in rows if t0-21 <= r['time_s'] < t0]
        after = [r for r in rows if t0 <= r['time_s'] <= t1 and r['target_c'] == step['target_c']]
        phase = settled['data']['stage']
        if phase.startswith('baseline'):
            continue  # Cold warm-up is not an isolated step about a settled baseline.
        transition = {
            'stage': phase, 'target_c': step['target_c'],
            'step_before_command_time_s': t0,
            'step_timestamp_note': 'Timestamp precedes the HTTP command; sensor/command transport adds timing uncertainty.',
            'observed_response_duration_s': t1-t0,
            'observed_min_temperature_c': min((r['temperature_c'] for r in after), default=None),
            'observed_max_temperature_c': max((r['temperature_c'] for r in after), default=None),
        }
        samples = [{**r, 'flow_mm3_s': 0.0} for r in before+after]
        try:
            model = fit_temperature_step(samples, t0)
            transition.update(status='candidate_fit', model=model)
        except InsufficientExperiment as exc:
            transition.update(status='not_identified', reason=str(exc))
        transitions.append(transition)
    return {
        'source_session': str(directory.resolve()),
        'physical_tool': expected_tool,
        'physical_heater': f'extruder{expected_tool}' if expected_tool else 'extruder',
        'fan_fraction_values': sorted(set(r['fan_fraction'] for r in rows)),
        'samples': len(rows), 'transition_statuses': dict(Counter(t['status'] for t in transitions)),
        'transitions': transitions,
        'ready_for_automatic_control': False,
        'scope': 'No-flow sensor response only; loaded-flow response and print quality remain uncalibrated.',
        'flow_annotation': 'Zero flow is assigned from the controlled no-extrusion test procedure, not from a flow sensor.',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session')
    parser.add_argument('--output', required=True)
    parser.add_argument('--tool', type=int, default=2)
    args = parser.parse_args()
    report = analyze_session(args.session, args.tool)
    path = Path(args.output)
    # Preserve original evidence and avoid accidental overwrite of an earlier fit.
    with path.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
