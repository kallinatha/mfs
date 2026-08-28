# Revision engineering notes — what changed, why, and which reviewer concern each change answers

Every item below was found by auditing the actual production results
(`cross_stack_results.csv`, 260 rows, run of 2026-08-18) against the code that
produced them. Each fix is verified where verification is possible without the
real traces: the 134-test conformance suite passes after every change, all 162
(scheme, parameter) grid combinations instantiate, and a bounded synthetic
end-to-end replay produces rows for **all 22 schemes** with the new guard
columns populated.

## 1. Six schemes never ran in production (BLOCKING — fixed)

`stage3_hycsim/tune_and_run.py`'s `GRID` drives which schemes the replay
executes. Six registered, conformance-tested policies were absent from it:
`psc-balance`, `tailguard`, `ayush`, `dwawr`, `elba`, `psc-soft`. Two of these
are this paper's proposed mechanisms and two are published competitors, so the
production run was missing both contributions and baselines. They now have
bounded tuning grids (4–18 points each); `HYBRID_ONLY` gained the three
SRAM-partition mechanisms that are undefined with zero SRAM ways.

**Consequence for the numbers:** stage 7 (`hycsim`) must be re-run. Tuning
cost roughly doubles; budget ~1.5–2× the previous 22.5 h.

## 2. ChampSim never launched (BLOCKING — fixed)

`mfs.py:load_config` expanded `${...}` placeholders for `destiny_dir`,
`nvmexplorer_dir` and `hycsim_dir` but not `champsim_dir`, so the literal
string `${CHAMPSIM_DIR}` reached `subprocess.run` and every stage-11 attempt
died with `FileNotFoundError`. One line: `champsim_dir` added to the expansion
list. Verified by loading the production config with the variable set.

## 3. Headline geomean mixed guard-failed rows in (fixed)

20% of per-mix rows (52/260) fail the study's own selection guard, and the
reported PSC 14.8× LOO geomean folded them in — including its single largest
contributor (51.8×, a guard-failed row). `aggregate()` now emits, for every
(scheme, arena): `guard_mixes`, `rli_direct_gm_guarded`,
`rli_direct_max_guarded`, `hit_rate_delta_mean_guarded` alongside the full-set
figures. **Report the guarded columns; the gap between the two is itself a
result.** The guard here is evaluation-region: selection did not fall back AND
the measured suffix hit-rate delta stayed within `hit_tolerance`.

## 4. The lbm17 write-pressure breakdown (addressed by measurement)

On both `lbm17`-containing mixes, PSC/PSC-VRO lose 23–25% hit rate for their
smallest lifetime gains — the dirty-block SRAM partition saturating under
sustained write bandwidth. `psc-balance`'s `max_sram_frac` / `min_nvm_frac`
occupancy guards bound exactly this failure, and its tuning grid now sweeps
them (0.30/0.45/0.60 × 0.25/0.40 × three hot thresholds), so the fix is
selected per-mix and leave-one-mix-out rather than asserted. Whether it
rescues the lbm17 mixes is an empirical question the re-run answers.

## 5. Policy×technology interaction had one data point (fixed)

The cross-stack join ran against a single NVM cell (SOT_12F2). `integrate` now
loops over three joins — SOT-12/1 (primary, unchanged filename), STT-MRAM
baseline (`cross_stack_results_stt.csv`), PCM (`cross_stack_results_pcm.csv`)
— each with its cited endurance entry from `config/endurance.json`. Alternate
joins are additional evidence, not gates: their failure cannot abort the
primary result. DESTINY and NVMExplorer models for all three already exist in
the completed sweeps, so this costs seconds, not hours.

## 6. PCM missing from the DSE knob screen (fixed)

`pcram` added to `dse_cells`, so the "156× spread from OptimizationTarget"
sensitivity claim is now measured on SRAM, STT, SOT **and** PCM rather than
being silently MRAM-only. Cached design points are keyed by config hash; only
the new PCM points run.

## 7. AI/DNN workloads: unused NVMExplorer capability now wired (new stage)

NVMExplorer ships measured access profiles for DNN inference (ResNet-26/50
weights + activations at 60 fps; ALBERT NLP embeddings / full model) and graph
analytics. New stage `nvmdnn` (between `nvmexplorer` and `profile`;
`python3 mfs.py nvmexplorer-dnn`) runs those built-in sweeps over the SAME 25
cited custom cells the CPU study uses, with ReadEDP and WriteEDP targets, into
`results/<study>/nvmexplorer_dnn/`. DNN weight inference is read-dominated
(writes/input = 0), the regime where NVM's write penalty stops mattering and
its leakage advantage dominates — a favourable, measured extension of the
paper's argument, and a direct answer to the AI-workload reviewer question.

## 8. ChampSim AI traces (config hook, data is a download)

`champsim_ai_traces_root` (default
`/media/kallinath/UBUNTU-ONLY/traces/ai_dpc`): every `*.champsimtrace.xz`
found under it — e.g. the DPC AI/ML trace suite — is appended to the ChampSim
batch automatically. Download the traces into that folder; no code change
needed. Directory absent ⇒ silently skipped.

## What was verified before packaging

1. `tests/test_policy_conformance.py` — 0 failures after all changes.
2. Registry ↔ GRID agreement: no scheme in either set is missing from the other.
3. All 162 (scheme, parameter) combinations instantiate against `PARAMS`.
4. Synthetic end-to-end `run_mixes.py` run: 22/22 schemes emit rows, tuning +
   selection + guard columns all populate, `psc-balance` engages (3.5× on the
   toy mix, guard passed).
5. Production config loads with `champsim_dir` fully expanded; `run_all.sh`
   passes `bash -n`; DNN config generates with 25 cells and dnn+graph traffic.

## What could NOT be verified here and must be watched on the real machine

- nvsim executing the DNN sweep (the cloud copy has no built nvsim binary; the
  build on your machine already exists and is untouched).
- The actual re-run cost of stage 7 with the doubled grid.
- Whether psc-balance's occupancy guards rescue the lbm17 mixes — that is the
  experiment, not a promise.

## The one command

```bash
./run_revision.sh
```

which is `setup.sh --with-champsim` (idempotent, skips everything already
built) followed by `run_all.sh --config config/suscom_ten_mixes.json`. Stages
with cached results (DSE, DESTINY, prepare, NVMExplorer trace profiles) skip
their completed work automatically; stage 7 re-runs in full because its scheme
set changed, and that is the point.
