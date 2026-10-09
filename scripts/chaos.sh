#!/usr/bin/env bash
# Fault injection for the simulated cameras: make them fail like real IP cameras do.
#
#   scripts/chaos.sh stop    <cam>        graceful shutdown (RTSP TEARDOWN, then TCP FIN)
#   scripts/chaos.sh start   <cam>        start a stopped camera again
#   scripts/chaos.sh crash   <cam>        SIGKILL the encoder; publish.sh restarts it after 2 s
#   scripts/chaos.sh freeze  <cam>        docker pause: socket stays open, bytes stop (hung camera)
#   scripts/chaos.sh thaw    <cam>        docker unpause
#   scripts/chaos.sh flap    <cam> [--down S] [--up S] [--count N]   stop/start N times
#   scripts/chaos.sh netem   <cam> <netem args...>   e.g. loss 10%  |  delay 200ms 50ms  |  loss 100%
#   scripts/chaos.sh clear   <cam>        remove netem shaping
#   scripts/chaos.sh status  [<cam>]      container state + qdisc of one or all cameras
#
# <cam> is the compose service name (= camera id in sim/cameras.yaml), e.g. cam-gate.
# netem shapes the camera's egress (camera -> MediaMTX, the publish direction). It runs `tc` in a
# throw-away helper container that shares the camera's network namespace, so neither sudo on the
# host nor NET_ADMIN on the camera itself is needed.
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"

COMPOSE=(docker compose -f docker-compose.yml -f compose/cameras.generated.yml)
IFACE=eth0

log() {
    printf '%s [chaos] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

die() {
    log "error: $*"
    exit 1
}

usage() {
    sed -n '2,/^set -euo/{/^set -euo/d;s/^# \{0,1\}//;p}' "${BASH_SOURCE[0]}" >&2
    exit 2
}

# Container id of a camera service (also when stopped or paused).
cid_of() {
    local cid
    cid=$("${COMPOSE[@]}" ps -a -q "$1" 2>/dev/null) || true
    [[ -n "$cid" ]] || die "no container for service '$1' (typo? is the fleet up: scripts/sim-up.sh)"
    printf '%s\n' "$cid"
}

state_of() {
    docker inspect -f '{{.State.Status}}' "$1"
}

# Run tc inside the camera's network namespace from a helper container using the camera's image.
tc_in() {
    local cid=$1
    shift
    [[ "$(state_of "$cid")" != exited ]] || die "camera is stopped; start it first"
    local image
    image=$(docker inspect -f '{{.Config.Image}}' "$cid")
    docker run --rm --network "container:$cid" --cap-add NET_ADMIN --entrypoint tc "$image" "$@"
}

cmd_crash() {
    local cid=$1
    # No pkill in the image: walk /proc for processes named ffmpeg.
    docker exec "$cid" bash -c \
        'for p in /proc/[0-9]*; do [[ $(<"$p/comm") == ffmpeg ]] && kill -KILL "${p#/proc/}"; done; true'
}

cmd_flap() {
    local svc=$1 cid=$2
    shift 2
    local down=5 up=10 count=3
    while (($# > 0)); do
        case "$1" in
            --down) down=${2:?}; shift 2 ;;
            --up) up=${2:?}; shift 2 ;;
            --count) count=${2:?}; shift 2 ;;
            *) die "flap: unknown option '$1'" ;;
        esac
    done
    local i
    for ((i = 1; i <= count; i++)); do
        log "$svc flap $i/$count: down for ${down} s"
        docker stop -t 2 "$cid" >/dev/null
        sleep "$down"
        log "$svc flap $i/$count: up for ${up} s"
        docker start "$cid" >/dev/null
        sleep "$up"
    done
}

cmd_status() {
    local svcs=("$@") svc cid
    if ((${#svcs[@]} == 0)); then
        mapfile -t svcs < <("${COMPOSE[@]}" config --services | grep '^cam-')
    fi
    for svc in "${svcs[@]}"; do
        cid=$(cid_of "$svc")
        local state qdisc="-"
        state=$(state_of "$cid")
        if [[ "$state" != exited ]]; then
            qdisc=$(tc_in "$cid" qdisc show dev "$IFACE" | head -n 1)
        fi
        printf '%-16s %-8s %s\n' "$svc" "$state" "$qdisc"
    done
}

(($# >= 1)) || usage
action=$1
shift

if [[ "$action" == status ]]; then
    cmd_status "$@"
    exit 0
fi

(($# >= 1)) || usage
svc=$1
shift
cid=$(cid_of "$svc")

case "$action" in
    stop) docker stop -t 2 "$cid" >/dev/null ;;
    start) docker start "$cid" >/dev/null ;;
    crash) cmd_crash "$cid" ;;
    freeze) docker pause "$cid" >/dev/null ;;
    thaw) docker unpause "$cid" >/dev/null ;;
    flap) cmd_flap "$svc" "$cid" "$@" ;;
    netem)
        (($# >= 1)) || die "netem needs arguments, e.g.: netem $svc loss 10%"
        # replace = add or change, so repeated calls don't fail with "File exists".
        tc_in "$cid" qdisc replace dev "$IFACE" root netem "$@"
        ;;
    clear)
        # Deleting the root qdisc restores the default; ignore "no such qdisc".
        tc_in "$cid" qdisc del dev "$IFACE" root 2>/dev/null || true
        ;;
    *) usage ;;
esac
log "$svc: $action $*"
