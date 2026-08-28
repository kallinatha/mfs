# Cell files: from paper to `.cell`, with the citation attached

The weakest link in every NVM architecture paper is the cell file. A reviewer
can check your simulator, your traces and your statistics, but the numbers that
set every latency, energy and area figure usually appear as bare literals in a
config file with nothing to say where they came from. This project shipped three
SOT cells in exactly that state.

That is now fixed structurally rather than by discipline.

```
cells/sources.json     20 published parameter sets from 11 papers. One record
                       per set. Every field carries a provenance string and a
                       kind: reported / derived / assumed. Data, not code.
cells/from_paper.py    validates a record against DESTINY's grammar and against
                       physics, then emits a .cell whose header states, line by
                       line, where each number came from.
cells/generated/       the emitted files. Regenerate; never hand-edit.
```

```bash
cd stage1_destiny/cells
python3 from_paper.py --list
python3 from_paper.py --check
python3 from_paper.py --compare "CellArea (F^2)"
python3 from_paper.py --emit-all --complete \
        --complete-from sot_hanjiang_aipadv2024_16nm --auto-read -o generated
cd .. && python3 sweep.py --destiny-dir $DESTINY_DIR --include-generated
```

---

## 1. The headline result: SOT cell area is a design choice, not a constant

`--compare "CellArea (F^2)"` over 11 papers:

| | n | min | median | max | spread |
|---|---|---|---|---|---|
| SOT-MRAM | 8 | **18 F²** | 32 F² | **96 F²** | **5.3×** |
| STT-MRAM | 5 | 16 F² | 40.3 F² | 54 F² | 3.4× |
| SRAM | 1 | 146 F² | — | — | — |

More decisive is the **within-paper** ratio, where layout assumptions cancel:

| Paper | SOT | STT | SOT / STT |
|---|---|---|---|
| Wu 2020 (TCAS-I), 2T1SOT | 18 | 40.33 | **0.45×** |
| Wu 2020, 3T2SOT field-free | 27 | 40.33 | 0.67× |
| Seo 2020, proposed | 31.8 | 41.1 | 0.77× |
| Liao 2020 (JXCDC) | 20 | 16 | 1.25× |
| Seo 2020, conventional | 54.5 | 41.1 | 1.33× |
| Hanjiang 2024 / Singh 2020 | 57 | 54 | 1.06× |
| Saha 2022 (JMMM), θ_SH = 1.4 | 32 | 16 | 2.0× |
| Saha 2022, θ_SH = 0.3 | 96 | 16 | **6.0×** |

**So the earlier conclusion was too strong.** "SOT is larger than STT" is not
general; it is what you get from one class of assumptions. The ordering tracks
two things, and they are the two things a paper must state:

- **the spin Hall angle of the SOT channel.** Saha's own two rows are the clean
  experiment: same paper, same node, same MTJ, θ_SH = 1.4 gives 32 F² and
  θ_SH = 0.3 gives 96 F², because a weaker spin Hall effect needs more write
  current and therefore a wider write transistor. A factor of three in cell
  area from one material parameter.
- **the cell topology.** SOT needs separate read and write paths, so 2T1SOT or
  3T2SOT against STT's 1T1M. `from_paper.py` parses the transistor count out of
  the recorded geometry rather than guessing it from the technology name,
  because miscounting transistors is the most common error in SOT area claims.

**What to write.** Do not claim a SOT area advantage unconditionally, and do not
concede one either. State the channel material and the spin Hall angle, report
the 18–96 F² published range, and run your sweep at three points across it. Then
make the argument you can actually defend: the **write path** advantage is
robust across every one of these papers — sub-nanosecond pulses and lower
switching current in all eight SOT rows — whereas area is contested.

---

## 2. The published parameter sets do not all close

Feeding the papers' own numbers to a real array model exposes inconsistencies
the papers never had to face.

**Read bias.** Singh/Hanjiang give SOT-MRAM a read current of 54.02 µA and
R_on = 16.5 kΩ. That biases the junction at **0.89 V**, which is above the
supply and far above any safe MTJ read bias. DESTINY refuses it outright:

```
Error[Subarray]: Read current too large or too small that no reasonable
                 precharge voltage existing
```

The paper is not wrong so much as incomplete: 54.02 µA is the *write* current,
reused in a table row that also has to serve as the read current. `--auto-read`
solves the pair of constraints instead —

```
I × R_on           ≤ 0.2 V     no read disturb, and a precharge voltage exists
I × (R_off − R_on) ≥ MinSenseVoltage
```

— takes the largest current respecting the bias ceiling, and records the
arithmetic in the emitted line as `derived`. Both constraints are satisfiable
only when TMR ≥ margin / bias, which is 12.5% at 0.2 V and 25 mV; every MTJ here
clears that as SLC.

**Transistors that do not fit.** A device of width W needs roughly 3W F² of
footprint. `--check` flags any cell whose declared area cannot hold its declared
access transistors. DESTINY's own shipped STT cell fails this by a factor of
eight — 6 F² with an 8.5 F access transistor — and the tool says nothing.

**Energy that does not reconcile.** Where current, pulse and energy are all
given, the validator compares the energy against I²·R_on·t and flags a
discrepancy beyond 10×. That usually means the three numbers come from different
operating points in the paper.

---

## 3. The hand-made cells were optimistic, and not where you would guess

Same DESTINY run, 1 MB, 16-way, 45 nm, HP, WriteEDP:

| cell | area mm² | write ns | leakage mW |
|---|---|---|---|
| `SOT-24/2` (hand-made, 24 F²) | 2.14 | 0.70 | 846 |
| `gen:sot_wu2020_2t1sot_45nm` (18 F², cited) | 3.99 | 1.34 | 1,679 |

The cited cell declares a **smaller** area yet produces nearly double the array
area and leakage. Geometry is not the driver; the electricals are — R_on 6250 Ω
against 5000 Ω, a 3 F access transistor against 2.5 F, and an aspect ratio of
0.57 against 2.0.

**Caveat, and it is the point of the tooling.** That aspect ratio was *borrowed*
from another paper, because Wu et al. do not print one — and the emitted cell
says so on the line, in the header, and in `--check`. Part of that 1.9× is
therefore my assumption, not their measurement. Run the aspect-ratio sensitivity
before quoting this comparison. Previously this ambiguity existed too; the
difference is that now it is visible.

---

## 4. MLC

`--mlc 4` synthesises a four-level MRAM cell. DESTINY models MLC MRAM as a
*hard* and a *soft* switching event (`-SoftSetCurrent` and friends): two stacked
free layers flip at different currents, and the soft layer carries the low-order
bit. **No device paper in `sources.json` publishes both**, so the soft transition
is scaled from the hard one by an exposed ratio and every synthesised field is
marked `assumed`, in the file.

```bash
python3 from_paper.py --emit sot_wu2020_3t2sot_fieldfree_45nm \
        --complete --auto-read --mlc 4 --soft-ratio 0.45 -o generated
```

Levels above 4 are refused: two stacked free layers give four states, and more
would need a device model DESTINY does not have. For MLC PCM and RRAM, DESTINY
uses `-AverageIterations` (write-verify loops) instead; set it from the paper,
because there is no defensible way to guess it.

**The MLC cell currently fails DESTINY's sense-amplifier solver**, and that is
probably a result rather than a bug: resolving four levels needs roughly three
times the sense margin of two, so the TMR that comfortably supports SLC does
not automatically support MLC. Before reporting any MLC row, sweep TMR and
`--soft-ratio` and show where the boundary is. Do not report a single MLC point.

---

## 5. Status of the 20 records

19 of 20 emit; `review_wang2017` is a materials review with no cell parameters
and is retained as a record of that fact. Through DESTINY at 1 MB / 45 nm,
**14 of 20 produce a valid array**. The six failures split into three causes,
all recorded rather than papered over:

- **two** (`cubukcu2018` ×2) have no cell area at all — the paper is a device
  measurement, not a memory design. Kept in `sources.json` for their switching
  data, which is the fastest measured SOT switching in the set (550 ps).
- **three** (`liao2020` ×2, `stt_wu2020`) fail DESTINY's bank organiser at this
  capacity and word width, not its cell parser. Their small cells (16–20 F²)
  need a different subarray organisation; retry at other capacities or with
  `-EnablePruning: No` and a longer budget.
- **one** is the MLC cell above.

`--complete` fills required-but-missing fields, tagging each; `--complete-from
<id>` names a donor record in the emitted header; `--no-defaults` refuses the
DESTINY sample fallback entirely, for when you would rather have a gap than a
borrowed number.

---

## 6. What still needs your input

1. **Endurance.** Nothing in these papers pins it, and reported STT/SOT values
   span 10⁶ to 10¹⁵ — nine orders of magnitude, and years-to-failure scales
   linearly with it. This is why `statistics.py` reports a lifetime *grid* and
   not a single number. If any of the papers you have left gives a measured
   cycling figure with its test conditions, it is the single most valuable
   number you can still add.
2. **Which SOT point is yours.** Name the channel material and spin Hall angle
   of the device your manuscript claims, and I will make that the primary cell
   with the other published points as the sensitivity band.
3. **A 22 nm or 16 nm STT/SOT pair from one paper**, if you have one. Most of
   the set is 40–45 nm; the manuscript's roadmap argument would be stronger with
   a same-paper comparison at an advanced node.
