#!/usr/bin/env python3
"""Stage 3: trace-driven lifetime / write-variation evaluation.

Every scheme is evaluated on the *same physical array* so that the comparison
isolates the management policy.  Two arenas are run:

  all-nvm   16 NVM ways, 0 SRAM ways.  The arena the published wear-levelling
            schemes (i2WAP, EqualChance, Start-Gap) were designed for.
  hybrid    12 NVM ways + 4 SRAM ways at the same total capacity.  Every
            scheme is re-run here, so PSC-VRO is not credited with the benefit
            of simply having SRAM ways present.

Baseline for all RLI figures is `unmanaged` in the *same* arena.
"""
import argparse, csv, json, os, sys, time
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llc import LLC                       # noqa: E402
from metrics import wear_metrics, rli_proxy, rli_direct   # noqa: E402
import policies                            # noqa: E402

ARENAS = {'all-nvm': (16, 0), 'hybrid': (12, 4)}

DEFAULT_SCHEMES = ['unmanaged', 'lru-hybrid', 'psc', 'vro', 'psc-vro',
                   'i2wap', 'equalchance', 'startgap', 'sequoia', 'swwr', 'dwwr']


def load(path, limit=0):
    z = np.load(path)
    op, addr, dirty = z['op'], z['addr'], z['dirty']
    if limit:
        op, addr, dirty = op[:limit], addr[:limit], dirty[:limit]
    return op, addr, dirty


def run_one(scheme, arena, size_mb, op, addr, dirty, params=None):
    nvm, sram = ARENAS[arena]
    if arena == 'all-nvm' and scheme in ('psc', 'psc-vro', 'lru-hybrid'):
        return None                        # meaningless without SRAM ways
    pol = policies.REGISTRY[scheme](**(params or {}))
    llc = LLC(size_mb * (1 << 20), nvm, sram, policy=pol)
    t0 = time.time()
    llc.run(op, addr, dirty)
    dt = time.time() - t0
    w = llc.wear_array(nvm_only=True)
    m = wear_metrics(w)
    row = dict(scheme=scheme, arena=arena, size_MB=size_mb,
               nvm_ways=nvm, sram_ways=sram, config=pol.describe(),
               runtime_s=round(dt, 1), accesses=len(op))
    row.update({k: m[k] for k in ('mu', 'interv', 'intrav', 'max_write', 'total_writes', 'cov')})
    row.update(llc.stats())
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('trace')
    ap.add_argument('-o', '--out', default='lifetime.csv')
    ap.add_argument('--size-mb', type=int, default=16)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--arenas', default='all-nvm,hybrid')
    ap.add_argument('--schemes', default=','.join(DEFAULT_SCHEMES))
    ap.add_argument('--beta-max', type=int, default=1023)
    a = ap.parse_args()

    op, addr, dirty = load(a.trace, a.limit)
    print('trace %s: %d accesses' % (a.trace, len(op)), flush=True)

    rows = []
    for arena in a.arenas.split(','):
        for scheme in a.schemes.split(','):
            kw = {}
            if scheme in ('vro', 'psc-vro'):
                kw['beta_max'] = a.beta_max
            r = run_one(scheme, arena, a.size_mb, op, addr, dirty, kw)
            if r is None:
                continue
            rows.append(r)
            print('  %-12s %-8s mu=%.1f interV=%.3f intraV=%.3f max=%.0f hit=%.3f (%.0fs)' % (
                scheme, arena, r['mu'], r['interv'], r['intrav'], r['max_write'],
                r['hit_rate'], r['runtime_s']), flush=True)

    # RLI against the unmanaged baseline of the same arena
    base = {r['arena']: r for r in rows if r['scheme'] == 'unmanaged'}
    for r in rows:
        b = base.get(r['arena'])
        if not b:
            r['rli_proxy'] = r['rli_direct'] = ''
            continue
        bm = {k: b[k] for k in ('mu', 'interv', 'intrav', 'max_write')}
        pm = {k: r[k] for k in ('mu', 'interv', 'intrav', 'max_write')}
        r['rli_proxy'] = rli_proxy(bm, pm)
        r['rli_direct'] = rli_direct(bm, pm)
        r['intrav_reduction'] = 1 - r['intrav'] / b['intrav'] if b['intrav'] else ''
        r['interv_reduction'] = 1 - r['interv'] / b['interv'] if b['interv'] else ''

    fields = list(rows[0].keys())
    with open(a.out, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print('wrote', a.out)


if __name__ == '__main__':
    main()
