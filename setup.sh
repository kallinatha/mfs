#!/usr/bin/env bash
# Build the bundled tools and fetch the two that are too large to bundle.
# Idempotent: re-running skips anything already built.
#
#   ./setup.sh                     DESTINY + NVMExplorer (enough for stages 1-4)
#   ./setup.sh --with-champsim     also ChampSim, for stage 5
#   ./setup.sh --with-example-trace  also a bounded HyCSim trace for the
#                                    conformance test
#   ./setup.sh --offline           build only what is bundled; skip all fetching
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS="$ROOT/tools"
WITH_CHAMPSIM=0; WITH_TRACE=0; OFFLINE=0
for a in "$@"; do
  case "$a" in
    --with-champsim) WITH_CHAMPSIM=1 ;;
    --with-example-trace) WITH_TRACE=1 ;;
    --offline) OFFLINE=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '   \033[32mok\033[0m    %s\n' "$*"; }
warn() { printf '   \033[33mwarn\033[0m  %s\n' "$*"; }
die()  { printf '   \033[31mFAIL\033[0m  %s\n' "$*" >&2; exit 1; }

NPROC="$(nproc 2>/dev/null || echo 4)"

# ---------------------------------------------------------------- prereqs
say "Prerequisites"
for c in python3 g++ make; do
  command -v "$c" >/dev/null || die "$c not found. On Debian/Ubuntu: sudo apt install build-essential python3"
done
ok "python3 $(python3 -c 'import sys;print(".".join(map(str,sys.version_info[:3])))')"
ok "$(g++ --version | head -1)"
python3 -c 'import numpy' 2>/dev/null && ok "numpy $(python3 -c 'import numpy;print(numpy.__version__)')" || {
  warn "numpy missing; installing into the user site"
  python3 -m pip install --user numpy || die "could not install numpy. Try: sudo apt install python3-numpy"
}

# ---------------------------------------------------------------- DESTINY
say "DESTINY v2 (bundled)"
if [ -x "$TOOLS/destiny_v2/destiny" ]; then
  ok "already built"
else
  [ -f "$TOOLS/destiny_v2/Makefile" ] || die "tools/destiny_v2 missing from the archive"
  # DESTINY predates C++11 and breaks on g++ 11+: typedef.h declares an enum
  # member `data` while the sources do `using namespace std;`, and C++17 added
  # std::data. Forcing gnu++03 is the whole fix; no model code is touched.
  grep -q 'gnu++03' "$TOOLS/destiny_v2/Makefile" || \
    sed -i 's/^CXXFLAGS := -Wall /CXXFLAGS := -Wall -std=gnu++03 -w /' "$TOOLS/destiny_v2/Makefile"
  ( cd "$TOOLS/destiny_v2" && make -j"$NPROC" ) >"$TOOLS/destiny_build.log" 2>&1 \
    && ok "built -> tools/destiny_v2/destiny" \
    || die "build failed; see tools/destiny_build.log"
fi

# ------------------------------------------------------------ NVMExplorer
say "NVMExplorer"
if [ -x "$TOOLS/NVMExplorer/nvmexplorer_src/nvsim_src/nvsim" ]; then
  ok "already built"
elif [ "$OFFLINE" = 1 ]; then
  warn "offline: skipping. Stage 2 will be skipped by run_all.sh."
else
  command -v git >/dev/null || die "git not found"
  [ -d "$TOOLS/NVMExplorer" ] || \
    git clone --depth 1 --recurse-submodules https://github.com/lpentecost/NVMExplorer.git \
      "$TOOLS/NVMExplorer" >"$TOOLS/nvmexplorer_clone.log" 2>&1 \
      || warn "clone failed; see tools/nvmexplorer_clone.log"
  if [ -d "$TOOLS/NVMExplorer/nvmexplorer_src/nvsim_src" ]; then
    make -C "$TOOLS/NVMExplorer/nvmexplorer_src/nvsim_src" -j"$NPROC" \
      >"$TOOLS/nvsim_build.log" 2>&1 \
      && ok "built -> NVMExplorer/nvmexplorer_src/nvsim_src/nvsim" \
      || warn "NVSim build failed; see tools/nvsim_build.log. Stage 2 will be skipped."
  else
    warn "NVMExplorer not available. Stage 2 will be skipped."
  fi
fi

# --------------------------------------------------------------- ChampSim
if [ "$WITH_CHAMPSIM" = 1 ]; then
  say "ChampSim (stage 5)"
  if [ -x "$TOOLS/ChampSim/bin/champsim" ]; then
    ok "already built"
  elif [ "$OFFLINE" = 1 ]; then
    warn "offline: skipping"
  else
    [ -d "$TOOLS/ChampSim" ] || \
      git clone --depth 1 https://github.com/ChampSim/ChampSim.git "$TOOLS/ChampSim" \
        >"$TOOLS/champsim_clone.log" 2>&1 || warn "clone failed"
    if [ -d "$TOOLS/ChampSim" ]; then
      # Copy the module *contents*, never the directory. `cp -r src dst` means
      # two different things depending on whether dst exists: it creates dst on
      # the first run and nests src *inside* dst on every run after that. The
      # nested copy is still a .cc under the module root, so ChampSim compiles
      # psc_family.cc twice and the link dies with pages of
      # "multiple definition of psc_family::...". Re-running setup.sh broke the
      # build; a clean checkout worked. Delete and refill instead.
      rm -rf "$TOOLS/ChampSim/replacement/psc_family"
      mkdir -p "$TOOLS/ChampSim/replacement/psc_family"
      cp "$ROOT/stage5_champsim/module/"*.cc "$ROOT/stage5_champsim/module/"*.h \
         "$TOOLS/ChampSim/replacement/psc_family/" 2>/dev/null || true
      ( cd "$TOOLS/ChampSim"
        git submodule update --init >/dev/null 2>&1 || true

        # ------------------------------------------------------------------
        # The real reason a from-source ChampSim build fails without vcpkg.
        #
        # ChampSim's Makefile generates its include options like this:
        #
        #   absolute.options:
        #       @echo "-I$(realpath inc) -isystem $(realpath $(TRIPLET_DIR)/include)" > $@
        #
        # `TRIPLET_DIR` is discovered by globbing `vcpkg_installed/*/`. With no
        # vcpkg install that glob is empty, `$(realpath )` expands to nothing,
        # and the file ends up containing a **dangling `-isystem`**.
        #
        # A bare `-isystem` consumes the next token on the command line. The
        # Makefile puts the response file first, so:
        #
        #   dep-gen:  ... @absolute.options -MM ...   -> `-MM` is eaten
        #             => "cc1plus: error: to generate dependencies you must
        #                 specify either '-M' or '-MM'"   (238 times)
        #   compile:  ... @absolute.options -I.csconfig ...  -> `-I.csconfig` eaten
        #             => "fatal error: module_decl.inc: No such file or directory"
        #                "fatal error: core_inst.inc: No such file or directory"
        #
        # Both symptom families come from that one missing argument. Installing
        # libfmt-dev / libcli11-dev / nlohmann-json3-dev removes the *header*
        # errors and leaves this one untouched, which is exactly what happened.
        #
        # Fix: write `absolute.options` ourselves, and patch the rule so it
        # stays correct if make ever regenerates it.
        # ------------------------------------------------------------------

        # Vendor the three header-only dependencies. Deterministic, and it
        # removes the version risk of whatever the distro ships: Ubuntu 24.04
        # has fmt 9.1.0, the ChampSim CI uses 10.x. `-DFMT_HEADER_ONLY` means
        # nothing has to link against libfmt either.
        VEND="$PWD/vendor/include"
        if [ ! -f "$VEND/fmt/core.h" ] || [ ! -f "$VEND/CLI/CLI.hpp" ] \
           || [ ! -f "$VEND/nlohmann/json.hpp" ]; then
          mkdir -p "$VEND/CLI" "$VEND/nlohmann"
          python3 - "$VEND" <<'PY' || true
import os, sys, urllib.request
v = sys.argv[1]
for u, p in (
    ('https://github.com/nlohmann/json/releases/latest/download/json.hpp',
     'nlohmann/json.hpp'),
    ('https://github.com/CLIUtils/CLI11/releases/latest/download/CLI11.hpp',
     'CLI/CLI.hpp'),
):
    dst = os.path.join(v, p)
    if os.path.exists(dst):
        continue
    r = urllib.request.Request(u, headers={'User-Agent': 'mfs/1.0'})
    open(dst, 'wb').write(urllib.request.urlopen(r, timeout=180).read())
PY
          if [ ! -f "$VEND/fmt/core.h" ]; then
            # fmt 11 deleted fmt/core.h, which ChampSim includes directly.
            # 10.2.1 is the last release that has it.
            rm -rf .fmtsrc
            git clone --depth 1 --branch 10.2.1 \
              https://github.com/fmtlib/fmt.git .fmtsrc >/dev/null 2>&1 \
              && cp -r .fmtsrc/include/fmt "$VEND/fmt"
            rm -rf .fmtsrc
          fi
        fi

        # Nothing is added for the system headers on purpose. /usr/include is
        # already on the default search path, so a distro-installed
        # libfmt-dev / libcli11-dev / nlohmann-json3-dev is picked up with no
        # flag at all -- and naming it explicitly is actively harmful:
        # `-isystem /usr/include` reorders the system include chain and breaks
        # libstdc++'s `#include_next <stdlib.h>`. That was the first version of
        # this fix and the clean-room build caught it:
        #     /usr/include/c++/13/cstdlib:79: fatal error: stdlib.h: No such
        #     file or directory
        INCS="-I$(cd inc && pwd)"
        [ -d "$VEND" ] && INCS="$INCS -isystem $VEND"
        printf '%s -DFMT_HEADER_ONLY\n' "$INCS" > absolute.options

        # Keep the generated rule honest too: emit the -isystem only when the
        # vcpkg triplet directory actually exists.
        python3 - <<'PY' || true
import re
s = open('Makefile').read()
bad = '@echo "-I$(realpath inc) -isystem $(realpath $(TRIPLET_DIR)/include)" > $@'
good = ('@echo "-I$(realpath inc) '
        '$(if $(realpath $(TRIPLET_DIR)/include),'
        '-isystem $(realpath $(TRIPLET_DIR)/include),)" > $@')
if bad in s:
    s = s.replace(bad, good)
    open('Makefile', 'w').write(s)
PY

        # libcli11-dev is header-only (there is no libCLI11 to link), and fmt
        # is header-only here by the define above. Both -l flags are wrong.
        sed -i 's/-lCLI11 //; s/ -lfmt//' Makefile 2>/dev/null || true

        python3 - <<'PY' 2>/dev/null || true
import json, os
p = 'champsim_config.json'
if os.path.exists(p):
    c = json.load(open(p)); c.setdefault('LLC', {})['replacement'] = 'psc_family'
    json.dump(c, open(p, 'w'), indent=2)
PY
        ./config.sh champsim_config.json >/dev/null 2>&1 || true
        # config.sh can recreate absolute.options; write it again afterwards.
        printf '%s -DFMT_HEADER_ONLY\n' "$INCS" > absolute.options
        make -j"$NPROC"
        [ -x bin/champsim ] ) >"$TOOLS/champsim_build.log" 2>&1 \
        && ok "built -> tools/ChampSim/bin/champsim" \
        || warn "ChampSim build failed; see tools/champsim_build.log. Stage 5 will be skipped.
          If the box is offline, first: sudo apt install libcli11-dev nlohmann-json3-dev libfmt-dev libbz2-dev liblzma-dev zlib1g-dev"
    fi
  fi
fi

# ------------------------------------------------- HyCSim conformance trace
if [ "$WITH_TRACE" = 1 ] && [ "$OFFLINE" != 1 ]; then
  say "HyCSim example trace (conformance test only)"
  T="$TOOLS/hycsim_reference/traces_dir/example_trace.txt"
  if [ -s "$T" ]; then ok "already present"; else
    mkdir -p "$(dirname "$T")"
    python3 - "$T" <<'PY' && ok "downloaded a bounded prefix" || warn "download failed; the conformance test will skip"
import sys, urllib.request
u = ('https://gitlab.com/uz-gaz/hycsim/-/raw/main/traces_dir/example_trace.txt')
r = urllib.request.Request(u, headers={'User-Agent': 'mfs/1.0'})
f = urllib.request.urlopen(r, timeout=180)
tot = 0
with open(sys.argv[1], 'wb') as o:          # bounded: the full file is >1 GB
    while tot < 120 * 1024 * 1024:
        b = f.read(1 << 20)
        if not b:
            break
        o.write(b); tot += len(b)
print('   %d MB' % (tot >> 20))
PY
  fi
fi

say "Done"
cat <<EOF
   DESTINY      $( [ -x "$TOOLS/destiny_v2/destiny" ] && echo built || echo MISSING )
   NVMExplorer  $( [ -x "$TOOLS/NVMExplorer/nvmexplorer_src/nvsim_src/nvsim" ] && echo built || echo 'absent (stage 2 skipped)' )
   ChampSim     $( [ -x "$TOOLS/ChampSim/bin/champsim" ] && echo built || echo 'absent (stage 5 skipped)' )

   Next:  ./run_all.sh
EOF
