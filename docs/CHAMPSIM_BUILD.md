# Why ChampSim would not build, and the four defects behind it

Two build logs from the same machine, the second taken after installing every
dependency I asked for. The packages were not the problem. Four separate
defects were, and each one was hidden by the one before it.

---

## 1. A dangling `-isystem` (the actual root cause)

ChampSim generates its include flags in the Makefile:

```make
absolute.options:
	@echo "-I$(realpath inc) -isystem $(realpath $(TRIPLET_DIR)/include)" > $@
```

`TRIPLET_DIR` is found by globbing `vcpkg_installed/*/`. With no vcpkg install
that glob is empty, `$(realpath )` expands to nothing, and the file ends up
containing a bare `-isystem` with no argument after it.

A bare `-isystem` **consumes the next token on the command line.** The Makefile
places the response file first, so:

| recipe | command | token eaten | symptom |
|---|---|---|---|
| dependency generation | `@absolute.options -MM …` | `-MM` | `cc1plus: error: to generate dependencies you must specify either '-M' or '-MM'` — **238 times** |
| compilation | `@absolute.options -I.csconfig …` | `-I.csconfig` | `fatal error: module_decl.inc: No such file or directory`, `core_inst.inc: No such file …` |

Both symptom families come from that one missing argument.

This is also why my apt advice failed. `libfmt-dev`, `libcli11-dev` and
`nlohmann-json3-dev` removed the *header* errors — the first log has 15
`fmt/core.h` failures and the second has none — and left the real fault
untouched. I diagnosed from the symptom instead of reading the generated file.

**Fix.** `setup.sh` writes `absolute.options` itself, and patches the Makefile
rule to emit the `-isystem` only when the vcpkg directory actually exists.

---

## 2. `-isystem /usr/include` — my first fix, which was also wrong

The first version of the fix added the system include directory explicitly so a
distro-installed fmt would be found. The clean-room build rejected it:

```
/usr/include/c++/13/cstdlib:79:15: fatal error: stdlib.h: No such file or directory
   79 | #include_next <stdlib.h>
```

Naming `/usr/include` with `-isystem` reorders GCC's system include chain and
breaks libstdc++'s `#include_next`. Nothing needs to be added: `/usr/include` is
already on the default search path.

Worth stating plainly — I would have shipped this to you had I not rebuilt from
a clean tree before packaging.

**Fix.** Header-only dependencies are vendored into
`tools/ChampSim/vendor/include` instead, at pinned versions:

- `nlohmann/json.hpp` — single header
- `CLI/CLI.hpp` — single header
- `fmt` 10.2.1 — **not** 11.x, which deleted `fmt/core.h` that ChampSim
  includes directly. Built with `-DFMT_HEADER_ONLY`, so nothing links libfmt.

Vendoring also removes a version risk: Ubuntu 24.04 ships fmt 9.1.0 while
ChampSim's CI uses 10.x. If the box is offline, the system headers still work —
they are on the default path.

`-lCLI11` and `-lfmt` are stripped from `LDLIBS`. Ubuntu's `libcli11-dev` is
header-only and ships no `libCLI11` to link against, so that flag could never
have worked.

---

## 3. Re-running `setup.sh` corrupted the module directory

```sh
cp -r "$ROOT/stage5_champsim/module" "$TOOLS/ChampSim/replacement/psc_family"
```

`cp -r src dst` means two different things depending on whether `dst` exists. It
*creates* `dst` on the first run and nests `src` **inside** `dst` on every run
after that. The nested copy is still a `.cc` under the module root, so ChampSim
compiled `psc_family.cc` twice and the link died with pages of:

```
multiple definition of `psc_family::update_replacement_state(...)'
```

A first run worked; a second run failed. Since the earlier defects meant the
build never reached the link stage, this had never been visible.

**Fix.** Delete the destination and copy the file contents explicitly. Verified
by building twice from the same tree.

---

## 4. An unknown policy silently ran LRU

Found while driving the freshly built binary directly, not from any log.

`PSC_POLICY=psc-balance` and `PSC_POLICY=psc-vro` returned results **identical to
`lru`** — 122,170 NVM writes, interV 0.149828, to every digit. The parser matched
only the underscored spellings and its final `else` fell back to LRU without a
word:

```cpp
else { policy = policy_t::LRU; policy_name = "lru"; }
```

Everything else in the project — the replay, the docs, the CSV columns — uses
hyphens. The Python driver happens to pass underscores, so the harness was never
wrong. But this is the most dangerous failure mode in the whole study: a mistyped
or renamed policy runs plain LRU, produces entirely plausible numbers, and the
CSV labels them with the policy that never executed. A reviewer could not detect
it and neither could you.

**Fix.** Hyphens are normalised to underscores, and an unrecognised name now
aborts with a message rather than defaulting:

```
psc_family: unknown PSC_POLICY 'nonsense'. Refusing to run, because falling
back to LRU would produce results labelled with a policy that never executed.
```

Both spellings now agree exactly (`psc-balance` and `psc_balance`: 65,396 NVM
writes, interV 0.0485437) and both differ from LRU.

---

## 5. A hard-coded path from my machine

`stage5_champsim/run_champsim.py` defaulted `CHAMPSIM_DIR` to
`/home/claude/ChampSim` — a path that exists only in the container this was
written in. `run_all.sh` always exports the variable so the pipeline was fine,
but running the stage directly failed against a directory you had never heard
of. Now defaults to `<package>/tools/ChampSim`.

---

## What was verified, and how

Not "it compiles". A clean tree, twice:

1. Fresh clone, full `./setup.sh --with-champsim` → `bin/champsim` built,
   zero errors in the log.
2. Second run over the same tree → still builds (the `cp -r` idempotency test).
3. All 22 policies executed on a synthetic 200k-instruction trace (table below).
4. Unknown-policy abort confirmed.
5. `pytest tests -q` → 21 passed.

### The 22-policy execution check

| policy | NVM writes | interV | | policy | NVM writes | interV |
|---|---|---|---|---|---|---|
| lru | 221,919 | 0.1508 | | ayush | 268,945 | 0.1612 |
| psc | 268,945 | 0.1612 | | dwawr | 222,147 | 0.1516 |
| vro | 268,399 | 0.1632 | | i2wap | 218,204 | 0.1523 |
| vro_rank | 224,706 | 0.1529 | | elba | 221,919 | 0.1508 |
| psc_pred | 170,670 | 0.1663 | | sequoia | 227,758 | 0.1528 |
| psc_vro | 268,168 | 0.1615 | | startgap | 223,461 | 0.1557 |
| psc_vro_rank | 170,560 | 0.1660 | | equalchance | 224,465 | 0.1531 |
| psc_soft | 169,019 | 0.1632 | | swwr | 216,345 | 0.1479 |
| psc_soft_rank | 168,778 | 0.1645 | | dwwr | 216,345 | 0.1479 |
| **psc_balance** | 196,096 | **0.0263** | | wear_swap | 222,173 | 0.1499 |
| tailguard | 170,560 | 0.1674 | | full | 170,626 | 0.1669 |

**This is a build check, not a result.** Do not quote these numbers. The trace is
a synthetic strided stream and it produces **no LLC write misses at all**
(`LLC WRITE ACCESS 66637 HIT 66637 MISS 0`), so every mechanism that keys on a
dirty fill has nothing to act on. That is why `ayush` ties `psc` to seven digits
and `elba` ties `lru` to seven digits — both degenerate to the same
allocate-in-NVM path, confirmed by `ayush_migrations 0`. It is a property of the
toy trace, not a defect; your SPEC traces have the writeback traffic these
policies exist for.

What the table does establish is the thing that was broken: 22 distinct policies
are compiled into the binary, are selectable, and are not silently running LRU.
`psc_balance` cutting interV to 0.0263 against everything else's ~0.15 shows the
mechanism engages even on a workload that starves the rest of the family.

If it still fails on your box, the log will now contain a real error rather than
238 copies of a symptom. Send it.
