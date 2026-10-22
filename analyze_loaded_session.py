# SPDX-License-Identifier: GPL-3.0-only
"""Summarize controlled loaded tests from executing-motion and heater telemetry.

This measures heater tracking under commanded load, not melt quality or slip.
"""
import argparse
import json
from pathlib import Path
from statistics import mean

from analyze_thermal_session import read_jsonl


def band_recovery_seconds(rows, target, extreme='minimum', hold_s=5):
    """Observed return to a +/-0.5 C band for >=5 seconds after the extreme.

    Relative to the first executing-flow sample of the supplied phase. This
    is an empirical PID recovery observation, not a material response model.
    """
    if not rows:
        return None
    chooser = min if extreme == 'minimum' else max
    peak = chooser(range(len(rows)), key=lambda i: rows[i]['temperature_c'])
    for i in range(peak, len(rows)):
        start = rows[i]['time_s']
        window = [r for r in rows[i:] if r['time_s'] <= start+hold_s+0.75]
        if window and window[-1]['time_s']-start >= hold_s and all(abs(r['temperature_c']-target) <= 0.5 for r in window):
            return start-rows[0]['time_s']
    return None


def summarize_rows(rows, target, full_flow):
    def near(row, flow):
        actual = row.get('flow_mm3_s')
        return actual is not None and abs(actual-flow) <= max(0.25, flow*0.03)

    loaded = [r for r in rows if r['stage'] == 'loaded_flow']
    full = [r for r in loaded if near(r, full_flow)]
    if len(full) < 10 or full[-1]['time_s']-full[0]['time_s'] < 25:
        raise ValueError('Insufficient executing full-flow telemetry for the planned 30-second plateau')
    target_values = {r['target_c'] for r in loaded}
    if target_values != {target}:
        raise ValueError('Temperature target changed during the constant-target load experiment')
    tail = [r for r in full if r['time_s'] >= full[-1]['time_s']-10]
    reduced = [r for r in loaded if r['time_s'] > full[-1]['time_s'] and near(r, full_flow*0.25)]
    baseline = [r for r in rows if r['stage'] == f'loaded_baseline_{int(target)}']
    baseline = [r for r in baseline if r['time_s'] >= baseline[-1]['time_s']-20] if baseline else []
    recovery = [r for r in rows if r['stage'] == f'loaded_recovery_{int(target)}']
    return {
        'target_temperature_c': target,
        'supplied_full_flow_mm3_s': full_flow,
        'full_flow_samples': len(full),
        'observed_full_flow_span_s': full[-1]['time_s']-full[0]['time_s'],
        'executing_commanded_flow_range_mm3_s': [min(r['flow_mm3_s'] for r in full), max(r['flow_mm3_s'] for r in full)],
        'full_flow_temperature_range_c': [min(r['temperature_c'] for r in full), max(r['temperature_c'] for r in full)],
        'maximum_full_flow_sag_c': max(0, target-min(r['temperature_c'] for r in full)),
        'full_flow_mean_temperature_c': mean(r['temperature_c'] for r in full),
        'full_flow_mean_power_fraction': mean(r['power_fraction'] for r in full),
        'full_flow_peak_power_fraction': max(r['power_fraction'] for r in full),
        'full_flow_saturated_sample_fraction': sum(r['power_fraction'] >= 0.98 for r in full)/len(full),
        'full_flow_last_10s_mean_temperature_c': mean(r['temperature_c'] for r in tail),
        'full_flow_last_10s_mean_power_fraction': mean(r['power_fraction'] for r in tail),
        'observed_full_flow_band_recovery_from_onset_s': band_recovery_seconds(full, target),
        'no_flow_baseline_mean_power_fraction': mean(r['power_fraction'] for r in baseline) if baseline else None,
        'reduced_flow_peak_temperature_c': max((r['temperature_c'] for r in reduced), default=None),
        'observed_reduced_flow_band_recovery_from_drop_s': band_recovery_seconds(reduced, target, extreme='maximum'),
        'post_stop_peak_temperature_c': max((r['temperature_c'] for r in recovery), default=None),
        'tracking_within_5c_in_sampled_full_flow_window': all(abs(r['temperature_c']-target) <= 5 for r in full),
        'print_quality_validated': False,
        'delivered_filament_flow_independently_measured': False,
        'temperature_setpoint_step_response_under_load_identified': False,
        'scope': 'Constant-target flow-disturbance sag/recovery only; a separate constant-flow temperature-step experiment is required for loaded setpoint response.',
    }


def analyze_session(directory):
    directory = Path(directory)
    if (directory/'EXCLUDED.json').exists():
        raise ValueError('Session excluded from calibration')
    events = read_jsonl(directory/'events.jsonl')
    rows = read_jsonl(directory/'telemetry.jsonl')
    if any(e['event'] == 'session_error' for e in events):
        raise ValueError('Session aborted; retain raw evidence but do not mark completed')
    if not any(e['event'] == 'session_complete' for e in events) or not any(e['event'] == 'heater_off' for e in events):
        raise ValueError('Session or heater cleanup incomplete')
    plan = json.loads((directory/'probe-plan.json').read_text(encoding='utf-8-sig'))
    if any(r.get('physical_tool') != plan['physical_tool'] for r in rows):
        raise ValueError('Wrong or mixed physical tool data')
    if len(set(r['fan_fraction'] for r in rows)) != 1:
        raise ValueError('Fan conditions changed')
    result = summarize_rows(rows, plan['temperature_target_c'], plan['maximum_requested_flow_mm3_s'])
    result.update(source_session=str(directory.resolve()), physical_tool=plan['physical_tool'],
                  fan_fraction=rows[0]['fan_fraction'], samples=len(rows),
                  ready_for_automatic_temperature_control=False)
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
