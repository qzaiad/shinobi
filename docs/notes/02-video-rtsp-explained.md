# Session 2 — Video and RTSP fundamentals, explained

How a camera's picture becomes bytes on the network, and how to inspect every layer with ffprobe,
ffmpeg and a packet capture. Your questions and corrected answers are in
[02-video-fundamentals.md](02-video-fundamentals.md); the lab steps are in `media/session2/README.md`.
All outputs below are real, from the files in `media/session2/` (test clips made with FFmpeg's
`testsrc2` pattern) and from `rtsp.pcap`, the capture of that session.

Commit: `f7b6a57 feat(tools): add streamprobe ffprobe GOP and B-frame health check`.

---

## 1. The layers, from sensor to network

```
 camera sensor: 25 pictures per second, each 1280×720 pixels
        │ raw: 1280 × 720 × 1.5 bytes (yuv420p) ≈ 1.4 MB per frame ≈ 35 MB/s
        ▼
 ┌─────────────────┐
 │ ENCODER (codec) │  H.264: compresses ~100× by storing only what changed
 └────────┬────────┘
          ▼
 NAL units  (H.264's own packets: SPS, PPS, slices …)              ← the CODEC layer
          │
          ├─────────────────────────────┐
          ▼                             ▼
 ┌──────────────────┐         ┌──────────────────────┐
 │ CONTAINER (file) │         │ TRANSPORT (network)  │
 │ MP4, WebM, MKV   │         │ RTP packets, carried │
 │ + index, timing  │         │ over RTSP/TCP or UDP │
 └──────────────────┘         └──────────────────────┘
   cctv.mp4 on disk              rtsp://…:8554/cam1
```

**Container vs codec.** The codec is *how the picture is compressed* (H.264, VP8, H.265). The
container is *the file format that holds it*, plus timing, an index for seeking, and maybe audio
(MP4, WebM, MKV, MPEG-TS). Same codec, different containers: H.264 can sit in MP4, MKV, MPEG-TS or RTP.
Our footage mixes both kinds (see Session 3): H.264 in MP4, and VP8 in WebM.

---

## 2. Frame types: I, P, B

H.264 doesn't send every picture in full. It sends one full picture, then only differences:

| Type | Contains | Depends on | Size |
|---|---|---|---|
| **I** (intra) | a complete picture | nothing | large |
| **P** (predicted) | changes vs an **earlier** frame | the past | small |
| **B** (bi-directional) | changes vs an earlier **and a later** frame | past and future | smallest |

An **IDR** is a special I-frame: no frame after it may refer to anything before it. A decoder can
start from scratch only at an IDR.

A **GOP** (group of pictures) is an IDR plus everything until the next IDR.

### Two test clips, side by side

```
cctv.mp4      -g 50 -keyint_min 50 -sc_threshold 0 -bf 0 -profile:v main
broadcast.mp4 -g 250 -bf 3                       (x264 default profile: High)
```

Frame types in display order (real ffprobe output, first frames):

```
cctv.mp4:      IPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPPIPPPP…   I every 50 frames
broadcast.mp4: IBPBPPBBBPBPBBPPBPBPBBBPBBBPBPBPPBPBBBPBPBPBPBBBPBPBP…   I every 250 frames
```

```
 cctv.mp4, 25 fps, GOP 50 = 2 s
 t:  0 s                          2 s                          4 s
     I P P P P … P P P P P P P P  I P P P P … P P P P P P P P  I
     └────────── 50 frames ──────┘└────────── 50 frames ──────┘

 broadcast.mp4, GOP 250 = 10 s
     I B P B P P B B B P … (250 frames) … I
```

streamprobe (the tool built in this session) on both:

```
$ uv run python -m tools.streamprobe media/session2/cctv.mp4 --seconds 20
stream:     h264 Main 1280x720 yuv420p @ 25 fps
keyframes:  10
GOP frames: min 50 / max 50 / mean 50
GOP secs:   min 2 / max 2 / mean 2
fixed GOP:  yes
B-frames:   no

$ uv run python -m tools.streamprobe media/session2/broadcast.mp4 --seconds 20
stream:     h264 High 1280x720 yuv420p @ 25 fps
keyframes:  2
GOP frames: min 250 / max 250 / mean 250
GOP secs:   min 10 / max 10 / mean 10
fixed GOP:  yes
B-frames:   yes
WARNING:    B-frames present (CCTV streams should use -bf 0)
```

**Cost of an I-frame.** In `cctv.mp4` the I-packets average 27 378 bytes and the P-packets 13 415
bytes, a ratio of 2.0. A test pattern moves everywhere, so P-frames are unusually large; in a real
static CCTV scene the ratio is often 10× or more. That burst at every keyframe is why rate control
needs a buffer (Session 5, VBV).

---

## 3. Timestamps: PTS and DTS

Each frame has two timestamps:
- **PTS** (presentation time stamp): when to *show* it.
- **DTS** (decoding time stamp): when to *decode* it.

Without B-frames they're equal. With B-frames, the encoder must send a later frame early, because
the B-frame needs it as a reference:

```
 display order (PTS):   I0   B1   P2   B3   P4
 decode order  (DTS):   I0   P2   B1   P4   B3     ← P2 must be decoded before B1 can be
```

Real packets of `broadcast.mp4` (decode order; `K__` = keyframe):

```
pts_time   dts_time   size   flags
0.000000  -0.080000   22887  K__     I     ← DTS negative: decoding starts "before 0" so the
0.080000  -0.040000   14274  ___     P        first frame can still be shown at PTS 0
0.040000   0.000000    8740  ___     B     ← PTS goes back: shown before the previous packet
0.160000   0.040000   14021  ___     P
0.120000   0.080000    8597  ___     B
```

`cctv.mp4`: `pts == dts` on every packet.

**Why CCTV avoids B-frames:**

```
 1. latency      the decoder must wait for the future frame before showing a B-frame
 2. recording    cutting at an arbitrary packet can leave B-frames without their reference
 3. robustness   one lost reference damages frames before AND after it
 4. timestamps   non-monotonic PTS confuses simple muxers at loop seams (Session 4)
```

---

## 4. Profiles and levels

A **profile** is a feature set the decoder must support; a **level** caps resolution, frame rate and
bitrate:

| Profile | Adds | Typical use |
|---|---|---|
| Baseline | I/P only, no CABAC | old phones, video calls |
| **Main** | B-frames, CABAC entropy coding | **IP cameras**, universal decoder support |
| High | 8×8 transform, more tools | broadcast, Blu-ray, x264's default |

We use Main with B-frames switched off. It's the profile every NVR and browser path accepts.

---

## 5. NAL units: H.264's own packets

The encoder output is a sequence of **NAL units** (Network Abstraction Layer). Each starts with a
1-byte header whose low 5 bits are its type:

| nal_unit_type | Name | What it carries |
|---|---|---|
| 7 | SPS | Sequence Parameter Set: profile, level, resolution, reorder depth |
| 8 | PPS | Picture Parameter Set: entropy coding mode, slice settings |
| 6 | SEI | extra info (x264 writes its settings string here) |
| 5 | IDR slice | (part of) a keyframe picture |
| 1 | non-IDR slice | (part of) a P or B picture |

**A decoder can't decode anything without SPS + PPS.** They tell it the picture size and coding tools.

Real `trace_headers` output for `cctv.mp4`:

```
Extradata                                       ← from the MP4's avcC box, not the packets
  Sequence Parameter Set   nal_unit_type = 7
    profile_idc = 77                             → Main profile
    level_idc   = 31                             → Level 3.1
    pic_width_in_mbs_minus1 = 79                 → 80 × 16 = 1280 px
    pic_height_in_map_units_minus1 = 44          → 45 × 16 = 720 px
    max_num_reorder_frames = 0                   → no reordering = no B-frames
  Picture Parameter Set    nal_unit_type = 8
Packet: 21880 bytes, key frame, pts 0, dts 0
  SEI                      nal_unit_type = 6
  Slice Header             nal_unit_type = 5   IDR
Packet: 13541 bytes, pts 512, dts 512
  Slice Header             nal_unit_type = 1   non-IDR, slice_type = 5 (all P)
```

Sizes are in **macroblocks** of 16×16 pixels, so 1280 = 80 × 16.

### Two ways to frame NAL units: AVCC (MP4) vs Annex B (raw stream)

```
 MP4 / AVCC                                  Annex B (.h264 file, MPEG-TS)
 ┌─────────────────────┐                     00 00 00 01 [SPS]
 │ avcC box: SPS, PPS  │ ← stored once       00 00 00 01 [PPS]
 └─────────────────────┘                     00 00 00 01 [SEI]
 sample: [len 4B][NAL][len 4B][NAL]          00 00 00 01 [IDR slice]
         length prefix per NAL               start code before every NAL, SPS/PPS in-band
```

Real bytes after `ffmpeg -c copy -bsf:v h264_mp4toannexb -f h264 cctv.h264`:

```
00000000: 0000 0001 0605 ffff 6bdc …  ........k.          ← 00 00 00 01 start code, 06 = SEI
00000010: … 7832 3634 202d 20 …       x264 -               ← x264's settings string
```

The H.264 data is the same; only the framing differs. `-c copy` keeps the compressed data but may
change the framing to fit the output container.

---

## 6. Why recordings cut at keyframes

You asked FFmpeg for 3 s segments with `-c copy -f segment -segment_time 3`. Real durations:

```
seg_00.mp4 4.0 s   seg_01.mp4 2.0 s   seg_02.mp4 4.0 s   seg_03.mp4 2.0 s …
```

With stream copy a segment can only start at an IDR, and IDRs come every 2 s:

```
 keyframes:  I─────────I─────────I─────────I─────────I─────────I─────────I
 time:       0         2         4         6         8        10        12
 asked cut:                  3 ✗       6 ✓        9 ✗           12 ✓
 real cut:   │                   │         │                   │         │
             └──── seg_00 4 s ───┘seg_01 2s└──── seg_02 4 s ───┘seg_03 2s┘
```

Every cut lands on the first keyframe at or after the requested time. Shinobi records the same way
(Session 10), so a fixed, short GOP gives predictable segment lengths.

---

## 7. RTSP and RTP: control vs media

```
 RTSP (Real Time Streaming Protocol)          RTP (Real-time Transport Protocol)
   text, HTTP-like, port 8554/554               binary, carries the actual video
   "what's there, set it up, start, stop"       one NAL unit (or a fragment) per packet
   = the remote control                         = the tape
```

### The reader's handshake (ffprobe → MediaMTX), decoded from `rtsp.pcap`

```
 client (ffprobe)                                          MediaMTX :8554
   │── OPTIONS rtsp://127.0.0.1:8554/cam1 ────────────────────►│
   │◄─ 200 OK  Public: DESCRIBE, ANNOUNCE, SETUP, PLAY, RECORD …│  "what can you do?"
   │── DESCRIBE …/cam1   Accept: application/sdp ─────────────►│
   │◄─ 200 OK  Content-Type: application/sdp  [SDP body] ──────│  "what's in this stream?"
   │── SETUP …/cam1/trackID=0                                   │
   │     Transport: RTP/AVP/TCP;unicast;interleaved=0-1 ───────►│  "send track 0 inside this
   │◄─ 200 OK  Session: f3d3f7f2…;timeout=55 ───────────────────│   TCP connection, ch 0/1"
   │── PLAY …/cam1/   Session: f3d3f7f2… ──────────────────────►│
   │◄─ 200 OK ──────────────────────────────────────────────────│
   │◄═ $ 0 … RTP … $ 0 … RTP … (forever) ═══════════════════════│  media starts
```

The **publisher** (ffmpeg pushing the file) uses the mirror image. It sends `ANNOUNCE` (*it* provides
the SDP) and `RECORD` instead of `DESCRIBE` and `PLAY`:

```
OPTIONS  → ANNOUNCE (SDP) → SETUP (Transport: …;interleaved=0-1;mode=record) → RECORD → … → TEARDOWN
```

`timeout=55` means the client must send something (RTCP or a keep-alive request) within 55 s, or
the server drops the session.

### The SDP, line by line (real, from MediaMTX for `cctv.mp4`)

```
v=0                                     SDP version
o=- 0 0 IN IP4 127.0.0.1                origin (session id/version, unused here)
s=No Name                               session name
c=IN IP4 0.0.0.0                        connection address (not used with TCP interleaving)
t=0 0                                   live: no start/stop time
m=video 0 RTP/AVP 96                    one VIDEO track, port 0 = "negotiated in SETUP",
                                        RTP payload type 96 (dynamic)
a=control:trackID=0                     URL suffix for SETUP of this track
a=rtpmap:96 H264/90000                  payload 96 = H.264, RTP clock = 90 000 ticks/s
a=fmtp:96 packetization-mode=1;         FU-A fragmentation / STAP-A aggregation allowed
  profile-level-id=4D401F;              4D = Main (77), 40 = constraint flags, 1F = level 3.1
  sprop-parameter-sets=Z01AH9oB…,aO8PyA==   base64 SPS , PPS  → decoder can start without
                                                                 waiting for in-band SPS/PPS
```

`broadcast.mp4`'s SDP said `profile-level-id=64001F`: 0x64 = 100 = **High** profile.

### RTP over TCP: interleaved framing

On a TCP connection the RTSP text and the binary RTP share one byte stream. Each RTP packet is
prefixed with a 4-byte header:

```
 ┌────┬─────────┬─────────────┬──────────────────────────────────────────┐
 │ $  │ channel │ length (2B) │ RTP packet                               │
 │0x24│  0 = RTP│  e.g. 1472  │ ┌──────────── 12-byte RTP header ──────┐ │
 │    │  1 = RTCP│            │ │V P X CC│M PT│ seq │timestamp │ SSRC  │ │
 └────┴─────────┴─────────────┘ └────────┴────┴─────┴──────────┴───────┘ │
                                 then payload: H.264 NAL or FU-A fragment │
```

### FU-A: one frame, many RTP packets

An IDR frame of 20+ KB doesn't fit in one packet, so H.264-over-RTP splits it into **FU-A**
fragments (fragmentation unit, NAL type 28). Real first packets the publisher sent:

```
 seq   RTP timestamp  M  NAL  FU (start, end, type)   bytes
 472   3240385491     0   6   –                        635   SEI, fits in one packet
 473   3240385491     0  28   (1, 0, 5)  ← S=1: start 1472   IDR split into fragments
 474   3240385491     0  28   (0, 0, 5)              1472
 …                                                          (all with the SAME timestamp)
```

And the reader's stream (MediaMTX → ffprobe), with the frame ending:

```
 2282  3241022691     0  28   (0, 0, 1)              1452
 2283  3241022691     1  28   (0, 1, 1)  ← E=1: end,  137    M (marker) = 1: last packet
 2284  3241026291     0  28   (1, 0, 1)  ← next frame 1452   timestamp +3600
```

- **Same RTP timestamp** = same picture. The receiver gathers fragments until it sees E=1, then
  reassembles the NAL unit.
- **Marker bit M=1** = last packet of this picture, so the picture is complete and can be decoded.
- **Lost fragment** (over UDP): the whole NAL can't be rebuilt and is dropped. The decoder hides the
  hole, and every following P-frame inherits the damage until the next IDR.

### RTP timestamps: 90 kHz clock

```
 1280 frames from cctv.mp4: every timestamp delta = 3600
 90 000 ticks/s ÷ 25 frames/s = 3600 ticks per frame ✓
```

For `broadcast.mp4` the deltas are mixed: **-3600** (244×), **+10800** (203×), +7200, -7200. RTP
timestamps follow *display* order, but packets travel in *decode* order, so with B-frames the
timestamps jump back and forth. This is the PTS/DTS reordering from section 3, now visible on the wire.

The packet sizes also explain a later MediaMTX log line. The publisher's RTP packets are 1472 bytes
(12 + 1460 payload), and MediaMTX re-sends them as 1452 bytes (12 + 1440). That is the "RTP packets
are too big (1460 > 1440), remuxing" message seen in Sessions 4 and 5.

### TCP vs UDP transport

```
                 UDP (RTP on separate ports)          TCP (interleaved, our choice)
 setup           extra ports per track, NAT/firewall   one connection, port 8554 only
 packet loss     visible as smear until next IDR       none (TCP retransmits)
 delay on loss   none (late packet just missing)       grows (head-of-line blocking)
 typical use     LAN with multicast                    NVRs over routed networks, Docker
```

We use TCP everywhere (`-rtsp_transport tcp`). The simulation should measure our pipeline, not random loss.
Packet loss gets injected deliberately in Session 7.

---

## 8. Startup latency: why a new viewer waits

```
 viewer connects here
        ▼
 … P P P P P P [I] P P P …     cctv.mp4: next I within ≤ 2 s (average 1 s)
 … P P P P P P P P P … (10 s) … [I]     broadcast.mp4: up to 10 s of black
```

A decoder that joins mid-GOP has P-frames whose reference it never saw, so it discards them and
waits for the next IDR. The worst-case wait is one GOP.

---

## 9. The tool: `tools/streamprobe`

```
 python -m tools.streamprobe <file|rtsp://…> [--seconds N] [--json]
        │
        ▼
 probe.py: build_command → ffprobe -select_streams v:0
             -show_entries stream=codec_name,profile,width,height,pix_fmt,r_frame_rate
                           :frame=pict_type,pts_time,key_frame
             -read_intervals %+N   (only the first N seconds)
             -rtsp_transport tcp   (only for rtsp:// sources)
             -of json
        │ JSON
        ▼
 parse_output → StreamInfo + [Frame(pict_type, key_frame, pts_time), …]
        │
        ▼
 gop.py: compute_gop_stats (PURE: no I/O, unit-tested)
   keyframe indices → GOP lengths in frames (only COMPLETE GOPs between two keyframes)
   keyframe PTS     → GOP lengths in seconds (dropped if any PTS is missing)
   fixed_gop  = all GOP lengths equal
   has_b_frames = any frame with pict_type B
        │
        ▼
 __main__.py: summary (or --json) + warnings
```

Design decisions:
- **Only complete GOPs count.** Joining an RTSP stream mid-GOP gives a partial first GOP, which
  would be wrongly short.
- **Missing PTS → seconds = n/a**, frame counts still valid. A raw `.h264` file has no timestamps, and
  a wrong number is worse than "unknown".
- **`-read_intervals`, not kill.** ffprobe stops itself after N seconds and flushes its output.
  Killing it would lose buffered output (a gotcha found in Session 4).

Tests: `tests/streamprobe/test_gop.py` (pure logic: empty input, no keyframes, fixed, irregular,
partial edges, B-frames, missing PTS) and `test_probe_integration.py` (generates a real clip with
ffmpeg and probes it, marked `integration`).

---

## 10. Verify it yourself

```bash
cd media/session2
ffprobe -v error -select_streams v:0 -show_entries frame=pict_type -of csv=p=0 broadcast.mp4 | tr -d '\n' | head -c 80; echo
ffprobe -v error -select_streams v:0 -show_entries packet=pts_time,dts_time,size,flags -of csv=p=0 broadcast.mp4 | head
ffmpeg -i cctv.mp4 -c copy -bsf:v trace_headers -frames:v 2 -f null - 2>&1 | grep -E "nal_unit_type|profile_idc|width_in_mbs"
cd ../.. && uv run python -m tools.streamprobe rtsp://localhost:8554/cam-gate     # a live camera
uv run pytest tests/streamprobe -v
# Wireshark: open media/session2/rtsp.pcap, filter "rtsp", then "rtp" and look at the FU-A headers
```

## Glossary

| Term | Meaning |
|---|---|
| Codec / container | Compression method (H.264) / file format that holds it (MP4). |
| I / P / B frame | Full picture / change vs past / change vs past and future. |
| IDR | Keyframe after which nothing refers back; decoding can start here. |
| GOP | IDR + frames until the next IDR. |
| PTS / DTS | When to show / when to decode a frame. |
| NAL unit | H.264's internal packet; type 7 SPS, 8 PPS, 5 IDR slice, 1 non-IDR slice. |
| SPS / PPS | Parameter sets the decoder needs before any picture. |
| AVCC / Annex B | Length-prefixed NAL framing (MP4) / start-code framing (raw, TS). |
| Macroblock | 16×16 pixel coding unit; SPS sizes are in macroblocks. |
| RTSP / RTP / RTCP | Control protocol / media packets / control reports (timing, loss). |
| SDP | Session description returned by DESCRIBE (tracks, codec, SPS/PPS). |
| FU-A | RTP fragmentation of one large NAL into several packets. |
| Marker bit | RTP flag on the last packet of a picture. |
| Interleaved | RTP carried inside the RTSP TCP connection, framed by `$ ch len`. |
