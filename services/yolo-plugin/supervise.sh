#!/bin/bash
# Run the plugin's own entrypoint, pass its output through, and exit when it gives up on Shinobi.
#
# pluginBase.js (client mode) retries the socket.io connection maxRetryConnection times (5,
# 3 s apart; one failure can count up to 3 times), then sets stayDisconnected and never tries
# again - but the node process keeps running, so Docker sees a healthy container that will never
# send a detection (reproduced in docs/notes/15-object-detection.md). Exiting instead lets
# `restart: unless-stopped` start a fresh process (model load ~1 s), which reconnects as soon as
# Shinobi is back.
set -uo pipefail

fifo=$(mktemp -u)
mkfifo "$fifo"
/docker-entrypoint.sh >"$fifo" 2>&1 &
child=$!

stop() {
    kill -TERM "$child" 2>/dev/null
    wait "$child"
    exit "$1"
}
trap 'stop 143' TERM INT

while IFS= read -r line; do
    printf '%s\n' "$line"
    if [[ $line == *"Max retries reached"* ]]; then
        echo "[supervise] plugin stopped reconnecting to Shinobi; exiting so Docker restarts it"
        stop 1
    fi
done <"$fifo"

wait "$child"
exit $?
