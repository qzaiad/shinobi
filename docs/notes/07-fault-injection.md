# Session 7 — Fault injection and stream health checks

How the simulated cameras can now fail on demand like real IP cameras (`scripts/chaos.sh`), and
how a test notices (`tests/test_streams.py`). All outputs below are real, captured from this repo
on 2026-10-09 (MediaMTX 1.21.1, Linux 7.0, Docker 29.8).

```
                    scripts/chaos.sh
        stop/start/crash  freeze/thaw   netem (tc in a helper container)
               │              │              │
               ▼              ▼              ▼
 ┌─ container cam-gate ─────────────────────────────┐            ┌─ MediaMTX :8554 ─┐
 │ publish.sh ─▶ ffmpeg ─▶ socket ─▶ eth0 [qdisc] ──┼── TCP ────▶│ /cam-gate         │◀── RTSP/TCP ── tests/test_streams.py
 └──────────────────────────────────────────────────┘            │ /cam-gate-sub     │      (ffprobe, 6 s per stream)
                                                                 └───────────────────┘
```

---

## 1. How real cameras fail

A port has hundreds of cameras on poles, cranes and gates, connected through switches, fibre,
PoE (Power over Ethernet: one cable carries data and power) and sometimes wireless bridges.
They fail in a few typical ways, and each looks different *on the wire*:

| Real failure | Cause in the field | What the server (MediaMTX, later Shinobi) sees | chaos.sh |
|---|---|---|---|
| Clean shutdown / reboot | Firmware update, scheduled reboot | RTSP `TEARDOWN`, then TCP `FIN`: an immediate, explicit goodbye | `stop` / `start` |
| Encoder crash | Firmware bug, watchdog restarts the encoder | TCP closed by the kernel (`FIN`/`RST`) without `TEARDOWN`, reconnect a few s later | `crash` |
| Hang / power loss | Encoder hangs, PoE switch drops power, cable cut | **Nothing at all.** No FIN, no RST: the bytes just stop. Only a timeout notices | `freeze` / `thaw`, or `netem loss 100%` |
| Flapping | PoE budget exceeded, loose connector, overheating | Path appears and disappears again and again | `flap` |
| Bad link | Wireless bridge, overloaded uplink, duplex mismatch | Packet loss, delay, jitter (delay that varies per packet), reordering | `netem` |

Why it matters for this project: an NVR (network video recorder, here Shinobi) must reconnect after
each of these, and the evaluator must not mistake "camera down" for "nothing happening in the
yard". Sessions 9, 10 and 25 run Shinobi and the evaluator against these faults.

## 2. Close vs silence: the two families of failure

The most important distinction is whether the camera **says goodbye**:

```
 time ─────────────────────────────────────────────────────────────────────────▶

 stop     camera: ─RTP─RTP─RTP─ TEARDOWN FIN ×
 (+crash)  server: ───────────────────────── path gone at once ─▶ readers: 404 Not Found

 freeze   camera: ─RTP─RTP─RTP─ (silence, socket still open) ··········· thaw: write fails, reconnect
          server: ─────────────── waits ── readTimeout (10 s) ─▶ i/o timeout, path gone
          reader:               "connected", gets 0 frames ────────────▶ then 404
```

Captured. **stop** (`chaos.sh stop cam-gate` at 19:32:40), MediaMTX log:

```
19:32:40 INF [RTSP] [session 2434df21] destroyed: torn down by 172.18.0.6:42912   ← RTSP TEARDOWN from ffmpeg
19:32:40 INF [RTSP] [conn 172.18.0.6:42912] closed: EOF                           ← TCP FIN (read returns EOF)
```

`docker stop` sends SIGTERM; `publish.sh` forwards it to ffmpeg, which shuts down politely.

**crash** (`chaos.sh crash cam-gate`: SIGKILL to ffmpeg inside the container):

```
cam-gate-1  | publish.sh: line 88:     9 Killed                  "${cmd[@]}" < /dev/null
cam-gate-1  | 19:33:33Z [cam-gate] command exited with code 137, restarting in 2 s    ← 137 = 128 + 9 (SIGKILL)
mediamtx-1  | 19:33:33 INF [RTSP] [conn 172.18.0.6:59818] closed: EOF                 ← kernel closed the socket: FIN, but
mediamtx-1  | 19:33:33 INF [RTSP] [session 9d2fe94e] destroyed: not in use             ←   no TEARDOWN ("not in use", not "torn down")
mediamtx-1  | 19:33:36 INF [path cam-gate] stream is available and online, 1 track (H264)   ← back after 3 s
```

A killed process can't send RTSP messages, but the kernel still closes its sockets, so the server
learns about it at once. That is the "watchdog restarts the encoder" case: an outage of a few seconds.

**freeze** (`chaos.sh freeze cam-gate` at 19:33:45; `docker pause` stops every process in the
container with the cgroup freezer, but the container's network stack keeps running):

```
19:33:55 INF [RTSP] [conn 172.18.0.6:42096] closed: read tcp 172.18.0.2:8554->172.18.0.6:42096: i/o timeout
19:33:55 INF [RTSP] [session 97dec1f9] destroyed: not in use
19:33:55 INF [RTSP] [session 783c6b74] destroyed: terminated        ← our reader, kicked off the dead path
```

Exactly 10 s of silence (MediaMTX's default `readTimeout`) before the server gives up. During
those 10 s the path still *looks* healthy: a reader can connect (DESCRIBE returns the SDP, the
session description listing the tracks) but receives no frames. The first test run hit exactly that:

```
E       AssertionError: cam-gate: size 0x0                          ← connected, no frames, no size
E       Failed: cam-gate-sub: down: ... DESCRIBE failed: 404 Not Found   ← 10 s later: path gone
```

(The test now checks the frame count first, so this reads `0 frames in 6 s` instead.)

After `thaw`, ffmpeg's next write hits the socket that MediaMTX closed meanwhile, ffmpeg exits with
code 1, and `publish.sh` reconnects:

```
cam-gate-1  | 19:33:56Z [cam-gate] command exited with code 1, restarting in 2 s
mediamtx-1  | 19:33:59 INF [path cam-gate] stream is available and online, 1 track (H264)
```

What breaks without a read timeout: a frozen camera would keep its path "online" forever, and an
NVR would record an endless empty segment instead of raising "camera offline". Shinobi has its own
equivalent watchdog (Session 9).

## 3. netem: shaping a camera's network link

`tc` (traffic control, from iproute2) attaches a **qdisc** (queueing discipline: the kernel's
per-interface packet scheduler) to a network interface. Every outgoing packet passes through it.
The default `noqueue`/`pfifo_fast` just sends. **netem** (network emulator) is a qdisc that holds,
drops, duplicates or reorders packets on purpose:

```
 ffmpeg write() ─▶ TCP socket ─▶ IP ─▶ eth0 egress qdisc ─▶ veth ─▶ docker bridge ─▶ MediaMTX
                   (send buffer,        ┌──────────────┐
                    retransmits)        │ netem        │  loss 10%       : drop 1 in 10 packets
                                        │  delay/jitter│  delay 200ms    : hold each packet 200 ms
                                        │  loss        │  delay 200ms 50ms: hold 150..250 ms, random
                                        └──────────────┘
```

Where it runs. Each container has its own network namespace (its own `eth0`, routes, qdiscs).
The camera image is unprivileged and normally needs no `tc`. So `chaos.sh` starts a throw-away
**helper container** that *joins the camera's network namespace* and has the `NET_ADMIN` capability:

```
 docker run --rm --network container:<cam-id> --cap-add NET_ADMIN --entrypoint tc <camera image> \
            qdisc replace dev eth0 root netem loss 10%

 ┌─ netns of cam-gate ──────────────────────┐
 │  cam-gate container (ffmpeg, no caps)    │
 │  helper container (tc, NET_ADMIN, --rm)  │──▶ both see the same eth0 and its qdisc
 └──────────────────────────────────────────┘
```

No sudo on the host, nothing privileged left running, and the shaping disappears when the camera
container is recreated (the namespace dies with it). `iproute2` was added to the camera image so the
helper can reuse it. netem shapes **egress** only, here the camera → MediaMTX (publish) direction,
which is the direction that carries the video.

```
$ scripts/chaos.sh netem cam-gate loss 10%
$ scripts/chaos.sh status cam-gate
cam-gate         running  qdisc netem 8002: root refcnt 15 limit 1000 loss 10%
$ scripts/chaos.sh clear cam-gate
```

## 4. Why loss over TCP becomes delay, and when it becomes an outage

Our cameras publish **RTSP over TCP** (interleaved: RTP packets travel inside the RTSP TCP
connection). TCP never delivers a gap; it **retransmits**. So network loss never shows up as a
corrupted picture, only as late data:

```
 UDP (RTP/UDP)                              TCP (RTP interleaved in RTSP/TCP)
 seq 1 2 3 _ 5 6 ...   lost packet 4         seq 1 2 3 . . . 4 5 6   4 retransmitted, 5 and 6 wait for it
      ▼                                           ▼
 decoder: slice of frame missing             decoder: complete frames, but late
 → smeared/grey blocks until next IDR        → stall, then a burst ("head-of-line blocking")
```

How much loss TCP survives depends on its **congestion window** (cwnd: how many packets may be
in flight unacknowledged). Each loss halves it; repeated loss of the retransmission doubles the
**retransmission timeout** (RTO: 200 ms → 400 ms → 800 ms → 1.6 s → ...). Results for `cam-gate`
(main needs 2 Mb/s, sub 0.4 Mb/s):

| netem | Test | What happened |
|---|---|---|
| `loss 10%` | **pass** (6.3 s per stream, normal) | Enough headroom: Docker's bridge has ~0.1 ms RTT, so retransmits are fast |
| `loss 30%` | **fail**: `0 frames in 6 s` | RTO backoff stalls the connection > 10 s → MediaMTX `i/o timeout` on the *publisher* → camera reconnects, stalls again |
| `delay 200ms` | **pass** | Constant delay: latency, not throughput loss |
| `delay 200ms 50ms` | **fail**: `ffprobe timed out after 9 s` | See below: jitter → reordering → cwnd collapse |
| `loss 100%` | **fail**: `0 frames in 6 s`, then `404` | Cable cut, same as freeze: silence, MediaMTX `i/o timeout` ~9 s after netem was applied; no reconnect possible until `clear` |

The jitter result surprised us: 200 ± 50 ms jitter is worse than 10 % loss. `ss -ti` (socket
statistics, run in the camera's namespace with the same helper trick) shows why:

```
                     rtt (ms)      cwnd  reordering  delivery_rate   notsent (bytes queued in camera)
 clean               0.133/0.047     59      5       772 Mb/s        -
 delay 200ms 50ms    208.2/19.2       3     16       0.30 Mb/s       113382
 delay 200ms fixed   200.2/0.09     119     16       6.4 Mb/s        494637 (backlog from the jitter phase)
```

netem draws a random delay **per packet**. A packet sent 1 ms after another can get 100 ms less
delay and overtake it, so the receiver sees packets out of order. TCP treats out-of-order arrival
(duplicate ACKs) as a sign of loss, retransmits and shrinks cwnd. At cwnd 3 and 200 ms RTT, TCP
moves ~0.3 Mb/s, far below the 2 Mb/s the main stream produces. The backlog grows in the camera's
socket (`notsent`) and the stream falls further behind real time every second.

Real networks rarely reorder that much; netem's per-packet jitter is a worst case. But the effect
is real: a wireless bridge with variable latency can starve a TCP camera that a lossy but steady
link would not.

## 5. The health check: `tests/test_streams.py`

```
 sim/cameras.yaml ── gen_compose.load() + resolve_sub() ──▶ 8 streams (4 main, 4 sub), each with
                                                             expected width, height, fps
          │
          ▼ one pytest case per stream (ids: cam-gate, cam-gate-sub, ...)
 probe(rtsp://localhost:8554/<path>, seconds=6, timeout=9)  ── tools/streamprobe (ffprobe)
          │
          ├─ ProbeError (404, refused, timeout) ─────────▶ FAIL "down"
          ├─ frames ≥ 0.8 × fps × 4 s ? ─────────────────▶ FAIL "N frames in 6 s"
          ├─ codec h264, width×height as configured ? ──▶ FAIL "size"/"codec"
          ├─ wall clock ≤ 9 s ? ─────────────────────────▶ FAIL "6 s of video took X s"
          └─ no B-frames
```

Design points:

- **No camera list in the test.** It reads `sim/cameras.yaml` through the generator's own loader,
  so a new camera, a removed sub stream or a changed resolution is picked up automatically.
- **Join latency.** The first version read 4 s and expected ≥ 80 % of `4 × fps` frames. It failed
  randomly (`cam-yard-sub: 22 frames in 4 s, expected >= 32`): a reader joins **mid-GOP**, and
  frames before the next IDR (keyframe) cannot be decoded, so ffprobe never reports them. Up to one
  GOP (2 s) is lost per connect. The test now reads 6 s (4 s + one GOP) and still expects 4 s worth.
  The same delay is what a Shinobi live view shows when you open a camera.

  ```
  stream:  I P P P … P I P P P … P I P P …       GOP = 2 s
                 ▲ reader joins here
                 └──── undecodable ───┘└── counted ─────────▶
  ```

- **Wall-clock budget.** `ffprobe -read_intervals %+6` stops after 6 s of *stream time* (PTS),
  not wall time. A stream that arrives at half speed still delivers 6 s of video, just in 12 s.
  So the test times the probe and fails above 6 s + 3 s slack (`timeout=9` also kills ffprobe).
- **Not covered: latency.** A stream with a fixed 2 s backlog delivers at full speed and passes.
  The OSD clock vs host UTC (`sim/scripts/snapshot.sh`, Session 6) shows that; the end-to-end
  latency check belongs with Shinobi (Session 10+).
- **Opt-in.** `pyproject.toml` adds `-m "not e2e"` to `addopts`, so plain `uv run pytest` stays
  unit + integration only; `uv run pytest -m e2e` overrides it (the last `-m` wins). A stopped
  MediaMTX therefore *fails* the e2e run instead of being skipped.

Healthy run (fleet up, three runs in a row, all identical):

```
$ uv run pytest -m e2e -q
........                                                                 [100%]
8 passed, 70 deselected in 51.09s
```

## 6. Making one camera flap, and the test noticing (the "done when" of this session)

```
$ scripts/chaos.sh stop cam-gate
$ uv run pytest -m e2e -q
E   Failed: cam-gate: down: ffprobe exited 1: [rtsp @ 0x...] method DESCRIBE failed: 404 Not Found
E   Failed: cam-gate-sub: down: ffprobe exited 1: [rtsp @ 0x...] method DESCRIBE failed: 404 Not Found
2 failed, 6 passed, 70 deselected in 38.69s          ← only the stopped camera's two streams fail
$ scripts/chaos.sh start cam-gate
```

Flapping, probing every ~5 s while `flap --down 8 --up 12 --count 2` runs:

```
19:36:41Z [chaos] cam-gate flap 1/2: down for 8 s
19:36:42 Failed: cam-gate: down: ... 404 Not Found      1 failed in 0.40s
19:36:47 Failed: cam-gate: down: ... 404 Not Found      1 failed in 0.40s
19:36:50Z [chaos] cam-gate flap 1/2: up for 12 s
19:36:52 1 passed in 6.52s                              ← healthy 2 s after start
19:37:02Z [chaos] cam-gate flap 2/2: down for 8 s
19:37:04 Failed: cam-gate: down: ... 404 Not Found      1 failed in 0.36s
19:37:10Z [chaos] cam-gate flap 2/2: up for 12 s
```

Summary of how each fault shows up in the test:

| Fault | Test failure | Time to detect |
|---|---|---|
| `stop`, `flap` (down phase) | `down: DESCRIBE failed: 404` | immediate (0.4 s) |
| `crash` | none if the probe misses the ~3 s gap; else 404 | outage lasts ~3 s |
| `freeze` | first `0 frames`, after 10 s `404` | up to the 9 s budget |
| `netem loss 30%`, `loss 100%` | `0 frames in 6 s` (then `404` once MediaMTX times out) | 6–9 s |
| `netem delay 200ms 50ms` | `ffprobe timed out after 9 s` | 9 s |
| `netem loss 10%`, `delay 200ms` | passes: degraded but healthy | — |

## 7. Commands

```bash
scripts/chaos.sh status                              # all cameras: state + qdisc
scripts/chaos.sh stop cam-gate ; scripts/chaos.sh start cam-gate
scripts/chaos.sh crash cam-gate                      # encoder dies, back in ~3 s
scripts/chaos.sh freeze cam-gate ; scripts/chaos.sh thaw cam-gate
scripts/chaos.sh flap cam-gate --down 8 --up 12 --count 3
scripts/chaos.sh netem cam-gate loss 10%             # any netem args: delay 200ms 50ms, loss 100%, ...
scripts/chaos.sh clear cam-gate
uv run pytest -m e2e                                 # stream health (fleet must be up)
uv run pytest -m e2e -k cam-gate                     # one camera
```

## Open questions

- How does Shinobi react to each fault (reconnect delay, recording gaps, "camera offline" events)?
  → Sessions 9, 10, 25.
- Should cameras optionally publish over UDP to see real picture corruption under loss?
  `transport: udp` exists in `cameras.yaml`, but MediaMTX is TCP-only (`rtspTransports: [tcp]`).
