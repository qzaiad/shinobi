"""ONNX Runtime thread settings for the Shinobi YoloV9 plugin's inference server.

Python imports `sitecustomize` at startup from any sys.path entry; the Dockerfile puts this
directory on PYTHONPATH. The plugin's tools/yolov9_onnx_server.py calls
`ort.InferenceSession(model, providers=...)` without SessionOptions, so ONNX Runtime uses its
defaults: one intra-op thread per host core (14 here, also inside a `cpus: 2` container) that
spin-wait between operators. Measured on 640x360 frames (docs/notes/15-object-detection.md):

    default (14 threads, spinning)   95 ms wall  1134 ms CPU per frame
    2 threads, no spinning          140 ms wall   265 ms CPU per frame
    default under --cpus 2          965 ms wall  (14 threads throttled by the CFS quota)

The patch only fills in SessionOptions when the caller passed none; vendor files stay untouched.

    ORT_INTRA_OP_THREADS   threads per inference (0 = ONNX Runtime default)   default 2
    ORT_ALLOW_SPINNING     1 = idle threads busy-wait (lower latency, more CPU) default 0
"""

import os
import sys

try:
    import onnxruntime as _ort
except ImportError:  # some other Python in the image
    _ort = None

if _ort is not None:
    _Original = _ort.InferenceSession

    class _TunedInferenceSession(_Original):
        def __init__(self, path_or_bytes, sess_options=None, *args, **kwargs):
            if sess_options is None:
                sess_options = _ort.SessionOptions()
                threads = int(os.environ.get("ORT_INTRA_OP_THREADS", "2"))
                spinning = os.environ.get("ORT_ALLOW_SPINNING", "0")
                if threads > 0:
                    sess_options.intra_op_num_threads = threads
                sess_options.inter_op_num_threads = 1
                sess_options.add_session_config_entry("session.intra_op.allow_spinning", spinning)
                print(
                    f"[sitecustomize] onnxruntime intra_op_num_threads={threads or 'default'}"
                    f" allow_spinning={spinning}",
                    file=sys.stderr,
                    flush=True,
                )
            super().__init__(path_or_bytes, sess_options, *args, **kwargs)

    _ort.InferenceSession = _TunedInferenceSession
