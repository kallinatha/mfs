# Running it

Two commands. The first builds the tools, the second does the study.

```bash
git clone https://github.com/REPLACE_WITH_OWNER/mfs.git && cd mfs
./setup.sh                      # ~5 min: builds bundled DESTINY, fetches NVMExplorer
./run_all.sh --smoke            # ~15 min: proves the pipeline on a bounded slice
./run_all.sh                    # the real thing
```

Do the smoke run first. It exercises the identical code path on 400,000 records
per mix and catches a bad path or a missing tool in fifteen minutes rather than
after a day of replay.

For stage 5 you also need ChampSim:

```bash
./setup.sh --with-champsim      # adds ~10 min and a git clone
```

---

## What ships in the archive, and what does not

| | how |
|---|---|
| **DESTINY v2** | **bundled** — 964 kB of BSD-licensed source in `tools/destiny_v2`, licences intact. `setup.sh` builds it. |
| **HyCSim reference model** | **bundled** — seven Python files, used *only* by the bit-exact conformance test, never to produce a result. |
| NVMExplorer | fetched by `setup.sh` (git repo with submodules) |
| ChampSim | fetched by `setup.sh --with-champsim` |
| traces | yours, on the external drive — nothing is copied |

`./setup.sh --offline` builds only what is bundled. Stage 1 (DESTINY), stage 3
(lifetime), stage 4 (statistics) and the conformance suite all work offline;
stages 2 and 5 are skipped and `run_all.sh` says so rather than failing.

---

## Paths, already set

```
traces.raw_dir            /media/kallinath/UBUNTU-ONLY/HyCSim-SPEC-2006-gem5-traces/exported_traces
traces.prepared_dir       /media/kallinath/UBUNTU-ONLY/suscom_prepared
champsim_traces_root      /media/kallinath/UBUNTU-ONLY/traces
```

Prepared traces land on the same volume as the raw ones, so nothing crosses
devices mid-run. **Confirm 20–30 GB free on `UBUNTU-ONLY`** before the full run.

---

## Which ChampSim traces, and why those

You have roughly 800 trace files. Sixteen are configured, chosen for write
intensity and suite coverage rather than convenience:

| set | traces | reason |
|---|---|---|
| SPEC CPU2017 (`BARD-HPCA-2026-TRACES/ctf/spec2017`) | mcf, lbm, cactuBSSN, bwaves, omnetpp, xalancbmk, roms, fotonik3d | The suite the manuscript already uses. These eight are the memory- and write-intensive points; the compute-bound rest cost hours and produce no wear signal. |
| PARSEC 2.1 | canneal, streamcluster, facesim | Multithreaded, and named by the audit as missing. canneal and streamcluster are the write-heavy pair. |
| GAP + XSBench | bc.twitter, bc.web, xs.XL64unionized | Irregular graph and HPC access patterns. Also named by the audit. |
| STREAM | copy, triad | Pure bandwidth — the write-saturated worst case, and the cleanest place to see a wear mechanism succeed or fail. |

**Deliberately omitted.** `CRC2_trace` is SPEC CPU2006 and duplicates the
benchmarks already inside the ten HyCSim mixes — running it would double-count,
not broaden. `Pythia` is a simulator checkout with its own traces directory, not
a trace set. `ligra` and `google` overlap GAP on access pattern; add them with
`experiment.champsim_traces` if a reviewer asks for graph breadth.

Missing files are reported and skipped, not fatal.

---

## The twelve stages

| # | stage | what it decides |
|---|---|---|
| 0 | check | Tools present; **policy conformance suite** must pass. Runs even when tools are missing. |
| 1 | cells | Emits `.cell` and NVMExplorer cells from `sources.json`, every line citing its paper. |
| 2 | destiny | Array models, all technologies, 1–32 MB. The AE's gate. |
| 3 | nvmexplorer | Independent second opinion on the same cells. |
| 4 | prepare | Trace conversion **and the time-base check**. |
| 5 | profile | Write recurrence. Decides whether the mechanisms have anything to fire on. |
| 6 | hycsim | The long one. Policies × arenas × mixes, three selection modes. |
| 7 | integrate | Cross-stack join with the cited endurance sweep. |
| 8 | statistics | Bootstrap CIs, proxy validation, log-scale interaction. |
| 9 | validate | The acceptance gate. |
| 10 | champsim | IPC and wear from the same run. |
| 11 | provenance | Config hashes, tool versions, trace digests. |
| 12 | report | Prints the tables you actually need. |

Resume with `--from hycsim`; run one with `--only destiny`. Every stage caches,
so a re-run after a failure costs only the failed stage.

---

## Three things to check when it finishes

**1. The time base.** Stage 4 prints one line per trace. Every one must say
*ticks*. The first trace column is a gem5 tick at 1 ps, not a clock cycle, and
reading it as cycles inflates duration — and therefore every energy rate and
every years-to-failure figure — by 500×. If a line says CHECK, stop.

**2. The acceptance gate.** `logs/validate-*.log` must be clean. A failure means
some row obtained its lifetime improvement by collapsing NVM use or losing hit
rate, and that row is disqualified. The gate exists because RLI has a degenerate
optimum: a policy that stops using the NVM array scores an unbounded lifetime
gain while turning a 16-way cache into a 4-way one.

**3. Which column you quote.** The report prints leave-one-mix-out **and**
per-mix. Report LOO. Per-mix tuning saw the mix it is scored on and is an oracle
ceiling, not a claim anyone could ship. The gap between them is printed so you
can state it.

And when quoting SOT lifetime, quote the endurance decade with it. It is a band
(10¹²–10¹⁵), not a number — see `docs/ENDURANCE.md`.

---

## If something breaks

| symptom | cause |
|---|---|
| `DESTINY not built` | run `./setup.sh` |
| `doctor found missing prerequisites` | expected without NVMExplorer/ChampSim; the conformance result above it is what matters |
| `no space left` | prepared traces need 20–30 GB on `UBUNTU-ONLY` |
| DESTINY `No valid solutions` | that cell's geometry has no valid array at that capacity — a result, recorded in `results.csv` as `fail:`, not a crash |
| ChampSim build fails on vcpkg 403 | `sudo apt install libcli11-dev nlohmann-json3-dev libfmt-dev libbz2-dev liblzma-dev zlib1g-dev` |
| stage 6 out of memory | lower `experiment.hycsim_workers` |

Everything is in `logs/`, one file per stage per run.
