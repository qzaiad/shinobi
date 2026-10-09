#!/usr/bin/env bash
# Grab one decoded frame from an RTSP stream as JPEG, like a camera's snapshot endpoint.
# Prints the host UTC time just before and after, to compare with the burned-in OSD clock.
#
#   sim/scripts/snapshot.sh rtsp://localhost:8554/cam-gate [out.jpg]
set -euo pipefail

url="${1:?usage: $0 RTSP_URL [OUT.jpg]}"
out="${2:-snapshot-$(basename "$url").jpg}"

echo "before: $(date -u '+%Y-%m-%d %H:%M:%S.%3N') UTC"
# -update 1: write a single image (no %03d pattern); -q:v 3: good JPEG quality.
ffmpeg -hide_banner -loglevel error -rtsp_transport tcp -i "$url" \
    -frames:v 1 -update 1 -q:v 3 -y "$out"
echo "after:  $(date -u '+%Y-%m-%d %H:%M:%S.%3N') UTC"
echo "wrote $out"
