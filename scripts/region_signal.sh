#!/usr/bin/env bash
# Per-frame "% of region pixels changed" of a live stream, computed the way Shinobi's built-in
# motion detector (pam-diff, pixel-change) does: gray frames at the detector fps, difference to the
# previous frame, a pixel counts when |Δluma| >= COLOR_THRESHOLD, percent = changed / region pixels.
# Rectangle regions only (crop); no blob step, so this is the upper bound of what pam-diff reports.
#
#   scripts/region_signal.sh URL X Y W H [SECONDS=20] [FPS=5] [COLOR_THRESHOLD=9]
#   scripts/region_signal.sh rtsp://localhost:8554/cam-quay-sub 0 288 550 72 20
#
# X Y W H in pixels of the stream (sub: 640x360). Output: "t_seconds percent", one line per frame.
# Use it to pick sensitivity (above the quiet-scene percent) and max_sensitivity (below spikes such
# as loop seams), see docs/notes/14-motion-detection.md.
set -euo pipefail

[[ $# -ge 5 ]] || { sed -n '2,12p' "$0"; exit 2; }
url=$1 x=$2 y=$3 w=$4 h=$5 secs=${6:-20} fps=${7:-5} thr=${8:-9}

ffmpeg -nostdin -hide_banner -loglevel fatal -rtsp_transport tcp -i "$url" -t "$secs" \
  -vf "fps=${fps},format=gray,crop=${w}:${h}:${x}:${y},tblend=all_mode=difference,\
lut=y='if(gte(val,${thr}),255,0)',signalstats,\
metadata=print:key=lavfi.signalstats.YAVG:file=-" \
  -f null - |
  awk -v fps="$fps" '
    /^frame:/ { split($3, a, ":"); t = a[2] }
    /YAVG=/   { split($0, b, "="); printf "%6.1f %6.2f\n", t, b[2] / 255 * 100 }'
