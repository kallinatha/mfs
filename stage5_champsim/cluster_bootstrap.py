#!/usr/bin/env python3
"""Cluster-aware bootstrap CI for scheme geomeans over champsim_results.csv.

Why this file exists: the ChampSim trace list mixes two populations. The 16
SPEC2017/PARSEC/GAP/XSBench/STREAM traces (config/suscom_ten_mixes.json,
experiment.champsim_traces) are each a different program - independent draws,
plain bootstrap is correct for them. The AI traces are NOT independent in the
same sense: llama2.c-stories ships 3 model sizes x multiple seeds, bark.cpp
ships 5 checkpoint variants, stable-diffusion.cpp ships 3 checkpoints, etc.
Seeds of the same model on the same architecture produce highly correlated
rli_direct/ipc_delta values (same working-set shape, same write pattern
class), so treating all 31 AI traces as 31 independent draws - which a plain
`np.random.default_rng().integers(0, n, n)` bootstrap over the flat list
does - understates the true sampling uncertainty. Verified numerically
(see RESCUE_PLAN_EXHAUSTIVE.md Part 4.5): on synthetic data with realistic
within-family correlation, the naive CI is roughly HALF the width of the
correct cluster-bootstrap CI at the same nominal 95% coverage.

The fix costs nothing on the non-AI traces: family_of() maps every SPEC/
PARSEC/GAP/STREAM trace name to itself (a singleton "family" of size one),
so the two-stage cluster bootstrap collapses exactly to the plain bootstrap
for that subset - one code path, correct for both populations.

Usage (library):
    from cluster_bootstrap import family_of, cluster_boot_ci
    rows = [(family_of(t), r) for t, r in zip(traces, rli_values)]
    (lo, hi), n_families = cluster_boot_ci(rows)

Usage (CLI, against a champsim_results.csv):
    python3 stage5_champsim/cluster_bootstrap.py \
        --csv results/suscom_ten_mixes/champsim/champsim_results.csv \
        --metric rli_direct
"""
import argparse, csv, sys
import numpy as np

# Longest/most specific tokens first is not required here because none of
# these substrings collide with each other, but keep the list explicit and
# reviewed rather than inferring families from filename edit-distance -
# implicit clustering rules are exactly the kind of silent choice this file
# is meant to avoid making. Extend this list, not the matching logic, when
# new AI trace families are added to the corpus.
AI_FAMILY_TOKENS = [
    'stable-diffusion', 'llama2', 'whisper', 'biogpt', 'bark',
    'vit', 'clip', 'rwkv',
]


def family_of(trace_name):
    """Map a trace's basename (as ChampSim/run_champsim.py record it, i.e.
    already stripped of the `.champsimtrace*` suffix) to its cluster id.

    AI traces: the shared model-family token (e.g. all of
    'llama2.c-stories15M.1', 'llama2.c-stories42M.3' -> 'llama2'). Everything
    else (SPEC2017, PARSEC, GAP, XSBench, STREAM trace names, none of which
    contain any AI_FAMILY_TOKENS substring) falls through to the trace's own
    full name, i.e. a singleton family - the correct behaviour for genuinely
    independent programs.
    """
    t = trace_name.lower()
    for tok in AI_FAMILY_TOKENS:
        if tok in t:
            return tok
    return trace_name


def cluster_boot_ci(rows, B=10000, seed=20260819):
    """Two-stage nonparametric cluster bootstrap (Field & Welsh 2007,
    'Bootstrapping clustered data', JRSS-B 69(3)): resample clusters (here,
    model families) with replacement, then resample within each selected
    cluster with replacement. Reduces to the plain percentile bootstrap when
    every cluster has size 1 (i.e. for the SPEC/PARSEC/GAP/STREAM subset).

    rows: iterable of (family, r) where r is a per-trace ratio metric
          (rli_direct, ipc_delta, ...); r > -1 required (log1p domain).
    Returns ((lo95, hi95), n_families).
    """
    fams = {}
    for fam, r in rows:
        fams.setdefault(fam, []).append(r)
    fam_names = list(fams.keys())
    F = len(fam_names)
    if F == 0:
        return (float('nan'), float('nan')), 0
    rng = np.random.default_rng(seed)
    gms = np.empty(B)
    for b in range(B):
        pooled = []
        chosen = rng.integers(0, F, F)
        for idx in chosen:
            fam_vals = fams[fam_names[idx]]
            n_f = len(fam_vals)
            if n_f == 1:
                pooled.append(fam_vals[0])
            else:
                j = rng.integers(0, n_f, n_f)
                pooled.extend(fam_vals[k] for k in j)
        logs = np.log1p(np.asarray(pooled, dtype=float))
        gms[b] = np.expm1(np.mean(logs))
    lo, hi = np.percentile(gms, [2.5, 97.5])
    return (float(lo), float(hi)), F


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--csv', required=True, help='champsim_results.csv')
    ap.add_argument('--metric', default='rli_direct',
                     help='column to bootstrap (rli_direct, ipc_delta, ...)')
    ap.add_argument('--B', type=int, default=10000)
    a = ap.parse_args()

    by_scheme = {}
    with open(a.csv, newline='') as f:
        for r in csv.DictReader(f):
            v = r.get(a.metric)
            if v in (None, ''):
                continue
            try:
                v = float(v)
            except ValueError:
                continue
            by_scheme.setdefault(r['policy'], []).append((family_of(r['trace']), v))

    print('%-16s %8s %10s %10s %8s' % ('policy', 'n_traces', 'n_fam', 'CI_lo', 'CI_hi'))
    for policy, rows in sorted(by_scheme.items()):
        (lo, hi), F = cluster_boot_ci(rows, B=a.B)
        print('%-16s %8d %10d %10.4f %8.4f' % (policy, len(rows), F, lo, hi))


if __name__ == '__main__':
    main()
