# SPDX-License-Identifier: GPL-3.0-only
"""Check the source tree and nested release archives for accidental private data.

Reports locations without echoing matching values. This is a release check,
not a guarantee that arbitrary files are safe to publish.
"""
import io
import json
import os
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from build_plugin import VERSION

IGNORED = {'.git', '.build', '__pycache__', '.venv', 'venv', '.pytest_cache'}
PRIVATE_DIRS = {'backups', 'research', 'test project file', 'sessions', '.codex', '.agents'}
PATTERNS = {
    'Windows account path': re.compile(rb'(?i)\b[A-Z]:[\\/]+Users[\\/]+[^\\/\s\x22\x27]+'),
    'Unix account path': re.compile(rb'/(?:home|Users)/[A-Za-z0-9_.-]+/'),
    'private IPv4 address': re.compile(rb'\b(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b'),
    'access token': re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{30,})\b'),
    'private key': re.compile(b'-----BEGIN ' + rb'(?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
}
BLOCKED_SUFFIXES = {'.3mf', '.gcode', '.bgcode', '.jsonl', '.log', '.pyc', '.pem', '.key'}


def public_files(include_dist=True):
    for folder, dirs, names in os.walk(str(ROOT)):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED and (include_dist or d != 'dist'))
        for name in sorted(names):
            path = Path(folder) / name
            if not path.is_symlink():
                yield path


def inspect_bytes(label, data, issues, depth=0):
    for description, pattern in PATTERNS.items():
        match = pattern.search(data)
        if match:
            issues.append({'path': label, 'kind': description,
                           'line': data[:match.start()].count(b'\n') + 1})
    if data.startswith(b'PK\x03\x04'):
        if depth >= 3:
            issues.append({'path': label, 'kind': 'archive nesting exceeds review limit'})
            return
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                bad = archive.testzip()
                if bad:
                    issues.append({'path': label, 'kind': 'archive integrity failure'})
                for member in archive.infolist():
                    parts = Path(member.filename.replace('\\', '/')).parts
                    if any(part.lower() in PRIVATE_DIRS for part in parts) or '..' in parts:
                        issues.append({'path': label + '!' + member.filename, 'kind': 'private or unsafe archive path'})
                    if Path(member.filename).suffix.lower() in BLOCKED_SUFFIXES:
                        issues.append({'path': label + '!' + member.filename, 'kind': 'raw data, log or credential archive member'})
                    if not member.is_dir():
                        inspect_bytes(label + '!' + member.filename, archive.read(member), issues, depth + 1)
        except zipfile.BadZipFile:
            issues.append({'path': label, 'kind': 'invalid ZIP or wheel'})


def check_tree():
    issues = []
    count = 0
    for path in public_files():
        relative = path.relative_to(ROOT)
        if any(part.lower() in PRIVATE_DIRS for part in relative.parts):
            issues.append({'path': relative.as_posix(), 'kind': 'private development directory'})
        if path.suffix.lower() in BLOCKED_SUFFIXES:
            issues.append({'path': relative.as_posix(), 'kind': 'raw data, log or credential file'})
        inspect_bytes(relative.as_posix(), path.read_bytes(), issues)
        count += 1
    allowed = {f'Adaptive Temperature {VERSION}', f'Adaptive-Temperature-{VERSION}.zip',
               f'Adaptive-Temperature-{VERSION}-User-Guide.zip', f'Adaptive-Temperature-{VERSION}-Source.zip'}
    dist = ROOT / 'dist'
    if dist.exists():
        for path in dist.iterdir():
            if path.name not in allowed:
                issues.append({'path': 'dist/' + path.name, 'kind': 'obsolete or unexpected artifact'})
    return {'version': VERSION, 'files_checked': count, 'issues': issues,
            'status': 'passed' if not issues else 'failed'}


def main():
    report = check_tree()
    target = ROOT / '.build'
    target.mkdir(exist_ok=True)
    (target / 'release-check.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
