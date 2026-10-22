# Session 9 — Understanding Shinobi's camera-monitor pipeline, in exhaustive detail

Your document explains how a camera becomes a working monitor in Shinobi: how the configuration is saved, how FFmpeg is launched, how the video is transported, how browser live view works, how recording differs from live streaming, and what happens when the camera disconnects.

I'll explain it from the ground up, connecting the individual commands and configuration fields to the larger architecture. I'll distinguish what your document reports as experimentally verified from what it identifies as an interpretation or an open question.&#x20;

Pasted markdown.md



## 1. The big picture: what is Shinobi actually doing?

Imagine a camera pointed at a gate. The camera continuously produces a sequence of video frames. You want to watch those frames in a browser, perhaps record them, and eventually use them for motion or object detection.

The camera doesn't simply send a finished browser video directly to Shinobi. There are several components involved, each doing a different job.

1\. Camera simulator

Produces H.264 video and publishes it using RTSP.

RTSP over TCP

2\. MediaMTX

Receives the camera stream and makes it available to readers.

FFmpeg connects to the RTSP path

3\. Shinobi's Node.js camera process

Manages the monitor, launches FFmpeg, tracks progress, and serves stream endpoints.

FFmpeg — the video-processing worker

In the default HLS configuration, it reads the incoming video and copies the H.264 packets into HLS output without re-encoding them.

4A. Live view

HLS playlist and segments in RAM, delivered over HTTP to the browser.

4B. Recording

MP4 recording segments saved to persistent video storage when recording is enabled.

5\. Browser

JavaScript downloads and assembles the live stream for display in a video element.

The essential distinction is that MediaMTX transports the camera's stream, FFmpeg transforms or repackages it, and Shinobi coordinates the processes and exposes the resulting streams to users.

Those are separate responsibilities. A MediaMTX server can be running while a camera isn't publishing. An FFmpeg process can exist while no useful frames are arriving. A monitor can be configured correctly while its browser playback is delayed. Understanding those distinctions makes the rest of the document much easier.

Pasted markdown.md

## 2. First, learn the vocabulary

Before examining the commands, it helps to separate terms that sound similar but refer to different things.

### 2.1 Monitor

A monitor is Shinobi's configuration and runtime unit for one video input.

Think of it as a combination of:

- The address from which Shinobi reads the camera.
- The way it connects to that source.
- The live-view format it produces.
- Whether it records video.
- Whether detection features are enabled.
- The settings that govern process management and output.

In your example, the monitor is named `cam-gate`.

Shinobi stores its configuration in the MariaDB table `ccio.Monitors`. The document describes the table as having ordinary columns plus a large JSON `details` column containing the monitor's settings.

A monitor is therefore not the same thing as an FFmpeg process. The monitor is the logical configuration; FFmpeg is one of the processes Shinobi starts to implement it.

That distinction matters because the configuration can remain in the database even when the camera is offline or the FFmpeg process has died.

### 2.2 Monitor name versus monitor ID

Your document uses these two values:

| Field              | Value      | Purpose                   |
| ------------------ | ---------- | ------------------------- |
| Name               | `cam-gate` | Human-readable name       |
| Monitor ID (`mid`) | `cam_gate` | Machine-facing identifier |

Why not use `cam-gate` for both?

The document reports that Shinobi sanitizes monitor IDs by retaining only letters, numbers, underscores and spaces. The hyphen in `cam-gate` would be removed, producing `camgate`.

Instead, the project explicitly uses `cam_gate`, replacing the hyphen with an underscore before saving the monitor.

This is important because the monitor ID appears in paths, API calls and generated stream filenames. If different scripts use different IDs, they may address different resources or fail to find the intended monitor.

For example, the live HLS playlist is stored under a path resembling:

```
/dev/shm/streams/<ke>/cam_gate/s.m3u8
```

Here, `<ke>` is a placeholder for the account's group key. It is not the literal string you should use in a command.

### 2.3 Group key and authentication key

The document distinguishes two secrets:

- `<ke>` — the account or group key, used to identify the relevant group in paths.
- `<auth>` — a session or API key used to authenticate requests.

They serve different purposes.

A request may contain the group key in the URL path and use an authentication key to authorize the operation. Knowing a monitor's ID is not the same as being authorized to view or manage it.

The document explicitly treats both keys as secrets. They should not be copied into public logs, screenshots or documentation.

### 2.4 Input type versus stream type

This is one of the most important distinctions in the entire session.

Input type describes how FFmpeg reads the source.

Your configuration uses:

```
type = h264
```

In this installation, that type selects the network-stream input path. The document notes that the name is historical: it doesn't mean the source must exclusively use H.264; H.265 can also work through this path.

Stream type describes how Shinobi delivers the live-view output.

Your configuration uses:

```
details.stream_type = hls
```

That tells Shinobi to generate HLS output for the browser.

These settings answer different questions:

- Input type: How do I get video into FFmpeg?
- Stream type: How do I deliver video from FFmpeg to the viewer?

For example, the same RTSP camera can be read using the same input configuration while the output is changed from HLS to FLV or fragmented MP4. The source does not have to change merely because the browser-delivery format changes.

### 2.5 Mode

The monitor's mode describes what Shinobi should do with the input.

| Mode       | Configuration value | Meaning                                          |
| ---------- | ------------------- | ------------------------------------------------ |
| Disabled   | `stop`              | No running video process                         |
| Watch-Only | `start`             | Stream the input without continuous recording    |
| Record     | `record`            | Stream and record                                |
| Idle       | `idle`              | No running video process, with a distinct status |

Watch-Only does not mean that recording is impossible under every circumstance. The document explains that event-triggered or API-ordered recording can be initiated even when a monitor normally operates in Watch-Only mode.

### 2.6 Status code

Mode is the intended operating state; status is the runtime state Shinobi reports.

The document lists the following status codes:

| Code | Status     | Interpretation             |
| ---- | ---------- | -------------------------- |
| 0    | Disabled   | Disabled state             |
| 1    | Starting   | Startup in progress        |
| 2    | Watching   | Live-streaming state       |
| 3    | Recording  | Recording state            |
| 4    | Restarting | Restart in progress        |
| 5    | Stopped    | Stopped state              |
| 6    | Idle       | Idle state                 |
| 7    | Died       | Unexpected process failure |
| 8    | Stopping   | Shutdown in progress       |
| 9    | Started    | Started state              |

One subtlety: a status is not a complete diagnosis of video health. For example, a successful TCP connection to MediaMTX does not prove that the camera is publishing frames. Similarly, an FFmpeg process can remain alive while its input is frozen.

That is why the document separately examines progress monitoring and recovery behavior.

Pasted markdown.md

## 3. Adding `cam-gate`: from configuration to a database row

Your document says that the monitor was created by sending the same configuration request the browser UI would send.

The ordinary UI workflow is:

1. Open the Shinobi dashboard.
2. Add a monitor.
3. Enter its settings.
4. Save it.

Underneath, the browser sends a request resembling:

```
POST /<auth>/configureMonitor/<ke>/cam_gate
```

The request body contains the monitor's JSON configuration.

The script used in your session followed the equivalent sequence: log in, obtain default settings, apply the required overrides, and submit the configuration.

This is useful because it means the configuration process can eventually be automated without manually clicking through the dashboard.

### 3.1 Why start with defaults?

Shinobi has a function named `generateDefaultMonitorSettings()`.

Rather than manually constructing every setting, the script begins with the application's defaults and changes only the fields the project needs.

That approach has several benefits:

- It avoids accidentally omitting settings Shinobi expects.
- It keeps the configuration aligned with the version of Shinobi being used.
- It reduces the number of fields the provisioning script must manage.
- It makes it easier to compare intended settings against the defaults.

There is a trade-off: defaults can change between software versions. A provisioning script should therefore be tested against the specific Shinobi version deployed in the project.

### 3.2 The monitor's configuration

These are the fields your document says were explicitly configured.

| UI setting         | JSON field                                      | Value                                    | Why it matters                                      |
| ------------------ | ----------------------------------------------- | ---------------------------------------- | --------------------------------------------------- |
| Name               | `name`                                          | `cam-gate`                               | Human-readable camera name                          |
| Monitor ID         | `mid`                                           | `cam_gate`                               | Stable machine identifier                           |
| Mode               | `mode`                                          | `start`                                  | Watch-Only operation                                |
| Input type         | `type`                                          | `h264`                                   | Selects the network-stream input path               |
| Automatic/full URL | `details.auto_host_enable`, `details.auto_host` | Enabled; `rtsp://mediamtx:8554/cam-gate` | Defines the source URL                              |
| RTSP transport     | `details.rtsp_transport`                        | `tcp`                                    | Selects TCP transport                               |
| Width              | `width`                                         | `1280`                                   | Descriptive unless a relevant encoding path uses it |
| Height             | `height`                                        | `720`                                    | Descriptive unless a relevant encoding path uses it |
| FPS                | `fps`                                           | `15`                                     | Not a general command-wide frame-rate limiter       |
| Stream type        | `details.stream_type`                           | `hls`                                    | Selects HLS live view                               |
| Stream video codec | `details.stream_vcodec`                         | `copy`                                   | Copies the compressed video                         |
| Detector           | `details.detector`                              | `0`                                      | Detector disabled in this configuration             |

The values `1280`, `720` and `15` deserve special attention.

It is tempting to assume that setting width, height and FPS automatically forces every output to those dimensions and that frame rate. But your document reports that, for this particular command, these fields do not impose that kind of general restriction.

In particular, the monitor's FPS setting is used by the relevant recording re-encoding path, rather than acting as a universal limiter for stream-copy output.

Configuration fields only have an effect when the code that builds or executes the relevant output actually uses them.

### 3.3 Why `mediamtx` instead of `localhost`?

The source URL is:

```
rtsp://mediamtx:8554/cam-gate
```

Here is what each part means:

- `rtsp://` — the Real-Time Streaming Protocol.
- `mediamtx` — the hostname of the MediaMTX service.
- `8554` — the RTSP listening port.
- `/cam-gate` — the path identifying the published stream.

The hostname `mediamtx` works because Docker Compose provides service-name DNS on the relevant Docker network.

Inside the Shinobi container, `localhost` means the Shinobi container itself. It does not mean the MediaMTX container, the host machine or the simulated camera.

This is a common container-networking mistake: a hostname that works on the host is not necessarily the hostname another container should use.

### 3.4 What the successful response means

The request returns a response resembling:

```
{
  "ok": true,
  "msg": "Monitor Added by user."
}
```

This indicates that the monitor-add operation succeeded.

It does not, by itself, prove every part of the video pipeline is working. Database configuration, process startup, input connectivity and browser playback are separate things to verify.

The document reports that the monitor configuration lives in the `shinobi-db` volume and survives the project's `sim-down` and `sim-up` operations.

That persistence is distinct from the live stream files, which are kept in RAM and disappear on restart.

Pasted markdown.md

## 4. What happens after you click Save?

Saving the configuration triggers a chain of operations inside Shinobi. This is where the configuration becomes a running system.

### 4.1 Step one: authenticate the request

The Node.js server handles the configuration request through `camera.js`.

It checks whether `<auth>` represents a valid session associated with the requested group.

This is a security boundary. The monitor ID and group key identify the target, but authentication determines whether the request is authorized.

### 4.2 Step two: clean and parse the configuration

The request passes through `cleanStringsInObject()`.

According to the document, this function sanitizes incoming values and attempts to parse every string as JSON.

That sounds harmless, but it has an important consequence: values that look like JSON primitives can change type.

For example:

```
Incoming JSON value:  "0"     (string)
Stored JSON value:     0      (number)
```

We'll examine this in detail later, because it affects automated provisioning.

### 4.3 Step three: inspect the input

Shinobi calls `getWarningChangesForMonitor()` to probe the source and suggest fixes where appropriate.

The document reports that no changes were suggested for this camera.

This is different from the TCP connection test that occurs during process startup. A probe examines stream characteristics; a TCP test merely checks whether a network port accepts a connection.

Neither check alone guarantees healthy, ongoing video delivery.

### 4.4 Step four: save the configuration

Shinobi inserts or updates a row in:

```
ccio.Monitors
```

The database now contains the monitor's settings.

At this point, the monitor's desired configuration exists independently of whether the FFmpeg process is currently alive.

### 4.5 Step five: restart an already-running monitor

If the monitor was already running and you edit its settings, the document reports that Shinobi stops it before starting it again.

The sequence is approximately:

```
Existing process
      |
      v
Stop monitor
      |
      v
Save updated configuration
      |
      v
Build a new FFmpeg command
      |
      v
Launch a new process
      |
      v
Resume live streaming
```

In the observed setup, the interruption was approximately three seconds.

This explains why even a small configuration change can interrupt a live view. Shinobi generally rebuilds the running pipeline rather than changing every FFmpeg option in place.

The document notes an exception for certain detection settings, which can be pushed into an existing camera thread without a full restart.

### 4.6 Step six: test the TCP port

Before launching FFmpeg, Shinobi tests connectivity to:

```
mediamtx:8554
```

The test has a two-second timeout.

This answers a narrow question:

> Can Shinobi establish a TCP connection to the MediaMTX listening port?

It does not answer:

- Is the camera publishing?
- Does `/cam-gate` exist?
- Is the stream producing frames?
- Are the timestamps usable?
- Can the browser play the output?

A network server can be listening successfully even when its upstream camera is disconnected.

### 4.7 Step seven: construct the FFmpeg command

Shinobi builds the command from configuration-dependent components in `libs/ffmpeg/builders.js`.

The document identifies these builder functions:

- `buildMainInput()`
- `buildMainStream()`
- `buildJpegApiOutput()`
- `buildMainRecording()`
- `buildAudioDetector()`
- `buildMainDetector()`
- `buildEventRecordingOutput()`
- `buildTimelapseOutput()`

Empty components are omitted.

This modular structure explains why the command changes when you switch the mode, live-view type or detection settings. The final command is not a single fixed string; it is assembled from the applicable pieces.

### 4.8 Step eight: pass the command to a helper process

Shinobi writes a temporary file resembling:

```
/dev/shm/streams/<ke>/cam_gate/cmd_<token>.txt
```

The document reports that this file contains the argument array, monitor configuration and the whole `conf.json`.

A Node.js helper, `singleCamera.js`, receives the temporary file and launches FFmpeg.

The helper also manages pipes, forwards output and handles process termination. If FFmpeg exits, the helper exits too, allowing the parent camera-management process to detect the failure.

This extra process is useful because the camera-management code doesn't need to perform all stream handling and process supervision directly inside one long-running process.

Pasted markdown.md

## 5. The process tree: who owns whom?

The observed process hierarchy looks like this:

```
PM2 / Shinobi process manager
└── camera.js
    └── singleCamera.js
        └── ffmpeg
```

Each level has a different responsibility.

| Process               | Responsibility                                               |
| --------------------- | ------------------------------------------------------------ |
| PM2 / process manager | Keeps the Shinobi application process running                |
| `camera.js`           | Manages monitor state and starts or stops monitor pipelines  |
| `singleCamera.js`     | Owns the FFmpeg child, handles pipes and supervises its exit |
| FFmpeg                | Reads, packages and outputs the video stream                 |

### 5.1 Why use a separate camera thread?

The helper opens the numbered file descriptors, forwards data, and hosts the built-in motion detector when enabled.

The document reports that a `SIGTERM` received by the helper leads to termination of FFmpeg's whole process group using a negative process ID.

The practical goal is to prevent an orphaned FFmpeg process from continuing to run after its monitor is stopped.

When FFmpeg exits, the helper exits as well. The parent process can then update the monitor status and apply the restart policy.

### 5.2 Resource use

The process snapshot in your document reports roughly 100 MB of resident memory for one copy-mode monitor across the helper and FFmpeg processes, with a few percent of one CPU core used in the observed environment.

These figures are measurements from the specific test setup, not universal resource requirements.

CPU and memory usage can change with:

- Video resolution and frame rate.
- Codec and compression settings.
- Whether the output is copied or re-encoded.
- Whether detection or snapshots are enabled.
- How many viewers consume the stream.
- FFmpeg version and implementation details.

The important architectural result is that stream copy avoids the major computational cost of decoding and re-encoding the main video.

## 6. File descriptors: how video and status information travel between processes

File descriptors are integer handles a process uses to access open files, sockets and pipes.

You don't need to memorize every descriptor number, but understanding the arrangement makes the FFmpeg command much less mysterious.

Your document says Shinobi creates ten numbered pipe channels through its `pipeAddition` configuration.

The relevant assignments are:

| Descriptor   | Purpose in the documented configuration            |
| ------------ | -------------------------------------------------- |
| 0            | Standard input; a pipe from the helper             |
| 1            | Standard output; used for some live-stream formats |
| 2            | Standard error; FFmpeg logs                        |
| 3            | Grayscale PAM frames for built-in motion detection |
| 4            | JPEG frames for object-detection plugins           |
| 5            | FFmpeg progress information                        |
| 6            | PCM audio for audio detection                      |
| 7            | Timelapse JPEG frames                              |
| 8            | Recording-segment completion notifications         |
| 9            | Unused in this example                             |
| 10 and above | Additional channels and FFmpeg's own sockets       |

The exact channels used depend on which features are enabled.

### 6.1 Why is `pipe:5` important?

The command includes:

```
-progress pipe:5
```

This instructs FFmpeg to write progress information to file descriptor 5.

The output consists of lines such as:

```
out_time_us=1933333
out_time=00:00:01.933333
progress=continue
```

Shinobi monitors this channel to determine whether the FFmpeg pipeline continues making progress.

The document reports that the watchdog resets a 60-second timer whenever data arrives on the progress pipe.

Notice the distinction: the watchdog doesn't necessarily need to understand every field in the progress report. In the described implementation, receiving data is itself evidence of activity.

That is useful because a process can exist without actually advancing through a video stream.

### 6.2 How can we tell which network connection is FFmpeg's?

The document inspects `/proc/<pid>/fd` and `/proc/<pid>/net/tcp`.

It identifies the RTSP connection from FFmpeg to MediaMTX using the destination port `8554`.

This is a practical debugging technique:

- The process tree tells you which process is running.
- The file descriptors tell you which resources it has opened.
- The TCP table helps identify network connections.
- The command line tells you which input and outputs the process was configured to use.

Taken together, these observations provide stronger evidence than simply checking whether a container is running.

Pasted markdown.md

## 7. The generated FFmpeg command, explained from beginning to end

This is the core technical section.

Your document records a command of this general form:

```
/usr/bin/ffmpeg \
  -progress pipe:5 \
  -use_wallclock_as_timestamps 1 \
  -analyzeduration 1000000000 \
  -probesize 1000000000 \
  -fflags +igndts \
  -rtsp_transport tcp \
  -loglevel warning \
  -i rtsp://mediamtx:8554/cam-gate \
  -strict -2 \
  -fflags +genpts+igndts \
  -muxdelay 0 \
  -muxpreload 0 \
  -an \
  -c:v copy \
  -f hls \
  -hls_time 2 \
  -hls_list_size 3 \
  -start_number 0 \
  -hls_allow_cache 0 \
  -hls_flags +delete_segments+omit_endlist+discont_start \
  /dev/shm/streams/<ke>/cam_gate/s.m3u8
```

This is a representative command based on the documented configuration; `<ke>` remains a placeholder.

A key FFmpeg rule is that options are position-sensitive. Options before `-i` generally configure the input, while options placed before an output filename generally configure that output. Some options are global.

One input can feed multiple outputs. Record mode, for example, adds an MP4 recording output to the existing live-stream output.

Let's go through the command in functional groups.

### 7.1 `-progress pipe:5`

```
-progress pipe:5
```

This sends progress reports to the pipe monitored by Shinobi.

Its primary purpose here is operational rather than visual: it allows Shinobi to detect that the FFmpeg pipeline is still producing progress information.

Without a mechanism like this, an FFmpeg process that remains alive but stops receiving useful input could appear healthy indefinitely.

### 7.2 `-use_wallclock_as_timestamps 1`

```
-use_wallclock_as_timestamps 1
```

This tells FFmpeg to use packet arrival time from the server's wall clock as input timestamps, rather than simply trusting the source's original packet timing.

Why would an NVR want to do that?

Real cameras can have imperfect timestamps. A camera may restart its timestamp sequence, produce discontinuities after a reboot or use a clock that doesn't align with the server's clock.

Using arrival times can help align captured events with the NVR's timeline.

However, it introduces a trade-off: network delay and scheduling jitter become part of the timestamp sequence.

Your document measures this effect:

| Measurement                                      | Result                             |
| ------------------------------------------------ | ---------------------------------- |
| Nominal frame interval at 15 FPS                 | About 66.67 ms                     |
| Source timestamps                                | Regular, 66.7 ms steps             |
| HLS timestamps with wall-clock stamping enabled  | Steps ranging from 39.2 to 89.7 ms |
| HLS timestamps with wall-clock stamping disabled | Regular, 66.7 ms steps             |

The experiment therefore demonstrates that, for this simulated camera, wall-clock timestamping introduces timing variation that wasn't present in the source.

It also explains why a stream-probing tool can estimate 30 FPS even though counting the frames shows 15 FPS. A frame-rate estimate derived from a jittered timeline can be misleading.

Important qualification: these results describe the tested streams. They do not establish that wall-clock stamping is always wrong. The document explicitly leaves the choice open for the later recording session, where timestamp accuracy and event alignment must be considered together.

### 7.3 `-analyzeduration` and `-probesize`

```
-analyzeduration 1000000000
-probesize 1000000000
```

These options give FFmpeg large upper limits for inspecting the input before deciding what streams it contains.

The units differ:

- `-analyzeduration` is measured in microseconds.
- `-probesize` is measured in bytes.

The configured values correspond to an analysis-duration limit of 1,000 seconds and a probe-size limit of one billion bytes.

These are upper bounds, not amounts FFmpeg must always consume. FFmpeg can stop probing as soon as it has enough information.

Why use large limits? Some sources advertise stream information incompletely or require more data before their format can be identified.

Why can this be harmful? If a source advertises a track that never supplies the expected data, FFmpeg may spend a long time probing before producing usable output.

Your document reports that startup time in the tested setup was around four seconds with the larger values, compared with a range of 2.1–4.1 seconds using defaults. It found no clear benefit for the simulated source beyond normal variation in when a usable keyframe arrived.

### 7.4 `-fflags +igndts`

```
-fflags +igndts
```

This is an input-side flag intended to ignore decode timestamps when presentation timestamps are available.

The distinction between DTS and PTS is useful:

- DTS — Decoding Timestamp: when a compressed frame needs to be decoded.
- PTS — Presentation Timestamp: when the frame should be presented.

With video containing B-frames, decoding and presentation order can differ.

In the documented simulated stream, there are no B-frames, so the timestamps are effectively the same in this respect. The flag is consequently harmless for that particular stream.

The flag exists to improve resilience to sources whose decode timestamps are unreliable.

### 7.5 `-rtsp_transport tcp`

```
-rtsp_transport tcp
```

RTSP is used to establish and control the streaming session. The media itself is commonly transported using RTP, which can use UDP or be interleaved over the RTSP TCP connection.

This setting selects TCP transport for the RTSP input.

Why does the project choose it?

TCP is often easier to operate through container networks and firewalls because it uses the established connection rather than requiring separate UDP media ports.

The trade-off is that TCP retransmissions and head-of-line blocking can increase latency when packets are lost. TCP improves delivery reliability; it does not guarantee that the video will be perfectly timely.

For this project, the document says RTSP-over-TCP is a project rule and the default in the tested UI configuration.

### 7.6 `-loglevel warning`

```
-loglevel warning
```

This controls the severity of FFmpeg messages that appear on its logging output.

At this level, warnings and errors are reported while ordinary informational messages are suppressed.

That keeps logs more manageable, but it also means some useful diagnostic details may not appear. If you are debugging a difficult stream problem, temporarily using a more verbose logging level may reveal what is happening.

### 7.7 `-i rtsp://mediamtx:8554/cam-gate`

```
-i rtsp://mediamtx:8554/cam-gate
```

The `-i` option introduces the input source.

In this case, FFmpeg connects to the RTSP path served by MediaMTX.

Shinobi assembles this URL from the monitor's protocol, hostname, port and path fields, unless the full-URL setting supplies it directly.

The document also notes that the logged command is sanitized to remove credentials where applicable.

That is important for security: FFmpeg may need credentials to connect, but log output should not unnecessarily expose them.

Pasted markdown.md

## 8. Output-side flags: how the incoming stream becomes browser video

The input options tell FFmpeg how to read the camera. The output options tell it how to package and deliver what it reads.

### 8.1 `-strict -2`

```
-strict -2
```

This is a legacy compatibility option associated with FFmpeg's experimental-codec handling. It was historically relevant to certain encoders, including earlier versions of the native AAC encoder.

In the documented configuration, the video is copied rather than re-encoded, and audio output is disabled. Therefore, this option has no meaningful effect on the principal video-copy operation.

The lesson is that a generated command can contain options inherited from a general-purpose builder even when a particular monitor doesn't need every one of them.

### 8.2 `-fflags +genpts+igndts`

```
-fflags +genpts+igndts
```

This flag appears again on the output side.

The document's author suspects that these flags are read on the demuxing side and therefore have no useful effect when attached to the output in this context. However, the note explicitly says this was not experimentally verified.

So the correct interpretation is:

- The flag is present in the generated command.
- The project has a reason to question its usefulness in that position.
- Its lack of effect is a source-based hypothesis, not a confirmed result of the reported experiment.

This is an example of an important engineering habit: distinguish observed behavior from an explanation that still needs testing.

### 8.3 `-muxdelay 0 -muxpreload 0`

```
-muxdelay 0
-muxpreload 0
```

These options configure muxing behavior.

A muxer combines encoded audio and video streams into a container format. Here, FFmpeg is producing MPEG-TS segments for HLS.

The documented intention is to avoid additional startup delay associated with muxing and buffering.

Setting these values to zero does not eliminate all latency. The camera's encoding interval, network transport, HLS segment creation, HTTP delivery and browser buffering still take time.

### 8.4 `-an`

```
-an
```

This disables audio output.

Even if the source contains audio, FFmpeg does not include an audio stream in this output.

That matches the documented monitor configuration, where audio output is disabled.

It also reduces unnecessary work if audio is not required for live view.

### 8.5 `-c:v copy`

```
-c:v copy
```

This is one of the most consequential flags.

The `-c:v` option selects the video codec or processing method. `copy` means stream copy: FFmpeg does not decode the compressed video into raw frames and then encode those frames again for this output.

Instead, it passes the compressed video packets through into the output container.

The difference is substantial.

### Stream copy versus re-encoding

Stream copy — the configured HLS path

Compressed H.264 packets

Repackage into MPEG-TS segments

HLS output with the original compressed video

Re-encoding — a more computationally expensive path

Compressed H.264 packets

Decode into raw frames

Optionally resize, filter or change frame rate

Encode a new compressed video stream

Stream copy is efficient because it avoids the decode-and-encode cycle. But it also means Shinobi generally cannot change the video's resolution, frame rate or compression settings through that output path.

Those properties remain determined by the source encoding.

Your document reports that the output preserved the simulated camera's H.264 Main profile, 1280 × 720 resolution, YUV 4:2:0 pixel format and two-second GOP structure.

A GOP, or group of pictures, is a sequence of encoded frames organized around keyframes. It matters greatly for HLS segmentation.

### 8.6 `-f hls`

```
-f hls
```

This selects FFmpeg's HLS muxer.

HLS stands for HTTP Live Streaming. Rather than creating one ordinary video file that grows continuously, FFmpeg generates:

- A playlist file, ending in `.m3u8`.
- A series of media-segment files, ending in `.ts` in this configuration.

The playlist tells the player which media segments are available and how long they last.

The player downloads the playlist and its referenced segments, then presents the resulting media as a continuous stream.

### 8.7 `-hls_time 2`

```
-hls_time 2
```

This sets a target segment duration of approximately two seconds.

A common misunderstanding is that this guarantees each segment will be exactly two seconds long.

With stream copy, FFmpeg cannot freely cut the compressed video at arbitrary points if each segment must start at a usable keyframe. It generally waits for an appropriate keyframe boundary.

Your simulated camera produces a keyframe every 30 frames. At 15 frames per second:

\\[ \frac{30\text{ frames}}{15\text{ frames/second}}=2\text{ seconds} \\]

That makes the target segment duration and GOP duration line up well.

The result in your document is segments lasting approximately two seconds.

If the camera instead produces a keyframe only every four seconds, the segmenter may have to wait for that keyframe. The output segments can then be much longer than the configured target.

For stream-copy HLS, camera GOP settings are therefore an important determinant of segment duration and live-view latency.

### 8.8 `-hls_list_size 3`

```
-hls_list_size 3
```

This sets the playlist to list only the three most recent segments.

Suppose the current segment sequence is:

```
s41.ts
s42.ts
s43.ts
```

As new segments appear, the oldest entry drops out of the playlist.

This keeps the live playlist short. It does not mean that only three files must physically exist at every instant, because file deletion and active client downloads also matter.

### 8.9 `-start_number 0`

```
-start_number 0
```

This configures the initial segment number.

In a fresh sequence, the first segment is named `s0.ts`.

The actual segment numbers observed later in the session are higher because the stream has been running and producing additional segments.

### 8.10 `-hls_allow_cache 0`

```
-hls_allow_cache 0
```

In the tested FFmpeg version, this sets the corresponding HLS playlist cache directive to disallow caching.

The document also reports that Shinobi serves no-cache HTTP headers.

The intent is to prevent a live viewer from receiving stale playlist or segment information when fresh data is required. Exact caching behavior still depends on the client and any intermediate HTTP infrastructure.

### 8.11 The three HLS flags

```
-hls_flags +delete_segments+omit_endlist+discont_start
```

These flags control different aspects of playlist and segment lifecycle.

`delete_segments` — remove old segments

When segments fall out of the playlist, FFmpeg deletes them after the relevant retention window. The document reports that one extra segment is kept, helping avoid a situation where a player is still downloading a segment that has just been deleted.

`omit_endlist` — retain live-playlist semantics

FFmpeg does not append `#EXT-X-ENDLIST` when it finishes. That tag normally indicates that the playlist has reached its end. Omitting it helps a player treat the playlist as a live stream rather than a completed presentation.

This does not keep FFmpeg running. It only changes the playlist's signaling.

`discont_start` — mark a discontinuity

FFmpeg marks the beginning of the sequence with `#EXT-X-DISCONTINUITY`. This signals that the stream's timeline may have restarted or changed discontinuously.

It can be useful when playback resumes with timestamps that do not continue the preceding sequence.

### 8.12 The final output path

The command ends with a path resembling:

```
/dev/shm/streams/<ke>/cam_gate/s.m3u8

#EXTM3U
#EXT-X-VERSION:3
#EXT-X-ALLOW-CACHE:NO
#EXT-X-TARGETDURATION:3
#EXT-X-MEDIA-SEQUENCE:4
#EXTINF:2.693967,
s4.ts
#EXTINF:2.517611,
s5.ts
#EXTINF:2.415678,
s6.ts

```

The `.m3u8` file is the playlist. The associated `.ts` files contain the actual media segments.

The directory lives in a RAM-backed filesystem, or tmpfs.

This is intentional: live-view segments are temporary working data, not the permanent recording archive.

The document describes a 512 MB RAM allocation for the relevant tmpfs in this setup. When the container or relevant RAM filesystem is restarted, the disposable live-stream files are wiped.

That separation is fundamental:

- Live streaming needs fresh, short-lived segments.
- Recording needs persistent video files that survive restarts.

Pasted markdown.md

## 9. Why HLS playback is several seconds behind the camera

Your document reports that a frame showing the camera's on-screen clock was approximately seven seconds behind the host clock.

That delay is not necessarily evidence of a broken stream. HLS introduces buffering because it is built around downloading completed segments.

### 9.1 The timeline

The path from camera to browser looks like this:

1. The camera produces a frame.
2. The frame travels to MediaMTX.
3. FFmpeg reads the stream.
4. FFmpeg writes a segment containing the frame.
5. The segment becomes available to the player through the playlist.
6. The browser downloads the segment.
7. The player buffers and displays it.

Each stage can add some delay.

With two-second segments, FFmpeg may need to wait for a segment to finish before the relevant data is fully available. The playlist contains only a small sliding window of recent segments, but the player may deliberately begin playback behind the live edge.

### 9.2 Why three listed segments matter

The document gives the following approximate relationship:

\\[ \text{Playlist-window delay}\approx (\text{list size}-1)\times\text{segment duration} \\]

For the documented settings:

\\[ (3-1)\times2=4\text{ seconds} \\]

This is an estimate of the playlist-window contribution under the behavior described in the note, not a universal formula for total HLS latency.

The full delay also includes segment availability, network transfer, browser buffering and player-specific choices.

The measured seven-second delay is consistent with a pipeline that has several seconds of built-in buffering.

### 9.3 How can the delay be reduced?

The document suggests three broad options:

- Reduce the HLS playlist length.
- Use a lower-latency output such as FLV or Poseidon, where supported.
- Use WebRTC through MediaMTX outside the described Shinobi live-view path.

Each choice has trade-offs. A shorter HLS playlist can reduce buffering, but a player may become more sensitive to network jitter. A different stream format may reduce latency but require different browser-side handling.

The key lesson is that stream latency is a property of the entire pipeline, not just the camera.

Pasted markdown.md

## 10. The other live-view formats: HLS, FLV, Poseidon and MJPEG

Your document tests several output formats using the same monitor, changing only `details.stream_type`.

The source remains the same RTSP stream. What changes is the output generated by FFmpeg and the way Shinobi serves it to the browser.

[HLS Video Streaming, Encryption & Player | Publitio](https://images.openai.com/static-rsc-4/Mx7YLfFMTRUBAfAFqWRqehRnmkiuuNYexWqdocUUQW_XosDIHw3_etnEoU0DRkgDNCWk9aLU9EML0XQEsJdCf3bokxOuI3zoBXNNhVXCAe1YGElsle4kNgiRgA6Gbr2i13Mo495pB4Qfmbi1wyyZOYZUWS_yZjloOSiOuPVeMPs?purpose=inline)

HLS — HTTP Live Streaming

Default in this setup

FFmpeg writes a playlist and MPEG-TS segment files to tmpfs. The browser downloads them over HTTP, with hls.js used when native HLS support is unavailable.

Advantages: simple segment-based delivery, familiar browser workflow and efficient stream copy. Main trade-off: several seconds of latency in the tested setup.

[FLV格式解析 | 老司机种菜](https://images.openai.com/static-rsc-4/IA26p8wrfVDoLIeA4wxT3NlAMUfMALyt2ks29nZbQwJWy3ZD4a3BFTKu6L_jDG9ZHkMLijC9WRkH2JDxoCa6GxiZZ0pz8eZnOy_uOTF3CqhC9FwL1joJ4WCK-96JIPg1AR6sy-P4qRYczZsM-2ndygzWxTyuO-keSkWuDvqUhdk?purpose=inline)

FLV — Flash Video container

FFmpeg writes a continuous stream to `pipe:1`, and Shinobi distributes it to viewers through an HTTP or WebSocket endpoint. The browser uses flv.js in the described setup.

The document estimates roughly one-second latency in its test environment. This is not a guarantee for every client or network.

[\[MPEG-DASH\] Fragmented MP4のファイル構造 #初心者向け - Qiita](https://images.openai.com/static-rsc-4/lfb2uYy-aKwgayMxFLakEXNOFlYKtwWTz_cjLSLhWudsX1R2EgmxoEeIVHaFCEEWof4lKfOsC-SMRVP_lonF1h-WvRlJk9Cvo-SF6Qmbv6kykj7N1HpeBntzbk1_NvV5_8mM2nkU5caWyVn9owL0I1En89XcceMlXTZQ-0d6c2M?purpose=inline)

Poseidon — fragmented MP4

The output uses fragmented MP4 and is delivered through a stream endpoint. The browser plays it using Media Source Extensions (MSE).

The document describes latency on the order of a GOP. Fragmented MP4 can begin playback without waiting for an ordinary complete MP4 file.

[GitHub - vascojdb/pizero-zoneminder-cam: Create your very low cost ZoneMinder camera using a Raspberry Pi Zero, MotionEyeOS and a RPi Camera · GitHub](https://images.openai.com/static-rsc-4/gCZPfO_MCfhmVFi7Otux-eyjWczatDfIOqE2zFEDyl7gPwUthfw1Pxbf7PgA2IM4O0UYgTt_TDyov49PSxAdBznBHkSWWqxk9nb3cm1Pch2KRFKtfk-YTSKo2RZBiO8spXnatO-OfzmlAE8wtSgmCBYrvD8rL2MvNGIGV7BgkHU?purpose=inline)

MJPEG — Motion JPEG

FFmpeg decodes the compressed source and encodes individual frames as JPEG images. Shinobi sends them as a multipart HTTP response.

The tested configuration outputs two frames per second. It is easy to display in many clients, but decoding and re-encoding every frame consumes substantially more CPU.

### 10.1 Why is MJPEG more expensive?

The other three output paths can copy the compressed video packets. MJPEG cannot simply pass the H.264 packets through and call them JPEG images.

It must:

1. Decode the incoming H.264 stream into frames.
2. Select frames at the configured rate.
3. Encode the selected frames as JPEG.
4. Send the images as a continuous multipart stream.

The document's measured resource snapshots were approximately:

| Output type | CPU usage | Resident memory |
| ----------- | --------- | --------------- |
| HLS         | 3%        | 46 MB           |
| FLV         | 3%        | 46 MB           |
| Poseidon    | 5%        | 47 MB           |
| MJPEG       | 17%       | 138 MB          |

These are approximate readings from the documented test, taken a few seconds after startup. They are not capacity-planning guarantees.

The MJPEG result illustrates the computational cost of decoding and encoding. With multiple cameras, that extra cost can accumulate even when no one is watching the feeds.

### 10.2 When would each format make sense?

- HLS: a practical default when a few seconds of delay is acceptable.
- FLV: potentially useful when low latency matters and the client stack supports the required delivery mechanism.
- Poseidon: useful for fragmented-MP4 playback through MSE.
- MJPEG: useful for simple frame-oriented displays or compatibility needs, provided the processing cost is acceptable.

For the project described in your document, HLS with stream copy is the sensible baseline for ordinary gate monitoring.

One caution: the labels and client libraries here describe the documented Shinobi implementation. Browser support and library compatibility can change over time, so they should be checked against the deployed version before making a production design decision.

Pasted markdown.md

## 11. Recording: what changes when mode becomes `record`?

One of the most useful architectural facts in your document is that Shinobi does not need a second FFmpeg process just to record the same input.

In the tested configuration, the same FFmpeg process produces both:

- The live-view HLS output.
- The recording MP4 output.

This is possible because one FFmpeg input can feed multiple output destinations.

### 11.1 The additional recording output

The document gives this representative output configuration:

```
-an \
-vcodec copy \
-strict -2 \
-segment_format_options movflags=faststart \
-fflags +genpts+igndts \
-muxdelay 0 \
-muxpreload 0 \
-f segment \
-segment_atclocktime 1 \
-reset_timestamps 1 \
-strftime 1 \
-segment_list pipe:8 \
-segment_time 900 \
"/home/Shinobi/videos/<ke>/cam_gate/%Y-%m-%dT%H-%M-%S.mp4"
```

Let's examine the recording-specific options.

### 11.2 `-f segment`

```
-f segment
```

This selects FFmpeg's segment muxer.

Instead of producing one indefinitely growing file, FFmpeg creates multiple files over time.

This is useful for surveillance because long-running recording files are harder to manage, index, transfer and delete selectively.

### 11.3 `-segment_time 900`

```
-segment_time 900
```

The target duration is 900 seconds:

\\[ 900\div60=15\text{ minutes} \\]

The document reports that the application clamps the recording cutoff to a range of one to fifteen minutes.

Shorter files can be easier to retrieve and manage. They also limit how much video is tied to a single file if a file becomes corrupted or an operation interrupts the recording.

The segment duration is still subject to the muxer's segmentation behavior and the available frame boundaries.

### 11.4 `-segment_atclocktime 1`

```
-segment_atclocktime 1
```

This aligns segment boundaries with wall-clock time.

Rather than simply starting a new file 900 seconds after the previous one started, the intent is to split at clock-aligned boundaries, such as:

```
12:00
12:15
12:30
12:45
13:00
```

This makes it easier to relate filenames and recording segments to real-world event times.

It also means that the first segment can be shorter than the nominal duration if recording begins partway through an interval.

### 11.5 `-reset_timestamps 1`

```
-reset_timestamps 1
```

This resets timestamps for each output segment.

The goal is for each MP4 file to have a timeline that starts near zero instead of inheriting a timestamp that continues from the start of the entire session.

That can make individual files easier to play independently.

It does not, by itself, guarantee that all wall-clock timestamp problems are solved. Timestamp behavior depends on the input, the other FFmpeg options and the output muxer.

### 11.6 `-strftime 1`

```
-strftime 1
```

This tells FFmpeg to interpret time-formatting directives in the filename pattern.

The pattern in your document is:

```
%Y-%m-%dT%H-%M-%S.mp4
```

That produces filenames resembling:

```
2026-10-17T18-53-59.mp4
```

The filename records the date and time used when the segment is created.

This makes files easier to locate by time, although filenames alone do not replace a database index or accurate media timestamps.

### 11.7 `-segment_list pipe:8`

```
-segment_list pipe:8
```

When a recording segment finishes, FFmpeg reports the event through file descriptor 8.

Shinobi can consume this notification and update its video index.

This is a different use of a pipe from `pipe:5`:

- `pipe:5` carries progress reports used for monitoring.
- `pipe:8` communicates recording-segment information.

The distinction is important because recording has a lifecycle beyond simply writing bytes. Shinobi also needs to know which files exist and when segments are ready to be indexed.

### 11.8 `movflags=faststart`

```
-segment_format_options movflags=faststart
```

This requests the MP4 `faststart` behavior.

Ordinary MP4 files contain metadata describing their tracks and samples. The `moov` atom holds important indexing information. When this metadata is placed at the end of a file, a player may need to obtain more of the file before playback can begin.

`faststart` moves the relevant index metadata toward the beginning of the MP4.

The goal is to make playback start sooner when the file is accessed over HTTP or by a browser.

### 11.9 Where recordings are stored

The document reports that recording files live under a path resembling:

```
/home/Shinobi/videos/<ke>/cam_gate/
```

This is backed by the persistent `shinobi-videos` volume in the documented setup.

That is different from the live-view HLS path under `/dev/shm/streams/`.

| Property         | Live HLS                                            | Recording MP4                                           |
| ---------------- | --------------------------------------------------- | ------------------------------------------------------- |
| Purpose          | Current browser playback                            | Persistent video archive                                |
| Storage          | RAM-backed tmpfs                                    | Persistent video volume                                 |
| File lifecycle   | Old segments continuously discarded                 | Files retained according to recording-management policy |
| Main format      | Playlist plus MPEG-TS segments                      | MP4 segments                                            |
| Typical duration | Approximately two seconds per segment in this setup | Target of fifteen minutes per file                      |

The recording directory may contain a file only a few seconds after recording begins, as the document reports. Retention and long-term archive management are deferred to the next session.

### 11.10 Does recording substantially increase CPU usage?

In this setup, recording also uses stream copy.

That means FFmpeg does not need to decode and re-encode the main video simply because a recording output has been added.

The extra work consists mainly of managing another output and writing the compressed packets into MP4 segments.

This is why the documented design can support live view and recording in the same FFmpeg process without the cost of a full video transcode.

Pasted markdown.md

## 12. The modes, explained as a state machine

The four modes are easier to understand when you consider both the requested behavior and the resulting process state.

Disabled — `stop`

No FFmpeg process; status 5, Stopped.

Enable or disable monitor

Watch-Only — `start`

FFmpeg runs the input and live-view output; status 2, Watching.

Start or stop recording

Record — `record`

One FFmpeg process supplies live view and recording; status 3, Recording.

Idle — `idle`

No FFmpeg process; status 6, Idle.

The diagram is conceptual: it summarizes the operational states rather than asserting that every transition occurs through a single UI action.

### 12.1 Why are Disabled and Idle separate?

The document examines the relevant monitor-management code and reports that both `stop` and `idle` follow a stop path.

The observed difference is primarily the state and log message:

- `stop` reports Stopped.
- `idle` reports Idle and logs `Monitor Idling`.

The author did not find another behavioral difference in the inspected source files.

That is a source-inspection result, not a claim that no version of Shinobi could ever distinguish them elsewhere.

### 12.2 Why does Watch-Only still matter if recording can be triggered?

Watch-Only is useful when you want a stream continuously available without continuously recording it.

Later, detection logic or an API request can trigger recording when needed.

This separates the baseline behavior from event-driven behavior:

- Baseline: keep the monitor available for live viewing.
- Event: initiate recording when a relevant condition occurs.
- After the event: return to the intended operating behavior.

The document reserves the detailed recording and detection workflows for later sessions.

### 12.3 Why do configuration edits interrupt video?

As discussed earlier, changing a running monitor's settings normally leads to a stop-and-start cycle.

That means a configuration edit is also a runtime event.

For a production installation, it is worth planning around that behavior:

- Provision multiple monitors in a controlled batch.
- Avoid unnecessary edits during operationally sensitive periods.
- Distinguish live configuration changes from changes that can be pushed dynamically.

This is particularly relevant for a gate-monitoring system, where a brief interruption may matter even if the new configuration is otherwise correct.

Pasted markdown.md

## 13. The subtle database problem: strings turn into numbers

This section matters especially if you plan to automate monitor configuration in Python.

The document reports that Shinobi's configuration-cleaning function attempts to parse incoming strings as JSON.

Consider a field sent as:

```
{
  "detector": "0"
}
```

The value `"0"` is a string. But the function effectively attempts:

```
JSON.parse("0")
```

The result is the number `0`.

Likewise, a string containing a JSON array such as `"[]"` can become an actual array.

### 13.1 What changed in the experiment?

The document reports that 82 of 229 settings changed type during storage, while their values remained the same.

This is an important distinction:

- The data's meaning may appear unchanged.
- Its representation and type may differ.
- Code that relies on strict type comparisons can behave differently.

For example, in JavaScript:

```
0 === "0"   // false
0 == "0"    // true
```

Strict equality checks the type as well as the value. Loose equality may coerce types before comparing.

### 13.2 Why did the wall-clock setting still work?

The document gives a particularly interesting example involving:

```
details.wall_clock_timestamp_ignore
```

The command builder compares the setting with the string `'1'`.

Yet the experiment found that sending `"1"` disabled the wall-clock timestamp option as intended.

The note's explanation is that the configuration is parsed when it is stored, but the value is represented as a string again when it reaches the builder.

The important point is not to assume that a database export exactly matches the types that a builder receives at runtime.

The full path is:

```
UI or provisioning script
        |
        v
Incoming JSON values
        |
        v
Configuration cleaning and parsing
        |
        v
MariaDB JSON storage
        |
        v
Monitor configuration read by Shinobi
        |
        v
Command-builder logic
```

A value may pass through several representations along this path.

### 13.3 What this means for a provisioning script

The document identifies Session 12 as the point where monitor provisioning will become code.

The main implication is that an idempotency check must not mistake `"0"` and `0` for two different intended configurations if the application treats them as equivalent.

An idempotent provisioning operation can be run repeatedly without producing unintended changes when the desired state is already present.

A sensible comparison strategy should therefore distinguish between:

1. Fields where string-versus-number representation is semantically irrelevant.
2. Fields where type differences are meaningful.
3. Fields that need normalization before comparing.
4. Fields that should remain exact strings, such as URLs and identifiers.

Do not blindly convert every value to a string or use loose equality everywhere. Instead, normalize the fields according to their expected schema and behavior.

That makes the comparison more reliable and avoids hiding real configuration mistakes.

Pasted markdown.md

## 14. What happens when the camera disappears?

This is one of the most operationally important findings in the document.

Shinobi uses two separate mechanisms to detect failure:

1. The FFmpeg process exits unexpectedly.
2. The FFmpeg process stays alive but stops reporting progress.

These are different failure modes and require different detection logic.

### 14.1 Failure type A: FFmpeg exits

The document describes this sequence:

```
FFmpeg exits
     |
     v
fatalError()
     |
     v
Monitor status becomes 7 — Died
     |
     v
Restart delay selected
     |
     v
Attempt to launch the monitor again
```

The restart delay depends on how many failures have occurred.

The documented behavior is:

| Failure condition                           | Retry delay |
| ------------------------------------------- | ----------- |
| First failure                               | 5 seconds   |
| Second failure                              | 5 seconds   |
| Third failure and subsequent rapid failures | 10 minutes  |

The note says the failure counter resets only when 60 seconds have passed after a launch without another failure.

The configuration field `fatal_max` can also limit retries; the documented value `0` means unlimited retries.

This is a back-off strategy: after repeated rapid failures, Shinobi waits longer before attempting another launch.

### 14.2 Why have back-off at all?

Without back-off, a failed monitor could repeatedly launch FFmpeg, fail immediately and launch again.

That can generate excessive process churn and logs while the underlying problem remains unresolved.

Back-off reduces repeated attempts against a system that appears unhealthy.

But it introduces a dangerous operational trade-off: if the camera recovers while Shinobi is waiting through a long back-off interval, video may remain unavailable until the next retry.

The mechanism protects the server from an endless rapid retry cycle, but it doesn't guarantee the quickest possible recovery.

### 14.3 Failure type B: the process is alive but makes no progress

Now consider a camera that freezes while FFmpeg remains alive.

A simple process check sees:

```
ffmpeg process exists
```

But that does not mean frames are arriving.

The progress-pipe watchdog solves this by monitoring activity on `pipe:5`.

If progress data stops arriving for 60 seconds, Shinobi reports that the camera is not streaming and forces a restart.

This mechanism detects a problem that a process-existence check alone cannot detect.

### 14.4 Why the TCP connectivity test can be misleading

Before each launch, Shinobi checks whether `mediamtx:8554` accepts a TCP connection.

But MediaMTX can listen on port 8554 even if no camera is publishing to the requested stream path.

Consequently:

```
TCP connection succeeds
        |
        v
MediaMTX is listening
        |
        v
FFmpeg requests /cam-gate
        |
        v
No publisher exists
        |
        v
FFmpeg fails to read the stream
```

The successful TCP check is not a false result. It answers a narrower question than the operator might expect.

This is a classic distinction between service health and end-to-end application health.

### 14.5 The observed ten-minute recovery delay

Your document reports a controlled experiment in which the camera simulator was stopped and restarted after about 30 seconds.

The sequence was approximately:

| Event            | Observed behavior                                                |
| ---------------- | ---------------------------------------------------------------- |
| Camera stopped   | MediaMTX closed the reader session                               |
| First failure    | Retry scheduled after 5 seconds                                  |
| Second failure   | Retry scheduled after 5 seconds                                  |
| Third failure    | Retry delayed by 10 minutes                                      |
| Camera restarted | MediaMTX became available again                                  |
| During back-off  | Shinobi remained in the Died state                               |
| Final retry      | FFmpeg started successfully and the monitor returned to Watching |

The camera was back well before Shinobi resumed its attempt to read it.

The failure policy had effectively turned a short source interruption into a much longer live-view outage.

This is why the document highlights the issue as especially important for a port or gate.

### 14.6 What would a more robust design look like?

The document proposes investigating two options in a later robustness session.

Option A: an external watchdog

A separate service could periodically inspect monitor health and determine whether the camera stream is available again.

If the camera has recovered while Shinobi remains stuck in the long back-off period, the service could request a monitor restart.

The important design point is that the watchdog should check end-to-end stream availability, not merely whether MediaMTX's TCP port is open.

Option B: a MediaMTX fallback path

The document also suggests checking whether MediaMTX can keep the stream path available through a configured fallback.

That possibility must be validated against the deployed MediaMTX version and configuration. The note proposes it as an option to investigate, not as a tested solution.

Neither option should be considered implemented simply because it appears in the design notes.

Pasted markdown.md

## 15. The troubleshooting guide: symptom, cause and fix

Your document's final checklist condenses the most useful operational lessons. Here is what each one means in practice.

| Symptom                                               | Underlying cause                                                            | Recommended response                                              |
| ----------------------------------------------------- | --------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| Monitor ID becomes `camgate`                          | Hyphens are removed during ID sanitization                                  | Use `cam_gate` consistently                                       |
| Monitor immediately dies with `localhost` in its URL  | `localhost` refers to the Shinobi container                                 | Use `mediamtx` for the Compose service                            |
| Stream probe reports 30 FPS                           | Timestamp jitter affects the frame-rate estimate                            | Count frames or compare results with wall-clock stamping disabled |
| Browser is approximately seven seconds behind         | HLS segmentation and playback buffering                                     | Investigate playlist length and alternative output formats        |
| Monitor stays Died after the camera recovers          | Long back-off following repeated failures                                   | Restart the monitor or implement a recovery watchdog              |
| Live view briefly disappears after editing            | Shinobi restarts the monitor                                                | Plan configuration changes and batch provisioning                 |
| Exported configuration contains `0` rather than `"0"` | JSON parsing changes the value's type                                       | Normalize values when comparing configurations                    |
| Adding another monitor fails after 15 monitors        | The documented installation is not activated and reports a 15-monitor limit | Stay within the reported limit or investigate activation          |

Pasted markdown.md

## 16. How to verify the running system yourself

The document provides several diagnostic commands. Each answers a different question.

### 16.1 Inspect the generated command

```
docker compose exec shinobi mysql ccio -N -B --raw -e \
  "select time, json_value(info,'$.msg.cmd')
   from Logs
   where mid='cam_gate'
   order by time desc
   limit 1"
```

Purpose: inspect the most recent logged FFmpeg command.

This is useful when you want to confirm whether a particular setting actually affected the command builder.

For example, if you change the stream type and the resulting command still appears to use the previous output format, inspect the saved monitor configuration and the generated command separately.

### 16.2 Inspect the process tree

```
docker compose exec shinobi \
  ps -eo pid,ppid,rss,pcpu,args --forest
```

Purpose: identify the parent-child relationships between Shinobi, the camera helper and FFmpeg.

This helps establish whether the monitor has a running FFmpeg process and whether its resource use appears unusual.

### 16.3 Inspect the actual FFmpeg arguments

```
docker compose exec shinobi sh -c \
  'tr "\0" " " < /proc/$(pgrep -x ffmpeg)/cmdline; echo'
```

Purpose: read the arguments of a running FFmpeg process directly from the operating system.

The actual command line is particularly useful when comparing what Shinobi logged against what is running.

One caution: `pgrep -x ffmpeg` may not select the intended process if several monitors are running. For a multi-camera setup, identify the relevant PID from the process tree first.

### 16.4 Inspect the live HLS output

```
docker compose exec shinobi sh -c \
  'ls -la /dev/shm/streams/*/cam_gate/;
   cat /dev/shm/streams/*/cam_gate/s.m3u8'
```

Purpose: confirm that the playlist and segment files exist and that the playlist contains recent entries.

This helps separate a browser problem from an upstream problem. If FFmpeg is producing a healthy playlist but the browser is failing, investigate the HTTP endpoint and player. If the playlist isn't advancing, investigate the FFmpeg input and output pipeline first.

### 16.5 Inspect the stored monitor configuration

```
docker compose exec shinobi mysql ccio -N -B --raw -e \
  "select details from Monitors where mid='cam_gate'"
```

Purpose: inspect the settings stored in the database.

Remember that some values may have different types from those originally submitted through the UI.

### 16.6 Check the activation status

```
docker compose exec shinobi \
  curl -s http://127.0.0.1:8080/activationStatus
```

Purpose: query the activation endpoint from inside the container, as required by the documented setup.

Your document reports that the tested installation returned `activated: false` and `maxCameras: 15`.

That value describes the observed installation at the time of the experiment, not a universal limit for every Shinobi installation or version.

Pasted markdown.md

## 17. The deeper engineering lessons

The individual flags are useful to understand, but the broader lessons are even more valuable.

### Lesson 1: Configuration is not the same as runtime state

A monitor can exist in MariaDB without an active FFmpeg process. A process can exist without receiving frames. A stream can exist without being playable in the browser.

Therefore, a robust diagnostic process checks each layer separately.

### Lesson 2: Stream copy saves resources, but the source controls much of the output

When FFmpeg uses `-c:v copy`, it avoids re-encoding the main video.

That is efficient, but it also limits what the output pipeline can change. The source's codec, resolution, frame rate and keyframe interval become important constraints.

### Lesson 3: GOP configuration affects live-view behavior

The HLS segment duration is influenced by keyframe availability when using stream copy.

A nominal two-second segment target works well when the camera produces a keyframe every two seconds. An irregular or longer GOP can increase segment duration and latency.

### Lesson 4: A healthy network endpoint does not guarantee a healthy video stream

The TCP check proves that MediaMTX is listening. It does not prove that the camera is publishing, that FFmpeg is receiving frames or that browser playback is advancing.

Health checks should test the layer they are intended to validate.

### Lesson 5: Monitoring needs more than process supervision

The progress-pipe watchdog detects a stalled stream even when FFmpeg remains alive. The restart back-off policy, however, can delay recovery after repeated failures.

Reliable monitoring needs both failure detection and an appropriate recovery strategy.

### Lesson 6: Live-view storage and recording storage serve different purposes

RAM-backed HLS files are temporary delivery artifacts. Persistent MP4 segments are the recording archive.

Separating the two prevents the live-stream mechanism from becoming the permanent storage system and allows each to have an appropriate retention policy.

### Lesson 7: Automated configuration requires type-aware comparison

The database's string-to-number conversion means a provisioning script cannot always compare raw JSON values naively.

The correct comparison should reflect the intended schema and the way Shinobi consumes the values.

### Lesson 8: Every observation needs an evidence level

Your document includes several different kinds of evidence:

- Direct observations: the recorded FFmpeg command, process tree and HLS playlist.
- Measured results: approximate resource use, frame intervals and startup delays.
- Source-code interpretations: the explanation of the configuration parser and command builder.
- Open questions: the output-side timestamp flags and the best timestamp policy for recording.
- Future proposals: external recovery monitoring and MediaMTX fallback behavior.

These should not be treated as interchangeable. A measured result establishes what happened in the test; it doesn't automatically establish what will happen under every deployment condition.

Pasted markdown.md

## 18. What should happen next in the project?

Your document identifies several unresolved questions that are deliberately left for later sessions.

Session 10 — recording and timestamps

Decide whether wall-clock stamping or camera timestamps are preferable for actual recordings and event-time alignment. Test with real recordings rather than relying exclusively on the simulated stream.

Session 12 — monitor provisioning as code

Implement the provisioning script and make configuration comparisons robust to the string-to-number conversion. Test repeated runs to ensure that the script does not introduce unnecessary changes.

Session 14 — detection

Enable the detector and investigate whether configuration type conversion affects strict string comparisons on the detection path. Validate the actual detector output rather than assuming the disabled configuration behaves identically.

Session 25 — robustness

Investigate the long restart back-off, determine how to detect source recovery reliably, and test whether an external watchdog or MediaMTX fallback improves recovery.

Pasted markdown.md

## 19. Resources for further study

The following resources are the most relevant to the concepts in your document.

[FFmpeg Logo PNG Vector (SVG) Free Download](https://images.openai.com/static-rsc-4/W81diETQqys0nSIr2ggR3Z4kUj7hA-tF9b0nxdwGhCZIWgf7QWJBN6KZ__X3oAJqtMAcamw2iYOmpQaIyABUbcLvWYU9wVdQOKkS2HcmkC7KbQbe6o4sUvsGivVQGTCMeLiS8m9zG-CsB0-433u78oQ3JFG2e0Ust9x2CSXdW20?purpose=inline)

FFmpeg

The command-line reference explains input and output options, stream copy, timestamps, HLS segmentation and the segment muxer.

[FFmpeg command-line documentation](https://ffmpeg.org/ffmpeg.html?utm_source=chatgpt.com)

[FFmpeg formats and muxers](https://ffmpeg.org/ffmpeg-formats.html?utm_source=chatgpt.com)

[Shinobi - Agilicus](https://images.openai.com/static-rsc-4/47G3VHlJC_Oh_glm4j4hCXWP2IjnGd95U5x8yaQT9t91X6vwN4vvyrJ9GbSDHupmeefPbskwiE8lnQIKSzfkzULzxY1Bi1BWklnTDA7bV6-dycR1tQispCw7ZKfyTT8VZYWaOxK4okFrJgvS36Fxw5JqDMe1O-xu3tRQiJTyFJo?purpose=inline)

Shinobi

Consult the project's documentation and source code for monitor configuration, FFmpeg command generation, status management and recording behavior.

[Shinobi documentation](https://docs.shinobi.video/?utm_source=chatgpt.com)

[Shinobi source repository](https://gitlab.com/Shinobi-Systems/Shinobi?utm_source=chatgpt.com)

[mediamtx v1.15.2 更新详解：功能优化与问题修复-CSDN博客](https://images.openai.com/static-rsc-4/w3oLSX4MXXO-NA4teQqOh9Z5cvLCdHyD_EGyJ4YGeU3mhbDl66oPsgOs3DXaEcKpHV23_l_ufd0srf0LS8tVSo-Ozt7JuNSO5o2vWGok1D_rwqjbKkC9div1IzFx3rJDKwlKQcr70tm9-W8BzRqMhtFzXLhxJ2cTdWOvdcp_eU8?purpose=inline)

MediaMTX

Useful for understanding RTSP paths, publishers, readers, transport options and fallback configuration.

[MediaMTX official site](https://mediamtx.org/?utm_source=chatgpt.com)

[MediaMTX source repository](https://github.com/bluenviron/mediamtx?utm_source=chatgpt.com)

[Back to basics: HTTP video streaming | AWS for M&E Blog](https://images.openai.com/static-rsc-4/s3kVh-U4s51yN542dADE6DO51M05rtz0r3ECvUexg7hIDypMMrtagYnSMXUo7EF9cP1Gv6cH_oYu3MVPnk3W2Zx956z-BF-UbSLEARJxg7DKoG1XEZXFSsigWEQaVYmL43eF4D3qrROpc5A_oElFKhLT023ETqwI1oEy8fJWuSU?purpose=inline)

HTTP Live Streaming

Apple's documentation describes playlists, media segments and the protocol's live-streaming model.

[Apple streaming resources](https://developer.apple.com/streaming/?utm_source=chatgpt.com)

[GitHub - video-dev/hls.js: HLS.js is a JavaScript library that plays HLS in browsers with support for MSE. · GitHub](https://images.openai.com/static-rsc-4/iJ0bpY4hXVvWcZPWbY_k3omlUbtjMADwPlLAUY2jo-aB38e6yr9uZoRYb3eAQJLFC4HFVNIZqA20l0DYLoIFl-YwYgI08y63YsNFD0WzQr5DfAAZuqomVpR7BSGKh_hDsYBFsg3eBvOCd6Kl2-7lgSgE_wUcmMl5mNrqp8RCMZ8?purpose=inline)

hls.js

Explains how HLS playlists and segments are delivered into browser playback using Media Source Extensions.

[hls.js project and documentation](https://github.com/video-dev/hls.js?utm_source=chatgpt.com)

Primary resource: your uploaded Session 9 — First monitor: what Shinobi does with a camera. It is the source for the specific versions, measurements, commands, implementation details and unresolved questions explained above.&#x20;

Pasted markdown.md



## Final takeaway

The central idea is that Shinobi is not simply displaying a camera feed. It is managing a persistent monitor configuration, creating an FFmpeg process, reading an RTSP stream through MediaMTX, producing one or more outputs, delivering live video to browsers, and monitoring the process for failures.

The default design uses HLS for live viewing and stream copy to keep processing inexpensive. Recording adds a second output to the same FFmpeg process. The main operational risk highlighted by this session is that repeated failures can trigger a long back-off period, leaving the monitor unavailable even after the camera has recovered.

The most useful debugging habit is to verify the configuration, process, input stream, generated output and browser delivery as separate layers.

My Trust: 9/10

Check the uncertain points: The explanations of the documented pipeline are grounded in your file. The resource figures and timings are specific to its test environment. The claimed ineffectiveness of the output-side timestamp flags was not experimentally verified in the document, and the best timestamp policy, detection-path type behavior and proposed recovery improvements remain open questions.