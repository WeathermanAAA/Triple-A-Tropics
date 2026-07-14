# TAT stream encoder: headless Chromium on Xvfb -> ffmpeg -> YouTube RTMP.
# Runs on its OWN instance (NOT the render box, NOT a Codespace) - see
# RUNBOOK-STREAM.md at the repo root. Build context is this directory.
#
# The container renders https://triple-a-tropics.com/stream/ (a fixed
# 1920x1080 broadcast canvas that hydrates itself and never blanks) on a
# virtual X display and pushes an H.264/AAC 1080p30 stream with 2 s
# keyframes - YouTube Live's recommended ingest settings.
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

# chromium + xvfb + ffmpeg + fonts. Noto keeps glyph coverage (the page
# uses the Metropolis webfont, but fallbacks must not tofu). procps for
# the supervisor's pgrep/pkill, curl for the healthcheck + page probe.
# NOTE: chromium runs --no-sandbox (see supervisor.sh) - Docker's default
# seccomp profile denies the namespace clones both chromium sandboxes need
# for a non-root user; the container is the isolation boundary and the
# browser only ever loads the first-party /stream/ page.
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        xvfb \
        x11-utils \
        x11-xserver-utils \
        ffmpeg \
        fonts-liberation \
        fonts-noto-core \
        fonts-noto-color-emoji \
        procps \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 encoder
COPY --chmod=0755 supervisor.sh /opt/encoder/supervisor.sh

USER encoder
WORKDIR /home/encoder

ENV DISPLAY=:99 \
    STREAM_PAGE_URL=https://triple-a-tropics.com/stream/ \
    RTMP_URL=rtmp://a.rtmp.youtube.com/live2 \
    VIDEO_BITRATE_K=6000 \
    FPS=30 \
    BROWSER_RECYCLE_S=43200

# YT_STREAM_KEY comes from the instance .env - never baked into the image.
ENTRYPOINT ["/opt/encoder/supervisor.sh"]
