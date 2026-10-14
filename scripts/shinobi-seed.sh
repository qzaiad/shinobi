#!/usr/bin/env bash
# Create data/shinobi/{conf,super}.json from the samples in the pinned Shinobi image, once.
# They are bind-mounted as single files; a missing source would make Docker create a directory.
# Existing files are never touched (they hold your superuser password hash and settings).
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"

digest=$(sed -n 's/^SHINOBI_DIGEST=//p' .env 2>/dev/null || true)
[[ -n "$digest" ]] || { echo "set SHINOBI_DIGEST in .env (see .env.example)" >&2; exit 1; }
image="registry.gitlab.com/shinobi-systems/shinobi@$digest"

mkdir -p data/shinobi
for name in conf super; do
    dst="data/shinobi/$name.json"
    if [[ -d "$dst" ]]; then
        echo "$dst is a directory (Docker created it); remove it and re-run" >&2
        exit 1
    fi
    [[ -f "$dst" ]] && continue
    docker run --rm --entrypoint cat "$image" "/home/Shinobi/$name.sample.json" > "$dst.part"
    mv "$dst.part" "$dst"
    echo "seeded $dst"
done
