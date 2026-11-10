"""Detection quality: labelled port frames vs the object detector plugin (Session 16).

    frames ──▶ label (you, in the browser) ──▶ labels.yaml
       └─────▶ predict (plugin's own detector, low threshold) ──▶ predictions.json
                                                score: IoU matching ──▶ precision / recall

Explained in docs/notes/16-detection-quality.md.
"""
