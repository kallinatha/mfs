#!/usr/bin/env python3
"""Stage 3 driver: tune every competing scheme, then run the tuned comparison.

Untuned baselines are the most common unintentional bias in wear-levelling
papers: the proposed scheme gets its parameters swept, the competitors get
whatever the original paper printed.  Here every scheme - including the
proposed one - gets the same treatment:

  Phase 1  parameter search on a held-out prefix of the trace
  Phase 2  the winning configuration of each scheme re-run on the full trace

Selection metric is RLI measured directly from the peak frame write count
(max_base / max_prop - 1), not the closed-form estimator, so the search cannot
be won by inflating the estimator's variance terms.
"""
import argparse, csv, itertools, json, os, sys, time
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llc import LLC
from metrics import wear_metrics, rli_proxy, rli_direct, multiplicative_null
import policies

ARENAS = {'all-nvm': (16, 0), 'hybrid': (12, 4)}

GRID = {
    'unmanaged':   [{}],
    'lru-hybrid':  [{}],
    'psc':         [{}],
    'vro':         [{'beta_max': b} for b in (256, 512, 1023, 2048, 4096)],
    'psc-vro':     [{'beta_max': b} for b in (256, 512, 1023, 2048, 4096)],
    'i2wap':       [{'polf_threshold': p, 'swapshift_interval': s}
                    for p in (10, 25, 50, 100) for s in (10_000, 100_000)],
    'equalchance': [{'relocate_interval': r} for r in (4, 8, 16, 32, 64)],
    'startgap':    [{'gap_interval': g} for g in (25, 50, 100, 200, 400)],
    'sequoia':     [{'group': g, 'oas': o, 'wad': wd}
                    for g in (16, 64, 256) for (o, wd) in ((1, 1), (1, 0), (0, 1))],
    'swwr':        [{'m': m, 'interval': i}
                    for m in (2, 4) for i in (500_000, 1_000_000, 2_000_000)],
    'dwwr':        [{'m': m, 'interval': i}
                    for m in (2, 4) for i in (500_000, 1_000_000, 2_000_000)],
    # --- proposed variants ---
    'vro-rank':     [{'trigger': t, 'pool': p}
                     for t in (2, 4, 8, 16) for p in (2, 3, 4)],
    'psc-pred':     [{'entries': e, 'threshold': th}
                     for e in (1024, 4096, 16384) for th in (1, 2, 3)],
    'psc-vro-rank': [{'entries': e, 'threshold': th, 'trigger': tr, 'pool': p}
                     for e in (4096,) for th in (1, 2) for tr in (4, 8, 16) for p in (2, 4)],
    'wear-swap':    [{'interval': i} for i in (5000, 20000, 80000)],
    'full':         [{'entries': e, 'threshold': th, 'trigger': tr, 'pool': p, 'interval': i}
                     for e in (4096,) for th in (1, 2) for tr in (4, 8) for p in (2, 4)
                     for i in (20000, 80000)],
    # --- schemes below were registered in policies/ but absent from this GRID,
    # so the production replay silently never ran them. The conformance suite
    # passing told us the mechanisms work; it said nothing about the ten real
    # mixes. Grids are deliberately bounded; every knob here is a PARAMS key.
    #
    # psc-balance's max_sram_frac / min_nvm_frac double as the write-pressure
    # guard: the lbm17-heavy mixes showed PSC losing 23-25% hit rate when the
    # dirty partition saturates, and the occupancy cap is the lever that bounds
    # exactly that failure. Tuning it per-mix (and LOO) is the fix, measured
    # rather than asserted.
    'psc-balance':  [{'hot_threshold': h, 'max_sram_frac': c, 'min_nvm_frac': f}
                     for h in (1.01, 1.02, 1.05)
                     for c in (0.30, 0.45, 0.60) for f in (0.25, 0.40)],
    'tailguard':    [{'kappa': k, 'candidates': c}
                     for k in (1.5, 2.0, 3.0) for c in (2, 4, 8)],
    'ayush':        [{'adaptive': 1}] + [{'adaptive': 0, 'Z': z} for z in (2, 4, 8)],
    'dwawr':        [{'interval': i, 'n_restricted': n}
                     for i in (1024, 4096, 16384) for n in (1, 2, 4)],
    'elba':         [{'gwab_entries': e, 'elastic': el}
                     for e in (1024, 2048, 4096) for el in (0, 1)],
    # PSC-Guard: theta brackets 'never binds' (0.9) to 'binds early' (0.6);
    # ewma_shift brackets 2^s >> ways=16 and 2^s << measured burst length.
    'psc-guard':    ([{'theta': t, 'reserve': 1} for t in (0.25, 0.5, 0.75)]
                     + [{'theta': 0.75, 'reserve': 0}]),   # reserve=0 = plain-PSC ablation
    'psc-soft':     [{'threshold': t, 'max_sram_frac': c, 'use_rank': r}
                     for t in (1, 2) for c in (0.30, 0.45, 0.60) for r in (0, 1)],
}

HYBRID_ONLY = {'psc', 'psc-vro', 'lru-hybrid', 'psc-pred', 'psc-vro-rank', 'full',
               # SRAM-partition mechanisms are undefined with zero SRAM ways:
               'psc-balance', 'psc-soft', 'ayush', 'psc-guard'}


DAMAGE_KW = {}          # set from the CLI; empty means the model is off


def sim(scheme, arena, size_mb, op, addr, dirty, params):
    nvm, sram = ARENAS[arena]
    pol = policies.REGISTRY[scheme](**params)
    llc = LLC(size_mb * (1 << 20), nvm, sram, policy=pol)
    if DAMAGE_KW:
        import damage as _damage
        _damage.attach(llc, **DAMAGE_KW)
    t0 = time.time()
    llc.run(op, addr, dirty)
    m = wear_metrics(llc.wear_array(True))
    r = dict(scheme=scheme, arena=arena, size_MB=size_mb, params=json.dumps(params),
             runtime_s=round(time.time() - t0, 1), accesses=len(op))
    r.update({k: m[k] for k in ('mu', 'interv', 'intrav', 'max_write', 'total_writes', 'cov')})
    r.update(llc.stats())
    r['state_bits'] = pol.state_bits() if hasattr(pol, 'state_bits') else 0
    # management state as a fraction of the array's own tag+data storage
    tagdata = llc.sets * llc.ways * (llc.blocksize * 8 + 32)
    r['state_overhead_pct'] = 100.0 * r['state_bits'] / tagdata
    for extra in ('interventions', 'steered', 'swaps',
                  'pg_admitted', 'pg_rejected', 'pg_drained', 'pg_d_max_hits'):
        r[extra] = getattr(pol, extra, '')
    return r


def add_rli(rows):
    base = {r['arena']: r for r in rows if r['scheme'] == 'unmanaged'}
    for r in rows:
        b = base.get(r['arena'])
        if not b:
            continue
        bm = {k: b[k] for k in ('mu', 'interv', 'intrav', 'max_write')}
        pm = {k: r[k] for k in ('mu', 'interv', 'intrav', 'max_write')}
        r['rli_proxy'] = rli_proxy(bm, pm)
        r['rli_direct'] = rli_direct(bm, pm)
        r['intrav_reduction'] = (1 - r['intrav'] / b['intrav']) if b['intrav'] else ''
        r['interv_reduction'] = (1 - r['interv'] / b['interv']) if b['interv'] else ''
        r['write_amplification'] = (r['total_writes'] / b['total_writes']) if b['total_writes'] else ''
    return rows


def dump(rows, path):
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print('wrote', path, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('trace')
    ap.add_argument('--size-mb', type=int, default=1)
    ap.add_argument('--tune-accesses', type=int, default=8_000_000)
    ap.add_argument('--outdir', default='results')
    ap.add_argument('--tag', default='')
    ap.add_argument('--hit-tol', type=float, default=0.005,
                    help='max allowed hit-rate loss vs unmanaged (absolute)')
    ap.add_argument('--min-nvm-share', type=float, default=0.25,
                    help='min NVM mean write count as a fraction of the unmanaged baseline')
    a = ap.parse_args()

    z = np.load(a.trace)
    op, addr, dirty = z['op'], z['addr'], z['dirty']
    print('trace: %d accesses, LLC %d MB' % (len(op), a.size_mb), flush=True)
    tn = min(a.tune_accesses, len(op))
    top, taddr, tdirty = op[:tn], addr[:tn], dirty[:tn]

    tag = a.tag or ('%dMB' % a.size_mb)
    os.makedirs(a.outdir, exist_ok=True)

    # ---------------------------------------------------------------- phase 1
    print('\n== phase 1: parameter search on %d accesses ==' % tn, flush=True)
    tune_rows = []
    for arena in ARENAS:
        for scheme, grid in GRID.items():
            if arena == 'all-nvm' and scheme in HYBRID_ONLY:
                continue
            for p in grid:
                r = sim(scheme, arena, a.size_mb, top, taddr, tdirty, p)
                tune_rows.append(r)
                print('  %-12s %-8s %-42s max=%.0f intraV=%.3f (%.0fs)' % (
                    scheme, arena, json.dumps(p), r['max_write'], r['intrav'],
                    r['runtime_s']), flush=True)
    add_rli(tune_rows)
    dump(tune_rows, os.path.join(a.outdir, 'tuning_%s.csv' % tag))

    # Selection is constrained, not free.  RLI has a degenerate optimum: a policy
    # that simply stops using the NVM partition drives peak NVM writes to zero
    # and scores an unbounded lifetime gain while turning a 16-way cache into a
    # 4-way one.  A configuration is therefore only eligible if it keeps the hit
    # rate within `hit_tol` of the unmanaged baseline in the same arena, and
    # keeps the NVM partition carrying at least `min_nvm_share` of allocations.
    base_hit = {r['arena']: r['hit_rate'] for r in tune_rows if r['scheme'] == 'unmanaged'}
    base_mu = {r['arena']: r['mu'] for r in tune_rows if r['scheme'] == 'unmanaged'}
    best, rejected = {}, []
    for r in tune_rows:
        k = (r['scheme'], r['arena'])
        v = r.get('rli_direct')
        if v is None or v != v:
            v = -1e9
        bh, bm = base_hit.get(r['arena'], 0.0), base_mu.get(r['arena'], 1.0)
        ok = (r['hit_rate'] >= bh - a.hit_tol) and (bm == 0 or r['mu'] >= a.min_nvm_share * bm)
        r['eligible'] = int(ok)
        if not ok:
            rejected.append((k, json.loads(r['params']), v, r['hit_rate'], r['mu']))
            continue
        if k not in best or v > best[k][0]:
            best[k] = (v, json.loads(r['params']))
    # A scheme whose entire grid was rejected still needs a representative, but
    # picking an arbitrary row would silently report a configuration that failed
    # the guard.  Take the one that violates the hit-rate constraint least, and
    # record that it was a fallback.
    fallbacks = set()
    for k in {(r['scheme'], r['arena']) for r in tune_rows}:
        if k in best:
            continue
        cand = [r for r in tune_rows if (r['scheme'], r['arena']) == k]
        bh_k = base_hit.get(k[1], 0.0)
        cand.sort(key=lambda r: (-(r['hit_rate'] - bh_k), -(r.get('rli_direct') or -1e9)))
        best[k] = (cand[0].get('rli_direct') or 0.0, json.loads(cand[0]['params']))
        fallbacks.add(k)
    if fallbacks:
        print('\nno configuration met the guard for: %s' %
              ', '.join('%s/%s' % k for k in sorted(fallbacks)), flush=True)
    if rejected:
        print('\nrejected by the hit-rate / NVM-utilisation guard: %d configurations' % len(rejected))
        for k, p, v, h, m in rejected[:12]:
            print('  %-14s %-8s %-46s RLI_direct %.2f but hit %.4f, mu %.1f' % (
                k[0], k[1], json.dumps(p), v, h, m), flush=True)
    print('\nselected configurations:')
    for k in sorted(best):
        print('  %-14s %-8s %s  (tuning RLI_direct %.3f)' % (k[0], k[1], json.dumps(best[k][1]), best[k][0]),
              flush=True)

    # ---------------------------------------------------------------- phase 2
    print('\n== phase 2: full trace ==', flush=True)
    rows = []
    for (scheme, arena), (_, p) in sorted(best.items()):
        r = sim(scheme, arena, a.size_mb, op, addr, dirty, p)
        rows.append(r)
        print('  %-12s %-8s mu=%.1f interV=%.3f intraV=%.3f max=%.0f hit=%.4f (%.0fs)' % (
            scheme, arena, r['mu'], r['interv'], r['intrav'], r['max_write'],
            r['hit_rate'], r['runtime_s']), flush=True)
    add_rli(rows)
    dump(rows, os.path.join(a.outdir, 'lifetime_%s.csv' % tag))

    # -------------------------------------------------- super-additivity check
    g = {(r['scheme'], r['arena']): r for r in rows}
    out = {}
    for metric in ('rli_direct', 'rli_proxy'):
        try:
            psc = g[('psc', 'hybrid')][metric]
            vro = g[('vro', 'all-nvm')][metric]
            both = g[('psc-vro', 'hybrid')][metric]
            out[metric] = dict(psc=psc, vro=vro, combined=both,
                               additive_null=psc + vro,
                               multiplicative_null=multiplicative_null(psc, vro),
                               excess_over_additive=both - (psc + vro),
                               excess_over_multiplicative=both - multiplicative_null(psc, vro))
        except KeyError:
            pass
    for metric in ('rli_direct', 'rli_proxy'):
        try:
            p2 = g[('psc-pred', 'hybrid')][metric]
            v2 = g[('vro-rank', 'all-nvm')][metric]
            b2 = g[('psc-vro-rank', 'hybrid')][metric]
            out[metric + '_proposed'] = dict(
                psc_pred=p2, vro_rank=v2, combined=b2,
                additive_null=p2 + v2,
                multiplicative_null=multiplicative_null(p2, v2),
                excess_over_additive=b2 - (p2 + v2),
                excess_over_multiplicative=b2 - multiplicative_null(p2, v2))
        except KeyError:
            pass
    with open(os.path.join(a.outdir, 'ablation_%s.json' % tag), 'w') as f:
        json.dump(out, f, indent=2)
    print('\nablation / composition check:')
    print(json.dumps(out, indent=2), flush=True)


if __name__ == '__main__':
    main()
