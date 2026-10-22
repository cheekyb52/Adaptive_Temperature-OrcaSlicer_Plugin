# SPDX-License-Identifier: GPL-3.0-only
"""Read-only Moonraker telemetry recorder. This module can only issue GETs.

Example:
  python calibration_logger.py --url http://printer-address:7125 \
      --heater extruder2 --fan 'fan_generic e2_fan' --seconds 10 --output samples.jsonl

It checks live print state before each telemetry read, as required by the
MCP project. No heat, motion, print, configuration or calibration commands.
The flow value is asserted only for idle no-flow records; during a print it
is null until synchronized motion telemetry has been integrated.
"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
from urllib.parse import quote, urlsplit
from urllib.request import urlopen


class MoonrakerReader:
    def __init__(self, base_url, timeout_s=5.0):
        parsed = urlsplit(base_url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError('Expected an HTTP(S) Moonraker URL without credentials/query/fragment')
        self.base_url = base_url.rstrip('/')
        self.timeout_s = timeout_s

    def server_info(self):
        with urlopen(self.base_url+'/server/info', timeout=self.timeout_s) as response:
            data=json.load(response)
        result=data.get('result')
        if not isinstance(result,dict) or not isinstance(result.get('klippy_state'),str):
            raise RuntimeError('The address did not return Moonraker server information')
        return result

    def query(self, objects):
        query = '&'.join(quote(name, safe='') for name in objects)
        # URL is constructed internally and this is always a read-only endpoint.
        with urlopen(self.base_url+'/printer/objects/query?'+query, timeout=self.timeout_s) as response:
            data = json.load(response)
        if 'error' in data or 'result' not in data:
            raise RuntimeError('Moonraker rejected telemetry query')
        return data['result']

    def sample(self, heater, fan=None):
        checked = self.query(('print_stats', 'virtual_sdcard', 'pause_resume'))
        state = checked['status']
        for key, field in (('print_stats', 'state'), ('virtual_sdcard', 'is_active'), ('pause_resume', 'is_paused')):
            if key not in state or field not in state[key]:
                raise RuntimeError('Cannot verify live print state')
        print_state = state['print_stats']['state']
        active = state['virtual_sdcard']['is_active'] or state['pause_resume']['is_paused'] or print_state in ('printing', 'paused')
        # Still read-only when active. Do not infer extrusion flow from file position.
        result = self.query((heater, 'toolhead') + ((fan,) if fan else ()))
        status = result['status']
        hotend = status[heater]
        for key in ('temperature', 'target', 'power'):
            if key not in hotend or not math.isfinite(float(hotend[key])):
                raise RuntimeError('Incomplete heater telemetry')
        return {
            'time_s': result['eventtime'],
            'utc': datetime.now(timezone.utc).isoformat(),
            'temperature_c': hotend['temperature'], 'target_c': hotend['target'],
            'power_fraction': hotend['power'],
            'flow_mm3_s': None,
            'flow_status': 'unmeasured: idle state alone does not exclude manual extrusion',
            'fan_fraction': status.get(fan, {}).get('speed') if fan else None,
            'physical_heater': heater, 'hotend_state': hotend.get('state'),
            'active_extruder': status['toolhead'].get('extruder'),
            'print_state': print_state, 'active_job_or_pause': bool(active),
            'file_position': state['virtual_sdcard'].get('file_position'),
            'state_check_eventtime': checked.get('eventtime'),
        }


def record(reader, heater, fan, duration_s, output, interval_s=0.5):
    if not 0 < duration_s <= 1800 or not 0.1 <= interval_s <= 10:
        raise ValueError('Recording duration must be 0..1800 seconds and interval 0.1..10 seconds')
    start = time.monotonic()
    count = 0
    # Exclusive create prevents overwriting previous calibration evidence.
    with Path(output).open('x', encoding='utf-8') as stream:
        while time.monotonic()-start < duration_s:
            tick = time.monotonic()
            row = reader.sample(heater, fan)
            row['client_elapsed_s'] = time.monotonic()-start
            stream.write(json.dumps(row, allow_nan=False)+'\n')
            stream.flush()
            count += 1
            remaining = min(interval_s-(time.monotonic()-tick), duration_s-(time.monotonic()-start))
            if remaining > 0:
                time.sleep(remaining)
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--heater', required=True)
    parser.add_argument('--fan')
    parser.add_argument('--seconds', type=float, default=10)
    parser.add_argument('--interval', type=float, default=0.5)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    count = record(MoonrakerReader(args.url), args.heater, args.fan,
                   args.seconds, args.output, args.interval)
    print(f'Recorded {count} read-only telemetry samples to {args.output}')


if __name__ == '__main__':
    main()
