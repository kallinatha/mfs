#!/usr/bin/env python3
"""Stage 5: cycle-level performance AND physical wear from the same simulation.

The manuscript takes IPC from one place and wear from another.  That is what
makes its overhead arithmetic irreconcilable: 0.31 reorder events per million
accesses at 4 cycles each is 1.24 controller cycles per million, which cannot
produce a 0.11% IPC change unless the event stalls a much longer path.  Running
both in ChampSim removes the gap - every number below comes from one run.

The `psc_family` ChampSim replacement module implements every policy under
comparison and is selected at run time through the environment, so a policy
change never means a rebuild and never means a different binary.

Outputs one CSV row per (policy, trace, config) with IPC, MPKI, LLC hit rates,
and the NVM wear distribution (mu, max, p99, p99.9, InterV, IntraV, CVaR).
"""
import argparse, csv, itertools, os, re, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

# The fallback must be a path inside THIS package. It used to be
# '/home/claude/ChampSim' - an absolute path from the machine the module was
# written on, which exists nowhere else. Anyone invoking this script directly
# rather than through run_all.sh (which does export CHAMPSIM_DIR) got a
# "champsim not found" against a path they had never heard of.
CHAMPSIM = os.environ.get(
    'CHAMPSIM_DIR',
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 'tools', 'ChampSim'))

# Every policy the module supports, in the order read_env() recognises them.
# `psc_soft` and `psc_soft_rank` were missing here even though the module has
# always implemented them, so the two soft-partition variants were never being
# run.  `elba`, `sequoia`, `startgap` and `equalchance` are the stage-3 policies
# ported to ChampSim; see docs/POLICY_COVERAGE.md for what each one does and
# does not reproduce under ChampSim's non-exclusive LLC protocol.
#
# `swwr`, `dwwr`, `wear_swap`, `full` and `psc_vro` close the remaining gaps
# against the stage-3 replay.  Two warnings about reading their rows:
#   * `swwr`/`dwwr` clock their interval in PROCESSOR CYCLES at the thesis
#     default I = 2,000,000.  A run shorter than that crosses no interval
#     boundary, leaves the whole cache unrestricted, and is therefore LRU under
#     another name.  Check `wwr_intervals` before believing any swwr/dwwr row;
#     the module also prints a loud warning when it is 0.  At --sim 10000000 on
#     619.lbm the run is 22.5M cycles, i.e. 11 intervals; at --sim 1000000 it is
#     1.8M cycles and swwr/dwwr are LRU exactly.  The default --sim of 10M is
#     therefore the minimum honest setting for these two rows.
#   * `full` is a composition (psc_pred + vro_rank + wear_swap) and each channel
#     can be switched off with PSC_FULL_PRED / PSC_FULL_RANK / PSC_FULL_SWAP.
#     With SWAP=0 it must equal `psc_vro_rank` bit for bit; with SWAP=0 RANK=0
#     it must equal `psc_pred`.  Those two equalities are the test that the
#     composition is real and not a re-implementation.
POLICIES = ['lru', 'psc', 'vro', 'psc_vro', 'vro_rank', 'psc_pred', 'psc_vro_rank',
            'ayush', 'dwawr', 'i2wap', 'tailguard', 'psc_soft', 'psc_soft_rank',
            'elba', 'sequoia', 'startgap', 'equalchance',
            'swwr', 'dwwr', 'wear_swap', 'full', 'psc_balance']

# Fields scraped from ChampSim's own output plus the module's final stats.
PATTERNS = {
    'pb_diversions':      r'pb_diversions (\d+)',
    'pb_blocked_cap':     r'pb_blocked_cap (\d+)',
    'pb_blocked_floor':   r'pb_blocked_floor (\d+)',
    'ipc':            r'CPU 0 cumulative IPC:\s*([\d.]+)',
    'instructions':   r'instructions:\s*(\d+)',
    'cycles':         r'cycles:\s*(\d+)',
    'llc_access':     r'LLC TOTAL\s+ACCESS:\s*(\d+)',
    'llc_hit':        r'LLC TOTAL\s+ACCESS:\s*\d+\s+HIT:\s*(\d+)',
    'llc_miss':       r'LLC TOTAL\s+ACCESS:\s*\d+\s+HIT:\s*\d+\s+MISS:\s*(\d+)',
    'llc_miss_lat':   r'LLC AVERAGE MISS LATENCY:\s*([\d.]+)',
    'nvm_frames':     r'nvm_frames (\d+)',
    'nvm_writes':     r'nvm_writes (\d+)',
    'sram_writes':    r'sram_writes (\d+)',
    'induced_writes': r'induced_writes (\d+)',
    'mu':             r'\nmu ([\d.eE+-]+)',
    'max_writes':     r'max_writes ([\d.eE+-]+)',
    'p99_writes':     r'p99_writes ([\d.eE+-]+)',
    'p999_writes':    r'p999_writes ([\d.eE+-]+)',
    'interv':         r'interV ([\d.eE+-]+)',
    'intrav':         r'intraV ([\d.eE+-]+)',
    'max_damage':     r'max_damage ([\d.eE+-]+)',
    'cvar99':         r'cvar99 ([\d.eE+-]+)',
    'cvar999':        r'cvar999 ([\d.eE+-]+)',
    'steered':        r'steered (\d+)',
    'allocations':    r'allocations (\d+)',
    'vro_rotations':  r'vro_rotations (\d+)',
    'rank_interventions': r'rank_interventions (\d+)',
    'ayush_migrations':   r'ayush_migrations (\d+)',
    'dwawr_redirects':    r'dwawr_redirects (\d+)',
    'polf_flushes':       r'polf_flushes (\d+)',
    'ss_swaps':           r'ss_swaps (\d+)',
    'tg_actions':         r'tg_actions (\d+)',
    'displacements':      r'displacements (\d+)',
    # remapped_writes is the Start-Gap / G-OAS liveness instrument: if it is 0
    # while sg_movements or seq_associations is non-zero, the set remap has been
    # disconnected and only its write tax remains.
    'remapped_writes':    r'remapped_writes (\d+)',
    'elba_promotions':      r'elba_promotions (\d+)',
    'elba_frames_released': r'elba_frames_released (\d+)',
    'elba_absorbed':        r'elba_absorbed (\d+)',
    'elba_buf_hits':        r'elba_buf_hits (\d+)',
    'elba_buf_evictions':   r'elba_buf_evictions (\d+)',
    'elba_buf_writebacks':  r'elba_buf_writebacks (\d+)',
    'elba_early_evictions': r'elba_early_evictions (\d+)',
    'elba_boundary_changes': r'elba_boundary_changes (\d+)',
    'elba_cap':             r'elba_cap (\d+)',
    'seq_write_hits':       r'seq_write_hits (\d+)',
    'seq_write_hits_nvm':   r'seq_write_hits_nvm (\d+)',
    'seq_write_hits_sram':  r'seq_write_hits_sram (\d+)',
    'seq_ssc_saturations':  r'seq_ssc_saturations (\d+)',
    'seq_associations':     r'seq_associations (\d+)',
    'seq_completions':      r'seq_completions (\d+)',
    'seq_aborts':           r'seq_aborts (\d+)',
    'seq_abort_invalidations': r'seq_abort_invalidations (\d+)',
    'seq_abort_writebacks': r'seq_abort_writebacks (\d+)',
    'seq_line_moves':       r'seq_line_moves (\d+)',
    'seq_second_searches_elided': r'seq_second_searches_elided (\d+)',
    'seq_wad_arms':         r'seq_wad_arms (\d+)',
    'seq_wad_displacements': r'seq_wad_displacements (\d+)',
    'seq_wad_victim_evictions': r'seq_wad_victim_evictions (\d+)',
    'seq_wad_writebacks':   r'seq_wad_writebacks (\d+)',
    'sg_movements':         r'sg_movements (\d+)',
    'sg_rotations':         r'sg_rotations (\d+)',
    'sg_frames_moved':      r'sg_frames_moved (\d+)',
    'ec_i_shifts':          r'ec_i_shifts (\d+)',
    'ec_c_shifts':          r'ec_c_shifts (\d+)',
    'ec_hit_shifts':        r'ec_hit_shifts (\d+)',
    'ec_no_candidate':      r'ec_no_candidate (\d+)',
    # SWWR / DWWR.  wwr_intervals is the liveness instrument: 0 means the run
    # never reached the first interval boundary, so no window was ever
    # restricted and the row is LRU.  wwr_degenerate is the replay's collapse
    # mode (DWWR's argmax taken over an all-zero counter vector); it should be
    # ~0 under ChampSim, where write hits are plentiful, and that contrast with
    # the replay is a result rather than a defect.
    'wwr_intervals':        r'wwr_intervals (\d+)',
    'wwr_clock_ticks':      r'wwr_clock_ticks (\d+)',
    'wwr_cycles':           r'wwr_cycles (\d+)',
    'wwr_degenerate':       r'wwr_degenerate (\d+)',
    'wwr_counter_saturations': r'wwr_counter_saturations (\d+)',
    'wwr_write_hits':       r'wwr_write_hits (\d+)',
    'wwr_write_hits_restricted': r'wwr_write_hits_restricted (\d+)',
    'wwr_redirects':        r'wwr_redirects (\d+)',
    'wwr_redirect_to_invalid': r'wwr_redirect_to_invalid (\d+)',
    'wwr_redirect_evictions': r'wwr_redirect_evictions (\d+)',
    'wwr_redirect_writebacks': r'wwr_redirect_writebacks (\d+)',
    'wwr_redirect_to_sram': r'wwr_redirect_to_sram (\d+)',
    'wwr_no_dest':          r'wwr_no_dest (\d+)',
    'wwr_alloc_deflected':  r'wwr_alloc_deflected (\d+)',
    'wwr_alloc_to_sram':    r'wwr_alloc_to_sram (\d+)',
    # R3 says a restricted way takes no write at all; handle_fill can violate
    # that without ever consulting the module, so the violation rate is
    # measured rather than assumed.
    'wwr_fill_into_wrw':    r'wwr_fill_into_wrw (\d+)',
    'wwr_state_bits':       r'wwr_state_bits (\d+)',
    # WearSwap, also the inter-set channel of `full`.
    'ws_swaps':             r'ws_swaps (\d+)',
    'ws_migration_writes':  r'ws_migration_writes (\d+)',
    'ws_no_candidate':      r'ws_no_candidate (\d+)',
}


def parse(txt):
    out = {}
    for k, p in PATTERNS.items():
        m = re.search(p, txt)
        out[k] = float(m.group(1)) if m else ''
    return out


def one(job):
    policy, trace, warm, sim, nvm_ways, extra, outdir = job
    tname = os.path.basename(trace).split('.champsimtrace')[0]
    tag = '%s__%s__nvm%d' % (policy, tname, nvm_ways)
    logp = os.path.join(outdir, tag + '.log')
    if os.path.exists(logp) and 'end psc_family' in open(logp, errors='ignore').read():
        txt = open(logp, errors='ignore').read()
        r = dict(policy=policy, trace=tname, nvm_ways=nvm_ways, status='cached', runtime_s=0)
        r.update(parse(txt))
        return r

    env = dict(os.environ)
    env.update({'PSC_POLICY': policy, 'PSC_NVM_WAYS': str(nvm_ways),
                'PSC_STATS': os.path.join(outdir, tag + '_wear.csv')})
    env.update({k: str(v) for k, v in extra.items()})
    cmd = [os.path.join(CHAMPSIM, 'bin', 'champsim'),
           '--warmup-instructions', str(warm),
           '--simulation-instructions', str(sim), trace]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=CHAMPSIM, env=env, capture_output=True, text=True, timeout=36000)
        txt = p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        txt = 'TIMEOUT'
    dt = round(time.time() - t0, 1)
    open(logp, 'w').write(txt)
    r = dict(policy=policy, trace=tname, nvm_ways=nvm_ways, runtime_s=dt,
             status='ok' if 'end psc_family' in txt else 'fail')
    r.update(parse(txt))
    print('[%s] %s ipc=%s max=%s (%.0fs)' % (r['status'], tag, r.get('ipc'), r.get('max_writes'), dt), flush=True)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--traces', required=True, help='comma-separated trace paths or a directory')
    ap.add_argument('--policies', default=','.join(POLICIES))
    ap.add_argument('--warmup', type=int, default=2_000_000)
    ap.add_argument('--sim', type=int, default=10_000_000)
    ap.add_argument('--nvm-ways', default='12')
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--outdir', default='results/champsim')
    ap.add_argument('--thermal', type=int, default=0)
    ap.add_argument('--endurance-cv', type=float, default=0.0)
    ap.add_argument('--out', default='')
    a = ap.parse_args()

    if os.path.isdir(a.traces):
        # Recursive walk, both compressions, DPC/ML numeric-suffix style
        # ('name.champsimtrace-023.gz') included: mirror mfs.py's champsim()
        # matcher exactly so a standalone screening pass (this branch is the
        # only place a raw directory is ever passed - mfs.py always resolves
        # to an explicit comma list first) finds the same files the full
        # pipeline would. The old `f.endswith('.xz')`, non-recursive listdir
        # silently matched 0 of 31 real AI traces (all .gz, many one level
        # down under AI-ML/) while reporting nothing was wrong - exactly the
        # silent-selection failure mode this codebase's own docs warn against.
        traces = sorted(
            os.path.join(dp, f)
            for dp, _, fs in os.walk(a.traces)
            for f in fs
            if '.champsimtrace' in f and f.endswith(('.xz', '.gz')))
    else:
        traces = a.traces.split(',')
    os.makedirs(a.outdir, exist_ok=True)
    extra = {'PSC_THERMAL': a.thermal, 'PSC_ENDURANCE_CV': a.endurance_cv}

    jobs = [(pol, tr, a.warmup, a.sim, int(nw), extra, a.outdir)
            for tr, nw, pol in itertools.product(traces, a.nvm_ways.split(','), a.policies.split(','))]
    print('%d runs, %d workers' % (len(jobs), a.workers), flush=True)

    rows = []
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for r in ex.map(one, jobs):
            rows.append(r)

    # derived: everything relative to lru on the same trace and array geometry
    base = {(r['trace'], r['nvm_ways']): r for r in rows if r['policy'] == 'lru'}
    for r in rows:
        b = base.get((r['trace'], r['nvm_ways']))
        if not b or not b.get('ipc') or not b.get('max_writes'):
            continue
        try:
            r['ipc_delta'] = r['ipc'] / b['ipc'] - 1.0
            r['rli_direct'] = b['max_writes'] / r['max_writes'] - 1.0 if r['max_writes'] else ''
            r['rli_damage'] = b['max_damage'] / r['max_damage'] - 1.0 if r.get('max_damage') else ''
            r['cvar99_ratio'] = r['cvar99'] / b['cvar99'] if b.get('cvar99') else ''
            r['write_amp'] = (r['nvm_writes'] + r['sram_writes']) / (b['nvm_writes'] + b['sram_writes'])
            r['nvm_write_ratio'] = r['nvm_writes'] / b['nvm_writes'] if b['nvm_writes'] else ''
            r['mpki'] = 1000.0 * r['llc_miss'] / r['instructions'] if r.get('instructions') else ''
        except (TypeError, ZeroDivisionError):
            pass

    out = a.out or os.path.join(a.outdir, 'champsim_results.csv')
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(out, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print('wrote', out)

    print('\n%-14s %-24s %8s %9s %10s %9s %9s' % ('policy', 'trace', 'IPC', 'dIPC', 'maxWrites', 'RLIdir', 'WA'))
    for r in sorted(rows, key=lambda x: (x['trace'], -(x.get('rli_direct') or -9))):
        print('%-14s %-24s %8.4f %8.2f%% %10s %8.1f%% %9.4f' % (
            r['policy'], r['trace'], r.get('ipc') or 0, 100 * (r.get('ipc_delta') or 0),
            r.get('max_writes'), 100 * (r.get('rli_direct') or 0), r.get('write_amp') or 0))


if __name__ == '__main__':
    main()
