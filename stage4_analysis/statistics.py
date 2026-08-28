#!/usr/bin/env python3
"""Statistics for lifetime comparisons: aggregation, uncertainty, validation.

Four things the manuscript needs and does not have.

1. Geometric-mean ratios with a paired bootstrap confidence interval.
   Arithmetic means of ratios are wrong and reviewers know it; a point estimate
   with no interval cannot support "substantially exceeds".

2. Factorial interaction on the log scale, with a bootstrap interval.
     I_AB = ln(L_AB/L_0) - ln(L_A/L_0) - ln(L_B/L_0)
   A combination is only synergistic if that interval lies strictly above zero.
   The manuscript compares a ratio metric against an *additive* null, which is
   the wrong null and inflates the apparent synergy.

3. Validation of the closed-form RLI proxy against directly measured lifetime.
   The proxy is mean writes scaled by (1 + InterV + IntraV). Two distributions
   with identical mean and variance can have very different maxima, so the proxy
   can reverse policy rankings. Spearman rho, Kendall tau, median absolute
   percentage error and an explicit list of pairwise reversals settle whether it
   is safe to use. If it is safe, say so with evidence; if not, stop using it.

4. Absolute lifetime in years, swept over the endurance assumption, because
   a relative improvement over an unstated base is not a lifetime claim.
"""
import argparse, csv, itertools, json, math, os, sys
import numpy as np


# --------------------------------------------------------------- aggregation
def geo_mean_ratio(ratios):
    """Geometric mean of (1+r) - 1 over paired observations."""
    r = np.asarray([x for x in ratios if np.isfinite(x) and x > -0.999], dtype=float)
    if r.size == 0:
        return float('nan')
    return float(np.exp(np.mean(np.log1p(r))) - 1.0)


def bootstrap_ci(values, stat=geo_mean_ratio, n_boot=10000, alpha=0.05, seed=0):
    """Paired bootstrap over observations (workloads/seeds). Returns
    (point, lo, hi). Resampling is over the observation axis, so the interval
    reflects workload-to-workload variation, which is what a reader cares about."""
    v = np.asarray([x for x in values if np.isfinite(x)], dtype=float)
    if v.size < 2:
        return (stat(v) if v.size else float('nan')), float('nan'), float('nan')
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, v.size, size=(n_boot, v.size))
    boots = np.array([stat(v[i]) for i in idx])
    boots = boots[np.isfinite(boots)]
    lo, hi = np.percentile(boots, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return stat(v), float(lo), float(hi)


# --------------------------------------------------------------- interaction
def log_interaction(L0, LA, LB, LAB):
    """Interaction contrast on the log scale for one paired observation.

    L* are *lifetimes* (larger is better), not improvements. Positive means the
    combination beats the multiplicative expectation of the two parts.
    """
    if min(L0, LA, LB, LAB) <= 0:
        return float('nan')
    return math.log(LAB / L0) - math.log(LA / L0) - math.log(LB / L0)


def interaction_report(rows_by_obs, n_boot=10000, seed=0):
    """rows_by_obs: list of dicts with keys L0, LA, LB, LAB (one per workload)."""
    vals = [log_interaction(r['L0'], r['LA'], r['LB'], r['LAB']) for r in rows_by_obs]
    vals = [v for v in vals if np.isfinite(v)]
    if not vals:
        return {}
    v = np.asarray(vals)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, v.size, size=(n_boot, v.size))
    boots = v[idx].mean(axis=1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    mean = float(v.mean())
    return dict(
        n=len(vals),
        interaction_log=mean,
        ci_lo=float(lo), ci_hi=float(hi),
        # exp(I) - 1 expresses the interaction as a percentage of the
        # multiplicative expectation, which is easier to read in a table
        interaction_pct=float(math.exp(mean) - 1.0),
        verdict=('synergistic' if lo > 0 else
                 'sub-multiplicative' if hi < 0 else
                 'indistinguishable from multiplicative'))


# --------------------------------------------------------- proxy validation
def spearman(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    ra, rb = a.argsort().argsort().astype(float), b.argsort().argsort().astype(float)
    ra -= ra.mean(); rb -= rb.mean()
    d = math.sqrt(float((ra ** 2).sum()) * float((rb ** 2).sum()))
    return float((ra * rb).sum() / d) if d else float('nan')


def kendall_tau(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    n = len(a)
    conc = disc = 0
    for i, j in itertools.combinations(range(n), 2):
        s = np.sign(a[i] - a[j]) * np.sign(b[i] - b[j])
        if s > 0:
            conc += 1
        elif s < 0:
            disc += 1
    tot = conc + disc
    return (conc - disc) / tot if tot else float('nan')


def _proxy_verdict(n_reversals: int, median_ape: float) -> str:
    """Two independent failure modes, reported as such.

    A proxy can rank correctly but be badly calibrated, or be well calibrated
    and still swap two policies. Collapsing both into one sentence hides which
    problem you have, and only the first is fatal for a comparison table.
    """
    if n_reversals > 0 and median_ape >= 0.05:
        return ('unsafe: reverses %d pairwise rankings and mis-estimates by %.0f%% median; '
                'report direct lifetime' % (n_reversals, 100 * median_ape))
    if n_reversals > 0:
        return ('unsafe for ranking: reverses %d pairwise rankings despite %.0f%% median error; '
                'report direct lifetime' % (n_reversals, 100 * median_ape))
    if median_ape >= 0.05:
        return ('ranking preserved on this sample, but magnitude is unreliable '
                '(%.0f%% median error): usable to order policies, not to quote a number'
                % (100 * median_ape))
    return 'agrees with direct lifetime in both ranking and magnitude on this sample'


def validate_proxy(records):
    """records: list of dicts with 'label', 'rli_proxy', 'rli_direct'.

    Returns rank agreement, error magnitude, and every pairwise ranking
    reversal — the reversals are the part that matters, because a reversal means
    the proxy would have selected a different winner.
    """
    recs = [r for r in records
            if np.isfinite(r.get('rli_proxy', float('nan')))
            and np.isfinite(r.get('rli_direct', float('nan')))]
    if len(recs) < 3:
        return {}
    p = np.array([r['rli_proxy'] for r in recs])
    d = np.array([r['rli_direct'] for r in recs])
    with np.errstate(divide='ignore', invalid='ignore'):
        ape = np.abs(p - d) / np.maximum(np.abs(d), 1e-9)
    reversals = []
    for i, j in itertools.combinations(range(len(recs)), 2):
        if np.sign(d[i] - d[j]) * np.sign(p[i] - p[j]) < 0:
            reversals.append(dict(a=recs[i]['label'], b=recs[j]['label'],
                                  direct_a=float(d[i]), direct_b=float(d[j]),
                                  proxy_a=float(p[i]), proxy_b=float(p[j])))
    npairs = len(recs) * (len(recs) - 1) // 2
    return dict(
        n=len(recs),
        spearman_rho=spearman(p, d),
        kendall_tau=kendall_tau(p, d),
        median_abs_pct_error=float(np.median(ape) * 100),
        max_abs_pct_error=float(np.max(ape) * 100),
        n_pairs=npairs,
        n_reversals=len(reversals),
        reversal_rate=len(reversals) / npairs if npairs else float('nan'),
        reversals=reversals[:25],
        verdict=_proxy_verdict(len(reversals), float(np.median(ape))))


# ----------------------------------------------------------- absolute years
def lifetime_years(max_writes, sim_accesses, accesses_per_second, endurance_cycles):
    """Years to first cell failure under a deterministic first-failure model.

    max_writes is the peak physical write count on any frame observed over
    sim_accesses LLC accesses. Scaling to a real machine needs the machine's
    access rate; both inputs are stated so the reader can substitute their own.
    """
    if max_writes <= 0 or sim_accesses <= 0:
        return float('inf')
    writes_per_access = max_writes / sim_accesses
    writes_per_second = writes_per_access * accesses_per_second
    if writes_per_second <= 0:
        return float('inf')
    return endurance_cycles / writes_per_second / (365.25 * 24 * 3600)


def lifetime_grid(max_writes, sim_accesses, accesses_per_second=1e8,
                  endurances=(1e12, 1e14, 1e15, 1e18)):
    """Sensitivity over the endurance assumption.

    Reported SOT-MRAM endurance spans several orders of magnitude depending on
    stack, drive condition and test method. A single assumed constant is not
    defensible; the grid makes the dependence explicit.
    """
    return {('E=%g' % e): lifetime_years(max_writes, sim_accesses, accesses_per_second, e)
            for e in endurances}


# ------------------------------------------------------------------ driver
def load(path):
    with open(path) as f:
        return [{k: (float(v) if _num(v) else v) for k, v in r.items()}
                for r in csv.DictReader(f)]


def _num(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('csvs', nargs='+', help='lifetime CSVs (one per capacity/mix)')
    ap.add_argument('--out', default='results/statistics.json')
    ap.add_argument('--accesses-per-second', type=float, default=1e8)
    ap.add_argument('--sim-accesses', type=float, default=0,
                    help='override the accesses column when scaling to years')
    a = ap.parse_args()

    rows = []
    for p in a.csvs:
        for r in load(p):
            r['source'] = os.path.basename(p)
            rows.append(r)

    report = {'sources': a.csvs, 'n_rows': len(rows)}

    # 1. proxy validation across every (scheme, arena, source) observation
    recs = [dict(label='%s/%s/%s' % (r.get('scheme'), r.get('arena'), r.get('source')),
                 rli_proxy=r.get('rli_proxy', float('nan')),
                 rli_direct=r.get('rli_direct', float('nan')))
            for r in rows if r.get('scheme') != 'unmanaged']
    report['proxy_validation'] = validate_proxy(recs)

    # 2. geometric-mean lifetime by scheme, with bootstrap CI over observations
    by = {}
    for r in rows:
        by.setdefault((r.get('scheme'), r.get('arena')), []).append(r)
    agg = []
    for (scheme, arena), rs in sorted(by.items()):
        vals = [x.get('rli_direct') for x in rs if np.isfinite(x.get('rli_direct', float('nan')))]
        pt, lo, hi = bootstrap_ci(vals)
        agg.append(dict(scheme=scheme, arena=arena, n=len(vals),
                        rli_direct_gm=pt, ci_lo=lo, ci_hi=hi,
                        max_write_mean=float(np.mean([x['max_write'] for x in rs])) if rs else None))
    report['aggregate'] = agg

    # 3. interaction, computed from lifetimes (1/max_writes), never from the proxy
    obs = []
    for src in {r['source'] for r in rows}:
        sub = {(r.get('scheme'), r.get('arena')): r for r in rows if r['source'] == src}
        def life(k):
            r = sub.get(k)
            return (1.0 / r['max_write']) if r and r.get('max_write') else None
        for pair, (kA, kB, kAB) in {
            'PSC x VRO': (('psc', 'hybrid'), ('vro', 'all-nvm'), ('psc-vro', 'hybrid')),
            'PSC-Pred x VRO-Rank': (('psc-pred', 'hybrid'), ('vro-rank', 'all-nvm'), ('psc-vro-rank', 'hybrid')),
        }.items():
            L0 = life(('unmanaged', 'hybrid'))
            LA, LB, LAB = life(kA), life(kB), life(kAB)
            if None not in (L0, LA, LB, LAB):
                obs.append((pair, dict(L0=L0, LA=LA, LB=LB, LAB=LAB)))
    inter = {}
    for pair in {p for p, _ in obs}:
        inter[pair] = interaction_report([d for p, d in obs if p == pair])
    report['interaction'] = inter

    # 4. absolute lifetime in years
    years = []
    for r in rows:
        n = a.sim_accesses or r.get('accesses') or 0
        if not r.get('max_write') or not n:
            continue
        years.append(dict(scheme=r.get('scheme'), arena=r.get('arena'), source=r['source'],
                          **{k: v for k, v in lifetime_grid(r['max_write'], n, a.accesses_per_second).items()}))
    report['lifetime_years'] = years

    os.makedirs(os.path.dirname(a.out) or '.', exist_ok=True)
    with open(a.out, 'w') as f:
        json.dump(report, f, indent=2, default=str)
    print('wrote', a.out)

    pv = report['proxy_validation']
    if pv:
        print('\nRLI proxy vs directly measured lifetime')
        print('  Spearman rho        %.3f' % pv['spearman_rho'])
        print('  Kendall tau         %.3f' % pv['kendall_tau'])
        print('  median |error|      %.1f%%' % pv['median_abs_pct_error'])
        print('  ranking reversals   %d of %d pairs (%.1f%%)' % (
            pv['n_reversals'], pv['n_pairs'], 100 * pv['reversal_rate']))
        print('  verdict             %s' % pv['verdict'])
    print('\nLifetime by scheme (geometric mean, 95%% bootstrap CI)')
    for r in sorted(agg, key=lambda x: -(x['rli_direct_gm'] if x['rli_direct_gm'] == x['rli_direct_gm'] else -9)):
        print('  %-14s %-8s n=%d  %+7.1f%%  [%+7.1f%%, %+7.1f%%]' % (
            r['scheme'], r['arena'], r['n'], 100 * r['rli_direct_gm'],
            100 * r['ci_lo'], 100 * r['ci_hi']))
    for pair, d in inter.items():
        if d:
            print('\nInteraction %s: I=%.4f  95%% CI [%.4f, %.4f]  -> %s'
                  % (pair, d['interaction_log'], d['ci_lo'], d['ci_hi'], d['verdict']))


if __name__ == '__main__':
    main()
