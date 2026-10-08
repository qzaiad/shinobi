#!/usr/bin/env bash
# Stop and remove MediaMTX and all simulated cameras.
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"

files=(-f docker-compose.yml)
[[ -f compose/cameras.generated.yml ]] && files+=(-f compose/cameras.generated.yml)
docker compose "${files[@]}" down --remove-orphans
