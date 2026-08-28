#!/usr/bin/env python3
"""Record tool revisions, environment, config hash and prepared trace hashes."""
import argparse
import glob
import hashlib
import json
import os
import platform
import subprocess
import sys


def command(args):
    try:
        return subprocess.check_output(args, stderr=subprocess.STDOUT, text=True).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        return 'unavailable: %s' % exc


def record(config, destiny_dir, nvmexplorer_dir, hycsim_dir, profiles_dir, out):
    with open(config, 'rb') as src:
        config_sha = hashlib.sha256(src.read()).hexdigest()
    tools = {}
    for name, path in (('DESTINY', destiny_dir), ('NVMExplorer', nvmexplorer_dir),
                       ('HyCSim', hycsim_dir)):
        if path:
            tools[name] = {'path': os.path.abspath(path),
                           'git_commit': command(['git', '-C', path, 'rev-parse', 'HEAD'])}
    traces = []
    for path in sorted(glob.glob(os.path.join(profiles_dir, '**', 'profile.json'), recursive=True)):
        with open(path) as src:
            p = json.load(src)
        traces.append({'mix': os.path.basename(os.path.dirname(path)),
                       'records': p.get('records'),
                       'source_region_sha256': p.get('source_region_sha256'),
                       'clock_hz': p.get('clock_hz')})
    try:
        import numpy
        numpy_version = numpy.__version__
    except ImportError:
        numpy_version = 'unavailable'
    try:
        import pandas
        pandas_version = pandas.__version__
    except ImportError:
        pandas_version = 'unavailable'
    data = {
        'config': os.path.abspath(config),
        'config_sha256': config_sha,
        'python': sys.version,
        'platform': platform.platform(),
        'numpy': numpy_version,
        'pandas': pandas_version,
        'cxx': command(['c++', '--version']).splitlines()[0],
        'tools': tools,
        'prepared_traces': traces,
    }
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, 'w') as dst:
        json.dump(data, dst, indent=2, sort_keys=True)
        dst.write('\n')
    print('wrote %s' % os.path.abspath(out))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config', required=True)
    ap.add_argument('--destiny-dir', required=True)
    ap.add_argument('--nvmexplorer-dir', required=True)
    ap.add_argument('--hycsim-dir', default='')
    ap.add_argument('--profiles-dir', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    record(args.config, args.destiny_dir, args.nvmexplorer_dir, args.hycsim_dir,
           args.profiles_dir, args.out)


if __name__ == '__main__':
    main()
