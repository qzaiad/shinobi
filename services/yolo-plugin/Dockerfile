# Shinobi's official YoloV9 ONNX object detection plugin (CPU build), pinned by digest, plus two
# fixes that need no change to vendor files (docs/notes/15-object-detection.md):
#   sitecustomize.py  ONNX Runtime threads/spinning (default burns 12 cores per inference)
#   backpressure.js   drop frames while inference is busy instead of queueing them without bound
#   supervise.sh      exit when the plugin stops reconnecting, so Docker restarts it
# License: AGPL-3.0 or Shinobi Enterprise; YOLOv9 weights GPL-3.0 (docs/adr/0002-shinobi-license.md).
ARG YOLO_PLUGIN_DIGEST
FROM registry.gitlab.com/shinobi-systems/shinobi-plugins/yolo-v9-onnx@${YOLO_PLUGIN_DIGEST}

COPY sitecustomize.py backpressure.js /opt/port-twin/
COPY --chmod=755 supervise.sh /usr/local/bin/supervise.sh

ENV PYTHONPATH=/opt/port-twin \
    NODE_OPTIONS="--require /opt/port-twin/backpressure.js" \
    ORT_INTRA_OP_THREADS=2 \
    ORT_ALLOW_SPINNING=0 \
    YOLO_MAX_IN_FLIGHT=2 \
    SKIP_WEIGHTS_DOWNLOAD=true

ENTRYPOINT ["/usr/local/bin/supervise.sh"]
