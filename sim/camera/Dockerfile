FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core iproute2 \
    && rm -rf /var/lib/apt/lists/*

COPY --chmod=0755 publish.sh /usr/local/bin/publish.sh

# Pass the full ffmpeg command as the container command (generated fleet), or set
# CAM_NAME, INPUT, RTSP_URL, MODE (copy|encode), FPS, BITRATE.
# fonts-dejavu-core: font for the drawtext OSD (sim/gen_compose.py OSD_FONT).
# iproute2: `tc` for scripts/chaos.sh netem; it runs in a separate NET_ADMIN helper
# container from this image, the camera containers themselves stay unprivileged.
ENTRYPOINT ["/usr/local/bin/publish.sh"]
