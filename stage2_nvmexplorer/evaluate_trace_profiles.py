#!/usr/bin/env python3
"""Evaluate prepared HyCSim profiles with NVMExplorer's analytical model.

NVMExplorer first generates NVSim array pickles.  This adapter then supplies
each measured mix's GetS and L2_Rpl rates/counts to PatternConfig and
ExperimentResult.  It avoids substituting NVMExplorer's built-in generic sweep
for the user's ten actual mixes.
"""
import argparse
import csv
import glob
import json
import math
import os
import pickle
import sys


def profiles(root):
    pats = glob.glob(os.path.join(root, '**', 'profile.json'), recursive=True)
    pats += glob.glob(os.path.join(root, '*.profile.json'))
    for path in sorted(set(pats)):
        with open(path) as src:
            p = json.load(src)
        mix = os.path.basename(os.path.dirname(path)) if path.endswith('/profile.json') \
            else os.path.basename(path).replace('.profile.json', '')
        yield mix, path, p


def cell_name(obj, pkl_path):
    cfg = obj.input_cfg.cell_type
    path = getattr(cfg, 'cell_file_path', '')
    if path:
        return os.path.splitext(os.path.basename(path))[0]
    return os.path.basename(pkl_path).split('_nvsim_output.pkl')[0]


def evaluate(nvmexplorer_dir, nvm_output_dir, profiles_dir, out, config_path=''):
    root = os.path.abspath(nvmexplorer_dir)
    sys.path.insert(0, root)
    # These imports are required both for the public analytical interface and
    # to make the NVSimOutputConfig classes available to pickle.
    from nvmexplorer_src.input_defs.access_pattern import PatternConfig
    from nvmexplorer_src.eval_utils import ExperimentResult

    pickles = sorted(glob.glob(os.path.join(nvm_output_dir, 'nvsim_output', '*.pkl')))
    if not pickles:
        pickles = sorted(glob.glob(os.path.join(nvm_output_dir, '**', '*.pkl'), recursive=True))
    if config_path:
        with open(config_path) as src:
            cfg = json.load(src)
        names = [c['name'] for c in cfg.get('custom_cells', [])]
        pickles = [p for p in pickles
                   if any(('_%s_nvsim_output.pkl' % name) in os.path.basename(p)
                          for name in names)]
    profs = list(profiles(profiles_dir))
    if not pickles:
        raise FileNotFoundError('no NVSim output pickles under %s' % nvm_output_dir)
    if not profs:
        raise FileNotFoundError('no profile.json files under %s' % profiles_dir)

    rows = []
    for pkl_path in pickles:
        with open(pkl_path, 'rb') as src:
            model = pickle.load(src)
        invalid = [name for name in ('read_latency', 'write_latency', 'read_energy',
                                     'write_energy', 'leakage_power', 'area')
                   if float(getattr(model, name, -1)) <= 0]
        if invalid:
            raise RuntimeError('invalid NVSim output %s (%s <= 0); inspect the matching log' %
                               (pkl_path, ', '.join(invalid)))
        inp = model.input_cfg
        for mix, profile_path, p in profs:
            duration = float(p.get('duration_s', 0.0))
            counts = p['counts']
            # Mapping follows the HyCSim data-array interface: GetS can read a
            # resident line; L2_Rpl inserts a line. GetX is retained separately
            # because it is an ownership request, not automatically an array write.
            reads = int(counts['GetS'])
            writes = int(counts['L2_Rpl'])
            rf = reads / duration if duration else 0.0
            wf = writes / duration if duration else 0.0
            pat = PatternConfig(
                exp_name='mfs_trace', benchmark_name=mix,
                read_freq=rf, total_reads=reads, read_size=64,
                write_freq=wf, total_writes=writes, write_size=64,
                workingset=int(inp.capacity))
            result = ExperimentResult(pat, inp, model)
            result.evaluate()
            transfers_r = int(math.ceil((8.0 * reads * 64) / inp.word_width))
            transfers_w = int(math.ceil((8.0 * writes * 64) / inp.word_width))
            direct_read_mJ = transfers_r * model.read_energy / 1.0e9
            direct_write_mJ = transfers_w * model.write_energy / 1.0e9
            rows.append({
                'mix': mix,
                'profile': os.path.abspath(profile_path),
                'technology': getattr(inp.cell_type, 'mem_cell_type', ''),
                'cell': cell_name(model, pkl_path),
                'capacity_MB': inp.capacity,
                'process_node_nm': inp.process_node,
                'opt_target': inp.opt_target,
                'word_width_bits': inp.word_width,
                'duration_s': duration,
                'trace_GetS': reads,
                'trace_GetX': int(counts['GetX']),
                'trace_L2_Rpl': writes,
                'read_frequency_per_s': rf,
                'write_frequency_per_s': wf,
                'array_read_transfers': transfers_r,
                'array_write_transfers': transfers_w,
                'read_latency_ns': model.read_latency,
                'write_latency_ns': model.write_latency,
                'read_energy_pJ': model.read_energy,
                'write_energy_pJ': model.write_energy,
                'leakage_power_mW': model.leakage_power,
                'area_mm2': model.area,
                'read_bw_GB_s': model.read_bw,
                'write_bw_GB_s': model.write_bw,
                'dynamic_read_power_mW': result.total_dynamic_read_power,
                'dynamic_write_power_mW': result.total_dynamic_write_power,
                'total_power_mW': result.total_power,
                'total_read_energy_mJ': result.total_read_energy,
                'total_write_energy_mJ': result.total_write_energy,
                'total_latency_ms': result.total_read_latency + result.total_write_latency,
                'formula_check_abs_mJ': abs(result.total_read_energy - direct_read_mJ) +
                                        abs(result.total_write_energy - direct_write_mJ),
            })
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, 'w', newline='') as dst:
        writer = csv.DictWriter(dst, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print('wrote %s (%d mix x array rows)' % (os.path.abspath(out), len(rows)))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--nvmexplorer-dir', required=True)
    ap.add_argument('--nvm-output-dir', required=True)
    ap.add_argument('--profiles-dir', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--config', default='',
                    help='generated study JSON; filters stale pickles by cell fingerprint')
    args = ap.parse_args()
    evaluate(args.nvmexplorer_dir, args.nvm_output_dir, args.profiles_dir, args.out,
             args.config)


if __name__ == '__main__':
    main()
