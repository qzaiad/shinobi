# Session 16 — Detection Quality: Detailed Explanation

This session explains how to measure the quality of an object-detection system used in a port surveillance environment.

The main objective is to answer three questions:

1. Does the AI detect objects correctly?
2. How many real objects does it miss?
3. How can Shinobi filters reduce unnecessary detection events without accidentally removing useful events?

The session evaluates four cameras:

- `cam-gate`: Entrance gate.
- `cam-quay`: Quay (where ships are loaded and unloaded).
- `cam-yard`: Container storage yard.
- `cam-waterway`: Waterway where ships pass.

It uses a YOLOv9-tiny model (`yolov9t`) trained on the COCO dataset.

Let's break down the entire session, including the technical concepts, calculations, results, and decisions.

# 1. Understanding the main objective

Before Session 16, the system could detect objects and generate events.

For example:

[09 - Object Detection with Ultralytics YOLO — Application of AI](https://images.openai.com/static-rsc-4/HqDesHc0xtx56vl3zvvGtatJpNHGyRF9MCXWfGUgxWj7LQzMeGvACtpSC-V1ew3xgB0ygbtc3NivBpxwypWUn52ghDq3XVukSOvNwlvqMklzAqkQDNKR_BxGoHysdbsvTkcR8acssmKydYnoGKBfs6ua9wgNM2qmp70LNkfbGrY?purpose=inline)

[ezponda.github.io](https://ezponda.github.io/ai-application-course-materials/notebooks/07_object_detection_with_ultralytics.html?utm_source=chatgpt.com)

A camera sees a truck, and the AI produces an event:

```
Camera: cam-gate
Detected object: truck
Confidence: 0.85
Event: Object detected
```

But there is an important problem.

An event does not automatically mean that the detection is correct.

Consider two situations:

- Situation A: A truck passes the gate, and the AI detects it.
- Situation B: A crane is incorrectly classified as a truck.

Both situations generate a truck event, but only the first is correct.

There is also another problem: the AI might miss objects completely.

For example, 10 trucks pass a camera, but the AI detects only 3.

If we look only at the generated events, we cannot know that 7 trucks were missed.

Therefore, Session 16 introduces a systematic evaluation process.

The goal is to measure detection quality using manually labelled images and compare those labels against the AI predictions.

# 2. Understanding the important evaluation terms

This is the most important theoretical part of the session.

## 2.1 Ground truth

Ground truth means the actual objects present in an image, verified and labelled by a human.

For example, imagine a camera image containing:

- One truck.
- One car.
- One person.

A human draws a rectangle around each object and assigns its class.

[Precision Tracking: YOLOv8 and DeepSORT Redefine Real-Time Vision AI | by Harshitha Paritala | Medium](https://images.openai.com/static-rsc-4/DdOTE3Twcs7PT_EMiEqDkwfe7nr0hVCPJKL87QjHgKNyRoXge2UyOtqBG-r7uEG_Rzw3QLaWKnbZkuc2TFZf8TTCq37xA8ML-59vjTlBCwlcRTmTG34efutf5VOewYOWTS4UkMWAUEyt7DmWHM23ol6FqlPndQBLMyQmK9aJEeo?purpose=inline)

[medium.com](https://medium.com/%40harshithaparitala1/precision-tracking-yolov8-and-deepsort-redefine-real-time-vision-ai-6e317498c7b?utm_source=chatgpt.com)

These rectangles are called bounding boxes.

The ground truth is stored in:

```
tests/fixtures/detection/labels.yaml
```

The important point is that ground truth represents what is actually present, not what the AI believes is present.

Without ground truth, we cannot reliably calculate precision and recall.

## 2.2 True Positive (TP)

A True Positive occurs when the AI detects an object correctly.

Example:

| Ground truth | AI prediction | Result |
| ------------ | ------------- | ------ |
| Truck        | Truck         | TP     |
| Car          | Car           | TP     |
| Person       | Person        | TP     |

A prediction must satisfy the matching requirements, including the correct class and sufficient bounding-box overlap.

## 2.3 False Positive (FP)

A False Positive occurs when the AI reports a detection that does not correctly match a ground-truth object.

Example:

- There is a crane.
- The AI identifies it as a truck.
- The prediction is incorrect.

This is a false positive.

False positives are particularly problematic because they create false alarms.

## 2.4 False Negative (FN)

A False Negative occurs when an actual object exists, but the AI fails to detect it.

Example:

- There are five trucks in an image.
- The AI detects only three.
- Two trucks are missed.

Therefore:

\\[ FN=2 \\]

False negatives are particularly important in surveillance because real objects can go unnoticed.

## 2.5 Understanding the difference

## Three possible detection outcomes

[pylon vTool Object Detection | Basler AG](https://images.openai.com/static-rsc-4/NYsAgfe4algaznzH4KBjvLBancd97ebGm12UQ3Hr9ToSD8HK_Qrtz2C3QLgRp1ex15G_51T_RVziwqbijsjOznx25jmehz5SaE4V3bpTlDcE35ktPP3YeFqO86WFIpi9fmBSHX9ShKCjrMtR3n5hAi3SsVeoZTUHNoIbg2SoS78?purpose=inline)

True Positive (TP)

The object exists, and the AI detects it correctly.

Correct detection

[A Comprehensive Guide to Mean Average Precision](https://images.openai.com/static-rsc-4/8b3QaEg71sbgKhaeuiGxtbtULAbeMMT00qxMWwkgihbY1z7ulKZ8mL-V0Iqt2km7xXbSlx7GitPJZs_V_1URPu4oUqQKxXkTm8PM6H9HAQKftr_wRBNq0rxKlg3mLAbA4kLVPdJS7OKiheSOvqqQqzklVApx6OsItheirufULKA?purpose=inline)

False Positive (FP)

The AI reports an incorrect detection.

False alarm

[Defining Accuracy for Street-Level Mobility Data | by Jennifer Ding | Numina | Medium](https://images.openai.com/static-rsc-4/TYKrfb2o9LM6dhaUA1KhBBx5DZyfufHXb6n5dmE0Uv_9PZX9Z3RBQYyqF-FIgl6yhUzMOUGgBE5yFOQ1JuP9r6K0XmTkX4lQMXAHPhxoAA6_MB0DeRaWoHVU2lvssVYAqje2oY80ky4QY8DDgqacDyMYE0SXv1nXHrTMkCeSWgs?purpose=inline)

False Negative (FN)

An actual object is present, but the AI misses it.

Missed detection

# 3. Precision and Recall

These are the two main metrics used to evaluate object detection.

## 3.1 Precision

Precision answers:

Of all the objects detected by the AI, how many were correct?

Formula:

\\[ Precision=\frac{TP}{TP+FP} \\]

Example:

Suppose the AI produces 100 detections:

- 80 are correct.
- 20 are incorrect.

Then:

\\[ Precision=\frac{80}{80+20}=0.8 \\]

\\[ Precision=80\\% \\]

This means 80% of the AI's reported detections are correct.

A high precision means fewer false alarms.

## 3.2 Recall

Recall answers:

Of all the real objects present, how many did the AI successfully detect?

Formula:

\\[ Recall=\frac{TP}{TP+FN} \\]

Example:

There are 100 real trucks.

The AI detects 60 correctly and misses 40.

\\[ Recall=\frac{60}{60+40} \\]

\\[ Recall=60\\% \\]

This means the AI detects 60% of the real objects.

A high recall means fewer missed objects.

## 3.3 Precision versus Recall

| Metric    | Main question                      | What is undesirable? |
| --------- | ---------------------------------- | -------------------- |
| Precision | Are the detections correct?        | False positives      |
| Recall    | Does the AI find the real objects? | False negatives      |

An AI system can have:

- High precision but low recall.
- Low precision but high recall.
- High precision and high recall.
- Low precision and low recall.

For the port surveillance system, Session 16 prioritizes reliable events while recognizing that many objects are still missed.

## 3.4 Why both metrics are necessary

Imagine a system that detects only one truck, even though 100 trucks pass.

That one detection is correct.

Its precision could be 100%, but its recall would be only 1%.

Therefore, saying that an AI has excellent precision does not mean that it is an excellent detector.

This is exactly why Session 16 evaluates both metrics.

# 4. IoU — Intersection over Union

Precision and recall are not enough.

We also need to determine whether the AI draws the bounding box around the correct area.

This is where IoU comes in.

IoU measures the overlap between:

- The ground-truth bounding box.
- The AI-predicted bounding box.

[Train a YOLOv8 object detection model in Python](https://images.openai.com/static-rsc-4/xF8EPGILUXoX-CE-ZgmMaWNx_VjSUGshs0GL-YopNVJvNOTzBt2DeLo1l-EkCScnDor9kbAp3SVvtFgCCv7XmJKih35P-dlK1SXFwtZI1rRj3qJFHPOK0DMOaba1Kgf4OOcb1K2H2iqMZwiox0E1KTDsOF-4gdYcQl3u-AbqLo0?purpose=inline)

[developer.ibm.com](https://developer.ibm.com/tutorials/awb-train-yolo-object-detection-model-in-python?utm_source=chatgpt.com)

Formula:

\\[ IoU=\frac{\text{Intersection Area}}{\text{Union Area}} \\]

Where:

- Intersection = area shared by both boxes.
- Union = total area covered by both boxes combined.

### Example

Suppose:

- Ground-truth area = 100 pixels².
- Predicted area = 100 pixels².
- Overlapping area = 80 pixels².

The union is:

\\[ 100+100-80=120 \\]

Therefore:

\\[ IoU=\frac{80}{120}=0.667 \\]

The IoU is approximately 0.67.

### What do the values mean?

| IoU  | Interpretation                              |
| ---- | ------------------------------------------- |
| 1.0  | Perfect overlap                             |
| 0.75 | Strong overlap                              |
| 0.50 | Minimum accepted overlap in this evaluation |
| 0.25 | Weak overlap                                |
| 0.0  | No overlap                                  |

Session 16 uses:

\\[ IoU\geq0.5 \\]

This means a prediction normally needs at least 50% overlap with the ground-truth box to count as a match.

Important: A correct object classification does not automatically mean a correct bounding box.

For example, the AI might recognize a truck but draw a box around only its tractor.

The class is correct, but the box is incomplete.

# 5. IoA — Intersection over Area

IoA is another overlap metric.

It is useful when the predicted box is much smaller than the actual object.

Formula:

\\[ IoA=\frac{\text{Intersection Area}}{\text{Area of one box}} \\]

Why does this matter?

Imagine:

- A ground-truth truck box includes the tractor and trailer.
- The AI detects only the tractor.

The AI box is contained inside the ground-truth box.

IoU might be relatively low because the predicted box is much smaller.

IoA can show that most of the predicted box is nevertheless inside the actual truck.

Session 16 uses IoA in special matching situations, particularly for identifying predictions that represent only part of an object.

# 6. COCO recall versus Port recall

This is one of the most important concepts in the entire session.

## 6.1 What is COCO?

COCO is a dataset used to train and evaluate object-detection models.

The model in this project was trained using 80 everyday object classes.

Examples include:

- Person.
- Car.
- Truck.
- Bus.
- Boat.
- Traffic light.

However, a port contains many objects that are not represented as individual classes in COCO.

[Причальні крани-перевантажувачі Ship-to-shore](https://images.openai.com/static-rsc-4/-3XhfbdiUGIYY_uLubdQbyTkqD6ZrtlUG_AxZb5FG2qnWtY8bGgZFSPKS9fqJyH_qCTpnRGXI9xfWaU_Q4vJmmlrv9eXmLXtAYc6PY4FxPoiokK436cOdEgUCYykC5tT-DVgA6iF_0j30U0JtqCakkx84tLxukIoUaM-H09p8SQ?purpose=inline)

[RTG/Rubber-tired Container Gantry Crane](https://images.openai.com/static-rsc-4/w_HEd-kXxXaxo3HeLHQEID6VEC9IVa0l59MUQFjYXvugRyxqmTyzzbgz9C9IQ0T_1Tt-OLvEhBiLhULYQ0cBS8qAg_2-W6Xvw3a-EsCPMUsyxUWJLdB0WwKIgUmMvAY4Dxxv7b_y_TuHaY7bsCtd5trqqCVE8XF9LCNVSEFNMIM?purpose=inline)

[Supply-chain issues to bedevil retailers through 2022, according to survey - MarketWatch](https://images.openai.com/static-rsc-4/-sPSQpwNvbovX_Cksg0oUDxGmF_wkLfqOvhMFmRoCCKot6HR97cixr1Q0GkleUY5qEByBcdtbTu_1dNkF489VMZ65V_zBaTF8pz5gCNc3IFEQTRw0yronJ6ZK8H7mthjGvZIr2OEwMfRH6Ix5dlHkg_BBT-0p4WvEHlkzRV3yIY?purpose=inline)

8

Examples:

- Shipping containers.
- STS cranes.
- RTG cranes.
- Reach stackers.
- Straddle carriers.

The model does not have dedicated classes for these objects.

## 6.2 COCO recall

COCO recall counts only objects that the model could theoretically recognize using its existing classes.

For example:

| Object       | COCO class available? |
| ------------ | --------------------- |
| Truck        | Yes                   |
| Car          | Yes                   |
| Person       | Yes                   |
| Boat         | Yes                   |
| Container    | No                    |
| Crane        | No                    |
| Port vehicle | No                    |

## 6.3 Port recall

Port recall includes all objects relevant to the port environment.

This includes containers, cranes and specialized port vehicles.

The difference is crucial.

A model could have reasonable COCO recall but extremely poor port recall.

Why?

Because the model is not trained to recognize many important port objects.

This is not necessarily a configuration problem. It is a limitation of the model's training classes.

The session therefore reports both metrics.

# 7. How the ground-truth dataset was created

The evaluation uses 79 manually labelled frames.

## Dataset distribution

06121824GateQuayYardWaterway

| Camera   | Frames |
| -------- | ------ |
| Gate     | 20     |
| Quay     | 19     |
| Yard     | 20     |
| Waterway | 20     |
| Total    | 79     |

The dataset contains 474 labelled boxes.

## 7.1 Why use the same images as the live system?

This is an important technical detail.

In production, the images pass through two encoding stages before reaching YOLO.

The session reproduces both stages offline.

The pipeline is:

Original video

1280 × 720

Encoding stage 1

640 × 360, 10 FPS, OSD

Encoding stage 2

Shinobi MJPEG output at 2 FPS

JPEG frames

Actual images used for evaluation

YOLO object detector

Why reproduce the exact pipeline?

Because image quality influences object detection.

The document identifies a specific problem with JPEG encoding.

The first JPEG images were approximately 21 KB, while live images were approximately 11.8 KB.

This means the first images were sharper than the images the live detector normally receives.

If the evaluation used these sharper images, the results would not accurately represent production performance.

The solution was to encode the clips twice and keep the second pass.

This allowed the offline JPEGs to resemble the live JPEGs much more closely.

## 7.2 Manual labelling

The team created a browser-based labelling tool:

```
python -m tools.detect_eval label
```

It runs on:

```
http://127.0.0.1:8765
```

The important design decision is that the labelling interface does not show the AI predictions.

Why?

Because showing predictions could influence the human annotator.

The human might unconsciously copy the AI's mistakes.

Instead, the person independently labels the objects.

## 7.3 Ignore labels

Some objects are too difficult to evaluate reliably.

Examples:

- Extremely small objects.
- Mostly hidden objects.
- Objects cut off by the image boundary.
- Objects hidden underneath the OSD.

These receive an `ignore` label.

An ignored object:

- Does not create a false negative if missed.
- Does not create a false positive when a prediction matches it sufficiently.

This prevents the evaluation from unfairly penalizing the detector for objects that cannot reasonably be evaluated.

# 8. How the scoring algorithm works

The evaluation tool compares the AI predictions with the manually labelled objects.

The matching process follows these steps:

1. Sort AI predictions by confidence score, highest first.
2. Look for an unmatched ground-truth object of the same class.
3. Check whether IoU is at least 0.5.
4. If a valid match exists, classify the prediction as TP.
5. Otherwise, check special cases such as ignore labels, partial detections and duplicates.
6. If no special case applies, classify it as FP.
7. Any remaining unmatched labels become FN or port-only FN.

### Why sort by confidence?

Imagine the AI predicts the same truck twice:

| Prediction | Confidence |
| ---------- | ---------- |
| Box A      | 0.92       |
| Box B      | 0.67       |

The highest-confidence prediction gets priority.

If Box A matches the truck, Box B cannot claim the same ground-truth object again.

Box B becomes a duplicate false positive.

This prevents the AI from receiving credit multiple times for detecting one object.

## 8.1 Event evaluation versus box evaluation

This is another essential distinction.

The system produces two kinds of evaluation results.

Box-level evaluation

Measures the quality of individual bounding boxes.

This is where precision and recall are calculated.

Event-level evaluation

Measures whether a frame generates a meaningful event.

For example:

A truck is correctly detected, but the bounding box covers only the tractor.

At box level:

- FP because the bounding box is incomplete.
- FN because the full truck label remains unmatched.

At event level:

\- The event is correct because a truck is genuinely present.

This is called a part-of detection.

It is especially important at the gate.

# 9. Understanding Shinobi event filters

Now we move from evaluating the detector to controlling its output.

Shinobi has event filters that can remove unwanted detections.

For example, the detector sometimes recognizes a traffic light.

But the project does not need traffic-light events.

A filter can remove these detections.

[Shinobi - Agilicus](https://images.openai.com/static-rsc-4/47G3VHlJC_Oh_glm4j4hCXWP2IjnGd95U5x8yaQT9t91X6vwN4vvyrJ9GbSDHupmeefPbskwiE8lnQIKSzfkzULzxY1Bi1BWklnTDA7bV6-dycR1tQispCw7ZKfyTT8VZYWaOxK4okFrJgvS36Fxw5JqDMe1O-xu3tRQiJTyFJo?purpose=inline)

[agilicus.com](https://www.agilicus.com/starlink-resources/shinobi/?utm_source=chatgpt.com)

## 9.1 Where filters execute

The processing order is:

```
YOLO plugin answer
       |
       v
triggerEvent()
       |
       v
checkEventFilters()
       |
       v
checkMotionLock()
       |
       v
detector_obj_region
       |
       v
Save Events row
       |
       v
Webhooks / MQTT / Recording
```

The key detail:

Filters execute before the motion lock.

Therefore, an event rejected by a filter does not consume the two-second motion lock.

A subsequent frame can still produce an event.

## 9.2 How the filter conditions work

A filter contains:

- A field.
- An operator.
- A value.
- Logical operators such as AND and OR.
- An action.

Example:

```
cam-gate:
  object:
    filters:
      - name: no_traffic_light
        drop: on_match
        where: [[tag, "===", traffic light]]
```

This filter means:

If the object's tag equals `traffic light`, drop it according to the configured filter action.

However, the session discovered that Shinobi's filter behaviour is more complicated than simply evaluating every condition independently for every box.

## 9.3 The important box-order behaviour

Imagine the detector returns:

```
Box 0: truck, confidence 0.80
Box 1: traffic light, confidence 0.60
```

The filter removes the traffic-light box and retains the truck.

But consider the reverse order:

```
Box 0: traffic light, confidence 0.80
Box 1: truck, confidence 0.50
```

The entire event is dropped.

Why?

Because Shinobi evaluates the first box differently from subsequent boxes.

The first box can determine whether the whole event is dropped.

This is important because the AI normally sorts predictions by confidence.

Therefore, a high-confidence unwanted detection can cause a lower-confidence wanted detection to disappear as well.

## 9.4 Why one condition per drop filter?

The session also discovered that conditions are not evaluated as a simple per-box AND expression.

For example:

```
tag === person
AND
confidence < 0.6
```

It might seem that this means:

Only delete persons whose confidence is below 0.6.

But the actual behaviour can remove different boxes that independently match either condition.

This is dangerous.

The project's configuration validator therefore refuses `drop: on_match` with more than one condition.

The practical lesson is:

Use one condition per drop filter when removing individual unwanted object categories.

# 10. Understanding the actual results

This is the central result of Session 16.

The evaluation used:

- Confidence threshold: 0.5.
- IoU threshold: 0.5.
- Gate traffic-light filter enabled.

## Detection performance by camera

Precision and COCO recall percentages at the live settings.

Precision

COCO recall

0%25%50%75%100%GateQuayWaterway

| Camera   | Precision | COCO recall | Port recall |
| -------- | --------- | ----------- | ----------- |
| Gate     | 62%       | 20%         | 10%         |
| Quay     | 88%       | 58%         | 44%         |
| Yard     | —         | —           | 0%          |
| Waterway | 100%      | 29%         | 29%         |
| All      | 79%       | 33%         | 9%          |

The overall result is:

- Precision: 79%.
- COCO recall: 33%.
- Port recall: 9%.
- False events: 0.

The interpretation is:

The detections that the system reports are mostly correct, but the system misses many real objects.

Let's examine each camera.

## 10.1 Gate camera

[豪州の自動化コンテナターミナルのゲートシステム](https://images.openai.com/static-rsc-4/Vf-vA2clzlTeUVvVFYv7TbmhwCQr69mSKN_jDbNHxiodgCqZCJmbzniQWTrz9fiJeZ7GfWyDUSAPx7XtGM2F6ePIdbUdwmfNwBirbiS793LkfHwI2ETNL1miK6RBBQ4TOkox9u3B0_xadBwe4fEuI0jWKrAYd4MXqVi-IGiLPpA?purpose=inline)

[sentanlabo.jp](https://www.sentanlabo.jp/news-item/%E8%B1%AA%E5%B7%9E%E3%81%AE%E8%87%AA%E5%8B%95%E5%8C%96%E3%82%B3%E3%83%B3%E3%83%86%E3%83%8A%E3%82%BF%E3%83%BC%E3%83%9F%E3%83%8A%E3%83%AB%E3%81%AE%E3%82%B2%E3%83%BC%E3%83%88%E3%82%B7%E3%82%B9%E3%83%86%E3%83%A0?utm_source=chatgpt.com)

Results:

- Precision: 62%.
- COCO recall: 20%.
- Port recall: 10%.
- Frames with objects that fire: 13/20.
- False events: 0.

What does the AI detect?

- Cars: 11/11 correctly detected.
- Trucks: It mostly detects only the tractor.
- Cranes: It does not detect them.

The biggest problem is truck bounding boxes.

The AI identifies the tractor but misses the attached trailer.

For the complete truck, the bounding box should cover both tractor and trailer.

However, the AI predicts only a smaller box around the tractor.

This causes a mismatch in IoU.

The prediction is therefore considered an FP and the complete truck label an FN.

Nevertheless, the event itself is useful because a truck really is present.

Conclusion: The gate camera is useful for detecting cars and identifying some truck traffic, but its bounding boxes are not reliable for measuring full truck dimensions.

## 10.2 Quay camera

[Transport services exports worth €245 billion in 2023 - Nachrichtenartikel - Eurostat](https://images.openai.com/static-rsc-4/nLICI_-9rVADLbhHlECXHzDvTw_a049_GHg18mBbmp06MS5seAfISpeCpkyTmA2xqnIJ_1hEajYVoLlgU7tMdxJOJ0-oaB4l5vgyttwSvlvYINOCb5YD5SdL7yM6m2iqkehofH0Tn507GjYUxOi2Q_embcRVckkJfaQ1qB-X1BQ?purpose=inline)

[ec.europa.eu](https://ec.europa.eu/eurostat/web/products-eurostat-news/w/ddn-20250506-1?utm_source=chatgpt.com)

Results:

- Precision: 88%.
- COCO recall: 58%.
- Port recall: 44%.
- Frames with objects that fire: 19/19.
- False events: 0.

The AI detects:

- Trucks: 19/19.
- Small persons: 3/19.

It also mistakes a crane leg for a truck three times.

The main issue is the small person.

The person is only approximately:

- 6–9 pixels wide.
- 15–19 pixels high.

This is extremely small for an object detector operating on a 640 × 360 image.

The detector's confidence fluctuates between 0.31 and 0.57.

With a threshold of 0.5, it detects the person in only three out of 19 frames.

Conclusion: The quay camera performs best overall, but person detection remains unreliable.

For safety-related applications, this level of person detection is insufficient.

The document suggests using more image pixels, such as a main-stream crop or higher-resolution region.

## 10.3 Yard camera

[First Konecranes RTG cranes still going strong | Konecranes](https://images.openai.com/static-rsc-4/qPKQWm2nQlIkGQ6hLoxwTub0-MGzWbeL2kv1eDwGvztAiJL-VtFN5N4dGiXzMspazOEpy9qH8Z8tV9mkl7PuRlWw_biYhrh7X6xOgOzB2yn1eY6QgJsz17QPvo7tG476rnwFlrAh9zZgXggvsNRgk9h_vQWyAZYaHME-jocxorE?purpose=inline)

[konecranes.com](https://www.konecranes.com/discover/first-konecranes-rtg-cranes-still-going-strong?utm_source=chatgpt.com)

Results:

- Port recall: 0%.
- Frames with objects that fire: 0/20.
- 195 containers labelled.
- 81 cranes labelled.

Why does the AI detect nothing?

Because containers and cranes are not available COCO classes.

The model cannot specifically recognize them.

This is not solved by simply lowering the confidence threshold.

The appropriate solution is a detector trained for port-specific objects.

Conclusion: The current YOLO model is unsuitable for meaningful yard object detection.

## 10.4 Waterway camera

[Live Webcam Port Huron View](https://images.openai.com/static-rsc-4/bohdCCVh1yZqW6Ja7Y3Gfqg1W8g-95yaJhGxzN8vQLiWNyv2wVWqFOVhKyEG0mvIhl4-MosZFn5EeQeGifmtQZ1P5_FRbHtvEJjZq36TcDvXQ8r6BOYUHRmIq7Z8sennwZPgdsMpdtgHYUDbsWdvoyoinN0rqkirofqmPFNWaQg?purpose=inline)

[webcamera24.com](https://webcamera24.com/camera/usa/port-huron-view/?utm_source=chatgpt.com)

Results:

- Precision: 100%.
- COCO recall: 29%.
- Port recall: 29%.
- Frames with objects that fire: 5/19.
- False events: 0.

The AI can detect ships when they are relatively far away.

However, when a ship gets close, its hull fills almost the entire image.

The detector then struggles.

Why?

The model was trained to identify complete objects.

When the image contains mostly a large red ship hull, the detector loses important contextual information:

- The ship's edges.
- The surrounding water.
- The horizon.
- The complete ship shape.

In nine frames, the hull fills almost the entire image and is not detected.

Conclusion: The problem is largely related to camera positioning and the visible field of view.

A wider view from farther away could improve detection.

# 11. Confidence threshold analysis

The confidence threshold determines how confident YOLO must be before its predictions are accepted.

For example:

```
YOLO confidence = 0.50
```

A prediction with confidence 0.80 is accepted.

A prediction with confidence 0.35 is rejected.

The session tested several thresholds.

## Overall precision versus recall

Threshold sweep with filters off. Values are percentages.

PrecisionRecall

0%25%50%75%100%0.350.400.450.500.600.70

| Threshold | Precision | Recall |
| --------- | --------- | ------ |
| 0.35      | 71%       | 58%    |
| 0.40      | 71%       | 54%    |
| 0.45      | 75%       | 49%    |
| 0.50      | 79%       | 37%    |
| 0.60      | 83%       | 24%    |
| 0.70      | 96%       | 21%    |

Notice the relationship:

When the threshold decreases:

- More detections are accepted.
- Recall generally increases.
- More incorrect detections can appear.
- Precision can decrease.

When the threshold increases:

- Fewer detections are accepted.
- Precision generally increases.
- Recall decreases.

### Why keep 0.5?

The project decided to keep:

```
YOLO_CONFIDENCE: 0.5
```

The reason is that this setting produced zero false events across the evaluated cameras with the configured filters.

Lowering the threshold increased recall, but also introduced false events.

The project therefore prioritizes avoiding false events over detecting every possible object.

An important qualification: these results are based on a relatively small dataset, so they should not be treated as a guarantee of future performance.

# 12. Why the gate truck bounding boxes fail

This is one of the most interesting technical findings.

The AI recognizes the tractor but not the full trailer.

[Skeletal trailer - leasing or rental | TIP Group](https://images.openai.com/static-rsc-4/RFJdoe1ekbetmMalUqQ39eCxCEilH7xjjeCEe6nQRkFMhf30Zjx3fJm87r8AyOkYSEtVgudKEpKAAYJUoYCrakrDtAfBAIlP1r_Lzk9IZbn5vZL33Lo9HgRm8CaiKf9jbiuCjB1EIMZ1I3g4shzZQaSg8BMcALDODw9kSdedxtE?purpose=inline)

[tip-group.com](https://www.tip-group.com/en/fleet/chassis/container-chassis?utm_source=chatgpt.com)

The manually labelled truck covers:

- Tractor.
- Attached trailer.
- Load, if present.

The AI prediction covers only the tractor.

According to the document:

- Complete truck width: approximately 137 pixels.
- Predicted tractor width: approximately 72 pixels.
- IoU: approximately 0.42.

The required IoU is 0.5.

Therefore:

\\[ 0.42<0.5 \\]

The prediction is classified as a false positive and the truck as a false negative.

However, the AI has still provided useful information:

A truck is present at the gate.

This demonstrates why the project separates event correctness from bounding-box correctness.

It also explains why reducing the IoU threshold to 0.3 changes the gate results significantly.

At IoU 0.3:

- TP increases from 18 to 27.
- FP decreases from 9 to 0.
- Precision increases from 67% to 100%.
- Recall increases from 28% to 42%.

These are filter-off results, not the headline live configuration.

The project retains IoU 0.5 for comparability with standard evaluation conventions.

# 13. Why the gate traffic-light filter is retained

The traffic-light filter removes an object that the project does not need.

The live test evaluated 146 gate events over 40 minutes.

Results:

| Event type                          | Count |
| ----------------------------------- | ----- |
| No traffic light                    | 109   |
| Vehicle first, traffic light second | 25    |
| Traffic light only                  | 8     |
| Traffic light first, vehicle second | 4     |
| Total                               | 146   |

The filter successfully:

- Removes traffic-light-only events.
- Removes traffic-light boxes from events that also contain vehicles.

However, in four events, the traffic light was first in the detection order.

In those cases, the entire event was lost.

That represents:

\\[ \frac{4}{146}\times100\approx2.7\\% \\]

Despite this limitation, the project retains the filter because the overall benefit is considered worthwhile.

The lost event may also be detected again in the next frame.

# 14. Final engineering decisions

The session makes several concrete decisions.

| Configuration               | Decision          | Reason                                           |
| --------------------------- | ----------------- | ------------------------------------------------ |
| Global confidence threshold | Keep 0.5          | Zero false events in the evaluation              |
| Gate traffic-light filter   | Keep              | Removes unnecessary traffic-light events         |
| Minimum-size filter         | Do not add        | Could remove real small objects                  |
| Quay crane-leg filter       | Do not add        | Could remove real trucks because the camera pans |
| Waterway detection          | Do not enable yet | High processing cost and limited benefit         |
| Yard detection              | Do not enable     | Model cannot recognize port-specific objects     |

## Why no minimum-size filter?

A minimum-size filter sounds useful because it can remove tiny detections.

But the quay camera has a genuine small person.

Removing small detections would remove that person too.

Therefore, the project does not introduce a size filter.

## Why no waterway object detection?

The waterway camera does not have motion zones.

This means frames would flow continuously at two FPS.

The document estimates this would add:

- 120 inferences per minute.
- To a plugin already processing approximately 155 inferences per minute.
- With roughly 50 seconds of every minute occupied.

The additional load is not justified by detecting ships in only five of the 19 evaluated frames.

## Why no yard detection?

Because the model has no dedicated classes for containers and cranes.

A different detector is needed.

# 15. New API key and end-to-end testing

Session 16 also introduces an additional API key:

```
SHINOBI_TRIGGER_API_KEY
```

Its permission is:

```
control_monitors
```

The project adds:

```
ShinobiClient.trigger()
```

Why?

The team needs to verify that Shinobi's real event filters behave exactly like the offline emulator.

The test injects synthetic events into the live Shinobi server.

For example:

```
Synthetic event
       |
       v
Shinobi receives event
       |
       v
Event filters execute
       |
       v
Check saved Events row
       |
       v
Compare with emulator result
```

This is called an end-to-end test.

It verifies the actual integration rather than testing only the Python implementation.

The test checks situations such as:

- An event being correctly saved.
- An event being rejected by a filter.
- An event being temporarily blocked by the motion lock.
- An invalid monitor or insufficient API permission.

This is important because a local emulator might behave correctly while the real server behaves differently.

The end-to-end test helps detect that type of discrepancy.

# 16. The five most important lessons

The document concludes with five lessons.

1\. Live event counts do not measure detection quality.

Knowing that a camera generates nine events per minute tells us nothing about how many objects it misses.

We need labelled images and a proper evaluation process.

2\. Always check the ground-truth labels.

The initial dataset contained 30 labelling mistakes.

These mistakes significantly affected precision and recall.

For example, quay recall initially appeared to be 100%, but after correcting the labels it became 58%.

3\. Reproduce the exact live image pipeline.

Different encoding quality can change detector results.

Offline evaluation must use images that resemble what the production model actually receives.

4\. Bounding-box conventions matter.

A truck can mean:

- Only the tractor.
- Tractor plus trailer.

The chosen definition changes the evaluation results.

5\. Shinobi filters must be tested against the real server.

The filter logic is box-ordered and does not behave like a simple per-box AND condition.

The emulator and end-to-end tests protect against incorrect assumptions.

# 17. What happens next? (Open questions)

Session 16 identifies three future improvements.

1. Person detection: Can the main 1280 × 720 stream provide enough pixels to detect quay workers reliably without excessive CPU usage?
2. Truck geometry: Could the evaluator combine a tractor detection with the motion-zone extent to estimate the full truck length?
3. Per-camera thresholds: Should different cameras use different confidence thresholds, or should the project introduce its own detector service?

These questions are intended for future work, particularly Session 23.

# 18. Final summary — What you should remember

## Session 16 in one overview

1. Create ground truth

   79 manually labelled frames and 474 bounding boxes.
2. Run the same detector

   Reproduce the production image pipeline and run the pinned YOLO model.
3. Measure detection quality

   Calculate TP, FP, FN, precision, recall and IoU.
4. Evaluate Shinobi filters

   Understand how filters remove boxes or entire events.
5. Verify with live tests

   Inject synthetic events and compare Shinobi against the emulator.
6. Make engineering decisions

   Keep the 0.5 threshold, retain the gate filter, and avoid enabling unsuitable cameras.

## Important numbers to memorize

| Measurement                   | Result |
| ----------------------------- | ------ |
| Labelled frames               | 79     |
| Labelled boxes                | 474    |
| Overall precision             | 79%    |
| Overall COCO recall           | 33%    |
| Overall port recall           | 9%     |
| False events at live settings | 0      |
| Global confidence threshold   | 0.5    |
| IoU threshold                 | 0.5    |
| Gate traffic-light event loss | 2.7%   |

The central message of Session 16:

A good object-detection system is not simply one that generates many events. It must generate correct events, detect enough of the objects that actually exist, and operate within the limitations of its model, cameras and processing resources.

## Resources for further learning

These resources explain the underlying technical concepts:

1. [COCO Dataset](https://cocodataset.org/?utm_source=chatgpt.com) — Understand object classes and annotations.
2. [YOLO documentation](https://docs.ultralytics.com/guides/yolo-common-issues/?utm_source=chatgpt.com) — Understand object detection and common limitations.
3. [Precision and Recall](https://en.wikipedia.org/wiki/Precision_and_recall?utm_source=chatgpt.com) — Learn the evaluation metrics.
4. [Intersection over Union](https://learnopencv.com/intersection-over-union-iou-in-object-detection-and-segmentation/?utm_source=chatgpt.com) — Understand bounding-box matching.
5. [Shinobi documentation](https://docs.shinobi.video/?utm_source=chatgpt.com) — Learn about Shinobi's monitoring and event system.

My Trust: 10/10 — Explanation grounded in your uploaded Session 16 document.

Uncertain points: The reported performance is based on only 79 frames. The document itself estimates uncertainty of approximately ±10–20 percentage points per camera. These results should not be interpreted as guaranteed production performance.


```
Imagine:
- A ground-truth truck box includes the tractor and trailer.
- The AI detects only the tractor.
The AI box is contained inside the ground-truth box.

IoU might be relatively low because the predicted box is much smaller.

IoA can show that most of the predicted box is nevertheless inside the actual truck.
```

Session 16 uses IoA in special matching situations, particularly for identifying predictions that represent only part of an object.

Area of one box in this example is the area of tractor & trailer or only the tractor? If yes then IoA = IoU, i.e. Area of one box = Union Area, right? explain this example by pictures or diagrams

Excellent question! You've identified an important distinction between IoU and IoA, and there is a subtle detail in the denominator that makes all the difference.

The short answer is:

The area of one box in the IoA calculation depends on which box we choose as the reference.

In our truck example:

- Ground-truth box = tractor + trailer.
- Predicted box = tractor only.

Let's visualize it.

## 1. First, visualize the two bounding boxes

Imagine the truck from a side view:

Ground truth

AI prediction

Schematic illustration, not to scale. The blue rectangle is entirely inside the green rectangle.

We have two boxes:

Box A — Ground truth

Covers the entire truck:

\\[ A\_{GT}=A\_{tractor}+A\_{trailer} \\]

Box B — AI prediction

Covers only the tractor:

\\[ A\_{AI}=A\_{tractor} \\]

Because the AI box is entirely inside the ground-truth box:

\\[ Intersection=A\_{AI} \\]

This is the key observation.

## 2. Calculate IoU

Remember:

\\[ IoU=\frac{Intersection}{Union} \\]

The union is the combined area of both boxes, without counting their overlapping area twice.

Since the AI box is completely inside the ground-truth box:

\\[ Union=A\_{GT} \\]

Therefore:

\\[ IoU=\frac{A\_{AI}}{A\_{GT}} \\]

Let's use example numbers:

- Ground-truth area = 1,000 pixels².
- AI tractor area = 400 pixels².
- Intersection = 400 pixels².
- Union = 1,000 pixels².

Thus:

\\[ IoU=\frac{400}{1000}=0.4 \\]

IoU = 0.40

The prediction fails the required IoU threshold of 0.5.

## 3. Now calculate IoA

Here is where your question becomes important.

IoA means:

\\[ IoA=\frac{Intersection}{Area\ of\ one\ selected\ box} \\]

There are TWO possible choices.

### Case A: Use the AI-predicted box as the denominator

\\[ IoA\_{AI}=\frac{Intersection}{A\_{AI}} \\]

Using our example:

\\[ IoA\_{AI}=\frac{400}{400}=1.0 \\]

IoA = 100%

Interpretation:

100% of the AI's predicted tractor box lies inside the actual truck.

This tells us that the AI has detected a real part of the truck, even though it has not detected the entire truck.

### Case B: Use the ground-truth box as the denominator

\\[ IoA\_{GT}=\frac{Intersection}{A\_{GT}} \\]

\\[ IoA\_{GT}=\frac{400}{1000}=0.4 \\]

IoA = 40%

This tells us that the AI prediction covers only 40% of the ground-truth truck area.

## 4. Compare the three calculations

## Same boxes, different measurements

IoU

# 40%

Intersection ÷ Union

IoA (AI box as denominator)

# 100%

Intersection ÷ AI-predicted area

IoA (ground-truth box as denominator)

# 40%

Intersection ÷ ground-truth area

## 5. So, is your statement correct?

You asked:

> If the area of one box is the tractor and trailer, then IoA = IoU, because the area of one box = Union Area, right?

Yes! Absolutely correct.

When one box is completely contained inside another:

\\[ Union=Area\_{\text{larger box}} \\]

Therefore, if you calculate IoA using the larger box as the denominator:

\\[ IoA\_{larger}=IoU \\]

But if you calculate IoA using the smaller, predicted box:

\\[ IoA\_{smaller}=100\\% \\]

This is why IoA is directional, whereas IoU is symmetric.

## 6. Why is this useful in Session 16?

The document defines IoA as a way to measure how much of one box lies inside another.

This helps identify a prediction that represents only part of an object.

For the gate truck:

- YOLO recognizes the tractor.
- It does not recognize the complete trailer.
- The prediction is entirely inside the ground-truth truck.
- IoU is approximately 0.42.
- IoU fails the 0.5 matching threshold.

The evaluator can recognize this as a part-of-truck detection.

It can therefore distinguish two questions:

1. Is the bounding box geometrically correct? No.
2. Does the detection indicate that a real truck is present? Yes.

That distinction is important for evaluating port surveillance events.

The most important thing to remember:

- IoU measures overlap relative to the combined area of both boxes.
- IoA measures overlap relative to one specifically selected box.
- If the predicted box is completely inside the ground-truth box, IoA using the predicted box equals 100%.
- IoA using the ground-truth box equals IoU in this containment situation.

My Trust: 10/10

Resources:

- [Intersection over Union (IoU) explained](https://learnopencv.com/intersection-over-union-iou-in-object-detection-and-segmentation/?utm_source=chatgpt.com)
- [COCO evaluation methodology](https://cocodataset.org/?utm_source=chatgpt.com)




```
Why reproduce the exact pipeline?

Because image quality influences object detection.

The document identifies a specific problem with JPEG encoding.

The first JPEG images were approximately 21 KB, while live images were approximately 11.8 KB.

This means the first images were sharper than the images the live detector normally receives.

If the evaluation used these sharper images, the results would not accurately represent production performance.

The solution was to encode the clips twice and keep the second pass.

This allowed the offline JPEGs to resemble the live JPEGs much more closely.```

detailed explanaition

# Why reproduce the exact image-encoding pipeline?

The main idea is:

The object detector must be tested on images that look like the images it will actually receive in production.

If the test images are clearer or higher quality than the live images, the evaluation can give an overly optimistic impression of how well the detector works.

Let's break this down step by step.

## 1. First, understand the complete process

The system uses camera footage to detect objects such as trucks, cars, people, and other port-related objects.

The general process looks like this:

Camera footage

Video recorded by the camera

Extract video frames

Select individual images from the video

JPEG encoding

Compress each frame into an image file

Object detector

Analyzes the JPEG and predicts objects

Detection results

Bounding boxes, object classes and confidence scores

An important detail is that the detector does not necessarily receive the original video frame. It receives an encoded JPEG image.

That JPEG encoding step can change the image's visual quality.

## 2. What is JPEG encoding?

JPEG is an image format that reduces file size by compressing image information.

For example, imagine a camera captures a frame containing a truck.

[Exportime - Soluções em Comércio Exterior](https://images.openai.com/static-rsc-4/yQfA62Zt5o6y2fs_cH3fvMrhdhma_DFIfTz_j9_Zm1PE0XppeqFzUB06zJPcQaHo-7WYKCdreK9Dx0oKdRe-dAC-pezuXJc31q6X5zNcX1_yYfCW8iQckWiaQKgrJIC1AAwUax53EL1rh8r3mNeQxuCrBHErM3ejC9u3ZNGGvUQ?purpose=inline)

Higher image quality

More visible detail and sharper edges.

[Coal India's second-quarter profit misses view on tepid demand, lower e-auction premiums | Reuters](https://images.openai.com/static-rsc-4/2AInOZ7I4mSV_M2demDdVo8AWYxj6ndjqUiLOjQK1Dh5iiD-GN9V7JpNF4JpzR92kvNUITLfo3tM_sNUIBXmB75qyMzfDgOqebum6x48ARMzkl34gWsbK-dcTeggXortKWnLo90eeEuxWgXAziE2CMuN5pqGlg6lKaJMDxmv-ww?purpose=inline)

Lower image quality

Fine details may become less clear.

When JPEG compression is stronger, some visual information is lost.

For example:

- Small details may become less visible.
- Edges can become less sharp.
- Fine textures can disappear.
- Compression artifacts may appear around objects.

Why does this matter?

Because an object detector uses visual information to recognize objects.

For example, a detector might use:

- The shape of a vehicle.
- The outline of its body.
- The wheels.
- The boundaries between the vehicle and its surroundings.

If these details become less clear, the detector's predictions can change.

Important: JPEG compression does not always cause a detector to perform worse. The important point is that changing image quality can change detection results, so the evaluation should reproduce the production conditions.

## 3. The problem discovered in Session 16

The document identifies a difference between the offline evaluation images and the images received by the live system.

| Image source              | Approximate JPEG size |
| ------------------------- | --------------------- |
| First offline JPEG images | 21 KB                 |
| Live JPEG images          | 11.8 KB               |

Let's compare them.

Offline images

# 21 KB

Larger files

Live images

# 11.8 KB

Smaller files

The offline files are approximately 1.78 times the size of the live files.

The offline images were larger, which indicated that they were likely retaining more image information than the live images.

The document describes the first offline images as sharper than the live images.

This creates a problem.

Imagine you are evaluating a student who will take an exam using a difficult version of a test. Instead, you give them an easier version during practice.

They might perform very well during practice, but their actual exam performance could be worse.

The same principle applies here:

- Offline evaluation: detector receives sharper images.
- Production: detector receives more compressed images.
- Result: offline performance might not represent actual production performance.

## 4. Why does image quality affect detection results?

Let's use a truck as an example.

Suppose the detector needs to identify a truck in a port camera image.

Example: detecting a truck

[Asset Finance | Commercial Banking | HSBC](https://images.openai.com/static-rsc-4/v11PvshNpuZ4t6TTlnt8e8UKxmfZwU3gw81vIc78b3WMUMl5kJapTGOgLpw4pu35F96QSWRF4DFzVybFm7L53WJv0bA9hR3-STH7yyYYmToTrQvDhZH4b7rN4VNG8v1kRB1u8H7KIHTT4TjlM-S-QwGNgP5exklxFZgAirpalQM?purpose=inline)

[business.hsbc.uk](https://www.business.hsbc.uk/en-gb/products/asset-finance?utm_source=chatgpt.com)

Imagine the detector needs to identify the truck's complete outline.

With a sharper image:

- The truck's outline is clearer.
- Its shape is easier to distinguish from the background.
- Small visual details are more visible.

With a more compressed image:

- The outline may be less clear.
- Some details may disappear.
- The detector may produce a different confidence score or bounding box.

This can affect two important evaluation measurements:

Precision

Precision measures how many predicted detections are correct.

\\[ Precision=\frac{TP}{TP+FP} \\]

Recall

Recall measures how many real objects the detector successfully finds.

\\[ Recall=\frac{TP}{TP+FN} \\]

For example, a truck might be easy to recognize in a sharper image but harder to recognize in a compressed image.

The detector could miss it in the second case, increasing false negatives and reducing recall.

This is an illustrative example, not a claim that the document measured this exact change.

## 5. What did the team do to solve the problem?

The document says that the team encoded the clips twice and kept the second pass.

The purpose was to make the offline JPEGs more closely resemble the live JPEGs.

Conceptually, the process was:

Original video clip

First encoding pass

Convert the video into JPEG images.

First-pass JPEGs

Second encoding pass

Encode the resulting material again.

Keep this output

Offline JPEGs for evaluation

More closely resemble the live JPEG images.

Why encode twice?

The intention was to reproduce the effective image quality of the live pipeline.

The second encoding pass can introduce additional JPEG compression, reducing the file size and potentially removing some visual detail.

This is why the team did not simply use the first JPEG images.

One important distinction: The document tells us that the team used two encoding passes and kept the second output. It does not, in the selected passage alone, give every technical encoding parameter or prove that two-pass encoding will always produce an exact match. The goal was to make the images much more similar to the live images.

## 6. Why is matching the live pipeline so important?

The team wanted to answer this question:

> How well does our object detector actually perform when it receives the same kind of images it receives in production?

To answer that question fairly, the test conditions should match the real operating conditions.

Consider these two situations:

| Condition              | Offline test                  | Live system                  |
| ---------------------- | ----------------------------- | ---------------------------- |
| Source                 | Video clip                    | Camera stream                |
| JPEG size              | Approximately 21 KB initially | Approximately 11.8 KB        |
| Image quality          | Initially sharper             | More compressed              |
| Detector               | Same detector                 | Same detector                |
| Evaluation reliability | Initially less representative | Actual production conditions |

After reproducing the encoding pipeline, the team could evaluate the detector using more representative images.

This makes the evaluation more useful for deciding whether a detection threshold or camera-specific filter should be changed.

## 7. What would happen if the team ignored this problem?

There are several possible consequences.

1\. Overestimating detector performance: The detector might perform better on sharper offline images than on live images.

2\. Choosing unsuitable thresholds: A confidence threshold that works well on sharper images might not work as well on compressed live images.

3\. Incorrect camera comparisons: One camera might appear to perform better simply because its evaluation images have different visual quality.

4\. Unreliable engineering decisions: The team could enable a filter or detection configuration based on results that do not reflect production.

The key is that an evaluation should measure the detector, not accidentally measure differences between image-processing pipelines.

## 8. The most important distinction to remember

There are two separate things:

- Object detection quality: How well the model recognizes objects.
- Image-processing quality: How the video is converted into images before the model sees them.

If you change the second one, you may change the first one's measured performance.

That is why Session 16 emphasizes reproducing the exact pipeline.

In one sentence:

The team encoded the video clips twice and kept the second output so that offline evaluation images would have image quality closer to the smaller, more compressed JPEG images received by the live detector, making the evaluation more representative of real production performance.

## Resources

- [JPEG compression explained — Adobe](https://www.adobe.com/creativecloud/file-types/image/raster/jpeg-file.html?utm_source=chatgpt.com)
- [Object detection metrics — Ultralytics](https://docs.ultralytics.com/guides/yolo-performance-metrics/?utm_source=chatgpt.com)
- [COCO dataset and evaluation](https://cocodataset.org/?utm_source=chatgpt.com)

My Trust: 9/10

Check the uncertain points: The file sizes and the two-pass procedure come from the selected Session 16 text. File size is an indicator, not a perfect measurement of image quality. The exact encoding settings and the precise reason the second pass matches the live pipeline would require the full encoding commands or configuration.



```
The entire event is dropped.

Why?

Because Shinobi evaluates the first box differently from subsequent boxes.

The first box can determine whether the whole event is dropped.

This is important because the AI normally sorts predictions by confidence.

Therefore, a high-confidence unwanted detection can cause a lower-confidence wanted detection to disappear as well.
```

Does this make sence? what is the truck event is needed?

Yes, it makes sense, but the explanation is a little confusing because a truck event and a detection box are two different things.

Let's use a simple example.

## 1. What is a truck event?

A truck event is an event generated by the detection system when it detects a truck in the camera footage.

For example, imagine a camera monitoring a port entrance.

[ANPR System for Ports](https://images.openai.com/static-rsc-4/peTY-xQQahRp9EJAU63e6-vZupnLixAcoytzljJ1Qnxpn4XlMBT_hcMdjUBPR9TSJdS5LwY48IZtz7PMyMZnhlQ2K1olMUcxKOZwSDb3IPLoQijQEiIVWmB0NozW4aYHDIUeaIIWQhEuAgosb3DekIRnlcw9aAbLSwWoxOMwH3Y?purpose=inline)

[maritime.mantratec.com](https://maritime.mantratec.com/port-solutions/anpr?utm_source=chatgpt.com)

When the AI detects a truck, it can generate an event containing information such as:

- The detected object and its class.
- Its bounding box (location in the image).
- Its confidence score.
- The camera and time of detection.

The event is what the system processes as a detection notification or trigger. It is not the truck itself.

## 2. What are the first box and subsequent boxes?

Imagine the AI detects two objects in the same image:

Example image:

[English -Article2 browser  - APM Terminals](https://images.openai.com/static-rsc-4/S6GoxtoqnL8QRXDL-2FH0GCSx4Etous55kFHCV2bwvVH0ijSpreuqayFbOSvOgEUYPzw7RUjXfK3WMjEmtSJR6g-7dqAbM2kBdvg4LZ37pfmWG6OAhYNDtnz4V_BPN_grZCizcnrXKtiDyJPOpEVgRX4GR6v-PFRGGjq_d-8C5Y?purpose=inline)

[cms-rendering-eu1-cdt.apmterminals.com](https://cms-rendering-eu1-cdt.apmterminals.com/en/cit/news/news-and-updates/Article2?utm_source=chatgpt.com)

Box 1: Traffic light

Confidence 95%

Unwanted detection for this truck event.

Box 2: Truck

Confidence 85%

Wanted detection.

The AI normally sorts its predictions by confidence.

That means the traffic light appears first because its confidence is 95%, while the truck appears second with 85%.

Now imagine Shinobi applies a filter that rejects traffic-light detections.

According to the behavior described in Session 16, the first box can determine whether the entire event is dropped.

The result could be:

First box: Traffic light

Rejected

Entire event dropped

The truck detection may disappear too.

This is the surprising part: even though the truck was a wanted detection, it might not be processed because the first box caused the whole event to be rejected.

## 3. Why is this a problem?

The system wants to detect trucks, not traffic lights.

But because the traffic light has a higher confidence score, it appears first.

If Shinobi drops the entire event based on that first box, the system loses the truck detection as well.

This creates a false negative at the event level: a real truck was present, but the system failed to produce the desired truck event.

## 4. What does the team need to be careful about?

The important distinction is:

- Rejecting one unwanted box should ideally reject only that box.
- Rejecting the entire event removes all detections belonging to that event.

The Session 16 passage describes a Shinobi behavior where the first box can affect the entire event. It is therefore important to understand how box ordering interacts with the filters.

One caveat: This is an explanation of the behavior described in the document, not a guarantee that every Shinobi version or every filter behaves this way.

## 5. Test your understanding

Suppose the AI detects:

- Traffic light: 98% confidence (first box).
- Truck: 90% confidence (second box).

The traffic-light filter rejects the first box, and Shinobi drops the entire event.

What happens to the truck detection?

It may disappear with the entire event.

It is always kept because it is a truck.

Its confidence automatically increases.

Check answer



In short: A truck event is the detection notification containing the truck prediction. The problem is that an unwanted first detection (such as a traffic light) can cause Shinobi to discard the whole event, taking the wanted truck detection with it.

Resources:

- Your Session 16 document — Shinobi filtering and box-order behavior.
- [Shinobi documentation](https://docs.shinobi.video/?utm_source=chatgpt.com)

My Trust: 9/10

Check the uncertain points: The exact filter behavior should be confirmed against the Shinobi version and configuration used in the project.