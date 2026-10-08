#!/usr/bin/env bash
# Simulated IP camera: loop a clip into an RTSP server forever.
# Configured purely by environment variables (see sim/camera/Dockerfile).
set -euo pipefail

: "${CAM_NAME:?CAM_NAME is required}"
: "${INPUT:?INPUT is required (path to the clip inside the container)}"
RTSP_URL="${RTSP_URL:-rtsp://mediamtx:8554/${CAM_NAME}}"
MODE="${MODE:-copy}"
FPS="${FPS:-25}"
BITRATE="${BITRATE:-2M}"

log() {
    printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$CAM_NAME" "$*" >&2
}

die() {
    log "error: $*"
    exit 1
}

[[ -f "$INPUT" ]] || die "INPUT '$INPUT' does not exist (is the footage mounted? run ./scripts/fetch_media.sh)"
[[ "$FPS" =~ ^[1-9][0-9]*$ ]] || die "FPS must be a positive integer, got '$FPS'"

# -bufsize = 2 x bitrate, keeping FFmpeg's unit suffix (2M -> 4M, 2500k -> 5000k).
[[ "$BITRATE" =~ ^([1-9][0-9]*)([kKmM]?)$ ]] || die "BITRATE must look like 2M, 2500k or 2000000, got '$BITRATE'"
BUFSIZE="$((BASH_REMATCH[1] * 2))${BASH_REMATCH[2]}"

GOP=$((FPS * 2))

cmd=(ffmpeg -hide_banner -loglevel warning -nostdin
    -re -stream_loop -1 -i "$INPUT"
    -map 0:v:0 -an)

case "$MODE" in
    copy)
        cmd+=(-c:v copy)
        ;;
    encode)
        # -r pins the output rate so the GOP below really is 2 s,
        # whatever the source frame rate is.
        cmd+=(-c:v libx264 -preset veryfast -tune zerolatency
            -profile:v main -pix_fmt yuv420p -r "$FPS"
            -g "$GOP" -keyint_min "$GOP" -sc_threshold 0 -bf 0
            -b:v "$BITRATE" -maxrate "$BITRATE" -bufsize "$BUFSIZE")
        ;;
    *)
        die "MODE must be 'copy' or 'encode', got '$MODE'"
        ;;
esac

cmd+=(-f rtsp -rtsp_transport tcp "$RTSP_URL")

ffmpeg_pid=""

shutdown() {
    log "signal received, stopping"
    if [[ -n "$ffmpeg_pid" ]]; then
        kill -TERM "$ffmpeg_pid" 2>/dev/null || true
        wait "$ffmpeg_pid" 2>/dev/null || true
    fi
    exit 0
}
trap shutdown TERM INT

log "publishing $INPUT -> $RTSP_URL (mode=$MODE)"
while true; do
    # Run in the background and wait, so the trap fires immediately on a
    # signal instead of after FFmpeg exits.
    "${cmd[@]}" &
    ffmpeg_pid=$!
    rc=0
    wait "$ffmpeg_pid" || rc=$?
    ffmpeg_pid=""
    log "ffmpeg exited with code $rc, restarting in 2 s"
    sleep 2
done
