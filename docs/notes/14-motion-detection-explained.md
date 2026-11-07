# Session 14 — Motion Detection: Detailed Explanation

Your document describes how to configure and optimize motion detection in Shinobi CCTV/NVR, using its built-in pixel-change detector.

The main objective is not simply to detect movement. It is to detect meaningful movement while ignoring false alarms caused by:

- Camera shaking.
- Water and clouds.
- On-screen clocks.
- Compression noise.
- Sudden changes between looping video clips.

I'll explain the concepts, technical architecture, configuration, measurements, discovered bug, and final results step by step.

# 1. What is the objective of Session 14?

The goal is to make Shinobi detect meaningful events in two cameras:

1. `cam-gate`: Detect vehicles passing through the gate lane.
2. `cam-quay`: Detect containers being lowered by a crane.

There are also two cameras used as controls:

- `cam-waterway`: Contains moving water and clouds.
- `cam-yard`: Mostly static scenery.

The important distinction is between real movement and irrelevant pixel changes.

For example:

| Situation                    | Should Shinobi detect it? |
| ---------------------------- | ------------------------- |
| Car passing through the gate | Yes                       |
| Container being lowered      | Yes                       |
| Water moving                 | No                        |
| Camera shaking               | No                        |
| OSD clock changing           | No                        |
| Video loop restarting        | No                        |
| Stationary car               | No                        |

The final system uses:

- One detection region on `cam-gate`.
- Three detection regions on `cam-quay`.
- The camera substream for detection.
- YAML configuration to define detection settings.
- Shinobi's built-in PAM/pixel-change detector.

The final measurements show that the system detects meaningful events while producing zero events on the two inactive control cameras.

# 2. Understanding motion detection

## 2.1 What is motion detection?

Shinobi's built-in detector uses a relatively simple principle:

Compare two consecutive images and measure how many pixels changed.

It does not understand what an object is.

It does not know whether the moving object is:

- A car.
- A person.
- A container.
- A cloud.
- Water.

It only measures differences between images.

This is called pixel-change detection.

For example:

[Arabako kontzejuak kexu azaldu dira LEAren fabrika berriaren kokapena dela eta](https://images.openai.com/static-rsc-4/9PLACCHVweh1HxRr6_IgBiVzPu4aVuu7wm1r6eOsa1pH3jyVYrUEoB_PbbCVGnGdbjx82deRnv-GZJJjfU2nhvNdme0NhDrPb8rlHpaWp47w5i1HbQZYsxt7LANfkWnOeaKrbsMhzu_wifv_0-CwfJdQjN2ks5iqTXuWPRocURc?purpose=inline)

Frame 1: Previous image

[Los camiones siguen rodando | Axis Communications](https://images.openai.com/static-rsc-4/tXyxzmjQJ7AHW3hhuT4IcoO7Rz9B5r0tgjOrGRnJqJbECoY67UhboOsj-HTwBevQ268SgR94VN8MpCJr7N4ocUcPc5hhD-TpN5bPaT7_1-2UerbOeNsc0AjWSRFWFTaxW6yFxNCGkVkm4o1AbLk9i25YTfERPvxOQ5GFM2ilxzw?purpose=inline)

Frame 2: Current image

The detector compares the two images and identifies pixels whose brightness has changed.

## 2.2 What is luma?

Luma represents the brightness of a pixel.

In this detector, brightness is represented using values between 0 and 255.

| Value | Meaning           |
| ----- | ----------------- |
| 0     | Black             |
| 50    | Very dark         |
| 128   | Medium brightness |
| 200   | Bright            |
| 255   | White             |

The detector calculates:

\\[ \Delta L = |L_t-L\_{t-1}| \\]

Where:

- \\(L_t\\): Current pixel brightness.
- \\(L\_{t-1}\\): Previous pixel brightness.
- \\(\Delta L\\): Absolute brightness difference.

For example:

Previous frame: 100

Current frame: 120

\\[ \Delta L=|120-100|=20 \\]

If the configured `color_threshold` is 9:

\\[ 20 \geq 9 \\]

The pixel is classified as changed.

If the difference is only 5:

\\[ 5 < 9 \\]

The pixel is ignored.

Why is this useful?

It prevents very small brightness fluctuations from automatically being counted as motion.

However, a threshold that is too low allows compression noise to trigger detection, while a threshold that is too high can miss objects with similar brightness to their surroundings.

## 2.3 Why does the detector detect edges rather than entire objects?

Imagine a white truck moving against a grey road.

The middle of the truck may remain almost the same brightness between two frames.

However, its position changes.

Therefore:

- The old truck boundary disappears.
- The new truck boundary appears.
- The surrounding pixels change.

The detector mostly identifies these changing edges.

This explains an important concept in your document:

Changed area is usually smaller than object area.

A truck might occupy 20% of a region, but only 5% of the region may have changed between two consecutive frames.

This is why the sensitivity value needs careful tuning.

# 3. How Shinobi evaluates motion

The detection process has several stages.

Previous frame and current frame

Two consecutive grayscale images

Pixel comparison

Calculate absolute luma differences

Color threshold

Keep pixels where Δluma ≥ color_threshold

Region percentage calculation

Measure changed pixels inside each polygon

Blob detection

Check whether a sufficiently large connected group exists

Shinobi trigger

Accept the detection after the configured consecutive-frame checks

Let's understand the important parameters.

## 3.1 Sensitivity

Sensitivity defines the minimum percentage of a region that must change.

The formula is:

\\[ P=\frac{\text{Changed pixels}}{\text{Total region pixels}}\times100 \\]

Example:

Suppose a detection region contains 10,000 pixels.

A vehicle changes 400 pixels.

\\[ P=\frac{400}{10000}\times100=4\\% \\]

If sensitivity is 3%, the initial percentage condition passes.

If sensitivity is 5%, it fails.

This explains why the region size is important.

The same vehicle may produce:

- 4% change inside a small road region.
- 0.9% change across the full camera image.

With a full-frame sensitivity of 10%, the detector misses the vehicle.

Main lesson: Sensitivity is relative to the selected region, not the entire image.

## 3.2 Blob detection

A blob is a connected group of changed pixels.

Consider two situations:

[python - Detecting and counting blobs/connected objects with opencv - Stack Overflow](https://images.openai.com/static-rsc-4/s6SKRKOnqbl-4b3zNEYP3GJHJVjCN5GzmK7njgRFYv4LUJ_WV5Y_y8R-jHHBTH-PX0A8Aid9w_txPk9wc6wbbrpsaN0gEoQ7uhT2okAXxGw52qZ-1a0Z5xuJg-QwDUjggyiF2vTlcmjimFSsKXWajFnTcIjJXIZjxyknEhF7R4o?purpose=inline)

Large connected area

Potential meaningful object movement.

[Activity 8: Morphological Operations | by Ralph Aaron Aguinaldo | Medium](https://images.openai.com/static-rsc-4/rEebpHgeFApGUw5e36_x7YiX5Wa0Ts9K67Ws3_wq3Hw9tOfdgpAskcHpBIcgcx09q4qn-mBSzPJdOUGWcjLE9Q3erSmYNu08VCDNeZyhRRpnX3OX59HDH_7hIHeRmneidqKOk-94W5RaPeirhtz0G_NNzDTs5uMHKsbQd9hYN58?purpose=inline)

Scattered pixels

Potential compression noise.

Why is blob detection necessary?

Imagine 100 changed pixels distributed randomly across a region.

They might be caused by compression noise.

Now imagine 100 connected changed pixels forming a coherent shape.

That is more likely to represent an actual moving object.

Shinobi uses connected-component labelling to identify these groups.

According to the document, at least one blob must reach the configured percentage of the region.

This adds another filter beyond the total changed-pixel percentage.

## 3.3 Threshold

The `threshold` parameter specifies how many consecutive detector frames must satisfy the detection conditions.

For example:

- `threshold: 1`: One qualifying frame.
- `threshold: 2`: Two consecutive qualifying frames.
- `threshold: 3`: Three consecutive qualifying frames.

Increasing the threshold reduces short-lived false detections.

However, too high a value may miss fast-moving objects.

Your final configuration uses values of 2 or 3, depending on the region.

## 3.4 Maximum sensitivity

The `max_sensitivity` parameter establishes an upper limit.

This is especially important for:

- Camera movement.
- PTZ movements.
- Infrared day/night switching.
- Video loop transitions.

For example, suppose a region experiences 85% pixel change because the camera suddenly moves.

If the maximum sensitivity is 40%, the detector rejects this trigger.

This helps prevent large scene changes from being interpreted as ordinary motion.

## 3.5 Motion lock

The motion lock is controlled by:

`detector_lock_timeout`

It specifies the minimum interval between accepted events for one monitor.

Your final configuration uses:

\\[ 5000\text{ ms}=5\text{ seconds} \\]

Imagine a container being lowered for 6 seconds.

The detector might identify several changes during that period.

However, the motion lock prevents an event from being accepted every moment.

This reduces event duplication.

An important distinction:

- Detector trigger: A region passes the detection conditions.
- Accepted event: The main Shinobi process accepts it after filtering and motion-lock checks.

The motion lock does not necessarily mean one event per physical activity. It only limits the frequency of accepted events.

# 4. Understanding Shinobi's architecture

This is one of the most important sections because it explains where detection actually happens.

Your system uses MediaMTX, FFmpeg, PAM, a Node.js camera thread, and Shinobi's main process.

## 4.1 Overall architecture

MediaMTX

Provides RTSP camera streams

FFmpeg

Receives main stream and substream

Main stream

Live view and continuous recording

Substream

Grayscale PAM frames for detection

PAM / pam-diff

Compares consecutive detector frames

detectorUtils.js

Applies filters and evaluates regions

Main Shinobi process

Filters triggers and applies motion lock

Events database

Stores accepted events when saving is enabled

## 4.2 What is FFmpeg doing?

FFmpeg is responsible for receiving and processing the camera streams.

The document shows two RTSP inputs:

```
-i "rtsp://mediamtx:8554/cam-gate"
-i "rtsp://mediamtx:8554/cam-gate-sub"
```

The first input is the main stream.

The second input is the substream.

FFmpeg produces several outputs:

| Output   | Purpose              |
| -------- | -------------------- |
| HLS      | Live viewing         |
| Segment  | Continuous recording |
| PAM pipe | Motion detection     |

The detector output is:

```
-map 1:0
-vf "fps=5"
-s 640x360
-an
-c:v pam
-pix_fmt gray
-f image2pipe pipe:3
```

Let's explain the important parts.

- `-map 1:0`: Selects video stream 0 from input 1 (the substream).
- `fps=5`: Outputs five detector frames per second.
- `640x360`: Detector resolution.
- `-an`: No audio.
- `-c:v pam`: Uses PAM image format.
- `-pix_fmt gray`: Converts the image to grayscale.
- `pipe:3`: Sends raw image data through file descriptor 3.

## 4.3 What is PAM?

PAM means Portable Arbitrary Map.

It is an image format from the Netpbm family.

In your system, FFmpeg generates grayscale images and sends them through a pipe.

Why?

Because the detector does not need to display a normal video.

It only needs pixel brightness values.

Grayscale frames are therefore sufficient.

This reduces the amount of data that needs to be processed.

## 4.4 What is pam-diff?

`pam-diff` is the module used by Shinobi to compare consecutive PAM frames.

It uses a C++ addon to perform pixel comparisons efficiently.

The Node.js camera thread receives the detector output and passes it through the detection utilities.

The important files identified in your document are:

- `libs/cameraThread/singleCamera.js`
- `detectorUtils.js`
- `camera.js`
- `libs/events/utils.js`

These files represent different stages of the detection and event-processing pipeline.

# 5. Why use the substream instead of the main stream?

Your measurements demonstrate that the substream is much more efficient.

The main stream is 1280×720 at 15 FPS.

The substream is 640×360 at 10 FPS.

Detection uses five frames per second from the substream.

| Measurement      | Main stream | Substream |
| ---------------- | ----------- | --------- |
| Resolution       | 1280×720    | 640×360   |
| Original FPS     | 15          | 10        |
| Detector FPS     | 2           | 5         |
| FFmpeg CPU, gate | 11.1%       | 5.0%      |
| FFmpeg CPU, quay | 17.2%       | 5.7%      |

FFmpeg CPU usage per camera

Comparison of measured detector CPU usage on main and substreams.

Main stream

Substream

0%5%10%15%20%GateQuay

The document concludes that substream detection uses approximately one-third of the CPU per camera compared with main-stream detection.

Why?

Because video decoding is expensive.

A higher-resolution stream contains more pixels that must be decoded.

For example:

\\[ 1280\times720=921600 \\]

\\[ 640\times360=230400 \\]

The substream has only one-quarter as many pixels per frame.

Despite running detection at a higher frame rate, it is still much cheaper.

Important disadvantage: Both inputs run in the same FFmpeg process. If the substream fails, FFmpeg exits, potentially restarting the main recording process as well.

# 6. Understanding detection regions

This is probably the most important configuration decision in the entire session.

Instead of analyzing the whole image, Shinobi analyzes selected polygons.

Why?

Because irrelevant image areas create false positives.

For example:

- The sky contains moving clouds.
- The water contains continuous movement.
- The OSD clock changes every second.
- The crane camera experiences movement.

A region allows the detector to focus only on useful areas.

## 6.1 Gate camera

[Warehouse AI Video Analytics | Safety & Logistics | FlowLinks](https://images.openai.com/static-rsc-4/k1Prejpm621mHtAv6gPE6qCzYfTo-nMnR9KfyQ1yKfoNysJp0n4obLp7kU99z3vqfIa7YBxC4Oah6RsKh1YrMsYoTQVCyksRhT-sLFxYDwHgzZnAGm8IUgm_5JdZTmKEez4ZkIW2iIv908YuGf92HstJ-O9BGmIKr0djZeLQaz4?purpose=inline)

[flowlinks.ai](https://www.flowlinks.ai/warehouse-management?utm_source=chatgpt.com)

The gate camera uses one region:

`gate_lane`

This region covers the road and both lanes.

It excludes:

- The OSD clock.
- The sky.
- The cranes.
- Irrelevant parts of the image.

The purpose is to detect passing vehicles.

## 6.2 Quay camera

[新漢旗下綠基 登錄興櫃交易 | 產業熱點 | 產業 | 經濟日報](https://images.openai.com/static-rsc-4/93k-Vz7RVXfDuFWjfHC7h93rokduR28mhIeeyBGKmb5LWGnf4iZhBl7EOfGYuGF5g1KWf1UXXUPpy99Fk_IihQSA8EJHoFvIgS_KJRHAU4RVyoZlO6Pstsx3zUCMHsc2H6kt6vLLykarX0C-CH9kRgfRYk8FjknkPxgXEsoC8PU?purpose=inline)

[money.udn.com](https://money.udn.com/money/story/5612/9406912?utm_source=chatgpt.com)

The quay camera uses three regions:

| Region            | Purpose                      |
| ----------------- | ---------------------------- |
| `hoist`           | Detect container lowering    |
| `berth_edge`      | Monitor the berth boundary   |
| `restricted_quay` | Monitor the restricted floor |

However, the final results show that only `hoist` is reliable for the intended movement.

The other two regions remain silent because camera shake produces too much background noise.

This is not necessarily a configuration failure. It reveals the limitation of pixel-based detection.

## 6.3 How are regions defined?

Your configuration uses YAML.

A region can be defined using:

- `points`: Coordinates expressed as fractions from 0 to 1.
- `points_px`: Coordinates expressed in detector pixels.

For example, normalized coordinates:

```
points:
  - [0.10, 0.20]
  - [0.80, 0.20]
  - [0.80, 0.80]
  - [0.10, 0.80]
```

These coordinates describe a polygon.

The advantage of normalized coordinates is that they remain proportional when the detector resolution changes.

Pixel coordinates, on the other hand, correspond directly to the detector image.

Your validation rules include:

- At least four points per polygon.
- Coordinates must be inside the image.
- Region names must be unique.
- Maximum sensitivity must be greater than minimum sensitivity.
- Cameras must exist.
- Renderer-controlled keys cannot be overridden.

Cameras not listed in the configuration have detection disabled.

# 7. Understanding the configuration parameters

Here is the practical meaning of the most important settings.

| Parameter                    | Final value | Explanation                         |
| ---------------------------- | ----------- | ----------------------------------- |
| `detector`                   | 1           | Enables detector                    |
| `detector_pam`               | 1           | Uses PAM detection                  |
| `detector_send_frames`       | 1           | Enables detector frame output       |
| `detector_fps`               | 5           | Five detector frames per second     |
| `detector_scale_x`           | 640         | Detector width                      |
| `detector_scale_y`           | 360         | Detector height                     |
| `color_threshold`            | 9           | Minimum pixel brightness difference |
| `detector_lock_timeout`      | 5000        | Five-second event lock              |
| `detector_save`              | `"1"`       | Saves event database records        |
| `detector_trigger`           | 0           | Disables event-based recording      |
| `detector_motion_save_frame` | 1           | Enables motion snapshots            |

There are also region-specific settings:

- `sensitivity`
- `max_sensitivity`
- `threshold`
- `cords`

These determine how each region behaves.

## 7.1 Why is `detector_trigger` set to 0?

Your cameras already record continuously.

Shinobi also supports event-based recording in Watch-Only mode.

That feature can create a rolling video buffer and preserve footage around an event.

But your system does not need that because recording is already continuous.

The document discovered that leaving `detector_trigger` enabled in continuous-recording mode still creates an unnecessary HLS buffer output and a 15-second motion lock after every start.

Therefore:

`detector_trigger: 0`

This avoids unnecessary processing.

# 8. Understanding the measurement results

Your document includes several experiments.

These experiments are important because they show why the final settings were selected.

## 8.1 Baseline experiment

The initial experiment used:

- Full-frame detection.
- Main stream.
- Default sensitivity of 10%.
- Two detector frames per second.

The results:

| Camera   | Events/min | Interpretation        |
| -------- | ---------- | --------------------- |
| Gate     | 2.8        | Cars were missed      |
| Quay     | 24.8       | Too many false events |
| Waterway | 15.8       | All events were false |
| Yard     | 0.4        | Mostly static         |

The problem was obvious.

The gate sensitivity was too high for small vehicles.

The quay was producing events because of camera shake and video loop transitions.

The waterway demonstrated that pixel-change detection cannot distinguish water movement from meaningful motion.

## 8.2 Tuning experiments

Four tuning rounds were performed.

| Round | Gate sensitivity | Lock  | Result                      |
| ----- | ---------------- | ----- | --------------------------- |
| 1     | 2%               | 2 sec | Too many events             |
| 2     | 4%               | 2 sec | Better, but multiple events |
| 3     | 4%               | 5 sec | Better event grouping       |
| 4     | 3%               | 5 sec | Best gate detection         |

The final settings were:

Gate lane

- Sensitivity: 3%.
- Threshold: 2.
- Motion lock: 5 seconds.

Hoist

- Sensitivity: 5%.
- Maximum sensitivity: 60%.
- Threshold: 2.

Berth edge

- Sensitivity: 15%.
- Maximum sensitivity: 40%.
- Threshold: 3.

Restricted quay

- Sensitivity: 12%.
- Maximum sensitivity: 40%.
- Threshold: 3.

The higher sensitivity values on the quay regions are intentional.

They prevent camera-shake noise from triggering events.

The trade-off is that smaller objects, such as people on the quay floor, may not be detected.

# 9. The important Shinobi bug: Live updates stop event saving

This is one of the most significant technical findings in the document.

Understanding it requires understanding JavaScript data types.

## 9.1 The problem

Shinobi checks whether event saving is enabled using:

```
detector_save === '1'
```

Notice the single quotes.

This means Shinobi expects the string `"1"`.

But JavaScript distinguishes strings and numbers.

```
1
```

is a number.

```
"1"
```

is a string.

They are not strictly equal:

```
1 === "1" // false
```

Therefore, if the configuration contains the number `1`, event saving can silently fail.

## 9.2 Why does it happen during live updates?

A normal monitor restart passes through a conversion function:

`convertNumbersToStrings`

This converts certain numeric monitor settings into strings.

However, a live detection update follows a different path.

It updates the detector thread without restarting the camera.

Therefore, the conversion function is not executed.

The main process can retain:

```
detector_save = 1
```

When an event arrives, Shinobi evaluates:

```
1 === "1"
```

The result is false.

No event database record is inserted.

The detector itself may still recognize motion correctly.

But event saving fails.

This explains why the problem is particularly confusing: detection appears to work, yet no events are saved.

## 9.3 How was the bug verified?

The document describes three tests.

| Test                                 | Stored value | Events/min |
| ------------------------------------ | ------------ | ---------- |
| After live update                    | Number       | 0          |
| After non-live change and restart    | String       | 19.1       |
| After forcing string and live update | String       | 14.2       |

This strongly demonstrates that the issue is related to the value's data type.

## 9.4 The implemented solution

The provisioner was modified to preserve the expected string representation.

The transformation is:

Original template

`1` (number)

Stored runtime configuration

`"1"` (string)

Shinobi receives the value

`"1"` (string)

The invariant is:

\\[ stored\\\_form(wire\\\_form(desired))=desired \\]

In simple words:

The configuration should retain its intended values after being stored, transmitted, and parsed by Shinobi.

The migration changed 288 values and required one restart per monitor.

Afterwards, live updates worked without restarting FFmpeg.

This is a particularly important software-engineering lesson: A configuration can look correct in YAML but still behave incorrectly because of data-type conversions across different application layers.

# 10. Final results

The final five-minute measurement window produced:

| Monitor      | Region    | Events | Events/min |
| ------------ | --------- | ------ | ---------- |
| cam_gate     | gate_lane | 35     | 7.0        |
| cam_quay     | hoist     | 34     | 6.8        |
| cam_waterway | —         | 0      | 0          |
| cam_yard     | —         | 0      | 0          |

Final detected events per minute

Five-minute measurement from the final unchanged configuration.

Quay

Events per minute6.8/min

0/min2/min4/min6/min8/minGateQuayWaterwayYard

## 10.1 Gate camera

Ground truth:

Approximately 7–8 vehicle passages per 55-second loop.

Measured:

7 events per minute.

This is close to the expected activity.

The five-second lock merges close vehicle detections.

## 10.2 Quay camera

Ground truth:

One container lowering every 9.36 seconds.

Expected event rate:

\\[ \frac{60}{9.36}=6.41 \\]

Measured:

6.8 events per minute.

The median gap between accepted events was 9 seconds.

This is consistent with approximately one event per lowering, although a few lowerings produced two events.

## 10.3 Control cameras

Both control cameras produced zero events.

This is important because the baseline experiment previously showed:

- Waterway: 15.8 false events/minute.
- Yard: 0.4 events/minute.

The final result demonstrates that irrelevant motion has been successfully excluded from the configured monitoring system.

The document also reports:

`uv run pytest -m e2e tests/test_motion.py`

7 tests passed

# 11. What are the limitations of this system?

Despite the successful results, there are limitations.

1\. It cannot detect stationary objects.

A vehicle waiting at the gate does not produce pixel changes.

2\. It struggles with camera movement.

If the camera moves, many pixels change simultaneously.

3\. It cannot identify object classes.

It cannot distinguish a car from a person or container.

4\. It can miss slow movement.

At higher detector frame rates, a slowly moving object may produce very small differences between consecutive frames.

5\. It depends heavily on region selection.

A badly selected region can include clocks, water, sky, or other irrelevant movement.

6\. The substream is coupled to the recording process.

If its input fails, the shared FFmpeg process can restart.

This is why the next planned sessions introduce object detection.

# 12. What happens in the next sessions?

Your document identifies three important future developments.

| Session    | Objective                                              |
| ---------- | ------------------------------------------------------ |
| Session 15 | Object detection on gate and quay cameras              |
| Session 16 | Combine motion detection with object detection         |
| Session 17 | Verify webhooks after live updates                     |
| Session 18 | Manage `conf.json` settings as code                    |
| Session 21 | Merge multiple detection pulses into activity episodes |

The most important next step is object detection.

Motion detection answers:

Did something change in the image?

Object detection answers:

What object is present?

For example:

[エッジAIカメラとアプリケーション | i-PRO Products](https://images.openai.com/static-rsc-4/JPcuTh7PYEJDX1611QBgHqNsSjjxODuECHWQ5CdQEmH_gV2FY3STbg7GRWhWCJe7gyzYRkqsKuzidPJ4wTLMXzJekUlA_tiLvYxhouFlHNSi-IN7IODk56wWMeOA_Ju2n9WT2h6LMV5UqMXeBvzU43cJrXaI80Ty7h7wcZNftBk?purpose=inline)

Motion detection

Detects changing pixels.

[C-CON SYSTEM Technology Co.,Ltd](https://images.openai.com/static-rsc-4/EzKFaTmL2RRqETzpEXhYdU7DCm8GicAlhf3A9SPVvCLI_PtPHIm9XkAptKneA2rWgh4aD0k9BiG_XGPiBFJdaiNYkeqGBUMcvTids38dyFmcETSM3BbihjD77ayg0ZHFMAF6GIR7ujOTS_Hjff3MQY_sE-5j1vyoBoHXeMIFGLM?purpose=inline)

Object detection

Identifies objects such as cars, trucks and people.

Combining both can make the system more intelligent.

For example:

1. Motion detection identifies an area of activity.
2. Object detection examines the relevant image.
3. The system identifies whether the object is a vehicle or person.
4. The application generates a meaningful event.

# 13. Important terminology to remember

| Term            | Simple explanation                             |
| --------------- | ---------------------------------------------- |
| Pixel change    | Difference in pixel brightness between frames  |
| Luma            | Pixel brightness                               |
| PAM             | Image format used to transfer grayscale frames |
| pam-diff        | Library that compares PAM frames               |
| Blob            | Connected group of changed pixels              |
| Region          | Polygon defining where motion is analyzed      |
| Sensitivity     | Minimum changed-pixel percentage               |
| Threshold       | Number of consecutive qualifying frames        |
| Max sensitivity | Upper limit for accepted change                |
| Motion lock     | Minimum interval between accepted events       |
| Substream       | Lower-resolution camera stream                 |
| Trigger         | A region passes detector conditions            |
| Event           | An accepted trigger recorded by Shinobi        |
| Live update     | Changes settings without restarting FFmpeg     |

# 14. Final understanding: What did Session 14 actually achieve?

The session achieved four major things.

1\. Technical implementation: Built-in motion detection was configured on two cameras using the substream.

2\. False-positive reduction: Carefully selected regions and thresholds eliminated irrelevant activity from the final measurements.

3\. Performance optimization: Substream detection significantly reduced CPU usage compared with main-stream detection.

4\. Bug discovery and resolution: A Shinobi live-update bug involving JavaScript number/string comparisons was identified and addressed in the provisioner.

The most important conclusion is:

Motion detection is not object recognition. It is a measurement of image changes. Its effectiveness depends on the quality of the regions, thresholds, frame rate, and event filtering.

# 15. Resources for further learning

These resources will help you understand the underlying technologies.

1. [Shinobi documentation](https://docs.shinobi.video/?utm_source=chatgpt.com) — Monitor configuration, recording and motion detection.
2. [FFmpeg documentation](https://ffmpeg.org/ffmpeg.html?utm_source=chatgpt.com) — Inputs, outputs, filters and stream mapping.
3. [Netpbm PAM format](https://netpbm.sourceforge.net/doc/pam.html?utm_source=chatgpt.com) — Understanding PAM image files.
4. [JavaScript equality comparisons](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Equality_comparisons_and_sameness?utm_source=chatgpt.com) — Understanding why `1 !== "1"`.
5. [OpenCV connected components](https://docs.opencv.org/4.x/d3/dc0/group__imgproc__shape.html?utm_source=chatgpt.com) — Understanding connected-component detection.

## Check your understanding

Try answering these five questions:

Knowledge check

5/5 answered

1\. Does Shinobi's built-in motion detector recognize whether an object is a car or a person?

Yes

No

Correct — It detects pixel changes, not object classes.

2\. Why was the substream selected?

Better image quality

Lower CPU usage

More colors

Correct — Its lower resolution significantly reduces decoding cost.

3\. What does sensitivity measure?

Camera temperature

Percentage of changed pixels in a region

Number of cameras

Correct — Sensitivity is based on changed pixels relative to region size.

4\. Why did live updates stop saving events?

The RTSP stream stopped

A number was compared strictly with a string

The camera resolution changed

Correct — JavaScript evaluates 1 === '1' as false.

5\. What is the purpose of the motion lock?

Prevent too-frequent accepted events

Increase image resolution

Identify vehicles

Correct — It enforces a minimum interval between accepted events.

Check my answers

# 5/5 correct

Try again

My Trust: 10/10 — The explanation is grounded in your uploaded Session 14 document. The external resources are supplementary.

Check the uncertain points: The exact Shinobi implementation described is tied to the pinned `dev` image from Session 8. Behaviour may differ in other Shinobi versions.