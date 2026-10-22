#!/usr/bin/env bash
# CPU used inside the shinobi container over a time window: per process (camera.js, each monitor's
# node helper + ffmpeg) and for the whole container (cgroup v2 cpu.stat).
#
#   scripts/shinobi-cpu.sh [SECONDS=60] [RUNS=1]
#
# 100 % = one full core. Counts CPU *time* (utime+stime ticks, cgroup usage_usec) between two
# snapshots, so it is exact for the window, unlike a %CPU snapshot from ps/top. Processes that
# start and exit inside the window (e.g. Shinobi's per-segment thumbnail ffmpeg) are missing from
# the per-process lines but included in CONTAINER TOTAL. On a laptop the numbers depend on the
# CPU clock (battery, power profile, P- vs E-cores): compare runs taken under the same conditions.
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
secs=${1:-60}
runs=${2:-1}

read -r -d '' inner <<'EOF' || true
secs=$1
hz=$(getconf CLK_TCK)
snap() {
  for p in /proc/[0-9]*; do
    [ -r "$p/stat" ] || continue
    [ "${p#/proc/}" = "$$" ] && continue   # this script: its own text mentions ffmpeg
    cmd=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null)
    case "$cmd" in *ffmpeg*|*singleCamera*|*camera.js*|*mariadbd*|*pm2*) ;; *) continue ;; esac
    # utime + stime (fields 14, 15); strip "pid (comm) " first, comm may contain spaces
    t=$(sed 's/^.*) //' "$p/stat" | awk '{print $12+$13}')
    mon=$(echo "$cmd" | grep -o '/cam_[a-z0-9_]*/' | head -1 | tr -d /)
    kind=$(echo "$cmd" | awk '{n=$1; if($1=="node")n=$2; sub(/.*\//,"",n); print n}')
    echo "${p#/proc/} $t ${kind}:${mon:--}"
  done
}
snap > /tmp/cpu0
c0=$(awk '/usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat)   # after the snapshot: its cost stays outside
sleep "$secs"
c1=$(awk '/usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat)
snap > /tmp/cpu1
awk -v hz="$hz" -v s="$secs" 'NR==FNR{a[$1]=$2; next} ($1 in a){d=($2-a[$1])/hz/s*100; if (d>=0.05) printf "%7.1f %%  %s\n", d, $3}' /tmp/cpu0 /tmp/cpu1 | sort -k3
awk -v c0="$c0" -v c1="$c1" -v s="$secs" 'BEGIN{printf "%7.1f %%  CONTAINER TOTAL\n", (c1-c0)/1e4/s}'
EOF

for ((i = 1; i <= runs; i++)); do
  echo "== run $i/$runs (${secs} s)"
  docker compose exec -T shinobi sh -c "$inner" sh "$secs"
done
