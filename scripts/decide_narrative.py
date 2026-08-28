#!/usr/bin/env python3
"""Apply the rescue plan's narrative decision rule to the measured artefacts.

RESCUE_PLAN_EXHAUSTIVE Part 10 fixes three story architectures and an order:

    B (mechanism)  if PSC-Guard's Part 1.7 Definition of Done holds
    C (cross-stack) else if any pairwise Kendall tau < 0.5
    A (measurement) otherwise -- always available, needs no further compute

The rule was written before the results existed, which is the point: a decision
rule chosen after seeing the data is not a rule, it is a preference.  This
script reads the artefacts and applies it, so the choice is auditable rather
than argued.

It also reports one thing the plan did not anticipate.  The plan's mechanism
branch is keyed on PSC-Guard specifically.  A different mechanism may have
earned a mechanism claim on the evidence even though PSC-Guard did not, so the
script checks, separately, whether ANY scheme beats the strongest published
baseline on the subset of mixes where both hold the acceptance guard.  That is
the comparison a reviewer will demand, and passing it is a mechanism result
whatever the plan's branch says.

    python3 scripts/decide_narrative.py --results results/suscom_ten_mixes
"""
import argparse
import csv
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'stage4_analysis'))

LBM_MIXES = ('gobmkmilcmcf17lbm17', 'lbm17xz17GemsFDTDwrf')
HIT_TOL = 0.005


def read(p):
    with open(p, newline='') as f:
        return list(csv.DictReader(f))


def num(r, k, d=float('nan')):
    v = r.get(k, '')
    try:
        return float(v) if v not in ('', None) else d
    except (TypeError, ValueError):
        return d


def check_dod(results):
    """Part 1.7: on BOTH lbm17 mixes, hit_rate_delta >= -0.005 AND rli > 0."""
    p = os.path.join(results, 'hycsim', 'lifetime_all_mixes.csv')
    if not os.path.exists(p):
        return None, ['lifetime_all_mixes.csv absent']
    rows = [r for r in read(p)
            if r.get('scheme') == 'psc-guard' and r.get('arena') == 'hybrid']
    if not rows:
        return False, ['psc-guard did not run; the mechanism branch cannot be taken']
    notes, met = [], True
    for tag in LBM_MIXES:
        hit = [r for r in rows if tag in r.get('mix', '')]
        if not hit:
            notes.append('%s: no psc-guard row' % tag)
            met = False
            continue
        r = hit[0]
        dh, rli = num(r, 'hit_rate_delta'), num(r, 'rli_direct')
        ok = (dh >= -HIT_TOL) and (rli > 0)
        met = met and ok
        notes.append('%-24s d_hit %+8.5f %s   RLI %+8.4f %s  -> %s'
                     % (tag, dh, 'PASS' if dh >= -HIT_TOL else 'FAIL',
                        rli, 'PASS' if rli > 0 else 'FAIL', 'met' if ok else 'NOT met'))
    return met, notes


def check_tau(results):
    """Part 10 branch C: any pairwise Kendall tau below 0.5."""
    files = [('SOT', 'cross_stack_results.csv'),
             ('STT', 'cross_stack_results_stt.csv'),
             ('PCM', 'cross_stack_results_pcm.csv')]
    data = {}
    for tag, f in files:
        p = os.path.join(results, f)
        if not os.path.exists(p):
            return None, ['%s absent' % f], []
        data[tag] = read(p)

    def rank(rows, metric):
        by = {}
        for r in rows:
            v = num(r, metric)
            if v == v and v > 0:
                by.setdefault((r['scheme'], r['arena']), []).append(math.log(v))
        return {k: math.exp(sum(v) / len(v)) for k, v in by.items()}

    def tau(a, b):
        keys = sorted(set(a) & set(b))
        n = len(keys)
        if n < 2:
            return float('nan')
        c = d = 0
        for i in range(n):
            for j in range(i + 1, n):
                s = (a[keys[i]] - a[keys[j]]) * (b[keys[i]] - b[keys[j]])
                if s > 0:
                    c += 1
                elif s < 0:
                    d += 1
        return (c - d) / (n * (n - 1) / 2.0)

    notes, low = [], []
    for metric in ('total_energy_mJ', 'dynamic_EDP_mJ_ms'):
        r = {t: rank(data[t], metric) for t in data}
        for x, y in (('SOT', 'STT'), ('SOT', 'PCM'), ('STT', 'PCM')):
            t = tau(r[x], r[y])
            flag = ''
            if t == t and t < 0.5:
                low.append((metric, x, y, t))
                flag = '   <-- below 0.5'
            notes.append('%-20s tau(%s,%s) = %+.4f%s' % (metric, x, y, t, flag))
    return (len(low) > 0), notes, low


def check_guarded_headtohead(results):
    """Does ANY scheme beat the strongest published baseline where both hold
    the guard? This is the comparison a reviewer demands, and it is not the
    same question as the plan's PSC-Guard branch."""
    try:
        import sota
    except ImportError:
        return None, ['stage4_analysis/sota.py not importable']
    p = os.path.join(results, 'hycsim', 'lifetime_all_mixes_loo.csv')
    if not os.path.exists(p):
        return None, ['lifetime_all_mixes_loo.csv absent']
    life = read(p)
    by, guard = {}, {}
    for r in life:
        by.setdefault(r['scheme'], {})[r['mix']] = num(r, 'rli_direct')
        guard.setdefault(r['scheme'], {})[r['mix']] = (r.get('selection_guard_passed') == '1')
    mixes = sorted({r['mix'] for r in life})

    def gm(s):
        v = [by[s][m] for m in mixes if m in by.get(s, {}) and by[s][m] > -1]
        return math.expm1(sum(math.log1p(x) for x in v) / len(v)) if v else float('nan')

    pub = [s for s in by if s in sota.PUBLISHED]
    if not pub:
        return None, ['no published scheme present']
    best = max(pub, key=lambda s: gm(s) if gm(s) == gm(s) else -9e9)
    notes = ['strongest published baseline: %s (geomean RLI %.4f)' % (best, gm(best))]
    winners = []
    for c in sorted(by):
        if c in sota.PUBLISHED or c in sota.BASELINES or c == best:
            continue
        d = []
        for m in mixes:
            if not (guard[c].get(m) and guard[best].get(m)):
                continue
            u, v = by[c].get(m), by[best].get(m)
            if u is None or v is None or u != u or v != v or u <= -1 or v <= -1:
                continue
            d.append(math.log1p(u) - math.log1p(v))
        if len(d) < 5:
            continue
        p_, _ = sota.wilcoxon(d)
        w = sum(1 for x in d if x > 0)
        if p_ == p_ and p_ <= 0.05 and w > len(d) - w:
            winners.append((c, len(d), w, p_))
            notes.append('%-14s beats %s on %d guarded mixes, %d-%d, p=%.5f'
                         % (c, best, len(d), w, len(d) - w, p_))
    if not winners:
        notes.append('no scheme beats %s significantly on a guarded subset of '
                     '5+ mixes' % best)
    return (len(winners) > 0), notes


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--results', default='results/suscom_ten_mixes')
    a = ap.parse_args()
    print('narrative decision for %s\n' % os.path.abspath(a.results))

    print('--- branch B test: PSC-Guard Definition of Done (Part 1.7) ---')
    dod, notes = check_dod(a.results)
    for n in notes:
        print('  ' + n)
    print('  => Definition of Done: %s\n'
          % ('MET' if dod else ('NOT MET' if dod is not None else 'UNKNOWN')))

    print('--- branch C test: any pairwise Kendall tau < 0.5 ---')
    low_tau, tnotes, low = check_tau(a.results)
    for n in tnotes:
        print('  ' + n)
    print('  => a ranking fails to transfer: %s\n'
          % ('YES' if low_tau else ('NO' if low_tau is not None else 'UNKNOWN')))

    print('--- additional test the plan did not specify: guarded head-to-head ---')
    beat, hnotes = check_guarded_headtohead(a.results)
    for n in hnotes:
        print('  ' + n)
    print('  => a mechanism beats the published SOTA under the guard: %s\n'
          % ('YES' if beat else ('NO' if beat is not None else 'UNKNOWN')))

    print('=' * 72)
    if dod:
        pick, why = 'B - mechanism paper', 'PSC-Guard met its Definition of Done.'
    elif low_tau:
        pick = 'C - cross-stack interaction paper'
        why = ('PSC-Guard did not meet its Definition of Done, and %d ranking(s) '
               'fail\nto transfer across technologies (lowest tau = %+.4f on %s, '
               '%s vs %s).' % (len(low), min(t for _, _, _, t in low),
                               min(low, key=lambda x: x[3])[0],
                               min(low, key=lambda x: x[3])[1],
                               min(low, key=lambda x: x[3])[2]))
    else:
        pick = 'A - measurement paper'
        why = 'Neither the mechanism nor the cross-stack branch is supported.'
    print('NARRATIVE: %s' % pick)
    print(why)
    if beat:
        print('\nHOWEVER: a mechanism DOES beat the published state of the art on the')
        print('guarded subset (above). The plan\'s branch B is keyed on PSC-Guard')
        print('specifically and is therefore not met, but the mechanism contribution')
        print('is real and belongs in the paper as a supporting section with its')
        print('guard split stated. Lead with %s; carry the mechanism second.'
              % pick.split(' - ')[1])
    print('=' * 72)
    return 0


if __name__ == '__main__':
    sys.exit(main())
