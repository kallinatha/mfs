# Cross-stack protocol, equations and novelty gate

## 1. Research question

The revision should answer two different questions with different evidence:

1. **Technology competitiveness:** under common capacity, node and optimization
   settings, how does the SOT array compare with SRAM, STT-MRAM and PCM in
   latency, energy, leakage and area?
2. **Architectural effectiveness:** holding cache capacity and associativity
   fixed, does PSC or its extension reduce physical NVM wear relative to named
   NVM-cache policies without obtaining the result by abandoning NVM capacity,
   losing hit rate or hiding migration writes?

DESTINY and NVMExplorer address question 1. Trace replay addresses question 2.
The cross-stack join estimates the energy/lifetime consequences but does not
turn one tool's model into ground truth for the others.

## 2. Normalized event model

For a measured region with first and last cycle `c0` and `c1`, configured clock
`f_clk`, the measured duration is

```text
T = (c1 - c0) / f_clk .
```

Let `N_S`, `N_X` and `N_R` be the counts of GetS, GetX and L2_Rpl. The logical
rates passed to NVMExplorer are

```text
lambda_read  = N_S / T
lambda_write = N_R / T .
```

GetX is retained as a separate ownership-request count. Treating every GetX as
a data-array write would double-count invalidations; treating every GetX as a
read would overstate data returns. If the generating simulator provides a more
precise transaction classification, extend the trace schema and rerun rather
than changing this mapping silently.

With a 64-byte line and a `B`-bit modeled array word, the number of transfers is

```text
k = ceil(8 * 64 / B),
R = k * N_read,
W = k * N_write .
```

The default `B=64`, hence eight modeled word transfers per cache line.

## 3. Wear and lifetime equations

Let `w(i,j)` be the measured write count of NVM way `j` in set `i`, for `S`
sets and `A` NVM ways. Define

```text
mu       = (1/(S A)) sum_i sum_j w(i,j)
sigma_i  = (1/A) sum_j w(i,j)
InterV   = sample_std_i(sigma_i) / mu
IntraV   = (1/(S mu)) sum_i sample_std_j(w(i,j))
w_max    = max_i,j w(i,j).
```

If each cell endures `E_end` writes and the measured interval lasts `T`, the
first-failure projection under stationary traffic is

```text
L_seconds = E_end * T / w_max .
```

This absolute projection is a sensitivity result, not a measured lifetime. The
direct relative lifetime improvement against baseline `b` is independent of
the chosen endurance:

```text
RLI_direct = w_max,b / w_max,p - 1 .
```

The manuscript's variation proxy is also emitted:

```text
RLI_proxy = (mu_b/mu_p)
            * (1 + InterV_b + IntraV_b)/(1 + InterV_p + IntraV_p) - 1 .
```

Use `RLI_direct` for the primary claim. A large proxy/direct disagreement is a
warning that the closed-form estimator, rather than the observed hottest cell,
is driving the conclusion.

For two independent mechanisms with gains `r1` and `r2`, the correct neutral
composition for a ratio metric is

```text
r_null = (1+r1)(1+r2) - 1,
```

not `r1+r2`. Report residual gain relative to both nulls if the manuscript
retains any interaction claim.

## 4. NVMExplorer and cross-stack equations

For array read/write costs `e_r,e_w` in pJ and physical event counts from the
policy replay,

```text
E_dynamic,pJ = k_NVM (N_NVM,r e_NVM,r + N_NVM,w e_NVM,w)
             + k_SRAM (N_SRAM,r e_SRAM,r + N_SRAM,w e_SRAM,w).
```

For a 12+4 hybrid and full-capacity modeled array leakage powers,

```text
P_leak,hybrid = (12/16) P_leak,NVM + (4/16) P_leak,SRAM
E_leak,mJ     = P_leak,mW * T_seconds
E_total,mJ    = E_dynamic,pJ / 10^9 + E_leak,mJ.
```

The area estimate uses the same linear partition weighting. This is a first
order estimate; it excludes hybrid-bank routing, tag, controller and predictor
area. Report it as “modeled data-array area,” not total LLC area.

Serialized memory-service time is

```text
D_ns = sum_t N_t * latency_t.
```

This is not execution time: it omits memory-level parallelism, queues, core
stall overlap and instruction count. Do not translate it into IPC or speedup.

## 5. Required ten-mix matrix

For every mix, run these controls before a novel design:

| ID | Arena | Policy | Purpose |
|---|---|---|---|
| A0 | all-NVM | unmanaged LRU | homogeneous NVM reference |
| H0 | 12+4 | technology-blind LRU | equal-geometry hybrid control |
| A1 | 12+4 | PSC | physical split ablation |
| A2 | all-NVM and 12+4 | VRO | reordering ablation and portability boundary |
| A3 | 12+4 | PSC-VRO | manuscript design |
| C1–C5 | applicable arena | i2WAP, EqualChance, Start-Gap, Sequoia, SWWR/DWWR | named prior-work reimplementations (the earlier `linovo` entry is withdrawn — see `policies/swwr.py`) |
| N1 | 12+4 | PSC-VRO-Rank | recommended novelty candidate |

Each reimplementation needs a paper-to-code conformance table: original state,
trigger, victim selection, movement semantics, metadata cost and any adaptation
from main memory to LLC. Until that table and at least microtrace invariants are
complete, label results “independent reimplementation,” not “state of the art
implementation.”

Use the first 6 million events only for parameter selection and the remaining
suffix for scoring. Preserve per-mix rows; aggregate ratios with geometric means
and rates with arithmetic means. Also report min/max and a confidence interval
or a paired nonparametric test across ten mixes. A geometric mean alone can hide
one severe regression.

## 6. Recommended novel idea: predictive PSC plus non-restrictive wear ranking

The most credible near-term idea in the current design is `psc-vro-rank`, not a
more complicated remapper. It attacks two measured failure modes independently:

1. **Predictive PSC:** a direct-mapped `q`-bit counter table estimates repeated
   write intensity from block signatures instead of assuming that every dirty
   replacement is equally hot.
2. **Non-restrictive wear ranking:** when a set's coarse-counter range exceeds a
   trigger, choose the least-worn frame only among the `p` least-recently-used
   NVM candidates. All ways remain eligible; this avoids VRO's concentration
   caused by closing ways during an epoch.

For block address `a`, table size `H=2^h`, saturating maximum `2^q-1`, and hash

```text
x(a) = ((a>>6) xor (a>>13) xor (a>>23)) mod H,
c[x] <- min(c[x]+1, 2^q-1) on a dirty replacement,
c[x] <- floor(c[x]/2) every D dirty replacements.
```

The line is predicted write-intensive when `c[x] >= tau` and may be placed in
SRAM subject to

```text
N_steered / N_allocated < rho_max .
```

That cap is essential: without it, an optimizer can produce infinite-looking
RLI by ceasing to use NVM. It is paired with the external hit-rate and minimum
NVM-share guards.

For an NVM set, let `C_j` be a coarse wear counter and let `P_p` contain the `p`
least-recently-used ways. When

```text
max_j C_j - min_j C_j >= delta,
```

select

```text
j* = argmin_{j in P_p} C_j;
```

otherwise use ordinary LRU. On saturation, halve all counters in that set. The
extra state is

```text
State_bits = H q + S A_NVM q_w,
Overhead = State_bits / [S A_total (512 + tag_state_bits)].
```

### Falsifiable novelty gate

Do not choose this idea for the paper because it is new-looking. Advance it only
if the ten mixes satisfy all of these against PSC alone and the best named
competitor:

1. positive geometric-mean `RLI_direct`, with at least 7/10 mixes positive;
2. no mix loses more than 0.5 percentage points of hit rate;
3. NVM write amplification no greater than 1.05 relative to the same-arena
   unmanaged baseline;
4. `selection_guard_passed=1` for every reported row;
5. cross-stack EDP does not regress by more than 2%;
6. metadata below 1% of tag+data storage;
7. improvement survives an unseen parameter setting or leave-one-mix-out
   selection, not only a grid chosen on each evaluated mix.

The last point is important. Per-mix tuning establishes an oracle upper bound.
For an implementable controller, select one global configuration on nine mixes
and test it on the tenth, rotating through all ten.

## 7. Reviewer-critical gaps that remain

- **Device calibration:** replace assumed SOT pulse/energy/resistance/endurance
  with cited values and run low/nominal/high sensitivity points.
- **SOT specificity:** state that heavy-metal geometry and decoupled paths are
  SOT-specific; PSC, prediction, wear ranking and metrics are portable.
- **Baseline fidelity:** complete the conformance tables and validate small
  invariant traces before publishing competitor rankings.
- **Cold start:** the prepared measured region currently starts with an empty
  cache. Quantify a longer warm-up or extend replay to retain warm cache state
  while resetting only counters.
- **Timing model:** trace replay is not a cycle-accurate execution-time model.
  Do not claim IPC/speedup from serialized array latency.
- **Model mismatch:** DESTINY cache mode and NVMExplorer RAM mode are separate
  triangulation points. Differences must be explained, not averaged.
- **Statistics:** report per-mix values and paired uncertainty; do not rely only
  on aggregate means.
- **Reproducibility:** record upstream commit hashes, compiler, Python package
  lock, OS, complete configs, raw trace hashes and every failed model point.
- **Claim hygiene:** remove or recompute any 75% latency, super-additivity or
  inconsistent IntraV claim that is not regenerated from the final CSVs.

## 8. Stop/go decision

Run the smoke test, then one complete real mix with only A0/H0/A1/A3/N1. Inspect
physical counts, selection guards and cross-stack energy. If the data is sane,
run all ten mixes on the baseline matrix. Only after those results are frozen
should the broader novelty grid run. This sequence prevents weeks of simulation
from being spent on a policy whose gain is a metric loophole or trace-mapping
error.
