#!/usr/bin/env python3
"""DESTINY v2 cross-technology LLC sweep: SRAM / STT-MRAM / SOT-MRAM / PCM."""
import argparse, hashlib, os, re, csv, subprocess, itertools, time
from concurrent.futures import ThreadPoolExecutor

DEST = os.environ.get('DESTINY_DIR', '')
BASE = os.path.dirname(os.path.abspath(__file__))
OUT  = os.path.join(BASE, 'out')
CSVF = os.path.join(BASE, 'results.csv')

CFG_T = """-DesignTarget: cache
-CacheAccessMode: {access}
-Associativity (for cache only): {assoc}
-ProcessNode: {node}
-Capacity (MB): {cap}
-WordWidth (bit): 512
-DeviceRoadmap: {road}
-LocalWireType: LocalAggressive
-LocalWireRepeaterType: RepeatedNone
-LocalWireUseLowSwing: No
-GlobalWireType: GlobalAggressive
-GlobalWireRepeaterType: RepeatedNone
-GlobalWireUseLowSwing: No
-Routing: H-tree
-InternalSensing: true
-MemoryCellInputFile: {cell}
-Temperature (K): 350
-EnablePruning: Yes
-OptimizationTarget: {opt}
-BufferDesignOptimization: latency
-StackedDieCount: 1
"""

# Seven points, not five. Three of them are hand-set geometries carrying the
# DESTINY sample electricals (STT-6, SOT-12/1, SOT-24/2); two are transcribed
# from a published NVSim parameter set (Singh et al., SPIN 10(4):2050027, 2020,
# Table 2, 22 nm) and one is the hand-corrected 40 F^2 STT point. Run all of
# them. The Singh cells are the ones a reviewer can check, and they are also the
# ones least favourable to SOT on area - which is exactly why they must be in
# the table rather than left out.
CELLS = {
    'SRAM':          'cells/sram.cell',
    'STT-MRAM':      'cells/sttram.cell',            # DESTINY sample, 6 F^2 (infeasible)
    'STT-40':        'cells/sttram_40.cell',         # hand-corrected geometry
    'STT-54-Singh':  'cells/sttram_54_singh2020.cell',   # published, citable
    'SOT-12/1':      'cells/sot_12_1.cell',          # optimistic geometry
    'SOT-24/2':      'cells/sot_24_2.cell',          # relaxed geometry
    'SOT-57-Singh':  'cells/sot_57_singh2020.cell',  # published, citable
    'PCM':           'cells/pcram.cell',
}


def discover_generated(base=BASE):
    """Pick up every cell emitted by cells/from_paper.py.

    Kept separate from CELLS so the hand-written and the paper-derived sets stay
    distinguishable in results.csv: a `gen:` prefix marks a cell whose every
    value carries a citation in its own header.
    """
    import glob
    out = {}
    for path in sorted(glob.glob(os.path.join(base, 'cells', 'generated', '*.cell'))):
        out['gen:' + os.path.basename(path)[:-5]] = os.path.relpath(path, base)
    return out


PATS = {
    'area_mm2':        r'Total Area = ([\d.]+)mm\^2',
    'hit_latency_ns':  r'Cache Hit Latency\s*=\s*([\d.eE+-]+)ns',
    'miss_latency_ns': r'Cache Miss Latency\s*=\s*([\d.eE+-]+)ns',
    'write_latency_ns':r'Cache Write Latency\s*=\s*([\d.eE+-]+)ns',
    'hit_energy_nJ':   r'Cache Hit Dynamic Energy\s*=\s*([\d.eE+-]+)nJ',
    'miss_energy_nJ':  r'Cache Miss Dynamic Energy\s*=\s*([\d.eE+-]+)nJ',
    'write_energy_nJ': r'Cache Write Dynamic Energy\s*=\s*([\d.eE+-]+)nJ',
    'leakage_mW':      r'Cache Total Leakage Power\s*=\s*([\d.eE+-]+)mW',
}

FIELDS = ['tech','capacity_MB','node_nm','roadmap','opt_target','assoc','access',
          'status','runtime_s'] + list(PATS.keys())


def parse(txt):
    d = {}
    for k, p in PATS.items():
        m = re.search(p, txt)
        d[k] = float(m.group(1)) if m else ''
    return d


def one(job):
    tech, cap, node, road, opt = job
    tag = '%s_%sMB_%snm_%s_%s' % (tech.replace('/', '-'), cap, node, road, opt)
    cfg = CFG_T.format(access='Sequential', assoc=8, node=node, cap=cap,
                       road=road, cell=os.path.join(BASE, CELLS[tech]), opt=opt)
    cfgp = os.path.join(BASE, 'cfgs', tag + '.cfg')
    open(cfgp, 'w').write(cfg)
    logp = os.path.join(OUT, tag + '.log')
    hashp = logp + '.cfg.sha256'
    cfg_hash = hashlib.sha256(cfg.encode('utf-8')).hexdigest()
    cached_hash = open(hashp).read().strip() if os.path.exists(hashp) else ''
    if os.path.exists(logp) and cached_hash == cfg_hash:
        prev = open(logp).read()
        if 'CACHE DESIGN -- SUMMARY' in prev:
            row = dict(tech=tech, capacity_MB=cap, node_nm=node, roadmap=road,
                       opt_target=opt, assoc=8, access='Sequential',
                       runtime_s=0, status='cached')
            row.update(parse(prev))
            return row
    t0 = time.time()
    try:
        r = subprocess.run([os.path.join(DEST, 'destiny'), cfgp], cwd=DEST,
                           capture_output=True, text=True, timeout=3600)
        txt = r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        txt = 'TIMEOUT'
    dt = round(time.time() - t0, 1)
    open(os.path.join(OUT, tag + '.log'), 'w').write(txt)
    open(hashp, 'w').write(cfg_hash + '\n')
    row = dict(tech=tech, capacity_MB=cap, node_nm=node, roadmap=road,
               opt_target=opt, assoc=8, access='Sequential', runtime_s=dt)
    if 'CACHE DESIGN -- SUMMARY' in txt:
        row['status'] = 'ok'
        row.update(parse(txt))
    elif 'TIMEOUT' in txt:
        row['status'] = 'timeout'
    else:
        m = re.findall(r'(?i)(?:no valid|invalid|error|not found|cannot)[^\n]*', txt)
        row['status'] = 'fail:' + (m[0] if m else 'unknown')
    for k in PATS:
        row.setdefault(k, '')
    return row


def main():
    global DEST, OUT, CSVF, CELLS
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--destiny-dir', default=DEST, required=not bool(DEST))
    ap.add_argument('--out', default=CSVF, help='result CSV path')
    ap.add_argument('--log-dir', default=OUT)
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--preset', choices=('smoke', 'paper'), default='paper')
    ap.add_argument('--technologies', default='',
                    help='comma-separated subset; default is every entry in '
                         'CELLS (plus generated cells when --include-generated)')
    ap.add_argument('--include-generated', action='store_true',
                    help='also sweep every cell emitted by cells/from_paper.py, '
                         'tagged gen:<id> in the CSV')
    args = ap.parse_args()
    if args.include_generated:
        CELLS = dict(CELLS, **discover_generated())
    DEST, OUT, CSVF = map(os.path.abspath, (args.destiny_dir, args.log_dir, args.out))
    selected = ([x.strip() for x in args.technologies.split(',') if x.strip()]
                or list(CELLS))
    unknown = set(selected) - set(CELLS)
    if unknown:
        raise SystemExit('unknown technologies: %s' % ', '.join(sorted(unknown)))
    CELLS = {k: CELLS[k] for k in selected}
    binary = os.path.join(DEST, 'destiny')
    if not os.path.isfile(binary):
        raise SystemExit('DESTINY binary not found: %s (run make first)' % binary)
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(os.path.join(BASE, 'cfgs'), exist_ok=True)
    os.makedirs(os.path.dirname(CSVF), exist_ok=True)
    jobs = []
    # Ordered by decreasing value-per-minute so that a truncated run still
    # yields a complete cross-technology comparison.
    caps = [1] if args.preset == 'smoke' else [1, 2, 4, 8]
    roads = ['HP'] if args.preset == 'smoke' else ['HP', 'LOP']
    # A1: every technology at the capacities that carry the argument, 45 nm
    for cap, road, tech in itertools.product(caps, roads, CELLS):
        jobs.append((tech, cap, 45, road, 'WriteEDP'))
    if args.preset == 'paper':
        # A2: technology-node roadmap at 4 MB
        for node, tech in itertools.product([22, 32, 65], CELLS):
            jobs.append((tech, 4, node, 'HP', 'WriteEDP'))
        # A3: read-latency-optimised cross-check
        for cap, tech in itertools.product([1, 8], CELLS):
            jobs.append((tech, cap, 45, 'HP', 'ReadLatency'))
        # A4: large capacities last (slowest, least load-bearing)
        for cap, road, tech in itertools.product([16, 32], ['HP', 'LOP'], CELLS):
            jobs.append((tech, cap, 45, road, 'WriteEDP'))

    print('total jobs:', len(jobs), flush=True)
    f = open(CSVF, 'w', newline='')
    w = csv.DictWriter(f, fieldnames=FIELDS)
    w.writeheader(); f.flush()
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for row in ex.map(one, jobs):
            w.writerow(row); f.flush()
            done += 1
            print('[%d/%d] %s %sMB %snm %s %s -> %s (%ss)' % (
                done, len(jobs), row['tech'], row['capacity_MB'], row['node_nm'],
                row['roadmap'], row['opt_target'], row['status'], row['runtime_s']),
                flush=True)
    f.close()
    print('DONE', flush=True)


if __name__ == '__main__':
    main()
