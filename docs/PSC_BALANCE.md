# PSC-Balance: the new algorithm, and the measurement that produced it

## 1. The finding that started it

Measuring the whole family on one trace exposed something the manuscript's
composition story had missed. Peak NVM writes, 400k records, 1 MB, 12 NVM + 4
SRAM:

| policy | peak | RLI | hit | intraV | interV |
|---|---|---|---|---|---|
| unmanaged | 33 | — | 0.2219 | 0.306 | 0.097 |
| **psc** | **16** | **+106%** | 0.2218 | **0.041** | 0.087 |
| psc-vro | 18 | +83% | 0.2242 | 0.115 | 0.087 |
| psc-vro-rank | 19 | +74% | 0.2246 | 0.159 | 0.102 |
| psc-pred | 21 | +57% | 0.2255 | 0.203 | 0.104 |
| psc-soft | 22 | +50% | 0.2263 | 0.176 | 0.105 |

**Plain PSC beats every mechanism built on top of it.** Each addition improves
hit rate and makes peak wear *worse*. The family has been trading its headline
metric for a secondary one and reporting it as progress.

The cause is structural, not a tuning accident. PSC steers dirty blocks out of
the NVM partition, and the clean allocations that remain spread across all
twelve NVM ways under LRU — intraV 0.041, close to perfect. VRO then restricts
allocation to a four-way rotating window. On an unmanaged array that window is a
levelling device; **on top of PSC it is a concentrator**, undoing the levelling
PSC already achieved. The two mechanisms fight.

So the remaining headroom is not intra-set. PSC has taken it. Inter-set
variation is 0.087 against a baseline of 0.097 — essentially untouched, by PSC
and by everything else in the table. Peak wear is the worst frame, and the worst
frame sits in the worst set.

## 2. The idea

Every published inter-set leveller in this study — Start-Gap, i2WAP's
Swap-Shift, Sequoia's G-OAS — equalises by **moving data** or **remapping
addresses**, and pays migration writes for it. Measured on this workload:
Start-Gap 1302 migration writes for +0.0% peak-wear improvement, Sequoia 188 for
+0.0%, WearSwap 223 for +0.0%. All cost; none delivers.

PSC-Balance equalises inter-set wear through a channel that already exists and
is free: **the SRAM partition itself.**

> A hot set diverts more of its allocations into SRAM. A cold set diverts fewer.

PSC already diverts dirty blocks. PSC-Balance makes that threshold *per-set and
wear-adaptive*. It is a placement decision taken at allocation: it moves nothing,
remaps nothing, and charges **zero migration writes**. That property is possible
only because the cache is hybrid, which makes this a genuinely hybrid-specific
contribution rather than a uniprocessor wear-levelling technique retrofitted
onto one.

Per set, an 8-bit saturating counter tracks NVM writes; a global exponential
mean tracks the array average. Pressure is `p(s) = sc[s] / max(1, avg)`:

```
p(s) >= hot_threshold   ->  divert to SRAM whether clean or dirty
p(s) <= cold_threshold  ->  keep on NVM even when dirty   (off by default)
otherwise               ->  plain PSC
```

Counters halve array-wide on saturation, the same normalisation i2WAP uses, so
the comparison stays relative and the state stays at 8 bits per set. No
permutation table, no association tables, no per-line state — the levelling is
done by placement, so nothing has to remember where anything went.

## 3. Two guards, both load-bearing

**Incremental diversion cap.** The share of allocations diverted *by this
mechanism, above plain PSC* is held below `max_sram_frac`.

That it caps the *incremental* rate rather than total SRAM occupancy is not a
detail. Capping the total was the first implementation and it was wrong: on a
write-heavy stream PSC's own dirty-steering already exceeds the budget, so the
balance arm was refused on its first attempt and **the mechanism never engaged
at all**. It looked fine on a read-heavy trace and was a total no-op on a
write-heavy one. The conformance suite caught it — exactly the workload-dependent
silent failure this project keeps finding in other people's re-implementations.

**NVM utilisation floor.** A wear-only objective has a degenerate optimum: stop
using the NVM array, drive peak writes towards zero, and score an unbounded
lifetime gain while turning a 16-way cache into a 4-way one. The floor refuses
any diversion that would push NVM below `min_nvm_frac` of allocations. It binds
5,117 times in the headline run. **Without it the reported number would be an
artefact rather than a result.**

## 4. Results

### Replay (HyCSim protocol), 400k records, 1 MB, 12+4

| policy | peak | RLI | hit | interV | migration writes |
|---|---|---|---|---|---|
| unmanaged | 33 | — | 0.2219 | 0.0971 | 0 |
| psc | 16 | +106% | 0.2218 | 0.0868 | 0 |
| **psc-balance** | **9** | **+267%** | **0.2230** | **0.0204** | **0** |

Inter-set variation falls **4.3×**; peak wear falls a further **44% below plain
PSC**; hit rate is slightly *up*, because diverting a hot set's traffic into
SRAM also serves it faster.

### ChampSim (inclusive protocol), 605.mcf, 3M instructions

| policy | NVM writes | interV | IPC |
|---|---|---|---|
| lru | 312,525 | 0.6489 | 1.498 |
| psc | 349,835 | 0.6001 | 1.497 |
| psc-vro | 280,670 | 0.7041 | 1.497 |
| **psc-balance** | **184,064** (−41%) | **0.3989** (−39%) | **1.499** |

125,049 diversions — fully engaged. **The mechanism helps under both protocols**,
which is the cross-simulator validation that matters: nothing in this result
depends on the exclusive-versus-inclusive difference that governs so much else
in this study.

Note also that **PSC alone *increases* NVM writes under ChampSim** (349,835
against LRU's 312,525) — the documented PSC steering defect — and PSC-Balance is
what repairs it.

## 5. The ablation contract

`hot_threshold` high and `cold_threshold` 0 reduces PSC-Balance **exactly** to
PSC — bit for bit on the per-frame wear array, asserted in the conformance suite,
not approximately. Every gain reported above is attributable to the wear-adaptive
steering and nothing else.

## 6. Two honest limits

1. **The peak-wear gain is a small-capacity effect on this trace.** At 4 MB and
   16 MB, PSC already drives peak wear to 5 and 2 writes over the measured
   window, so there is nothing left to take and PSC-Balance ties it. Inter-set
   variation still improves at every capacity (16 MB: 0.184 → 0.116). Two writes
   is below the resolution of the experiment — settling this needs a longer
   trace, not a different mechanism.
2. **The `cold_threshold` arm is off by default and nearly inert when enabled** —
   154 retentions against tens of thousands of hot diversions. It is retained as
   the principled counterpart to the hot arm and costs nothing, but no result
   depends on it and it should not be described as load-bearing.

## 7. How to position it

Lead with the *diagnosis*, not the mechanism. "We show that the published
composition is anti-synergistic: VRO concentrates the wear PSC has already
levelled, and plain PSC outperforms every variant built on it." That is a
checkable negative result about the manuscript's own prior claim, and it
motivates everything after it.

Then the mechanism: inter-set levelling at **zero migration cost**, using the
hybrid partition as the levelling channel — which no prior scheme in the
comparison set can do, because none of them has a second technology to divert
into.

Do **not** claim super-additivity. The composition story is what the measurement
broke.
