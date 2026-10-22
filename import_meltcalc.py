# SPDX-License-Identifier: GPL-3.0-only
"""Generate offline preset records from the attributed, pinned MeltCalc CSV."""
import csv
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parent
DEST=ROOT/'plugin/orca_flow/resources/meltcalc'
COMMIT='e6bc61fef79f0da85e84977a878dd74d545b25b3'
SOURCE=f'https://github.com/robertsamples/meltcalc/blob/{COMMIT}/data/hotend%20data.csv'


def generate():
    raw=(DEST/'hotend data.csv').read_bytes()
    records=[]
    for row in csv.DictReader(raw.decode('utf-8-sig').splitlines()):
        manufacturer,name=row['Manufacturer'].strip(),row['Hotend'].strip()
        identity=re.sub(r'[^a-z0-9]+','-',(manufacturer.lower()+' '+name.lower()).replace('+',' plus ')).strip('-')
        physical=float(row['Melt zone'])
        effective=float(row['Effective melt zone'] or row['Melt zone'])
        diameter=float(row['Filament (mm)'] or 1.75)
        paths=int(row['Filament paths'] or 1)
        records.append({'id':identity,'manufacturer':manufacturer,'name':name,
                        'physical_length_mm':physical,'effective_length_mm':effective,
                        'filament_diameter_mm':diameter,'filament_paths':paths,
                        'notes':row['Notes'].strip()})
    if len({r['id'] for r in records})!=len(records):
        raise ValueError('Duplicate hotend preset identifiers')
    payload={'schema_version':1,'creator':'Robert Samples','project':'MeltCalc',
             'license':'CC-BY-4.0','license_url':'https://creativecommons.org/licenses/by/4.0/',
             'source_url':SOURCE,'source_commit':COMMIT,'source_csv_sha256':hashlib.sha256(raw).hexdigest(),
             'modifications':'Selected columns normalized to JSON; stable identifiers added. Burst policy is an independent, unvalidated Adaptive Temperature heuristic.',
             'hotends':sorted(records,key=lambda r:(r['manufacturer'].casefold(),r['name'].casefold()))}
    (DEST/'hotends.json').write_text(json.dumps(payload,indent=2,allow_nan=False),encoding='utf-8')
    return payload


if __name__=='__main__':
    data=generate()
    print(f"Generated {len(data['hotends'])} attributed hotend presets from {COMMIT}")
