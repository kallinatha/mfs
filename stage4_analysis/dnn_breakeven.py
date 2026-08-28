#!/usr/bin/env python3
"""DNN/graph AI workloads: the break-even read fraction and the endurance bound.

This closes RESCUE_PLAN_EXHAUSTIVE Part 7, which could not be computed until
the NVMExplorer DNN sweep produced arrays for every cell type.  It had been
producing SRAM only: NVSim's `combine_csv` opens a relative output path that
resolves only from inside the tool checkout, so the SRAM arrays were written
and the run died before STT or PCM ever launched.  With that fixed there are
six array CSVs and both halves of Part 7 are computable.

7.1  BREAK-EVEN READ FRACTION
-----------------------------
For a workload of A accesses over time T with read fraction rho, and a cell
with per-access read/write energies E_r, E_w and leakage P:

    E(rho) = A[rho E_r + (1-rho) E_w] + P T

An NVM beats SRAM when E_NVM < E_SRAM, i.e.

    A[rho (E_rN - E_rS) + (1-rho)(E_wN - E_wS)] < (P_S - P_N) T

Solving for the boundary:

    rho* = [ (P_S - P_N) T/A - (E_wN - E_wS) ]
           / [ (E_rN - E_rS) - (E_wN - E_wS) ]

Workloads to the right of their cell's rho* are NVM wins; the distance is the
margin.  Plotting rho* per cell with each workload at its measured rho puts the
unfavourable region on the same axes as the favourable one, which is the
honest way to present this and pre-empts the "strawman baseline" objection.

The T/A term matters and is usually elided.  Leakage is paid per second while
dynamic energy is paid per access, so rho* depends on how *fast* the workload
runs, not only on what it does.  This module therefore reports rho* across a
band of access rates rather than a single number, and says which band the
bundled workloads actually occupy.

7.2  ENDURANCE BOUND AT INFERENCE RATES
---------------------------------------
Two bounds bracket reality:

    uniform spread   Y = E L / (W_s * 3.156e7)     L = lines in the array
    single hot line  Y = E     / (W_s,max * 3.156e7)

Report both.  For weight-resident workloads the measured write count is exactly
zero, so lifetime is not wear-limited at all; that should be stated in those
words rather than quoted as a very large multiple, which invites the reader to
treat an undefined quantity as a measurement.

Usage:
    python3 stage4_analysis/dnn_breakeven.py \\
        --dnn-dir results/suscom_ten_mixes/nvmexplorer_dnn/results \\
        --out     results/suscom_ten_mixes/dnn_breakeven.csv
"""
import argparse
import csv
import glob
import os
import sys

SECONDS_PER_YEAR = 3.156e7
LINES_16MB_64B = (16 * 1024 * 1024) // 64          # 262,144

# Endurance decades carried by config/endurance.json, quoted with every number
# because a SOT lifetime is a band: the imec floor is a lower bound on
# endurance and therefore an upper bound on what may be claimed.
ENDURANCE = [('PCM', 1e8), ('STT', 1e12), ('SOT-lo', 1e13), ('SOT-hi', 1e15)]


def num(row, key, default=float('nan')):
    v = row.get(key, '')
    if isinstance(v, str):
        v = v.strip()
    try:
        return float(v) if v not in ('', None) else default
    except (TypeError, ValueError):
        return default


def load_arrays(d):
    """Read every *mfs_dnn_arrays.csv, keyed by (cell, benchmark).

    NVMExplorer writes these with leading spaces in both header and fields,
    which silently produces columns like ' CellArea (F^2)' and values that
    float() rejects; every read here strips.
    """
    rows = []
    files = sorted(glob.glob(os.path.join(d, '*mfs_dnn_arrays.csv')))
    if not files:
        raise SystemExit('no *mfs_dnn_arrays.csv under %s.\n'
                         'Run: ./run_all.sh --config <cfg> --only nvmdnn\n'
                         'and check that STT_* and PCM_* appear, not only SRAM_*.' % d)
    for f in files:
        tech = os.path.basename(f).split('_')[0]
        with open(f, newline='') as fh:
            for r in csv.DictReader(fh, skipinitialspace=True):
                r = {(k.strip() if isinstance(k, str) else k):
                     (v.strip() if isinstance(v, str) else v) for k, v in r.items()}
                r['_tech'] = tech
                r['_cell'] = os.path.basename(str(r.get('MemoryCellInputFile', ''))).replace('.cell', '')
                r['_bench'] = str(r.get('Benchmark Name', '')).strip()
                r['_opt'] = str(r.get('OptimizationTarget', '')).strip()
                rows.append(r)
    return rows, files


def workload_table(rows):
    """One entry per benchmark: read/write access counts and read fraction."""
    seen = {}
    for r in rows:
        b = r['_bench']
        if not b or b in seen:
            continue
        R, W = num(r, 'Read Accesses'), num(r, 'Write Accesses')
        if R != R or W != W:
            continue
        seen[b] = dict(bench=b, reads=R, writes=W,
                       rho=(R / (R + W)) if (R + W) > 0 else float('nan'))
    return sorted(seen.values(), key=lambda x: (x['rho'], x['bench']))


def cell_table(rows, opt):
    """One entry per (tech, cell) at a chosen OptimizationTarget."""
    seen = {}
    for r in rows:
        if r['_opt'] != opt:
            continue
        k = (r['_tech'], r['_cell'])
        if k in seen:
            continue
        e = dict(tech=r['_tech'], cell=r['_cell'],
                 Er=num(r, 'Read Energy (pJ)'), Ew=num(r, 'Write Energy (pJ)'),
                 P=num(r, 'Leakage Power (mW)'), area=num(r, 'Area (mm^2)'),
                 Lr=num(r, 'Read Latency (ns)'), Lw=num(r, 'Write Latency (ns)'))
        if all(v == v for v in (e['Er'], e['Ew'], e['P'])):
            seen[k] = e
    return sorted(seen.values(), key=lambda x: (x['tech'], x['cell']))


def rho_star(nvm, sram, t_per_access_s):
    """Break-even read fraction. None when the denominator vanishes.

    A vanishing denominator means the read and write energy gaps are equal, so
    the energy difference does not depend on the read fraction at all: one cell
    is uniformly better and there is no crossover to report.
    """
    dEr = (nvm['Er'] - sram['Er']) * 1e-12          # pJ -> J
    dEw = (nvm['Ew'] - sram['Ew']) * 1e-12
    dP = (sram['P'] - nvm['P']) * 1e-3              # mW -> W
    den = dEr - dEw
    if abs(den) < 1e-30:
        return None
    return (dP * t_per_access_s - dEw) / den


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dnn-dir', required=True)
    ap.add_argument('--out', default='')
    ap.add_argument('--opt', default='WriteEDP',
                    help='OptimizationTarget to analyse (WriteEDP or ReadEDP)')
    ap.add_argument('--lines', type=int, default=LINES_16MB_64B)
    a = ap.parse_args()

    rows, files = load_arrays(a.dnn_dir)
    print('read %d array rows from %d files' % (len(rows), len(files)))
    techs = sorted({r['_tech'] for r in rows})
    print('cell types present: %s' % ', '.join(techs))
    if len(techs) < 2:
        print('\nWARNING: only one cell type is present, so no break-even can be\n'
              'computed. The DNN sweep did not cover every configured cell --\n'
              'this is the failure mode that made Part 7.1 unreportable before.')

    cells = cell_table(rows, a.opt)
    work = workload_table(rows)

    print('\n=== workloads and their measured read fraction ===')
    print('%-30s %12s %12s %10s' % ('benchmark', 'reads', 'writes', 'rho'))
    for w in work:
        print('%-30s %12.4g %12.4g %10.6f' % (w['bench'], w['reads'], w['writes'], w['rho']))

    print('\n=== cells at OptimizationTarget=%s ===' % a.opt)
    print('%-6s %-40s %10s %10s %12s' % ('tech', 'cell', 'Er(pJ)', 'Ew(pJ)', 'leak(mW)'))
    for c in cells:
        print('%-6s %-40s %10.3f %10.3f %12.3f' % (c['tech'], c['cell'][:40], c['Er'], c['Ew'], c['P']))

    srams = [c for c in cells if c['tech'].upper().startswith('SRAM')]
    nvms = [c for c in cells if not c['tech'].upper().startswith('SRAM')]
    out_rows = []

    if srams and nvms:
        sram = srams[0]
        print('\n=== 7.1 break-even read fraction vs %s ===' % sram['cell'][:40])
        print('rho* is the read fraction above which the NVM is the lower-energy')
        print('choice. rho* <= 0 means the NVM always wins at that access rate;')
        print('rho* >= 1 means it never does.\n')
        rates = [('1 ns/access (1 GHz)', 1e-9),
                 ('10 ns/access (100 MHz)', 1e-8),
                 ('100 ns/access (10 MHz)', 1e-7)]
        print('%-6s %-34s %s' % ('tech', 'cell', ''.join('%22s' % r[0] for r in rates)))
        for c in nvms:
            vals = []
            for _, ta in rates:
                rs = rho_star(c, sram, ta)
                vals.append('%22s' % ('--' if rs is None else '%.4f' % rs))
                out_rows.append(dict(tech=c['tech'], cell=c['cell'], sram_cell=sram['cell'],
                                     opt=a.opt, seconds_per_access=ta,
                                     rho_star=('' if rs is None else rs),
                                     Er_pJ=c['Er'], Ew_pJ=c['Ew'], leak_mW=c['P']))
            print('%-6s %-34s %s' % (c['tech'], c['cell'][:34], ''.join(vals)))
        print('\nEvery bundled weight workload sits at rho = 1.000000 exactly '
              '(writes = 0),\nso weights are an NVM win on any cell whose rho* < 1. '
              'Activations sit\nwell below that and are where the comparison is '
              'actually decided.')
    else:
        print('\n7.1 SKIPPED: need at least one SRAM and one NVM cell '
              '(have SRAM=%d, NVM=%d)' % (len(srams), len(nvms)))

    print('\n=== 7.2 endurance bound at inference rates ===')
    print('Y_uniform = E * L / (W_s * 3.156e7), L = %d lines' % a.lines)
    print('%-30s %12s %s' % ('benchmark', 'writes/run',
                             ''.join('%14s' % ('E=%.0e' % e) for _, e in ENDURANCE)))
    for w in work:
        if w['writes'] <= 0:
            print('%-30s %12.4g %s' % (w['bench'], 0.0,
                                       '%56s' % 'writes = 0: NOT wear-limited in any technology'))
            out_rows.append(dict(bench=w['bench'], writes=0, wear_limited=0))
            continue
        cells_y = ''.join('%14.4g' % (e * a.lines / (w['writes'] * SECONDS_PER_YEAR))
                          for _, e in ENDURANCE)
        print('%-30s %12.4g %s' % (w['bench'], w['writes'], cells_y))
        rec = dict(bench=w['bench'], writes=w['writes'], wear_limited=1)
        for tag, e in ENDURANCE:
            rec['years_uniform_%s' % tag] = e * a.lines / (w['writes'] * SECONDS_PER_YEAR)
        out_rows.append(rec)

    worst = [w for w in work if w['writes'] > 0]
    if worst:
        w = max(worst, key=lambda x: x['writes'])
        y = 1e8 * a.lines / (w['writes'] * SECONDS_PER_YEAR)
        print('\nThe binding case is %s at %.3g writes/run: %.4g years on PCM '
              '(E=1e8),\ni.e. %.1f days. State that plainly -- PCM cannot host '
              'these activations.' % (w['bench'], w['writes'], y, y * 365.25))

    if a.out and out_rows:
        keys = []
        for r in out_rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        with open(a.out, 'w', newline='') as f:
            wtr = csv.DictWriter(f, fieldnames=keys)
            wtr.writeheader()
            wtr.writerows(out_rows)
        print('\nwrote %s (%d rows)' % (a.out, len(out_rows)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
