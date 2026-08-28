# Pre-run verification: what I tested, what broke, what is ready

Everything below was executed, not planned. The trace used is the **official
HyCSim `example_trace.txt`** — a real gem5 export, not a synthetic stand-in —
sliced into two disjoint 900,000-record mixes so the multi-mix machinery gets
exercised end to end.

---

## 1. The 500× time-base error

**Found, fixed, and it would have invalidated every energy and lifetime number
in the paper.**

The first column of a HyCSim trace is a **gem5 tick, and gem5's default tick is
1 picosecond** — not a clock cycle. The pipeline was dividing it by
`clock_hz = 2e9`. Duration divides into every energy rate and multiplies into
every years-to-failure figure, so the error passes straight through to the
headline numbers without ever looking wrong on its face.

The trace states the answer itself. Its first line reads
`Sim_seconds: 0.058413 Hits: ... Misses: ...`, and a prefix holding 12.77 M of
the run's records spans 34.93 × 10⁹ units:

| reading | implied duration | share of the run's own Sim_seconds |
|---|---|---|
| **1 ps gem5 tick** | 0.0349 s | **59.8%** — consistent with holding ~60% of it |
| cycles at 2 GHz | 17.46 s | 29,898% — impossible |
| cycles at 1 GHz | 34.93 s | 59,796% — impossible |

On the 1 M-record slice the correction moves `duration_s` from 1.58 s to
**0.00316 s**, and the GetS rate from 236 k/s to **118 M/s** — the second is the
plausible figure for a four-core LLC.

**What changed.** `prep_trace.resolve_tick_rate()` now decides the time base and
cross-checks it against the `Sim_seconds` header, recording the arithmetic in
`profile.json` under `tick_rate_evidence`. If neither reading accounts for
≤150% of the header, preparation **stops** rather than guessing. `run_mixes.py`
now reads `tick_rate_hz` and raises if it is absent, so an old `profile.json`
cannot silently reintroduce the bug.

**This also removes the blocker.** You no longer need to tell me the gem5 clock —
the trace carries enough to derive and verify it. Check
`profile.json → tick_rate_evidence.decision` after `prepare` and confirm it says
ticks.

---

## 2. Full pipeline, end to end, on a real trace

```
prepare    → 2 mixes, 850,000 measured records each, 0 malformed, markers found
hycsim     → 16 policies × 2 arenas × 2 mixes, disjoint 150 k tuning prefix
integrate  → 42 rows joined, 0 rejected, assumptions.json written
statistics → bootstrap CIs, proxy validation, PSC×VRO log interaction
validate   → FAILED, correctly (see below)
```

The validator did its job: it refused the run because `psc` and `vro` on MIXB
fell back — no configuration passed the hit-rate and NVM-utilisation guard — so
those rows carry `selection_guard_passed=0` and are disqualified. That is the
degenerate-optimum guard working, caught automatically, before anything reached
a table.

Two results worth noting from the dry run, on two mixes and therefore indicative
only:

- **The RLI proxy again fails.** Spearman 0.710, Kendall 0.609, **median error
  79.3%**, and **127 of 703 pairwise rankings reversed (18.1%)**. That is a
  second independent trace agreeing with the first (69% / 14.1%). The proxy is
  not safe as a comparison metric, and this is now a robust finding rather than
  a one-trace curiosity.
- **PSC × VRO composes sub-multiplicatively**, I = −0.294. The manuscript's
  super-additivity claim does not survive here either.

---

## 3. Leave-one-mix-out tuning — the oracle gap is now measured

The verified package listed this as an open limitation: *"Per-mix prefix tuning
is an oracle-style upper bound. Use a global setting or leave-one-mix-out
selection for an implementable claim."* It is implemented.

`run_mixes.py --selection per-mix,loo,global` now runs all three and writes
`aggregate_loo.csv` and `aggregate_global.csv` beside the per-mix one:

- **per-mix** — parameters chosen knowing the mix they are scored on. A ceiling,
  not a claim; nobody tunes per workload at design time.
- **loo** — tuned on every *other* mix. The implementable number, and the one to
  headline.
- **global** — one parameter set for all mixes. The most conservative.

Pooled scores are averaged across mixes by mean RLI, so a configuration that
wins on one mix and collapses on another cannot be selected on its best day.

Measured (two mixes, so LOO here means "tuned on the other one"):

| scheme | arena | per-mix | loo | global | oracle gap |
|---|---|---|---|---|---|
| psc | hybrid | 109.5% | 109.5% | 109.5% | 0.0 |
| psc-vro | hybrid | 100.0% | 100.0% | 100.0% | 0.0 |
| vro-rank | all-nvm | 35.5% | 35.4% | 41.6% | 0.2 |
| vro | all-nvm | 27.8% | 27.8% | 27.8% | 0.0 |
| vro-rank | hybrid | 8.6% | 4.7% | 9.9% | **3.9 pts** |
| i2wap | hybrid | −1.2% | **+2.2%** | +2.2% | **−3.4 pts** |
| vro | hybrid | −11.9% | −16.8% | −23.3% | **4.9 pts** |

**The gap is zero for the schemes that matter.** PSC's result is not an artefact
of per-mix tuning, which is the answer to the obvious reviewer challenge. The
exceptions are VRO and vro-rank on the hybrid arena, both already weak, and
i2WAP — where leave-one-out *improves* the competitor by 3.4 points, meaning
per-mix tuning had picked a bad configuration for one mix. Report LOO.

---

## 4. Cell files through the real DESTINY binary

19 of 20 published parameter sets emit a `.cell`; **14 produce a valid array**
at 1 MB / 45 nm / 16-way. The six failures are recorded with causes in
`docs/CELLS.md`, not hidden. `sweep.py --include-generated` sweeps them
alongside the hand-written cells, tagged `gen:<id>` in the CSV so the two
provenance classes never blur together.

---

## 5. Ready to run

```bash
python3 mfs.py doctor      --config config/suscom_ten_mixes.json
python3 mfs.py prepare     --config config/suscom_ten_mixes.json
#   → then read profile.json: tick_rate_evidence.decision must say ticks
python3 stage3_hycsim/profile_writes.py <prepared>/MIX1 --size-mb 1
python3 mfs.py destiny     --config config/suscom_ten_mixes.json
python3 mfs.py nvmexplorer --config config/suscom_ten_mixes.json
python3 mfs.py hycsim      --config config/suscom_ten_mixes.json
python3 mfs.py integrate   --config config/suscom_ten_mixes.json
python3 mfs.py statistics  --config config/suscom_ten_mixes.json
python3 mfs.py validate    --config config/suscom_ten_mixes.json
```

Config already points at
`/media/kallinath/UBUNTU-ONLY/HyCSim-SPEC-2006-gem5-traces/exported_traces`,
with prepared output on the same volume. Confirm 20–30 GB free there.

**Watch for three things.**

1. `tick_rate_evidence.decision` after `prepare`. If it does not say ticks, stop
   and send me the block — the time base is the highest-leverage number in the
   run.
2. `selection_guard_passed` in the lifetime CSVs. Any zero disqualifies that
   row; it means no configuration held hit rate and NVM utilisation.
3. The `loo` column, not the `per-mix` one, for anything that goes in the paper.

---

## 6. What is still not done, and why

- **Warm-start sensitivity.** Replay begins from an empty cache at the first
  measured record. On 850 k records the transient is small relative to the run,
  but it is unquantified. One mix run twice — once as-is, once with the warm-up
  region replayed into the array before counters reset — would settle it. Not
  done; it needs a real mix, and the ten mixes are on your machine.
- **`swwr` and `dwwr` report identical numbers** in this dry run, to four
  decimal places on both arenas. On a separate 400 k-record smoke they differed
  (746 versus 953 redirects), so this is probably the tuning grid collapsing
  them rather than a code defect — but check it on the ten mixes before
  reporting both as distinct rows.
- **FINDINGS.md still holds numbers from the defective policies.** Regenerating
  it needs the real mixes. Treat every figure in it as void until the run
  completes.
- **Endurance is still unpinned.** Reported STT/SOT values span 10⁶ to 10¹⁵.
  That is why lifetime is reported as a four-point grid rather than a number.
