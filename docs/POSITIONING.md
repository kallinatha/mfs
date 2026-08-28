# Positioning against the literature you just gave me

Six papers read in full against your claims. This is the part of the revision
that decides whether Reviewer #1 is satisfied, and two of the six force changes
you will not enjoy.

---

## 1. The SOT cell values change the area claim

`Paper vPublished.pdf` = I. Singh, B. Raj, M. Khosla, B. K. Kaushik,
"Comparative Analysis of Spintronic Memories for Low Power on-chip Caches",
*SPIN* 10(4):2050027, World Scientific, 2020, DOI 10.1142/S2010324720500277.
NVSim, 22 nm, 300 K. Table 2:

| | SRAM | STT-MRAM | SOT-MRAM |
|---|---|---|---|
| Cell area | 146 F² | 54 F² | **57.5 F²** |
| Aspect ratio | 1.46 | 0.54 | 0.57 |
| R_P / R_AP | — | 3000 / 6000 Ω | 16500 / 32400 Ω |
| Read current | — | 12.5 µA | 54.02 µA |
| Write current | — | 80 µA | **54.02 µA** |
| Write pulse | — | 10 ns | **1 ns** |
| Access CMOS width | 1.31 F | 8 F | 8.5 F |

Their Table 1 adds, at 512 kB: SOT write access energy **33 pJ** against STT's
**573 pJ**, and read/write access time 1.92/1.37 ns against 1.89/>10 ns.

**What this does to you, bluntly.**

- **SOT is *larger* than STT here, not smaller** — 57.5 F² against 54 F². That
  is physically expected: SOT is a three-terminal device and needs a wider
  access transistor (8.5 F against 8 F). Your sweep runs SOT at 12 F² and
  24 F², which are hand-set geometries carrying DESTINY's sample electricals.
  They are assumptions, and a reviewer who finds this paper will say so.
- Your DESTINY sweep already found SOT's advantage inverting at 32 MB (leakage
  +24.2%, area +8.5%). With a 57.5 F² cell that inversion arrives far earlier.
  **Do not claim a cell-area or density advantage for SOT-MRAM.**
- What *is* decisive, and what this paper corroborates independently of your own
  tool: **the write path.** 1 ns against 10 ns, 54 µA against 80 µA, 33 pJ
  against 573 pJ. A 7–17× write-energy advantage from a citable third party is a
  stronger argument than anything you were claiming on area.
- It also corroborates the PCM exclusion from a second source: PCM write
  40–150 ns and >6000 pJ at 512 kB (their Table 1). Your DESTINY numbers are
  301 ns and 102.8 nJ. The magnitudes disagree — say so — but the conclusion is
  the same, and having two tools reach it independently is worth more than one
  tool reaching it precisely.

**Applied:** two new cell files, `sot_57_singh2020.cell` and
`sttram_54_singh2020.cell`, transcribed from Table 2 with every non-transcribed
value marked in-file. The sweep now runs **seven** technology points instead of
five: SRAM, STT at 6 F² (DESTINY sample, infeasible), 40 F² (hand-corrected) and
54 F² (published), SOT at 12 F², 24 F² and 57.5 F² (published), and PCM. Report
all of them. The published points are the least favourable to SOT on area, which
is precisely why leaving them out would be indefensible.

---

## 2. Sivakumar & Jose is the novelty threat, and it hits ELBA

"Self Adaptive Logical Split Cache Techniques for Delayed Aging of NVM LLC",
*ACM TODAES* 28(6), Art. 97, 2023, DOI 10.1145/3616871.

**P-ViSC is an adaptive partition-boundary controller for a wear-aware NVM LLC.**
A runtime write-composition signal (`data_count`, one 10-bit global counter)
moves a boundary between two way groups, one way at a time, within hard bounds
(1:7 to 4:4), with consecutive-interval hysteresis and a restoring affinity to a
3:5 default. In your vocabulary: an elastic boundary.

| Your claim | Verdict |
|---|---|
| PSC / psc-soft | **Survives.** Their LLC is homogeneous STT-RAM; the partition axis is instruction-vs-data, decided at fill from an existing signal, never learned. No SRAM, no technology asymmetry, no predictor. But this kills any framing of PSC as "the first way-partitioned wear-aware LLC" — frame the novelty on the *soft* partition and the predictor. |
| ELBA — PR | **Survives, narrowly.** Not threatened by this paper, but by **PoLF**, which their §3 describes: "the new data is put into the write-back buffer directly, and the cache line is marked invalid." That is threshold-triggered invalidate-instead-of-write-in-place on a write hit. Your delta over AYUSH∪PoLF is the *conjunction*: deterministic Nth-write trigger, SRAM destination that stays resident, no NVM backfill. State the conjunction or a reviewer calls PR "PoLF with an SRAM destination". |
| ELBA — GWAB | **Survives cleanly, and it is your strongest claim.** Every scheme in their related-work survey — i2WAP, EqualWrites, EqualChance, SWWR, DWWR, DWAWR, WALL-NVC, ViSC — is intra-set and geometry-pinned. Decoupling absorption capacity from per-set way geometry is the one thing none of them do. Lead with it. |
| ELBA — EB | **Substantially pre-empted.** "Adaptive resizing of a partition boundary in a wear-aware NVM LLC, driven by a runtime write signal, with hysteresis and bounds" is P-ViSC, TODAES 2023. What remains yours: the boundary is between *technologies*, not within one array; and **the two-sided hit-rate guard with the degenerate-optimum argument**, which they cannot have because both sides of their boundary are NVM. EB is a delta unless you foreground the guard and run the ablation with it disabled to show the collapse. |
| Proxy mis-ranking | **Not claimed by them — but their own data exhibits it, undiagnosed.** Same paper, same dual-core 512 kB configuration: P-ViSC beats ViSC by **7% on IntraV** and loses to it by **13% on measured peak-wear lifetime**. An explicit rank inversion, in a TODAES paper, never remarked on. That is third-party corroboration you did not have. |

Citing this paper is **mandatory**. If EB appears without P-ViSC on the page, the
natural reviewer inference is that you do not know the adaptive-partitioning
literature or are avoiding it — both worse than the overlap. Cite the GLSVLSI
2021 ViSC ancestor alongside it. Implement E-ViSC as a baseline if you want a
free, pure-NVM comparison point; do **not** put P-ViSC in a bar chart, because
it partitions I/D within NVM while you partition SRAM against NVM, and any
head-to-head number invites the fair objection that you measured a different
mechanism. Engage it in prose.

One more asymmetry to exploit: **that paper reports no performance metric at
all** — no IPC, no hit rate, no miss rate, in 24 pages — while criticising DWWR
for "degrading performance". Your two-sided hit-rate guard is exactly the
discipline it lacks.

---

## 3. POEM partially pre-empts the proxy claim — reframe it

Bagchi et al., *ACM TODAES* 29(5), Art. 79, 2024. Sec. 5.4, verbatim:

> "if all cache ways are written uniformly and also heavily, then M_var would
> still be 0, failing to capture the endurance threat of the scenario. That is
> why M_max is a crucial metric ... However, M_max has not been adopted
> extensively by the state-of-the-art, which focused only on variation metrics
> such as M_var."

POEM published the structural argument that variation metrics are inadequate,
and its own results already show M_var/M_max rank inversions (mixes 1–3: WR
reduces M_var by 15% against POEM's 5%, yet POEM is 8 points better on M_max).
**Claiming this as novel is the fastest way to lose the audit.**

Reframe: POEM identified the inadequacy and kept both metrics. Your contribution
is that *closed-form lifetime proxies built on those coefficients reorder
policies against directly measured peak wear*, at capacities and in hybrid
organisations neither paper evaluates — plus the Sivakumar inversion above as
independent evidence. That is still a section; it is just not a discovery.

Useful side-effect: POEM's **M_max** (Eq. 3) is the peak write count over all
ways, so their reported reductions map straight onto your RLI —
`RLI = 1/(1−r) − 1`. Their 15% is RLI 0.176, WCAB 6% is 0.064, WR 14% is 0.163.
You can put that column directly in your table. Sequoia's numbers you cannot:
its lifetime is an α-quantile statistic (default: 1% of lines failed) and the
paper never publishes the formula converting write counts to years.

---

## 4. Sequoia is the paper a reviewer will hand you

Jokar et al., IEEE TVLSI 24(3):954–967, 2016. It is the direct successor to
i2WAP (SwS→G-OAS, PoLF→WAD) and *the* inter-plus-intra-set NVM wear-levelling
paper. Now implemented (`policies/sequoia.py`).

Two things to prepare for.

**Their Table I is the counter-evidence to your fill-driven claim** and you must
engage it rather than ignore it: write hits contribute InterV 169.89% against
82.83% for all other write types. Your answer is precise and defensible — that
is a *variation* decomposition, not a *peak-wear* decomposition, measured on a
2 MB **all-NVM** array with no SRAM partition absorbing repeat writes. It does
not test your claim. Say that explicitly.

**Their Sec. VI-B5 is your best supporting citation.** On `swaptions`, ~60% of
sets never reach the RSC threshold and ~80% never reach the SSC threshold, so
both mechanisms go idle and the scheme adds write-backs while delivering
essentially nothing. Under this project's exclusive trace protocol that is the
*expected* regime, and it is the authors' own description of it. Measured here
on 400 k records: SSC never saturated once, `associations = 0`, and the `oas=0`
ablation was bit-identical to the default.

Also worth having ready: their Sec. VI-C finds InterV and IntraV **grow** with
capacity in the baseline, and Table VI shows lifetime barely moving from 2 MB
(0.28 yr) to 8 MB (0.38 yr) "because these are hot lines/sets in the baseline
system that limit lifetime". That second half supports you; the first half does
not. Quote both.

---

## 5. AYUSH gives you a supporting measurement you can cite

Mittal & Vetter, MASCOTS 2015, Table I — percentage of cumulative hits by MRU
position:

| | L1-I | L1-D | L2 |
|---|---|---|---|
| MRU way | 97.81% | 92.38% | **57.11%** |
| First two | 99.64% | 98.01% | 72.21% |
| First three | 99.90% | 99.44% | 78.66% |

Their own framing: "filtering by first level cache significantly reduces the
temporal locality of LLC access stream ... the write endurance limit of NVM may
be reached in a few days even with a few (e.g. 3 out of 16) SRAM ways." That is
published support for the low-locality premise your fill-driven finding rests
on, from the author of the scheme you are competing with.

Their lifetime metric is also exactly yours: "the inverse of maximum writes on
any NVM (and not SRAM) cache blocks". Confirm your `wear_array(nvm_only=True)`
matches — it does — and say so, because it makes the AYUSH comparison
metric-compatible with no conversion.

---

## 6. What to change in the manuscript, in order

1. **Drop any SOT cell-area or density advantage claim.** Reposition on write
   latency and write energy, cite Singh et al. Table 2 alongside your DESTINY
   sweep, and report all seven technology points.
2. **Add a related-work paragraph** covering Sivakumar & Jose (P-ViSC as the
   closest adaptive-boundary prior), POEM, Sequoia, and the Agarwal thesis
   Ch. 4. Their absence is the defect; the overlap is manageable.
3. **Reframe the proxy claim** as an extension of POEM Sec. 5.4, not a
   discovery, and add the Sivakumar dual-core inversion as third-party evidence.
4. **Foreground ELBA's hit-rate guard** and run the no-guard ablation, or drop
   EB. Lead ELBA on GWAB.
5. **State the protocol dependence** of every write-hit-triggered result, and
   report those schemes under both HyCSim and ChampSim semantics.
6. **Label Start-Gap** as a set-granularity adaptation of plain (not Randomized)
   Start-Gap, and cite the 53% figure, not the 97%.
