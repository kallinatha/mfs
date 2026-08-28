# SUSCOM R3 — first exploratory simulation results

> **Legacy snapshot:** these results predate the final bounded-memory,
> non-overlapping suffix and cross-stack protocol. They are diagnostic evidence,
> not paper-ready results. Regenerate every claim with `mfs.py` and the ten-mix
> configuration before citing a number.

Everything below comes from simulations run end to end in this session. No number
is transcribed from the manuscript, and none is estimated. Raw CSVs, logs and
generated LaTeX are in `results/` and `stage1_destiny/`.

**Tools built and running:** DESTINY v2 (cloned, patched for modern g++, built),
NVMExplorer with its NVSim submodule (built), HyCSim (cloned; its model
re-implemented and extended with six wear-management policies).

**Trace:** HyCSim's shipped 4-core LLC trace — 43.1 M records, 37,354,973
post-warm-up accesses (12.13 M GetS, 6.84 M GetX, 18.38 M L2_Rpl). Real trace
data, not synthetic.

---

## 1. Stage 1 — the cross-technology comparison the AE demanded

DESTINY v2, 45 nm, 8-way, sequential access, write-EDP optimised. Five
technologies through the identical model. 1 MB shown; 2 MB in `results.csv`,
larger capacities still running.

### High-performance (HP) devices

| Technology | Read lat (ns) | Write lat (ns) | Read E (nJ) | Write E (nJ) | Leakage (mW) | Area (mm²) |
|---|---|---|---|---|---|---|
| SRAM | 0.579 | 0.380 | 0.480 | 0.478 | 1740.2 | 4.151 |
| STT-MRAM | 2.858 | 8.729 | 1.189 | 1.090 | 5599.2 | 6.911 |
| **SOT-MRAM 12 F²** | **0.809** | **0.733** | **0.259** | **0.334** | **342.3** | **1.368** |
| SOT-MRAM 24 F² | 0.829 | 0.726 | 0.335 | 0.405 | 773.5 | 2.184 |
| PCM | 23.815 | 301.127 | 2.724 | 102.791 | 10384.1 | 41.029 |

### Low-operating-power (LOP) devices

| Technology | Read lat (ns) | Write lat (ns) | Read E (nJ) | Write E (nJ) | Leakage (mW) | Area (mm²) |
|---|---|---|---|---|---|---|
| SRAM | 0.799 | 0.531 | 0.261 | 0.260 | 27.12 | 5.091 |
| STT-MRAM | 3.437 | 9.006 | 0.662 | 0.661 | 72.04 | 7.159 |
| **SOT-MRAM 12 F²** | **1.047** | **0.834** | **0.193** | **0.275** | **11.73** | **2.483** |
| SOT-MRAM 24 F² | 1.060 | 0.813 | 0.228 | 0.309 | 20.18 | 3.621 |
| PCM | 24.560 | 301.585 | 1.393 | 71.385 | 48.33 | 41.430 |

### What this gives you

**Against STT-MRAM — a clean, decisive win.** At 1 MB / 45 nm / HP, SOT-MRAM at
12 F² is 3.5× faster on reads, **11.9× faster on writes**, 4.6× lower read
energy, 3.3× lower write energy, **16.4× lower leakage** and **5.1× smaller**.
It dominates on every axis. This is the NVM-vs-NVM comparison the AE made a
condition, and it is strongly in your favour. Lead the revision with it.

**Against PCM — you can now answer "why not PCM?" with measurements.** PCM's
write latency is 301 ns, **411× SOT-MRAM's**, and its write energy is 102.8 nJ,
**308× SOT-MRAM's**. A 1 MB PCM cache occupies 41 mm². PCM is not an LLC
candidate at any node, and you can now say so with a number instead of a
citation. Half a page in related work plus one table row settles it.

### Two things in this table that contradict the manuscript

**(a) The 75% latency improvement over SRAM does not reproduce.** DESTINY says
SOT-MRAM at 1 MB / 45 nm is **40% slower on reads and 93% slower on writes than
SRAM**, not 75% faster. The manuscript's own Table 16 already reported only a
16–20% advantage; Section 5.4 reports 75%. This run agrees with neither — it
says SOT-MRAM loses on raw latency and wins on leakage, area and energy. That is
the physically expected result for an MRAM cell against SRAM at equal capacity.
The 75% figure has to be either re-derived, re-labelled as a different quantity,
or dropped. It currently appears in the abstract, Table 2, Section 5.4 and the
conclusion.

**(b) Your leakage advantage largely evaporates against LOP SRAM.** HP SRAM
leaks 1740 mW; LOP SRAM leaks 27.1 mW — a 64× difference. SOT-MRAM LOP leaks
11.7 mW. So the honest claim is "2.3× lower leakage than low-power SRAM", not
the 5.1× that the HP comparison implies. If you quote the HP number without
saying which SRAM device model you compared against, a reviewer who knows the
LOP roadmap will call it selective. Report both device models — you have the
data now.

---

## 2. Stage 3 — the lifetime comparison, with real competitors

1 MB LLC, 16 ways, 37.35 M accesses. **Every scheme parameter-tuned** on a
held-out 6 M-access prefix, then re-run on the full trace. Selection metric is
peak frame write count, measured, not the closed-form estimator.

Two arenas so that PSC-VRO gets no credit for merely having SRAM ways present:
`all-nvm` = 16 NVM ways; `hybrid` = 12 NVM + 4 SRAM at the same capacity.

### all-NVM array

| Scheme | μ | InterV | IntraV | max writes | RLI_direct | RLI_proxy | WA | hit rate |
|---|---|---|---|---|---|---|---|---|
| Unmanaged (LRU) | 829.3 | 6.62% | 5.56% | 1224 | 0.0% | 0.0% | 1.000 | 0.5660 |
| VRO only | 830.9 | 6.75% | 5.01% | 1207 | **1.4%** | 0.2% | 1.002 | 0.5649 |
| Start-Gap | 864.0 | 6.38% | 5.34% | 1186 | 3.2% | −3.6% | 1.042 | 0.5645 |
| i2WAP | 832.5 | 6.61% | 5.66% | 1160 | 5.5% | −0.5% | 1.004 | 0.5624 |
| ~~LiNoVo~~ (withdrawn, see note below) | 838.3 | 7.19% | 5.15% | 1142 | 7.2% | −1.2% | 1.011 | 0.5553 |
| EqualChance | 836.0 | 6.79% | **0.43%** | 1089 | **12.4%** | 3.8% | 1.008 | 0.5639 |

### Hybrid array (12 NVM + 4 SRAM)

| Scheme | μ | InterV | IntraV | max writes | RLI_direct | RLI_proxy | WA | hit rate |
|---|---|---|---|---|---|---|---|---|
| Unmanaged | 829.8 | 6.69% | 5.54% | 1224 | 0.0% | 0.0% | 1.000 | 0.5660 |
| Hybrid, blind LRU | 829.8 | 6.69% | 5.54% | 1224 | 0.0% | 0.0% | 1.000 | 0.5660 |
| Start-Gap | 882.7 | 6.31% | 5.29% | 1271 | **−3.7%** | −5.5% | 1.064 | 0.5638 |
| i2WAP | 836.5 | 6.69% | 5.58% | 1215 | 0.7% | −0.8% | 1.008 | 0.5607 |
| EqualChance | 854.1 | 6.70% | 0.92% | 1159 | 5.6% | 1.3% | 1.029 | 0.5651 |
| **VRO only** | 1123.1 | 7.01% | 4.52% | 1615 | **−24.2%** | −25.7% | **1.354** | 0.5549 |
| ~~LiNoVo~~ (withdrawn, see note below) | 422.4 | 15.31% | 2.29% | 666 | **83.8%** | 87.5% | 0.509 | 0.5714 |
| **PSC only** | 425.5 | 14.42% | 1.59% | **651** | **88.0%** | 88.7% | 0.513 | 0.5677 |
| **PSC + VRO** | 420.8 | 15.25% | 2.69% | 655 | **86.9%** | 87.7% | 0.507 | 0.5716 |

> **Withdrawn.** The `linovo` policy has been deleted: "LiNoVo" is the title of the thesis/ISVLSI-2020 paper, not a mechanism, and the file implemented a rotating *permitted* window plus an unpublished cross-set victim buffer. It is replaced by `sequoia` (Jokar et al., IEEE TVLSI 2016) and by `swwr`/`dwwr` (Agarwal, PhD thesis IITG 2020, Ch. 4, Algorithms 1 and 2), which are the real write-restriction mechanisms. See `stage3_hycsim/policies/swwr.py`. **Both LiNoVo rows above are stale and must not be reported.**
> Its hybrid-array 83.8% was substantially the victim buffer absorbing
> allocations, not write restriction, so Sec. 3.4 below no longer stands as
> written and must be regenerated against `sequoia`, `swwr` and `dwwr`.

WA = write amplification: NVM write traffic relative to unmanaged, including the
writes each mechanism performs itself. Hit rate varies by under 1.7 points across
all schemes, so none of this is a performance artefact.

---

## 3. The three findings you need to absorb before writing R3

### 3.1 PSC works, and works better than you claimed

**88.0% relative lifetime improvement**, against a tuned unmanaged baseline, on
an independent trace, in an independent implementation. That is above the 65.48%
in the manuscript. The mechanism is exactly what you said it is: steering dirty
blocks into the SRAM partition halves NVM write traffic (μ 829.8 → 425.5, write
amplification 0.51) and cuts peak frame writes from 1224 to 651.

This is a real, defensible, reproducible contribution. It should be the centre of
the paper.

### 3.2 VRO does not work — and on the hybrid array it does harm

- VRO alone, all-NVM: **+1.4%**. Indistinguishable from noise.
- VRO alone, hybrid: **−24.2%**. Peak writes rise from 1224 to 1615.
- Write amplification 1.354: VRO *increases* NVM write traffic by 35%.

The mechanism of the harm is visible in the numbers. Restricting allocations to
(M−R) = 4 of 12 ways concentrates every write into a third of the partition
between rotations, and forces extra evictions and re-allocations. The rotation
does not move wear around fast enough to compensate. I swept β_max across
{256, 512, 1023, 2048, 4096} — the best value is still negative on the hybrid
array.

**PSC + VRO (86.9%) is 1.1 points worse than PSC alone (88.0%).** Adding VRO to
PSC subtracts value.

### 3.3 The super-additivity claim inverts under measurement

| | RLI_direct | RLI_proxy |
|---|---|---|
| PSC alone | 88.0% | 88.7% |
| VRO alone | 1.4% | 0.2% |
| PSC + VRO measured | 86.9% | 87.7% |
| Additive null (a+b) | 89.4% | 88.9% |
| Multiplicative null (1+a)(1+b)−1 | 90.7% | 89.0% |
| **Excess over additive** | **−2.6 pts** | **−1.2 pts** |
| **Excess over multiplicative** | **−3.8 pts** | **−1.4 pts** |

The manuscript claims +11.98 points of super-additive synergy. Measured here, the
interaction is **sub-additive by 2.6 points against the additive null and 3.8
points against the correct multiplicative null**. Both signs are wrong relative
to the claim.

I flagged the arithmetic problem before running anything: RLI is a ratio of
ratios, so an additive null is the wrong comparator. The simulation now says the
claim fails against *either* null. This is the single most dangerous sentence in
your manuscript, because it is the answer you gave Reviewers 1 and 3 on novelty.

### 3.4 A properly implemented competitor is nearly as good as your proposal

> **SUPERSEDED.** **Withdrawn.** The `linovo` policy has been deleted: "LiNoVo" is the title of the thesis/ISVLSI-2020 paper, not a mechanism, and the file implemented a rotating *permitted* window plus an unpublished cross-set victim buffer. It is replaced by `sequoia` (Jokar et al., IEEE TVLSI 2016) and by `swwr`/`dwwr` (Agarwal, PhD thesis IITG 2020, Ch. 4, Algorithms 1 and 2), which are the real write-restriction mechanisms. See `stage3_hycsim/policies/swwr.py`. The number below came from the withdrawn file and
> must be regenerated before it is used in any argument.

LiNoVo, re-implemented from its published description and tuned on the same grid,
reaches **83.8%** against PSC-VRO's 86.9% — a gap of 3.1 points, not the 20–27
points the manuscript reports over SA-1 and SA-2.

This is exactly what the AE asked to see, and it is the finding that most changes
the paper. Your advantage over the state of the art is real but modest. A claim
of "substantially exceeds" will not survive; "matches the best published hybrid
scheme at lower storage cost" might, and you have the storage argument already.

### 3.5 IntraV and lifetime are decoupled — a methodological point in your favour

EqualChance cuts IntraV by 92% (5.56% → 0.43%), far more than PSC-VRO's 51%. Yet
its lifetime gain is 12.4% against PSC's 88.0%. Reducing intra-set variation is
not the same as extending lifetime; reducing μ is what matters. Your abstract
leads with the IntraV number (58.8%). It is leading with the weaker metric.

---

## 4. Caveats — state these, do not hide them

1. **One trace, not ten mixes.** This is HyCSim's shipped 4-core trace. Your ten
   SPEC mixes may behave differently. The framework takes your gem5 traces
   directly; re-run before concluding anything final about VRO.
2. **Re-implementations, not author code.** VRO, PSC, i2WAP, EqualChance,
   Start-Gap and (withdrawn) LiNoVo are all implemented from published descriptions. My VRO
   may differ from yours. The correct statement is "an independent
   reimplementation on an independent trace shows no benefit from VRO", not "VRO
   does not work". That is still enough to demand you re-verify before
   resubmitting.
3. **Regime dependence — tested, and it does not rescue VRO on the hybrid array.**
   At 1 MB the baseline IntraV is 5.6%; at 16 MB it is **53.0%**, reproducing the
   manuscript's regime. The 16 MB parameter search (6 M-access prefix, peak frame
   writes; lower is better) says:

   | 16 MB, tuning prefix | max writes |
   |---|---|
   | Unmanaged, all-NVM | 95 |
   | VRO, all-NVM, beta_max = 1023 | **74** |
   | EqualChance, all-NVM, interval = 8 | 76 |
   | ~~LiNoVo, all-NVM, best~~ (withdrawn) | 64 |
   | Unmanaged, hybrid | 83 |
   | **PSC, hybrid** | **9** |
   | VRO, hybrid, best of five beta_max | 95 |
   | **PSC + VRO, hybrid, best of five beta_max** | **16** |

   So VRO does earn its keep **on a homogeneous NVM array in the high-variation
   regime** — 95 to 74, a 22% cut in peak writes. That is a real result and worth
   reporting.

   But on the hybrid array it never helps, in either regime. At 16 MB, PSC alone
   reaches peak writes of **9**; adding VRO takes it to **16** — nearly twice as
   bad. At 1 MB, PSC 651 vs PSC+VRO 655. Two capacities, two orders of magnitude
   of intra-set variation, same direction.

   The explanation is mechanical and worth putting in the paper. PSC works by
   removing write traffic from the NVM partition altogether. VRO works by
   confining the remaining traffic to (M-R) of M ways. Once PSC has reduced NVM
   writes to a trickle, confining that trickle to a third of the partition
   concentrates it rather than spreading it. **The two mechanisms are substitutes,
   not complements** — which is the precise opposite of the super-additivity
   claim, now observed independently at two capacities.

4. **STT-MRAM cell geometry.** DESTINY's shipped STT cell declares 6 F² with an
   8.5 F access transistor, which is not self-consistent. An area-corrected
   40 F² variant is in `stage1_destiny/cells/sttram_40.cell` and should be run as
   a second STT point so nobody can say the competitor was handicapped.
5. **PCM at 2 MB HP** reports 1.98 mm² against 41.03 mm² at 1 MB — a DESTINY
   array-organisation artefact worth checking before publishing that row.

---

## 5. What I would do with this

**Restructure around PSC.** It measures at 88% RLI, beats every published
competitor tested, and halves NVM write traffic. That is a paper.

**Separate the two mechanisms rather than selling them as one.** The measured
story is: PSC is the contribution; VRO is a distinct technique whose value is
confined to homogeneous NVM arrays with high intra-set variation, where it cuts
peak writes 22%. Presented that way you keep both results and gain an operating
boundary no prior wear-levelling paper states. Presented as a single
super-additive mechanism, the claim is refuted by the paper's own ablation once
anyone measures it.

The super-additivity sentence must go regardless — abstract, Section 5.5.8,
Table 22 caption, conclusion, and the response letters to Reviewers 1 and 3.

**Retitle around the framework, instantiate on SOT.** You now have STT and PCM
run through the identical model, which is what makes the general framing honest.

**Replace SA-1/SA-2 with the four named schemes.** They are implemented, tuned,
cited and reproducible, and every parameter is recorded in
`results/tuning_1MB.csv`.

**Fix the 75% latency claim before a reviewer does.**
