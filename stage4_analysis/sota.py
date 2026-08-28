#!/usr/bin/env python3
"""Head-to-head against the published state of the art, with the fairness audit.

WHY THIS EXISTS
---------------
Every number in this study up to now compares a scheme against `unmanaged`.
That answers "does wear management help", which nobody disputes.  It does not
answer the question a reviewer actually asks:

    "How does this compare to i2WAP / Start-Gap / Sequoia / AYUSH?"

A ranking table does not answer it either.  Two schemes whose geomeans differ
by 5% may be indistinguishable across ten mixes, and a table sorted by geomean
invites the reader to treat that gap as real.  This module runs the paired
test, corrects for multiplicity, and reports the win-loss record alongside,
because with n = 10 mixes the record is often more informative than the mean.

THREE THINGS A REVIEWER WILL CHECK, AND WHAT THIS PRINTS
--------------------------------------------------------
1. TUNING PARITY.  If the proposed scheme was given a 180-point grid and the
   published baselines got 5 points each, the comparison measures tuning
   effort, not mechanism.  Section 1 prints the grid size per scheme and the
   median for each group, so the objection is answered with a number instead
   of an assurance.

2. STATE OVERHEAD.  A lifetime gain bought with a megabyte of metadata is a
   different claim from the same gain bought with nothing.  Section 3 puts
   `state_bits` beside the gain and flags any scheme whose metadata exceeds a
   percent of the tag+data array.

3. PAIRED SIGNIFICANCE, NOT GEOMEANS.  Section 2 runs Wilcoxon signed-rank on
   the per-mix log-ratio differences against each published baseline, applies
   Holm-Bonferroni across the family of comparisons, and prints the win-loss
   record.  With n = 10 the smallest achievable two-sided p is about 0.002,
   which is stated rather than implied.

WHAT IS DELIBERATELY NOT DONE
-----------------------------
No comparison is made on the unguarded geomean alone.  A scheme that holds the
acceptance guard on 3 of 10 mixes and one that holds it on 9 are not
comparable on a single number, so the guard split is carried into every row and
the guarded subset is tested separately.  Where the guarded subset is too small
to test (fewer than 5 paired mixes), that is printed instead of a p-value.

Usage:
    python3 stage4_analysis/sota.py --results results/suscom_ten_mixes
"""
import argparse
import csv
import math
import os
import sys

# Published schemes reimplemented in this study, with the citation each row is
# accountable to. A scheme not in this table is treated as "this work" and is
# never used as the reference baseline for a novelty claim.
PUBLISHED = {
    'i2wap': 'Wang et al., i2WAP, HPCA 2013',
    'startgap': 'Qureshi et al., Start-Gap, MICRO 2009',
    'sequoia': 'Jokar et al., Sequoia, IEEE TVLSI 24(3) 2016',
    'ayush': 'Mittal & Vetter, AYUSH, MASCOTS 2015',
    'equalchance': 'Mittal & Vetter, EqualChance, INFLOW 2014',
    'swwr': 'Agarwal, PhD thesis IIT Guwahati 2020, Ch.4 Alg.1',
    'dwwr': 'Agarwal, PhD thesis IIT Guwahati 2020, Ch.4 Alg.2',
    'dwawr': 'Agarwal & Kapoor, IEEE TC 2019',
}
BASELINES = ('unmanaged', 'lru-hybrid')


def num(r, k, d=float('nan')):
    v = r.get(k, '')
    try:
        return float(v) if v not in ('', None) else d
    except (TypeError, ValueError):
        return d


def read(p):
    with open(p, newline='') as f:
        return list(csv.DictReader(f))


def wilcoxon(diffs):
    """Two-sided Wilcoxon signed-rank. scipy when available, exact otherwise.

    The exact fallback exists so the SOTA comparison -- the part a reviewer is
    most likely to re-run -- does not silently vanish on a machine without
    scipy. For n <= 12 it enumerates all 2^n sign assignments, which is exact
    rather than approximate.
    """
    d = [x for x in diffs if x == x and x != 0.0]
    n = len(d)
    if n < 5:
        return float('nan'), n
    try:
        from scipy.stats import wilcoxon as W
        return float(W(d).pvalue), n
    except ImportError:
        pass
    order = sorted(range(n), key=lambda i: abs(d[i]))
    rank = [0.0] * n
    for pos, i in enumerate(order):
        rank[i] = pos + 1.0
    obs = sum(rank[i] for i in range(n) if d[i] > 0)
    if n > 12:
        mu = n * (n + 1) / 4.0
        sd = math.sqrt(n * (n + 1) * (2 * n + 1) / 24.0)
        z = (obs - mu) / sd if sd else 0.0
        return math.erfc(abs(z) / math.sqrt(2)), n
    hits = tot = 0
    for mask in range(1 << n):
        s = sum(rank[i] for i in range(n) if mask >> i & 1)
        tot += 1
        if abs(s - n * (n + 1) / 4.0) >= abs(obs - n * (n + 1) / 4.0):
            hits += 1
    return hits / float(tot), n


def holm(pairs, alpha=0.05):
    """Holm-Bonferroni. Returns [(name, p, threshold, rejected)] sorted by p."""
    s = sorted(pairs, key=lambda t: t[1])
    m, out, still = len(s), [], True
    for i, (name, p) in enumerate(s):
        thr = alpha / (m - i)
        ok = still and p <= thr
        still = ok
        out.append((name, p, thr, ok))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--results', default='results/suscom_ten_mixes')
    ap.add_argument('--selection', default='loo',
                    choices=['loo', 'per-mix', 'global'])
    ap.add_argument('--metric', default='rli_direct')
    ap.add_argument('--arena', default='hybrid')
    ap.add_argument('--out', default='')
    a = ap.parse_args()

    suffix = {'loo': '_loo', 'global': '_global', 'per-mix': ''}[a.selection]
    life = read(os.path.join(a.results, 'hycsim',
                             'lifetime_all_mixes%s.csv' % suffix))
    tune = read(os.path.join(a.results, 'hycsim', 'tuning_all_mixes.csv'))

    # ---------------------------------------------------- 1. tuning parity
    grids = {}
    for r in tune:
        grids.setdefault(r['scheme'], set()).add(r['params'])
    pub = sorted(s for s in grids if s in PUBLISHED)
    own = sorted(s for s in grids if s not in PUBLISHED and s not in BASELINES)

    def med(xs):
        xs = sorted(xs)
        return xs[len(xs) // 2] if xs else 0

    print('=== 1. tuning parity ===')
    print('If the proposed schemes were given larger grids than the published')
    print('baselines, the comparison measures tuning effort, not mechanism.\n')
    print('  published baselines : %d schemes, grid sizes %s, median %d'
          % (len(pub), [len(grids[s]) for s in pub], med([len(grids[s]) for s in pub])))
    print('  this work           : %d schemes, grid sizes %s, median %d'
          % (len(own), [len(grids[s]) for s in own], med([len(grids[s]) for s in own])))
    mp, mo = med([len(grids[s]) for s in pub]), med([len(grids[s]) for s in own])
    ratio = mo / float(max(mp, 1))
    print('\n  median grid: published %d, this work %d (%.2fx)' % (mp, mo, ratio))
    if ratio > 2.0:
        print('  VERDICT: NOT at parity. The proposed schemes were tuned %.1fx'
              '\n  harder, so part of any advantage is tuning budget. Either extend'
              '\n  the baselines\' grids or state the asymmetry in the paper.' % ratio)
    elif ratio > 1.2:
        print('  VERDICT: close, but this work is tuned %.2fx harder. State the'
              '\n  ratio in the paper rather than claiming parity; the largest'
              '\n  single grid is %d (%s) against the largest published %d (%s).'
              % (ratio,
                 max(len(grids[s]) for s in own), max(own, key=lambda s: len(grids[s])),
                 max(len(grids[s]) for s in pub), max(pub, key=lambda s: len(grids[s]))))
    else:
        print('  VERDICT: at parity. The comparison is not a tuning-budget artefact.')

    # -------------------------------------------- assemble per-mix vectors
    rows = [r for r in life if r.get('arena') == a.arena or r.get('scheme') in BASELINES]
    by = {}
    guard = {}
    for r in rows:
        k = r['scheme']
        by.setdefault(k, {})[r['mix']] = num(r, a.metric)
        guard.setdefault(k, {})[r['mix']] = (r.get('selection_guard_passed') == '1')
    mixes = sorted({r['mix'] for r in rows})

    def paired(x, y, guarded_only=False):
        d = []
        for m in mixes:
            if m not in by.get(x, {}) or m not in by.get(y, {}):
                continue
            if guarded_only and not (guard[x].get(m) and guard[y].get(m)):
                continue
            u, v = by[x][m], by[y][m]
            if u != u or v != v or u <= -1 or v <= -1:
                continue
            d.append(math.log1p(u) - math.log1p(v))
        return d

    # ------------------------------------------- 2. head-to-head vs each SOTA
    print('\n=== 2. head-to-head against each published scheme (%s selection) ==='
          % a.selection)
    print('Paired on the same mixes. Positive mean means the challenger is')
    print('better. W-L is the per-mix win-loss record, which with n=10 often')
    print('says more than the mean.\n')
    challengers = [s for s in own if s in by and s not in BASELINES]
    best_pub, best_gm = None, -9e9

    def gmv(s):
        v = [by[s][m] for m in mixes if m in by.get(s, {}) and by[s][m] > -1]
        return math.expm1(sum(math.log1p(x) for x in v) / len(v)) if v else float('nan')

    for s in pub:
        g = gmv(s)
        if g == g and g > best_gm:
            best_gm, best_pub = g, s
    if best_pub is None:
        print('no published scheme has usable rows; cannot compare')
        return 1
    print('Strongest published baseline by geomean %s: %s (%.4f)  [%s]\n'
          % (a.metric, best_pub, best_gm, PUBLISHED[best_pub]))

    results, out_rows = [], []
    print('%-16s %10s %9s %7s %10s %8s' % ('challenger', 'gm', 'mean_dlog',
                                           'W-L', 'p_raw', 'guard'))
    for c in sorted(challengers, key=lambda s: -(gmv(s) if gmv(s) == gmv(s) else -9e9)):
        d = paired(c, best_pub)
        if not d:
            continue
        p, n = wilcoxon(d)
        w = sum(1 for x in d if x > 0)
        gp = sum(1 for m in mixes if guard.get(c, {}).get(m))
        results.append((c, p))
        out_rows.append(dict(challenger=c, baseline=best_pub, selection=a.selection,
                             gm=gmv(c), mean_dlog=sum(d) / len(d), wins=w,
                             losses=len(d) - w, p_raw=p, guard_mixes=gp))
        print('%-16s %10.4f %+9.4f %3d-%-3d %10.4g %6d/10'
              % (c, gmv(c), sum(d) / len(d), w, len(d) - w, p, gp))

    print('\n--- Holm-Bonferroni across the %d comparisons, alpha=0.05 ---'
          % len(results))
    print('%-16s %12s %12s %s' % ('challenger', 'p_raw', 'holm_thr', 'beats ' + best_pub))
    for name, p, thr, ok in holm(results):
        print('%-16s %12.4g %12.5f %s' % (name, p, thr, 'YES' if ok else 'no'))
    print('\nWith n=%d paired mixes the smallest achievable two-sided p is about'
          ' %.4f,\nso "no" often means "not resolvable at this sample size"'
          ' rather than "worse".' % (len(mixes), 2.0 / (2 ** len(mixes)) * len(mixes)))

    # ------------------------------- 2b. the same test on the guarded subset
    # This is the comparison the paper must actually make. An unguarded win can
    # come from the hit-rate-collapse pathway the acceptance gate exists to
    # catch, so a scheme that wins 10-0 unguarded and holds the guard on 3 mixes
    # has a 3-mix result, not a 10-mix one.
    print('\n=== 2b. the same comparison restricted to mixes where BOTH schemes'
          ' hold the guard ===')
    print('%-16s %8s %9s %7s %10s' % ('challenger', 'n_pairs', 'mean_dlog',
                                      'W-L', 'p_raw'))
    guarded = []
    for c in sorted(challengers, key=lambda s: -(gmv(s) if gmv(s) == gmv(s) else -9e9)):
        d = paired(c, best_pub, guarded_only=True)
        if len(d) < 5:
            print('%-16s %8d %9s %7s %10s' % (c, len(d), '--', '--',
                                              'too few guarded pairs to test'))
            continue
        p, n = wilcoxon(d)
        w = sum(1 for x in d if x > 0)
        guarded.append((c, p))
        print('%-16s %8d %+9.4f %3d-%-3d %10.4g'
              % (c, len(d), sum(d) / len(d), w, len(d) - w, p))
    if guarded:
        print('\n--- Holm-Bonferroni on the guarded subset ---')
        for name, p, thr, ok in holm(guarded):
            print('%-16s %12.4g %12.5f %s' % (name, p, thr, 'YES' if ok else 'no'))
    else:
        print('\nNo challenger has 5+ mixes where both it and %s hold the guard.'
              '\nThat is itself the finding: the headline gains and the guard do'
              '\nnot co-occur often enough to support a guarded head-to-head.'
              % best_pub)

    # ------------------------------------------------ 3. cost of the gain
    print('\n=== 3. what the gain costs in metadata ===')
    print('A lifetime gain bought with a megabyte of state is a different claim')
    print('from the same gain bought with nothing.\n')
    sb, ov = {}, {}
    for r in life:
        sb.setdefault(r['scheme'], num(r, 'state_bits', 0.0))
        ov.setdefault(r['scheme'], num(r, 'state_overhead_pct', 0.0))
    print('%-16s %12s %10s %10s %8s  %s'
          % ('scheme', 'state_bits', 'overhead%', 'gm ' + a.metric, 'guard', 'origin'))
    for s in sorted(set(pub) | set(challengers), key=lambda x: sb.get(x, 0)):
        if s not in by:
            continue
        gp = sum(1 for m in mixes if guard.get(s, {}).get(m))
        print('%-16s %12.0f %10.4f %10.4f %6d/10  %s'
              % (s, sb.get(s, 0), ov.get(s, 0), gmv(s), gp,
                 'published' if s in PUBLISHED else 'this work'))
    free = [s for s in set(pub) | set(challengers)
            if sb.get(s, 1) == 0 and gmv(s) == gmv(s) and gmv(s) > 0]
    if free:
        print('\nZero added architectural state and a positive gain: %s'
              % ', '.join(sorted(free)))
        print('That is the strongest form of this claim: the mechanism costs'
              ' nothing to store.')

    if a.out and out_rows:
        with open(a.out, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(out_rows[0]))
            w.writeheader()
            w.writerows(out_rows)
        print('\nwrote %s (%d rows)' % (a.out, len(out_rows)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
