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

## Phase 0 — circuit breaker + kill switch (status)

See the section appended after deployment (below) and `workers/README.md` § r2-breaker.
