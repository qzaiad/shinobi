#!/usr/bin/env bash
# Check a normalized clip against the simulated-camera profile (docs/camera-profile.md).
# Prints PASS/FAIL per check; exit 0 if all pass, 1 if any fails, 2 on usage/read errors.
#
# Usage: sim/scripts/probe_clip.sh FILE
set -uo pipefail

FPS=25/1
GOP=50
MAX_WIDTH=1280

[[ $# -eq 1 ]] || { printf 'usage: %s FILE\n' "$0" >&2; exit 2; }
file=$1
[[ -f "$file" ]] || { printf 'probe_clip: %s does not exist\n' "$file" >&2; exit 2; }

failures=0
check() { # check NAME OK DETAIL
    if [[ $2 == 1 ]]; then
        printf 'PASS  %-24s %s\n' "$1" "$3"
    else
        printf 'FAIL  %-24s %s\n' "$1" "$3"
        failures=$((failures + 1))
    fi
}
ok() { "$@" && echo 1 || echo 0; }

# --- stream parameters (first video stream) --------------------------------
if ! stream=$(ffprobe -v error -select_streams v:0 \
    -show_entries stream=codec_name,profile,pix_fmt,width,height,avg_frame_rate,r_frame_rate,has_b_frames \
    -of default=noprint_wrappers=1 -- "$file") || [[ -z "$stream" ]]; then
    printf 'probe_clip: ffprobe found no video stream in %s\n' "$file" >&2
    exit 2
fi
declare -A s
while IFS='=' read -r k v; do s[$k]=$v; done <<<"$stream"

check codec "$(ok [ "${s[codec_name]}" = h264 ])" "codec_name=${s[codec_name]}"
check profile "$(ok [ "${s[profile]}" = Main ])" "profile=${s[profile]}"
check pix_fmt "$(ok [ "${s[pix_fmt]}" = yuv420p ])" "pix_fmt=${s[pix_fmt]}"
w=${s[width]} h=${s[height]}
check even_dimensions "$(ok [ $((w % 2)) -eq 0 -a $((h % 2)) -eq 0 ])" "${w}x${h}"
check max_width "$(ok [ "$w" -le "$MAX_WIDTH" ])" "width=$w (max $MAX_WIDTH)"
check frame_rate \
    "$(ok [ "${s[avg_frame_rate]}" = "$FPS" -a "${s[r_frame_rate]}" = "$FPS" ])" \
    "avg_frame_rate=${s[avg_frame_rate]} r_frame_rate=${s[r_frame_rate]} (want $FPS)"
check no_b_frames "$(ok [ "${s[has_b_frames]}" = 0 ])" "has_b_frames=${s[has_b_frames]}"

# --- no audio ---------------------------------------------------------------
audio=$(ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 -- "$file" | wc -l)
check no_audio "$(ok [ "$audio" -eq 0 ])" "audio streams=$audio"

# --- first packet starts at 0 -----------------------------------------------
# cut: ffprobe's CSV appends a trailing ',' on packets with side data.
pts0=$(ffprobe -v error -select_streams v:0 -show_entries packet=pts_time \
    -read_intervals '%+#1' -of csv=p=0 -- "$file" | cut -d, -f1)
check first_pts_zero "$(ok awk -v t="$pts0" 'BEGIN { exit !(t != "" && t + 0 == 0) }')" \
    "pts_time=${pts0:-none}"

# --- first picture is an IDR ------------------------------------------------
# In MP4, SPS/PPS (7/8) live in the avcC extradata and x264 puts an SEI (6) in
# front of the first slice, so the check is: the first VCL NAL (type 1..5) of
# the first packet is type 5 (IDR slice).
nal=$(ffmpeg -hide_banner -nostdin -loglevel info -i "$file" -map 0:v:0 -c copy \
    -bsf:v trace_headers -frames:v 1 -f null - 2>&1 |
    awk '/Packet:/ { inpkt = 1; next }
         inpkt && /nal_unit_type/ { t = $NF + 0; if (t >= 1 && t <= 5) { print t; exit } }')
check first_nal_idr "$(ok [ "$nal" = 5 ])" "first VCL nal_unit_type=${nal:-none}"

# --- fixed GOP --------------------------------------------------------------
# Keyframe exactly on packets 0, GOP, 2*GOP, ... and nowhere else. Packets are
# in decode order, which equals presentation order without B-frames.
gop=$(ffprobe -v error -select_streams v:0 -show_entries packet=flags -of csv=p=0 -- "$file" |
    awk -v gop="$GOP" -F, '
        { key = substr($1, 1, 1) == "K"; want = (NR - 1) % gop == 0; nk += key
          if (key != want && !bad) bad = sprintf("packet %d key=%d", NR - 1, key) }
        END { printf "%s|%d packets, %d keyframes%s", (NR && !bad), NR, nk, bad ? ", first mismatch: " bad : "" }')
check fixed_gop "${gop%%|*}" "every $GOP frames: ${gop#*|}"

[[ $failures -eq 0 ]] || { printf '%d check(s) failed\n' "$failures"; exit 1; }
printf 'all checks passed\n'
