# Incident: box1 under Hostinger's CPU limiter (2026-09-01 04:10 UTC → ongoing)

Read-only triage on 2026-09-02 15:46–17:30 UTC. Nothing on the box was killed, restarted, deployed, or
pulled. Findings were adversarially verified by three independent read-only reviewers plus a
completeness critic; their corrections are folded in below (see "What the verification changed").
box2 was read only for control comparisons and is healthy.

## Summary

box1 (srv1739889, 2.25.183.231) is not running a runaway process, is not stacking overlapping timers, and
has no lane whose steady-state workload grew. Since **2026-09-01 07:20 UTC the guest has been receiving
about 0.4 of its 8 vCPUs**: steal time is 94–96 % on every vCPU, flat for 33+ hours, with no in-guest
cgroup quota, nice, or traffic shaping. The load of 16–49 is the run queue backing up behind that cap;
the task population is unchanged (~1,030–1,070 threads before and during).

The cap engaged in four hourly steps (12 → 45 → 75 → 95 % steal, 04:10–07:20 Sep 1). That is the exact
shape of Hostinger's documented automated limiter ("CPU capacity decreased automatically by 25 % per hour"
when a VPS "sustains high CPU usage for longer period of time"). It followed a **synchronized catch-up
burst** at 00:20–04:00 Sep 1 (60–76 % user + 8–15 % sys ≈ 6–7 cores, 14–55 MiB/s ingress, a global OOM at
01:35) which itself followed a **35-hour host-side ingress throttle** (eth0 rx pinned at exactly
1280 KiB/s ≈ 10 Mbit/s from Aug 30 13:10 to Sep 1 00:15, box1 only, lifting at the calendar-month
boundary). The burst-tripped-the-limiter link is the leading hypothesis, not a measurement: the ramp
began four hours after the burst started, and nothing inside the guest can distinguish a penalty from a
host-side problem that happened afterwards. The ticket to Hostinger should ask, not assert.

Under the cap the box is stacking work: the meso render app is completing requests queued 27.7 hours
earlier (its only client gave up after 45 s), the s1 SQS queues hold ~4,570 slots 22–29 hours old, two
s1 ingest lanes are stranded exited after dockerd failed to recreate their tasks, and the browse emit
lanes publish 3-hour-old frames at 3–4 per hour.

## Timeline (all UTC; sources: sysstat 10-minute samples, docker logs, kernel journal)

| When | What | Evidence |
| --- | --- | --- |
| Aug 24 | Free-running baseline | user 43 %, sys 6 %, steal 0.1 %; eth0 rx 8.6–12.3 MiB/s (0.86 TB/day) |
| Aug 24 20:00–21:00 | Ingress shaped to ~52 Mbit/s | rx 12,300 → 6,265 KiB/s; then pinned 6,100–6,400 (max 6400.4) for 5.5 days; box2 unshaped (6–14.5 MiB/s) |
| Aug 25–29 | "Planned ~3.7-core" era is a network-queued era | user 29–33 %, load 3.3–3.5; rx at the cap every sample means demand exceeded the cap continuously |
| Aug 27 05:40, Aug 30 10:15 | Two global OOM kills before the incident | victim both times: uvicorn in tat-render-render-1 (cyclolab's live origin) |
| Aug 30 13:10 | Ingress shaped to ~10 Mbit/s | rx 6,178 → 1,766 → 1,279.99 KiB/s, flat (1279.6–1280.6) every sample until Sep 1 00:10; box2 unaffected; DNS timeouts 20–72/h (queries queued behind the shaper) |
| Aug 30 13:10 → Sep 1 00:10 | Every consumer starves | emit lanes at 5–10 % of normal throughput; render-1 queue grows to 13.9–21 h; meso-render renders take 1–13 min |
| Sep 1 00:10–00:20 | Shaper lifts at month rollover | rx 1,280 → 55,653 KiB/s (00:20), 55,796 (00:30) |
| Sep 1 00:20–04:00 | Catch-up burst | user 57–76 % + sys 8–15 %, load 5–9; render-1 510–735 renders/h (baseline 23–148) and 84 % of ingress at the lift; meso-render 459/h (333 baseline); s2 lanes 1.2–2× normal for one hour only; s1 lanes cold-start and drain 13-hour-old slots |
| Sep 1 01:35 | Global OOM | victim: 5.2 GB emit child of tat-s2-g19fd-emit-cron-1 |
| Sep 1 04:10 → 07:20 | CPU limiter engages in hourly steps | steal 12–19 % (04:10–05:00), 39–49 % (05:10–06:00), 66–75 % (06:10–07:00), 90 % (07:10), 94.7 % (07:20) |
| Sep 1 07:20 → now | Cap holds | steal 93.9–96.3 % every sample; five more global OOMs on Sep 1 (02:45, 11:37, 13:11, 15:50, 16:19); s1-render memcg OOMs 19–22/day (1–2/day before) |
| Sep 2 15:47 | Triage snapshot | load 34.7, runq 30–54, 74–85 % memory used, 0 % idle at ~0.35 vCPU |

## What it is, and what it is not

- **Not a runaway or stuck process.** No D-state tasks, no single hog; the top consumers are the same emit children, render apps, and pollers that ran before, each now slow because the whole VM has ~5 % of its CPU.
- **Not a timer/cron pile-up.** systemd timers are the heartbeat (60 s), the floater watchdog (10 min), and stock housekeeping; no user crontab; no transient units. The heartbeat's "Consumed 25–29 s CPU" per run is vCPU residency that includes stolen time (this kernel has `CONFIG_PARAVIRT_TIME_ACCOUNTING` unset), so the same caveat applies to every per-cgroup CPU figure: they rank residency, not work.
- **Not a lane whose steady-state workload grew.** The four s2 lanes' backfill windows are bounded (fast2 6 × 5 min, leads 6 × 10, conus 9 × 10, g19fd 6 × 20) and the logs never show more than "9/9" or "6/6" slots missing. The 35-hour hole in the browse products was never refilled and is permanently missing.
- **It is a provider CPU cap** that followed a **synchronized catch-up** after a **provider bandwidth throttle**, and under the cap it has become a **queue pile-up** (work outliving its interval and stacking inside the render apps and SQS).

## Who actually produced the burst

| Consumer | Behaviour at the lift | Bounded? |
| --- | --- | --- |
| tat-render-render-1 (cyclolab render; floater/guidance/intensity pollers) | 13.9–21 h of queued requests drained at 510–735 renders/h with MAX_CONCURRENT_RENDERS=4 (full-disk CMIPF fetch+render ×4); 84 % of ingress at 00:20–00:30 | No: `asyncio.Semaphore` wait has no deadline; a client that timed out after 45–60 s still gets rendered |
| meso-render / meso-render-cold | 459/h vs 333 baseline; same unbounded queue | No; also no memory limit (5.5 GB and 3.6 GB now) |
| s1 ingest lanes (goes18/goes19/himawari9) | cold start lists 5.8k R2 keys each, then renders every SQS slot inside `S1_RETAIN_H` (default 72 h) | Only by the 72 h window |
| s2 emit lanes ×4 | one bounded backfill pass at 1.2–2× normal for ~1 h | Yes (window), but no cross-lane concurrency guard |
| tat-overlays uhr-poller | the box's largest lifetime ingester (5.58 TB) and ~half of today's ingress | No governor |

## Under the cap right now

- meso-render "render ok … ms=" has grown in a straight line from 2.6 M ms (Sep 1 07:00) to **99.6 M ms = 27.7 h** (Sep 2 15:00); it is fetching Sep 1 12Z mesoscale files at Sep 2 15:28. meso-render-cold is 18 h behind. meso-poller times out every request at 45 s and reports "circuit OPEN" every minute; the live meso loops have been empty for ~34 h. Roughly 2,500–2,800 zombie requests sit in the hot queue and will be rendered, output discarded, the moment the cap lifts.
- SQS: tat-sat-goes18-cmip 1,762 (+72 DLQ), tat-sat-goes19-cmip 1,990 (+13), tat-sat-himawari9-fldk 814 (+1). The goes19 and himawari9 lanes are exited (dockerd "failed to create task … AlreadyExists" 26 s before a global OOM; "unable to start unit … dbus disconnected") and the restart manager does not retry; with no consumer those slots will not DLQ, and at 72 h retention they will all be rendered when the lanes start.
- tat-s1-s1-ingest-goes18-1: 347 restarts; each cycle lists 4.4–4.5k R2 keys, renders 3–6 slots from Sep 1 (22–29 h old, 60–140 s each), then its watchdog exits after 600 s without progress.
- tat-s2-g19fd and tat-s2-conus: emitting stamps from 12:40Z–14:00Z at ~20 min per frame; peak RSS 6.5 GB and 4.7 GB per emit. tat-s2-g19fd-leads: goes19/fd/ir is 2.9 h old against sat-health's 3 h threshold, so the live GOES-19 "paused" chrome trips regardless of anything below.
- render-1 exited with 137 four times (Aug 27, Aug 30, Sep 1 ×2, each within a second of a global OOM) and with 139 twice (Sep 2 01:54 and 02:00). The chronic problem is the memory ceiling: coincident cgroup peaks sum to ~40 GB on a 32 GB box (g19fd 6.5, leads 6.8, conus 4.7, fast2 4.1, meso 5.5, cold 4.6, render-1 7.7), and the OOM killer's preferred victim is cyclolab's live origin.
- tat-render-floater-poller-1: 52 self-restarts (exit 86) in 5 days, plus the host floater watchdog restarts it every ~40 min (its own log: manifest 2,258–2,317 s stale) because a sweep takes >30 min under the cap.
- Ingress has collapsed to 0.17 TB/day; September's bandwidth total is being suppressed by the CPU cap and will resume at 0.55–0.9 TB/day when it lifts.

## Hostinger facts that change the plan

- Hostinger documents that the limiter **lifts automatically "once CPU usage visibly drops to normal levels"**, and that the account holder can **remove the CPU limit once per week** from the VPS dashboard. A ticket is the fallback, not the unblocker. Demand reduction on the box is the precondition for either path.
- Their guest agent runs `usage-telemetry.py` and `ps --sort=-%cpu` inside this VM every hour (journal, since at least Aug 26); whatever `ps` shows at the top of each hour is what their detector sees.
- The bandwidth tiers do not match the published 32 TB/month: our counters read ~20 TB when the 52 Mbit/s shaper engaged (day 24) and ~22–25 TB when the 10 Mbit/s shaper engaged (day 30), while box2 ingested ~24–25 TB in August and was never shaped. Either the meter counts differently or the tiers are undocumented; ask.
- This account tripped the CPU flag before (AGENT_STATUS 2026-07-25: "Hostinger kept flagging box1 as pegged").

## Proposed fix (nothing executed; needs a go)

### Step 1 — shed dead work now (reversible; reads nothing under /root/tat-satellite-render)

Execute one at a time, verify `docker ps` after each (task creation fails ~0.5 % of the time on this starved box and the restart manager does not retry):

1. `docker restart tat-satellite-render-meso-render-1` → verify Up. Drops the 27.7 h zombie queue (~2,500–2,800 requests). meso-poller is its only client (loopback binds, zero established connections, caddy fronts only render:8080) and already has its circuit open. The 10 s stop grace will be exceeded and the app SIGKILLed; it is stateless.
2. `docker restart tat-satellite-render-meso-render-cold-1` → verify Up.
3. `docker stop tat-s2-g19fd-emit-cron-1` and `docker stop tat-s2-conus-emit-cron-1` (plain `docker stop` reads no compose file). Freezes 35 browse products at ~14:00Z instead of a 3–5 h trickle. Mid-emit SIGKILL is safe: tiles → `_ready.json` → `latest_times.json` ordering, and the orphan-reconcile path heals a stamp whose tiles landed without a manifest entry.
4. `docker stop tat-s1-s1-ingest-goes18-1`: pure churn (347 restarts, 4.4k-key R2 listing per restart, renders of 22–29 h-old shadow slots nobody views).
5. Leave render-1/caddy (cyclolab), radar, s1-render, meso-poller, and every poller untouched. Leave the two exited s1 lanes down.
6. Judge success by **steal % in `sar -u 1 3` falling** and by goes19/fd/ir `latest_times.json` advancing, not by load. If steal has not started to fall within 1–2 h, pause tat-s2-conus-fast2 as well so g19fd-leads is the only emitter (box2's conus-fast lane already carries goes19/conus/ir). That is a product-priority decision made under an incident, named as such.

Re-arm hazards: all four stopped/exited containers have `restart: always` and will start on any dockerd restart or reboot (systemd re-executed itself at 06:35 Sep 1 during an unattended upgrade), and `scripts/fleet.sh deploy box1` re-ups every assigned lane. Memory relief is ~9 GB now (the two meso apps) and is temporary; the restarted uvicorns regrow.

### Step 2 — code, in tat-satellite-render, reviewed and tested before any box deploy

Ordered by what actually produced the burst:

1. **Render apps: bound the queue.** Wrap the `render_semaphore` acquisition in `asyncio.wait_for(…)` and answer 503 past a deadline (the pollers already treat non-2xx as retry-then-circuit). This kills the zombie-queue class of failure; it does not bound in-flight time (the S3 fetch sits inside the semaphore), so it will not restore meso output while a cap holds. Add `mem_limit` to meso-render and meso-render-cold. render-1's copy is reachable from /root/tsr-s2; the meso copies live in the tat-satellite-render tree and need an owner decision (below).
2. **s1 ingest: shrink the never-miss window.** The stale-slot guard already exists (`s1_ingest.py` lines ~508–517, `S1_RETAIN_H`, default 72 h); set it to the real window and recreate the s1 containers from /root/tat-sat-s1 before the exited lanes are started.
3. **s2 lanes: overlap guards, not priority tweaks.** (a) a host lock directory bind-mounted into every lane and each `python s2_pyramid_emit.py` wrapped in `flock -w` over N slot files (N = 2 on box1), with **one slot reserved for the leads lanes** so an unordered semaphore cannot demote them; (b) `--max-rebuild-per-pass K` in `s2_pyramid_emit.py` (backfill is newest-first; fix the `--step` help text that says oldest-first); (c) a gate that reads **steal and idle from /proc/stat, never /proc/loadavg** (loadavg is not namespaced, cannot see steal, and would halt the leads lanes for as long as any cap holds); (d) optionally all emit lanes under one systemd slice with `CPUQuota` via `cgroup_parent` (cgroup v2 + systemd driver confirmed) as a post-cap guard, noting it moves the cgroup paths tooling reads. (a), (c), (d) require lane recreation, i.e. a deploy; never batch recreates while steal is above ~50 %.
4. **Heartbeat: publish steal % and eth0 rx** so /fleet/ distinguishes "provider throttled" from "busy". Both throttles sat unnoticed (35 h + 33 h) because the heartbeat reads load only.
5. **Month-end ingest brake:** when the heartbeat sees rx pinned at a shaper value for more than three samples, shrink backfill windows and stop render-1 accepting new work. Smaller than the full governor and it prevents the queue that produced the burst.

### Step 3 — when the cap lifts (or before the weekly removal button is used)

Concrete sequence now that the code is on tsr main @72cb9d9 (run from /root/tsr-s2 on box1; wait for steal below ~50 % in `sar -u 1 3` first). Steps 1–2 are staged as `/root/tat-step3-deploy.sh` on box1 (not run): it builds tat-s2, recreates ONLY the two leads lanes governed, leaves the browse lanes stopped, rebuilds tat-render and recreates only `render`, and asserts exactly one container per service. Run it as `systemd-run --unit tat-step3 /root/tat-step3-deploy.sh` and read `journalctl -u tat-step3 -o cat`. At 22:26–22:29 UTC steal briefly stepped to 70–82 % and returned to 91–93 %: the limiter is moving but not settled.

1. `scripts/fleet.sh deploy box1` (from a tsr checkout) rebuilds tat-s2 and recreates ALL FOUR lanes governed (the bind mount changed the compose hash; the stopped browse lanes come back too). Immediately `docker stop tat-s2-g19fd-emit-cron-1 tat-s2-conus-emit-cron-1` if you want them back one at a time; with the governor they are bounded to one browse emit at a time and 2–3 rebuilt slots per product per pass either way.
2. `docker compose -p tat-render -f docker-compose.render.yml -f docker-compose.render.override.yml build render && docker compose -p tat-render -f docker-compose.render.yml -f docker-compose.render.override.yml up -d --no-deps --no-build render` for the queue deadline on cyclolab's render (Python 3.11 image; MAX_CONCURRENT_RENDERS stays 4). Verify exactly one render container afterwards.
3. The meso-render / meso-render-cold copies of the same app and their `mem_limit` need the tat-satellite-render tree's owner (docker-compose.meso.yml on main now sets RENDER_QUEUE_WAIT_S=15 for both).
4. s1: after Andrew's backlog decision, set `S1_RETAIN_H` in /root/tat-sat-s1 and recreate the three ingest lanes plus s1-render (docker-compose.s1.yml on main sets RENDER_QUEUE_WAIT_S=25).
5. Watch `/fleet/` steal % and goes19/fd/ir latest_times for the first two hours.

With the browse lanes still stopped: deploy Step 2 items 1–3, set `S1_RETAIN_H`, then bring tat-s2-g19fd and tat-s2-conus back one at a time, start the two exited s1 lanes, and watch steal for the first two hours. Without Step 1 the at-lift demand is ~6–8 cores for the first hour (meso zombies 1–2 cores for 3–6 h, s1 goes18 backlog ~1 core for 2–3 h, four lanes at 1.2–2× for an hour, render-1, pollers, uhr) plus coincident memory peaks of ~40 GB: the same profile as Sep 1 00:20–04:00. With Step 1 done it is ~3–4 cores for about an hour, inside the Aug 25–29 envelope that drew no flag.

### Sequencing

"Deploy the governor before asking Hostinger to lift the cap" does not hold: the cap can lift on its own, or Andrew can click the weekly button, at any time. Correct order: Step 1 now → observe steal 1–2 h → land Step 2 code in tsr with review and tests (no box deploy) → at the lift, deploy with browse lanes still stopped → return lanes one at a time.

### Recurrence

Free-running demand is ~0.86 TB/day on box1 and ~0.85 TB/day on box2 (both ~26 TB per 31 days). Against the empirical trip points (~20 TB, then ~22–25 TB) box1 reaches the first shaper around day 23–24 and the second around day 29–30 of every month, then refills the hole at rollover and re-trips the CPU limiter. Moving the browse lanes to box2 (+0.4 TB/day) only converts one shaped box into two. The levers that change the total: deduplicate ingest across consumers (g19fd, g19fd-leads, render-1, meso, s1, uhr each pull their own copy of the same NOAA objects), drop products (uhr-poller is ~half of current box1 ingress), a third box, or a plan whose limits Hostinger states in writing.

## Andrew (only the account holder can do these)

1. hPanel for srv1739889: read the CPU-limit notice, Server Usage, and the bandwidth meter (August total, September to date, and the readings at Aug 24 ~20:xx and Aug 30 13:09 UTC if history is kept). Same for box2 so the meters can be compared. Screenshot.
2. Do **not** use the once-per-week "remove CPU limit" before Step 1 has run and the meso queues are gone; ideally after Step 2 items 1–2 are deployed. Using it now burns the removal on a full-speed catch-up that re-enters the 25 %/hour limiter.
3. Open the ticket (points below).
4. Decide the fate of the s1 shadow backlog: purge the three queues, or approve lowering `S1_RETAIN_H`, before the exited lanes start. It is a data-loss decision on a shadow product.
5. Own the /root/tat-satellite-render tree question: meso-render/meso-render-cold need `mem_limit` and the queue deadline, both edits plus `compose up` in the tree this workstream must not touch. Release it (land what is dirty) or assign the change to its owner. Until then the zombie-queue mechanism re-arms after every slowdown.
6. Make the product-priority ranking explicit for throttle conditions (what is protected: goes19/fd/ir leads, conus fast bands; what sheds first: browse bands, shadow s1, meso under the current pipeline). The governor's slot reservation needs it.
7. Ingest strategy for September (dedupe, drop products, third box, or a plan with written limits). Moving lanes alone spreads the throttle.

## Ticket to Hostinger (ask, do not assert)

- VPS srv1739889 (2.25.183.231, KVM 8). Reference the July "CPU pegged" warnings on this account.
- CPU: steal rose in hourly steps from 04:10 UTC Sep 1 (12–19 %, 39–49 %, 66–75 %, 90 %) and has been flat at 94–95 % on all 8 vCPUs since 07:20 UTC Sep 1 (95.8 % at 17:13 UTC Sep 2). Is a CPU limiter applied, since when, on which metric/threshold/window? When does it auto-lift and what usage do they need to see? Can it be lifted now? If no limiter is applied, then 95 % steal is host contention on their side: was the VM migrated or is the host oversubscribed?
- Network: ingress pinned at exactly 6400 KiB/s from Aug 24 between 20:00 and 21:00 UTC, and at exactly 1280 KiB/s from Aug 30 13:09 UTC until Sep 1 between 00:10 and 00:20 UTC. Our counters read roughly 20 TB and 22–25 TB at those moments against the published 32 TB. What did their meter read, what are the tiers, is it inbound+outbound, is it a UTC calendar month, and why was the second VPS (72.62.97.220, ~24–25 TB in August) never shaped?
- Cause statement as our leading hypothesis with timestamps: the shaper queued ~35 h of scheduled work; at the month boundary the server caught up at ~6–7 cores for ~3.5 h (00:20–04:00 UTC Sep 1); the limiter engaged four hours later. We are reducing demand and deploying a rate governor; ask them for the usage envelope (cores, duration) that stays under their detector so it can be sized.
- Does the weekly self-service removal apply to this restriction, and does using it while usage is still high result in a stricter or longer penalty? What criteria does their hourly guest-agent telemetry apply?

## Execution log

Andrew's go (2026-09-02 ~19:10 UTC): Step 1 exactly as written, one action at a time with docker ps checks; hold the weekly hPanel removal until steal is falling on its own; pausing conus-fast2 approved if steal has not moved in 2 h; Step 2 through normal review; the heartbeat must gain steal % and eth0 rx.

| UTC | Action | Result |
| --- | --- | --- |
| 19:14 | Baseline | steal 95.8 %, load 24.2, mem used 23.0 GB; goes19/fd/ir latest 14:30:20Z |
| 19:14–19:18 | `docker restart` meso-render | Docker's kill call timed out on the starving box ("PID is zombie and can not be killed") but the SIGKILL landed: exited 137 at 19:15:39. `docker start` at 19:18:15 → Up, new PID 1528768, healthy by 19:25 |
| 19:19–19:21 | `docker restart` meso-render-cold | Same failure shape; exited, `docker start` at 19:21:08 → Up, healthy by 19:25 |
| 19:22 | `docker stop` tat-s2-g19fd-emit-cron-1 | Exited (137) at 19:23; leads lane untouched |
| 19:23 | `docker stop` tat-s2-conus-emit-cron-1 | Exited (137) at 19:24; conus-fast2 untouched |
| 19:25 | `docker stop` tat-s1-s1-ingest-goes18-1 | Exited (137) at 19:25 |
| 19:26 | First reading | steal 90.8 %, idle 4.3 %, load 21.7, mem used 12.8 GB (available 19.3 GB) |
| 19:28 | Watch tick | steal 94.9 %, load 15.5, mem used 13.8 GB |
| 19:27 onward | meso loops recover | meso-poller uploads frames again (11 in the first 10 min, renders 24–62 s) after ~34 h of nothing; meso-render render times fall from 141 s to 22–45 s, no queue re-forming |
| 19:39 | Watch tick | steal 94.6 %, load 12.1 |
| 19:50–20:02 | 1 h reading | 10-min steal 94.7 (19:20), 93.2, 91.3, 90.5 (19:50), 92.9 (20:00): a drift, not a step; load 13–19; mem used 16.1 GB; meso 43 uploads / 13 timeouts in 30 min; goes19/fd/ir on the CDN still 14:30:20Z (the leads lane cannot catch up on ~0.4 vCPU; box2's goes19/conus products are current). Decision on conus-fast2 stays at the 2 h mark, 21:25 UTC |
| 20:11 | Watch tick | steal 88.7 %, idle 7.0 %, load 5.7, mem used 13.9 GB: steal drifting down for the first time |
| 20:0x–20:2x | Step 2 items 1–3 landed on tsr main @72cb9d9 (not deployed) | render queue deadline (503 past RENDER_QUEUE_WAIT_S, admission-time disconnect probe, per-service budgets: meso 15 s, s1 25 s, render 40 s) and the s2 governor (slots with S2_SLOTS_RESERVED leads reservation, steal gate for browse, per-pass rebuild cap), both adversarially reviewed with fixes applied; box1 .env now carries S2_SLOTS=3 S2_SLOTS_RESERVED=2 S2_GATE_STEAL_MAX=50 (inert until lanes are recreated); both boxes' checkouts at 72cb9d9; no container touched |
| 20:21–21:24 | Watch ticks | steal 95.1, 94.9, 93.9, 93.1, 92.9, 93.2, 94.95 %: flat in the 93–95 band for the full 2 h after Step 1; load 6–16; the 20:11 dip to 88.7 % was noise |
| 21:25–21:26 | 2 h escalation (approved): `docker stop` tat-s2-conus-fast2-emit-cron-1 | Exited (137); g19fd-leads is the only emitter left on box1 (box2's conus-fast carries goes19/conus/ir, truecolor, irbd, c02); a second 2 h steal watch runs to ~23:30 UTC |
| 21:32 | Post-pause reading | steal 92.9 %, load 15.8, mem used 14.8 GB; meso 45 uploads / 30 min; g19fd-leads emits one full-disk frame per ~15 min (airmass 19:40Z–20:10Z written 20:38–21:26), so a five-product sweep takes hours and goes19/fd/ir on the CDN stays at 14:30:20Z until its turn; box2 steal 0.0 %, load 2.4 |
| 21:37–22:29 | Watch after the pause | steal 94.4, 93.6, 94.2, 94.4, 94.2, 93.2 %: unchanged one hour after conus-fast2 stopped; idle 0.5–2.8 %, i.e. the guest still consumes 100 % of its ~0.4 vCPU allotment with only cyclolab, radar, s1-render, the meso stack, the overlay pollers and g19fd-leads running. The approved shedding is exhausted; Andrew notified 22:30 UTC. Remaining levers are the hPanel weekly removal and the ticket |
| 21:34–22:58 | Watch ticks | steal 94.4, 94.7, 94.2, 95.6, 94.8, (22:26 dip to 81.7 / 16 s avg 69.7, back to 91.6 by 22:29), 93.8, 92.4, 90.8 % |
| 23:09 | Transient dip, not a release | one 5 s sample read steal 74.2 % / idle 22.1 %; a second sample one minute later read 94.5 % / idle 0.4 %. Like the 22:26 dip, the limiter wobbles but has not stepped; deploy trigger stays two consecutive 10-min ticks under 50 % (script staged as /root/tat-step3-deploy.sh) |
| 22:40–23:22 | Watch end | steal 93.1, 91.8, 93.7, 91.9, 93.7 %: flat for 4 h after Step 1 and 2 h after the conus-fast2 pause. Cap not lifted by demand reduction alone. A quiet watch now fires only if steal falls below 50 %, at which point the queued Step 2 deploy runs (governor lanes, then cyclolab's render with the queue deadline), browse lanes returned one at a time |
| 23:20–23:41 | Watch ticks | steal 94.0, 92.6, 91.9 %: no self-lift 4 h 15 min after Step 1 and 2 h 15 min after the conus-fast2 pause, with box1 demand at its floor (leads lane, cyclolab render, radar, s1-render, meso stack, pollers) |
| 23:42 | Andrew notified (push) | the remaining levers are his: hPanel weekly CPU-limit removal (now defensible: demand is shed, the zombie queues are gone, the governor and deadline are on main with the deploy script staged) or the ticket; no further shedding is approved |
| 19:41–19:44 | Step 2 item 4 shipped | heartbeat publishes steal % / busy % / eth0 KiB/s (tsr 4b4eb83, reviewed, unit-tested); installed on both boxes; box1 reports steal 95.8 %, run 13 s (was 65 s); box2 steal 0.0 %; /fleet/ renders the new tiles (TAT 415524b4) |

Untouched throughout: tat-render-render-1 (cyclolab) and caddy, tat-radar-render, tat-s1-s1-render-1, meso-poller, every tat-overlays and tat-render poller, the two already-exited s1 lanes. A 10-minute steal/load watch runs until ~21:25 UTC; the 2 h decision point for conus-fast2 is 21:25 UTC. goes19/fd/ir on the CDN was still 14:30:20Z at 19:38 UTC (the leads lane cannot catch up on ~0.4 vCPU); box2's goes19/conus/ir is current.

Lesson from the restarts: on a box this starved, `docker restart` reliably fails its kill step with "PID is zombie" while the kill itself succeeds; the container reaches exited ~20 s later and needs an explicit `docker start`. Sequence: restart → wait for exited → start → verify.

## What the verification changed

Three independent read-only reviewers (virtualization, network/timeline, operations safety) and a completeness critic were given the raw evidence and the draft, told to refute it, and allowed to re-check the box. Verdicts: the cap mechanism stands (per-vCPU steal uniform to 1 %, all cgroups unlimited, a fixed ~5 % guest share to ±0.8 % for 33 h, box2 at 0.00 %); the bandwidth-throttle trigger stands; the draft was corrected on: (1) the cap can auto-lift and has a self-service weekly removal, so demand reduction is the unblocker; (2) the burst was mostly render-1's unbounded queue and the s1 backlog, not the s2 lanes, which reorders Step 2; (3) the memory ceiling is a second pre-existing constraint (eight global OOMs, two before the incident; render-1's "SIGSEGV exits" were mostly OOM kills); (4) per-cgroup CPU numbers include stolen time on this kernel; (5) a load-average gate would halt the leads lanes and livelock, so the gate must read steal/idle; (6) an unordered flock semaphore is a priority change in disguise unless leads get a reserved slot; (7) the 32 TB premise does not fit our counters or box2; (8) the s1 stale-slot drop already exists as a knob; (9) uhr-poller is missing from every ingest budget; (10) the "planned ~3.7-core" baseline was itself a network-shaped period.
