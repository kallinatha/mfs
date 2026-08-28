# Proposed variants — exploratory design rationale and status

> **Legacy snapshot:** numerical results below were produced before the final
> non-overlapping evaluation suffix and cross-stack validity gates. Treat them
> as hypothesis-generating only; use the protocol in
> `INTEGRATION_AND_EXPERIMENT_PROTOCOL.md` for the paper decision.

Every design below was derived from a measurement, not from analogy. The
measurements are in `results/lifetime_1MB.csv` and `results/tuning_*.csv`, all
produced before any of these mechanisms was written.

## What the measurements said

| Observation | Evidence |
|---|---|
| The μ channel dominates lifetime | PSC cut μ from 829.8 to 425.5 and peak writes from 1224 to 651 → 88.0% RLI. Nothing that leaves μ alone came within 75 points. |
| Restriction backfires | VRO alone: −24.2% RLI on the hybrid array, write amplification 1.354. Confining writes to (M−R) of M ways concentrates them. |
| Non-restrictive steering is nearly free | EqualChance cut IntraV 5.56% → 0.43% at 1.008× write amplification, purely by choosing *which* way, never forbidding one. |
| IntraV is not lifetime | EqualChance won IntraV by 12× over PSC-VRO and lost lifetime by 76 points. |

Design consequence: **keep the steering, drop the restriction, and find a better
signal for μ than the dirty bit.**

---

## 1. VRO, re-specified — "Variation-Ranked Ordering"

Same name, different mechanism, reported as a change rather than as the original.

**Original:** designate (M−R) of M NVM ways as write-ways; rotate the window on a
saturating counter. During any epoch two-thirds of the partition is closed to
writes.

**Re-specified:** no way is ever closed. When a set's wear imbalance exceeds a
threshold, the victim is chosen by wear *from within the set of LRU-plausible
victims* — the `pool` least-recently-used NVM ways. Otherwise plain LRU runs
untouched.

**Why the pool matters — and what went wrong first.** The first version picked
the globally least-worn way in the set. It cost **8.2 points of hit rate** on the
multi-mix test, because recency was being ignored and live blocks were evicted to
protect cold frames. That configuration was caught and rejected by the tuning
guard rather than reported. Restricting the wear decision to ways LRU already
considers disposable fixes the mechanism: the victim is always an LRU-plausible
one, and wear still migrates because *which* plausible victim gets taken is
decided by wear.

It is also cheaper in hardware than the original. Recency order already exists in
the replacement state; the added work is a minimum over `pool` coarse counters,
not a rotation register plus a write-way mask.

**Cost:** `bits`-bit saturating counters per NVM frame, halved on saturation.
Reported per configuration as `state_overhead_pct`.

---

## 2. PSC-Pred — write-intensity prediction instead of the dirty bit

**Observation:** the dirty bit says *this writeback is a write*, not *this address
is repeatedly written*. A block written once and read many times gets steered
into the small SRAM partition for no lifetime benefit, evicting something hot.

**Mechanism:** a direct-mapped signature table of saturating counters, indexed by
a hash of the block address, incremented on each dirty writeback and periodically
halved so the prediction tracks phase behaviour. An address at or above
`threshold` is steered to SRAM.

**Guard against the degenerate optimum.** RLI rewards *not writing to NVM at
all*. The first version of this policy discovered exactly that: it drove μ to
0.0 and peak NVM writes to 1, scoring an enormous lifetime gain while turning a
16-way cache into a 4-way one and dropping hit rate 3.3 points. Two fixes:

- a cap on the share of allocations that may be steered (`max_sram_frac`), so
  the NVM partition keeps carrying capacity;
- a hit-rate and NVM-utilisation constraint in the tuning search itself, so a
  configuration that wins RLI by abandoning the NVM array is rejected before it
  can be reported.

**This is a finding about the metric, not just about the policy.** Any lifetime
comparison scored on RLI alone can be won by under-using the array. The
constrained search is now part of the framework and every rejected configuration
is printed. It belongs in the paper's methodology section — no prior
wear-levelling paper states the constraint.

**Cost:** one table for the whole cache (`entries` × `bits` bits), not per set.

---

## 3. PSC-VRO-Rank — the two combined

PSC-Pred decides *which partition*; VRO-Rank decides *which way inside the NVM
partition*. They act on disjoint decisions, so neither can starve the other the
way PSC and window-restricted VRO do — where PSC drains the NVM partition and VRO
then concentrates what little is left into a third of the ways.

Whether they actually compose positively is an empirical question the ablation
answers, against both the additive and the multiplicative null. The framework
prints both nulls for the original pair *and* this pair, so the comparison is
symmetric.

---

## 4. WearSwap — measured inter-set levelling

**Observation:** Start-Gap and i2WAP's Swap-Shift rotate the set mapping blindly.
Measured, both cost more in migration writes than they return: Start-Gap came in
at −3.7% RLI with 1.064× write amplification on the hybrid array.

**Mechanism:** coarse per-set wear counters; every `interval` NVM writes, the
hottest set found in a sampled window of `sample` sets is swapped with the
coldest, and the migration writes are charged to the destination frames. Sampling
keeps the search cost constant instead of O(sets).

---

## 5. Full — all three channels

Prediction-based steering, ranked intra-set placement, measured inter-set
swapping. Included so the marginal value of the third channel is visible rather
than assumed.

---

## Results so far

134 configurations searched per capacity on a held-out 6 M-access prefix, under
the hit-rate and NVM-utilisation guard. **1 MB LLC, search phase, RLI measured
from peak frame write count:**

| Scheme | Arena | RLI_direct | Note |
|---|---|---|---|
| **psc-vro-rank** | hybrid | **+44.4%** | proposed |
| full | hybrid | +43.5% | proposed, adds inter-set channel |
| psc-pred | hybrid | +36.3% | proposed |
| psc | hybrid | +29.1% | manuscript, split only |
| ~~linovo~~ (withdrawn; policy deleted, see `policies/swwr.py`) | hybrid | +25.8% | Agarwal & Kapoor 2020 |
| **psc-vro** | hybrid | **+23.9%** | manuscript, as published |
| equalchance | all-nvm | +10.7% | Mittal & Vetter 2014 |
| vro-rank | all-nvm | +7.4% | proposed |
| vro | all-nvm | +3.3% | manuscript |
| i2wap | all-nvm | +1.6% | Wang et al. 2013 |
| wear-swap | hybrid | +1.2% | proposed |
| startgap | hybrid | −3.6% | Qureshi et al. 2009 |
| vro | hybrid | −25.6% | manuscript |

**The redesign works.** PSC-VRO-Rank at +44.4% beats the published PSC-VRO at
+23.9% by 20.5 points, and beats PSC alone by 15.3 points. The mechanism change
— rank inside the LRU victim pool instead of restricting to a rotating write
window — is what moved it.

**Two honest negatives in the same table.**

1. `full` (+43.5%) is *below* `psc-vro-rank` (+44.4%). The inter-set channel adds
   nothing once the other two are in place. Report WearSwap as a negative result
   and drop it from the proposal; keeping a component that does not earn its
   storage is the kind of thing Reviewer 1 has already objected to twice.
2. VRO-Rank does **not** dominate the original VRO everywhere. On the all-NVM
   array at 16 MB, where intra-set variation is high, the original restrictive
   VRO reaches peak writes of 74 against VRO-Rank's 90. Restriction wins in that
   regime because it forces writes into cold ways that LRU would never offer.
   The claim that survives is regime-specific, not universal.

The guard also earned its place: it rejected the (since-withdrawn) `linovo`
policy and VRO configurations that
scored well on RLI while dropping the hit rate from ~0.56 to ~0.31. Those would
have been reported as wins by any RLI-only search.

## Status

| Variant | Implemented | Search phase | Full-trace phase |
|---|---|---|---|
| vro-rank | yes | done (rev. 2 after 8-point hit-rate failure) | running |
| psc-pred | yes | done (rev. 2 after degenerate optimum) | running |
| psc-vro-rank | yes | done — best of all schemes at 1 MB | running |
| wear-swap | yes | done — no measurable benefit | running |
| full | yes | done — no gain over psc-vro-rank | running |

The full-trace phase re-runs each winning configuration on all 37.35 M accesses;
16 MB is still in its search phase. Final numbers land in
`results/lifetime_innov{1,16}.csv` and `results/ablation_innov{1,16}.json`.

The two revisions documented above are why the search phase is not the last word:
both first versions looked excellent on a short run and were wrong for different
reasons.

---

## Running it on the ten mixes

```bash
# one-off conversion, parallel across mixes; produces mmap-able per-mix dirs
./prep_all_mixes.sh /data/raw_traces /data/prepared 4

# full comparison across all mixes
python3 stage3_hycsim/run_mixes.py \
    --traces-dir /data/prepared --outdir results/mixes \
    --size-mb 16 --workers 4
```

At 3 GB per mix the text traces become roughly 90 M records each. Prepared as
`.npy` directories they are about 1 GB on disk and are opened with
`mmap_mode='r'`, so a worker's resident memory stays flat no matter how long the
trace is. Mixes run in parallel; schemes run serially inside a mix, which keeps
peak memory at (workers × working set) rather than (workers × schemes).

Outputs:

- `lifetime_<mix>.csv` — every scheme on that mix
- `tuning_all_mixes.csv` — every configuration tried, with an `eligible` flag
- `lifetime_all_mixes.csv` — all mixes, one row per scheme per mix
- `aggregate.csv` — cross-mix summary; geometric mean for ratio metrics
  (lifetime, write amplification), arithmetic mean for rates, plus per-scheme
  min and max across mixes so the spread is visible rather than hidden behind a
  mean

Tuning happens once per mix on that mix's prefix, so no scheme is ever tuned on
the data it is scored on.
