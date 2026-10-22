# SPDX-License-Identifier: GPL-3.0-only
"""Bundle the reviewed source and documentation without generated/local files."""
import zipfile

from check_public_release import ROOT, VERSION, check_tree, public_files


def main():
    report = check_tree()
    if report['issues']:
        raise SystemExit('Public-release checks failed; run tools/check_public_release.py for details.')
    output = ROOT / 'dist' / f'Adaptive-Temperature-{VERSION}-Source.zip'
    files = list(public_files(include_dist=False))
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, f'Adaptive-Temperature-{VERSION}/' + path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
    print(f'{output.name}: {len(files)} source/documentation files')


if __name__ == '__main__':
    main()
