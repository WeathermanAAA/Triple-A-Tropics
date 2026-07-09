# Agent status — Triple-A-Tropics autonomous work log

Maintained by Claude while Andrew is away. Updated after each meaningful step;
newest state first. Raw URL:
`https://raw.githubusercontent.com/WeathermanAAA/Triple-A-Tropics/main/AGENT_STATUS.md`

_Last update: 2026-07-09 ~06:30 UTC_

---

## MY QUEUE (Andrew's hands / decisions, ordered)

**① Box (next convenient session, ~1 min) — start the emit loop + the new prune:**
the 27/27 one-shot emit LANDED + live-verified (see below); frames stay at 1/product
until the cron loop runs. In the tsr dir on the box:

```bash
git pull      # s2-sat-ingest
docker compose -p tat-s2 -f docker-compose.s2.yml --profile cron up -d --build emit-cron prune-cron
```

`prune-cron` is the NEW object-level shadow TTL (ListObjectsV2+DeleteObject on
`shadow/sat/**` >14 d, keeps a per-product minimum) — it replaces the
`lifecycle --days 10` step that AccessDenied'd (bucket-lifecycle needs a
permission the box R2 token doesn't have; no scope change needed for this).
The same pull also picks up **Day Snow-Fog** (28th product — its greyed picker
entry lights up on the next emit). Optional extra:
`run --rm emit --suite fd --store r2 --prefix shadow --max-zoom 5` lights the
Full Disk domain in the cockpit (27 more products; heavier, on-demand).

**Explorer preview gate: DROPPED (Andrew 2026-07-09).** Not deploying the
Worker; no CLOUDFLARE_API_TOKEN. The explorer stays live-but-unlinked +
noindex/robots — the intended dev-preview state. (Vendored worker code stays
in `workers/` for a possible future launch gate; not to be re-raised.)

**①·c History rewrite: READY, needs one explicit confirm.** Both repos' HEADs
are fully clean (TAT `9131452`, tsr `731bbef` — files removed + references
neutralized, verified). The main-branch history rewrite is scoped and cheap
(main only — the planning files only ever existed on main, all 35 other
branches provably predate them or are orphans; ~1.1 GB on /tmp, bundle backup
first, no giant mirrors) but the tooling's safety gate wants a fresh explicit
go after the earlier "too much" interrupt. Say "run the history rewrite" and
it executes in one pass; afterwards GitHub support can force-GC orphaned SHAs.

**② Decision (art): HAFS env-color v0.12 on the live worker** — say go and
Claude repins `hafs-render-worker` hafs-render v0.11.0→v0.12.0 (Railway
auto-rebuilds; restyles the 9 env products to the palette look on TAT main
since 07-01).

**③ Decision (art, month-old): TCHP records hatching** — crops at
`https://cdn.triple-a-tropics.com/sst/records/review/<region>_tchp_anom.png`
(east-pacific / north-atlantic / western-atlantic / global-tropics). Say go
(Claude merges + disclosure + temp-workflow cleanup) or park.

**④ One-clicks, LOW:** close stale [PR #24](https://github.com/WeathermanAAA/Triple-A-Tropics/pull/24)
(ASCAT shipped via main 06-28; permission gate blocked Claude closing it);
optional $10 AWS budget alarm (console/root only).

## LANDED (with SHAs + artifacts)

- **Cockpit v3 — single watermark, RGB keys, TIME MACHINE** (2026-07-09, TAT
  `9a64ad7` + fixes `9725ba5`): one @WeathermanAAA_ watermark for the whole
  view (headers stay per-pane; exports keep one per output). Every cbar-less
  pane now carries a compact quick-guide interpretation key (Air Mass, Dust,
  Ash, Day Convection, DCPD, Night Micro, Snow-Fog, Fire Temp + labeled
  natural-color keys) overlay + in exports — no pane is ever bare. **Time
  Machine**: the timeline chip toggles Live ↔ archive mode driving the
  existing render-on-demand backend; field = rail (8 archive-servable fields,
  rest grey in-mode), region = viewport (auto-clamped to the render cap + a
  great-circle limb guard that shrinks the box until every corner is on-disk
  — found the hard way: off-disk corners 500 the renderer), overlays/quality =
  existing toggles; UTC picker bounded to the GOES-R era; ±1h steps; a time
  range assembles a rate-limit-paced ≤12-frame archive loop that plays on the
  normal transport and exports via the same ≤10MB/HQ WebM path. Live-verified:
  2026-07-08 18Z render displayed in-pane (backend satellite-hopped to
  GOES-18 for a west-CONUS box — its own covering-product logic at work).
  The standalone custom-snapshot panel is now hidden in the embedded legacy
  section (function lives in the cockpit); floaters/meso/VIIRS-MODIS intact
  (legacy iframe 4245→3291 px). Scripts got ?v= cache-busting (edge cache
  masked the first fix — lesson noted).

- **Cockpit v2 — branded panes, linked cameras, unified rail, scroll page**
  (2026-07-09, TAT `9012388`): every pane (1/2/4) now carries burned-in
  branded chrome — title strip (product · domain · valid time) + watermark +
  per-product colorbar (scalar/BT only; RGB composites correctly carry none) —
  overlaid on the interactive map AND composited into PNG/WebM exports, so a
  saved pane is a finished graphic. Cameras link across panes (pan/zoom one,
  all follow; toolbar Link toggle, default ON). The satellite subnav is gone:
  **Microwave + Scatterometer are FIELD-rail categories** that mount the
  existing viewers chrome-less (`?embed=1` flag added to the three legacy
  pages; components untouched), gated off their own R2 manifests (all 6
  entries lit live). The page scrolls: cockpit = one full viewport under the
  main nav; below the fold the existing /satellite/ page (floater/meso loops,
  custom snapshots, GIBS daily true-color) mounts unchanged in an auto-sizing
  iframe. Live-verified end-to-end, zero JS errors: linked quad pans as one,
  MW embed opens/returns, legacy section shows BAVI floater + meso 03:14Z +
  VIIRS/MODIS intact. **Full Disk went LIVE during the pass** — the fd suite
  (27 products) landed on R2 and the domain self-enabled; conus loop at 5
  frames and climbing.

- **Cockpit full-bleed layout** (2026-07-09, TAT `63c4202`): the explorer now
  owns the whole viewport below the nav/subnav — rails flush to both screen
  edges, the pane grid fills all space between and grows vertically, timeline
  + toolbar pinned to the bottom, the page footer folded into a thin strip
  (© + helper hints + shadow-preview note). Panel gaps/radii/shadows replaced
  by hairline dividers so it reads as ONE app surface; short windows fall back
  to page scroll; mobile keeps the stacked flow. Container/CSS only.
  Live-verified at 1920×1080: rails at 0px from both edges, strip at the
  viewport bottom, no page scroll, quad panes filling the frame, zero JS
  errors. Timeline was at 3 frames (01:41Z) during the check — the box
  emit-cron keeps backfilling.

- **Cockpit design pass** (2026-07-09, TAT `a789981`): the shell now reads
  designed, not scaffolded — inline-SVG icons across the toolbar, grouped
  playback | tools | export | view with dividers; monospace/dev-console text
  replaced by Metropolis everywhere (branded ring-spinner pane loading, error
  card, pill flash toast with busy spinner, brand-type timeline labels +
  single-frame note); selected field/domain rows pop with a teal accent bar +
  soft glow; rail tabs mirror the main-nav underline; unified section headers
  (teal tick + tracked caps), consistent region-group carets, softer uppercase
  SOON chips, gradient depth + shadow on rails/bottom bar. Zero layout or
  behavior change — headless-verified (play/product-switch/HQ/loader), then
  live-screenshot on the deployed page. **Bonus observed live: the box
  emit-cron is RUNNING** — the timeline already shows 2 frames
  (23:36Z + 01:11Z), so the loop/export light up on their own now; only the
  `git pull` for prune + Snow-Fog remains from queue ①.

- **Explorer COCKPIT — the full §6 shell** (2026-07-09, TAT `7525ecc` +
  cockpit.js): /satellite/explorer/ went from bare map + dropdown to the full
  toolkit, additive around the working viewer (zero regression to pyramid/
  BT-inspector/export/compare — compare.html re-verified clean). Left rail =
  field selector (RGB·Composites / Channels tabs, MET labels: Upper/Mid/Low
  WV, Clean IR, Dvorak BD…). Right rail = satellite (GOES-18 greyed "coming"),
  domain (CONUS active; Full Disk self-enables when the box emits `--suite fd`;
  Meso greyed), TATRegions presets + a US-states group derived from the
  admin_1 overlay geojson, overlay toggles + MRMS/METAR/model STUBS. Bottom =
  canvas timeline (hover-scrub; fills when the cron backfills), fps-ladder
  transport, Measure (geodesic km/nmi), freehand Sketch v1, draw-box,
  select-on-map, Share permalink (URL state incl. camera/panes/frame), PNG
  snapshot, WebM loop (≤10MB default / HQ toggle), Settings, Reset; 1/2/4
  panes time-locked by VALID TIME with per-pane field+region. **Honesty rule
  enforced**: everything without real data ships greyed with a "coming"/"no
  data yet" chip, driven by the R2 products.json ground truth — nothing fake.
  Headless-verified against real tiles (boot/product-switch/quad/measure all
  clean; only pre-existing console noise). **Day Snow-Fog RGB** landed in tsr
  (`cacbf64`, verified numbers test-locked, registry now 28 conus + 27 fd) —
  it sits greyed in the picker until the box's next pull+emit.

- **Object-level shadow prune** (2026-07-09, tsr `1d5046d`): `s2_prune.py` —
  ListObjectsV2+DeleteObject only (works with the box token's frozen scopes;
  bucket-lifecycle is OUT). Stamp-parsed 14-day TTL on `shadow/sat/**`,
  keep-min newest-2 per product, ready-marker-first deletes, manifest rewrite
  for retired products, dry-run default, `shadow/`-only guard, batch-delete
  with per-key fallback. `prune`/`prune-cron` compose services replace the
  removed `lifecycle` service; RUNBOOK rewritten; 8 new tests (114 s2 green).
  Goes live on the box at queue ①'s `git pull` + `--profile cron up -d`.

- **Box emit VERIFIED LIVE — the 27-product explorer is real** (2026-07-09):
  Andrew's box session emitted the full GOES-19 CONUS suite (27/27, 0 failed,
  scan 2026-07-08T23:36Z). Claude live-verified end-to-end against the REAL
  R2 tiles: products.json (count/fields ok) ↔ picker products.js ids/paths/bt
  flags match 27/27; every manifest carries the full viewer contract
  (webmercator-xyz, tile template, bounds, times/latest/count, bt descriptor);
  z0 + max-zoom tile per product = HTTP 200 + valid 512px WebP; `_ready.json`
  present per frame; BT rasters decode to physically-sensible ranges per
  channel (e.g. C08 −62..−24 °C, C07 −29..+42 °C). Headless-browser run
  (Playwright): viewer + compare render real tiles with ZERO JS errors;
  product switch (?product=airmass/truecolor) works; **BT hover probe read
  31.3 °C at 32.7°N −101.6°W (clear-sky W-Texas, correct)**; compare 2-pane
  C13/C08 synced at 23:36Z. Only console noise = the site-wide cdnfonts
  stylesheet block (cosmetic, pre-existing) + CF analytics beacon injection.
  **Not exercisable yet: loop playback + 90-frame WebM export — every product
  has exactly 1 frame until the emit cron runs (gated on cadence, by design).**

- **Sat explorer Phase 3 — multi-product imagery suite** (2026-07-08):
  tsr `s2-sat-ingest` `a00fa8e` + review-fix `52a3306`; TAT main `d0b7c7c`
  (+ review-fix, see git log). 24 registry-driven products — true color
  (frozen GeoColor-lite pipeline verbatim), Sandwich, Air Mass, Dust, Fire
  Temperature, Day Cloud Phase, Nighttime Microphysics, all 16 ABI channels
  (+ Dvorak BD) — recipe engine + generated registry rows ("add a product =
  a config row"), suite emit off one pinned scan with shared band fetches,
  dockerized box service, viewer product picker + per-pane compare products +
  per-product colorbars (exact-norm tick placement). All 5 RGB scalings
  verified against the CIRA/RAMMB quick-guide PDFs; 4-finding adversarial
  review folded (tsr `52a3306` + TAT `da0a66e`).
  Artifact: https://claude.ai/code/artifact/22ac0c0b-3cfe-4ab5-aad8-53291ee55783
- **Suite extension** (2026-07-08, tsr `7902a98` + TAT, see git log): **Ash,
  Day Convection, Day Land Cloud/Natural Color** (primary-source verified —
  Day Convection green gamma 1 per the ABI guide, not EUMETSAT's 0.5 heritage)
  + **full-disk rows for every recipe** (goes19-fd-*, config-only; fd anchor
  path verified on a real CMIPF scan) + C13 BT rasters on the IR-window RGBs
  so the inspector works there. Registry now 27 CONUS + 26 FD tiled products;
  106 s2 tests green. Same box command lights everything: the cron form covers
  `--suite conus`; full disk = `--suite fd` (heavier, on-demand).
- **Phase 2d 90-frame WebM export** (recovered from dead session, `0906d09`) +
  **Q7 tiering plumbing** (tsr `ced02e1`: `--max-zoom`, lifecycle-TTL script).
- Earlier phases: 2a pyramid emitter `dfde476` ·
  [artifact](https://claude.ai/code/artifact/ec072fec-e15e-44d7-9fda-a4f6a073da0f);
  2b webmerc viewer `059ae48`/`3fe538c` ·
  [artifact](https://claude.ai/code/artifact/23740da2-ee95-4037-b1d7-94f18d4bb392);
  2c compare + BT inspector `9692eb4`/`5822abb` ·
  [artifact](https://claude.ai/code/artifact/5bc85d26-a907-4f65-bfd1-09ca896e4619).

## IN PROGRESS

- Nothing mid-flight. Natural next steps: MRMS/METAR/model overlay pipelines
  (separate builds, stubs already in the cockpit), Chart (point time-series)
  once multi-frame history exists, GOES-18 West onboarding (registry rows +
  SNS re-subscribe per Stage-2 Phase 4), icon stamps for Sketch.

## BLOCKERS

- None. Shadow explorer awaits the box emit (queue ①) to show real tiles —
  by design, not a defect. Live site unaffected throughout.

## HEALTH SNAPSHOT (2026-07-08 ~18 UTC sweep)

All pages 200 and fresh; feeds 4–10 min old; all 7 workflows green; Typhoon
BAVI (WP09, C4 125 kt) + invest 97W active; AL/EP quiet. Railway: nothing new
beyond the two verified palette repins (07-05/07-06).
