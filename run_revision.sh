#!/usr/bin/env bash
# One command for the full corrected revision study.
#
# What this runs, in order (docs/REVISION_NOTES.md has the full rationale):
#   1. setup.sh --with-champsim     idempotent; skips tools already built
#   2. run_all.sh with the production config, which now includes:
#        - all 22 schemes in the stage-7 replay (6 were silently missing)
#        - guard-split aggregation (report the *_guarded columns)
#        - three-technology cross-stack join (SOT primary, STT, PCM)
#        - PCM in the DSE knob screen
#        - the new nvmdnn stage: NVMExplorer's DNN/graph AI workloads on the
#          same cited cells
#        - ChampSim with the ${CHAMPSIM_DIR} expansion fix, plus any AI traces
#          found under champsim_ai_traces_root
#
# NOTE if a ChampSim batch from a previous invocation is still running, let it
# finish or kill it first — two batches writing the same outdir will interleave
# logs. Completed per-run .log files are cached and reused, so nothing already
# finished is wasted.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# A stale exported CHAMPSIM_DIR (e.g. the literal '${CHAMPSIM_DIR}' or a path
# with an empty $ROOT) has burned this study twice. The config and run_all.sh
# resolve it correctly on their own; never inherit a broken one.
case "${CHAMPSIM_DIR:-}" in
  ''|*'$'*) unset CHAMPSIM_DIR 2>/dev/null || true ;;
  *) [ -d "${CHAMPSIM_DIR}" ] || unset CHAMPSIM_DIR ;;
esac

./setup.sh --with-champsim
exec ./run_all.sh --config config/suscom_ten_mixes.json "$@"
