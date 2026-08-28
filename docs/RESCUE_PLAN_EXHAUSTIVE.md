# Exhaustive rescue plan
## Every failure mode, with the mathematics, the algorithms, the code-level specification, the tests, and the decision rules

**How to use this document.** Each Part is self-contained: a student who has
never seen this repository can implement it from the text alone. Structure of
every Part: *Symptom* (what the failing CSV looks like) → *Diagnosis* (exact
columns to check, with thresholds) → *Remedy* (ranked options, each with
derivation, data structures, pseudocode/real code, tests, and a Definition of
Done). Citations are the ones this repository already carries and verifies
(`config/endurance.json`, `stage1_destiny/cells/sources.json`, policy
docstrings): Mittal, Vetter & Li, *LastingNVCache*, 2014 (STT 4×10¹², PCM 10⁸);
Agarwal, PhD thesis, IIT Guwahati, 2020 (SWWR/DWWR, endurance tables);
Qureshi et al., *Start-Gap*, MICRO 2009; Wang et al., *i2WAP*, HPCA 2013;
Jokar et al., *Sequoia*, IEEE TVLSI 24(3), 2016; Mittal & Vetter, *AYUSH*,
MASCOTS 2015 and *EqualChance*, INFLOW 2014; Zhang et al. 2021 (DESTINY
geometry/scaling baselines); Singh et al. 2020 (SOT/STT cell parameters);
imec press release, 13 Dec 2023 (SOT endurance measurement floor >10¹⁵).
No other citations are used, deliberately: a rescue plan built on references
nobody has checked is a second failure, not a rescue.

---

# PART 0 — Foundations: every formula used later, with its derivation

### 0.1 The wear vector and its two variations

After a replay, each frame (set `s`, way `j`) has a charged write count
`w[s,j]`. Define per-set mean `m_s = (1/W) Σ_j w[s,j]` and global mean
`μ = (1/SW) Σ_{s,j} w[s,j]`.

- **InterV** (inter-set variation) = `std_s(m_s) / μ` — the coefficient of
  variation of *set means*.
- **IntraV** = `(1/S) Σ_s std_j(w[s,j]) / μ` — the mean within-set CV.

**Variance decomposition (law of total variance).** Treat the frame index as
random. Then

```
Var_total(w) = Var_between + Var_within
Var_between  = (1/S) Σ_s (m_s − μ)²          (variance of set means)
Var_within   = (1/S) Σ_s (1/W) Σ_j (w[s,j] − m_s)²
```

This is an exact identity (expand the square, the cross term vanishes because
`Σ_j (w[s,j] − m_s) = 0` within each set). **Why you need it:** it tells you
*which* leveller can possibly help. If `Var_between / Var_total < 0.3`, the
peak frame is an intra-set phenomenon and **no inter-set mechanism
(psc-balance, wear-swap, i2WAP Swap-Shift, Start-Gap) can reduce the peak by
more than a bounded amount** — the remedy must act within the set
(vro-rank, tailguard). Compute it in one line per mix from the per-frame wear
CSVs the ChampSim module writes (`PSC_STATS` wear.csv) or by re-running one
replay with the wear array dumped:

```python
import numpy as np
w = wear.reshape(S, W)                     # frames
m_s = w.mean(axis=1)
var_between = ((m_s - w.mean())**2).mean()
var_within  = w.var(axis=1).mean()
share_between = var_between / (var_between + var_within)
```

**Decision rule used throughout this document:**
`share_between ≥ 0.5` → inter-set remedies; `≤ 0.3` → intra-set remedies;
between → composition (Part 3).

### 0.2 Direct lifetime and RLI, derived

Assume stationary per-frame write rates `r_f` (writes/second) over the
measured window — the replay measures `w_f = r_f · T` over duration `T`.
A frame fails when its cumulative writes reach endurance `E`, so the array's
first failure is at `T_fail = E / max_f r_f`. For two policies on identical
input, the ratio of lifetimes is

```
T_fail(policy) / T_fail(base) = max_f w_f(base) / max_f w_f(policy)
```

(the `E` and `T` cancel). Define `RLI_direct = that ratio − 1`, so 0 means
"same lifetime as baseline". This is why `max_write` is the load-bearing
column and why the proxy's failure (93% median error, 21.6% of 28,680
pairwise rankings reversed, measured in `statistics.json`) matters: the proxy
does not track the max.

**Years to first failure.** With measured duration `T` seconds and peak frame
count `w_max`:

```
Y = E · T / (w_max · 3.156e7)      [years; 3.156e7 s/yr]
```

Unit check: `E` [writes] × `T` [s] / (`w_max` [writes] × [s/yr]) = years.
Linear in `E` — hence the endurance sweep and the standing rule: quote the
decade with every SOT number (imec floor is a lower bound on endurance,
therefore an upper bound on what may be claimed).

### 0.3 Geometric mean for ratio metrics

For per-mix gains `r_i > −1`, define `GM = exp((1/n) Σ ln(1 + r_i)) − 1`.
Why GM and not the arithmetic mean: lifetime gains compose multiplicatively
(a 2× gain on one mix and ½× on another should cancel, and in log space they
do: `ln 2 + ln ½ = 0`), and GM is symmetric under baseline inversion. Guard:
any `r_i ≤ −1` (policy worse than losing the entire array) is a data error —
assert it never happens.

### 0.4 CVaR (tail lifetime)

For damage values `d_1 … d_N` sorted descending, `CVaR_α = mean(d_1 … d_k)`
with `k = ⌈(1−α)N⌉`. It is the expected damage *given* you are in the worst
`(1−α)` tail. The conformance suite already asserts `mean ≤ CVaR ≤ max`.
Report `CVaR_0.99` next to `max`: the max is one frame and noisy; CVaR₉₉ is
the whole tail and stable.

### 0.5 Lognormal endurance bins with unit mean (used by the damage model)

Requirement: multiplicative per-frame endurance factors with `E[X] = 1` and a
chosen CV. If `X = exp(Z)`, `Z ~ N(μ, σ²)`, then `E[X] = exp(μ + σ²/2)` and
`CV² = exp(σ²) − 1`. Solve:

```
σ² = ln(1 + CV²)          μ = −σ²/2
```

Derivation: set `E[X] = 1` ⇒ `μ = −σ²/2`; `Var[X] = (exp(σ²)−1)·exp(2μ+σ²)
= exp(σ²)−1` when `E[X]=1`. The code's clamp at 0.05 prevents a pathological
near-zero-endurance frame from dominating every result; both properties are
already asserted by tests ("bins have ~unit mean", "respect the 0.05 clamp").

### 0.6 Weibull hazard (TailGuard's score)

Weibull failure with shape `κ` and scale `η` has hazard
`h(d) = (κ/η)(d/η)^{κ−1}` at accumulated damage `d`. For `κ > 1` the hazard
grows with damage — placing the next write on the frame with the lowest
hazard minimises the *marginal increase in failure probability*, which is not
the same as least-written (that is the κ=1 special case; the suite asserts
κ=1 ⇒ linear). TailGuard's selection score over the `c` least-recent ways:

```
score(j) = λ_r · h(d_j) + λ_wa · recency(j)
pick argmin score
```

`λ_wa > 0` is mandatory: with pure hazard the degenerate optimum is to hammer
one immortal frame (stated in the policy's own docstring).

---

# PART 1 — PSC-GUARD: the designed fix for the measured lbm17 hit-rate collapse

### 1.1 The measured failure (from `cross_stack_results.csv`, run 2026-08-18)

| mix | scheme | hit_rate_delta | rli_direct |
|---|---|---|---|
| gobmkmilcmcf17lbm17 | psc | **−0.2287** | 4.93 |
| gobmkmilcmcf17lbm17 | psc-vro | **−0.2303** | 2.42 |
| lbm17xz17GemsFDTDwrf | psc | **−0.2459** | 2.70 |
| lbm17xz17GemsFDTDwrf | psc-vro | **−0.2458** | 2.08 |

Both failing mixes contain `lbm17` (SPEC lbm: streaming stencil, sustained
write bandwidth). On the other eight mixes PSC's hit loss is ≤1%, gain
9–52×. The mechanism: PSC sends **every dirty fill** to the K=4 SRAM ways.
Under sustained dirty-fill pressure the SRAM partition becomes 100% dirty
churn; clean read-reuse blocks can never stay resident in SRAM, and the NVM
partition (12 ways) must absorb the entire mixed working set: hit rate
collapses. Note `profile_writes.json` measured burst working set p95 = 21,940
blocks vs 65,536 SRAM frames at 16 MB — *on average* SRAM fits, so the
failure is phase-local: it is the *rate*, not the footprint.

### 1.2 The controller, derived

Let `u(t)` = fraction of SRAM frames currently holding dirty-fill blocks.
Discrete dynamics per allocation event:

```
u ← u + [ a·p_admit(u) − drain(u) ] / (S·K)
```

where `a ∈ {0,1}` is "this allocation is a dirty fill" and `drain` is
evictions of dirty SRAM blocks. Without control (`p_admit ≡ 1`), sustained
`a`-rate above the drain rate saturates `u → 1`, which is the measured
failure. Choose the linear back-off controller:

```
p_admit(u) = 1                    u ≤ θ
           = (1 − u)/(1 − θ)      u > θ        θ ∈ (0,1), default 0.75
```

**Why linear, and why it is stable.** In the saturating regime the admitted
dirty inflow is `λ·p_admit(u)` for offered rate λ. The fixed point solves
`λ·(1−u*)/(1−θ) = δ(u*)` where δ is the drain. Since `p_admit` is strictly
decreasing on `(θ,1)` and hits 0 at `u=1`, a fixed point `u* < 1` exists for
any finite λ: the partition provably cannot saturate. The loop is a
first-order negative-feedback system; its damping is set by measuring `u`
through an EWMA rather than instantaneously:

```
û ← û + (u_now − û) / 2^s          (integer shift, s = 6 default)
```

Time constant = `2^s` allocations. Choose `s` between per-access noise
(needs `2^s ≫ W = 16`) and burst reaction time (needs `2^s ≪` burst length;
measured bursts are thousands of accesses). `s ∈ {5,6,8}` is the sweep.

**Why probabilistic admission and not a hard cap.** A hard cap (`admit iff
occupancy < θ`) creates bang-bang oscillation at the boundary and unfairly
serialises: whichever addresses arrive just after the cap trips are always
rejected. Proportional thinning spreads the rejection uniformly over the
address stream and keeps the marginal behaviour continuous in `u`.

### 1.3 Determinism (non-negotiable)

Replay determinism is asserted for every policy by the conformance suite, so
no RNG. Use the Knuth multiplicative hash on the **block address** (not the
set index — the low bits are the set index and would correlate admission
with sets):

```
h(a) = ((a * 2654435761) >> 16) & 1023        # 10-bit uniform-ish digest
admit ⇔ h(a) < floor(1024 · p_admit(û))
```

Same trace ⇒ same `û` trajectory ⇒ same decisions, bit for bit.

### 1.4 State and hardware cost

One 17-bit occupancy counter (counts dirty SRAM lines, max S·K = 65,536),
one 16-bit fixed-point EWMA register, one 10-bit threshold comparator, one
32-bit multiplier for the hash (shared with existing tag logic in any real
implementation). `state_bits() = 17 + 16 = 33`. Report it: at 16 MB the tag
array is ~1.1 MB, so this is ~4×10⁻⁴ % overhead — quote the number, not the
adjective.

### 1.5 Implementation (drop-in, `stage3_hycsim/policies/psc_guard.py`)

```python
from .psc_vro import PSC

class PSCGuard(PSC):
    """PSC with pressure-adaptive dirty-partition admission.

    Fixes the measured lbm17 failure: PSC loses 23-25% hit rate when
    sustained dirty fills saturate the SRAM partition. Above occupancy
    theta, dirty fills are admitted with probability (1-u)/(1-theta),
    deterministically via an address hash; rejected fills follow PSC's
    NVM path. Costs 33 state bits and zero migration writes.
    """
    name = 'psc-guard'
    PARAMS = dict(theta=0.75, ewma_shift=6)

    def setup(self):
        super().setup()
        self.sram_frames = self.llc.sets * self.llc.sram_ways
        self.dirty_in_sram = 0            # exact occupancy counter
        self.u_hat = 0.0                  # EWMA of occupancy fraction
        self.g_admitted = 0               # instrumentation
        self.g_rejected = 0

    def _admit(self, addr):
        th = self.params['theta']
        u = self.u_hat
        p = 1.0 if u <= th else max(0.0, (1.0 - u) / (1.0 - th))
        return ((addr * 2654435761) >> 16) & 1023 < int(1024 * p)

    def choose_way(self, cs, addr, is_dirty):
        # EWMA update on every allocation decision
        occ = self.dirty_in_sram / self.sram_frames
        self.u_hat += (occ - self.u_hat) / (1 << self.params['ewma_shift'])
        if is_dirty and not self._admit(addr):
            self.g_rejected += 1
            return self._nvm_victim(cs)   # PSC's clean/NVM path
        if is_dirty:
            self.g_admitted += 1
        return super().choose_way(cs, addr, is_dirty)
```

Two integration points need care and are the only real work:
`dirty_in_sram` must be incremented when a dirty block lands in an SRAM way
and decremented when one is evicted or written back clean — find the
eviction path in `llc.py` (`allocate`) and mirror how `elba` hooks
evictions; and `_nvm_victim` is PSC's existing clean-fill branch —
refactor PSC's `choose_way` so the NVM-victim selection is a named method
both can call (do NOT duplicate the LRU scan; duplicated victim logic is how
the `cp -r` class of bug happens). Add stats to `describe()`/final print:
`pg_admitted`, `pg_rejected`, `pg_u_final`.

### 1.6 Tests (add to `tests/test_policy_conformance.py`, same style)

1. **Ablation:** `theta=1.0` ⇒ bit-identical wear vector and hit counts to
   plain `psc` on a 50k mixed stream. (`p_admit` never < 1.)
2. **Anti-saturation:** all-dirty-fill stream (every op L2_Rpl, dirty=1,
   fresh addresses) ⇒ final `dirty_in_sram/sram_frames ≤ theta + 0.1`.
3. **Determinism:** two replays, identical wear vectors (reuse the existing
   determinism harness).
4. **Accounting:** `sum(per-frame writes) == nvm_writes + sram_writes`
   (reuse the invariant test's policy list — add `psc-guard`).
5. **Recovery:** dirty burst then all-clean reads ⇒ `u_hat` decays below θ
   within `4·2^s` allocations and admission returns to 1.0 (check
   `pg_rejected` stops growing).

### 1.7 Wiring and success criteria

`GRID['psc-guard'] = [{'theta': t, 'ewma_shift': s} for t in (0.6, 0.75, 0.9)
for s in (5, 6, 8)]` (9 points); add to `HYBRID_ONLY`; register in
`policies/__init__.py` REGISTRY. **Definition of done:** on both lbm17
mixes, `hit_rate_delta ≥ −0.005` (inside the guard) with `rli_direct > 0`;
on the other eight mixes, wear vector within 2% of plain PSC (θ never
binds). If criterion 1 holds but RLI ≈ 0 on lbm17, the honest sentence is
"under sustained write bandwidth the partition must choose between lifetime
and hit rate; PSC-Guard chooses hit rate and degrades to LRU-equivalent
lifetime instead of collapsing service" — that is still a designed,
measured contribution.

### 1.8 If PSC-Guard itself fails

`pg_rejected = 0` on lbm17 ⇒ occupancy never crossed θ ⇒ the hit loss is
**not** dirty saturation; run the 0.1 variance decomposition and go to
Part 2/3. Oscillating `u_hat` (visible as alternating admit/reject phases in
a debug dump) ⇒ raise `s`. RLI zero everywhere ⇒ θ too low; the grid covers
0.9 for exactly this.

---

# PART 2 — psc-balance triage tree (run this before blaming the mechanism)

All columns named here are already emitted per row of
`hycsim/lifetime_all_mixes.csv` once the scheme runs (instrumentation:
`pb_diversions`, `pb_blocked_cap`, `pb_blocked_floor`, `allocations`).

**Step 2.1 — did it fire?** `fire_rate = pb_diversions / allocations`.
If < 0.005: the hot-set trigger never engaged. Sub-checks: (a) compute the
actual per-set pressure distribution — `p(s) = sc[s]/avg` in the policy; a
`hot_threshold` of 1.02 fires only if some set's EWMA wear exceeds the mean
by 2%; on a workload with genuinely flat inter-set wear (InterV already
< 0.05 under plain PSC) there is *nothing to level* and psc-balance ≡ psc is
the correct, reportable outcome, not a bug. (b) If InterV is high AND
fire_rate ~ 0, the threshold grid is too tight: extend to
`hot_threshold ∈ {1.005, 1.01}` and re-run only this scheme
(`--only hycsim` + `--schemes psc-balance` costs ~1/22 of a full replay).

**Step 2.2 — is a guard binding?** `cap_ratio = pb_blocked_cap /
(pb_diversions + pb_blocked_cap)`. If > 0.5 the SRAM cap is rejecting most
diversions: raise `max_sram_frac` (grid already spans 0.30–0.60; extend to
0.75 only if hit_rate_delta stays clean, because this cap is also the lbm17
protection). Symmetrically `pb_blocked_floor` high ⇒ `min_nvm_frac` too
high.

**Step 2.3 — is the peak intra-set?** Run the §0.1 decomposition on the
baseline (unmanaged) wear vector for the offending mix.
`share_between < 0.3` ⇒ psc-balance is structurally unable to move
`max_write` even while it flattens InterV (this is visible in the data as:
InterV drops, RLI does not). Remedy is Part 3, not more tuning.

**Step 2.4 — report whatever happened.** The three outcomes above are,
respectively: "no inter-set headroom on this workload class",
"guard-limited, quantified", and "intra-set-bound, motivates composition".
Each is a sentence with a number, and each is more credible than an
unexplained mid-pack geomean.

---

# PART 3 — BALANCE-RANK: the composition when neither channel suffices alone

**Claim to build:** inter-set steering (psc-balance) and intra-set victim
ranking (vro-rank) act on *disjoint decisions* — WHICH partition an
allocation goes to, versus WHICH way inside the NVM partition takes it —
so they compose without interference and their gains are approximately
multiplicative when the variance decomposition shows both components matter.

**Specification.** New policy `balance-rank`, state = union of the two
parents' state (no shared variables). Decision order per dirty/clean fill:

```
1. psc-balance logic decides partition (SRAM divert / NVM stay)
   - identical code path, identical counters
2. if NVM: victim = vro-rank selection over the NVM ways
   (least-worn among the `pool` most-LRU ways, firing only when
    imbalance ≥ trigger — dormancy preserved)
   if SRAM: plain LRU within SRAM ways
```

Implementation: subclass PSCBalance; override its NVM-victim step to call a
VRORank instance's selection method (composition by delegation, mirroring
how `full` composes psc_pred + vro_rank + wear_swap in `novel.py` — read
that file first and copy its structure, including how it namespaces the two
parents' stats).

**Mandatory ablation equalities (the test that the composition is real):**
with the rank channel disabled (`trigger = ∞`) the wear vector must equal
`psc-balance` bit for bit; with the balance channel disabled
(`hot_threshold = ∞`, i.e. never hot) it must equal a hybrid-arena
`vro-rank` bit for bit. These are exact-equality asserts, same pattern as
`full`'s PSC_FULL_* channel tests. Grid: cross of each parent's best 2
points from the individual sweeps (4 combos — selected *after* the parents'
results exist, which is why this is Part 3 and not in the main run).
Migration writes must remain 0 (both channels are allocation-time; assert
via the existing accounting invariant).

**Expected-gain arithmetic** (for the paper's analysis section): if the
baseline variance shares are `b = Var_between/Var_total` and `1−b`, and each
channel alone reduces its component's contribution to the peak by measured
factors `g_inter`, `g_intra`, the composed peak reduction is bounded above
by the product — report measured versus this bound; the gap quantifies
channel interference honestly.

---

# PART 4 — Statistics upgrade pack (do this regardless of outcomes)

**4.1 Bootstrap CI on every geomean** (percentile method, mixes are the
resampling unit because they are the independent draws):

```python
import numpy as np
def boot_ci(r, B=10000, seed=20260819):     # r: per-mix gains, len n
    rng = np.random.default_rng(seed)
    n = len(r); logs = np.log1p(r)
    gms = np.expm1([np.mean(logs[rng.integers(0, n, n)]) for _ in range(B)])
    return np.percentile(gms, [2.5, 97.5])
```

n=10 is small; the percentile CI is honest about that (it will be wide).
Never quote a geomean without its interval. (`statistics.py` already has a
bootstrap — extend it to the guarded subsets from the new aggregate columns.)

**4.2 Paired scheme-vs-scheme claims** ("A beats B"): Wilcoxon signed-rank
on the 10 per-mix `log(1+r)` differences, `scipy.stats.wilcoxon(dA, dB)`.
With n=10 the smallest achievable two-sided p is ~0.002; say so rather than
implying more resolution than ten mixes can give.

**4.3 Many schemes ⇒ multiplicity.** Testing ~16 schemes against baseline:
Holm–Bonferroni. Sort p-values ascending; reject `p_(i)` iff
`p_(i) ≤ α/(m−i+1)` and all earlier were rejected. Ten lines of code;
pre-empts the statistical-reviewer objection at zero experimental cost.

**4.4 Ranking agreement across technologies/protocols:** Kendall
`τ = (C−D)/binom(n,2)` over (concordant−discordant) pairs;
`scipy.stats.kendalltau`. Used in Parts 5 and 6 as the single number for
"does the conclusion survive the modelling choice".

**4.5 The AI traces are clustered, not IID — §4.1's plain bootstrap is wrong
for them.** The 31 real AI ChampSim traces (Part 8) are replicate seeds
within roughly eight model families: `llama2.c-stories` at 3 sizes across
several seeds, `bark.cpp` at 5 checkpoint variants, `stable-diffusion.cpp`
at 3 checkpoints, `whisper_trace`/`biogpt.cpp` at 3 seeds each, `vit.cpp`,
`clip_trace`, `rwkv_trace`. Seeds of the same model share architecture,
working-set shape and write pattern class, so their `rli_direct`/`ipc_delta`
values are correlated, not independent draws. Resampling all 31 as if they
were IID (`§4.1`'s `boot_ci` applied to the flat list) treats 31 correlated
observations as 31 independent ones and reports a confidence interval that
is too narrow — a reviewer who understands mixed-effects/clustered-data
methodology will catch this immediately if it ships uncorrected.

*Fix — two-stage nonparametric cluster bootstrap* (Field, C.A. & Welsh, A.H.,
2007, "Bootstrapping clustered data", *Journal of the Royal Statistical
Society Series B*, 69(3), 369–390): resample **families** with replacement,
then resample **within** each selected family with replacement. This is the
standard fix for exactly this failure mode (repeated/panel measurements
where the natural resampling unit is the cluster, not the row) and it costs
nothing on data that is genuinely independent, because a family of size 1
resamples to itself every time — the method strictly generalises the plain
bootstrap rather than replacing it.

Implementation: `stage5_champsim/cluster_bootstrap.py`, function
`cluster_boot_ci(rows, B=10000, seed=20260819)` where `rows` is
`[(family, r), ...]`. `family_of(trace_name)` maps every AI trace to its
model-family token (`AI_FAMILY_TOKENS = ['stable-diffusion', 'llama2',
'whisper', 'biogpt', 'bark', 'vit', 'clip', 'rwkv']`, substring match on the
lower-cased trace name) and every SPEC2017/PARSEC/GAP/XSBench/STREAM trace to
itself, i.e. a singleton family — so `champsim_results.csv` can be run
through one function regardless of which trace subset a row belongs to. Run
it directly against the pipeline's own output:

```bash
python3 stage5_champsim/cluster_bootstrap.py \
    --csv results/suscom_ten_mixes/champsim/champsim_results.csv \
    --metric rli_direct
```

which prints, per policy, `n_traces` (rows), `n_fam` (independent clusters —
this is the number that actually bounds statistical resolution once AI
traces are in the mix, not `n_traces`), and the 95% CI.

*Numerically verified before shipping this recommendation* (not asserted on
authority): synthetic data built with 8 families of realistic size (matching
the real corpus: llama2×9, bark×5, the rest ×2–3, summing to 31), a strong
shared family-level effect (`N(0.30, 0.15)`) and small within-family seed
noise (`N(0, 0.02)`) gives a naive IID bootstrap CI of width **0.0516**
against the correct cluster-bootstrap CI of width **0.1000** — the naive
method understates the interval by essentially **2×** at the same nominal
95% coverage. A second check confirmed the collapse property: run through
16 genuinely independent (singleton-family) synthetic points, `cluster_boot_ci`
reproduces the plain bootstrap CI to the last printed digit
(`(0.2233, 0.3137)` both ways, same RNG seed). Both checks are reproducible
from the docstring in `cluster_bootstrap.py`.

*What to report:* every AI-inclusive statistic in the manuscript states
`n_families` alongside `n_traces` (e.g. "31 AI traces across 8 model
families"), and any CI drawn from the AI subset — or from a pooled
SPEC+AI subset — uses `cluster_boot_ci`, not `boot_ci`. The pure-SPEC/PARSEC/
GAP/STREAM numbers from Parts 0–7 are unaffected (all singleton families) and
need no restatement.

---

# PART 5 — ChampSim disagrees with the replay

**5.1 Row sanity gates before ANY interpretation** (each is one filter on
`champsim_results.csv`): `status == ok`; for swwr/dwwr rows
`wwr_intervals > 0` (else the row is LRU under another name — the module
warns, the docstring documents the 2M-cycle interval maths);
`remapped_writes > 0` wherever `sg_movements` or `seq_associations` > 0
(liveness instrument: remap disconnected = write tax without the mechanism);
`ayush_migrations`, `pb_diversions` etc. non-zero before crediting the
mechanism (a zero-activity row that beats LRU is measuring noise).

**5.2 Pareto front, not averaging.** For each trace, collect points
`(x_i, y_i) = (ipc_delta, rli_direct)` per policy. Policy `i` is admissible
iff no `j` has `x_j ≥ x_i ∧ y_j ≥ y_i` with at least one strict. O(n log n):
sort by `x` descending, sweep keeping the running max of `y`; a point is
dominated iff its `y` is below the running max. Report the front per trace
and the count of traces on which each policy is admissible.

**5.3 The tiebreak scalar** (only after the front):
`LPP = (1+RLI)·(1+ΔIPC) − 1`. Properties worth one manuscript sentence:
zero at baseline by construction; symmetric (a 10% IPC loss cancels a 10%
lifetime-rate gain); monotone in each argument; and equal to the gain in
`lifetime × throughput`, i.e. total useful work served before first failure,
which is the quantity an endurance-limited cache actually maximises.

**5.4 Protocol sensitivity (the deep fix).** The replay charges NO wear on
write hits (HyCSim BaseHybrid semantics, bit-exact-verified); ChampSim's LLC
sees writeback traffic. If rankings disagree, run the replay's *selected*
configurations (≈35 evaluations, not the 300+-point tuning grid) under a
`--charge-write-hits` variant: in `llc.py`, the `o == 2 and way >= 0` branch
(currently only updates recency and calls `on_write_hit`) additionally does
`writes[cs][way] += 1` and increments the partition's write counter when the
flag is set. Keep it default-off so the bit-exact conformance test still
passes; add a second conformance case asserting the flag changes total
writes by exactly the write-hit count. Then Kendall τ between the two
protocol rankings: τ ≥ 0.8 ⇒ one sentence ("rankings are
protocol-robust"); τ < 0.8 ⇒ a subsection reporting both, stating which
published mechanism was designed under which assumption (i2WAP's PoLF and
AYUSH's migration trigger on write hits; PSC keys on dirty fills — the
docstrings already say this).

---

# PART 6 — STT/PCM joins reorder the ranking

Compute Kendall τ between guarded per-scheme geomean rankings under
SOT/STT/PCM joins (the three `cross_stack_results*.csv` now produced).
τ < 0.5 for any pair ⇒ dedicate a results subsection; this is a finding
single-technology studies cannot produce.

**The mechanism-cost inequality that explains reorderings.** A mechanism
that performs `m` extra mechanism-writes per demand write must buy enough
peak-wear reduction to pay for them in lifetime: total writes scale by
`(1+m)` while the peak scales by `1/(1+RLI)`, so it wins iff
`(1+RLI) > (1+m)` *if its extra writes land uniformly* — but on the energy
axis its cost is `m · E_w` per demand write, and `E_w` spans 0.70 nJ (SOT)
to 88 nJ (PCM) in the DESTINY sweep at 16 MB: a 125× multiplier on the same
`m`. So migration-charging schemes (wear-swap, AYUSH, Sequoia's lazy swaps)
slide down the energy-side ranking as `E_w` grows, while zero-migration
schemes (psc family, psc-balance by construction and by test) are invariant.
Verify against the data: regress each scheme's rank change (SOT→PCM) on its
measured `migration_writes/total_writes`; a positive slope confirms the
account and gives the paper its explanatory figure.

---

# PART 7 — DNN/graph study: from "favourable" to "bounded and credible"

**7.1 Break-even read fraction, full algebra.** Per-access energies
`E_r`, `E_w`, leakage `P`, workload of `A` accesses over time `T` with read
fraction `ρ`:

```
E_tech(ρ) = A[ρ·E_r + (1−ρ)·E_w] + P·T
NVM wins  ⇔  E_NVM(ρ) < E_SRAM(ρ)
          ⇔  A[ρ(E_r,N − E_r,S) + (1−ρ)(E_w,N − E_w,S)] < (P_S − P_N)·T
```

Solve for the boundary:

```
ρ* = [ (P_S − P_N)·T/A − (E_w,N − E_w,S) ] / [ (E_r,N − E_r,S) − (E_w,N − E_w,S) ]
```

Every symbol is a column in `nvmexplorer_dnn` output or the trace-profile
CSV. Plot `ρ*` per cell; place each bundled workload at its actual ρ
(weights: ρ = 1 exactly — writes/input = 0 in `dnn_inputs.py`; activations:
compute from the reads/writes arrays in the same file). Workloads right of
their cell's ρ* are NVM wins; the distance is the margin. This single figure
pre-empts the "strawman" objection because the unfavourable region is drawn
on the same axes.

**7.2 The endurance bound at inference rates.** Writes/s to the array
= `writes_per_input × ips` (both in `dnn_inputs.py`). Two bounds bracket
reality: uniform spread, `Y_uniform = E · L / (W_s · 3.156e7)` with `L` =
lines in the array; and single-hot-line worst case, `Y_worst = E /
(W_s,line_max · 3.156e7)`. Report both: for weight workloads `W_s = 0` and
lifetime is unbounded by wear (say exactly that); for activations the two
bounds with the four SOT endurance decades give an 8-number table that
closes the AI-endurance question quantitatively.

---

# PART 8 — AI ChampSim traces: onboarding the actual folder (verified contents)

The folder `/media/kallinath/WD_1_exFAT/SUSCOM-revision/AI_WORKLOADS_CHAMPSIM`
currently holds (verified over the device bridge, 2026-08-20): five loose
traces — `clip_trace_{1,3}.champsimtrace-0NN.gz`,
`rwkv_trace_{1,2,3}.champsimtrace-0NN.gz`, 2.7–3.6 GB each — plus **26
`ML-*.zip` Google-Drive archive parts (~44 GB) that the pipeline cannot see
into.**

- **Step 8.1 — extract the zips in place**:
  `cd <folder> && for z in ML-*.zip; do unzip -n "$z"; done` (needs ~45 GB
  free on the exFAT drive; check first with `df -h`). `-n` never overwrites,
  so a re-run is safe.
- **Step 8.2 — nothing else.** The walker now matches any file containing
  `.champsimtrace` ending `.gz`/`.xz`, recursively, and the config already
  points at this exact path. ChampSim decompresses gz natively.
- **Step 8.3 — screen before the full batch, with one command.**
  `stage5_champsim/screen_ai_traces.py` runs the `lru`-only pass over every
  trace under a root directory, applies the MPKI ≥ 1 / nonzero-write gate,
  and writes both the survivor manifest and the excluded list with reasons —
  no hand-built pandas filter, no silent dropping:

  ```bash
  cd /home/claude/mfs2   # or wherever the revised package was unpacked
  python3 stage5_champsim/screen_ai_traces.py \
      --root /media/kallinath/WD_1_exFAT/SUSCOM-revision/AI_WORKLOADS_CHAMPSIM \
      --outdir results/ai_screen \
      --warmup 20000000 --sim 100000000 --nvm-ways 12 --workers 2
  ```

  This is 31 ChampSim invocations at `--policies lru` (one per real trace —
  the loose `clip_trace`/`rwkv_trace` files plus everything under `AI-ML/`
  once the zips are extracted per 8.1), not 31 × 22; on a modern desktop at
  ChampSim's typical 0.5–2 MIPS that is roughly 1–3 hours total with
  `--workers 2`, not days. It prints a per-trace pass/fail line as it goes
  and, at the end, the excluded traces with their MPKI/write numbers so the
  exclusion is visible immediately, not just in the CSV.

  Then point the full batch at the survivors only, so the expensive 22-policy
  run never touches a trace that cannot show a lifetime or IPC effect. Add
  one key to `config/suscom_ten_mixes.json`'s `experiment` block:

  ```json
  "champsim_ai_traces_manifest": "results/ai_screen/survivors.manifest"
  ```

  `mfs.py`'s `champsim()` checks this key first: when set, it is the
  *authoritative* trace list (the directory walk is skipped entirely), so
  the manifest — a plain text file, one path per line, safe to open and
  hand-edit — is the single source of truth for which AI traces entered the
  paper's numbers. Commit it alongside the config; a reviewer asking "which
  traces did you actually run" gets a file, not a claim. Re-running the
  batch later with the manifest unchanged reproduces the exact same trace
  set even if more files are later dropped into the AI_WORKLOADS_CHAMPSIM
  folder.
- **Step 8.4 — warmup adequacy.** The array holds S·W = 262,144 lines at
  16 MB. Warmup must plausibly fill it: check in each log that warmup-period
  LLC fills ≥ 3× that (≈800k). If not, raise `champsim_warmup` for the AI
  batch (config key, no code). RWKV/CLIP traces at 3 GB are hundreds of
  millions of instructions; the default 20M warmup may be thin — verify,
  don't assume.
- **Step 8.5 — budget.** Each 100M-instruction run at ChampSim's typical
  0.5–2 MIPS is 1–3 h; 5 loose + unzipped traces × 12 policies is
  days-of-compute territory. Screen (8.3) exists precisely to spend those
  hours only where the LLC matters. Run the AI batch as a separate
  invocation (`--only champsim` with a config whose trace list is the
  survivors) so it never blocks the SPEC results.

---

# PART 9 — TailGuard / damage model flat at CV=0

Expected, not a failure: with identical frames, hazard-weighted placement
has nothing to exploit (its own conformance test encodes this: advantage
grows 0% → 24% with endurance variation). The experiment that gives
TailGuard its result: sweep `PSC_ENDURANCE_CV ∈ {0, 0.15, 0.30}`
(`champsim_endurance_cv`; bins per §0.5, seeded, clamp-tested). Success
criterion: `max_damage` and `cvar99` advantages monotone in CV, reported as
"under realistic endurance variation, tail-aware placement buys X% CVaR₉₉
damage headroom" — with §0.4's estimator and the κ-sensitivity from the
grid (κ ∈ {1.5, 2, 3} already added). If flat even at CV=0.30, TailGuard is
reported as a negative result with the κ=1 degeneracy explanation — the
suite's own assertions make that account checkable.

---

# PART 10 — Narrative pivots if headline numbers thin out

Three complete story architectures; pick by what survives, never blend:

- **A. Measurement paper** (if guarded geomeans compress): lead with the
  proxy falsification (93% median error, 21.6% reversals, n_pairs = 28,680),
  the acceptance-gate methodology and the 20%-of-rows finding, the 156×
  OptimizationTarget spread, the SRAM→SOT write-latency crossover at ~4 MB.
  Policies become the evaluation corpus. This paper exists today with zero
  further compute.
- **B. Mechanism paper** (if PSC-Guard/psc-balance deliver on lbm17): lead
  with the write-pressure failure mode as the discovered problem, the
  controller derivation (§1.2) as the solution, cross-technology and
  protocol robustness (Parts 5–6) as the evaluation. Requires the re-run to
  land.
- **C. Cross-stack interaction paper** (if τ across
  technologies/protocols is low): lead with "which wear mechanism wins is
  not a property of the mechanism" — the τ tables, the §6 cost inequality,
  and the per-decade endurance bands. Requires only the joins and the
  τ computations, all cheap.

Decision rule, in order: B if §1.7's Definition of Done holds; else C if
any pairwise τ < 0.5; else A. A is always available because every number it
uses already exists.

---

# Appendix — file-touch map and master checklist

| change | file |
|---|---|
| PSC-Guard policy | `stage3_hycsim/policies/psc_guard.py` (new), `policies/__init__.py`, `tune_and_run.py` (GRID + HYBRID_ONLY) |
| PSC-Guard tests | `tests/test_policy_conformance.py` |
| charge-write-hits switch | `stage3_hycsim/llc.py` (+1 conformance case) |
| balance-rank composition | `stage3_hycsim/policies/` (new, delegate pattern from `novel.py`) |
| bootstrap on guarded columns | `stage4_analysis/statistics.py` |
| Pareto/LPP/τ analyses | new `stage4_analysis/pareto.py` (reads existing CSVs only) |
| ρ*/endurance-bound figures | new `stage4_analysis/dnn_breakeven.py` (reads `nvmexplorer_dnn` output) |
| AI trace screen | config-only (`champsim_ai_traces_root`, thresholds in a note) |

Master Definition of Done: every geomean quoted with a CI (§4.1); every
"A beats B" with a Wilcoxon p (§4.2); every ranking claim with its τ under
at least one modelling perturbation (§4.4, Part 5.4 or 6); every mechanism
row gated by its liveness instrument (§5.1); every SOT lifetime with its
endurance decade; every excluded trace named with its threshold (§8.3).
