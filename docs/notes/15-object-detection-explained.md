# Session 15 — Object Detection Plugin: Detailed Explanation

This document explains how YOLOv9 object detection was integrated into Shinobi, how the system works internally, what problems occurred, how they were fixed, and why the final configuration was selected.

I'll explain it step by step, including the technical concepts, the architecture, the measurements, and the reasoning behind the engineering decisions.

Pasted markdown(9).md

## 1. What is the main objective?

The objective is to make the existing camera surveillance system understand what objects are visible in a camera image.

Before this session, the system already had motion detection (Session 14).

Motion detection answers:

- Is something moving?
- When did movement happen?
- Which camera detected movement?

However, motion detection does not understand what caused the movement.

For example:

[Warehouse Security Systems | CCTV and Access Control | Metwide](https://images.openai.com/static-rsc-4/OdwtWnetnz0g-zfEo4fIHQTrbLKwQ3NJp5DmDw64vX9g3NXJ3AIeRBn9rDCYeVyV3d8kgZKLC81JW24_FpIIO_GuGYqn66k64ENbQQd3XWXAAUZOy6UH3WZrMLH_f-Pvh3ylUPEwdlh2NpzaH0xe3fcNWJEef3KRvTGWsXddw2A?purpose=inline)

Motion detection

Detects movement but cannot identify the vehicle.

[AI-Powered Truckload Validation | TriSeed Distribution Success Story](https://images.openai.com/static-rsc-4/UwN6ypIoTv1gbmTeZigvg98-Q6jNd0_1aLl9-J_qZbGftjo-4NikPQ-CkQRg6BDE7pVHluQzC2RQSzIX6TFhqo4XoiRPVYJCv8CoVuh9evLDjfieEV4WHvcaavgC87WoYGTfj8ljWG48khFai0Pgz6uzWGXB72DkpYARUawLrG4?purpose=inline)

Object detection

Identifies the object as a truck and assigns a confidence score.

The new system should produce events such as:

```
{
  "reason": "object",
  "objects": [
    {
      "label": "truck",
      "confidence": 0.85
    },
    {
      "label": "car",
      "confidence": 0.92
    }
  ]
}
```

These are illustrative simplified values, not the exact Shinobi event structure.

The actual system uses YOLOv9 and sends detections back to Shinobi.

The two cameras involved are:

- `cam-gate`: monitors the entrance.
- `cam-quay`: monitors the quay or port area.

The final result is approximately:

| Camera | Object events/min |
| ------ | ----------------- |
| Gate   | 9.6               |
| Quay   | 13                |

This means Shinobi receives object detection events with labels, bounding boxes and confidence scores.

## 2. Understanding the main technologies

Before examining the architecture, it is important to understand the technologies involved.

### 2.1 What is YOLO?

[Precision Tracking: YOLOv8 and DeepSORT Redefine Real-Time Vision AI | by Harshitha Paritala | Medium](https://images.openai.com/static-rsc-4/DdOTE3Twcs7PT_EMiEqDkwfe7nr0hVCPJKL87QjHgKNyRoXge2UyOtqBG-r7uEG_Rzw3QLaWKnbZkuc2TFZf8TTCq37xA8ML-59vjTlBCwlcRTmTG34efutf5VOewYOWTS4UkMWAUEyt7DmWHM23ol6FqlPndQBLMyQmK9aJEeo?purpose=inline)

[medium.com](https://medium.com/%40harshithaparitala1/precision-tracking-yolov8-and-deepsort-redefine-real-time-vision-ai-6e317498c7b?utm_source=chatgpt.com)

YOLO stands for You Only Look Once.

It is a family of neural networks designed to detect objects in images.

A normal image is just a collection of pixels. YOLO processes those pixels and tries to identify objects.

For example, it can detect:

- Person
- Car
- Truck
- Bus
- Boat
- Train
- Traffic light

Its output contains three important things:

1. Object class: what the object is.
2. Bounding box: where the object is located.
3. Confidence score: how confident the model is about its prediction.

For example:

```
Object: Truck
Confidence: 0.85
Position: x=200, y=100
Width: 180
Height: 120
```

This means the model believes that a truck exists in that area of the image, with a confidence score of 85%.

Important: A confidence score is not necessarily the real-world probability that the classification is correct.

### 2.2 Why YOLOv9?

The project uses the official Shinobi YoloV9 ONNX plugin.

There are several model sizes:

| Model   | Approximate size |
| ------- | ---------------- |
| YOLOv9t | 8.7 MB           |
| YOLOv9s | 29 MB            |
| YOLOv9m | Larger           |
| YOLOv9c | Larger           |
| YOLOv9e | Largest          |

The project selected `yolov9t`.

The letter `t` means tiny.

Why?

Because the system runs on a laptop CPU without a dedicated GPU.

The smaller model offers:

- Lower computational requirements.
- Faster inference.
- Lower memory requirements.
- Suitable performance for the current two-camera setup.

The document reports that YOLOv9s took approximately twice as long as YOLOv9t without providing a sufficiently useful improvement for this application.

Therefore, YOLOv9t was retained.

### 2.3 What is COCO?

COCO is a dataset used to train object detection models.

The model in this project recognizes 80 everyday object categories.

Examples:

| Recognized | Not recognized          |
| ---------- | ----------------------- |
| Person     | Shipping container      |
| Car        | Crane                   |
| Truck      | Reach stacker           |
| Bus        | Straddle carrier        |
| Boat       | Port-specific machinery |
| Train      | Container stack         |

This is a very important limitation.

The camera may clearly show a shipping container, but YOLOv9t cannot identify it as a container because that category is not part of its 80-class COCO vocabulary.

It may classify a port machine incorrectly as another familiar category.

This explains why the yard test produced zero detections.

Key lesson: A model cannot reliably recognize classes it was not trained to recognize.

A specialized port dataset and model will be needed later (Session 16 or Session 23).

### 2.4 What is ONNX?

ONNX stands for Open Neural Network Exchange.

Think of ONNX as a standardized file format for machine learning models.

There are two different things:

- ONNX: stores the trained neural network.
- ONNX Runtime: executes the network.

In this project:

```
yolov9t.onnx
       |
       v
ONNX Runtime
       |
       v
CPU execution
       |
       v
Object detections
```

The plugin uses the CPU execution provider because the laptop does not have a dedicated GPU for this workload.

### 2.5 What is a detector plugin?

The detector plugin is a separate application that communicates with Shinobi.

This is an important architectural decision.

Shinobi does not directly execute the YOLO neural network.

Instead:

```
Shinobi
   |
   | Sends image
   v
YOLO plugin
   |
   | Runs neural network
   v
Object detection
   |
   | Returns results
   v
Shinobi
   |
   v
Stores object event
```

This separation means that Shinobi manages cameras and events while the plugin handles machine learning.

It also means that the plugin has independent failure modes and must be monitored separately.

## 3. Understanding the complete architecture

[Object Detection YOLO Algorithms and Their Industrial Applications: Overview and Comparative Analysis](https://images.openai.com/static-rsc-4/Ia28WAjQ8YPRazCIBcegqvVGXRKfF1iKlAPQlPwxypA1-i64KLt2DZSQCwQBctT6v6yRulH4o6PKIuTQ3Uc9Cs70LQys_Eql_CqOcrTHDzEVq0YsTzDznF19H2I6HvJ3MxerCy35OXC1DXiz_-KExY1laNUrh3_a-lt6EUI1td4?purpose=inline)

[mdpi.com](https://www.mdpi.com/2079-9292/14/6/1104?utm_source=chatgpt.com)

The architecture has four major components:

1. MediaMTX.
2. Shinobi.
3. YOLO plugin.
4. ONNX Runtime.

Let's follow an image through the complete system.

### Step 1: Camera sends video to MediaMTX

The cameras publish their video streams.

MediaMTX makes those streams available to Shinobi.

For example:

```
/cam-gate
/cam-gate-sub

/cam-quay
/cam-quay-sub
```

There are two streams per camera:

- Main stream: higher quality, used for recording.
- Substream: lower resolution, used for analysis.

Why use two streams?

Because object detection does not necessarily need the same image quality as video recording.

Using the smaller stream reduces processing requirements.

### Step 2: Shinobi receives the video

Shinobi uses FFmpeg to process the incoming streams.

The system already uses FFmpeg for:

- Live video.
- Recording.
- Motion detection.

Now another output is added for object detection.

The important part is that the object detector uses input `#1`, the substream.

The document specifies:

```
Main stream (#0)
    |
    +--> Recording and HLS

Substream (#1)
    |
    +--> Motion detection
    |
    +--> Object detection
```

This avoids opening another RTSP connection to the camera.

### Step 3: FFmpeg generates images

The object detection pipeline generates:

- Resolution: 640 × 360.
- Frame rate: 2 FPS.
- Format: MJPEG.

FPS means frames per second.

At 2 FPS, the system generates two images every second for each camera.

For two cameras:

\\[ 2 \times 2 = 4 \text{ frames/second} \\]

These JPEG images are sent through `pipe:4`.

Motion detection uses a different output, `pipe:3`.

The FFmpeg configuration looks like:

```
-map 1:0 -s 640x360 \
-vf "fps=2" \
-an -f mjpeg pipe:4
```

Explanation:

| Parameter    | Meaning                        |
| ------------ | ------------------------------ |
| `-map 1:0`   | Select video from input 1      |
| `-s 640x360` | Set image resolution           |
| `fps=2`      | Generate two frames per second |
| `-an`        | Disable audio                  |
| `-f mjpeg`   | Output MJPEG images            |
| `pipe:4`     | Send images through pipe 4     |

An important detail: FFmpeg generates these JPEGs continuously, even when there is no movement.

The motion-first setting controls whether Shinobi forwards those images to the plugin. It does not stop FFmpeg from generating them.

### Step 4: Shinobi forwards images to YOLO

Shinobi receives the JPEG images from `pipe:4`.

It reconstructs complete JPEG frames and checks whether object detection is currently allowed.

The setting is:

```
check_motion_first: true
```

This corresponds to:

```
detector_use_motion = 1
```

The logic is:

```
Is there recent motion?
       |
       +--- NO ---> Do not forward image
       |
       +--- YES --> Forward image to YOLO
```

The motion event opens a five-second detection window.

This is called motion gating.

It avoids running object detection continuously on quiet cameras.

### Step 5: Shinobi communicates with the plugin

Shinobi and the plugin communicate using Socket.IO.

The plugin connects to Shinobi as a client.

The plugin sends an initialization message:

```
{
  f: "init",
  plug: "YoloV9",
  type: "detector",
  pluginKey: "SECRET"
}
```

Shinobi checks whether the supplied key matches its configuration.

If it matches, the connection is accepted.

If it does not match, Shinobi rejects the connection.

Once connected, the plugin receives frames and monitor updates.

### Step 6: YOLO processes the image

The plugin sends the image to its local Flask server.

The request is:

```
multipart POST localhost:8993/upload
```

The Flask application invokes ONNX Runtime.

The processing pipeline is:

```
JPEG
  |
  v
Image decoding
  |
  v
Letterbox resizing
  |
  v
YOLOv9t inference
  |
  v
Confidence filtering
  |
  v
Non-maximum suppression
  |
  v
Bounding boxes
```

Let's examine the image processing.

## 4. Understanding image preprocessing

### 4.1 Why resize the image?

YOLO expects a 640 × 640 input.

However, the camera substream is 640 × 360.

These resolutions have different aspect ratios.

If we simply stretch the image to 640 × 640, objects become distorted.

For example, a truck would become unnaturally tall.

Instead, the plugin uses a technique called letterboxing.

[python - numpy image, crop one side, fill with black the ther - Stack Overflow](https://images.openai.com/static-rsc-4/pi7MsP5c5TEfnq1cOwZ256GqY31IQl3BYIsL11s2cxUkyNJfpOEZ-oy2jCkJVfBsfTVpGM6_NzSQ7d3wb2hPK1bXciQ2Sfwdbx0V5_pJkheHW8wfeRlDnqwrgg8LmpNrpYPmK-xhIQ4Mzj1toQNWBfTwtxv7FhCkDi9vo-x93MI?purpose=inline)

[stackoverflow.com](https://stackoverflow.com/questions/74344904/python-numpy-image-crop-one-side-fill-with-black-the-ther?utm_source=chatgpt.com)

The original image is resized while preserving its aspect ratio.

The resulting image is:

- Width: 640 pixels.
- Height: 360 pixels.

The remaining space is filled with grey padding.

The padding is:

\\[ 640-360=280 \\]

Therefore:

\\[ 280/2=140 \\]

There are 140 pixels of padding at the top and 140 pixels at the bottom.

The final image is:

```
       640 pixels
  ┌──────────────────────┐
  │                      │ 140 px
  ├──────────────────────┤
  │                      │
  │    Original image    │ 360 px
  │                      │
  ├──────────────────────┤
  │                      │ 140 px
  └──────────────────────┘
```

Why is this useful?

Because the image geometry remains correct.

Also, using 640 × 360 as the source is sufficient because the model will ultimately resize it to 640 × 640 anyway.

Increasing the input to 1280 × 720 would not add meaningful information to this particular pipeline.

### 4.2 Confidence threshold

The plugin uses a default confidence threshold of 0.5.

This means detections below 0.5 are discarded.

For example:

| Detection | Confidence | Result   |
| --------- | ---------- | -------- |
| Truck     | 0.93       | Accepted |
| Car       | 0.81       | Accepted |
| Person    | 0.56       | Accepted |
| Bus       | 0.42       | Rejected |
| Boat      | 0.31       | Rejected |

Mathematically:

\\[ \text{Accept detection if score} \geq 0.5 \\]

Why not lower the threshold?

The document tested a threshold of 0.3.

It produced more detections, but many were incorrect.

For example, some images generated additional labels such as:

- Train.
- Kite.
- Stop sign.

Therefore, lowering the threshold increases sensitivity but also increases false positives.

### 4.3 Non-maximum suppression (NMS)

Sometimes YOLO detects the same object several times.

For example, one truck might produce three overlapping bounding boxes.

[Non-Maximum Suppression(NMS)란   낙낙이](https://images.openai.com/static-rsc-4/O5AcuoLVDBnHdS-3qVeIh8nB4bC4u7QgPCtmyQUw2-BhcEPoKbreMz0qKt310YnVS3rs4KBW0_sA4trNr7THM0ZsZOpAzYJQQ8AN9SmG2sPQp3ey_SCi5teY4JFJ3thzZAppGhCHLXij2j0jHgdGI6i3wRfk-G7FOi8uhLIzX0c?purpose=inline)

[naknaklee.github.io](https://naknaklee.github.io/etc/2021/03/08/NMS/?utm_source=chatgpt.com)

NMS removes redundant boxes.

It works approximately like this:

1. Find the box with the highest confidence.
2. Compare it with other boxes.
3. Calculate their overlap.
4. Remove boxes that overlap too much.
5. Keep the strongest detection.

The overlap is measured using IoU.

IoU means Intersection over Union.

\\[ IoU=\frac{\text{Intersection area}}{\text{Union area}} \\]

The project uses an NMS threshold of 0.45.

This helps prevent multiple overlapping predictions for the same object within one inference.

However, NMS is not object tracking. It does not guarantee one event per physical vehicle across multiple frames.

## 5. Connecting the YOLO plugin to Shinobi

This section explains how the plugin is configured and authenticated.

The official plugin is not included inside the Shinobi image.

It runs as an independent Docker Compose service.

The architecture is:

```
Docker Compose
     |
     +----------------+
     |                |
     v                v
  Shinobi            YOLO
     |                |
     +---- Socket.IO -+
```

### 5.1 Why a plugin key is necessary

Shinobi does not accept every process that tries to connect as a detector.

It checks a shared secret.

The configuration uses:

```
YoloV9: SECRET_KEY
```

The same key must be available in both containers.

The mapping is important:

```
pluginKeys.YoloV9 = SECRET_KEY
```

The plugin identifies itself as:

```
plug: YoloV9
```

Therefore, the configuration key must use exactly `YoloV9`.

### 5.2 Why the key is generated only once

The project uses:

```
scripts/shinobi-seed.sh
```

This generates a random 48-character hexadecimal key.

The key is stored in `.env`.

Docker Compose passes the key to both services.

This ensures that both containers use the same secret.

There is another important detail.

Shinobi's `init.sh` rewrites `pluginKeys` from the environment every time it starts.

Therefore, manually editing `conf.json` is not a permanent solution.

The configuration should be maintained through the environment variables and Compose configuration.

### 5.3 Security problem: keys appear in logs

The investigation found that both containers log the plugin key in plaintext.

This means:

```
docker compose logs yolo
docker compose logs shinobi
```

may expose the secret.

This is a security weakness.

The logs should therefore be treated as sensitive information, just like `.env`.

The project documents this issue rather than modifying vendor files.

## 6. Problem number one: ONNX Runtime consumes too much CPU

Performance problem

This was one of the most important discoveries.

### 6.1 What happened?

ONNX Runtime automatically creates worker threads based on the number of CPU cores available on the host.

The laptop has:

- Intel Core Ultra 7 265U.
- 14 logical CPUs.

Without explicit configuration, ONNX Runtime creates approximately 14 inference threads.

That sounds reasonable.

But there is a problem.

The YOLO container is limited to:

```
cpus: 2
```

This means the container has a CPU quota equivalent to two CPU cores.

ONNX Runtime does not automatically adjust its thread pool to respect that quota.

As a result, too many threads compete for too little CPU time.

### 6.2 What is CPU throttling?

Imagine 14 workers trying to use two available workstations.

They cannot all work simultaneously.

Some workers must wait.

ONNX Runtime also uses a technique called spinning.

Spinning means a thread repeatedly checks whether another thread has finished its work.

Instead of sleeping, it consumes CPU while waiting.

This makes the situation worse.

### 6.3 The measurements

| Configuration         | Wall time/frame |
| --------------------- | --------------- |
| Default, unrestricted | 95 ms           |
| Default, CPU limit 2  | 965 ms          |
| Default, CPU set 0–1  | 1,362 ms        |

The CPU-limited configuration became approximately ten times slower.

Why?

Because the runtime was creating more threads than the container could effectively execute.

### 6.4 What is the solution?

The project created:

```
services/yolo-plugin/sitecustomize.py
```

This file modifies the Python environment before the plugin starts.

It injects ONNX Runtime session settings.

The selected configuration is:

```
Threads = 2
Spinning = Disabled
```

Conceptually:

```
options.intra_op_num_threads = 2options.add_session_config_entry(    "session.intra_op.allow_spinning",    "0")
```

The exact implementation is in the project's derived image.

The purpose is to avoid modifying the original vendor files.

### 6.5 Why two threads?

The benchmark:

| Threads | Spinning | Wall time |
| ------- | -------- | --------- |
| 1       | Off      | 215 ms    |
| 2       | On       | 123 ms    |
| 2       | Off      | 140 ms    |
| 4       | Off      | 121 ms    |
| 6       | Off      | 104 ms    |

Six threads are faster for one inference, but they consume substantially more CPU.

The project prioritizes overall system stability rather than minimizing the latency of one individual image.

Therefore, two threads without spinning were selected.

Main lesson: The number of threads must be configured according to the container's CPU allocation, not just the host's CPU count.

## 7. Problem number two: The plugin stops reconnecting

Reliability problem

This is a subtle but serious problem.

### 7.1 What should normally happen?

Imagine Shinobi stops temporarily.

The YOLO plugin loses its connection.

Normally, it should retry until Shinobi becomes available again.

For example:

```
YOLO
 |
 | Connection failed
 v
Wait 3 seconds
 |
 v
Try again
 |
 v
Wait 3 seconds
 |
 v
Try again
```

### 7.2 What actually happened?

The plugin has a maximum retry setting:

```
const MAX_RETRY = 5;
```

When the retry count exceeds the maximum, it sets:

```
stayDisconnected = true;
```

Then it disconnects its WebSocket.

The problem is that the Node.js process continues running.

This means Docker sees:

```
Container: Up
```

But the plugin is no longer trying to connect.

This is a false healthy state.

The container is running, but its primary function is broken.

### 7.3 Why is this dangerous?

Suppose Shinobi restarts.

The plugin has already given up.

Even after Shinobi becomes available, the plugin does not reconnect.

The system looks operational, but object detection is disabled.

The API shows:

```
{}
```

for connected plugins.

### 7.4 The solution: supervise.sh

The project created:

```
services/yolo-plugin/supervise.sh
```

This wrapper monitors the vendor plugin.

When the plugin logs:

```
Max retries reached
```

the wrapper exits with an error code.

Docker then restarts the container because the Compose service uses:

```
restart: unless-stopped
```

The new process starts with a clean retry counter.

The result:

```
Shinobi unavailable
       |
       v
Plugin retries
       |
       v
Maximum retries reached
       |
       v
supervise.sh exits
       |
       v
Docker restarts plugin
       |
       v
Plugin tries again
       |
       v
Shinobi available
       |
       v
Connection successful
```

This is an example of process supervision.

Main lesson: A running process is not necessarily a working process.

A process that has permanently stopped performing its job should exit so that the supervisor can restart it.

## 8. Problem number three: No backpressure

Major performance problem

This is probably the most important architectural lesson in Session 15.

### 8.1 What is backpressure?

Backpressure is a mechanism that prevents a system from accepting more work than it can process.

Imagine a restaurant.

- Customers arrive at four per second.
- The kitchen can prepare four orders per second.
- Everything works.

Now imagine the kitchen becomes slower.

Customers continue arriving, but orders accumulate.

The queue becomes larger and larger.

Eventually, the restaurant cannot serve customers in a reasonable amount of time.

The same thing happened in the YOLO plugin.

### 8.2 What happened in the original implementation?

Two cameras were producing:

\\[ 2 \times 2 = 4 \text{ frames/second} \\]

The plugin immediately started processing every incoming frame.

There was no limit on concurrent inference requests.

The processing flow was:

```
Shinobi
   |
   | Frame 1
   | Frame 2
   | Frame 3
   | Frame 4
   v
Plugin
   |
   +--> Inference 1
   +--> Inference 2
   +--> Inference 3
   +--> Inference 4
```

Each inference used two ONNX Runtime threads.

Several simultaneous inferences competed for the same two CPU cores.

This caused CPU throttling.

### 8.3 What were the consequences?

The processing times increased continuously.

| Time    | Processing delay |
| ------- | ---------------- |
| Initial | 5.8 seconds      |
| Later   | 23.7 seconds     |
| Later   | 37.1 seconds     |
| Final   | 52.5 seconds     |

The delay kept growing.

This is called congestion collapse.

The system was doing more work but producing results increasingly late.

Why is this particularly dangerous for surveillance?

Imagine a truck enters the gate at 13:28:24.

The system only processes its image at 13:29:16.

The resulting event is almost a minute late.

Shinobi records the time when the trigger arrives, not the original image capture time.

Therefore, the event timestamp can be misleading.

### 8.4 The solution: backpressure.js

The project introduced:

```
services/yolo-plugin/backpressure.js
```

It wraps the detector's `detect()` function.

The new setting is:

```
YOLO_MAX_IN_FLIGHT = 2
```

In-flight means the number of frames currently being processed.

The new logic is:

```
Frame arrives
     |
     v
How many frames are processing?
     |
     +--- 0 or 1 ---> Process frame
     |
     +--- 2 -------> Drop frame
```

This is called load shedding.

Instead of building an unlimited queue, the system drops excess work.

### 8.5 Why dropping frames is better than queuing them

Consider two possibilities.

Without backpressure

Growing queue

F1

F2

F3

F4

F5

F6

Old frames wait increasingly long before detection.

With backpressure

Bounded processing

F1

F2

F3 dropped

The system stays current instead of processing stale images.

A dropped frame means losing one opportunity to detect an object.

An unlimited queue can make every later detection increasingly outdated.

For real-time surveillance, timely information is often more useful than complete processing of old frames.

### 8.6 What was the result?

After implementing backpressure:

| Measurement                    | Result                |
| ------------------------------ | --------------------- |
| Average plugin processing time | 343 ms                |
| Maximum processing time        | 472 ms                |
| Frames inferred per minute     | Approximately 237–239 |
| Frames dropped                 | 0                     |

The system could now process all four incoming frames per second under the tested load.

The important point is that the plugin had sufficient processing capacity.

The problem was uncontrolled concurrency, not insufficient raw CPU capacity.

## 9. Problem number four: Motion and object detection share one lock

Integration problem

This problem comes from the interaction between motion detection and object detection inside Shinobi.

### 9.1 What is a lock?

A lock prevents multiple operations from interfering with each other.

Shinobi uses a motion lock to prevent too many events from being triggered simultaneously.

The configuration is:

```
detector_lock_timeout
```

The project originally used five seconds.

This worked for the motion detection system.

However, object detection uses the same lock.

That creates a problem.

### 9.2 What happens with a five-second lock?

Imagine the following sequence:

| Time    | Event                      |
| ------- | -------------------------- |
| 0 s     | Motion detected            |
| 0 s     | Five-second lock begins    |
| 0.5 s   | YOLO detects a truck       |
| 0.5 s   | Object event rejected      |
| 1–4.5 s | More detections rejected   |
| 5 s     | Lock expires               |
| 5 s     | Motion window also expires |

The object detector works correctly, but Shinobi refuses to save its events.

This is event starvation.

The detector identifies objects, but the event system blocks them.

### 9.3 Why does motion-first create this problem?

The motion-first mechanism has two functions:

1. A motion trigger opens a five-second window for object detection.
2. The same trigger passes through the shared event lock.

The project discovered that when object detection is enabled, motion triggers primarily open the detection window rather than saving ordinary motion events.

This means the event behavior changes.

Instead of receiving both motion and object events, the cameras receive object events only.

### 9.4 The solution: reduce the lock timeout

The project tested different configurations.

| Configuration                     | Gate events/min | Quay events/min |
| --------------------------------- | --------------- | --------------- |
| Motion-first, 5 s lock            | 2.0             | 3.3–4.0         |
| Continuous, 5 s lock, no shedding | 6.3             | 5.3             |
| Continuous, 5 s lock, shedding    | 8.0             | 7.3             |
| Motion-first, 2 s lock, shedding  | 9.3             | 13.0            |

The final configuration uses:

```
detector_lock_timeout = 2000
```

Instead of five seconds, the lock lasts two seconds.

This allows more object events to pass through.

### 9.5 Why not use continuous detection?

Continuous detection means forwarding images whether or not motion has occurred.

It provides more consistent image sampling, but consumes CPU even when the scene is quiet.

Motion-first detection only processes frames during active periods.

The project measured approximately 40% less CPU usage with motion-first detection than continuous detection in its comparison.

Therefore, the final decision was:

- Motion-first enabled.
- Two-second lock.
- Maximum two frames in flight.
- Excess frames dropped.

Main lesson: When two subsystems share a lock, changing one subsystem's configuration can affect the other.

Motion detection and object detection must therefore be tuned together.

## 10. Understanding the final object event

Now let's examine what Shinobi actually saves.

The event contains information similar to this:

```
{
  "ke": "GROUP_KEY",
  "mid": "cam_gate",
  "time": "2026-11-07T13:44:59.000Z",
  "archive": 0,
  "details": {
    "plug": "YoloV9",
    "name": "yolov9-onnx",
    "reason": "object",
    "matrices": [
      {
        "tag": "truck",
        "confidence": 0.53,
        "x": 200,
        "y": 100,
        "width": 180,
        "height": 120
      }
    ],
    "imgWidth": 640,
    "imgHeight": 360,
    "time": 317
  }
}
```

This is a simplified example based on the documented event format.

Let's understand every field.

| Field          | Explanation                            |
| -------------- | -------------------------------------- |
| `ke`           | Shinobi group key                      |
| `mid`          | Camera monitor ID                      |
| `time`         | Time Shinobi accepted the event        |
| `plug`         | Detector plugin name                   |
| `name`         | Plugin identifier                      |
| `reason`       | Event type                             |
| `matrices`     | Detected object information            |
| `tag`          | Object class                           |
| `confidence`   | Detection score                        |
| `x`            | Horizontal position                    |
| `y`            | Vertical position                      |
| `width`        | Bounding-box width                     |
| `height`       | Bounding-box height                    |
| `imgWidth`     | Original detection frame width         |
| `imgHeight`    | Original detection frame height        |
| `details.time` | Plugin processing time in milliseconds |

### 10.1 Understanding bounding boxes

A bounding box describes the position and size of an object.

The coordinates use the 640 × 360 detection image.

For example:

```
x = 200
y = 100
width = 180
height = 120
```

The top-left corner is at (200, 100).

The box extends 180 pixels horizontally and 120 pixels vertically.

[javascript - OpenCV Rect conventions – What is x, y, width, height? - Stack Overflow](https://images.openai.com/static-rsc-4/47LGbV80_LoZnLYKo4-7rWfYO9aJLYpUlSc_xQuvba-IOXwc-xwWxtJNiV698bo2WgHbrvTeSnNnjTzxZ0jFojz2Twcsk5F5_JXu-2pExxsYmQKJtkjAQ578BR2F5yLpoP7jmUGf-3IigF7GPzxNVAO2fUs-j6YndETlUbfOf50?purpose=inline)

[stackoverflow.com](https://stackoverflow.com/questions/57068928/opencv-rect-conventions-what-is-x-y-width-height?utm_source=chatgpt.com)

This is important because the coordinates are not based on the 1280 × 720 recording.

A downstream system must use the correct image dimensions when drawing bounding boxes.

### 10.2 Understanding the two different time fields

There are two fields that must not be confused.

`time`:

The timestamp when Shinobi accepted the event.

`details.time`:

The plugin processing time in milliseconds.

For example:

```
Event time: 13:44:59
Plugin processing: 317 ms
```

This does not mean the object was captured exactly at 13:44:59.

The image may be older because of processing and transport delays.

This is an important limitation for future real-time analytics.

Session 23 plans to address this by using capture timestamps.

### 10.3 Timelapse Frames

Each accepted object event can also save its JPEG image.

The image is stored as a Timelapse Frame.

This is valuable because it allows the project to build a dataset of actual camera images and detection results.

The document reports:

- 83 frames for one camera in ten minutes.
- 123 frames for the other camera in ten minutes.
- Approximately 15 KB per image.

Estimated storage:

\\[ 170\text{–}230\text{ MB per camera per day} \\]

These images can later be used for evaluating the detector and preparing a specialized port dataset.

## 11. Understanding the final performance results

The final system was tested after restarting the complete environment.

The important results are:

# 9.0

Gate events/min

Final test

# 13.5

Quay events/min

Final test

# 235 ms

Average inference time

Backpressure log

# 0

Dropped frames

Typical reported minute

### 11.1 Gate camera

| Label         | Events/min | Median confidence |
| ------------- | ---------- | ----------------- |
| Truck         | 6.2        | 0.57              |
| Car           | 3.2        | 0.83              |
| Traffic light | 3.0        | 0.57              |

The traffic light is a false positive.

The detector repeatedly identifies a small region of the image as a traffic light.

This represents approximately one-third of the gate's object events.

It is an example of why a high event count does not automatically mean high detection quality.

### 11.2 Quay camera

| Label         | Events/min | Median confidence |
| ------------- | ---------- | ----------------- |
| Truck         | 13.5       | 0.81              |
| Person        | 5.2        | 0.61              |
| Traffic light | 0.2        | 0.57              |

Truck detection is more consistent on the quay camera.

Person detections are plausible because workers may be visible in the area, but the project has not yet verified them against a manually labelled dataset.

### 11.3 CPU performance

The final test reported:

| Component     | CPU                           |
| ------------- | ----------------------------- |
| YOLO plugin   | Approximately 96% of one core |
| Shinobi total | 17.1%                         |
| FFmpeg gate   | 5.1%                          |
| FFmpeg quay   | 5.9%                          |

The YOLO plugin uses approximately one CPU core for both cameras combined.

This is a good result for the current two-camera configuration.

However, scaling to 14 cameras would require substantially more CPU capacity.

The project estimates approximately 5–8 CPU cores for the plugin at the same processing rate.

## 12. Why the project uses a derived Docker image

An important engineering decision was to avoid changing the vendor's original plugin files.

Instead, the project created a thin derived image:

```
services/yolo-plugin/
```

It contains three important additions:

| File               | Responsibility                                |
| ------------------ | --------------------------------------------- |
| `sitecustomize.py` | Controls ONNX Runtime threads                 |
| `supervise.sh`     | Restarts plugin after permanent disconnection |
| `backpressure.js`  | Limits concurrent inference                   |

Why is this better?

Imagine updating the official plugin image.

If you directly modify its internal files, you may lose those modifications during an update.

With a derived image, your customizations are maintained separately.

The architecture becomes:

```
Official YOLO plugin image
          |
          v
      Derived image
          |
          +--> Thread configuration
          |
          +--> Process supervision
          |
          +--> Backpressure
```

This makes the deployment easier to maintain and reproduce.

## 13. How to test and monitor the system

The document provides several commands.

### Check whether the plugin is connected

```
uv run python -m services.common.shinobi_client plugins
```

This checks which detector plugins are connected to Shinobi.

### Check object events

```
uv run python -m services.common.shinobi_client events cam_gate
```

This displays the camera's events, including labels and confidence scores.

### Calculate detection statistics

```
uv run python scripts/object_stats.py --minutes 10
```

This calculates:

- Object events per minute.
- Processing time.
- Median latency.
- 90th percentile latency.
- Maximum latency.
- Label distribution.
- Confidence statistics.

### Check backpressure

```
docker compose logs yolo | grep backpressure
```

This displays the number of processed and dropped frames.

### Run the object detection integration tests

```
uv run pytest -m e2e tests/test_object_detection.py
```

The final test results were:

- 23 end-to-end tests passed.
- 6 tests skipped.
- 188 total tests passed.

The skipped motion tests are expected because the two cameras use motion-first object detection rather than ordinary motion events.

## 14. The most important engineering lessons

Let's summarize the four major problems and their solutions.

| Problem                   | Root cause                          | Solution                             |
| ------------------------- | ----------------------------------- | ------------------------------------ |
| Slow inference            | Too many ONNX threads               | Two threads, spinning disabled       |
| Plugin stops reconnecting | Retry logic permanently disconnects | Supervisor exits and Docker restarts |
| Growing processing delay  | Unlimited concurrent inference      | Maximum two in-flight frames         |
| Object events disappear   | Shared motion/object lock           | Reduce lock from 5 s to 2 s          |

There are also several broader lessons.

1\. Detection and motion detection are different.

Motion tells you that something changed. Object detection tells you what the object might be.

2\. More threads do not always mean better performance.

Thread pools must respect container CPU limits.

3\. Never allow unlimited processing queues in a real-time system.

Old detections can become misleading.

4\. A running Docker container does not guarantee a working application.

Health and connectivity must be monitored.

5\. The detector's training dataset defines its capabilities.

COCO does not contain the specialized classes needed for port operations.

6\. Shared event mechanisms can create unexpected interactions.

The motion lock had to be adjusted for object detection.

## 15. What comes next?

The document identifies several open items for future sessions.

[Last cargo ships with Chinese goods arrive in LA as US-China trade tensions mount](https://images.openai.com/static-rsc-4/4LNCjvWDq6aiaHOnT0dUAuo2vygp7aDYT1Iyxp2TTiyL09jkxUi0zaBXoYhXm9cOJIlQhJcgQshnHKuLuYKrMan0KL_kotinD9XUKzsZq1-e_XxNXeiOAg9I5nXHl_itBCPMwW6BSLSXHPn4PyouGD04-5ooYDKXs9b9zdFPBFY?purpose=inline)

Session 16 — Specialized port detection

Develop a better detection approach for shipping containers, cranes, reach stackers and other port equipment.

Also investigate the traffic-light false positive and confidence threshold.

[11 - Object Tracking with Ultralytics YOLO — Application of AI](https://images.openai.com/static-rsc-4/PM3T7VWdWAjKWlkgSYgfWE8OFeTtGQPzvDic1iNjnU4l_ja3NF_JVV954ZqlE-RGZJhRBt6bIAmuEA7zdIIqCDvYslqjzv1vn3Ihfj2agXoN0jXLPmuuiJqGIXkfGuHqCd3UdXySq2Bt8TpsyGB2Xja5iPn_ui2yBAZalRwsgB4?purpose=inline)

Session 22 — Object tracking

Several events may describe the same physical truck.

Tracking and deduplication will help identify one vehicle across multiple frames.

[HiveIntel](https://images.openai.com/static-rsc-4/iNse_aX2isdiZIxNF1U6imTkbm2zQrPxdx_TitQ98sI71ZLKAXhDzoV5UgTbYoB8LVhSuL8ad1ongHyQQkrZYs9EI_0dLY3VRHD-xDQxi0WXrDmWeV2_6a2sPgyUMZoYvYq9THtaDGwzJLm9g9Fs5KGOKQNxy1FZM4GMkNjPn1U?purpose=inline)

Session 23 — Improved detector architecture

Introduce capture timestamps instead of arrival timestamps and improve queue/drop management so processing is fair between cameras.

[Webhooks Activate Asynchronous Integration Patterns - Antonello@Provenza.no](https://images.openai.com/static-rsc-4/9zTFA-Pl8ycDhjypVcVBfFmyYX1FERDYPUyqZ3oI8BsxUC-yBanYkNyg5rY_HzY0yBM_cmmIXObXd1vjTeJe3qmx6ngsA1ChVMPkf5OQSDnc3ugWU77t-nhX7QwDHEvazJO7MHOfgsDCYfgOX_nVTk_GvJpvsK5U0xvTwV8OlGo?purpose=inline)

Session 17 — Object event webhooks

Investigate how Shinobi sends object events to external applications and whether the webhook includes the original frame timestamp.

## 16. Final understanding: How everything works together

The complete system can be understood with this diagram:

IP cameras

Gate and quay

MediaMTX

Makes video streams available

Shinobi + FFmpeg

Recording, motion detection and 640×360 JPEG generation

Motion-first filtering

Only forward frames during the motion window

YOLOv9 ONNX plugin

Two threads · maximum two in-flight frames

Object detection

Class labels, confidence and bounding boxes

Shinobi Events + Timelapse Frames

Stores detections and associated images

Future analytics

Tracking, port-specific recognition and event evaluation

The fundamental achievement of Session 15 is not simply installing YOLO.

It is building a reliable object detection pipeline around YOLO that works with Shinobi, respects CPU limitations, recovers from connection failures, controls processing load, and generates usable events.

That is the difference between having an AI model running and having an AI detection system that can operate reliably in a real surveillance deployment.

## Resources for further learning

These resources help explain the technologies used in the document:

1. [Ultralytics documentation](https://docs.ultralytics.com/?utm_source=chatgpt.com) — Object detection, YOLO concepts and model training.
2. [ONNX Runtime documentation](https://onnxruntime.ai/docs/?utm_source=chatgpt.com) — CPU execution, threading and inference optimization.
3. [Docker Compose documentation](https://docs.docker.com/compose/?utm_source=chatgpt.com) — Containers, CPU limits and restart policies.
4. [FFmpeg documentation](https://ffmpeg.org/ffmpeg.html?utm_source=chatgpt.com) — Video processing, stream mapping and frame extraction.
5. [Shinobi documentation](https://docs.shinobi.video/?utm_source=chatgpt.com) — Camera configuration and plugin integration.
6. [Socket.IO documentation](https://socket.io/docs/v4/?utm_source=chatgpt.com) — Real-time communication between applications.

My Trust: 10/10 — The explanation is grounded in your uploaded Session 15 document.

Check the uncertain points: The COCO model's actual accuracy on port-specific scenes, the correctness of person detections, and the original frame capture timestamps remain open questions in the project.




```
That is the difference between having an AI model running and having an AI detection system that can operate reliably in a real surveillance deployment.
```

I did not unterstand the difference. explain it

The difference is actually very important. Let's understand it using your Shinobi + YOLOv9 project as an example.

The main idea is:

Having an AI model running means that the AI can make predictions. Having a reliable AI detection system means that the entire application can continuously receive images, process them correctly, return results, and save those results without failing or becoming too slow.

These are two different things.

# 1. First: What does it mean to have an AI model running?

Imagine you download YOLOv9 and run it on your laptop.

You give it an image:

[Warehouse AI Video Analytics | Safety & Logistics | FlowLinks](https://images.openai.com/static-rsc-4/k1Prejpm621mHtAv6gPE6qCzYfTo-nMnR9KfyQ1yKfoNysJp0n4obLp7kU99z3vqfIa7YBxC4Oah6RsKh1YrMsYoTQVCyksRhT-sLFxYDwHgzZnAGm8IUgm_5JdZTmKEez4ZkIW2iIv908YuGf92HstJ-O9BGmIKr0djZeLQaz4?purpose=inline)

[flowlinks.ai](https://www.flowlinks.ai/warehouse-management?utm_source=chatgpt.com)

YOLOv9 processes the image and returns:

```
Object detected: Truck
Confidence: 0.87

Bounding box:
x = 200
y = 100
width = 180
height = 120
```

Great! The model works.

It can:

- Receive an image.
- Analyze the pixels.
- Recognize an object.
- Return a label.
- Calculate a confidence score.
- Locate the object using a bounding box.

This demonstrates that the AI model is operational.

But there is a problem.

This test only proves that the model can analyze an image. It does not prove that the entire surveillance system works.

For example, what happens if:

- The camera sends images faster than YOLO can process them?
- Shinobi disconnects?
- YOLO stops reconnecting?
- The CPU becomes overloaded?
- Shinobi receives detection results too late?
- The event is rejected because of a lock?

The model might still be perfectly functional, but the actual surveillance application can fail.

# 2. What does a real AI detection system require?

A real detection system consists of multiple components working together.

Look at your project:

1\. IP Camera

Produces video images

2\. Shinobi + FFmpeg

Receives video and extracts images

3\. Communication

Transfers frames to YOLO through Socket.IO

4\. YOLOv9 + ONNX Runtime

Recognizes objects

5\. Shinobi Event System

Receives and stores detections

6\. Monitoring and Analytics

Measures events, latency and failures

Every component must work correctly.

Let's examine why.

# 3. Four examples from your project

Your Session 15 document describes four problems.

These examples demonstrate the difference particularly well.

## Example 1: The AI model works, but the system is too slow

Imagine YOLO correctly identifies a truck.

However, your computer has limited CPU resources.

Your system receives four images per second, but processing becomes slower and slower.

The queue starts growing.

Situation A: Model works in isolation

Successful

One image

Image → YOLO → Truck detected

Situation B: Real system overloaded

Unreliable

Continuous images

F1

F2

F3

F4

F5

Frames accumulate faster than they are processed.

In your project, processing delays reached approximately 52 seconds and continued growing.

The model was still capable of detecting trucks.

But the system was no longer providing timely information.

What did you do?

You implemented `backpressure.js`.

It limits processing to two frames simultaneously and drops additional frames when both slots are occupied.

The result was approximately 343 ms average processing time under the tested load, instead of delays exceeding 52 seconds.

This is an example of making the overall system reliable rather than simply making the model work.

## Example 2: The AI model works, but communication fails

Imagine Shinobi is restarted.

The YOLO plugin loses its connection.

The plugin attempts to reconnect five times.

After that, it permanently stops trying.

But the Docker container remains running.

Docker status

Running

YOLO connection

Disconnected

Object detection

Not working

The AI model itself is not necessarily broken.

It could successfully detect a truck if you manually gave it an image.

But Shinobi cannot send images to it because communication has stopped.

What did you do?

You created `supervise.sh`.

When the plugin gives up reconnecting, the script exits with an error.

Docker notices that the process has stopped and restarts the container.

The plugin then tries to reconnect.

This is reliability engineering: ensuring the system can recover from failures.

## Example 3: The AI detects the object, but Shinobi does not save the event

Imagine the following:

1. Motion is detected.
2. Shinobi opens the detection window.
3. YOLO receives an image.
4. YOLO recognizes a truck.
5. YOLO sends the result back.

Everything works correctly until Shinobi tries to save the event.

The problem is that the shared motion lock is still active.

Shinobi rejects the object event.

YOLO prediction

Truck detected

Shinobi event lock

Blocked

Saved event

Rejected

Notice something important:

The model did its job perfectly.

The communication also worked.

But the overall application failed to produce the expected event.

What did you do?

You reduced the lock timeout from five seconds to two seconds.

This allowed more object detection events to be saved.

This demonstrates that successful AI predictions do not automatically mean successful application behavior.

## Example 4: The AI model recognizes objects, but cannot recognize the objects you actually need

Your model uses COCO, which contains 80 everyday object classes.

It can recognize a truck.

However, your port surveillance system also needs to recognize:

- Shipping containers.
- Cranes.
- Reach stackers.
- Straddle carriers.

These are not available as classes in the model used in Session 15.

Imagine giving YOLO an image of a shipping container.

It might return no detection or an incorrect familiar category.

The model is running correctly, but it does not have the knowledge required for this particular application.

This is a model capability problem, not a Docker or communication problem.

The solution is to develop a specialized dataset and model.

That is why Session 16 and Session 23 are important.

# 4. The difference summarized

Here is the simplest comparison.

| Having an AI model running    | Having a reliable AI detection system      |
| ----------------------------- | ------------------------------------------ |
| Can analyze an image          | Continuously receives camera images        |
| Can recognize a truck         | Correctly delivers truck events to Shinobi |
| Returns a confidence score    | Stores and manages detection results       |
| Works in an isolated test     | Works with multiple cameras                |
| Runs inference                | Controls CPU and memory usage              |
| Produces predictions          | Handles overload and dropped frames        |
| Can execute successfully      | Recovers after failures                    |
| Demonstrates AI functionality | Demonstrates operational reliability       |

# 5. An everyday analogy

Think about a car.

Having a working engine does not mean you have a reliable car.

The engine might start perfectly.

But a car also needs:

- A functioning transmission.
- Working brakes.
- Fuel delivery.
- Cooling.
- Electrical systems.
- Reliable steering.

If the engine works but the brakes fail, the car is not reliable.

Similarly:

- YOLO is like the engine.
- Shinobi is part of the vehicle's operating system.
- Socket.IO is part of the communication system.
- Docker supervision helps recover from failures.
- Backpressure controls the workload.
- Event storage preserves the results.

All these components must work together.

# 6. What did Session 15 actually achieve?

Before Session 15, you had a surveillance system that could detect movement.

After Session 15, you had an integrated object detection pipeline that could:

1. Receive images from two cameras.
2. Detect objects using YOLOv9.
3. Return object labels and confidence scores.
4. Save object detection events in Shinobi.
5. Store associated images.
6. Control inference concurrency.
7. Recover from plugin disconnections.
8. Operate within the available CPU resources.

That is a much bigger achievement than simply running YOLO on one image.

The key sentence to remember:

> An AI model answers the question: Can the AI recognize something in an image?
>
> An AI detection system answers the question: Can the complete application continuously recognize objects from real cameras, deliver the results correctly, store them, and continue working when problems occur?

That is the difference between an AI experiment and an operational AI application.

## Resources to understand the difference

- [ONNX Runtime performance](https://onnxruntime.ai/docs/performance/?utm_source=chatgpt.com) — Why inference performance and thread management matter.
- [Docker restart policies](https://docs.docker.com/engine/containers/start-containers-automatically/?utm_source=chatgpt.com) — How applications recover from failures.
- [YOLO documentation](https://docs.ultralytics.com/?utm_source=chatgpt.com) — How the AI model performs object detection.
- [Docker Compose](https://docs.docker.com/compose/?utm_source=chatgpt.com) — How separate services work together.

My Trust: 10/10

Check the uncertain points: The examples above explain the architecture and problems documented in Session 15. They do not establish that the system has been tested under every possible camera load or failure condition.

