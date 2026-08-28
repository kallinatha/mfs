#!/usr/bin/env python3
"""Run upstream NVMExplorer with one documented custom-cell compatibility fix.

NVMExplorer's cell classes support ``cell_ratio``, but ``gen_custom_cell`` does
not expose it in JSON.  SOT geometry needs that knob (12 F^2, aspect ratio 1).
This wrapper passes ``cell_aspect_ratio`` through without modifying the upstream
checkout, then executes the official run.py unchanged.
"""
import argparse
import os
import runpy
import sys


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--nvmexplorer-dir', required=True)
    ap.add_argument('config')
    args = ap.parse_args()
    root = os.path.abspath(args.nvmexplorer_dir)
    sys.path.insert(0, root)
    import nvmexplorer_src.tentpoles as tentpoles

    original = tentpoles.gen_custom_cell

    def with_aspect_ratio(cell_type, custom_cell_inputs):
        path, cfg = original(cell_type, custom_cell_inputs)
        if 'cell_aspect_ratio' in custom_cell_inputs:
            cfg.cell_ratio = float(custom_cell_inputs['cell_aspect_ratio'])
            cfg.generate_cell_file()
            cfg.append_cell_file()
        return path, cfg

    tentpoles.gen_custom_cell = with_aspect_ratio
    driver = os.path.join(root, 'run.py')
    sys.argv = [driver, os.path.abspath(args.config)]

    # Upstream run.py assumes it is executed FROM the NVMExplorer checkout:
    # nvmexplorer_src/combine_csv.py opens 'output/results/<cell>_<bpc>-combined.csv'
    # by a relative path, and open(..., 'a') raises FileNotFoundError when the
    # directory does not exist rather than creating it.  Running the driver from
    # the mfs-revision root therefore produced per-array CSVs (their output_path
    # is absolute and resolved fine) and then died in combine_csv, which is why
    # the DNN sweep emitted only the first cell type's arrays and never reached
    # STT or PCM.  Enter the checkout, guarantee the output tree exists, and
    # restore the caller's cwd afterwards.
    prev = os.getcwd()
    os.makedirs(os.path.join(root, 'output', 'results'), exist_ok=True)
    os.chdir(root)
    try:
        runpy.run_path(driver, run_name='__main__')
    finally:
        os.chdir(prev)


if __name__ == '__main__':
    main()
