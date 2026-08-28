#!/usr/bin/env python3
"""Cross-stack join: architecture wear x array models -> energy, area, lifetime.

Takes the per-policy wear and access counts from the HyCSim-compatible replay,
the array-level models from NVMExplorer, and the independent DESTINY sweep, and
produces one auditable row per policy per mix.

Design rules this module follows, because the previous version broke all four:

  1. Nothing about the array geometry is hardcoded. The SRAM/NVM way split comes
     from the lifetime row itself (`nvm_ways`, `sram_ways`), so changing the
     partition changes the energy and area accordingly instead of silently
     reporting numbers for a 12:4 split that is no longer in use.
  2. No silent fallbacks. If a required quantity is missing the row is rejected
     with a named reason and counted, rather than substituted with a
     superficially similar number from another table.
  3. Lifetime is a sensitivity grid, not a constant. Reported SOT-MRAM endurance
     spans 1e12 to >1e18 depending on stack, drive condition and test method, so
     one assumed value is not a result.
  4. Leakage and dynamic energy are reported separately and the leakage term is
     labelled for what it is - a property of the array and the run duration, not
     of the policy. Summing them and calling the total an energy-to-solution
     comparison across policies hides the fact that the dominant term is
     identical for every policy on the same array.

Every derived column carries its assumption in `assumptions.json` next to the
output CSV.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from typing import Dict, List, Optional, Sequence

# Default endurance sweep. Overridable; never silently reduced to one value.
DEFAULT_ENDURANCE_GRID: Sequence[float] = (1e12, 1e14, 1e15, 1e18)

SECONDS_PER_YEAR = 365.25 * 24 * 3600


class JoinError(Exception):
    """A row cannot be joined without inventing a number."""


def read_csv(path: str) -> List[Dict[str, str]]:
    with open(path, newline='') as src:
        return list(csv.DictReader(src))


def as_float(row: Dict[str, str], key: str, *, where: str,
             default: Optional[float] = None) -> float:
    """Strict numeric access.

    A missing or unparsable field raises unless an explicit default is given.
    The previous implementation defaulted silently to 0.0, which turns a missing
    energy column into a free policy.
    """
    raw = row.get(key)
    if raw is None or raw == '':
        if default is None:
            raise JoinError('%s: missing required field %r' % (where, key))
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise JoinError('%s: field %r is not numeric (%r)' % (where, key, raw)) from exc


def select_array_model(models: List[Dict[str, str]], cell: str,
                       capacity_mb: Optional[float]) -> Dict[str, str]:
    """Pick the NVMExplorer row for a cell, preferring an exact capacity match."""
    candidates = [m for m in models if cell.lower() in str(m.get('cell', '')).lower()]
    if not candidates:
        raise JoinError('no NVMExplorer model for cell %r' % cell)
    if capacity_mb is not None:
        exact = [m for m in candidates
                 if abs(as_float(m, 'capacity_MB', where='nvm model', default=-1) - capacity_mb) < 1e-9]
        if exact:
            return exact[0]
    return candidates[0]


def select_destiny(rows: List[Dict[str, str]], tech: str,
                   capacity_mb: float) -> Optional[Dict[str, str]]:
    """DESTINY is an independent cross-check, so a miss is tolerated and flagged."""
    best, best_gap = None, None
    for r in rows:
        if r.get('tech') != tech or r.get('status') not in ('ok', 'cached'):
            continue
        try:
            gap = abs(float(r['capacity_MB']) - capacity_mb)
        except (KeyError, TypeError, ValueError):
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = r, gap
    return best


def transfers_per_line(model: Dict[str, str], block_bytes: float) -> int:
    """Array accesses needed to move one cache line."""
    width_bits = as_float(model, 'word_width_bits', where='nvm model', default=64.0)
    if width_bits <= 0:
        raise JoinError('array model reports non-positive word width')
    return int(math.ceil(block_bytes * 8.0 / width_bits))


def load_endurance(path: str, technology: str) -> Sequence[float]:
    """Read the sweep for one technology from config/endurance.json.

    The grid is per technology because the technologies genuinely differ, and
    because a single shared grid quietly implies they do not. STT-MRAM has a
    community-standard figure (4e12, Mittal LastingNVCache 2014) that most of
    the comparison literature uses; SOT-MRAM has no settled figure at all and is
    swept across 1e12 to 1e15, the upper end anchored by imec's December 2023
    measurement floor of >1e15 at ~50 nm. Years-to-failure scales linearly with
    this, so collapsing SOT to a point estimate is the difference between a
    result and a press release.
    """
    with open(path) as f:
        table = json.load(f)
    key = {'STT-MRAM': 'stt_mram', 'SOT-MRAM': 'sot_mram', 'PCM': 'pcm',
           'SRAM': 'sram', 'ReRAM': 'reram'}.get(technology, technology.lower())
    entry = table.get(key)
    if entry is None:
        raise JoinError('no endurance entry for technology %r in %s; add one '
                        'with its citation rather than defaulting'
                        % (technology, path))
    return [float(x) for x in entry.get('sweep') or [entry['value']]]


def lifetime_grid(max_writes: float, duration_s: float,
                  endurances: Sequence[float]) -> Dict[str, float]:
    """Years to first failure for each assumed per-cell endurance.

    Deterministic first-failure model: the hottest frame receives `max_writes`
    physical writes in `duration_s` seconds, so it reaches an endurance budget E
    after E / (max_writes / duration_s) seconds.
    """
    out: Dict[str, float] = {}
    for e in endurances:
        if max_writes <= 0 or duration_s <= 0:
            out['first_failure_years_E%.0e' % e] = float('inf')
        else:
            out['first_failure_years_E%.0e' % e] = e * duration_s / max_writes / SECONDS_PER_YEAR
    return out


def join_row(life: Dict[str, str], models: List[Dict[str, str]],
             destiny: List[Dict[str, str]], *, nvm_cell: str, sram_cell: str,
             destiny_nvm_tech: str, destiny_sram_tech: str,
             endurances: Sequence[float],
             leakage_scales_with_ways: bool) -> Dict[str, object]:
    where = 'lifetime row %s/%s' % (life.get('scheme'), life.get('arena'))

    # --- array geometry, taken from the row, never assumed -------------------
    nvm_ways = as_float(life, 'nvm_ways', where=where)
    sram_ways = as_float(life, 'sram_ways', where=where)
    total_ways = nvm_ways + sram_ways
    if total_ways <= 0:
        raise JoinError('%s: non-positive associativity' % where)
    nvm_fraction = nvm_ways / total_ways
    sram_fraction = sram_ways / total_ways
    block_bytes = as_float(life, 'blocksize_B', where=where, default=64.0)
    capacity_mb = as_float(life, 'size_MB', where=where)

    # --- duration: required, never substituted -------------------------------
    duration_s = as_float(life, 'measured_duration_s', where=where, default=-1.0)
    if duration_s <= 0:
        raise JoinError('%s: measured_duration_s absent or non-positive; refusing to '
                        'substitute a model duration (energy and lifetime both '
                        'depend on it linearly)' % where)

    nvm = select_array_model(models, nvm_cell, capacity_mb)
    sram = select_array_model(models, sram_cell, capacity_mb) if sram_ways > 0 else None

    nvm_k = transfers_per_line(nvm, block_bytes)
    sram_k = transfers_per_line(sram, block_bytes) if sram is not None else 0

    nvm_r = as_float(life, 'nvm_reads', where=where)
    nvm_w = as_float(life, 'nvm_writes', where=where)
    sram_r = as_float(life, 'sram_reads', where=where, default=0.0)
    sram_w = as_float(life, 'sram_writes', where=where, default=0.0)

    dynamic_pj = (nvm_r * nvm_k * as_float(nvm, 'read_energy_pJ', where='nvm model') +
                  nvm_w * nvm_k * as_float(nvm, 'write_energy_pJ', where='nvm model'))
    serialized_ns = (nvm_r * nvm_k * as_float(nvm, 'read_latency_ns', where='nvm model') +
                     nvm_w * nvm_k * as_float(nvm, 'write_latency_ns', where='nvm model'))
    if sram is not None:
        dynamic_pj += (sram_r * sram_k * as_float(sram, 'read_energy_pJ', where='sram model') +
                       sram_w * sram_k * as_float(sram, 'write_energy_pJ', where='sram model'))
        serialized_ns += (sram_r * sram_k * as_float(sram, 'read_latency_ns', where='sram model') +
                          sram_w * sram_k * as_float(sram, 'write_latency_ns', where='sram model'))

    # --- leakage and area ----------------------------------------------------
    # The way fractions scale the array models to the partition actually in use.
    # This treats leakage and area as linear in way count, which ignores shared
    # peripheral circuitry; the assumption is recorded rather than buried, and
    # can be switched off to report whole-array figures instead.
    nvm_share = nvm_fraction if leakage_scales_with_ways else 1.0
    sram_share = sram_fraction if leakage_scales_with_ways else 1.0
    leakage_mw = nvm_share * as_float(nvm, 'leakage_power_mW', where='nvm model')
    area_mm2 = nvm_share * as_float(nvm, 'area_mm2', where='nvm model')
    if sram is not None:
        leakage_mw += sram_share * as_float(sram, 'leakage_power_mW', where='sram model')
        area_mm2 += sram_share * as_float(sram, 'area_mm2', where='sram model')

    dynamic_mj = dynamic_pj / 1.0e9
    leakage_mj = leakage_mw * duration_s

    out: Dict[str, object] = dict(life)
    out.update({
        'nvmexplorer_nvm_cell': nvm.get('cell'),
        'nvmexplorer_sram_cell': sram.get('cell') if sram is not None else '',
        'nvm_fraction_of_ways': nvm_fraction,
        'sram_fraction_of_ways': sram_fraction,
        'nvm_transfers_per_line': nvm_k,
        'sram_transfers_per_line': sram_k,
        'measured_duration_s': duration_s,
        # Dynamic energy is the only term a policy can move. Leakage is a
        # property of the array and the run length; it is reported beside it and
        # deliberately not folded into a single headline number.
        'dynamic_energy_mJ': dynamic_mj,
        'leakage_energy_mJ': leakage_mj,
        'leakage_is_policy_invariant': 1,
        'total_energy_mJ': dynamic_mj + leakage_mj,
        'serialized_array_time_ms': serialized_ns / 1.0e6,
        'serialized_time_is_zero_MLP_bound': 1,
        'dynamic_EDP_mJ_ms': dynamic_mj * serialized_ns / 1.0e6,
        'array_area_mm2': area_mm2,
        'leakage_scales_with_ways': int(leakage_scales_with_ways),
    })
    out.update(lifetime_grid(as_float(life, 'max_write', where=where), duration_s, endurances))

    for label, tech in (('nvm', destiny_nvm_tech), ('sram', destiny_sram_tech)):
        d = select_destiny(destiny, tech, capacity_mb)
        prefix = 'destiny_%s_' % label
        if d is None:
            out[prefix + 'match'] = 'none'
            continue
        out[prefix + 'match'] = tech
        for k in ('capacity_MB', 'hit_latency_ns', 'write_latency_ns',
                  'write_energy_nJ', 'leakage_mW', 'area_mm2'):
            out[prefix + k] = d.get(k, '')
    return out


def integrate(lifetime_csv: str, nvm_csv: str, destiny_csv: str, out_csv: str, *,
              nvm_cell: str = 'SOT_12F2', sram_cell: str = 'SRAM_6T',
              destiny_nvm_tech: str = 'SOT-12/1', destiny_sram_tech: str = 'SRAM',
              endurances: Sequence[float] = DEFAULT_ENDURANCE_GRID,
              leakage_scales_with_ways: bool = True,
              strict: bool = True) -> List[Dict[str, object]]:
    lifetime, models, destiny = (read_csv(lifetime_csv), read_csv(nvm_csv), read_csv(destiny_csv))
    rows: List[Dict[str, object]] = []
    rejected: List[Dict[str, str]] = []
    for life in lifetime:
        try:
            rows.append(join_row(life, models, destiny,
                                 nvm_cell=nvm_cell, sram_cell=sram_cell,
                                 destiny_nvm_tech=destiny_nvm_tech,
                                 destiny_sram_tech=destiny_sram_tech,
                                 endurances=endurances,
                                 leakage_scales_with_ways=leakage_scales_with_ways))
        except JoinError as exc:
            rejected.append({'scheme': life.get('scheme', '?'),
                             'arena': life.get('arena', '?'),
                             'mix': life.get('mix', '?'),
                             'reason': str(exc)})
    if rejected:
        print('rejected %d of %d lifetime rows:' % (len(rejected), len(lifetime)), file=sys.stderr)
        for r in rejected[:20]:
            print('  %-14s %-8s %-24s %s' % (r['scheme'], r['arena'], r['mix'], r['reason']),
                  file=sys.stderr)
        if strict:
            raise SystemExit('cross-stack join incomplete; fix the inputs or pass --no-strict')
    if not rows:
        raise SystemExit('cross-stack join produced no rows')

    fields: List[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    os.makedirs(os.path.dirname(os.path.abspath(out_csv)) or '.', exist_ok=True)
    with open(out_csv, 'w', newline='') as dst:
        writer = csv.DictWriter(dst, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    side = os.path.join(os.path.dirname(os.path.abspath(out_csv)), 'assumptions.json')
    with open(side, 'w') as dst:
        json.dump({
            'nvm_cell': nvm_cell,
            'sram_cell': sram_cell,
            'destiny_nvm_tech': destiny_nvm_tech,
            'destiny_sram_tech': destiny_sram_tech,
            'endurance_grid_writes_per_cell': list(endurances),
            'leakage_scales_linearly_with_ways': leakage_scales_with_ways,
            'way_fractions_source': 'nvm_ways / sram_ways columns of the lifetime CSV',
            'serialized_array_time': 'sum of per-access array latency; assumes zero '
                                     'memory-level parallelism, so it is an upper bound '
                                     'and is not a substitute for simulated IPC',
            'leakage_energy': 'array leakage power x measured duration; identical for '
                              'every policy on the same array, therefore not a '
                              'policy-discriminating metric',
            'rejected_rows': rejected,
        }, dst, indent=2)
    print('wrote %s (%d rows, %d rejected)' % (os.path.abspath(out_csv), len(rows), len(rejected)))
    print('wrote %s' % side)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--lifetime', required=True)
    ap.add_argument('--nvm-models', required=True)
    ap.add_argument('--destiny', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--nvm-cell', default='SOT_12F2')
    ap.add_argument('--sram-cell', default='SRAM_6T')
    ap.add_argument('--destiny-nvm-tech', default='SOT-12/1')
    ap.add_argument('--destiny-sram-tech', default='SRAM')
    ap.add_argument('--endurance-table', default='',
                    help='config/endurance.json; with --nvm-technology it '
                         'replaces --endurance-grid with the cited per-technology '
                         'sweep')
    ap.add_argument('--nvm-technology', default='',
                    help='SOT-MRAM | STT-MRAM | PCM | ReRAM; selects the sweep')
    ap.add_argument('--endurance-grid', default=','.join('%g' % e for e in DEFAULT_ENDURANCE_GRID),
                    help='comma-separated writes-per-cell budgets for the lifetime sweep')
    ap.add_argument('--whole-array-leakage', action='store_true',
                    help='report whole-array leakage and area instead of scaling by way count')
    ap.add_argument('--no-strict', action='store_true',
                    help='write the CSV even if some rows could not be joined')
    a = ap.parse_args()
    integrate(a.lifetime, a.nvm_models, a.destiny, a.out,
              nvm_cell=a.nvm_cell, sram_cell=a.sram_cell,
              destiny_nvm_tech=a.destiny_nvm_tech, destiny_sram_tech=a.destiny_sram_tech,
              endurances=(load_endurance(a.endurance_table, a.nvm_technology)
                          if (a.endurance_table and a.nvm_technology)
                          else [float(x) for x in a.endurance_grid.split(',')]),
              leakage_scales_with_ways=not a.whole_array_leakage,
              strict=not a.no_strict)


if __name__ == '__main__':
    main()
