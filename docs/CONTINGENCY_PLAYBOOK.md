# Contingency playbook: every way the results can go wrong, and the ranked rescue path for each

Audience: an implementer who has never seen this codebase. Every algorithm here
is specified to coding level — data structures, update rules, parameter
defaults, verification tests, and the exact file to modify. Citations are the
ones already carried in this repository (`config/endurance.json`,
`stage1_destiny/cells/sources.json`, policy docstrings); no citation below is
new or unverified.

Notation used throughout:

- `S` sets, `W` ways, `M = nvm_ways`, `K = sram_ways`, 64 B lines.
- `w_f` = charged writes to frame `f` (a frame is one (set, way) slot).
- Direct lifetime gain: `RLI_direct = max_f w_f(baseline) / max_f w_f(policy) − 1`.
- Inter-set variation `InterV` and intra-set `IntraV` as in `stage3_hycsim/metrics.py`.
- Years to first failure at endurance `E` writes/cell:
  `Y = E · T_measured / (max_f w_f · 3.15e7)` — linear in `E`, which is why
  endurance is swept, never point-estimated (imec 2023 measurement floor
  >1e15 for SOT; community-standard 4e12 for STT via Mittal, Vetter & Li,
  LastingNVCache 2014; both quoted with caveats in `config/endurance.json`).
- Geometric mean over mixes for any ratio `r_i`: `GM = exp(mean(log(1 + r_i))) − 1`.

---

## Scenario 1 — psc-balance disappoints on the real mixes

**Failing looks like:** `rli_direct_gm_guarded` for `psc-balance`/hybrid below
plain `psc`, or guard failures on the same mixes as PSC.

**Diagnose first (30 minutes, no code):** open
`results/suscom_ten_mixes/hycsim/lifetime_all_mixes.csv`, filter
`scheme == psc-balance`. Check three columns per mix: `pb_diversions` (0 means
the hot-set trigger never fired — thresholds wrong, not mechanism wrong),
`pb_blocked_cap` (large means the SRAM cap is binding — raise `max_sram_frac`
grid), `interv` vs psc's (if InterV fell but max_write did not, the peak frame
is intra-set, not inter-set, and no inter-set leveller can fix it — go to
Rescue 1B).

### Rescue 1A — PSC-Guard: pressure-adaptive dirty-partition cap (new, small)

The measured failure on the two `lbm17` mixes is PSC losing 23–25% hit rate:
the dirty partition saturates and evicts the clean working set. The static
policy admits every dirty fill to SRAM; the fix is a feedback controller on
SRAM dirty occupancy.

State per cache (not per set): one EWMA `u` of SRAM dirty occupancy fraction,
updated on every allocation: `u ← u + (occ − u)/2^s` with `s = 6`, where
`occ = dirty_sram_lines / (S·K)` maintained incrementally (O(1): increment on
dirty fill to SRAM, decrement on eviction/clean-back).

Control law (derivation: we want the admission probability to fall linearly
once occupancy exceeds a knee `θ`, reaching zero at saturation):

```
p_admit(u) = 1                      if u ≤ θ
           = (1 − u) / (1 − θ)      if u > θ          (θ default 0.75)
```

On a dirty fill, admit to SRAM if `hash(addr) mod 1024 < 1024·p_admit(u)`
(deterministic hash, not RNG — replay must stay deterministic, a property the
conformance suite already asserts for every policy). Rejected dirty fills go
to NVM under plain PSC victim choice.

Implementation: ~40 lines as a subclass of `PSC` in
`stage3_hycsim/policies/psc_vro.py`; register as `psc-guard`; grid
`θ ∈ {0.6, 0.75, 0.9}`. Storage cost: one 16-bit counter plus one EWMA
register — report it in `state_bits()`.

Verification tests to add (pattern-match the existing suite): (i) with an
all-clean stream it is bit-identical to PSC; (ii) with an all-dirty stream,
SRAM dirty occupancy never exceeds `θ + slack`; (iii) determinism replay.
Expected result: on lbm17 mixes, hit-rate delta recovers to within the 0.5%
tolerance while retaining part of PSC's lifetime gain; on the other eight
mixes it is PSC to within noise (because `u` never crosses `θ`).

### Rescue 1B — if the peak frame is intra-set: compose, do not level

If diagnosis shows `IntraV` dominates, the correct composition is
psc-balance's inter-set channel on top of `vro_rank`'s intra-set channel
(rank, never restrict — the repository's own re-specification of VRO). That
composition already exists as independent code paths; add a grid entry
combining both parameter sets (mirror how `full` composes three channels, and
reuse `full`'s ablation-equality test discipline: with the inter-set channel
off it must equal `vro_rank` bit for bit).

### Rescue 1C — honest fallback

psc-balance's conformance-verified property (charges zero migration writes,
reduces InterV) is still a contribution even at modest geomean; report it per
write-intensity class (split mixes by charged-write rate above/below median)
rather than as one number. No code needed; one pandas groupby.

---

## Scenario 2 — the guarded PSC geomean collapses

**Failing looks like:** `rli_direct_gm_guarded` for PSC drops far below 14.8×
once guard-failed mixes are excluded.

This is not a disaster; it is the paper's honesty mechanism working. The
story becomes: *headline lifetime claims in this literature are inflated by
exactly the hit-rate-collapse pathway our gate detects; we quantify the
inflation.* Compute both columns for every published competitor too — if
their numbers also shrink, the gate is a levelling contribution, and the
proxy-falsification result (93% median error, 21.6% rank reversals, already
measured) becomes the paper's methodological core. Add a Wilcoxon signed-rank
test across the ten per-mix RLI values between guarded and unguarded
aggregates (scipy.stats.wilcoxon, n=10 pairs) so the difference is a tested
claim, not an impression.

---

## Scenario 3 — ChampSim contradicts the replay (PSC costs IPC)

**Failing looks like:** `ipc_delta` strongly negative for the schemes the
replay favours.

Do not average the two objectives away. Report the Pareto front: for each
(policy, trace), plot `(ΔIPC, RLI_direct)`; a policy is *admissible* if no
other policy dominates it on both axes. Define the single-number tiebreak
only after the front: `LPP = (1 + RLI_direct) · (1 + ΔIPC) − 1`
(lifetime-performance product; both factors are ratios to the same LRU
baseline from the same run, so the product is dimensionless and symmetric).
The replay/ChampSim disagreement itself is reportable: HyCSim's protocol
charges no wear for write hits while ChampSim's non-exclusive LLC sees
writeback traffic, so mechanisms keyed on dirty fills legitimately rank
differently — state which protocol favours which mechanism and why
(`docs/POLICY_COVERAGE.md` already carries the per-policy caveats).

Also check `wwr_intervals > 0` on every swwr/dwwr row before believing it
(interval clocked in processor cycles; a short run leaves the whole cache
unrestricted and the row is LRU under another name — the module prints a
warning, the check is one grep).

---

## Scenario 4 — STT/PCM joins reorder the policy ranking

**Failing looks like:** the best policy under SOT is mid-pack under STT.

This is a *finding*, not a failure — policy×technology interaction is exactly
what single-technology studies cannot see. Quantify it: Kendall τ between the
policy rankings under each technology pair (scipy.stats.kendalltau over the
guarded per-mix geomeans). τ < 0.5 justifies a manuscript section: write
latency asymmetry changes which mechanism pays for itself (PCM at 301 ns /
88 nJ writes in our DESTINY sweep punishes every migration-charging scheme;
SOT at 1.1 ns forgives them). The formula connecting them: a mechanism that
adds `m` migration writes per demand write breaks even when
`m < (E_policy_gain · T) / (E_write · N_writes)` — evaluate per technology
from the join's own energy columns.

---

## Scenario 5 — the DNN/graph study is trivially favourable

**Failing looks like:** NVM wins every DNN row because weight inference is
read-only, and a reviewer calls it a strawman.

Report the break-even write fraction, not just the win. Total energy at read
fraction `ρ`: `E(ρ) = A·[ρ·E_r + (1−ρ)·E_w] + P_leak·T`. NVM beats SRAM when
`ΔP_leak·T > A·(1−ρ)·(E_w,NVM − E_w,SRAM) + A·ρ·(E_r,NVM − E_r,SRAM)`;
solve for `ρ*` per cell from the NVMExplorer columns already produced. Then
place each bundled workload (ResNet-26/50 weights ρ=1; activations ρ≈0.5;
ALBERT variants as shipped in `data/workload_data/dnn_inputs.py`) on that
axis. The activations rows are the stress case — if NVM still holds there,
the claim is strong; if not, `ρ*` is the honest boundary and endurance at
60 fps write rates (`writes/s · 3.15e7 · T_years ≤ E`) gives the lifetime
boundary on the same plot.

---

## Scenario 6 — DPC AI ChampSim traces show weak LLC sensitivity

Screen before simulating: run each candidate trace once under `lru` only and
keep traces with LLC MPKI ≥ 1 and nonzero writeback rate (both scraped by the
existing PATTERNS regexes). Report the screened-out traces in the config note
— silent trace selection is the thing this repository's own docs warn
against. Then run the full policy set only on the survivors.

---

## Scenario 7 — reviewers attack the HyCSim zero-wear-on-repeat protocol

The 77% repeat-write share means protocol choice is material. Cheap
sensitivity: a `--charge-write-hits` switch in `llc.py` that increments
`writes[cs][way]` on the `o == 2, way >= 0` path (one line, off by default,
bit-exact conformance preserved when off). Re-run the replay's *selected*
configurations only (35 evaluations, not the full tuning grid) under the
variant and report how rankings move. If they are stable, the protocol
concern is closed with data; if not, both protocols are reported and the
manuscript states which published mechanism assumed which.

---

## Scenario 8 — tailguard/damage results are flat

TailGuard only differentiates under endurance *variation*; with CV = 0 every
frame is identical and hazard-weighted placement equals LRU-adjacent
behaviour (its own conformance test asserts the advantage grows with
variation: 0% → 24%). Sweep `PSC_ENDURANCE_CV ∈ {0, 0.15, 0.3}`
(`champsim_endurance_cv` in config; damage bins are lognormal with unit mean,
σ from CV, clamped at 0.05, reproducible from the seed — all asserted by
existing tests). Report years-to-failure under CVaR₉₉ rather than max:
`CVaR_α = mean of the worst (1−α) fraction of frame damages` — the suite
already asserts `mean ≤ CVaR ≤ max`. The claim becomes: under realistic
process variation, tail-aware placement buys X% CVaR lifetime — a novel,
defensible framing the flat CV=0 result cannot touch.

---

## Priority if several fail at once

Guard-split honesty (Scenario 2) first — it is prose plus one test and makes
every other number credible. Then 1A (PSC-Guard) because it converts the
paper's worst measured result into a designed fix with a control-law
derivation. Then 4 and 5, which turn breadth gaps into findings. 7 and 8 are
armour: do them if reviewer bandwidth allows, cite them as available
sensitivity otherwise.
