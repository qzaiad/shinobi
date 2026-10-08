#!/usr/bin/env bash
# Check that an RTSP stream keeps flowing across clip loop boundaries.
# Reads video packet DTS for 2.5 x the clip duration of stream time (so at least one loop
# boundary is crossed) and fails on non-increasing DTS or a gap > 0.5 s.
#
# Usage: sim/scripts/check_stream.sh URL INPUT_FILE
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 URL INPUT_FILE" >&2
    exit 2
fi
url=$1
input=$2
max_gap=0.5

duration=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$input")
[[ "$duration" =~ ^[0-9]+(\.[0-9]+)?$ ]] || {
    echo "cannot read duration of '$input' (got '$duration')" >&2
    exit 2
}
seconds=$(awk -v d="$duration" 'BEGIN { printf "%d", d * 2.5 + 0.5 }')
echo "clip duration ${duration}s, sampling $url for ${seconds}s" >&2

# ffprobe stops itself after $seconds of stream time (-read_intervals) so it
# exits cleanly and flushes its output; killing it from outside loses the
# buffered packet lines. timeout is only a watchdog: since the publisher runs
# with -re, stream time tracks wall time, so a stall makes timeout fire (124).
set +e
timeout "$((seconds + 20))" ffprobe -v error -rtsp_transport tcp -select_streams v:0 \
    -read_intervals "%+${seconds}" \
    -show_entries packet=dts_time -of csv=p=0 "$url" |
    # -F, because ffprobe appends an empty side-data field (trailing comma)
    # to packets that carry side data, e.g. keyframes with SPS/PPS.
    awk -F, -v max_gap="$max_gap" -v seconds="$seconds" '
        $1 ~ /^-?[0-9]+(\.[0-9]+)?$/ {
            dts = $1 + 0
            if (n == 0)
                first = dts
            if (n > 0) {
                gap = dts - prev
                if (gap <= 0) {
                    if (++bad <= 10)
                        printf "non-increasing DTS at packet %d: %.6f -> %.6f\n", n + 1, prev, dts
                } else if (gap > maxg) {
                    maxg = gap
                    maxg_at = prev
                }
            }
            prev = dts
            n++
        }
        END {
            span = n > 0 ? prev - first : 0
            printf "packets: %d\nDTS span: %.3f s\nnon-increasing DTS: %d\nmax DTS gap: %.3f s (after DTS %.3f)\n", n, span, bad, maxg, maxg_at
            # A short span means packets were lost or not parsed: never pass on that.
            if (span < 0.9 * seconds)
                printf "DTS span %.3f s covers less than 90%% of %d s\n", span, seconds
            exit (span < 0.9 * seconds || bad > 0 || maxg > max_gap)
        }'
status=("${PIPESTATUS[@]}")
set -e

if [[ ${status[0]} -eq 124 ]]; then
    echo "FAIL: stream stalled, ffprobe did not finish within $((seconds + 20))s" >&2
    exit 1
fi
if [[ ${status[0]} -ne 0 ]]; then
    echo "FAIL: ffprobe exited with ${status[0]}" >&2
    exit 1
fi
if [[ ${status[1]} -ne 0 ]]; then
    echo "FAIL: DTS not increasing, gap > ${max_gap}s, or too little of the stream seen" >&2
    exit 1
fi
echo "OK" >&2
