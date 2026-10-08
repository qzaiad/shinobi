# Camera Simulator

The camera simulator (docker) takes a prerecorded video file, continuously loops it through FFmpeg, and publishes it as an RTSP stream to MediaMTX.

```
video file
   │
   │ INPUT
   ▼
┌───────────────────┐
│     cam-gate      │
│                   │
│  Debian + FFmpeg  │
│   publish.sh      │
└─────────┬─────────┘
          │
          │ RTSP/TCP
          ▼
┌───────────────────┐
│     MediaMTX      │
│  mediamtx:8554    │
└─────────┬─────────┘
          │
          ▼
     NVR / viewer /
     other clients
```

This script is test/simulation camera, probably for testing an NVR (Network Video Recorder) or video-processing system without needing physical cameras.

# Dockerfile

```
FROM debian:bookworm-slim   # Starts with a minimal Debian 12 environment

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \  # Installs FFmpeg
    && rm -rf /var/lib/apt/lists/*  # The cleanup keeps the resulting Docker image smaller.

COPY --chmod=0755 publish.sh /usr/local/bin/publish.sh  # copy from this folder into docker -> implements the camera logic

# Configure with CAM_NAME, INPUT, RTSP_URL, MODE (copy|encode), FPS, BITRATE.
ENTRYPOINT ["/usr/local/bin/publish.sh"]  # every time the container starts, publish.sh runs

CONTAINER ID   IMAGE                        COMMAND                  CREATED       STATUS       PORTS                      NAMES
baf71134f932   bluenviron/mediamtx:1.21.1   "/mediamtx"              6 hours ago   Up 6 hours   127.0.0.1:8554->8554/tcp   shinobi-mediamtx-1
6060d38d9ef0   shinobi-cam-gate             "/usr/local/bin/publ…"   6 hours ago   Up 6 hours                              shinobi-cam-gate-1

shinobi$ ps aux | grep docker
root        3019  0.0  0.3 5195312 98792 ?       Ssl  Oct03   1:42 /usr/bin/dockerd -H fd:// --containerd=/run/containerd/containerd.sock
root     2951618  0.0  0.0   1064   728 ?        Ss   Oct07   0:00 /sbin/docker-init -- /usr/local/bin/publish.sh
root     2952161  0.0  0.0 1747168 5588 ?        Sl   Oct07   0:06 /usr/bin/docker-proxy -proto tcp -host-ip 127.0.0.1 -host-port 8554 -container-ip 172.18.0.2 -container-port 8554 -use-listen-fd

```

## Explanation

- FFmpeg can:
  - receive an RTSP camera stream
  - copy the existing video without re-encoding
  - decode and re-encode video
  - change FPS/bitrate
  - publish a stream to another RTSP endpoint
  - handle audio/video streams

MODE=copy
FFmpeg doesn't re-encode the video. It essentially forwards the compressed video stream.
Advantages:
- very low CPU usage
- little additional latency
- preserves the camera's existing encoding

```
CAM_NAME = front-door

INPUT = rtsp://camera/... 
             │
             ▼
         FFmpeg
             │
       ┌─────┴─────┐
       │           │
     copy        encode
       │           │
       └─────┬─────┘
             ▼
RTSP_URL = rtsp://some-server/...
```

## The two modes are particularly important:
### MODE=copy
FFmpeg doesn't re-encode the video. It essentially forwards the compressed video stream.
Advantages:
- very low CPU usage
- little additional latency
- preserves the camera's existing encoding

### MODE=encode
FFmpeg decodes and re-encodes the video, presumably using the configured:
```
FPS
BITRATE
```

### Advantages:
- you can reduce bitrate
- you can reduce FPS
- you can convert an incompatible camera stream
- potentially makes downstream streaming easier
The tradeoff is substantially higher CPU usage.

# publish.sh

The comment at the top confirms it:
```
Simulated IP camera: loop a clip into an RTSP server forever.
```

So this appears to be a test/simulation camera, probably for testing an NVR or video-processing system without needing physical cameras.

## What happens when it starts

These variables configure it:
```
CAM_NAME
INPUT
RTSP_URL
MODE
FPS
BITRATE
```

For example, you might have:
```
CAM_NAME=frontdoor
INPUT=/media/frontdoor.mp4
MODE=encode
FPS=25
BITRATE=2M
```

If RTSP_URL isn't explicitly supplied, it automatically uses:
```
rtsp://mediamtx:8554/frontdoor
```

So the resulting stream would be:
```
rtsp://mediamtx:8554/frontdoor
```

## The important FFmpeg command
The script builds this:
```
ffmpeg \
  -re \
  -stream_loop -1 \
  -i "$INPUT" \
  -map 0:v:0 \
  -an \
  ...
  -f rtsp \
  -rtsp_transport tcp \
  "$RTSP_URL"
```

### -stream_loop -1
This means: **loop the video forever.**

So if camera.mp4 is 30 seconds long:
```
0s ───────── 30s
       ↓
0s ───────── 30s
       ↓
0s ───────── 30s
       ↓
       ...
```

On EOF, the demuxer seeks back to 0 and adds the accumulated duration to the timestamps, so PTS/DTS keep increasing. That's the theory. In practice, the seam breaks when:

- **Audio and video durations differ.** The offset is computed from the longer stream, so video timestamps jump forward, which shows up as a visible stall. Fix: `-an`, since port cameras have no useful audio anyway.
- **B-frames are present.** Reordering near EOF/BOF gives "Non-monotonous DTS" warnings, and the muxer clamps or drops packets.
- **The MP4 has an edit list (`elst`)** with a non-zero start, which gives an offset error at every wrap.
- **The clip doesn't start on an IDR.** With `-c copy`, readers get garbage until the next keyframe after each loop.
- **The footage is VFR** (typical for phone or stock footage). Per-frame durations are irregular, and `-re` pacing becomes jittery.

### -re

This is important for a simulated camera.

It tells FFmpeg to read the file at approximately its normal playback rate rather than processing it as fast as possible.

Without it, FFmpeg might send a 30-minute recording to the RTSP server in a few seconds.

### -map 0:v:0
Only the first video stream is selected.

### -an
No audio.
So this simulated camera publishes **video only**.
Background:

### MODE=copy
This is the interesting part:
```
```
-c:v copy

FFmpeg doesn't decode/re-encode the video.

For example:
```
MP4/H.264
   │
   │ copy
   ▼
RTSP/H.264
```

This is very efficient because there's almost no CPU-intensive video processing.
If your source is already suitable for your RTSP consumers, this is generally preferable.

### MODE=encode

Here it actually re-encodes:
```
-c:v libx264
-preset veryfast
-tune zerolatency
-profile:v main
-pix_fmt yuv420p
-r "$FPS"
```

So:
```
video file
    │
    ▼
 decode
    │
    ▼
 H.264 encoder
    │
    ▼
 RTSP stream
```

It also sets:
```
-b:v "$BITRATE"
```

So with:
```
BITRATE=2M
```

the target bitrate is approximately **2 Mbps.**

### The GOP settings

This is another useful detail:
```
GOP=$((FPS * 2))
```

If:
```
FPS=25
```

then:
```
GOP=50
```

That's intended to give you a 2-second keyframe interval:
```
I .... P P P ... I .... P P P ... I
<---- 2 sec ---->
```

And:
```
-keyint_min "$GOP"
-sc_threshold 0
-bf 0
```

make the keyframe structure more predictable and disable B-frames.
That's quite reasonable for a low-latency/test RTSP stream.

## Automatic recovery

This part is also important:
```
while true; do
    ...
    "${cmd[@]}" &
    ...
    wait "$ffmpeg_pid"
    ...
    sleep 2
done
```

If FFmpeg crashes or exits:
```
FFmpeg exits
     │
     ▼
wait returns
     │
     ▼
log error
     │
     ▼
wait 2 seconds
     │
     ▼
start FFmpeg again
```

So the container is designed to **keep the simulated camera alive indefinitely**.

## What happens when Docker stops it?

This is nicely handled too:
```
trap shutdown TERM INT
```

Docker normally sends SIGTERM when stopping a container.
The script catches it and does:
```
kill -TERM "$ffmpeg_pid"
```

then waits for FFmpeg to terminate.
So it doesn't simply abandon the FFmpeg process.
One thing that's easy to misunderstand
The name cam-gate might make it sound like this:
REAL IP CAMERA
      ↓
 cam-gate
      ↓
 MediaMTX

But this particular script does not do that.
It's actually:
RECORDED VIDEO FILE
      ↓
   cam-gate
      ↓
   MediaMTX

The Dockerfile/comment explicitly calls it a simulated IP camera.
If you have several of these containers, you could effectively create:
camera-1.mp4 ──→ cam-gate ──→ rtsp://mediamtx:8554/camera1
camera-2.mp4 ──→ cam-gate ──→ rtsp://mediamtx:8554/camera2
camera-3.mp4 ──→ cam-gate ──→ rtsp://mediamtx:8554/camera3

This is very useful for testing an NVR, computer-vision application, recording system, or RTSP infrastructure.
One thing I'd check next
If you show me your docker-compose.yml, I can map the whole setup for you — particularly:
video file → cam-gate → MediaMTX → NVR/client
and tell you which RTSP URL you can actually open to view the simulated camera.
My Trust: 10/10
Check the uncertain points: the exact downstream consumers and available RTSP URLs depend on your Compose configuration and MediaMTX configuration, which aren't shown yet.
Resources: FFmpeg (ffmpeg), MediaMTX, Docker Compose, and the publish.sh you provided.