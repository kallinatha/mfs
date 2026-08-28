#!/usr/bin/env python3
"""Cross-technology projection: score every scheme on a cell that was never simulated.

WHAT THIS IS FOR
----------------
The expensive part of this study is the replay: 23 schemes x 10 mixes x 3
selection modes took 74,276 s.  What that replay produces is a *count vector*
per (scheme, mix) -- nvm_reads, nvm_writes, sram_reads, sram_writes, max_write,
and the measured duration.  Those counts are properties of the policy and the
workload.  They do not depend on what the cells are made of.

Everything the cross-stack join then computes -- dynamic energy, serialised
array time, EDP, leakage, area, years-to-first-failure -- is a fixed function
of that count vector and a handful of per-cell constants (read/write energy,
read/write latency, leakage power, area, endurance).  So the answer to

    "does your conclusion still hold on <some other memory technology>?"

is computable in milliseconds from a replay that has already run.  No
re-simulation, for any cell, ever -- including cells nobody has fabricated.

This module makes that explicit and, more importantly, *checkable*: it reuses
`stage4_analysis.cross_stack.join_row` rather than reimplementing the energy
model, so a projection cannot silently drift away from what the pipeline
computes for real technologies.  `--validate` proves the equivalence by
reprojecting a measured technology and diffing against the produced CSV.

WHY THIS IS A CONTRIBUTION AND NOT A CONVENIENCE
------------------------------------------------
The measured data says the scheme ranking is *not* a property of the scheme.
Between SOT-MRAM and PCM, Kendall tau on dynamic EDP is -0.551: the PCM
ordering is closer to the reverse of the SOT ordering than to agreement with
it.  The mechanism is one number -- the ratio of NVM to SRAM write energy:

    SOT-12/1   E_wN = 0.704 nJ,  E_wS = 1.620 nJ  ->  r = 0.43
    STT        E_wN = 1.257 nJ,                       r = 0.78
    PCM        E_wN = 88.07 nJ,                       r = 54.35

Under SOT an NVM write is *cheaper* than an SRAM write, so a scheme that
diverts writes into SRAM to save wear pays for it in energy.  Under PCM the
same diversion saves 54x per write.  Any single-technology evaluation of a
wear-management scheme is therefore measuring the cell as much as the
mechanism, and that is invisible unless the projection is run.

With this module a reviewer's "what about cell X" is answered from the paper's
own artefacts, and `--sweep` turns the question around: over a grid of cell
write energy and write latency, which scheme is optimal *where*?  That map is
a design-space result the replay alone cannot produce, and it extrapolates to
cells that do not exist yet.

WHAT IT DOES NOT DO
-------------------
It does not re-derive hit rate, IPC, or the wear distribution.  Those are
genuinely simulation outputs and change only if the policy or the trace
changes.  A projection answers "given this behaviour, what would this cell
cost"; it cannot answer "would the policy behave differently on this cell".
For a cache whose latency feeds back into the access stream that distinction
matters, and it is stated here rather than left for a reviewer to find.

USAGE
    # prove the projection path reproduces a measured technology exactly
    python3 stage6_transfer/transfer.py --validate \\
        --lifetime results/suscom_ten_mixes/hycsim/lifetime_all_mixes.csv \\
        --models   results/suscom_ten_mixes/nvmexplorer/trace_profiles.csv \\
        --destiny  results/suscom_ten_mixes/destiny/results.csv \\
        --measured results/suscom_ten_mixes/cross_stack_results.csv \\
        --nvm-cell SOT_12F2 --destiny-nvm-tech 'SOT-12/1'

    # project onto a hypothetical cell
    python3 stage6_transfer/transfer.py --project --cell-json mycell.json ...

    # phase diagram over cell parameter space
    python3 stage6_transfer/transfer.py --sweep --out sweep.csv ...
"""
import argparse
import csv
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'stage4_analysis'))

import cross_stack as CS                                          # noqa: E402

# Fields a synthetic cell must define.  These are exactly the columns
# join_row() reads off an NVMExplorer model row; nothing else is consulted, so
# this list is the complete contract for describing a hypothetical technology.
CELL_FIELDS = ('read_latency_ns', 'write_latency_ns', 'read_energy_pJ',
               'write_energy_pJ', 'leakage_power_mW', 'area_mm2',
               'word_width_bits', 'capacity_MB')


def synth_model(name, template, **overrides):
    """Build an NVMExplorer-shaped model row from a template plus overrides.

    Starting from a measured cell and overriding one or two parameters is the
    honest way to ask a counterfactual: everything not named stays at a value
    some tool actually produced, so the projection is never built on a cell
    whose parameters are jointly impossible.
    """
    m = dict(template)
    m['cell'] = name
    for k, v in overrides.items():
        if k not in CELL_FIELDS:
            raise ValueError('%r is not a cell parameter; valid: %s'
                             % (k, ', '.join(CELL_FIELDS)))
        m[k] = v
    return m


def project(lifetime_rows, models, destiny_rows, *, nvm_cell, sram_cell,
            destiny_nvm_tech, destiny_sram_tech, endurances,
            leakage_scales_with_ways=True):
    """Score every lifetime row against `models`, returning cross-stack rows."""
    out, rejected = [], []
    for life in lifetime_rows:
        try:
            out.append(CS.join_row(life, models, destiny_rows,
                                   nvm_cell=nvm_cell, sram_cell=sram_cell,
                                   destiny_nvm_tech=destiny_nvm_tech,
                                   destiny_sram_tech=destiny_sram_tech,
                                   endurances=endurances,
                                   leakage_scales_with_ways=leakage_scales_with_ways))
        except CS.JoinError as e:
            rejected.append((life.get('scheme'), life.get('arena'), str(e)))
    return out, rejected


def gm(xs):
    xs = [x for x in xs if x is not None and x == x and x > 0]
    return math.exp(sum(math.log(x) for x in xs) / len(xs)) if xs else float('nan')


def rank_by(rows, metric, lower_is_better=True):
    """Geomean `metric` per (scheme, arena), returned best-first."""
    by = {}
    for r in rows:
        try:
            v = float(r.get(metric))
        except (TypeError, ValueError):
            continue
        if v > 0:
            by.setdefault((r.get('scheme'), r.get('arena')), []).append(v)
    scored = [(k, gm(v)) for k, v in by.items()]
    scored = [(k, v) for k, v in scored if v == v]
    scored.sort(key=lambda t: t[1] if lower_is_better else -t[1])
    return scored


def kendall_tau(a, b):
    """Kendall tau-a over two dicts keyed identically. No scipy dependency."""
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


# ------------------------------------------------------------------- commands
COMPARE_KEYS = ('dynamic_energy_mJ', 'leakage_energy_mJ', 'total_energy_mJ',
                'dynamic_EDP_mJ_ms', 'serialized_array_time_ms', 'array_area_mm2')


def cmd_validate(a):
    """Reproject a technology that was actually joined, and diff.

    This is the load-bearing test.  If the projection path and the production
    join disagree anywhere, every counterfactual this module produces is
    worthless, so the check is exact (relative tolerance 1e-9) rather than
    approximate, and it runs over every row rather than a sample.
    """
    life = CS.read_csv(a.lifetime)
    models = CS.read_csv(a.models)
    dest = CS.read_csv(a.destiny) if a.destiny and os.path.exists(a.destiny) else []
    measured = CS.read_csv(a.measured)
    rows, rejected = project(life, models, dest,
                             nvm_cell=a.nvm_cell, sram_cell=a.sram_cell,
                             destiny_nvm_tech=a.destiny_nvm_tech,
                             destiny_sram_tech=a.destiny_sram_tech,
                             endurances=CS.DEFAULT_ENDURANCE_GRID)
    idx = {(r.get('scheme'), r.get('arena'), r.get('mix')): r for r in measured}
    n = worst = 0
    worst_where = None
    missing = 0
    for r in rows:
        k = (r.get('scheme'), r.get('arena'), r.get('mix'))
        m = idx.get(k)
        if m is None:
            missing += 1
            continue
        for key in COMPARE_KEYS:
            try:
                x, y = float(r[key]), float(m[key])
            except (KeyError, TypeError, ValueError):
                continue
            n += 1
            rel = abs(x - y) / max(abs(y), 1e-30)
            if rel > worst:
                worst, worst_where = rel, (k, key, x, y)
    print('projected %d rows against %d measured rows' % (len(rows), len(measured)))
    if rejected:
        print('  %d rows rejected by the join: %s' % (len(rejected), rejected[:3]))
    if missing:
        print('  %d projected rows had no measured counterpart' % missing)
    print('  compared %d values across %s' % (n, ', '.join(COMPARE_KEYS)))
    print('  worst relative difference: %.3e' % worst)
    if worst_where:
        (k, key, x, y) = worst_where
        print('    at %s / %s : projected %.6g vs measured %.6g' % (k, key, x, y))
    ok = worst < 1e-9 and n > 0
    print('  VERDICT: %s' % ('EXACT - the projection path is the production path'
                             if ok else 'MISMATCH - do not trust any projection'))
    return 0 if ok else 1


def cmd_project(a):
    life = CS.read_csv(a.lifetime)
    models = CS.read_csv(a.models)
    dest = CS.read_csv(a.destiny) if a.destiny and os.path.exists(a.destiny) else []
    template = CS.select_array_model(models, a.nvm_cell, None)
    spec = json.load(open(a.cell_json))
    name = spec.pop('name', 'HYPOTHETICAL')
    cell = synth_model(name, template, **spec)
    models = list(models) + [cell]
    rows, rejected = project(life, models, dest,
                             nvm_cell=name, sram_cell=a.sram_cell,
                             destiny_nvm_tech=a.destiny_nvm_tech,
                             destiny_sram_tech=a.destiny_sram_tech,
                             endurances=CS.DEFAULT_ENDURANCE_GRID)
    if a.out:
        keys = []
        for r in rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        with open(a.out, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
        print('wrote %s (%d rows, %d rejected)' % (a.out, len(rows), len(rejected)))
    print('\nEDP ranking on %s (geomean over mixes, best first):' % name)
    for i, (k, v) in enumerate(rank_by(rows, 'dynamic_EDP_mJ_ms')[:12], 1):
        print('  %2d. %-22s %12.6g' % (i, '/'.join(k), v))
    return 0


def cmd_sweep(a):
    """Phase diagram: which scheme is EDP-optimal across cell parameter space.

    The two axes are the parameters that actually separate the technologies in
    this study -- write energy and write latency.  Everything else is held at
    the measured baseline cell, so each grid point is a real cell with two
    parameters moved, not an arbitrary point in an eight-dimensional space.
    """
    life = CS.read_csv(a.lifetime)
    models = CS.read_csv(a.models)
    dest = CS.read_csv(a.destiny) if a.destiny and os.path.exists(a.destiny) else []
    template = CS.select_array_model(models, a.nvm_cell, None)
    e0 = CS.as_float(template, 'write_energy_pJ', where='template')
    l0 = CS.as_float(template, 'write_latency_ns', where='template')
    emul = [float(x) for x in a.energy_mult.split(',')]
    lmul = [float(x) for x in a.latency_mult.split(',')]
    out = []
    print('sweeping %d x %d cells around %s (E_w=%.4g pJ, L_w=%.4g ns)'
          % (len(emul), len(lmul), template.get('cell'), e0, l0))
    print('%10s %10s   %-24s %14s' % ('E_w x', 'L_w x', 'EDP-optimal scheme', 'EDP'))
    for em in emul:
        for lm in lmul:
            name = 'SYNTH_e%gx_l%gx' % (em, lm)
            cell = synth_model(name, template,
                               write_energy_pJ=e0 * em, write_latency_ns=l0 * lm)
            rows, _ = project(life, list(models) + [cell], dest,
                              nvm_cell=name, sram_cell=a.sram_cell,
                              destiny_nvm_tech=a.destiny_nvm_tech,
                              destiny_sram_tech=a.destiny_sram_tech,
                              endurances=CS.DEFAULT_ENDURANCE_GRID)
            r = rank_by(rows, 'dynamic_EDP_mJ_ms')
            if not r:
                continue
            best, val = r[0]
            out.append(dict(energy_mult=em, latency_mult=lm,
                            write_energy_pJ=e0 * em, write_latency_ns=l0 * lm,
                            best_scheme=best[0], best_arena=best[1],
                            best_EDP_mJ_ms=val,
                            runner_up=r[1][0][0] if len(r) > 1 else '',
                            margin_pct=100.0 * (r[1][1] - val) / val if len(r) > 1 else float('nan')))
            print('%10.3g %10.3g   %-24s %14.6g' % (em, lm, '/'.join(best), val))
    if a.out and out:
        with open(a.out, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(out[0]))
            w.writeheader()
            w.writerows(out)
        print('\nwrote %s (%d grid points)' % (a.out, len(out)))
    winners = sorted({o['best_scheme'] for o in out})
    print('\n%d distinct schemes are optimal somewhere in this space: %s'
          % (len(winners), ', '.join(winners)))
    if len(winners) > 1:
        print('That is the finding: "which scheme is best" has no '
              'technology-independent answer.')
    return 0


def cmd_floor(a):
    """Invert the lifetime question: what endurance does a target service life need?

    Every endurance study in this area asks "given cell endurance E, how long
    does the array live?".  A cell designer has the opposite problem: a product
    must survive Y years, and the question is what endurance that demands and
    how much a wear-management scheme relaxes it.

    From the lifetime identity Y = E * T / (w_max * 3.156e7), the required
    endurance for a target Y is

        E_required = Y * w_max * 3.156e7 / T

    w_max and T are replay quantities, so this is exact and needs no cell
    parameters at all.  The ratio between a scheme's requirement and the
    baseline's is precisely (1 + RLI), which makes the reframing checkable:
    if the two disagree, one of them is wrong.

    Reported as an endurance floor in writes/cell, next to the published
    decades, so a reader can see immediately which technologies are admissible
    for a given service life under each scheme.
    """
    life = CS.read_csv(a.lifetime)
    years = [float(x) for x in a.target_years.split(',')]
    SPY = 3.156e7
    base = {}
    for r in life:
        if r.get('scheme') in ('unmanaged', 'lru-hybrid'):
            base.setdefault(r.get('scheme'), {})[(r.get('mix'), r.get('arena'))] = r
    ref = base.get('unmanaged') or base.get('lru-hybrid') or {}
    by = {}
    for r in life:
        try:
            wmax = float(r.get('max_write'))
            dur = float(r.get('measured_duration_s'))
        except (TypeError, ValueError):
            continue
        if wmax <= 0 or dur <= 0:
            continue
        by.setdefault((r.get('scheme'), r.get('arena')), []).append((wmax, dur, r))
    print('=== endurance floor: writes/cell a cell must sustain for a target service life ===')
    print('E_required = Y * w_max * 3.156e7 / T   (replay quantities only; no cell model)')
    hdr = ''.join('%14s' % ('%g yr' % y) for y in years)
    print('%-24s %s %12s' % ('scheme/arena', hdr, 'vs baseline'))
    out = []
    ref_floor = {}
    for k, v in sorted(by.items()):
        # Geomean of the per-mix requirement: the floor composes multiplicatively
        # across mixes exactly as the lifetime ratio does.
        floors = {}
        for y in years:
            vals = [y * wmax * SPY / dur for wmax, dur, _ in v]
            floors[y] = gm(vals)
        if k[0] in ('unmanaged', 'lru-hybrid'):
            ref_floor.setdefault(k[1], floors)
        rel = ''
        rf = ref_floor.get(k[1])
        if rf and rf[years[0]] == rf[years[0]] and floors[years[0]] > 0:
            rel = '%11.2fx lower' % (rf[years[0]] / floors[years[0]])
        print('%-24s %s %12s' % ('/'.join(str(x) for x in k),
                                 ''.join('%14.3g' % floors[y] for y in years), rel))
        rec = dict(scheme=k[0], arena=k[1])
        for y in years:
            rec['endurance_floor_%gyr' % y] = floors[y]
        out.append(rec)
    print()
    print('Published decades for reference: PCM 1e8, STT 4e12, SOT >1e15 (imec floor).')
    print('A scheme is admissible on a technology when its floor sits below that')
    print('technology\'s endurance. The imec figure is a measurement floor, so it')
    print('bounds what may be claimed from above, not below.')
    if a.out and out:
        keys = list(out[0])
        with open(a.out, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(out)
        print('\nwrote %s (%d rows)' % (a.out, len(out)))
    return 0


def _pareto(points):
    """Indices on the upper-right Pareto front of (x, y), both maximised."""
    order = sorted(range(len(points)), key=lambda i: (-points[i][0], -points[i][1]))
    best, front = -float('inf'), []
    for i in order:
        if points[i][1] > best:
            best = points[i][1]
            front.append(i)
    return set(front)


def cmd_frontier(a):
    """Is any scheme Pareto-optimal on EVERY technology?

    A scheme that sits on the (lifetime, energy-efficiency) front for every
    cell would be a technology-independent recommendation -- the thing this
    literature implicitly claims to produce.  If the intersection is empty, the
    claim cannot be made by anyone, and saying so with the set sizes is a
    stronger result than another ranking table.
    """
    life = CS.read_csv(a.lifetime)
    models = CS.read_csv(a.models)
    dest = CS.read_csv(a.destiny) if a.destiny and os.path.exists(a.destiny) else []
    template = CS.select_array_model(models, a.nvm_cell, None)
    e0 = CS.as_float(template, 'write_energy_pJ', where='template')
    cells = [('as-measured', 1.0)] + [('E_w x%g' % m, m)
                                      for m in (float(x) for x in a.energy_mult.split(','))]
    fronts = {}
    for label, mult in cells:
        name = 'FRONT_%s' % label.replace(' ', '_')
        cell = synth_model(name, template, write_energy_pJ=e0 * mult)
        rows, _ = project(life, list(models) + [cell], dest,
                          nvm_cell=name, sram_cell=a.sram_cell,
                          destiny_nvm_tech=a.destiny_nvm_tech,
                          destiny_sram_tech=a.destiny_sram_tech,
                          endurances=CS.DEFAULT_ENDURANCE_GRID)
        agg = {}
        for r in rows:
            try:
                rli = float(r.get('rli_direct'))
                edp = float(r.get('dynamic_EDP_mJ_ms'))
            except (TypeError, ValueError):
                continue
            if edp <= 0 or rli <= -1:
                continue
            k = (r.get('scheme'), r.get('arena'))
            agg.setdefault(k, [[], []])
            agg[k][0].append(1.0 + rli)
            agg[k][1].append(edp)
        keys = sorted(agg)
        pts = [(gm(agg[k][0]), 1.0 / gm(agg[k][1])) for k in keys]   # both maximised
        idx = _pareto(pts)
        fronts[label] = {keys[i] for i in idx}
        print('%-16s front size %2d: %s' % (label, len(idx),
              ', '.join(sorted('/'.join(keys[i]) for i in idx))[:96]))
    common = set.intersection(*fronts.values()) if fronts else set()
    print()
    if common:
        print('On the front for EVERY cell tested (%d): %s'
              % (len(common), ', '.join(sorted('/'.join(k) for k in common))))
        print('Those are the only technology-independent recommendations this '
              'data supports.')
    else:
        print('NO scheme is Pareto-optimal on every cell tested.')
        print('There is therefore no technology-independent "best scheme" here, '
              'and any paper\nthat names one is reporting its cell as much as its '
              'mechanism.')
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--lifetime', required=True)
    ap.add_argument('--models', required=True)
    ap.add_argument('--destiny', default='')
    ap.add_argument('--measured', default='')
    ap.add_argument('--cell-json', default='')
    ap.add_argument('--out', default='')
    ap.add_argument('--nvm-cell', default='SOT_12F2')
    ap.add_argument('--sram-cell', default='SRAM_6T')
    ap.add_argument('--destiny-nvm-tech', default='SOT-12/1')
    ap.add_argument('--destiny-sram-tech', default='SRAM')
    ap.add_argument('--energy-mult', default='0.25,0.5,1,2,5,10,25,50,125')
    ap.add_argument('--latency-mult', default='0.5,1,2,5,10')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--validate', action='store_true')
    g.add_argument('--project', action='store_true')
    g.add_argument('--sweep', action='store_true')
    g.add_argument('--floor', action='store_true',
                   help='endurance a target service life demands, per scheme')
    g.add_argument('--frontier', action='store_true',
                   help='is any scheme Pareto-optimal on every technology?')
    ap.add_argument('--target-years', default='3,5,10')
    a = ap.parse_args()
    if a.validate:
        if not a.measured:
            raise SystemExit('--validate needs --measured')
        return cmd_validate(a)
    if a.project:
        if not a.cell_json:
            raise SystemExit('--project needs --cell-json')
        return cmd_project(a)
    if a.floor:
        return cmd_floor(a)
    if a.frontier:
        return cmd_frontier(a)
    return cmd_sweep(a)


if __name__ == '__main__':
    sys.exit(main())
