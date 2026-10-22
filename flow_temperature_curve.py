# SPDX-License-Identifier: GPL-3.0-only
"""Offline temperature estimates from measured reliable flow limits.

This curve answers what temperature to request. The thermal response model
separately answers when to request it. Interpolation is a hypothesis until
intermediate flow/temperature combinations pass independent validation.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class FlowPoint:
    temperature_c: float
    max_reliable_flow_mm3_s: float


class FlowOutsideEnvelope(ValueError):
    pass


class FlowTemperatureCurve:
    def __init__(self, points, *, max_temperature_c, max_flow_mm3_s,
                 flow_headroom_fraction, interpolation='linear'):
        self.points = sorted(points, key=lambda p: p.temperature_c)
        if len(self.points) < 2:
            raise ValueError('At least two measured flow/temperature points are required')
        if not math.isfinite(flow_headroom_fraction) or not 0 <= flow_headroom_fraction < 1:
            raise ValueError('Headroom must be a fraction from 0 to less than 1')
        if interpolation not in ('linear', 'upper_anchor'):
            raise ValueError('Unknown interpolation method')
        if not all(math.isfinite(v) and v > 0 for v in (max_temperature_c, max_flow_mm3_s)):
            raise ValueError('Explicit positive finite ceilings are required')
        for i, point in enumerate(self.points):
            if not all(math.isfinite(v) and v > 0 for v in (point.temperature_c, point.max_reliable_flow_mm3_s)):
                raise ValueError('Curve points must be positive finite measurements')
            if point.temperature_c > max_temperature_c or point.max_reliable_flow_mm3_s > max_flow_mm3_s:
                raise ValueError('Curve point exceeds the test envelope')
            if i and (point.temperature_c <= self.points[i-1].temperature_c or
                      point.max_reliable_flow_mm3_s < self.points[i-1].max_reliable_flow_mm3_s):
                raise ValueError('Temperatures must increase and flow limits must not decrease; review inconsistent measurements')
        self.headroom = flow_headroom_fraction
        self.interpolation = interpolation

    def temperature_for_flow(self, flow_mm3_s):
        if not math.isfinite(flow_mm3_s) or flow_mm3_s < 0:
            raise ValueError('Flow must be nonnegative and finite')
        # Reserve flow margin BEFORE inverting the measured capacity curve.
        required_capacity = flow_mm3_s/(1-self.headroom)
        if required_capacity > self.points[-1].max_reliable_flow_mm3_s:
            raise FlowOutsideEnvelope('No calibrated temperature supports this flow with the selected headroom; reduce flow or recalibrate within explicit limits')
        first = self.points[0]
        if required_capacity <= first.max_reliable_flow_mm3_s:
            temperature, bracket, interpolated = first.temperature_c, [first.temperature_c], False
        else:
            for lower, upper in zip(self.points, self.points[1:]):
                if required_capacity <= upper.max_reliable_flow_mm3_s:
                    bracket = [lower.temperature_c, upper.temperature_c]
                    if self.interpolation == 'upper_anchor' or required_capacity == upper.max_reliable_flow_mm3_s:
                        temperature, interpolated = upper.temperature_c, False
                    else:
                        fraction = (required_capacity-lower.max_reliable_flow_mm3_s)/(upper.max_reliable_flow_mm3_s-lower.max_reliable_flow_mm3_s)
                        temperature = lower.temperature_c+fraction*(upper.temperature_c-lower.temperature_c)
                        interpolated = True
                    break
        return {
            'temperature_target_estimate_c': temperature,
            'requested_flow_mm3_s': flow_mm3_s,
            'capacity_required_with_headroom_mm3_s': required_capacity,
            'temperature_bracket_c': bracket,
            'interpolated': interpolated,
            'below_tested_temperature_range': False,
            'ready_for_automatic_control': False,
            'note': 'Independent print validation and thermal lead-time calibration remain required; zero flow does not imply a standby/off policy.',
        }
