# Shinobi Port Twin

Simulated cameras observing a real port stream RTSP into [Shinobi](https://shinobi.video)
(open-source NVR/CCTV), which records and runs detection. An evaluator turns detections into
port events, and a digital twin keeps port state and shows it in 3D.

This is the camera pillar of a larger "smart ports" twin. Components are decoupled: they talk
only through MQTT topics and JSON schemas (`docs/schema/`), so each part can be replaced or scaled.

## Architecture

```
media/ clips → FFmpeg publishers → MediaMTX (RTSP :8554)
  → Shinobi (:8080, monitors, recording, motion + object-detection plugin)
  → Mosquitto (MQTT :1883) / webhooks
  → services/evaluator → port/<site>/{detections,state,alerts}/...
  → services/twin-state (REST + WebSocket) → viewer/ (3D)
```

## Status

Early bootstrap. See [docs/progress.md](docs/progress.md) for session-by-session progress and
[docs/learning-plan.md](docs/learning-plan.md) for the roadmap.
