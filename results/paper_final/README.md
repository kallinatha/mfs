# Results shipped with the paper

This directory is the exact output of the pipeline run that produced the
numbers in "A Multi-Factor Scaling and Lifetime-Extension Framework for
Endurance-Limited NVM Caches: An SOT-MRAM Case Study" (Kallinatha H D,
Basavaraj Talawar; SUSCOM revision R3). It is committed as data, not
regenerated on clone, so a reviewer can check a table in the paper against a
CSV in this repo without first running the pipeline.

`provenance.json` records the toolchain, the config file's SHA-256, and a
SHA-256 of the source region of every trace used — so "was this run against
the trace it claims to be" is checkable, not just asserted.

## Paper artefact -> file in this directory

| Paper item | What it is | Source file here |
|---|---|---|
| Table 21, Fig. 19 | Device-level SRAM/STT-MRAM/SOT-MRAM/PCM comparison, 16 MB/45 nm | `destiny/results.csv` (filter `capacity_MB==16, node_nm==45, roadmap=='HP', opt_target=='WriteEDP'`) |
| Table (SOT-MRAM roadmap scaling) | MFS design-space sweep across node/capacity | `dse/screen.csv`, `dse/geometry.csv`, `dse/scaling.csv` |
| Table "Head-to-head against DWWR" (`tab:sota-headtohead`) | PSC/VRO/PSC-VRO/PSC-Soft vs. the strongest published baseline, RLI_direct, Wilcoxon, guard pass counts | `sota_headtohead.csv`, `hycsim/aggregate.csv`, `hycsim/aggregate_loo.csv`, `hycsim/aggregate_global.csv` |
| Table "Architectural state cost per scheme" (`tab:sota-metadata`) | Metadata/state-bit overhead per scheme, as a percentage of the 17 MB LLC used in this evaluation | `hycsim/aggregate.csv` (`state_overhead_pct` column; e.g. `psc-soft,hybrid` row = 0.5572, matching the paper's 0.56%), per-mix detail in `hycsim/tuning_all_mixes.csv` (`state_bits`, `state_overhead_pct` columns) |
| Fig. "IntraV/InterV across ten mixes" | Per-mix write-variation diagnostics with guard annotation | `hycsim/lifetime_all_mixes.csv`, `hycsim/lifetime_<mix>.csv` (one file per ten-mix trace, guard pass/fail per scheme) |
| Fig. "Per-mix RLI (symlog)" | Relative lifetime improvement per mix, DWWR/PSC/PSC-VRO/PSC-Soft | `hycsim/lifetime_all_selections.csv` |
| Fig. "PSC x VRO interaction" (ablation) | Sub-additivity finding (bootstrap CI on the log scale) | `statistics.json` (`interaction` block) |
| Fig. "Geomean RLI vs. LRU, ChampSim" and the 43-5 win vs. i2WAP | Cycle-accurate validation, 48 traces / 24 families | `champsim/champsim_results.csv` |
| Table "Kendall tau on scheme ranking across cell projections" (`tab:portability-tau`) | Cross-technology projection, SOT-MRAM/STT-MRAM/PCM | `cross_stack_results.csv`, `cross_stack_results_stt.csv`, `cross_stack_results_pcm.csv` |
| Density-replacement / iso-capacity / iso-area figures | NVMExplorer application-level replacement study | `nvmexplorer/trace_profiles.csv`, `nvmexplorer_dnn/results/*.csv` |
| Endurance-sweep discussion | SOT-MRAM endurance sensitivity (no settled community value; STT-MRAM pinned at 4x10^12) | `endurance_floor.csv`, `../../config/endurance.json` (pipeline input, not output) |
| Cell-phase / density discussion | `cell_phase_diagram.csv` |
| DNN break-even discussion | `dnn_breakeven.csv` |

`profile_writes.json` and `assumptions.json` are supporting intermediates
(per-workload write-intensity profile used to build the ten mixes, and the
explicit list of modelling assumptions the run declares up front) rather than
numbers quoted directly in the paper.

## Regenerating this directory

`config/suscom_ten_mixes.json` is the exact config used to produce these
results, with two things you must change before it will run on your machine:
`traces.raw_dir`/`traces.prepared_dir` point at the trace set's location on
the original machine (traces are not bundled — see "What is not in this
repo" in the top-level README), and `paths.results_dir` (currently
`results/suscom_ten_mixes`) is where the run will write, which you may want
to point somewhere other than this directory so you don't overwrite the
shipped copy while comparing against it.

```bash
./setup.sh --with-champsim
# edit config/suscom_ten_mixes.json: traces.raw_dir, traces.prepared_dir,
# paths.results_dir
./run_all.sh --config config/suscom_ten_mixes.json
```

A re-run will not be byte-identical (wall-clock `runtime_s` columns and the
`platform`/tool-version fields in `provenance.json` reflect the machine that
ran it), but every RLI, Wilcoxon p-value, bootstrap CI, and Kendall tau is
deterministic given the same trace SHA-256s and config SHA-256 recorded here
in `provenance.json` — check those hashes first if a rerun disagrees.
`tests/test_hycsim_conformance.py` and `tests/test_policy_conformance.py`
check the parts of the pipeline that must reproduce bit-exactly, independent
of any trace.
