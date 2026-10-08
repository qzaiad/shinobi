#!/usr/bin/env bash
# Generate the camera fleet from sim/cameras.yaml and start MediaMTX + all cameras.
# --remove-orphans drops containers of cameras that were removed from the YAML.
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"

uv run python sim/gen_compose.py
# --build: rebuild the camera image when sim/camera/ changed (cached otherwise).
docker compose -f docker-compose.yml -f compose/cameras.generated.yml up -d --build --remove-orphans
