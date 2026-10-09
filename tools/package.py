"""Development helper: build the submission zip (section 7.1).

    python3 tools/package.py ENTRY1 ENTRY2

Creates A3_<entry1>_<entry2>.zip (entry numbers upper-cased and sorted)
in the repository root, containing only the required files. Executable
bits are kept.
"""

import os
import sys
import zipfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
REQUIRED = ['netcfg/client.sh', 'netcfg/r1.sh', 'netcfg/r2.sh', 'netcfg/server.sh',
            'client/run-client', 'server/run-server', 'README.md', 'report.pdf']


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    entries = sorted(e.upper().replace(' ', '').replace('-', '') for e in sys.argv[1:])
    name = 'A3_%s_%s' % tuple(entries)
    files = list(REQUIRED)
    for f in sorted(os.listdir(os.path.join(ROOT, 'src'))):
        if f.endswith('.py'):
            files.append('src/' + f)

    missing = [f for f in files if not os.path.exists(os.path.join(ROOT, f))]
    if missing:
        sys.exit('missing: %s' % ', '.join(missing))

    path = os.path.join(ROOT, name + '.zip')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in files:
            src = os.path.join(ROOT, f)
            info = zipfile.ZipInfo.from_file(src, '%s/%s' % (name, f))
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(src, 'rb') as fh:
                z.writestr(info, fh.read())
    print('wrote %s' % os.path.relpath(path))
    for f in files:
        print('  %s/%s' % (name, f))


if __name__ == '__main__':
    main()
