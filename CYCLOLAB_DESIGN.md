# CycloLab — per-storm broadcast-grade web app (DESIGN)

**Status: GREENLIT 2026-06-06 — building per §12 stages.** All §13
decisions confirmed: (1) CF route → R2, Worker vendored in-repo; (2) ramp
hues proceed, finalized only on rendered swatch recordings; (3) WP radii
pinned `jtwc-wpac-mean-2015`; (4) V1 = designated storms only; (5) doc
convention = SATELLITE.md / README-HAFS-WORKER.md. **Binding fix folded
into §3.3** (explicit basin-letter map — the AL trap). V1 additionally
includes the per-storm ACE ticker (§7.1/§11-b). HARD STOPS: Stage-2 gate
(art direction on ramps/banner-morph/odometer recordings) and Stage-4
gate (cone reveal + methodology copy) — user sign-off before proceeding.
Research basis: live-dissected NHC KMZ for AMANDA (EP01, adv #13), a
downloaded-and-verified JTWC WPAC error table (citations inline), the
Phase-A mobile audit (`/tmp/mobile-audit/REPORT.md`), and a code-level
integration map of both repos. Doc precedent: `SATELLITE.md` /
`README-HAFS-WORKER.md` (no `MODEL_PLATFORM_DESIGN.md` file exists; those
two are the de-facto house design docs).

---

## 1. What it is

`triple-a-tropics.com/cyclolab/{sid}/` — one page per active storm, born
when the storm is born, frozen when it dies, beautiful the whole time.
Four sections (Overview / Satellite / Models / Advisories), one visual
system that "wears the storm" (the app re-skins to the storm's category),
and one signature animation: the forecast cone drawing itself.

V1 scope: **live storms only** (page exists birth → season's end; frozen
"ended" state after dissipation; no archive backfill).

---

## 2. THE TAT WEB-APP PATTERN (reusable — document once, reuse for ERA5 toolkit)

The pattern every future TAT app (CycloLab now, the ERA5 toolkit later)
follows. It is the site's existing discipline, named:

1. **Pre-generated static entry pages with real meta.** Every shareable
   URL is a real document served HTTP 200 with its own `<title>` + OG
   tags (name, current intensity, preview image). No hash routing, no
   SPA-404 rewriting. (12 site pages already carry `og:` tags; none yet
   carry `og:image` — CycloLab adds it.)
2. **A poller owns lifecycle; the page owns presentation.** Server-side
   writers (poller_framework sources) create/update/freeze the per-entity
   artifacts on their own clock. Pages are dumb shells.
3. **Vanilla-JS hydration from R2 JSON.** The page boots from baked
   content, then cache-busted `fetch` (`?t=` + `no-store`) hydrates live
   state on the existing poll + **diff-merge** discipline: polls *grow*
   state in place and **never reset user selection** (the /models/
   contract, `hafs.js` `_mergeManifest`).
4. **Lazy sections.** Each section loads its data + assets on first open
   only (audit findings #2/#6: eager loading is the site's worst mobile
   sin — 24 MB cold on /models/, 11 MB homepage).
5. **No server in the page path. No CDN script deps. Generated templates
   only** (a generator script owns the HTML; humans never edit emitted
   files).
6. **One canonical JS implementation per component** (the `ICON_*` rule
   generalized): a component used in two places is one file with two
   mounts, never a fork.
7. **Mobile-first against the audit:** `width=device-width` honored (no
   min-content >viewport — audit #1), ≥44 px tap targets, ≤400 KB
   first-paint budget per section on Fast-3G, `prefers-reduced-motion`
   respected everywhere.

---

## 3. Architecture & routing

### 3.1 The serving problem, resolved
The poller writes R2; GitHub Pages serves git. Verified today:
`cdn.triple-a-tropics.com` (R2 custom domain) serves objects with their
**stored Content-Type** — an object PUT as `text/html` renders as a page —
but there is **no route mapping `triple-a-tropics.com/cyclolab/*` to R2
today**, and missing R2 keys 404 as `text/plain` (no fallback).

**Primary design — CF route to R2 (satisfies every requirement):**
- One Cloudflare route on the existing zone:
  `triple-a-tropics.com/cyclolab/*` → a ~20-line Worker that maps the
  path to the R2 bucket key `cyclolab/{sid}/index.html` (same account
  that runs the ATCF proxy Worker; Worker source should be vendored in
  the repo this time — lesson from the deck-fetch investigation where
  the proxy's behavior was unauditable).
- The **intensity poller** renders the per-storm page (template string
  from a generator module, storm-specific OG tags + baked snapshot) and
  PUTs it `Content-Type: text/html` on **storm birth**; refreshes the
  baked snapshot + OG intensity on category change; PUTs the frozen
  "ended" variant on dissipation. Lifecycle latency = poll cadence
  (~2 min), exactly "the poller owns lifecycle."
- R2 additions needed: `R2.put_html` (the current sink hard-codes JSON)
  — small, poller-repo.

**Documented fallback (if the CF route is declined):** committed shell at
`/cyclolab/index.html` reading `?sid=` — works today with zero infra, but
breaks per-storm OG tags and real paths; named here only so the trade is
explicit. **The design assumes the CF route.**

### 3.2 Hydration data (all existing, all R2)
| Section | Source | Notes |
|---|---|---|
| Overview | `feeds/{basin}_tracks_data.json` | per-storm `points[]` carry `t`, `wind_kt`, `pressure_mb` — chart-ready |
| Satellite | floater manifest + frames (`satellite/…`) | same objects the floater page reads |
| Models | `models/hafs/manifest.json` (v2) | storm-scoped mount (§7.3) |
| Advisories+Cone | **new** `cyclolab/adv/{sid}.json` (§9) | parsed advisory + cone JSON, never raw KML in the browser |

### 3.3 The ID join (real examples from tonight)
Three id dialects exist; CycloLab's `sid` is the tracks-feed sid, and the
join is derivational (no lookup table):

```
tracks sid     NHC_EP012026 / JTWC_WP062026   (agency_BASINnnYYYY)
→ atcf longid  ep012026                        (basin.lower + nn + yyyy)
→ hafs id      01e                             (nn + SUFFIX LETTER, lower)
→ NHC products EP012026 (CurrentStorms.json binNumber/id fields)
```
Parser: `sid.split("_")[1]` → `(basin, nn, yyyy)`; emit all three forms.

**BINDING (review fix): the HAFS/floater suffix letter is an EXPLICIT
map, never a slice of the basin code:**

```python
BASIN_SUFFIX = {"AL": "l", "EP": "e", "CP": "c", "WP": "w"}
```
`AL → "l"` is the trap (a first-letter slice yields "a"; the ATCF
single-letter convention is L=Atlantic) and `CP → "c"` must hold. The
mandatory test case — **no Atlantic storm has run the models pipeline
this season, so this path is otherwise unexercised**:
`AL052026 → atcf al052026 → hafs 05l → NHC AL052026`. One Python module
(poller) + one mirrored JS constant (shell), node-harness parity-tested
(the ICON_* rule).

Edge: invests renumber on designation (`transitioned_from` handoff) — V1
scopes CycloLab to **designated storms only** (invests get no page).

### 3.4 Lifecycle states
`LIVE` (hydrating, all sections) → `ENDED` (poller writes frozen page:
"This storm has ended" banner, last-known data baked, hydration script
replaced by a no-op stamp; stays up through season's end so shared links
never 404) → season cleanup (manual/cron, out of V1 scope).
Dissipation signal: storm leaves the feed's active set (NHC: also leaves
`CurrentStorms.json`). Debounce 2 polls before freezing (the system's
standard transient guard).
*Timely note: AMANDA is 35 kt and forecast to a remnant low within days —
the ENDED path gets a real-world test almost immediately after V1.*

---

## 4. Layout — left-sidebar shell, mobile-first

**Desktop:** fixed left sidebar (~260 px): identity banner on top
(wordmark, storm name, intensity chip — **no pulsing dot**), section nav
(Overview / Satellite / Models / Advisories) with a 3 px `--cat-accent`
rail on the active item, "← Back to map" pinned at the bottom. Content
stage to the right keeps the site navy family (`--bg/--panel`).

**Mobile (≤640 px):** the SAME components rearranged — banner becomes a
slim sticky top bar (name + chip), section nav becomes a 4-item bottom
tab bar (each tab ≥44 px, audit P1 rule), back-to-map lives in the top
bar. One DOM, CSS-only rearrangement (flex order + position), no
duplicate markup.

**Entry:** the active-marker popup on the global map and the per-basin
storm placards gain a "Launch CycloLab" button (tinted `--cat-accent`).
Same-tab navigation; browser back returns to the map (no state to lose —
the maps re-hydrate). Integration points: the global popup builder in
`generate_tracks_plot.py` (`activeMarkerPopupHtml`-area), and the
per-basin `render_storm_card` **+ its LIVE_BASIN_JS mirror
`buildStormCard` — byte-parity pair, both sides or neither** (the
standing parity rule; parity suite must stay green).

---

## 5. Visual system — "the app wears the storm"

One token, fed by the feed's `current_category`, re-skins everything:
`data-cat="C3"` on `<html>` switches a token SET. Seven **glossy gradient
ramps** in the LIVE-STATUS chrome style (banded vertical gradient — dark
edge, lit middle band, dark edge — NOT flat):

```css
/* token set per category: ramp + flat accent + on-accent text color.
   Construction shown for two; all seven follow the same 5-stop banded
   form (dark edge, lit middle, dark edge - the .status-head gloss). */
[data-cat="TD"] { --cat-ramp: linear-gradient(180deg,
    #16324a 0%, #2c5a80 22%, #3f7cab 50%, #2c5a80 78%, #16324a 100%);
  --cat-accent: #3f7cab; --cat-ink: #ffffff; }
[data-cat="TS"] { --cat-ramp: linear-gradient(180deg,
    #0d3b2a 0%, #1d6f4f 22%, #2aa169 50%, #1d6f4f 78%, #0d3b2a 100%);
  --cat-accent: #2aa169; --cat-ink: #ffffff; }
/* Anchor hues for the remaining five (same banded construction):
   C1 gold #d9a91f (--cat-ink dark #0a1324) · C2 amber-orange #e07b28
   (dark ink) · C3 red #d23b2e · C4 hot-magenta #d62fa0 ·
   C5 deep-violet #7a3df0 (white ink) */
```
(The two written out above show the construction; the implementation
defines all seven as literal token sets — exact stops are an
art-direction review item, delivered as rendered swatches, §6.)

**Tint touches (exhaustive):** identity banner background, nav active
rail, intensity chip, placards (cards' header band), Models scrubber
tick/current-hour highlight, Overview chart line + area fill, OG
share-card border, the "Launch CycloLab" button on the maps.
**Never tinted:** body text, data tables, advisory text — readability is
never category-dependent (and `--cat-ink` guarantees AA contrast on the
chip/banner: dark ink on gold/amber, white elsewhere).

---

## 6. Motion — broadcast-grade

Global rules: **~4–5 s slow timing**, `transform`/`opacity` only (60 fps
mid-render on phones — no layout/paint properties), triggers on
**state-change only** (never ambient loops, one exception below),
`prefers-reduced-motion: reduce` ⇒ every animation lands on its final
frame instantly.

| Moment | Choreography |
|---|---|
| Launch | full-screen `--cat-ramp` wipe (scaleY) revealing the shell, banner text rises in |
| Category change | gradient crossfade between token sets + ONE shine sweep across the banner |
| New fix (intensity/pressure) | odometer roll on the changed number (translateY digit column) |
| Section switch | content wipe (translateX + fade), nav rail slides |
| Overview chart | line draws itself (stroke-dashoffset), fill fades up after |
| Satellite | frame-to-frame crossfade (opacity), band switch = quick dip-to-navy |
| THE CONE | §8 — the signature |

**Art-direction deliverable:** Playwright **screen recordings**
(`page.video`) of every styled component animating with real data —
swatch sheet of the 7 ramps, banner morph TD→C5, the cone reveal, the
odometer — reviewed on real renders, never flat mockups. Recordings land
in `/tmp/cyclolab/motion/` at review time.

---

## 7. Sections

### 7.1 Overview
Zoomed track map (reuse the per-basin SVG projection/renderers,
storm-filtered, auto-bbox from the storm's points + forecast cone),
current-stats banner (position, motion, VMAX, MSLP, last fix, and the
**live per-storm ACE ticker** — V1 per greenlight; `ace` is already in
the feed, odometer-rolled on each new fix), and a **hand-rolled SVG
wind+pressure timeline** (no chart
libs): dual-axis polyline from `points[].wind_kt/pressure_mb/t`, SSHWS
band shading behind the wind trace, `--cat-accent` line, draw-in on first
open. Hover/touch crosshair reuses the ACE-chart pattern
(`touch-action: pan-y`, the Phase-B fix).

### 7.2 Satellite
Floater imagery scoped to the storm: band switcher (existing manifest
bands), availability-aware scrub (grey unavailable frames — the /models/
hour-grid idiom), lazy: nothing fetched until the tab opens, then
current-frame-first with a small concurrency window (the audit's P3-b
loader pattern, applied here from day one).

### 7.3 Models — componentize, don't fork
`models/hafs.js` becomes **one canonical implementation, two mounts**:

```js
new HafsViewer(root, {
  manifestUrl,            // today: hard const MANIFEST_URL
  els,                    // today: hard el('hafs-*') id table in this.dom
  stormLock: "01e"|null,  // CycloLab passes the joined id; hides storm picker
})
```
Inventory (from the integration map): the only mount-coupled pieces are
the `MANIFEST_URL` const, the `this.dom = el('hafs-*')` table, the
storm-`<select>` population (lock = single option, picker hidden), and
the document-level keyboard handler (scope to root). Everything else
(cycle normalization, grids, diff-merge, preload, badges) is already
mount-agnostic. `/models/` keeps identical behavior (its mount passes
today's values); the parity precedent applies: **one impl, two mounts, no
fork**.

**Storm-centered bboxes are the contract:** HAFS already complies
(v0.3.0 track-anchoring; the nest follows the storm). Future registry
models inherit a **storm-centered crop spec**: a registry entry must
declare `crop: {anchor: "atcf_fix", radius_deg: N}` (renderer crops
upstream grids to the anchored window before plotting) so any new model
drops into the same viewer scoped per-storm. (Spec only; no new models
in V1.)

### 7.4 Products / Advisories
Advisory text panel (Public Advisory TCP + Discussion TCD, parsed text
from the advisories poller; monospace block, never tinted) + **THE CONE**.

---

## 8. THE CONE (the must-have)

### 8.1 Reveal choreography (~4–5 s, plays once per tab open)
1. Push-in zoom on the storm-centered map (scale transform, ~1 s).
2. Current-intensity placard pops at the present position (~0.5 s).
3. The cone **draws itself outward** from the current position along the
   track axis (~2 s slow ease — implemented as a clip-path/stroke-dash
   reveal of the pre-parsed polygon, transform-only compositing).
4. Forecast-point icons pop in sequence (stagger ~0.2 s): color-coded
   **slow-spinning cyclone glyphs** (the site's hurricane path) + glossy
   category placards (mini `--cat-ramp` chips with tau + intensity).
Reduced motion: final frame instantly. The icon spin is the **one
permitted continuous loop** (slow, `transform: rotate`, reduced-motion ⇒
static).

### 8.2 Style
The cone is **brand blue/white, never category-colored**: translucent
white fill (~12%), soft feathered light-blue inner edge, navy outline.
Category color lives ONLY in the forecast-point icons + placards — the
icons spinning green → gold → orange → red along the track ARE the
intensity-forecast timeline. (Cone = uncertainty area; color = intensity.)

### 8.3 NHC source (AL/EP/CP) — verified against live AMANDA
Pipeline (all dissected tonight; artifacts in `/tmp/cyclolab/kmz/`):
- **Discovery: `CurrentStorms.json`** (already polled by the intensity
  poller) — carries advisory number, issuance, and direct URLs for every
  GIS + text product. (Do NOT scrape `nhc.kmz` NetworkLinks for
  discovery; same URLs, cleaner source.)
- Per-storm `…_CONE_latest.kmz` + `…_TRACK_latest.kmz` (KMZ = zip):
  cone = `Placemark/Polygon/outerBoundaryIs/LinearRing/coordinates`
  (lon,lat order, closed ring — Amanda adv #13: 1,272 vertices);
  track = per-point Placemarks with intensity kt, valid time, dev label
  (Amanda: 9 points, tau 0/12/24/36/48/60/72/96/120).
- The **advisories poller** (§9) unzips + parses to the cached contract:

```json
{ "sid": "NHC_EP012026", "advisory": 13, "issued_utc": "2026-06-05T21:00:00Z",
  "source": "nhc", "method": "official-cone",
  "cone": [[lon,lat], …],
  "points": [{ "tau_h": 12, "valid_utc": "…", "lat": …, "lon": …,
               "intensity_kt": 30, "dev_label": "TD" }, …],
  "text": { "tcp_url": "…", "tcd_url": "…" } }
```
The browser animates **pre-parsed geometry only** — never raw KML.

### 8.4 JTWC source (WP) — the DERIVED cone, with sourced radii
JTWC issues no official cone. WP storms get the forecast track
(b-deck/knackwx forecast positions) + a **derived uncertainty envelope**:
buffer each forecast point by JTWC's published **average track-forecast
error** at that lead time and sweep the convex envelope of the circles.

**The radii (REAL, downloaded, image-verified — never invented):**

| Lead (h) | Mean error (km) | Radius (n mi) | n |
|---|---|---|---|
| 24 | 72.1 | 39 | 612 |
| 48 | 112.7 | 61 | 512 |
| 72 | 169.7 | 92 | 426 |
| 96 | 238.1 | 129 | 347 |
| 120 | 334.1 | 180 | 273 |

Source: **ESCAP/WMO Typhoon Committee, "Verification of Tropical Cyclone
Operational Forecast" (2015 season), Table 3, JTWC-sub row** (mean
great-circle errors vs RSMC-Tokyo best track; km→n mi × 0.539957).
PDF archived: typhooncommittee.org/48th/docs (md5
`a5eafd0f4ce55ac4e2c7f4420bfe43f6`, local `/tmp/cyclolab/tc_verif_2015.pdf`,
table render `tc_table3_crop.png`). Cross-corroborated: ATCR 2020 Fig 6-2/6-3
chart endpoints (~40/70/95-100/130/190-200 n mi) and the 2017 Typhoon
Committee table (worse year: 47/80/129/179/195 n mi) bracket these values.
**12 h and 36 h are NOT in any JTWC table** (24 h verification cadence) —
they are linearly interpolated (12 h ≈ 24, 36 h ≈ 50 n mi) and the
disclosure says so.

**Method versioning (the v0.3.0 spirit):** the radii ship as a versioned
JSON blob — `{"method_version": "jtwc-wpac-mean-2015", "source_doc": …,
"table": "Table 3 JTWC-sub", "km_values": {…}, "nm_values": {…},
"conversion": 0.539957, "interpolated_taus": [12, 36]}` — rendered cones
stamp the version; re-pinning to a newer year is a data edit, not code.

**Construction choice (explicit):** radii are used **1:1** (radius = mean
error). We deliberately do NOT apply NHC's 2/3 scaling: NHC's cone radii
are the **67th percentile of its own per-case error distribution**, not
⅔ × mean — applying that scale to a *mean* would fabricate a
statistically meaningless, too-small band. A mean-error circle encloses
well over half of cases (right-skewed error distribution) and is the
honest "average miss" band.

### 8.5 WP methodology disclosure (house rule: label it AND show the method)
- Inline caption under the cone:
  *"Derived uncertainty envelope — not an official JTWC product."*
- Tappable **"How is this derived?"** panel (plain language):
  *"JTWC issues a forecast track for western Pacific storms but no
  official 'cone of uncertainty.' This envelope is drawn by buffering
  each forecast point by JTWC's published average track-forecast error
  at that lead time (ESCAP/WMO Typhoon Committee verification report,
  2015 season, Table 3) and sweeping the boundary. It reflects the
  historical AVERAGE error of past forecasts — it is not a probabilistic
  bound, and the storm can travel outside it. 12 h and 36 h radii are
  interpolated between published values. Method jtwc-wpac-mean-2015."*

---

## 9. New data source: the advisories+cone poller

A new `poller_framework` **Source** on the existing intensity-poller
engine (same process; per-source isolation means it can never stale the
feeds):

- **NHC sub-source (AL/EP/CP):** poll `CurrentStorms.json` (cheap, already
  fetched) → per active storm, fetch `CONE/TRACK_latest.kmz` **only when
  `advNum` changes** (change-gate), unzip, parse, validate (ring closes,
  point count ≥ 2, taus monotonic), PUT `cyclolab/adv/{sid}.json` + the
  TCP/TCD text URLs. Cadence: advisories are 6 h + intermediates — a
  2-min poll of the cheap index with adv-gated heavy work.
- **JTWC sub-source (WP):** forecast positions from the existing
  knackwx/b-deck path + the versioned radii blob → derived-cone JSON in
  the same contract (`"method": "derived-mean-error-v…"`).
- **Source-freshness verified before wiring** (the 91W recycled-deck +
  deck-fetch lessons): each fetch records provenance (bytes, hash,
  issuance time parsed FROM the document, not assumed) and rejects a
  document whose parsed issuance is older than the last cached one.
- **Guards:** per-source isolation (inherited), kill-switch
  `CYCLOLAB_ADVISORIES` (house env idiom), failure = last-known-good
  JSON stays (pf standard).

---

## 10. Testing

- **Node-harness tests driving the real inline scripts** (the
  homepage-harness precedent, `tests/home_status_harness.cjs` style):
  shell hydration (fixture feeds → banner/chip/sections), category
  token switching (`data-cat` flips on fixture category change), cone
  JSON → SVG geometry builder (deterministic path output), odometer
  state machine, ENDED freeze (hydration no-op).
- **Parser tests:** NHC KML fixtures = Amanda's real adv #13 artifacts
  (checked into `tests/fixtures/`), malformed-zip/short-ring rejection;
  derived-cone construction against hand-computed buffers (radii blob
  pinned); issuance-regression rejection.
- **Parity:** the storm-card "Launch CycloLab" button lands in BOTH
  `render_storm_card` and `buildStormCard` — existing byte-parity suite
  extends automatically.
- **Synthetic-storm dev fixture:** a fake LIVE storm feed + advisory JSON
  (needed regardless — Amanda may dissipate before V1 lands; dev cannot
  depend on a live storm existing).
- **Mobile emulation:** the Phase-A harness asserts on every CycloLab
  page: viewport == device width, tap targets ≥ 44 px, section first-open
  payload ≤ 400 KB on Fast-3G, reduced-motion path renders final frames.

---

## 11. Research track (report only — not built)

Greenlight dispositions: **(b) is IN V1** (Overview stats banner, §7.1).
**(a) and (d) are KEEP-WARM** — recorded here as the committed roadmap
((a) = the V1.5 flagship follow-up), explicitly NOT built in V1.

| Candidate | Feasibility | Effort | Notes |
|---|---|---|---|
| (a) **Forecast-vs-reality scrubber** — HAFS frame + floater frame at the SAME valid time on one slider | **High** — both assets exist today with valid-time metadata (HAFS init+tau; floater frame timestamps). The join is pure time math; UI = one slider, two panes (or A/B wipe). Gaps: floater history depth (frames age out — need retention or accept "last N hours"), HAFS 6 h cycles vs floater ~10-min cadence (snap rule needed) | **M** | **KEEP WARM — V1.5 flagship** |
| (b) **Live per-storm ACE ticker** | Trivial — per-storm `ace` already in the feed; odometer on new fix | **S** | **IN V1** (Overview stats banner) |
| (c) **Auto plain-language storm history** | Medium — template-generated narrative from the points series (genesis, peaks, landfalls?) ; landfall detection needs coastline test (Natural Earth polygons already in repo) | **M** | Deterministic templates only (no LLM in the page path); V2 |
| (d) *Candidate:* **Cone-verification overlay** — past advisories' cones replayed against the actual track ("how did the forecast do") | High — advisories poller already caches per-advisory JSON; keep N advisories instead of latest-only | **S–M** | **KEEP WARM** — pairs with (a) as the honesty theme |
| (e) *Candidate:* **Side-by-side model strip** — when the registry grows past HAFS, synced multi-model loop on the storm-centered crop spec | Blocked on registry growth | L | Spec'd by §7.3's crop contract |

---

## 12. Rollout (stages + gates, shadow-first)

1. **Stage 0 — prerequisites:** CF route `triple-a-tropics.com/cyclolab/*`
   → R2 (user's CF account; Worker source vendored in-repo), `R2.put_html`
   in the poller repo. *Gate: a hand-PUT test page serves as text/html on
   the live path.*
2. **Stage 1 — advisories poller, shadow:** new Source writing
   `shadow/cyclolab/adv/*.json`; verify against live NHC advisories for
   ≥ 2 advisory cycles + derived-cone JSON for a WP storm/fixture.
   *Gate: parsed JSON matches the published advisory by hand-check.*
3. **Stage 2 — shell + Overview** (template generator, poller page
   writer, hydration, visual system, motion pass 1) on a synthetic storm
   at a shadow path. *Gate: art-direction review of the Playwright
   recordings (ramps, banner, odometer).*
4. **Stage 3 — Satellite + Models mounts** (hafs.js componentization
   lands with the /models/ mount proven byte-identical in behavior tests
   first). *Gate: /models/ regression suite green.*
5. **Stage 4 — Advisories + THE CONE.** *Gate: cone recording review +
   methodology panel copy sign-off.*
6. **Stage 5 — entry buttons on the maps (parity pair) + go-live.**
   Kill-switches: `CYCLOLAB_ADVISORIES`, `CYCLOLAB_PAGES` (poller page
   writer), and the CF route itself (instant un-route).

**Untouched throughout:** floater poller, HAFS worker, intensity feeds
(the new Source rides alongside; per-source isolation protects them).

---

## 13. Open questions for review

1. **CF route** — confirm you'll add the `cyclolab/*` route + are happy
   vendoring the new Worker's source in-repo (the proxy Worker's
   unauditability bit us in the deck investigation).
2. **Category ramp hues** — the seven anchors in §5 are proposals;
   art-direction happens on the rendered swatch recordings, not hex
   review.
3. **WP radii pin** — 2015 (best-verified, better-year values) vs 2017
   (more recent, worse-year values). Design pins 2015; flip if you
   prefer recency over sample quality. EP/AL always use the official NHC
   cone, so this only affects WP.
4. **V1 storm scope** — designed as designated-storms-only (invests get
   no page). Confirm.
5. **`MODEL_PLATFORM_DESIGN.md`** — doesn't exist; this doc follows
   SATELLITE.md / README-HAFS-WORKER.md conventions. If you have a
   different precedent in mind, point me at it and I'll restyle.
