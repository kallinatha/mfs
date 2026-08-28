#!/usr/bin/env python3
"""Single entry point for HyCSim + NVMExplorer + DESTINY experiments."""
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))


def load_config(path):
    path = os.path.abspath(path)
    with open(path) as src:
        cfg = json.load(src)
    # Relative paths in a config resolve against the PROJECT ROOT, not against
    # the config file's own directory.
    #
    # Resolving against the config directory was the original behaviour and it
    # was wrong in a way that hid: every config ships in `config/`, so
    # `"results_dir": "results/smoke"` silently became
    # `<root>/config/results/smoke`. Each stage still wrote and read
    # consistently, so nothing failed loudly - but `run_all.sh` computes the
    # same string relative to the root, so the runner looked for outputs in a
    # directory nothing ever wrote to. The visible symptoms were "no
    # NVMExplorer models", a failed acceptance gate, and an empty summary,
    # none of which named the actual cause.
    base = ROOT

    def resolve(value):
        value = os.path.expanduser(os.path.expandvars(value))
        return value if os.path.isabs(value) else os.path.abspath(os.path.join(base, value))

    for section, keys in {
        'tools': ('destiny_dir', 'nvmexplorer_dir', 'hycsim_dir', 'champsim_dir'),
        'traces': ('raw_dir', 'prepared_dir'),
        'paths': ('results_dir', 'nvm_output_dir'),
    }.items():
        for key in keys:
            if cfg.get(section, {}).get(key):
                cfg[section][key] = resolve(cfg[section][key])
    cfg['_config_path'] = path
    return cfg


def run(cmd, cwd=None, env=None):
    print('\n$ %s' % ' '.join(cmd), flush=True)
    start = time.time()
    proc = subprocess.run(cmd, cwd=cwd, env=env)
    print('completed in %.1fs (exit %d)' % (time.time() - start, proc.returncode), flush=True)
    if proc.returncode:
        raise SystemExit(proc.returncode)


def doctor(cfg):
    checks = {
        'DESTINY binary': os.path.join(cfg['tools']['destiny_dir'], 'destiny'),
        'NVMExplorer driver': os.path.join(cfg['tools']['nvmexplorer_dir'], 'run.py'),
        'NVMExplorer NVSim': os.path.join(cfg['tools']['nvmexplorer_dir'],
                                         'nvmexplorer_src', 'nvsim_src', 'nvsim'),
    }
    if cfg['tools'].get('hycsim_dir'):
        checks['HyCSim sample trace'] = os.path.join(cfg['tools']['hycsim_dir'],
                                                     'traces_dir', 'example_trace.txt')
    failed = False
    for label, path in checks.items():
        ok = os.path.isfile(path)
        print('%-25s %s  %s' % (label, 'OK' if ok else 'MISSING', path))
        failed |= not ok
    try:
        import numpy
        print('%-25s OK  %s' % ('NumPy', numpy.__version__))
    except ImportError:
        print('%-25s MISSING' % 'NumPy')
        failed = True
    # Conformance runs even when tools are missing. It needs nothing external,
    # and it answers the question that actually matters - are the mechanisms
    # right - which should not be gated behind whether NVMExplorer is installed.
    if cfg.get('experiment', {}).get('skip_conformance'):
        print('%-25s %-3s %s' % ('policy conformance', 'SKIP', 'disabled in config'))
    else:
        print()
        try:
            conformance(cfg)
        except SystemExit:
            failed = True
            print('policy conformance FAILED - fix this before anything else')
    if failed:
        raise SystemExit('doctor found missing prerequisites')


def fetch_sample(cfg):
    raw = cfg['traces']['raw_dir']
    out = os.path.join(raw, 'example_trace.txt')
    sample = cfg.get('sample', {})
    cmd = [sys.executable, os.path.join(ROOT, 'scripts', 'fetch_hycsim_sample.py'),
           '--out', out, '--events', str(sample.get('events', 20000)),
           '--warmup', str(sample.get('warmup', 2000))]
    if cfg['tools'].get('hycsim_dir'):
        cmd += ['--hycsim-dir', cfg['tools']['hycsim_dir']]
    run(cmd)


def prepare(cfg):
    trace, exp = cfg['traces'], cfg['experiment']
    cmd = [sys.executable, os.path.join(ROOT, 'scripts', 'prepare_mixes.py'),
           '--raw-dir', trace['raw_dir'], '--prepared-dir', trace['prepared_dir'],
           '--pattern', trace.get('pattern', '*.txt'),
           '--workers', str(trace.get('prepare_workers', 2)),
           '--clock-hz', str(trace.get('clock_hz', 2.0e9)),
           '--limit', str(trace.get('limit', 0))]
    if trace.get('force', False):
        cmd.append('--force')
    run(cmd)


def destiny(cfg):
    paths, exp = cfg['paths'], cfg['experiment']
    outdir = os.path.join(paths['results_dir'], 'destiny')
    cmd = [sys.executable, os.path.join(ROOT, 'stage1_destiny', 'sweep.py'),
           '--destiny-dir', cfg['tools']['destiny_dir'],
           '--out', os.path.join(outdir, 'results.csv'),
           '--log-dir', os.path.join(outdir, 'logs'),
           '--workers', str(exp.get('destiny_workers', 2)),
           '--preset', exp.get('destiny_preset', 'paper')]
    run(cmd)


def nvmexplorer(cfg):
    tools, paths, exp = cfg['tools'], cfg['paths'], cfg['experiment']
    result_dir = os.path.join(paths['results_dir'], 'nvmexplorer')
    config_path = os.path.join(result_dir, 'mfs_nvmexplorer.json')
    os.makedirs(result_dir, exist_ok=True)
    run([sys.executable, os.path.join(ROOT, 'stage2_nvmexplorer', 'gen_config.py'),
         '--nvmexplorer-dir', tools['nvmexplorer_dir'], '--out', config_path,
         '--output-dir', paths['nvm_output_dir'],
         '--capacities', str(exp.get('size_mb', 16)),
         '--node', str(exp.get('process_node_nm', 45)),
         '--opt', exp.get('nvm_opt_target', 'ReadEDP')])
    # Upstream run.py writes data/* and output/results using cwd-relative paths,
    # even when output_path is absolute. Give it an isolated compatibility cwd
    # so the NVMExplorer source checkout remains unchanged.
    workdir = os.path.join(result_dir, 'workdir')
    for rel in ('data/cell_cfgs', 'data/mem_cfgs', 'output/results'):
        os.makedirs(os.path.join(workdir, rel), exist_ok=True)
    run([sys.executable, os.path.join(ROOT, 'stage2_nvmexplorer', 'run_compat.py'),
         '--nvmexplorer-dir', tools['nvmexplorer_dir'], config_path],
        cwd=workdir)
    run([sys.executable, os.path.join(ROOT, 'stage2_nvmexplorer', 'evaluate_trace_profiles.py'),
         '--nvmexplorer-dir', tools['nvmexplorer_dir'],
         '--nvm-output-dir', paths['nvm_output_dir'],
         '--profiles-dir', cfg['traces']['prepared_dir'],
         '--out', os.path.join(result_dir, 'trace_profiles.csv'),
         '--config', config_path])


def hycsim(cfg):
    exp, paths = cfg['experiment'], cfg['paths']
    outdir = os.path.join(paths['results_dir'], 'hycsim')
    cmd = [sys.executable, os.path.join(ROOT, 'stage3_hycsim', 'run_mixes.py'),
           '--traces-dir', cfg['traces']['prepared_dir'], '--outdir', outdir,
           '--size-mb', str(exp.get('size_mb', 16)),
           '--tune-accesses', str(exp.get('tune_accesses', 6_000_000)),
           '--workers', str(exp.get('hycsim_workers', 1)),
           '--evaluation', exp.get('evaluation_region', 'suffix'),
           # per-mix tuning is an oracle ceiling; loo is the implementable
           # number. Running both makes the gap visible instead of assumed.
           '--selection', exp.get('selection', 'per-mix,loo,global'),
           '--hit-tol', str(exp.get('hit_tolerance', 0.005)),
           '--min-nvm-share', str(exp.get('min_nvm_share', 0.25))]
    if exp.get('schemes'):
        cmd += ['--schemes', ','.join(exp['schemes'])]
    if exp.get('label_spec'):
        cmd += ['--label-spec', exp['label_spec']]
    run(cmd)


def nvmexplorer_dnn(cfg):
    """AI/DNN workload characterization using NVMExplorer's native traffic.

    NVMExplorer ships measured access profiles for DNN inference (ResNet-26/50
    weights and activations at 60 fps, ALBERT NLP embeddings/full-model) and
    graph analytics (data/workload_data/). The main study never used them, so
    the paper had no answer to "does any of this transfer to AI workloads".
    This stage runs those built-in sweeps over OUR custom cells - the same
    cited SOT/STT/SRAM/PCM parameter sets the CPU study uses - so the AI-side
    numbers are directly comparable to the trace-driven ones.

    DNN inference on weight arrays is read-dominated (writes/input = 0 for the
    weight tensors), which is exactly the regime where NVM's write penalty
    stops mattering and its leakage advantage dominates: a favourable, honest
    extension of the paper's argument, measured rather than asserted.
    """
    tools, paths, exp = cfg['tools'], cfg['paths'], cfg['experiment']
    result = paths['results_dir']
    outdir = os.path.join(result, 'nvmexplorer_dnn')
    conf = os.path.join(outdir, 'mfs_dnn.json')
    run([sys.executable, os.path.join(ROOT, 'stage2_nvmexplorer', 'gen_config.py'),
         '--nvmexplorer-dir', tools['nvmexplorer_dir'],
         '--out', conf, '--output-dir', outdir,
         '--name', 'mfs_dnn_arrays',
         '--capacities', str(exp.get('dnn_capacity_mb', exp.get('size_mb', 16))),
         '--node', str(exp.get('process_node_nm', 45)),
         '--opt', exp.get('dnn_opt_targets', 'ReadEDP,WriteEDP'),
         '--traffic', exp.get('dnn_traffic', 'dnn,graph')])
    run([sys.executable, os.path.join(ROOT, 'stage2_nvmexplorer', 'run_compat.py'),
         '--nvmexplorer-dir', tools['nvmexplorer_dir'], conf],
        cwd=tools['nvmexplorer_dir'])


def champsim(cfg):
    """Cycle-level IPC and physical wear from the same simulation.

    Requires a built ChampSim whose replacement/ directory contains the
    psc_family module shipped in stage5_champsim/module/.
    """
    tools, paths, exp = cfg['tools'], cfg['paths'], cfg['experiment']
    cs_dir = tools.get('champsim_dir')
    # champsim_traces may be a list of paths relative to champsim_traces_root,
    # which is how the production config expresses a deliberate trace selection
    # rather than "whatever is in that directory".
    traces = exp.get('champsim_traces') or tools.get('champsim_traces_dir')
    if isinstance(traces, (list, tuple)):
        root = exp.get('champsim_traces_root') or ''
        traces = list(traces)
        # AI workload traces (e.g. the DPC ML/AI ChampSim trace suite): any
        # *.champsimtrace.xz dropped under champsim_ai_traces_root is picked up
        # automatically, so adding AI coverage is a download, not a code change.
        ai_root = exp.get('champsim_ai_traces_root') or ''
        # If a screening manifest is configured, it is authoritative and the
        # directory walk below is skipped entirely: this is what makes trace
        # selection auditable rather than "whatever showed up in the folder
        # today". Generate the manifest with a screening pass (see
        # docs/RESCUE_PLAN_EXHAUSTIVE.md Part 8.3) - one path per line,
        # absolute or relative to champsim_ai_traces_root, '#' comments and
        # blank lines ignored.
        ai_manifest = exp.get('champsim_ai_traces_manifest') or ''
        if ai_manifest and os.path.exists(ai_manifest):
            lines = [ln.strip() for ln in open(ai_manifest)
                     if ln.strip() and not ln.strip().startswith('#')]
            found = [ln if os.path.isabs(ln) else os.path.join(ai_root, ln)
                     for ln in lines]
            print('champsim: %d AI trace(s) taken from manifest %s (unscreened '
                  'traces under %s are deliberately excluded)'
                  % (len(found), ai_manifest, ai_root))
            traces += found
        elif ai_root and os.path.isdir(ai_root):
            # Accept both compression formats and the DPC/ML suffix style
            # ('name.champsimtrace-023.gz'): anything containing
            # '.champsimtrace' and ending in .xz or .gz. ChampSim's trace
            # reader decompresses both natively.
            found = sorted(
                os.path.join(dp, f)
                for dp, _, fs in os.walk(ai_root)
                for f in fs
                if '.champsimtrace' in f and f.endswith(('.xz', '.gz')))
            print('champsim: %d AI trace(s) found under %s (unscreened - set '
                  'champsim_ai_traces_manifest to restrict to screened '
                  'survivors)' % (len(found), ai_root))
            traces += found
        resolved, missing = [], []
        for t in traces:
            full = t if os.path.isabs(t) else os.path.join(root, t)
            (resolved if os.path.exists(full) else missing).append(full)
        if missing:
            print('champsim: %d of %d selected traces are absent and will be '
                  'skipped:' % (len(missing), len(traces)))
            for m in missing[:8]:
                print('   missing  %s' % m)
        if not resolved:
            raise SystemExit('champsim: none of the selected traces exist under %r'
                             % root)
        traces = ','.join(resolved)
    if not cs_dir or not traces:
        raise SystemExit('champsim stage needs tools.champsim_dir and a trace path')
    cmd = [sys.executable, os.path.join(ROOT, 'stage5_champsim', 'run_champsim.py'),
           '--traces', traces,
           '--warmup', str(exp.get('champsim_warmup', 20000000)),
           '--sim', str(exp.get('champsim_sim', 100000000)),
           '--nvm-ways', str(exp.get('champsim_nvm_ways', 12)),
           '--workers', str(exp.get('champsim_workers', 2)),
           '--thermal', str(int(exp.get('champsim_thermal', 0))),
           '--endurance-cv', str(exp.get('champsim_endurance_cv', 0.0)),
           '--outdir', os.path.join(paths['results_dir'], 'champsim')]
    if exp.get('champsim_policies'):
        cmd += ['--policies', ','.join(exp['champsim_policies'])]
    run(cmd, env=dict(os.environ, CHAMPSIM_DIR=cs_dir))


def integrate(cfg):
    paths, exp = cfg['paths'], cfg['experiment']
    result = paths['results_dir']
    # Prefer the cited per-technology sweep in config/endurance.json over a
    # hand-set grid: SOT is swept 1e12..1e15 because it has no settled value,
    # STT is pinned at the community-standard 4e12 so the comparison is fair.
    table = os.path.join(ROOT, 'config', 'endurance.json')
    grid = exp.get('endurance_grid_writes') or [1e12, 1e14, 1e15, 1e18]

    # The join used to run against exactly ONE NVM cell, so the whole
    # policy-lifetime story had a single technology data point and the
    # policy x technology interaction was never measured. Each entry below is
    # (tag, nvm_cell, destiny_tech, endurance_key); the first is the primary
    # and keeps the unsuffixed output filename so nothing downstream changes.
    joins = exp.get('join_technologies') or [
        {'tag': '',     'nvm_cell': exp.get('nvm_cell', 'SOT_12F2'),
         'destiny_nvm_tech': exp.get('destiny_nvm_tech', 'SOT-12/1'),
         'nvm_technology': exp.get('nvm_technology', 'SOT-MRAM')},
        {'tag': 'stt',  'nvm_cell': 'STT_baseline',
         'destiny_nvm_tech': 'STT-MRAM', 'nvm_technology': 'STT-MRAM'},
        {'tag': 'pcm',  'nvm_cell': 'PCM_baseline',
         'destiny_nvm_tech': 'PCM', 'nvm_technology': 'PCM'},
    ]
    for j in joins:
        tag = j.get('tag', '')
        out = os.path.join(result, 'cross_stack_results%s.csv'
                           % (('_' + tag) if tag else ''))
        cmd = [sys.executable, os.path.join(ROOT, 'stage4_analysis', 'cross_stack.py'),
               '--lifetime', os.path.join(result, 'hycsim', 'lifetime_all_mixes.csv'),
               '--nvm-models', os.path.join(result, 'nvmexplorer', 'trace_profiles.csv'),
               '--destiny', os.path.join(result, 'destiny', 'results.csv'),
               '--out', out,
               '--nvm-cell', j['nvm_cell'],
               '--sram-cell', exp.get('sram_cell', 'SRAM_6T'),
               '--destiny-nvm-tech', j['destiny_nvm_tech'],
               '--destiny-sram-tech', exp.get('destiny_sram_tech', 'SRAM'),
               '--endurance-grid', ','.join('%g' % float(e) for e in grid)]
        tech = j.get('nvm_technology', '')
        if os.path.isfile(table) and tech:
            cmd += ['--endurance-table', table, '--nvm-technology', tech]
        if exp.get('whole_array_leakage'):
            cmd.append('--whole-array-leakage')
        if not exp.get('strict_join', True):
            cmd.append('--no-strict')
        # Alternate-technology joins are additional evidence, not gates: a
        # missing PCM model must not abort the primary SOT result.
        try:
            run(cmd)
        except SystemExit:
            if not tag:
                raise
            print('integrate: %s join failed; primary result unaffected' % tag)


def statistics(cfg):
    """Bootstrap CIs, proxy validation, log-scale interaction, lifetime years."""
    paths, exp = cfg['paths'], cfg['experiment']
    result = paths['results_dir']
    run([sys.executable, os.path.join(ROOT, 'stage4_analysis', 'statistics.py'),
         os.path.join(result, 'hycsim', 'lifetime_all_mixes.csv'),
         '--out', os.path.join(result, 'statistics.json'),
         '--accesses-per-second', str(exp.get('accesses_per_second', 1e8))])


def conformance(cfg):
    """Policy conformance suite. A result run must not start without it."""
    run([sys.executable, os.path.join(ROOT, 'tests', 'test_policy_conformance.py')])


def provenance(cfg):
    tools, paths = cfg['tools'], cfg['paths']
    cmd = [sys.executable, os.path.join(ROOT, 'scripts', 'record_provenance.py'),
           '--config', cfg['_config_path'], '--destiny-dir', tools['destiny_dir'],
           '--nvmexplorer-dir', tools['nvmexplorer_dir'],
           '--profiles-dir', cfg['traces']['prepared_dir'],
           '--out', os.path.join(paths['results_dir'], 'provenance.json')]
    if tools.get('hycsim_dir'):
        cmd += ['--hycsim-dir', tools['hycsim_dir']]
    run(cmd)


def validate(cfg):
    result = cfg['paths']['results_dir']
    run([sys.executable, os.path.join(ROOT, 'scripts', 'validate_results.py'),
         '--profiles-dir', cfg['traces']['prepared_dir'],
         '--lifetime-csv', os.path.join(result, 'hycsim', 'lifetime_all_mixes.csv'),
         '--nvmexplorer-csv', os.path.join(result, 'nvmexplorer', 'trace_profiles.csv'),
         '--cross-stack-csv', os.path.join(result, 'cross_stack_results.csv'),
         '--expected-mixes', str(cfg['experiment'].get('expected_mixes', 10))])


COMMANDS = {
    'doctor': doctor, 'fetch-sample': fetch_sample, 'prepare': prepare,
    'destiny': destiny, 'nvmexplorer': nvmexplorer, 'hycsim': hycsim,
    'integrate': integrate, 'statistics': statistics, 'validate': validate,
    'provenance': provenance, 'conformance': conformance, 'champsim': champsim,
    'nvmexplorer-dnn': nvmexplorer_dnn,
}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command', choices=tuple(COMMANDS) + ('sample', 'all'))
    ap.add_argument('--config', required=True)
    ap.add_argument('--stages',
                    default='prepare,destiny,nvmexplorer,hycsim,integrate,statistics',
                    help='for all: comma-separated ordered stages')
    args = ap.parse_args()
    cfg = load_config(args.config)
    if args.command == 'sample':
        doctor(cfg)
        fetch_sample(cfg)
        for name in ('prepare', 'destiny', 'nvmexplorer', 'hycsim', 'integrate', 'statistics'):
            COMMANDS[name](cfg)
        validate(cfg)
        provenance(cfg)
    elif args.command == 'all':
        doctor(cfg)
        for name in (x.strip() for x in args.stages.split(',')):
            if name not in COMMANDS or name == 'doctor':
                raise SystemExit('invalid stage: %s' % name)
            COMMANDS[name](cfg)
        if 'integrate' in args.stages.split(','):
            validate(cfg)
        provenance(cfg)
    else:
        COMMANDS[args.command](cfg)


if __name__ == '__main__':
    main()
