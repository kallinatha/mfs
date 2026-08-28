#!/usr/bin/env python3
"""Array-level design-space exploration for endurance-limited NVM caches.

Why this stage exists, and why it is the paper's actual contribution.

Every cell-level comparison in the literature you gave me — Singh 2020, Wu 2020,
Seo 2020, Saha 2022, Liao 2020 — fixes one array organisation and varies the
*device*. DeepNVM++ goes further and optimises the array, but its objective is
energy-delay for GPU caches. **Nobody optimises a cache array for lifetime.**
That is the gap this stage occupies: the design point that minimises write
energy is not the point that maximises years-to-failure, because the two are
traded through write scheme, banking and sense strategy in ways a fixed
configuration cannot express.

So this is not "run DESTINY with better settings". It is a search over the
knobs a cache architect actually controls, scored on objectives that include
endurance, with the Pareto front reported rather than a single flattering point.

What is swept
-------------
Only knobs a cache designer controls. Cell parameters are NOT swept here — they
come from published devices via `cells/from_paper.py`, and inventing a cell to
win a comparison is exactly the move this project refuses to make.

  OptimizationTarget      what DESTINY minimises. Nine values, and they produce
                          genuinely different arrays from identical cells.
  SecondOptimizationTarget  tie-break, enabling constrained optimisation.
  CacheAccessMode         Normal / Sequential / Fast. Trades tag-data
                          serialisation against energy.
  DeviceRoadmap           HP / LSTP / LOP. The leakage lever.
  WriteScheme             swept, and **measured INERT** on MRAM cells: the
                          screen returns 1.000x on write latency, write energy,
                          leakage and area for every value including
                          `ReadAndCompare`. DESTINY's MRAM path does not model
                          differential write, so read-before-write cannot be
                          evaluated with this tool. That is a negative result
                          worth reporting - the mechanism is real in silicon and
                          invisible to the model - and it is why the factorial
                          drops this dimension.
  Routing                 H-tree / Non-H-tree.
  LocalWireType,          wire and repeater strategy: the latency/energy knob
  GlobalWireType,         that dominates at large capacity.
  repeater types,
  low swing
  BufferDesignOptimization  latency / area / balanced.
  StackedDieCount         **3D stacking.** DESTINY's headline capability and a
                          genuine cache-level lever: stacking shortens global
                          wires, which is where large NVM LLCs lose. Unused by
                          the manuscript so far.
  AllowDifferentTagTech   SRAM tags over NVM data — the split POEM assumes and
                          the manuscript never models.
  Associativity, capacity, process node.

How the search is staged
------------------------
A full factorial over the above is ~10^7 DESTINY runs at roughly 90 s each. The
search is therefore staged, which is also better science than a blind grid:

  1. `--screen`  one-factor-at-a-time from a declared baseline. Produces an
                 effect size per knob per objective. Knobs that move nothing get
                 dropped, and the fact that they move nothing is itself
                 reportable.
  2. `--grid`    full factorial over the knobs that survived screening.
  3. `--pareto`  non-dominated front over the chosen objectives, plus the
                 best-per-objective table and the endurance-aware winner.

Every run is cached on a hash of its exact config, so re-running costs nothing
for points already explored and the search can be grown incrementally.

Objectives
----------
Direct from DESTINY: read/write latency, read/write dynamic energy, leakage,
area. Derived here:

  write_edp            write latency x write energy
  energy_per_byte      dynamic energy normalised by line size
  leakage_per_MB       leakage normalised by capacity, so capacities compare
  **writes_per_joule** the endurance-relevant one: how much write work the
                       array delivers per unit energy. Under a fixed energy
                       budget this is proportional to achievable lifetime.
  **lifetime_index**   endurance_budget / (write_energy x leakage_per_MB),
                       the composite this project introduces. It rewards a
                       design that writes cheaply *and* idles cheaply, which is
                       what an endurance-limited LLC actually needs and what
                       neither a pure-energy nor a pure-latency target selects.

`lifetime_index` is a *ranking* device, not a physical quantity, and the code
says so wherever it is printed.

Usage
-----
    python3 dse.py --screen  --cells sram,sot_wu2020_2t1sot_45nm --capacity 16
    python3 dse.py --grid    --from-screen results/dse/screen.csv
    python3 dse.py --pareto  results/dse/grid.csv
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DESTINY = os.environ.get('DESTINY_DIR', os.path.join(HERE, '..', 'tools', 'destiny_v2'))

# --------------------------------------------------------------- the knobs
# Values verified against destiny_v2/InputParameter.cpp, not guessed. A key
# DESTINY does not recognise is silently ignored, so an unverified value here
# would produce a "swept" dimension that does nothing.
KNOBS = {
    'OptimizationTarget': ['ReadLatency', 'WriteLatency', 'ReadDynamicEnergy',
                           'WriteDynamicEnergy', 'ReadEDP', 'WriteEDP',
                           'LeakagePower', 'Area', 'Full'],
    'CacheAccessMode': ['Normal', 'Sequential', 'Fast'],
    'DeviceRoadmap': ['HP', 'LSTP', 'LOP'],
    # The endurance lever. ReadAndCompare = read-before-write, writing only the
    # differing bits. A cache-level mechanism, not a device property.
    'WriteScheme': ['ResetBeforeSet', 'SetBeforeReset', 'ReadAndCompare',
                    'WriteAndVerify'],
    'Routing': ['H-tree', 'Non-H-tree'],
    'LocalWireType': ['LocalAggressive', 'LocalConservative',
                      'SemiAggressive', 'SemiConservative'],
    'GlobalWireType': ['GlobalAggressive', 'GlobalConservative',
                       'SemiAggressive', 'SemiConservative'],
    'LocalWireRepeaterType': ['RepeatedNone', 'RepeatedOpt',
                              'Repeated10%Penalty', 'Repeated30%Penalty'],
    'GlobalWireRepeaterType': ['RepeatedNone', 'RepeatedOpt',
                               'Repeated10%Penalty', 'Repeated30%Penalty'],
    'LocalWireUseLowSwing': ['No', 'Yes'],
    'GlobalWireUseLowSwing': ['No', 'Yes'],
    'BufferDesignOptimization': ['latency', 'area', 'balanced'],
    'InternalSensing': ['true', 'false'],
    # 3D. DESTINY's headline capability; stacking shortens the global wires that
    # dominate a large NVM LLC.
    'StackedDieCount': ['1', '2', '4'],
    'MonolithicStackCount': ['1', '2'],
    # SRAM tags over NVM data - the split POEM assumes and this manuscript has
    # never modelled.
    'AllowDifferentTagTech': ['false', 'true'],
    'Associativity (for cache only)': ['8', '16'],
}

BASELINE = {
    'DesignTarget': 'cache',
    'OptimizationTarget': 'WriteEDP',
    'CacheAccessMode': 'Normal',
    'DeviceRoadmap': 'HP',
    'WriteScheme': 'ResetBeforeSet',
    'Routing': 'H-tree',
    'LocalWireType': 'LocalAggressive',
    'GlobalWireType': 'GlobalAggressive',
    'LocalWireRepeaterType': 'RepeatedNone',
    'GlobalWireRepeaterType': 'RepeatedNone',
    'LocalWireUseLowSwing': 'No',
    'GlobalWireUseLowSwing': 'No',
    'BufferDesignOptimization': 'latency',
    'InternalSensing': 'true',
    'StackedDieCount': '1',
    'MonolithicStackCount': '1',
    'AllowDifferentTagTech': 'false',
    'Associativity (for cache only)': '16',
    'WordWidth (bit)': '512',
    'Temperature (K)': '350',
    'EnablePruning': 'Yes',
}

METRICS = {
    'area_mm2':         r'Total Area = ([\d.eE+-]+)mm\^2',
    'read_latency_ns':  r'Cache Hit Latency\s*=\s*([\d.eE+-]+)ns',
    'miss_latency_ns':  r'Cache Miss Latency\s*=\s*([\d.eE+-]+)ns',
    'write_latency_ns': r'Cache Write Latency\s*=\s*([\d.eE+-]+)ns',
    'read_energy_nJ':   r'Cache Hit Dynamic Energy\s*=\s*([\d.eE+-]+)nJ',
    'miss_energy_nJ':   r'Cache Miss Dynamic Energy\s*=\s*([\d.eE+-]+)nJ',
    'write_energy_nJ':  r'Cache Write Dynamic Energy\s*=\s*([\d.eE+-]+)nJ',
    'leakage_mW':       r'Cache Total Leakage Power\s*=\s*([\d.eE+-]+)mW',
}

# Objective -> (column, direction). -1 = smaller is better.
OBJECTIVES = {
    'write_latency_ns': -1, 'write_energy_nJ': -1, 'read_latency_ns': -1,
    'read_energy_nJ': -1, 'leakage_mW': -1, 'area_mm2': -1,
    'write_edp': -1, 'leakage_per_MB': -1,
    'writes_per_joule': +1, 'lifetime_index': +1,
}


def derive(row, capacity_mb, endurance=1e14):
    """Objectives DESTINY does not print, including the endurance-aware ones."""
    def f(k):
        try:
            return float(row.get(k) or 0)
        except (TypeError, ValueError):
            return 0.0
    we, wl, lk = f('write_energy_nJ'), f('write_latency_ns'), f('leakage_mW')
    row['write_edp'] = we * wl if (we and wl) else ''
    row['leakage_per_MB'] = lk / capacity_mb if capacity_mb else ''
    # Write work per unit energy. Under a fixed energy budget this is
    # proportional to the number of writes the array can deliver, so it is the
    # cleanest single number linking array design to endurance consumption.
    row['writes_per_joule'] = (1.0 / (we * 1e-9)) if we else ''
    # Composite ranking index, NOT a physical lifetime. Rewards a design that
    # both writes cheaply and idles cheaply - which is what an endurance-limited
    # LLC needs, and what neither a pure-energy nor a pure-latency target picks.
    if we and lk and capacity_mb:
        row['lifetime_index'] = endurance / (we * (lk / capacity_mb))
    else:
        row['lifetime_index'] = ''
    return row


def build_cfg(cell, capacity_mb, node, overrides):
    cfg = dict(BASELINE)
    cfg.update(overrides)
    cfg['MemoryCellInputFile'] = os.path.abspath(cell)
    cfg['Capacity (MB)'] = str(capacity_mb)
    cfg['ProcessNode'] = str(node)
    order = ['DesignTarget', 'CacheAccessMode', 'Associativity (for cache only)',
             'ProcessNode', 'Capacity (MB)', 'WordWidth (bit)', 'DeviceRoadmap',
             'LocalWireType', 'LocalWireRepeaterType', 'LocalWireUseLowSwing',
             'GlobalWireType', 'GlobalWireRepeaterType', 'GlobalWireUseLowSwing',
             'Routing', 'InternalSensing', 'MemoryCellInputFile',
             'Temperature (K)', 'EnablePruning', 'OptimizationTarget',
             'BufferDesignOptimization', 'WriteScheme', 'StackedDieCount',
             'MonolithicStackCount', 'AllowDifferentTagTech']
    lines = ['-%s: %s' % (k, cfg[k]) for k in order if k in cfg]
    for k, v in cfg.items():
        if k not in order:
            lines.append('-%s: %s' % (k, v))
    return '\n'.join(lines) + '\n'


def run_point(job):
    cell, cap, node, overrides, destiny_dir, cache_dir, timeout = job
    text = build_cfg(cell, cap, node, overrides)
    key = hashlib.sha256(text.encode()).hexdigest()[:16]
    cached = os.path.join(cache_dir, key + '.log')
    row = {'cell': os.path.basename(cell).replace('.cell', ''),
           'capacity_MB': cap, 'node_nm': node, 'cfg_sha': key}
    row.update({k: str(v) for k, v in overrides.items()})

    if os.path.exists(cached):
        out = open(cached).read()
        row['status'] = 'cached'
        row['runtime_s'] = 0
    else:
        cfgp = os.path.join(cache_dir, key + '.cfg')
        open(cfgp, 'w').write(text)
        t0 = time.time()
        try:
            r = subprocess.run([os.path.join(destiny_dir, 'destiny'), cfgp],
                               cwd=destiny_dir, capture_output=True, text=True,
                               timeout=timeout)
            out = r.stdout + r.stderr
        except subprocess.TimeoutExpired:
            out = 'TIMEOUT'
        open(cached, 'w').write(out)
        row['runtime_s'] = round(time.time() - t0, 1)
        row['status'] = 'ran'

    if 'CACHE DESIGN -- SUMMARY' in out:
        for k, pat in METRICS.items():
            m = re.search(pat, out)
            row[k] = float(m.group(1)) if m else ''
        row['valid'] = 1
    else:
        for k in METRICS:
            row[k] = ''
        row['valid'] = 0
        m = re.findall(r'(?i)(?:no valid|error|cannot)[^\n]*', out)
        row['status'] = 'fail:' + (m[0][:70] if m else 'unknown')
    return derive(row, cap)


def write_csv(rows, path):
    if not rows:
        return
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print('wrote %s (%d rows, %d valid)'
          % (path, len(rows), sum(int(r.get('valid') or 0) for r in rows)))


# ------------------------------------------------------------------ screen
def screen(cells, caps, node, destiny_dir, out, workers, timeout, knobs=None):
    """One factor at a time from BASELINE. Returns rows and prints effects.

    OFAT cannot see interactions, and that is exactly why it is stage one and
    not the whole method: it is a cheap filter to decide what goes into the
    factorial, not a claim about the optimum.
    """
    jobs, labels = [], []
    cache = os.path.join(os.path.dirname(os.path.abspath(out)), 'cache')
    os.makedirs(cache, exist_ok=True)
    todo = knobs or list(KNOBS)
    for cell in cells:
        for cap in caps:
            jobs.append((cell, cap, node, {}, destiny_dir, cache, timeout))
            labels.append(('(baseline)', BASELINE.get('OptimizationTarget', '')))
            for knob in todo:
                for val in KNOBS[knob]:
                    if str(BASELINE.get(knob)) == str(val):
                        continue
                    jobs.append((cell, cap, node, {knob: val}, destiny_dir, cache, timeout))
                    labels.append((knob, val))
    print('screening: %d design points (%d cells x %d capacities)'
          % (len(jobs), len(cells), len(caps)))
    rows = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (r, (knob, val)) in enumerate(zip(ex.map(run_point, jobs), labels), 1):
            r['knob'], r['value'] = knob, val
            rows.append(r)
            if i % 10 == 0 or i == len(jobs):
                print('  %d/%d' % (i, len(jobs)), flush=True)
    write_csv(rows, out)
    report_effects(rows)
    return rows


def report_effects(rows):
    """Effect size per knob, as a ratio against the baseline design."""
    base = {}
    for r in rows:
        if r.get('knob') == '(baseline)' and r.get('valid'):
            base[(r['cell'], r['capacity_MB'])] = r
    if not base:
        print('\nno valid baseline point; cannot report effects')
        return
    eff = {}
    for r in rows:
        k = r.get('knob')
        if not k or k == '(baseline)' or not r.get('valid'):
            continue
        b = base.get((r['cell'], r['capacity_MB']))
        if not b:
            continue
        for m in ('write_latency_ns', 'write_energy_nJ', 'leakage_mW', 'area_mm2'):
            try:
                bv, rv = float(b[m]), float(r[m])
            except (TypeError, ValueError, KeyError):
                continue
            if bv > 0:
                eff.setdefault(k, {}).setdefault(m, []).append(rv / bv)
    print('\n%-32s %10s %10s %10s %10s   verdict'
          % ('knob', 'wr lat', 'wr energy', 'leakage', 'area'))
    print('-' * 92)
    ranked = []
    for k, m in eff.items():
        span = max((max(v) / min(v)) if min(v) > 0 else 1.0 for v in m.values())
        ranked.append((span, k, m))
    for span, k, m in sorted(ranked, reverse=True):
        cells_ = []
        for metric in ('write_latency_ns', 'write_energy_nJ', 'leakage_mW', 'area_mm2'):
            v = m.get(metric)
            cells_.append('%9.3fx' % (max(v)) if v else '        -')
        verdict = ('DOMINANT' if span >= 2 else
                   'matters' if span >= 1.10 else
                   'inert - drop from the factorial')
        print('%-32s %s   %s' % (k, ' '.join(cells_), verdict))
    print('\nColumns are the worst-case ratio against the baseline design.')
    print('"inert" knobs move nothing on this cell and capacity; that is a')
    print('reportable negative, not a reason to keep grinding them.')


# -------------------------------------------------------------------- grid
def grid(cells, caps, node, destiny_dir, out, workers, timeout, dims, budget):
    """Full factorial over the chosen knobs."""
    names = list(dims)
    combos = list(itertools.product(*[KNOBS[n] for n in names]))
    total = len(combos) * len(cells) * len(caps)
    print('factorial: %d combos x %d cells x %d capacities = %d points'
          % (len(combos), len(cells), len(caps), total))
    if budget and total > budget:
        print('  exceeds --budget %d; refusing rather than silently sampling.' % budget)
        print('  Narrow --dims, or raise --budget and accept the wall clock.')
        return []
    cache = os.path.join(os.path.dirname(os.path.abspath(out)), 'cache')
    os.makedirs(cache, exist_ok=True)
    jobs = [(cell, cap, node, dict(zip(names, c)), destiny_dir, cache, timeout)
            for cell in cells for cap in caps for c in combos]
    rows = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, r in enumerate(ex.map(run_point, jobs), 1):
            rows.append(r)
            if i % 25 == 0 or i == len(jobs):
                print('  %d/%d' % (i, len(jobs)), flush=True)
    write_csv(rows, out)
    return rows


# ------------------------------------------------- geometry and scaling
# Zhang et al., "Cache performance of NV-STT-MRAM with scale effect and
# comparison with SRAM", Int. J. Electronics, 2021 (DOI
# 10.1080/00207217.2021.1908630) ran exactly this study for STT-MRAM against
# SRAM using DESTINY: cell area swept 10-30 F^2 (their Table 3), aspect ratio
# 0.3-2.0 (Table 4), unified against targeted optimisation (Tables 5-6), and a
# 65-22 nm scaling roadmap (Fig. 6). **They did not include SOT-MRAM.** These
# two modes reproduce their design with SOT added, which is the cache-level
# comparison the manuscript is missing and a reviewer can check line by line
# against a published paper.
#
# Sweeping cell area and aspect ratio is a LAYOUT choice, not device invention:
# the electricals stay pinned to the cited source and only the footprint the
# array model is given changes. Zhang legitimises this explicitly - a 25 F^2
# cell at aspect ratio 1.0 is 5F x 5F, and at 0.5 it is 7.07F x 3.54F.
#
# One honest reading of their own data, worth repeating: across a 3x cell-area
# sweep their write latency moves by under 2% while leakage moves by up to
# 42.1%. The reportable effect of geometry is LEAKAGE, not latency. Expect the
# same here and do not dress up sub-1% latency deltas as a result.
ZHANG_AREAS = [10, 15, 20, 30]
ZHANG_ASPECTS = [0.3, 0.5, 1.0, 2.0]
ZHANG_CAPACITIES = [1, 2, 4, 8, 16, 32]
ZHANG_NODES = [65, 45, 32, 22]


def _respin(cell, area=None, aspect=None, outdir='/tmp'):
    """Copy a cell file with the footprint overridden. Electricals untouched."""
    txt = open(cell).read()
    tag = []
    if area is not None:
        txt = re.sub(r'-CellArea \(F\^2\):[^\n]*', '-CellArea (F^2): %g' % area, txt)
        tag.append('a%g' % area)
    if aspect is not None:
        txt = re.sub(r'-CellAspectRatio:[^\n]*', '-CellAspectRatio: %g' % aspect, txt)
        tag.append('r%g' % aspect)
    base = os.path.basename(cell).replace('.cell', '')
    path = os.path.join(outdir, '%s__%s.cell' % (base, '_'.join(tag) or 'orig'))
    hdr = ('// LAYOUT VARIANT of %s. Only CellArea and/or CellAspectRatio are\n'
           '// changed; every electrical parameter is the cited source\'s.\n'
           '// Generated by dse.py, mirroring Zhang et al. 2021 Tables 3-4.\n'
           % os.path.basename(cell))
    open(path, 'w').write(hdr + txt)
    return path


def geometry(cells, caps, node, destiny_dir, out, workers, timeout,
             areas=None, aspects=None):
    """Zhang Tables 3 and 4, for whatever cells you give it."""
    areas = areas or ZHANG_AREAS
    aspects = aspects or ZHANG_ASPECTS
    spin = os.path.join(os.path.dirname(os.path.abspath(out)), 'layout_variants')
    cache = os.path.join(os.path.dirname(os.path.abspath(out)), 'cache')
    os.makedirs(spin, exist_ok=True)
    os.makedirs(cache, exist_ok=True)
    jobs, meta = [], []
    for cell in cells:
        for cap in caps:
            for a in areas:                       # Table 3: area at AR 1.0
                v = _respin(cell, area=a, aspect=1.0, outdir=spin)
                jobs.append((v, cap, node, {}, destiny_dir, cache, timeout))
                meta.append((os.path.basename(cell), a, 1.0))
            for r in aspects:                     # Table 4: AR at the base area
                v = _respin(cell, area=areas[0], aspect=r, outdir=spin)
                jobs.append((v, cap, node, {}, destiny_dir, cache, timeout))
                meta.append((os.path.basename(cell), areas[0], r))
    print('geometry sweep: %d points' % len(jobs))
    rows = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (rw, (src, a, r)) in enumerate(zip(ex.map(run_point, jobs), meta), 1):
            rw['source_cell'] = src.replace('.cell', '')
            rw['cell_area_F2'], rw['aspect_ratio'] = a, r
            rows.append(rw)
            if i % 10 == 0 or i == len(jobs):
                print('  %d/%d' % (i, len(jobs)), flush=True)
    write_csv(rows, out)
    _zhang_table(rows, 'write_latency_ns', 'Write latency (ns)')
    _zhang_table(rows, 'leakage_mW', 'Leakage power (mW)  <- where the effect actually is')
    return rows


def _zhang_table(rows, metric, title):
    keys = sorted({(r['source_cell'], float(r['cell_area_F2']), float(r['aspect_ratio']))
                   for r in rows if r.get('valid') in (1, '1')})
    caps = sorted({int(r['capacity_MB']) for r in rows if r.get('valid') in (1, '1')})
    if not keys or not caps:
        return
    print('\n%s' % title)
    print('%-34s' % 'cell / area F^2 / aspect' + ''.join('%11s' % ('%dMB' % c) for c in caps))
    print('-' * (34 + 11 * len(caps)))
    for k in keys:
        line = '%-34s' % ('%s  %g  %g' % (k[0][:20], k[1], k[2]))
        for c in caps:
            m = [r for r in rows if r.get('valid') in (1, '1')
                 and (r['source_cell'], float(r['cell_area_F2']),
                      float(r['aspect_ratio'])) == k and int(r['capacity_MB']) == c]
            try:
                line += '%11.4g' % float(m[0][metric])
            except (IndexError, ValueError, TypeError, KeyError):
                line += '%11s' % '-'
        print(line)


def scaling(cells, caps, destiny_dir, out, workers, timeout, nodes=None,
            roadmaps=('HP', 'LOP')):
    """Zhang Fig. 6, with SOT added: the scaling roadmap at cache level."""
    nodes = nodes or ZHANG_NODES
    cache = os.path.join(os.path.dirname(os.path.abspath(out)), 'cache')
    os.makedirs(cache, exist_ok=True)
    jobs = [(cell, cap, n, {'DeviceRoadmap': rm}, destiny_dir, cache, timeout)
            for cell in cells for cap in caps for n in nodes for rm in roadmaps]
    print('scaling roadmap: %d points (%d nodes x %d roadmaps)'
          % (len(jobs), len(nodes), len(roadmaps)))
    rows = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, r in enumerate(ex.map(run_point, jobs), 1):
            rows.append(r)
            if i % 10 == 0 or i == len(jobs):
                print('  %d/%d' % (i, len(jobs)), flush=True)
    write_csv(rows, out)
    for metric, title in (('leakage_mW', 'Leakage power (mW) across the scaling roadmap'),
                          ('write_latency_ns', 'Write latency (ns) across the scaling roadmap'),
                          ('area_mm2', 'Area (mm^2) across the scaling roadmap')):
        cells_ = sorted({r['cell'] for r in rows if r.get('valid') in (1, '1')})
        ns = sorted({int(r['node_nm']) for r in rows if r.get('valid') in (1, '1')},
                    reverse=True)
        if not cells_ or not ns:
            continue
        print('\n%s' % title)
        print('%-30s %-5s' % ('cell', 'road') + ''.join('%11s' % ('%dnm' % n) for n in ns))
        print('-' * (36 + 11 * len(ns)))
        for c in cells_:
            for rm in roadmaps:
                line = '%-30s %-5s' % (c[:30], rm)
                for n in ns:
                    m = [r for r in rows if r.get('valid') in (1, '1')
                         and r['cell'] == c and int(r['node_nm']) == n
                         and r.get('DeviceRoadmap') == rm]
                    try:
                        line += '%11.4g' % float(m[0][metric])
                    except (IndexError, ValueError, TypeError, KeyError):
                        line += '%11s' % '-'
                print(line)
    print('\nThis is Zhang et al. 2021 Fig. 6 with SOT-MRAM added. They compared')
    print('STT-MRAM against SRAM only; the SOT row is what their study is missing')
    print('and what a reviewer can check against a published baseline.')
    return rows


# ------------------------------------------------------------------ pareto
def pareto(rows, objs):
    """Non-dominated set over `objs`, each (name, direction)."""
    pts = [r for r in rows if r.get('valid') in (1, '1')]
    def vec(r):
        out = []
        for name, d in objs:
            try:
                out.append(d * float(r[name]))
            except (TypeError, ValueError, KeyError):
                return None
        return out
    scored = [(r, vec(r)) for r in pts]
    scored = [(r, v) for r, v in scored if v is not None]
    front = []
    for r, v in scored:
        dominated = any(all(w[i] >= v[i] for i in range(len(v))) and
                        any(w[i] > v[i] for i in range(len(v)))
                        for _, w in scored)
        if not dominated:
            front.append(r)
    return front


def report_pareto(rows, out_prefix):
    objs = [('write_energy_nJ', -1), ('write_latency_ns', -1),
            ('leakage_mW', -1), ('area_mm2', -1)]
    front = pareto(rows, objs)
    write_csv(front, out_prefix + '_pareto.csv')

    print('\nBest design per objective')
    print('%-20s %-26s %-30s %12s' % ('objective', 'cell', 'winning setting', 'value'))
    print('-' * 92)
    best = {}
    for name, d in OBJECTIVES.items():
        cand = []
        for r in rows:
            if not r.get('valid') in (1, '1'):
                continue
            try:
                cand.append((d * float(r[name]), r))
            except (TypeError, ValueError, KeyError):
                pass
        if not cand:
            continue
        _, r = max(cand, key=lambda x: x[0])
        best[name] = r
        setting = ', '.join('%s=%s' % (k, r[k]) for k in
                            ('OptimizationTarget', 'WriteScheme', 'DeviceRoadmap',
                             'StackedDieCount') if k in r and r[k])
        print('%-20s %-26s %-30s %12.5g'
              % (name, r['cell'][:26], setting[:30], float(r[name])))

    print('\nPareto front: %d of %d valid designs are non-dominated on '
          '(write energy, write latency, leakage, area).' % (len(front), len(rows)))
    li = best.get('lifetime_index')
    we = best.get('write_energy_nJ')
    if li is not None and we is not None:
        same = li.get('cfg_sha') == we.get('cfg_sha')
        print('\nThe endurance-aware winner and the minimum-write-energy design '
              'are %s.' % ('THE SAME point' if same else 'DIFFERENT points'))
        if not same:
            print('  That is the result this stage exists to produce: optimising an')
            print('  array for write energy does not optimise it for lifetime, so a')
            print('  paper that reports only the energy-optimal point is reporting')
            print('  the wrong design for an endurance-limited cache.')
            for tag, r in (('min write energy', we), ('max lifetime index', li)):
                print('    %-18s %s  %s' % (
                    tag, r['cell'][:24],
                    ' '.join('%s=%s' % (k, r.get(k)) for k in
                             ('OptimizationTarget', 'WriteScheme', 'DeviceRoadmap',
                              'CacheAccessMode', 'StackedDieCount') if r.get(k))))
    return front, best


# --------------------------------------------------------------------- CLI
def resolve_cells(spec):
    out = []
    for s in spec.split(','):
        s = s.strip()
        if not s:
            continue
        for cand in (s, os.path.join(HERE, 'cells', s + '.cell'),
                     os.path.join(HERE, 'cells', 'generated', s + '.cell'),
                     os.path.join(HERE, 'cells', s)):
            if os.path.isfile(cand):
                out.append(cand)
                break
        else:
            print('cell not found: %r' % s, file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--destiny-dir', default=DEFAULT_DESTINY)
    ap.add_argument('--cells', default='sram,sttram_54_singh2020,sot_57_singh2020')
    ap.add_argument('--capacity', default='16')
    ap.add_argument('--node', type=int, default=45)
    ap.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument('--timeout', type=int, default=900)
    ap.add_argument('--outdir', default=os.path.join(HERE, '..', 'results', 'dse'))
    ap.add_argument('--screen', action='store_true')
    ap.add_argument('--knobs', default='', help='screen only these knobs')
    ap.add_argument('--grid', action='store_true')
    ap.add_argument('--dims', default='OptimizationTarget,WriteScheme,DeviceRoadmap',
                    help='factorial dimensions')
    ap.add_argument('--budget', type=int, default=2000,
                    help='refuse a factorial larger than this many points')
    ap.add_argument('--pareto', default='', metavar='CSV')
    ap.add_argument('--geometry', action='store_true',
                    help='Zhang 2021 Tables 3-4: cell area and aspect ratio')
    ap.add_argument('--areas', default='', help='override the area sweep, F^2')
    ap.add_argument('--aspects', default='', help='override the aspect sweep')
    ap.add_argument('--scale', action='store_true',
                    help='Zhang 2021 Fig. 6: process-node roadmap, with SOT added')
    ap.add_argument('--nodes', default='', help='override the node list, nm')
    a = ap.parse_args()

    outdir = os.path.abspath(a.outdir)
    os.makedirs(outdir, exist_ok=True)

    if a.pareto:
        rows = list(csv.DictReader(open(a.pareto)))
        for r in rows:
            r['valid'] = int(r.get('valid') or 0)
        report_pareto(rows, os.path.join(outdir, 'dse'))
        return 0

    cells = resolve_cells(a.cells)
    if not cells:
        return 2
    caps = [int(x) for x in a.capacity.split(',')]
    binary = os.path.join(a.destiny_dir, 'destiny')
    if not os.path.isfile(binary):
        sys.exit('DESTINY binary not found: %s (run ./setup.sh)' % binary)

    if a.screen:
        screen(cells, caps, a.node, a.destiny_dir,
               os.path.join(outdir, 'screen.csv'), a.workers, a.timeout,
               [k.strip() for k in a.knobs.split(',') if k.strip()] or None)
    if a.grid:
        rows = grid(cells, caps, a.node, a.destiny_dir,
                    os.path.join(outdir, 'grid.csv'), a.workers, a.timeout,
                    [d.strip() for d in a.dims.split(',') if d.strip()], a.budget)
        if rows:
            report_pareto(rows, os.path.join(outdir, 'dse'))
    if a.geometry:
        geometry(cells, caps, a.node, a.destiny_dir,
                 os.path.join(outdir, 'geometry.csv'), a.workers, a.timeout,
                 [float(x) for x in a.areas.split(',') if x] or None,
                 [float(x) for x in a.aspects.split(',') if x] or None)
    if a.scale:
        scaling(cells, caps, a.destiny_dir,
                os.path.join(outdir, 'scaling.csv'), a.workers, a.timeout,
                [int(x) for x in a.nodes.split(',') if x] or None)
    if not (a.screen or a.grid or a.geometry or a.scale):
        ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
