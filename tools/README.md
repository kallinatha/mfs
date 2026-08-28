# Bundled tools

Two tools ship inside this archive because they are small, essential, and this
package's results are not checkable without them. Two more are fetched and built
by `setup.sh`, because they are large git repositories with submodules and
vendoring them would be both enormous and stale within a month.

| tool | how it arrives | why |
|---|---|---|
| **DESTINY v2** | **bundled**, `tools/destiny_v2` | 964 kB of source, BSD-style licence (see its `LICENSE`, `LICENSE_NVSim`, `LICENSE_CACTI3DD`, `NOTICE` — all retained unmodified). Stage 1 cannot run without it, and its shipped cell files are part of the argument. |
| **HyCSim reference model** | **bundled**, `tools/hycsim_reference` | Seven small Python files from https://gitlab.com/uz-gaz/hycsim. Bundled *only* so `tests/test_hycsim_conformance.py` can prove this package's replay is bit-exact against the original. Not used to produce any result. |
| NVMExplorer | fetched by `setup.sh` | Git repo with submodules plus an NVSim build. |
| ChampSim | fetched by `setup.sh` | Git repo with submodules and a config step. |

## DESTINY

Cite: S. Mittal, R. Wang, J. Vetter, "DESTINY: A Comprehensive Tool with 3D and
Multi-Level Cell Memory Modeling Capability", *Journal of Low Power Electronics
and Applications*, 2017. Upstream:
https://bitbucket.org/sparsh_mittal/destiny_v2

**One patch is applied at build time**, in `setup.sh`, and it touches no model
code: DESTINY predates C++11 and fails on g++ 11+ with
`reference to 'data' is ambiguous`, because `typedef.h` declares an enum member
named `data` while the sources do `using namespace std;` and C++17 added
`std::data`. `setup.sh` forces `-std=gnu++03 -w`. That is the entire fix; the
source in `tools/destiny_v2` is unmodified upstream.

## HyCSim

Cite: C. Escuin, A. A. Khan, P. Ibañez, T. Monreal, V. Viñals and J. Castrillon,
"HyCSim: A rapid design space exploration tool for emerging hybrid last-level
caches", DroneSE and RAPIDO 2022. https://doi.org/10.1145/3522784.3522801

The bundled copy exists for one purpose: the conformance test replays the same
records through the original `BaseHybrid` model and through this package's
`stage3_hycsim/llc.py`, and asserts the per-frame write vectors are identical.
They are — 16,384 of 16,384 frames, on 1,000,000 real trace records. Without the
bundled reference that claim would be unverifiable on your machine.

Its own `traces_dir/example_trace.txt` is **not** bundled (it is over a
gigabyte). `setup.sh --with-example-trace` downloads a bounded prefix if you
want to run the conformance test.
