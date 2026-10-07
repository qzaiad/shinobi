# Session 3 — Port footage

Source of truth: [`media/manifest.tsv`](../../media/manifest.tsv) (URL + sha256),
attribution in [`media/SOURCES.md`](../../media/SOURCES.md). Restore with
`./scripts/fetch_media.sh` (`--probe` prints the summary below).

## Selection criteria

1. **License** on the allowlist (CC0, CC-BY, CC-BY-SA, PD, Pexels, Pixabay); no NC/ND, no live webcams.
2. **Fixed camera.** Shinobi motion detection diffs consecutive frames; a pan, drone drift or cut
   turns the whole frame into "motion". Checked by sampling 8 frames evenly across each clip
   (contact sheet) and comparing the background.
3. **Something detectable moves**: trucks, cranes, ships. Landscape 16:9, ≥ 720p.
4. **Stable direct URL** so the sha256 pin stays valid (Pexels `videos.pexels.com/video-files/…`,
   Commons `upload.wikimedia.org/…` originals; not Commons transcodes, which can be regenerated).

## Chosen clips

| id | Role | Why |
|---|---|---|
| `gate-santos-01` | gate | Santos (BR). Fixed camera on a port road; trucks and cars cross the frame in front of STS cranes. Closest thing to a gate lane found; 54 s. |
| `quay-sts-01` | quay | Fixed camera under an STS crane; a container is lowered onto a waiting truck. Clear truck + container + spreader for detection. Only 9 s, so the loop seam is visible. |
| `yard-malta-01` | yard | Malta Freeport, tripod view over container stacks with RTGs moving and STS cranes behind. Longest clip (142 s). |
| `waterway-porpoise-01` | waterway | Fixed shore camera; an LPG carrier crosses the whole frame, then a tug. Real enter → pass → leave sequence; CC0; 160 s. |

## Source stream summary (ffprobe)

Keyframe interval measured from keyframe `pts_time` deltas
(`ffprobe -select_streams v:0 -skip_frame nokey -show_entries frame=pts_time`); every clip has a
perfectly regular GOP.

| id | Container | Video codec | Resolution | fps | B-frames | Keyframe interval | Duration | Bitrate | Audio |
|---|---|---|---|---|---|---|---|---|---|
| `gate-santos-01` | MP4 | H.264 High, yuv420p | 1920×1080 | 59.94 (60000/1001) | yes (has_b_frames=2) | 4.171 s (250 frames) | 54.9 s | 3.7 Mb/s | none |
| `quay-sts-01` | MP4 | H.264 High, yuv420p | 1920×1080 | 25 | yes (has_b_frames=2) | 3.040 s (76 frames) | 9.4 s | 4.8 Mb/s | AAC |
| `yard-malta-01` | WebM | VP8, yuv420p | 1920×1080 | 25 | no | 5.120 s (128 frames) | 141.8 s | 5.6 Mb/s | Vorbis |
| `waterway-porpoise-01` | WebM | VP8, yuv420p | 1280×720 | 23.976 (24000/1001) | no | 5.339 s (128 frames) | 160.0 s | 5.0 Mb/s | Opus |

**Consequences for Session 4:** no clip matches the camera convention (H.264 Main, `-bf 0`, GOP 2 s),
so `-c copy` is not an option. Every publisher must re-encode: `-c:v libx264 -profile:v main -bf 0`,
a common output rate (`-r 25`; drops/duplicates frames for the 59.94 and 23.976 sources),
`-g 50 -keyint_min 50 -sc_threshold 0`, and `-an` (three clips carry audio; real port cameras
here don't). The 720p waterway clip needs a scale decision (keep 720p, or upscale to match the others).

## Rejected candidates

| Candidate | Source | Reason |
|---|---|---|
| 5 Shutterstock previews (Dunedin, Bangkok, Vladivostok, Antwerp, Barcelona) | Shutterstock | **Watermark**; comp license forbids use beyond evaluation; also drone/pan shots at 596×336 |
| Wando Welch Terminal clips (USDA, 0044–0117) | Commons, PD | **Drone/boat**, slow drift; the near-static 0045 shows a berthed ship with no activity |
| MSC Paris berthing at BCT Gdynia | Commons, CC-BY-3.0 | **Timelapse** plus logo intro |
| Malmö kombiterminal 05 | Commons, CC-BY-4.0 | **Timelapse** (day → night); 01–04/06 are usable backups (filmed through a window) |
| Container reach stacker, Port of Rauma | Commons, CC-BY-3.0 | Handheld, multiple cuts |
| Containerkraan Delfzijl | Commons, CC-BY-SA-4.0 | Cuts and zooms |
| Ajax South Harbour Helsinki 01/02, Angela Oulu | Commons, CC0 | **Pan** following the vessel |
| Containership Santa Clara | Commons, CC0 | Cuts, burned-in caption, 640×480 |
| Port Fouad/Port Said water passage | Commons, CC0 | Handheld from a moving boat |
| ContainerShip.webm | Commons, CC0 | Animation |
| Tralee Bay ship | Commons, CC-BY-SA-4.0 | Static but ship is a few pixels — not detectable |
| Güterverkehrszentrum Hof C0222 | Commons, CC-BY-4.0 | Good static yard/truck shot, but the original is 4K, 1.5 GB |
| Pexels 8344321, 8344449, 6618335, 37914269, 10458402, 34109533 | Pexels | **Drone** or pan |
| Pexels 6193732 | Pexels | Handheld from a moving vehicle |
| Pexels 37778419, 37447408, 37669831, 37669830, 37778389, 37778420, 37656182, 37651090, 37499892 | Pexels | Portrait (9:16) |
| Pexels 39130071 | Pexels | Car carriers (Ro-Ro), not containers; cut at start |
| Pexels 31750578 | Pexels | Night, close-up forklift, mostly dark |

## Backups (static, license OK)

- gate: Pexels 35425649 (Melbourne, trucks pass straddle carriers, 4K 16 s); Pexels 13742716 (hovering drone over truck lanes, 4K 17 s)
- quay: Pexels 35562081 / 35562082 (Hamburg STS cranes, 1080p25, 10–13 s)
- yard: Commons *StraddleCarrierLiftsContainerFromDoubleStackTrain* (CC-BY-4.0, 66 s); Pexels 37927911 (Melbourne ASC, 4K 20 s)
- waterway: Commons *Star Pisces 1* (Hong Kong, CC-BY-SA-4.0, 73 s)
