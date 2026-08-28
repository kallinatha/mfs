# Policy and mechanism coverage: HyCSim replay vs ChampSim

Which mechanism exists in which simulator, and for anything present in only one,
why. Nothing here is aspirational: a row marked "yes" is code that runs.

*Replay* = `stage3_hycsim/policies/*.py`, driven by HyCSim's `BaseHybrid`
semantics.
*ChampSim* = `stage5_champsim/module/psc_family.{h,cc}`, selected at run time
through `PSC_POLICY`.

## Status: full parity

**22 mechanisms, both simulators, no gaps.** Every policy that exists in the
trace replay exists in ChampSim and vice versa. The rows below record how each
was made to work under the other simulator's protocol, and — where the event
model forced a genuine behavioural difference — what that difference is and
which way it cuts.

| mechanism | replay | ChampSim | note |
|---|---|---|---|
| `unmanaged` / `lru-hybrid` | yes | `lru` | controls |
| `psc`, `vro`, `psc-vro`, `vro-rank`, `psc-pred`, `psc-vro-rank`, `psc-soft`, `psc-soft-rank` | yes | yes | manuscript mechanisms and repairs |
| `wear-swap`, `full` | yes | yes | this project's composites; `full` composes its constituents rather than duplicating them |
| `i2wap`, `equalchance`, `startgap`, `ayush`, `dwawr`, `sequoia`, `swwr`, `dwwr` | yes | yes | published competitors |
| `elba` | yes | yes | proposed mechanism, three ablatable channels |
| **`tailguard`** | **yes (new)** | yes | needed the damage model below; was ChampSim-only |
| **`psc-balance`** | **yes (new)** | **yes (new)** | this project's new algorithm — inter-set levelling at zero migration cost. See `docs/PSC_BALANCE.md`. |

## What closed the last two gaps

**SWWR/DWWR were never actually blocked.** The old excuse — "cycle-clocked
intervals cannot be expressed" — was wrong. `CACHE` derives from
`champsim::operable`, whose `current_time` and `clock_period` are public, so the
thesis's interval `I` is used verbatim. The ChampSim port is in fact *more*
faithful than the replay, which had to bind a cycle column onto the record
stream.

**TailGuard needed a damage model, so one was built.** `stage3_hycsim/damage.py`
is the Python counterpart of the C++ model, definition for definition: lognormal
per-frame endurance bins (unit mean, 0.05 clamp), Arrhenius thermal
acceleration, Weibull hazard, and CVaR over the damage tail.

## The identity that protects every earlier result

With `endurance_cv=0` and `thermal=0`, `damage_per_write` returns **exactly**
1.0, so accumulated damage equals the write count bit for bit. The conformance
suite asserts equality, not approximation. Every `RLI_direct` number in this
project therefore stands unchanged, and the damage model is strictly an
addition.

## TailGuard in the replay: the result

400k records, 1 MB, 12 NVM + 4 SRAM. Peak damage and CVaR, against the
unmanaged baseline:

| endurance CV | baseline peak | TailGuard peak | baseline CVaR | TailGuard CVaR |
|---|---|---|---|---|
| 0.0 (uniform) | 24.0 | **18.0** (−25%) | 18.0 | **13.3** (−26%) |
| 0.3 | 37.3 | **22.8** (−39%) | 24.0 | **16.4** (−32%) |

Two things to take from this, and the second is the paper-worthy one.

1. **Process variation punishes the baseline badly.** Peak damage rises 24.0 →
   37.3, a 55% worsening, purely from endurance variation the policy cannot see.
   A write-count leveller is optimising a proxy that stops tracking the thing
   that fails.
2. **TailGuard's advantage grows with variation** — 25% → 39% on peak, 26% →
   32% on CVaR. That is the mechanism's whole thesis, and it is the regime no
   write-count policy in the comparison set can address. It is also why
   `endurance_cv` must be swept, not fixed: a single-CV result understates the
   mechanism at high variation and overstates it at zero.

TailGuard raises hit rate slightly here (0.2219 → 0.2251) because the recency
term keeps it from evicting live lines to protect healthy frames. With
`lambda_wa=0` it finds the degenerate optimum every wear-only objective has.

## A defect fixed, and the fourth of its kind

`wear-swap` in the replay called `swap_sets()` — which moves set **contents** —
without ever setting `does_remap` or overriding `remap_set`. Every moved block
still indexed to its original set, so the blocks became unreachable: the policy
paid the full migration tax and delivered no levelling at all.

This is the same defect already documented in `startgap.py`'s post-mortem of its
own v2, and it has now appeared four times in this codebase — Start-Gap, i2WAP's
Swap-Shift, ELBA's absorption buffer, and here. **Moving data is not remapping
addresses.** A mechanism that does the first without the second is pure cost.

Fixed with a real permutation table plus the data movement that keeps the array
consistent with it. Two invariants are now asserted in the conformance suite and
will catch a fifth instance: the mapping stays a bijection, and no live block is
left unreachable. Measured after the fix: 7 swaps, 223 migration writes, **zero
orphans**, hit rate unchanged.

Wear counters stay attached to **physical** sets and are not swapped with the
mapping — wear is a property of the cells, not of the data. The old code swapped
them, which was self-consistent only because it had no remap at all.

## Where the event models still differ, honestly

These are protocol-forced, not choices, and each is instrumented rather than
hidden.

| mechanism | difference | direction |
|---|---|---|
| Start-Gap, Sequoia G-OAS, WearSwap (ChampSim) | `CACHE::get_set_index()` is private and non-virtual, so no module can remap an address. Modelled as a permutation of the wear accounting — exact for the wear distribution, but **IPC is identical to LRU by construction**. A 0.00% IPC delta there is a model artefact, not a finding. | neutral on wear, uninformative on IPC |
| SWWR/DWWR (ChampSim) | source-line invalidation and destination-victim flush are not performed; `wwr_redirect_evictions` / `wwr_redirect_writebacks` size the omission | flatters SWWR/DWWR |
| EqualChance (ChampSim) | `handle_fill` takes invalid ways itself, so I-shifting is unreachable and EqualChance gets no credit for it | penalises EqualChance |
| Sequoia WAD (ChampSim) | victim flush unmodelled, omitting ~9.5× writeback traffic | flatters WAD |
| ELBA (ChampSim) | PR's capacity release and keep-serving cannot both be expressed; `PSC_ELBA_RELEASE=1` gives the replay's reading, `elba_early_evictions` measures the shortfall | measurable either way |
| TailGuard (replay) | decision point is allocation only, since the replay charges no write-hit writes | fewer decisions, same decision |
| DWWR | in the replay, starved write hits pin the counters at zero and DWWR collapses to a static restriction of window 0; under ChampSim `wwr_degenerate` is 0 | the clearest protocol contrast in the study |

## How to report this

Run both simulators and report both. Where a mechanism succeeds under one
protocol and fails under the other, that disagreement is a finding about
protocol sensitivity — and it is the cleanest explanation available for why PSC
and VRO behaved differently in the two tools. Name the protocol beside every
number.
