# Final check and run plan

Read this before starting the ten-mix run. It states the order, what each stage
decides, what must be true before it starts, and how each result maps to a
reviewer demand.

---

## 0. Position: what this revision claims

Three claims, in descending order of how well the evidence supports them.

**Claim 1 — the technology comparison (strong).** SOT-MRAM against SRAM,
STT-MRAM and PCM through one model, 1–32 MB, HP and LOP. This is the AE's gate
and the data already favours you decisively on write latency. It is the safest
part of the paper.

**Claim 2 — the framework (defensible if renamed).** A cross-layer *evaluation
flow*, not an optimiser. It has no joint objective, no search, no Pareto
frontier, so calling it an optimiser invites a reviewer to ask for one. Call it
an evaluation flow and the claim holds.

**Claim 3 — the lifetime mechanism (needs the run to settle).** PSC and VRO as
published did not survive independent measurement: on ChampSim with real SPEC
CPU2017 traces they made peak wear *worse* than LRU. The repaired variants
recover and exceed it. Which version you can claim depends on what the ten mixes
say, and that is the point of running them.

**Do not claim:** "first", "optimal", "super-additive", or the 60% / 75% figures
as universal. The composition measured sub-additive, and the 75% latency number
does not reproduce.

---

## 1. Order of execution, and why

### Stage 0 — write-recurrence profile (thirty minutes, run it first)

```bash
python3 stage3_hycsim/profile_writes.py <prepared>/MIX1 --size-mb 1  --out results/profile_mix1_1mb.json
python3 stage3_hycsim/profile_writes.py <prepared>/MIX1 --size-mb 16 --out results/profile_mix1_16mb.json
```

Every promotion-based scheme in this paper — PSC, AYUSH, ELBA — and every
write-hit-triggered wear-leveller (Sequoia's G-OAS and WAD, i2WAP's PoLF,
SWWR/DWWR's redirection) assumes
a meaningful share of blocks are written **more than once while resident**. The
LLC filters temporal locality by construction, so that assumption can simply be
false, and if it is, the whole family reduces to LRU with extra hardware. One
pass over the trace settles it before anything expensive starts.

Measured here on 4 M accesses of one mix:

| | 1 MB | 16 MB |
|---|---|---|
| repeat-write share | 30.3% | 64.3% |
| residencies written exactly once | 97.7% | — |
| burst working set p95 vs SRAM frames | 9,461 vs 4,096 (2.31×) | 28,398 vs 65,536 (fits) |
| hottest set, burst blocks vs SRAM ways | 14 vs 4 | — |

Read those two rows together, because they say opposite-looking things. A third
of write *traffic* is repeat, but it is concentrated in 2.3% of residencies. So
peak wear at small capacity is **fill-driven, not repeat-write-driven**, and no
promotion scheme can touch it. That is the result that killed ELBA's lifetime
claim (§ ELBA below); it would have killed it after three weeks of running
instead of thirty minutes had this stage come later.

If `verdict()` prints KILL on your mixes, stop and tell me — the manuscript's
mechanism section needs rewriting before the run, not after.

### Stage 1 — DESTINY and NVSim, all technologies, 1 MB to 32 MB

```bash
python3 mfs.py doctor  --config config/suscom_ten_mixes.json
python3 mfs.py destiny --config config/suscom_ten_mixes.json
```

Answers the AE's gate. Five cell files ship: SRAM, STT-MRAM at the tool default
6 F² *and* an area-consistent 40 F², SOT at 12 F² and 24 F², PCM.

**Run both STT points.** DESTINY's shipped STT cell declares 6 F² with an 8.5 F
access transistor, which cannot physically fit. Reporting only that point means
a reviewer can say the competitor was handicapped; reporting both settles it.

NVSim runs underneath NVMExplorer in stage 2, so you get the independent second
opinion automatically. **Do not pick the tool that flatters the result.** Report
both and treat disagreement as a finding — the 1 MB point already disagrees with
the manuscript (DESTINY makes SOT *slower* than SRAM on raw read latency).

*Already complete here:* all 85 design points, in `stage1_destiny/results.csv`.
Re-run only if you change cells or nodes.

**Watch for:** PCM's leakage is erratic across capacity (10,384 mW at 1 MB,
1,291 mW at 2 MB) because DESTINY selects different array organisations. Pin the
bank/mat organisation before publishing that row. The PCM-is-unsuitable
conclusion rests on write latency (301 ns) and does not depend on it.

### Stage 2 — NVMExplorer, iso-capacity and iso-area, AI workloads

```bash
python3 mfs.py nvmexplorer --config config/suscom_ten_mixes.json
```

NVMExplorer ships the workload traffic your manuscript already uses: ResNet and
ALBERT (DNN), Facebook and Wikipedia BFS (graph), SPEC CPU2017. Iso-capacity is
N MB against N MB; iso-area is N MB SRAM against 2N MB NVM.

**State which comparison each number belongs to.** Mixing an iso-area result
into an iso-capacity claim is how the 75% latency figure became irreproducible.

### Stage 3 — lifetime and write variation on the ten mixes

```bash
python3 mfs.py hycsim --config config/suscom_ten_mixes.json
```

The core run: 17 policies × 2 arenas × 10 mixes, per-mix tuning on a disjoint
prefix, scored on the suffix.

**Before you start it, three checks.**

1. `python3 stage3_hycsim/mix_labels.py <traces>/*.txt` — all ten must resolve to
   MIX1–MIX10 with no note. Verified here; re-check after any rename.
2. `head -3` one trace and confirm the grammar matches
   `<cycle> <core> GetS|GetX|L2_Rpl <hex> [bs dirty tcbit]`, and that
   `WARMUP COMPLETED` is present. **Send me the output before the long run** —
   a grammar mismatch wastes the whole job.
3. `traces.clock_hz` must be the gem5 clock the traces were generated with.
   Energy and years-to-failure both scale linearly with it.

**Cost:** ten mixes at roughly 90 M records, 17 policies, two arenas. Budget
20–30 GB of prepared storage and start with `hycsim_workers: 2`. Raise it only
after watching RAM on one mix.

### Stage 4 — join and statistics

```bash
python3 mfs.py integrate  --config config/suscom_ten_mixes.json
python3 mfs.py statistics --config config/suscom_ten_mixes.json
```

`integrate` produces energy, area and a four-point endurance sweep, and writes
`assumptions.json` beside it. `statistics` produces what the manuscript has
never had: bootstrap CIs on the geometric mean, the log-scale interaction
contrast for PSC × VRO, and the validation of Eq. (5) against directly measured
lifetime.

**The proxy validation is a result in its own right.** On one trace it already
shows 69% median error and 14% pairwise ranking reversals. Across ten mixes it
becomes publishable, and it is the cleanest possible answer to the reviewer
question "why is your lifetime metric valid?".

### Stage 5 — ChampSim, performance of the novel ideas

```bash
python3 mfs.py champsim --config config/suscom_ten_mixes.json
```

Last, and scoped as exploration. It answers the one question nothing else can:
what does the mechanism cost in IPC, measured rather than estimated, with wear
counted in the same run.

**Set `champsim_traces`** to your `traCES/dpc3_speccpu` directory. Your existing
SPEC CPU2017 ChampSim traces are exactly right, and `parsec2.1`, `gap_xsbench`
and `CRC2_trace` cover the suites the audit says are missing.

---

## 2. Fidelity: what I verified against the primary papers

Every re-implementation has now been checked against the primary source rather
than a summary. **Seven of eight were wrong**, in both directions. That hit rate
is the argument for doing this before the run, not after review.

| Scheme | Defect found | Direction | Status |
|---|---|---|---|
| **Start-Gap** | The remap was never wired up. `does_remap` was never set and `remap_set` never overridden, so `llc.run` bound `remap = None`, `self.start` was written and never read, and no address was ever relocated. What remained was a 1/(psi+1) write tax with zero benefit: measured inter-set variation got monotonically **worse** as psi shrank, so the tuner's best Start-Gap was Start-Gap switched off. It also corrupted live blocks (no spare reserved) and orphaned blocks into sets their address does not index to. | **Catastrophically penalised the competitor** | **Rewritten.** Set-granularity transplant: Fig. 7 mapping, Fig. 6 atomic wrap, appended spare set, psi scaled by associativity to hold the paper's overhead budget. Verified: bijection holds, 0 orphans, hit rate identical to `lru-hybrid`, overhead 2.7% against the 3.8% budget, and interV now falls monotonically (0.0200 → 0.0103) as psi tightens. |
| i2WAP Swap-Shift | Migrated set contents and charged 2 × ways writes per swap. The paper invalidates; there is no data movement. | **Penalised** | Fixed, both simulators |
| i2WAP PoLF | Fired on fills and performed the write before invalidating. The paper counts write *hits* and *avoids* the write. | **Penalised** | Fixed in ChampSim; starved by the trace protocol here, documented |
| EqualChance | Selected the globally least-written way using per-way counters it does not have. The paper redirects to an invalid block, else a clean one, with one counter and one flag per set. | **Flattered**, and understated its storage | Fixed |
| **AYUSH — mechanism** | Was an entirely different scheme (reuse-based admission) instead of the write-hit swap with the SRAM LRU. Venue also wrong. | Unfaithful | Fixed; MASCOTS 2015, DOI 10.1109/MASCOTS.2015.29 |
| **AYUSH — Z** | Z was a static tuned constant. Sec. II-B defines it as *measured*: W per-LRU-position hit counters, Z chosen so the top-Z positions capture alpha·H of hits, recomputed every I, floored at L. A grid-searched constant is an oracle the paper does not have. | **Flattered** (oracle) *and* penalised (no phase tracking) | Fixed: adaptive Z, alpha=0.60, L=WS, I=500K |
| **AYUSH — WS** | `WS` was bound to `nvm_ways` (12) instead of the SRAM way count (4). The paper's WS is the number of SRAM ways. The floor `Z >= WS` therefore clamped Z to 12 and silently disabled the adaptation. | **Penalised** | Fixed; 1,339 migrations at the correct floor against 894 at the wrong one |
| LiNoVo | Modelled as a rotating write window plus victim buffer. | Unfaithful | **RESOLVED — deleted.** "LiNoVo" is a *thesis title*, not a mechanism (it appears twice in 300 pages, both on the front matter). The thesis presents four orthogonal works; the rotating window is Ch. 4 (SWWR/DWWR), the victim buffer is Ch. 7 (WLVC), and the dataless/RDAWIP scheme people mean by "LiNoVo" is Ch. 3 — which **cannot** be implemented here (see below). Replaced by faithful `swwr` and `dwwr`. |

New competitors added, both demanded by the audit:

- **`sequoia`** (Jokar, Arjomand, Sarbazi-Azad, IEEE TVLSI 24(3), 2016) — G-OAS
  inter-set association plus WAD intra-set displacement, with AT/DT/RT tables
  and the per-line native bit. Hybrid adaptations (SSC counts NVM write hits
  only; WAD victims restricted to NVM ways) are stated as deviations.
- **`swwr` / `dwwr`** (Agarwal thesis Ch. 4, Algorithms 1 and 2) — m=4 vertical
  write-restricted windows over the NVM ways, static and write-counter-driven.

Two competitors are **excluded with a stated, checkable reason** rather than
faked:

- **POEM's bypass half (PO)** is underspecified in the paper: the reuse
  predictor's entry format, tag semantics, training rule, counter width and
  decay are never given, and `match == 0 ⇒ bypass` — which the authors call the
  source of their speedup — cannot be defined without them. It also needs
  read-queue occupancy and PC signatures. POEM's *endurance* half (Alg. 2) is
  implementable and should be run as "POEM's endurance policy, ETh=400", never
  as "POEM": their own Fig. 10 shows PO contributes ~12 of POEM-400's 15 points.
- **LiNoVo Ch. 3 (RDAWIP)** is indexed by `H3(PC ‖ byte offset)`. The trace
  carries no PC. Separately, its dataless-entry optimisation targets
  *memory-sourced fills*, and this LLC has none — it is filled only by L2
  writebacks. Both reasons are verifiable by a reviewer against the thesis.

Conformance suite is now **134 cases** across **20 policies**, and `doctor` will
not pass without it.

---

## 2a. HyCSim conformance: bit-exact, and it changes the mechanism story

The package's own release verification listed this as an open limitation: *"the
replay ... has not been demonstrated bit-for-bit against an official HyCSim
result. Describe it as HyCSim-compatible until such a conformance test exists."*
It now exists (`tests/test_hycsim_conformance.py`) and it passed.

Official HyCSim `BaseHybrid`, 1 MB, 12 NVM + 4 SRAM, 1,000,000 real records from
the project's own `example_trace.txt`, both models starting cold:

| | HyCSim | this model |
|---|---|---|
| hits | 156,326 | 156,326 |
| misses | 379,677 | 379,677 |
| total frame writes | 407,735 | 407,735 |
| peak frame writes | 59 | 59 |
| per-frame vector | — | **16,384 / 16,384 identical (100.000000%)** |

Not just the aggregates: the entire per-frame write vector, which is what every
lifetime number is computed from. The one difference found was a recency
tie-break — HyCSim stamps recency with the trace *cycle*, and gem5 emits many
records per cycle, so ties are common and HyCSim breaks them by way index. This
model stamps by record index, which never ties. Default stays record index
because it is the better model; `llc.set_recency(cycle)` reproduces HyCSim
exactly, and that is the mode the test asserts at 100%.

**Now the part that matters more than the test.** Reading the HyCSim source to
build the test settled a question the whole mechanism section rests on.
`BaseHybrid.process_L2Rpl` contains `assert(not block.dirty)` on the resident
path: a writeback to a block already in the LLC is *never dirty*, because
`process_GetS` invalidates a dirty block and `process_GetX` invalidates
unconditionally when the block goes up to L2. There is also no fill on a read
miss — `process_GetS` on a miss counts the miss and allocates nothing.

Two consequences, and they are structural, not tuning:

1. **Every charged NVM write in this protocol is an allocation.** Peak wear is
   fill-driven by construction. This is not an artefact of my model — it is the
   reference model's own semantics, and it independently confirms the finding
   that killed ELBA's promote-and-release.
2. **Every write-hit-triggered mechanism is trigger-starved here**: AYUSH's
   Algorithm 1, i2WAP's PoLF, Sequoia's G-OAS and WAD, ELBA's PR. Measured on
   400 k records: Sequoia's SSC never once reached its 127 threshold, so
   `associations = 0` and the `oas=0` ablation is bit-identical to the default.
   That is the paper's own documented `swaptions` regime (Sec. VI-B5), not a
   broken re-implementation — but it must be *said*, or every one of those rows
   reads as a scheme that failed rather than a scheme that never fired.

The honest framing for the manuscript: report these schemes under both
protocols. ChampSim's LLC does charge write hits, and there the same mechanisms
have real headroom — which is exactly why PSC/VRO behaved differently in the two
simulators. That disagreement is a finding about protocol sensitivity, and it is
more interesting than either number alone.

---

## 2b. ELBA: what it does and does not claim

`policies/elba.py` is the new mechanism, and stage 0 already removed half of it.
Full argument in `docs/ELBA_DESIGN.md`; the short version:

| Mechanism | Claim | Verdict |
|---|---|---|
| PR — promote and *release* the NVM frame (AYUSH backfills; ELBA does not) | lifetime | **Killed.** Peak wear is fill-driven at 1 MB. Measured max wear 166 vs 173 unmanaged — 4%, against psc-soft's 110. |
| GWAB — one globally pooled absorption buffer instead of per-set SRAM ways | performance | **Survives.** +8.2 points hit rate, the largest performance effect in the project. |
| EB — elastic boundary with a two-sided hit-rate guard | both | Survives as the thing that stops GWAB finding the degenerate optimum. |

Measured, 4 M accesses, 1 MB, hybrid arena:

| config | max wear | μ | NVM writes | hit rate |
|---|---|---|---|---|
| unmanaged | 173 | 109.4 | 1,343,954 | 0.3671 |
| psc-soft | **110** | **66.1** | 811,679 | 0.3756 |
| elba, 2048-entry elastic | 168 | 108.0 | 1,327,305 | 0.3995 |
| elba, 8192-entry | 166 | 100.7 | 1,237,869 | **0.4488** |

**Do not write ELBA into R3 as a lifetime mechanism.** The evidence says it is a
capacity mechanism that happens to sit in the write path. Either report it as
that, or hold it for a separate paper. The composite psc-soft + ELBA is the next
experiment worth running: the two act on different channels (μ via redirection,
hit rate via pooling), so they should compose, and if they do not, that is worth
knowing before either goes in.

One implementation quirk the run exposed: the per-residency write counter starts
at 1, so `promote_on=1` and `promote_on=2` are the same policy. Documented rather
than silently patched.

---

## 3. What I still cannot verify, and need from you

1. **A trace grammar sample.** `head -3` of one exported trace, plus
   `grep -c 'WARMUP COMPLETED'`. This is the single highest-risk unknown.
   Config now points at
   `/media/kallinath/UBUNTU-ONLY/HyCSim-SPEC-2006-gem5-traces/exported_traces`,
   with prepared output on the same volume — confirm 20–30 GB is free there.
2. **The gem5 clock** used to generate them. `clock_hz` currently reads 2.0 GHz,
   which is a placeholder, not a measurement.
3. **Papers I do not have.** From the reference library I have i2WAP,
   EqualChance/EqualWrites, LiNoVo, LastingNVCache, Sequoia (Jokar et al., TVLSI
   2016) and Agarwal's 2020 IITG thesis. I do **not** have:
   - **AYUSH** (Mittal & Vetter, IEEE CAL 2015) — implemented from description
     only, and it is the closest prior work to PSC
   - **DWAWR** (Agarwal & Kapoor, IEEE TC 2019) — same
   - **Start-Gap** (Qureshi et al., MICRO 2009) — same
   - **POEM**, named by the audit as a required comparison (Sequoia is now
     obtained and implemented as `sequoia`)
   - the **DeepNVM++** paper you uploaded, which I have not yet read against
     your positioning
   If any of these are on the MacBook, grant that folder and I will verify them
   the same way. Two of four checks so far found real defects, so the prior on
   the remaining four is not good.

---

## 4. Pre-run checklist

- [ ] `mfs.py doctor` passes, including the 52 conformance cases
- [ ] All ten traces resolve to MIX1–MIX10
- [ ] `clock_hz` matches the gem5 configuration
- [ ] Prepared-trace disk budget confirmed (20–30 GB free on `UBUNTU-ONLY`)
- [ ] Stage 0 (`profile_writes.py`) run on two mixes; verdict is not KILL
- [ ] `champsim_traces` set if stage 5 is wanted
- [ ] Decide now: `psc-soft` and `tailguard` are proposals with promising but
      single-trace evidence. Run them, but do not write them into the manuscript
      until the ten mixes agree.
- [ ] Accept in advance that the run may not support the manuscript's current
      claims. On the evidence so far it probably will not support the
      super-additivity claim or the 75% latency figure. Better now than in
      round four.
