#!/usr/bin/env bash
# Download and verify simulator footage listed in media/manifest.tsv.
#
# Usage: scripts/fetch_media.sh [--pin] [--only ID] [--probe]
#   --pin       for rows with an empty sha256: download, hash, write the hash back
#   --only ID   process only the row with this id
#   --probe     print a one-line ffprobe summary per file (skipped if ffprobe is missing)
#
# Exit codes: 0 all OK, 1 hash mismatch / unpinned row, 2 download failure,
#             3 missing dependency, bad usage or bad manifest.
set -euo pipefail

readonly USER_AGENT="shinobi-port-twin/0.1 (+https://github.com/qzaiad/shinobi)"
readonly REQUIRED_COLS=(id role filename url sha256 license)

usage() {
    sed -n '2,10s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"
}

die() {  # die CODE MESSAGE
    echo "fetch_media: $2" >&2
    exit "$1"
}

# --- dependencies ---
missing=()
for dep in curl sha256sum; do
    command -v "$dep" >/dev/null 2>&1 || missing+=("$dep")
done
((${#missing[@]} == 0)) || die 3 "missing dependency: ${missing[*]}"

# --- paths: repo root, falling back to the parent of this script's directory ---
script_path=${BASH_SOURCE[0]}
[[ $script_path == */* ]] || script_path=./$script_path
script_dir=$(cd -- "${script_path%/*}" && pwd)
root=$(git -C "$script_dir" rev-parse --show-toplevel 2>/dev/null) || root=${script_dir%/*}
readonly media_dir="$root/media"
readonly manifest="$media_dir/manifest.tsv"

# --- arguments ---
pin=0
probe=0
only=""
while (($#)); do
    case $1 in
        --pin) pin=1 ;;
        --probe) probe=1 ;;
        --only)
            (($# >= 2)) && [[ -n $2 ]] || die 3 "--only needs an id"
            only=$2
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            die 3 "unknown argument: $1"
            ;;
    esac
    shift
done

[[ -f $manifest ]] || die 3 "manifest not found: $manifest"

# --- cleanup: never leave .part or temp files behind ---
cleanup_files=()
# shellcheck disable=SC2329  # invoked via trap
cleanup() {
    local f
    for f in "${cleanup_files[@]}"; do
        [[ -n $f ]] && rm -f -- "$f"
    done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Split a TSV line into the global array `fields`. `read -a` with IFS=$'\t' would
# collapse consecutive tabs (tab is IFS whitespace) and shift columns after an empty field.
fields=()
split_tsv() {
    local rest=$1
    fields=()
    while [[ $rest == *$'\t'* ]]; do
        fields+=("${rest%%$'\t'*}")
        rest=${rest#*$'\t'}
    done
    fields+=("$rest")
}

sha256_of() {
    local out
    out=$(sha256sum -- "$1") || return 1
    echo "${out%% *}"
}

probe_file() {  # probe_file PATH NAME
    local out
    out=$(ffprobe -v error -select_streams v:0 \
        -show_entries stream=codec_name,profile,width,height,pix_fmt,r_frame_rate,has_b_frames \
        -show_entries format=duration -of default=noprint_wrappers=1 -- "$1" 2>&1) || {
        echo "  probe $2: ffprobe failed: ${out//$'\n'/ }"
        return 0
    }
    echo "  probe $2: ${out//$'\n'/ }"
}

# --- header: column name -> index ---
declare -A col=()
header=""
{ IFS= read -r header || true; } <"$manifest"
header=${header%$'\r'}
split_tsv "$header"
for i in "${!fields[@]}"; do
    col[${fields[$i]}]=$i
done
for c in "${REQUIRED_COLS[@]}"; do
    [[ -n ${col[$c]+x} ]] || die 3 "manifest header lacks column '$c'"
done
ncols=${#fields[@]}

if ((probe)) && ! command -v ffprobe >/dev/null 2>&1; then
    echo "note: ffprobe not installed, --probe skipped" >&2
    probe=0
fi

mkdir -p -- "$media_dir"

worst=0
n_ok=0 n_cached=0 n_pinned=0 n_fail=0
seen_only=0
declare -A pinned=()

fail() {  # fail CODE MESSAGE
    echo "FAIL  $2" >&2
    n_fail=$((n_fail + 1))
    (($1 > worst)) && worst=$1
    return 0
}

lineno=1
while IFS= read -r line || [[ -n $line ]]; do
    lineno=$((lineno + 1))
    line=${line%$'\r'}
    [[ -z ${line//[[:space:]]/} || $line == \#* ]] && continue

    split_tsv "$line"
    id=${fields[${col[id]}]:-}
    file=${fields[${col[filename]}]:-}
    url=${fields[${col[url]}]:-}
    want=${fields[${col[sha256]}]:-}
    want=${want,,}

    [[ -n $only && $id != "$only" ]] && continue
    seen_only=1

    if [[ -z $id || -z $file || -z $url ]]; then
        fail 3 "line $lineno: id, filename and url are required"
        continue
    fi
    # Plain file names only: no paths, no hidden files, no traversal.
    if [[ ! $file =~ ^[A-Za-z0-9_][A-Za-z0-9._-]*$ || $file == *.part ]]; then
        fail 3 "$id: bad filename '$file'"
        continue
    fi
    if [[ -z $want && $pin -eq 0 ]]; then
        fail 1 "$id: sha256 is empty (run with --pin to record it)"
        continue
    fi

    target="$media_dir/$file"
    part="$target.part"

    if [[ -n $want && -f $target ]]; then
        if [[ $(sha256_of "$target") == "$want" ]]; then
            echo "OK    $id  $file (cached)"
            n_cached=$((n_cached + 1))
            ((probe)) && probe_file "$target" "$file"
            continue
        fi
        echo "STALE $id  $file: hash mismatch, re-downloading"
    fi

    echo "GET   $id  $url"
    cleanup_files+=("$part")
    rm -f -- "$part"
    progress=(--silent --show-error)
    [[ -t 2 ]] && progress=(--progress-bar)
    if ! curl --fail --location --retry 3 --retry-delay 2 --connect-timeout 15 \
        -A "$USER_AGENT" "${progress[@]}" -o "$part" -- "$url"; then
        rm -f -- "$part"
        fail 2 "$id: download failed"
        continue
    fi

    got=$(sha256_of "$part")
    if [[ -z $want ]]; then
        pinned[$id]=$got
        echo "PIN   $id  $got"
    elif [[ $got != "$want" ]]; then
        rm -f -- "$part"
        fail 1 "$id: sha256 mismatch: want $want, got $got"
        continue
    fi

    mv -f -- "$part" "$target"
    if [[ -z $want ]]; then
        n_pinned=$((n_pinned + 1))
    else
        n_ok=$((n_ok + 1))
        echo "OK    $id  $file"
    fi
    ((probe)) && probe_file "$target" "$file"
done < <(tail -n +2 -- "$manifest")

[[ -n $only && $seen_only -eq 0 ]] && die 3 "no row with id '$only' in manifest"

# --- write pinned hashes back (only into empty sha256 cells) ---
if ((${#pinned[@]})); then
    tmp=$(mktemp -- "$manifest.XXXXXX")
    cleanup_files+=("$tmp")
    {
        printf '%s\n' "$header"
        while IFS= read -r line || [[ -n $line ]]; do
            line=${line%$'\r'}
            if [[ -n ${line//[[:space:]]/} && $line != \#* ]]; then
                split_tsv "$line"
                id=${fields[${col[id]}]:-}
                si=${col[sha256]}
                if [[ -n ${pinned[$id]+x} && -z ${fields[$si]:-} ]]; then
                    while ((${#fields[@]} < ncols)); do fields+=(""); done
                    fields[si]=${pinned[$id]}
                    line=$(IFS=$'\t'; echo "${fields[*]}")
                fi
            fi
            printf '%s\n' "$line"
        done < <(tail -n +2 -- "$manifest")
    } >"$tmp"
    chmod --reference="$manifest" -- "$tmp" 2>/dev/null || true
    mv -f -- "$tmp" "$manifest"
    echo "wrote ${#pinned[@]} pinned hash(es) to ${manifest#"$root"/}"
fi

echo "summary: $n_ok downloaded, $n_cached cached, $n_pinned pinned, $n_fail failed (exit $worst)"
exit "$worst"
