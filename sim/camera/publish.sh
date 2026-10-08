#!/usr/bin/env bash
# Simulated IP camera: loop a clip into an RTSP server forever.
#
# Two ways to configure it:
#   - Command mode: pass the full command as arguments (the generated fleet in
#     compose/cameras.generated.yml does this); this script only supervises it.
#   - Env mode (no arguments): CAM_NAME, INPUT, RTSP_URL, MODE, FPS, BITRATE.
# Either way, the command is restarted 2 s after it exits and SIGTERM is forwarded.
set -euo pipefail

CAM_NAME="${CAM_NAME:-${HOSTNAME:-camera}}"

log() {
    printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$CAM_NAME" "$*" >&2
}

die() {
    log "error: $*"
    exit 1
}

build_env_cmd() {
    : "${INPUT:?INPUT is required (path to the clip inside the container)}"
    RTSP_URL="${RTSP_URL:-rtsp://mediamtx:8554/${CAM_NAME}}"
    MODE="${MODE:-copy}"
    FPS="${FPS:-25}"
    BITRATE="${BITRATE:-2M}"

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
    log "publishing $INPUT -> $RTSP_URL (mode=$MODE)"
}

if (($# > 0)); then
    cmd=("$@")
    log "supervising: ${cmd[*]}"
else
    build_env_cmd
fi

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

while true; do
    # Run in the background and wait, so the trap fires immediately on a
    # signal instead of after FFmpeg exits. </dev/null: no -nostdin in command mode.
    "${cmd[@]}" </dev/null &
    ffmpeg_pid=$!
    rc=0
    wait "$ffmpeg_pid" || rc=$?
    ffmpeg_pid=""
    log "command exited with code $rc, restarting in 2 s"
    sleep 2
done
