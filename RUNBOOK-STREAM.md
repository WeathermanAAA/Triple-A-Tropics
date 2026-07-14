# RUNBOOK-STREAM — 24/7 broadcast channel

The pipeline has two halves:

1. **The page** — `https://triple-a-tropics.com/stream/` (source
   `stream/index.html`). A fixed 1920×1080 broadcast canvas: TAT chrome,
   UTC clock, ticker, active-systems rail, names-board carousel, global
   overview + Cat-1+ storm-focus modes with a full-frame stinger. It
   hydrates itself from the live CDN feeds every 5 minutes and renders
   from an embedded fallback snapshot when offline — it **never blanks**,
   so the encoder needs no page-level babysitting. Verified headless via
   `node tests/stream_smoke.cjs` (19 checks).
2. **The encoder** — `stream-encoder/` (this runbook). Headless Chromium
   renders the page on a virtual X display; ffmpeg captures it and pushes
   H.264/AAC 1080p30 with 2-second keyframes to YouTube's RTMP ingest.
   **Built and reviewed, NOT yet deployed — it needs its own instance and
   the YouTube stream key (both Andrew's hands).**

## 1. What Andrew provisions (one time, ~20 min)

1. **An instance of its own.** NOT the S2/render box (the encoder wants
   ~2 dedicated cores for x264 veryfast 1080p30 and must not compete
   with renders), NOT a Codespace (they sleep). Any $10–20/mo VPS with
   2–4 vCPU / 4 GB / Docker installed works. No inbound ports needed —
   the encoder only dials out (HTTPS 443 to the site, RTMP 1935 to
   YouTube). IPv4 egress required.
2. **The YouTube stream key.** YouTube Studio → Create → Go live →
   Streaming software. Copy the **Stream key** (keep default ingest
   `rtmp://a.rtmp.youtube.com/live2`). While there: set the stream to
   1080p, mark it "Not made for kids", and enable the 24/7 "persistent
   stream key" behavior by just reusing this key — YouTube keeps the
   channel live as long as ingest continues.
3. On the instance:

   ```bash
   git clone --depth 1 https://github.com/WeathermanAAA/Triple-A-Tropics.git
   cd Triple-A-Tropics/stream-encoder
   printf 'YT_STREAM_KEY=%s\n' '<the stream key>' > .env
   docker compose -p tat-stream -f docker-compose.stream.yml --profile stream up -d --build
   ```

4. Verify (§3), then bookmark the YouTube Live dashboard — it shows
   ingest health (bitrate, keyframe cadence) straight from YouTube's
   side.

## 2. What the container runs

One supervisor (`supervisor.sh`, all legs logged to `docker logs`):

- **Xvfb `:99`** at 1920×1080×24, root window pre-painted `#0c0e11` so a
  browser recycle never flashes white on stream.
- **Chromium kiosk** at `STREAM_PAGE_URL`. Waits for the page to answer
  before first launch (no error-page frames at boot), relaunches if it
  exits, and is **recycled every `BROWSER_RECYCLE_S`** (default 12 h)
  against renderer leaks and compositor drift.
- **ffmpeg** `x11grab → libx264 veryfast → flv → RTMP`:
  `-b:v 6000k -maxrate 6000k -bufsize 12000k -g 60 -keyint_min 60
  -sc_threshold 0` (exact 2 s GOP at 30 fps — YouTube's ask) plus
  `anullsrc` AAC 128k stereo silence (YouTube requires an audio track).
  Restarted with capped exponential backoff on any exit; a **progress
  watchdog** kills a wedged encoder (RTMP socket half-open, zero frames
  advancing for 60 s) so the restart loop can take it from there.
- **Container `restart: unless-stopped`** is the outer net; the compose
  healthcheck fails if either the browser or the encoder leg is down.

## 3. Verification after bring-up

```bash
docker logs -f tat-stream-encoder-1 | head -50   # xvfb up, chromium up, ffmpeg starting
docker inspect --format '{{.State.Health.Status}}' tat-stream-encoder-1   # healthy
```

Then YouTube Live dashboard → "Stream health" should read **Excellent /
6.0 Mbps steady** within ~30 s, and the public watch page shows the
canvas with the clock ticking. Leave it 15 min and confirm no
"keyframe interval" warnings (2 s GOP is what it wants).

## 4. Operations

- **Rotate the stream key**: update `.env`, `docker compose ... up -d`
  (recreates the container; ~10 s of ingest gap, YouTube rides it out).
- **Page changes deploy themselves** — the encoder just shows whatever
  `/stream/` serves; the page self-hydrates data every 5 min. A layout
  change lands on stream at the next browser recycle, or immediately via
  `docker exec tat-stream-encoder-1 pkill chromium` (the supervisor
  relaunches it in ~3 s).
- **Bitrate/fps tuning**: `VIDEO_BITRATE_K` (4500–9000 sensible for
  1080p30) and `FPS` in `.env`, then `up -d`.
- **Instance sizing check**: `docker stats` — x264 veryfast 1080p30
  should sit ~120–180% of one core. Sustained >90% of ALL cores means
  the instance is undersized; drop `VIDEO_BITRATE_K` or move up a tier.
- **Kill switch**: `docker compose -p tat-stream -f docker-compose.stream.yml --profile stream down`.

## 5. Known limits / future

- Silence on the audio bed (license-clean). A weather-radio-style TTS or
  licensed music bed would be a separate, deliberate addition.
- The stinger/focus logic is page-side; the encoder is dumb by design.
- If YouTube ingest is regionally down, the loop keeps retrying with
  backoff — nothing to do; it recovers when ingest does.
- DVR/archive settings live in YouTube Studio, not here.
