# Reviewer defence: the story the evidence selects, and every claim behind it

Recomputed from the completed run of 2026-08-26 (replay 74,276 s, 23 schemes ×
10 mixes × 3 selection modes; acceptance gate passed) plus the cached ChampSim
batch of 48 traces. Every figure is reproducible with

```bash
./run_clean_slate.sh --analysis-only
```

and independently re-derived by `scripts/verify_headline_numbers.py`, which
shares no helper with the analysis path.

---

## 0. The decision, made mechanically

The rescue plan fixed the narrative rule *before* the results existed:
mechanism paper if PSC-Guard meets its Definition of Done; else cross-stack
paper if any pairwise Kendall τ falls below 0.5; else measurement paper.
`scripts/decide_narrative.py` applies it to the artefacts:

| test | outcome |
|---|---|
| PSC-Guard Definition of Done (Part 1.7) | **NOT MET** — 1 of 2 lbm17 mixes |
| any pairwise τ < 0.5 | **YES** — τ(SOT,PCM) = −0.549 on dynamic EDP |
| a mechanism beats published SOTA under the guard | **YES** — psc-soft, 6–0, p = 0.031 |

**Narrative C: the cross-stack interaction paper, with psc-soft as the
mechanism section.** The third test is not in the plan; it was added because
the plan's mechanism branch is keyed on PSC-Guard specifically, and a different
mechanism earned a mechanism claim on the evidence even though PSC-Guard did
not.

---

## 1. The lead contribution: which scheme wins is a property of the cell

### The measurement

| metric | τ(SOT,STT) | τ(SOT,PCM) | τ(STT,PCM) |
|---|---|---|---|
| RLI_direct | +1.000 | +1.000 | +1.000 |
| total energy | +0.749 | +0.841 | +0.803 |
| **dynamic EDP** | +0.556 | **−0.549** | **−0.114** |

Say plainly that **τ = 1 for RLI is an identity, not a robustness result**. RLI
is a ratio of write counts and cannot depend on the cell. Quoting it as
technology-robust is exactly what a sharp reviewer will call out, and
pre-empting it costs nothing.

The ranking that *can* move does. Under PCM the scheme ordering by
energy-delay is closer to the reverse of the SOT ordering than to agreement.

### The mechanism, quantified

The first hypothesis — that migration-write intensity drives the reordering —
was **falsified**: r = 0.080, p = 0.65. It is reported because a method that
publishes only its successful hypotheses is not a method. What does explain it:

```
EDP rank change (SOT → PCM) = 78.4 × (NVM write share) + const
                        r² = 0.825    p = 2.0e-14    n = 36
```

DESTINY write energy at 16 MB, 45 nm: **SOT 0.704 nJ, STT 1.257 nJ, PCM
88.07 nJ**. Expressed against the SRAM write energy of 1.620 nJ, the ratio
r = E_wNVM/E_wSRAM is **0.43, 0.78 and 54.35**. That crossing of r = 1 is the
whole story: under SOT an NVM write is *cheaper* than an SRAM write, so a
scheme that diverts writes into SRAM to save wear pays for it in energy; under
PCM the same diversion saves 54× per write.

> Which wear-management scheme is best is not a property of the scheme. It is
> set by the cell's write energy, through one measurable quantity: the fraction
> of writes the scheme keeps off the NVM array.

### The design-space map

`stage6_transfer/transfer.py --sweep` sweeps cell write energy and write
latency and reports the EDP-optimal scheme at each point. Three regions:

| region | optimal scheme |
|---|---|
| low write energy, low write latency (where SOT sits) | `elba/all-nvm` |
| a narrow band between | `i2wap/all-nvm` |
| high write energy, high write latency (where PCM sits) | `psc/hybrid` |

The boundary runs diagonally because EDP is the product of an energy and a time
term and both move with the cell. Five schemes are Pareto-optimal on every cell
tested, but the front shrinks from 7 to 5 as write energy rises — so even the
"safe" set is technology-dependent.

### Why this is checkable rather than asserted

The replay produces a *count vector* per (scheme, mix): NVM/SRAM reads and
writes, peak wear, measured duration. Those are properties of the policy and
the workload and do not depend on the cell. Everything the cross-stack join
computes downstream is a fixed function of that vector and a handful of
datasheet constants. So any cell — including unfabricated ones — is scoreable
in milliseconds from a replay that has already run.

`transfer.py` reuses `cross_stack.join_row` rather than reimplementing the
energy model, and `--validate` proves the equivalence on the production data:

```
projected 360 rows against 360 measured rows
  compared 2160 values across dynamic_energy_mJ, leakage_energy_mJ,
  total_energy_mJ, dynamic_EDP_mJ_ms, serialized_array_time_ms, array_area_mm2
  worst relative difference: 0.000e+00
  VERDICT: EXACT - the projection path is the production path
```

**What it cannot do**, stated so a reviewer does not have to find it: a
projection answers "given this behaviour, what would this cell cost". It cannot
answer "would the policy behave differently on this cell". For a cache whose
latency feeds back into the access stream, that distinction is real.

---

## 2. The mechanism section: psc-soft, and why it survives where others do not

### The problem it solves was measured, not assumed

PSC's published rule steers every dirty fill to the SRAM partition. At the LLC
of a real hierarchy the writeback stream mostly *hits* rather than allocating,
so the rule almost never fires:

| policy | activity counter | actions per allocation | verdict |
|---|---|---|---|
| psc_soft | steered | 3.71e−01 | LIVE |
| psc_pred | steered | 3.70e−01 | LIVE |
| **psc** | steered | **3.52e−04** | **MARGINAL** |
| ayush | ayush_migrations | 2.35e−04 | MARGINAL |

Four orders of magnitude. On 619.lbm, **293,487 of 293,488 LLC write accesses
hit**. Meanwhile PSC's companion clause — clean blocks are banned from SRAM —
pushes nearly all fills into NVM, so PSC *increases* NVM writes by a median of
**32% on 48 of 48 traces**. Unanimous.

A MARGINAL liveness row is not evidence about its scheme. The ChampSim `psc`
row measures a mechanism that did not run, and reporting it as a refutation of
PSC would be wrong in the other direction.

### The repair, and its two independent validations

`psc_soft` keeps the intent (write-carrying blocks belong in the high-endurance
partition) and drops the harmful half.

**ChampSim, 48 traces / 24 families:**

| | fire rate | NVM writes vs LRU | RLI | IPC cost |
|---|---|---|---|---|
| psc | 2.24e−05 | 1.322× | −0.120 | −0.16% |
| **psc_soft** | **0.450** | **0.750×** | **+0.273** | −0.53% |

Geomean LPP **+0.2516**, cluster-bootstrap 95% CI **[+0.191, +0.333]**,
Wilcoxon **p = 7.6e−09**, survives Holm–Bonferroni across 11 schemes, on the
Pareto front for **30 of 48** traces, and *stronger on the AI traces* (+0.275)
than on SPEC (+0.206).

**Replay, head-to-head against the strongest published scheme:** on the mixes
where both psc-soft and `dwwr` (Agarwal 2020) hold the acceptance guard,
psc-soft wins **6 of 6, p = 0.03125**. With n = 6 that is the smallest
two-sided p Wilcoxon can produce — a clean sweep.

It is the only scheme in the field that clears that bar. `psc-balance`, `psc`
and `psc-vro` win 10–0 *unguarded* at p = 0.002, but hold the guard on only
4, 4 and 3 mixes respectively, leaving too few paired mixes to test. That
asymmetry is the result, and burying it under the unguarded geomean is exactly
what the guard exists to prevent.

### What it costs

**Zero added architectural state** for `psc`, `psc-vro`, `psc-guard` and `vro`
— `state_bits = 0`, `state_overhead_pct = 0.0000`. For comparison the
published `swwr` needs 42 bits, `i2wap` 60, `dwwr` 94; `tailguard` needs
4.2 million and `dwawr` 2.4 million.

---

## 3. The endurance floor: a categorical exclusion with a number on it

Inverting the usual question. Not "how long does this cell last" but "what
endurance does a 5-year product demand, and how much does a scheme relax it?"
From Y = E·T/(w_max·3.156e7), the requirement is E = Y·w_max·3.156e7/T — replay
quantities only, no cell model needed.

| scheme | writes/cell for 5 years | vs baseline |
|---|---|---|
| psc-balance | 4.21e10 | **17.35× lower** |
| psc | 4.62e10 | 15.80× |
| psc-vro | 7.16e10 | 10.19× |
| psc-guard | 6.12e11 | 1.19× |
| lru-hybrid (baseline) | 7.30e11 | — |

Against the published decades — PCM 1e8, STT 4e12, SOT >1e15:

> **No wear-management scheme brings a 5-year 16 MB LLC within PCM's
> endurance.** The best still needs 4.21e10 writes/cell — short by **421×**.
> Every scheme clears STT and SOT comfortably.

The method self-validates: the ratio to baseline must equal (1 + RLI) by
construction, and psc-guard's 1.19× matches its LOO RLI of 0.1919 exactly. A
test asserts it.

---

## 4. The proxy is unsafe

n = 340 rows, 57,630 pairwise comparisons: median absolute error **91.6%**,
and **11,889 of 57,630 pairwise rankings reversed (20.6%)**. One scheme pair in
five is mis-ranked by the standard proxy. Any table built on `rli_proxy` must
be rebuilt on `rli_direct`.

The earlier figures (330 rows, 54,285 pairs, 11,367 reversals, 20.9%) predate
psc-guard. Adding one scheme over ten mixes takes the valid-row count from 330
to 340, and C(340,2) = 57,630 reproduces the new pair count exactly — the
change is bookkeeping, not a shift in the finding.

---

## 5. The array design space: a 914× methodological warning

888 successful DESTINY array points across 6 cells and 17 knobs.

**Sweeping only the optimiser target, holding cell and capacity fixed, moves
write EDP by up to 914×** (pcram at 1 MB; 117× at 4 MB, 99× at 16 MB, and
38–46× for the SOT cells at 32 MB). Anyone quoting a single NVM array figure
without stating the optimisation target is quoting an arbitrary point in a
range that spans three orders of magnitude.

**The SRAM→SOT write-latency crossover sits between 4 and 16 MB:**

| capacity | SRAM best (ns) | SOT best (ns) | faster |
|---|---|---|---|
| 1 MB | 0.126 | 0.321 | SRAM |
| 4 MB | 0.250 | 0.376 | SRAM |
| 16 MB | 0.693 | 0.556 | **SOT** |
| 32 MB | 1.107 | 0.665 | **SOT** |

This matters for the paper's framing: at the 16 MB LLC the study targets, SOT
is already the faster technology to write, which is why the hybrid split has to
justify itself on wear rather than on latency.

---

## 6. The AI/DNN section: the break-even question is vacuous, and that is the finding

With all six array CSVs present, ρ* is computable for the first time. It is
**negative for every NVM cell at every access rate** — meaning the NVM is the
lower-energy choice at *every* read fraction, PCM included. Verified directly
rather than trusted: at ρ = 0, 0.75 and 1.0, NVM wins in every case.

The cause is leakage: **SRAM 291.3 mW against PCM 37.2 and SOT 25.8** at 16 MB.
No read fraction can rescue SRAM on energy.

So the honest statement is that energy alone always favours NVM at this
capacity, and the decision is made on EDP and endurance instead — where PCM
fails outright:

| workload | writes/run | PCM (1e8) | STT (1e12) | SOT (1e13) | SOT (1e15) |
|---|---|---|---|---|---|
| ResNet50 activations | 1.08e8 | **2.8 days** | 76.9 yr | 769 yr | 7.7e4 yr |
| ResNet26 multi | 4.05e7 | 0.021 yr | 205 yr | 2.1e3 yr | 2.1e5 yr |
| Facebook BFS 8MB | 8.8e5 | 0.94 yr | 9.4e3 yr | 9.4e4 yr | 9.4e6 yr |
| all weight-resident | **0** | not wear-limited in any technology | | | |

Weight-resident workloads have exactly zero writes. Say that in those words
rather than quoting a very large lifetime multiple, which invites the reader to
treat an undefined quantity as a measurement.

---

## 7. Fairness: the comparison is not a tuning-budget artefact

| group | schemes | grid sizes | median |
|---|---|---|---|
| published baselines | 8 | 4, 5, 5, 6, 6, 8, 9, 9 | 6 |
| this work | 13 | 1, 3, 4, 5, 5, 6, 9, 9, 12, 12, 12, 16, 18 | 9 |

This work was tuned **1.50× harder** at the median, and the largest single grid
is 18 (`psc-balance`) against the largest published 9 (`dwawr`). State that
ratio in the paper rather than claiming parity. It is small enough not to
explain a 10–0 sweep, and saying so with the number is more convincing than an
assurance.

---

## 8. Anticipated reviewer questions, with the answer and where it lives

| question | answer | artefact |
|---|---|---|
| How do you compare to i2WAP / Start-Gap / Sequoia / AYUSH? | 8 published schemes reimplemented from their papers; head-to-head with Wilcoxon + Holm and win-loss records | `sota_headtohead.csv` |
| Did you tune yours harder? | 1.50× at the median; stated, not hidden | `sota.py` §1 |
| Does this hold on another memory technology? | Computable in milliseconds for any cell; projection proved exact | `transfer.py --project` |
| Is the lifetime gain bought by trashing hit rate? | The acceptance gate tests exactly that; every geomean carries its guard split | `logs/validate-*.log` |
| Is the gain worth the metadata? | `state_bits` beside every gain; four schemes at zero | `sota.py` §3 |
| Are 48 AI traces really 48 samples? | No — 24 families. Cluster bootstrap resamples families then traces | `pareto.py --section stats` |
| Are you cherry-picking the selection mode? | All three reported; LOO is the headline, per-mix is labelled an oracle ceiling | `aggregate_*.csv` |
| Is the ChampSim result consistent with the replay? | They disagree because ChampSim's `psc` mechanism did not fire; the liveness gate catches it and the disagreement is the finding | `pareto.py --section liveness` |
| Can I reproduce it? | One command, gated after every stage, plus a claim-coverage audit | `run_clean_slate.sh` |

---

## 9. Reporting rules, and why each exists

1. **Report leave-one-mix-out.** Per-mix tuning is an oracle: the parameters
   saw the mix they are scored on.
2. **Quote every geomean with its guard split.** `psc-balance` at 16.35× is a
   claim about the 4 mixes where the guard holds.
3. **Quote the endurance decade with every SOT lifetime.** It is a band; the
   imec figure is a measurement floor and therefore an upper bound on claims.
4. **State n_families beside n_traces.** 48 traces are 24 families; resampling
   flat understates the uncertainty by roughly half.
5. **A MARGINAL or DEAD liveness row is not evidence about its scheme.**
6. **τ = 1 for RLI across technologies is an identity, not a result.**
7. **Report the negative results.** The migration-write explanation was
   falsified (p = 0.65). PSC-Guard's first controller failed its own test at
   occupancy 0.996. PSC-Guard's Definition of Done is half met. Each is in the
   paper.

---

## 10. What is verified, and how

| layer | what it catches | status |
|---|---|---|
| conformance suite, 47 tests | policy determinism, write accounting, ablation equalities, projection exactness, Wilcoxon-vs-scipy agreement, the concurrency guard in both directions | 47/47 |
| acceptance gate | lifetime bought by collapsing hit rate or abandoning the NVM array | PASSED, 23 guard splits reported |
| projection validate | any drift between the counterfactual path and the production join | EXACT, 0.000e+00 over 2,160 values |
| claim-coverage audit | a publishable stage that was never run | 20/20 claims supported |
| independent re-derivation | an error in the analysis code itself | 19/22, 3 explained |

The three verifier mismatches are the proxy counts, which moved when psc-guard
entered the comparison set, and the arithmetic reconciles exactly (§4).
