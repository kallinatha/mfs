#!/usr/bin/env python3
"""Fail fast on incomplete, invalid or guard-violating experiment output."""
import argparse
import csv
import glob
import json
import os


def rows(path):
    with open(path, newline='') as src:
        return list(csv.DictReader(src))


def validate(profiles_dir, lifetime_csv, nvm_csv, cross_csv, expected_mixes):
    profiles = glob.glob(os.path.join(profiles_dir, '**', 'profile.json'), recursive=True)
    life, nvm, cross = rows(lifetime_csv), rows(nvm_csv), rows(cross_csv)
    errors, warnings = [], []
    mix_names = {r['mix'] for r in life}
    if len(profiles) != expected_mixes:
        errors.append('prepared profiles: expected %d, found %d' % (expected_mixes, len(profiles)))
    if len(mix_names) != expected_mixes:
        errors.append('lifetime mixes: expected %d, found %d' % (expected_mixes, len(mix_names)))
    if not life or not nvm or not cross:
        errors.append('one or more result CSVs are empty')
    # ---- acceptance guard -------------------------------------------------
    # A blanket "any guard failure is an error" gate was wrong in both
    # directions.  A wear-management scheme that trades hit rate on some
    # workloads and not others is the normal case, and that split is a result
    # to report, not a pipeline fault; failing the whole run on it pushes the
    # author towards deleting the guard rather than reporting it.  What is
    # genuinely fatal is (a) a *baseline* failing its own guard, which can only
    # mean the harness is broken, and (b) a scheme with no guarded support at
    # all whose unguarded gain is large enough that it would inevitably be
    # quoted -- that is the laundering case the guard exists to catch.
    BASELINES = ('unmanaged', 'lru-hybrid')
    by = {}
    for r in life:
        by.setdefault((r['scheme'], r['arena']), []).append(r)

    def passed(r):
        return r.get('selection_guard_passed') == '1'

    def rli(r):
        try:
            return float(r.get('rli_direct') or 0.0)
        except ValueError:
            return 0.0

    for k, rs in sorted(by.items()):
        n_ok = sum(1 for r in rs if passed(r))
        if k[0] in BASELINES and n_ok != len(rs):
            errors.append('baseline %s/%s failed its own acceptance guard on '
                          '%d/%d mixes - the harness, not the scheme, is wrong'
                          % (k[0], k[1], len(rs) - n_ok, len(rs)))
            continue
        if k[0] in BASELINES:
            continue
        if n_ok == 0:
            gains = [rli(r) for r in rs]
            best = max(gains) if gains else 0.0
            if best > 0.05:
                errors.append(
                    'no guarded support: %s/%s passes the acceptance guard on '
                    '0/%d mixes yet reports up to %.2fx lifetime gain. Either '
                    'report it as a guard-failing scheme with its hit-rate '
                    'cost, or do not report the gain.' % (k[0], k[1], len(rs), best))
        elif n_ok < len(rs):
            warnings.append('%s/%s holds the guard on %d/%d mixes; quote the '
                            'guarded geomean and state the split'
                            % (k[0], k[1], n_ok, len(rs)))
    positive = ('read_latency_ns', 'write_latency_ns', 'read_energy_pJ',
                'write_energy_pJ', 'leakage_power_mW', 'area_mm2')
    for row in nvm:
        for key in positive:
            try:
                ok = float(row[key]) > 0
            except (KeyError, ValueError):
                ok = False
            if not ok:
                errors.append('invalid NVMExplorer %s for %s/%s' %
                              (key, row.get('mix'), row.get('cell')))
        if abs(float(row.get('formula_check_abs_mJ', 'inf'))) > 1e-9:
            errors.append('NVMExplorer formula check failed for %s/%s' %
                          (row.get('mix'), row.get('cell')))
    expected_cross = {(r['mix'], r['scheme'], r['arena']) for r in life}
    actual_cross = {(r['mix'], r['scheme'], r['arena']) for r in cross}
    if actual_cross != expected_cross:
        errors.append('cross-stack join keys differ from lifetime keys')
    if warnings:
        print('GUARD SPLIT (report these, they are results, not faults):')
        for w in warnings:
            print('  - %s' % w)
    if errors:
        raise SystemExit('VALIDATION FAILED\n- ' + '\n- '.join(errors))
    print('VALIDATION PASSED: %d mixes, %d policy rows, %d array-profile rows, '
          '%d scheme(s) with a partial guard split' %
          (expected_mixes, len(life), len(nvm), len(warnings)))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--profiles-dir', required=True)
    ap.add_argument('--lifetime-csv', required=True)
    ap.add_argument('--nvmexplorer-csv', required=True)
    ap.add_argument('--cross-stack-csv', required=True)
    ap.add_argument('--expected-mixes', type=int, required=True)
    args = ap.parse_args()
    validate(args.profiles_dir, args.lifetime_csv, args.nvmexplorer_csv,
             args.cross_stack_csv, args.expected_mixes)


if __name__ == '__main__':
    main()
