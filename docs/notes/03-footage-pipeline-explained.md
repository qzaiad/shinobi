# Session 3 — Port footage, explained

How real port video gets into the project legally and reproducibly, without ever committing a video
file. Which clips were chosen and why: [03-port-footage.md](03-port-footage.md).

Commit: `0687bd4 feat(sim): add port footage manifest and reproducible download script`.

---

## 1. The problem

```
 We need:                                   But:
 • real port video (trucks, cranes, ships)   • video is large (100 MB per clip) → not in git
 • the SAME bytes on every machine           • live webcams can't legally be restreamed
 • proof we may use it (license, author)     • URLs can silently serve different content later
```

The solution separates **what** we use (committed, small, text) from **the bytes** (downloaded,
gitignored, verified):

```
 committed (git)                              not committed (media/, gitignored)
 ┌─────────────────────────────┐             ┌──────────────────────────────────┐
 │ media/manifest.tsv          │ fetch_media │ gate_santos_port.mp4             │
 │   id, url, sha256, license… │ ──────────► │ quay_sts_truck_loading.mp4       │
 │ media/SOURCES.md            │  download + │ yard_malta_freeport.webm         │
 │   human-readable attribution│  verify     │ waterway_porpoise_bay.webm       │
 └─────────────────────────────┘             └──────────────────────────────────┘
          ▲
          │ checked by tests/test_media_manifest.py (no network)
```

---

## 2. Licensing: what we may use

| License | May we use it? | Why |
|---|---|---|
| CC0 / Public Domain | yes | no conditions |
| CC-BY (2.0/3.0/4.0) | yes | must credit the author → `SOURCES.md` |
| CC-BY-SA | yes | credit + derivatives under the same license |
| Pexels / Pixabay license | yes | free use, attribution appreciated |
| **…-NC** (NonCommercial) | **no** | would restrict the whole project's use |
| **…-ND** (NoDerivatives) | **no** | we re-encode, scale, overlay and loop: that IS a derivative |
| live webcams | **no** | usually no license at all; restreaming is redistribution |

The test enforces this. `test_license_allowed` checks every row against an allowlist, and a regex
flags any `NC`/`ND` token.

---

## 3. Selection: why "fixed camera" matters most

Shinobi's motion detection compares consecutive frames (Session 14). If the camera moves, every
pixel changes:

```
 fixed camera                              panning / drone camera
 frame N    frame N+1   diff               frame N    frame N+1   diff
 ┌──────┐   ┌──────┐   ┌──────┐            ┌──────┐   ┌──────┐   ┌──────┐
 │ ▓▓ ▢ │   │ ▓▓  ▢│   │    ░ │ ← only the │ ▓▓ ▢ │   │▓ ▢   │   │░░░░░░│ ← everything
 │══════│   │══════│   │      │   truck    │══════│   │═════ │   │░░░░░░│   "moves":
 └──────┘   └──────┘   └──────┘   moved    └──────┘   └──────┘   └──────┘   useless
```

So drone shots, pans, timelapses and clips with cuts were rejected. The full list with reasons is in
`03-port-footage.md`. Each candidate was checked by sampling 8 frames evenly and comparing the
background.

---

## 4. `media/manifest.tsv`: one row per clip

TSV (tab-separated values) because URLs and notes contain commas, and a plain `split("\t")` is
enough to parse it in bash and Python.

```
 id                    role      filename                   url               sha256     license   author …
 gate-santos-01        gate      gate_santos_port.mp4       https://videos.…  25be9b67…  Pexels    Fábio Reis de Abreu
 quay-sts-01           quay      quay_sts_truck_loading.mp4 https://videos.…  c706e7df…  Pexels    Thanh Văn
 yard-malta-01         yard      yard_malta_freeport.webm   https://upload.…  52779a2b…  CC-BY-SA-3.0  Frank Vincentz
 waterway-porpoise-01  waterway  waterway_porpoise_bay.webm https://upload.…  67e478e6…  CC0-1.0   Extemporalist
```

The **role** column later names the cameras (`cam-gate`, `cam-quay`, …, Session 5).

### sha256 pinning

```
 download ──► sha256 of the bytes ──► equal to the pinned value?
                                         yes → these are exactly the bytes we tested with
                                         no  → URL now serves something else → refuse
```

A hash is a 64-hex-character fingerprint; changing one bit of the file changes it completely. It
protects against a URL that later serves a re-encoded file, a different video, or an error page saved
as `.mp4`. Commons *transcodes* (re-generated derivatives) were avoided for this reason; only the
uploaded originals have stable bytes.

---

## 5. `scripts/fetch_media.sh`: per-row flow

```
 for each row in manifest.tsv:
   │
   ├─ filename safe? (^[A-Za-z0-9_][A-Za-z0-9._-]*$, no "/", no "..")  no → FAIL (exit 3)
   ├─ sha256 empty and no --pin?                                     yes → FAIL (exit 1)
   │
   ├─ file exists AND its sha256 == pinned? ──yes──► "OK (cached)"   no network at all
   │        │ exists but hash differs → "STALE …, re-downloading"
   │        ▼
   ├─ curl --fail --retry 3 → media/<file>.part        ← never the real name yet
   │        │ failure → delete .part, FAIL (exit 2)
   │        ▼
   ├─ sha256(.part) == pinned? ──no──► delete .part, FAIL (exit 1)
   │        │ yes   (or --pin: remember the hash)
   │        ▼
   └─ mv .part → <file>          ← atomic rename: the file is either complete or absent
 end
 --pin: write remembered hashes into empty sha256 cells (via temp file + mv)
 exit = worst code seen (0 ok, 1 hash, 2 download, 3 usage/manifest/dependency)
```

**Why `.part` + `mv`?** `mv` within one filesystem is a single `rename()` system call, which is
atomic. A crash, a Ctrl-C or a dropped connection mid-download leaves at most a `.part` file, which the
`trap cleanup EXIT` deletes. You never get a truncated `.mp4` that looks valid. (Session 4's
`normalize.sh` uses the same trick after a cut-off encode left a broken MP4 behind.)

**Why a custom `split_tsv`?** The obvious `IFS=$'\t' read -a` has a trap:

```
 row:      id ⇥ role ⇥ ⇥ url          (empty filename column)
 read -a:  [id, role, url]            ✗ tab is "IFS whitespace": consecutive tabs collapse,
                                        every later column shifts left by one
 split_tsv:[id, role, "", url]        ✓ splits on every single tab
```

Real run (this clip had been deleted locally, so it was downloaded again):

```
$ ./scripts/fetch_media.sh --only waterway-porpoise-01 --probe
GET   waterway-porpoise-01  https://upload.wikimedia.org/wikipedia/commons/2/27/22%2C000_cbm_…webm
OK    waterway-porpoise-01  waterway_porpoise_bay.webm
  probe waterway_porpoise_bay.webm: codec_name=vp8 profile=0 width=1280 height=720 has_b_frames=0
        pix_fmt=yuv420p r_frame_rate=24000/1001 duration=160.008000
summary: 1 downloaded, 0 cached, 0 pinned, 0 failed (exit 0)
```

A second run would print `OK … (cached)` without touching the network.

**Interaction with normalized clips (Session 4):** `media/raw/` holds the pristine downloads, and
`normalize.sh` writes the camera-ready versions to `media/<stem>.mp4`. For `gate` and `quay` the
filename is the same as the manifest's, so `fetch_media.sh` sees a hash mismatch, prints `STALE`, and
**downloads the original over the normalized clip**. After running it, rerun `normalize.sh` for those two.

---

## 6. `tests/test_media_manifest.py`: what's enforced without network

```
$ uv run pytest tests/test_media_manifest.py -q
27 passed
```

| Test | Guards against |
|---|---|
| header matches expected columns | renamed/missing column breaking `fetch_media.sh` |
| every row has header width | stray tab or missing cell |
| id / filename / url unique | two rows overwriting the same file |
| sha256 is 64 lowercase hex | typo'd or truncated hash |
| url is https | downgrade / tampering in transit |
| license in allowlist, no NC/ND token | accidentally adding unusable footage |
| roles gate, quay, yard, waterway present | a camera without footage |
| every id attributed in SOURCES.md | missing credit (CC-BY requires it) |

---

## 7. What the probe told us (and why Session 4 had to re-encode)

| Clip | Codec | fps | B-frames | Keyframe interval | Fits the camera convention? |
|---|---|---|---|---|---|
| gate | H.264 **High** | **59.94** | **yes** | **4.17 s** | no |
| quay | H.264 **High** | 25 | **yes** | **3.04 s** | no |
| yard | **VP8** (WebM) | 25 | no | **5.12 s** | no |
| waterway | **VP8** (WebM) | **23.976** | no | **5.34 s** | no |

Convention: H.264 Main, no B-frames, fixed 2 s GOP, 25 fps. Not a single clip fits, so `-c copy`
was impossible as-is. That led to camera profile v1 and `normalize.sh` (Session 4).

## 8. Verify it yourself

```bash
./scripts/fetch_media.sh --probe                 # caution: re-downloads gate/quay over normalized clips
./scripts/fetch_media.sh --only yard-malta-01    # one clip
sha256sum media/raw/*                            # compare with manifest.tsv
uv run pytest tests/test_media_manifest.py -v
```
