#!/usr/bin/env python3
"""Create a bounded smoke trace from HyCSim's official example trace."""
import argparse
import os
import urllib.request

URL = ('https://gitlab.com/uz-gaz/hycsim/-/raw/main/'
       'traces_dir/example_trace.txt')
OPS = {b'GetS', b'GetX', b'L2_Rpl'}


def open_source(hycsim_dir):
    if hycsim_dir:
        path = os.path.join(os.path.abspath(hycsim_dir), 'traces_dir', 'example_trace.txt')
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        return open(path, 'rb'), path
    request = urllib.request.Request(URL, headers={'User-Agent': 'mfs-integration/1.0'})
    return urllib.request.urlopen(request, timeout=60), URL


def fetch(out, events=20_000, warmup=2_000, hycsim_dir=''):
    if not 0 <= warmup < events:
        raise ValueError('--warmup must be >=0 and less than --events')
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    src, label = open_source(hycsim_dir)
    n = 0
    with src, open(out, 'wb') as dst:
        dst.write(b'# bounded smoke trace derived from: ' + label.encode() + b'\n')
        for line in src:
            p = line.split()
            if len(p) < 4 or p[2] not in OPS:
                continue
            if n == warmup:
                dst.write(b'WARMUP COMPLETED\n')
            dst.write(line if line.endswith(b'\n') else line + b'\n')
            n += 1
            if n >= events:
                break
    if n != events:
        raise RuntimeError('source ended at %d valid events (wanted %d)' % (n, events))
    print('wrote %s from %s (%d events; %d measured)' %
          (os.path.abspath(out), label, events, events - warmup))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', default='traces/sample_raw/example_trace.txt')
    ap.add_argument('--events', type=int, default=20_000)
    ap.add_argument('--warmup', type=int, default=2_000)
    ap.add_argument('--hycsim-dir', default='',
                    help='read <dir>/traces_dir/example_trace.txt; otherwise download official raw file')
    args = ap.parse_args()
    fetch(args.out, args.events, args.warmup, args.hycsim_dir)


if __name__ == '__main__':
    main()
