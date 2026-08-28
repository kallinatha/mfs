#!/usr/bin/env python3
"""Every claim the paper can make, and whether the artefacts to support it exist.

Written because the honest answer to "are you skipping stages that produced
publishable results" turned out to be yes.  The clean-slate runner started at
`--from hycsim`, which silently omitted the array design-space exploration --
the source of the 156x OptimizationTarget spread and the SRAM-to-SOT
write-latency crossover, both named in the rescue plan as headline material for
the measurement narrative.  Nothing flagged it, because a stage that is never
asked to run does not fail.

This audit inverts the check.  Rather than asking "did the stages I ran
succeed", it enumerates the *claims* and asks, for each one, whether the file
that substantiates it is present and non-empty.  A claim whose evidence is
missing is reported as MISSING with the command that produces it, so the gap is
actionable rather than merely noted.

    python3 scripts/coverage_audit.py --results results/suscom_ten_mixes

Exit 0 when every claim is supported, 1 otherwise.
"""
import argparse
import csv
import glob
import json
import os
import sys

# (claim, relative path or glob, minimum rows/files, command that produces it)
# One row per reviewer-facing claim. The path is the artefact that would be
# cited; the minimum is what makes it non-vacuous.
CLAIMS = [
    ('array design-space: OptimizationTarget spread, SRAM/SOT crossover',
     'dse/screen.csv', 10, './run_all.sh --config <cfg> --only dse'),
    ('DESTINY array models, all technologies',
     'destiny/results.csv', 5, './run_all.sh --config <cfg> --only destiny'),
    ('NVMExplorer array models (needed by the cross-stack join AND the projection)',
     'nvmexplorer/trace_profiles.csv', 2, './run_all.sh --config <cfg> --only nvmexplorer'),
    ('write-recurrence profile: repeat-write share, burst working set',
     'profile_writes.json', 1, './run_all.sh --config <cfg> --only profile'),
    ('per-mix lifetime and wear, per-mix selection',
     'hycsim/lifetime_all_mixes.csv', 50, './run_all.sh --config <cfg> --from hycsim --skip champsim'),
    ('leave-one-mix-out selection (the reportable number)',
     'hycsim/lifetime_all_mixes_loo.csv', 50, 'same as above'),
    ('global selection (the most conservative number)',
     'hycsim/lifetime_all_mixes_global.csv', 50, 'same as above'),
    ('tuning grid: the lifetime/hit-rate exchange rate',
     'hycsim/tuning_all_mixes.csv', 200, 'same as above'),
    ('guarded aggregates (guard_mixes and the *_guarded columns)',
     'hycsim/aggregate_loo.csv', 10, 'same as above'),
    ('cross-stack join: energy, EDP, area, years-to-failure (SOT)',
     'cross_stack_results.csv', 50, './run_all.sh --config <cfg> --only integrate'),
    ('cross-stack join, STT (the technology-transfer claim)',
     'cross_stack_results_stt.csv', 50, 'same as above'),
    ('cross-stack join, PCM (the ranking-inversion claim)',
     'cross_stack_results_pcm.csv', 50, 'same as above'),
    ('proxy falsification: median error and pairwise reversals',
     'statistics.json', 1, './run_all.sh --config <cfg> --only statistics'),
    ('ChampSim: IPC, liveness counters, AI traces (the protocol-dependence claim)',
     'champsim/champsim_results.csv', 100, './run_all.sh --config <cfg> --only champsim  [~21 h]'),
    ('DNN/graph arrays for EVERY cell type (break-even needs >1 technology)',
     'nvmexplorer_dnn/results/*mfs_dnn_arrays.csv', 6, './run_all.sh --config <cfg> --only nvmdnn'),
    ('DNN break-even and endurance bounds',
     'dnn_breakeven.csv', 5, 'python3 stage4_analysis/dnn_breakeven.py --dnn-dir <dir> --out <csv>'),
    ('cell-parameter phase diagram (which scheme wins where)',
     'cell_phase_diagram.csv', 5, 'python3 stage6_transfer/transfer.py --sweep ...'),
    ('endurance floor per target service life',
     'endurance_floor.csv', 5, 'python3 stage6_transfer/transfer.py --floor ...'),
    ('head-to-head against published SOTA, with tuning-parity audit',
     'sota_headtohead.csv', 5, 'python3 stage4_analysis/sota.py --results <dir> --out <csv>'),
    ('provenance: config hashes, tool versions, trace digests',
     'provenance.json', 1, './run_all.sh --config <cfg> --only provenance'),
]


def count(path):
    """Rows for a CSV, files for a glob, 1 for a non-empty JSON, else 0."""
    if any(ch in path for ch in '*?['):
        return len([p for p in glob.glob(path) if os.path.getsize(p) > 0])
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return 0
    if path.endswith('.json'):
        try:
            json.load(open(path))
            return 1
        except (ValueError, OSError):
            return 0
    try:
        with open(path, newline='') as f:
            return sum(1 for _ in csv.DictReader(f))
    except OSError:
        return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--results', default='results/suscom_ten_mixes')
    ap.add_argument('--quiet', action='store_true')
    a = ap.parse_args()
    print('claim coverage for %s\n' % os.path.abspath(a.results))
    print('%-4s %-62s %9s %8s' % ('', 'claim', 'found', 'status'))
    missing, thin = [], []
    for claim, rel, need, how in CLAIMS:
        n = count(os.path.join(a.results, rel))
        if n == 0:
            status, mark = 'MISSING', '  ! '
            missing.append((claim, rel, how))
        elif n < need:
            status, mark = 'THIN', '  ~ '
            thin.append((claim, rel, n, need, how))
        else:
            status, mark = 'ok', '    '
        print('%-4s %-62s %9d %8s' % (mark, claim[:62], n, status))
    print()
    if missing:
        print('MISSING (%d) - the claim cannot be made:' % len(missing))
        for claim, rel, how in missing:
            print('  %s' % claim)
            print('      expected: %s' % rel)
            print('      produce : %s' % how)
    if thin:
        print('\nTHIN (%d) - present but smaller than a full run:' % len(thin))
        for claim, rel, n, need, how in thin:
            print('  %-58s %d rows, expected >= %d' % (claim[:58], n, need))
            print('      produce : %s' % how)
    if not missing and not thin:
        print('Every claim is supported by a non-empty artefact.')
    print('\nNote: an artefact being present says the stage ran, not that the '
          'number is\nright. The acceptance gate, pareto.py and '
          'verify_headline_numbers.py check that.')
    return 1 if missing else 0


if __name__ == '__main__':
    sys.exit(main())
