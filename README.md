# MFS — cross-stack evaluation of endurance-limited NVM last-level caches

Reference implementation for **"A Multi-Factor Scaling and Lifetime-Extension
Framework for Endurance-Limited NVM Caches: An SOT-MRAM Case Study"**
(Kallinatha H D, Basavaraj Talawar; *Sustainable Computing: Informatics and
Systems*, in revision). Five pipeline stages, four simulators, one script —
and it is not SOT-MRAM-specific: the orchestration loop, the diagnostic
metrics, and the PSC/PSC-Soft placement rules take a cell's measured
behaviour as input and can be pointed at a different NVM cell, a different
trace set, or a different wear-management policy without touching the
framework itself (see "Using this for other work" below).

```bash
git clone https://github.com/REPLACE_WITH_OWNER/mfs.git && cd mfs
./setup.sh                # builds bundled DESTINY, fetches NVMExplorer  (~5 min)
./run_all.sh --smoke      # proves the pipeline on a bounded slice       (~15 min)
./run_all.sh              # the study
```

Add `./setup.sh --with-champsim` for stage 5. Use `./setup.sh --offline` to
build only what is bundled — stages 1, 3 and 4 and the conformance suite all
work with no network.

Full instructions: **`docs/RUNNING.md`**. To check a specific number in the
paper against the data in this repo without running anything, see
**`results/paper_final/README.md`** — it maps every table and figure to the
CSV/JSON that produced it.

```
stage 1  DESTINY v2      cell + array design space  -> latency, energy, leakage, area
stage 2  NVMExplorer     independent array models   -> per-access energy, latency, area
stage 3  HyCSim-compat   trace replay               -> wear, IntraV/InterV, direct lifetime
stage 4  analysis        join + statistics          -> CIs, proxy validation, endurance sweep
stage 5  ChampSim        cycle-level                -> IPC and wear from the SAME run
```

## What is bundled

DESTINY v2 (964 kB of BSD source, licences intact) and the HyCSim reference
model (seven files, used only by the bit-exact conformance test). NVMExplorer
and ChampSim are fetched by `setup.sh` — they are git repositories with
submodules and vendoring them would be enormous and stale within a month. See
`tools/README.md`.

Traces stay where they are; nothing is copied.

## Why the results here are defensible

**Bit-exact against HyCSim.** 1,000,000 real trace records, 16,384 of 16,384
frames identical to the official `BaseHybrid` model — not just the aggregates,
the whole per-frame write vector. `tests/test_hycsim_conformance.py`.

**The time base is verified, not assumed.** The first trace column is a gem5
tick at 1 ps, not a clock cycle. `prepare` resolves which and cross-checks it
against the trace's own `Sim_seconds` header, then stops if they disagree.
Reading it wrong scales every energy rate and every lifetime by 500×.

**Cell files cite their sources per line.** 20 published parameter sets from 11
papers in `sources.json`; `from_paper.py` validates each against DESTINY's
grammar *and* against physics, then emits `.cell` and NVMExplorer cells whose
headers say where every number came from and which were assumed.

**Endurance is a cited sweep.** STT-MRAM pinned at the community-standard
4×10¹²; SOT-MRAM swept 10¹²–10¹⁵ because it has no settled value.
`config/endurance.json`, `docs/ENDURANCE.md`.

**Two arenas, always both.** Every scheme runs on an all-NVM 16-way array *and*
a 12 NVM + 4 SRAM hybrid of equal capacity, each against its own baseline. A
hybrid proposal never gets credit for merely having SRAM ways.

**Three selection modes.** Per-mix tuning is an oracle ceiling; leave-one-mix-out
is what a design could ship; global is the most conservative. All three are
reported so the gap is visible.

**Every physical write is charged**, including the migration and swap traffic a
mechanism performs itself, asserted per policy in the test suite.

**Direct lifetime is primary.** `RLI_direct = max_b/max_p − 1` from the peak
frame write count. The manuscript's closed-form proxy is reported beside it and
validated against it — and it fails: ~70–80% median error, 14–18% of pairwise
rankings reversed, on two independent traces.

**134 conformance cases across 20 policies**, and `doctor` will not pass without
them. Competitors are re-implemented from published descriptions, not author
code; every deviation is named in the policy's docstring with its direction.

## The new algorithm

`psc-balance` came out of a measurement that contradicted the manuscript's own
composition claim: **plain PSC beats every mechanism built on top of it**,
because VRO concentrates the intra-set wear PSC has already levelled. The
remaining headroom is inter-set, and every published inter-set leveller here pays
migration writes for nothing on this workload.

PSC-Balance equalises inter-set wear using the SRAM partition as the levelling
channel — a hot set diverts more of its allocations into SRAM. It moves nothing,
remaps nothing, charges **zero migration writes**, and helps under **both**
simulator protocols:

| | peak wear | interV | migration writes |
|---|---|---|---|
| unmanaged | 33 | 0.0971 | 0 |
| psc | 16 (+106%) | 0.0868 | 0 |
| **psc-balance** | **9 (+267%)** | **0.0204** | **0** |

ChampSim, 605.mcf: NVM writes −41% against LRU, interV −39%, best IPC of the
four. Full argument and both honest limits in `docs/PSC_BALANCE.md`.

## Using this for other work

The framework is built in the shape it needs to be for that portability claim
to be checkable, not just stated. Two extension points cover most re-use:

**A different cell or technology.** `stage1_destiny/cells/sources.json` holds
one record per published cell, each tagged `reported` / `derived` / `assumed`
with a citation string. `from_paper.py --emit-all` turns a new record into a
DESTINY `.cell` file and an NVMExplorer cell whose header states where every
number came from; nothing about stages 2–6 needs to change. This is the same
principle the cross-technology projection (`stage6_transfer/`, Table/Fig. in
the paper's portability section) already exploits: it re-scores the measured
per-scheme write vectors against a different cell's constants — no
re-simulation, milliseconds instead of another full replay — and its
`--validate` flag reprojects a technology that *was* simulated and diffs the
result against the real pipeline output, so a projection cannot silently
drift from what stage 4 would actually compute. See `docs/CELLS.md`.

**A different wear-management policy.** Add one file to
`stage3_hycsim/policies/` (the interface is whatever the 22 existing policies
there already implement) and, if you also want cycle-accurate validation, its
counterpart in `stage5_champsim/module/psc_family.{h,cc}` selected via the
`PSC_POLICY` environment variable. `docs/POLICY_COVERAGE.md` documents every
existing mechanism's status in both simulators as a template for what a new
one needs to match. The acceptance guard, the LOO/global/per-mix selection
protocol, and the Wilcoxon+Holm-Bonferroni comparison in `stage4_analysis/`
apply to any policy added this way without modification — that machinery is
what makes a new mechanism's numbers checkable against the published
baselines already implemented here (AYUSH, EqualChance, i2WAP, Start-Gap,
SWWR, DWWR, DWAWR, Sequoia), rather than only against your own prior work.

A different trace set drops in at `traces/` and `config/*.json`
(`traces.raw_dir`, `traces.pattern`); `docs/DRY_RUN.md` covers the time-base
verification step that most silently wrong lifetime numbers come from.

## What is not in this repo

Raw and prepared trace files (hundreds of MB to low GB; point
`config/*.json`'s `traces.raw_dir`/`traces.prepared_dir` at your own copy).
NVMExplorer and ChampSim source (fetched by `setup.sh` from their own
upstream repositories — vendoring a multi-hundred-MB git history with
submodules would make this repository unreviewable). Anything under
`results/` other than `results/paper_final`, `results/sample`, and
`results/smoke` is local run output and is git-ignored by design.

## Known limits

Stated here rather than discovered in review. Re-implementations are not author
artefacts. Serialized array time assumes zero memory-level parallelism — use
stage 5 for anything performance-related. Leakage and area scale linearly with
way count in the join. Damage-model parameters are templates. Replay starts from
an empty cache. Not done: full factorial decomposition, Monte Carlo over
endurance CV, warm-start sensitivity.

See `docs/`: `RUNNING.md`, `RUN_PLAN.md`, `POSITIONING.md`, `CELLS.md`,
`ENDURANCE.md`, `DRY_RUN.md`, `ELBA_DESIGN.md`.

If `./setup.sh --with-champsim` fails, read `docs/CHAMPSIM_BUILD.md` before
installing anything — the failure is almost certainly not a missing package.
