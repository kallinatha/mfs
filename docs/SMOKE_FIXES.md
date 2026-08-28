# Smoke run: what broke, and what changed

Four defects, all now fixed. Three were mine; one is your machine.

## 1. Relative paths resolved against `config/`, not the project root

**The root cause of three of the four symptoms.** `load_config` resolved a
relative path against the config file's own directory, and every config lives in
`config/` — so `"results_dir": "results/smoke"` silently became
`<root>/config/results/smoke`.

Every stage then wrote *and read* consistently, so nothing failed loudly. But
`run_all.sh` computes the same string relative to the project root, so the
runner looked for outputs in a directory nothing had ever written to. That
produced all three of:

- "no NVMExplorer models" at stage 8
- the acceptance gate failing on a missing file
- "(hycsim/aggregate_loo.csv not produced)" in the summary

**Fixed:** relative paths now resolve against the project root. `sample.json`'s
`../`-prefixed paths corrected to match.

My dry runs used absolute paths in the config throughout, which is exactly why
this never surfaced here.

## 2. NVMExplorer ran before `prepare`

`evaluate_trace_profiles.py` reads the `profile.json` that preparation writes,
so it failed every time with `no profile.json files under /tmp/mfs_smoke_prepared`
— which reads like a missing tool rather than a stage-ordering mistake.

**Fixed:** NVMExplorer is now stage 5, after `prepare` at stage 4. The
`--from`/`--only` stage list was reordered to match.

## 3. The smoke DSE took 58 minutes

It screened every knob on the production cell list. A smoke run exists to prove
the path, not to produce a result.

**Fixed:** smoke now screens three knobs on one cell at 1 MB. New
`experiment.dse_knobs` key so the screen can be narrowed from config.

## 4. ChampSim build failed — my code, not your machine

I said this was "almost certainly vcpkg being blocked" and that the apt packages
would fix it. **That was wrong, and your second log proves it.** The packages
installed cleanly and the build failed again in exactly the same way. See
`docs/CHAMPSIM_BUILD.md` for the full diagnosis; the short version is a dangling
`-isystem` in a generated options file, and it is fixed in `setup.sh`.

---

# What the run already produced, and it is good

## The trace time base is confirmed on all ten mixes

Every one reads as gem5 ticks at 1 ps. Had this read as cycles, every energy
rate and every years-to-failure number would have been wrong by 500×. That check
has now paid for itself.

## The write-recurrence profile

41.9% of write events are repeats, but **zero of them charge array wear** under
HyCSim's protocol. All 107,621 charged writes are fills. Burst working set p95 is
9,767 blocks against 4,096 SRAM frames — 2.38× overflow, so a fixed partition
cannot hold it. That is the measured justification for a pooled or adaptive
partition, on your real traces rather than on my slice.

## The DSE screen is a publishable result on its own

195 design points. Worst-case ratio against the baseline design:

| knob | wr latency | wr energy | leakage | area | verdict |
|---|---|---|---|---|---|
| **OptimizationTarget** | **156.6×** | 2.93× | 3.27× | 2.11× | DOMINANT |
| DeviceRoadmap | 1.87× | 2.60× | **0.025×** | 2.87× | DOMINANT |
| GlobalWireType | 7.69× | 1.13× | 1.04× | 1.16× | DOMINANT |
| GlobalWireUseLowSwing | 6.66× | 0.81× | 1.60× | 1.49× | DOMINANT |
| Routing | 0.97× | 1.29× | 2.36× | 2.42× | DOMINANT |
| GlobalWireRepeaterType | 1.40× | 3.09× | 2.13× | 2.28× | DOMINANT |
| StackedDieCount | 1.01× | 1.75× | 2.91× | 1.84× | DOMINANT |
| CacheAccessMode | 1.99× | 1.41× | 1.81× | 1.34× | DOMINANT |
| **WriteScheme** | 1.000× | 1.000× | 1.000× | 1.000× | **inert** |

**A 156× spread in write latency from one cell, purely by changing what DESTINY
is asked to minimise.** Any paper reporting a single array number without
stating its optimisation target has not reported a number. That result alone
justifies the stage, and it is the strongest form of your cache-level argument.

`DeviceRoadmap` cuts leakage **40×** for 1.9× write latency and 2.9× area — the
cleanest trade in the space, and exactly the lever a large NVM LLC needs.

## A claim of mine your data falsified

I wrote that `WriteScheme: ReadAndCompare` was "the endurance lever". **Your
screen measured it at 1.000× on every objective** — DESTINY's MRAM path does not
model differential write at all. I have corrected the code comments and
`docs/DSE.md` to report it as a modelling limitation rather than a mechanism
result. Read-before-write is real in silicon; it is simply invisible to this
tool, and saying so is the honest position.
