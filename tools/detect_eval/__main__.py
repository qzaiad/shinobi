"""CLI: python -m tools.detect_eval frames|label|predict|score  (docs/notes/16-detection-quality.md)

frames [--init]      JPEGs of the labelled frames from the clips (--init: add missing cameras
                     with 20 evenly spaced empty frames to labels.yaml)
label [--port N]     browser labeller on 127.0.0.1 (writes labels.yaml on every change)
predict              run the plugin's detector on the frames -> data/detection/predictions.json
score [--iou 0.5] [--threshold 0.5] [--json]   precision / recall per camera and class
"""

from __future__ import annotations

import argparse
import sys

from sim import gen_compose
from tools.detect_eval import labels as lb


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.detect_eval")
    ap.add_argument("--labels", type=lb.Path, default=lb.DEFAULT_LABELS)
    sub = ap.add_subparsers(dest="cmd", required=True)
    fr = sub.add_parser("frames", help="regenerate the labelled JPEGs from the clips")
    fr.add_argument("--init", action="store_true", help="add missing cameras to labels.yaml")
    la = sub.add_parser("label", help="browser labeller")
    la.add_argument("--port", type=int, default=8765)
    sub.add_parser("predict", help="plugin detector on the frames")
    sc = sub.add_parser("score", help="precision / recall")
    sc.add_argument("--iou", type=float, default=0.5, help="min IoU for a hit (default 0.5)")
    sc.add_argument("--threshold", type=float, default=0.5, help="score cut-off (plugin: 0.5)")
    sc.add_argument("--filters", action="store_true", help="also apply detection.yaml filters")
    sc.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "frames":
            from tools.detect_eval import frames

            labels = lb.load(args.labels) if args.labels.exists() else lb.LabelSet()
            fleet = gen_compose.load(gen_compose.DEFAULT_CONFIG)
            labels = frames.generate(labels, fleet, init=args.init)
            if args.init:
                lb.save(labels, args.labels)
            return 0
        if args.cmd == "label":
            from tools.detect_eval import labeler

            return labeler.serve(args.labels, args.port)
        if args.cmd == "predict":
            from tools.detect_eval import predict

            return predict.run(lb.load(args.labels))
        from tools.detect_eval import score

        return score.main(args)
    except lb.LabelsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
