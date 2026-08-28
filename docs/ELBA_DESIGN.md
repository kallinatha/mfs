# ELBA — Elastic Boundary Write Absorption

A hybrid SRAM/NVM last-level cache design derived from five measurements made in
this project, not from analogy with prior schemes. Every design choice below
names the measurement that forced it, and every claim has a kill criterion.

---

## 1. The measurement that changes everything

At the last level of a real memory hierarchy, **write-backs hit; they do not
allocate.**

| 619.lbm, ChampSim, 2 MB LLC | accesses | hits | misses |
|---|---|---|---|
| LLC WRITE | 293,488 | 293,487 | **1** |

Almost the entire write stream that wears the NVM array arrives as **in-place
rewrites of blocks that are already resident**. It does not arrive as fills.

Every hybrid placement scheme in the competitor set makes its technology
decision **at fill time** — PSC on the dirty bit, Prediction-Hybrid and
write-intensity predictors on a predicted class, LiNoVo on a reuse-distance
predictor.[^linovo] At fill time the wear-relevant information does not yet exist, and
the wear-relevant event has not yet happened.

Measured consequence for PSC: its "dirty → SRAM" rule fired **119 times in
445,341 allocations**. Its companion rule, "everything else → NVM only", fired
on essentially every fill and routed ~100% of fills into the 12 NVM ways where
plain LRU routes 12/16 = 75%. NVM writes rose 635,490 → 817,131 and peak frame
writes 33 → 42: **−21.4% lifetime against doing nothing.**

## 2. Four more measurements that constrain the design

| # | Measurement | Constraint it imposes |
|---|---|---|
| M2 | μ dominates. EqualChance cut IntraV 12× more than PSC-VRO and lost lifetime by 76 points. | Redistribution cannot beat removal. A mechanism must **remove** NVM writes, not spread them. |
| M3 | Restriction backfires. VRO: −24.2% RLI, write amplification 1.354. | Never make a frame unavailable. Steer, do not forbid. |
| M4 | RLI has a degenerate optimum — stop using NVM and lifetime → ∞ while the cache becomes 4-way. | The objective must be constrained by NVM utilisation and hit rate, not just wear. |
| M5 | Regime dependence is real. VRO helps all-NVM at 16 MB (95 → 74) and hurts hybrid everywhere. | A fixed partition and a fixed policy are wrong. The mechanism must adapt to measured demand. |

## 3. What every existing mechanism does, and what none of them does

| Family | Acts at | Effect on NVM writes | Blind spot |
|---|---|---|---|
| Fill-time placement — PSC, Prediction-Hybrid, write-intensity predictors | allocation | none, because writes are hits | M1 |
| Write-hit swap — AYUSH | write hit ✓ | **spends one NVM write per promotion**: the displaced SRAM victim is written *into* the NVM frame | pays endurance to save endurance |
| Second-write migration — LiNoVo | write hit ✓ | migration | per-set SRAM capacity |
| Wear levelling — i2WAP, EqualChance, Start-Gap, DWAWR | anywhere | redistributes, never removes | M2 |

Two things nobody does.

**(a) Promotion does not have to cost an NVM write.** When a hot block is
promoted out of an NVM frame, AYUSH writes the displaced SRAM resident *into*
that frame. But nothing requires the frame to be refilled. It can simply be
**released**. The displaced SRAM block is the LRU of the SRAM partition — the
coldest thing there — and can go to memory. Promotion then costs **zero NVM
writes** and *frees* an NVM frame, instead of costing one and dirtying one.

**(b) Absorption capacity is locked to set geometry.** A 12:4 way split gives
every set exactly four high-endurance frames, forever. But a write burst does
not distribute itself evenly across sets — the hot-set case, which is precisely
what determines lifetime, concentrates it. A set that needs twenty absorbers has
four; the other 1,023 sets hold four each and use none. The resource is in the
wrong shape.

---

## 4. ELBA

Three mechanisms. Each is separately ablatable, and each has its own kill
criterion.

### 4.1 Promote-and-Release (PR)

On the **second write** to a resident NVM block within a residency window, the
block is promoted to SRAM and **the NVM frame is invalidated, not rewritten**.
The displaced SRAM resident is evicted to memory.

Write accounting per hot block, k writes during one residency:

| | NVM writes |
|---|---|
| Baseline LRU | k |
| PSC (fill-time) | k — the rule never fires |
| AYUSH (swap) | 2 — the fill, plus the victim written into NVM |
| **ELBA-PR** | **1** — the fill only |

This attacks μ, which M2 says is the channel that matters, at the point M1 says
the writes actually are.

*Overlap, stated:* migrating on the second write is LiNoVo's trigger. The novel
part is **releasing rather than backfilling the NVM frame** — halving the
promotion cost and returning a free frame.

### 4.2 Global Write-Absorption Buffer (GWAB)

The SRAM budget is split into two parts: `local_ways` SRAM ways per set as
usual, and a **globally shared, highly-associative absorption buffer** carved
from the remainder of the same budget. Promoted blocks go to the buffer, not to
a set-local way.

Absorption capacity is then **demand-allocated across sets**. A set in a write
burst can hold twenty absorbed blocks; a quiet set holds none. The same SRAM
bits do strictly more work because they are no longer pinned to set geometry.

This is the part that attacks **inter-set** variation, which per-set rotation
provably cannot: the manuscript's own InterV reduction is only 37%, and it
concedes that within-set way rotation cannot move set totals.

*Cost, honestly:* the buffer needs full tags and an associative lookup in
parallel with the set lookup. That is a real critical-path and area cost and
must be reported as such, not waved away. It is bounded: the buffer is small,
and the tag energy is charged in the results.

### 4.3 Elastic Boundary (EB)

`local_ways` and the buffer size are not fixed. The controller measures the
**write-burst working set** — the number of distinct blocks currently receiving
repeat writes — and moves the boundary so that the absorber is just large enough
to hold it.

Estimator: a small counting structure over recently write-hit addresses, decayed
each epoch. Its occupancy is the set point. Grow the buffer while bursts spill;
shrink it while the hit-rate cost exceeds the wear benefit.

The control loop is explicitly two-sided, because M4 says a wear-only objective
has a degenerate optimum: growth stops when measured hit rate falls more than
`ε` below the fixed-split baseline, regardless of what the wear signal wants.

---

## 5. Why this is a different claim from the prior art

| Question | AYUSH | LiNoVo | i2WAP | PSC | **ELBA** |
|---|---|---|---|---|---|
| Acts where the writes are (write hits)? | yes | yes | partly | no | **yes** |
| Removes NVM writes rather than spreading them? | partly | partly | no | intended, fails | **yes** |
| Promotion cost in NVM writes | 1 | migration | n/a | n/a | **0** |
| Absorption capacity follows the burst across sets? | no | no | no | no | **yes** |
| Partition adapts to measured demand? | no | no | no | no | **yes** |
| Objective constrained by utilisation? | no | no | no | no | **yes** |

The defensible novelty claim is **not** "we promote hot blocks to SRAM" — that
is dense prior art. It is: *absorption capacity is a global, demand-allocated
resource whose boundary is controlled by the measured write-burst working set,
and promotion releases NVM frames instead of backfilling them.*

---

## 6. Kill criteria — decided before running

Each mechanism dies independently if its experiment fails.

**PR dies if** promote-and-release does not beat AYUSH's swap at equal SRAM
budget and equal trigger. Experiment: ELBA-PR vs AYUSH, ten mixes, identical
geometry. If the NVM-write reduction does not materialise, the release argument
is wrong.

**GWAB dies if** a global buffer of B entries does not beat B/sets additional
SRAM ways distributed per-set. Experiment: `local=4, gwab=0` against
`local=2, gwab=2×sets` at identical total SRAM. If distributing beats pooling,
set geometry was never the problem.

**EB dies if** any single fixed split wins across all ten mixes. Experiment:
sweep 16:0, 14:2, 12:4, 8:8, 4:12 fixed, against the elastic controller. The
elastic version must track the per-mix best without being told which it is. If
one fixed split dominates everywhere, elasticity is complexity for nothing.

**The whole thing dies if** the hit-rate cost exceeds the lifetime gain under
the stated guard, or if the buffer's tag energy and critical-path cost exceed
the endurance benefit once both are charged.

Report all four outcomes. A negative on GWAB or EB is a publishable finding in
its own right — it would establish that per-set static partitioning is
sufficient, which nobody has actually shown.

---

## 7. What must be measured, not assumed

1. **Is there a write-burst working set at all?** Before any of this is worth
   building, measure the distribution of per-block write counts within a
   residency on the ten mixes. If almost every block is written once, PR has
   nothing to promote and ELBA collapses to LRU. This is the cheapest possible
   experiment and it must be run first.
2. **How much does it move across sets, and how fast?** If the burst set is
   stable and evenly spread, GWAB buys nothing over per-set ways.
3. **Buffer tag cost** from DESTINY/NVSim at the same node, charged in the
   cross-stack join like every other structure.
4. **IPC**, from ChampSim, in the same run as the wear — because the buffer sits
   in the lookup path and an analytical model cannot price that.

---

## 8. Honest risk assessment

**Strongest part:** the elastic boundary. It is the only piece with no close
prior art I can find, it is directly motivated by a measured failure (M5), and
its kill criterion is clean.

**Weakest part:** promote-on-second-write overlaps LiNoVo enough that it cannot
carry a novelty claim alone. It earns its place as the mechanism that makes the
other two work, not as the contribution.

[^linovo]: Every "LiNoVo" in this document refers to the *published* Work-1
    mechanism of Agarwal & Kapoor, ISVLSI 2020 (dataless entries + second-write
    migration + RDAWIP), as a point of comparison in the literature. **It is not
    implemented in this framework.** The `linovo` policy that once existed has
    been deleted because it implemented something else entirely; see
    `stage3_hycsim/policies/swwr.py`. Nothing in the ELBA argument depends on a
    LiNoVo measurement.

**Biggest threat:** experiment 7.1. If the ten mixes show little write
recurrence at the LLC, the entire design has no fuel. That is a real
possibility — the LLC filters temporal locality by construction — and it is why
that measurement comes first, before a line of controller code is written.

**Second threat:** the buffer's associative lookup. If it lands on the critical
path, the IPC cost may swamp a lifetime gain that only matters over years. The
mitigation is that the buffer is consulted in parallel with the set lookup and
sized to stay small, but this must be shown, not assumed.

---

## 9. First measurement of ELBA — and what it kills

Run before writing any of this section: 4 M accesses of the HyCSim SPEC trace,
1 MB LLC, 12 NVM + 4 SRAM, against the same baselines.

| Config | peak writes | μ | NVM writes | hit rate |
|---|---|---|---|---|
| unmanaged (LRU) | 173 | 109.4 | 1,343,954 | 0.3671 |
| **psc-soft** | **110** | **66.1** | **811,679** | 0.3756 |
| ELBA, buffer 2048, elastic | 168 | 108.0 | 1,327,305 | 0.3995 |
| **ELBA, buffer 8192** | 166 | 100.7 | 1,237,869 | **0.4488** |

**The primary claim fails at this capacity.** ELBA was designed to cut peak wear
by absorbing repeat writes. It cuts peak wear by 4%. psc-soft, which does
nothing clever and merely steers fills without banning clean blocks from SRAM,
cuts it by **36%**.

The profiler said why, before the run: 30.3% of write *traffic* is repeat, but
**97.7% of residencies are written exactly once**. The repeats concentrate in a
small number of very hot blocks. ELBA promotes those (54,818 promotions) and
removes their traffic — but the *hottest frame* is not hot because of repeat
writes to one block. It is hot because of **fill traffic** from many different
blocks passing through it. Peak wear is a fill-channel phenomenon at this
capacity, and PR does not touch the fill channel.

**PR is therefore killed as a lifetime mechanism at 1 MB**, on its own stated
criterion. It should not be presented as one.

**GWAB and EB survive, but as something else.** Demand-allocated absorption
raises the hit rate from 0.3671 to **0.4488 — 8.2 points**, the largest
performance effect measured anywhere in this project, at 8,192 buffer entries.
That is a real result and a large one. It is a *performance* contribution
arising from capacity that follows demand across sets, not a lifetime one.

**Implementation note found by the same run:** with the write counter
initialised at 1, `promote_on=1` and `promote_on=2` are identical — both runs
produce byte-identical numbers. The threshold is not the lever; buffer capacity
is. The counter semantics need tightening before the parameter is swept.

### Revised position

The two mechanisms attack **different channels and are orthogonal**:

| | fill channel | repeat-write channel | capacity shape |
|---|---|---|---|
| psc-soft | **yes** — steers fills, does not ban clean blocks from SRAM | no | per-set |
| ELBA | no | yes | **global, demand-allocated** |

Neither is the answer alone. The composite worth testing on the ten mixes is
**psc-soft's fill steering plus ELBA's global absorption buffer**: one attacks
the channel that sets peak wear, the other the channel that sets hit rate. Their
combination is the next experiment, and unlike PSC+VRO there is a mechanistic
reason to expect them to compose — they act on disjoint events and disjoint
resources.

### What this says about the paper

Do not write ELBA into the manuscript as a lifetime mechanism. Either report it
honestly as a performance result with a modest wear side-effect, or hold it for
the follow-on paper where the composite can be evaluated properly. The R3
revision does not need it: the technology comparison and the corrected competitor
set are what the AE asked for, and psc-soft is the better-evidenced lifetime
story.
