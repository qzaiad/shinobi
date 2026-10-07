# Media sources

Footage used by the camera simulator (`sim/`). The machine-readable list is
[`manifest.tsv`](manifest.tsv); `scripts/fetch_media.sh` downloads and verifies it.
Every `id` in the manifest must have a row here (enforced by `tests/test_media_manifest.py`).

## Policy

- **Media is never committed.** Only `manifest.tsv` and this file are tracked; everything
  else under `media/` is gitignored. Restore footage with `./scripts/fetch_media.sh`.
- **No NC or ND licenses.** NonCommercial and NoDerivatives terms are not accepted: clips are
  re-encoded, overlaid and cut, which is a derivative work. Allowed: CC0-1.0, CC-BY,
  CC-BY-SA (2.0/3.0/4.0), public domain, Pexels License, Pixabay Content License.
- **No live webcams.** Only recorded clips with a stated license; public live streams are not
  restreamed or recorded.
- Every clip is pinned by SHA-256 so a changed upstream file is detected, not silently used.

## Attribution

| id | role | author | license | source page | retrieved |
|---|---|---|---|---|---|
| gate-santos-01 | gate | Fábio Reis de Abreu | [Pexels License](https://www.pexels.com/license/) | [Pexels: Industrial Container Cranes at Santos Port](https://www.pexels.com/video/industrial-container-cranes-at-santos-port-33585292/) | 2026-10-07 |
| quay-sts-01 | quay | Thanh Văn | [Pexels License](https://www.pexels.com/license/) | [Pexels: Loading Cargo on Truck](https://www.pexels.com/video/loading-cargo-on-truck-10472290/) | 2026-10-07 |
| yard-malta-01 | yard | Frank Vincentz | [CC-BY-SA-3.0](https://creativecommons.org/licenses/by-sa/3.0/) | [Wikimedia Commons: Malta Freeport (Freeport centre) 03](https://commons.wikimedia.org/wiki/File:Malta_-_Birzebbuga_-_Triq_Kalafrana_-_Freeport_(Freeport_centre)_03_(1)_ies.webm) | 2026-10-07 |
| waterway-porpoise-01 | waterway | Extemporalist | [CC0-1.0](https://creativecommons.org/publicdomain/zero/1.0/) | [Wikimedia Commons: LPG Carrier Navigator Centauri transits Porpoise Bay](https://commons.wikimedia.org/wiki/File:22,000_cbm_LPG_Carrier_Navigator_Centauri_transits_Porpoise_Bay.webm) | 2026-10-07 |
<!-- one row per manifest id, e.g.
| gate-01 | gate | Jane Doe | [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/) | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Example.webm) | 2026-10-06 |
-->

License links:
[CC0-1.0](https://creativecommons.org/publicdomain/zero/1.0/) ·
[CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/) ·
[CC-BY-SA-3.0](https://creativecommons.org/licenses/by-sa/3.0/) ·
[CC-BY-SA-4.0](https://creativecommons.org/licenses/by-sa/4.0/) ·
[Pexels License](https://www.pexels.com/license/) ·
[Pixabay Content License](https://pixabay.com/service/license-summary/)
