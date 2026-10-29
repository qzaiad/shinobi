# Session 10 — Recording, storage, and CPU in Shinobi: an exhaustive explanation

This document is a technical investigation into how a four-camera surveillance system records video, stores it on disk, tracks it in a database, deletes old footage, and consumes CPU resources. It also investigates what happens when a camera disconnects, FFmpeg crashes, or the Shinobi container restarts.

I'll explain the document from the ground up, including the architecture, important commands, configuration values, calculations, experiments, failure cases, and engineering conclusions. I'll distinguish between what the document directly measured and what follows from general technical principles.

The central idea is this:

Recording video is not simply a matter of writing a stream to disk. A reliable recording system must coordinate video timestamps, file boundaries, database indexing, crash recovery, storage limits, cleanup jobs, and CPU-intensive background work.

The document examines all those responsibilities in the context of Shinobi, FFmpeg, MediaMTX, and MariaDB.

## 1. First, understand the system's architecture

Four simulated cameras

Encode video and expose RTSP streams

RTSP

MediaMTX

Receives and relays the camera streams

RTSP over TCP

## Shinobi / FFmpeg

Live view

HLS playlist and temporary segments

Recording

MP4 files, divided into segments

Finished-file notification

MariaDB — `ccio.Videos`

Records metadata about finished video files

Age retention

Delete footage older than the configured age

Size retention

Delete old footage when the storage threshold is exceeded

### 1.1 What each component actually does

The cameras generate the video. In this experiment, they are simulated sources defined in `sim/cameras.yaml`, version 2. This is useful because the experiment can control resolution, bitrate, frame rate, timestamps, and keyframe intervals.

RTSP is the protocol used to transport the video stream from a camera to a client. RTSP describes session control, while the media itself is commonly carried in RTP packets.

MediaMTX receives the simulated camera streams and makes them available to Shinobi. This allows Shinobi to read a consistent stream without each application needing to connect independently to the simulated camera.

FFmpeg processes the stream. In the configuration described here, each Shinobi monitor has one main FFmpeg process. That process supplies both the live view and the recording output.

Shinobi coordinates the system: it builds FFmpeg commands, tracks completed recordings, updates the database, generates thumbnails, and applies retention rules.

MariaDB stores metadata. It knows about a recording's monitor, start time, end time, size, and status. It does not store the MP4 video content inside each row; the actual video is stored as a file on disk.

That last distinction is fundamental: the file and its database row are two separate things that can get out of sync.

A file may exist without a database row. A database row may point to an incomplete or unplayable file. A duplicate database row may refer to the same physical file.

The document demonstrates all three kinds of problems.

### 1.2 What does the experimental setup look like?

The notes record the following environment on October 22, 2026:

- Shinobi development revision `63755122`.
- FFmpeg 4.3.9 inside the container.
- Four simulated cameras.
- An Intel Core Ultra 7 265U laptop, measured on battery in the quiet power profile.
- MariaDB database `ccio`, with recordings indexed in the `Videos` table.

These details matter because the results are not universal hardware benchmarks. CPU use, encoding speed, and timing behavior depend on the FFmpeg build, processor, clock speed, video properties, and system configuration.

The document uses the following terms:

| Term     | Meaning                                                                            |
| -------- | ---------------------------------------------------------------------------------- |
| Monitor  | A camera configuration inside Shinobi                                              |
| `mid`    | Monitor identifier                                                                 |
| `ke`     | Shinobi group key                                                                  |
| RTSP     | Real Time Streaming Protocol                                                       |
| HLS      | HTTP Live Streaming                                                                |
| MP4      | Container format holding the video                                                 |
| GOP      | Group of Pictures, the interval between keyframes                                  |
| PTS      | Presentation timestamp                                                             |
| MiB      | 1,048,576 bytes                                                                    |
| CPU 100% | Approximately one fully occupied logical CPU's worth of work in these measurements |

The group key and session/API key mentioned in the notes are sensitive credentials. They are deliberately not reproduced.

## 2. Why one FFmpeg process can do two jobs

The first important finding in the document is that enabling recording does not create a second main FFmpeg process for each camera.

Instead, Shinobi adds another output to the FFmpeg command that already handles the live stream. The same input is used for two purposes:

1. Produce the live HLS stream.
2. Write persistent MP4 recordings to disk.

This is an efficient architecture because the camera stream is pulled once, then directed to multiple outputs.

### 2.1 What FFmpeg is doing

The document shows a command built by Shinobi's `buildMainRecording()` function in `libs/ffmpeg/builders.js`.

Conceptually, the command looks like this:

```
FFmpeg
  │
  ├── Input: RTSP stream from MediaMTX
  │
  ├── Output 1: HLS live view
  │     ├── playlist: s.m3u8
  │     └── temporary live segments
  │
  └── Output 2: MP4 recording
        ├── recording segment 1
        ├── recording segment 2
        └── recording segment 3
```

The two outputs have different purposes.

| Property                 | Live output            | Recording output            |
| ------------------------ | ---------------------- | --------------------------- |
| Format                   | HLS                    | MP4                         |
| Destination              | `/dev/shm/streams/...` | `/home/Shinobi/videos/...`  |
| Purpose                  | Live viewing           | Persistent storage          |
| Retention                | Short rolling buffer   | Age- and size-based cleanup |
| Codec in this experiment | Copied H.264           | Copied H.264                |

The live output uses an HLS playlist with three listed segments and a target segment duration of two seconds. FFmpeg deletes old live segments as the playlist advances.

The recording output uses the segment muxer to produce MP4 files with timestamp-based names.

### 2.2 What does `-c:v copy` mean?

This is one of the most important settings in the whole investigation.

`-c:v` means the video codec option. The value `copy` tells FFmpeg to copy the encoded video packets into the output container without decoding the video into pixels and encoding it again.

Imagine a camera has already compressed its video using H.264.

There are two possible paths:

Stream copy

H.264 packets → MP4 muxer → MP4 file

The compressed video is preserved. FFmpeg mainly moves packets and packages them into the destination container.

Transcoding

H.264 packets → Decode → Pixels → Encode → MP4 file

The video is decoded and compressed again. This consumes much more CPU and may change quality, bitrate, frame rate, and codec parameters.

Stream copy does not mean that FFmpeg performs no work. It still has to receive packets, demultiplex or depacketize them, handle timestamps, process the output container, and write data.

But it avoids the expensive video decoding and encoding stages.

That is why the document measured approximately 0.8% CPU for one camera in copy mode versus approximately 141% for transcoding with the Shinobi defaults.

### 2.3 Why one input is beneficial

Many physical cameras support only a limited number of simultaneous RTSP sessions. The document notes that two to four sessions are common in the class of cameras being considered.

If live viewing and recording independently opened the camera stream, each camera might need multiple sessions. Multiple clients could also increase camera load and complicate stream management.

With the shared-input architecture, one main FFmpeg process can feed both outputs.

There is a trade-off, however.

Shared input means shared failure.

If the FFmpeg process crashes, both live viewing and recording stop. The recording path does not have an independent process to keep working.

The document also found that editing a monitor restarts its FFmpeg process, creating approximately a four-second recording gap in the measured example. A crash restart produced a gap of roughly seven to nine seconds.

The Session 9 retry back-off is another important consideration: a retry delay of ten minutes can become a ten-minute recording gap if the recording process cannot reconnect.

This illustrates a common systems-engineering principle: sharing a process reduces resource use, but it also creates a larger failure domain.

## 3. The segment muxer: why recordings are divided into files

Continuous recording cannot simply create an endlessly growing MP4 and assume that everything will remain safe and manageable.

Shinobi uses FFmpeg's segment muxer to divide the output into separate MP4 files.

The relevant output options in the final configuration include:

```
-f segment
-segment_atclocktime 1
-reset_timestamps 1
-strftime 1
-segment_list pipe:8
-segment_time 900
```

The number `900` represents 900 seconds, or 15 minutes.

The notes describe the final configuration as using the maximum segment length permitted by Shinobi: 15 minutes.

### 3.1 Why use separate segments?

Suppose a camera records for an entire day.

At 15-minute segmentation, a continuous 24-hour recording would nominally produce:

\\[ \frac{24\times60}{15}=96 \\]

So the expected count is 96 segments per camera per day, assuming continuous recording and ignoring restarts, initial partial segments, and other interruptions.

With four cameras, that is approximately:

\\[ 96\times4=384 \\]

recording files per day.

If segmentation were instead set to one minute, the same four cameras could generate:

\\[ 24\times60\times4=5760 \\]

files per day.

That is a substantial difference in filesystem activity, database rows, thumbnail-generation jobs, and retention work.

Smaller files do have benefits: finished footage becomes available sooner, and less footage is at risk in an ordinary incomplete segment. The engineering question is how to balance those benefits against the overhead.

### 3.2 Understanding the important FFmpeg flags

| Option                   | What it does                                                  | Why it matters                                                         |
| ------------------------ | ------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `-segment_time 900`      | Sets the target segment duration to 900 seconds               | Limits file duration and bounds the normal segment completion interval |
| `-segment_atclocktime 1` | Aligns segment boundaries with wall-clock intervals           | Makes recordings from different cameras easier to align                |
| `-strftime 1`            | Interprets the output pattern as a time-based filename        | Gives files timestamp-based names                                      |
| `-reset_timestamps 1`    | Resets timestamps for each segment                            | Lets each file start its own local timeline                            |
| `-segment_list pipe:8`   | Writes completed filenames to file descriptor 8               | Gives Shinobi a notification when a segment closes                     |
| `movflags=faststart`     | Moves the MP4 index toward the beginning of the file at close | Helps playback begin without downloading the entire file first         |

These options solve different problems. None should be treated as a substitute for all the others.

### 3.3 Why wall-clock alignment matters

Consider two cameras that start recording at different moments.

Without wall-clock alignment, each camera could create files at offsets determined by its own start time.

For example:

```
Camera A:
12:00:00 ───── 12:15:00 ───── 12:30:00

Camera B:
12:03:12 ───── 12:18:12 ───── 12:33:12
```

With clock-aligned segmentation, the intended boundaries instead fall on shared intervals:

```
12:00:00 | 12:15:00 | 12:30:00 | 12:45:00
```

This makes it easier to correlate recordings of the same event across multiple cameras.

There is an important qualification: the segment muxer does not guarantee a perfectly exact boundary when stream copy is used. Video keyframes affect when the actual cut can happen. The filenames and actual content boundaries may therefore differ by a small amount.

### 3.4 Keyframes explain why segments do not always start exactly on time

A compressed video usually contains different types of frames.

- I-frames / keyframes: frames that can be decoded independently, subject to codec-specific requirements.
- P-frames: frames that predict content using earlier frames.
- B-frames: frames that may use both earlier and later reference frames.

For a simple H.264 stream-copy recording, a new segment normally needs a suitable keyframe at its beginning.

Suppose the intended cut is at 12:45:00 and the camera generates a keyframe every two seconds.

Illustrative timeline

#### 12:44:58

Keyframe

#### 12:45:00

Target cut

#### 12:45:02

Next keyframe

If the suitable keyframe arrives at 12:45:02, the new file may begin then rather than exactly at 12:45:00.

This explains why a nominal 15-minute file may be slightly longer or shorter than 900 seconds.

The document observed the following real files:

- `12:38:43` to `12:45:01`: an initial partial segment.
- `12:45:01` to `13:00:02`: approximately 15 minutes.
- `13:00:02` to `13:11:31`: recording ended when the monitor stopped.

The container clock was UTC, while the host was using Central European Summer Time. The note reports that the filename timestamps matched the camera's on-screen timestamps.

Important distinction: `-segment_time 900` is a target, not a guarantee that every file contains exactly 900 seconds of video.

### 3.5 Why the GOP length is so important

GOP stands for Group of Pictures. For this investigation, the practical issue is how long the encoder waits between keyframes.

The document's copy-mode cameras used a two-second GOP.

At 15 frames per second:

\\[ 15 \times 2 = 30 \text{ frames} \\]

At 10 frames per second:

\\[ 10 \times 2 = 20 \text{ frames} \\]

This gives the segment muxer relatively frequent opportunities to begin a new file at a suitable keyframe.

The default x264 transcoding experiment instead produced a GOP of 250 frames. At 15 fps:

\\[ \frac{250}{15}=16.67\text{ seconds} \\]

A 16.7-second interval is much longer than two seconds. If the desired cut occurs between keyframes, FFmpeg may have to wait substantially longer to begin the next segment.

The document measured segment lengths of 37, 67, and 50 seconds in an experiment with a nominal 60-second target and that long GOP.

The general lesson is that segment duration and keyframe interval must be considered together. Setting a segment target alone does not guarantee consistent file boundaries.

## 4. MP4 structure and why a crash can destroy the current segment

This is one of the most consequential findings in the investigation.

An MP4 file is a container. It holds media data and structural metadata describing how that data should be interpreted.

Two important structures are:

- `mdat`: the media-data box containing encoded audio/video samples.
- `moov`: the movie metadata box, which describes tracks, timing, sample tables, and where the media samples are located.

In the non-fragmented MP4 workflow described in the notes, the file can accumulate media data while it is being recorded, but its complete playback index may not be finalized until the file closes.

Conceptually:

```
During recording:
[ ftyp ][ mdat: growing media data ........ ]

After a clean close:
[ ftyp ][ mdat: media data ][ moov ]

After faststart processing:
[ ftyp ][ moov ][ mdat: media data ]
```

The `faststart` operation moves the index toward the beginning after the recording is finalized. It does not make an actively written, unfinished file equivalent to a fully finalized MP4.

### 4.1 What happens after a hard kill?

If FFmpeg is killed abruptly while writing a non-fragmented MP4, the file may contain media data but lack the finalized metadata needed for ordinary playback.

The document tested a hard FFmpeg kill and observed this error:

```
moov atom not found
Invalid data found when processing input
```

The affected file was approximately 6.3 MB.

This error is not proof that every byte of video in the file has disappeared. It indicates that the file lacks the expected MP4 movie metadata. Recovering usable footage may be possible in some circumstances, but the document does not establish a recovery method.

It also does not mean that all MP4 files are inherently unsafe. The problem is specifically the behavior of the non-fragmented recording output when the process does not finalize the file cleanly.

### 4.2 Why the database can still contain a row for a broken file

Shinobi's database indexing and FFmpeg's file finalization are separate operations.

A row can be created even when a hard kill has left the file unplayable. The document measured exactly that situation.

Therefore, a query such as:

```
SELECT * FROM Videos;
```

does not prove that all returned files are playable.

The row is evidence that Shinobi indexed the file. It is not a complete integrity check.

For a surveillance system, this distinction is critical: an API can return a recording that looks available in the interface but cannot actually be played.

### 4.3 Why fragmented MP4 is proposed as a solution

The document identifies fragmented MP4 as a future investigation in Session 25, using flags such as:

```
-movflags=+frag_keyframe+empty_moov
```

Fragmented MP4 stores media in a sequence of fragments, with metadata that can make completed fragments usable before the entire recording is finished.

That architecture can improve crash resilience because a hard kill may leave earlier fragments playable even if the final fragment is incomplete.

However, this is a proposed solution, not a proven result in this document. The author explicitly leaves open whether Shinobi's thumbnail generation, browser playback, and recording pipeline fully support the proposed configuration.

### 4.4 How much footage is exposed to a crash?

The segment length affects the amount of footage in the current, not-yet-finalized file.

| Segment target | Potential footage exposed in the current segment |
| -------------- | ------------------------------------------------ |
| 1 minute       | Up to approximately 1 minute                     |
| 15 minutes     | Up to approximately 15 minutes                   |

These are nominal bounds for the current segment, not guarantees about every failure or every file.

A crash can also create a gap before the next segment starts. The document measured restart gaps of approximately seven to nine seconds for the tested FFmpeg failure cases and a longer startup interruption after restarting the container.

A robust system must therefore handle both problems:

1. The current segment may be lost or become unplayable.
2. Recording may not resume immediately.

## 5. How a finished recording becomes a database row

The next major topic is the handoff between FFmpeg and Shinobi.

FFmpeg writes video files, but Shinobi also needs to know when each recording is complete so that it can list the video, report its size, generate thumbnails, and apply retention policies.

The document traces this workflow through `libs/monitor/utils.js` and `libs/videos.js`.

### 5.1 The completion notification

The recording output includes:

```
-segment_list pipe:8
```

Here, `pipe:8` refers to file descriptor 8, a communication channel between FFmpeg and the parent application.

When a segment closes, FFmpeg writes its filename to that channel. In the experiment, the notification resembles:

```
2026-10-22T11-13-00.mp4
```

Shinobi reads the notification and invokes its completed-video processing function.

The basic sequence is:

### 1

FFmpeg closes the segment

The MP4 is finalized, assuming the process closed it normally.

### 2

Shinobi reads the filename

The completion notification arrives through file descriptor 8.

### 3

Shinobi inspects the file

It obtains metadata such as size and modification time.

### 4

Shinobi updates accounting

It updates used-space tracking and may trigger size-based retention.

### 5

MariaDB receives a row

The Videos table records the video metadata.

### 6

Thumbnail processing begins

A separate FFmpeg invocation extracts a JPEG frame.

This pipeline explains why the database does not necessarily contain a row for a file as soon as recording starts. Ordinarily, the row is created after the segment finishes.

### 5.2 How the filename becomes the video's start time

Shinobi uses a timestamp-based filename pattern:

```
YYYY-MM-DDTHH-MM-SS.mp4
```

For example:

```
2026-10-22T11-13-00.mp4
```

The filename represents the intended start time of that recording segment, with one-second filename precision.

The completion handler uses the filename to derive the recording's `time` field. It also obtains file metadata, including size and modification time, which the notes use for the `end` field.

The database consequently stores a record containing information such as:

- The monitor identifier.
- The recording's start time.
- The end time.
- The file size.
- The file extension.
- The recording's status.

The document lists these columns in the `Videos` table:

`ke`, `mid`, `ext`, `size`, `status`, `archive`, `objects`, `saveDir`, `time`, `end`, and `details`.

### 5.3 What does `status` mean?

The notes identify two ordinary status values:

- `status = 1`: unread.
- `status = 2`: read.

The user interface can mark a video as read, which updates its status.

This is a user-interface state, not an integrity state.

A recording with `status = 2` is not necessarily complete, and a recording with `status = 1` is not necessarily broken.

Similarly, `archive = 1` is important because it protects a video from both retention mechanisms described in the document.

Archiving should not be confused with marking a video as read.

### 5.4 The orphan scan: why startup recovery can create duplicates

Shinobi performs an orphan scan at startup. Its purpose is to discover files on disk that do not have corresponding database rows.

This is useful after a crash. If a video was finalized but the application failed before inserting its row, the scan can recover the missing index entry.

However, the document found a flaw in this recovery process: the scan also picked up files that were still being written.

The recorded example is:

| Row                   | Start time | End time | Size             |
| --------------------- | ---------- | -------- | ---------------- |
| Startup orphan row    | 11:28:00   | 11:28:02 | 524,336 bytes    |
| Normal completion row | 11:28:00   | 11:29:00 | 14,995,169 bytes |

Both rows refer to the same physical file.

The first row was created while the recording was incomplete. The second was created when FFmpeg later finished the file and sent the normal completion notification.

This creates three problems.

1. Duplicate results: an API client can list the same recording twice.
2. Incorrect space accounting: the partial file size may be counted in addition to the final size.
3. Incorrect metadata: the early row contains an incomplete size and an incorrect end time.

This is a classic recovery race condition. A recovery mechanism that checks only whether a file exists may not distinguish a completed file from an active one.

The document proposes deduplicating by monitor and start time in the client, but a stronger general design would also make completion processing idempotent and distinguish actively written files from finalized files.

That latter point is an engineering recommendation, not a feature the document proves Shinobi already implements.

### 5.5 Two watchdogs, two different failure modes

The notes identify two watchdog mechanisms.

| Watchdog                             | Trigger                                         | Intended problem detected                      |
| ------------------------------------ | ----------------------------------------------- | ---------------------------------------------- |
| FFmpeg `-progress pipe:5` monitoring | No progress data for 60 seconds                 | A hung input or frozen camera                  |
| `resetRecordingCheck()`              | No finished segment within 1.3 times the cutoff | A recording output that stops completing files |

For the final 15-minute configuration:

\\[ 15 \times 1.3=19.5\text{ minutes} \\]

So the completion watchdog can wait approximately 19.5 minutes before deciding that a finished segment has not arrived in time.

Why is this useful?

An FFmpeg process can still be alive while a particular output is no longer progressing correctly. A process-existence check alone cannot detect every stalled recording.

Conversely, a live input may be frozen or unresponsive even if the recording process has not immediately exited.

The two watchdogs observe different symptoms. They complement each other, but neither guarantees that a completed file is playable.

## 6. The hidden CPU cost: thumbnails

One of the most interesting findings is that copying the video stream is cheap, but the system still performs additional work for every completed recording.

Shinobi calls the completed-video processing path, which invokes thumbnail extraction. The notes trace this through:

- `postProcessCompletedMp4Video()`
- `saveVideoFrameToTimelapse(video, 7)`
- `getVideoFrameAsJpeg()`

The operation starts another FFmpeg process to extract a JPEG from approximately seven seconds into the recording.

The command shown in the document is:

```
ffmpeg -loglevel warning \
  -ss 7 -i recording.mp4 \
  -frames:v 1 -q:v 2 \
  -f image2pipe -vcodec mjpeg pipe:1
```

Let's break down what that means.

| Option             | Meaning                                                                            |
| ------------------ | ---------------------------------------------------------------------------------- |
| `-ss 7`            | Seek to the requested position of seven seconds                                    |
| `-i recording.mp4` | Use the completed MP4 as the input                                                 |
| `-frames:v 1`      | Output only one video frame                                                        |
| `-q:v 2`           | Request a high-quality JPEG setting in FFmpeg's MJPEG encoder                      |
| `-f image2pipe`    | Send the image through a pipe rather than creating a separate ordinary output file |
| `-vcodec mjpeg`    | Encode the extracted frame as JPEG/MJPEG                                           |
| `pipe:1`           | Write the output to standard output                                                |

The document measured approximately 0.31 seconds of wall-clock time and 0.68 seconds of CPU time for a thumbnail extraction. These are different measurements: wall time measures elapsed time, while CPU time sums the processor time used by the work.

### 6.1 Why a short-lived process matters

The main FFmpeg processes are long-lived. Thumbnail FFmpeg processes are short-lived and may run only when a recording finishes.

That means they can be missed by a quick inspection of `ps` or `top`.

The document measured CPU consumption using the container's cgroup accounting as well as per-process statistics. The cgroup counter captures CPU work from short-lived child processes that might start and exit between snapshots of a process list.

This explains an initially confusing result: adding recording output barely increased the CPU usage of the main FFmpeg processes, yet the total container CPU consumption rose substantially when recordings were segmented every minute.

The extra work came from thumbnails.

### 6.2 Deriving the thumbnail overhead

The measured thumbnail extraction cost was approximately 0.68 CPU-seconds per completed segment.

With one-minute segments and four cameras:

\\[ 4\times\frac{0.68}{60}\times100 \approx4.53\\% \\]

That is approximately 4.5% of one CPU core in steady-state average terms.

With 15-minute segments:

\\[ 4\times\frac{0.68}{900}\times100 \approx0.30\\% \\]

The difference is approximately fifteenfold because 15-minute segmentation produces roughly one-fifteenth as many thumbnail jobs as one-minute segmentation, assuming continuous recording.

Estimated average CPU used by thumbnail generation

Derived from the measured 0.68 CPU-seconds per completed segment and four continuously recording cameras.

0%1.25%2.5%3.75%5%1-minute segments15-minute segments

These estimates isolate thumbnail work, not total Shinobi CPU usage.

The chart shows average CPU usage attributable to thumbnail generation, not the instantaneous CPU spike when a thumbnail process runs.

### 6.3 A subtle issue with `-ss 7`

The notes place `-ss 7` before `-i`.

For many input formats, seeking before the input is opened allows FFmpeg to seek efficiently to an appropriate earlier position rather than decoding the entire file from the beginning.

With a two-second GOP, the actual seek may land on a keyframe at or before the seven-second target. FFmpeg then decodes forward to obtain the desired frame.

This is why the document notes that the seek can begin around six seconds for a two-second GOP.

The extracted frame is therefore intended to represent the recording at approximately seven seconds. The exact seek behavior depends on the container, timestamps, and keyframe placement.

### 6.4 Where do the JPEGs go?

The notes identify directories of the form:

```
<mid>_timelapse/<date>/*.jpg
```

The images are associated with rows in the `Timelapse Frames` table. Their retention uses the `timelapseFrames_days` setting, whose documented default in this experiment is 60 days.

This is a separate retention policy from the MP4 recordings.

Deleting an old recording does not, by itself, establish that every associated thumbnail has also been deleted. The two kinds of data have their own lifecycle and cleanup behavior.

Engineering implication: when measuring storage use, count thumbnails and other generated assets as well as MP4 files. The main video directory alone may not explain every byte used by the application.

## 7. Retention: why two cleanup mechanisms are necessary

The document identifies two distinct mechanisms for deleting recordings:

1. Age-based retention, implemented through a cron worker.
2. Size-based retention, invoked after a completed recording is indexed.

These mechanisms answer different questions.

- Age retention: How old is this recording?
- Size retention: Has the recording storage exceeded its configured threshold?

They can both be active at once.

### 7.1 Age-based retention

The age-based cleanup runs from the cron worker in `libs/cron/worker.js`.

According to the notes, the worker runs its cleanup at startup and then at hourly intervals.

It processes recordings according to monitor-specific or account-level settings.

| Setting                | Purpose                         |
| ---------------------- | ------------------------------- |
| `max_keep_days`        | Optional per-monitor age limit  |
| `details.days`         | Account-level default age limit |
| `log_days`             | Log retention                   |
| `event_days`           | Event retention                 |
| `timelapseFrames_days` | Thumbnail retention             |

The account-level default for video retention is described as five days when not otherwise configured.

An important exception is archived video. Rows with `archive = 1` are skipped by this age-based cleanup.

### 7.2 Why fractional days are dangerous

The document investigated a surprising behavior in the JavaScript implementation.

The code uses `Date.setDate()` to calculate an age cutoff. Although JavaScript can accept fractional values in some date operations, `setDate()` ultimately works with a calendar-day value that is converted to an integer.

The experiment found that fractional values behaved like whole-day offsets in the tested environment:

- `0.01` resulted in a one-day cutoff.
- `1.5` resulted in a two-day cutoff.

This is not the same as keeping video for exactly 0.01 days or 1.5 days.

For instance, 0.5 days would nominally represent 12 hours if interpreted as a duration. The tested implementation instead behaved in whole-day terms.

The document's recommendation is therefore to use whole-day values rather than assuming fractional settings provide sub-day precision.

The exact cutoff is also affected by how the application computes the current date and calendar boundary. The experiment establishes the behavior it observed in that environment, not a universal promise about every future implementation.

### 7.3 Why age retention can be late

The cleanup runs at startup and hourly, so a recording that becomes old enough just after a cleanup pass may remain until the next pass.

That means age-based deletion can be delayed by approximately an hour under the described schedule, in addition to any relevant processing and scheduling delays.

This is normal for periodic cleanup. It differs from a continuously evaluated retention condition.

### 7.4 A logging bug in the age-based cleanup

The document identifies another implementation problem.

The code tests something like:

```
affectedRows.length > 0
```

But the SQL operation returns `affectedRows` as a number, not an array.

For a numeric result, `.length` is undefined. The condition therefore does not behave as intended, so the expected deletion-count log line does not appear under ordinary circumstances.

This matters operationally because missing logs do not necessarily mean no deletion happened.

The document's tests found that old recordings were deleted even though the expected deletion message did not appear.

A proper implementation should test the numeric count, for example:

```
if (affectedRows > 0) {
    // Log the number of deleted rows.
}
```

This snippet illustrates the intended type check; it is not a claim that the installed Shinobi code has already been patched.

## 8. Size-based retention: how the storage cap actually works

The size-based retention mechanism is especially important because it can delete recordings much sooner than the configured age limit suggests.

The document traces this behavior to `deleteMainVideos()` in `libs/user/utils.js`.

The configured storage limit is not necessarily the exact amount of video data Shinobi allows to accumulate. The application calculates a lower threshold using several multipliers.

The documented formula is:

\\[ T=S\times P\times O \\]

where:

- \\(T\\) is the effective video-storage threshold.
- \\(S\\) is the configured account size in MiB.
- \\(P\\) is the video-storage percentage, defaulting to 90%.
- \\(O\\) is `cron.deleteOverMaxOffset`, described as 0.9 in this configuration.

Thus:

\\[ T=S\times0.9\times0.9 \\]

\\[ T=0.81S \\]

Only about 81% of the configured size value becomes the normal size-retention threshold.

The document notes that Shinobi divides bytes by 1,048,576 when calculating these MiB values, even where the UI labels the setting as MB.

### 8.1 Worked example: a configured size of 3,000

Suppose the configured size is 3,000 MiB.

The effective threshold is:

\\[ 3000\times0.9\times0.9=2430\text{ MiB} \\]

If the current tracked video usage is 2,736 MiB, then the system exceeds the threshold by:

\\[ 2736-2430=306\text{ MiB} \\]

When a new completed recording triggers the size-pruning process, Shinobi starts deleting eligible old videos.

The experiment recorded two batches of three deletions:

| Batch   | Number of files deleted |
| ------- | ----------------------- |
| Batch 1 | 3                       |
| Batch 2 | 3                       |
| Total   | 6                       |

After those deletions, the tracked storage dropped to 2,374 MiB, below the 2,430 MiB threshold, so the loop stopped.

This demonstrates an important implementation detail: the size-retention algorithm does not necessarily delete exactly the number of bytes needed to reach the threshold. It deletes batches of the three oldest eligible videos, potentially going below the threshold.

### 8.2 Which videos can be deleted?

The notes describe the size-pruning logic as selecting eligible recordings across the entire group, not independently for each monitor.

The selection excludes:

- Videos with `status = 0`.
- Videos with `archive = 1`.
- Videos stored in a custom directory.

It deletes three of the oldest eligible videos per batch while the size condition remains true, subject to the documented maximum of ten rounds in the disk-pressure case.

The filesystem-pressure branch is also important. If the filesystem holding the recordings is at least 95% full, the system can invoke pruning even apart from the ordinary tracked-size threshold.

That is a safety mechanism, but it should not be treated as a substitute for properly sizing storage.

### 8.3 Why every camera loses roughly the same time depth

The document's steady-state measurements show different storage usage by camera:

| Camera         | Approximate bitrate | Storage observed |
| -------------- | ------------------- | ---------------- |
| `cam_gate`     | 2 Mb/s              | 470 MiB          |
| `cam_quay`     | 4 Mb/s              | 916 MiB          |
| `cam_waterway` | 3 Mb/s              | 686 MiB          |
| `cam_yard`     | 1.5 Mb/s            | 343 MiB          |

These measurements show that higher-bitrate cameras consume more storage for roughly comparable recording durations.

The size-retention algorithm deletes the oldest eligible files across the group. It is not trying to allocate exactly one quarter of the disk to each camera.

Consequently, the footage depth tends to be similar across cameras, while the storage consumed by each camera is proportional to its bitrate.

That is a sensible behavior if the goal is to preserve approximately the same historical time window for every camera.

It is not ideal if one camera must retain a much longer history than the others. The document identifies `max_keep_days` as the available per-monitor age-based control, though group-wide size pruning can still affect the overall outcome.

### 8.4 Why storage deletion can appear silent

The document reports that the size-pruning operation does not create a corresponding row in the `Logs` table in the tested case. It emits a websocket `video_delete` event instead.

The result is that a recording may disappear without the expected log entry.

This is another example of why a surveillance system should not rely on a single observability channel. Application logs, websocket notifications, database records, filesystem checks, and independent storage monitoring each answer different questions.

## 9. Calculating how much storage the cameras need

Retention settings should be derived from the actual camera bitrates and the desired recording duration, rather than chosen arbitrarily.

The document measures four camera streams with the following approximate bitrates:

| Camera         | Bitrate   | Approximate storage per hour | Approximate storage per day |
| -------------- | --------- | ---------------------------- | --------------------------- |
| `cam_gate`     | 2 Mb/s    | 0.90 GB                      | 21.6 GB                     |
| `cam_quay`     | 4 Mb/s    | 1.80 GB                      | 43.2 GB                     |
| `cam_yard`     | 1.5 Mb/s  | 0.68 GB                      | 16.2 GB                     |
| `cam_waterway` | 3 Mb/s    | 1.35 GB                      | 32.4 GB                     |
| Total          | 10.5 Mb/s | 4.7 GB                       | 113 GB                      |

These figures are estimates based on the stated bitrates. Real storage requirements vary with actual average bitrate, container overhead, and recording interruptions.

### 9.1 Where does the storage formula come from?

A bitrate measured in megabits per second describes how many bits are transferred every second.

To convert to bytes per day, divide by eight because there are eight bits in a byte, then multiply by the number of seconds in a day.

For a camera running at 2 Mb/s:

\\[ \frac{2\times10^6}{8}\times86400 =21.6\times10^9\text{ bytes} \\]

That is 21.6 decimal GB per day.

For the entire fleet:

\\[ \frac{10.5\times10^6}{8}\times86400 =113.4\times10^9\text{ bytes/day} \\]

So the fleet needs approximately 113 GB per day under the assumed bitrates.

The distinction between decimal GB and binary GiB or MiB matters when comparing these calculations with filesystem tools and Shinobi's internal accounting.

### 9.2 What happens with the defaults?

The document states that the machine had approximately 297 GB free.

Shinobi's default account settings were five days of age retention and a configured size of 10,000 MB.

However, the effective video-storage threshold under the documented multipliers was:

\\[ 10000\times0.9\times0.9=8100\text{ MiB} \\]

At the measured fleet consumption rate, that is only around 1.8 hours of footage.

Therefore, even though the account's age setting says five days, the size limit can delete the recordings much sooner.

This is an example of two limits interacting: the first one reached determines the practical retention duration.

### 9.3 What does the final configuration achieve?

The document sets:

- Account video retention age: `days = 1`.
- Account configured size: `size = 20000` MB.
- Effective size threshold: approximately 16,200 MiB.

The notes estimate that this provides roughly 3.6 hours of footage across all four cameras, while the one-day age setting serves as a secondary limit.

This is a deliberate configuration for a disk-constrained test environment, not a configuration that provides a full day of recording.

If the requirement were to retain a full day of footage, a 20,000 MB configured size would be inadequate for a fleet consuming around 113 GB daily.

For \\(N\\) days of retention, a first-order estimate is:

\\[ \text{Required GB}\approx113\times N \\]

So three days would require roughly 339 GB of video storage, before adding operational headroom, thumbnails, filesystem overhead, and any additional streams.

For real deployments, the correct process is to calculate the expected footage requirement first, then configure both age and size limits to match it.

## 10. Camera timestamps versus wall-clock timestamps

The next investigation concerns a subtle but important issue: whether FFmpeg should use the camera's timestamps or timestamps derived from the local system clock.

The document measures two configurations on the same camera.

| Measurement                 | Wall-clock timestamps enabled | Camera timestamps    |
| --------------------------- | ----------------------------- | -------------------- |
| Frames in the recording     | 900                           | 900                  |
| Duration                    | 59.96 s                       | 59.93 s              |
| Minimum PTS step            | 9.2 ms                        | 66.7 ms              |
| Maximum PTS step            | 153.3 ms                      | 66.7 ms              |
| PTS step standard deviation | 21.6 ms                       | 0.0 ms               |
| Exactly on nominal timing   | 3 of 899 intervals            | 899 of 899 intervals |

The nominal frame interval for a 15 fps stream is:

\\[ \frac{1}{15}\approx0.06667\text{ seconds} \\]

That is approximately 66.67 milliseconds per frame.

### 10.1 Why timestamps matter

A video is not merely a list of images. Each frame has timing information indicating when it should be presented.

If the timestamp intervals vary, the playback timing can vary too.

The document observed that wall-clock timestamping produced irregular frame intervals in the resulting MP4. Those irregular intervals can be preserved in the file's timing metadata, resulting in visible judder or uneven motion during playback.

By contrast, the simulated cameras supplied perfectly regular timestamps. Using those timestamps produced the expected 66.7 ms interval for every measured frame.

This led to the final configuration:

```
details.wall_clock_timestamp_ignore = 1
```

The notes interpret the value `1` as using the camera timestamps rather than imposing wall-clock timestamps for the stream.

### 10.2 Why this decision is conditional

The simulated cameras have continuous, well-behaved timestamps. Real cameras do not always behave that way.

A real camera may have:

- A broken or drifting clock.
- Timestamp discontinuities after reconnecting.
- Irregular timestamp intervals.
- An incorrect relationship between its clock and the host clock.

If the camera timestamps are unreliable, blindly preserving them can create problems of its own.

Therefore, the document's decision is specific to the tested stream properties: use camera timestamps for these simulated cameras, but allow wall-clock timestamping for a real camera whose timestamps are demonstrably unreliable.

### 10.3 Where does absolute time come from if timestamps reset?

The recording configuration also uses:

```
-reset_timestamps 1
```

This resets the segment's internal presentation timeline. The first frame in the file begins at a local PTS of zero.

For example, suppose an event occurs 37.4 seconds into this file:

```
2026-10-22T11-13-00.mp4
```

Its estimated absolute time is:

\\[ 11{:}13{:}00+37.4\text{ seconds} =11{:}13{:}37.4 \\]

This combines the filename's start time with the local elapsed time within the file.

But this estimate is not infinitely precise. The filename has one-second precision, and keyframe-based segmentation can shift the actual boundary. The document explicitly states that precise event times should instead come from the event records themselves, as investigated in later sessions.

### 10.4 An unexplained frame-count anomaly

The notes report that the first complete file after switching the timestamp configuration contained 870 frames and the next contained 930, instead of the usual 900 frames each.

Later files returned to 900 frames.

The investigation found regular keyframe spacing and did not identify the cause. The author considers the anomaly harmless, but it remains an unresolved observation.

It would be incorrect to claim that the document proved a specific cause.

## 11. CPU measurements: how expensive is recording?

The document measures CPU consumption at both the individual FFmpeg-process level and the container level.

The distinction is important because the main processes do not account for all work performed by the system. Thumbnail processes, other application tasks, and additional background work contribute to the container total.

The measurement script is:

```
scripts/shinobi-cpu.sh SECONDS RUNS
```

The notes describe it as comparing CPU-time snapshots using process statistics and cgroup v2's `cpu.stat`.

### 11.1 What does 100% CPU mean here?

In these measurements, 100% represents approximately one fully occupied logical CPU.

That means:

- 5% represents roughly 0.05 CPU cores of average work.
- 40% represents roughly 0.4 CPU cores.
- 100% represents approximately one full core.
- 141% represents approximately 1.41 cores.

A process can exceed 100% if it uses multiple threads that run simultaneously on different logical CPUs.

The percentage does not mean that 141% of the entire 14-CPU machine is occupied. Nor is it necessarily the same as a dashboard's normalized percentage across all available cores.

### 11.2 Why the hardware conditions matter

The tests were conducted on an Intel Core Ultra 7 265U system with a mixture of performance cores and efficiency cores.

The laptop was running on battery in the quiet power profile, with measured core frequencies of roughly 1.1–1.9 GHz.

The notes observed a 2.5-fold difference in a window of CPU-time measurements for what was intended to be the same copy-recording workload.

This illustrates why CPU comparisons need controlled conditions. Processor frequency, thermal behavior, core type, and scheduling can all affect the time required to complete the same work.

The reported results are useful for comparing the tested configurations, but they should not be treated as fixed CPU requirements for every server.

### 11.3 Comparing the three recording states

| System state                   | Container CPU utilization | Interpretation                                        |
| ------------------------------ | ------------------------- | ----------------------------------------------------- |
| Live viewing only, copy mode   | 4.7%                      | Baseline for the four camera streams                  |
| Recording, one-minute segments | 9.1–9.4%                  | Includes frequent thumbnail extraction                |
| Recording, 15-minute segments  | 5.4%                      | Final configuration with much less thumbnail overhead |

The surprising result is that enabling recording in copy mode adds relatively little work to the main FFmpeg processes, but one-minute segmentation nearly doubles the measured container CPU consumption.

The explanation is the frequency of thumbnail generation.

At 15-minute segmentation, the container total returns close to the live-view baseline.

### 11.4 How does copy-mode CPU scale with bitrate?

The document measured approximate per-process CPU use in the final configuration:

| Camera   | Bitrate  | Approximate FFmpeg CPU |
| -------- | -------- | ---------------------- |
| Gate     | 2 Mb/s   | 0.9%                   |
| Quay     | 4 Mb/s   | 1.4%                   |
| Waterway | 3 Mb/s   | 1.0%                   |
| Yard     | 1.5 Mb/s | 0.7%                   |

The relationship is not perfectly linear, but the general trend is clear: higher-bitrate streams require more packet processing and data movement.

The important point is that stream copy avoids the much more expensive decoding and encoding stages.

The notes also report that the simulated camera containers, which encode the video rather than copy it, consume around 0.7–1.2 cores each according to `docker stats`.

That is a different workload from Shinobi's copy-mode recording.

## 12. Main stream versus substream: the cost of decoding

The final major performance experiment compares the CPU cost of decoding the main video stream with decoding a lower-resolution substream.

This matters when a surveillance system needs to perform motion detection, object detection, or other image analysis.

Recording the original compressed stream can be cheap, but analyzing its images generally requires decoding them.

### 12.1 What the experiment measured

The document ran FFmpeg against each stream for 30 seconds and discarded the decoded frames using the null output format.

The measurements were:

| Stream    | Pixels per second | Bitrate  | CPU time per 30 seconds | Average CPU |
| --------- | ----------------- | -------- | ----------------------- | ----------- |
| Gate main | 13.8 million      | 2 Mb/s   | 3.53 s                  | 11.8%       |
| Gate sub  | 2.3 million       | 384 kb/s | 1.03 s                  | 3.4%        |
| Quay main | 13.8 million      | 4 Mb/s   | 5.80 s                  | 19.3%       |
| Quay sub  | 2.3 million       | 384 kb/s | 1.09 s                  | 3.6%        |

The substream reduces measured decoding CPU consumption by approximately 3.4–5.3 times in these experiments.

### 12.2 Why fewer pixels help

Video decoding reconstructs image frames from compressed data.

A 1280 × 720 image contains:

\\[ 1280\times720=921600\text{ pixels} \\]

At 15 fps, that is:

\\[ 921600\times15=13{,}824{,}000 \\]

or approximately 13.8 million pixels per second.

The document's substream processes approximately 2.3 million pixels per second.

The substream therefore reduces the raw pixel rate by about sixfold.

That can substantially reduce decoding work, although the CPU reduction is not exactly sixfold.

Why not?

Because decoding cost is not determined by resolution alone. Bitrate, compression complexity, stream parsing, fixed per-process overhead, and other codec work all matter.

The document specifically notes that the quay main stream costs more than the gate main stream despite their matching resolution and frame rate. The quay stream has twice the bitrate, and its measured decoding cost is higher.

### 12.3 What this means for object detection

Suppose the application needs to record high-quality footage but also analyze every frame for potential events.

A reasonable architecture is:

- Use the main stream for recording.
- Use the substream for detection.
- Avoid transcoding the main recording unless a concrete requirement justifies it.

The notes explain that Shinobi's `details.input_maps` and `input_map_choices.detector` can route detector outputs from a second FFmpeg input. The separate `details.substream` setting, by contrast, is a different feature used for on-demand live viewing.

This distinction matters because enabling a substream for live viewing does not automatically establish that the detector is consuming that stream.

The document estimates that decoding the main streams of four cameras could consume around 60% of one CPU core, compared with around 14% for the substreams, before adding the detector itself.

That last qualification is essential. The measurements cover decoding, not the entire cost of object detection.

A detector may add significant work for inference, preprocessing, resizing, and postprocessing.

### 12.4 What do the decoder warnings mean?

The document observed messages such as:

```
Missing reference picture
decode_slice_header error
```

at the start of some stream-decoding experiments.

The author attributes them to joining a stream in the middle of a GOP. If the reader starts receiving packets while the encoder is already partway through a sequence, predictive frames may arrive before the keyframe needed to decode them properly.

In the tested setup, the next keyframe arrived within approximately two seconds. The author considers the startup warnings harmless for that experiment.

They should not, however, be treated as universally harmless in every stream or application. Persistent decoder errors or missing frames would warrant further investigation.

## 13. The final configuration, explained setting by setting

The notes end up with a configuration designed to prioritize low CPU consumption, continuous recording, reasonable storage usage, and consistent timestamps.

| Setting            | Final value                       | Why it was chosen                                       |
| ------------------ | --------------------------------- | ------------------------------------------------------- |
| Monitor mode       | `record`                          | Record continuously                                     |
| Video codec        | `copy`                            | Avoid costly re-encoding                                |
| Segment length     | `15` minutes                      | Reduce file, database, and thumbnail overhead           |
| Timestamp behavior | `wall_clock_timestamp_ignore = 1` | Preserve the tested cameras' regular timestamps         |
| CRF                | `23`                              | A reasonable fallback if transcoding is enabled         |
| Recording preset   | `veryfast`                        | Reduce encoding cost if transcoding is needed           |
| Monitor FPS        | 15 / 15 / 10 / 10                 | Avoid unintended frame-rate settings during transcoding |
| Age retention      | 1 day                             | Secondary cleanup limit                                 |
| Configured size    | 20,000 MB                         | Main practical storage limit in this experiment         |

Two details deserve emphasis.

First, CRF 23 and `veryfast` do not make copy mode cheaper. They matter only if the recording output actually transcodes. With `-vcodec copy`, the video is not encoded by x264, so the CRF and encoding preset are not the active controls for the video stream.

Second, the configured storage size is not the exact deletion threshold. The document's retention formula applies two 90% multipliers, so a configured size of 20,000 MB produces a nominal threshold of 16,200 MiB under the tested settings.

The final configuration is therefore a deliberate compromise: inexpensive video copying, 15-minute segments, regular camera timestamps, and aggressive size-based cleanup. It is not a configuration intended to preserve an entire day's footage.

## 14. Every important gotcha, translated into practical terms

The following table consolidates the document's troubleshooting findings.&#x20;

Pasted markdown.md



| Symptom                                         | Underlying reason                                                                                       | Practical response                                                                        |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Footage disappears after only a few hours       | The effective size limit is reached before the age limit                                                | Calculate the required storage and configure both limits deliberately                     |
| Fractional `max_keep_days` behaves unexpectedly | The tested JavaScript date calculation operates in whole-day terms                                      | Use whole-day retention settings                                                          |
| Videos disappear without useful logs            | Size pruning does not log the expected deletion record; the age-pruning log condition is also defective | Audit database and filesystem changes independently                                       |
| Transcoding uses more than one CPU core         | The default CRF 1 and `medium` preset cause expensive encoding                                          | Prefer copy mode when possible; otherwise benchmark an appropriate encoding configuration |
| A transcoded video records at 1 fps             | The monitor's FPS setting was effectively `1`                                                           | Set the intended frame rate explicitly                                                    |
| Segment lengths are irregular                   | The encoder's keyframe interval is longer than the segment target                                       | Coordinate the GOP interval with the intended segment duration                            |
| `moov atom not found`                           | The file may still be open or may have been interrupted before MP4 finalization                         | Verify file completion; investigate fragmented MP4 support                                |
| Container restart breaks recordings             | FFmpeg processes are terminated before closing their current files                                      | Investigate graceful shutdown and crash-resilient recording formats                       |
| The same video appears twice                    | Startup orphan recovery inserted a row while the file was still being written                           | Deduplicate records and improve completion-state validation                               |
| A database row points to an unplayable file     | Database indexing does not guarantee media integrity                                                    | Check playability before relying on the recording                                         |
| `top` shows less CPU than container statistics  | Short-lived thumbnail processes can be missed                                                           | Measure CPU using cgroup counters over a suitable interval                                |
| CPU results vary between runs                   | Power profile, frequency, and heterogeneous CPU cores affect execution time                             | Compare configurations under controlled conditions and repeat measurements                |

These are not all the same type of bug. Some are configuration mistakes, some are expected architectural trade-offs, and others are implementation defects or unresolved reliability questions.

Separating those categories makes the system easier to troubleshoot.

## 15. The commands in the document, explained

The notes include a small operational cheat sheet. Here is what each command is intended to tell you.

### 15.1 Count recordings and inspect their sizes

```
docker compose exec shinobi mysql ccio -e \
"select mid, count(*) n, round(sum(size)/1048576) MiB, min(time) oldest, max(time) newest from Videos group by mid"
```

This groups database rows by monitor.

The output tells you:

- How many indexed videos each monitor has.
- The total indexed video size in MiB.
- The oldest recorded start time.
- The newest recorded start time.

It is useful for checking retention and recording coverage, but it does not prove every file is playable. Duplicate rows can also inflate the counts and size totals.

### 15.2 Inspect account retention settings

```
docker compose exec shinobi mysql ccio -N -B -e \
"select json_value(details,'$.days'), json_value(details,'$.size') from Users"
```

This queries the JSON settings stored in the `Users` table.

It helps distinguish a retention problem caused by the configured values from one caused by the retention implementation.

### 15.3 Inspect disk usage

```
docker compose exec shinobi sh -c \
'du -sm /home/Shinobi/videos/*/cam_*'
```

This estimates disk usage for the matched recording directories in decimal megabytes, as reported by `du -m`.

Compare it with the database totals, remembering that the database may contain duplicates or incomplete metadata and that this command does not account for data outside the matched directories.

### 15.4 Check whether a video can be parsed

```
ffprobe -v error -select_streams v -count_packets \
-show_entries stream=nb_read_packets -of csv=p=0 recording.mp4
```

This asks FFprobe to inspect the video stream and count its packets.

If the command returns an error such as `moov atom not found`, the file is not being parsed as an ordinary playable MP4 in the tested way.

A successful packet count is useful evidence, but it is not a complete guarantee of successful playback from beginning to end.

### 15.5 Inspect the recording output in the live FFmpeg command

```
docker compose exec shinobi sh -c \
'for p in $(pgrep -x ffmpeg); do tr "\0" " " < /proc/$p/cmdline | grep -o "\-f segment.*"; done'
```

This inspects the running FFmpeg processes' command lines and searches for the segment-muxer arguments.

It can help verify whether the process is configured for recording, which segment target it uses, and where the output is written.

The output depends on the shell and regular expression matching the actual command line. It is a diagnostic aid rather than a comprehensive configuration audit.

### 15.6 Measure CPU consumption

```
scripts/shinobi-cpu.sh 60 3
```

This runs the measurement script with a 60-second measurement interval and three runs, according to the document's described interface.

It is useful for comparing configurations, particularly when short-lived thumbnail processes are otherwise difficult to observe.

### 15.7 Check whether the cron worker ran

```
docker compose logs shinobi | grep "CRON.js LOG"
```

This searches Shinobi's container logs for the cron worker's log messages.

Because the notes identify a problem with the age-retention deletion-count logging, absence of an expected deletion message should not be interpreted as proof that no deletion occurred.

## 16. The three unresolved engineering questions

The investigation does not claim that every reliability problem has been solved.

It explicitly leaves three questions for a subsequent session.

1\. Will fragmented MP4 work throughout Shinobi's pipeline?

The proposed `frag_keyframe` and `empty_moov` flags may improve crash resilience. But the team still needs to verify that thumbnail extraction, browser playback, and the rest of Shinobi's recording workflow accept the resulting files.

2\. What caused the one-time 870/930 frame split?

The document observes that two consecutive files after changing timestamp behavior contained different frame counts. It does not establish a definitive cause, and subsequent recordings returned to the expected frame count.

3\. Can the container shut down gracefully?

The team needs to test whether Docker's `stop_grace_period`, combined with Shinobi's own shutdown procedure, can finalize active segments instead of leaving them unplayable.

These questions are important because the current setup demonstrates that ordinary recording can work well while forced shutdowns remain dangerous.

The next step is not simply to optimize CPU. It is to improve recording integrity and recoverability without breaking the application's file-indexing and playback behavior.

## 17. The deeper engineering lessons

Several broad lessons emerge from the measurements.

### Lesson 1: Optimize the entire pipeline, not just the main process

The main FFmpeg process is not the whole workload. Thumbnail extraction and other short-lived operations can account for a meaningful share of CPU usage.

Measure at the container level as well as the process level.

### Lesson 2: Treat metadata and media as separate sources of truth

The database tells you what Shinobi believes exists. The filesystem tells you what files exist. FFprobe or an actual playback test tells you whether a file can be parsed or played.

A reliable system should reconcile these sources rather than assuming they are always consistent.

### Lesson 3: Recovery needs to distinguish complete files from active files

An orphan scan is useful, but merely finding a file on disk is insufficient evidence that it is complete.

The duplicate-row experiment shows why recovery and normal completion processing need to coordinate.

### Lesson 4: Retention settings are coupled

Age limits and size limits are not independent in their practical effect. If the size threshold is reached first, footage can disappear long before the age setting expires.

Storage capacity must be calculated from bitrate and required retention duration.

### Lesson 5: Keyframes affect both usability and reliability

The GOP length affects segment alignment, how quickly a new file can start, and the decoder's ability to recover after joining a stream.

A long GOP can undermine the assumption that a nominal segment duration maps cleanly to a file duration.

### Lesson 6: Recording and detection are different workloads

Copying a compressed stream is relatively cheap. Decoding every frame is more expensive, and running an object detector adds another layer of computation.

Using a substream for detection while preserving the main stream for recording is a sensible architecture when the lower-resolution stream meets the detector's needs.

### Lesson 7: Reliability must be tested with failures, not just steady-state operation

The document's most important findings came from intentionally stopping cameras, killing FFmpeg, editing monitors, and restarting the container.

A system that works during a normal ten-minute recording test may still lose footage during a restart or create incorrect database rows during recovery.

## 18. Final mental model

The easiest way to understand the entire system is to think of it as four interconnected pipelines.

Pipeline A — Media

Camera → MediaMTX → FFmpeg → HLS and MP4.

Responsible for receiving, processing, and writing the video.

Pipeline B — Indexing

Finished segment → Completion notification → Metadata inspection → MariaDB row.

Responsible for making recordings discoverable in the application.

Pipeline C — Retention

Age-based cleanup + size-based pruning → File and metadata deletion.

Responsible for keeping storage usage within the configured limits.

Pipeline D — Resource usage and recovery

Stream copy + thumbnail extraction + decoding + watchdogs + restart handling.

Responsible for performance monitoring and recovering from failures, though some recovery gaps remain.

A failure in one pipeline can affect another. For example, FFmpeg may stop writing a file, yet the database may still contain a row. Or retention may delete a recording without producing the expected log message.

The core challenge is ensuring that these pipelines agree about what has been recorded, what is complete, what remains available, and what can safely be deleted.

My overall conclusion: the document provides strong experimental evidence that stream-copy recording with 15-minute segments is substantially more CPU-efficient than transcoding or one-minute segmentation in this four-camera setup. It also reveals meaningful correctness risks in crash handling, orphan recovery, database indexing, and retention observability. The biggest remaining improvement is to make recording finalization and recovery reliable, not merely to make recording faster.

## Resources and further reading

These resources provide useful background for understanding the mechanisms discussed. They are general technical references, not additional experimental evidence for the particular Shinobi revision in the notes.

- [FFmpeg documentation](https://ffmpeg.org/ffmpeg.html?utm_source=chatgpt.com) — command-line options, inputs, outputs, stream copy, and timestamp behavior.
- [FFmpeg formats documentation](https://ffmpeg.org/ffmpeg-formats.html?utm_source=chatgpt.com) — segment muxer, MP4 muxer, segmentation, and format-specific flags.
- [FFmpeg codecs documentation](https://ffmpeg.org/ffmpeg-codecs.html?utm_source=chatgpt.com) — codec configuration and encoding concepts.
- [Shinobi documentation](https://docs.shinobi.video/?utm_source=chatgpt.com) — monitor configuration, recording, and application behavior.
- [MediaMTX documentation](https://mediamtx.org/docs?utm_source=chatgpt.com) — RTSP streams, stream publishing, and reading media.
- [MariaDB documentation](https://mariadb.com/kb/en/documentation/?utm_source=chatgpt.com) — SQL queries, JSON functions, and database operations.
- [Docker documentation](https://docs.docker.com/compose/?utm_source=chatgpt.com) — Compose services, container lifecycle, and shutdown configuration.

For the exact implementation details and measurements, the uploaded Session 10 note is the primary source. The most relevant sections are §2–3 for segmentation and indexing, §4 for copy versus transcode, §5 for retention, §6 for timestamps, and §7 for CPU measurements.&#x20;

Pasted markdown.md



My Trust: 9/10

Check the uncertain points: The main qualifications are the three unresolved questions in the original document, the fact that CPU and storage figures are specific to the measured setup, and the possibility that Shinobi's implementation may behave differently in other revisions. The explanations of the architecture are grounded in the supplied notes; recommendations about stronger recovery and validation are engineering interpretations rather than verified fixes.



I thought that shinobi create a row in db only after finalizing the file

You're right to question that. Under the normal Shinobi recording workflow described in the notes, the `Videos` database row should be created when FFmpeg reports that a segment has finished—not when the file is first created.

The apparent contradiction comes from the orphan-recovery mechanism.

## What should normally happen

1\. FFmpeg writes the MP4

The file exists on disk, but it is still being recorded. Normally, there is no `Videos` row for this segment yet.

2\. FFmpeg closes the segment

After closing and finalizing the MP4, FFmpeg sends the completed filename through `pipe:8`.

3\. Shinobi creates the database row

The completion handler processes the filename and inserts the metadata into `Videos`.

## So how did the premature row appear?

The notes describe a second path: Shinobi's startup orphan scan.

That scan searches the filesystem for video files that do not have corresponding database entries. The observed problem was that it apparently found a file that was still being written.

The resulting sequence was:

T1 — Recording in progress

File exists and is growing. The orphan scan discovers it and creates a premature row.

T2 — Recording finishes

FFmpeg closes the MP4 and sends its normal completion notification.

T3 — Duplicate database entry

The completion handler inserts another row for the same physical file, this time with the final size and end time.

The notes give this example:

|            | Premature row | Normal completion row |
| ---------- | ------------- | --------------------- |
| Start time | 11:28:00      | 11:28:00              |
| End time   | 11:28:02      | 11:29:00              |
| Size       | 524,336 bytes | 14,995,169 bytes      |

Both rows refer to the same recording file.

## One important qualification

The notes report this behavior as an experimental finding, but they do not establish exactly why the orphan scan ran while the file was active or whether the same race occurs in every Shinobi version.

If the normal completion handler also checks for an existing row, updates it, or uses a uniqueness constraint in your version, that could change the outcome. We would need to inspect the actual code to confirm.

The key distinction is: Shinobi's normal completion path creates the row after finalization; the reported premature row came from the separate orphan-recovery path.

### Resources

- [Shinobi documentation](https://docs.shinobi.video/?utm_source=chatgpt.com)
- The investigation notes, particularly the sections describing `postProcessCompletedMp4Video()` and the startup orphan scan.

My Trust: 9/10

Check the uncertain points: The intended normal workflow is clear from the notes. The precise conditions that allow the orphan scan to index an active file—and whether your installed version has protections against this—remain to be verified.




what is cgroup?

A cgroup (short for control group) is a Linux feature that lets the operating system limit and monitor how much of a computer’s resources a group of processes can use.

Think of it as a resource budget for a program or service.

## What can a cgroup control?

- &#x20;CPU: Limit how much processing time a program can use.
- &#x20;Memory (RAM): Set a memory limit to help prevent a program from consuming all available RAM.
- &#x20;Disk I/O: Control or monitor how much disk-reading and writing activity processes perform.
- &#x20;Processes: In some configurations, limit how many processes a group can create.

## How does this relate to Shinobi and Docker?

Suppose Shinobi is recording video from several CCTV cameras using FFmpeg.

Linux operating system

Docker container: Shinobi

A cgroup can enforce resource limits

CPU limit

RAM limit

Shinobi and FFmpeg processes

For example, if a Docker container has a 2-CPU quota, its processes can collectively use up to the equivalent of two CPU cores over time. This does not necessarily mean they are assigned two specific cores.

If FFmpeg suddenly needs more CPU than the cgroup allows, Linux throttles the processes. Video processing may then fall behind, depending on the workload.

## Is a cgroup the same as a Docker container?

No.

- Docker container: Packages and isolates an application's execution environment.
- cgroup: Enforces resource accounting and limits for processes.
- Docker uses cgroups on Linux to apply container resource limits.

## How can you inspect it?

For a Docker container named `shinobi`, you can check configured limits with:

```
docker inspect shinobi
docker stats shinobi
```

`docker inspect` shows configuration, while `docker stats` shows live resource usage. The exact cgroup settings depend on the Docker and Linux versions and configuration.

In short: A cgroup is Linux's mechanism for controlling how much CPU, RAM, and other resources a group of processes can consume.

Resources

- [Linux kernel documentation: Control Group v2](https://docs.kernel.org/admin-guide/cgroup-v2.html?utm_source=chatgpt.com)
- [Docker documentation: Resource constraints](https://docs.docker.com/engine/containers/resource_constraints/?utm_source=chatgpt.com)

My Trust: 10/10

Check the uncertain points: The exact limits and cgroup version used by your Shinobi installation depend on your host and Docker configuration.



explain how does the bitrate of both main streams differ although the pixels per second is the same. give example

The key idea is: pixels per second tells you how many pixels the camera produces each second; bitrate tells you how much data is used to represent those pixels each second. They are related, but they are not the same thing.

## 1. Same pixels per second, different bitrate

Imagine the two Shinobi main streams have these settings:

| Setting           | Gate main stream | Quay main stream |
| ----------------- | ---------------- | ---------------- |
| Resolution        | 1920 × 1080      | 1920 × 1080      |
| Frame rate        | 25 FPS           | 25 FPS           |
| Pixels per frame  | 2,073,600        | 2,073,600        |
| Pixels per second | 51.84 million    | 51.84 million    |
| Bitrate (example) | 4 Mbps           | 8 Mbps           |

Both streams produce the same number of pixels per second:

\\[ 1920 \times 1080 \times 25 =51{,}840{,}000 \\]

But the quay stream uses twice the bitrate.

That means the quay stream sends approximately twice as many encoded bits per second, even though it produces the same number of pixels.

The 4 Mbps and 8 Mbps figures are illustrative examples, not verified measurements from your document.

## 2. Why does the quay stream need more bits?

Video compression does not simply store every pixel independently. It tries to represent the visual information efficiently.

[Station de Gosselies | AIBV SA](https://images.openai.com/static-rsc-4/PV6ZC_iOlsCgLoanI_OzOiiATrLXMz8Uoj3IDSbTNcGA0CCVB7JlqR9LfZLSt-UZvkUMiAXBZP2qQ9hvPB1OZTcf7DajBHaRG2jF_GHU3LtA2gHQMX5kH1JbcHCCyV30BhcneIEjcKBh4hOu765YYv8JzVvmJcPXTKYndkjcC14?purpose=inline)

Gate: relatively simple scene

Imagine a fixed camera looking at a quiet entrance. Most of the background stays the same from frame to frame, with only occasional movement. The encoder may represent many parts of the scene efficiently.

[Nowa kamera! Oglądaj port we Władysławowie](https://images.openai.com/static-rsc-4/WWj9W3bg2AI5eG__jc1egnYnxZBul3qCIlAt6piUvtUh-Uxzn3pdlbYR0z9MtEngvzmhPi7HfCoBP6s5TlNRf-wXXQOlOx-HtH5BJi4snecwKMDTEsti9uoUj43ffdO57uG-rNTMksEV0ZERuTXHjGZ77ceK2F4nrw5LS2fTOY8?purpose=inline)

Quay: more complex scene

Imagine a camera looking at water, moving boats, reflections, and people. The scene changes more frequently, so the encoder may need more bits to preserve similar visual quality.

Other factors can also increase bitrate:

- More detail: Fine textures, foliage, gravel, and complex surfaces are harder to compress.
- More movement: Motion between frames can require additional information.
- Noise: Low-light sensor noise can make an image harder to compress, even when the scene looks relatively still.
- Encoder settings: A higher quality target or a less aggressive compression setting can produce a higher bitrate for the same scene.

These are possible explanations, not proof of which factor causes the difference in your particular cameras.

## 3. How can bitrate affect CPU usage?

This is the important distinction for your Shinobi analysis.

The main stream's bitrate can influence decoding cost, but twice the bitrate does not automatically mean twice the CPU usage.

For example:

- Gate: 4 Mbps, 1080p at 25 FPS.
- Quay: 8 Mbps, 1080p at 25 FPS.

Both decoders still process 25 frames per second at the same resolution. However, the quay stream's compressed video may contain more complex information to decode, depending on its codec, encoding settings, and scene content.

Also, bitrate directly affects the amount of compressed data that must be read and transferred. But decoding CPU cost depends on more than bitrate alone; codec, profile, reference frames, compression structure, and hardware acceleration can matter substantially.

One important nuance: a higher bitrate does not necessarily mean the video is harder to decode. A high bitrate can simply mean the encoder uses less compression, which can sometimes make decoding no harder at all.

## 4. What can you conclude from your document?

The document reports that the quay main stream has a higher bitrate and a higher measured decoding cost than the gate main stream, despite matching resolution and frame rate.

That establishes an observed correlation in your measurements. It does not, by itself, establish that the higher bitrate caused the higher decoding cost. To investigate the cause, compare the streams' codec and profile, encoder settings, and CPU measurements under controlled conditions.

Resources

- [FFmpeg documentation](https://ffmpeg.org/ffmpeg.html?utm_source=chatgpt.com)
- [H.264 video compression overview — ITU](https://www.itu.int/rec/T-REC-H.264?utm_source=chatgpt.com)

My Trust: 9/10

Check the uncertain points: The exact bitrate values and the reason for the quay stream's higher measured decoding cost need to be confirmed from your document and stream metadata.



explain the meaning of transcodes

Transcoding means converting a video or audio stream from one format, codec, resolution, or bitrate to another by decoding it and then encoding it again.

In your Shinobi CCTV setup, this matters because transcoding can require significantly more CPU than simply copying an existing video stream into a recording file.

## 1. Transcoding vs. copying

Imagine your camera sends an H.264 video stream to Shinobi.

### A. Without transcoding — stream copy

Camera sends H.264 video

Shinobi copies the compressed video into an MP4 file

Usually low CPU cost for video processing

### B. With transcoding

Camera sends H.264 video

Decode the compressed video

Encode it again, perhaps at a different bitrate or resolution

Shinobi writes the newly encoded video to an MP4 file

Typically higher CPU cost

A stream copy does not decode and re-encode the video itself. FFmpeg still has to perform other work, such as handling the container and writing the file.

## 2. Example with CCTV recordings

Suppose your camera sends video at 1920 × 1080, 25 FPS, with a bitrate of 8 Mbps.

- Stream copy: Shinobi records that compressed stream directly into MP4. The resolution, frame rate, and encoded video quality remain essentially unchanged.
- Transcoding: FFmpeg decodes the video and re-encodes it at, for example, 1280 × 720 and 3 Mbps. This can reduce storage use, but it requires extra processing.

Changing only the container from one compatible format to another does not necessarily require transcoding. For example, copying H.264 video into an MP4 container is usually called remuxing, not transcoding.

## 3. How to recognize it in FFmpeg

A common stream-copy option is:

```
-c:v copy
```

This tells FFmpeg to copy the video stream without re-encoding it.

An encoding option might look like:

```
-c:v libx264
```

This tells FFmpeg to encode the video using the x264 H.264 encoder. If the input is already H.264, this still involves decoding and re-encoding rather than copying.

The exact command matters: audio and video can be handled differently. For example, FFmpeg could copy the video but transcode the audio.



which packets are counted? RTP only? or RTP & RTSP or ...

FFprobe counts media packets from the video stream, not RTP or RTSP protocol packets. The exact answer depends on the FFprobe command you used and whether it reads a camera stream or a recorded file.

These are different layers of the video pipeline:

RTSP

Controls the streaming session

RTP

Transports media data over the network (in a common setup)

H.264/H.265 video stream

Compressed video data reconstructed from the transport

FFprobe media packets

Packets recognized by the media demuxer

RTSP can also use RTP over TCP, or media can be carried in other ways. FFprobe's packet count is not a count of all the network packets in those transports.

For example, if the command is:

```
ffprobe -count_packets -select_streams v:0 \
  -show_entries stream=nb_read_packets input.mp4
```

FFprobe counts video packets in the file's media stream—not the RTP packets or RTSP messages used to transport the stream. The `-select_streams v:0` option selects the first video stream, and `-count_packets` counts its demuxed media packets.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://www.ffmpeg.org\&sz=32)

ffmpeg.org+1



### Example

Imagine a camera sends one second of video at 25 FPS:

- RTSP messages: Session-control requests and responses; not included in the media packet count.
- RTP packets: Network transport packets carrying pieces of the encoded video; not counted individually by this command.
- FFprobe video packets: The compressed video packets FFprobe reads from the demuxed stream; these are what gets counted.
- Video frames: 25 frames per second in this example, but the number of FFprobe packets need not equal 25.

One video frame can be carried across multiple RTP packets, and the relationship between demuxed packets and decoded frames depends on the codec and container.

Important for your Shinobi analysis: If you ran FFprobe against a completed MP4 file, the count refers to packets read from that file. If you ran it directly against an RTSP URL, FFprobe still reports media packets after demuxing the stream—not raw RTP network packets.

Resource: [Official FFprobe documentation](https://ffmpeg.org/ffprobe.html?utm_source=chatgpt.com)

My Trust: 10/10

Check the uncertain points: The exact count depends on the input and command options. If you share your precise FFprobe command, I can explain exactly what it counts.

