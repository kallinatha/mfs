#!/usr/bin/env python3
"""Independent re-derivation of the reviewer-facing numbers.

Deliberately written against the raw `csv` and `json` modules and sharing no
helper with stage4_analysis/pareto.py: the point is to catch an error in the
analysis path, which a check that imports that path cannot do.

Expected values are the ones recorded in docs/REVIEWER_DEFENCE.md.  A MISMATCH
is not necessarily a bug in the pipeline -- after a re-run with the corrected
guard columns some values legitimately move -- but every mismatch must be
explained before the number is quoted.

Usage:
    python3 scripts/verify_headline_numbers.py --results results/suscom_ten_mixes
"""
import argparse, csv, glob, json, math, os, statistics, sys


# Directories that hold fixtures, samples or archived copies rather than the
# run being verified.  A recursive glob without this filter silently picked a
# results/sample/statistics.json in testing and reported three mismatches that
# were an artefact of the search, not of the pipeline -- exactly the failure
# this script exists to catch, so it must not commit it itself.
IGNORE_DIRS = ('sample', 'samples', 'test', 'tests', 'fixture', 'fixtures',
               'example', 'examples', 'bak', 'backup', 'old', 'archive')


def find(root, name):
    """Locate a result file under the results tree, shallowest match first.

    The pipeline puts hycsim outputs in <root>/hycsim, ChampSim outputs in
    <root>/champsim and the cross-stack joins at the top level; globbing
    avoids hardcoding a layout that has already changed once.  Depth ordering
    means a file sitting where the pipeline actually writes it always beats a
    copy buried further down.
    """
    hits = []
    for p in glob.glob(os.path.join(root, '**', name), recursive=True):
        rel = os.path.relpath(p, root)
        parts = [x.lower() for x in rel.split(os.sep)[:-1]]
        if any(x in IGNORE_DIRS for x in parts):
            continue
        hits.append((len(parts), p))
    if not hits:
        return None
    hits.sort()
    if len(hits) > 1:
        print('note: %d copies of %s found; using the shallowest, %s'
              % (len(hits), name, hits[0][1]), file=sys.stderr)
    return hits[0][1]


def rows(root, name):
    p = find(root, name)
    if not p:
        return None
    with open(p, newline='') as fh:
        return list(csv.DictReader(fh))


def F(r, k, d=0.0):
    v = r.get(k, '')
    try:
        return float(v) if v not in ('', None) else d
    except (TypeError, ValueError):
        return d


def GM(xs):
    xs = [v for v in xs if v > -0.999]
    return math.expm1(sum(math.log1p(v) for v in xs) / len(xs)) if xs else float('nan')


RESULTS = []


def med(xs):
    """Median, or None for an empty sequence so chk() records a SKIP."""
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


def chk(label, got, exp, tol=0.02):
    if got is None or (isinstance(got, float) and got != got):
        RESULTS.append((label, None, exp, 'SKIP'))
        print('%-58s %14s  vs %-12.5f %s' % (label, 'n/a', exp, 'SKIP (file absent)'))
        return
    good = abs(got - exp) <= tol * max(abs(exp), 1e-9) if exp else abs(got) <= tol
    RESULTS.append((label, got, exp, 'OK' if good else 'MISMATCH'))
    print('%-58s %14.5f  vs %-12.5f %s' % (label, got, exp, 'OK' if good else '** MISMATCH **'))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--results', default='results/suscom_ten_mixes',
                    help='results directory root')
    a = ap.parse_args()
    D = a.results
    if not os.path.isdir(D):
        raise SystemExit('no such results directory: %s' % D)
    print('verifying against %s\n' % os.path.abspath(D))
    # --- 1. corrected LOO guard for psc / psc-balance
    life=rows(D, 'lifetime_all_mixes_loo.csv')
    if not life:
        print('lifetime_all_mixes_loo.csv not found under %s -- has the hycsim '
              'stage run? Skipping the guard checks.' % D)
        life=[]
    if not life:
        base={}
    else:
        base={(r['mix'],r['arena']):(F(r,'hit_rate'),F(r,'mu')) for r in life if r['scheme']=='unmanaged'}
    for sch,exp_n,exp_gm in [('psc',4,13.82),('psc-balance',4,17.18),('psc-vro',3,5.97)]:
        g=[r for r in life if r['scheme']==sch and r['arena']=='hybrid'
           and F(r,'hit_rate')>=base[(r['mix'],r['arena'])][0]-0.005
           and F(r,'mu')>=0.25*base[(r['mix'],r['arena'])][1]]
        chk('LOO %s guard-passing mixes'%sch,float(len(g)) if life else None,exp_n,0)
        chk('LOO %s guarded geomean RLI'%sch,GM([F(r,'rli_direct') for r in g]) if g else None,exp_gm)

    # --- 2. ChampSim: psc fire rate and NVM write inflation
    cs=rows(D, 'champsim_results.csv') or []
    psc={r['trace']:r for r in cs if r['policy']=='psc'}
    lru={r['trace']:r for r in cs if r['policy']=='lru'}
    sft={r['trace']:r for r in cs if r['policy']=='psc_soft'}
    fr=(F(r,'steered')/F(r,'allocations',1) for r in psc.values())
    chk('psc median fire rate (per allocation)',med(fr),2.241e-05,0.05)
    ratio=[F(psc[t],'nvm_writes')/F(lru[t],'nvm_writes',1) for t in psc if t in lru]
    chk('psc NVM writes / LRU (median)',med(ratio),1.322)
    chk('traces where psc INCREASES NVM writes',float(sum(1 for x in ratio if x>1)) if ratio else None,48,0)
    sfr=(F(r,'steered')/F(r,'allocations',1) for r in sft.values())
    chk('psc_soft median fire rate',med(sfr),0.450,0.05)
    sr=[F(sft[t],'nvm_writes')/F(lru[t],'nvm_writes',1) for t in sft if t in lru]
    chk('psc_soft NVM writes / LRU (median)',med(sr),0.750)
    lpp=lambda r:(1+F(r,'rli_direct'))*(1+F(r,'ipc_delta'))-1
    chk('psc_soft geomean LPP',GM([lpp(r) for r in sft.values()]) if sft else None,0.2516)
    chk('psc geomean LPP',GM([lpp(r) for r in psc.values()]) if psc else None,-0.1604)

    # --- 3. proxy falsification
    _sp=find(D,'statistics.json')
    st=json.load(open(_sp))['proxy_validation'] if _sp else {}
    chk('proxy median abs pct error',st.get('median_abs_pct_error'),91.6927,1e-4)
    chk('proxy pairwise reversals',st.get('n_reversals'),11367,0)
    chk('proxy reversal rate',st.get('reversal_rate'),0.20939,1e-3)

    # --- 4. cross-technology: write energy spread and psc-balance NVM share
    sot,pcm=rows(D, 'cross_stack_results.csv'),rows(D, 'cross_stack_results_pcm.csv')
    if not sot or not pcm:
        print('cross-stack CSVs not found under %s; skipping technology checks' % D)
        sot=pcm=[]
    chk('PCM/SOT write energy ratio',(F(pcm[0],'destiny_nvm_write_energy_nJ')/F(sot[0],'destiny_nvm_write_energy_nJ')) if sot and pcm else None,125.1,0.02)
    for sch,exp in [('psc-balance',0.212),('lru-hybrid',0.772)]:
        rs=[r for r in sot if r['scheme']==sch and r['arena']=='hybrid']
        nv=sum(F(r,'nvm_writes') for r in rs); sr_=sum(F(r,'sram_writes') for r in rs)
        chk('%s NVM write share (SOT)'%sch,(nv/(nv+sr_)) if (nv+sr_) else None,exp)

    # --- 5. frontier anchor points
    tn=rows(D, 'tuning_all_mixes.csv') or []
    tb={(r['mix'],r['arena']):(F(r,'hit_rate'),F(r,'mu')) for r in tn if r['scheme']=='unmanaged'}
    agg={}
    for r in tn:
        if r['arena']!='hybrid': continue
        b=tb.get((r['mix'],r['arena']));
        if not b: continue
        k=(r['scheme'],r['params']); a=agg.setdefault(k,[[],[],[]])
        a[0].append(F(r,'hit_rate')-b[0]); a[1].append(F(r,'mu')/b[1] if b[1] else 0); a[2].append(F(r,'rli_direct'))
    pts=[(statistics.mean(v[0]),statistics.mean(v[2]),k[0]) for k,v in agg.items() if statistics.mean(v[1])>=0.25]
    for tol,exp in [(0.005,0.25),(0.021,4.36),(0.10,25.62)]:
        c=[p for p in pts if p[0]>=-tol]
        chk('frontier: best RLI at hit budget %.3f'%tol,max(c,key=lambda p:p[1])[1] if c else None,exp)
    bad = [r for r in RESULTS if r[3] == 'MISMATCH']
    skipped = [r for r in RESULTS if r[3] == 'SKIP']
    print('\n%d/%d checks passed, %d mismatched, %d skipped'
          % (sum(1 for r in RESULTS if r[3] == 'OK'), len(RESULTS), len(bad), len(skipped)))
    if bad:
        print('\nEvery mismatch must be explained before the number is quoted:')
        for label, got, exp, _ in bad:
            print('  %-56s got %.5f, documented %.5f' % (label, got, exp))
    raise SystemExit(1 if bad else 0)


if __name__ == '__main__':
    main()
