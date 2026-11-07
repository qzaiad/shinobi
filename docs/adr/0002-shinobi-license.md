# ADR 0002 — Shinobi license and replaceability

- Status: accepted
- Date: 2026-10-17 (after Session 9)

## Context

The pinned image (`SHINOBI_DIGEST`, commit `63755122`, 2026-09-02) is built from
`gitlab.com/Shinobi-Systems/Shinobi`, whose README calls it **Shinobi Pro** ("Shinobi Open Source
Software"). It is not the older GPLv3 **Shinobi CE** (separate repo, unmaintained for years; from
memory, not re-checked).

Despite the name, `LICENSE.md` in the image (commercial terms dated 2022-07-12, file last changed
2025-01-18) is a **source-available EULA**, not an OSI open-source license: the code may be read and
modified, but use is restricted. The clauses that matter here, quoted from the file:

- **Commercial use** (needs a paid subscription) includes "Using the software in locations that
  engage in the buying and selling of goods and services", and since 2022-07-12:
  - "15 Active Monitor Rule : Having at least 15 Active Monitors and not a Primary School or
    Secondary School. Does not apply to Personal Use."
  - "150 Active Monitor Rule : If you have more than 150 Active Monitors please contact support
    for an Enterprise License. The retail Shinobi Pro license will not be applicable."
- **Free use** includes "When used for research or educational purposes" and "Testing Purposes
  (5 Cameras Maximum, 14 Days)", but "If you fall under any conditions for 'Commercial Use' none
  of the following can apply".
- Organisations using it for free "must display that you are using Shinobi Systems software as
  'shinobi.video'".
- "Shinobi Systems reserves the right to change the license and set of terms at any time.
  Continued use is agreement to those possible changes."
- Contributed code becomes property of Shinobi Systems.

The code enforces part of this: an unactivated install reports `"maxCameras": 15`
(`GET /activationStatus`) and refuses to add more monitors (Session 9 notes §1).

## Decision

1. Keep using Shinobi Pro for this project, which is research/education: free under the
   license **as long as fewer than 15 monitors are active** in Shinobi.
2. Treat any production use in a port (a commercial location, typically hundreds of cameras) as
   **requiring a paid license, likely an Enterprise License (> 150 monitors)**, to be clarified
   with Shinobi Systems and legal before deployment. This repo makes no such deployment.
3. Keep Shinobi **replaceable**: the only contract between Shinobi and the rest of the system
   stays the MQTT topics and JSON schemas in `docs/schema/` (already a rule in CLAUDE.md).
   Shinobi-specific code stays in the adapter layer (`services/common/shinobi_client.py`, the
   webhook receiver, the normalizer's `RawShinobiEvent`); the evaluator, twin-state and viewer
   must not know which NVR produced an event.

## Consequences

- **Active monitors ≤ 14.** Main streams only, or main + sub as separate monitors, must stay
  below 15. Session 34 (scaling to 16 cameras) scales the *simulator and MediaMTX* to 16, but
  Shinobi monitors stay ≤ 14; the rest of the measurement uses the own detection path
  (Session 23) or an alternative NVR.
- Our own Python services, the schemas and the twin are unaffected by Shinobi's license (they
  only talk to it over its API/webhooks/MQTT and contain no Shinobi code).
- Upstream changes to the license apply on upgrade: check `LICENSE.md` whenever `SHINOBI_DIGEST`
  changes (`git log -- LICENSE.md` inside the image).
- The image's `git remote -v` contains an upstream CI job token; never copy that output into notes.
- Before a production recommendation (Session 35 retrospective at the latest), compare
  alternatives behind the same contract; licenses to be verified then:
  Frigate (MIT, detection-focused, MQTT built in), ZoneMinder (GPL-2.0), Viseron (MIT),
  MediaMTX + own recorder + Session 23 detector, or commercial VMS products.

## Addendum 2026-11-07 (Session 15): object detection plugin

The YoloV9 ONNX plugin image (`registry.gitlab.com/shinobi-systems/shinobi-plugins/yolo-v9-onnx`,
`YOLO_PLUGIN_DIGEST`) is licensed separately. Its `LICENSE` file:

- "AGPL-3.0 License: This OSI-approved open-source license is ideal for students and enthusiasts"
- "Enterprise License: Designed for commercial use, this license permits seamless integration of
  Shinobi Systems software and AI models into commercial goods and services, bypassing the
  open-source requirements of AGPL-3.0."

The bundled YOLOv9 weights come from the YOLOv9 project (GPL-3.0, from memory, not re-checked).
For this project (education, nothing distributed) AGPL-3.0 is fine. Our derived image
(`services/yolo-plugin/`) adds files next to the vendor code without editing it; under AGPL,
offering a modified version over a network obliges publishing its source, which matters only for
a deployment. A production deployment needs the Enterprise License or a different detector behind
the same contract (Session 23: own detection path). Check the plugin `LICENSE` whenever
`YOLO_PLUGIN_DIGEST` changes.

Not legal advice; the license itself says to contact Shinobi Systems when unsure.
