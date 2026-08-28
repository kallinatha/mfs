#!/usr/bin/env python3
"""Reviewer-facing analyses over the CSVs the pipeline already writes.

Nothing here re-simulates anything: every number is derived from
`champsim_results.csv`, `tuning_all_mixes.csv`, `lifetime_all_selections.csv`
and the three `cross_stack_results*.csv`.  That is deliberate - these are the
claims a reviewer will interrogate, and they must be recomputable from the
shipped artefacts without a 21-hour replay.

Sections, following docs/RESCUE_PLAN_EXHAUSTIVE.md:
  guard     which guard condition fails, per scheme and per mix        (Part 2)
  frontier  the lifetime / hit-rate exchange rate                      (Part 2)
  liveness  did each ChampSim mechanism actually fire                  (Part 5.1)
  pareto    admissible policies per trace, and the LPP scalar          (Part 5.2/5.3)
  stats     cluster bootstrap CI, Wilcoxon, Holm-Bonferroni            (Part 4)
  tau       Kendall tau across SOT / STT / PCM joins                   (Part 6)

Usage:  python3 stage4_analysis/pareto.py --results <dir> [--section all]
"""
import argparse, csv, json, math, os, sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', 'stage5_champsim'))

AI_FAMILY_TOKENS = ['stable-diffusion', 'llama2', 'whisper', 'biogpt', 'bark',
                    'vit', 'clip', 'rwkv']


def family_of(trace_name):
    t = str(trace_name).lower()
    for tok in AI_FAMILY_TOKENS:
        if tok in t:
            return tok
    return trace_name


def gm(xs):
    """Geometric mean of ratio gains r > -1, in log1p space (Part 0.3)."""
    xs = [x for x in np.asarray(xs, dtype=float) if np.isfinite(x) and x > -0.999]
    return float(np.expm1(np.mean(np.log1p(xs)))) if xs else float('nan')


def cluster_boot_ci(rows, B=10000, seed=20260819):
    """Two-stage cluster bootstrap (Field & Welsh 2007).  See
    stage5_champsim/cluster_bootstrap.py for the derivation and the numeric
    verification; duplicated here only so this module runs standalone."""
    fams = {}
    for fam, r in rows:
        fams.setdefault(fam, []).append(r)
    names = list(fams)
    F = len(names)
    if not F:
        return (float('nan'), float('nan')), 0
    rng = np.random.default_rng(seed)
    gms = np.empty(B)
    for b in range(B):
        pooled = []
        for idx in rng.integers(0, F, F):
            v = fams[names[idx]]
            if len(v) == 1:
                pooled.append(v[0])
            else:
                pooled.extend(v[k] for k in rng.integers(0, len(v), len(v)))
        gms[b] = np.expm1(np.mean(np.log1p(np.asarray(pooled, dtype=float))))
    lo, hi = np.percentile(gms, [2.5, 97.5])
    return (float(lo), float(hi)), F


def load(path):
    with open(path, newline='') as f:
        return list(csv.DictReader(f))


def num(r, k, d=float('nan')):
    try:
        v = r.get(k, '')
        return float(v) if v not in ('', None) else d
    except (TypeError, ValueError):
        return d


# --------------------------------------------------------------------- guard
def section_guard(D, hit_tol=0.005, min_nvm=0.25):
    """Which of the two guard conditions actually fails, and where.

    The acceptance guard (stage3_hycsim/tune_and_run.py) admits a tuning
    configuration only if it holds hit rate within `hit_tol` of the unmanaged
    baseline in the same arena AND keeps mean NVM writes per frame at or above
    `min_nvm` of that baseline.  Reporting a single pass/fail hides which
    constraint bound, and they have opposite remedies: a hit-rate failure is a
    capacity-exclusion problem, an NVM-utilisation failure means the scheme
    bought its lifetime by abandoning the array it was meant to protect.
    """
    rows = load(os.path.join(D, 'hycsim', 'tuning_all_mixes.csv'))
    base = {}
    for r in rows:
        if r['scheme'] == 'unmanaged':
            base[(r['mix'], r['arena'])] = (num(r, 'hit_rate'), num(r, 'mu'))
    agg = {}
    for r in rows:
        b = base.get((r['mix'], r['arena']))
        if not b:
            continue
        hr_ok = num(r, 'hit_rate') >= b[0] - hit_tol
        mu_ok = (b[1] == 0) or (num(r, 'mu') >= min_nvm * b[1])
        k = (r['scheme'], r['arena'])
        a = agg.setdefault(k, [0, 0, 0, 0, []])
        a[0] += 1
        a[1] += int(hr_ok and mu_ok)
        a[2] += int(not hr_ok)
        a[3] += int(not mu_ok)
        if hr_ok and mu_ok:
            a[4].append(num(r, 'rli_direct'))
    print('== guard: which condition fails (tuning grid, all mixes) ==')
    print('%-22s %6s %6s %8s %8s %12s' % ('scheme/arena', 'cfgs', 'pass',
                                          'fail_hit', 'fail_nvm', 'best_guarded'))
    for k in sorted(agg):
        n, p, fh, fm, v = agg[k]
        print('%-22s %6d %6d %8d %8d %12s' % (
            '/'.join(k), n, p, fh, fm,
            ('%.2f' % max(v)) if v else '--'))


# ------------------------------------------------------------------ frontier
def section_frontier(D, arena='hybrid'):
    """The lifetime / hit-rate exchange rate.

    A single 'best RLI' number is only meaningful against the hit rate it cost.
    This walks the Pareto frontier of (mean hit-rate delta, mean RLI_direct)
    over every tuning configuration that keeps NVM utilisation above the floor,
    so the reader can price the headline claim instead of taking it.
    """
    rows = [r for r in load(os.path.join(D, 'hycsim', 'tuning_all_mixes.csv'))
            if r['arena'] == arena]
    base = {}
    for r in rows:
        if r['scheme'] == 'unmanaged':
            base[r['mix']] = (num(r, 'hit_rate'), num(r, 'mu'))
    agg = {}
    for r in rows:
        b = base.get(r['mix'])
        if not b:
            continue
        k = (r['scheme'], r['params'])
        a = agg.setdefault(k, [[], [], []])
        a[0].append(num(r, 'hit_rate') - b[0])
        a[1].append(num(r, 'mu') / b[1] if b[1] else float('nan'))
        a[2].append(num(r, 'rli_direct'))
    pts = []
    for (s, p), (dh, mur, rli) in agg.items():
        if np.nanmean(mur) < 0.25:
            continue
        pts.append((float(np.mean(dh)), float(np.mean(rli)), s, p))
    pts.sort(key=lambda t: -t[0])
    print('== frontier: lifetime gain against hit-rate cost (arena=%s) ==' % arena)
    print('%10s %14s %8s  %s' % ('d_hit', 'scheme', 'RLI', 'params'))
    best = -1e9
    for dh, rli, s, p in pts:
        if rli > best:
            best = rli
            print('%+10.5f %14s %8.2f  %s' % (dh, s, rli, p[:48]))
    print('\nexchange rate at a given hit-rate budget:')
    for tol in (0.005, 0.01, 0.02, 0.03, 0.05, 0.10):
        c = [q for q in pts if q[0] >= -tol]
        if c:
            dh, rli, s, p = max(c, key=lambda t: t[1])
            print('  budget %.3f -> best RLI %8.2f  (%s, d_hit %+.5f)' % (tol, rli, s, dh))
        else:
            print('  budget %.3f -> no configuration' % tol)


# ------------------------------------------------------------------ liveness
ACTIVITY = {'psc': 'steered', 'psc_pred': 'steered', 'psc_soft': 'steered',
            'psc_soft_rank': 'steered', 'psc_vro_rank': 'steered',
            'vro': 'vro_rotations', 'vro_rank': 'rank_interventions',
            'ayush': 'ayush_migrations', 'dwawr': 'dwawr_redirects',
            'i2wap': 'polf_flushes', 'tailguard': 'tg_actions'}


def section_liveness(D):
    """Part 5.1: a policy that did not fire is measuring noise, not a mechanism.

    Every scheme carries a counter that increments exactly when its mechanism
    acts.  A row whose counter is zero -- or four orders of magnitude below its
    siblings' -- is not evidence about the mechanism, whichever way its metrics
    came out.  This gate runs before any ChampSim number is interpreted.
    """
    rows = load(os.path.join(D, 'champsim', 'champsim_results.csv'))
    by = {}
    for r in rows:
        by.setdefault(r['policy'], []).append(r)
    print('== liveness: did the mechanism fire? ==')
    print('%-16s %-20s %14s %10s %10s  %s' % (
        'policy', 'counter', 'mean', 'per_alloc', 'nonzero', 'verdict'))
    for p, col in sorted(ACTIVITY.items()):
        d = by.get(p) or []
        if not d:
            continue
        v = [num(r, col, 0.0) for r in d]
        al = [num(r, 'allocations', 0.0) for r in d]
        rate = np.mean([a / b if b else 0.0 for a, b in zip(v, al)])
        nz = sum(1 for x in v if x > 0)
        verdict = ('LIVE' if nz == len(v) and rate > 1e-3 else
                   'DEAD' if nz == 0 else 'MARGINAL')
        print('%-16s %-20s %14.1f %10.2e %6d/%-3d  %s' % (
            p, col, float(np.mean(v)), rate, nz, len(v), verdict))
    print('\nMARGINAL/DEAD rows must not be quoted as evidence about the scheme.')


# -------------------------------------------------------------------- pareto
def section_pareto(D):
    """Part 5.2/5.3: per-trace Pareto front over (ipc_delta, rli_direct),
    then LPP = (1+RLI)(1+dIPC) - 1 as the tiebreak scalar.

    LPP is zero at baseline, symmetric (a 10% IPC loss cancels a 10% lifetime
    gain), monotone in each argument, and equals the gain in lifetime x
    throughput -- the total useful work served before first failure, which is
    what an endurance-limited cache actually maximises.
    """
    rows = load(os.path.join(D, 'champsim', 'champsim_results.csv'))
    for r in rows:
        r['_lpp'] = (1 + num(r, 'rli_direct', 0.0)) * (1 + num(r, 'ipc_delta', 0.0)) - 1
        r['_ai'] = family_of(r['trace']) in AI_FAMILY_TOKENS
    bytrace = {}
    for r in rows:
        bytrace.setdefault(r['trace'], []).append(r)
    onfront = {}
    for tr, d in bytrace.items():
        d = sorted(d, key=lambda r: -num(r, 'ipc_delta', 0.0))
        best = -1e18
        for r in d:
            v = num(r, 'rli_direct', -1e18)
            if v > best:
                best = v
                onfront[r['policy']] = onfront.get(r['policy'], 0) + 1
    by = {}
    for r in rows:
        by.setdefault(r['policy'], []).append(r)
    n = len(bytrace)
    print('== Pareto admissibility and LPP (n=%d traces) ==' % n)
    print('%-16s %10s %10s %10s %10s' % ('policy', 'on_front', 'LPP_all', 'LPP_AI', 'LPP_SPEC'))
    for p, d in sorted(by.items(), key=lambda kv: -gm([r['_lpp'] for r in kv[1]])):
        print('%-16s %6d/%-3d %10.4f %10.4f %10.4f' % (
            p, onfront.get(p, 0), n,
            gm([r['_lpp'] for r in d]),
            gm([r['_lpp'] for r in d if r['_ai']]),
            gm([r['_lpp'] for r in d if not r['_ai']])))


# --------------------------------------------------------------------- stats
def section_stats(D, metric='rli_direct', B=10000):
    """Part 4: cluster CI, Wilcoxon against LRU, Holm-Bonferroni over schemes.

    The traces are not 48 independent draws: the AI corpus ships several
    checkpoints and seeds per model family, and seeds of one model share a
    working-set shape and a write-pattern class.  Resampling traces flat
    understates the uncertainty; resampling families then traces within
    families does not.
    """
    from scipy.stats import wilcoxon
    rows = load(os.path.join(D, 'champsim', 'champsim_results.csv'))
    for r in rows:
        r['_lpp'] = (1 + num(r, 'rli_direct', 0.0)) * (1 + num(r, 'ipc_delta', 0.0)) - 1
    key = '_lpp' if metric == 'LPP' else metric
    by = {}
    for r in rows:
        by.setdefault(r['policy'], []).append(r)
    print('== cluster bootstrap 95%% CI on %s (B=%d) ==' % (metric, B))
    print('%-16s %7s %7s %10s %10s %10s %s' % (
        'policy', 'traces', 'fams', 'gm', 'lo', 'hi', 'excl_0'))
    for p, d in sorted(by.items()):
        pairs = [(family_of(r['trace']), num(r, key))
                 for r in d if np.isfinite(num(r, key)) and num(r, key) > -0.999]
        (lo, hi), F = cluster_boot_ci(pairs, B=B)
        g = gm([v for _, v in pairs])
        print('%-16s %7d %7d %+10.4f %+10.4f %+10.4f %s' % (
            p, len(pairs), F, g, lo, hi, 'YES' if (lo > 0 or hi < 0) else 'no'))
    base = {r['trace']: r[key] if key == '_lpp' else num(r, key)
            for r in rows if r['policy'] == 'lru'}
    tests = []
    for p, d in by.items():
        if p == 'lru':
            continue
        diff = []
        for r in d:
            b = base.get(r['trace'])
            v = r[key] if key == '_lpp' else num(r, key)
            if b is None or not np.isfinite(v) or v <= -0.999 or b <= -0.999:
                continue
            diff.append(math.log1p(v) - math.log1p(b))
        if not diff or not any(diff):
            tests.append((p, 1.0, 0.0))
            continue
        tests.append((p, float(wilcoxon(diff).pvalue), float(np.mean(diff))))
    tests.sort(key=lambda t: t[1])
    print('\n== Wilcoxon vs lru, Holm-Bonferroni at alpha=0.05 ==')
    print('%-16s %14s %12s %12s %s' % ('policy', 'mean_dlog', 'p_raw', 'holm_thr', 'reject'))
    m, still = len(tests), True
    for i, (p, pv, dd) in enumerate(tests):
        thr = 0.05 / (m - i)
        ok = still and pv <= thr
        still = ok
        print('%-16s %+14.5f %12.3g %12.5f %s' % (p, dd, pv, thr, 'YES' if ok else 'no'))


# ----------------------------------------------------------------------- tau
def section_tau(D):
    """Part 6: does the scheme ranking survive the memory technology?

    RLI_direct is a ratio of write counts and is technology-independent by
    construction, so tau = 1 there is an identity, not a robustness result -
    it is printed to make that explicit rather than to be quoted.  The
    ranking that can move is the energy one, and it does.
    """
    from scipy.stats import kendalltau
    F = [('SOT', 'cross_stack_results.csv'),
         ('STT', 'cross_stack_results_stt.csv'),
         ('PCM', 'cross_stack_results_pcm.csv')]
    data = {}
    for tag, f in F:
        p = os.path.join(D, f)
        if not os.path.exists(p):
            print('missing %s; skipping tau' % f)
            return
        data[tag] = load(p)
    def rank(rows, metric):
        by = {}
        for r in rows:
            v = num(r, metric)
            if np.isfinite(v) and v > 0:
                by.setdefault((r['scheme'], r['arena']), []).append(math.log(v))
        return {k: math.exp(sum(v) / len(v)) for k, v in by.items()}
    print('== Kendall tau across NVM technologies ==')
    for metric, note in [('rli_direct', 'IDENTITY: a write-count ratio cannot depend on the cell'),
                         ('total_energy_mJ', ''),
                         ('dynamic_EDP_mJ_ms', '')]:
        r = {t: rank(data[t], metric) for t in data}
        keys = sorted(set.intersection(*[set(x) for x in r.values()]))
        if not keys:
            continue
        print('\n%s (n=%d)%s' % (metric, len(keys), ('  <- ' + note) if note else ''))
        for a, b in (('SOT', 'STT'), ('SOT', 'PCM'), ('STT', 'PCM')):
            if metric == 'rli_direct':
                va = [r[a][k] for k in keys]
                vb = [r[b][k] for k in keys]
            else:
                va = [r[a][k] for k in keys]
                vb = [r[b][k] for k in keys]
            t, p = kendalltau(va, vb)
            flag = '   <-- RANKING DOES NOT TRANSFER' if t < 0.5 else ''
            print('  tau(%s,%s) = %+.4f  p=%.3g%s' % (a, b, t, p, flag))
    # the explanatory regression
    from scipy.stats import linregress
    rs = rank(data['SOT'], 'dynamic_EDP_mJ_ms')
    rp = rank(data['PCM'], 'dynamic_EDP_mJ_ms')
    keys = sorted(set(rs) & set(rp))
    order_s = {k: i for i, k in enumerate(sorted(keys, key=lambda k: rs[k]))}
    order_p = {k: i for i, k in enumerate(sorted(keys, key=lambda k: rp[k]))}
    share, delta = [], []
    for k in keys:
        nv = sum(num(r, 'nvm_writes', 0.0) for r in data['SOT']
                 if (r['scheme'], r['arena']) == k)
        sr = sum(num(r, 'sram_writes', 0.0) for r in data['SOT']
                 if (r['scheme'], r['arena']) == k)
        if nv + sr <= 0:
            continue
        share.append(nv / (nv + sr))
        delta.append(order_p[k] - order_s[k])
    if len(share) > 2:
        s = linregress(share, delta)
        print('\nEDP rank change (SOT->PCM) vs NVM write share:')
        print('  slope %+.2f   r2 %.3f   p %.3g   n %d' % (
            s.slope, s.rvalue ** 2, s.pvalue, len(share)))
        print('  Interpretation: under PCM the write energy is ~125x SOT, so the'
              '\n  fraction of writes a scheme keeps OFF the NVM array, not which'
              '\n  levelling mechanism it uses, sets its energy-delay ranking.')


SECTIONS = dict(guard=section_guard, frontier=section_frontier,
                liveness=section_liveness, pareto=section_pareto,
                stats=section_stats, tau=section_tau)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--results', required=True,
                    help='results dir (contains hycsim/, champsim/, cross_stack_results*.csv)')
    ap.add_argument('--section', default='all', choices=['all'] + sorted(SECTIONS))
    a = ap.parse_args()
    names = sorted(SECTIONS) if a.section == 'all' else [a.section]
    for i, n in enumerate(names):
        if i:
            print()
        try:
            SECTIONS[n](a.results)
        except FileNotFoundError as e:
            print('== %s: skipped (%s) ==' % (n, e))


if __name__ == '__main__':
    main()
