# Incident: floater stall on the satellite page (2026-09-21), diagnosed blind

Status at 23:30 UTC 2026-09-21: cause established from outside the box; the box itself is
unreachable from the Codespace (port 22/443 to both box IPs filtered here; github.com:22
open, so it is IP-specific). Nothing on the box, in R2, or in .env was touched. No deploy.

## Symptom
Live Storm Floater last frame 05:30Z, GOES Mesoscale Sector last frame 11:47Z, both panels
showing "Imagery paused". Tropical Storm Fay (AL06) active.

## What is actually wrong
- **box1 is still under Hostinger's CPU limiter that engaged on 2026-09-01** (see
  INCIDENT-2026-09-02-box1-cpu-cap.md). Heartbeat at 22:15Z: steal 86.4 %; at 23:26Z: 94.7 %,
  idle 0.0 %. The Sep 2 remediation stalled at "deploy when steal < 50 %", which never came.
- **The floater has been effectively dead since Sep 1, not since 05:30Z.** The freshness
  monitor's "floater frame discriminator" row has been stale continuously (475 h as of
  05:07Z today). Newest floater frame age per sampled monitor run: Sep 6 41 h; Sep 9 92 min;
  Sep 13 20 h; Sep 15 20 h; Sep 17 "no frame readable"; Sep 19 38 days (August leftovers);
  Sep 21 05:07 25 days; Sep 21 10:47 5 h. Dujuan (WP24) has been active since Sep 13 and never
  received a floater frame (no manifest at all).
- Today 05:30-05:40Z three storms (al06, ep16, ep17) each got one IR + one IRBD frame, the
  first in weeks; per-storm manifests hold that single frame because the 24 h prune had
  removed everything older. Then the shared render service stopped answering entirely:
  floaters/backdrops.json (rendered through the same service every 30 min) last 05:34Z.
- **render.triple-a-tropics.com refuses TCP 443** (ECONNREFUSED from a second network at
  22:1xZ and 23:2xZ; the host resolves straight to box1, no Cloudflare proxy). Caddy is down,
  so draw-a-box on /satellite/, the explorer's live renders (cockpit.js, objfix_sources.js)
  and microwave.js are down too. What happened at ~05:45Z needs the box logs.
- The floater poller's loop is alive: floaters/manifest.json re-stamped 21:31Z, 22:12Z,
  22:52Z with the correct storm list.

## The storm-position source is fine
feeds/al_tracks_data.json regenerated 22:06Z with Fay's 18:00Z fix (18 six-hourly points);
NHC CurrentStorms 21:00Z; the poller's top manifest carries lat 33.3 / lon -32.1 /
last_fix 18:00Z. Position did not go quiet; it is not the split.

## Why the mesoscale looked like a working control
It is not healthy: goes19-m1 IR has 4 frames in 24 h (latest 11:47Z), goes18-m1 5 (12:07Z),
against ~1,400/day nominal; its top manifest is re-stamped every minute by the discover loop,
which is why its "generated" time looks current. It fails less often because it has TWO
dedicated render containers (tat-satellite-render meso-render / meso-render-cold, restarted
Sep 2 with empty queues), a 45 s client timeout that fails fast, and small CMIPM files. The
floater shares ONE render container (tat-render `render`, MAX_CONCURRENT_RENDERS=4, no queue
deadline deployed) with cyclolab's guidance pollers and public traffic, crops 12 deg boxes out
of full-disk CMIPF files, and waits 300 s per attempt (RENDER_TIMEOUT_S override). On ~1 vCPU
its renders never finish and the queue fills with requests whose clients already gave up (the
Sep 2 diagnosis; fix on tsr main @72cb9d9, never deployed).

## Banner (question 4)
Neither sat-health.js variant is showing. Its probe reads goes19 fd/ir + conus/ir
latest_times.json (the explorer tile feeds), keys on the freshest, and box2's conus/ir is
minutes old, so state.stale is false and notice() is null (verified under jsdom against the
live manifests). The "Imagery paused · last frame ..." text is the per-panel note in
satellite/index.html (satUpdateInactive), generic wording. The NOAA-attribution variant is
gated to the hard-coded 2026-07-15..18 window and cannot fire now. No user is being told
NOAA broke. Design note: the site banner cannot see a producer stall at all; the per-panel
notes carry that.

## Fix
1. Immediate, box-independent (needs the go): re-arm the runbook's emergency lever
   `.github/workflows/floater-worker.yml` on its :07/:37 schedule with the intensity +
   guidance riders OFF (box1's copies are healthy; two writers on global_storms.geojson is
   the home-map flap trap). It boots its own /render on the runner. Proposed diff prepared.
2. On the box once reachable: run the read-only diagnostic (render-1 / caddy /
   floater-poller logs around 05:45Z, dockerd journal, OOMs); then, with cause known, bring
   caddy + render back; deploy the reviewed render queue deadline (tsr 72cb9d9; script staged
   at /root/tat-step3-deploy.sh); give the floater its own render container or move the
   floater stack to box2 (0 % steal).
3. Andrew: the CPU limiter itself (hPanel weekly removal / ticket). Everything on box1 is
   degraded while it stands; the s2 fd/ir feed from box1 is 5 h behind too.

## Recurrence
Not a one-off. It recurs for as long as box1 is capped and the floater rides an unbounded
shared render queue; the freshness monitor has emailed red runs about it since Sep 6.
