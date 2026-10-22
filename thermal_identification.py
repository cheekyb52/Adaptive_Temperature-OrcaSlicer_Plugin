# SPDX-License-Identifier: GPL-3.0-only
"""Hardware-independent candidate closed-loop response identification.

Offline analysis only. No connections, heater commands, G-code or PID changes.
Samples: dicts with time_s, temperature_c, target_c, power_fraction,
flow_mm3_s and fan_fraction. Requires one isolated setpoint step, a stable
pre-step baseline and a settled tail at constant flow/fan conditions.
This is a first-order-plus-delay approximation to the sensor response with
the existing PID active, not a measurement of polymer melt temperature.
"""
import math
from dataclasses import dataclass
from statistics import median


class InsufficientExperiment(ValueError):
    pass


@dataclass(frozen=True)
class CalibrationBounds:
    """User-supplied hard ceilings for every proposed test setpoint/load.

    Limits apply to commanded values. Sensor overshoot must be evaluated
    separately; a temperature setpoint cannot guarantee a measured ceiling.
    Existing firmware limits can only further restrict this envelope.
    """
    max_flow_mm3_s: float
    max_temperature_c: float

    def __post_init__(self):
        for value in (self.max_flow_mm3_s, self.max_temperature_c):
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError('Maximum flow and temperature must be positive finite numbers')

    def validate_probe(self, temperature_c, flow_mm3_s):
        if not math.isfinite(temperature_c) or not 0 < temperature_c <= self.max_temperature_c:
            raise ValueError('Proposed temperature exceeds the permitted test envelope')
        if not math.isfinite(flow_mm3_s) or not 0 <= flow_mm3_s <= self.max_flow_mm3_s:
            raise ValueError('Proposed flow exceeds the permitted test envelope')
        # Passing ceilings does not certify material suitability or cold-extrusion safety.
        return True


def slope(samples):
    mean_t = sum(s['time_s'] for s in samples)/len(samples)
    mean_y = sum(s['temperature_c'] for s in samples)/len(samples)
    denom = sum((s['time_s']-mean_t)**2 for s in samples)
    return sum((s['time_s']-mean_t)*(s['temperature_c']-mean_y) for s in samples)/denom if denom else 0


def fit_temperature_step(samples, step_time_s, min_step_c=2.0,
                         settling_tolerance_c=0.5, max_drift_c_s=0.03,
                         max_sample_gap_s=2.0, settling_tolerance_percent=None,
                         settling_window_s=None):
    """Identify separate candidate heating/cooling models from telemetry.

    Delay and tau come from interpolated 10% and 90% crossings. Residuals
    reject clearly non-first-order records; validation on independent trials
    is required before control. All temperatures/flows remain observations.
    """
    required = ('time_s', 'temperature_c', 'target_c', 'power_fraction',
                'flow_mm3_s', 'fan_fraction')
    if len(samples) < 40:
        raise InsufficientExperiment('Need at least 40 telemetry samples, including baseline and settled tail')
    for row in samples:
        if any(k not in row or not math.isfinite(float(row[k])) for k in required):
            raise InsufficientExperiment('Missing or nonfinite telemetry')
        if not 0 <= row['power_fraction'] <= 1 or not 0 <= row['fan_fraction'] <= 1 or row['flow_mm3_s'] < 0:
            raise InsufficientExperiment('Invalid flow, fan or power telemetry')
    gaps = [b['time_s']-a['time_s'] for a, b in zip(samples, samples[1:])]
    if min(gaps) <= 0 or max(gaps) > max_sample_gap_s:
        raise InsufficientExperiment('Telemetry must be monotonic and have no large sample gaps')
    before = [s for s in samples if s['time_s'] < step_time_s]
    after = [s for s in samples if s['time_s'] >= step_time_s]
    if len(before) < 20 or len(after) < 20:
        raise InsufficientExperiment('Need separate baseline and response records')
    if after[0]['time_s']-step_time_s > max_sample_gap_s:
        raise InsufficientExperiment('Step start missing from telemetry')
    baseline = before[-max(20, len(before)//2):]
    tail = after[-max(20, len(after)//5):]
    if settling_window_s is not None:
        baseline = [s for s in before if s['time_s'] >= before[-1]['time_s']-settling_window_s]
        tail = [s for s in after if s['time_s'] >= after[-1]['time_s']-settling_window_s]
        if any(len(section)<20 or section[-1]['time_s']-section[0]['time_s']<settling_window_s-1 for section in (baseline,tail)):
            raise InsufficientExperiment('Need a complete settled observation window')
    target0, target1 = median(s['target_c'] for s in before), median(s['target_c'] for s in after)
    if any(abs(s['target_c']-target0) > 0.01 for s in before) or any(abs(s['target_c']-target1) > 0.01 for s in after):
        raise InsufficientExperiment('Record must contain exactly one target step')
    if abs(target1-target0) < min_step_c:
        raise InsufficientExperiment('Target step too small to identify reliably')
    for key, tolerance in (('flow_mm3_s', 0.05), ('fan_fraction', 0.02)):
        center = median(s[key] for s in samples)
        if max(abs(s[key]-center) for s in samples) > max(tolerance, abs(center)*0.05):
            raise InsufficientExperiment(f'{key} changed: use an isolated step at constant conditions')
    tolerance0 = tolerance1 = settling_tolerance_c
    if settling_tolerance_percent is not None:
        if isinstance(settling_tolerance_percent,bool) or not math.isfinite(settling_tolerance_percent) or not 0.05<=settling_tolerance_percent<=1:
            raise InsufficientExperiment('Stability tolerance must be between 0.05 and 1 percent')
        tolerance0=abs(target0)*settling_tolerance_percent/100
        tolerance1=abs(target1)*settling_tolerance_percent/100
    for name, section, tolerance in (('baseline', baseline,tolerance0), ('tail', tail,tolerance1)):
        if abs(slope(section)) > max_drift_c_s:
            raise InsufficientExperiment(f'{name} still drifting: extend the observation')
        if max(s['temperature_c'] for s in section)-min(s['temperature_c'] for s in section) > 2*tolerance:
            raise InsufficientExperiment(f'{name} has not settled within tolerance')
        if settling_tolerance_percent is not None and any(abs(s['temperature_c']-(target0 if name=='baseline' else target1))>tolerance for s in section):
            raise InsufficientExperiment(f'{name} outside the selected stability tolerance')
    start = median(s['temperature_c'] for s in baseline)
    final = median(s['temperature_c'] for s in tail)
    delta = final-start
    if abs(delta) < min_step_c or delta*(target1-target0) <= 0:
        raise InsufficientExperiment('Temperature did not follow the target step')
    if abs(start-target0) > tolerance0 or abs(final-target1) > tolerance1:
        raise InsufficientExperiment('Setpoint tracking error: do not accept as a usable response model')

    def crossing(fraction):
        prev_t = 0.0
        prev_progress = 0.0
        for row in after:
            t = row['time_s']-step_time_s
            progress = (row['temperature_c']-start)/delta
            if progress >= fraction and progress > prev_progress:
                return prev_t + (t-prev_t)*(fraction-prev_progress)/(progress-prev_progress)
            prev_t, prev_progress = t, progress
        raise InsufficientExperiment('Response never reached the required fraction')

    t10, t90 = crossing(0.1), crossing(0.9)
    tau = (t90-t10)/math.log(9)
    if tau <= median(gaps)*2:
        raise InsufficientExperiment('Response is too fast for the telemetry sample cadence')
    raw_delay = t10 + tau*math.log(0.9)
    if raw_delay < -max_sample_gap_s:
        raise InsufficientExperiment('Response is inconsistent with a first-order-plus-delay model')
    delay = max(0.0, raw_delay)
    errors = []
    for row in after:
        elapsed = max(0, row['time_s']-step_time_s-delay)
        predicted = start+delta*(1-math.exp(-elapsed/tau))
        errors.append(row['temperature_c']-predicted)
    rmse = math.sqrt(sum(e*e for e in errors)/len(errors))
    if rmse > max(tolerance0, tolerance1, 0.1*abs(delta)):
        raise InsufficientExperiment('First-order fit is poor: use a richer model or repeat the experiment')
    signed_progress = [(s['temperature_c']-start)/delta for s in after]
    if max(signed_progress) > 1.2:
        raise InsufficientExperiment('Excessive overshoot for this candidate model')
    return {
        'model': 'empirical_closed_loop_first_order_plus_delay',
        'direction': 'heating' if delta > 0 else 'cooling',
        'delay_s': delay, 'time_constant_s': tau,
        'sensor_response_95_percent_s': delay-tau*math.log(0.05),
        'gain': delta/(target1-target0), 'fit_rmse_c': rmse,
        'sample_cadence_median_s': median(gaps),
        'heater_saturated_fraction': sum(s['power_fraction'] >= 0.98 for s in after)/len(after),
        'observed_conditions': {
            'start_temperature_c': start, 'end_temperature_c': final,
            'flow_mm3_s': median(s['flow_mm3_s'] for s in samples),
            'fan_fraction': median(s['fan_fraction'] for s in samples),
        },
        'independent_validation_required': True,
        'ready_for_control': False,
        'scope': 'Sensor response at measured conditions only; no melt-quality or maximum-flow inference',
        **({'stability_tolerance_percent':settling_tolerance_percent,'settling_window_s':settling_window_s} if settling_tolerance_percent is not None else {}),
    }


def suggest_next_probe(model, minimum_temperature_c, maximum_temperature_c,
                       maximum_flow_mm3_s):
    """Offline recommendation, not an executable test or hardware action.

    After one successful isolated step, probe the reverse direction before
    changing flow. Keep the same interval instead of assuming faster heating
    allows a larger temperature step. Duration adapts to measured tau/delay.
    """
    if not math.isfinite(minimum_temperature_c) or not math.isfinite(maximum_temperature_c) or minimum_temperature_c >= maximum_temperature_c:
        raise ValueError('Explicit valid material/hardware test bounds are required')
    c = model['observed_conditions']
    start, end = c['end_temperature_c'], c['start_temperature_c']
    if not minimum_temperature_c <= min(start, end) <= max(start, end) <= maximum_temperature_c:
        raise ValueError('Observed interval is outside the permitted test bounds')
    bounds = CalibrationBounds(maximum_flow_mm3_s, maximum_temperature_c)
    bounds.validate_probe(start, c['flow_mm3_s'])
    bounds.validate_probe(end, c['flow_mm3_s'])
    return {
        'action': 'propose_reverse_temperature_step',
        'start_temperature_c': start, 'target_temperature_c': end,
        'flow_mm3_s': c['flow_mm3_s'], 'fan_fraction': c['fan_fraction'],
        'provisional_observation_s': max(20.0, model['delay_s']+5*model['time_constant_s']),
        'completion_condition': 'Continue until settling criteria pass, or stop the trial at its reviewed maximum duration',
        'status': 'proposal_only', 'executes_hardware_actions': False,
    }
