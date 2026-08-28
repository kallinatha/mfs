#!/usr/bin/env bash
# Convert every raw mix trace in $1 into an mmap-able per-mix directory in $2.
# Usage: ./prep_all_mixes.sh /data/raw_traces /data/prepared [parallel_jobs]
set -euo pipefail
SRC=${1:?source dir of *.txt traces}
DST=${2:?destination dir}
J=${3:-4}
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$DST"
ls "$SRC"/*.txt | xargs -P "$J" -I{} bash -c '
  f="{}"; n=$(basename "$f" .txt)
  echo "prep $n"
  python3 '"$HERE"'/stage3_hycsim/prep_trace.py "$f" -o "'"$DST"'/$n"
'
echo "prepared mixes:"; ls -d "$DST"/*/
