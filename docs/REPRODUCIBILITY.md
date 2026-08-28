# Reproducibility: how to regenerate every number, and how to check it

Someone who has never seen this repository should be able to reproduce every
figure in the paper from this document alone, and — more importantly — should
be able to tell when a run has silently failed. That second part is what most
artefacts get wrong, and what this one is built around.

---

## 1. One command

```bash
./run_clean_slate.sh                 # everything, from the replay onward
./run_clean_slate.sh --resume        # skip stages whose outputs already exist
./run_clean_slate.sh --analysis-only # re-derive from an existing results tree
```

It runs the replay, the DNN array sweep, the acceptance gate, the
cross-technology projection, the phase diagram, the reviewer analyses and an
independent re-derivation — and it **refuses to continue past any stage that
did not do what it claimed**.

Expect roughly 21 hours for a full run: ~20.6 h for the replay (23 schemes ×
10 mixes × 3 selection modes) and ~2 h for the DNN arrays. The analysis stages
take seconds.

---

## 2. Why the gates exist

Every check in `run_clean_slate.sh` corresponds to a failure that actually
happened during this revision and was not noticed until hours later. They are
documented here because a reader deciding whether to trust the artefact is
better served by the list of ways it broke than by an assurance that it works.

| Failure | How it looked | The gate that catches it |
|---|---|---|
| `--only a,b,c` matched no stage | Full summary printed in 3 seconds, all numbers stale | Output files must exist and be non-empty after stage A |
| Six policies implemented, registered, tested — and never run | Results looked complete | Config scheme list is diffed against the policy registry before starting |
| NVMExplorer wrote one cell type and died in `combine_csv` | Stage exited 0, two of six CSVs present | Cell types found are diffed against cell types configured |
| Two replays writing one results tree | Both wrote plausible CSVs; last writer won | `pgrep run_mixes.py` refuses to start a second |
| A verifier pinned to a path that existed only on the author's machine | `FileNotFoundError` | Takes `--results`; degrades to SKIP, never crashes |
| A recursive glob picked up `results/sample/statistics.json` | Three confident, wrong mismatches | `find()` ignores `sample/`, `test/`, `bak/`, prefers shallowest |
| A pooled fallback bit stamped onto every per-mix row | `guard_mixes = 0` for schemes that held the guard on 4 of 10 mixes | `selection_fallback` and `selection_guard_passed` are now separate columns |

The general rule: **a stage is not done because it exited 0. It is done when
its output exists, is non-empty, and contains what the next stage needs.**

---

## 3. What is measured and what is projected

This distinction matters more than any single number, so it is made explicit
rather than left implicit in the code.

**Measured** — requires the replay, changes only if the policy or trace changes:
per-frame wear vectors, `max_write`, hit rate, read/write counts per partition,
IPC (ChampSim), mechanism activity counters.

**Projected** — a fixed function of the measured counts and per-cell constants,
computable in milliseconds for any technology: dynamic energy, serialised array
time, EDP, leakage, area, years-to-first-failure.

`stage6_transfer/transfer.py` makes the projection explicit and checkable. It
reuses `stage4_analysis.cross_stack.join_row` rather than reimplementing the
energy model, and `--validate` proves the equivalence by reprojecting a
technology that was actually joined and diffing every value:

```
worst relative difference: 0.000e+00
VERDICT: EXACT - the projection path is the production path
```

That check runs inside `run_clean_slate.sh`. If it is not exact, every
projected-technology result is void and the script stops.

**What projection cannot do**, stated so a reviewer does not have to find it:
it answers "given this behaviour, what would this cell cost". It cannot answer
"would the policy behave differently on this cell". For a cache whose latency
feeds back into the access stream, that difference is real.

---

## 4. Answering "does it hold for technology X?"

Without re-simulating anything:

```bash
cat > mycell.json <<'EOF'
{ "name": "MY_CELL",
  "write_energy_pJ": 4200.0,
  "write_latency_ns": 65.0,
  "read_energy_pJ": 180.0,
  "read_latency_ns": 4.5,
  "leakage_power_mW": 900.0 }
EOF

python3 stage6_transfer/transfer.py --project --cell-json mycell.json \
  --lifetime results/suscom_ten_mixes/hycsim/lifetime_all_mixes.csv \
  --models   results/suscom_ten_mixes/nvmexplorer/trace_profiles.csv \
  --destiny  results/suscom_ten_mixes/destiny/results.csv \
  --out      results/mycell_cross_stack.csv
```

Any parameter left out keeps the value of the measured template cell, so a
projection is never built on a cell whose parameters are jointly impossible.
`synth_model` rejects any key that is not a real cell parameter.

And the design-space map — which scheme is EDP-optimal *where* in cell
parameter space:

```bash
python3 stage6_transfer/transfer.py --sweep \
  --lifetime results/suscom_ten_mixes/hycsim/lifetime_all_mixes.csv \
  --models   results/suscom_ten_mixes/nvmexplorer/trace_profiles.csv \
  --destiny  results/suscom_ten_mixes/destiny/results.csv \
  --out      results/suscom_ten_mixes/cell_phase_diagram.csv
```

The measured technologies sit at write-energy ratios r = E_wNVM / E_wSRAM of
**0.43 (SOT-12/1)**, **0.78 (STT)** and **54.35 (PCM)**. Under SOT an NVM write
is *cheaper* than an SRAM write, so a scheme that diverts writes into SRAM to
save wear pays for it in energy; under PCM the same diversion saves 54× per
write. That single number is why Kendall τ on dynamic EDP between SOT and PCM
is **−0.551** — the PCM ordering is closer to the reverse of the SOT ordering
than to agreement with it.

---

## 5. Verification layers

Four independent layers, each able to catch what the others miss.

1. **Conformance suite** (`pytest tests/`, 35 tests). Policy determinism, write
   accounting invariants, ablation equalities, the projection's exactness and
   its monotonicity in write energy. Runs before anything else in
   `run_clean_slate.sh`.
2. **Acceptance gate** (`scripts/validate_results.py`). Hard-fails on only two
   conditions: a *baseline* failing its own guard (the harness is broken), or a
   scheme with zero guarded rows still reporting a gain above 0.05 (the
   laundering case). Partial guard splits are printed as results to report, not
   as faults. Failing the whole run on any guard failure pushes an author
   towards deleting the guard rather than reporting it.
3. **Reviewer analyses** (`stage4_analysis/pareto.py --section all`). Guard
   decomposition, the lifetime/hit-rate frontier, ChampSim liveness gates,
   Pareto admissibility and LPP, cluster bootstrap, Wilcoxon with
   Holm–Bonferroni, Kendall τ across technologies.
4. **Independent re-derivation** (`scripts/verify_headline_numbers.py`).
   Deliberately shares no helper with layer 3 and uses only `csv`/`json`: a
   check that imports the code it checks cannot catch an error in that code.

A mismatch in layer 4 is **not automatically a failure**. Adding or removing a
scheme changes the proxy comparison set and the counts move with it. Every
mismatch must be *explained* before the number is quoted. Worked example: adding
`psc-guard` took the valid-row count from 330 to 340, and C(340,2) = 57,630
reproduces the new pair count exactly, with 11,889 / 57,630 = 0.20630 matching
the reported rate to five decimals.

---

## 6. Reporting rules

These are not style preferences. Each one exists because dropping it produces a
claim the data does not support.

1. **Report leave-one-mix-out.** Per-mix tuning is an oracle ceiling: the
   parameters saw the mix they are scored on.
2. **Quote every geomean with its guard split.** A scheme holding the guard on
   4 of 10 mixes has a 4-of-10 result. `psc-balance` at 17.18× is a claim about
   four mixes, and saying so is what makes it survive review.
3. **Quote the endurance decade with every SOT lifetime.** It is a band, not a
   number; the imec floor is a lower bound on endurance and therefore an upper
   bound on what may be claimed.
4. **State `n_families` beside `n_traces`.** The 48 ChampSim traces are 24
   families — 16 independent programs plus 8 AI model families shipping several
   checkpoints and seeds each. Resampling traces flat understates the
   uncertainty by roughly half.
5. **A MARGINAL or DEAD liveness row is not evidence about its scheme.** ChampSim
   `psc` fires on 3.5e−04 of allocations against its siblings' 0.37; that row
   measures a mechanism that did not run.
6. **τ = 1 for RLI across technologies is an identity, not a robustness result.**
   RLI is a ratio of write counts and cannot depend on the cell. Quoting it as
   technology-robust is exactly what a sharp reviewer will call out.
7. **Report the negative results.** The migration-write explanation for the EDP
   reordering was falsified (r = 0.080, p = 0.65) before NVM write share was
   found (r² = 0.825, p = 2.0e−14). The first PSC-Guard controller failed its own
   test at occupancy 0.996. A method that reports only its successful hypotheses
   is not a method.

---

## 7. Environment

- Python 3.11, `numpy`, `scipy`, `pytest` (see `requirements.txt`)
- DESTINY and NVMExplorer are built by `./setup.sh`; ChampSim by
  `./setup.sh --with-champsim`
- `provenance.json` records config hashes, tool versions and trace digests for
  every run; cite the stamp of the run a number came from
- Traces are not redistributable. `docs/RUNNING.md` documents how they were
  produced and `prep_trace.py` verifies the time base against each trace's own
  `Sim_seconds` header — an unverified clock silently rescales every energy and
  years-to-failure figure, and once did so by 500×.
