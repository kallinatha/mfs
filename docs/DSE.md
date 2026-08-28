# Cache-level design-space exploration

The manuscript's weakest position is a cell-level one: "SOT-MRAM has better
numbers than STT-MRAM". Eleven papers already say that, they disagree with each
other by 5.3× on cell area, and a reviewer can pick whichever one contradicts
you. This stage moves the claim to ground you can defend — **the array, not the
cell** — which is also where DeepNVM++ and Singh et al. put theirs.

`stage1_destiny/dse.py`, run as part of `./run_all.sh` (stage 2 of 13).

---

## 1. What is swept, and what deliberately is not

**Swept: the knobs a cache architect controls.** All values verified against
`destiny_v2/InputParameter.cpp` rather than guessed — DESTINY silently ignores a
key it does not recognise, so an unverified value produces a dimension that
looks swept and does nothing.

| knob | values | why it is in |
|---|---|---|
| `OptimizationTarget` | ReadLatency, WriteLatency, ReadDynamicEnergy, WriteDynamicEnergy, ReadEDP, WriteEDP, LeakagePower, Area, Full | Nine different arrays from one cell. Most papers report one and never say which. |
| `WriteScheme` | ResetBeforeSet, SetBeforeReset, ReadAndCompare, WriteAndVerify | Swept and **measured inert** — 1.000x on every objective for MRAM cells. DESTINY does not model differential write on this path, so read-before-write is not evaluable here. Reported as a negative result, and dropped from the factorial. |
| **`StackedDieCount`**, `MonolithicStackCount` | 1, 2, 4 | **3D stacking** — DESTINY's headline capability. Stacking shortens the global wires that dominate a large NVM LLC, which is exactly where the manuscript's 32 MB inversion comes from. Unused so far. |
| **`AllowDifferentTagTech`** | false, true | **SRAM tags over NVM data** — the split POEM assumes and this manuscript has never modelled. |
| `CacheAccessMode` | Normal, Sequential, Fast | Tag–data serialisation against energy. |
| `DeviceRoadmap` | HP, LSTP, LOP | The leakage lever. |
| `Routing`, wire types, repeaters, low swing | see `KNOBS` | Dominates latency and energy at large capacity. |
| `BufferDesignOptimization`, `InternalSensing`, associativity, capacity, node | | |

**Not swept: the cell electricals.** They come from published devices through
`cells/from_paper.py`. Inventing a cell to win a comparison is the one move this
project refuses, and a reviewer checking `sources.json` can see that.

The exception is **layout** — cell area and aspect ratio — swept in `--geometry`
mode, because Zhang et al. establish that as a design choice rather than a
device claim: a 25 F² cell at aspect ratio 1.0 is 5F × 5F; at 0.5 it is
7.07F × 3.54F. Same device, different floorplan.

---

## 2. Four modes

```bash
python3 stage1_destiny/dse.py --screen   --cells sram,sot_57_singh2020 --capacity 16
python3 stage1_destiny/dse.py --grid     --dims OptimizationTarget,WriteScheme,DeviceRoadmap
python3 stage1_destiny/dse.py --geometry --cells sot_57_singh2020 --capacity 1,2,4,8,16,32
python3 stage1_destiny/dse.py --scale    --cells sram,sttram_54_singh2020,sot_57_singh2020
python3 stage1_destiny/dse.py --pareto   results/dse/grid.csv
```

**`--screen`** — one factor at a time from a declared baseline, reporting an
effect size per knob per objective and labelling each **DOMINANT / matters /
inert**. OFAT cannot see interactions, which is precisely why it is a filter for
the factorial and not the method. A knob that moves nothing is a reportable
negative, not a reason to keep grinding it.

**`--grid`** — full factorial over the survivors. It **refuses** a factorial
larger than `--budget` rather than silently sampling, because a quietly
truncated search reported as a search is a fabrication.

**`--geometry`** — Zhang et al. 2021 Tables 3 and 4: cell area {10, 15, 20,
30} F² at aspect 1.0, and aspect {0.3, 0.5, 1.0, 2.0} at the base area, across
1–32 MB. **With SOT added**, which their study does not have.

**`--scale`** — Zhang et al. 2021 Figure 6: the 65 → 22 nm roadmap at cache
level, HP and LOP. Their paper is *"Cache performance of NV-STT-MRAM with scale
effect and comparison with SRAM"* — STT against SRAM only. Adding the SOT row is
a direct, checkable extension of a published study, which is a far stronger
position than another standalone comparison.

Every point is cached on a hash of its exact config, so the search grows
incrementally and a re-run costs nothing for ground already covered.

---

## 2b. Measured screen (195 points, SOT 24 F², 1 MB, 45 nm)

Worst-case ratio against the baseline design. This is the ordered list a paper
should report, because it says which levers matter and which do not:

| knob | wr latency | wr energy | leakage | area | verdict |
|---|---|---|---|---|---|
| **OptimizationTarget** | **156.6×** | 2.93× | 3.27× | 2.11× | DOMINANT |
| DeviceRoadmap | 1.87× | 2.60× | 0.025× | 2.87× | DOMINANT |
| GlobalWireType | 7.69× | 1.13× | 1.04× | 1.16× | DOMINANT |
| GlobalWireUseLowSwing | 6.66× | 0.81× | 1.60× | 1.49× | DOMINANT |
| Routing | 0.97× | 1.29× | 2.36× | 2.42× | DOMINANT |
| GlobalWireRepeaterType | 1.40× | 3.09× | 2.13× | 2.28× | DOMINANT |
| StackedDieCount | 1.01× | 1.75× | **2.91×** | 1.84× | DOMINANT |
| CacheAccessMode | 1.99× | 1.41× | 1.81× | 1.34× | DOMINANT |
| Associativity | 1.04× | 1.50× | 1.74× | 1.64× | matters |
| MonolithicStackCount | 1.06× | 1.28× | 1.83× | 1.37× | matters |
| LocalWireType | 1.05× | 1.11× | 1.14× | 1.16× | matters |
| BufferDesignOptimization | 1.08× | 1.08× | 0.85× | 1.11× | matters |
| **WriteScheme** | 1.000× | 1.000× | 1.000× | 1.000× | **inert** |

Three things to take from it.

**The optimisation target dominates everything, by two orders of magnitude.**
A 156× spread in write latency from the same cell, purely by changing what
DESTINY is asked to minimise. Any paper that reports one array number without
stating its optimisation target has not reported a number at all. That alone
justifies this stage.

**`DeviceRoadmap` cuts leakage 40× (0.025×) while costing 1.9× write latency and
2.9× area.** That is the cleanest trade in the whole space and it is exactly the
lever a large NVM LLC needs.

**`WriteScheme` is inert.** DESTINY's MRAM path does not differentiate write
schemes, so read-before-write — a real silicon mechanism — is invisible to this
tool. Report it as a modelling limitation, not as evidence the mechanism does
not help.

## 3. The objective that makes this novel

DESTINY optimises latency, energy, EDP, leakage or area. **None of those is
lifetime.** For an endurance-limited cache that is the wrong objective set, and
the gap is the contribution.

Derived alongside the standard metrics:

- `write_edp` — write latency × write energy
- `leakage_per_MB` — so capacities compare
- `writes_per_joule` — write work per unit energy. Under a fixed energy budget
  this is proportional to the writes the array can deliver.
- **`lifetime_index` = endurance / (write_energy × leakage_per_MB)** — rewards a
  design that both writes cheaply *and* idles cheaply, which is what an
  endurance-limited LLC needs and what neither a pure-energy nor a pure-latency
  target selects.

`lifetime_index` is a **ranking device, not a physical quantity**, and the code
says so wherever it prints it. The physical lifetime comes from stage 8's
endurance sweep against measured per-frame wear; this index exists to *choose an
array*, not to quote a number.

`--pareto` prints the non-dominated set over (write energy, write latency,
leakage, area), the best design per objective, and then the sentence the stage
exists to produce:

> The endurance-aware winner and the minimum-write-energy design are DIFFERENT
> points.

If that holds on your cells — and it should, because leakage and write energy
pull opposite ways through the roadmap and sensing choices — then a paper
reporting only the energy-optimal array is reporting the wrong design for an
endurance-limited cache. That is a cache-level result, it does not depend on
which SOT cell area you believe, and it survives the 5.3× disagreement in the
device literature.

---

## 4. How to write it up

1. **Lead with the array, not the cell.** "We show that the array configuration
   which minimises write energy is not the one that maximises lifetime, and that
   the gap is X% at 16 MB." Cell-level numbers become a *sensitivity band*, not
   the claim.
2. **Report the Pareto front**, not one point. `dse_pareto.csv`.
3. **Name the knob that mattered.** Screening gives an ordered list with effect
   sizes; the honest headline is whichever knob is DOMINANT, even if it is an
   unglamorous one like the wire type.
4. **Put the scaling roadmap beside Zhang's.** Same tool, same study design,
   same node list, SOT added. Any discrepancy on the STT rows is a validation
   result worth reporting either way.
5. **State what was inert.** Knobs that moved nothing at your capacity are a
   finding: they tell a reader which levers not to bother with.

---

## 5. Cost, and how to bound it

DESTINY takes roughly 90–320 s per design point at 1–16 MB. Screening the full
knob list on three cells at one capacity is ~150 points; the default factorial
(`OptimizationTarget × WriteScheme × DeviceRoadmap`) is 108 combinations per
cell per capacity. Budget overnight for the production settings in
`config/suscom_ten_mixes.json`, and use `experiment.dse_modes` to run only what
you need:

```json
"dse_modes": "screen,grid,geometry,scale",
"dse_cells": "sram,sttram_54_singh2020,sot_57_singh2020,sot_24_2,stt_zhang2021_45nm",
"dse_capacities": "1,4,16,32",
"dse_workers": 4
```

`--smoke` runs `screen` only, at 1 MB, on one cell.

Raise `dse_workers` cautiously: DESTINY is single-threaded but memory-hungry at
32 MB, and oversubscribing turns a four-minute sweep into an hour.
