# Simulated camera profile — v1

Every clip a simulated camera loops must look like the output of a real IP camera. Raw footage
(`media/raw/`) almost never does (see [notes/03-port-footage.md](notes/03-port-footage.md)), so it is
re-encoded once, offline, into this profile. The publisher can then use `MODE=copy` and the stream
stays stable across loop boundaries.

```bash
sim/scripts/normalize.sh media/raw/gate_santos_port.mp4 media/gate_santos_port.mp4
sim/scripts/probe_clip.sh media/gate_santos_port.mp4     # exit 0 = all checks pass
```

## The profile

| # | Property | Required value | Why |
|---|---|---|---|
| 1 | Codec / profile | H.264, **Main** | What IP cameras and Shinobi's FFmpeg handle everywhere; no High-profile 8×8 transform. |
| 2 | Pixel format | `yuv420p` | 4:2:0 is the only chroma format Main supports. |
| 3 | Dimensions | width and height **even**, width **≤ 1280** | 4:2:0 halves chroma in both axes, so odd sizes can't be encoded. 720p keeps CPU low with several cameras. |
| 4 | Frame rate | `avg_frame_rate == r_frame_rate == 25/1` | Constant frame rate. If the two differ the clip is VFR, and `-re` pacing becomes jittery. |
| 5 | B-frames | `has_b_frames == 0` | Decode order equals presentation order, so DTS stays monotonic across the `-stream_loop` seam and latency stays low. |
| 6 | Audio | none | Port cameras here carry no audio; mismatched A/V durations cause a stall at every loop. |
| 7 | Start | first video packet `pts_time == 0` | A non-zero start (an edit list or offset) becomes a timestamp jump at every loop. |
| 8 | First picture | first VCL NAL is an IDR (`nal_unit_type == 5`) | Readers can decode from the first packet; with `-c copy` each loop restarts on a clean IDR. |
| 9 | GOP | keyframe on frames 0, 50, 100, … and nowhere else (fixed 2 s) | Matches the camera convention and gives Shinobi predictable segment cut points. |

## How `normalize.sh` meets it

```
ffmpeg -i RAW_IN -map 0:v:0 -an -sn -dn \
  -vf "fps=25,scale=w='trunc(min(1280\,iw)/2)*2':h=-2" \
  -c:v libx264 -preset slow -crf 23 -profile:v main -pix_fmt yuv420p \
  -g 50 -keyint_min 50 -sc_threshold 0 -bf 0 \
  -movflags +faststart OUT
```

- `fps=25` resamples to CFR, dropping or duplicating frames for 59.94 and 23.976 sources (items 4 and 9).
- The `scale` expression caps the width at 1280 without upscaling and keeps both dimensions even (item 3).
- `-g 50 -keyint_min 50 -sc_threshold 0` gives a keyframe exactly every 50 frames and no scene-cut keyframes (item 9).
- `-bf 0` turns off B-frames (item 5), and `-an` drops audio (item 6).
- `-crf 23 -preset slow` is offline quality-targeted encoding. The live bitrate cap only matters
  in `MODE=encode` of the publisher.
- `+faststart` moves `moov` to the front of the file.

Safety rules: the script refuses to write over `RAW_IN`, including through a hard link or symlink. It refuses an
input inside `media/` that is not under `media/raw/`, because derived clips must not be re-derived from each other.
It writes to `OUT.part` and renames that file only on success, so an interrupted run never leaves a truncated MP4
(no `moov` atom) at `OUT`.

## How `probe_clip.sh` checks it

| Check | Source |
|---|---|
| `codec`, `profile`, `pix_fmt`, `even_dimensions`, `max_width`, `frame_rate`, `no_b_frames` | `ffprobe -select_streams v:0 -show_entries stream=…` |
| `no_audio` | `ffprobe -select_streams a` lists no streams |
| `first_pts_zero` | `pts_time` of the first video packet (`-read_intervals %+#1`) |
| `first_nal_idr` | `ffmpeg -c copy -bsf:v trace_headers -frames:v 1`: first `nal_unit_type` in 1..5 of the first packet must be 5 |
| `fixed_gop` | `ffprobe -show_entries packet=flags`: `K` exactly at packet indices that are multiples of 50 |

"First NAL" means the first **VCL** NAL. In MP4 the SPS (7) and PPS (8) are stored in the `avcC`
extradata, not in-band, and x264 puts an SEI (6, its encoder-settings string) in front of the first
slice. A literal "first NAL == 5" check would therefore fail every valid file.

`fixed_gop` reads packets in decode order. That is only the same as frame order because item 5 forbids
B-frames, so a B-frame failure also makes the GOP result meaningless.

Exit codes: `0` all checks pass, `1` at least one FAIL, `2` usage error or unreadable file.

`probe_clip.sh` checks a **file**. For a **live RTSP stream** use
`uv run python -m tools.streamprobe <url>`. It checks GOP regularity and B-frames over a time window.
