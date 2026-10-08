#!/usr/bin/env bash
# Re-encode a raw source clip to the simulated-camera profile (docs/camera-profile.md),
# so publishers can loop it with -c copy.
#
# Usage: sim/scripts/normalize.sh RAW_IN OUT
#   RAW_IN  source clip; if it lives in media/, it must be under media/raw/
#   OUT     normalized MP4 (written to OUT.part, then renamed atomically)
#
# Check the result with: sim/scripts/probe_clip.sh OUT
set -euo pipefail

die() {
    printf 'normalize: %s\n' "$*" >&2
    exit 1
}

[[ $# -eq 2 ]] || die "usage: $0 RAW_IN OUT"
raw_in=$1
out=$2

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)
media="$repo/media"

[[ -f "$raw_in" ]] || die "RAW_IN '$raw_in' does not exist"
in_abs=$(realpath -e -- "$raw_in")
out_abs=$(realpath -m -- "$out")

# Raw footage is the pristine copy; normalized clips are derived from it.
if [[ "$in_abs" == "$media"/* && "$in_abs" != "$media"/raw/* ]]; then
    die "RAW_IN '$raw_in' is inside media/ but not under media/raw/"
fi
# -ef also catches hard links and symlinks to RAW_IN.
if [[ "$in_abs" == "$out_abs" || "$raw_in" -ef "$out" ]]; then
    die "OUT '$out' would overwrite RAW_IN"
fi

tmp="$out.part"
trap 'rm -f -- "$tmp"' EXIT

# - fps=25 makes the output CFR (drops/duplicates frames for 59.94 / 23.976 sources).
# - Width is capped at 1280 (never upscaled) and both dimensions are kept even,
#   which yuv420p chroma subsampling requires.
# - Fixed 2 s GOP: -g = -keyint_min = 50 and no scene-cut keyframes; -bf 0: no B-frames.
# - -f mp4 is explicit because the .part suffix hides the container from FFmpeg.
ffmpeg -hide_banner -nostdin -loglevel warning -y \
    -i "$raw_in" \
    -map 0:v:0 -an -sn -dn \
    -vf "fps=25,scale=w='trunc(min(1280\,iw)/2)*2':h=-2" \
    -c:v libx264 -preset slow -crf 23 -profile:v main -pix_fmt yuv420p \
    -g 50 -keyint_min 50 -sc_threshold 0 -bf 0 \
    -movflags +faststart -f mp4 "$tmp"

mv -f -- "$tmp" "$out"
trap - EXIT
printf 'normalize: wrote %s\n' "$out" >&2
