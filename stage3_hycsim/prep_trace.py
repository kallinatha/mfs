#!/usr/bin/env python3
"""Prepare a HyCSim trace without holding the trace in memory.

The preferred output is a directory containing mmap-able ``.npy`` arrays plus
``profile.json``.  Conversion is deliberately two-pass: pass one counts and
profiles valid records; pass two writes directly into fixed-size memory maps.
Memory use is therefore O(1) for a 3 GB trace.

HyCSim record formats:
    <cycle> <core> GetS   <hex_addr>
    <cycle> <core> GetX   <hex_addr>
    <cycle> <core> L2_Rpl <hex_addr> <blocksize> <dirty> <tcbit>
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile

import numpy as np
from numpy.lib.format import open_memmap

OPMAP = {b'GetS': 0, b'GetX': 1, b'L2_Rpl': 2}
NAMES = ('GetS', 'GetX', 'L2_Rpl')
DTYPES = {
    'op': np.uint8, 'core': np.uint16, 'cycle': np.uint64,
    'addr': np.uint64, 'bs': np.uint16, 'dirty': np.uint8, 'tcbit': np.uint8,
}


def find_warmup(path):
    """Return byte offset just after the first warm-up marker, or zero."""
    off = 0
    with open(path, 'rb') as src:
        for line in src:
            off += len(line)
            if b'WARMUP COMPLETED' in line:
                return off
    return 0


def parse_line(line):
    p = line.split()
    if len(p) < 4 or p[2] not in OPMAP:
        return None
    op = OPMAP[p[2]]
    if op == 2 and len(p) < 7:
        raise ValueError('short L2_Rpl record')
    try:
        cycle, core, addr = int(p[0]), int(p[1]), int(p[3], 16)
        if op == 2:
            bs, dirty, tcbit = int(p[4]), int(p[5]), int(p[6])
        else:
            bs = dirty = tcbit = 0
    except (ValueError, OverflowError) as exc:
        raise ValueError('invalid numeric field') from exc
    return op, core, cycle, addr, bs, dirty, tcbit


GEM5_TICKS_PER_SECOND = 1e12          # gem5's default Tick is 1 ps


def read_header(path):
    """Return the trace's own summary line, if it has one.

    HyCSim's exported traces begin with, verbatim:
        Sim_seconds: 0.058413 Hits: ... Misses: ...
    That single number is the only independent statement of how much simulated
    time the trace covers, and it is what makes the tick rate checkable instead
    of assumed.
    """
    with open(path, 'rb') as f:
        line = f.readline().decode('utf-8', 'replace')
    m = re.search(r'Sim_seconds:\s*([\d.eE+-]+)', line)
    return (float(m.group(1)) if m else None), line.strip()


def resolve_tick_rate(path, requested, first_cycle, last_cycle, records,
                      total_records=None):
    """Decide what the first column actually means, and prove it.

    **This is not a formality.** The first column of a gem5-exported HyCSim trace
    is a *tick*, and gem5's default tick is 1 ps - not a clock cycle. Treating it
    as cycles at 2 GHz, which this pipeline did until this check existed, inflates
    the measured duration by 500x. Duration divides into every energy rate and
    multiplies into every years-to-failure figure, so the error passes straight
    through to the headline numbers without ever looking wrong.

    The evidence, on the project's own reference trace: a prefix holding roughly
    60% of the records spans 34.93e9 units. Read as 1 ps ticks that is 0.0349 s,
    which is 59.8% of the 0.058413 s the header reports for the whole run -
    exactly right. Read as cycles at 2 GHz it is 17.5 s, or 29,898% of the whole
    run, which is impossible.

    Returns (ticks_per_second, evidence-dict). Raises when the requested rate
    contradicts the header by more than 3x, because silently overriding a
    number the trace states about itself is how the 500x error survived.
    """
    sim_seconds, header = read_header(path)
    span = max(0, (last_cycle or 0) - (first_cycle or 0))
    ev = {'header': header[:200], 'sim_seconds_header': sim_seconds,
          'span_units': span, 'requested_rate_hz': requested}

    if sim_seconds and span:
        # The scanned region is the post-warm-up part, and may be a prefix.
        # Its share of the whole run bounds how much of Sim_seconds it may use.
        share = (records / total_records) if total_records else None
        implied = span / sim_seconds
        ev['implied_rate_hz_if_region_is_whole_run'] = implied
        ev['region_record_share'] = share
        for name, rate in (('gem5_tick_1ps', GEM5_TICKS_PER_SECOND),
                           ('cycles_at_requested', float(requested or 0) or None)):
            if not rate:
                continue
            frac = (span / rate) / sim_seconds
            ev.setdefault('fraction_of_sim_seconds', {})[name] = frac
        chosen_frac = ev['fraction_of_sim_seconds'].get('gem5_tick_1ps')
        if chosen_frac is not None and chosen_frac <= 1.5:
            ev['decision'] = ('first column read as gem5 ticks at 1 ps: the '
                              'scanned region accounts for %.1f%% of the '
                              'Sim_seconds the trace reports for itself'
                              % (100 * chosen_frac))
            ev['tick_rate_source'] = 'gem5 default tick, cross-checked against '\
                                     'the Sim_seconds header'
            return GEM5_TICKS_PER_SECOND, ev
        alt = ev['fraction_of_sim_seconds'].get('cycles_at_requested')
        if alt is not None and alt <= 1.5:
            ev['decision'] = ('first column read as clock cycles at %.3g Hz; '
                              'the region accounts for %.1f%% of Sim_seconds'
                              % (requested, 100 * alt))
            ev['tick_rate_source'] = 'user-supplied rate, cross-checked'
            return float(requested), ev
        raise ValueError(
            'Trace time base does not reconcile with the header.\n'
            '  Sim_seconds (whole run, from the trace): %g s\n'
            '  scanned span: %d units over %d records\n'
            '  as 1 ps gem5 ticks -> %.4g s (%.0f%% of the whole run)\n'
            '  as cycles at %.4g Hz -> %.4g s (%.0f%% of the whole run)\n'
            'Neither is <= 150%%. Pass the correct --clock-hz, or --no-clock-check '
            'if you accept an unverified time base and the energy and lifetime '
            'numbers it produces.'
            % (sim_seconds, span, records,
               span / GEM5_TICKS_PER_SECOND,
               100 * span / GEM5_TICKS_PER_SECOND / sim_seconds,
               requested, span / float(requested),
               100 * span / float(requested) / sim_seconds))

    ev['decision'] = ('no Sim_seconds header; falling back to the requested rate '
                      'UNVERIFIED. Energy per second and years-to-failure scale '
                      'linearly with this and cannot be trusted without it.')
    ev['tick_rate_source'] = 'unverified fallback'
    return float(requested), ev


def scan(path, start, limit, clock_hz, check_clock=True):
    counts = [0, 0, 0]
    dirty_replacements = 0
    malformed = ignored = n = 0
    first_cycle = last_cycle = None
    digest = hashlib.sha256()
    consumed = 0
    with open(path, 'rb') as src:
        src.seek(start)
        for line in src:
            digest.update(line)
            consumed += len(line)
            try:
                rec = parse_line(line)
            except ValueError:
                malformed += 1
                continue
            if rec is None:
                ignored += 1
                continue
            op, _, cycle, _, _, dirty, _ = rec
            counts[op] += 1
            dirty_replacements += int(op == 2 and dirty != 0)
            first_cycle = cycle if first_cycle is None else min(first_cycle, cycle)
            last_cycle = cycle if last_cycle is None else max(last_cycle, cycle)
            n += 1
            if limit and n >= limit:
                break
    if n == 0:
        raise ValueError('no valid post-warm-up HyCSim records found in %s' % path)
    cycle_span = max(0, last_cycle - first_cycle)
    if check_clock:
        rate, evidence = resolve_tick_rate(path, clock_hz, first_cycle,
                                           last_cycle, n)
    else:
        rate, evidence = float(clock_hz), {
            'decision': 'clock check disabled with --no-clock-check; the time '
                        'base is unverified and every per-second and '
                        'per-year number inherits that.',
            'tick_rate_source': 'unchecked'}
    duration_s = cycle_span / rate if cycle_span else 0.0
    rates = {NAMES[i] + '_per_s': (counts[i] / duration_s if duration_s else 0.0)
             for i in range(3)}
    return {
        'schema_version': 1,
        'source': os.path.abspath(path),
        'source_region_sha256': digest.hexdigest(),
        'source_region_bytes': consumed,
        'warmup_marker_found': bool(start),
        'post_warmup_byte_offset': start,
        'records': n,
        'counts': {NAMES[i]: counts[i] for i in range(3)},
        'dirty_L2_Rpl': dirty_replacements,
        'malformed_records': malformed,
        'ignored_lines': ignored,
        'first_cycle': first_cycle,
        'last_cycle': last_cycle,
        'cycle_span': cycle_span,
        'clock_hz': float(clock_hz),
        'tick_rate_hz': rate,
        'tick_rate_evidence': evidence,
        'duration_s': duration_s,
        'rates': rates,
        'traffic_mapping': {
            'logical_read': 'GetS',
            'ownership_request': 'GetX',
            'array_fill_or_writeback': 'L2_Rpl',
            'note': 'Policy-specific NVM/SRAM physical events come from the LLC result CSV.',
        },
    }


def write_arrays(path, start, arrays, n, progress_every=5_000_000):
    i = 0
    with open(path, 'rb') as src:
        src.seek(start)
        for line in src:
            try:
                rec = parse_line(line)
            except ValueError:
                continue
            if rec is None:
                continue
            for key, value in zip(('op', 'core', 'cycle', 'addr', 'bs', 'dirty', 'tcbit'), rec):
                arrays[key][i] = value
            i += 1
            if progress_every and i % progress_every == 0:
                print('  wrote %.1f M records' % (i / 1_000_000.0), flush=True)
            if i >= n:
                break
    for value in arrays.values():
        value.flush()
    if i != n:
        raise RuntimeError('trace changed between passes: expected %d records, found %d' % (n, i))


def convert(path, out, limit=0, clock_hz=2.0e9, force=False, check_clock=True):
    path, out = os.path.abspath(path), os.path.abspath(out)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    start = find_warmup(path)
    print('pass 1/2: profile %s region' % ('post-warm-up' if start else 'complete'), flush=True)
    profile = scan(path, start, limit, clock_hz, check_clock)
    n = profile['records']

    if out.endswith('.npz') and n > 5_000_000:
        raise ValueError('.npz is limited to 5M records; use a directory for multi-GB traces')
    parent = os.path.dirname(out) or '.'
    os.makedirs(parent, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='.prep-', dir=parent)
    try:
        arrays = {key: open_memmap(os.path.join(tmp, key + '.npy'), mode='w+',
                                   dtype=dtype, shape=(n,))
                  for key, dtype in DTYPES.items()}
        print('pass 2/2: write %d mmap records' % n, flush=True)
        write_arrays(path, start, arrays, n)
        with open(os.path.join(tmp, 'profile.json'), 'w') as dst:
            json.dump(profile, dst, indent=2, sort_keys=True)
            dst.write('\n')

        if out.endswith('.npz'):
            if os.path.exists(out) and not force:
                raise FileExistsError('%s exists; use --force to replace it' % out)
            np.savez_compressed(out, **{k: np.load(os.path.join(tmp, k + '.npy'), mmap_mode='r')
                                      for k in DTYPES})
            with open(out + '.profile.json', 'w') as dst:
                json.dump(profile, dst, indent=2, sort_keys=True)
                dst.write('\n')
        else:
            if os.path.exists(out):
                if not force:
                    raise FileExistsError('%s exists; use --force to replace it' % out)
                shutil.rmtree(out)
            os.replace(tmp, out)
            tmp = None
    finally:
        if tmp and os.path.exists(tmp):
            shutil.rmtree(tmp)
    print('wrote %s: %d records (%s)' % (
        out, n, ', '.join('%s=%d' % x for x in profile['counts'].items())), flush=True)
    return profile


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('trace')
    ap.add_argument('-o', '--out', required=True,
                    help='mmap output directory (recommended), or .npz for <=5M records')
    ap.add_argument('-n', '--limit', type=int, default=0,
                    help='stop after N post-warm-up records (0 = all)')
    ap.add_argument('--clock-hz', type=float, default=2.0e9,
                    help='trace clock used to derive events/s (default: 2 GHz)')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--no-clock-check', action='store_true',
                    help='skip the Sim_seconds cross-check on the time base')
    args = ap.parse_args()
    convert(args.trace, args.out, args.limit, args.clock_hz, args.force,
            not args.no_clock_check)


if __name__ == '__main__':
    main()
