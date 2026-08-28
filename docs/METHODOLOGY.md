# Experimental contract

The rules every measurement in this framework obeys. They are written down
because a comparison whose rules are implicit is a comparison a reviewer has to
take on trust.

---

## 1. One base system, frozen

| Parameter | Value | Where set |
|---|---|---|
| LLC capacity | 2 MB (ChampSim default) / 1 MB / 16 MB (trace study) | `champsim_config.json`, `--size-mb` |
| Associativity | 16 ways | both |
| Line size | 64 B | both |
| Sets | capacity / (ways × line) | derived, never overridden per policy |
| NVM : SRAM ways | 12 : 4 (hybrid) or 16 : 0 (all-NVM) | `PSC_NVM_WAYS` |
| Replacement baseline | LRU | `lru` policy |
| Core, L1, L2, DRAM | ChampSim defaults, unchanged across policies | `champsim_config.json` |

No policy receives extra effective capacity, extra associativity, or free
metadata. A scheme that needs state pays for it in `state_bits`, reported in
every result row.

**Two arenas, always both.** A hybrid proposal evaluated against homogeneous-NVM
competitors on a *different* array is being credited for having SRAM ways at
all. Every scheme is therefore run on the all-NVM array and on the hybrid array
of the same total capacity, and each is compared against the unmanaged baseline
of its own arena.

---

## 2. What counts as a physical write

One write event per fill into a frame, plus every write the policy itself
causes:

```
W_phys = W_fill + W_writehit + W_migration + W_swap + W_redirect
```

`LLC.charge_write()`, `move_set()` and `swap_sets()` exist so that migration and
swap traffic lands on the destination frames' wear counters. Start-Gap moves
lines; i2WAP's Swap-Shift swaps sets; DWAWR redirects writes. All three cost NVM
writes, and all three are charged. The resulting write amplification is reported
as a column, not buried.

An accounting invariant is asserted in the test suite: for every policy,
`sum(per-frame writes) == nvm_writes + sram_writes`. A policy that loses or
double-counts a write fails the build.

---

## 3. Tuning is disjoint from evaluation

Parameters are searched on `[0, tn)` and results are measured on `[tn, n)`. The
two windows do not overlap.

This was wrong in an earlier version of the framework — the search ran on a
prefix and the evaluation then ran on the *whole* trace, prefix included. That
is not a held-out split, and a parameter that overfits the prefix would have
been rewarded twice. Fixed in `tune_and_run.py` and `run_mixes.py`; the split
boundary is printed at the top of every run and recorded in the output as
`tune_accesses` / `eval_accesses`.

Every scheme gets a grid of comparable size, searched the same way, scored on
the same metric. The proposed schemes are not tuned harder than the competitors.

---

## 4. Selection is constrained

RLI has a degenerate optimum: a policy that stops using the NVM partition drives
peak NVM writes towards zero and scores an unbounded lifetime gain while turning
a 16-way cache into a 4-way one. An early version of the predictor found exactly
that optimum — μ went to 0.0, peak writes to 1, and hit rate fell 3.3 points.

A configuration is therefore eligible only if it

- keeps the hit rate within `--hit-tol` (default 0.5 points) of the unmanaged
  baseline in the same arena, and
- keeps NVM mean write count at or above `--min-nvm-share` (default 25%) of the
  unmanaged baseline, so the NVM array is still carrying capacity.

Every rejected configuration is printed with the reason. In the 1 MB search the
guard rejected the (since-withdrawn) `linovo` policy and VRO configurations that
scored well on RLI while
dropping hit rate from 0.56 to 0.31.

---

## 5. Lifetime metrics: what is measured and what is estimated

**Measured.** Peak physical write count per frame gives raw lifetime under a
deterministic first-failure model:

```
Lambda_raw  = 1 / max_f W_f
RLI_direct  = max_f W_f,base / max_f W_f,policy - 1
```

**Estimated.** The manuscript's closed form:

```
RLI_proxy = (mu_b/mu_p) * (1 + InterV_b + IntraV_b)/(1 + InterV_p + IntraV_p) - 1
```

Both are reported for every run, and `stage4_analysis/statistics.py` validates
the second against the first: Spearman rho, Kendall tau, median absolute
percentage error, and an explicit list of every pairwise ranking reversal.

**InterV and IntraV are never summed.** They are coefficients of variation at
different nesting levels. The law of total variance combines variances, not
normalised standard deviations, so their sum has no probabilistic meaning. They
are reported as separate diagnostics.

**Damage, not just writes.** `psc_family` also tracks endurance-normalised
damage, so a frame with a weaker endurance bin or a hotter bank accumulates
damage faster than its raw write count suggests:

```
z_f = sum_k  a_k / eta_f        a_k = exp[(Ea/kB)(1/T_ref - 1/T_f,k)]
```

With `PSC_THERMAL=0` and `PSC_ENDURANCE_CV=0` this reduces to 1.0 exactly, so
the uniform-endurance results are directly comparable with the write-count
literature. Turning either term on is what turns "level the writes" into "level
the damage", and the difference between the two is the point of the TailGuard
variant. CVaR at 0.99 and 0.999 is reported alongside the maximum, because a
single noisy maximum is a poor optimisation target even though it is the correct
reporting target.

**Absolute years.** `lifetime_grid()` converts peak writes into years to first
failure over an endurance sweep of 10^12, 10^14, 10^15 and 10^18 cycles. Reported
SOT-MRAM endurance spans several orders of magnitude depending on stack, drive
condition and measurement method; a single assumed constant is not defensible.

---

## 6. Aggregation and uncertainty

- Ratios (lifetime, write amplification) aggregate by **geometric mean**.
- Rates (hit rate, IPC delta) aggregate by **arithmetic mean**.
- Every aggregate carries a **paired bootstrap 95% CI** over the workload axis.
- Per-workload results are reported, not only the average; the aggregate table
  also carries per-scheme min and max across mixes.

**Interaction is tested on the log scale**, against the multiplicative null:

```
I_AB = ln(L_AB/L_0) - ln(L_A/L_0) - ln(L_B/L_0)
```

A combination is called synergistic only if the bootstrap interval for `I_AB`
lies strictly above zero. Comparing a ratio metric against an *additive* null —
which is what the manuscript does — inflates the apparent synergy by
construction.

---

## 7. Baseline fidelity

Competitors are re-implemented from published descriptions, not from author
code. That is stated plainly, and it is backed by conformance tests rather than
by assertion. `stage3_hycsim/test_fidelity.py` gives every mechanism three
hand-built cases:

- **ACTIVE** — a trace that must force the mechanism to fire, with the expected
  outcome computed by hand
- **INACTIVE** — a trace on which it must stay dormant and leave the baseline
  replacement decision untouched
- **BOUNDARY** — a tie or threshold case with a documented resolution

A policy that passes only the ACTIVE case is not verified: a mechanism that
fires all the time is as wrong as one that never fires. The suite also asserts
the write-accounting invariant and deterministic replay for every policy, and
exits non-zero on any failure so it can gate a result run.

Writing the tests changed the results. Three cases failed on first run:

1. **PSC has no spill rule.** Its SRAM selection always returns a way, so a
   stream of dirty blocks thrashes 4 SRAM ways while 12 NVM ways stay idle. That
   is a property of the published mechanism, now pinned by a test, and it is why
   PSC-Pred needs an explicit steering cap.
2. A DWAWR boundary expectation was wrong in the test, not the policy: a
   restricted way must receive *zero* writes, which is what it does.
3. An AYUSH case was invalidated by trace semantics — a GetS hit on a dirty
   block invalidates it, so the SRAM frames emptied themselves before the
   assertion ran.

---

## 8. Performance and wear from the same simulation

The manuscript takes IPC from one tool and wear from another, and the two do not
reconcile: 0.31 reorder events per million accesses at 4 cycles each is 1.24
controller cycles per million, which cannot produce a 0.11% IPC change unless
the event stalls a much longer path.

`stage5_champsim` removes the gap. The `psc_family` ChampSim replacement module
implements every policy under comparison and tracks physical wear inside the
same run that produces IPC, MPKI and miss latency. Policy selection is by
environment variable, so a policy change is never a rebuild and never a
different binary.

Asymmetric hit latency between SRAM and NVM ways is not expressible in
ChampSim's fixed per-cache latency. It is handled by bracketing: the LLC latency
is swept across the SRAM and SOT-MRAM values that stage 1 (DESTINY) produces,
and the resulting IPC range is reported. The share of hits served from each
partition is reported by the module, so an effective latency can also be
computed post hoc. This is stated as an approximation rather than presented as a
per-way timing model.

---

## 9. Reproducibility

- Stage 1 is resumable and content-addressed by config: a design point whose log
  already parsed is reused.
- Stage 5 caches on the presence of a complete module stats block in the log.
- Every result row records the exact parameter tuple used (`params` /
  `PSC_*` environment), the accesses evaluated, and the tuning split boundary.
- Random draws (endurance bins) are seeded (`PSC_SEED`), and deterministic
  replay is asserted in the test suite.
- Tool versions: DESTINY v2 (bitbucket `sparsh_mittal/destiny_v2`), NVMExplorer
  with its NVSim submodule, ChampSim (`ChampSim/ChampSim`), HyCSim
  (`uz-gaz/hycsim`). The DESTINY build needs `-std=gnu++03` on g++ 11+; the
  ChampSim build here uses system CLI11/fmt/nlohmann rather than vcpkg because
  several vcpkg downloads are blocked in this environment.

---

## 10. Known limitations

These belong in the paper, not in a footnote.

1. **Re-implementations, not author artefacts.** Conformance-tested, but not the
   original code. Any residual difference is a threat to the comparison and is
   disclosed as such.
2. **Warm-up.** The ChampSim runs here use 2 M warm-up / 8 M simulation
   instructions to fit the available compute. Published LLC studies typically
   use far more; the numbers are directionally useful and should be re-run
   longer before submission.
3. **Single-core.** The ChampSim configuration is 1-core. Multi-programmed
   pressure changes LLC write behaviour and should be added.
4. **Damage model parameters are templates.** The Arrhenius acceleration and the
   endurance distribution are parametric forms with plausible defaults, not
   values fitted to a specific SOT stack. They must be calibrated against
   primary device data, and the sensitivity swept, before any absolute claim.
5. **No per-way latency model.** Bracketed, as described in §8.
