# Agent status — Triple-A-Tropics autonomous work log

Maintained by Claude while Andrew is away. Updated after each meaningful step;
newest state first. Raw URL:
`https://raw.githubusercontent.com/WeathermanAAA/Triple-A-Tropics/main/AGENT_STATUS.md`

_Last update: 2026-07-09 ~21:30 UTC — objfix + MW/ASCAT-native builds STARTED_

---

## IN PROGRESS tonight (2026-07-09 evening)

Two explorer builds running now, committed piecewise as they land:

1. **Objective center + intensity (ARCHER/ADT)** against the frozen spec
   (`satellite/explorer/OBJFIX-METHODS.md`). Pre-build: every UNCONFIRMED
   constant is now RESOLVED from primary source (ajwimmers/archer @ d09f5c7 +
   ADT v8.x via the SSEC McIDAS-V port, cross-checked vs AODT v7.2) — two
   spec corrections recorded in the spec's new §D addendum (penalty is
   linear 0.33/deg; Raw T# uses BD-category base tables). Data paths
   verified live: fd `bt.png` (u16, 0.01 °C, 1280×1045, ~14 km — flagged);
   WP floater frames 1056×1056, data rect from render.py axes
   [0.04,0.04,0.84,0.90] (graticule-verified ±2 px), display extent
   per-frame = [cx−6, S, cx+6, N] from the backdrop bounds; LUT inversion
   self-calibrates from each frame's own baked colorbar (rainbow_ir linear
   −95→40 °C). Honesty contract enforced in the panel.
2. **MW + ASCAT as NATIVE cockpit fields/layers** (retiring the ?embed=1
   stage takeover): MW georeferenced overpass tiles as MapLibre image
   sources, ASCAT barbs as a camera-synced per-pane canvas overlay, both
   reusing the legacy viewers' fetch/product/legend/barb code (re-hosted,
   not rebuilt); per-pane controls in the rail; ASCAT defaults
   high-contrast; layerable over any base field; exports composite them.

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
