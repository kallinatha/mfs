# Response to the R3 revision blueprint

Point-by-point: what the audit demanded, what the framework now does, and what
it still does not do. Items marked **not done** are not excuses — they are the
remaining work, sized.

---

## Defects the audit found in my earlier framework

Three were real. All three are fixed.

| Audit finding | Status |
|---|---|
| "The current 'held-out prefix' is later included in full-trace evaluation, so it is not held out." | **Fixed.** Tuning runs on `[0, tn)`, evaluation on the disjoint `[tn, n)`. The boundary is printed and recorded per row as `tune_accesses` / `eval_accesses`. |
| "The independent framework contains simplified/unfaithful policies; its results are diagnostic warnings, not paper-grade evidence." | **Addressed, not eliminated.** 30 conformance tests now cover every policy with ACTIVE / INACTIVE / BOUNDARY cases, plus a write-accounting invariant and deterministic-replay checks. They still test a re-implementation, not author code, and that is stated in every document. |
| "The supplied variant file lacks the expected full-trace 1 MB/16 MB results." | **Partly fixed.** 1 MB search-phase results are complete; the full-trace phases were killed by memory pressure and are re-running. ChampSim now supplies an independent second measurement of the same quantities. |

---

## §1.2 Common comparison contract

| Requirement | Status |
|---|---|
| Freeze one base system; no comparator gets extra capacity or free metadata | **Done.** Fixed capacity/associativity/line size; `state_bits` and `state_overhead_pct` reported per scheme. Two arenas (all-NVM, hybrid) so a hybrid proposal is never compared against homogeneous competitors on a different array. |
| Identical traces and windows; record access count | **Done.** Access counts recorded per row. Trace hashing **not done** — one line to add. |
| Define a physical write event once; charge every induced event | **Done.** `W_fill + W_writehit + W_migration + W_swap + W_redirect`, with `charge_write()` / `move_set()` / `swap_sets()`. Write amplification is a reported column. An accounting invariant is asserted in the test suite. |
| Tune each policy only on a declared training subset | **Done**, and now genuinely disjoint. Equal-size grids per scheme. |
| Publish policy fidelity tests with expected vs observed transitions | **Done.** `stage3_hycsim/test_fidelity.py`, 30 cases, exits non-zero on failure. |
| Paired runs, report every workload, bootstrap the geometric mean with 95% CI | **Implemented, under-populated.** `stage4_analysis/statistics.py` does paired bootstrap CIs and per-workload reporting; with a single trace the interval degenerates. Needs the ten mixes. |

---

## §4 Mathematical and metric audit

**"The current RLI proxy is not a lifetime equation."** Agreed, and now
quantified rather than asserted. Validating the proxy against directly measured
peak writes across every scheme and arena in the 1 MB run:

| | |
|---|---|
| Spearman rho | 0.830 |
| Kendall tau | 0.718 |
| **Median absolute error** | **69.4%** |
| **Pairwise ranking reversals** | **11 of 78 (14.1%)** |

A 69% median error and a reversal in one comparison in seven means the proxy
would have picked a different winner seven times out of fifty. `RLI_direct` is
now the primary metric everywhere; the proxy is reported beside it so the gap is
visible rather than hidden.

**"InterV and IntraV cannot be added as standard deviations."** Agreed. They are
computed and reported as separate diagnostics in both the Python model and the
ChampSim module. Nothing sums them.

**"Interactions need the right aggregation."** Implemented exactly as specified:

```
I_AB = ln(L_AB/L_0) - ln(L_A/L_0) - ln(L_B/L_0)
```

with a bootstrap interval, and a verdict of `synergistic` only when the interval
is strictly above zero. On the 1 MB data `I = -0.0201` — sub-multiplicative,
consistent with the audit's reading of the peak-write numbers.

**"Absolute lifetime is required."** `lifetime_grid()` converts peak writes to
years to first failure over 10^12 / 10^14 / 10^15 / 10^18 cycles, parameterised
by access rate. The manuscript's treatment of 10^12–10^14 as a contemporary
constant is not carried forward.

---

## §5 Algorithm correctness

| Invariant | Status |
|---|---|
| Bounded role count | **Done** for VRO (`R` clamped at construction); tested. |
| Legal rotation is a bijection | **Done** by construction (modular window over a fixed way set); the ACTIVE test checks the window contents explicitly. |
| Accounting conservation | **Done** and asserted per policy. |
| Deterministic replay | **Done** and asserted. |
| No data loss/duplication across migration | **Partly.** `move_set` / `swap_sets` preserve tags and charge writes; there is no separate property test for duplication. One test to add. |
| Coherence atomicity | **Not modelled.** Neither the trace model nor the ChampSim module changes coherence state; this is a stated scope limit, not a claim. |

---

## §6 Hardware overhead

`state_bits()` on every policy gives a bill of materials, reported as
`state_overhead_pct` against tag+data storage. The manuscript's dimensionally
incorrect overhead equation is not reproduced.

**Not done:** controller cycles, critical-path analysis, synthesis area. The
audit is right that "0.31 events/M × 4 cycles" cannot explain a 0.11% IPC
change. Stage 5 now measures IPC in the same run as the wear, so the two are at
least consistent by construction — but a cycle-accurate controller model is not
present, and no IPC claim should be attributed to controller stalls without one.

---

## §7 Experimental method

| Requirement | Status |
|---|---|
| Same capacity/associativity/line/replacement/core/memory/window for every baseline | **Done.** |
| Disjoint tuning and evaluation | **Done.** |
| ≥30 seeds where randomisation is modelled; 95% CIs | **Infrastructure done, runs not done.** Endurance draws are seeded (`PSC_SEED`) and the bootstrap machinery exists. The Monte Carlo sweep over endurance CV has not been run. |
| Modern suites and diverse write patterns | **Partly.** SPEC CPU2017 ChampSim traces (lbm, omnetpp, mcf, cactuBSSN, roms, xalancbmk) downloaded and running. PARSEC, GAPBS and the adversarial microbenchmarks are **not done**. |
| Phase sensitivity and trace-length convergence | **Not done.** |
| Calibrate technology parameters against primary device measurements | **Not done.** DESTINY cell files use tool defaults plus the manuscript's geometry; the damage model uses parametric templates. Both are flagged wherever used. |
| MPKI, IPC, latency breakdown, energy-to-solution, write amplification, max damage, p99/p99.9, temperature, area | **Mostly done.** IPC, MPKI, miss latency, write amplification, max writes, p99/p99.9, max damage, CVaR@0.99/0.999 all reported. Energy-to-solution and area **not done**. |
| One-command reproducibility with a versioned manifest | **Partly.** `run_all.py` drives every stage and stages are resumable; code/config/trace hashes are **not** yet in a manifest. |

**"The decomposition experiment is confounded."** Agreed — the C1→C2→C3→C4 path
changes technology, capacity and architecture in an author-chosen order. The
framework's two-arena design removes part of the confound (technology mix is
held fixed within an arena), but the **full factorial is not yet run**. That is
the right fix and it is cheap: the arenas and capacities already exist as
parameters.

---

## §9–10 Novel idea portfolio

**"Why the existing PSC-Pred and VRO-Rank variants are not lead ideas."**
Accepted. Write prediction and wear-aware victim selection both have dense prior
art. They are now positioned as components, not as the novelty claim.

**TailGuard-PSC is implemented** in the ChampSim module (`PSC_POLICY=tailguard`):

- per-frame damage normalised by an endurance bin drawn from a lognormal with
  sweepable CV (`PSC_ENDURANCE_CV`), spatially assigned per frame;
- a bank-level thermal estimator in the discrete IIR form of the audit's Eq. 3,
  driving an Arrhenius acceleration factor (`PSC_THERMAL`, off by default so the
  uniform case reduces exactly to write counting);
- placement by **marginal Weibull hazard** on normalised damage —
  `Δh_f = (z_f + δ_f)^κ − z_f^κ` — evaluated over a bounded candidate set of the
  `K` least-recently-used NVM ways, so there is no global associative search;
- CVaR at 0.99 and 0.999 reported alongside the maximum.

**Not done:** virtual-queue shadow prices (Eqs. 8–10), the oracle predictor
ablation, PulseDose, RegimeGate, Reliability-Elastic and QoS-Wear. The
virtual-queue controller is the most valuable of these and is the natural next
step — the cost function `C(a)` is already factored in the code, so the queues
slot in where `λ` currently sits.

---

## §13 Acceptance-rescue plan

| Item | Status |
|---|---|
| Named competitors replacing SA-1/SA-2 | **Done.** AYUSH, DWAWR, i2WAP, EqualChance, Start-Gap, Sequoia, SWWR, DWWR — all implemented, conformance-tested, tuned. (The earlier `linovo` entry has been withdrawn: "LiNoVo" is a thesis title, not a mechanism; see `stage3_hycsim/policies/swwr.py`.) |
| Direct lifetime as the primary result | **Done.** |
| InterV/IntraV as diagnostics only | **Done.** |
| Title broadening | Recommended, unchanged from the earlier plan; the audit's wording is better and should be used. |
| Factorial/Pareto presentation instead of a sequential decomposition | **Not done.** |
| Response-letter four-field format | Adopted in the revision plan document. |

---

## What I would do next, in order

1. **Run the ten mixes.** Everything statistical is starved by n=1. The bootstrap
   CIs, the interaction interval and the proxy-reversal rate all become
   publishable only with the full set. `prep_all_mixes.sh` + `run_mixes.py` are
   ready; the traces are on the user's disk.
2. **Full factorial** over {technology, capacity, architecture} instead of the
   sequential path, with Shapley attribution if any cell is infeasible.
3. **Monte Carlo over endurance CV** (0/5/10/20/30%) × seeds, which is what makes
   TailGuard's premise testable at all — if damage variation does not change the
   ranking, TailGuard's motivation collapses and that should be reported.
4. **Longer ChampSim runs and multi-core**, plus GAPBS and the adversarial
   microbenchmarks.
5. **Result manifest** with code, config and trace hashes.
6. **Energy-to-solution and area**, which the sustainability framing needs and
   which the paper currently asserts rather than measures.
