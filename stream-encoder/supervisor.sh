#!/bin/bash
# TAT stream-encoder supervisor: one process tree, three supervised legs.
#
#   Xvfb :99 (1920x1080x24)  - virtual display, restarted if it dies
#   chromium --kiosk         - renders /stream/; relaunched on exit AND
#                              recycled every BROWSER_RECYCLE_S (leaks,
#                              long-session compositor drift)
#   ffmpeg x11grab -> RTMP   - restarted with backoff on ANY exit
#                              (encoder crash, YouTube ingest reset, DNS
#                              blip); a progress watchdog kills a WEDGED
#                              ffmpeg (socket half-open, zero frames
#                              advancing) so the loop can relaunch it
#
# The container's restart policy (compose: unless-stopped) is the outer
# safety net; this script is the fast inner loop. Everything logs to
# stdout for `docker logs`.
set -u

: "${YT_STREAM_KEY:?YT_STREAM_KEY is required (YouTube Studio -> Live -> stream key)}"
STREAM_PAGE_URL="${STREAM_PAGE_URL:-https://triple-a-tropics.com/stream/}"
RTMP_URL="${RTMP_URL:-rtmp://a.rtmp.youtube.com/live2}"
FPS="${FPS:-30}"
VIDEO_BITRATE_K="${VIDEO_BITRATE_K:-6000}"
BROWSER_RECYCLE_S="${BROWSER_RECYCLE_S:-43200}"   # 12 h
DISPLAY="${DISPLAY:-:99}"
export DISPLAY

log() { echo "[supervisor $(date -u +%H:%M:%S)] $*"; }

# ---------------------------------------------------------------- Xvfb
start_xvfb() {
  Xvfb "$DISPLAY" -screen 0 1920x1080x24 -nolisten tcp &
  XVFB_PID=$!
  for _ in $(seq 1 50); do
    xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 && break
    sleep 0.2
  done
  # root window in the page's own off-canvas color: a browser recycle
  # exposes the root for ~2 s and it must not flash white
  xsetroot -solid "#0c0e11" 2>/dev/null || true
  log "Xvfb up (pid $XVFB_PID)"
}

# ------------------------------------------------------------ chromium
# Kiosk chromium pointed at the live page. The page never blanks by
# design (embedded fallback + last-good state), so the only jobs here
# are: reach it at boot (retry until the site answers) and stay fresh
# (periodic recycle).
start_browser() {
  # --no-sandbox: Docker's default seccomp denies the namespace clones both
  # chromium sandboxes need for a non-root user. The container itself is the
  # isolation boundary here, and the browser only ever loads our own
  # first-party page - do not point this profile at arbitrary URLs.
  chromium \
    --kiosk "$STREAM_PAGE_URL" \
    --no-sandbox \
    --window-size=1920,1080 --window-position=0,0 \
    --no-first-run --no-default-browser-check --disable-infobars \
    --disable-session-crashed-bubble --noerrdialogs \
    --autoplay-policy=no-user-gesture-required \
    --disable-features=TranslateUI \
    --disable-dev-shm-usage \
    --force-device-scale-factor=1 \
    --hide-scrollbars \
    >/dev/null 2>&1 &
  BROWSER_PID=$!
  BROWSER_STARTED=$(date +%s)
  log "chromium up (pid $BROWSER_PID) -> $STREAM_PAGE_URL"
}

browser_leg() {
  # wait for the page to answer before first launch so kiosk never
  # opens onto a chrome error page at instance boot
  until curl -fsS --max-time 10 "$STREAM_PAGE_URL" >/dev/null 2>&1; do
    log "waiting for $STREAM_PAGE_URL to answer"
    sleep 5
  done
  start_browser
  while true; do
    sleep 15
    if ! kill -0 "$BROWSER_PID" 2>/dev/null; then
      log "chromium exited; relaunching"
      start_browser
      continue
    fi
    local age=$(( $(date +%s) - BROWSER_STARTED ))
    if [ "$age" -ge "$BROWSER_RECYCLE_S" ]; then
      log "browser recycle after ${age}s"
      kill "$BROWSER_PID" 2>/dev/null; wait "$BROWSER_PID" 2>/dev/null
      start_browser
    fi
  done
}

# -------------------------------------------------------------- ffmpeg
# YouTube 1080p30 recommended ingest: H.264 high, CBR-ish ~6 Mbps,
# keyframes every 2 s (-g = 2*FPS, no scene-cut keyframes), AAC 128k
# 44.1 kHz. YouTube REQUIRES an audio track: anullsrc provides silence.
ffmpeg_cmd() {
  ffmpeg -hide_banner -loglevel warning \
    -f x11grab -framerate "$FPS" -video_size 1920x1080 -draw_mouse 0 -i "$DISPLAY" \
    -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=44100 \
    -c:v libx264 -preset veryfast -tune zerolatency -pix_fmt yuv420p \
    -b:v "${VIDEO_BITRATE_K}k" -maxrate "${VIDEO_BITRATE_K}k" \
    -bufsize "$((VIDEO_BITRATE_K * 2))k" \
    -g "$((FPS * 2))" -keyint_min "$((FPS * 2))" -sc_threshold 0 \
    -c:a aac -b:a 128k -ar 44100 -ac 2 \
    -progress /tmp/ffmpeg_progress -nostats \
    -f flv "$RTMP_URL/$YT_STREAM_KEY"
}

ffmpeg_leg() {
  local backoff=2
  while true; do
    : > /tmp/ffmpeg_progress
    log "ffmpeg starting (bitrate ${VIDEO_BITRATE_K}k, ${FPS} fps, 2s GOP)"
    ffmpeg_cmd &
    FFMPEG_PID=$!

    # progress watchdog: -progress appends frame= lines ~1/s; if the
    # file stops growing for 60 s the encoder or the RTMP socket is
    # wedged (a plain exit is caught by wait below) - kill so the loop
    # relaunches
    (
      last=0; still=0
      while kill -0 "$FFMPEG_PID" 2>/dev/null; do
        sleep 10
        size=$(stat -c %s /tmp/ffmpeg_progress 2>/dev/null || echo 0)
        if [ "$size" -eq "$last" ]; then
          still=$((still + 10))
          if [ "$still" -ge 60 ]; then
            log "ffmpeg wedged (no progress 60s); killing for relaunch"
            kill -9 "$FFMPEG_PID" 2>/dev/null
            exit 0
          fi
        else
          still=0; last=$size
        fi
      done
    ) &
    WATCHDOG_PID=$!

    wait "$FFMPEG_PID"
    rc=$?
    kill "$WATCHDOG_PID" 2>/dev/null
    log "ffmpeg exited rc=$rc; restarting in ${backoff}s"
    sleep "$backoff"
    backoff=$(( backoff < 30 ? backoff * 2 : 30 ))
    # reset backoff after a long healthy run
    [ -s /tmp/ffmpeg_progress ] && [ "$(stat -c %s /tmp/ffmpeg_progress)" -gt 100000 ] && backoff=2
  done
}

# ------------------------------------------------------------- driver
trap 'log "terminating"; trap - TERM INT; kill 0' TERM INT

start_xvfb
browser_leg &
ffmpeg_leg &

# outer loop: keep Xvfb alive (it should never die; if it does, restart
# the whole tree - the container policy would also catch a full exit)
while true; do
  sleep 30
  if ! kill -0 "$XVFB_PID" 2>/dev/null; then
    log "Xvfb died; exiting for container restart"
    kill 0
    exit 1
  fi
done
