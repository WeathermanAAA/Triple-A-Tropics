# Agent status — Triple-A-Tropics autonomous work log

Maintained by Claude while Andrew is away. Updated after each meaningful step;
newest state first. Raw URL:
`https://raw.githubusercontent.com/WeathermanAAA/Triple-A-Tropics/main/AGENT_STATUS.md`

_Last update: 2026-07-13 ~17:3x UTC — 90C/91C fix LANDED (ace-core-v0.8.3; EP feed + stopgap geojson verified live; **home map converges when Q17 box rebuild runs — one manual step**) · explorer ASCAT backdrop + barb tooltips SHIPPED · bug-board friendly numbers SHIPPED · ERA5 χ-climo building (supervised, APDRC stalls now auto-recover) · morning: bug board LIVE + edge purge ACTIVE_

---

## 2026-07-13 (~17:3x UTC) — 90C/91C letter fix landed · ASCAT backdrop + tooltips · friendly bug numbers

- **90C/91C (ace-core-v0.8.3 @04f7574a, tag pushed):** the mislabel was
  `merge_and_extract_storms` rebuilding invest/PTC `atcf_id` from the PAGE
  basin's `invest_letter` ("E") — now it derives from the storm's OWN SID
  token (`NHC_CP902026`→"C"; page letter only for token-less IBTrACS SIDs).
  `parse_bdeck` keys SID + fallback name off each deck row's own basin
  field (byte-identical no-op today). Also fixed the adversarially-confirmed
  letter-blind SPAWNINVEST dedup ("…to ep902026" no longer retires an
  unrelated 90C; letterless legacy feeds keep number-only semantics).
  Gates: --no-live A/B byte-identical (EP+WP × ACE+tracks), 533-test suite,
  adversarial review LAND, 9 new locked tests. **Verified live:** EP tracks
  feed `atcf_id` 90C/91C ✓ (update-ace dispatched); poller repin+mirror
  pushed to tat-satellite-render @ba5085c6 and the DISPATCHED stopgap run
  wrote `designation` 90C/91C into global_storms.geojson ✓.
- **🔥 HOME MAP still flaps** (Andrew's report: X-markers/labels come and
  go): global_storms.geojson has TWO writers — the GH stopgap (:07/:37,
  now fully corrected) and the BOX intensity poller (07-12 build:
  pre-discovery-fix, drops CP invests entirely). They alternate ~every
  minute; ~half of refreshes lack 90C/91C. **The already-queued Q17 box
  pull+rebuild is the convergence step** — same requirements.txt now pins
  ace-core-v0.8.3, so one rebuild fixes floaters AND the home map for good.
- **Explorer scatterometer (@9abb33ac):** (1) satellite backdrop under the
  barbs — SC controls gain Backdrop: None / **Clean IR (default)** / IR
  color; Clean IR = native-gray C07/B07 3.9 µm, geo ring falls to pure-gray
  Dvorak-BD C13 (availSet-honest chain; NO desaturated-rainbow hack — its
  cold-top luminance is non-monotonic and would misrank tops); barbs get
  the dark casing over imagery; exiting the field restores the user's own
  tiles; chrome names the backdrop product. (2) barb hover tooltips —
  thinned cells retained per frame (from the loaded wvc arrays; no raster
  retention), 14 px nearest-cell scan, cursor tooltip with kt + FROM° +
  lat/lon + sensor + pass time. Stamps mwsc3/core4 bumped in-commit; edge
  purge had it live ~2 min after push. Programmatic verify done (smokes,
  suite, manifest 200s, cp902026 passes present); **eyeball on a real
  storm-locked view still worth 30 s of Andrew's time**.
- **Bug board friendly numbers (@3ac8f3f3):** testers see #1, #2, … by
  creation order (created-timestamp sort, GitHub-number tiebreak — closing
  never renumbers); real issue number stays under the hood (links, admin
  PATCH, "fixes #N"). Confirmed live at the edge.
- **ERA5 χ-climo:** APDRC OPeNDAP randomly stalls responses ~50% of slabs
  and libnetcdf honors no timeout (.dodsrc ignored) — a raw build hung
  twice. Now running under a supervisor (scratchpad): each 5-yr slab in a
  child process, 480 s hard-kill + fresh-connection retry, on-disk slab
  resume, then the committed builder's own main() runs against the local
  slabs (identical attrs/structure/sanity-check). Slabs 1-2 ok, slab 3
  recovered from a stall as designed. Lands as .nc commit + re-render.

---

## 2026-07-13 (~15:5x UTC) — CF-token morning batch: bug board LIVE · edge purge ACTIVE

- **Bug board deployed passcode-free** (`bash workers/deploy-bugs.sh`,
  token-auth, no login): Worker + route live, GITHUB_TOKEN/ADMIN_KEY wired,
  TESTER_PASSCODE confirmed absent. Smoke loop proven on prod: POST filed
  issue #30 → PATCH closed it with the fresh admin key → label stripped →
  board GET returns 0 reports; `/bugs/` serves HTTP 200. **Admin key was
  handed to Andrew in-chat only** (never in-repo). Deploy script hardened
  (@ac116bd0): smoke POST now retries through the ~30 s secret-propagation
  window that 503'd the first attempt ("board backend not configured yet"
  = the pre-secret Worker version still serving), and the bogus
  `wrangler secret delete --force` line is gone (no such flag in v4).
- **Edge-cache purge ACTIVE**: zone id pulled via the API, purge permission
  proven with a live single-URL purge, then `CLOUDFLARE_ZONE_ID` +
  `CLOUDFLARE_PURGE_TOKEN` set via `gh secret set`. Acceptance run on the
  real push @ac116bd0: workflow diffed the push, purged exactly
  `styles.css` (skipped `workers/`), waited for the Pages build, and the
  new bytes were serving at the edge ~2.5 min after `git push`
  (cf-cache-status HIT on fresh body). The 4-h stale-asset window is dead.
- In flight this session: (3) ERA5 χ-climatology swap — last night's build
  died with the Codespace; re-running from the committed ERA5-ready
  builder, then re-render + no mislabel left standing. (4) home-map
  invests at ~150–160°W read 90E/91E but 140°W–180° is CPHC's basin →
  must read 90C/91C; fixing the basin-letter assignment + keying their
  ASCAT passes to the C ids. Diagnosis running.

---

## 2026-07-13 (~03:3x UTC) — VP MAPS RE-METHODED: time-mean windows, not snapshots (v2)

**The product was wrong, not the pattern: a χ′ map from ONE fxx=0
analysis is transient-wave noise (±16 bullseyes vs a real anomaly's ±5).
v2 is a TIME-MEAN product** (TAT commit — see git log — generator +
`subseasonal/vp_windows.py` + workflow + page):

- **Rolling daily-χ archive.** Each day = mean of the day's 00/06/12/18Z
  GFS 1° analyses (≥2 required) → ONE T21 solve. The solve is LINEAR in
  wind, so mean-of-χ ≡ χ-of-mean-wind, window means, the bandpass, and
  the divergent wind all derive from stored daily χ (~50 KB/day zlib) —
  test-locked linearity in `tests/test_vp_windows.py` (7 tests). Archive
  lives in R2 `_buildcache/chi_daily_archive.nc`, restored/saved around
  each workflow run; missing days self-heal newest-first
  (`--backfill-days`, dispatch input; 250 bootstraps cold in one run).
  GOTCHA baked into the code: eccodes/cfgrib + pyshtools are NOT
  thread-safe — day fetches run in SPAWNED PROCESSES (a ThreadPool
  segfaulted).
- **Window selector restored**: pentad / 30-day (default) / 90-day /
  20–100-day MJO Lanczos bandpass (Duchon 1979, 121 taps; real-time
  endpoint zero-padded with the retained amplitude fraction PRINTED ON
  the map — 52% tonight; needs ≥61 archived days, refuses honestly
  below). Filenames `chi_anom_{lvl}_{win}.png`; legacy
  `chi_anom_{lvl}.png` = the 30-day default so cached pages keep
  working. Meta-driven buttons on /subseasonal/ (browser-verified:
  default on, swaps, retention note, 0 page errors; falls back to legacy
  images if meta is old/unreachable).
- **Map widened to 60S–60N** (45° clipped the subtropical centers) +
  arrows are now the ANOMALOUS divergent wind (grad of the plotted χ′ —
  linearity again), figsize (18.9, 8.6) for the full-bleed page.
- **VERIFIED against the operational ground truth** (research agents
  pulled CPC's current products; mid-July consensus: negative χ′200
  centered ~130–110W, positive over Africa/IO/MC peaking ~60–90E,
  monthly-mean magnitudes ±5): our 30-day map = ONE clean planetary
  dipole, green cores near the dateline + 130–110W, brown cores ~55E and
  ~120E (exactly CPC's June monthly pockets), NO bullseyes; pentad ±15.5
  ≈ CPC's 5-day products; MJO panel correctly strips the standing El
  Niño wave-1 leaving the weak suppressed-IO MJO CPC describes. 220-day
  archive built locally (2025-12-05..2026-07-12).
- **Climatology swap ERA5 (in flight tonight):** R1-vs-GFS is a
  cross-model baseline mismatch (part of the inflation — 30-day peaks
  ±7.9 on R1). `build_chi_climatology.py` rewritten to ERA5 TRUE monthly
  means 1991–2020 (C3S via APDRC anonymous OPeNDAP — verified access, no
  key needed; ~375 MB one-off), IDENTICAL T21 solve both sides. Build
  running locally (~server-limited); lands as its own commit + re-render
  tonight. If it dies, the shipped R1 fallback stays (a touch hot,
  shape-correct) and the swap re-queues.

---

## 2026-07-13 (~03 UTC) — tester bug board (built + E2E-tested) · edge-cache purge on deploy (built, one token from live)

- **Tester bug board `/bugs/`** (nav-hidden, noindex; direct link only):
  house-chrome page (form + Open/Fixed board) + `workers/bugs-api.js`
  (GitHub-issues-backed: POST files a `tester-report`-labeled issue via a
  SERVER-side durable PAT — testers need no GitHub account; GET shapes the
  board; PATCH close/reopen behind an admin key; `fixes #N` in a commit
  crosses reports off). Anti-spam: honeypot + shared passcode + per-IP/day
  rate limit that uses GitHub itself as the counter (invisible HMAC
  ratekey tag — no KV). **Fully E2E-tested locally** (wrangler dev --local
  against a mock GitHub: validation, honeypot files-nothing, 401s, PATCH
  guard, 8/day limit → 9th refused) and the real PAT proven able to
  create/label/close issues (issue #29, closed). The classifier rightly
  blocked writing the PAT to a dev-vars file — tests ran with dummy
  secrets + a GH_BASE mock override instead; the token only ever flows
  env → `wrangler secret put` (deploy-bugs.sh). **Deploy = Q18** (one CF
  token, or `wrangler login` + `bash workers/deploy-bugs.sh`). Until then
  /bugs/ shows an honest "backend unreachable" and submissions are off.
- **Edge-cache purge on deploy** (`purge-edge-cache.yml` +
  `scripts/purge_edge_cache.py`): on every push to main, diff the push via
  the compare API, map changed repo files to site URLs (index.html → both
  URL forms), WAIT for the Pages build of that SHA, then purge exactly
  those URLs (30/call). Deliberately NEVER `purge_everything` — data
  workflows push to main several times a day and a full purge would flush
  the cdn.* media cache each time. Dry-run verified on the real nav-batch
  push range (35 URLs, correct mapping). INERT (green no-op with a
  ::notice) until `CLOUDFLARE_ZONE_ID` + `CLOUDFLARE_PURGE_TOKEN` exist —
  same Q18 token covers it; acceptance check (~1 min propagation) runs
  the moment it lands. Kills the stale-asset class behind the strobe saga
  + the styles.css nav caveat, and pre-empts false "layout broken" tester
  reports.
- `tests/test_site_nav.py` extended: nav-hidden utility pages (bugs/)
  must render the standard chrome with ZERO active link.
- **~03:30Z UPDATE — deploy attempt per Andrew's ask: BLOCKED, token
  truly absent.** Andrew asked for the end-to-end board deploy on the
  premise the Codespace still had the cyclolab CF token. Verified NOT so:
  the user Codespaces secrets are exactly AWS_ACCESS_KEY_ID /
  AWS_DEFAULT_REGION / AWS_SECRET_ACCESS_KEY / GH_PUSH_TOKEN / PPS_EMAIL
  (listed via the GitHub API), no CLOUDFLARE_* env, no wrangler oauth
  config on disk — the cyclolab worker was deployed from Andrew's own
  `wrangler login` machine (workers/README.md documents it as a user
  action). NOT a missing-permission case; the token doesn't exist here.
  **Q18 = mint ONE token** (Workers Scripts:Edit + zone Workers
  Routes:Edit + zone Cache Purge:Purge) as Codespaces secret
  `CLOUDFLARE_API_TOKEN` → Claude runs `bash workers/deploy-bugs.sh`
  headlessly (no login needed with the env token) + activates the purge
  workflow + reports the tester passcode/admin key in-chat. Done
  meanwhile: passcode placeholder reworded; deploy script now de-boards
  its own smoke issue (strips `tester-report`); capability-check issue
  #29 stripped off the board — testers will see a clean slate.

---

## 2026-07-13 (~02 UTC) — front-end polish: nav sweep + one-line nav + full-bleed /subseasonal/

- **Subseasonal link in EVERY page's nav** (15 files; it only existed on
  Home + the page itself). Same slot everywhere: Models → Subseasonal →
  Recon. New `tests/test_site_nav.py` globs every nav-bearing page and
  asserts the canonical 8-link order + exactly-one-active — the
  hand-duplicated-nav bug class (Satellite, Models, Recon, now
  Subseasonal all went missing this way) now fails tests loudly instead
  of shipping. 17/17 pages green.
- **Nav wraps no more at desktop.** Measured with the real Metropolis
  metrics: brand@28px + 8 links at the old 16px/26px-gap = ~1184px vs
  the 1140px .nav-inner content cap — it wrapped at EVERY desktop
  width. Now 15px/1.1px/18px-gap (~1085px, one line ≥1150px viewport) +
  a new 980–1149 step (13px/22px brand) + retuned 761–979 band (12px).
  Sizing budget documented in styles.css. Playwright-verified single
  line at 1920/1280/1150/1024 with the real webfont.
- **/subseasonal/ full-bleed**: 1240px cap → ~2vw gutters (18–40px);
  page-head follows the same gutter (h1 and section heads share a left
  edge); prose (intro/subs/method note) keeps a 1240px measure; MJO grid
  goes 2fr/3fr with a 460px phase-diagram floor. Measured 1843px content
  at 1920.
- **Renders scaled to match, not CSS-upscaled**: both generators bumped
  1.5x figsize at constant dpi/fonts (phase 1935², amplitude 2362×765,
  χ′ 2835×1035) — same physical text size at the new display width,
  ~2x pixel density. Phase-diagram subtitle offset retuned (1.022→1.012,
  axes-fraction creep on the bigger canvas). Both generators run clean
  locally (BoM RMM fresh 07-11; GFS 07-12 18Z via walk-back — 00Z hits
  an unrelated data.rda.ucar.edu cert failure). Also swept the em-dashes
  the new page/generators had reintroduced into RENDERED text (house
  style). `update-subseasonal` dispatched post-push so R2 serves the
  big renders immediately.

---

## 2026-07-13 (just past midnight) — two follow-on catches while re-verifying SATCON

- **A storm switch now KILLS the in-flight workup** (`53dd0107`, ofx6).
  Observed live: picking 98W while BAVI's auto-run loop was mid-flight let
  BAVI's next frame land in the cleared results (the chain pushes into
  whatever the array is at resolve time) — the TC-Diag board and §4 showed
  ONE stale inland BAVI frame under 98W's name, and this morning's first
  live verify actually blended BAVI's ADT with 98W's MW members. A
  generation token (S.gen) invalidates every pending chain step on switch;
  a dead run can't flip the successor's running flag; the new storm's
  auto-loop starts immediately. Re-verified live: the 98W workup analyzes
  8 real fresh frames (newest 21:50Z) at the correct mid-ocean anchor.
- **§4 refusals state their reason** (`cf73169a`, sc2). A run whose latest
  frame yields no ADT member showed a bare "no consensus". Both real cases
  are honest — inland (Dvorak suspended) and a sheared invest whose Dvorak
  chain declines a vmax — and the tile now says which. Live-verified on
  98W. NOTE: the consensus itself was proven this morning with a real
  3-member render; tonight's refusals are the data being honest, not a
  regression (98W is a sheared 25-kt invest, BAVI is inland).

---

## 2026-07-12 (late evening) — the rest of the block

### 2. BOX FLOATER POLLER STALL — root-caused from code, fixed, tested (tsr main `dff79b1`)

**Root cause (no box access needed):** the global-mosaic disk fetch ran in
a `ProcessPoolExecutor` whose `future.result(timeout=...)` LOOKED bounded —
but on timeout the child keeps running and the `with`-block exit calls
`shutdown(wait=True)`, which blocks the MAIN POLLER LOOP forever behind the
hung s3fs fetch (no total-timeout on a stalled TLS read). A hang is
invisible to `restart:` policies; the container sat "healthy" while
floaters went stale. The GH stopgap never hit it because its 45-min
chained runs are externally reaped — an accidental watchdog the box lacks.

**Fix:** (a) each disk now fetches in a spawned process that is KILLED at
`PER_DISK_TIMEOUT_S` — queue-get with short polls, crashed children
noticed in ~2 s, results racing the exit drained, no wait-on-exit path
left; (b) a process-level stall **Watchdog** (`FLOATER_WATCHDOG_STALL_S`,
default 900 s) hard-exits on any future wedge so `restart:` actually
recovers it — beats at every loop turn, per tick() unit, per basin
backdrop, between mosaic disks. Tests: 14 mosaic (kill-not-wait,
crash-fast, 37 MB payload round-trip) + 32 poller (watchdog) green.
**Q17 is now a pull+rebuild** (RUNBOOK-RENDER §4a), then stopgap retires.

### 3. EXPLORER STROBE — fixed for real this time (TAT `8e81ea32`), verified like a user

Why e76bdedd didn't hold: (a) it shipped behind **un-bumped ?v= stamps**
(4-h Cloudflare edge cache -> users ran mixed stale JS — fixed earlier
today); (b) its full-loop residency didn't survive the 10-min-backfill
era — manifests grew 17→90 frames, so 4-pane mounted ~360 raster sources
and EVERY camera move became a 90-source tile-fetch storm that starved
the visible frame (seconds of partial dark = the strobe Andrew saw).

The rewrite (playback contract rules 4+5, tiled_viewer.js header):
**bounded-loop residency** (trailing 48 frames; 48/36/24 by pane count),
**camera fetch discipline** (during a move all but the on-screen frame
park — hidden with readiness REVOKED — then resume staggered through the
event gate), **live-manifest merge** (90-s background refresh handles the
densifying manifest: preserves the current stamp mid-play, quiet fill, and
flags cleared BEFORE removeSource — MapLibre fires sourcedata
synchronously inside it, the source of a console-error spam class).

**Verified as a user** (real Chrome + live CDN, not one scripted lap):
boot → play → timeline scrub-drag → world→conus domain switch → 3 field
switches → **4-pane compare** → pan+zoom mid-play → **a manifest that
densifies 17→48 MID-PLAY**. Zero un-ready reveals, zero page errors,
dark-pixel fraction never above the parked baseline in any phase. Node
harness extended to 40 checks. Also root-caused+fixed the SATCON
"1 frame analyzed" carry-over: same stale-JS + a starved wp_bt suite.

### 4. emit-geo-global TIMEOUT TREADMILL — broken (TAT `89e2a35c`); wpac-ir healed

e76bdedd's `--step 10 --backfill 90` needed ~175 min/run (measured ~19.5
min per geo slot) vs `timeout-minutes: 110` — every run since 04:03Z was
killed, each successor re-walked an aged-out window, and the wpac/goes-fd
rider emits (sequenced AFTER geo) never ran: **himawari9-wpac-ir froze at
04:00Z for 17+ h** (why the WP BT gate was starving the TC-Diag workup).
Fix: riders run FIRST in their own step (`--backfill 60`), geo gets
30-min slots (the GH runner's real budget; 10-min cadence is the box's
job, Q11), step-level timeouts, cadence arithmetic documented in the
header. Stuck runs cancelled, fresh dispatch on the fixed config:
**wpac-ir newest frame 21:50Z within 20 min** — series live again.

### 5. Coastlines BLACK (tsr main `6dc8ee6`) — Andrew's call, overrides the cyan restyle

COAST/BORDER/HALO all #000000 (constants-only; halo geometry kept so future
restyles stay one-line). 23 render-quality tests green + live-rendered a
China/Taiwan clean_ir frame locally to eyeball the black linework. Goes
live via the stopgap's next tsr checkout and the box's Q17 rebuild.

### 6. /subseasonal/ PHASE 1 — built, rendered, page live (TAT `0a42ae17`)

MJO RMM (BoM; found the FRESH IDCKGEM000 path — the documented graphics/
URL froze in 2024-02; staleness gate + daily cache + a WAF-safe UA):
WH04 phase diagram (8 octants, region labels, 40-day dated track,
eastward=CCW) + amplitude panel. Velocity potential: GFS 1° analysis →
χ at T21 via a spherical-harmonic Poisson solve on pyshtools (pyspharm
has no wheels; solver analytically validated — Y₃² recovery r>0.995 —
and physics-checked: the July climo puts the χ200 min at 15N/132.5E,
textbook monsoon outflow) → anomalies vs a committed 1991–2020 NCEP/NCAR
R1 climatology → Pacific-centered 200/850 maps, BrBG diverging
(green=−χ′), divergent-wind quiver, honest per-level reading lines.
House-style page + daily workflow (15:41Z + backup, R2-only — repo is
NEAR ITS SIZE QUOTA, so no rendered images are ever committed). First
publish run dispatched; current state: phase 7, amplitude 2.15 — a
strong W-Pac MJO consistent with the active season. Subseasonal is in
the home nav; full site-nav sweep deferred. BSISO/QBO/Hovmöllers later.

---

## 2026-07-12 (evening) — SATCON §4 CLOSED · stale-JS hazard fixed

### 1. SATCON consensus tile — LIVE-VERIFIED end-to-end, DONE

MW data is flowing again post-cert-fix (fresh BAVI AMSR2 17:32Z, 98W GMI
20:09Z, all with mwi-v1.0 `intensity{}`; manifest `intensity_model` card
live). Verified three ways:
- **Unit + DOM suites green** (test_satcon.cjs, satcon_dom_smoke.cjs).
- **Pure core against the LIVE CDN data** (node): 98W forms a real
  2-member consensus, MW age-decay factor 0.20 at 4.2 h (curve correct),
  97W near-cutoff at f=0.066, BAVI honestly refuses (every MW record
  land-gated "inner core over land" — it's a dissipating inland TD).
- **Real browser on the live site** (puppeteer, TC-Diag mode, storm
  picker, Analyze loop): §4 rendered **~29 kt ±21 kt · "3 members ·
  experimental"** on 98W — ADT-port 37% + fresh 54-min GMI 51% +
  age-decayed 4.0 h SSMIS 13%, per-member σ/weight/age/caveats table,
  ±10 kt floor respected, full automated/experimental/never-official
  disclosure. BAVI: honest "no consensus" + the V&H §2c reason. Zero
  page errors. Shots: scratchpad satcon_98w.png / satcon_bavi.png.
- MAYSAK/DOUGLAS have no intensity{} — they're DEAD storms (last passes
  Jul 3–5, pre-dating the model). Expected, not a gap.

**Carried finding → the strobe task:** the ADT member's input workup
analyzed only ONE stale frame on the invests ("1 frame analyzed", scene
stamped 2026-07-11T21:03Z) even though the 98W floater manifest holds 70
fresh frames — a loop/workup input-path defect in item-2 territory (the
consensus math above it is verified correct and honest about its "as of").

### 2. SITE-WIDE STALE-JS HAZARD — found + fixed (stamp bumps)

`triple-a-tropics.com` is Cloudflare-proxied and serves JS with
`cache-control: max-age=14400` (4 h, cf-cache-status HIT). Both e76bdedd
(the strobe fix!) and 19fd6f2a (WP BT freshness gate) changed explorer JS
**without bumping the `?v=` cache-bust stamps** — so real users (and my
own headless verify) can run **hours-stale, mixed-version** explorer JS.
That may be part of why Andrew still saw strobing after the "verified"
fix. Bumped: tiled_viewer core1→core2, cockpit core1→core2,
cockpit_fields mwsc1→mwsc2, objfix_sources ofx2→ofx3, microwave.js
11→12 (explorer + standalone page aligned). **RULE REAFFIRMED: any edit
to a `?v=`-stamped file MUST bump the stamp in the same commit.**

### 3. Also observed (feeding the strobe investigation)

- `emit-geo-global.yml` runs are being serially cancelled since 15:44Z
  (concurrency group) — one run in_progress ~2 h. The himawari9-wpac BT
  suite's newest frame is 04:00Z (17 h stale), so the WP BT path is
  correctly gated off; the geo manifest keeps densifying mid-session via
  the 10-min backfill (the prime strobe suspect).

---

## 2026-07-12 (afternoon) — SATCON last mile: PPS cert outage found + fixed; forced re-render in flight

### 0. WHY THE LIVE MW TIER WENT QUIET (root cause, fixed @`8597c30f`)

No MW overpass had landed since **Jul 10 22:19Z** despite green 2-hourly
runs. Root cause: **NASA let the PPS NRT server's TLS certificate expire
at 2026-07-10T23:59:59Z** (jsimpsonhttps.pps.eosdis.nasa.gov). Every NRT
list/download died with SSLCertVerificationError, which
`pps.recent_granule_urls` swallowed per-dir → "0 candidate granules" on
healthy-looking runs for ~40 h.

Fix (tcprimed/pps.py): on a cert-verification failure ONLY, retry with a
context that **keeps full CA-chain + hostname verification** and exempts
only the validity-time check (OpenSSL `X509_V_FLAG_NO_CHECK_TIME`), then
additionally requires the peer cert to match the **pinned SHA-256
fingerprint** of the exact cert NASA is serving — anything else refused.
Strict path always tried first, so the fallback self-retires when NASA
renews (then delete the pin). Per-dir listing failures now print loudly.
Verified locally against the live server (401 auth-challenge roundtrip
through the pinned path; wrong-pin refused). Tests 59/59 + satcon suite
green. **Remove the pin once NASA renews the cert.**

### In flight

Forced `update-tcprimed-live` run 29197070010 (window=48h, force=true) on
the fixed commit: re-lists Jul 10 15Z→now, re-renders BAVI + 97W + 98W
passes **with the mwi-v1.0 per-overpass intensity{}** (build.py computes
it at render time; existing records lacked it because they pre-date the
model landing). Manifest `intensity_model` card already confirmed live on
R2 (14:18Z manifest). Next: verify intensity{} records on R2, then
live-verify the §4 SATCON tile (real consensus if a fresh-enough pass
exists; otherwise the honest awaiting-overpass state + archive-overpass
end-to-end check).

## 2026-07-12 — Explorer loops fixed (strobe + cadence) · Railway → box migration authored

### 1. Satellite Explorer strobe — FIXED, LIVE, browser-verified (TAT main `e76bdedd`)

**What was broken:** every loop on /satellite/explorer/ strobed dark on
playback and most product switches sat on a blank map. Root cause in
`tiled_viewer.js` (the ONE engine every product runs through): the old
keep-window eviction hid frames outside a 12-frame window, and a HIDDEN
MapLibre source requests no tiles so `isSourceLoaded()` reports TRUE on an
empty source — wrap-around "revealed" tile-less frames over the dark
basemap. Switches kept only one retired product → cold refetch → blank.

**The fix (PLAYBACK CONTRACT, documented in the file header):**
① full-loop residency — every in-loop frame's source stays mounted
(opacity 0) for the life of the loop, never hidden/evicted; ② the whole
loop preloads up front (staggered, playback order) with a real
"Loading loop N / M" state — pane overlay at boot, flash toast on
switches; ③ reveals are gated — a frame shows ONLY once its tiles are
event-confirmed loaded, the prior frame holds opaque until then, and an
out-of-order token kills stale scrub reveals. Stamp-keyed state swaps
with the product (two products can share stamps). Riders: MW overpass
swaps in `cockpit_fields.js` now double-buffer (old image holds until the
new one decodes); empty-manifest boot drops the overlay honestly; the
loading toast is owner-keyed so pane switches can't strand it.

**Verification:** new node harness `tests/tiled_viewer_playback_smoke.cjs`
(27 checks: residency, gating, token, preload progress, product-switch
readiness isolation) + a real-browser Playwright run against the LIVE CDN:
global boot → conus ir **90-frame full lap** → ir→truecolor→airmass→ir.
Hard gates all green: **zero reveals of unloaded sources, dark-pixel
fraction never above the parked baseline in any phase**, loading toast on
switches, instant switch-back. An adversarial review of the diff found 2
issues (empty-manifest overlay hang, sticky toast) — both fixed pre-push.
Full suite 509/513 (4 pre-existing `hafs_render` env errors in this
codespace, unrelated). Pages deploys on push — **live now**.

### 2. Loop cadence — frames were ~30–35 min apart (CONUS is 5-min native)

Every emitter wrote ONE scan per pass, so frame spacing = pass duration,
not the data. tsr `s2-sat-ingest@f6b0893` adds `--step MIN --backfill MIN`
slot backfill to `s2_pyramid_emit.py`: each run fills EVERY missing
10-min slot in the trailing window (covered slots skip via ready-marker
+ store re-check; per-slot failure isolation; no flags = old behavior;
test-locked, 13 tests green incl. the legacy suite).
- **LIVE now (GH side), CONFIRMED IN CI:** `emit-geo-global.yml` passes
  `--step 10 --backfill 90` + gained a `:53` backup cron (GitHub shed the
  01–04Z hourly ticks — 4 straight drops observed). Dispatched run
  29179150313 verified mid-flight at 04:56Z: geo/global/ir manifest
  growing (17→20 frames, fresh as_of, first 10-min steps in the tail) as
  it walks the missing slots oldest-first. A cold window may outlive one
  run — harmless, the next tick resumes (per-slot ready-marker dedup).
- **Needs the box (CONUS + full suites):** `docker-compose.s2.yml`
  emit-cron now defaults `--step 10 --backfill 90` (knob
  `S2_CRON_STEP_MIN`, `0`=legacy, `5` chases CONUS native if the box
  keeps up). This rides the SAME Q11 box session already queued:
  `git pull` + `--profile cron up -d --force-recreate emit-cron`.

### 3. Railway → Hostinger box migration — authored, pushed, awaiting Andrew's bring-up (Q16)

Railway paused all 6 tsr services at the $150 compute cap; per the
decision we are OFF Railway permanently. Landed on tsr **main `e98fca9`**:
- **`docker-compose.render.yml`** (project `tat-render`, mirrors the
  S2/meso box pattern): `caddy` TLS edge → `render` (FastAPI /render +
  /export; `Dockerfile.render` = the proven meso base **+ ffmpeg**, which
  nixpacks had but Dockerfile.meso lacks — without it /export 503s) +
  `floater-poller` / `intensity-poller` / `guidance-poller` /
  `ens-watchdog` off one shared image and the box's existing `.env`
  (R2 creds reused, nothing committed). Pollers reach the API over the
  compose network (`http://render:8080`), `GLOBAL_GEOJSON_KEY` parity
  baked in, healthcheck on /health, restart policies + log rotation.
- **HAFS worker: profile-gated OFF by default** — it peaks ~23 GB and the
  June telemetry verdict was "HAFS gets its own box". GH `update-hafs.yml`
  (+ ens-watchdog dispatch once its token lands) stays the HAFS renderer.
  Enable deliberately with `--profile hafs` (builds from the pinned
  `hafs-render-worker` branch, NOT main — main carries the un-approved
  v0.12 repin, Q9).
- **`RUNBOOK-RENDER.md`** — the exact box session (§2), verification
  (§3), stopgap retirement (§4), HAFS notes (§5), Railway teardown (§6).
- **Frontend already repointed** (in `e76bdedd`): all 5 render-host
  literals (`satellite/index.html` ×2, `cockpit.js`, `objfix_sources.js`,
  `microwave.js`) now say `https://render.triple-a-tropics.com`. The four
  dead features (custom-zoom draw-a-box, explorer Time Machine,
  deep-archive objfix, mp4 export) light up the moment DNS + the box are
  live — they are equally dead today either way.

**ANDREW'S HAND-STEPS (Q16, ~15 min):** ① box session per
RUNBOOK-RENDER.md §2 (`git pull` → `docker compose -p tat-render -f
docker-compose.render.yml build && up -d`); ② Cloudflare DNS **A record
`render.triple-a-tropics.com` → box IP, grey cloud/DNS-only** (Caddy does
Let's Encrypt; ports 80+443 open); ③ optionally append
`ENS_WATCHDOG_GH_TOKEN` (GitHub PAT, actions RW on this repo) to the box
`.env` — watchdog logs-only without it; ④ ping Claude or run RUNBOOK §3 —
the CDN-side checks are pollable from here; ⑤ then disable
`floater-worker.yml`'s `schedule:` block (stopgap stays live + idempotent
until then — no rush); ⑥ after a clean week, delete the Railway project.

**Not verifiable from here:** the box bring-up itself (no box access from
the Codespace) — compose validated with `docker compose config`, image
recipe mirrors the proven Dockerfile.meso, but the first real `/render` +
poller run needs the box session. Everything else above is live-verified.

## 🔥 OUTAGE — 2026-07-11 15:08 UTC — RAILWAY PROJECT GONE (mitigated 21:03 UTC)

**Root cause:** the Railway project hosting the `/render` service
(`web-production-b88d.up.railway.app`) AND the always-on workers returned
platform-level "Application not found" from ~15:08 UTC — the app is gone
from Railway's edge (deleted/suspended/billing; only the dashboard knows).
NOT an R2 problem (every published frame fetches 200), NOT the player
(unchanged for weeks), NOT upstream satellites (GOES + Himawari storms all
froze at the same minute).

**Blast radius (all frozen 15:02–15:08Z):** floater frames for every storm
(the reported symptom — loops went stale/choppy/black); the home map's
`global_storms.geojson`; live ACE/tracks feeds (`feeds/*` — basin pages
degraded to their 6-h baked fallbacks by design); CycloLab guidance JSONs;
plus the interactive `/render` API (explorer custom-zoom, objfix WP floater
path, loop export) — the latter CANNOT be stopgapped from Actions.

**Mitigation (live):** TAT workflow `floater-worker.yml` @cb93aca3+abd67735
— chained GH-Actions runs (crons :07/:37, serializing concurrency) boot the
UNMODIFIED FastAPI `/render` on localhost and run the UNMODIFIED
`floater_poller.py` (foreground) + `intensity_poller.py` +
`cyclolab_guidance_poller.py` (background riders) for ~45 min per run.
Restart-safe + dual-writer-safe by the pollers' own design (R2 manifest
resync, content-hash dedupe, atomic per-key puts) — coexists with Railway
whenever it returns. Floater production VERIFIED resumed 21:03Z (BAVI hot
bands ir/irbd first, cold bands following).

**Verified restored (21:03–21:18Z):** floater frames for ALL 4 storms
(BAVI wp09 + cp90/wp97/wp98, GOES + Himawari paths, healthy ~5–8 min IR
cadence, every sampled loop frame fetches 200); per-basin live feeds
(`feeds/wp_tracks_data.json` fresh 21:10); CycloLab guidance
(`cyclolab/JTWC_WP092026/guidance.json` fresh 21:18). The intensity
rider's global-map key needed Railway-env parity
(`GLOBAL_GEOJSON_KEY=global_storms.geojson` — the code default is the
shadow-cutover key) @30d083a3; the home map heals on the current run.

**Secondary (user-reported) also shipped + EYEBALL-VERIFIED:** solid-black
coastlines mudded the storm on rainbow_ir — tsr main @ebcf004 restyles
coast to light cyan + borders/state lines off-white, each over a thin
near-black halo (23 render-quality tests green). Verified on the LIVE
21:20Z BAVI landfall frame: the China coast reads crisply through the
eyewall where the old black stroke vanished into the maroon cold tops
(before/after in scratchpad bavi_{old,new}_coast.png).

**Home map heal VERIFIED 21:22Z:** global_storms.geojson fresh + correct
(BAVI 60 kt @ 28.7N 120.4E — moved/weakened vs the frozen snapshot; cp90
dropped as a dissipated invest).

**Andrew:** Q15 in the queued manual steps — restore the Railway project
(or say the word and it stays on Actions), then re-point `RENDER_API` if
the domain changed, then disable this stopgap's schedule. Until Railway is
back, the explorer custom-zoom, objfix WP floater input, and loop export
(the interactive `/render` API) stay down — Actions can't host those.

**Also fixed (tsr main @ac28a2f): the Time-Machine archive render 500**
("x and y arguments to pcolormesh cannot have non-finite values…",
2017-09-05 GOES-16 CONUS). Early-ABI sectors (GOES-16's 89.5W checkout
slot) carry masked lat/lon INSIDE the sector; the main scalar path passed
them to pcolormesh unguarded while the backdrop path guarded. The backdrop
guard is factored verbatim into `_guard_mesh_coords` and called at BOTH
sites. True Color confirmed unaffected (RGB → imshow; pre-2017-03 archive
truecolor 422s honestly — no silent scalar fallback). LIVE-VERIFIED
locally against real 2017 data: Irma clean_ir 10:15Z + CONUS-edge box +
day True Color all 200 (renders in scratchpad tm_*.png); 6 new regression
tests, 43 render tests green. Deploys via the Actions stopgap's next
checkout; Railway picks it up automatically on restore.

---

## IN PROGRESS — 2026-07-11 — MW-IMAGER INTENSITY + OBJECTIVE CONSENSUS

Building consensus member #2 (PMW-imager intensity from 89/37-GHz PCT) +
the SATCON-method blend (Velden & Herndon 2020) for the TC-Diagnostics board.

- **Landed so far** (all pushed to main, tests green):
  - `tcprimed/mwi.py` @11355611 — SHARED predictor extraction (crop → render
    regrid → PCT ring/sector superset; line-faithful Kieper & Jiang 37-GHz
    ring via the Jiang et al. 2018 Table 2 cyan/pink classes + fitted
    annulus + 90% closure; NE land gate; committed-model evaluator).
    Identical in training and the CI cron — no train/serve skew.
  - `tcprimed/mwi_train.py` + `mwi_fit.py` — offline TC-PRIMED trainer
    (inventory → 6-h thin/stratify → parallel extract → forward-selected
    linear fit, leave-one-YEAR-out validation, error tables by bin/sensor/
    year, provenance-carrying model JSON).
  - `tcprimed/build.py` @2d508db5 — both cron tiers write per-overpass
    `intensity{}` records + a manifest `intensity_model` card (no-op until
    the model JSON is committed; never sinks a render).
  - `satellite/explorer/satcon.js` @3d2c8d86 — the SATCON-method consensus
    (V&H 2020 verbatim 3-member equation, situational-RMSE weights, CIMSS
    3→6 h MW age decay, ≥2-member rule, ±10 kt band floor, departures D1–D8
    documented) + dashboard section 4 replacing the SOON stub; honest empty
    states while the model isn't deployed. `SATCON-METHODS.md` = the
    OBJFIX-METHODS-style provenance doc. Tests: tests/test_mwi.py (16),
    tests/test_satcon.cjs (30+ assertions), tests/satcon_dom_smoke.cjs.
- **Running now (Codespace)**: training extraction over 6,600 TC-PRIMED
  final-tier overpasses (2014–2024, all basins, GMI/AMSR2/SSMIS, ~85 GB
  streamed, restart-safe shards) — restarted after two REAL catches: the
  ring criterion needed per-pixel joint (PCT37, H37) classes, and the
  inherited PCT display-clip [105,290] was flattening the Cat-5 minima
  signal (widened to [50,350]). Plus the 12-agent research workflow
  (4/6 topics reported; SATCON + SHIPS-MI + K&J + TC-PRIMED all verified).
- **Next**: fit + validate → commit `tcprimed/mwi_model_v1.json` → force a
  tcprimed re-render so intensity{} lands on R2 → live-verify the panel.

---

## LANDED — 2026-07-11 ~03:10 UTC — TC-DIAGNOSTICS DASHBOARD + HOVMÖLLER + DAV

**The headline feature is live** (TAT @82135cdb + wiring): TC-Diagnostics is
now a full STORM-ANALYSIS WORKSHEET — stage splits imagery | board; picking
a storm auto-runs the loop workup; §1 objfix docked wide (scene | stats), §2
IR Hovmöller, §3 DAV, §4 honest SOON tiles (SATCON / WN-1 / eye-CDO /
sat-intensity-fixes / env / GLM).

- **IR Hovmöller** (Kossin 2002; Dunion et al. 2014; Ditchek et al. 2019):
  azimuthal-mean BT per 10 km ring about each frame's OBJECTIVE center,
  computed INSIDE the objfix worker while the BT grid is alive (loop-memory
  rule intact). Coverage-gated rings (never interpolated), low-confidence
  columns dimmed + amber-flagged, true time axis with honest gaps, PNG
  export, TM-scrub column highlight. Rides Track JSON as a `hovmoller` block.
- **DAV** (Piñeros/Ritchie/Tyo 2008/2011/2012/2014; Hu et al. 2020): Sobel
  gradient DIRECTIONS vs radial, folded ±90°, sample variance deg² on a
  ~10 km work grid; published regime bands + uniform-random 2700 line;
  trailing 24-h mean; **no DAV→intensity sigmoid** (would overstate skill).
  `dav_deg2` rides Track JSON per point.
- **VERIFIED — Irma 2017 archive workup** (17 frames, 2017-09-05 ±12 h, via
  the NEW native-era btpng): Hovmöller shows the cold inner core through the
  cat-5 RI day; DAV mid-window ≈1000 deg² sits in the published
  hurricane-typical band with the trailing mean FALLING (organizing) through
  the RI — and the window-edge frames flag themselves low-confidence (the
  archive anchor is the view center; a mover drifts off it at the edges —
  known v1 limit, honestly flagged). Shots:
  `_shots/tcd_irma17_hovmoller.jpg`, `_shots/tcd_irma17_dav.jpg`.
- **VERIFIED — BAVI live**: worksheet + objfix workup render correctly
  (`_shots/tcd_worksheet_bavi_live.jpg`); the LIVE loop time axis needs the
  calibrated-BT series to accumulate — the hourly emit workflow now also
  emits `himawari9-wpac-ir` + `goes19-fd-ir` (2 frames each already), so
  Hovmöller/DAV on live storms fill in over the next hours automatically.
- **format=btpng for the native ABI era** (tsr main @92d4483, live on
  Railway): GOES `fetch_regular` regrids one band to a regular lat/lon grid
  (the truecolor pattern) — per-frame objfix/Hovmöller/DAV now work for the
  ENTIRE 2017+ archive (Irma verified end-to-end), not just pre-2017 tiers.
- Unit tests: `node tests/test_diag_core.cjs` (axisym vortex → DAV≈0; noise
  → ≈2700; eyewall-ring recovery; coverage honesty) — all green.
- Gotcha for future sessions: the render service rate-limits 10 req/min/IP —
  do NOT probe it while a TM window or archive workup is loading (that's
  what emptied the first Irma attempt's window).

---

## LANDED — 2026-07-11 ~02:30 UTC

- **World composite FIXED + LIVE-VERIFIED**: the emit-geo-global workflow's
  first run succeeded — fresh frame `20260711T005020Z` with ALL THREE members
  (GOES-E 00:50 · GOES-W 00:50 · HW-9 00:50, listed in the manifest's
  `members[]` and shown in the pane header). Hourly cron keeps it fresh.
  Shot: `_shots/world_geo_ring_fixed_3sat.jpg` — seamless Americas + WPac
  (BAVI's eye visible), honest labeled Meteosat gap awaiting the Q13 key.
  The stale half-world 07-10 frame stays in the loop honestly and ages out.
- **GridSat-GOES deep tier SHIPPED** (tsr main `d11966b`, Railway
  auto-deploys /render): Time Machine 1994-10..2017-12 is now PER-SATELLITE
  ~4 km HOURLY with visible + 3.9 µm + WV + IR window — ordered tier ladder
  (GridSat-GOES → MergIR → GridSat-B1) with resolve-time fall-through and
  honest per-tier headers. Frontend era gating: TAT `2e423c3d` (hourly
  windows + c02/c07 unlocked in the GOES era). Resolution proof:
  `_shots/tm_katrina05_gridsat_goes_4km.jpg` (GOES-12 pinhole eye) vs
  `_shots/tm_katrina05_gridsat_b1_8km.jpg`.

## IN FLIGHT — 2026-07-11 (core-fix pass per Andrew's priority messages)

Order per Andrew: ① core explorer fixes ② Meteosat global-gap fill (URGENT)
③ deep-archive native overhaul (research-first) — TC-Diagnostics dashboard
build is PAUSED (WIP committed @7d0096ad, honest placeholders; Hovmöller+DAV
research is done and banked).

### World composite — ROOT-CAUSED, fix chain landed
The "blob + duplicate coastlines" World view: **the data pipeline was
correct** (proved: Andes anchor at −67.9/−68.7/−69.2 in the live bt.png;
local tile-cutter reproduces the CDN tiles at IoU 0.998; mercator addresses
exact). The real defects: the ONE frame on R2 (2026-07-10 03:40, emitted
once — box cron not running) is missing its **Himawari lobe**: suite emits
pin the scan to the GOES anchor, and the AHI loader TRUSTED the pin — a
mid-upload/housekeeping FLDK slot failed the whole member (honest degrade →
half a world). Fixes:
- tsr `3ef26d1` — AHI pinned-slot fallback to the newest COMPLETE slot
  (himawari-suite pins pre-verified → byte-identical there).
- tsr `7359e5f` — **Meteosat SEVIRI ring members** (0° HRSEVIRI + IODC
  45.5°E), REST Data Store client, licence-compliant ≥60 min delay,
  creds-gated (no key → wedge stays the honest labeled gap). Manifest gains
  `members[]` (per-satellite valid times — the Meteosat skew is surfaced).
- TAT `92c9b64f` — **emit-geo-global workflow**: hourly GH-Actions stopgap
  emitter (R2 secrets live there) until the box cron runs. First run in
  flight → the World view gets a FRESH GOES-E+W+Himawari frame today.
- TAT `e0b43a7a` — viewer core pass: product switches never flash (outgoing
  frames stay until incoming tiles land; last product kept resident =
  instant switch-back), per-product zoom-out pinning (CONUS↔World both
  correct now), manifest cache, world-copy hygiene, lost-view guard,
  members-driven gap badge + per-member valid-time chrome.

### QUEUED for Andrew (one-time, when back)
- **EUMETSAT key** (lights Meteosat in the World ring): free account at
  user.eumetsat.int → API key at api.eumetsat.int/api-key/ → accept the free
  "Meteosat L1 ≥1 h latency" licence → add `EUMETSAT_CONSUMER_KEY` +
  `EUMETSAT_CONSUMER_SECRET` as TAT repo Actions secrets AND to the box
  `.env`. Everything else is wired (satpy installs in the workflow already).
- **GH_PUSH_TOKEN as a TAT Actions secret** was NOT created (secret-store
  writes are yours to make; turned out unnecessary — tsr is public).
- Box s2 emit-cron session (existing Q11-family step) — once running,
  disable the GH emit cron (keep dispatch).

### Deep-archive research (Andrew's 3rd priority) — DONE, build pending
Native GVAR (1 km) is NOT render-on-demand anywhere public (CLASS =
order-staged; SSEC McFetch = .edu-licence-incompatible). The honest tier:
**GridSat-GOES** (NCEI, direct HTTPS, VERIFIED with Katrina GOES-12
2005-08-28 18Z, 63 MB netCDF): 4 km / HOURLY / per-satellite / 6 channels —
vs GridSat-B1's 8 km / 3-hourly / blended. Meteosat archive: SEVIRI full
archive (2004+) + MVIRI FCDR (1983+) on the Data Store with the same free
token. Era-by-era build next.

---

## OVERNIGHT REPORT — 2026-07-10 (Andrew asleep; autonomous)

### LANDED (newest first, all pushed)
- **TM draw-box foundation + archive render queue** — TAT `c25effab` +
  shots `d0cf60c8`. A drawn AOI is the archive crop for ALL panes (box the
  storm → scrub it); no box = the first render's extent FROZEN for the
  session (this also fixed the self-resizing archive pane — it was viewport
  drift changing each request's aspect). EVERY archive render goes through
  one serialized queue with de-dupe + cache (same band+time+box = one render,
  reused instantly), 429 back-off/retry, concurrency 1. Per-pane band picks
  in TM (box+time shared; a switch re-renders just that crop cache-first and
  lazily refills that pane's window). Archive coverage: Clean IR / C14 IR
  window (honest map to the archive's ~11 µm channel) / Dvorak BD / 6.7 µm
  WV; vis+RGB grey pre-2017 as before. Live playback decode-ahead (next 2
  frames' tiles mount invisibly; the flip is a pure opacity toggle).
  **ACCEPTED on Irma 2017-09-05 18Z, 4-pane** (Clean IR / BD / WV / True
  Color, boxed, scrubbed): 4/4 panes filled, 0 blocked, 0 failures — the 2
  raw 429s the limiter threw were absorbed by retry. Shot:
  `_shots/tm_irma17_4pane_boxed.jpg`.
- **main → s2-sat-ingest MERGE (veg-green skew fixed)** — tsr `fd98aa96`.
  20 conflict hunks resolved keeping BOTH lineages' behavior: box branch's
  custom rate limiter (+ /export converted to it), header_get +
  second-precision frame keys, antimeridian gridliner (now behind main's
  gridlines toggle), concurrent truecolor band-resolve (+ main's downsample
  threading), multiband AHI slot prober. Suite: **929 passed**. Veg-green
  VERIFIED on a fresh wpac truecolor emit: Borneo/Sumatra forest green
  (`_shots/veg_green_merged_branch.jpg`). tat-palettes stays pinned v0.2.2.
- **emit-cron foolproofed** — tsr `84da1685`: bare `compose up` now runs the
  FULL suite set (conus fd himawari9-wpac himawari9-fd geo-global); env still
  overrides.
- **Rail accent** → real site teal #49b6c8 — TAT `47cf4821`.
- Earlier tonight: rails redesign, MergIR tier (Q12-gated), TM multi-pane
  honesty, global-default composite, TM deep archive, unified header,
  Himawari-9 suite, TC-Diagnostics — see the sections below.

### IN PROGRESS / HONEST GAPS
- Live-loop jank: decode-ahead landed; real-browser feel needs Andrew's
  eyeball (headless can't measure smoothness). If still janky, next lever is
  pre-warming the whole window on play start.
- Storm-following archive box: needs a per-time historical track source
  (HURDAT/objfix chaining is banned as first-guess) — designed, not built.
- TCD SOON diagnostics (DAV/WN-1/eye-CDO/Hovmöller): scaffolded, archive
  hook contract ready, not implemented.

### ANDREW QUEUE (box + secrets; full detail in the memory queue file)
1. **Box session (Q11, updated):** in the tsr dir — `git fetch && git
   checkout s2-sat-ingest && git pull` (now INCLUDES the main merge + cron
   default), `docker compose -p tat-s2 -f docker-compose.s2.yml build emit`,
   then `--profile cron up -d --force-recreate emit-cron` (no env edit
   needed anymore). **For imagery NOW before the cron warms:** one-shots
   `run --rm emit --suite himawari9-wpac --store r2 --prefix shadow
   --max-zoom 5` and the same with `--suite geo-global` light the Himawari
   domains, the global default, and objfix's WP real-BT path immediately.
2. **Q12 Earthdata:** authorize "NASA GESDISC DATA ARCHIVE" on the profile,
   re-run the `fetch-mergir-sample` workflow (green = fixed), add the
   credential to the Railway render env → MergIR serves 2000-2017 at 4 km.
3. Q9/Q10/Q8/Q4 unchanged.

### BLOCKERS
- MergIR live verification blocked on Q12 (GridSat serves those dates
  honestly meanwhile). Nothing else blocked.

### HEALTH
- tsr main deployed tiers live via Railway auto-deploy (confirm one pre-2017
  render once it cycles). Box branches untouched since the last box session;
  the merged s2-sat-ingest is ready for Q11. All suites green at push time
  (TAT 496−4 pre-existing env; tsr s2 929; tsr main 692+21). Feeds normal.

---

## LANDED 2026-07-10 (overnight batch 2)

### 0a. Himawari-9 as a FULL suite satellite — SHIPPED both repos, locally live-verified

The roadmapped "BT feed → full Himawari suite" step. tsr `s2-sat-ingest`
@ef3a983 + TAT @c8bba3e0 (+ products.js regen). "Add a satellite = registry
rows" held: 28 wpac + 27 fd tiled products (all 16 AHI bands, B02 native
green, NO fake cirrus; True Color/Sandwich/Air Mass/Dust/Ash/Day Convection/
Natural Color/Snow-Fog/Fire Temp/Day Cloud Phase/Night Micro; BT raster per
emissive band) over the EXISTING recipe engine/pyramid/Q7/prune plumbing.

- **AHI recipes are first-class, JMA-verified** (17-agent verification pass
  against JMA MSC Quick Guides Ver 1.0 + Tech Note 65): JMA RETUNED the AHI
  thresholds (Murata & Shimizu 2017) — dust/ash/nightmicro/airmass/
  dayconvection/daycloudphase/firetemp/daylandcloud all differ from BOTH
  SEVIRI heritage and the ABI guides; every number test-locked
  (`tests/test_s2_ahi.py`, 23 tests). Snow-Fog ships ABI-heritage renumbered
  (JMA's exact blue gun is a derived 3.9 µm solar-reflectance product — a
  documented departure, not forced). JMA's printed tables use flipped
  difference orders — verifiers decoded sign conventions against the guides'
  own imagery; do NOT re-transcribe naively.
- **Infra:** AHI band-disk fetch off ONE complete FLDK slot (per-sector
  strides keep a full-disk B03 at ~60 MB instead of ~1 GB), shared-trig
  sampling with CHUNKED interpolation (one-shot scipy RGI = ~2 GB transient,
  proven to thrash), antimeridian-aware webmerc (fd bbox 60→221°E, dual
  x-ranges, wrap-aware sampling; MapLibre world copies on hw-fd).
  `--suite himawari9-wpac|himawari9-fd`; emit-cron iterates `S2_CRON_SUITES`.
  FD excludes truecolor (B03 disk budget, mirrors goes19-fd); wpac truecolor
  renders at the 2 km-class raster (Rayleigh at 0.5 km class = minutes/GBs).
- **Cockpit:** Himawari-9 satellite + W Pacific/Full Disk domains, FIELD rail
  swaps to the AHI set, availability per-domain off its own products.json
  (satellite/domain rows self-enable when the box emits — greyed until then).
  C##↔B## field mapping on cross-sat switch; TM refuses himawari domains
  honestly (GOES-East archive only, for now — see deep-archive spec below).
- **objfix WP cutover:** WP storms consume the wpac suite's calibrated AHI
  B13 `bt.png` (2560 px ≈ 3.7 km, `bt_px` registry knob); rainbow_ir LUT
  inversion RETIRED for WP when the suite manifest is live (labeled fallback
  until box emit + for CP). Anchors stay floater box centers. **BAVI
  before/after** (real FLDK scans, local emit): center Δ ~7 km; calibrated
  BT reads colder tops (−68.5 vs −65.0 °C cloud) → T# 3.5/~55 kt vs
  3.3/~51 kt; ARCHER conf dropped (0.34→0.08 — 3.7 km input is coarser than
  the 1.5 km floater pixels; honest tradeoff, both labeled low; bt_px can go
  higher later if the box budget allows). Also fixed a LATENT fd-path decode
  TypeError (`id.data.data`) that never fired live because every storm to
  date had a floater. Shots: `_shots/himawari_wpac_truecolor_airmass.jpg`,
  `_shots/objfix_bavi_before_lut.jpg` / `objfix_bavi_after_ahi_b13.jpg`.
- **KNOWN SKEW (pre-existing, surfaced by verification):** tsr
  `s2-sat-ingest` does NOT contain main @74f9298 (veg-green both sensors) —
  suite truecolor renders the pre-veg-green look until main is merged into
  the branch. Queue a `git merge main` on s2-sat-ingest as a follow-up.
- **BOX STEP QUEUED** (lights everything): see the runbook line appended to
  the queued-manual list — `git pull` on s2-sat-ingest, rebuild the tat-s2
  image, set `S2_CRON_SUITES="conus fd himawari9-wpac himawari9-fd"` in
  `.env`, restart emit-cron. The cockpit + objfix WP path self-enable off
  the manifests the moment the first himawari suite emit completes.

### 0b. TC-Diagnostics MODE scaffold + objfix panel polish — SHIPPED @827debc9 / @ac41d47c

Third cockpit mode alongside Live/Time Machine: storm selector drives a
per-storm dashboard; anchor pane frames the storm with the objective center
marked; Obj Fix docks as card #1 (same DOM node, state survives); 8 greyed
SOON cards (Sat Intensity Fixes, SATCON, DAV, WN-1, Eye/CDO, IR Hovmöller,
Env Favorability, GLM) — scope visible, nothing faked. Panel fixes: scene
header per the burned-in convention (provenance line + watermark + caveat
badge, no stacking) + grouped stats (Center & Fix / Intensity / Scene &
Structure). Live-verified on BAVI; shots in `_shots/`.

### 0c. Unified archive-grade burned-in header + multi-pane TM/TCD — SHIPPED @fdf9146a

Live tile panes carry the archive render's EXACT header (centered
SAT·INSTRUMENT·CHANNEL·VALID UTC, right product·palette tag, per-pane
watermark+attribution, REAL viewport min/max BT off the calibrated bt.png,
colorbar) — no lesser overlay; exports draw the same layout; compact at
2/4-pane (shot: `_shots/unified_header_4pane.jpg`). Time Machine loops load
per-pane on every servable pane; TCD frames the storm on all panes.

### 0d. TIME MACHINE DEEP ARCHIVE to 1980 — SHIPPED (tsr main c616602+86716a7f → Railway auto-deploys the live /render; TAT @0d896f59)

- **GridSat-B1 tier** (`gridsat.py`): explicit-time /render requests before
  2017-03-01 (previously 502) serve the NOAA CDR (noaa-cdr-gridsat-b1-pds,
  3-hourly, ~8 km, GLOBAL, 1980→present; lazy S3 range-crops, wrap-aware).
  11 µm IR window + 6.7 µm WV only; multi-band 422s honestly. Era header
  baked: "GridSat-B1 · 11 µm IR window · 3-hourly · ~8 km". `format=btpng`
  returns the frame's calibrated BT (u16, suite-encoding) — deep tier only
  (ABI's curvilinear grid would misregister the linear decode). ADDITIVE:
  live/2017+ paths untouched; full main suite 692 green. MergIR (2000-17,
  4 km/30-min) DEFERRED (GES DISC Earthdata auth) — GridSat serves those
  dates honestly labeled; noted as the future middle tier.
- **Cockpit:** date picker to 1980; pre-2017 flips `cx-tm-deep` (IR-only
  fields; RGBs grey "not available before 2017"); deep era skips GOES disk
  clamps. Render auto-builds a **12 h scrub window +6 h buffers**
  (center-out render-ahead, rate-limit paced, time-sorted — the timeline
  scrubber drags real archive frames while it fills); loop export downloads
  the window with the era header baked in.
- **Per-frame TC-Diagnostics:** TCD now COEXISTS with TM; every scrubbed
  frame recomputes objfix from GridSat calibrated BT (ArchiveSource →
  btpng; first guess = view center, stated; independent single-frame
  estimates with the full honesty ladder). `TCDiag.onArchiveFrame` is the
  archive-capability contract for every future diagnostic (DAV/WN-1/etc
  remain SOON cards, to be built archive-capable from day one).
- **Verified end-to-end on Hurricane Gilbert 1988-09-13** against a local
  backend: 888 mb eye rendered, era gating flips, window fills, scrubbing
  recomputes the center per frame (18Z → 21Z track motion visible), archive
  loop downloaded. Shots: `_shots/tm_gilbert88_scrub_objfix.jpg` +
  `tm_gilbert88_gridsat_render.jpg`. NOTE: the LIVE Railway /render picks
  the tier up on its auto-deploy from main — poll a pre-2017 render to
  confirm once deployed.

---

### 0e. GLOBAL-DEFAULT EXPLORER — geo-ring composite SHIPPED (tsr s2 @e277fc78 + TAT @b96a187b)

The cockpit now OPENS ON THE WORLD: GOES-19 + GOES-18 + Himawari-9 full
disks stitched nadir-nearest (10° BT cross-fade, 65° limb cutoff) into one
global webmerc pyramid (`sat/geo/global/{ir,irbd,wv}`); satellite/domain/
region controls are drill-downs from it. Meteosat sector = honest
transparent gap + dashed 'no ingest yet · coming' map badge (never
stretched). BT fields only on the global view (BT blends across sensors);
RGBs stay per-satellite, manifest-gated. Global 2560px BT raster feeds the
inspector. Q7 z0-5 cron + shape-driven prune as the other suites.
Live-verified on a full 3-disk local emit: seamless E/W overlap, BAVI on
the Himawari side, honest gap (shots `_shots/global_default_meteosat_gap.jpg`
+ `global_composite_z2_tiles.jpg`). Auto-default waits for pane readiness
and yields to any user steer. BOX: add `geo-global` to S2_CRON_SUITES (Q11).

### 0f. MergIR tier + TM multi-pane honesty — SHIPPED (tsr main e518e548+b63fd29c, TAT da8b6290)

**MergIR (2000-02→2017, 4 km/30-min):** access VERIFIED — NO anonymous path
exists (the AWS Open Data 'nasa-gpmmergir' listing = the CONTROLLED
requester-pays GES DISC bucket; PPS doesn't mirror the Tb granules). Tier
implemented against GES DISC HTTPS with Earthdata creds from env; the
best-available selector prefers MergIR for clean_ir ≥2000-02 ONLY when it
can actually fetch — otherwise GridSat serves the date HONESTLY (verified
via HTTP: 2005 render → X-Satellite: GridSat-B1). Era header 'NASA MergIR ·
11 µm IR window · 30-min · ~4 km'. WV + pre-2000 stay GridSat. 21 tests
(synthetic merg-granule decode/crop seam; live Katrina test creds-gated).
**BLOCKED ON Q12** (Earthdata account: authorize 'NASA GESDISC DATA
ARCHIVE' app, re-run the fetch-mergir-sample workflow, put creds in the
Railway render env) — then the Katrina 4 km re-render verifies itself.
Deferred by decision: native GOES archives (NOAA CLASS/AWS) for pre-2017
Americas.

**TM multi-pane honesty (bug fix):** entering TM in 4-pane no longer leaves
non-servable panes showing LIVE imagery beside the archive. Every pane
participates: servable panes render at the scrubbed time (Render + window
builder now cover ALL servable panes, per-pane frame lists, stamp-matched
followers); the rest BLOCK with the honest reason (RGBs pre-2017, MW/ASCAT
live-only, era-absent channels e.g. 1992 WV '35% valid — VAS era' via a
paired backend coverage message). Verified 4-pane on Andrew 1992-08-23 18Z
(shot `_shots/tm4_andrew92_honest_panes.jpg`). NEXT IN FLIGHT: rail
redesign (blue-chrome register, real-LUT swatches, ring provenance).

### 0g. Rail redesign — blue-chrome channel register SHIPPED (TAT + tsr e7ff90cd)

Rails restyled to the approved mockup (restyle/reorg ONLY — imagery, plate,
burned-in header, toolbar, panes, widths all untouched): the site nav's REAL
navy chrome gradient + bevel on both rails; active states in the mockup teal
#35d0a5 (no teal existed in site CSS — flagged). LEFT = channel register:
spectral-region subheaders (VISIBLE/NIR · WATER VAPOR · INFRARED; COMPOSITES
· MULTISPECTRAL RGB), band-ordered rows with REAL-LUT palette swatches
(horizontal ramps generated from the frozen tat_palettes cmap+norm; discrete
Dvorak renders discretely; RGBs get quick-guide hue dots, never fake ramps).
RIGHT: satellite ring with orbital provenance (75.2°W / 137.2°W / 0° /
45.5°E / 140.7°E) incl. quiet Meteosat-0° + IODC 'soon' rows; domains
World · Full Disk · CONUS · Meso; availability honesty kept, chips quiet.
Shots: `_shots/rails_redesign_channels.jpg` + `rails_redesign_rgb.jpg`.

## LANDED tonight (2026-07-09 evening → 07-10)

### 1. Objective center + intensity (ARCHER/ADT) — SHIPPED, live-verified on BAVI + 97W

On the explorer dev route (`/satellite/explorer/`, still unlinked+noindex).
Commits: spec §D addendum @f2b294 → compute module @ca3bb4 → panel+sources
@a2f22d → loop-memory fix @116683. Eyeball crops (also in repo):
`satellite/explorer/_shots/objfix_bavi_loop.jpg` + `objfix_97w_degraded.jpg`.

- **Faithful, not fast:** `objfix.js` is a line-faithful PORT — ARCHER from
  ajwimmers/archer @ d09f5c7 (log-compressed-gradient spiral score ×15−20,
  cube-root ring score ×250 then 0.0167, LINEAR 0.33/deg penalty, no-penalty
  prominence → per-sensor alpha → real 50%/95% certainty radii, quality
  gates + weak-center demotion, feature/surface ladder) and ADT v8.x from
  the SSEC McIDAS-V Java (ring/sector/FFT stats, exact scene-score cutoffs,
  10° log-spiral curved-band, BD-category base-table Raw T#, Rule 8 clamps,
  3 h Final T#, Rule 9 CI + rapid-diss, full 0.1-step Dvorak tables). Every
  UNCONFIRMED in the frozen spec was resolved from primary source pre-build
  (two spec corrections recorded in §D); the 7 unavoidable departures are
  D1–D7 in the file header, each flagged inline. Unit tests
  (`tests/test_objfix.cjs`): synthetic-vortex center recovery <0.15°,
  eye/shear scene classification, Rule 8/9 behavior, table exactness.
- **Data paths per spec:** AL/EP = fd `bt.png` (calibrated u16, ~14 km,
  labeled); WP = floater WebP **LUT inversion self-calibrated from each
  frame's own baked colorbar** (layout from tsr render.py axes rects,
  graticule-verified ±2 px; chrome/coast pixels masked + median-filled,
  labeled DEGRADED PRECISION). First guess per frame = the floater box
  center (official-track anchor) — chaining ARCHER's own fixes measurably
  drifted (22.9°N vs 20.5°N on BAVI) and is banned in-code.
- **Panel** (Obj Fix · beta, toolbar): honesty banner AUTOMATED OBJECTIVE
  SATELLITE ESTIMATE / not official / see NHC-JTWC; scene canvas with solid
  crosshair fix, faint rejected-candidate crosshairs, dashed weak center,
  r50/r95 circles, eye ring; confidence tier + km radii + eye probability;
  scene type + ADT skill tier (eye r≈0.70 / cloud r≈0.50); Raw/Final T#,
  CI, ~Vmax, ~MSLP (table, unadjusted; Pacific column for WP per the ADT
  source); Raw/Final/CI **trend chart** over a 26 h loop; center-track JSON
  (download + `window.ObjFix.tracks`) = the Hovmöller's input. Compute runs
  in a Web Worker (~2 min for a 41-frame loop).
- **Live results:** BAVI 41-frame loop → fix 20.48N 127.30E, conf 0.66
  (r50 23 km), UNIFORM CDO, CI 3.9 → ~63 kt / ~978 mb vs official 90 kt —
  the expected ADT no-eye-CDO low bias, surfaced as "skill: low" + degraded
  input. 97W invest → POOR FIX (gates rejected → weak center, dashed),
  SHEAR scene, r95 253 km, numbers stamped "UNRELIABLE (poor fix)" — the
  degradation path working as contracted.

### 2. MW + ASCAT as NATIVE cockpit fields/layers — SHIPPED, live-verified

The `?embed=1` stage takeover is retired (`#cx-embed` deleted). Eyeball
crops: `_shots/mw_91h_pane.jpg`, `_shots/ascat_layer_over_ir.jpg`,
`_shots/ascat_pane.jpg`.

- **Fields:** MW (37/91 color, 37H/91H, smoothed/raw) and ASCAT winds are
  pickable per pane like any field in 1/2/4-pane: MW overpass tiles are
  georeferenced MapLibre image sources (smoothed/raw = raster resampling),
  ASCAT barbs a camera-synced per-pane canvas overlay — pan/zoom/linked
  cameras/time-lock all apply (the clock pulls MW panes to the
  nearest-in-time overpass unless pinned).
- **Layers:** "MW pass" + "ASCAT winds" in the Overlays rail layer onto the
  ACTIVE pane's base field (91H over Clean IR, barbs with dark halos over
  IR) with a burned-in provenance badge; PNG/WebM exports composite both.
- **Re-hosted, not rebuilt:** the legacy engines now export their
  primitives (`MicrowaveViewer.PRODUCTS/tileRel/boundsOf`,
  `AscatViewer.STYLES/KT_SCALE*/drawBarb`) and the cockpit adapters
  (`cockpit_fields.js`) consume them; per-pane controls live in the rail
  tabs (MW: storm/overpass/product/display; ASCAT: view/pass/density/style,
  **high-contrast default**). Standalone pages unchanged for the below-fold
  section.
- Verified live end-to-end (puppeteer, zero pageerrors): field modes, layer
  modes, per-pane controls, chrome/legends, reset regression, exports path.

Also: `tests/` suite green except 4 pre-existing HAFS errors (installed
tat-palettes 0.1.0 lacks `era5_isotach_cmap` — environment, not code) and
the stale models asset-stamp failure which I fixed @bb7ee1. CLAUDE.md
gained the objfix/MW-ASCAT gotchas. **Next up (queue):** the Hovmöller can
now consume `ObjFix.tracks`; MW-channel ARCHER (constants already in hand)
is a natural follow-on.

---

## MY QUEUE (Andrew's hands / decisions, ordered)

**① Box (~1 min) — pull for the prune + Snow-Fog:** the emit-cron is already
running (loop is backfilling on its own) and you ran the fd suite — the ONE
remaining box step is:

```bash
git pull      # s2-sat-ingest, in the tsr dir
docker compose -p tat-s2 -f docker-compose.s2.yml --profile cron up -d --build emit-cron prune-cron
```

This starts `prune-cron` (the object-level shadow TTL that replaces the
permission-blocked bucket-lifecycle — 14 d, keep-min 2/product) and picks up
**Day Snow-Fog** (its greyed picker entry lights on the next emit).

**② Decision — history hygiene, optional follow-on:** main's history rewrite
is DONE and verified (see LANDED). Residual, outside last night's main-only
scope: ~20 stale June feature branches still carry the old shipped-file
third-party names in their historical blobs (NOT the planning artifacts —
those only ever existed on main and are fully gone). Say "sweep the branches"
and the same replace-text pass runs per contaminated branch (bundle-based,
cheap); or accept them as low-risk residue (stale branches, no artifact
files). Also optional: ask GitHub support to force-GC orphaned pre-rewrite
SHAs (they stay fetchable by direct SHA until then). Forks: ZERO, so no fork
residue exists.

**③ Decision (art): HAFS env-color v0.12 on the live worker** — say go and
Claude repins `hafs-render-worker` hafs-render v0.11.0→v0.12.0 (Railway
auto-rebuilds; restyles the 9 env products to the palette look on main).

**④ Decision (art, month-old): TCHP records hatching** — crops at
`https://cdn.triple-a-tropics.com/sst/records/review/<region>_tchp_anom.png`.
Go (merge + disclosure + temp-workflow cleanup) or park.

**⑤ One-clicks, LOW:** close stale [PR #24](https://github.com/WeathermanAAA/Triple-A-Tropics/pull/24)
(shipped via main; permission gate blocked Claude closing it); optional $10
AWS budget alarm (console/root only). NOTE: PR #24 and the stale June
branches reference pre-rewrite history — harmless, but closing/deleting them
is tidier post-rewrite.

## LANDED overnight (with SHAs)

- **HISTORY REWRITE — done and verified** (2026-07-09 ~05:30Z, per the
  overnight go): `git filter-repo` over all 2,254 main commits — the 16
  planning-artifact paths removed from every commit, 21 replace-text rules
  scrubbed the third-party names from all remaining blobs AND commit
  messages. A cron commit that raced the rewrite was cherry-picked on top;
  force-pushed (old tip `730bd656` → new `6ea658f0`). **Verification in the
  rewritten history: every name pickaxe = 0; every removed file's log =
  empty; HEAD grep clean.** Backup: `/workspaces/_backups/TAT-main-pre-expunge.bundle`
  (97 MB, pre-rewrite main; also on /tmp) + this Codespace's old objects
  (left un-gc'd). Pages redeployed from the rewritten main; site + explorer
  200. Caveats flagged: orphaned old SHAs remain fetchable on GitHub until
  support GC; zero forks exist; stale-branch residue = queue ②. tsr HEAD was
  also scrubbed earlier (`731bbef`).

- **Objective center + intensity — research phase DONE, spec committed**
  (`cadefb7`, `satellite/explorer/OBJFIX-METHODS.md`): ARCHER (Wimmers &
  Velden 2010/2016 + the author's released reference code — 5° spiral score,
  ring score with IR weight 0.0167, first-guess penalty 0.33, 0.75°
  peak-prominence confidence, gamma error CDF, quality gates) and ADT
  (Users' Guide v8.2.1 read in full — 24 km eye / 24-136 km cloud annulus
  geometry, BD categories, v8.x scene regressions, Dvorak CI→Vmax table,
  Rule-8/9 time constraints, per-scene skill). Unverifiable constants are
  marked UNCONFIRMED in the spec rather than guessed. Data path established:
  fd `bt.png` rasters for AL/EP; rainbow_ir LUT inversion (norm −95→40 °C,
  verified) for WP floaters incl. BAVI; first guess from the live feeds.
  Implementation = next session against this frozen spec (accuracy-critical;
  not winged at 5am). Honesty contract is written into the spec.

- **Cockpit v3 — single watermark, RGB keys, TIME MACHINE** (`9a64ad7` +
  `9725ba5`): one watermark for the view; every cbar-less pane carries a
  quick-guide interpretation key (overlay + exports) so no pane is bare;
  Time Machine mode drives the existing render-on-demand archive backend
  (field = rail with 8 servable fields, region = viewport with an 80°-cap
  clamp + a great-circle limb guard — off-disk corners 500 the renderer,
  found live; UTC picker bounded to GOES-R era; ≤12-frame rate-paced archive
  loops; exports reuse the ≤10MB/HQ path; PNG saves the backend's branded
  render directly). Live-verified with a real 2026-07-08 18Z archive render.
  The standalone custom-snapshot panel is hidden in the embedded legacy
  section — its function lives in the cockpit now. Scripts carry ?v=
  cache-busting (the CDN edge masked a fix once — lesson noted).

- **Cockpit v2 — branded panes, linked cameras, unified rail, scroll page**
  (`9012388`): per-pane burned-in chrome composited into exports; linked
  cameras with a Link toggle; subnav folded into the FIELD rail (Microwave +
  Scatterometer mount the existing viewers via ?embed=1, gated off their own
  manifests); page scrolls to the legacy loops/imagery section (floaters,
  meso, VIIRS/MODIS unchanged). Full Disk went live mid-pass (fd suite on
  R2, domain self-enabled).

- Earlier in the session (see git log for full detail): cockpit full-bleed
  layout (`63c4202`), cockpit design pass (`a789981`), the full §6 cockpit
  shell (`7525ecc`), object-level shadow prune (tsr `1d5046d`, 114 s2 tests),
  Day Snow-Fog RGB (tsr `cacbf64`), box-emit live verification (27/27
  products, BT probe physically correct), explorer preview gate DROPPED
  (Andrew's call — page stays unlinked+noindex by design).

## IN PROGRESS

- **Objective center + intensity build** — methods researched, spec + data
  path committed (`OBJFIX-METHODS.md`); the compute module + cockpit panel
  land next session. Nothing half-shipped: no UI is exposed yet.
- MRMS / METAR / model-field overlays, Chart, GOES-18 West, meso domains:
  honest "SOON" stubs in the cockpit awaiting their own pipelines.

## BLOCKERS

- None. Queue ① is a one-minute box pull at your convenience; everything
  else is decisions.

## HEALTH (as of the wind-down)

- Site + explorer 200 on the rewritten main; Pages deploying normally
  (rewritten history included — deploys `6ea658f0`+).
- Box emit-cron live and backfilling (5+ conus frames and climbing, fd suite
  emitted); ACE/tracks cron commits landing on schedule through the night
  (two raced my pushes and were rebased/cherry-picked cleanly).
- Typhoon BAVI (WP09, C3 110 kt) + Invest 97W active in the feeds.
- Codespace disk: /workspaces at 90% (the 3 GB backup experiments were
  cleaned; the 97 MB bundle kept). The Codespace may idle-suspend — all work
  is committed and pushed; nothing uncommitted anywhere.
