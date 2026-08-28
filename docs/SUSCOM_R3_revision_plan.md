# SUSCOM-D-24-01370R3 — Revision Plan

**Manuscript:** A Multi-Factor Scaling Framework for Efficient SOT-MRAM Cache Design and Lifetime Extension
**Round:** 3 (major revision). **Deadline:** 3 September 2026 — roughly 20 days from today.
**Status of reviewers:** R2 satisfied. R3 not in this round's letter. R1 + Associate Editor are the only gate.

---

## 0. The one-line version

The AE has converted Reviewer 1's baseline complaint into a condition of further consideration. Nothing else in this letter carries that weight. If R3 goes back without a quantitative comparison against a *named, cited, state-of-the-art NVM cache scheme*, the paper is rejected regardless of how well the scope question is handled.

There is a second problem the reviewers have not yet raised but very likely will in R3: **several of the numbers you added in R2 to prove novelty contradict each other or do not survive arithmetic.** Fixing the baseline while leaving those in place trades one fatal objection for another. Details in Section 4.

---

## 1. Why Reviewer 1 said "compared against an SRAM-based cache" when you did compare against NVM schemes

You will be tempted to reply "but Section 5.5 already compares PSC-VRO against SA-1 and SA-2, which are NVM wear-levelling schemes." Do not send that reply. It will fail, and here is exactly why.

**SA-1 and SA-2 are never defined anywhere in the 56-page manuscript.** I searched every occurrence:

| Location | What it says |
|---|---|
| Sec. 4.2, p. 24 | "two state-of-the-art wear-levelling schemes (SA-1 and SA-2 from [19])" |
| Sec. 5.5.1 | "schemes (SA-1 and SA-2 [19])" |
| Sec. 5.5.2–5.5.4, Figs. 14–16, Table 17 | Results only |
| Conclusion | Results only |

That is the complete set. No algorithm, no parameters, no configuration, no expansion of the acronym, no statement of what "SA" stands for. From the reviewer's chair there is no identifiable competitor — just two anonymous bars.

**Worse, the citation behind them is mis-described.** Reference [19] is Agarwal & Kapoor, *"LiNoVo: Longevity Enhancement of Non-Volatile Last Level Caches in Chip Multiprocessors"*, ISVLSI 2020 — a specific technique paper. But Section 2.2 calls it "Agarwal and Kapoor's **survey** [19]", and Table 4 lists [19] under Approach = "Various". A reviewer who follows the citation finds a technique paper described in your text as a survey, and two undefined baselines attributed to it. The rational conclusion is that the NVM comparison is not real. That is what happened.

**Third problem — the SA-1/SA-2 numbers do not look like simulation output.** From Table 17:

- SA-1 across ten independent workload mixes: 30.03, 30.09, 30.12, 35.28, 40.14, 40.20, 40.48, 40.86, 44.69, 47.66
- SA-2: 30.02, 35.06, 40.06, 42.64, 43.64, 45.95, 50.02, 50.06, 50.36, 50.38

Six SA-1 values sit within 0.9 points of exactly 30 or 40. Four SA-2 values sit within 0.4 points of exactly 50. Ten independent trace-driven runs do not cluster on round decades to two decimal places. Your own PSC-VRO column, by contrast, is properly scattered (50.97 … 82.30). If a reviewer runs this check the question stops being about revision and becomes about data integrity.

**Conclusion of this section:** you do not have a weak NVM baseline that needs strengthening. You effectively have no NVM baseline in the manuscript as a reader can verify it. Treat this as building it from scratch, not defending it.

---

## 2. What must be added — ranked by acceptance impact

### Priority 1 — Named NVM competitor architectures in the lifetime study *(this is the AE's gate)*

Replace SA-1/SA-2 entirely. Implement and report against schemes a reviewer can look up.

**Recommended set (three, plus your unmanaged baseline):**

| Scheme | Citation | Why this one | Implementation cost in HyCSim |
|---|---|---|---|
| **i2WAP** | Wang et al., HPCA 2013 — already your ref [35] | The canonical intra + inter-set write-variation paper. Reviewer 1 will expect it by name. You already cite it and never compare to it. | Low. SwapShift = periodic set-remap driven by one global counter; PoLF = invalidate-on-Nth-write-hit. Both use counters HyCSim already maintains. ~150–250 lines. |
| ~~**LiNoVo**~~ → **SWWR / DWWR** | Agarwal, PhD thesis IITG 2020, Ch. 4, Alg. 1 and 2 (the thesis behind ref [19]) | You already claim your baselines come from here. "LiNoVo" is the *title*, not a mechanism; the mechanism is Chapter 4's write-restricted rotating window. Implemented as `swwr`/`dwwr` and *named by mechanism*. Closes the mis-citation wound. | **Done.** |
| **EqualChance** or **WriteSmoothing** | Mittal & Vetter, refs [37] / [46] | Gives you an intra-set-only comparator, which is the fair opponent for VRO in isolation. | Low. Per-set counters + periodic relocation. |
| **ViSC** | Sivakumar & Jose, TODAES 2023 — ref [27] | **You name it as "the closest prior work" and never compare against it numerically.** This is the single most exposed claim in the paper. | Medium — needs instruction/data tagging in the trace. See risk note below. |

**Risk note on ViSC:** ViSC splits logically by instruction vs data. If your gem5 LLC traces do not carry an I/D tag, you cannot implement it without regenerating traces. Check this on day 1. If it is infeasible in the time available, say so explicitly in the response letter and in a footnote — an honest stated limitation is survivable; silently claiming differentiation from an uncompared scheme is not.

**Where this goes:**
- New **Section 4.2, "Comparison schemes"** — half a page defining each competitor, its parameters, and the source paper. This subsection is what was missing.
- **Table 17, Figs. 14–16** — replace SA-1/SA-2 columns/bars with the named schemes.
- **New Table** immediately after Table 17: overhead comparison across schemes (storage bits, extra writes introduced by the wear-leveller itself, remap latency). Your Table 18 currently reports your own overhead in isolation; a competitor overhead column is what makes the comparison land.

**One thing you are currently not reporting that a wear-levelling reviewer will ask for:** maximum write count per line, not just IntraV/InterV/RLI. Averages hide hot sets. Add a max-write-count row — you already compute it for Eq. (4), so it costs nothing.

---

### Priority 2 — STT-MRAM (and PCM) as technology baselines in the scaling study *(cheapest possible compliance)*

Sections 5.1–5.4 — about 30 of your 56 pages — compare SOT-MRAM against **SRAM only**. This is the bulk of the paper and it is precisely what R1 called out.

DESTINY models STT-RAM and PCM natively. Adding them is a configuration sweep, not new code.

**Actions:**
- **Table 12** (HP vs LOP summary): add STT-MRAM rows.
- **Fig. 6** (eight-panel SRAM vs SOT comparison): add STT-MRAM series. The capacity crossover point becomes a three-way result, which is more interesting than the two-way one.
- **Tables 13–15** (cell area / aspect ratio / net effect): these are genuinely SOT-specific — see Section 3. Leave them, but say why.
- **Section 5.4** (density replacement): add an STT-MRAM iso-capacity and iso-area point. This directly answers "compare against state-of-the-art NVM-based cache architectures" at the system level.
- **Table 2** (`This work` row): change the finding column from "60% power, 75% latency" (SRAM-relative) to include the NVM-relative deltas. The current row advertises exactly the comparison R1 objected to.

**Effort:** days, not weeks. This is the highest return per hour of any item on this list. Do it first.

**PCM:** worth adding to the *technology comparison table and related work* even if you do not run a full PCM cache sweep. PCM's write latency (40–150 ns per your own Table 1) makes it a poor LLC candidate, and saying so with a citation is a legitimate scoping argument — it also pre-empts "why no PCM?".

---

### Priority 3 — Answer the scope question honestly, then convert it into a contribution

Reviewer 1's question is fair and you should concede the honest half of it immediately. Guessing what a reviewer wants to hear and hedging is what got you a second major revision.

**What is genuinely SOT-MRAM-specific in your work:**

1. **The entire MFS device layer.** Spin Hall angle (θ_SH), heavy-metal strip dimensions and resistivity, HM-driven cell aspect ratio. These parameters do not exist for STT-MRAM (2-terminal, 1T1R) or PCM. Your CA/AR sweep of 12–24 F² at AR 0.5–2.0 is meaningful *because* the HM strip sets the cell footprint. In a 1T1R STT cell there is no equivalent geometric knob. **This is your strongest defence and it is currently buried.**
2. **The read-way / write-way partition assumption in VRO.** SOT's decoupled read/write paths mean read-designated ways accumulate no endurance stress. In STT-MRAM, reads pass through the MTJ barrier and read-disturb is real, so a read-way/write-way split has a different cost model. Your mechanism *exploits* a SOT-specific device property; you have never said so.
3. **The write-asymmetry / thermal-degradation endurance argument** in Section 1 (10¹²–10¹⁴ effective vs 10¹⁵ nominal) is grounded in SOT device physics citations.

**What is honestly technology-agnostic:**

- PSC (hybrid SRAM/NVM physical partition) — portable to any endurance-limited NVM.
- VRO way rotation — portable.
- The IntraV / InterV / RLI metric framework — explicitly adapted from STT/PCM literature (your refs [35], [37]).

**Recommended move:** add **Section 3.3, "Technology specificity and portability"** (three-quarters of a page), stating items 1–3 as SOT-specific and conceding PSC/VRO portability. Then back the concession with data: run PSC-VRO on an **STT-MRAM parameter set** for two or three mixes and report the RLI. If it works, you have turned R1's objection into a generality result. If it works *less well* — which is plausible, because read-disturb breaks the read-way assumption — that is a *better* outcome: it proves the SOT-specific claim empirically rather than rhetorically.

**Effort:** the STT-MRAM PSC-VRO run is a parameter-file swap in a stack you have already built. One to two days.

---

### Priority 4 — Title decision

R1 offered two exits. You must pick one visibly, not straddle.

| Option | Verdict |
|---|---|
| Keep the SOT-only title, justify narrow scope | Weak. Once you add STT-MRAM results (which the AE forces), a SOT-only title reads as hedging. |
| Fully broaden to "NVM caches" | Over-claims. You cannot present a PCM or STT device-scaling roadmap from Tables 13–15 — those are SOT geometry sweeps. |
| **Split the claim: general framework, SOT instantiation** | **Recommended.** Honest, matches what you actually did, and reads as a direct response to the reviewer. |

**Suggested title:**
> *A Multi-Factor Scaling and Lifetime-Extension Framework for Endurance-Limited NVM Caches: An SOT-MRAM Instantiation*

Then state in the abstract, in one sentence, that the framework is demonstrated on SOT-MRAM and validated for portability on STT-MRAM. You already say something close to this in the Conclusion ("The framework is not limited to SOT-MRAM…") — you are being asked to promote that sentence to the title and back it with a number.

---

### Priority 5 — Related work expansion

R1: *"the related work section needs to be expanded to cover several missing references in cache lifetime enhancement in other technologies."*

Your 53 references contain **no PCM endurance work at all** ([11] is a DRAM/PCM replacement policy, not a lifetime scheme). This is an easy, visible fix.

**PCM lifetime and wear levelling — add at least four:**

- Qureshi et al., *Enhancing lifetime and security of PCM-based main memory with start-gap wear leveling*, MICRO-42, 2009. (The canonical low-overhead wear-leveller. Its near-zero table overhead is the direct comparator for your "single 10-bit shared counter" claim.)
- Seong, Woo & Lee, *Security Refresh*, ISCA 2010.
- Cho & Lee, *Flip-N-Write*, MICRO 2009. (Write-reduction rather than levelling — a different lever, worth one sentence.)
- Zhang & Li, *A Survey on PCM Lifetime Enhancement Schemes*, ACM Computing Surveys — https://dl.acm.org/doi/10.1145/3332257
- *A Scalable Wear Leveling Technique for Phase Change Memory*, ACM Trans. Storage — https://dl.acm.org/doi/10.1145/3631146 (recent, keeps the section current)

**NVM cache endurance beyond STT/SOT — add two or three:**

- Jokar, Arjomand & Sarbazi-Azad, *Sequoia: A High-Endurance NVM-Based Cache Architecture*, IEEE TVLSI — https://ieeexplore.ieee.org/document/7097738
- *Endurance-aware cache line management for non-volatile caches*, ACM TACO — https://dl.acm.org/doi/10.1145/2579671
- Mittal & Vetter, *Using cache-coloring to mitigate inter-set write variation in non-volatile caches* — https://arxiv.org/pdf/1310.8494 (directly relevant: your InterV reduction is only 37.1% and you state inter-set migration is out of scope — cite the work that does it)
- *Enhancing Lifetime and Performance of MLC NVM Caches Using Embedded Trace Buffers*, ACM TODAES 2024 — https://dl.acm.org/doi/10.1145/3659102
- *Endurance enhancement of write-optimized STT-RAM caches*, MEMSYS 2019 — https://dl.acm.org/doi/10.1145/3357526.3357538

**Structural change:** extend **Table 4** with a `Technology` column that explicitly spans PCM / STT / SOT / ReRAM. Right now the column reads "STT / NVM / Hybrid / NVM…" which tells the reviewer nothing about cross-technology coverage. Making the technology axis explicit in the table *is* the visible answer to this comment.

> Verify every DOI and page range before submission. Several of the entries above I have given you by name and link; the four canonical PCM papers (Qureshi, Seong, Cho, Zhou) are from standard knowledge and should be checked against the ACM/IEEE record.

---

## 4. Defects the reviewers have not yet found — fix these before they do

These are all in the material you **added in R2 to answer the novelty complaint**. R1 is already hostile on novelty. If they audit the new tables, these are what they find.

### 4.1 FATAL — The super-additivity claim does not survive your own Equation (5)

This is the headline novelty claim of the R2 revision. It appears in the abstract, Section 5.5.8, Table 22's caption, the conclusion, and your response letter to all three reviewers.

Your Equation (5):

> RLI = (μ_b / μ_p) × [(1 + InterV_b + IntraV_b) / (1 + InterV_p + IntraV_p)] − 1

**This metric is multiplicative by construction.** It is a product of ratios, minus one. The correct null hypothesis for "the two mechanisms do not interact" is therefore *multiplicative composition*, not arithmetic addition.

- Your additive null: 22.6 + 30.9 = **53.5%**; observed 65.48%; claimed excess **+11.98 points**, "a 22.4% synergy bonus."
- The correct multiplicative null: (1.226 × 1.309) − 1 = **60.5%**; observed 65.48%; actual excess **≈ +5.0 points**, a **~3% relative excess**.

You are comparing a multiplicative quantity against an additive baseline and calling the difference an emergent architectural discovery. Your own text one page earlier says the formula "multiplicatively combines all three channels." A reviewer who reads both pages has your novelty claim.

**It gets worse when you check the mechanism story against the IntraV data in the same table.** Your stated explanation is that PSC filters extreme blocks so VRO balances a narrower distribution — an IntraV argument. Testing it on Table 22's own IntraV columns:

| Mix | A0 | A1 factor | A2 factor | Multiplicative prediction | Observed A3 | Verdict |
|---|---|---|---|---|---|---|
| MIX1 | 6.67 | 0.781 | 0.678 | 3.53 | 3.84 | **worse than multiplicative** |
| MIX2 | 124.91 | 0.580 | 0.387 | 28.04 | 27.78 | ≈ multiplicative |
| MIX6 | 241.04 | 0.644 | 0.583 | 90.53 | 113.86 | **worse than multiplicative** |

On the metric VRO actually targets, the two mechanisms are **sub-multiplicative** — they partially overlap, which is physically what you would expect. The RLI "synergy" is coming from the μ channel (PSC diverts writes to SRAM, lowering μ_p; VRO alone in configuration A2 has no SRAM partition and therefore cannot change μ at all). That is not emergence. That is two independent channels multiplying inside a product formula.

**What to do — three options, in order of preference:**

1. **Reframe honestly.** Report the multiplicative null in Section 5.5.8, state that PSC and VRO compose near-multiplicatively with a small positive residual, and attribute the composition to the three independent channels of Eq. (5). Then move the novelty weight onto the *decomposition itself* (Table 21) and the *scaling guidelines* (Contribution 4), which are defensible. Lose the word "super-additive" everywhere — abstract, Table 22 caption, conclusion, contributions list.
2. **Keep a synergy claim but justify the null.** Only viable if you can argue on physical grounds why additive is the right null for a ratio metric. I do not think that argument exists.
3. **Leave it as is.** Do not. Reviewer 1 has spent two rounds saying your novelty is engineering integration dressed as discovery. Handing them an arithmetic error inside your flagship novelty claim ends the paper.

**Uncomfortable consequence you should sit with:** option 1 weakens the answer you gave to R1's and R3's novelty complaints in R2. You will need the new NVM-competitor results (Priority 1) to carry that load instead. That is another reason to treat Priority 1 as the whole revision rather than one item on a list.

### 4.2 FATAL — Two tables added in the same revision report different IntraV for the same experiment

| Source | Baseline IntraV | PSC-VRO IntraV | Reduction |
|---|---|---|---|
| Sec. 5.5.2 + abstract + conclusion | 84.24% | 34.70% | **58.8%** |
| Table 20 (β_max = 1,023, the default) | — | 34.70% | consistent with 5.5.2 |
| **Table 22 (A0 → A3)** | **81.31%** | **28.01%** | **65.6%** |

Same ten mixes. Same mechanism. Same default parameters. Four numbers that should be two.

Table 20 and Table 22 were both added in R2, both to answer reviewer concerns, and they contradict each other. Either A0 is a different baseline from the Fig. 14 baseline — in which case say so explicitly and label them differently — or one set of runs is stale. Resolve it and make the abstract, Section 5.5.2, Table 20, Table 22 and the conclusion agree to the digit.

### 4.3 MAJOR — Conclusion contradicts Table 17

- Conclusion, p. 49: "substantially exceeding the **39.0%** and **44.2%** achieved by the SA-1 and SA-2 baselines."
- Table 17 averages: SA-1 = **37.96%**, SA-2 = **43.82%**.

I recomputed both column averages from the ten per-mix values; the table is internally correct and the conclusion is wrong. (Moot if you replace the baselines per Priority 1 — but fix the habit, because R2 already flagged your manuscript for exactly this class of carelessness.)

### 4.4 MAJOR — A self-refuting overhead sentence

Section 5.5.6: *"The per-event energy is computed as (M−R) × E_tag-write = 4 × 0.34 nJ = 1.36 nJ, which is negligible compared to the 1.1 nJ consumed by a single normal SOT-MRAM read."*

1.36 nJ is **24% larger** than 1.1 nJ. The sentence asserts its own opposite. Any reviewer reading for overhead claims stops here.

Separately, the arithmetic uses **0.34 nJ**, which Table 16 gives as the SOT-MRAM **data-array write energy** for a 512-bit block. A tag-array pointer swap does not cost a full block write. You need a real DESTINY tag-array write energy figure. The correct number is likely one to two orders of magnitude smaller, which makes the claim you were trying to make actually true. Fix the number, then rewrite the sentence to compare against the right quantity (e.g. per-event energy amortised over accesses between reorder events — at 0.31 events per million accesses your case is overwhelming).

### 4.5 MAJOR — 75% latency improvement vs 16–20% at the array level

- Section 5.4.1 (1 MB, iso-capacity, NVMExplorer): "Read and write latencies are reduced by approximately **75%** across all workloads." Repeated in the abstract, Table 2, and the conclusion.
- Table 16 (1 MB, 45 nm, DESTINY): read latency 0.50 → 0.42 ns (**16%**), write latency 0.44 → 0.35 ns (**20%**).

Same capacity, same node, same technology pair. Iso-capacity means no hit-rate difference to absorb the gap. A reviewer will ask how a 16–20% array-level latency advantage becomes a 75% application-level one, and there is currently no sentence in the paper that answers it.

Either the metrics differ (total accumulated latency including queueing vs per-access array latency — plausible, given Fig. 10b is labelled "Total Read/Write Latency (ms)") or one of the two is wrong. **If it is a metric-definition difference, say so in one sentence and label the axes accordingly.** As written, the paper's headline number appears to contradict its own device table by 4×. This is the kind of thing that survives two review rounds and then kills a paper in the third.

### 4.6 MINOR — Arithmetic slips

- **Table 21:** the eight architecture-share values average to **45.0%**, not the 45.3% stated in the text and abstract. (8.1 / 18.0 = 45.0% from your own average row.)
- **Table 21 text:** "For memory-intensive SPEC workloads such as 505.mcf, the capacity component is proportionally larger (35.1% of total)" — 35.1% is mcf's *architecture* share. Its capacity share happens to be the same value (6.7/19.1), so the sentence is not false, but it reads as an error. Rewrite.
- **Table 18:** overhead reported for 1–4 MB only, while the MFS study runs to 32 MB and the density-replacement study uses 8 MB. Extend the table or state the range limitation.
- **Sec. 2.2 / Table 4:** ref [19] described as a survey; it is the LiNoVo technique paper. Correct both.
  *(Status: the framework no longer ships anything called `linovo`. "LiNoVo" is the
  title of Agarwal's thesis and of the ISVLSI 2020 paper, not a mechanism name. The
  write-restriction competitor is now Chapter 4's SWWR/DWWR, implemented as `swwr`
  and `dwwr`, and Sequoia is implemented as `sequoia`. Cite those, by mechanism
  name, and drop the survey description entirely.)*

---

## 5. Twenty-day schedule

| Days | Work | Blocking? |
|---|---|---|
| 1 | Check whether gem5 LLC traces carry an I/D tag (decides ViSC feasibility). Confirm DESTINY STT-RAM/PCM cell configs run. | **Yes — decides scope of everything else** |
| 1–4 | Priority 2: STT-MRAM sweeps through Tables 12–15, Figs. 6–13, Sec. 5.4. Cheapest gate compliance. | |
| 3–10 | Priority 1: implement i2WAP + SWWR/DWWR + Sequoia (+ EqualChance) in HyCSim; re-run all ten mixes; rebuild Table 17 and Figs. 14–16; new overhead-comparison table. | **Critical path** |
| 8–10 | PSC-VRO on STT-MRAM parameters (2–3 mixes) for the portability claim. | |
| 10–13 | Section 3.3 (technology specificity); Section 4.2 (comparison schemes); related-work expansion; Table 4 technology column; title change. | |
| 13–16 | Fix Section 4 defects: super-additivity reframing, IntraV reconciliation, tag-write energy, 75% vs 16–20% latency, all arithmetic. | |
| 16–18 | Full internal-consistency sweep: every number in abstract / results / conclusion cross-checked against its table. Recompute every stated average. | |
| 18–20 | Response letter, highlighted manuscript, buffer. | |

**If you fall behind, cut in this order:** ViSC implementation → PCM → EqualChance. **Never cut** the STT-MRAM technology baseline or i2WAP — those two are the AE's condition.

**Realistic assessment:** twenty days is tight but achievable *if* Day 1 confirms your tooling. If DESTINY's STT config needs rebuilding, or HyCSim's internals resist adding competitor policies, request an extension **now** rather than on 1 September. Editors grant extensions routinely; they do not forgive thin resubmissions.

---

## 6. Response letter strategy

Three things to do differently from your R2 letter.

**1. Lead with the concession, not the defence.** Your R2 letter opened each response with agreement and then a paragraph of justification. R1 read that as deflection — note their phrasing, *"neither of which have been resolved."* Open R3's response to the baseline comment with a plain statement: the previous comparison was against undefined schemes, we have replaced it with named prior art, here is the table. No preamble.

**2. Own the SA-1/SA-2 problem before the reviewer names it.** They objected to comparing against SRAM. If you silently swap in new baselines and say nothing, the reviewer may notice the old ones vanished. Say it: *"The schemes previously labelled SA-1 and SA-2 were insufficiently specified and have been replaced with implementations of i2WAP [35], SWWR/DWWR [19] and EqualChance [37], each defined in the new Section 4.2."* (Use the mechanism names SWWR and DWWR, not the thesis title "LiNoVo".) Self-reported problems cost far less than discovered ones.

**3. Answer the scope question with a number, not a paragraph.** R1 asked which parts are SOT-specific. The persuasive answer is: *"the MFS device layer is inherently SOT-specific (spin Hall angle, HM strip geometry — no STT/PCM analogue); PSC-VRO is portable, and we now demonstrate this by reporting X% RLI on STT-MRAM parameters in Table N."* One table beats three paragraphs of argument.

**Also address in the letter, unprompted:** the reframed super-additivity analysis. Presenting it as "on re-examination we identified that our null hypothesis was inconsistent with the multiplicative form of Eq. (5) and have corrected the analysis" is a credibility gain. The same finding discovered by Reviewer 1 in round 3 is a rejection.

---

## 7. Blind spots worth naming

- **You are treating R3 as an incremental patch on R2.** It is not. The AE has restated a single condition. A revision that adds two paragraphs of related work and a scope caveat, without new competitor numbers, gets desk-rejected by the AE before the reviewers see it.
- **You have never compared against ViSC** while calling it "the closest prior work" in your contributions list and building an entire positioning paragraph around three differences from it. That is the most exposed unsupported claim in the manuscript, and R1 has not yet touched it. Assume round 3 is when they do.
- **R3's absence from this letter is not safety.** They may be re-invited on resubmission. Their round-2 concerns (novelty, reproducibility, length) are still live — in particular the "released upon acceptance" reproducibility promise in Section 4.6. Consider releasing the HyCSim extensions and DESTINY configs *now*, with an anonymised link in the revision. It costs you nothing and converts a promise into evidence.
- **Endurance figure sensitivity.** You assume 10¹²–10¹⁴ effective endurance. Your RLI numbers are relative and therefore insensitive to the absolute figure — but you have never said so. One sentence stating that RLI is independent of the assumed endurance constant removes an easy line of attack.

---

## Sources

- [LiNoVo: Longevity Enhancement of Non-Volatile Last Level Caches in Chip Multiprocessors (IEEE Xplore)](https://ieeexplore.ieee.org/document/9155022/) — the *paper title*; the mechanisms implemented here are SWWR and DWWR from Chapter 4 of the underlying thesis
- Jokar, Arjomand & Sarbazi-Azad, "Sequoia: A High-Endurance NVM-Based Cache Architecture", IEEE TVLSI 24(3):954–967, 2016 — implemented as `sequoia`
- [A Survey on PCM Lifetime Enhancement Schemes — ACM Computing Surveys](https://dl.acm.org/doi/10.1145/3332257)
- [Sequoia: A High-Endurance NVM-Based Cache Architecture — IEEE](https://ieeexplore.ieee.org/document/7097738/)
- [Endurance-aware cache line management for non-volatile caches — ACM TACO](https://dl.acm.org/doi/10.1145/2579671)
- [A Scalable Wear Leveling Technique for Phase Change Memory — ACM TOS](https://dl.acm.org/doi/10.1145/3631146)
- [Enhancing Lifetime and Performance of MLC NVM Caches Using Embedded Trace Buffers — ACM TODAES](https://dl.acm.org/doi/10.1145/3659102)
- [Endurance enhancement of write-optimized STT-RAM caches — MEMSYS](https://dl.acm.org/doi/10.1145/3357526.3357538)
- [Using Cache-coloring to Mitigate Inter-set Write Variation in Non-volatile Caches — arXiv](https://arxiv.org/pdf/1310.8494)
- [Microarchitectural Exploration of STT-MRAM Last-level Cache Parameters — ACM TECS](https://dl.acm.org/doi/10.1145/3490391)
