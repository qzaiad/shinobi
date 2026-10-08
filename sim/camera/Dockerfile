FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --chmod=0755 publish.sh /usr/local/bin/publish.sh

# Pass the full ffmpeg command as the container command (generated fleet), or set
# CAM_NAME, INPUT, RTSP_URL, MODE (copy|encode), FPS, BITRATE.
ENTRYPOINT ["/usr/local/bin/publish.sh"]
