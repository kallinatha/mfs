#!/usr/bin/env python3
"""Generate a path-correct NVMExplorer configuration for the MFS study.

SOT-MRAM is represented through NVMExplorer's custom STT/MRAM cell interface.
This is an array-model approximation: the 3-terminal SOT device parameters are
explicit inputs and must be replaced by measured/calibrated values before a
paper claim is made.
"""
import argparse
import hashlib
import json
import os
import shutil

SOT_COMMON = dict(
    # 2.5 F fits NVMExplorer's fixed 1.46 cell aspect ratio at 12 F^2.
    # DESTINY receives the same width in stage1_destiny/cells.
    access_CMOS_width=2.5,
    r_on=5000,
    r_off=10000,
    read_voltage=0.25,
    read_power=30,
    set_pulse=0.266,
    reset_pulse=0.266,
    set_energy=0.1,
    reset_energy=0.1,
)

CUSTOM_CELLS = [
    dict(cell_type='SRAM', name='SRAM_6T', cell_size_F2=146),
    dict(cell_type='STT', name='STT_baseline', cell_size_F2=40,
         r_on=3000, r_off=6000, read_voltage=0.25, read_power=30,
         set_pulse=10, reset_pulse=10, set_energy=1.0, reset_energy=1.0),
    dict(cell_type='STT', name='SOT_12F2', cell_size_F2=12,
         cell_aspect_ratio=1.0, **SOT_COMMON),
    dict(cell_type='STT', name='SOT_24F2', cell_size_F2=24,
         cell_aspect_ratio=2.0, **SOT_COMMON),
    dict(cell_type='PCM', name='PCM_baseline', cell_size_F2=25),
]


GENERATED_CELLS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               '..', 'stage1_destiny', 'cells', 'generated',
                               'nvmexplorer_cells.json')


def load_generated_cells(path=GENERATED_CELLS):
    """Cells emitted from published parameter sets by cells/from_paper.py.

    These carry citations; the hand-written entries in CUSTOM_CELLS above do
    not. Both are offered so the two provenance classes stay distinguishable in
    the results, and so a reviewer can see which rows are checkable.
    """
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return []
    with open(path) as f:
        return json.load(f)


def csv_list(value, cast=str):
    return [cast(x.strip()) for x in value.split(',') if x.strip()]


def build(out, nvmexplorer_dir, output_dir, exp_name='mfs_trace_arrays',
          capacities=(16,), process_node=45, opt_targets=('ReadEDP',),
          traffic=(), technologies=('SRAM', 'STT', 'PCM'), cell_names=()):
    root = os.path.abspath(nvmexplorer_dir)
    binary = os.path.join(root, 'nvmexplorer_src', 'nvsim_src', 'nvsim')
    output_dir = os.path.abspath(output_dir)
    # NVMExplorer loads its technology database from <output_path>/NVM_data
    # even for custom-cell runs. Preserve that upstream contract in an isolated
    # result directory instead of writing results into the source checkout.
    database = os.path.join(root, 'output', 'NVM_data')
    if not os.path.isdir(database):
        raise FileNotFoundError('NVMExplorer database not found: %s' % database)
    os.makedirs(output_dir, exist_ok=True)
    shutil.copytree(database, os.path.join(output_dir, 'NVM_data'), dirs_exist_ok=True)
    catalogue = list(CUSTOM_CELLS) + load_generated_cells()
    seen = set()
    catalogue = [c for c in catalogue
                 if not (c['name'] in seen or seen.add(c['name']))]
    selected_cells = [c for c in catalogue if not cell_names or c['name'] in cell_names]
    chosen_cells = []
    for source in selected_cells:
        cell = dict(source)
        payload = json.dumps(cell, sort_keys=True, separators=(',', ':')).encode('utf-8')
        cell['name'] = '%s_%s' % (cell['name'], hashlib.sha256(payload).hexdigest()[:8])
        chosen_cells.append(cell)
    technologies = [t for t in technologies if any(c['cell_type'] == t for c in chosen_cells)]
    if not chosen_cells or not technologies:
        raise ValueError('cell/technology selection is empty')
    cfg = {
        'experiment': {
            'exp_name': exp_name,
            'cell_type': list(technologies),
            'process_node': int(process_node),
            'opt_target': list(opt_targets),
            # 64-bit array word avoids invalid wide-word SRAM organizations;
            # the trace adapter converts each 64-byte cache line to 8 transfers.
            'word_width': 64,
            'capacity': list(capacities),
            'bits_per_cell': [1],
            # Empty is intentional: evaluate_trace_profiles.py applies the
            # measured HyCSim workload/profile instead of a built-in sweep.
            'traffic': list(traffic),
            'nvsim_path': binary,
            'output_path': output_dir,
            'custom_cells': 'True',
        },
        'custom_cells': chosen_cells,
    }
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, 'w') as dst:
        json.dump(cfg, dst, indent=2)
        dst.write('\n')
    print('wrote %s' % os.path.abspath(out))
    return cfg


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--nvmexplorer-dir', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--output-dir', required=True)
    ap.add_argument('--name', default='mfs_trace_arrays')
    ap.add_argument('--capacities', default='16')
    ap.add_argument('--node', type=int, default=45)
    ap.add_argument('--opt', default='ReadEDP')
    ap.add_argument('--traffic', default='',
                    help='optional built-ins (generic,dnn,graph,spec); empty uses trace adapter')
    ap.add_argument('--technologies', default='SRAM,STT,PCM')
    ap.add_argument('--cell-names', default='',
                    help='optional comma-separated custom-cell names')
    args = ap.parse_args()
    build(args.out, args.nvmexplorer_dir, args.output_dir, args.name,
          csv_list(args.capacities, int), args.node, csv_list(args.opt),
          csv_list(args.traffic), csv_list(args.technologies), csv_list(args.cell_names))


if __name__ == '__main__':
    main()
