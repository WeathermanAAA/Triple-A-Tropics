# Cost audit and lockdown — main site / box infrastructure (2026-08-25)

Policy (Andrew, binding): no metered billing where flat rate is achievable; anything that
must stay metered gets a hard ceiling. This document is the measured Phase 1 report and the
Phase 2 decision it supports. Phase 0 (the circuit breaker) is tracked in its own section.
Every number here was MEASURED on 2026-08-25 (sources named inline), not inferred from a bill.

Sources: Cloudflare GraphQL Analytics (`r2OperationsAdaptiveGroups`, `r2StorageAdaptiveGroups`,
`workersInvocationsAdaptive`, `d1AnalyticsAdaptiveGroups`), a full ListObjectsV2 walk of the
bucket run from box2 (1,782 pages, ~$0.008), the fleet's own `[ops]` request counters
(`scripts/s2_ops_sweep.py` on both boxes, 24 h window), D1 REST queries, `/proc/net/dev` on
both boxes, and Hostinger's published terms.

## Box access (checked first)

Both boxes reachable from this Codespace over SSH on 2026-08-25 13:00Z: box1 `2.25.183.231`
(srv1739889, up 66 days) and box2 `72.62.97.220` (srv1856364, up 30 days).
Drift note: both boxes' `/root/tsr-s2` sit on tsr `340e783` while tsr `origin/main` is
`5e774f9` (10 commits ahead, docs/fleet/s1-filter changes); box1's tree has 1 dirty file.
Not touched by this audit, but any `fleet.sh deploy` will pull those commits too.

## Phase 1 — measurements

### 1. Listing attribution (the ~577K ListObjects/day)

Identical 24 h window (2026-08-24T13Z → 08-25T13Z): GraphQL counts **385,157 ListObjects**;
the eleven s2 emit lanes' own `list_pages` counters sum to **378,440 = 98%**. (The 577K figure
was the 08-18..20 level; the last four full days average 449K and are falling as retention
settles.) Per lane, list pages/day and share of all listing:

| lane (box) | list_pages/day | share | passes/day | pages/pass |
| --- | ---: | ---: | ---: | ---: |
| tat-s2-hwfd (box2) — Himawari FD, 22 browse bands | 148,741 | 38.6% | 11,415 | 13 |
| tat-s2-hwwpac (box2) — Himawari WPac suite | 57,173 | 14.8% | 145 | 394 |
| tat-s2-gk2a (box2) | 34,167 | 8.9% | 1,151 | 30 |
| tat-s2-hwfd-leads (box2) | 29,810 | 7.7% | 3,049 | 10 |
| tat-s2-conus (box1) | 28,793 | 7.5% | 1,412 | 20 |
| tat-s2-conus-fast (box2) | 19,073 | 5.0% | 1,576 | 12 |
| tat-s2-conus-fast2 (box1) | 17,995 | 4.7% | 803 | 22 |
| tat-s2-g19fd (box1) | 17,879 | 4.6% | 284 | 63 |
| tat-s2-g19fd-leads (box1) | 14,156 | 3.7% | 998 | 14 |
| tat-s2-geo (box2) | 10,272 | 2.7% | 228 | 45 |
| tat-s2-mtg (box2, idle) | 381 | 0.1% | 127 | 3 |
| **s2 lanes total** | **378,440** | **98.3%** | | |
| everything else (residual) | ~6,700 | 1.7% | | |

The residual is fully explained by known small listers: the prune walk (~1.3K pages/day:
1.14M `shadow/sat` objects / 1000 + ~120 delimiter lists), `meso_poller.reconcile_manifests`
(~2.4K/day: 21K `meso/` objects listed every 15 min), and the GitHub crons' `aws s3 sync`/`ls`
steps (<1K/day; each sync lists only its own small destination prefix). No Worker lists
(`cyclolab-router` has the only R2 binding and calls `get()` only); `s1_ingest` lists once at
cold start; the floater/intensity/guidance pollers never list; the box HAFS poller is not
running (the GH cron is the live `models/hafs` writer).

Top consumers, named: **hwfd, hwwpac, gk2a** — 60% of all listing between them.

PUTs in the same window: 182,193 total, s2 lanes 90,626 (50%). The non-s2 half is the
`update-hafs` cron (~40K PNGs/day = 4 cycles × ~10K objects), the meso poller (~17K/day),
s1 ingest, floaters, the 11 overlay pollers, intensity/guidance feeds, and the two box
heartbeats (2.9K/day).

### 2. Which listing loops can become a writer-maintained manifest

The s2 emit lanes are each the **sole writer** of their products, and every product already
carries a writer-maintained `latest_times.json` (incremental append since 2026-08-03, full
rebuild only on the 6 h heal tick). What still lists, per pass (`s2_pyramid_emit.py`):

| loop | what it does today | LIST cost | convertible? |
| --- | --- | ---: | --- |
| `_covered_times` → `complete_stamps(after=tail)` | backfill coverage: one delimiter list of the recent stamp tail + **one LIST probe per stamp** (checks `_ready.json`) | ~6–30/product/pass — the bulk of the 378K | **Yes.** Read the product's manifest (1 GET, Class B) as the covered set; the lane is the only writer, so R2 can only disagree by a frame this same process wrote and failed to append. Keep ONE `head()` (Class B) of the newest stamp's ready marker as the sanity check. Listing survives only in the 6 h heal tick and cold start. Expected: −90% of s2 listing (≈ −340K/day ≈ −$46/month). |
| `_reconcile_manifest` | 1 GET of the manifest + a LIST only when an orphan is found | ~0 in the healthy path | already manifest-based |
| `_probe_stale_pin` | HEAD of a pinned stamp | Class B | n/a |
| `has_complete_frame` (products index) | tail delimiter list + ≤8 probes, throttled to 30 min per sat/sector | ~5–10/index refresh | Yes: read `latest_times.json` for each sibling (1 GET each) instead of probing; index refresh is already throttled |
| heal tick / cold rebuild | full `complete_stamps` over the 90-frame window | ~100–300 per product per 6 h | **Genuinely needs to list** — it is the reconciliation that makes the manifest trustworthy. Keep. |
| prune walk (`s2_prune.py`) | full key walk under `shadow/sat/` daily | ~1.3K/day | Needs to list (finds orphans the manifest cannot know about). Keep; it is 0.3% of listing. |
| meso `reconcile_manifests` | full list of every band every 15 min | ~2.4K/day | Convertible (sole writer, manifest exists) but only 0.6% of listing — not worth the risk now. |

Also note the odd shape of `hwfd`: 11,415 passes/day for 22 bands is ~520 passes/band/day,
i.e. a pass every ~3 min on a 10-min product — the cheapest single cut is its pass cadence,
before any manifest work.

Conversion is a change to the emit hot path (the data-critical gate applies: adversarial
review + a frame-identical before/after check on one lane). It is NOT part of this audit's
changes; it is the recommended Phase 2 item (below).

### 3. R2 storage, measured

Full bucket walk 2026-08-25 13:08Z: **2.375 TB, 1,781,311 objects** (GraphQL daily max for the
same day: 2,427 GB, 1.82M objects — the 2% gap is in-flight/multipart data the walk does
not see). **Your 2.8 TB estimate is high by ~15%: it is 2.4 TB.**

| prefix | GB | objects | share |
| --- | ---: | ---: | ---: |
| `shadow/sat/` (tiled satellite pyramids: himawari9 621, goes19 610, geo 109, gk2a 79) | 1,419 | 1,139,833 | 59.7% |
| `models/hafs/` (HAFS render PNGs, one prefix per cycle, 15–22 GB per cycle) | 919 | 482,418 | 38.7% |
| `floaters/` | 15 | 88,252 | 0.6% |
| `microwave/` | 9 | 32,359 | 0.4% |
| `sst/` (animations + climo) | 3.5 | 1,668 | 0.1% |
| `meso/` | 2.8 | 21,117 | 0.1% |
| everything else (qscat, subseasonal, sar, radar, recon, armor3d, cyclolab, ascat, …) | < 4 | ~15,000 | 0.2% |

Storage cost at $0.015/GB-month: **$36/month today** — and it is the line that compounds (see 4).

### 4. Retention — what the prune deletes, and is storage growing (the important number)

Storage is **growing ~41 GB/day (7-day average), +$0.62/month of new recurring cost per day**.
30-day series (GraphQL, daily max): 296 GB on 07-25 → 1,068 GB on 08-03 → 2,140 GB on 08-18
→ 2,427 GB on 08-25. Object count meanwhile fell 28.6M → 1.8M (container tiles + prune), so
the byte growth is large objects, not tile sprawl. The age census says exactly where:

| prefix | <1 d | 1–3 d | 3–7 d | 7–14 d | 14–30 d | >30 d | retention in force |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `shadow/sat/` | 94 | 195 | 389 | 692 | 50 | **0** | `tat-s2-prune` lane (box2): `shadow/sat/**` only, 14-day TTL by frame stamp, keep-min 2, daily (last three runs 08-22 23:40Z, 08-23 23:57Z, 08-25 00:14Z), ~16.6K stamps / 79K objects / **~100 GB deleted per run** across 116 products. Write rate ≈ 95–100 GB/day, so 14 d × ~100 = **~1.4 TB is its steady state: FLAT**. |
| `models/hafs/` | 31 | 78 | 219 | 183 | **382** | 27 | **None.** Each cycle lands under its own `models/hafs/{cycle}/` prefix; `update-hafs.yml` syncs `--delete` only within the cycle it just rendered, so cycles never age out. 30–55 GB/day written (scales with storm count). This prefix is the entire growth: 919 GB after ~35 days and **unbounded**. |
| `floaters/backdrops`, `sst/climo`, `shadow/models`, `meso/backdrops` | | | | | | ~7 total | static assets, fine |
| `floaters/{storm}`, `microwave/{storm}` | | | | | | ~5 | per-storm; retired storms are pruned by their pollers; small |

Projection if nothing changes: HAFS adds ~1.2–1.6 TB/month → storage passes 4 TB by
October ($60/month and rising). With a **14-day HAFS retention** the prefix caps at ~450–750 GB
and total steady state is ~2.0–2.2 TB (≈ $31/month, flat); with 7 days, ~1.7 TB (≈ $26).
This is the single highest-value change in the audit and it is one lifecycle-style prune of
`models/hafs/{cycle}/` prefixes older than N days (the box R2 token cannot set bucket
lifecycle rules — verified 2026-07-08 — so it is a prune walk like `s2_prune`, ~480 list
pages/day at today's object count). Recommended Phase 2 item; NOT changed by this audit.

### 5. D1 size

| database | file size | tables | rows |
| --- | ---: | ---: | --- |
| `tat-sandbox` (accounts backend; Clerk-authenticated `tat-sandbox-api`, shipped 08-19) | **375.6 MB** | 9 | users **154** (not 106), seasons 3,035, storms 62,562, records 4,820, season_records 3,927, account_checkpoints 965, derived_state 152, climo 236 |
| `taw-subscriptions` (warning poller) | 45 KB | 2 | (subscriptions, sent_alerts) |

7-day load on `tat-sandbox`: 427K read queries / 129.5M rows read / 358K write queries /
1.12M rows written / 44 GB of result bytes; p50 query 0.2 ms, p99 41–75 ms. 375 MB and
~60K queries/day is trivially small for SQLite or Postgres on a box (box2 idles at 0.5 load/core
with 382 GB free). **Confirmed: it fits on a box.**

### 6. Workers — request volume, CPU, and the meter that actually matters

7-day totals: `tat-sandbox-api` 287,348 req (**109,112 errors**), `triple-a-tropics-proxy`
147,124, `sandbox-router` 94,991, `cyclolab-router` 17,776, `taw-warning-poller` 5,433,
`telemetry-collector` 168, `bugs-api` 20. Total ≈ **79K requests/day**.

Finding: the account is on the **Workers Free plan**. Evidence: `tat-sandbox-api` cpuTime p50 =
exactly 10,000 µs with no per-script limit configured, and its errors are `exceededResources`
(57,685 in the last 3 days = **41% of its requests**) — the free plan's 10 ms CPU cap. The meter
that matters is therefore not dollars but the **100K requests/day hard cap shared by all
workers**, of which ~80K/day is already used; and the sandbox API is failing four in ten
requests on CPU. (Out of this audit's scope — noted because Phase 2's box migration removes it,
and because the breaker was designed around the remaining ~20K/day of headroom.)

### 7. Box headroom

box1: **349 GB free** of 387 GB (10% used); box2: **382 GB free** (2% used). RAM 31 GB each.
Neither disk can hold the 2.4 TB media archive; either can hold D1's 375 MB a thousand times over.

### 8. Hostinger bandwidth terms and current usage

Terms (Hostinger KVM 8, both boxes): **32 TB/month**; when exceeded the port is throttled to
**10 Mbps for the rest of the month** — no overage fees, reset on the 1st; the only remedy is
a plan upgrade. Hostinger's article defines bandwidth as "data transferred between your server
and the internet" without stating direction; treat it as **inbound + outbound** until proven
otherwise (the conservative reading).

Measured from `/proc/net/dev` since boot:

| box | rx/day | tx/day | monthly total | of 32 TB |
| --- | ---: | ---: | ---: | ---: |
| box1 (66.7 d) | 739 GB | 31 GB | **23.4 TB** | 73% |
| box2 (30.8 d) | 725 GB | 66 GB | **24.1 TB** | 75% |

Almost all of it is inbound satellite ingest (NOAA/JAXA S3 pulls). R2 currently serves
~200 GB/day of egress to the CDN edge (GraphQL `responseObjectSize`, 176–243 GB/day), i.e.
~6 TB/month. **Moving media serving onto either box would push it past 32 TB and throttle it
to 10 Mbps — the site would go dark on the 20th of the month.** Even a dedicated third box
could not hold the 2.4 TB. Conclusion: media stays on R2, which has free egress; the Hostinger
cap is a hard ceiling to keep an eye on (a third emit box would be the first relief if ingest grows).

### Cost model, measured (Cloudflare)

| line | measured rate | $/month | trend |
| --- | ---: | ---: | --- |
| R2 Class A (last 4 full days avg 661K/day; 98% of LISTs are s2 lanes) | 661K/day | ~$90 (before the 1M/mo free allowance) | flat, slowly falling |
| R2 Class B (GET/HEAD) | 136K/day | ~$1.5 | flat |
| R2 storage | 2.43 TB | ~$36 | **+$19/month per month** (HAFS) |
| Workers / D1 / Pages | free plan | $0 | hard caps, not dollars (see 6) |
| AWS (tat-sat-ingest SQS/SNS) | as stated by Andrew | ~$1.6, $30 budget | cannot be verified from this Codespace — the `tat-sat-ingest` IAM user has no CE/Budgets read |

## Phase 2 — decision (no change made; this is the recommendation)

Your working assumption, checked against the measurements:

1. **Accounts backend → flat rate on a box: AGREED, with one correction to the reason.** D1
   holds 375 MB / 154 users and is trivially box-sized. The metered exposure today is not
   dollars (free plan) but the free plan's hard caps, which the sandbox API is already hitting
   (41% `exceededResources`). Moving it to SQLite-or-Postgres + a service behind box1's Caddy
   fixes both. Keep it behind Cloudflare's free proxy: box1 already runs `cloudflared` (the
   `radar-api.triple-a-tropics.com` tunnel), so the API gets a tunnel hostname, not an open port.
   Migration gate as you specified: dry run, verified row counts both sides (the counts above are
   the baseline), Clerk JWT verification ported unchanged, and a rollback that keeps D1 live
   read-only until the box copy has served a full day — no season lost. **Not started; needs
   your go and Phase 0's breaker in place first.**
2. **Media archive stays metered on R2 with ceilings: AGREED, and the measurements make it
   stronger** — 2.4 TB does not fit a 387 GB disk, and Hostinger's 32 TB cap (both boxes at
   ~74% from ingest alone) rules out serving from a box outright. The ceilings are: the breaker
   (Phase 0), the kill switch (Phase 0), and **retention on `models/hafs`** — the one line that
   compounds, and a one-file change.
3. **Leave alone: Pages, AWS.** Agreed. Nothing here touches either.

Recommended order after your authorization (each is its own small commit with its own gate):

| # | change | saves | risk / gate |
| --- | --- | ---: | --- |
| A | `models/hafs` retention prune (14 d; walk + DeleteObjects, DeleteObjects is free) | stops +$19/mo/mo; caps storage at ~$31/mo | low; dry-run report first, then apply |
| B | Cut `hwfd` pass cadence to its product cadence | −$10–15/mo Class A | low; cadence knob |
| C | s2 coverage check from the manifest instead of per-stamp LIST probes (§2) | −$40–50/mo Class A | medium; emit hot path → adversarial review + frame-identical check on one lane before fleet |
| D | Accounts backend to box1 (SQLite + service behind Caddy via the existing tunnel) | removes the free-plan cap failures; $0 | high care; the lineage-fix protocol |
| E | Replace the breaker's borrowed analytics token with a scoped Account Analytics:Read token | hygiene | needs Andrew (token creation is not in this Codespace's reach) |

Do NOT: reintroduce `actions/cache` for SST frames, widen the box R2 token, or move the
media to a box.

## Changes applied 2026-08-25 (authorized by Andrew: A, B, C in that order, each its own commit)

### Change A — `models/hafs` retention, 14 days: dry run (recorded BEFORE applying)

`scripts/hafs_r2_prune.py --days 14` run from box2 at 2026-08-25T16:04:38Z (read-only, 374 LIST pages).
Rule: a cycle is deleted iff its cycle time is older than 14 days AND it is not one of the newest 2
AND the live `manifest.json` does not reference it (live cycles at run time: 2026082400, 2026082406).
Non-cycle children of `models/hafs/` are never touched (found: none).

**Would delete 168 cycles, 220,745 objects, 435.2 GB**
(oldest 2026060518, 80.9 d; newest 2026081112, 14.2 d).
**Keeps 41 cycles** (2026081118 .. 2026082406), ~484 GB.
Recurring: `.github/workflows/prune-hafs.yml`, daily 05:31 UTC (+06:01 backup), dispatchable as a dry run.
Outcome of the apply and the measured storage: see the "Measured after" table appended below once available.

**Applied** from box2 as a detached unit, 2026-08-25 16:13:03Z to 16:45:55Z: `TOTAL deleted: 168 cycles,
220,745 objects, 435.2 GB (listing cost: 374 pages); kept 41 cycles`, 0 DeleteObjects errors, exit 0.
Post-apply dry run at 16:47Z: `41 cycle prefixes (2026081118 .. 2026082406) ... would delete: 0 cycles`.
Storage as accounted by R2 (GraphQL `r2StorageAdaptiveGroups`, lags ~1 h) is recorded in the
"Measured after" table below as it lands.

<details><summary>Full list of the 168 cycles (cycle, age in days, objects, GB)</summary>

| cycle | age d | objects | GB |
| --- | ---: | ---: | ---: |
| 2026060518 | 80.9 | 1 | 0.00 |
| 2026060600 | 80.7 | 1 | 0.00 |
| 2026060606 | 80.4 | 1 | 0.00 |
| 2026060612 | 80.2 | 1 | 0.00 |
| 2026060618 | 79.9 | 1 | 0.00 |
| 2026060700 | 79.7 | 1 | 0.00 |
| 2026060706 | 79.4 | 1 | 0.00 |
| 2026060712 | 79.2 | 1 | 0.00 |
| 2026060718 | 78.9 | 1 | 0.00 |
| 2026060800 | 78.7 | 1 | 0.00 |
| 2026060806 | 78.4 | 1 | 0.00 |
| 2026060812 | 78.2 | 1 | 0.00 |
| 2026060818 | 77.9 | 1 | 0.00 |
| 2026060900 | 77.7 | 1 | 0.00 |
| 2026060906 | 77.4 | 1 | 0.00 |
| 2026060912 | 77.2 | 1 | 0.00 |
| 2026060918 | 76.9 | 1 | 0.00 |
| 2026061000 | 76.7 | 1 | 0.00 |
| 2026061006 | 76.4 | 1 | 0.00 |
| 2026061012 | 76.2 | 1 | 0.00 |
| 2026061018 | 75.9 | 1 | 0.00 |
| 2026061100 | 75.7 | 1 | 0.00 |
| 2026061400 | 72.7 | 1 | 0.00 |
| 2026061406 | 72.4 | 1 | 0.00 |
| 2026061412 | 72.2 | 1 | 0.00 |
| 2026061418 | 71.9 | 1 | 0.00 |
| 2026061500 | 71.7 | 1 | 0.00 |
| 2026061506 | 71.4 | 1 | 0.00 |
| 2026061512 | 71.2 | 1 | 0.00 |
| 2026061518 | 70.9 | 1 | 0.00 |
| 2026061600 | 70.7 | 1 | 0.00 |
| 2026061606 | 70.4 | 1 | 0.00 |
| 2026061612 | 70.2 | 1 | 0.00 |
| 2026061618 | 69.9 | 1 | 0.00 |
| 2026061700 | 69.7 | 1 | 0.00 |
| 2026061706 | 69.4 | 1 | 0.00 |
| 2026061812 | 68.2 | 1 | 0.00 |
| 2026061818 | 67.9 | 1 | 0.00 |
| 2026061900 | 67.7 | 1 | 0.00 |
| 2026061906 | 67.4 | 1 | 0.00 |
| 2026061912 | 67.2 | 1 | 0.00 |
| 2026061918 | 66.9 | 1 | 0.00 |
| 2026062000 | 66.7 | 1 | 0.00 |
| 2026062006 | 66.4 | 1 | 0.00 |
| 2026062012 | 66.2 | 1 | 0.00 |
| 2026062018 | 65.9 | 1 | 0.00 |
| 2026062100 | 65.7 | 1 | 0.00 |
| 2026062106 | 65.4 | 1 | 0.00 |
| 2026062112 | 65.2 | 1 | 0.00 |
| 2026062118 | 64.9 | 1 | 0.00 |
| 2026062200 | 64.7 | 1 | 0.00 |
| 2026062206 | 64.4 | 1 | 0.00 |
| 2026062212 | 64.2 | 1 | 0.00 |
| 2026062218 | 63.9 | 1 | 0.00 |
| 2026062300 | 63.7 | 1 | 0.00 |
| 2026062306 | 63.4 | 1 | 0.00 |
| 2026062312 | 63.2 | 1 | 0.00 |
| 2026062318 | 62.9 | 1 | 0.00 |
| 2026062400 | 62.7 | 1 | 0.00 |
| 2026062406 | 62.4 | 1 | 0.00 |
| 2026062412 | 62.2 | 1 | 0.00 |
| 2026062418 | 61.9 | 1 | 0.00 |
| 2026062500 | 61.7 | 1 | 0.00 |
| 2026062506 | 61.4 | 1 | 0.00 |
| 2026062512 | 61.2 | 1 | 0.00 |
| 2026062518 | 60.9 | 1 | 0.00 |
| 2026062600 | 60.7 | 1 | 0.00 |
| 2026062606 | 60.4 | 1 | 0.00 |
| 2026062612 | 60.2 | 1 | 0.00 |
| 2026062618 | 59.9 | 1 | 0.00 |
| 2026062700 | 59.7 | 1 | 0.00 |
| 2026062706 | 59.4 | 1 | 0.00 |
| 2026062712 | 59.2 | 1 | 0.00 |
| 2026062818 | 57.9 | 1 | 0.00 |
| 2026062900 | 57.7 | 1 | 0.00 |
| 2026062906 | 57.4 | 1 | 0.00 |
| 2026062912 | 57.2 | 1 | 0.00 |
| 2026062918 | 56.9 | 1 | 0.00 |
| 2026063000 | 56.7 | 1 | 0.00 |
| 2026063006 | 56.4 | 1 | 0.00 |
| 2026063012 | 56.2 | 1 | 0.00 |
| 2026063018 | 55.9 | 1 | 0.00 |
| 2026070100 | 55.7 | 1 | 0.00 |
| 2026070106 | 55.4 | 1 | 0.00 |
| 2026070112 | 55.2 | 1 | 0.00 |
| 2026070118 | 54.9 | 1 | 0.00 |
| 2026070200 | 54.7 | 1 | 0.00 |
| 2026070206 | 54.4 | 1 | 0.00 |
| 2026070212 | 54.2 | 1 | 0.00 |
| 2026070218 | 53.9 | 1 | 0.00 |
| 2026070300 | 53.7 | 1 | 0.00 |
| 2026070306 | 53.4 | 1 | 0.00 |
| 2026070312 | 53.2 | 1 | 0.00 |
| 2026070318 | 52.9 | 1 | 0.00 |
| 2026070400 | 52.7 | 1 | 0.00 |
| 2026070406 | 52.4 | 1 | 0.00 |
| 2026070412 | 52.2 | 1 | 0.00 |
| 2026070418 | 51.9 | 1 | 0.00 |
| 2026070500 | 51.7 | 1 | 0.00 |
| 2026070506 | 51.4 | 1 | 0.00 |
| 2026070512 | 51.2 | 1 | 0.00 |
| 2026070518 | 50.9 | 1 | 0.00 |
| 2026070600 | 50.7 | 1 | 0.00 |
| 2026070606 | 50.4 | 1 | 0.00 |
| 2026070612 | 50.2 | 1 | 0.00 |
| 2026070618 | 49.9 | 1 | 0.00 |
| 2026070700 | 49.7 | 1 | 0.00 |
| 2026070706 | 49.4 | 1 | 0.00 |
| 2026070712 | 49.2 | 1 | 0.00 |
| 2026070718 | 48.9 | 1 | 0.00 |
| 2026070800 | 48.7 | 1 | 0.00 |
| 2026070806 | 48.4 | 1 | 0.00 |
| 2026070812 | 48.2 | 1 | 0.00 |
| 2026070818 | 47.9 | 1 | 0.00 |
| 2026070900 | 47.7 | 1 | 0.00 |
| 2026070906 | 47.4 | 1 | 0.00 |
| 2026070912 | 47.2 | 1 | 0.00 |
| 2026070918 | 46.9 | 1 | 0.00 |
| 2026071000 | 46.7 | 1 | 0.00 |
| 2026071006 | 46.4 | 1 | 0.00 |
| 2026071012 | 46.2 | 1 | 0.00 |
| 2026071018 | 45.9 | 1 | 0.00 |
| 2026071100 | 45.7 | 1 | 0.00 |
| 2026071106 | 45.4 | 1 | 0.00 |
| 2026071812 | 38.2 | 5,677 | 7.09 |
| 2026071818 | 37.9 | 1 | 0.00 |
| 2026071900 | 37.7 | 8,515 | 10.87 |
| 2026071906 | 37.4 | 7,150 | 8.93 |
| 2026072712 | 29.2 | 4,490 | 5.86 |
| 2026072718 | 28.9 | 7,095 | 8.55 |
| 2026072800 | 28.7 | 7,095 | 8.54 |
| 2026072806 | 28.4 | 7,095 | 8.46 |
| 2026072812 | 28.2 | 7,095 | 8.39 |
| 2026072818 | 27.9 | 7,095 | 8.48 |
| 2026080300 | 22.7 | 2,838 | 4.27 |
| 2026080306 | 22.4 | 3,234 | 8.56 |
| 2026080312 | 22.2 | 3,366 | 8.68 |
| 2026080318 | 21.9 | 1,683 | 4.43 |
| 2026080400 | 21.7 | 1,683 | 4.46 |
| 2026080406 | 21.4 | 3,366 | 8.74 |
| 2026080412 | 21.2 | 3,366 | 8.92 |
| 2026080418 | 20.9 | 1,683 | 4.59 |
| 2026080500 | 20.7 | 3,366 | 8.87 |
| 2026080506 | 20.4 | 3,366 | 8.91 |
| 2026080512 | 20.2 | 3,366 | 8.95 |
| 2026080518 | 19.9 | 3,366 | 8.77 |
| 2026080600 | 19.7 | 5,049 | 12.41 |
| 2026080606 | 19.4 | 5,049 | 12.23 |
| 2026080612 | 19.2 | 5,049 | 12.29 |
| 2026080618 | 18.9 | 3,366 | 8.29 |
| 2026080700 | 18.7 | 5,175 | 12.62 |
| 2026080706 | 18.4 | 6,907 | 16.48 |
| 2026080712 | 18.2 | 2,245 | 3.60 |
| 2026080718 | 17.9 | 5,165 | 12.72 |
| 2026080800 | 17.7 | 5,202 | 12.63 |
| 2026080806 | 17.4 | 3,468 | 8.20 |
| 2026080812 | 17.2 | 6,222 | 13.41 |
| 2026080818 | 16.9 | 6,327 | 13.82 |
| 2026080900 | 16.7 | 6,936 | 15.30 |
| 2026080906 | 16.4 | 6,936 | 15.25 |
| 2026080912 | 16.2 | 6,369 | 13.70 |
| 2026081000 | 15.7 | 6,264 | 12.30 |
| 2026081006 | 15.4 | 8,642 | 17.21 |
| 2026081012 | 15.2 | 5,192 | 10.24 |
| 2026081018 | 14.9 | 6,254 | 12.43 |
| 2026081100 | 14.7 | 6,216 | 12.25 |
| 2026081106 | 14.4 | 6,133 | 11.84 |
| 2026081112 | 14.2 | 6,464 | 12.64 |

</details>

### Change B — hwfd sweep cadence (tsr c3f739b, lane recreated on box2 2026-08-25 16:12:03Z)

`docker-compose.s2.hw-fd-lane.yml`: the 22-band Himawari FD browse lane slept 30 s between sweeps; now
`sleep "${S2_HWFD_SWEEP_SLEEP_S:-900}"`. Compose-only, recreated with `--no-build`. The first sweep after a
recreate is a cold manifest rebuild (~213 LIST pages per product, one-off by design: the heal state lives in
the container); steady state from the 16:54Z sweep on.

| hwfd lane | before (24 h to 13:06Z) | after (steady state, 16:54–17:20Z, 30 passes) |
| --- | ---: | ---: |
| sweep period | ~2.8 min (`sleep 30` + 22 idle probes) | ~23.5 min (`sleep 900` + ~8.5 min rendering 22 frames) |
| passes/day | 11,415 | ~1,350 |
| LIST pages per pass | ~13 (idle) | ~10 (each pass now lands a frame) |
| LIST pages/day | 148,741 | **~14,000** |
| PUT/day | 12,791 | ~7,400 |
| Class A/day | 161,569 | **~21,500 (−140K/day ≈ −$0.63/day ≈ −$19/month)** |

Estimate was $10–15/month; measured −$19/month. Slots are not skipped (every sweep renders every uncovered slot
in the 120-min backfill window: sweeps at 16:30, 16:54, 17:17Z each rendered exactly the one new 20-min slot);
the cost is ~+12 min mean latency on the 22 non-animated browse bands (the five animated leads stay on their own
10-min lane). Fleet-level confirmation from GraphQL hourly LIST counts is in the "Measured after" table.

### Change C — s2 backfill coverage from the manifest (tsr branch `coverage-manifest`, 7a19787)

Built under a three-lens adversarial review (hot path, cost/ops, tests+fallback). Two HIGH findings were real and
fixed before any deploy: a geometry migration inside the backfill window would have made the live manifest
flip-flop every pass, and a frame deleted under a live manifest would never have been re-rendered (the container
canary watchdog's rollback relies on delete-to-re-render). The fix added a periodic verify listing
(`S2_COVER_VERIFY_S`, 30 min per product, so deletions and off-grid orphans heal within 30 min instead of 6 h)
and a stray-geometry guard in `emit_one`. A second, independent re-review of the final code found no blocking
defects and two one-line hardenings, landed as 7a19787. `S2_COVER_FROM_MANIFEST=0` restores the old listing
path byte-for-byte without a code redeploy. 38 new tests; full suite 1,124 with the same 10 pre-existing
failures as the untouched tree.

Gate 1, equivalence (read-only, on box2, inside the image, 18:12Z): `scripts/s2_coverage_equivalence.py` on the
five hwfd-leads products: **EQUIVALENT** (manifest path `get=1` vs listing path 7 LIST pages per product; zero
orphans, strays or ghosts).
Gate 2, one-lane canary: box2 `tat-s2-hwfd-leads` recreated on image `tat-s2:cov` at **18:43:45Z** with an
auto-rollback watchdog (`s2_cov_canary_watchdog.sh`: stale manifest > 4,200 s, traceback or 6 `[FAIL]`s in a
round, newest advertised frame without `_ready.json`, or no `[done]` for 4,200 s → recreate on `tat-s2:latest`
and exit 42; 7,200 s clean → PASS). Fleet roll only after PASS.

**Canary PASS 20:45:36Z** (25 clean watchdog rounds): 268 passes, 249 on the manifest path, 20 verify listings,
0 fallbacks, 0 `[FAIL]`, 0 tracebacks, 0 strays; all five products advanced on cadence; last canary hour
**202 list pages vs ~1,240/h before on this lane (−84%)**. Fleet roll: tsr `main` fast-forwarded to a6e4500
(C + the re-review hardenings + the canary watchdog); `fleet.sh deploy box2` 20:48–20:50Z (8 lanes),
box1 21:35Z (4 lanes, run as a detached unit after the foreground attempt hit its 15-min timeout mid-build;
box1's checkout had already been reset to a6e4500, old lanes kept running — a consistent state). Every
recreate costs a one-off cold heal rebuild (~200 LIST pages per product) before steady state. The `mtg` lane
logs `[FAIL] ... HTTP 403` per slot after its recreate: that is the pre-existing EUMETSAT licence gate (the
lane has never published; ops unchanged at `get=3 list_pages=3`), and the new code handles its manifest-less
products correctly (`manifest unusable ... (missing) -- listing fallback`). Rollback lever if ever needed:
`S2_COVER_FROM_MANIFEST=0` in the box `.env` + recreate (no code redeploy).

Projection for C at the fleet with the 30-min verify cadence (from the measured pass rates): about
−151K list pages/day (−62% of what was left after B), ≈ −$21/month — below the original −$45 estimate because
B already took the largest single slice and the verify listing (the price of the delete-to-re-render fix)
barely helps lanes whose sweep is already ~30 min apart (hwfd, g19fd). `S2_COVER_VERIFY_S` can be raised per
lane if more is wanted. Measured fleet numbers: "Measured after" table.

### Measured after each change (Class A rate and storage, not projections)

| when (UTC) | change state | Class A trailing hour (breaker `rate_1h`) | GraphQL LIST/h (last complete hour) | storage (GraphQL, lags ~1 h) | objects |
| --- | --- | ---: | ---: | ---: | ---: |
| 08-25 15:20 | baseline (24 h to 15:00Z: A 567,191/day, LIST 383,264/day) | 21,294 (15:57 tick) | 15,627 (15:00h) | 2,382.3 GB | 1,786,928 |
| 08-25 16:50 | A applied 16:13–16:46Z (−435.2 GB reported) | 24,189 (prune walk + hwfd cold rebuild in window) | 14,324 (16:00h) | **1,953.0 GB (−431 GB, −18%)** | **1,570,490 (−218K)** |
| 08-25 17:17 | B steady state since 16:54Z | **19,129** | (17:00h pending) | 1,953.0 GB | 1,570,490 |
| 08-25 18:07 | B only (first clean hour 17:00–18:00Z) | **13,401** | **6,575 (17:00h)** vs 15,627 (15:00h) | 1,954.5 GB | 1,571,829 |
| 08-25 21:58 | C fleet: box2 steady (1 h after its roll), box1 in its cold rebuild (rolled 21:35Z) | 15,605 (28.5K peak during box1's cold sweep) | 8,661 (19:00h, canary+B) · 23,034 (20:00h, box2 cold rebuild) | 1,898.8 GB (the recreated prune lane ran once more) | 1,523,715 |
| 08-25 23:58 | C fleet +3 h: box2 steady, box1 past most of its cold rebuild (g19fd's ~110-min sweep still finishing it) | **10,525** | **6,058 (22:00h)** · 7,798 (21:00h, box1 cold rebuild) | 1,906.6 GB | 1,529,669 |
| 08-26 01:20 | C fleet +4.5 h (box1 +3.7 h): both boxes steady except g19fd's last cold rebuilds | **9,381** | **3,391 (00:00h)** · 4,228 (23:00h) | 1,910.1 GB | 1,531,846 |

Fleet-level reading two hours after B: Class A per hour fell from 21,845 (15:00Z hour) to 13,586 (17:00Z hour) and
LIST per hour from 15,627 to 6,575; the breaker's trailing hour read 13,401 at 18:07Z. The hwfd lane's own counter
is the precise instrument for B (148.7K to ~15–18K list pages/day); the fleet hourly figure also carries diurnal and
low-pass-count lane noise (box1's g19fd sweeps every ~110 min), so the 2026-08-26 daily GraphQL total is the
definitive before/after for B (expected ~250K LIST/day before C lands).

Workers Paid (Andrew's upgrade, confirmed active by him ~19:30Z). Measured on the RATE, not the period total:
previous hour 924 of 1,662 requests `exceededResources` (55.6%); last hour (to 19:35Z) 442 of 1,389 (31.8%);
5-minute buckets 19:20–19:40Z: 34%, 26%, 40%, 37%, 44% — **the errors have not stopped**. Two live tails
(`wrangler tail`, 19:38Z and 19:42–19:44Z): every failure is `exceededCpu` ("Worker exceeded CPU time limit")
on exactly two endpoints, `PUT /api/checkpoint/<key>` and `POST /api/season` (0.5–1.2 MB bodies: gunzip +
SHA-256 in JS), killed at **exactly 50 ms CPU** (min = median = max = 50) — while in the same two minutes the
same endpoints completed 25 times with CPU up to 341 ms. The kill point moved from 10,000 µs to 50,000 µs at
~19:33Z (the plan change propagating), but not to the Paid default of 30 s; the deployed version (6250f19f,
2026-08-19) carries no per-script `cpu_ms` limit. Cloudflare's limits page says each isolate has "built-in
flexibility" for infrequent overruns and terminates when the Worker "starts hitting the limit consistently",
which matches the pattern (some isolates still enforcing a small limit, bursts allowed, then kills).
**Follow-up, measured to 23:59Z (corrects an interim reading):** the kills did NOT stop. Hourly
`exceededResources`: 55% (17Z), 47% (18Z), 35% (19Z), 8% (20Z), 16% (21Z, 525 of 3,309), 9% (22Z), 24% (23Z,
460 of 1,923), every kill still at exactly 50 ms CPU. The 20:10–20:50Z stretch of 0% that looked like the cap
lifting coincided with almost no checkpoint writes (the only endpoints that ever fail), so it was traffic mix,
not the plan. The script's usage model is `standard` on every endpoint that reports it (scripts list, version
runtime), no per-script `cpu_ms`, account default `standard` — so this is not the legacy 50 ms "bundled"
model either. A direct probe of the account's CPU limit with a throwaway worker was attempted and abandoned
(workers.dev resolves IPv6-only from the Codespace and the boxes; the temporary zone route and the worker were
removed, route table verified back to the original six). Net: **the 10 ms cap was not the whole story — 3.5 h
after the upgrade this Worker is still terminated at 50 ms CPU while other invocations of the same endpoints
complete at 300+ ms.** Separate defect, sandbox-side, for Andrew: (1) redeploy the Worker unchanged and watch
the 5-minute rate; (2) if kills persist, set `[limits] cpu_ms = 30000` explicitly and redeploy; (3) if they
still persist, Cloudflare support with the tail evidence (this document's numbers); and independently, hash the
1 MB checkpoint uploads with `crypto.subtle.digest` instead of JS, which would bring those endpoints under
50 ms on any plan. Latest hours for the record: 00:00Z 6% (91 of 1,453), 01:00Z partial 0% (0 of 423, CPU

**2026-08-26 17:55Z, Andrew's follow-up ("replace the hand-written hash with crypto.subtle.digest"):** the deployed
module already hashes with `crypto.subtle.digest` and inflates with `DecompressionStream`; the hand-written JS on the
write path is `decodeBase64` (`Uint8Array.from(atob(v), c => c.charCodeAt(0))`, one callback per byte of a ~1 MB
checkpoint) plus a redundant `TextEncoder().encode(body)` length check. Measured with the exact functions from the
module (`workers/sandbox-api-hotfix/bench.mjs`): 0.57 MB gzip checkpoint **132 → 61 ms CPU** (decode 76 → 12 ms),
1.14 MB **210 → 132 ms** (decode 113 → 25 ms); decoded bytes identical. The residual is `inflate + JSON.parse` of the
whole checkpoint (46–102 ms, native but size-proportional) — the validation design, not the hash — so the kill
rate should fall, not vanish. Before numbers: last 8 h to 17:43Z 329 of 5,147 requests killed (6.4%; hourly 0–22%);
successful PUTs at 129–131 ms CPU in a 150 s tail. **The patch is vendored (`workers/sandbox-api-hotfix/`, patch +
config + `deploy.sh`) but NOT deployed: both the script-upload API call and `wrangler deploy` for this Worker were
blocked by the Codespace permission classifier.** After deploy, the after numbers come from the same two sources.
p50 11,942 µs) — the rate tracks the checkpoint-write traffic, so quiet hours read low without anything
having changed.

Change C, box2 steady state one hour after its roll (lane `[ops]` counters, 20:59–21:59Z window, all lanes
0 tracebacks / 0 `[FAIL]` / 0 fallbacks / 0 strays except the licence-gated `mtg`):

| box2 lane | list pages/day before B (13:06Z sweep) | after B | after C (this hour × 24) |
| --- | ---: | ---: | ---: |
| hwwpac (suite, 28 products) | 57,173 | 57,173 | **~9,900** |
| hwfd (22 products) | 148,741 | ~14,000 | **~10,700** |
| conus-fast | 19,073 | 19,073 | **~5,300** |
| hwfd-leads | 29,810 | 29,810 | **~5,000** |
| geo (suite) | 10,272 | 10,272 | **~2,200** |
| gk2a (suite) | 34,167 | 34,167 | **~1,900** |
| mtg (idle, licence 403) | 381 | 381 | ~430 |
| **box2 total** | **299,617** | **~165,000** | **~35,000 (−79% vs after B)** |

Box1 rolled at 21:35Z, so its lanes were still in the one-off cold heal rebuild in this window (conus-fast2
1,743 pages and conus 1,346 pages in 0.4 h). At +3 h (2 h window to 23:59Z) box1 read 7,158 pages, of which
g19fd's 3,513 were still its cold rebuild (a ~110-min sweep, 22 products); conus 1,793 (≈21.5K/day vs 28.8K
before), conus-fast2 1,081 (≈13K vs 18K), g19fd-leads 771 (≈9K vs 14K). Box2 at +3 h: 2,922 pages in 2 h
(≈35K/day, matching the +1 h reading). Fleet trailing hour at 23:58Z: **10,525 Class A/h** (breaker) against
22–24K/h in the same hours the day before and the 24 h-to-15:00Z baseline of 23.6K/h; the 22:00Z hour read
A 13,953 / LIST 6,058. At 01:20Z (2 h windows): box1 3,226 list pages (≈39K/day, of which g19fd 1,845 still finishing its 22
cold rebuilds; conus ≈6.2K/day vs 28.8K, conus-fast2 ≈5.5K vs 18K, g19fd-leads ≈4.9K vs 14.2K), box2 3,090
(≈37K/day). Fleet list pages ≈ 76K/day and falling as g19fd settles, against the 383K/day baseline (−80%);
the 00:00Z hour read **A 9,681 / LIST 3,391 / PUT 5,885** against 22–24K Class A in the same hours the day
before, i.e. ≈ 240K Class A/day (−58%) ≈ $1.08/day ≈ $33/month against $90/month at the baseline. Every lane
0 tracebacks / 0 `[FAIL]` / 0 fallbacks / 0 strays over the window except the licence-gated `mtg`. The
2026-08-26 daily GraphQL total is the definitive number for B + C together (baseline 567K Class A/day,
383K LIST/day).


Change A: storage −431 GB immediately (−$6.5/month of current bill) and the compounding stops: `models/hafs`
is now capped at 14 days (~450–750 GB depending on storm count) instead of growing 30–55 GB/day. The estimate
"+$19/month per month" was the growth rate; the first-day effect is the −431 GB step.

## Phase 0 — circuit breaker + kill switch (status as of 2026-08-25 16:05Z)

**Deployed, ALERT-ONLY.** Worker `r2-breaker` (`workers/r2-breaker.js`, TAT main a4a2b5c8) on
`https://triple-a-tropics.com/r2-breaker/status` (+ `r2-breaker.coloradoskier2018.workers.dev`),
cron every 5 min at 2 min past the boundary (`2,7,…,57`; a bucket read on its own boundary is
only ~70% ingested), state in D1 `tat-breaker`. First tick 15:57:55Z: rate_1h 21,294/h,
pace_15m 20,796/h, verdict ok, GraphQL 436 ms. Thresholds: warn 80,000/h, trip 150,000/h or a
15-min pace ≥ 300,000/h, sustained for 2 ticks (normal mean 29K, max 46K; incident 445K).
Alert mode opens one GitHub issue per episode ("[r2-breaker] WOULD HAVE TRIPPED …", label
`breaker`) and touches nothing; `writes_enabled` can only go false on a trip while ARMED or a
manual `POST /trip`, and only `POST /reset` (manual) turns it back on. Fail-open on every
monitoring failure: analytics errors, empty results, a missing token, or an exception never
change `writes_enabled`.

**It proves it is alive, three ways:** every tick stamps `last_tick`/`last_ok_tick` and a gap
> 20 min opens a "heartbeat gap" issue; `.github/workflows/breaker-liveness.yml` (half-hourly at
:11/:41, first manual run 32869163239 green) turns unreachable / silent > 20 min / 3 analytics
failures into a RED run (GitHub failure email); the `/fleet/` page carries a breaker card that
renders "unreachable" as a fault. Tests: 25 python + a 16-scenario node harness, adversarially
reviewed (three lenses; 8 confirmed findings fixed before deploy, including a deploy-script
SyntaxError that would have lost the admin key, a re-trip-after-reset window, and the
boundary under-count).

**Built, tested, NOT deployed (needs your authorization; it recreates lanes):** the box kill
switch. tsr branch `cost-breaker` (3b9b02c): `tat_killswitch.py` gates every R2 PUT in
`s1_ingest.R2` (all s2 lanes, s1, heartbeat), meso/floater/hafs `R2`, intensity `R2Sink`,
guidance `_R2`; `fleet/*` keys always pass (liveness); `scripts/heartbeat.sh` mirrors `/status`
to `fleet/breaker.json` on R2 (the only box→Worker traffic: 2 GET/min fleet-wide, because
Workers are on the free plan); `fleet.sh writes off|on [box|all]` is the 30-second manual path
(sets `TAT_R2_WRITES=0`, recreates only the write lanes from the same image, verifies the env
AND that the guard is present in each container); `fleet.sh breaker status|arm|disarm|trip|reset`.
TAT branch `cost-killswitch` (cc02f38b): the byte-identical module wired into the six
overlay `R2Store`s, `sarobs/store.py`, recon and ascat publishers. 73 + 65 tests. Until the
images are rebuilt, `writes off` reports `guard: ABSENT` on every lane and an armed trip
cannot stop anything, so: **deploy the switch before arming**.

Admin key: `TAT_BREAKER_ADMIN_KEY` in box1 `/root/tsr-s2/.env` (rotated once at deploy).
The Worker's GraphQL read currently uses a copy of the Codespace deploy token; a scoped
Account Analytics:Read token should replace it (queued, needs you).
