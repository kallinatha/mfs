#!/usr/bin/env python3
"""Prepare all raw HyCSim mixes with bounded-memory conversion."""
import argparse
import glob
import os
import sys
from concurrent.futures import ProcessPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'stage3_hycsim'))
from prep_trace import convert


def one(job):
    path, out, clock_hz, limit, force = job
    convert(path, out, limit, clock_hz, force)
    return out


def prepare(raw_dir, prepared_dir, pattern='*.txt', workers=2,
            clock_hz=2.0e9, limit=0, force=False):
    paths = sorted(glob.glob(os.path.join(os.path.abspath(raw_dir), pattern)))
    if not paths:
        raise FileNotFoundError('no traces matching %s in %s' % (pattern, raw_dir))
    os.makedirs(prepared_dir, exist_ok=True)
    jobs = []
    for path in paths:
        name = os.path.splitext(os.path.basename(path))[0]
        out = os.path.join(os.path.abspath(prepared_dir), name)
        if os.path.exists(os.path.join(out, 'profile.json')) and not force:
            print('reuse %s' % out)
            continue
        jobs.append((path, out, clock_hz, limit, force))
    if workers <= 1:
        iterator = map(one, jobs)
        for out in iterator:
            print('prepared %s' % out)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for out in pool.map(one, jobs):
                print('prepared %s' % out)
    return len(paths)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--raw-dir', required=True)
    ap.add_argument('--prepared-dir', required=True)
    ap.add_argument('--pattern', default='*.txt')
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--clock-hz', type=float, default=2.0e9)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--force', action='store_true')
    args = ap.parse_args()
    prepare(args.raw_dir, args.prepared_dir, args.pattern, args.workers,
            args.clock_hz, args.limit, args.force)


if __name__ == '__main__':
    main()
