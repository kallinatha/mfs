#!/usr/bin/env bash
# One command, from nothing to every reviewer-facing number, with a gate after
# every stage that can silently produce wrong output.
#
#   ./run_clean_slate.sh                  full pipeline, ChampSim reused from cache
#   ./run_clean_slate.sh --with-champsim  also (re)run the ChampSim batch
#   ./run_clean_slate.sh --resume         skip stages whose outputs already exist
#   ./run_clean_slate.sh --analysis-only  re-derive from an existing results tree
#
# WHAT IT COVERS.  Every stage, not just the replay.  An earlier version started
# at `--from hycsim`, which silently omitted the array design-space exploration
# -- the source of the OptimizationTarget spread and the SRAM-to-SOT
# write-latency crossover, both headline material.  A stage that is never asked
# to run does not fail, so nothing flagged it.  `coverage_audit.py` now
# enumerates the CLAIMS and checks each one has an artefact behind it.
#
# WHY THIS EXISTS.  Over this revision, four separate runs produced no usable
# output and were only discovered afterwards: a `--only a,b,c` list that
# matched no stage and silently skipped everything; an NVMExplorer sweep that
# wrote one cell's arrays and died before the rest; a verifier pinned to a path
# that did not exist on this machine; and a results tree written by two
# concurrent replays.  Each cost hours.  Every one of them would have been
# caught by a five-second check immediately afterwards, so this script runs
# those checks itself and refuses to continue past a stage that did not do
# what it claimed.
#
# The rule throughout: a stage is not "done" because it exited 0.  It is done
# when its output exists, is non-empty, and contains what the next stage needs.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
ROOT="$(pwd)"
CONFIG="${CONFIG:-config/suscom_ten_mixes.json}"
RESULTS="${RESULTS:-results/suscom_ten_mixes}"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="logs/clean-slate-${STAMP}.log"
mkdir -p logs

MODE=full
CHAMPSIM=0
for arg in "$@"; do
  case "$arg" in
    --resume)        MODE=resume ;;
    --analysis-only) MODE=analysis ;;
    --with-champsim) CHAMPSIM=1 ;;
    *) echo "unknown option: $arg"; exit 2 ;;
  esac
done

say()  { printf '\n\033[1m== %s\033[0m\n' "$*" | tee -a "$LOG"; }
info() { printf '   %s\n' "$*" | tee -a "$LOG"; }
die()  { printf '\n\033[31mSTOP: %s\033[0m\n' "$*" | tee -a "$LOG"; exit 1; }

# ---------------------------------------------------------------- preflight
say "preflight"

# A second replay writing the same tree is the failure that is hardest to see
# afterwards, because both processes write complete, plausible CSVs and the
# last one wins.  Refuse to start rather than produce a tree nobody can attest.
if ! python3 scripts/check_no_replay_running.py; then
  die "a replay is already running (pids above).
     Two batches writing $RESULTS interleave their output and the result
     cannot be attested. Wait for it, or kill it, then re-run."
fi

# Three outcomes, not two.
#
#   pytest missing  -> a dev tool is absent. The pipeline itself only needs
#                      numpy/pandas/scipy, so refusing to analyse would block a
#                      valid run over tooling. Warn loudly and continue.
#   pytest fails    -> a real defect. Stop, and PRINT WHY: an earlier version
#                      captured stdout only, so a collection error (which goes
#                      to stderr) produced a bare "STOP" with no cause. A gate
#                      that hides why it fired is worse than no gate.
#   pytest passes   -> proceed.
if ! python3 -c "import pytest" 2>/dev/null; then
  info ""
  info "WARNING: pytest is not installed, so the conformance suite did NOT run."
  info "The pipeline does not need it; the verification layer does. Nothing"
  info "below has been checked against the policy determinism, write-accounting"
  info "or projection-exactness tests. Install it and re-run before quoting any"
  info "number in a paper:"
  info "    pip install -r requirements.txt"
  info ""
  SUITE=skipped
else
  python3 -m pytest tests/ -q 2>&1 | tee -a "$LOG" | tail -25
  if [ "${PIPESTATUS[0]}" -ne 0 ]; then
    die "the conformance suite failed. The failure is printed above.
     Reproduce it directly with:
       python3 -m pytest tests/ -q
     If it is an import error, a dependency is missing:
       pip install -r requirements.txt"
  fi
  SUITE=passed
fi
info "conformance suite: $SUITE"

command -v python3 >/dev/null || die "python3 not found"
[ -f "$CONFIG" ] || die "config not found: $CONFIG"
python3 -c "import json,sys; json.load(open('$CONFIG'))" || die "config is not valid JSON"

# The scheme list is the single place a policy can be silently excluded: a
# policy can be implemented, registered, tested, and still never run because
# nobody added it here.  That happened to six schemes in this study.
python3 - "$CONFIG" <<'PY' || die "scheme registry and config disagree"
import json, sys, os
sys.path.insert(0, os.path.join(os.getcwd(), 'stage3_hycsim'))
import policies
cfg = json.load(open(sys.argv[1]))
want = set(cfg['experiment']['schemes'])
have = set(policies.REGISTRY)
missing = sorted(want - have)
unused  = sorted(have - want)
if missing:
    print('config asks for schemes that are not registered: %s' % missing); sys.exit(1)
if unused:
    print('registered but NOT in the config, so they will not run: %s' % unused)
print('%d schemes will run' % len(want))
PY

# --------------------------------------------------------------- the stages
if [ "$MODE" != analysis ]; then

  # ChampSim is ~21 h from cold. run_champsim.py caches per (policy, trace) and
  # reuses any log containing 'end psc_family', so an existing batch is reused
  # in minutes. It is still excluded by default: a cache miss costs a day, and
  # that should be a deliberate choice rather than a surprise. The coverage
  # audit flags its absence either way, so skipping it cannot go unnoticed.
  SKIP_LIST="champsim"
  if [ "$CHAMPSIM" = 1 ]; then
    SKIP_LIST=""
    info "ChampSim included: cached (policy, trace) pairs are reused; a cold"
    info "batch is roughly 21 h for 12 policies x 48 traces."
  else
    info "ChampSim excluded (--with-champsim to include). Existing results in"
    info "$RESULTS/champsim are still used by every analysis below."
  fi

  say "stage A - full pipeline (replay is the long one: ~20 h, 23 schemes x 10 mixes)"
  if [ "$MODE" = resume ] && [ -s "$RESULTS/hycsim/lifetime_all_mixes.csv" ]; then
    info "skipping: $RESULTS/hycsim/lifetime_all_mixes.csv already exists"
  else
    # No --from: cells, dse, destiny, prepare, nvmexplorer and profile all run.
    # Starting at hycsim is what dropped the design-space results last time.
    # --skip takes the comma list; --only matches ONE stage exactly, and getting
    # that backwards silently runs nothing, which is how a 3-second "run" once
    # printed a full summary of stale numbers.
    if [ -n "$SKIP_LIST" ]; then
      ./run_all.sh --config "$CONFIG" --skip "$SKIP_LIST" 2>&1 | tee -a "$LOG"
    else
      ./run_all.sh --config "$CONFIG" 2>&1 | tee -a "$LOG"
    fi
  fi
  for f in hycsim/lifetime_all_mixes.csv hycsim/lifetime_all_mixes_loo.csv \
           hycsim/tuning_all_mixes.csv hycsim/aggregate_loo.csv; do
    [ -s "$RESULTS/$f" ] || die "stage A did not produce $RESULTS/$f.
     If the run finished in seconds, the stage was skipped, not executed."
  done
  info "replay outputs present"

  python3 scripts/assert_schemes_ran.py "$RESULTS" "$CONFIG" \
    || die "the replay did not run every configured scheme"

  say "stage B - NVMExplorer DNN/graph arrays"
  if [ "$MODE" = resume ] && [ "$(ls "$RESULTS"/nvmexplorer_dnn/results/*mfs_dnn_arrays.csv 2>/dev/null | wc -l)" -ge 6 ]; then
    info "skipping: array CSVs already present"
  else
    ./run_all.sh --config "$CONFIG" --only nvmdnn 2>&1 | tee -a "$LOG"
  fi
  # This stage used to exit 0 having produced only the first cell type, because
  # NVSim's combine_csv opens a relative path that resolves only from inside
  # the checkout. Counting the cell types is the check that catches it.
  python3 - "$RESULTS" <<'PY' || die "the DNN sweep did not cover every configured cell"
import glob, os, sys, json
d = sys.argv[1] + '/nvmexplorer_dnn/results'
got = {os.path.basename(p).split('_')[0] for p in glob.glob(d + '/*mfs_dnn_arrays.csv')}
cfgs = glob.glob(sys.argv[1] + '/nvmexplorer_dnn/*.json') + glob.glob('config/mfs_dnn*.json')
want = set()
for c in cfgs:
    try: want |= set(json.load(open(c))['experiment']['cell_type'])
    except Exception: pass
want = want or {'SRAM', 'STT', 'PCM'}
missing = sorted(want - got)
if missing:
    print('array CSVs missing for: %s (found %s)' % (missing, sorted(got))); sys.exit(1)
print('array CSVs present for %s' % sorted(got))
PY
fi

# ------------------------------------------------------------------ analysis
say "stage C - acceptance gate"
LATEST_VALIDATE="$(ls -t logs/validate-*.log 2>/dev/null | head -1)"
if [ -z "$LATEST_VALIDATE" ]; then
  # A missing log and a failed gate are different situations. In a full run the
  # replay should have produced one, so its absence means the stage did not
  # reach the gate. When re-deriving from a tree produced elsewhere there may
  # legitimately be no local log, and refusing to analyse would be unhelpful.
  if [ "$MODE" = analysis ]; then
    info "no local validate log; skipping the gate check (analysis-only mode)."
    info "The tree has NOT been gated here - check it where it was produced."
    LATEST_VALIDATE=""
  else
    die "no validate log found; stage A did not reach the gate"
  fi
fi
if [ -n "$LATEST_VALIDATE" ]; then
  grep -q "VALIDATION PASSED" "$LATEST_VALIDATE" \
    || die "acceptance gate failed. Read $LATEST_VALIDATE.
     A guard SPLIT is a result to report. A guard FAILURE with no guarded
     support is a claim with nothing behind it."
  info "$(grep -c 'holds the guard on' "$LATEST_VALIDATE") scheme(s) reported a guard split"
  grep 'VALIDATION PASSED' "$LATEST_VALIDATE" | tee -a "$LOG"
fi

say "stage D - cross-technology projection, proved exact"
if [ ! -s "$RESULTS/nvmexplorer/trace_profiles.csv" ] || [ ! -s "$RESULTS/cross_stack_results.csv" ]; then
  info "no array models or cross-stack join in $RESULTS; skipping D and E."
  info "Run the nvmexplorer and integrate stages to enable the projection."
else
# Every counterfactual technology number rests on this being bit-identical to
# the production join. Check it before using any of them.
python3 stage6_transfer/transfer.py --validate \
  --lifetime "$RESULTS/hycsim/lifetime_all_mixes.csv" \
  --models   "$RESULTS/nvmexplorer/trace_profiles.csv" \
  --destiny  "$RESULTS/destiny/results.csv" \
  --measured "$RESULTS/cross_stack_results.csv" 2>&1 | tee -a "$LOG"
grep -q "EXACT" "$LOG" || die "the projection does not reproduce the production join.
     Every projected-technology result is void until this is exact."

say "stage E - cell-parameter phase diagram (which scheme wins where)"
python3 stage6_transfer/transfer.py --sweep \
  --lifetime "$RESULTS/hycsim/lifetime_all_mixes.csv" \
  --models   "$RESULTS/nvmexplorer/trace_profiles.csv" \
  --destiny  "$RESULTS/destiny/results.csv" \
  --out "$RESULTS/cell_phase_diagram.csv" 2>&1 | tee -a "$LOG"

say "stage E2 - endurance floor per target service life"
# Inverts the usual question: not "how long does this cell last" but "what
# endurance does a 3/5/10-year product demand, and how much does each scheme
# relax it". Uses replay quantities only, so it needs no cell model, and the
# ratio to baseline must equal (1 + RLI) exactly -- a built-in cross-check.
python3 stage6_transfer/transfer.py --floor \
  --lifetime "$RESULTS/hycsim/lifetime_all_mixes.csv" \
  --models   "$RESULTS/nvmexplorer/trace_profiles.csv" \
  --target-years 3,5,10 \
  --out "$RESULTS/endurance_floor.csv" 2>&1 | tee -a "$LOG"

say "stage E3 - is any scheme Pareto-optimal on EVERY technology?"
python3 stage6_transfer/transfer.py --frontier \
  --lifetime "$RESULTS/hycsim/lifetime_all_mixes.csv" \
  --models   "$RESULTS/nvmexplorer/trace_profiles.csv" \
  --destiny  "$RESULTS/destiny/results.csv" 2>&1 | tee -a "$LOG"

fi   # end of the "array models present" guard opened in stage D

say "stage E4 - DNN/graph break-even and endurance bounds (rescue plan Part 7)"
if [ -d "$RESULTS/nvmexplorer_dnn/results" ]; then
  python3 stage4_analysis/dnn_breakeven.py \
    --dnn-dir "$RESULTS/nvmexplorer_dnn/results" \
    --out "$RESULTS/dnn_breakeven.csv" 2>&1 | tee -a "$LOG"
else
  info "no nvmexplorer_dnn/results; Part 7 skipped"
fi

say "stage F - reviewer-facing analyses"
python3 stage4_analysis/pareto.py --results "$RESULTS" --section all 2>&1 | tee -a "$LOG"

say "stage G - independent re-derivation"
# Deliberately shares no helper with stage F: a check that imports the code it
# checks cannot catch an error in that code.
python3 scripts/verify_headline_numbers.py --results "$RESULTS" 2>&1 | tee -a "$LOG"
VERIFY=$?
if [ $VERIFY -ne 0 ]; then
  info "mismatches above are not automatically failures: adding or removing a"
  info "scheme changes the proxy comparison set, and the counts move with it."
  info "Each mismatch must be EXPLAINED before the number is quoted."
fi

say "stage G2 - head-to-head against the published state of the art"
# Everything up to here compares against `unmanaged`, which answers "does wear
# management help" -- a question nobody disputes. This answers the one a
# reviewer actually asks, audits tuning parity so the comparison cannot be a
# tuning-budget artefact, and repeats the test on the subset of mixes where
# both schemes hold the acceptance guard.
python3 stage4_analysis/sota.py --results "$RESULTS" --selection loo \
  --out "$RESULTS/sota_headtohead.csv" 2>&1 | tee -a "$LOG"

say "stage G3 - which story does the evidence select?"
# The rescue plan fixed the decision rule before the results existed. Applying
# it mechanically keeps the choice auditable: a rule chosen after seeing the
# data is not a rule, it is a preference.
python3 scripts/decide_narrative.py --results "$RESULTS" 2>&1 | tee -a "$LOG"

say "stage H - claim coverage audit"
# The last check, and the one that answers "did we skip a stage that produced
# a publishable result". It enumerates claims rather than stages: a claim whose
# artefact is absent is reported with the command that produces it.
python3 scripts/coverage_audit.py --results "$RESULTS" 2>&1 | tee -a "$LOG"
COVERAGE=$?

say "done"
info "results  $RESULTS"
info "log      $LOG"
info "phase    $RESULTS/cell_phase_diagram.csv"
info "floor    $RESULTS/endurance_floor.csv"
info "dnn      $RESULTS/dnn_breakeven.csv"
if [ "${COVERAGE:-0}" -ne 0 ]; then
  info ""
  info "Some claims have no artefact behind them (listed above). They are not"
  info "errors in what ran; they are results the paper cannot cite yet."
fi
cat <<'EOF'

Before any of it goes in the paper:
  1. Report the leave-one-mix-out numbers. Per-mix tuning is an oracle ceiling.
  2. Quote every geomean with its guard split. A scheme that holds the guard on
     4 of 10 mixes has a 4-of-10 result, not a 10-mix result.
  3. Quote the endurance decade with every SOT lifetime; it is a band.
  4. State n_families beside n_traces for any AI-inclusive statistic.
  5. A MARGINAL or DEAD liveness row is not evidence about its scheme.
EOF
