# Verification of `SUSCOM_MFS_HyCSim_NVMExplorer_DESTINY`

Verified by running it, not by reading it. Date 15 Aug 2026, on the session
container (Ubuntu 24.04, g++ 13.3, Python 3.12, NumPy 2.4.4).

---

## 1. Does it work? Yes.

Full pipeline executed end to end against local DESTINY and NVMExplorer
checkouts:

```
doctor      -> DESTINY, NVMExplorer driver, NVSim, NumPy all OK
sample      -> fetched 20,000 upstream events, 18,000 after the marker
prepare     -> two-pass, 18,000 mmap records, profile.json with SHA-256
destiny     -> 5/5 smoke points (SRAM, STT, SOT-12, SOT-24, PCM), 332 s
nvmexplorer -> array models produced for all five cells
hycsim      -> 7 policy/arena rows, prefix-tuned, suffix-scored
integrate   -> cross_stack_results.csv, 7 rows x 59 columns
validate    -> passed, selection_guard_passed = 1 on every row
provenance  -> provenance.json with config SHA-256, tool versions
```

Unit tests: 2 passed. The claims in its `VALIDATION_REPORT.md` reproduce here.

---

## 2. What it actually is

Its core **is** the framework I built. Byte-identical files:

| File | Diff vs mine |
|---|---|
| `stage3_hycsim/policies/psc_vro.py` | 0 lines |
| `stage3_hycsim/policies/novel.py` | 0 lines |
| `stage3_hycsim/metrics.py` | 0 lines |
| `stage3_hycsim/llc.py` | 22 lines |

So "better" is the wrong question. It is a fork taken from a snapshot of my
framework, developed along a different axis: **it improved the harness, I
improved the science.** The two merge cleanly because the core is the same code.

---

## 3. Where it is genuinely better

Six things it has that I did not build, every one of them demanded by the audit:

1. **Single CLI with nine resumable stages** (`mfs.py doctor|prepare|destiny|
   nvmexplorer|hycsim|integrate|validate|provenance|all`). Cleaner than my
   `run_all.py`.
2. **Bounded-memory trace preparation.** Two passes: count records, then write
   straight into fixed-size mmaps. Mine builds Python lists and concatenates, so
   it peaks at full trace size in RAM. On ten 3 GB mixes that difference decides
   whether the job finishes.
3. **Trace provenance.** `profile.json` records a SHA-256 of the measured
   region, record counts, malformed-line count, cycle span and clock. The audit
   asked for trace hashes; I never added them.
4. **Provenance manifest.** `provenance.json` with config SHA-256, compiler,
   NumPy/Pandas/Python versions, tool paths.
5. **Per-tier read counters and an explicit trace-to-array mapping.**
   `GetS` → array read, `L2_Rpl` → fill/writeback, `GetX` reported separately
   rather than silently counted as a write. My model tracked writes only, so it
   could not compute read energy at all.
6. **The cross-stack join.** `cross_stack.py` combines HyCSim wear, NVMExplorer
   array models and DESTINY into one CSV with dynamic energy, leakage energy,
   EDP, array area and projected first failure. My stages stayed separate and
   left the join to the reader.

---

## 4. Where it is behind

1. **No cycle-level performance at all.** There is no ChampSim stage and no IPC.
   It substitutes `cross_stack_serialized_latency_ms`, which sums
   per-access latency times access count. That assumes zero memory-level
   parallelism — no MLP, no MSHR overlap, no out-of-order tolerance. For a
   64-entry-MSHR core it overstates memory time by a large and workload-dependent
   factor. It is honestly named "serialized", but it cannot answer "what does
   this cost in IPC?", which is exactly the question the manuscript's 0.11%
   claim needs and exactly the arithmetic the audit says must be reconciled.
2. **No policy conformance tests.** Its two unit tests check trace parsing,
   mmap dtypes and tier accounting — plumbing. Nothing checks that VRO's window
   actually rotates, that DWAWR restricts the right ways, or that a mechanism
   stays dormant when it should. The audit's headline criticism of the earlier
   framework was "policy fidelity is inadequate", and that is untouched here.
3. **Missing the two named competitors the audit specifically calls for.** Its
   registry has i2WAP, EqualChance, Start-Gap and (the since-withdrawn) LiNoVo.
   It has no **AYUSH** and
   no **DWAWR**, which the audit names as the required comparison set and drafts
   conformance skeletons for.
4. **No `psc-soft`.** It cannot have it: that repair came from a ChampSim
   measurement it never made.
5. **No statistics.** No bootstrap CIs, no geometric-mean aggregation with
   intervals, no Spearman/Kendall proxy validation, no ranking-reversal count, no
   log-scale interaction contrast, no endurance sensitivity grid. The audit
   devotes an entire section to each of these. Its endurance is a single
   hardcoded `1.0e12`.

---

## 5. Defects found while verifying

| Severity | Location | Issue |
|---|---|---|
| Major | `cross_stack.py:59` | Way fractions are hardcoded `(0.75, 0.25)` for the hybrid arena. Change `nvm_ways` and leakage and area silently become wrong, with no warning. Derive them from the row's `nvm_ways`/`sram_ways`. |
| Major | `cross_stack.py` energy | `cross_stack_total_energy_mJ` is dominated by leakage, which is computed from the fixed way split and is therefore **policy-independent**. In the verification run all four hybrid rows report 0.158991 mJ regardless of policy while their dynamic parts differ in the fourth decimal. As an "energy-to-solution" comparison across policies this discriminates almost nothing. Report dynamic and leakage separately, and state that leakage is a function of the array, not the policy. |
| Moderate | `cross_stack.py:37` | Endurance is a single default `1.0e12`. Reported SOT endurance spans 10^12 to >10^18; a single constant is what the audit explicitly rejects. Needs the grid. |
| Moderate | `cross_stack.py:50` | `duration` falls back from `measured_duration_s` to the NVMExplorer model's `duration_s` silently. Those are different quantities; if the first is missing, every energy and lifetime number downstream is quietly wrong. Fail loudly instead. |
| Minor | leakage scaling | Multiplying whole-array leakage by the way fraction assumes peripheral circuitry scales linearly with ways. It does not. Acceptable as an approximation, but say so. |

None of these invalidate the harness. All five are in the join, not the model.

---

## 6. On the sample results

Do not quote them, and the package says so itself. On 18,000 events at 1 MB the
peak frame write count is **8**, and `vro` in both arenas lands on exactly the
same 8 as `unmanaged` — the trace is too short for any policy to separate.
`projected_first_failure_days` of 44,017 (120 years) is arithmetically correct
and physically meaningless at that trace length. It is a plumbing check and
nothing more.

---

## 7. Verdict and merge plan

Neither package is a superset. Use its harness, keep my science.

**Port into it, from here:**

- `stage5_champsim/` and `ChampSim/replacement/psc_family/` — real IPC, and wear
  measured in the same run, which is the only way to settle the overhead
  arithmetic
- `stage3_hycsim/test_fidelity.py` — 30 conformance cases; wire it into `mfs.py
  doctor` so a result run cannot start on unverified policies
- `stage4_analysis/statistics.py` — bootstrap CIs, proxy validation, log-scale
  interaction, endurance grid
- `policies/ayush.py`, `policies/dwawr.py`, `policies/psc_soft.py`
- the `on_hit` hook in `llc.py` (AYUSH needs it; so does predictor training)

**Port into mine, from it:**

- `mfs.py` stage CLI and the config-file interface
- two-pass bounded-memory `prep_trace.py`
- `profile.json` trace hashing and `record_provenance.py`
- per-tier read counters in `llc.py`
- `cross_stack.py`, with the five defects above fixed

**Fix in both:** the hardcoded way fractions, the endurance grid, and the silent
duration fallback.

The merged package answers the audit on every axis except the ones neither
covers yet: full factorial decomposition, Monte Carlo over endurance CV, and
the virtual-queue controller.

---

# Addendum: fidelity re-verified against the primary papers

The reference library on the Transcend drive was opened after the review above
was written. Three re-implementations were checked line by line against the
papers themselves rather than against summaries. All three were wrong, and two
of them were wrong in the direction that penalises the competitor — the worst
possible direction for a comparison a reviewer is auditing.

## i2WAP (Wang, Dong, Xie & Jouppi, HPCA 2013)

**Swap-Shift was charged for data movement it does not perform.** Section 5.2 is
explicit: SwS "uses data invalidation instead of data movement during cache set
address re-mapping", and the swap "only exchanges the set IDs, and the data
stored in these two sets are simply invalidated (or written-back to the next
level of caches if dirty)". Both implementations migrated set contents and
charged 2 × NVM_WAYS physical writes per swap. Every point of i2WAP's reported
write amplification was an artefact of my code.

**PoLF counted the wrong event and kept the write.** Section 6.3: the counter
counts *write hits* over the whole cache, and on saturation the new data goes to
the write-back buffer "instead of writing it to the hit data block". The array
write is *avoided*. My version fired on fills and performed the write before
invalidating — removing the benefit while keeping the cost.

Also corrected: the paper's own evaluation uses FT = 10 and ST = 100,000; the
defaults were FT = 100. And PoLF deliberately does not reset the LRU age of a
flushed line, so it is not immediately refilled with the same data.

The ChampSim module now suppresses the write on a PoLF event, which is the
faithful form. The trace model cannot: the HyCSim grammar has no in-place write
hit, so only PoLF's redistribution effect is expressible there. That difference
is now documented in the policy file, and it is a concrete argument for why the
cycle-level stage exists.

## EqualChance (Mittal & Vetter, INFLOW 2014)

**It was given state it does not have, and the wrong redirection target.** The
paper keeps one `numWrite` counter and one `FlagBit` per set and makes a point
of the small storage cost. My version selected the globally least-written way,
which needs a per-way write counter the scheme never specifies — so it was
scored with better information than it has, and its reported storage overhead
was understated.

Algorithm 1 redirects to an **invalid** block first (I-shifting), otherwise to a
**clean** block (C-shifting), and performs a normal write if neither exists. It
is in-cache data movement, never invalidation, and the LRU age is not updated on
a shift. That is now what the code does, and the two shift kinds are counted
separately so the mechanism's behaviour is visible in the results.

## LiNoVo (Agarwal & Kapoor, ISVLSI 2020)

The paper is a multi-part summary, not one algorithm. Its Work-1 mechanism is a
*dataless-entry* placement scheme: private blocks get a tag-only entry in the
NVM partition, real data is written at the first write-back, and **an NVM block
migrates to SRAM only on the second write to it**, guided by a reuse-distance
aware write-intensity predictor (RDAWIP). Work-2 adds a victim cache with
access-based placement and dynamic region partitioning.

My implementation is a rotating write-window plus a victim buffer, which is
closer to the authors' earlier DWAWR line than to LiNoVo. **It is relabelled as
an approximation and must not be presented as LiNoVo** until the second-write
migration and RDAWIP are implemented. DWAWR is already present separately and
correctly, so the honest interim position is to report DWAWR as the
write-restriction competitor and mark LiNoVo as pending.

**Resolved.** `policies/linovo.py` has been deleted rather than rebuilt. "LiNoVo"
is the title of the thesis and of the ISVLSI 2020 paper, not the name of an
algorithm, so no policy should carry that name. The rotating-window mechanism the
file approximated is Chapter 4 of the thesis, now implemented faithfully as
`swwr` (Algorithm 1) and `dwwr` (Algorithm 2) in `policies/swwr.py`, and Sequoia
(Jokar et al., IEEE TVLSI 24(3), 2016) is implemented as `sequoia`. If the
dataless-entry / RDAWIP Work-1 mechanism is ever wanted, it should be added under
its own descriptive name, not under the paper's title.

## Effect on conclusions

The conformance suite is now 47 cases. Every earlier i2WAP and EqualChance
number in `docs/FINDINGS.md` was produced by the defective versions and must be
regenerated; i2WAP in particular was handicapped and will improve. Nothing about
the PSC/VRO findings changes, because those mechanisms were unaffected — but the
margin between the proposal and i2WAP will narrow, and that is the correct
direction.
