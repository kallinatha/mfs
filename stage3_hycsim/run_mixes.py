#!/usr/bin/env python3
"""Run the full scheme comparison across a set of workload mixes.

Built for the real thing: ten mixes, traces up to ~3 GB of text each.

Design points that matter at that size:

  * Traces are converted once to `.npy` arrays inside a per-mix directory and
    then opened with `mmap_mode='r'`, so a worker's resident memory stays flat
    regardless of trace length. A 3 GB text trace becomes roughly 90 M records;
    held as arrays that is about 1 GB on disk and near zero in RAM.
  * Mixes run in parallel, schemes run serially inside a mix. That keeps peak
    memory at (workers x working-set) rather than (workers x schemes).
  * Parameter tuning happens once per mix on a prefix, exactly as in the
    single-trace driver, so a scheme cannot be tuned on the data it is scored on.
  * Per-mix rows and a cross-mix aggregate are both written. The aggregate uses
    the geometric mean for ratios (lifetime, write amplification) and the
    arithmetic mean for rates, which is the convention reviewers expect.

Usage
    python3 run_mixes.py --traces-dir /data/traces --outdir results/mixes \\
                         --size-mb 16 --workers 4
"""
import argparse, csv, glob, json, math, os, sys, time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llc import LLC
from metrics import wear_metrics, rli_proxy, rli_direct, multiplicative_null
import policies
from mix_labels import MixLabeller
from tune_and_run import ARENAS, GRID, HYBRID_ONLY


def open_mix(path):
    """Return (op, addr, dirty, cycle-or-None, profile) memory-mapped."""
    if os.path.isdir(path):
        pp = os.path.join(path, 'profile.json')
        profile = json.load(open(pp)) if os.path.exists(pp) else {}
        cp = os.path.join(path, 'cycle.npy')
        return (np.load(os.path.join(path, 'op.npy'), mmap_mode='r'),
                np.load(os.path.join(path, 'addr.npy'), mmap_mode='r'),
                np.load(os.path.join(path, 'dirty.npy'), mmap_mode='r'),
                np.load(cp, mmap_mode='r') if os.path.exists(cp) else None,
                profile)
    z = np.load(path)
    pp = path + '.profile.json'
    profile = json.load(open(pp)) if os.path.exists(pp) else {}
    return z['op'], z['addr'], z['dirty'], z['cycle'] if 'cycle' in z else None, profile


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
    r = dict(scheme=scheme, arena=arena, size_MB=size_mb,
             nvm_ways=nvm, sram_ways=sram, ways=nvm + sram,
             sets=llc.sets, blocksize_B=llc.blocksize, params=json.dumps(params),
             runtime_s=round(time.time() - t0, 1), accesses=int(len(op)))
    r.update({k: m[k] for k in ('mu', 'interv', 'intrav', 'max_write', 'total_writes', 'cov')})
    r.update(llc.stats())
    r['state_bits'] = pol.state_bits() if hasattr(pol, 'state_bits') else 0
    tagdata = llc.sets * llc.ways * (llc.blocksize * 8 + 32)
    r['state_overhead_pct'] = 100.0 * r['state_bits'] / tagdata
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
        r['hit_rate_delta'] = r['hit_rate'] - b['hit_rate']
    return rows


def one_mix(job):
    path, size_mb, tune_n, hit_tol, min_nvm, only, evaluation, label_spec = job
    name = os.path.basename(path).replace('.npz', '')
    labeller = MixLabeller(label_spec or None)
    mix_label, label_note = labeller.label(name)
    if label_note:
        print('[%s] mix label unresolved: %s' % (name, label_note), flush=True)
    op, addr, dirty, cycle, profile = open_mix(path)
    n = len(op)
    tn = min(tune_n, n)
    top = np.asarray(op[:tn]); taddr = np.asarray(addr[:tn]); tdirty = np.asarray(dirty[:tn])

    tune = []
    for arena in ARENAS:
        for scheme, grid in GRID.items():
            if only and scheme not in only:
                continue
            if arena == 'all-nvm' and scheme in HYBRID_ONLY:
                continue
            for p in grid:
                tune.append(sim(scheme, arena, size_mb, top, taddr, tdirty, p))
    add_rli(tune)
    for r in tune:
        r['mix'] = name
        r['mix_label'] = mix_label

    bh = {r['arena']: r['hit_rate'] for r in tune if r['scheme'] == 'unmanaged'}
    bmu = {r['arena']: r['mu'] for r in tune if r['scheme'] == 'unmanaged'}
    best = {}
    for r in tune:
        k = (r['scheme'], r['arena'])
        v = r.get('rli_direct')
        if v is None or v != v:
            v = -1e9
        ok = (r['hit_rate'] >= bh.get(r['arena'], 0) - hit_tol and
              (bmu.get(r['arena'], 1) == 0 or r['mu'] >= min_nvm * bmu[r['arena']]))
        r['eligible'] = int(ok)
        if ok and (k not in best or v > best[k][0]):
            best[k] = (v, json.loads(r['params']))
    fallbacks = set()
    for k in {(r['scheme'], r['arena']) for r in tune}:
        if k in best:
            continue
        cand = [r for r in tune if (r['scheme'], r['arena']) == k]
        baseline_hit = bh.get(k[1], 0.0)
        cand.sort(key=lambda r: (-(r['hit_rate'] - baseline_hit),
                                 -(r.get('rli_direct') if r.get('rli_direct') == r.get('rli_direct') else -1e9)))
        best[k] = (cand[0].get('rli_direct') or 0.0, json.loads(cand[0]['params']))
        fallbacks.add(k)

    if evaluation == 'suffix' and tn < n:
        eop, eaddr, edirty = op[tn:], addr[tn:], dirty[tn:]
        region, eval_start = 'post-tuning-suffix', tn
    else:
        eop, eaddr, edirty = op, addr, dirty
        region, eval_start = ('full-trace' if evaluation == 'full' else 'full-trace-too-short'), 0
    # The first trace column is a gem5 *tick* (1 ps by default), not a clock
    # cycle. prep_trace resolves which it is and cross-checks the answer against
    # the trace's own Sim_seconds header; `tick_rate_hz` is that verified figure.
    # Dividing by clock_hz here instead - which this line used to do - inflated
    # the measured duration by 500x on these traces, and duration divides into
    # every energy rate and multiplies into every years-to-failure number.
    rate = profile.get('tick_rate_hz')
    if not rate:
        raise ValueError(
            'profile.json has no tick_rate_hz. Re-run `prepare` with the current '
            'prep_trace.py: the time base must be resolved and cross-checked '
            'against the Sim_seconds header before any duration is computed. '
            'Falling back to a guessed clock is how a 500x error gets published.')
    if cycle is not None and len(eop) > 1:
        duration = (int(cycle[-1]) - int(cycle[eval_start])) / float(rate)
    else:
        duration = float(profile.get('duration_s', 0.0)) * (len(eop) / n if n else 0.0)

    rows = evaluate(name, size_mb, eop, eaddr, edirty, best, fallbacks,
                    region, tn, eval_start, duration, 'per-mix', mix_label)
    print('[%s] %d accesses, %d schemes done' % (name, n, len(rows)), flush=True)
    return name, tune, rows


def evaluate(name, size_mb, eop, eaddr, edirty, best, fallbacks,
             region, tn, eval_start, duration, selection, mix_label=''):
    rows = []
    for (scheme, arena), (_, p) in sorted(best.items()):
        r = sim(scheme, arena, size_mb, eop, eaddr, edirty, p)
        r['mix'] = name
        r['mix_label'] = mix_label
        r['evaluation_region'] = region
        r['tuning_records'] = tn
        r['evaluation_start_record'] = eval_start
        r['measured_duration_s'] = duration
        r['selection'] = selection
        # Two different facts, previously conflated into one column.
        #
        # `selection_fallback` is a property of the *pool*: under loo/global the
        # parameters are chosen from other mixes, and if no pooled configuration
        # met the guard on the pooled averages the scheme was represented by a
        # fallback.  That is one bit for the whole selection mode.
        #
        # `selection_guard_passed` must be a property of *this row*: did this
        # scheme, on this mix, actually hold hit rate and NVM utilisation
        # against this mix's own unmanaged baseline.  Setting it from the
        # pooled fallback bit instead is why aggregate_loo.csv reported
        # guard_mixes = 0 for psc while the identical simulations reported
        # 5/10 under per-mix selection: psc has a single configuration, so all
        # three selection modes run the *same* replay and only the flag
        # differed.  A scheme that genuinely holds the guard on four mixes was
        # being reported as holding it on none, and its guarded geomean -- the
        # number the paper should headline -- came out blank.
        r['selection_fallback'] = int((scheme, arena) in fallbacks)
        rows.append(r)
    add_rli(rows)
    _mark_row_guard(rows)
    return rows


def _mark_row_guard(rows, hit_tol=0.005, min_nvm=0.25):
    """Per-row acceptance guard against this mix's own unmanaged baseline.

    The same predicate as tune_and_run.select_from's, applied to the evaluated
    rows rather than to the tuning prefix, so the column means what
    aggregate()'s guarded geomeans assume it means.
    """
    base = {}
    for r in rows:
        if r.get('scheme') == 'unmanaged':
            base[r.get('arena')] = (r.get('hit_rate'), r.get('mu'))
    for r in rows:
        b = base.get(r.get('arena'))
        if not b or b[0] is None:
            r['selection_guard_passed'] = int(not r.get('selection_fallback', 0))
            continue
        bh, bm = b
        ok = (r.get('hit_rate', 0.0) >= bh - hit_tol and
              (not bm or r.get('mu', 0.0) >= min_nvm * bm))
        r['selection_guard_passed'] = int(ok)


def select_from(tune_rows, hit_tol, min_nvm):
    """Pick one parameter set per (scheme, arena) from a pool of tuning rows.

    The pool is what distinguishes the three selection modes:

      per-mix  the mix's own tuning prefix. An **oracle upper bound** - the
               parameters were chosen knowing this mix. Useful as a ceiling,
               not as a claim: nobody can tune per workload at design time.
      loo      every other mix's tuning rows. Leave-one-mix-out is the
               implementable number, and the honest one to headline.
      global   every mix's tuning rows, one parameter set for all. The most
               conservative, and the easiest to defend in hardware.

    Reporting all three brackets the oracle gap instead of hiding it. If
    per-mix and loo are close, the tuning is robust and the claim is safe; if
    they diverge, the per-mix figure was measuring the tuner, not the mechanism.
    Scores are aggregated across the pooled mixes by *mean* RLI, so a
    configuration that wins on one mix and collapses on another cannot be
    selected on the strength of its best day.
    """
    import collections
    by_arena_base = collections.defaultdict(list)
    for r in tune_rows:
        if r['scheme'] == 'unmanaged':
            by_arena_base[r['arena']].append(r)
    bh = {a: sum(x['hit_rate'] for x in v) / len(v)
          for a, v in by_arena_base.items()}
    bmu = {a: sum(x['mu'] for x in v) / len(v)
           for a, v in by_arena_base.items()}

    # group by (scheme, arena, params) across mixes, then average
    agg = collections.defaultdict(list)
    for r in tune_rows:
        agg[(r['scheme'], r['arena'], r['params'])].append(r)

    best, fallbacks = {}, set()
    cands = collections.defaultdict(list)
    for (scheme, arena, params), rs in agg.items():
        hit = sum(x['hit_rate'] for x in rs) / len(rs)
        mu = sum(x['mu'] for x in rs) / len(rs)
        vals = [x.get('rli_direct') for x in rs]
        vals = [v if (v is not None and v == v) else -1e9 for v in vals]
        v = sum(vals) / len(vals)
        ok = (hit >= bh.get(arena, 0.0) - hit_tol and
              (bmu.get(arena, 1.0) == 0 or mu >= min_nvm * bmu[arena]))
        cands[(scheme, arena)].append((ok, v, hit, params))
    for k, lst in cands.items():
        eligible = [c for c in lst if c[0]]
        if eligible:
            _, v, _, params = max(eligible, key=lambda c: c[1])
            best[k] = (v, json.loads(params))
        else:
            base = bh.get(k[1], 0.0)
            _, v, _, params = max(lst, key=lambda c: (c[2] - base, c[1]))
            best[k] = (v, json.loads(params))
            fallbacks.add(k)
    return best, fallbacks


def dump(rows, path):
    if not rows:
        return
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
    print('wrote %s (%d rows)' % (path, len(rows)), flush=True)


def geomean(xs):
    xs = [x for x in xs if isinstance(x, float) and x == x and x > -0.999]
    if not xs:
        return float('nan')
    return math.exp(sum(math.log(1 + x) for x in xs) / len(xs)) - 1


def aggregate(rows, path, hit_tol=0.005):
    """Cross-mix summary: geometric mean for ratio metrics, arithmetic for rates.

    Every ratio metric is reported TWICE: over all mixes, and over the subset
    whose rows pass the evaluation-region guard (selection did not fall back,
    AND the measured hit-rate delta stayed within -hit_tol). The two can
    differ badly: a scheme can pass selection on the tuning prefix and then
    collapse hit rate on the evaluation suffix, and folding those rows into a
    single geomean silently launders a hit-rate loss into a lifetime win.
    Report the guarded number; the gap between the two is itself a result.
    """
    def guard_ok(r):
        return (r.get('selection_guard_passed', 1) == 1 and
                (r.get('hit_rate_delta') is None or
                 (r.get('hit_rate_delta') or 0) >= -hit_tol))

    by = {}
    for r in rows:
        by.setdefault((r['scheme'], r['arena']), []).append(r)
    out = []
    for (scheme, arena), rs in sorted(by.items()):
        n = len(rs)
        gs = [r for r in rs if guard_ok(r)]
        d = dict(
            scheme=scheme, arena=arena, mixes=n,
            rli_direct_gm=geomean([r.get('rli_direct') for r in rs]),
            rli_proxy_gm=geomean([r.get('rli_proxy') for r in rs]),
            rli_direct_min=min((r.get('rli_direct', float('nan')) for r in rs), default=''),
            rli_direct_max=max((r.get('rli_direct', float('nan')) for r in rs), default=''),
            write_amp_gm=geomean([(r.get('write_amplification') or 1) - 1 for r in rs]) + 1,
            intrav_mean=sum(r['intrav'] for r in rs) / n,
            interv_mean=sum(r['interv'] for r in rs) / n,
            hit_rate_mean=sum(r['hit_rate'] for r in rs) / n,
            hit_rate_delta_mean=sum(r.get('hit_rate_delta', 0) or 0 for r in rs) / n,
            state_overhead_pct=rs[0].get('state_overhead_pct', 0),
            # ---- guard-passed subset (report THESE in the paper) ----
            guard_mixes=len(gs))
        if gs:
            d['rli_direct_gm_guarded'] = geomean([r.get('rli_direct') for r in gs])
            d['rli_direct_max_guarded'] = max(
                (r.get('rli_direct', float('nan')) for r in gs), default='')
            d['hit_rate_delta_mean_guarded'] = (
                sum(r.get('hit_rate_delta', 0) or 0 for r in gs) / len(gs))
        else:
            d['rli_direct_gm_guarded'] = ''
            d['rli_direct_max_guarded'] = ''
            d['hit_rate_delta_mean_guarded'] = ''
        out.append(d)
    dump(out, path)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--traces-dir', required=True,
                    help='directory of prepared .npz files or per-mix .npy directories')
    ap.add_argument('--outdir', default='results/mixes')
    ap.add_argument('--size-mb', type=int, default=16)
    ap.add_argument('--tune-accesses', type=int, default=6_000_000)
    ap.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument('--hit-tol', type=float, default=0.005)
    ap.add_argument('--min-nvm-share', type=float, default=0.25)
    ap.add_argument('--schemes', default='', help='comma-separated subset; default = all')
    ap.add_argument('--label-spec', default='',
                    help='JSON mix-label spec; default config/mix_labels.suscom.json')
    ap.add_argument('--evaluation', choices=('suffix', 'full'), default='suffix',
                    help='score on the post-tuning suffix (default) or the complete trace')
    ap.add_argument('--endurance-cv', type=float, default=0.0,
                    help='per-frame endurance CV. 0 disables the damage model, '
                         'in which case damage equals write count exactly and '
                         'every write-count result is unchanged.')
    ap.add_argument('--thermal', type=int, default=0,
                    help='Arrhenius thermal acceleration of damage')
    ap.add_argument('--selection', default='per-mix,loo,global',
                    help='comma-separated: per-mix (oracle ceiling), loo '
                         '(leave-one-mix-out, the implementable number), global '
                         '(one parameter set for all mixes). Default runs all '
                         'three so the oracle gap is visible rather than assumed.')
    a = ap.parse_args()

    if a.endurance_cv or a.thermal:
        globals()['DAMAGE_KW'] = dict(endurance_cv=a.endurance_cv,
                                      thermal=bool(a.thermal))
        print('damage model ON: endurance_cv=%g thermal=%d  (peak wear and CVaR '
              'are now damage-based; with both off they equal write counts)'
              % (a.endurance_cv, a.thermal))
    os.makedirs(a.outdir, exist_ok=True)
    paths = sorted(glob.glob(os.path.join(a.traces_dir, '*.npz')))
    paths += sorted(p for p in glob.glob(os.path.join(a.traces_dir, '*'))
                    if os.path.isdir(p) and os.path.exists(os.path.join(p, 'op.npy')))
    if not paths:
        sys.exit('no prepared traces in %s -- run prep_trace.py first' % a.traces_dir)
    print('%d mixes, %d workers, %d MB LLC' % (len(paths), a.workers, a.size_mb), flush=True)

    only = set(x for x in a.schemes.split(',') if x)
    if only:
        only.add('unmanaged')
    jobs = [(p, a.size_mb, a.tune_accesses, a.hit_tol, a.min_nvm_share, only,
             a.evaluation, a.label_spec) for p in paths]
    all_tune, all_rows = [], []
    iterator = map(one_mix, jobs)
    pool = None
    if a.workers > 1:
        pool = ProcessPoolExecutor(max_workers=a.workers)
        iterator = pool.map(one_mix, jobs)
    try:
        for name, tune, rows in iterator:
            all_tune += tune
            all_rows += rows
            dump(rows, os.path.join(a.outdir, 'lifetime_%s.csv' % name))
    finally:
        if pool is not None:
            pool.shutdown()
    dump(all_tune, os.path.join(a.outdir, 'tuning_all_mixes.csv'))

    # ---- selection modes beyond per-mix ---------------------------------
    # Per-mix tuning is an oracle: the parameters saw the mix they are scored
    # on. Leave-one-mix-out is what a design could actually ship. Running both
    # and printing the gap is the difference between a claim and a ceiling.
    modes = [m.strip() for m in a.selection.split(',') if m.strip()]
    extra = [m for m in modes if m != 'per-mix']
    if extra and len(paths) > 1:
        cache = {}
        for path in paths:
            nm = os.path.basename(path).replace('.npz', '')
            cache[nm] = path
        for mode in extra:
            print('\n=== selection: %s ===' % mode, flush=True)
            mode_rows = []
            for path in paths:
                nm = os.path.basename(path).replace('.npz', '')
                pool_rows = ([r for r in all_tune if r['mix'] != nm] if mode == 'loo'
                             else all_tune)
                if not pool_rows:
                    print('  [%s] no pool for %s; skipped' % (nm, mode))
                    continue
                best, fb = select_from(pool_rows, a.hit_tol, a.min_nvm_share)
                op, addr, dirty, cycle, profile = open_mix(path)
                n = len(op)
                tn = min(a.tune_accesses, n)
                if a.evaluation == 'suffix' and tn < n:
                    eop, eaddr, edirty, region, es = op[tn:], addr[tn:], dirty[tn:], 'post-tuning-suffix', tn
                else:
                    eop, eaddr, edirty, region, es = op, addr, dirty, 'full-trace', 0
                rate = profile.get('tick_rate_hz') or 0
                dur = ((int(cycle[-1]) - int(cycle[es])) / float(rate)
                       if (cycle is not None and rate and len(eop) > 1) else 0.0)
                mode_rows += evaluate(nm, a.size_mb, eop, eaddr, edirty, best, fb,
                                      region, tn, es, dur, mode)
            dump(mode_rows, os.path.join(a.outdir, 'lifetime_all_mixes_%s.csv' % mode))
            aggregate(mode_rows, os.path.join(a.outdir, 'aggregate_%s.csv' % mode), a.hit_tol)
            all_rows += mode_rows
    elif extra:
        print('\nleave-one-mix-out needs at least 2 mixes; only per-mix produced.')

    per_mix_rows = [r for r in all_rows if r.get('selection', 'per-mix') == 'per-mix']
    dump(all_rows, os.path.join(a.outdir, 'lifetime_all_selections.csv'))
    all_rows = per_mix_rows
    # `lifetime_all_mixes.csv` stays the per-mix rows and stays the name every
    # downstream stage joins on. Renaming it when the selection modes were added
    # silently broke integrate, statistics and validate - they failed on a
    # missing file after a successful replay, which is the worst kind of break
    # because the expensive stage looked fine.
    dump(all_rows, os.path.join(a.outdir, 'lifetime_all_mixes.csv'))
    agg = aggregate(all_rows, os.path.join(a.outdir, 'aggregate.csv'), a.hit_tol)

    print('\n%-15s %-8s %10s %10s %8s %9s %8s' % (
        'scheme', 'arena', 'RLIdir_gm', 'RLIprox_gm', 'WA_gm', 'hitDelta', 'state%'))
    for r in sorted(agg, key=lambda x: -(x['rli_direct_gm'] if x['rli_direct_gm'] == x['rli_direct_gm'] else -9)):
        print('%-15s %-8s %9.1f%% %9.1f%% %8.3f %8.4f %7.3f%%' % (
            r['scheme'], r['arena'], 100 * r['rli_direct_gm'], 100 * r['rli_proxy_gm'],
            r['write_amp_gm'], r['hit_rate_delta_mean'], r['state_overhead_pct']))


if __name__ == '__main__':
    main()
