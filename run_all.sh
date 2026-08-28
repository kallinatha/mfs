#!/usr/bin/env bash
# One script. Everything, in the order the results depend on each other.
#
#   ./run_all.sh                 the full study
#   ./run_all.sh --smoke         ~15 minutes, proves the pipeline before you
#                                commit a day to it. Do this first.
#   ./run_all.sh --from hycsim   resume at a stage
#   ./run_all.sh --only destiny  one stage
#   ./run_all.sh --skip destiny,nvmexplorer   run everything EXCEPT these
#                                (comma-separated stage names); use when you
#                                copied their output in from a prior verified
#                                run and don't want it silently recomputed -
#                                destiny/nvmexplorer have no output-exists
#                                check of their own, unlike dse's per-point
#                                cache, so without --skip they always rerun.
#   ./run_all.sh --no-champsim   skip stage 5
#   ./run_all.sh --config X.json use a different config
#
# Every stage is resumable and skips completed work, so re-running after a
# failure costs only the failed stage.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

CONFIG="$ROOT/config/suscom_ten_mixes.json"
SMOKE=0; FROM=""; ONLY=""; SKIP=""; DO_CHAMPSIM=1
while [ $# -gt 0 ]; do
  case "$1" in
    --smoke) SMOKE=1; CONFIG="$ROOT/config/smoke.json" ;;
    --config) CONFIG="$2"; shift ;;
    --from) FROM="$2"; shift ;;
    --only) ONLY="$2"; shift ;;
    --skip) SKIP="$2"; shift ;;
    --no-champsim) DO_CHAMPSIM=0 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

export DESTINY_DIR="${DESTINY_DIR:-$ROOT/tools/destiny_v2}"
export NVMEXPLORER_DIR="${NVMEXPLORER_DIR:-$ROOT/tools/NVMExplorer}"
export HYCSIM_DIR="${HYCSIM_DIR:-$ROOT/tools/hycsim_reference}"
export CHAMPSIM_DIR="${CHAMPSIM_DIR:-$ROOT/tools/ChampSim}"

LOGDIR="$ROOT/logs"; mkdir -p "$LOGDIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
MAIN="$LOGDIR/run-$STAMP.log"

say()  { printf '\n\033[1m===== %s\033[0m\n' "$*" | tee -a "$MAIN"; }
info() { printf '      %s\n' "$*" | tee -a "$MAIN"; }
ok()   { printf '      \033[32m✔\033[0m %s\n' "$*" | tee -a "$MAIN"; }
bad()  { printf '      \033[31m✘\033[0m %s\n' "$*" | tee -a "$MAIN"; }
skip() { printf '      \033[33m–\033[0m %s\n' "$*" | tee -a "$MAIN"; }

STAGES=(check cells dse destiny prepare nvmexplorer nvmdnn profile hycsim integrate statistics validate champsim provenance report)
want() {                       # honour --from / --only / --skip
  local s="$1"
  case ",$SKIP," in *",$s,"*) return 1 ;; esac
  [ -n "$ONLY" ] && { [ "$ONLY" = "$s" ] && return 0 || return 1; }
  [ -z "$FROM" ] && return 0
  local seen=0
  for x in "${STAGES[@]}"; do
    [ "$x" = "$FROM" ] && seen=1
    [ "$x" = "$s" ] && { [ "$seen" = 1 ] && return 0 || return 1; }
  done
  return 1
}

run() {                        # run() <stage> <human name> <command...>
  local stage="$1" name="$2"; shift 2
  want "$stage" || { skip "$name (skipped by --from/--only)"; return 0; }
  local log="$LOGDIR/$stage-$STAMP.log"
  info "$name  →  logs/$stage-$STAMP.log"
  local t0=$SECONDS
  if "$@" >"$log" 2>&1; then
    ok "$name  ($((SECONDS - t0))s)"
    return 0
  fi
  bad "$name failed after $((SECONDS - t0))s"
  tail -n 25 "$log" | sed 's/^/        /' | tee -a "$MAIN"
  return 1
}

RESULTS="$(python3 -c "
import json,sys
c=json.load(open('$CONFIG'))
print(c['paths']['results_dir'])" 2>/dev/null || echo results)"

cat <<EOF | tee -a "$MAIN"
MFS: cross-stack evaluation of endurance-limited NVM last-level caches
config   $CONFIG
results  $RESULTS
logs     $LOGDIR
started  $(date)
EOF

# ---------------------------------------------------------------- 0. check
say "0/13  Tools and policy conformance"
if [ ! -x "$DESTINY_DIR/destiny" ]; then
  bad "DESTINY not built. Run ./setup.sh first."
  exit 1
fi
run check "doctor (conformance suite gates the run)" \
    python3 mfs.py doctor --config "$CONFIG" || {
  info "doctor reported missing tools. Stages needing them will be skipped;"
  info "the policy conformance result above is the one that must be clean."
}

# ---------------------------------------------------------------- 1. cells
say "1/13  Cell files from published parameter sets"
run cells "emit .cell + NVMExplorer cells with per-line citations" \
    python3 stage1_destiny/cells/from_paper.py --emit-all --complete \
      --complete-from sot_hanjiang_aipadv2024_16nm --auto-read --format both \
      -o stage1_destiny/cells/generated

# ------------------------------------------------------------------- 2. dse
say "2/13  Array design-space exploration (the cache-level contribution)"
# Every cell-level comparison in the reference set fixes one array organisation
# and varies the device. This stage does the opposite, and it is where a
# cache-level claim can actually come from.
if want dse; then
  DSE_CELLS="$(python3 -c "
import json;print(json.load(open('$CONFIG'))['experiment'].get('dse_cells',
  'sram,sttram_54_singh2020,sot_57_singh2020'))")"
  DSE_CAPS="$(python3 -c "
import json;print(json.load(open('$CONFIG'))['experiment'].get('dse_capacities','16'))")"
  DSE_W="$(python3 -c "
import json;print(json.load(open('$CONFIG'))['experiment'].get('dse_workers',4))")"
  DSE_MODE="$(python3 -c "
import json;print(json.load(open('$CONFIG'))['experiment'].get('dse_modes','screen,grid'))")"
  DSE_KNOBS="$(python3 -c "
import json;print(json.load(open('$CONFIG'))['experiment'].get('dse_knobs',''))")"
  for m in ${DSE_MODE//,/ }; do
    case "$m" in
      screen)   run dse "DSE screen (one factor at a time)" \
                    python3 stage1_destiny/dse.py --screen --cells "$DSE_CELLS" \
                      --capacity "$DSE_CAPS" --workers "$DSE_W" \
                      ${DSE_KNOBS:+--knobs "$DSE_KNOBS"} \
                      --outdir "$RESULTS/dse" ;;
      grid)     run dse "DSE factorial + Pareto front" \
                    python3 stage1_destiny/dse.py --grid --cells "$DSE_CELLS" \
                      --capacity "$DSE_CAPS" --workers "$DSE_W" \
                      --outdir "$RESULTS/dse" ;;
      geometry) run dse "geometry sweep (Zhang 2021 Tables 3-4, SOT added)" \
                    python3 stage1_destiny/dse.py --geometry --cells "$DSE_CELLS" \
                      --capacity "$DSE_CAPS" --workers "$DSE_W" \
                      --outdir "$RESULTS/dse" ;;
      scale)    run dse "scaling roadmap (Zhang 2021 Fig. 6, SOT added)" \
                    python3 stage1_destiny/dse.py --scale --cells "$DSE_CELLS" \
                      --capacity "$DSE_CAPS" --workers "$DSE_W" \
                      --outdir "$RESULTS/dse" ;;
    esac
  done
  for f in screen grid geometry scaling; do
    [ -f "$RESULTS/dse/$f.csv" ] && info "  $f.csv  $(($(wc -l < "$RESULTS/dse/$f.csv") - 1)) rows"
  done
else
  skip "design-space exploration"
fi

# --------------------------------------------------------------- 3. destiny
say "3/13  DESTINY: array models, all technologies"
run destiny "DESTINY sweep" python3 mfs.py destiny --config "$CONFIG"

# --------------------------------------------------------------- 4. prepare
say "4/13  Prepare traces"
run prepare "trace conversion + time-base verification" \
    python3 mfs.py prepare --config "$CONFIG" || exit 1

# The time base is the highest-leverage number in the whole run: the first trace
# column is a gem5 tick (1 ps), not a clock cycle, and reading it wrong scales
# every energy rate and every years-to-failure figure by 500x.
python3 - "$CONFIG" <<'PY' 2>&1 | tee -a "$MAIN"
import glob, json, os, sys
cfg = json.load(open(sys.argv[1]))
bad = 0
for p in sorted(glob.glob(os.path.join(cfg['traces']['prepared_dir'], '*', 'profile.json'))):
    d = json.load(open(p))
    ev = d.get('tick_rate_evidence', {})
    dec = ev.get('decision', 'NO EVIDENCE RECORDED')
    tag = 'ok  ' if 'tick' in dec else 'CHECK'
    print('      %s %-22s %s' % (tag, os.path.basename(os.path.dirname(p)), dec[:96]))
    bad += 'tick' not in dec
if bad:
    print('      ^ time base not resolved to ticks on %d trace(s). Stop and check.' % bad)
PY

# ----------------------------------------------------------- 5. nvmexplorer
say "5/13  NVMExplorer: independent array models"
# MUST run after `prepare`: evaluate_trace_profiles reads the profile.json that
# preparation writes. It used to run before it and failed every time with
# "no profile.json files under ...", which reads like a missing tool rather
# than a stage-ordering mistake.
if [ -x "$NVMEXPLORER_DIR/nvmexplorer_src/nvsim_src/nvsim" ]; then
  run nvmexplorer "NVMExplorer" python3 mfs.py nvmexplorer --config "$CONFIG"
  # AI/DNN workload characterization on the same cited cells: NVMExplorer's
  # bundled ResNet/ALBERT inference and graph-analytics access profiles,
  # previously an unused capability of the tool. Answers the "does this
  # transfer to AI workloads" reviewer question with measurements.
  run nvmdnn "NVMExplorer DNN/graph AI workloads" python3 mfs.py nvmexplorer-dnn --config "$CONFIG"
else
  skip "NVMExplorer not built — the cross-tool check will be unavailable"
fi

# --------------------------------------------------------------- 5. profile
say "6/13  Write-recurrence profile (decides whether the mechanisms can work)"
if want profile; then
  PREP="$(python3 -c "import json;print(json.load(open('$CONFIG'))['traces']['prepared_dir'])")"
  FIRST="$(ls -d "$PREP"/*/ 2>/dev/null | head -1)"
  if [ -n "$FIRST" ]; then
    SZ="$(python3 -c "import json;print(json.load(open('$CONFIG'))['experiment']['size_mb'])")"
    python3 stage3_hycsim/profile_writes.py "$FIRST" --size-mb "$SZ" \
      --out "$RESULTS/profile_writes.json" 2>&1 | tee "$LOGDIR/profile-$STAMP.log" \
      | grep -E 'PROTOCOL|KILL|WEAK|SIGNAL|Burst|charged|hottest' | sed 's/^/      /' | tee -a "$MAIN"
  else
    skip "no prepared trace found"
  fi
else
  skip "profile"
fi

# ---------------------------------------------------------------- 6. hycsim
say "7/13  Lifetime and write variation (the long one)"
run hycsim "policy replay, all mixes, 3 selection modes" \
    python3 mfs.py hycsim --config "$CONFIG" || exit 1

# ------------------------------------------------------------- 7. integrate
say "8/13  Cross-stack join"
# The join needs array models from BOTH tools. Without NVMExplorer there is no
# second opinion to join against, and a traceback here would look like a defect
# rather than a missing optional tool.
if [ -f "$RESULTS/nvmexplorer/trace_profiles.csv" ]; then
  run integrate "join wear x array models, cited endurance sweep" \
      python3 mfs.py integrate --config "$CONFIG"
else
  skip "no NVMExplorer models — cross-stack energy, area and years-to-failure"
  info "  unavailable. Lifetime and statistics are unaffected. Run ./setup.sh"
  info "  with network access to enable this stage."
fi

# ------------------------------------------------------------ 8. statistics
say "9/13  Statistics"
run statistics "bootstrap CIs, proxy validation, interaction" \
    python3 mfs.py statistics --config "$CONFIG"

# -------------------------------------------------------------- 9. validate
say "10/13  Acceptance gate"
if want validate; then
  if python3 mfs.py validate --config "$CONFIG" >"$LOGDIR/validate-$STAMP.log" 2>&1; then
    ok "every check passed"
  else
    bad "acceptance gate FAILED — read this before using any number"
    grep -E '^-|FAIL' "$LOGDIR/validate-$STAMP.log" | head -20 | sed 's/^/        /' | tee -a "$MAIN"
  fi
else
  skip "validate"
fi

# ------------------------------------------------------------- 10. champsim
say "11/13  ChampSim: performance of the mechanisms"
if [ "$DO_CHAMPSIM" = 1 ] && [ -x "$CHAMPSIM_DIR/bin/champsim" ]; then
  run champsim "ChampSim batch" python3 mfs.py champsim --config "$CONFIG"
else
  skip "ChampSim not built or disabled (./setup.sh --with-champsim)"
fi

# ----------------------------------------------------------- 11. provenance
say "12/13  Provenance"
run provenance "config hashes, tool versions, trace digests" \
    python3 mfs.py provenance --config "$CONFIG"

# --------------------------------------------------------------- 12. report
say "13/13  Summary"
python3 - "$CONFIG" "$RESULTS" <<'PY' 2>&1 | tee -a "$MAIN"
import csv, json, os, sys
cfg, res = sys.argv[1], sys.argv[2]

def show(path, title, key, cols, n=14):
    p = os.path.join(res, path)
    if not os.path.exists(p):
        print('      (%s not produced)' % path); return
    rows = list(csv.DictReader(open(p)))
    if not rows: return
    print('\n      %s' % title)
    def num(r):
        try: return -float(r.get(key) or 0)
        except Exception: return 0
    hdr = '      %-15s %-8s' % ('scheme', 'arena') + ''.join(' %11s' % c[:11] for c in cols)
    print(hdr); print('      ' + '-' * (len(hdr) - 6))
    for r in sorted(rows, key=num)[:n]:
        vals = ''
        for c in cols:
            v = r.get(c, '')
            try: vals += ' %11.4g' % float(v)
            except Exception: vals += ' %11s' % str(v)[:11]
        print('      %-15s %-8s%s' % (r.get('scheme', '')[:15], r.get('arena', '')[:8], vals))

show('hycsim/aggregate_loo.csv',
     'Lifetime, leave-one-mix-out selection (report THIS, not per-mix)',
     'rli_direct_gm', ['rli_direct_gm', 'rli_proxy_gm', 'write_amp_gm', 'hit_rate_delta_mean'])
show('hycsim/aggregate.csv',
     'Lifetime, per-mix tuning (an ORACLE CEILING, not a claim)',
     'rli_direct_gm', ['rli_direct_gm', 'write_amp_gm', 'hit_rate_delta_mean'])

st = os.path.join(res, 'statistics.json')
if os.path.exists(st):
    d = json.load(open(st))
    pv = d.get('proxy_validation') or {}
    if pv:
        print('\n      RLI proxy vs directly measured lifetime')
        for k in ('spearman', 'kendall', 'median_abs_error', 'reversal_fraction'):
            if k in pv: print('      %-22s %s' % (k, pv[k]))
        if pv.get('verdict'): print('      verdict: %s' % pv['verdict'])

cs = os.path.join(res, 'cross_stack_results.csv')
if os.path.exists(cs):
    rows = list(csv.DictReader(open(cs)))
    cols = [c for c in rows[0] if c.startswith('first_failure_years_')]
    if cols:
        print('\n      Years to first failure, endurance sweep (config/endurance.json)')
        print('      %-15s %-8s' % ('scheme', 'arena') + ''.join('%12s' % c.replace('first_failure_years_', '') for c in cols))
        seen = set()
        for r in sorted(rows, key=lambda r: -float(r.get(cols[-1]) or 0)):
            k = (r['scheme'], r['arena'])
            if k in seen: continue
            seen.add(k)
            if len(seen) > 12: break
            print('      %-15s %-8s' % (k[0][:15], k[1][:8]) + ''.join('%12.4g' % float(r[c] or 0) for c in cols))
PY

cat <<EOF | tee -a "$MAIN"

$(printf '=%.0s' {1..70})
Finished $(date).   Full log: $MAIN

Before you use any of it:
  1. logs/validate-$STAMP.log must be clean. A failed acceptance gate means
     a row obtained its lifetime by collapsing NVM use or losing hit rate.
  2. Report the leave-one-mix-out numbers. Per-mix tuning is an oracle ceiling.
  3. SOT lifetime is a band, not a number — quote the endurance decade with it.
$(printf '=%.0s' {1..70})
EOF
