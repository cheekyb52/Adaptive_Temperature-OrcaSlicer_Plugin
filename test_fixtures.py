# SPDX-License-Identifier: GPL-3.0-only
"""Synthetic calibration data shared by offline regression tests."""
import json
import math
from pathlib import Path


def response_profile():
    return {
        'schema_version': 1, 'name': 'Synthetic loaded sensor response',
        'source': 'synthetic_fixture', 'configuration_state': 'configured',
        'independently_validated': False,
        'binding': {'printer_name': 'Example printer', 'physical_tool': 2,
                    'material': 'Example PLA', 'nozzle_diameter_mm': .8,
                    'hotend': 'Example hotend / 0.8 mm'},
        'heating': {'delay_s': 2, 'time_constant_s': 1.5},
        'cooling': {'delay_s': 3, 'time_constant_s': 2.5},
        'observed_conditions': {'flow_mm3_s': 15, 'fan_fraction': 0},
        'scope': 'Synthetic model for tests; not a measured printer calibration.',
    }


def write_response_session(directory):
    """Write a completed synthetic 220 -> 230 -> 220 C, 15 mm3/s session."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(200):
        t = i * .5
        target = 220 if t < 25 or t >= 60 else 230
        if t < 25:
            temperature = 220
        elif t < 60:
            temperature = 220 + 10 * (1 - math.exp(-max(0, t - 26) / 3))
        else:
            temperature = 230 - 10 * (1 - math.exp(-max(0, t - 61) / 4))
        rows.append(dict(stage='loaded_temperature_flow', time_s=t,
                         temperature_c=temperature, target_c=target,
                         flow_mm3_s=15, fan_fraction=0, power_fraction=.5,
                         physical_tool=2))
    events = [
        {'event': 'session_start', 'data': {'physical_tool': 2, 'action': 'LoadedThermal'}},
        {'event': 'loaded_temperature_sequence', 'data': {
            'commanded_flow_mm3_s': 15,
            'phases': [{'target_c': t} for t in (220, 230, 220)]}},
        {'event': 'heater_off', 'data': {}},
        {'event': 'session_complete', 'data': {}},
    ]
    for name, records in [('telemetry.jsonl', rows), ('events.jsonl', events)]:
        (directory / name).write_text(
            ''.join(json.dumps(record) + '\n' for record in records), encoding='utf-8')
    return directory
