#!/bin/bash
# I1 — repeatability matrix: N iters per VM, parallel. Logs heartbeat + CSV.
set -u

ITERS=${ITERS:-10}
HB="/tmp/i1-progress.log"
RESULTS="/tmp/i1-results.csv"
LOG_DIR="/tmp/i1-logs"
mkdir -p "$LOG_DIR"

cd "$(dirname "$0")/.."

[ -f "$RESULTS" ] || echo "vm,iter,result,duration_s,run_dir,end_ts" > "$RESULTS"

hb() { echo "[HB $(date -Iseconds)] $*" >> "$HB"; }

for_vm() {
  local vm=$1 test=$2
  for i in $(seq 1 "$ITERS"); do
    hb "vm=$vm iter=$i begin"
    local t0=$(date +%s)
    if mosdat functional examples/rocketchat.toml --vms "$vm" --test "$test" \
         > "$LOG_DIR/$vm-iter-$i.log" 2>&1; then
      result=PASS
    else
      result=FAIL
    fi
    local t1=$(date +%s)
    latest=$(ls -td results/functional/*/ 2>/dev/null | head -1 || echo "")
    echo "$vm,$i,$result,$((t1-t0)),$latest,$(date -Iseconds)" >> "$RESULTS"
    hb "vm=$vm iter=$i done $result ${tt:-$((t1-t0))}s"
  done
  hb "vm=$vm complete"
}

hb "matrix begin iters=$ITERS vms=ubuntu2204,ubuntu2404,fedora42,manjaro,opensuse"

for_vm ubuntu2204 rocketchat-smoke-linux
for_vm ubuntu2404 rocketchat-smoke-fedora       # Wayland: SSH launch broken, use Activities
for_vm fedora42   rocketchat-smoke-fedora
for_vm manjaro    rocketchat-smoke-linux-kde
for_vm opensuse   rocketchat-smoke-linux-kde

hb "matrix done"
