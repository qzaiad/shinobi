# Session 7 — Fault Injection and Stream Health Checks

This document explains how to simulate failures in IP cameras, observe how a video streaming server reacts, and automatically test whether each camera stream is healthy.

The explanations below are based on the supplied Session 7 document and its captured results. Additional conceptual explanations are included to make the technical details easier to understand.

## 1. Understand the Entire System

Imagine a port with eight simulated IP camera streams. The cameras monitor different areas, such as gates and yards. Their video travels over a network to a server, and a recording application eventually uses those streams to record footage.

The system contains four main parts:

1. **Simulated cameras** — Docker containers running FFmpeg. These generate video and publish it as if they were IP cameras. For example, `cam-gate` represents a camera watching a gate.
2. **MediaMTX** — an RTSP streaming server on port `8554`. It receives camera streams and makes them available to clients.
3. **Automated stream tests** — Pytest and FFprobe. They check whether video arrives, whether enough frames are decoded, whether the resolution and codec are correct, and whether probing finishes on time.
4. **Shinobi, the NVR** — the downstream video recording system. Later sessions investigate whether it reconnects, detects offline cameras, and handles recording gaps after failures.

### How video travels through the system

```text
FFmpeg inside camera container
             |
             v
       TCP socket -> eth0
             |
             v
    Optional netem fault injection
             |
             v
          MediaMTX
             |
             v
    FFprobe health check / NVR
```

The crucial idea is that **a stream being connected is not necessarily a stream that is delivering usable video**.

A TCP connection may remain open even after a camera stops producing frames. A superficial check could say, “The camera is connected.” A proper health check asks whether enough decodable video is arriving in the expected format and within a reasonable time.

## 2. What Is Fault Injection?

**Fault injection** means deliberately introducing a failure into a system to see whether the system detects it and recovers correctly.

Ordinary testing might start all eight camera streams and confirm that they work. That proves only that the system works under healthy conditions. It does not prove that it behaves correctly when a camera is switched off, its encoder crashes, its operating system freezes, packets are dropped, or the network becomes slow.

The project's `scripts/chaos.sh` can reproduce failures so they can be observed and tested repeatedly.

A typical fault-injection workflow is:

1. Establish a healthy baseline by verifying that camera streams deliver the expected video.
2. Inject a fault by stopping a camera, crashing FFmpeg, freezing its container, or introducing network impairment.
3. Observe the system by inspecting MediaMTX logs, connection behavior, received frames, and test results.
4. Verify detection and recovery by checking whether the test fails for the right reason and whether the stream becomes healthy again.

The purpose is not to make the system fail for its own sake. It is to determine whether it handles failures predictably.

## 3. The Five Camera Failure Modes

The five failure types below produce different network symptoms and therefore require different detection and recovery behavior.

### 3.1. Clean Shutdown or Reboot

A camera deliberately shuts down its streaming process, perhaps for a firmware update or scheduled reboot.

In this simulation, `stop` causes Docker to stop the camera container. FFmpeg receives a termination signal through `publish.sh` and shuts down politely.

The RTSP client sends a `TEARDOWN` message, and the TCP connection closes. MediaMTX receives an explicit indication that the session has ended.

**Expected result:** The server quickly removes the stream path, and readers trying to open it may receive `404 Not Found`.

```bash
scripts/chaos.sh stop cam-gate
```

### 3.2. Encoder Crash

The video encoder fails abruptly because of a software bug or similar problem. The document simulates this by sending `SIGKILL` to FFmpeg.

Unlike a clean shutdown, FFmpeg cannot send the RTSP `TEARDOWN` message. However, the operating system closes the process's TCP socket, so MediaMTX can still learn that the connection has ended.

In the captured example, the publishing script restarts the encoder, and the stream returns after approximately three seconds.

**Expected result:** A brief outage, followed by automatic reconnection.

```bash
scripts/chaos.sh crash cam-gate
```

### 3.3. Freeze or Loss of Power

This is the most important distinction in the session.

The camera stops producing data, but the connection does not immediately close. In the simulation, `docker pause` freezes the container's processes while its network namespace remains present.

MediaMTX initially has no explicit disconnect notification. The RTSP path can still appear available even though no new frames arrive.

The server must eventually notice that reading data has timed out.

**Expected result:** The stream becomes unavailable after the timeout, and a reader can experience a period in which the connection exists but the video is empty.

```bash
scripts/chaos.sh freeze cam-gate
scripts/chaos.sh thaw cam-gate
```

### 3.4. Flapping

A flapping camera repeatedly alternates between being online and offline.

Real-world causes might include an unstable power supply, an overloaded Power over Ethernet (PoE) switch, a loose connection, or overheating.

Flapping is useful for testing recovery logic because the system must cope with repeated outages rather than a single clean failure.

**Expected result:** Tests fail during down periods and pass when the camera has recovered, provided the test runs at the right time.

```bash
scripts/chaos.sh flap cam-gate --down 8 --up 12 --count 3
```

### 3.5. Bad Network Link

A camera may remain powered and continue running normally while its network connection becomes unreliable.

The network can introduce packet loss, delay, jitter, or packet reordering.

The camera is not necessarily offline. Instead, its video may arrive too late to be useful.

**Expected result:** Mild degradation might pass the test, while severe impairment may cause the probe to time out or the stream to become unavailable.

```bash
scripts/chaos.sh netem cam-gate loss 10%
```

The distinction between a process failure and a network failure matters because the recovery mechanism that works for one failure might not work for another. A clean disconnect is easy to recognize. A silent failure requires a timeout or data-flow health check. A degraded link may require measuring delivery performance rather than merely checking connectivity.

## 4. RTSP, TCP, RTP, and the Meaning of a Disconnect

### 4.1. What Is RTSP?

RTSP stands for **Real Time Streaming Protocol**. In this project, it is used to establish and manage streaming sessions.

For example, the test probes this address:

```text
rtsp://localhost:8554/cam-gate
```

| Component | Meaning |
|---|---|
| `rtsp://` | The streaming protocol |
| `localhost` | The local host from which the probe connects |
| `8554` | The MediaMTX RTSP port |
| `/cam-gate` | The requested stream path |

RTSP has commands for describing, starting, and ending a session. The document specifically discusses `DESCRIBE` and `TEARDOWN`.

- `DESCRIBE` requests the stream description, including its media tracks and codec-related information.
- `TEARDOWN` asks to terminate the streaming session.

A successful `DESCRIBE` does not guarantee that actual video frames will arrive.

### 4.2. What Is RTP?

RTP stands for **Real-time Transport Protocol**. It carries real-time media, such as video and audio.

The document's cameras use **RTSP over TCP with interleaved RTP**. This means the media packets travel within the RTSP TCP connection rather than through a separate RTP/UDP transport.

That design affects how network failures appear to the receiver.

### 4.3. What Are TCP FIN and RST?

TCP is the transport protocol that manages the connection.

- **FIN** generally indicates that a side is closing its connection in an orderly way.
- **RST** indicates an abrupt reset of the connection.

An RTSP `TEARDOWN` message is an application-level protocol action. A TCP `FIN` or `RST` is a transport-level event. They are different things, even though they can occur close together.

A clean shutdown can send `TEARDOWN` before the socket closes. A killed process cannot send that message, but the operating system can still close its socket.

### 4.4. Why Does `404 Not Found` Matter?

In these captured tests, MediaMTX returns `404 Not Found` when the requested camera path is no longer available.

The test interprets this as a stream-down condition.

However, `404` is not a universal diagnosis of every camera problem. It indicates that the requested path is unavailable in this scenario; it does not, by itself, identify the original physical cause.

- A stopped camera may produce a `404` almost immediately.
- A frozen camera may continue to have an apparently available path until a timeout removes it.
- A heavily delayed stream may still exist but fail the test's time budget.

The same general outcome—“the stream is not usable”—can arise through different mechanisms.

## 5. Clean Shutdown Versus Silence: The Central Lesson

The document's most important experiment compares `stop` with `freeze`.

### Scenario A: The Camera Says Goodbye

The captured MediaMTX log contains:

```text
19:32:40 INF [RTSP] [session 2434df21] destroyed: torn down by 172.18.0.6:42912
19:32:40 INF [RTSP] [conn 172.18.0.6:42912] closed: EOF
```

Here is what happened:

1. The camera's FFmpeg process shut down politely.
2. The RTSP session was explicitly torn down.
3. The TCP connection closed.
4. MediaMTX learned about the disconnection immediately.
5. The stream path became unavailable to readers.

The log entry `torn down` indicates that the server observed an orderly RTSP session termination.

### Scenario B: The Camera Stops Sending Data

The `freeze` experiment produced this log:

```text
19:33:55 INF [RTSP] [conn 172.18.0.2:8554->172.18.0.6:42096] closed: read tcp ...: i/o timeout
19:33:55 INF [RTSP] [session 97dec1f9] destroyed: not in use
```

This time, the server waited before closing the connection.

The document reports a ten-second `readTimeout` in this configuration.

During that interval:

- The camera container's processes are paused.
- The publishing connection has not yet been recognized as dead.
- A reader may still be able to request the stream description.
- The reader receives zero usable frames.
- Eventually, MediaMTX times out and removes the dead publishing path.

### Compare the Two Timelines

**Clean stop**

```text
Video flowing -> Explicit disconnect -> Path removed
```

**Frozen camera**

```text
Video flowing -> No frames -> Wait for timeout -> Path removed
```

A timeout establishes a limit on how long the server waits without receiving expected data.

Without an appropriate timeout, a frozen publisher might leave a stream path apparently online indefinitely. A downstream recording application could then fail to recognize the missing video promptly.

A read timeout detects prolonged lack of data, but it is not a complete video-quality monitor. A stream that continues sending data very slowly, or sends data that cannot be decoded, may need additional checks.

## 6. Understanding `netem`: Deliberately Impairing the Network

The command `scripts/chaos.sh netem` uses Linux's traffic-control system, `tc`, to change how packets leave a camera's network interface.

This lets the project simulate a bad network without physically disconnecting a cable or modifying the host's network hardware.

### 6.1. What Is a qdisc?

A **qdisc**, short for *queueing discipline*, controls how outgoing packets are queued and transmitted by a network interface.

Think of the network interface as a dispatch desk. Packets arrive at the desk, wait if necessary, and are sent onward.

A `netem` queue can deliberately introduce impairments:

| Impairment | What it does | Real-world analogy |
|---|---|---|
| Packet loss | Drops a proportion of packets | An unreliable network link |
| Delay | Holds packets before transmission | A long or congested network path |
| Jitter | Varies the delay between packets | An unstable wireless connection |
| Reordering | Allows later packets to arrive before earlier ones | Different packets experiencing different path delays |
| Duplication | Creates duplicate packets | An additional packet appearing unexpectedly |

### 6.2. Why Does the Project Use a Helper Container?

The simulated camera containers are intentionally not given broad network-administration privileges.

Changing a network interface's qdisc requires the `NET_ADMIN` capability in the relevant network namespace.

A Docker container normally has its own network namespace, which provides its own network interfaces, routes, and network settings.

The project launches a temporary helper container that shares the camera's network namespace.

```text
Camera network namespace
       Shared eth0
        /       \
       /         \
Camera container  Helper container
Runs FFmpeg       Runs tc with NET_ADMIN
No NET_ADMIN      Changes shared qdisc
```

The essential command structure shown in the document is:

```bash
docker run --rm \
  --network container:<cam-id> \
  --cap-add NET_ADMIN \
  --entrypoint tc <camera image> \
  qdisc replace dev eth0 root netem loss 10%
```

What each part means:

- `docker run`: launches a temporary container.
- `--rm`: removes that container after it exits.
- `--network container:<cam-id>`: shares the target container's network namespace.
- `--cap-add NET_ADMIN`: grants the capability required to administer network settings.
- `--entrypoint tc`: runs the traffic-control utility directly.
- `qdisc replace dev eth0 root netem`: installs or replaces the root qdisc on `eth0` with `netem`.
- `loss 10%`: instructs `netem` to drop approximately 10% of outgoing packets.

This avoids granting the simulated camera the network-administration capability just to perform tests.

The helper is temporary, and the camera's network namespace disappears when the camera container is recreated. The impairment is associated with the lifetime and configuration of that network namespace.

### 6.3. Which Direction Is Affected?

The document states that `netem` shapes **egress traffic from the camera**, meaning the camera-to-MediaMTX direction.

```text
FFmpeg
   |
   v
TCP send buffer
   |
   v
Camera eth0 egress qdisc
   |
   v
Docker network
   |
   v
MediaMTX
```

This simulates problems in the video-publishing direction. It does not automatically simulate every possible network failure. A problem that affects only server-to-camera traffic, or only the return path, may require a different arrangement.

### 6.4. Network-Emulation Commands

```bash
# Introduce 10% packet loss
scripts/chaos.sh netem cam-gate loss 10%

# Inspect the camera and its qdisc
scripts/chaos.sh status cam-gate

# Remove the injected impairment
scripts/chaos.sh clear cam-gate
```

The captured status output includes:

```text
cam-gate running qdisc netem 8002: root refcnt 15 limit 1000 loss 10%
```

This confirms that the camera is running and that the network interface has a `netem` qdisc configured with 10% loss.

A camera being reported as `running` by Docker does not prove that its video stream is healthy. Process state and stream health are separate observations.

## 7. Why Packet Loss over TCP Often Becomes Delay Instead of Corrupted Video

The cameras publish their video using RTSP over TCP. TCP provides reliable, ordered delivery of a byte stream.

That reliability has an important consequence: **a lost packet can delay later video data even when the receiver eventually gets every byte correctly**.

### 7.1. TCP Versus UDP

Consider a simplified sequence of media data.

**UDP:** Missing media data may remain missing.

```text
1  2  3  X  5  6
```

If a required video packet is lost and not recovered by another mechanism, the decoder may lack part of a frame. Depending on the codec and frame, the result can include visual corruption or missing imagery.

**TCP:** Missing bytes are retransmitted.

```text
1  2  3  ...  4  5  6
```

Later bytes may have to wait until the missing bytes are recovered. The picture can remain intact, but playback may stall and then catch up in a burst.

TCP does not eliminate network problems. It changes how those problems manifest.

For surveillance, receiving a complete frame several seconds late can be just as problematic as not receiving it at all. A live view of a gate that is five seconds behind reality may decode correctly but still be operationally misleading.

### 7.2. What Is Head-of-Line Blocking?

Head-of-line blocking occurs when later data cannot be delivered to the application in order because earlier data is missing.

Suppose TCP is carrying bytes corresponding to successive pieces of video:

1. Piece A arrives.
2. Piece B arrives.
3. Piece C is lost.
4. Pieces D, E, and F reach the receiver.
5. The receiving application cannot consume the byte stream beyond the missing section in the normal ordered manner.
6. The missing data is retransmitted.
7. The stream can continue, potentially after a noticeable delay.

The later data may already be waiting, but it cannot simply bypass the missing bytes in the ordered TCP stream.

### 7.3. What Is the Congestion Window?

TCP maintains a **congestion window**, usually abbreviated `cwnd`. It limits how much data can be in flight without acknowledgment, according to the congestion-control algorithm.

If TCP detects signs of congestion or packet loss, it may reduce the amount of data it sends before waiting for further acknowledgments.

The document focuses on two effects:

- The sender's congestion window can shrink.
- Retransmission delays can increase, making the connection much slower.

The retransmission timeout, or RTO, is the timer used when TCP must wait for acknowledgment of data that may need retransmission.

The document gives an illustrative progression of `200 ms -> 400 ms -> 800 ms -> 1.6 s -> ...` as retransmission timeouts back off.

These values illustrate exponential backoff; they are not a promise that every connection will follow exactly that sequence. Actual behavior depends on the TCP implementation, configuration, and network conditions.

### 7.4. The Network Experiments

The document reports the following results for `cam-gate`.

| Injected condition | Observed result | Interpretation |
|---|---|---|
| `loss 10%` | Pass; approximately 6.3 seconds per stream | TCP recovered sufficiently quickly in this environment. |
| `loss 30%` | Fail; zero frames in six seconds | Severe retransmission delays and repeated publisher timeouts prevented usable video delivery. |
| `delay 200ms` | Pass | Constant delay increased latency but did not prevent sufficient throughput. |
| `delay 200ms 50ms` | Fail; FFprobe timed out after nine seconds | Variable delay and reordering substantially reduced effective throughput in this experiment. |
| `loss 100%` | Fail; zero frames, followed by a `404` | No video could reach the server, and the publishing connection eventually timed out. |

The results are specific to the configuration in the document. They are not universal thresholds for all cameras or TCP connections.

### 7.5. Why Did 10% Loss Pass but 30% Loss Fail?

The document says that the main stream requires approximately 2 Mb/s, while the substream requires approximately 0.4 Mb/s.

At 10% packet loss, the simulated Docker network's very short round-trip time allowed TCP retransmissions to happen quickly enough to keep the stream operating within the test's limits.

At 30% loss, the connection repeatedly stalled. The publishing side could go without successfully delivering data for longer than MediaMTX's read-timeout interval. MediaMTX then closed the publishing connection, and the publisher's restart mechanism attempted to reconnect.

This can create a repeated cycle:

```text
Packet loss becomes severe
          |
          v
TCP retransmissions and backoff
          |
          v
Video data stops arriving promptly
          |
          v
MediaMTX read timeout
          |
          v
Publisher connection closes
          |
          v
FFmpeg/publishing script reconnects
          |
          v
Network is still impaired
          |
          +------> Failure can repeat
```

The failure is not necessarily caused by the video encoder itself. The network impairment can make a healthy encoder's output impossible to deliver reliably within the configured timeout.

### 7.6. Why Did Jitter Perform Worse Than Fixed Delay?

The document provides a comparison from `ss -ti`, which displays TCP socket statistics.

| Measurement | Clean link | 200 ms delay + 50 ms jitter | Fixed 200 ms delay |
|---|---:|---:|---:|
| RTT, as reported | 0.133/0.047 ms | 208.2/19.2 ms | 200.2/0.09 ms |
| Congestion window | 59 | 3 | 119 |
| Reordering | 5 | 16 | 16 |
| Delivery rate | 772 Mb/s | 0.30 Mb/s | 6.4 Mb/s |
| Unsent bytes | Not reported | 113,382 | 494,637 |

The values are captured observations, not universal performance benchmarks. The fixed-delay result was measured after the jitter phase and includes a backlog, so it is not a pristine independent baseline.

The most striking observation is the delivery rate of approximately 0.30 Mb/s during jitter.

The main stream requires approximately 2 Mb/s. Therefore:

\[
\frac{0.30\ \text{Mb/s}}{2\ \text{Mb/s}}=0.15
\]

That is only 15% of the main stream's nominal rate.

If the encoder keeps producing data faster than the connection can deliver it, queued data accumulates. The stream falls increasingly far behind real time.

#### Why Can Jitter Cause Reordering?

Suppose packet A is sent first and packet B one millisecond later.

If packet A experiences 250 ms of delay while packet B experiences 150 ms, packet B may arrive first.

The receiver now sees the packets in a different order from the order in which they were sent.

TCP implementations use acknowledgments and other signals to detect and recover from missing or reordered data. Depending on the conditions, reordering can trigger unnecessary retransmissions or congestion-window reductions.

The document's conclusion is that `netem`'s random per-packet delay created a particularly harsh reordering pattern in this test.

This demonstrates what happened under this specific impairment model, not that jitter is always worse than packet loss in every real network.

## 8. The Automated Health Check: `tests/test_streams.py`

The network emulator introduces faults. The health-check test determines whether the stream still satisfies the requirements.

The test uses:

- `sim/cameras.yaml` — the source of truth for simulated camera configuration.
- The project's configuration loader and stream generator.
- Pytest — the testing framework.
- FFprobe — the utility that inspects and decodes media streams.
- MediaMTX — the RTSP server that provides the streams.

The documented fleet contains eight streams: four main streams and four substreams.

### 8.1. Why Does Each Camera Have Two Streams?

A main stream typically provides the higher-quality video feed, while a substream provides a lower-bandwidth alternative.

For the camera examined in the document:

| Stream | Nominal video bitrate | Typical purpose |
|---|---:|---|
| Main | 2 Mb/s | Higher-quality viewing or recording |
| Substream | 0.4 Mb/s | Lower-bandwidth viewing |

The exact resolutions and frame rates are configured in `sim/cameras.yaml`; the excerpt does not enumerate every camera's individual values.

The main benefit of having both is flexibility. An NVR may use a high-quality stream for recording while using a lower-bandwidth stream for a live overview, depending on its configuration.

The tests treat the main stream and substream as separate things to validate. A camera can therefore have a failed main stream, a failed substream, or failures in both.

### 8.2. How the Test Discovers Cameras

The test does not contain a hard-coded list of camera names. Instead, it reads `sim/cameras.yaml` through the same configuration loader used by the generator.

The process is:

1. Load the camera configuration.
2. Resolve the relevant stream paths and substreams.
3. Obtain the expected codec, width, height, and frame rate.
4. Create a test case for each configured stream.
5. Probe the corresponding RTSP URL.
6. Compare the actual results against the expected values.

This reduces the risk that the simulator and its tests become inconsistent. If a camera's substream is added to the configuration, the test can pick it up automatically instead of requiring a separate test-list update.

## 9. What Exactly Does a Health Check Verify?

The document describes five checks.

### Check 1 — Can the Stream Be Opened?

The test attempts to connect to the RTSP path.

If the probe encounters a connection refusal, timeout, or `404 Not Found`, the test reports the stream as down.

This checks availability, but it is only the first stage.

### Check 2 — Are Enough Frames Arriving?

The probe reads six seconds of stream time, but the test expects at least four seconds' worth of frames.

The threshold is 80% of the expected frame count for four seconds:

\[
F_{\min}=0.8\times fps\times4
\]

A connection that produces zero frames fails, even if the RTSP path initially appeared available.

### Check 3 — Are the Codec and Dimensions Correct?

The test checks that the video uses H.264 and that its width and height match the configuration.

A stream can be online and deliver frames while still failing because it has the wrong resolution or codec.

### Check 4 — Does the Probe Finish Within the Wall-Clock Budget?

The probe is allowed nine seconds to retrieve six seconds of video.

If it takes too long, the test fails—even if the stream eventually delivers the requested media.

### Check 5 — Does the Stream Avoid B-Frames?

The test also checks for the absence of B-frames, as required by the simulator's expected encoding behavior.

The document records this as a final validation rule; it does not provide a separate B-frame failure log in the captured examples.

### 9.1. Working Through the Frame-Count Calculation

Suppose a stream is configured to produce 8 frames per second.

The expected number of frames in four seconds is:

\[
8\times4=32
\]

The minimum acceptable count is:

\[
0.8\times32=25.6
\]

Since the actual frame count must be an integer, the stream needs at least 26 frames to satisfy this threshold.

For a 15 fps stream:

\[
0.8\times15\times4=48
\]

It must deliver at least 48 frames.

For a 25 fps stream:

\[
0.8\times25\times4=80
\]

It must deliver at least 80 frames.

The test scales the threshold with the configured frame rate rather than using one arbitrary minimum for every camera.

### 9.2. Why Read Six Seconds if Only Four Seconds Are Required?

This is a subtle but important testing decision.

The first version of the test read four seconds and expected at least 80% of the corresponding frame count. It failed intermittently because an RTSP reader can connect in the middle of a GOP.

GOP stands for **Group of Pictures**. It is a sequence of encoded video frames organized around keyframes.

The document's simulated streams use a GOP interval of approximately two seconds.

An IDR frame is a special type of keyframe that can provide a clean decoding starting point. If a reader joins after the latest suitable keyframe, it may have to wait until the next one before it can decode frames normally.

The original test encountered an example like this:

```text
cam-yard-sub: 22 frames in 4 s, expected >= 32
```

The camera might have been functioning correctly. The probe had simply missed some decodable frames while waiting for the next keyframe.

The test was therefore changed to read six seconds while still requiring only four seconds' worth of frames.

```text
Stream: I P P P P P P P I P P P P P P P I
                    ^
                    reader connects

Initial portion: May not be decodable
Later portion:   Decodable frames counted
```

The extra two seconds provide room for approximately one GOP interval.

A good test should detect actual failures without randomly failing because of a normal characteristic of video decoding. This adjustment addresses the specific intermittent failure described in the document, though it does not guarantee that every possible GOP or connection pattern is covered.

## 10. Stream Time Versus Wall-Clock Time

This distinction explains why the test needs both a frame-count threshold and a time limit.

**Stream time** refers to the timestamps associated with the media. **Wall-clock time** refers to the actual time that passes while the probe runs.

Consider a probe that requests six seconds of video.

Under healthy conditions, it might retrieve those six seconds in approximately six seconds, subject to startup and processing overhead.

But suppose the stream delivers video at half its normal real-time rate. The probe may still accumulate six seconds of media timestamps, but doing so could take roughly twelve seconds of actual time.

The video may be decodable, and the media duration may be correct, but the stream is arriving too slowly.

The document's configuration is:

- Media interval: six seconds.
- Wall-clock allowance: nine seconds.
- Extra allowance: three seconds.

The simplified rule is:

\[
T_{\text{wall}}\leq6+3=9\text{ seconds}
\]

If the probe exceeds that limit, it fails.

This gives the test two complementary ways to catch a bad stream:

1. It checks that enough frames were decoded.
2. It checks that those frames arrived quickly enough.

Neither check replaces the other. A stream could deliver enough frames eventually but be too slow. Alternatively, the probe might finish quickly but deliver too few frames or the wrong video format.

### What the Test Does Not Measure

The document explicitly identifies a limitation: **the test does not measure absolute end-to-end latency**.

Suppose a camera produces video that is already two seconds old when it reaches the receiver, but then sends the frames steadily at the expected rate.

The probe may retrieve six seconds of media in an acceptable wall-clock interval and pass all of its checks. Yet the displayed video could still be two seconds behind reality.

The document says that comparing the on-screen display (OSD) clock with the host's UTC clock can reveal this sort of delay, and that the dedicated end-to-end latency check belongs in later Shinobi-focused sessions.

A passing health check establishes only the properties that the test actually measures.

## 11. How to Interpret the Test Results

The document records a healthy run:

```text
$ uv run pytest -m e2e -q

........                                                                 [100%]

8 passed, 70 deselected in 51.09s
```

Interpretation:

- `uv run`: runs the command in the project's configured Python environment using `uv`.
- `pytest`: executes the tests.
- `-m e2e`: selects tests marked as end-to-end tests.
- `-q`: enables quieter output.
- Eight passed: all eight configured stream tests succeeded.
- Seventy deselected: other tests were not selected by this invocation.
- `51.09s`: the total duration of this run.

The eight passing cases correspond to the eight configured streams in the documented fleet.

### 11.1. Why Are the End-to-End Tests Opt-In?

The document states that `pyproject.toml` configures pytest with a default marker filter excluding end-to-end tests.

As a result:

```bash
uv run pytest
```

runs the normal unit and integration tests without automatically requiring the complete streaming environment.

To run the end-to-end checks explicitly:

```bash
uv run pytest -m e2e
```

This is useful because end-to-end tests depend on external services and running containers. They are slower and may fail if the streaming server is unavailable.

The document also explains that the last `-m` marker expression supplied to pytest takes precedence in this setup. Therefore, explicitly specifying `-m e2e` overrides the default exclusion.

A successful ordinary test run does not necessarily mean that the camera streams are healthy. You must run the end-to-end checks to establish that.

## 12. What Happens When You Deliberately Stop a Camera?

The document's first failure experiment is straightforward. Start with a healthy fleet, stop `cam-gate`, and run the end-to-end tests.

```bash
scripts/chaos.sh stop cam-gate
uv run pytest -m e2e -q
```

The captured result was:

```text
Failed: cam-gate: down:
ffprobe exited 1: [rtsp @ 0x...] method DESCRIBE failed:
404 Not Found

Failed: cam-gate-sub: down:
ffprobe exited 1: [rtsp @ 0x...] method DESCRIBE failed:
404 Not Found

2 failed, 6 passed, 70 deselected in 38.69s
```

Notice that two streams fail, not one. `cam-gate` has both a main stream and a substream. Stopping the publishing camera makes both paths unavailable.

The other six streams pass.

This is the behavior we want from an isolated fault test: the affected camera's streams fail, while unrelated cameras continue to work.

After the experiment, the document restarts the camera:

```bash
scripts/chaos.sh start cam-gate
```

This test validates two things:

1. The test suite detects the affected streams as down.
2. The test suite does not automatically classify every other camera as failed.

It is a **negative test**: we deliberately create an unhealthy condition and verify that the software reports failure rather than incorrectly passing.

## 13. Flapping Tests and Why Timing Matters

A single stop-and-start experiment is not enough to validate all recovery behavior. Real systems can experience repeated outages, so the project includes a flapping mode.

```bash
scripts/chaos.sh flap cam-gate --down 8 --up 12 --count 3
```

The parameters mean:

- `cam-gate`: the target camera.
- `--down 8`: each down phase lasts eight seconds.
- `--up 12`: each up phase lasts twelve seconds.
- `--count 3`: perform three down/up cycles.

During the experiment, the test is run repeatedly.

The captured log includes this sequence:

| Approximate time | Event | Test result |
|---|---|---|
| 19:36:41 | First down phase begins | — |
| 19:36:42 | Camera unavailable | One test fails |
| 19:36:47 | Camera still down | One test fails |
| 19:36:50 | Camera comes back up | — |
| 19:36:52 | Camera is healthy again | One test passes |
| 19:37:02 | Second down phase begins | — |
| 19:37:04 | Camera unavailable | One test fails |
| 19:37:10 | Camera comes back up | — |

The precise test outcome depends on when the probe starts relative to the camera's state.

If a probe starts while the camera is offline, it may immediately receive a `404`. If it starts when the camera is online, it may complete successfully after collecting the required six seconds of media. If the camera fails during probing, the outcome depends on the exact timing and failure mode.

### Why This Is Not a Continuous Monitoring Test

A periodic test samples the system at particular moments. It does not observe every instant between samples.

Imagine a camera that fails for three seconds and recovers between two probes. If neither probe overlaps the outage, both can pass.

That is why the document notes that the encoder-crash outage may go undetected if the probe misses its approximately three-second window.

A production monitoring system might combine periodic probes with server-side events, connection monitoring, frame-arrival monitoring, and recording-state checks to reduce this blind spot. Those additional mechanisms are design considerations, not features demonstrated by the captured test output.

## 14. How Quickly Does Each Failure Become Detectable?

The document provides a summary of the observed detection behavior.

| Fault | Observed test symptom | Approximate detection window |
|---|---|---|
| `stop` or a flapping down phase | `404 Not Found` | About 0.4 seconds in the captured failure |
| `crash` | Either no observed failure or a `404` | Depends on whether the probe overlaps the roughly three-second outage |
| `freeze` | Initially zero frames; later a `404` | Up to the nine-second probe budget; server timeout is about ten seconds |
| `loss 30%` or `loss 100%` | Zero frames, then potentially `404` | Approximately six to nine seconds for the probe; server timeout may occur later |
| `delay 200ms 50ms` | FFprobe timeout | Nine seconds |
| `loss 10%` or fixed `delay 200ms` | Pass | No failure detected by this test |

These times describe the captured environment and the specific test configuration. They should not be treated as guaranteed worst-case detection times for every deployment.

The probe's nine-second budget and MediaMTX's ten-second read timeout are separate mechanisms. The test can give up before the server finishes its timeout process. Depending on when the freeze begins relative to the probe, a zero-frame failure can be observed before a subsequent `404`.

This demonstrates why system-level monitoring and test-level timeouts should be considered separately.

## 15. A Complete Practical Walkthrough

The following sequence follows the commands in the document. It assumes the repository's Docker services and Python environment are available. These commands are for the controlled test environment, not for disrupting real production cameras.

### Step 1 — Check the Current State

```bash
scripts/chaos.sh status
```

This reports the camera state and network qdisc information.

### Step 2 — Establish the Baseline

```bash
uv run pytest -m e2e -q
```

In the captured healthy run, all eight stream checks passed.

### Step 3 — Stop One Camera

```bash
scripts/chaos.sh stop cam-gate
uv run pytest -m e2e -q
```

Expected behavior: the main and substream checks for `cam-gate` fail, while the other six streams pass.

### Step 4 — Restore the Camera

```bash
scripts/chaos.sh start cam-gate
```

### Step 5 — Test an Encoder Crash

```bash
scripts/chaos.sh crash cam-gate
```

The document reports an outage of roughly three seconds before the publishing script restores the stream. Whether a probe detects it depends on its timing.

### Step 6 — Test a Silent Freeze

```bash
scripts/chaos.sh freeze cam-gate
```

Run the stream probe while the camera is frozen. The test may report zero frames even while the stream path is initially available.

Restore the container with:

```bash
scripts/chaos.sh thaw cam-gate
```

The document reports that FFmpeg then encounters a failed write to the closed socket and the publishing script restarts it.

### Step 7 — Test Packet Loss

```bash
scripts/chaos.sh netem cam-gate loss 10%
scripts/chaos.sh status cam-gate
```

The captured 10% loss test passed.

For the more severe impairment, the document records:

```bash
scripts/chaos.sh netem cam-gate loss 30%
```

The reported result was zero frames in six seconds, followed by a publisher timeout and reconnection attempts.

### Step 8 — Clear the Impairment

```bash
scripts/chaos.sh clear cam-gate
```

This removes the injected network impairment.

### Step 9 — Test Only the Affected Camera

```bash
uv run pytest -m e2e -k cam-gate
```

This is useful when investigating a single camera rather than running the full fleet test suite.

For all these experiments, the project-specific `chaos.sh` implementation is the authority on the exact behavior of each subcommand.

## 16. What Does Session 7 Actually Prove?

The evidence in the document supports several conclusions:

- **Faults can be reproduced on demand.** The project can simulate clean stops, crashes, freezes, repeated outages, and network impairment.
- **The health check distinguishes availability from usable video.** It detects cases where a stream is accessible but fails to deliver enough decodable frames.
- **The tests follow the camera configuration.** Camera and substream definitions come from the shared configuration loader instead of a separate hard-coded test list.
- **The checks identify several classes of failure.** They validate connection availability, frame count, encoding properties, and probe duration.
- **The experiments expose transport-level behavior.** The packet-loss and jitter experiments show that TCP reliability does not guarantee timely delivery.

But there are equally important limitations.

### What Has Not Yet Been Demonstrated?

The document does not establish that Shinobi will recover correctly from every fault. It explicitly reserves that investigation for later sessions.

It also does not prove:

- That every short outage will be detected by periodic probes.
- That passing streams have acceptable absolute latency.
- That every network topology will behave like the Docker bridge used in these tests.
- That the measured packet-loss and jitter thresholds apply to other hardware, kernels, or TCP configurations.
- That recording continuity and offline-camera notifications work correctly.

These are separate requirements that need their own tests.

## 17. Open Questions and Next Engineering Steps

The document identifies two explicit open questions.

### Question 1 — How Does Shinobi React to Each Fault?

The next step is to measure what the NVR does when the publisher disappears or becomes unreliable.

For each fault, useful observations include:

- How quickly Shinobi marks the camera offline.
- How long it takes to reconnect after the stream returns.
- Whether recording resumes automatically.
- Whether recordings contain gaps.
- Whether an offline event is raised and later cleared.

The distinction between stream health and recording health is crucial. A stream can recover successfully while the recording system still has a gap, or the stream can remain healthy while the NVR has another problem.

The document assigns this work to Sessions 9, 10, and 25.

### Question 2 — Should the Project Also Test UDP Transport?

The document notes that `transport: udp` exists in `cameras.yaml`, but MediaMTX is configured with `rtspTransports: [tcp]`.

That means the current tests do not demonstrate UDP-based RTP behavior.

Testing UDP would help explore a different failure profile. Since UDP does not provide TCP's built-in reliable, ordered byte-stream delivery, packet loss can have more direct effects on the media delivered to the decoder.

A meaningful comparison would need to control the camera settings, network impairment, receiver configuration, and measurement method. It should measure not only whether the probe passes but also frame loss, decode errors, latency, and visual corruption where applicable.

This is a proposed next step, not a completed result from Session 7.

## 18. Final Mental Model: Five Principles

1. **A disconnect is not the same as silence.** A cleanly closed connection can be detected immediately. A frozen publisher may require a timeout.
2. **Reliable transport does not guarantee timely video.** TCP may eventually deliver all bytes but still deliver them too late for a live surveillance application.
3. **A connected stream is not necessarily a healthy stream.** Check that decodable frames arrive in sufficient quantity and within an acceptable time.
4. **Media duration and real elapsed time are different.** A probe can obtain six seconds of media while taking much longer than six seconds to do so.
5. **Test the failure, the detection, and the recovery separately.** Introducing a fault is only the first step. A robust system must identify the fault, restore the stream when possible, and preserve accurate recording and monitoring behavior.

## Resources

1. **Supplied Session 7 document** — the primary source for the architecture, commands, captured logs, test results, and open questions.
2. [MediaMTX documentation](https://mediamtx.org/docs/) — RTSP server configuration, paths, publishing, and reading streams.
3. [FFmpeg and FFprobe documentation](https://ffmpeg.org/documentation.html) — probing media streams, codec information, and frame-related options.
4. [Linux `tc-netem` manual](https://man7.org/linux/man-pages/man8/tc-netem.8.html) — network delay, packet loss, duplication, and reordering.
5. [Pytest documentation](https://docs.pytest.org/en/stable/) — test selection, marker expressions, and test execution.
6. [Docker networking documentation](https://docs.docker.com/engine/network/) — container network isolation and shared network namespaces.
7. [TCP specification, RFC 9293](https://www.rfc-editor.org/rfc/rfc9293) — TCP connection behavior and reliable byte-stream delivery.

## Trust Assessment

**My Trust: 9/10.** This explanation follows the supplied document and its captured results. The main uncertainty is how those specific timings and network effects would generalize to other environments.

**Points to verify:** The exact `chaos.sh` implementation, active MediaMTX timeout configuration, per-camera frame rates and resolutions, TCP congestion-control algorithm, and Shinobi's recovery behavior are not fully established by the excerpt. The captured test results should be treated as evidence for the documented setup, not universal guarantees.
