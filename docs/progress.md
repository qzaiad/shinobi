# Progress

One line per session. See [learning-plan.md](learning-plan.md) for session details.

| Date | Session | Commit | Notes |
|---|---|---|---|
| 2026-10-03 | 1 · Repo bootstrap | f1e2269 | Ubuntu 24.04.5. Docker 29.8.1, Compose v5.5.1, ffmpeg/ffprobe 6.1.1, git 2.43.0. Python 3.14.6 via uv (`.python-version`, `.venv`); pyenv global 3.8.16 is bypassed. |
| 2026-10-04 | 2 · Video fundamentals (part) | d0f6657 | tools/streamprobe: ffprobe GOP/B-frame health check + pytest (unit + ffmpeg integration). S2 done. Open question "absolute timestamps: ingest-time stamping vs RTCP SR"|
| 2026-10-07 | 3 · Source real port footage | — | `media/manifest.tsv` (4 clips, sha256-pinned) + `SOURCES.md`; `scripts/fetch_media.sh` (curl, `.part` + atomic mv, `--pin/--only/--probe`); manifest tests. Clips: gate Santos (H.264 1080p59.94, GOP 4.17 s), quay STS (H.264 1080p25, GOP 3.04 s), yard Malta (VP8 1080p25, GOP 5.12 s), waterway Porpoise Bay (VP8 720p23.976, GOP 5.34 s); all need re-encode in S4. Choice + rejects: [notes/03-port-footage.md](notes/03-port-footage.md) |
