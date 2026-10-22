# SPDX-License-Identifier: GPL-3.0-only
"""Prepare bounded E-only extrusion segments for review; never connect or run.

Requires an already positioned/heated physical tool, verified tool mapping,
relative E mode, 100% speed/flow scaling and a collection area. These commands
alone are not a complete calibration job. They must not be sent to a printer
without the session's state, temperature, limit and telemetry checks.
"""
import math

from thermal_identification import CalibrationBounds


def extrusion_probe(flow_mm3_s, temperature_c, duration_s, bounds,
                    filament_diameter_mm=1.75, max_extrude_only_velocity=100,
                    max_extrude_only_distance=200, segment_duration_s=2):
    bounds.validate_probe(temperature_c, flow_mm3_s)
    values = (flow_mm3_s, duration_s, filament_diameter_mm,
              max_extrude_only_velocity, max_extrude_only_distance, segment_duration_s)
    if any(not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError('Extrusion geometry, flow, duration and limits must be positive finite values')
    area = math.pi*(filament_diameter_mm/2)**2
    velocity = flow_mm3_s/area
    if velocity > max_extrude_only_velocity:
        raise ValueError('Requested flow exceeds firmware extrude-only velocity limit')
    # Round feed DOWN so serialized commands cannot exceed the requested ceiling.
    feed = math.floor(velocity*60*1000)/1000
    if feed <= 0:
        raise ValueError('Requested feed is below command resolution')
    velocity = feed/60
    remaining = velocity*duration_s
    chunk = min(velocity*segment_duration_s, max_extrude_only_distance*0.5)
    chunk = math.floor(chunk*100000)/100000
    if chunk <= 0:
        raise ValueError('Segment is below extrusion command resolution')
    commands = []
    total_e = 0.0
    while remaining >= 0.00001:
        amount = min(chunk, math.floor((remaining+1e-10)*100000)/100000)
        if amount <= 0:
            break
        commands.append(f'G1 E{amount:.5f} F{feed:.3f}')
        remaining -= amount
        total_e += amount
    return {
        'status': 'review_only_not_executed',
        'temperature_target_c': temperature_c,
        'requested_flow_mm3_s': flow_mm3_s,
        'commanded_cruise_flow_mm3_s': velocity*area,
        'filament_feed_mm_s': velocity,
        'requested_duration_s': duration_s,
        'nominal_cruise_duration_s': total_e/velocity,
        'filament_length_mm': total_e,
        'commanded_volume_mm3': total_e*area,
        'commands': commands,
        'xyz_motion': False,
        'conditions': [
            'Existing firmware extrusion acceleration makes instantaneous flow lower during segment ramps.',
            'Controller-specific E-only look-ahead must be validated; do not assume zero gaps between chunks.',
            'No temperature, tool, motion-mode or speed/flow-scaling commands are included here.',
            'Free-air extrusion measures thermal load but does not establish quality at deposition backpressure.'
        ]
    }


def first_load_probe(max_flow_mm3_s, max_temperature_c, **kwargs):
    """Conservative first proposal scales with the supplied flow envelope."""
    return extrusion_probe(max_flow_mm3_s*0.05, max_temperature_c, 20,
                           CalibrationBounds(max_flow_mm3_s, max_temperature_c), **kwargs)


def loaded_response_probe(flow_mm3_s, temperature_c):
    """Bounded, constant-temperature flow-disturbance experiment.

    This observes load sag/recovery. It does not identify temperature-setpoint
    step response at constant flow, nor prove delivered flow or print quality.
    """
    bounds = CalibrationBounds(flow_mm3_s, temperature_c)
    phases = []
    commands = []
    offset = 0.0
    for name, fraction, seconds in (
            ('low', 0.25, 5), ('medium', 0.5, 5),
            ('full', 1.0, 30), ('reduced', 0.25, 15)):
        probe = extrusion_probe(flow_mm3_s*fraction, temperature_c, seconds, bounds)
        phases.append({
            'name': name, 'start_offset_s': offset, 'duration_s': seconds,
            'requested_flow_mm3_s': flow_mm3_s*fraction,
            'commanded_flow_mm3_s': probe['commanded_cruise_flow_mm3_s'],
            'command_count': len(probe['commands']),
        })
        commands.extend(probe['commands'])
        offset += seconds
    return {
        'version': '0.1', 'type': 'loaded_flow_response',
        'physical_tool': 2, 'temperature_target_c': temperature_c,
        'maximum_requested_flow_mm3_s': flow_mm3_s,
        'total_requested_duration_s': offset,
        'filament_diameter_mm': 1.75,
        'phases': phases, 'commands': commands,
        'xyz_motion': False,
        'status': 'prepared_not_executed',
    }
