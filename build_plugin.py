# SPDX-License-Identifier: GPL-3.0-only
"""Build a dependency-free Orca wheel and a disconnected HTML preview."""
import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parent
VERSION = '0.5.8'
NAME = 'adaptive_temperature'
PACKAGE = NAME+'_v'+VERSION.replace('.', '_')


def write_archive(path, files):
    """Keep rebuilds stable when only filesystem timestamps change."""
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            entry=zipfile.ZipInfo(name, date_time=(1980,1,1,0,0,0))
            entry.compress_type=zipfile.ZIP_DEFLATED
            entry.create_system=3
            entry.external_attr=0o100644 << 16
            archive.writestr(entry,data)


def build():
    source = ROOT/'plugin/orca_flow'
    # Orca shares one interpreter across installed wheels. Its loader imports
    # and unloads by top-level package name, so every release needs a separate
    # namespace even when the distribution/display name remains stable.
    files = {PACKAGE+'/'+p.relative_to(source).as_posix(): p.read_bytes()
             for p in source.rglob('*') if p.is_file() and '__pycache__' not in p.parts
             and 'sessions' not in p.parts and not p.name.startswith('example-')}
    modules = ('flow_evaluator', 'flow_scheduler', 'predictive_thermal', 'predictive_scheduler', 'burst_model', 'tool_profiles', 'configuration_defaults', 'moonraker_connection', 'multi_tool_scheduler', 'flow_temperature_curve', 'thermal_identification',
               'static_probe', 'calibration_logger', 'analyze_thermal_session',
               'analyze_loaded_session', 'analyze_loaded_temperature_session')
    for module in modules:
        text = (ROOT/f'{module}.py').read_text(encoding='utf-8-sig')
        if module == 'flow_evaluator':
            # The wheel owns capability registration; keep only the reusable core.
            text = text.split('\nif __name__ == "__main__":')[0]
        for dependency in modules:
            text = re.sub(rf'^([ \t]*)from {dependency} import ', rf'\1from .{dependency} import ', text, flags=re.M)
        files[f'{PACKAGE}/{module}.py'] = text.encode()
    for helper in ('run_moonraker_calibration.ps1','calibration_geometry.ps1','calibration_response.ps1','calibration_logging.ps1'):
        files[f'{PACKAGE}/resources/{helper}']=(ROOT/helper).read_bytes()
    files[f'{PACKAGE}/LICENSE.txt'] = (ROOT/'LICENSE.txt').read_bytes()
    files[f'{PACKAGE}/COPYING'] = (ROOT/'COPYING').read_bytes()
    info = f'{NAME}-{VERSION}.dist-info'
    files[f'{info}/METADATA'] = (
        f'Metadata-Version: 2.1\nName: {NAME}\nVersion: {VERSION}\n'
        'Summary: Predictive flow and hotend calibration for OrcaSlicer\n'
        'Author: Adaptive Temperature contributors\nLicense: GPL-3.0-only\nRequires-Python: >=3.12\n'
        f'Import-Name: {PACKAGE}\n\nAdaptive Temperature for OrcaSlicer 2.5 nightly.\n').encode()
    files[f'{info}/WHEEL'] = b'Wheel-Version: 1.0\nGenerator: Adaptive Temperature builder\nRoot-Is-Purelib: true\nTag: py3-none-any\n'
    files[f'{info}/top_level.txt'] = (PACKAGE+'\n').encode()
    rows = []
    for name, data in sorted(files.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        rows.append([name, 'sha256='+digest, len(data)])
    rows.append([f'{info}/RECORD', '', ''])
    record = io.StringIO(newline='')
    csv.writer(record).writerows(rows)
    files[f'{info}/RECORD'] = record.getvalue().encode()
    out = ROOT/'dist'/f'Adaptive Temperature {VERSION}'
    out.mkdir(parents=True,exist_ok=True)
    wheel = out/f'{NAME}-{VERSION}-py3-none-any.whl'
    write_archive(wheel,files)
    html = (source/'resources/panel.html').read_text(encoding='utf-8')
    # The preview uses the same page as Orca. It has no HTTP or hardware bridge.
    response = json.loads((source/'resources/default-response.json').read_text(encoding='utf-8'))
    html = html.replace('/*DEFAULT_RESPONSE*/null', json.dumps(response).replace('<', '\\u003c'))
    from configuration_defaults import default_configuration
    html=html.replace('/*DEFAULT_CONFIG*/null',json.dumps(default_configuration()).replace('<','\\u003c'))
    presets = json.loads((source/'resources/meltcalc/hotends.json').read_text(encoding='utf-8'))
    html = html.replace('/*HOTEND_PRESETS*/null', json.dumps(presets).replace('<', '\\u003c'))
    from tool_profiles import fresh_profile, PROFILE_FIELDS
    schema={'fields':PROFILE_FIELDS,'templates':{str(i):fresh_profile(i,'') for i in range(4)}}
    html=html.replace('/*TOOL_SCHEMA*/null',json.dumps(schema).replace('<','\\u003c'))
    preview = out/'Adaptive Temperature preview.html'
    preview.write_text(html, encoding='utf-8')
    release_files=[wheel,preview]
    for name in ('README.txt','CHANGELOG.txt','LICENSE','LICENSE.txt','COPYING'):
        target=out/name
        target.write_bytes((ROOT/name).read_bytes())
        release_files.append(target)
    guide=ROOT/'docs/Adaptive Temperature Guide/Adaptive Temperature User Guide.pdf'
    if guide.is_file():
        target=out/guide.name
        target.write_bytes(guide.read_bytes())
        release_files.append(target)
    bundle=ROOT/'dist'/f'Adaptive-Temperature-{VERSION}.zip'
    write_archive(bundle,{f'Adaptive Temperature {VERSION}/'+file.name:file.read_bytes() for file in release_files})
    print(wheel)
    print(preview)
    print(bundle)
    return wheel


if __name__ == '__main__':
    build()
