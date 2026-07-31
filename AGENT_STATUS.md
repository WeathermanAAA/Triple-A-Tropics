# Agent status — Triple-A-Tropics autonomous work log

Maintained by Claude while Andrew is away. Updated after each meaningful step;
newest state first. Raw URL:
`https://raw.githubusercontent.com/WeathermanAAA/Triple-A-Tropics/main/AGENT_STATUS.md`

Standing convention (2026-07-24): every ship also flips its item in
`roadmap.yml` (drives the `/roadmap/` shadow board) in the same scoped
commit — status + `date_shipped`, new items for new work, bump `updated:`.
See CLAUDE.md § Roadmap board.

_Last update: 2026-07-31 ~02:2x UTC — **ENSEMBLE PAINTBALL SHIPS (models batch item 3) — VECTORS ON THE PAGE, PUBLISHER ON THE CRON.** The decode landed two sessions ago; this session built the publisher and the on-page rendering, on the same storm-keyed CycloLab surface as the guidance/SHIPS tabs. **VECTORS, NOT PNGs, and that is the design**: member tracks ship as parallel arrays in one per-storm `ensemble_v2.json`, so every member interaction is a free client-side operation — hover BRUSHING (a 9 px transparent hit path per member; a 1.5 px stroke is not a hoverable object), click-to-SOLO (per-leg SSHWS colour + daily dots, the rest DIMMED not removed so context stays), Ctrl/⌘-click HIDE (chip stays dimmed to re-enable), and the **MEMBER-AXIS SWEEP** — soloing each member in turn, the interaction a pre-rendered paintball PNG cannot do at all. The map frame is computed over the visible bundle and **held still during the sweep** (rescaling every tick makes nothing comparable). One source at a time (ECMWF ENS / GEFS segmented control) because two SSHWS-coloured bundles on one map are indistinguishable; the verifying best track underlies the bundle, read from the guidance document already loaded; longitudes are unwrapped SEQUENTIALLY per member into the continuous frame so a dateline crosser keeps a monotone axis. **PUBLISHER**: the storm list comes from `global_storms.geojson` — the ONE source carrying both the CycloLab sid AND the storm NAME, and the name is what the ECMWF match runs on (ECMWF numbers storms on its own sequence; DOLPHIN = their 15W, and their "12W" is a DIFFERENT system, so an id join attaches the wrong storm's ensemble — the worst failure this product has). Newest PUBLISHED 00/12Z cycle found by walking back; the fallback fired live on the first build (31/00Z 404 → 30/12Z, 63 storms × 51 members). BUFR records dedupe per storm-id keeping the fuller record so a duplicate can never make the name match ambiguous; eccodes failing to import degrades to GEFS-only rather than failing the job (and the workflow installs it as an OPTIONAL dep for the same reason — a wheel hiccup must not kill the guidance+SHIPS publishes sharing the job). **GEFS walks back past the CARQ-only leading edge** to the newest deck cycle actually carrying members — GENEVIEVE hit exactly this on the first live build (leading-edge cycle = CARQ only → no GEFS; one cycle back → all 31). **VERIFIED**: 22 Python tests + a browser harness exercising solo/hide/source-switch/sweep against the real component; live docs built for both active storms (GENEVIEVE ECMWF 51 + GEFS 31; DOLPHIN ECMWF 51 + GEFS 31, matched by name to ECMWF id 15W — the exact trap case). **FLEET NOTE: BOTH BOXES UNREACHABLE over SSH from this codespace (connect timeout, box1 AND box2, ~02:00 UTC)** — the hand-upload relay was therefore impossible and the documents publish via the update-guidance cron instead (which is the durable path anyway). If the boxes are still dark next session, check the fleet health page and Hostinger before assuming the codespace egress changed._

---

_Last update: 2026-07-28 ~21:2x UTC — **THE FOUR STORM PANELS BECOME ONE PLATE.** The pieces existed as separate products, which is fine for a page and wrong for a post: showing someone a storm meant handing them three images and explaining how they line up. `centerfix_plot.render_composite()` puts them on one 2x2 PNG (1800x1344 at dpi 120) under ONE header carrying storm, intensity, valid time and forecast hour. **TOP ROW** is the IR window twice — grayscale with BD-step contours and every centre estimate keyed (ARCHER/ADT track + 50/95% certainty rings, official b-deck position, official forecast track, floater box), then the same scene enhanced with the extremes tagged **IR / WV / SWIR** (SWIR is new; an untagged −60 C invites reading a WV frame as a cloud top). The **90-minute comparability gate is intact** and visibly working on the live render: 12W's official fix was 2.3 h from the emphasised objective fix, so the plate drew both markers, drew the connector in grey, omitted the km number, and said `OFFICIAL FIX 2.3 H APART — SEPARATION NOT COMPARABLE` in the header. **BOTTOM-LEFT IS CAPTURED, NOT PORTED.** The wind/pressure + observed/projected ACE chart's method has real subtleties — forecast intensity regridded onto the 6-hourly synoptic grid before summing (issued taus are 12/24/36/48/72/96/120 h, so summing them directly skips the points between and weights a 24 h gap like a 12 h one), only ≥34 kt counts, projection resumes from the latest observed fix because the b-deck runs ahead of the advisory — and a matplotlib port would be a second copy of that drifting invisibly while both drew plausible curves. So `wpace_headless.{cjs,py}` photographs the renderer that exists, exactly as `objfix_headless` does for ARCHER, riding the collector lane that already has Chromium. **Element-screenshot did NOT work and the failure was silent-looking**: every capture came back a flat 2963-byte slab of card background, identically sized for three different storms, because `elementHandle.screenshot()` clips the PAGE raster to the element's box and the chart sits well below the fold. The element was fine all along (79 SVG nodes, correct namespace, its own full-bleed background rect, populated subhead, zero page errors). Serializing the SVG's `outerHTML` and rendering it standalone has no scroll/layout/viewport dependency and lands at exactly the viewBox size asked for (1000x480 @2x) — the font FAMILY has to be carried across explicitly, since the chart's text has inline font-size but inherits its family and a bare wrapper rasterises every label in the default serif. Verified live on 12W: observed 125 kt, forecast peak 155 kt at T+48h, observed ACE **3.97 against the published feed's 3.97**, projected 45.42. **BOTTOM-RIGHT REPORTS A CONTRAST, NOT A COUNT.** The eye score is the ADT's documented relative — warmest eye pixel against the coldest eyewall ring, in °C — drawn as a radial IR profile with the citable BD ladder (CIMSS, 9 steps, WMG warmest at > +9 C) behind it. **A pixel count was the other candidate and was rejected on its own merits**: it is resolution-dependent, so the same eye counts differently on 2 km ABI than on a 4 km reprojection, and no primary source defines the product. What is cited (the quantity, the ladder) and what is OURS (eyewall = coldest mean ring beyond 12 km, the ring width, the radius cap) is separated in the docstring and printed on the panel. Ring width comes from the grid's own sample spacing — rings finer than a pixel report sampling noise as structure. **THE GATE THAT MATTERED**: the first live render said `EYE SCORE 16.8 °C` off a "warmest eye pixel" of **−62.5 °C** — deep convective cloud, not a cleared eye — while the method's own classifier on the same fix said `UNIFORM CDO` at `CONF 0.31`. A CDO still has a warmest pixel and a coldest ring and their difference is a real contrast that is not an eye score, so the gate now uses the ADT's OWN scene classification rather than a brightness threshold invented here to answer the same question worse; the profile still draws, the number is withheld, the panel names the scene. **ADDITIVE**: `render()` still publishes the two-panel plot to `cyclolab/centerfix/{sid}.png` — the two map panels moved into `panel_centres`/`panel_enhanced` and BOTH products call them, so there is one implementation rather than a copy; the 13 existing contract tests pass untouched and the two-panel PNG came out **byte-identical in size (770334) across both live renders**, which is the evidence the extraction was faithful. Plate lands at `cyclolab/centerfix/{sid}_plate.png`. **Three defects came out of LOOKING at the render, not the tests**: the eye panel's tick labels and x-label ran through the footer strip (the bottom row sat on `ftr_h` with no room beneath it), the score drew under the panel label, and the construction note ran off the right edge. 16 new tests (29 in the file). tsr @26b91fe, @b7bf77b, @fa502c5; both lanes recreated on the new image. **QUEUED (Andrew)**: nothing blocking._

_Last update: 2026-07-28 ~20:0x UTC — **THE LIVE FEED WAS FIVE MINUTES LATE ON EVERY ADVISORY, AND IT WAS THE RETRY POLICY.** Reported as "Dolphin shows 100 kt, tcvitals has 125" — the diagnosis inverted the premise but found a real defect underneath. **WHAT THE SOURCES ACTUALLY SAID at 19:19-19:24Z:** the UCAR season file was fresh (Last-Modified 19:16:37Z) and its newest 12W record was **12Z / 51 m/s = 100 kt**; the NOMADS 18Z cycle 403'd (not published); JTWC's own WTPN31 was warning **NR 007, issued 281500Z, warning position 281200Z, 100 kt**. tcvitals did not have 18Z. **The b-deck did** — natyphoon posted `bwp122026.dat` with the 18Z BEST rows (125 kt / 937 mb / TY) at **19:13:54Z**, so this cycle the b-deck LED tcvitals and `prefer_bdeck` was correctly suppressing nothing. Every layer the report suspected was working: `basin_cfg["tcvitals"]` is `true` in the published base, the leg fires **every** WP cycle (`<ucar-season ok records=348>`, `<tgftp-pn ok records=4>`, `73 fix(es) older than the 48 h lead window`, `tcvitals: no fixes beyond the b-deck`), and **an untyped fix renders while only its ACE is withheld** — proved at runtime, not by reading: an IND-nature 125 kt fix comes out of `merge_and_extract_storms` as `is_active: True`, `current_category: C4`, `peak_wind_kt: 125`, `peak_pressure_mb: 937`, point serialized in full, with `ace` 1.0 against 2.562 for the same fix typed. `_serialize_point` has no nature filter and the active gate excludes only ET/DS, so `IND` passes both. **THE ACTUAL DEFECT.** The feed did carry 18Z — at 19:18:59Z, five minutes after the b-deck had it, and the CDN edge served the older copy for ~2.5 min beyond that. `fetch_live_bdecks` walks a mirror chain per storm number and gave **each mirror** the full `FETCH_POLICY` (4 attempts, 2/4/8 s backoff) before trying the next — which defeats the point of having a second mirror, the chain IS the redundancy. It only became expensive because **our own ATCF proxy Worker answers an upstream 404 with a 502** (natyphoon and ftp.nhc both return a clean 404), and `_get_text` correctly calls 5xx transient. So the three consecutive absences that END each basin's sweep burned the entire retry budget, every basin, every cycle: measured on box1 against the live chain **WP 100.4 s, AL 57.3 s, EP 113.0 s = 270.7 s**, which is why the poller's real period was **~7.2 min** against `POLL_INTERVAL_S=120` (cycle markers 18:01:52 / 18:09:06 / … / 19:21:08, dead consistent). **THE FIX** (tsr @18434d1): walk the chain with retries OFF, escalate to the full policy only when NOTHING in it answered — a clean 404 or a 200 without a BEST row is a definitive "this number does not exist" and needs no retry, while an all-errors chain is a real outage and still gets the budget. Trading a latency bug for a resilience bug is not a fix, so both halves are pinned by tests (3 new, suite 34 green). **DATA-IDENTICAL per the working agreement**: old and new run back-to-back against the live chain return frames comparing `.equals()` **True in all three basins**, same rows, same storms — `270.7 s -> 4.4 s, 61x`. Deployed: image rebuilt, `intensity-poller` recreated alone (`--no-deps`), first post-deploy WP leg completed **23 s** into the cycle against ~5 min before. Push relayed through box1 again (this codespace 403s on tat-satellite-render) via a `refs/relay/` ref, box working tree verified untouched before and after. **QUEUED (Andrew)**: the proxy Worker should return 404 for an absent deck instead of 502 — the poller is immune either way now, but the 6-hourly cron generators sweep the same chain and still pay it. Its source lives only in the CF dashboard and has never been back-vendored into `workers/`, which is the other half of that item (`atcf-proxy-404` on the board)._

_Last update: 2026-07-28 ~19:4x UTC — **GUIDANCE LANDS ON CYCLOLAB (item 8), AS ONE COMPONENT, WITH BASIN SCOPING ENFORCED IN THE DATA.** Andrew's call: CycloLab, not /models/ — it is already the storm-keyed surface (per-storm identity, OG tags, deep links, Models/Recon tabs), and /models/ stays the model-first browse surface with its own orthogonal URL scheme later. **ARCHITECTURE — the proven cross-repo pattern, and a fork retired.** The guidance viewer is now ONE implementation in the main repo (`guidance/guidance.js`), lazy-loaded by the CycloLab shell and mounted locked to `stormLock=SID`, exactly like the Recon/ASCAT/HAFS mounts. It replaced a **252-line INLINE implementation inside `cyclolab_shell.py`** — the exact second copy the brief said not to create, and the one that had already drifted. `gShips` stays (its own feed + renderer) until the SHIPS panel moves onto the component too. **WHAT IT DRAWS.** (1) Spaghetti tracks: every aid coloured by forecast intensity on the shared SSHWS hues, with OFCL drawn heaviest in white with τ labels and the verifying BEST TRACK distinct — an unlabelled bundle is decoration; those two traces are what the reader is comparing against. (2) Intensity with the **OCD5 no-skill baseline** drawn last and heaviest (white dashes): without it a guidance chart cannot answer the only question that matters when the aids disagree — is any of this beating climatology-and-persistence? (3) **Consensus membership in THREE states** — present / absent / **WITHHELD**. (4) Early/late badging, dashed on both plots, never blended. **THE HONESTY FEATURE, and why the third state is the whole point:** rendering a withheld member as merely "absent" implies it did not run. The truth is it ran and NHC does not ship it to the public feed. On live EP07 the strip shows 5 consensus aids, every one flagged NOT REPRODUCIBLE, with 8 withheld vs 2 absent chips visually distinct. **BASIN SCOPING IS ENFORCED IN THE DATA, NOT THE STYLING.** `aids.classify()` is basin-aware and **refuses** to return CONSENSUS for a JTWC aid; the builder emits no membership and no baseline there; the viewer drops the Consensus tab entirely and states why. **THIS FIXED A LIVE DEFECT**: the existing per-storm document listed `"consensus": ["AEMN"]` for DOLPHIN (WP 12) — AEMN is the GEFS ensemble MEAN, i.e. **one model averaged with itself, presented as several independent models agreeing**, in a basin that has no consensus aid at all. ENSEMBLE_MEAN is now its own kind, distinct from CONSENSUS, everywhere. **SOURCES.** AL/EP/CP from ftp.nhc.noaa.gov; **WP/IO/SH from UCAR — NHC carries ZERO JTWC-basin decks** and UCAR is the only survivor (EMC/ospo/metoc all 403). The repo already treats that host as primary for tcvitals, so no new failure domain; its a-decks are uncompressed and its b-decks year-nested, so the fetcher sniffs gzip magic rather than trusting the extension. **A SEPARATE R2 KEY (`guidance_v2.json`)**: the render box already writes `guidance.json` under the same prefix, and two writers on one object with different shapes is how a schema starts flapping. The viewer prefers v2 and falls back to legacy, normalising it once with the missing pieces explicitly EMPTY — so a panel that cannot be honest does not render, rather than rendering something invented. The sync is deliberately **not** `--delete`. Longitude maths runs in a continuous frame throughout, so a dateline storm plots in its own basin. **VERIFICATION**: 36 Python tests + a 28-assertion playwright harness that renders both an NHC and a JTWC document and asserts what the viewer must REFUSE to draw; the tsr tab tests are rewritten to pin the component contract plus a guard that FAILS if an inline renderer is ever reintroduced there (that reintroduction is how the copies drifted the first time). Screenshot-verified through the REAL regenerated shell for live **EP07 GENEVIEVE** (4 tabs, 33 aids, OFCL + best track, OCD5, 5 consensus all not-reproducible) and live **WP12 DOLPHIN** (3 tabs, 56 raw ensembles, no Consensus tab, no baseline, reason shown) — zero page errors in either. **Pre-existing failures confirmed NOT mine by re-running with my changes stashed**: `test_cone_is_blue_glass_with_solid_white_centerline` (the '2 5' dasharray appears twice at HEAD too). Three basemap tests counted "all THREE basemap sites" — gBasemap was the third and left with the renderers, so they count two now and say why. **TSR PUSH RELAYED VIA BOX1** (this codespace has no write credential for tat-satellite-render — 403): bundled `012cb80..3e33789`, fetched it into a `refs/relay/` ref on box1 and pushed THAT ref to origin/main, so **the box's working tree was never touched** — verified byte-identical before and after, which mattered because it carries unrelated uncommitted drift from another session. **NOTE FOR THE 18:5x SESSION**: the roadmap.yml edits you held off on are safe to make now — that file was mine and is committed. **QUEUED (Andrew)**: nothing blocking. `update-guidance.yml` uses the existing R2 secrets and populates `guidance_v2.json` on its next 6-hourly run; until then the component falls back to the legacy document._

---

_Last update: 2026-07-29 ~16:5x UTC — **HAFS INGEST: BUDGET RE-DERIVED, CONCURRENCY MEASURED ON REAL HARDWARE** (@5c633b74, tsr @b713771). Cashing in the 3.64→1.96 GB win meant re-deriving the gate constant and measuring the scaling curve rather than asserting one. **Both premises the budget rested on were wrong.** (1) **GRID VARIANCE IS ZERO.** The comment claimed "+17% for the largest parent grid observed" — probed the hafsa parent `.atm` domain in **every basin HAFS has run** (al, ep, cp, wp, io, sh): all **1361×1681, 81.6°×100.8° at 0.06°, identical**. The domain is storm-following in POSITION, not size, so the calibration grid IS the largest grid. The 17% was inferred from cache FILE sizes (166–192 MB across storms) — that spread is the weather's **compressibility**, not geometry. Warn tolerance 1.17 → 1.05. (2) **THE BASIS WAS A FRAME, NOT A WORKER.** One parent env frame peaks at **1913.9 MB and is essentially deterministic** (1913.9 / 1913.8 / 1913.9 across three storms in two basins — unsurprising once the grid is known fixed). But a WORKER's high-water climbs **+21 MB/frame, LINEARLY, no plateau over 12 frames**, and `malloc_trim` does not reclaim it: 1914.7 MB after the first frame, **2170.7 after the twelfth**. `_fit_ingest_width` divides by a per-WORKER number, so 2171 is the basis. **2300 survives** (= 2171 + ~6%) — right value, previously for the wrong reason. This makes `_INGEST_TASKS_PER_CHILD` **load-bearing, not a nicety**: unrecycled over a cycle's ~258 frames a worker would reach **~7.3 GB** and OOM every host; the two constants are coupled as `high-water ≈ 1915 + 21·(N−1)`. **SCALING CURVE, measured on box2** (8 vCPU, ~2.5 cores of emit work already running, 8 real parent frames/pass): width 1 = 383 s (1.00×), **2 = 203 s (1.89×, 94%)**, **4 = 112 s (3.43×, 86%)**, **8 = 83 s (4.61×, 58%)**. **The knee is between 4 and 8** — width 8 is the best absolute wall clock and is what the box worker is set to, but it buys +34% over width 4 for 2× the RAM and 2× the cores, so a box with heavy co-tenants should prefer 4–6. **THE COST ANSWER: RAM IS NO LONGER THE BINDING CONSTRAINT — CPU IS.** Before, RAM capped a box at 4 ingest workers while its 8 cores sat idle; now RAM allows 8 and the cores are what bind. Per-box ingest throughput **1.70×** (1.34× from the width the memory now permits × 1.26× from the faster frame, same-host), so a fixed workload needs **~41% fewer boxes** — not the ~50% a pure RAM calculation would suggest, precisely because CPU now binds first. A 6-storm cycle goes **~1.48 h → ~0.87 h**. **ALSO FOUND, and sharp:** `max_tasks_per_child` makes `ProcessPoolExecutor` use **SPAWN, not fork**. The entry point must stay `__main__`-guarded or every worker re-runs the module and the pool dies — verified end-to-end on the real CLI (3/3 ingested, 36/36 rendered, manifest written, and the guard logged `ingest width 2 -> 1: 4257 MB available`); an unguarded benchmark harness of mine died exactly that way, which is how it surfaced. Spawn also means workers share nothing with the parent, so `width × per-worker budget` is genuinely the right model rather than a conservative over-estimate. Recycle cost measured: 1.0–2.2 s to import the package fresh, ~0.3%. **Box2 benchmark artifacts removed; load and container count back to baseline.**_

---

_Prior: 2026-07-29 ~05:4x UTC — **HAFS INGEST MEMORY: PARENT-FRAME PEAK 3.64 GB → 1.96 GB, BYTE-IDENTICAL** (hafs-render v0.13.0 @e8dc70dc, tsr @4d96123). The ingest was memory-bound and the standing workaround was to narrow the pool (`HAFS_INGEST_JOBS` 8→4 on the box, `--ingest-jobs 2` on the runner) rather than fix the usage. **Profiled it per stage first, and the peak was not where anyone would have guessed**: not the GRIB decode, not the xarray dataset, not the NetCDF write — **the MetPy 200 mb PV solve, 1848 MB of a 3438 MB parent-frame peak (54%)**, run over a 7-level pressure stack when `potential_vorticity_baroclinic` differentiates the vertical with a **3-point stencil** and therefore cannot see past an interior level's two neighbours. `PV_STACK_LEVELS` 7→3 is **bit-identical, verified not assumed** (`tests/test_field_memory.py` asserts BIT equality of the 200 mb slice against the old deep stack, and asserts the mapped level stays INTERIOR — at an end its derivative goes one-sided and the equivalence silently dies). Next: the three 17-level layer-mean integrals held **three** full float64 copies of a (17,ny,nx) stack at once (~890 MB) because the level select/order ran AFTER the widening; selecting on the decoded float32 and widening ONCE is bit-for-bit the same (widening is exact and elementwise, so it commutes with permutation) at half the transient. Plus: cfgrib datasets are now closed after their values are taken, `malloc_trim()` runs between frames, ingest workers recycle every 12 frames, and **`load_frame` reads only the variables its product draws** — an entry holds the union of all 21 products' fields and every render task was materialising all 39 of them (**908 MB → 289 MB**). **THE DTYPE HYPOTHESIS WAS TESTED AND REJECTED ON EVIDENCE.** Every one of the 38 GRIB reads a parent frame makes does return float32, and `.astype(float)` was doubling all of them — but float32 **end to end** is not free: it **moves pixels**. `matplotlib`'s streamline integrator is an ODE solve and amplifies a last-ulp dtype difference into a different trajectory — **5.0% of pixels on `env_shear_500_850`, and 0.8% on `hgt_wind_700` whose field VALUES were bit-identical** (only the container dtype changed). 54 of 87 PNGs differed. So the policy is: float32 for **pass-through storage only**, derived fields stay float64, and `_pack_frame` **widens back at the render boundary** — the counter-intuitive step, which is why it is written down in a policy block rather than left to be inferred. Second surprise: **the on-disk win was ~0 either way (1.3%)** — zlib was already compressing away the 29 trailing zero mantissa bits of a float64-widened-from-float32, so the cache file already cost about float32; the 2× was only ever paid in **memory**. (That also explains the old "zlib compresses float64 height/vort grids less" note — it was seeing the genuinely-derived float64 fields.) **MEASURED, same 6 real parent frames before and after** (hafsa 2026072818, 12w/06e/07e): peak RSS/worker **3.64 GB → 1.96 GB (−46%)**, inter-frame floor **832 MB → 337–603 MB**, ingest wall **61.2 s → 48.5 s/frame (−21%)**, single-frame ingest 77.5 s → 45.3 s. Per-worker peak is **independent of pool width** (1956 / 1927 MB for two workers at width 2), so the budget is simply width × ~2.3 GB — hence **the box worker goes `HAFS_INGEST_JOBS` 4 → 8** (18.4 GB against its 24 GB limit, 5.6 GB spare). The GH runner **stays at 2** deliberately: 3 × 2.3 = 6.9 GB leaves nothing on a 7 GB runner, and an OOM there costs more than the third worker earns. **PARITY GATE: 87/87 PNGs byte-identical and 175/175 field values bit-identical**, 21 products × 5 real frames, both domains — the gate is what caught the float32 regression, and it ran before, during and after. **COST MEANING:** RAM per concurrent ingest worker is the unit that sets box count for the ~50-model buildout; it fell 46%, so a fixed RAM budget now buys **~1.9× the ingest concurrency**. **WATCH:** the box needs hafs-render ≥0.13.0 on its worker image, and 8 is a MEMORY ceiling — the wall-clock gain is core-bound on 8 shared vCPU, so the first cycle after deploy is worth a look._

---

_Prior: 2026-07-28 ~18:5x UTC — **CYCLOLAB STORM-DIAGNOSTIC SUITE: W&P/ACE, BT HEADER STATS, OBJECTIVE CENTRE-FIX PLOT.** Three ships plus one research finding. **(1) W&P becomes a two-panel diagnostic** (tsr @008f566): top panel observed wind solid + OFFICIAL FORECAST wind dotted on the kt axis with pressure dashed right, both peaks labelled; bottom panel cumulative OBSERVED ACE solid + PROJECTED ACE dotted, both maxima labelled. ACE gets its OWN panel, not a third axis — it shares no scale with wind. **The method detail that matters:** ACE is defined on 6-hourly synoptic points but forecasts are issued at 12/24/36/48/72/96/120 h, so summing the issued taus would skip the points between them AND weight a 24 h gap like a 12 h one (the 72→96→120 tail counting once per day while the first two days count twice). Forecast intensity is interpolated onto the 6-hourly grid first, only ≥34 kt points sum, and the interpolation is DISCLOSED on the panel as ours. The projection RESUMES from the latest observed fix, not the advisory t0 — the b-deck runs ahead of the advisory (Dolphin: newest fix 12Z vs t0 06Z) so summing from t0 double-counts the overlap. Projected ACE never enters the season total. The observed curve mirrors ace_core's gate client-side; the test pins it to **ace_core.storm_ace itself**, not a constant — which caught that the synth fixture's `ace: 12.34` field is decorative while ace_core computes 11.72 for its points (ET-nature fixes are not ACE-eligible in EP). Also pinned: TZ-independence, since JS parses naive ISO as LOCAL and the synoptic gate would shift with the viewer's clock (tested UTC/+14/−11). Verified live: Dolphin 2.41→39.18 over 19 interpolated points, Fausto 18.23→19.33, both observed totals equal their published ACE. **(2) BT header stats** (tsr @dd8d07a): the frame's min/max brightness temperature moves into the header TAGGED BY BAND (IR BT / WV BT / SWIR BT) — an unlabelled min/max invites reading a WV frame's −60 C as a cloud top. Visible/true-colour get no readout rather than reflectance printed in degrees. It also moved OUT of the map axes, where its opaque backing box was overwriting the very pixels the explorer's objfix reads BT back out of. **title_h stays 0.06** — objfix_sources.js LAYOUT.dataY1 is pinned to it, so a taller strip would silently shift every floater fix; a test guards the constant. **(3) OBJECTIVE CENTRE-FIX PLOT** (tsr @0c76e3b, @9609630; main @28a3916b). The fixes existed only in a browser tab. **We did NOT reimplement ARCHER** — ~1600 lines of sourced numerics with documented departures D1–D7, and two copies would drift invisibly while both emit plausible lat/lons. Instead `objfix_headless.cjs` boots the REAL explorer in headless Chromium and drives the existing `window.ObjFixPanel` seam, reading back the same trackJSON() the download button produces; the Python wrapper publishes to R2. Driving the PANEL (not archerFix directly) also preserves the orchestration — the per-frame first guess must stay the official-track anchor or the track drifts. `centerfix_plot.py` then renders the 2-panel PNG server-side: grayscale IR + BD-step contours with every centre estimate keyed (ARCHER/ADT track, newest ACCEPTED fix carrying the crosshair + ARCHER's own 50/95% certainty rings, official position, official forecast track, floater box) and the official-vs-objective separation drawn and labelled in km — that disagreement is the product. Second panel: enhanced colour IR + IR/WV BT extremes. **SATCON is an INTENSITY readout only** — it produces no position, so it is never a centre marker, and when its own ≥2-member rule is unmet the header says 'no consensus' rather than relabelling bare ADT (needed a small read-only `SatCon.latest()` export, main @28a3916b, ?v= bumped with it). Verified live: Dolphin's ARCHER fix sits **139 km** from the official position; SATCON 88 kt from ADT 99.6 + AMSR2 49.1 against an official 100 kt. Three defects found by LOOKING at the render: the view framed the tilted geostationary parallelogram instead of the requested box; the crosshair/rings/km hung off the newest FRAME, which is often a REJECTED candidate (publishing a position ARCHER refused); and the footer's two columns collided. All fixed, all disclosed — including a truncated collector run. Two compose lanes behind a `centerfix` profile, decoupled through R2 (browser lane needs chromium, render lane needs cartopy; neither should carry the other's stack). **(4) WMG — RESEARCHED, NOT BUILT, as instructed.** WMG = **Warm Medium Grey**, the WARMEST step of the Dvorak BD-curve enhanced-IR ladder, **> +9 °C** (CIMSS tropic.ssec.wisc.edu; 9 steps OW +9→−30, DG −30→−41, MG −41→−53, LG −53→−63, B −63→−69, W −69→−75, CMG −75→−80, CDG < −81). So a WMG pixel in an eye is a deeply-subsident, cloud-free eye — the classic T7.0 'warm medium grey eye in a white CDO' signature. **The DISCRETISATION is fully specified and citable; the COUNT is not.** No primary source defines a 'WMG count' product: not the region it is taken over, not pixel-count vs area, not the grid. And a raw pixel count is RESOLUTION-DEPENDENT, so it is not comparable across sensors — the same eye counts differently on 2 km ABI vs a 4 km reprojection. Recommendation if we build it: implement the BD discretisation exactly (citable), report **area in km²** (resolution-invariant) with the pixel count and grid stated beside it, over an explicit radius from the working centre, and flag the region/threshold choices as OUR construction — the ADT's own documented relative is its eye score (coldest eyewall BT vs warmest eye pixel). **QUEUED (Andrew):** the two centrefix lanes are code-complete and profile-gated but NOT yet started on the box — `docker compose -p tat-render -f docker-compose.render.yml --profile centerfix up -d` after a pull; the collector pulls a ~1.5 GB Playwright image on first start. Also queued: a roadmap.yml item for this suite was NOT added because a concurrent session had that file open with uncommitted edits, and stomping it would have cost their work._

---

_Prior: 2026-07-28 ~15:2x UTC — **/models/ PHASE 0 FOUNDATIONS + THE GUIDANCE LAYER STARTED.** Five foundation pieces, all cheap now and painful to retrofit, none of which changes a rendered physics frame. **(1) MODEL RECORD** (`hafs_render/model_registry.py`) — every model carries `convection_explicit` and an `ai_paradigm` enum. The convection boolean STRUCTURALLY gates reflectivity and simulated 89 GHz: a "reflectivity" field off a parameterised-convection model is not a weak signal, it is a **category error** — the hydrometeors it would come from are a diagnostic of the microphysics applied to grid-scale saturation, and the convection that would actually make the echo was removed from the grid by the cumulus scheme; simulated 89 GHz is worse, since its entire signal is ice scattering by convective cores, so the render would be a blue field with no storm in it. Both produce plausible images that mean nothing, which is worse than producing nothing. So it is enforced **in depth** — dropped at job planning, refused with `IncompatibleProduct` at the renderer, therefore never in the manifest and never offered by the frontend. Unknown model/product ids are DENIED, not waved through. Simulated IR/WV are deliberately **not** gated (grid-scale cloud + upper-tropospheric humidity survive a cumulus scheme; gating them would be over-application). The AI enum drives a badge and **withholds the intensity statistic** — current emulators train on ~0.25° reanalysis with a loss that rewards smooth fields and systematically under-deepen TCs, so their VMAX/MSLP extrema are resolution artifacts, not forecasts; the field still renders, only the number is withheld. The gate keys on CONVECTION, not on being AI, so a convection-permitting emulator may render reflectivity and still be denied an intensity claim. **(2) ENSEMBLE-MEAN POLICY** — per product, plus two structural rules so no spec restates them: a storm-following nest denies the mean for EVERY product (members centre nests on their own forecast positions, so a cell-wise mean averages different places), and the MSLP-minimum marker is dropped in any mean render — which is what lets the height products stay allowed despite drawing an L. Denials name a SUBSTITUTE (spaghetti / probability / percentile / envelope / member-picker) so a denial is constructive; an import-time validator enforces the biconditional. **(3) QUANTITY-KEYED PALETTE REGISTRY** (`tat_palettes/quantities.py`) — scales owned once per PHYSICAL QUANTITY, not per product. The failure only appears at scale: at ~50 models one quantity is drawn by dozens of products, and unless all use identical fixed levels a side-by-side comparison measures the palette, not the forecast. 16 quantities; wind speed now shared by 4 products, BT by 3. Keys are granular enough to pin ONE scale each, so 850 vs 500 mb vorticity stay separate — the comparison that must stay valid is model A's 500 mb against model B's. **Verified byte-identical**: every product's cmap, range, boundaries and a 9-point RGBA sample unchanged. **(4) MANIFEST GEOMETRY + VALUE PLANES** — `render_frame` now returns the drawn axes rect in output pixels plus its lon/lat extent. **The rect is read AFTER `savefig`**: the map letterboxes inside its fixed square box via `set_aspect`+`set_anchor` and matplotlib only resolves that during the draw, so reading it earlier gives a rect wrong by the letterbox margin and every pixel→latlon mapping built on it is silently offset. Geometry is keyed by FRAME (all 21 products share one axes) and pruned in lockstep with frames. Canvas constants computed 1963×1813 and the LIVE published PNG measures exactly 1963×1813. **(5) VIEW TELEMETRY** (`models/telemetry.js`) — the hero-set scheduler's popularity term. A view is a DISTINCT (cycle, storm, model, domain, product) tuple, **not a call**: `_selectProduct` fires from nine paths including the 45 s poll's selection regrow, so counting calls would score a product by whatever else the user changed. Dwell accrues per view, capped, paused while hidden. No identifiers of any kind; DNT and GPC disable collection outright. **GUIDANCE LAYER STARTED** (`guidance/atcf.py`, 47 tests) — a-deck/b-deck ingest + QC off ftp.nhc.noaa.gov, now the sole authorized publisher. **THE PUBLIC A-DECK IS FILTERED, verified not assumed**: across all 23 live 2026 decks (521,842 rows, 106 aids) EMX/EMXI/EMX2/EEMN/EMNI/SHPE/DSPE/LGME/EAIO/EAMN/UKM/UKMI/UEMN/FSSE/GFEX have **exactly zero rows**, and no aid id starting with "E" exists at all. It is withholding, not absence — NHC's techlist defines them and the post-season archive carries them (2025 AL05: EMX=2150, UEMN=1342). No public unfiltered fallback; `/atcf/aid/` 404s. **The consequence is now encoded and surfaced**: TVCN/RVCN etc. are plottable but NOT independently reproducible, because they were computed upstream from members we cannot see — a present consensus aid does not imply its members are present. QC calibrated on the real decks, not the format doc: MSLP==0 is MISSING (28.9% of rows), position **0N/0W is a sentinel on 9,561 rows** and is the format's most dangerous value (syntactically perfect, lands at null island), −99 is a SECOND sentinel, rows are variable-width 18–46 fields, TAU can be negative, and **the primary key is (BASIN,CY,DTG,TECH,TAU,RAD) — not the triple**, since the 34/50/64 kt radii rows legitimately share it and deduping on the triple would silently discard ~110k real records. **Ordering is load-bearing**: the motion check runs only AFTER position QC — before it the sentinel alone manufactures 288 false flags with implied speeds to 754 kt (there is a regression test for exactly that). Live validation: 123k rows, zero malformed, no sentinel reaching a row, and the single genuine motion flag (61.4 kt, RVCN in aal012026, TAU 72→84) reproduces exactly. **BASIN SCOPING RESOLVED — and the previous session's open question REFUTED.** That entry ended "if WP guidance is still stuck on 12Z at the next check…" and flagged a 16.9 KB sample for 11W. Re-sampled across cycles: the feed is **STABLE, not stalled**, and the one-cycle lag **does not reproduce** — at a simultaneous fetch, WP 12W and the NHC EP decks shared an IDENTICAL max DTG and an identical 2.1 h lag, with gap-free 6-hourly ladders in every deck tested. The 16.9 KB figure matches no endpoint found (awp112026.dat is 1.48 MB), so that sample was bad. Two findings reshape the work: **ftp.nhc.noaa.gov carries ZERO WP/IO/SH decks** — UCAR's `adecks_open` is the only surviving source (the repo already treats that host as primary for tcvitals, so no new failure domain) — and, more consequential, **JTWC-basin decks have never carried official, consensus or statistical aids**: no OFCL, TVCN, IVCN, SHIP, LGEM, HWRF, HFSA or AVNO, 52 techs fewer than NHC, raw ensembles only. So computing a member consensus and labelling it TVCN would be factually wrong, and the official track must keep coming from the warning-text path. Scoped best-effort on the board. **DATELINE — the manifest now DECLARES its longitude frame.** A West Pacific nest across the antimeridian is drawn on a CONTINUOUS lon axis running past +180 (e.g. 168..188), because signed −180..180 is non-monotonic there and would blow the extent out to ~360°. That continuous frame is the ONLY one in which pixel↔lon is the exact affine, so normalising the published bbox would make it non-monotonic and silently wrong — but leaving it undeclared would hand a consumer a lon of 188 with no warning, and putting a WPac storm in the Atlantic is precisely the class of bug the previous two sessions just spent their time fixing. So the projection block now carries `lon_frame: continuous` + the display rule, and each frame carries `crosses_antimeridian`. Do the affine in the published frame; normalise only for display. **ALSO FIXED**: `render_frame`'s docstring still described a per-side `BBOX_TRIM_DEG` trim on the storm nest — that constant was removed when the nest moved to the storm-centered `NEST_VIEW_DEG` window and no longer exists anywhere in the module. **SHIPS RECON LANDED (next build, item 7).** Filename is `{YYMMDDHH}{BB}{NN}{YY}_ships.txt` — 24 chars with a **TWO**-digit storm year, not four. **AL/EP/CP only — there are NO JTWC-basin SHIPS files** (a WP probe 404s), which matches the a-deck picture. Every columnar block is FIXED-WIDTH and must be sliced, not split: two contribution labels are exactly 22 chars and butt against the first cell, so a naive `.split()` merges label and value. `TOTAL CHANGE` equals the sum of its 19 components exactly across 283 files × 16 columns — the decomposition is safe to render as a waterfall. Sentinels are ROW-SPECIFIC (N/A, LOST, xx.x, DIS, ERR, 999/9999). The 4-digit year in the banner is the COEFFICIENT year, not the storm year, and the RI predictor table's labels, ordering and column offsets all change between coefficient years and between basins — it must be keyed by label string, never by row position. Line counts range 99–116, so anchor on strings, not line numbers. **Two corrections to the spec's premise**: RHLO/RHHI are NOT in the stext bulletin (only 700–500 mb RH is) — the low/high RH rows live in the 1:1 sibling `/atcf/lsdiag/{stem}_lsdiag.dat`; and there is NO archive anywhere on the NHC FTP for stext or lsdiag, so any historical panel requires continuous harvesting starting now. Test decks (cyclone number 80–89: GSTEST/ATCFTEST/GCOLTEST/MLCOLTEST, forecast VMAX to 337 kt) sit in the same directory and must be filtered. **VERIFICATION**: hafs_render 24→65 tests (correcting a mis-stated "58→92" in commit 719203ab's message — 34 was the new-test count, but the base was 24, and 7 geometry-contract tests were added after); guidance 47 new; full suite green but for the 4 known pre-existing failures (3 enscenters ECMWF-mirror errors from a missing optional `ecmwf` module, 1 tiled-viewer playback — both confirmed failing identically with my changes stashed). Screenshot-verified: the deployed page renders unchanged with HAFS-A keeping its `VMAX 60.2 kt / MSLP 995.7 mb`, and under a synthetic current-shape manifest carrying a coarse AI model the badge renders and the product list drops 21→19 with reflectivity and 89H gone. **NOTE — the frontend gate is currently INERT on the live site by design**: the live manifest is written by the box render worker, whose published copy predates these commits and carries only `{slug,label}` / `{slug,label,short}`, so the new fields are absent and the JS degrades to today's behaviour. The BUILDER-side gate is what protects correctness and is already structural. The worker runs `python -m hafs_render.generate_hafs_plots` as a subprocess, so it picks all of this up on its next package update — no cross-repo schema change needed. **ONE WORKING-TREE NOTE**: a stale `stash@{0}: autostash` from another session (carrying ace_core, stream/index.html, generate_velocity_potential.py, tests/stream_smoke.cjs) blocked all commits via two unmerged ACE artifacts; resolved to HEAD (== what was live) and **the stash was deliberately preserved, not dropped** — that other work is still recoverable from it._

---

_Prior: 2026-07-28 ~14:4x UTC — **TWO DATELINE RENDERING BUGS FIXED (DOLPHIN/12W), ONE ROOT CAUSE.** Both live surfaces broke on the same thing: longitude measured on RAW feed values across the antimeridian, where a crossing arrives as a sign flip (-178.9 -> +179.8) whose raw midpoint is the ANTIPODE of the true centre. **(1) Home map track break** — `global_tracks.html` showed a hole mid-track with non-tropical triangles either side. NOT missing fixes and NOT the tcvitals seam: the b-deck is continuous 6-hourly 07-25T00 -> 07-28T12, and the gap sat exactly on the crossing. `ace_core._split_at_antimeridian` correctly refuses to emit a LineString across ±180, but it cut BETWEEN the two real fixes, so each half stopped at its own last fix and left a 1.3° hole that read as missing data. The split now CARRIES the crossing point (latitude interpolated at ±180, appended to the outgoing half and prepended to the incoming one) so the halves abut. Honesty preserved: only the ±180 leg gets an inserted vertex, so a genuine reporting gap still renders as a break. **(2) CycloLab plot extents** — TRACK HISTORY and WIND HISTORY drew on a near-global grid labelled to **130°N / 105°S** with the storm a speck. Andrew's prime suspect was a bad four-quadrant radius; **it was not** — Dolphin's radii are clean (max 95 nm). Those two panels are simply the ONLY callers of `ensureMinExtent`, which measured lon span/centre on raw values: min -178.9, max 179.8 -> centre **0.45°E**, planting its four pad points over AFRICA, exploding the fitted x-span ~10x, collapsing fit-to-contain's scale, and (panel height being fixed) stretching the LATITUDE axis past both poles. Reproduced headlessly against the deployed page: lon axis read 5°W-30°E, and at a narrow card aspect the parallels ran to 115°N. Fixed by hoisting `normLon`/`FRAME_LON` to module scope (there were THREE verbatim copies) and running all extent maths in that shared continuous frame; `gFitFrame` (guidance panel) had the identical latent bug and was fixed with it, and the swath sweep was lerping raw lon, smearing wind blobs the long way round the globe. **Structural guards added as asked:** latitude clamped to [-90,90] in the graticule so an impossible label can never draw whatever upstream did; `sanitizeExtent` drops non-finite geometry before any span maths (one NaN poisons Math.min/max for the whole extent); `radiusNm`/`maxRadiusNm` reject NaN, negative and SENTINEL radii (999/9999 nm — 9999 pads the window by 167° and blows it apart by a different route). **Verification:** A/B of the pre-fix vs fixed shell over 4 real storms — Bertha (AL), Fausto + Genevieve (EP) render **byte-identical SVG** (same SHA-1, both plots); only Dolphin changes, 95°N/-70°S -> a correct 5-20°N / 170°E-180°. Splitter A/B over all 21 live tracks in `global_storms.geojson`: **only Dolphin changes**, 20 untouched. ACE gate: wp/al/ep `--no-live` outputs identical modulo the wall-clock stamps. 12 new ace_core tests + 6 new CycloLab harness tests (3 of which fail pre-fix); tsr cyclolab suite 118 green. **ADVERSARIAL REVIEW CAUGHT TWO DEFECTS IN MY OWN FIRST CUT**, both now tested: a 0-360-convention lon (185) made the crossing vertex EXTRAPOLATE off the leg (lat 8 for a leg spanning 10->12) — frac is clamped to [0,1]; and a consecutive +180/-180 pair (same meridian, 360 apart numerically) got a bridge inserted, emitting a segment running the full width of the map — the exact artifact the function exists to prevent. **ENVIRONMENT TRAP WORTH KNOWING:** a stale `ace-core` (0.8.6) is pip-installed in this codespace and the generators do a plain `import ace_core`, so under `unittest discover` an earlier module imports site-packages first and `sys.modules` caches it — the house `sys.path.insert` pattern is then a no-op and the suite silently tests the INSTALLED copy. My first ACE gate run was vacuous for exactly this reason (it compared 0.8.6 to itself); it was re-run with PYTHONPATH forcing the tree, and the new test loads ace_core by explicit file path with an assertion that it got the repo copy. **ace_core 0.8.7 -> 0.8.8** (tag `ace-core-v0.8.8`), tsr pin bumped, box tat-render lane rebuilt. Pre-existing unrelated failures untouched: 4 enscenters ECMWF-mirror errors, tiled-viewer playback, asset-versioning._

---

_Prior: 2026-07-25 ~21:2x UTC — **JTWC FEED REBUILT ON TWO INDEPENDENT LEGS.** JTWC's public a-decks are gone; the b-decks still arrive but only via the unofficial natyphoon mirror behind our Worker proxy, and they are post-analysis, landing ~1 synoptic cycle late. That lag (not a dead feed) is why the site showed Noul as C1 while JTWC had 85 kt = **C2**. **VERIFIED ENDPOINTS** — Leg 1 (numbers): `hurricanes.ral.ucar.edu/repository/data/tcvitals_open/combined_tcvitals.2026.dat`, whole-season, refreshed within minutes of each cycle, and AHEAD of NOMADS (it had 18Z at 19:46Z, before the 18Z GFS cycle published); backup `nomads.ncep.noaa.gov/.../gfs.<YMD>/<HH>/atmos/gfs.t<HH>z.syndata.tcvitals.tm00` (~11-day retention, probed). `ftpprd.ncep.noaa.gov/.../syndat/` does NOT connect from here or from Actions, and NOMADS `/syndat/` 403s — both recorded in code so nobody re-derives it. Leg 2 (type): **`tgftp.nws.noaa.gov/data/raw/wt/wt{pn,io,ps,xs}{NN}.pgtw..txt`** — NOAA-hosted, so it answers from CI address space where metoc 403s; metoc's own `.txt` products are the secondary (they DO fetch; only the JS-walled HTML 403s). The `5x` slots are a machine-readable ATCG form carrying the full **34/50/64 kt radii** that tcvitals lacks, plus the only unambiguous timestamp in the product set. **DECODE VALIDATED FIELD-BY-FIELD** against the live 11W b-deck: winds snap m/s→JTWC's own 5-kt grid (43→85 kt), km→nm exact (R34 222/204/167/195 km = 120/110/90/105 nm), MSLP/RMW/POUTER/ROUTER all exact. **THE TRAP**: tgftp never clears a bulletin slot — `wtpn54` was still serving a **2024** typhoon — so selection gates on the ATCG synoptic stamp, never on slot presence. **HONESTY**: tcvitals has no type field, so an unresolved fix is marked indeterminate and EXCLUDED from ACE, never guessed. The sentinel is a string (`IND`) not None, because pandas coerces None→NaN and `nature_eligible` ACCEPTS NaN via the provisional escape hatch — every untyped fix would have silently accrued ACE. B-deck still wins wherever it reaches (measured: 268 paired fixes, disagreements all in OLDER post-analysis-revised fixes); the legs only extend past its newest fix, bounded to a 48 h lead window so the season file cannot silently back-fill January (73 untypeable fixes on a real run). AL/EP untouched and byte-identical. 53 new tests, full suite 751 green but for 6 pre-existing failures. **QUEUED (Andrew)**: the out-of-repo box intensity poller (tat-satellite-render) still fetches b-decks only. `build_feed_base._poller_cfg` now ships `tcvitals: true`, but the poller must actually call `ace_core.jtwc_live.extend_with_tcvitals` and be running ace_core >= 0.8.6 — one import + one call in its fetch path, then redeploy the lane. Until then the 6-hourly cron carries the leg and the poller keeps its old ~1-cycle lag. **TWO AUDIT FINDINGS surfaced after the ship, neither a regression, both worth a look:** (1) `generate_hovmollers.py:79` has been fetching the SAME UCAR tcvitals URL since long before this work — independent confirmation the endpoint is sound, but it means there are now TWO tcvitals parsers in the tree. Its `parse_vitals_line` (:554) captures only id/name/time/lat/lon (genesis markers), so it is not wrong, just narrower; `ace_core.tcvitals.parse_tcvitals` is a strict superset and hovmollers should fold onto it — the repo's one-parser convention (parse_bdeck) exists for exactly this reason. NOT done here: it is a separate change to a working generator and did not belong in an ACE-critical commit. (2) The a-deck-derived model guidance (`cyclolab/{sid}/guidance.json`, written by the OUT-OF-REPO box poller, read by `satellite/explorer/cockpit_fields.js:1738` and the per-storm hub) is the one thing the a-deck shutdown could genuinely have killed. Checked live: **it is still publishing for JTWC basins** — 11W returns 16.9 KB of aids — but on init_cycle **2026072512** while the NHC storms (06E/07E) are already on **2026072518**. One sample cannot tell a lag from a stall; if WP guidance is still stuck on 12Z at the next check, the box's WP aid source is the thing to look at, not anything in this repo._

---

_Prior: 2026-07-25 ~19:4x UTC — **BOX 2 ONLINE; FLEET CONVENTIONS ESTABLISHED.** Andrew provisioned a second Hostinger KVM8 (8 vCPU / 31 GB, Boston, root@72.62.97.220) because Hostinger kept flagging box1 as pegged, and asked for the multi-box conventions to go in now while the fleet is still small. **Conventions (all in tat-satellite-render):** `fleet.yml` is the inventory AND the lane-to-box assignment map (box host/tier/role/lanes; lane project/compose/owner/measured cost) — moving a lane is an edit there plus a deploy, never a docker command typed on two hosts. `scripts/fleet.sh` is the single entry point: `status` (per-box sha/load/RAM/lanes), `drift` (the git rule enforced — every box tracks origin/main only; ahead/behind/dirty/off-main is reported as an INCIDENT, and the fix is to land the work on main, never to `reset --hard` over it and destroy the evidence), `provision`, `deploy`, `setenv` (secret propagation to one box or all), `lanes`. `scripts/provision_box.sh` makes box N+1 a step not a build (docker, git, repo pinned to main, .env skeleton, image, heartbeat timer) — and anything a future box needs goes IN it so box N+2 inherits the fix. Secrets live only in each box's `.env`, listed in `env_keys` so a missing one fails at provision time rather than at 03:00 in a cron log. **Health:** every box publishes a heartbeat to R2 once a minute from the HOST (not a container — it must keep reporting when every container is dead), surfaced at the new shadow page **/fleet/** (unlinked, noindexed, robots-disallowed). This closes the silent-box gap: a dead box used to just stop publishing frames, which on the imagery pages is indistinguishable from a quiet basin. Carries sha/branch/dirty (so drift shows on the page too), load per core, memory, disk, OOM kills, lanes up vs exited. **Migration + rebalance:** moved the two Himawari lanes to box2, then GK-2A, MTG, the GEO composite and the expensive z7 CONUS lane as the heartbeats showed the real balance — box1 went from load ~9-10 with ~1 GB free to **~4.2 with ~13 GB free**; box2 sits at ~3.9. The decisive measurement: box1 carries **~3.7 cores of NON-satellite work that cannot move** (meso-render 1.24, mrms-poller 1.23, cyclolab 1.21 — live requests and region-specific polls), so its emit budget is only ~3.3 cores. **Spent the freed capacity:** CONUS now runs its **native 5-min grid** (was 10 — half the frames ABI produces were never being cut), and each full disk's five loop-lead products moved into their own container so lead LATENCY is a ~3 min pass instead of a ~36 min sweep; browse bands went 30-min -> **20-min** grid (10 would need 1.43x real time on a serial lane; 20 needs 0.71x). Three script bugs were found by running the tooling against two real boxes rather than reasoning about it: `grep -c` prints its count AND exits 1, so every heartbeat published `"0\n0"` and was invalid JSON (now validates itself before the PUT); provisioning tested `-d .git` and box1's checkout is a git WORKTREE where .git is a FILE, so it tried to clone over a live production tree; and `ssh` without `-n` ate the deploy loop's stdin so only the first lane of each box deployed. **Still short of native:** browse bands at 20 min vs a 10-min scan — that needs a third box. Prior: PER-SATELLITE EMIT LANES AT NATIVE CADENCE — **PER-SATELLITE EMIT LANES AT NATIVE CADENCE.** Standing rule from Andrew: imagery must be as high-resolution and as frequent as possible, always; a satellite refreshing slower than it scans is a defect. GK-2A was publishing ~every 5 h against a 10-min scan because one container cycled every suite in turn. Root cause went deeper than the rotation: the manifest rebuild (`complete_stamps`) enumerated EVERY tile of EVERY retained frame — 460,528 keys / 239 s on the ring composite, 264k / 135 s on goes19-fd — twice per product-slot, plus an 18-min products-index scan per lane pass. That is 68–82% of every slot and it GREW with retention, so native cadence was impossible at any core count (one ABI FD slot needed 636 s of listing against a 600 s budget). Fixes, all measured: (1) read the key LAYOUT with delimiter listings instead of the contents — 239 s → ~8 s, constant in pyramid depth, with `tests/test_s2_liststamps.py` pinning byte-equivalence against the old walk; (2) the products-index boolean answered off the newest stamps — 1,086 s → 52 s; (3) `S2_CUT_WORKERS` threads the tile cut (both halves release the GIL), byte-identical output pinned by `tests/test_s2_cutworkers.py`; (4) WebP `method` 6 → 4 (libwebp's default) after measuring real TAT tiles: 3.0–3.2× faster at IDENTICAL PSNR and size within 0.5% — encoder effort was buying literally nothing. Result per product-slot: goes19-fd 116 s → 39 s, CONUS 47 s → 10 s, geo-global 363 s → ~60 s. Then the architecture: every ring member owns a lane (`tat-s2-{g19fd,hwfd,hwwpac,gk2a,mtg,geo,conus*}`), ordered loop-leads-first every pass with the browse catalog cycling in quarters. Live now: 10.0-min frame SPACING on every loop-lead product; GK-2A ~18 min old with 32 frames (was ~5 h with 8); himawari9-wpac/b11 1707 min → 28 min. Precise on latency, because I overstated it mid-session: a lane pass measures ~36 min (ir fetch 09:06:23→09:42:01), so lead LATENCY is 18–28 min (about 10 min of that is the satellite's own scan→S3 publish lag), not the ~7 min the design targeted. Browse bands sit on an explicit 30-min grid revisited ~142 min, with --backfill 240 sized off that measured revisit — twice I set a window narrower than the revisit and the browse loops silently grew holes (slots aging out unseen); the arithmetic that settles it is 27 FD products on a 10-min grid = 1.75× real time on a serial lane, vs leads@10 + browse@30 = 0.80×. RESOLUTION UNCHANGED — every product keeps its registry pyramid_px. **Honest ceiling (measured, not modelled):** the full browse catalog for every ring member at native cadence needs ~12.6 cores of cut CPU; the box has 8 (~6 free after the render stack). RAM binds FIRST — a full-disk cut peaks ~4.5 GB, the non-s2 stacks hold ~12 GB of 31 GB, so ~5 heavy lanes is the cap; my first attempt at 8 lanes tripped the HOST OOM killer (`constraint=CONSTRAINT_NONE, global_oom`) and I consolidated to one container per satellite. Still short of native: browse bands cycle ~30 min, and CONUS runs a 10-min grid against its 5-min scan (+1 core to close). Both need roughly double the cores AND RAM — a bigger box or a second one; nothing was silently degraded to fit. GOES-18 and SEVIRI-IODC have NO registry rows at all (they exist only as geo-ring composite members), so lanes for them are ingest work, not config. ALSO FIXED: every product switch on GOES-19 and Himawari-9 Full Disk was silently blocked and every field click ejected to CONUS — the generated catalog freezes one export sector per satellite so its ids never matched the per-domain availability sets; ids are now sector-rewritten like manifest paths already were (reproduced on the live site: 28/28 rows wrongly chipped → 1/28, the one being fd truecolor, which genuinely is not emitted). Prior: ROADMAP REALITY-AUDIT: reconciled all 56 board items against actual repo/live state (point-in-time seed notes had drifted). Moved 5 to shipped with git-mined dates + anchor commits — gk2a-explorer-wiring (@9074e204), mjo-rebuild-spec (RMM-reconstructed Hovmöller @33d6364d), hy2b-scatterometer (HSCAT on /obs/ascat/ @a4603add), hafs-diagnostics (9 env products @20d4508b), simulated-mw-hafs (sim_89h @2237f14f). Rewrote 3 notes: fci-activation (corrected the "41-chunk works" overclaim — downloads 403 until the licence click), cyclolab-stages (planned→active, route+pollers live, root index unpublished), youtube-stream (planned→needs-andrew, /stream/ live, encoder blocked on VPS+key). Adversarial pass REFUTED 3 tempting reclassifications (seviri-iodc, rapidscat-token, ascat-time-machine stay open) and found ZERO fake-shipped. Board now 27 shipped / 3 active / 3 shadow / 5 needs-andrew / 7 next / 9 planned / 2 blocked. Prior: SHADOW ROADMAP BOARD LANDED at `/roadmap/` (unlinked, noindexed, robots-disallowed like /bugs/ + /records/). Graphical kanban by status × area, driven by `roadmap.yml` at the repo root: client-side strict-subset YAML parse (fails loud to a red banner, never half-renders), content-gated 60s poll, area filter chips, recently-shipped strip, progress bars, detail modal. Seeded with the real current state (56 items: 22 shipped w/ git-mined ship dates + anchor commits, 4 active, 3 shadow, 4 needs-Andrew, 8 next, 13 planned, 2 blocked). 7-area palette validated colorblind-safe on the dark panel. New standing rule in CLAUDE.md: every ship flips its roadmap.yml item in the same scoped commit. Tests: tests/test_roadmap_board.py (data + jsdom DOM smoke + shadow-honesty guards) green; screenshot-verified desktop + mobile. Prior wave (2026-07-21): #4 ECMWF MJO models, recon Skew-T/VDM, QuikSCAT 1999-2009; details in git + on the board now._

---

## MORNING-TO-DO (Andrew) — running list, maintained by the agent

-2. ~~MERGE `sat-explorer-fixes` → main~~ **DONE 2026-07-18 ~16:5x**
   (Andrew gave the go-ahead mid-session; merged clean @a53aeee9 — the 7
   explorer client files + boundary-lines geojson + the headless harness;
   ingests were already live via cherry-picks).
-1. ~~BOX one-liners~~ **DONE 2026-07-18 ~17:0x by the agent** (standing
   directive received: box ops are the agent's now): emit-cron carries
   `--allow-geometry-change` (tsr-s2 @4afdee9) + newest-first (@397d9fe),
   image rebuilt, cron recreated; conus manifests current to ~10 min;
   emit-conus-stopgap schedule retired (dispatch kept); the GH lanes'
   self-patch steps removed (upstream carries the fix).
0. ~~PUSH THE TSR COMMITS~~ **DONE 2026-07-18 ~17:0x by the agent** via
   the box's deploy key: tat-satellite-render main @10ad9cc (the 3 box
   commits + render fast-path 05f03ba + the pace/rate env pin) and
   s2-sat-ingest @4afdee9 both on GitHub. Render service REBUILT +
   healthy: archive frames ~3x faster (measured 4.6-9 s/frame end-to-end,
   NCEI fetch now dominates — noted follow-on), X-Archive-Pace-Ms: 1500 +
   RATE_LIMIT 90/min live, floaters unaffected (cache filling within a
   minute of restart).
1. **(carried) Q18 — Cloudflare token** for headless Worker deploys
   (`CLOUDFLARE_API_TOKEN` Codespaces secret; Workers Scripts:Edit + zone
   Workers Routes:Edit + Cache Purge:Purge). Bug board + purge already live
   via your one-time deploy; this is only for future headless redeploys.
2. **(carried) EUMETSAT key** (`EUMETSAT_CONSUMER_KEY`/`_SECRET` as TAT
   Actions secrets + box `.env`) — lights the Meteosat wedge in the World
   composite.
3. ~~BOX pull+rebuild for tsr @863d6df~~ **DONE 2026-07-17** (agent,
   direct SSH): 863d6df deployed, floaters/ moving again (root cause was
   the R2_PREFIX env-parity bug, not a hang — see the 02:4x entry).
4. ~~BOX: start the remaining emit suites~~ **DONE 2026-07-17**: s2
   emit-cron recreated from the s2-sat-ingest tip in /root/tsr-s2 —
   suites `conus fd himawari9-wpac himawari9-fd geo-global`. GH-stopgap
   schedule cutover happens as soon as the fd/wpac first emits verify
   (in flight).
5. **BOX: post-restore band failures — VERIFY ONLY**: 863d6df is
   deployed; confirm goes19/conus/truecolor + sandwich refresh on the
   next sweeps (agent watches; escalate only if still failing).
6. ~~BOX: CycloLab adv/cone stuck~~ **DONE 2026-07-17**: CYCLOLAB_PREFIX
   parity bug — adv 9 was landing in shadow/cyclolab; promoted pin in
   compose, verification in flight.
7. **cyclolab/index.html decision** — the router maps /cyclolab/ root to
   a lab index that was never published (branded 404 serves now): either
   publish an index or drop the root mapping.
8. **Stream encoder go-live** (everything is built + container-tested,
   RUNBOOK-STREAM.md §1): ① provision a small VPS of its own (2-4 vCPU,
   4 GB, Docker), ② grab the YouTube stream key (YouTube Studio → Go
   live → Streaming software), ③ three commands from the runbook. The
   /stream/ page it broadcasts is already live and self-updating.
9. **(#14 RapidScat cred — only when #14 build resumes)** add
   `EARTHDATA_TOKEN=<token>` to the render box `.env`
   (`/root/tsr-s2/.env`) so `ascatobs/podaac.py` can pull PO.DAAC
   `RSCAT_LEVEL_2B_*`. Get a token at
   `https://urs.earthdata.nasa.gov/` (Applications -> Generate Token).
   Not needed for KNMI ASCAT (keyless) or QuikSCAT (anonymous BYU).
10. _(agent appends new steps here as the re-kick queue lands)_

---

## 2026-07-21 (~18:2x-19:0x UTC) — 3D CLOUD TOPS (#16) SHIPPED (@bb1dc5f1)

Explorer gains a 2D/3D toggle (settings, "View" section): IR cloud tops
extruded as a relief surface, tilt/rotate camera, exaggeration slider
(x4-16, default x8), honest proxy chip on-map. Fully client-side: ir3d.js
addProtocol('tatdem') synthesizes Terrain-RGB DEM tiles from the existing
per-frame bt.png (no box changes, no new R2 product); dem twin sources ride
the loop's mount/park machinery, terrain flips in _reveal only. Gated to
bt:true products; auto-drops to 2D on non-BT/MW/TM; Reset exits fully; 2D
path untouched when off. Verified via the new tilt3d harness scenario
(terrain+pitch asserts, terrain-follows-frame in playback, clean 2D return,
zero page errors) + a pitched GOES-19 CONUS IR screenshot. Rollback: revert
@bb1dc5f1 (pure frontend).

## 2026-07-21 (~08:0x-17:1x UTC) — #4 ECMWF MJO + RECON PRODUCTS + #14 QUIKSCAT SHIPPED

**#4 (@9aa187dc on main).** subseasonal/ecmwf_open.py: keyless ECMWF open-data
fetcher (index-driven coalesced+threaded byte-range GETs, backoff, per-day
reduced-field disk cache), OPER/ENS adapters mirroring gefs_mean. RMM + OLR
Hovmoller variants for IFS + ENS, _ifs/_ens suffixes, model selectors on
/subseasonal/ (freshness-gated; stale variants disappear). CI: additive steps,
timeout-bounded, per-model isolated — the GEFS publish cannot be taken down.
Numerically verified (day-1 corr vs GEFS c00: u850 .977/u200 .994/OLR .91;
tropical OLR ~263 W/m2; ens spread grows). Adversarially reviewed (2 real
findings fixed: mixed-member-set OLR differencing now tail-breaks; init
fallback returns newest probed). AIFS dropped per Andrew (no OLR field).
**First live publish: tomorrow's 15:41Z cron** (today's ran pre-merge; the
codespace gh token cannot dispatch — 403).
- Rollback: revert @9aa187dc; the page degrades to GEFS-only automatically.

**Recon (@ece430b7).** reconobs schema v2: full dropsonde profiles + full VDMs
+ two live decode bugs fixed (max_sfc_wind_kt was null on EVERY published VDM).
Frontend: adaptive-sheet Skew-T canvas + drop selector, VDM chips panel,
numbered clickable drop markers. Verified headless on live Bertha (AF309/
NOAA3) with real v2 output. The box recon-poller self-deploys (90s git reset;
publisher heartbeat re-emits within ~10 min). 67+21 tests green.
- Rollback: revert @ece430b7 (schema additive; old JS ignores new keys).

**#14 QuikSCAT (@1da0473e).** qscatobs package: pure-numpy decoder for BYU
HRStorms per-pass 2.5-km winds (reference IDL reader lat formula proven WRONG,
/180 not /200), colocation-table obs times, house-style renders, resumable R2
build. /obs/ascat/ gains an Archived-passes browser (hidden until manifest
exists). Katrina 2005 verified (eye vs best-track aligned). **Backfill running
on the box** (sar-poller container, AL+EP 1999-2009, /tmp/qscat_backfill.log;
resumable — rerun the same command if it dies). Honesty call: NO "peak
retrieved" headline (Ku rain contamination is coherent; despeckle still read
77 kt on a 30-kt TD).

**#14 HY-2 — SHIPPED (@a4603add, later same session).** hy2obs package on
the confirmed keyless Copernicus Marine S3 source; delayed-daily renders
(all labels honest: DELAYED, rain/land/QC-masked), outage-tolerant guarded
skip; /obs/ascat/ HY-2 section (6-day staleness gate); box hy2-poller live
(tsr @0443998, hourly). Verified on the 07-16 pre-outage granules end-to-end.
Follow-ups: watch first box tick publish to CDN once the upstream outage
clears; optional few-hour KNMI WIND-FTP creds remain an Andrew option.

**#14 remaining — RapidScat:**
- HY-2B: source PROVED keyless (Copernicus Marine native S3, daily 0.25-deg
  HSCAT L3 with per-cell times + rain/quality bitmask; 19-43h latency;
  listing recipe + schema in the scout notes). NOTE: the KNMI-to-CMEMS chain
  has an ACTIVE outage since 07-16. Build queued next session. The few-hour
  L2B path needs emailed KNMI OSI SAF WIND-FTP creds — Andrew option (queued).
- RapidScat: still no EARTHDATA_TOKEN on the box — skipped per instruction,
  queued (MORNING-TO-DO #9).

## 2026-07-21 (~05:3x-07:2x UTC) — SAR SALINITY OVERLAY SHIPPED (#10); EXTRA-MJO (#4) + SCATTEROMETERS (#14) ASSESSED + QUEUED

**#10 SAR low-salinity reliability overlay — SHIPPED, LIVE.** C-band SAR
ocean-wind retrieval is less reliable over low-salinity water (river plumes,
fresh rain lenses). Ingest RSS SMAP SSS 8-day running mean (rain-filtered
`sss_smap_RF`, anonymous HTTPS, no creds) and hatch sub-33-PSU water on each
/obs/sar/ pass as an honest reliability cue.
- Code **@6532e4b8** on main: `sarobs/salinity.py` (ingest + compact-grid
  publish, watermark-gated on SMAP DOY), `sarobs/salinity_cli.py` +
  `generate_sar_salinity.py` (poller shim), `sarobs/render.py`
  (`_overlay_low_salinity` muted hatch + legend/credit; fires only where
  low-SSS water falls inside the pass extent), `sarobs/build.py` (loads
  `sar/salinity/mask.nc` once per tick, passes to every render), `store.py`
  `get_bytes`, tests (24 pass).
- Box: `tat-overlays-salinity-poller-1` live (tsr s2-sat-ingest **@a40f2b0**,
  3600s watermark-gated ticks). First tick published `sar/salinity/mask.nc`
  (1.31 MB, DOY 177 / 2026-06-26) + `meta.json` — both HTTP 200 on
  cdn.triple-a-tropics.com. One-time `--rerender --max-new 80` backfilled 80
  existing passes; the tail flows via new passes.
- Verified: overlay fires on the Arthur Gulf pass (RCM1/VH) over the
  Mississippi/Atchafalaya plume (56% of in-bbox water 25.5-35.4 PSU); wind
  reads through; legend/credit clean (no watermark collision). Credit: RSS.
- Rollback: on the box `docker compose -p tat-overlays -f
  docker-compose.overlays.yml rm -sf salinity-poller`; revert the main commit.
  Fail-open: a missing mask just renders passes without the overlay.

**#4 Extra MJO forecasts (Euro/IFS-oper + IFS-ENS) — SOURCE VERIFIED, BUILD QUEUED.**
- ECMWF open-data (data.ecmwf.int, anonymous, CC-BY-4.0) confirmed end-to-end:
  the `.index` is JSON-lines with `_offset`/`_length` for byte-range GETs;
  `ttr` (sfc, accumulated top thermal radiation -> OLR = -ttr/Δaccum) and `u`
  at pl 850/200 are all present and decode cleanly via cfgrib (721x1440
  0.25 deg global, verified on the 20260721/00z oper 24h file).
- Plan: new `subseasonal/ecmwf_open.py` mirroring `gefs_mean`'s interface
  (`fetch_members_rmm` -> {member:(dates, olr[nd,144], u850, u200)},
  `fetch_olr_tail`, `newest_complete_init`); reduce ttr->OLR + winds to the
  15S-15N 144-lon RMM band; project via the shared `rmm_wh04` WH04 EOFs; then
  a `--model {gefs,ifs,ens}` dispatch in `generate_mjo_rmm.build_forecast` +
  `generate_hovmollers`, a model selector on /subseasonal/, and an
  `ecmwf-mjo-poller` on the box.
- **DECISION — AIFS: DROP (recommended) or winds-only.** AIFS (the ML model)
  emits no top-of-atmosphere radiation flux (no `ttr`), so no OLR; RMM is an
  OLR+u850+u200 projection, so AIFS can drive neither the RMM phase-space nor
  the OLR Hovmöller. Recommend omitting AIFS from the MJO selector (a
  winds-only "half-RMM" is a non-standard index that would mislead); ship
  IFS-oper + IFS-ENS fully.
- **BLOCKED THIS SESSION** on (a) the file-editing tools — the vibe-island VS
  Code bridge host went unreachable (~35 min; Edit/Write/Read all time out,
  only Bash works), so the large edits to `generate_mjo_rmm.py` /
  `generate_hovmollers.py` + the live /subseasonal/ frontend can't be made or
  image-verified safely, and (b) ECMWF now rate-limiting probes (429) — a
  fetcher built now couldn't be numerically verified before landing. Resumes
  when either clears.

**#14 Archived + extra scatterometers — ASSESSED, BUILD QUEUED.**
- **KNMI/OSI-SAF ASCAT is ALREADY LIVE** — the existing `ascat-poller` +
  `ascatobs` package ingests OSI SAF ASCAT-B/C coastal via the KNMI
  Scatterometer Data Portal (keyless; no `KNMI_API_KEY` on the box). #14's
  "live bonus KNMI ASCAT" is therefore already shipped.
- Remaining, all landing in `ascatobs` + the /obs/ascat/ + explorer frontend
  as selectable sources:
  - **HY-2B (HSCAT):** OSI SAF/KNMI distributes it — extends the existing KNMI
    fetch path as another sensor (portal reachable).
  - **RapidScat (ISS 2014-2016):** PO.DAAC `RSCAT_LEVEL_2B_*`;
    `ascatobs/podaac.py` already exists for the EARTHDATA path. **DECISION —
    cred:** the box `.env` currently has NO `EARTHDATA_TOKEN` (checked,
    presence-only) -> this is a one-time Andrew step (see QUEUED below). No new
    Codespaces secret.
  - **QuikSCAT (1999-2009):** BYU SCP anonymous (`ftp.scp.byu.edu/data/qscatv2/`,
    reachable) — enhanced-res SIR-format binary (a new decode path, not the
    swath-L2B pipeline); the archival bonus, heaviest lift.
- **BLOCKED THIS SESSION** on the same file-tool outage (new decode module +
  live frontend selector edits).

## 2026-07-19 (~00:4x–01:2x UTC) — OVERLAY FEEDS → BOX POLLERS (GH crons retired)

Per the directive: UHR ASCAT + MRMS + METAR + surface analysis converted from
GitHub-Actions crons to **box pollers** — the TAT never-miss ingest pattern,
one authoritative writer. GH load-shed even staggered schedules to ~hourly
all day (a native 2-min radar product was riding a 10-min cron that actually
fired 6× less often than declared).

- **Mechanism** (`tsr-s2 docker-compose.overlays.yml`, project `tat-overlays`,
  tsr @bcb21a8): four services on the `tat-s2` image; each keeps a sparse
  blob-less clone of the SITE repo and hard-resets to `origin/main` every
  tick, then runs its generator `--store r2` — so commit-to-main deploys
  poller code too, matching the site model. The generators carry their own
  watermark/new-object gates (MRMS + sfc no-op on an unchanged stamp — sfc
  gate added @43838b88; UHR dedups by pass id and backfills its window;
  METAR frames by run time at the minutely obs cadence), so ticks are cheap
  and writes are new-data-only. Cadences: MRMS 75 s (`--keep 150` ≈ 5 h of
  2-min scans), METAR 300 s, UHR 300 s (`--max-new 8`), sfc 600 s.
- **First-tick verification (box logs + CDN)**: MRMS emitted the 00:56:40Z
  native scan; METAR framed 00:58 with 708 buoy/C-MAN + 472 ships; sfc
  honestly no-op'd on the unchanged 21:00Z analysis; UHR published 8 passes
  (ELIDA/97E-tagged among them) — beating the GH workflow to its own first
  emit. Live explorer screenshot over ELIDA's swaths: dense 2 km barbs +
  wind-speed field, three UHR field layers mounted.
- **GH workflows retired to dispatch-only** @606c3b29 (`update-metar`,
  `update-mrms`, `update-sfc-analysis`, `update-uhr-ascat`) — kept for
  manual re-emits/backfills. Zero GH-cron overlay feeds remain.
- Note for the morning: the UHR workflow's ONE scheduled run (00:08Z) failed
  on a missing `requests` (fixed @244c06e9 before retirement — moot now,
  the poller owns the feed). Watch `docker logs tat-overlays-mrms-poller-1`
  if radar cadence ever slips; pygrib installs at container start.
- **Sustained-cadence verification (01:27Z, ~35 min in)**: MRMS at NATIVE
  2-min cadence (01:16:41 / 01:18:39 / 01:20:40 / 01:22:40 / 01:24:42,
  3-min staleness — was ~hourly under the shed GH cron); METAR 5-min
  (latest 01:23); UHR backfilled to 45 passes. Working as designed.

## 2026-07-19 (~21:1x-21:4x UTC) — MIMIC-TPW2 MOISTURE OVERLAY (built + deployed; upstream currently stalled)

New environmental overlay for the explorer: hourly global total precipitable
water (MIMIC-TPW2, courtesy CIMSS/SSEC — anonymous, no creds). Site @9f7f3a41,
tsr s2-sat-ingest @0999cf4 (compose).

- **The upstream mirror is STALLED**: every path on the public mirror
  (data/, the color imagery, latest_image) froze at **2026-07-06 18:00Z**
  — 13 days before this build; July 7-19 files 404. Decision: build the
  full never-miss pipeline anyway so it idles honestly and self-activates
  the moment the mirror resumes. Nothing needs re-touching then.
- **generate_mimic_tpw.py**: presence-gated tick (LISTS current+previous
  month dirs — never assumes on-the-hour; the mirror lags), idempotent
  stamp watermark, backfill bounded to the rolling 36-frame window (an
  826-file listing would otherwise churn ~17 h of doomed ingests), webp
  frames row-warped to web-mercator (±75°), the standard operational TPW
  ramp (browns→greens→blues→purples, 5-78 mm, ≤1 mm transparent), served
  colorbar, deferred prune. Verified against the newest real file:
  frame + cbar eyeballed, warnings-as-errors clean, scipy-free.
- **Frontend** (cockpit_fields.js v=tpw1, cockpit.js v=core14): "TPW
  moisture" toggle in Overlays — the radar image-source discipline (one
  stable per-pane source, serialized updateImage, 90-min nearest-join
  skew riding the cockpit clock), right-side mm colorbar, valid-time
  badge "TPW (CIMSS/SSEC) · <UTC>". **Freshness gate**: the toggle only
  enables when the newest frame is <3 h old — live right now the button
  shows a "stale" chip and stays disabled (verified on the deployed
  site), so 13-day-old moisture can never present as current. Harness
  scenario `tpw` (re-stamped bench feed): join at the right frame,
  playback walks the clock (21Z→20Z at the 18:50Z step), toggle-off
  clears layer+cbar — all pass.
- **Box poller** `tat-overlays-tpw-poller-1` (300 s ticks, --max-new 4):
  first tick listed 36-in-window and published 4 frames + manifest to R2
  (env/tpw/); the window finishes backfilling over ~9 ticks, then idles
  ("no new frames") until the mirror resumes. Rollback: `docker compose
  -p tat-overlays -f docker-compose.overlays.yml rm -sf tpw-poller`,
  revert site @9f7f3a41; R2 cleanup = delete env/tpw/ prefix.
- **FYI (queued, Andrew)**: GitHub warned "Repository is approaching its
  size quota" on push. Nothing failing yet. Options when you're back:
  prune the SST orphan-branch history (force-push already rewrites it
  each run; old runs' data blobs linger) or `gh api` repo-size audit +
  history expire. Destructive either way — your call, not mine.

## 2026-07-19 (~19:4x-21:0x UTC) — C02 NATIVE z7 SHIPPED (parallel tile PUTs)

The greenlit ceiling fell. tsr @2bdbd55 (emitter) + @598bacc/@c7eb480-era
lane files + the 12g memory bump:

- **Bounded-parallel tile PUTs** in s2_pyramid.emit_pyramid
  (S2_PUT_WORKERS, default 8; R2 client pool sized to match). Atomicity
  unchanged — a failed PUT raises BEFORE the ready marker. 25/25 pyramid
  tests green (4 emit-suite failures verified PRE-EXISTING at the parent
  commit — fixture drift, noted, untouched).
- **c02 cut at NATIVE z7** (10240 px base). First attempts OOM-killed at
  the stack's 8g default (rc=137 — the RENDER, not the cut, which is
  per-tile); fast lane now runs mem_limit 12g (transient ~1-min peak).
- **MEASURED slot economics**: 485 tiles/slot (z7 331 + z6 101 + lower;
  transparent-tile skip holds it under the ~610 estimate), fetch->done
  2:00-2:10 min, tile upload ~20-25 s for 485 (~14x the serial rate).
  Fast-lane pass ~5.7 min total — no starvation of ir/irbd/truecolor.
- **R2 PUT delta, honest**: c02 +432 tiles/slot x 6/h ≈ +1.9M Class-A/mo
  (~\$8-9/mo); with the earlier z6 bands + fast2 density the whole
  density+resolution program adds roughly ~\$20/mo of Class-A ops and
  negligible storage (14-day TTL). Documented, not hidden.
- **RECORDED live verification**: deep-zoomed C02 loop over 02L at camera
  z7.3 — individual convective turrets/cumulus streets, zero upscale
  mush; uniform 10-min timeline (window trimmed the OOM-era stragglers
  correctly and regrows 10-min-ly). c03 on its z6 grid alongside.
- NOTE for the record: no per-tile content-hash dedup exists in this
  emitter (the task assumed one) — PUT suppression is transparent-tile
  skip + per-frame ready markers. Inventing a cross-stamp hash scheme
  would break the immutable stamp-keyed tile contract; not done.

---

## 2026-07-19 (~18:1x-19:3x UTC) — LOOP DENSITY + NATIVE-RES LADDER (deployed; z6 series building)

Measured first (workflow fan-out): ~48 s wall per product-slot regardless
of product complexity -> one lane holds ~12 products on the 10-min grid;
box headroom ~5 cores. Resolution truth: over the conus box, 2 km bands
are NATIVE-BOUND at z5 (source 2500 px; deeper = upsampling); 1 km bands
support native z6; c02 (0.5 km) supports z7 — but z7 is ~610 tiles/slot
(~6.5 min serial upload), over any lane budget: DOCUMENTED CEILING, not
cut. c02 serves z6 (2x sharper than before, 2.4x under native).

Deployed (tsr @2c39407 + @c7eb480):
- fast lane v2: ir, irbd, truecolor (z5) + c02 (z6, 6144 px base).
- NEW fast2 lane: c01 c03 c05 (z6) + c07 c08 c09 c10 c14 sandwich
  nightmicro airmass (z5) — the commonly-looped set on the 10-min grid.
- full lane trimmed to the rare-13 (c04 c06 c11 c12 c15 c16 + 7 RGBs),
  pass ~10-11 min -> ~15-25 min cadence for the rare set (honest: those
  stay coarser by priority; nothing selectable is hourly anymore).
- MID-DEPLOY WAR CAUGHT + FIXED: leaving --suite conus on the full lane
  made two writers rebuild the upgraded bands at different geometries
  (manifests ping-ponged z5<->z6). The full lane now loops only its 13.
- Frontend @ab3aa390: live sessions adopt a deeper maxzoom in place
  (remerge re-applies maxzoom+1 + the min-zoom pin).
- TRANSITION: the geometry guard rebuilds upgraded-band manifests around
  z6 frames only, and ready markers skip z5-existing slots, so the z6
  series grows FORWARD at 10-min/slot — ~12 frames by ~21:15Z, full 6-h
  window by ~00:30Z. Loops on those 4 bands are short meanwhile.
  Density monitor armed; C02+C03 recording verification lands when ready.

---

## 2026-07-19 (~17:3x-18:0x UTC) — THE "ROGUE" HAFS DISPATCHER: traced, root-caused, defused

Forensics on the ~40-min update-hafs.yml dispatch loop (actor
WeathermanAAA):

- **The dispatcher is not rogue — the pipeline was stuck.** Trigger
  history: sporadic dispatches (0-5/day) all week, becoming a continuous
  40-min loop only at 18:30Z on 07-18 — four hours AFTER the HAFS cycle
  stopped closing. The +2-3 s cumulative drift between dispatches is a
  sleep(2400) process loop = ens_watchdog.py's HAFS_COOLDOWN_S exactly:
  a HEALTHY watchdog WITH a GH token, correctly re-dispatching against a
  genuinely stuck cycle.
- **The real defect**: when 02L formed, the cycle went from ~40-second
  no-op runs to REAL work — ~8,514 render tasks + 516 GRIB ingests for 3
  storms — and every run died at EXACTLY the 120-min job timeout
  (14:33:37→16:33:53 measured), restarting from zero on a stateless
  runner: one perpetual runner burning 24/7, the manifest never
  advancing, the watchdog never quieting. FIX @8afe0705: timeout 120→350
  (still clears the 6-h cadence; the commit says cut WORK not timeout if
  ever exceeded). Most "cancelled" runs never started (pending-slot
  bumps — cheap); cancel-in-progress was already correctly false.
- **Where the tokened watchdog lives**: every reachable host is
  eliminated (box watchdog token-less "WOULD dispatch" logs; box crontab
  clean; no repo workflow dispatches it; this harness has no crons). The
  only known host with the code + the token env is the paused-not-
  deleted legacy platform project — the same one whose render instance
  still answers /health. Andrew's project deletion kills it.
- **CONTINUITY WARNING for the deletion**: that legacy watchdog is
  currently the ONLY dispatcher with a token — the box watchdog is
  decide-only. After deleting the project, add ENS_WATCHDOG_GH_TOKEN to
  /root/tsr-s2's sibling /root/tat-satellite-render/.env and recreate
  tat-render-ens-watchdog-1, or the stuck-cycle backstop goes silent
  (QUEUED, one line + one compose command).
- Cancel/dispatch via this token: 403 (Actions write not granted), so
  the in-flight pre-fix runs drain naturally; the first post-push
  dispatch holds the queue slot and runs with the new timeout. A
  persistent monitor is armed for the first successful completion.

---

## 2026-07-19 (~17:1x-17:5x UTC) — RAILWAY DECOMMISSION SWEEP: GO

Four-lane verification (site repo, tsr both branches on the box, live
hosts, service-ownership inventory): **ZERO live Railway dependencies —
safe to delete the project.**

- render.triple-a-tropics.com A-records STRAIGHT to the box (2.25.183.231,
  uvicorn behind the box Caddy; public /health == box-local /health);
  cdn + apex are Cloudflare->R2/Pages. No served page or first-party JS
  contains a platform URL (127-568 KB live-page + 14-asset grep: zero).
- Every ex-platform service maps to a running owner: render/export ->
  tat-render-render-1; floater poller, intensity poller, guidance poller,
  ens-watchdog -> box containers; HAFS -> update-hafs.yml (GH, manifest
  fresh 17:04Z); meso/s1/s2 lanes + the six overlay pollers -> box. No
  container/crontab/Caddyfile/.env carries a platform string.
- LATENT HAZARD FIXED FIRST: floater_poller.py + meso_poller.py defaulted
  RENDER_BASE_URL to the retired platform URL (always overridden by
  compose env, but a dropped env line would have silently pointed at a
  deleted host) — defaults now box-internal (tsr main @2f7dfd1,
  s2-sat-ingest @bd12e0b).
- Scrubbed stale active-doc/comment mentions (@01612358, 15 files;
  SATELLITE.md deploy section rewritten to the box runbook). Dated
  history intentionally kept (this log, SATELLITE-REVIEW/REARCH, the
  floater-worker outage narrative). tsr keeps its railway.*.json/
  nixpacks/Procfile per RUNBOOK-RENDER §6 as the migration record —
  delete or keep at will once the project is gone.
- NOTE: the abandoned deployment web-production-b88d.up.railway.app is
  STILL RUNNING (its /health answers) — deleting the project also stops
  that burn. **Andrew: delete away.**

**Side-finding (NOT Railway, needs attention)**: update-hafs.yml is being
dispatched every ~40 min by an external loop (actor WeathermanAAA; the
box ens-watchdog is token-less and only logs "WOULD dispatch") — every
run since 2026-07-18 14:32Z gets CANCELLED at ~40 min by its successor.
Manifests still publish mid-run, but runner-hours are burning and cycle
2026071906 reports stuck. Find and stop that external dispatcher (it is
not this agent's harness and not the box).

---

## 2026-07-19 (~16:3x-17:0x UTC) — ASCAT TO THE BOX (cred-gated) + NHC formation-area gate

**ASCAT poller conversion** (recon via workflow fan-out; measured truth: the
"missing recent passes" is ~4-5 h SOURCE publication latency — the pipeline
ingests everything the source lists, health honest at 4.2/8 h — plus GH
shedding ~1/3 of hourly crons adding 1-3 h detection lag overnight):
- `scripts/ascat_r2_publish.py` (@18ce32ae) reproduces the workflow sync
  contract exactly (no-manifest no-op, single header set, manifest last,
  targeted reap). Proven against a fixture on a shadow R2 prefix (headers
  verified via CDN, test objects cleaned).
- Box `ascat-poller` joined tat-overlays (tsr @fd25cc0): 10-min ticks,
  pass-id watermark vs the live manifest, idempotent backfill — and
  CRED-GATED: the ingest needs EARTHDATA_TOKEN (or EARTHDATA_USERNAME+
  EARTHDATA_PASSWORD, or KNMI_API_KEY) which exists ONLY as a GH secret.
  Until a line lands in the box .env the tick idles with a clear log and
  the GH workflow remains the writer — its cadence DOUBLED to 2 slots/hr
  meanwhile. UHR was already box-authoritative (uhr-poller, minutes-fresh).
- **QUEUED (Andrew)**: add `EARTHDATA_TOKEN=...` (or KNMI_API_KEY) to
  `/root/tsr-s2/.env` + `docker compose -p tat-overlays -f
  docker-compose.overlays.yml up -d ascat-poller`; once its first publish
  lands, retire the update-ascat.yml schedule (comment marks the path).
- Live verify: SC layer's merged list leads with the newest pass each feed
  actually has (standard metopb 12:00Z — the newest PO.DAAC published;
  UHR 10:12Z — newest provider cut).

**NHC formation-area gate** (mid-flight report: dev-area wash over
designated 02L): areas now gate on the invest-vs-designated rule AT THE
SOURCE (@65884d3c) — an outlook polygon containing a designated storm's
current position (CurrentStorms lists designated only) is dropped;
developed systems carry a cone + track, never a chance-of-formation wash.
Plus the last GH-cron overlay feed moved to the box: `nhc-poller` joined
tat-overlays (5-min ticks), update-nhc-overlay.yml retired to
dispatch-only. Live-verified: 02L shows cone + timed track + obs at full
contrast with the wash GONE; the two open EPAC invest areas still render
their AOI (screenshot both ways).

---

## 2026-07-19 (~17:2x-18:3x UTC) — 4-PANE LOCKSTEP + UNIFORM CADENCE + FLUID MRMS + CONE LAYERING

All measured live on recorded 4-pane sessions (probe: per-pane cameras
sampled through drags, per-pane advance + radar stamps during play, gap
lists) — @b9bb163c + @1ffd0338, `cockpit_fields.js?v=mp2`:

- **Linked desync**: pane 0 kept its full-width camera (z2.76) while fresh
  quarter-cell panes fit-boot (z2.31) — equalized only on first drag, as a
  lurch. New panes now ADOPT the group camera; pane-count reflow resizes
  every map and snaps all to pane 0. AFTER: all four cameras identical at
  boot and through drags on any pane (probe-verified lockstep).
- **Non-uniform cadence** (measured 25/5/15/10/... gaps): the loop now
  presents one frame per fixed slot (modal gap floored at the 10-min
  fast-lane grid, walked from the newest frame; missing slots skip
  consistently). AFTER: every gap a clean multiple of 10 min — no 5/15
  hiccups; followers share the grid so cross-pane joins align.
- **MRMS stalling under playback**: 7000x3500 RGBA = ~98 MB GPU upload per
  advance, and MapLibre replaces the pending image per updateImage call —
  the texture ran behind. Updates now SERIALIZED (one in flight, latest
  queued) and playback uses a new half-res per-scan variant
  ({t}.s.webp + small_since from the mrms poller); full-res returns
  within a poll tick when paused. AFTER: radar advanced 9x in a 20-s
  recorded play, stepping with the sat frames.
- **NHC layering over 02L** (mid-flight report): cone now outline-forward
  (fill 0.10 -> 0.045, crisp dashed edge 0.95/1.7), formation-area wash
  0.24 -> 0.16 (the strong dashed outline carries the contrast), and the
  surface-obs canvas rides above every overlay canvas (z4): imagery ->
  cone fill -> track/markers -> obs. Live capture over 02L: storm
  convection, timed forecast positions, and every station plot read
  clearly through the cone.

---

## 2026-07-19 (~16:3x-17:0x UTC) — OBS JITTER: frame-stable declutter (recorded proof)

Land stations jittered across loop frames because the declutter re-ran per
frame over a feed that re-sorts by (rank, age) every emit — different cell
winners each frame, whole field reshuffling. Fixed @de76cbdf
(`cockpit_fields.js?v=obs8`): the kept set is computed ONCE per camera from
the newest series doc in a fully deterministic order (rank desc, then
station id); every frame draws exactly those stations at their canonical
coordinates with that frame's values. Ships (plat 1) still place per frame
at reported positions — real motion — filling leftover cells without ever
displacing the stable set.

Verified by RECORDING the live loop (12 frames, all joined to obs within a
minute) and eyeballing consecutive-frame crop strips: land station models
pixel-identical across frames while the imagery animates beneath; moored
platforms steady; ship diamonds persist with only true drift.

---

## 2026-07-19 (~15:1x-16:2x UTC) — LOOP QUALITY FROM A WATCHED CAPTURE + honest export

Andrew's real screen capture showed what the headless harness missed; every
cause was measured, fixed, and re-verified by RECORDING the live loop
(Playwright video -> frame extraction -> eyeballed contact sheet):

- **Surface obs flashed on only at the newest frame**: the obs series kept
  30 frames (2.5 h) against the 6 h loop window. keep 30->100 (@d7d9c7ed,
  poller self-deploys) + client LRU 44. Verified live: every frame of the
  current loop joins an obs frame within a minute (painted 87-121 stations
  per frame, per-frame join dump). Full 6-h depth finishes filling by
  ~21:30Z (the source is now-only; history can't backfill).
- **Visible dawn flash**: c02 mixed near-black 09-11Z frames with daylight.
  _deriveFrames now trims unlit frames (solar elevation at the footprint
  center, validated against real sunrise/noon/sunset) when the window is
  MIXED; all-dark windows stay untouched; IR unaffected. Verified live:
  the c02 loop now STARTS at 11:31Z (sunrise 11:25Z) — every frame in the
  recorded capture is daylit.
- **~24-min frame spacing**: the conus lane's 28-product pass takes 30-45
  min. A FAST lane (tsr @c1d95a3, `tat-s2-conus-fast`) pins ir/c02/irbd/
  truecolor to the true 10-min grid (verified: last-hour gaps 10/10/10/5).
  MRMS keep 150->220 so radar outruns the window too.
- **Export params matched to the screen** (@673dae56): the Loop button now
  opens a dialog whose defaults ARE the current loop (frame count = the
  window, fps = the playback rate), exports the LAST N frames under the
  <=10 MB budget. Verified with a real export: screen 7 frames @ 6 fps ->
  dialog 7/6 -> 0.13 MB artifact at the on-screen cadence. (The reported
  40-frame/60-fps dialog exists NOWHERE in either repo — likely an older
  cached page; the real defect was the dialog-less fixed-8fps export.)
  Legacy satellite-page dialogs now prefill from their live loop state too.

---

## 2026-07-19 (~04:5x–06:3x UTC) — MODEL TRACK GUIDANCE: one shared product, two consumers (rebuilt reference-grade)

Per the directives (build + the mid-flight "rebuild the presentation" follow-up):

- **One shared data product** (reused ingest, no new creds): the existing
  box guidance poller's `cyclolab/{sid}/guidance.json` (a-deck aids, taus
  0-132) EXTENDED with an `ens` block — EPS (ecens) + GEFS member tracks
  cut per storm from the existing enscenters tracks feeds (nearest
  early-tau member track within 400 km, 6-h decimation, cross-member mean
  with vector-mean lon; cached by cycle_version; absence never blocks the
  a-deck side). tsr @9155e07; live-verified on 91L (9/51 EPS members carry
  it, GEFS honestly zero). Pure matcher rides `cyclolab_guidance.py`
  (35/35 unit tests green).
- **CycloLab rebuild (tsr @877eb0c + @9fcd6b0, Andrew wasn't happy with
  v1)**: per-model identity hues (stable + shared with the explorer
  overlay) replace peak-SSHS coloring; tech labels ON the plot at track
  ends (stack-decluttered); daily forecast-hour dots + tau chips on the
  consensus spine; OFCL joins the consensus family; intensity plot gets
  the same hues, right-edge line labels, dashed statisticals, bold IVCN;
  NEW ensemble panel (members colored by min forecast MSLP, means heavy +
  labeled, honest member counts). Every guidance plot carries a branded
  header band (init time + @WeathermanAAA_, backing strip) and registers
  in the right-click @2x copy set (copy-band mark aligned to the site
  handle). Live-verified on 91L with per-plot captures.
- **Explorer "Model guidance" overlay (@f8640e28,
  `cockpit_fields.js?v=gd1`)**: toggleable like MRMS/METAR/NHC, honest-
  gated, per active storm off the SAME document — deterministic aids
  per-model colored w/ endpoint labels, consensus cased+bold, tau chips,
  ensemble members thin/translucent + means heavy w/ matched counts;
  layers re-raise above frame mounts; 10-min poll. Live-verified over 91L
  (26 aid tracks, 153 member lines, 5 means across actives).
- NOTE: the concurrent session's ATCF-number-gate work (ace_core 0.8.5,
  anchors.py, enscenters.js) was NOT built upon — the guidance join keys
  on storm_id + position only. Re-check `wears_invest_x` interplay once
  that lands (guidance draws for invests by design).

---

## 2026-07-19 (~01:0x–01:4x UTC) — LOOP FUNDAMENTALS: dense recent window + geo lane

Root-caused the "jittery/sparse loop": the viewer cut its loop as the
TRAILING loopCap of the whole manifest, and manifests hold days (90 conus
frames back to 07-14 with outage holes) — measured: 36 frames across
47.8 h including a 32 h hole. The scene teleported between frames; no
cadence smoothing could fix data that sparse. Fixes:
- `_deriveFrames` now cuts the newest **6 h window** (12-frame floor for
  thin feeds) before the residency cap — consecutive frames are minutes
  apart (@a9d0418a, `tiled_viewer.js?v=core8`).
- The ring default view gets a **dedicated geo-global emit lane** on the box
  (`tat-s2-geo-emit-cron-1`, tsr @13f6f8c) and the main rotation trims to
  fd/himawari via `.env S2_CRON_SUITES` — ring spacing heads from ~30 min
  toward the 10-min slot grid; box now runs 3 emit lanes + 4 overlay
  pollers (load headroom checked before adding each).
- Auto-follow-live was already present (90 s manifest poll → append +
  follow-tail; wrap-seam merge during play) and rides the windowed derive
  unchanged. FEED PAUSED keys on true data age only.

---

## 2026-07-18 (~21:3x–23:3x UTC) — FOURTH WAVE: live-testing round 2 (regressions + still-brokens) + UHR scatterometer

All eight reported items root-caused and live-verified with real deployed-site
captures (`LIVE=1` harness mode, screenshots in the session scratchpad).
Commits @47755eb1 (ops), @b5bd1275 (main batch), @aed3c37f (UHR).

1. **"METAR disappeared" — root cause was the CONUS sat feed freeze, not the
   obs layer.** The 17:2x stopgap retirement assumed the box emit-cron gives
   conus a 10-min cadence; the box actually runs ONE sequential pass over
   all five suites (~3.5 min/product, 4+ h/rotation — verified in its logs),
   so conus froze at the stopgap's last slot (17:01Z) and every time-locked
   join starved: obs blanked (75-min skew), MRMS pinned to pre-fix scans,
   loops lurched across a 5-h gap. Fixes, all live: stopgap schedule
   un-retired (2/hr, @47755eb1) AND a **dedicated conus emit lane on the
   box** (`docker-compose.s2.conus-lane.yml`, second compose project pinned
   to `S2_CRON_SUITES=conus` interval 300 s — running as
   `tat-s2-conus-emit-cron-1`, committed to tsr @25abacb). Once the lane
   proves out over ~a day, the GH stopgap schedule can retire again.
2. **Surface obs upgraded (was "METAR obs")**: NDBC buoy/C-MAN + VOS ship
   obs merged into the same rolling series with a `plat` field — ships =
   filled teal diamond, moored platforms = open diamond, land unchanged;
   one rank-desc declutter across classes; series keep 18→30 (must outrun
   the 4-h conus loop), client LRU 8→24. Live-verified global (WPAC
   station plots over Himawari-9 at 22:20Z). Ship/buoy symbols land with
   the first post-@b5bd1275 update-metar run (crons were shedding at
   check time; the generator is proven against the live sources locally).
3. **NHC overlay polish, all live-verified**: AOI click → genesis dialog
   (2-day/7-day % + risk words off the feed's prob2/risk2/prob7/risk7);
   AOI contrast fill .14→.24 + line .7→.95/2.2 (canonical tier colors
   kept); forecast POINTS emitted from the cone zip's pts shapefile
   (tau/maxwind/validtime/datelbl) → timed intensity-lettered discs along
   a cased solid track inside the cone.
4. **True Color / "channels went missing"**: nothing was ever deleted —
   the boot default moved to the GEO ring (@b96a187b), which is BT-only
   BY DESIGN (cross-sensor RGB/channel composites would fabricate; so
   documented in the tsr registry). The ring's rail now lists the whole
   single-sat catalog chipped `per-sat`; clicking ROUTES to the
   nadir-nearest satellite and selects the field there (live-verified:
   ring→True Color lands on conus truecolor, ring→C02 on conus c02).
   True color itself verified ONE shared frozen recipe everywhere it
   renders (tsr branches in sync @4afdee9, tile pixels checked — real
   RGB, not IR). FD truecolor remains registry-excluded (path collision
   + disk-scale C02 budget) — Andrew decision, queued below.
5. **MRMS "still blocky"**: the smooth renderer WAS live but unreachable —
   stale sat frames joined pre-fix immutable-cached scans. Fixed by the
   freshness work above + a RENDER_EPOCH floor in the generator that
   evicts pre-19:25Z frames from the series (keep 18→30). Live zoomed
   proof: continuously-graded cells over the Chesapeake at z7.6 on the
   22:26Z scan — no visible pixels.
6. **Choppy loops**: explicit play now releases the progressive residency
   ramp; manifest remerges defer to the wrap seam/pause (no mid-play
   source surgery); MRMS scan neighbors prefetch into HTTP cache; overlay
   canvases skip redraws when camera+doc unchanged and stop reallocating
   per advance. Live A/B (same harness, same machine): max reveal gap
   1240→672 ms, worst warm gap 1240→535 ms; median flat at the
   software-GL bench floor (real GPUs sit well under it).
7. **UHR scatterometer source (new)**: NOAA/STAR 2 km-class ASCAT-B/C
   storm cuts → `ascat/uhr/` riding the operational feed contract
   (manifest + wvc pass JSONs at ~14 km barb stride + a baked ~2 km
   wind-speed FIELD webp on the same stepped HC kt classes). Decode traps
   documented in the generator (broken valid_range; raw −32767 =
   no-retrieval sentinel == number_ambiguities 0). Explorer merges the
   UHR manifest (optional, never gates the operational feed), mounts the
   field under the barbs per drawn UHR pass. Harness-verified over ELIDA
   (passes storm-tagged 97E/ELIDA by the existing associator);
   `update-uhr-ascat.yml` 2×/hr — first R2 emit pending its cron.

**QUEUED manual steps (Andrew)** — appended to the running list above:
- **FD truecolor decision** (item 4): un-exclude `goes19-fd-truecolor` /
  `himawari9-fd-truecolor` in tsr `s2_registry.py` needs the Phase-1
  `goes19-fd-mcmip` placeholder row retired (product_path collision) and
  a fetch-budget call on disk-scale 0.5-km C02/B03. Everything else about
  ring vs per-sat truecolor is working-as-designed.
- **Stopgap retirement check (~a day out)**: if `tat-s2-conus-emit-cron-1`
  keeps conus manifests ≤15 min stale through 2026-07-19, re-retire the
  `emit-conus-stopgap.yml` schedule (workflow_dispatch stays).

---

## 2026-07-18 (~18:4x–21:3x UTC) — THIRD WAVE: consolidated overlay batch + NHC overlay + site-perf pass

### Consolidated tester batch (@3aa28a0b, fix-forwards @4e9ad623)

1. **Overlay persistence + toggle truth**: reproduced headless — overlays
   already survive channel/sat/domain switches post-re-raise; the REAL
   bugs were (a) manual sat/domain clicks never disengaged nadir-auto
   ("clicked GOES-19" could bounce to the GEO ring via the fit floor —
   verified, fixed) and (b) overlay buttons showed pane-agnostic state
   (syncControls now derives every toggle from the ACTIVE pane).
2. **Progressive cold load (the GEO-ring OOM)**: cold product mounts start
   at a 6-source residency cap that grows as each slice CONFIRMS (frame
   list stays full — playback order/followers/newest unchanged; the
   next-ready clock plays the resident subset). Verified: cold GEO switch
   mounts 3→9→20 sources paced, zero errors; contract smoke extended.
3. **Front pips**: line and pips now share ONE densely-sampled smoothed
   curve — symbols sit ON the line, spaced along it, oriented by the local
   tangent; stationary alternates type AND side. (They floated off at
   every bend before: the line was smoothed, the pip-walk wasn't.)
4. **NHC overlay — live**: generate_nhc_overlay.py + update-nhc-overlay
   (30-min): forecast cones + track lines per storm (official per-storm
   GIS zips via the public storm index) + 2/7-day formation areas
   (graphical outlook shapefile); positions REUSE global_storms.geojson
   (home-map marker classification — one truth). Client: in-GL vectors
   (cones white, areas colored by 7-day chance) + canvas SSHWS glyphs
   (D/S/1-5, invest red X, names at zoom), honest-gated, re-raise
   discipline, export-composited. AL/EP/CP; honestly blank elsewhere.
5. **METAR + sfc ANIMATE with the loop**: both emit rolling timestamped
   series + manifests (MRMS deferred-prune discipline; metar 10-min keep
   18, sfc keyed on analysis VALID keep 10; legacy latest.json kept in
   sync for deploy order). Client seriesStore (manifest + LRU frames +
   nearest-join + legacy fallback) joins per displayed sat frame through
   the same clock as MRMS; stale frame holds until the join lands (no
   stutter); honest skew gates (75 min obs / 4.5 h sfc). Verified joins +
   skew drops headless.
6. **MRMS modern-radar quality** (Andrew's mid-batch directive): NATIVE
   7000-px output, row-wise BICUBIC mercator warp (clip kills ringing),
   CONTINUOUS color ramp interpolated between the TAT-radar.pal anchors
   (same identity, no hard 5-dBZ bands, feathered echo edge), linear GPU
   sampling client-side. Max-preserving smoothing keeps cores honest
   (synthetic 1-cell 65 dBZ survives at 63.7). Verified zoomed through
   the real client: smooth continuous gradients, no blocky cells.

**Post-ship adversarial review caught 5 real majors** (one reproduced in a
Node simulation), all fixed forward @4e9ad623: a seriesStore LRU-ghost
crash that could freeze the playback clock; the residency-ramp gate
wedging camera-move resumes during cold ramps; an NHC toggle-off race
mounting ghost layers; all three series generators collapsing their
rolling series on a single transient R2 manifest-read error (now
fail-loudly); the NHC emitter lacking honest-gate parity (GIS outage
during active storms published a cone-less doc — now retries + refuses).

### Site-perf pass (@6fe7b8bb) — measured first, fixed what the numbers said

Headless-Chromium traces (per-page isolated browsers) + curl cross-checks:
**documents are fast site-wide** (TTFB 39–55 ms every page — an early
4–20 s reading was harness contention, disproven by curl ×2 per doc). The
real weight was vectors: models moved **40.6 MB decompressed** (ne_10m
coastline 10.1 MB + admin polygons, all cf-DYNAMIC = uncacheable at the
edge by extension; LCP 3.0 s), and the explorer **83.8 MB** — the same
basemap set fetched TWICE because loading=lazy still mounts the below-fold
legacy iframe inside Chromium's distance threshold.

- scripts/quantize_geojson.py: coords→3 decimals + strip unread attribute
  tables across the committed ne_* set — **19.7 → 12.2 MB decompressed**
  (coastline 10.1→7.2, countries 3.1→1.7, states 2.3→1.2, lines halved);
  idempotent, rerun after upstream re-downloads. No consumer reads
  properties (verified); furniture renders identically (headless).
- Explorer legacy iframe now mounts via IntersectionObserver (400 px
  margin) — the duplicate basemap + the whole legacy page cost nothing
  until genuinely scrolled toward.
- Verified live post-deploy via curl: quantized coastline serves at
  1.97 MB wire (stable ×3, TTFB 50–90 ms). Honest note: after-timings from
  this Codespace were unusable (its own network congested mid-run —
  untouched pages read 15 s); the SIZE wins are curl-proven, the timing
  delta follows from bytes-on-wire.
- **Flagged for the parked Cloudflare token** (asset-level fixes only
  tonight): an edge cache rule for .geojson (every models/explorer visitor
  still pulls ~2 MB wire from origin per visit — the single biggest
  remaining lever), fonts (woff→woff2 + preload or self-host), the SST
  global_actual.png/jpg double-load audit, maplibre self-host.

## 2026-07-18 (~16:4x–18:3x UTC) — BOX OPS EXECUTED + SECOND TESTER BATCH (MRMS animation/smoothing, selector SSOT, playback cadence)

**Standing directive received mid-session: operational box work is the
agent's now** (only NEW external credentials queue for Andrew). Executed
immediately over SSH + the box's tsr deploy key:

- **tsr publishes**: both previously-unpushable commit sets are on GitHub
  (main @10ad9cc, s2-sat-ingest @4afdee9) — routed through the box clone.
- **Render deploy**: fast-path build live, /health green, floaters
  unaffected; archive frame 4.6–9 s end-to-end (fetch-dominated now);
  pace hint 1500 ms + 90/min advertised. TM client (deployed): honors the
  hint down to 1.2 s and opens a SECOND request lane when the pace is
  ≤2 s — cold 25-frame window ~2.5–4 min → tens of seconds; warm scrubs
  instant. Remaining lever: NCEI ranged-read parallelism (queued).
- **s2 emit-cron**: newest-first + --allow-geometry-change deployed;
  conus current to ~10 min; stopgap schedule retired.
- **All three overlay feeds live** (mrms/metar/sfc first emits verified
  landing; toggles un-grey themselves).
- **`sat-explorer-fixes` MERGED** (Andrew's go-ahead) — the whole tester
  sweep + overlays serve at /satellite/explorer/.

**Second tester batch shipped (@8fac2656)**, adversarially reviewed
(3-dimension fan-out + per-finding refutation agents; 6 confirmed majors
ALL fixed pre-commit — including two that WOULD have shipped: the radar
layer being permanently buried under later-mounted frame layers, and
sigma-1.5 smoothing measurably erasing small intense cores):

1. **MRMS animates with the sat loop**: rolling timestamped series with
   one-cadence DEFERRED pruning (immediate deletes raced live manifests
   into 404 radar); client time-locks nearest-scan-per-displayed-frame
   through the shared clock via ONE stable image source per pane +
   ImageSource.updateImage (no source churn, no swap flash), >45-min skew
   hides the layer honestly, and the layer re-raises itself under 'grat'
   every sync (else frame layers bury it). Verified headless incl. layer
   order post-switch.
2. **MRMS smooth**: max-preserving smoothing (gaussian 0.8 ∨ original) +
   field-space bilinear web-mercator warp + colorize at output res +
   linear client resampling — smooth contours, honest cores (synthetic
   1-cell 65 dBZ survives at 63.7; was invisible under plain smoothing).
3. **Playback cadence**: the clock advances to the next READY frame
   (skip, never stall-and-jump); per-frame BT probe fetches pause during
   playback (a real per-tick fetch+decode stutter source) and restore on
   stop; gl-lost rebuilds carry the playback flag + re-enable active
   overlay layers.
4. **Selector SSOT**: headers derive from the RENDERED manifest;
   setProduct carries a request epoch (same-product re-select = clean
   freshness no-op — the GEO-ring re-select crash was retiring a product
   into itself, colliding source ids; superseded in-flight switches can
   no longer stomp newer selections); failed lead switches snap the rail
   back to rendered reality, tokened against stale failures. Verified
   headless: selection/tiles/header three-way agreement, re-select ×2 +
   direct same-URL setProduct clean, zero console errors.

## 2026-07-18 (~08:0x–16:2x UTC) — SATELLITE EXPLORER OVERNIGHT: 10/10 tester bugs fixed + 3 new overlays built, all headless-verified

Everything on branch `sat-explorer-fixes` (12 commits) except the emit
workflows + overlay ingests, which run only from main and were
cherry-picked there (b0749723, aafa182c, e4d3fef7, 4a1c41e6, 543f9e63).
Every viewer fix was verified in a real headless Chromium against the
live CDN (`tests/explorer_headless_harness.cjs` — 8 scenarios, console
errors captured, screenshots eyeballed). `python -m unittest discover
tests` green apart from the two PRE-EXISTING env/stranded-work failures
(ecmwf-dep imports; the un-stamped models hashes from the uncommitted
invest-marker work sitting in this Codespace's tree — untouched, still
uncommitted, NOT mine to land).

### The tester-bug matrix (Discord list → status)

1. **GEO-ring lags/crashes — FIXED (viewer)**: 48 world-covering raster
   sources was unbounded GPU texture residency (integrated GPUs lost the
   WebGL context = "site crashed"). World-spanning products now clamp
   the playback loop (20 frames; 10 on ≤4 GB devices), and a lost GL
   context self-heals: the viewer degrades its perf profile, the cockpit
   rebuilds the pane in place (same product/camera, smaller loop) instead
   of leaving a dead black stage.
2. **Viewer behind live (1630Z vs 1750Z) — FIXED, three roots**:
   ① viewer: the manifest merge deliberately never advanced a paused
   viewer — now a viewer sitting on the live edge follows the feed to the
   new tail through the gated reveal path, and hidden tabs refresh the
   moment they're visible again. ② emit ordering: `_backfill_slots`
   walked slots OLDEST-first — the newest geo slot rendered ~40 min after
   run start and died first on timeout kills (the §11-H enscenters lesson,
   unapplied). Fixed in tsr-s2 c5da203 + self-patched in the GH lanes.
   ③ emit structure: the geo composite ran hourly BEHIND ~56 min of
   setup+riders in one workflow — split into `emit-geo-global` (:23/:53,
   geo only) + `emit-diag-riders` (:13/:43). Verified live: world
   composite went from 2h+ behind to **latest 15:20Z at an 16:02Z check
   (as_of 16:02)** — steady-state ~25–55 min on GH, 10-min cadence
   returns with the box (queued).
   **PLUS the big one — 19 conus products frozen since 07-17 02:5x**:
   read from the box's emit-cron logs (SSH read-only): a prior session's
   on-demand deep-zoom cut left ONE frame at a different pyramid geometry
   (20260715T202117Z) in the cron prefix, and the emitter's geometry
   guard correctly REFUSES every manifest rebuild that would drop it —
   truecolor/sandwich/RGBs/channels all froze while ir/irbd (unaffected
   products) merely crawled. `emit-conus-stopgap.yml` (hourly :07/:37)
   now runs the emitter's own remediation (`--allow-geometry-change`,
   manifest-only, deletes nothing) — heals the manifests every run AND
   gives conus an hourly newest-slot refresh. Durable fix = box one-liner
   (morning-TODO −1).
3. **Fade/crossfade on frame switch — FIXED**: `raster-fade-duration:0`
   never governed the reveal's `setPaintProperty` opacity flips — MapLibre
   animates every paint change through a default **300 ms transition**
   unless `raster-opacity-transition {duration:0}` is set. It never was.
   One paint entry per frame layer; swaps cut clean now.
4. **Playback lag / Time Machine ~2+ min — FIXED where deployable,
   3× server fix queued**: the 25-frame window's floor is the client's
   own 6.5 s pacing (sized to the render box's public 10/min limit) plus
   seconds-per-frame server renders. Server: the archive tiers are
   REGULAR lat/lon grids being pushed through pcolormesh as millions of
   meshgridded quad vertices — tsr @7283267 adds a separability-detected
   imshow fast path (**0.70 s vs 2.06 s measured** at GridSat-GOES scale;
   floater/meso geos path provably can't take the branch; 8/8 new tests
   incl. both-paths pixel parity) + a 2-entry MergIR granule cache (each
   hourly file was re-downloaded for its second half-hour slot) + an
   `X-Archive-Pace-Ms` response hint. Client (deployed): adopts the pace
   hint when the backend advertises it, revokes on any 429 — deploy-order
   safe. Live playback also benefits from #1's caps + #3's clean swaps.
5. **IR blurry at default view — FIXED (viewer)**: the pyramid ships
   512-px tiles declared `tileSize:512` — every HiDPI screen displayed
   them 2× upsampled (the re-arch's "@2x asset" intent was never wired).
   HiDPI now declares tiles at 256 so MapLibre pulls one pyramid level
   deeper = native-res pixels; low-memory devices keep the cheap path
   (crisp isn't worth an OOM). Note: geo-global's native maxzoom is 4
   (8192 px raster) — deeper world sharpness is an emitter pyramid_px
   decision, logged, not a viewer bug.
6. **GEO ring VIS — SCOPED, deliberately not built** (stability first per
   the brief, and it's not a stitch extension): geo-global is BT-only by
   design (BT is BT on every sensor; the blend is per-pixel BT-weighted).
   VIS needs: a reflectance branch in `produce_global_composite` (the
   Kelvin-normed `_colorize_bt` is meaningless for 0–1 reflectance), a
   solar-zenith night mask (a world mosaic always holds a night
   hemisphere), `SEVIRI_DATASETS` vis entries (else the Meteosat member
   KeyErrors to a silent gap), products-index day/bt flag fixes
   (hardcoded for geo rows), and BT-inspector gating. ~a day's careful
   work on tsr-s2; the 8192-px grid (~22.8 px/deg) is the resolution
   ceiling either way.
7. **Coastlines look bad — FIXED**: the coast was already ne_10m — but
   borders/states drew admin POLYGON outlines, whose rings re-trace every
   coastline at 50m slightly offset from the 10m coast = a doubled fuzzy
   edge on every shoreline. Furniture now uses the Natural Earth
   boundary-LINES files (land borders only; vendored same-origin), coast
   casing lightened.
8. **Drag-a-box broken — FIXED, root cause reproduced headless**:
   `enableDrawBox` owns the shift+drag gesture but was only wired on the
   first Box-button click — every pane booted with NO draw listener, so
   shift+drag just panned (reproduced: no rectangle, camera pans). Now
   wired at pane creation on every pane; Box button shows its armed
   state; pointer events cover touch (armed-drag path — no shift key on
   mobile); capture-phase stopPropagation means MapLibre never sees the
   gesture (kills the competing pan AND the stray click-to-pin after
   every box). Verified: both paths draw + fitBounds land.
9. **Nadir-nearest auto-switch — BUILT**: zooming into an area from the
   GEO ring switches to the nadir-nearest satellite's tightest domain
   (CONUS/WPAC inside their footprints, else FD; availability-gated),
   panning across the Pacific follows the nadir, zooming out to the
   domain's fit floor returns to the ring at the WORLD fit. Auto only
   moves between auto states — hand-picked domains suppress it. Verified
   headless: ring → 135E z4 → hw-wpac (fresh 15:00Z AHI); pan 95W →
   conus; zoom out → ring.
10. **Time Machine back-button — FIXED**: no history management existed;
    Back left the page and a return rebooted to the default. Entering TM
    now pushes ONE history entry; Back closes the archive session with
    every pane/field/camera/frame intact (Forward re-enters; direct exits
    neutralize the entry). Live view state also persists in the URL via
    debounced replaceState → a full reload restores the prior view
    through the existing applyURLState path. Verified headless.

### New overlays (11–13) — all three BUILT, not just started

11. **MRMS radar — LIVE end-to-end**: `generate_mrms_overlay.py` +
    `update-mrms.yml` (every 10 min): newest MergedReflectivityQCComposite
    from anonymous `noaa-mrms-pds`, pygrib decode, colorized with
    `assets/TAT-radar.pal` via the hafs_render pal parser (one palette,
    every radar product), **web-mercator warped** (pure per-row resample —
    an equirect image-source would misregister tens of km at CONUS
    latitudes), ~260 KB q90 WebP + manifest to `radar/mrms/conus/`.
    Client: new `rad` layer (MW's double-buffered image-source
    discipline), 60 s freshness poll, scan-time badge, honest-gated
    toggle (un-greys when the manifest exists). Headless-verified: echoes
    register exactly inside the IR cloud canopies.
12. **METAR surface obs — LIVE end-to-end, GLOBAL**: `generate_metar_obs
    .py` + `update-metar.yml` (every 10 min): the free aviationweather.gov
    global cache → one compact rank-sorted JSON (5183 stations at build
    time, 182 in the WPAC box, ~250 KB). Client: new `obs` canvas layer
    reusing the ASCAT camera-sync + legacy `drawBarb` painter — standard
    station model (T upper-left, Td lower-left, coded SLP upper-right,
    halo'd barb, ID at z≥5.5), zoom-scaled declutter keeping the
    fullest ob per cell, export-composited. Headless-verified over CONUS
    IR and over Himawari-9 (Korean/Japanese stations).
13. **Surface analysis — LIVE end-to-end (fronts + centers)**:
    `generate_sfc_analysis.py` + `update-sfc-analysis.yml` (hourly;
    WPC issues 3-hourly): parses the coded CODSUS bulletin (38 centers +
    46 fronts on the live 12Z test) → `sfc/analysis/latest.json`.
    Client: new `sfc` canvas layer — smoothed fronts with pips (cold
    blue triangles / warm red semis / occluded purple alternating /
    stationary alternating red-blue on alternating sides / troughs
    dashed amber; pips straddle the line — the coded bulletin carries no
    movement side and asserting one would be fabrication), H/L letters
    with pressures, valid-time badge, honest-gated toggle. Isobars =
    noted follow-on (RTMA/GFS MSLP contouring; CODSUS has none).
    Headless-verified: the 12Z chart over CONUS IR + METAR reads as a
    proper synoptic analysis.

    First cron fires for all three were due 16:03–16:17Z; the toggles
    un-grey themselves the moment each feed lands (honest gate — no
    reload needed for later scans, one reload to enable).

### Also known / notes

- **This Codespace's permission layer** allows read-only box SSH but
  blocks box mutations, `gh` CLI, and some compound commands — hence the
  GH-workflow route for everything emit-side and the queued box
  one-liners. If you want the agent deploying to the box next session,
  an allow rule for `ssh -i ~/.ssh/tat_box root@2.25.183.231 *` does it.
- The uncommitted invest-marker/stream work predating this session is
  still uncommitted in this Codespace (11 files + 1 new test) — carried
  carefully around every commit, never staged.
- `?manifest=` dev override race + `SCData.storms()` dup-count quirk
  noted during recon (pre-existing, unfixed, low-priority).
- Emit lanes tonight also self-patch newest-first idempotently and
  no-op with a delete-me notice once c5da203 is pushed upstream.

## 2026-07-17 (~02:4x UTC) — BOX TAKEOVER: the "44-hour poller hang" was never a hang — two migration env-parity bugs found + fixed at the root

Direct SSH access live (key added by Andrew, `~/.ssh/tat_box` in this
Codespace). Everything below executed on the box per the takeover
directive; repo-side tsr changes are committed ON THE BOX (no tsr push
auth from this Codespace) — **Andrew: one `cd /root/tat-satellite-render
&& git push` publishes 4 commits** (R2_PREFIX pin, CYCLOLAB_PREFIX pin,
RUNBOOK §7, + the pull already applied 863d6df).

### The headline: floaters/ froze because NOBODY was writing it

The audit's "box floater poller stalled ~44 h" was misdiagnosed (by me).
The truth found with box access: the shared box `.env` carries
`R2_PREFIX=meso` (for the MESO stack), `env_file:` inheritance pointed
the render-stack floater-poller at `meso/*` from its 2026-07-14 bring-up
(double-writing the meso poller's keys, never writing `floaters/*` at
all), and `floaters/*` stayed alive only through the GH stopgap
`floater-worker.yml` — whose retired schedule's LAST run is the exact
freeze timestamp (Jul 15 01:03Z). Nothing hung; the process was healthy
and writing the wrong keys. **Fix: `R2_PREFIX: floaters` pinned in
`docker-compose.render.yml` (environment beats env_file) — floaters/
manifest advanced within minutes (02:18:07Z after 49 h frozen).**

### Same bug, second instance: CycloLab adv/cones

The "stuck adv sub-task" (ELIDA on adv 1) was the same class:
`CYCLOLAB_PREFIX` defaults to `shadow/cyclolab` (promote = set the env),
Railway had the promoted value, the box migration never carried it — so
the poller wrote adv 9 to the shadow tree while the site's
`cyclolab/adv/` froze at the last promoted write. **Fix:
`CYCLOLAB_PREFIX: cyclolab` pinned in the compose (the block even
documents this exact trap for GLOBAL_GEOJSON_KEY — this var was missed
in the parity list).** Swept the codebase for further `shadow/` prefix
defaults: no other instances.

### Also done on the box

- **RUNBOOK §2/§4a rebuild executed**: tsr `863d6df` (antimeridian
  bboxes + 429 backoff + internal rate tier) now deployed; render
  healthy; all pollers recreated; the stall watchdog armed.
- **s2 emit-cron now runs ALL suites** — recreated from the
  `s2-sat-ingest` tip (its default: `conus fd himawari9-wpac
  himawari9-fd geo-global`) in a dedicated worktree `/root/tsr-s2`
  (`--profile cron`; the two compose projects no longer share one
  checkout on the wrong branch). fd/wpac/himawari-fd first-emit
  verification in flight → the GH stopgap schedules (emit-geo-global +
  floater-worker) get disabled the moment those manifests advance.
- **Output watchdog installed** (systemd `tat-floater-watchdog.timer`,
  10-min cadence): restarts the floater-poller when the floaters
  manifest is >30 min stale, one restart per 30 min max (a config
  failure like this one can't restart-loop it — repeated stale findings
  in `/var/log/tat-floater-watchdog.log` are then the alert). Documented
  as RUNBOOK-RENDER §7: restart policies and in-process watchdogs are
  both blind to a healthy process writing the wrong keys — watch the
  PRODUCT.

## 2026-07-16 (~22:2x UTC) — SUBSEASONAL PHASE 3: Group A shipped (@7ea5eae5), Group B items 5+6 shipped (@6f469a88), RMM forecast in flight

Andrew's Phase-3 queue, reusing the Phase-2 stack as specced. Everything
smoke-tested offline end-to-end (synthetic archives + REAL GEFS fetches)
with panels eyeballed before each push; the live run lands at the next
update-subseasonal cron (15:41Z daily; job timeout raised 120→200 min for
the bigger matrix).

- **Item 1 — WK wave overlays on every Hovmöller variable**: u850/u200/
  χ200 run the same wk_filter real-time path as OLR; wave selector
  ungated (meta-driven `template_wave`, wave-less names + legacy copies
  kept one cycle for cached pages — both deploy-skew directions safe);
  contour levels are ±(1..4)×each variable's shading step, which
  reproduces OLR's historic ±10..40 exactly; new `mrgtd_er` combined set.
- **Item 2 — v850**: archive schema widened to carry v (old u-only
  archives load v=NaN and backfill forward — no cold restart); fetch
  grabs UGRD+VGRD in one byte-range; v850 section gates itself off until
  the rebuilt u+v ERA5 climatology lands (build running locally against
  APDRC as of this entry — the .nc + credits flip commit together, per
  the house rule); v850 defaults its overlay to MRG-TD + ER.
- **Item 3 — VP wave contours**: optional per-band WK overlay on the χ′
  maps (filter runs per LATITUDE row on the daily-chi archive; contours
  tropics-only 25°S–25°N, labeled, divergent solid / convergent dashed);
  band toggle on the page; advertised in vp_meta only when both levels
  rendered.
- **Item 6 — VP forecast maps**: +7/+10-day GEFS ENSEMBLE-MEAN χ′ map
  sets (`subseasonal/gefs_mean.py`: geavg via Herbie/AWS, daily means of
  the 00/06/12/18Z valid instants, verified live against the real
  2026-07-16 00Z cycle); heading carries init + valid, on-plot
  FORECAST/ensemble-mean-smoothed note, GEFS-named credit line; the
  16-day tail is fetched ONCE, feeds the wave-filter concat, and is
  saved to gefs_tail.nc for the Hovmöller step (one fetch, two
  consumers).
- **Item 5 — Hovmöller forecast tails**: u850/u200/v850/χ200 extend
  below a labeled dashed init line with the GEFS ens-mean tail (init +
  valid-to + smoothed + ~16-day limit burned onto the line; title tags
  "(+GEFS)"); χ tail solved from the ens-mean wind via chi_core (the
  solve is linear, so that IS the mean χ); anomalies vs the same ERA5
  climo; wave filter runs on the analysis+forecast concat; archive→tail
  holes stay honest NaN gap rows; OLR stays analysis-only per spec.
- **Item 4 — GEFS-member RMM forecast: SHIPPED (@20804e1c),
  closure-verified.** Every constant traced to a primary source before
  coding (research pass with cited URLs): the original WH04 combined EOF
  structures are vendored (`subseasonal/wh04_eofs.txt`, cross-checked
  byte-equal against the DTC/METplus reference copies), field norms
  15.11623 / 1.81355 / 4.80978 ship in the file, PC stds 8.6184 / 8.4074
  from the METplus reference (NOT sqrt-eigenvalues), preprocessing per
  Gottschalck et al. 2010 verbatim (trailing-120-day mean removed on the
  obs+forecast CONCAT per member; no ENSO step; no taper). **The
  pipeline was validated by projecting 60 days of REAL obs through it
  against BoM's official RMM: corr PC1 = 0.997, PC2 = 0.990, rmse
  0.35/0.22.** The residual climatology-base bias is measured per run
  (seam vs BoM's newest pure-obs day) and the forecast anchors onto the
  official endpoint when it exceeds 0.15 — offset + choice + the CDR
  OLR-lag bridge days all disclosed in mjo_meta.json. Phase space:
  members thin, ensemble mean bold amber, dated to ~16 d; amplitude:
  mean + member envelope past a labeled init line. ULWRF gotcha coded
  around (6-h avg buckets, absent at f000). 5 unit tests + live 7-member
  end-to-end smoke rendered + eyeballed.
- **v850 climatology build**: the first APDRC attempt silently hung for
  2 h (netCDF4 OPeNDAP reads have no socket timeout — noted); restarted
  unbuffered and moving at ~85 s per 5-year slab. The .nc + v850
  activation + credits commit lands when it completes.

## 2026-07-16 (~21:3x UTC) — FULL-SITE STALENESS AUDIT + REMEDIATION (the box migration IS the systemic cause)

Andrew's read was right. A 181-row origin-freshness audit (8 parallel
audit agents; every manifest fetched cache-busted at R2, committed
products checked via git on origin/main, `gh run list` for every GH
writer) found **nothing edge-cache-only — every stale product is stale at
the origin**, and the root causes cluster almost entirely on the
half-finished box migration.

### Audit verdict by family (full row-level evidence in the session; the
### standing monitor below now tracks all of it continuously)

| family | state | root cause |
| --- | --- | --- |
| ACE + tracks pages (3 basins + global, committed) | FRESH (19:45Z run) | — |
| live feeds (feeds/*_{ace,tracks}, global_storms.geojson) | FRESH (minutes old) | box intensity poller healthy |
| SST statics / subsurface / ARMOR3D / season GIFs / SST MP4s | FRESH | — |
| subseasonal (vp/hov/mjo metas) | FRESH (15:41Z run) | — |
| MW / ASCAT / recon swaths (GH writers) | FRESH | — |
| enscenters (5-workflow managed manifest) | FRESH (20:52Z) | — |
| explorer goes19/conus IR+longwave | FRESH | box conus emit-cron healthy |
| **explorer goes19/conus reflective+some-IR products** (truecolor, sandwich, RGBs, c01–c06/c10–c12) | **STALE since restore** | box-side per-band emit failure AFTER the GOES-19 restore — upstream NODD verified complete for ALL bands; pure-longwave products emit fine. Needs box logs. |
| **explorer goes19/fd + himawari9/wpac full suites** | **STALE (one-shot emits Jul 9/10)** | box emit-cron runs ONLY the conus suite — the queued `S2_CRON_SUITES="conus fd himawari9-wpac himawari9-fd"` + restart was never executed |
| **explorer himawari9/fd suite** | **NEVER EMITTED** | same queued box step |
| explorer fd/ir + wpac/ir + geo world composite | FRESH (GH rider/stopgap) | rider timeouts post-restore starved the geo step twice — **fixed** (see below) |
| **floater fleet + MW/ASCAT/recon backdrops** | **STALE since Jul 15 ~01:00Z (~44 h)** | box floater poller stalled — began ~19 h BEFORE the GOES-19 anomaly and froze Himawari-fed regions too, so NOT the satellite; consistent with the undeployed tsr 863d6df chain. Box restart needed. |
| **HAFS manifest** | STALE (Jul 13) | known, fix in flight elsewhere — recorded only (RENDER_HAFS_ON_CRON gate) |
| **CycloLab analogs** | was MISSING | **root-caused + FIXED here**: update-analogs ran green for days while every storm failed on `ModuleNotFoundError: shapely` and "wrote 0 storm(s)" |
| CycloLab adv/cone (ELIDA) | STALE (advisory 1 of ~8) | box intensity poller's adv sub-task stuck while its other outputs are minutes-fresh — box-side look needed |
| cyclolab/index.html (lab root) | missing | router maps the root but no index was ever published — Andrew's call: publish one or drop the mapping |

### Fixes landed (root, not fix-forward)

- **update-analogs.yml + generate_analogs.py (@e396bba4)**: `shapely`
  added to the install; the generator now FAILS LOUDLY when every active
  storm errors, so a systemic failure can never show green again.
  Verifies itself on the next 6-h cron (dispatch needs actions:write this
  Codespace token doesn't have).
- **emit-geo-global.yml (@e396bba4)**: the diagnostics-rider step gets
  `continue-on-error` + per-command 22-min timeouts — a step TIMEOUT
  (which `|| echo` never catches) was killing the job before the geo
  step, which starved the world composite ~2 h post-restore when NODD
  reads slowed to ~7 min/file.
- **GOES-19 anomaly handling (@0c5bceac)** — separate entry below.

### Migration / single-writer verdict (task: "no two writers, one key")

- feeds/* + global_storms.geojson: box poller is the SOLE writer —
  verified in the run logs that update-ace.yml's upload steps are skipped
  (`WRITE_LIVE_FEEDS=false`) and floater-worker.yml's schedule stays
  retired (dispatch-only emergency lever). Correctly single-writer.
- models/enscenters: five workflows, ONE manifest, managed by the CAS
  merge script — safe by design, verified fresh.
- models/hafs: designed dual-writer with never-regress If-Match merge —
  current problem is the opposite (zero live writers; in-flight fix).
- explorer `fd/ir`, `wpac/ir`, geo suite: GH rider + box suite both
  writable BY DESIGN (ready-marker dedup) — the workflow header already
  says to disable the GH cron once the box crons start. That cutover is
  the remaining migration step and it is box-side (queued below).
- MW/ASCAT/recon swaths: GH-only. Floater tree: box-only (that
  single-writer decision is why the poller stall has no fallback).

### Standing freshness monitor (@70e2760c) — Andrew stops eyeballing

`scripts/freshness_probe.py` + `.github/workflows/freshness-monitor.yml`
(half-hourly): probes 32 origin endpoints (every family above), compares
age against each writer's cadence (stale > max(3×cadence, cadence+45 min)
— the same margin the site's honesty gates use), publishes the rollup to
`feeds/freshness.json`, and turns the run RED exactly once when a product
NEWLY goes stale (GH failure email = the alert; known-down products
report but never alarm). Live validation at build time: 6/32 stale, all
with known causes (the box items + HAFS above). When the box work lands,
prune the `known_down` notes in the registry so alerting re-arms.

## 2026-07-16 (~18:0x UTC) — GOES-19 ANOMALY HANDLING: honest pause/resume + first-hour nav caveat

GOES-19 recovered FAST — CONUS scans resumed **17:16:15Z today** (verified in
the conus manifest's scan list; a 20.9 h gap ends there), full disk still
refilling as of ~17:50Z. Per Andrew's call the **multi-sat fallback build
(Meteosat / GOES-16) was DROPPED** — nothing had been wired yet, so nothing
was removed; not worth building for a ~1-day outage. What landed instead
(this commit + the prior session's honesty pass it finishes):

- **`/sat-health.js` v2** — the shared GOES-East health probe now watches
  BOTH `goes19/fd/ir` + `goes19/conus/ir` `latest_times.json` (conus resumes
  first after an outage; satellite-level truth = freshest of the two).
  Three states, all data-age-driven so every transition is automatic, no
  manual step: PAUSED (amber banner, with "imagery expected back ~19:00 UTC
  16 Jul (NOAA)" while that ETA is still meaningful, generic afterwards) ·
  RESUMED (NOAA first-hour nav-caveat note, clears itself ~90 min after the
  restore boundary) · CLEAR.
- **First-hour NAV CAVEAT (NOAA: navigation "slightly degraded" for ~1 h
  after ABI restore)** — the restore boundary is detected in the DATA: a
  >6 h gap in a manifest's scan list ending inside the configured anomaly
  window (Jul 15 17:00Z – Jul 18 00:00Z; the window scoping is deliberate —
  an ordinary producer stall also leaves a gap, and blaming satellite
  navigation for a cron outage would be a lie; after Jul 18 the constant is
  inert). Frames scanned within 70 min of a boundary answer
  `TATSatHealth.navDegraded(scanMs)` = true and every quantitative surface
  tags them: cockpit pane chrome/clock/exports ("NAV CAVEAT (scanned <1 h
  after GOES-19 restore)"), floater/meso player time readouts (+ tooltip),
  objfix ARCHER/ADT runs (counted on the final analyzed frame set, east
  sources only), TC-diag completion line. Live right now: window
  17:16–18:26Z, verified with a node harness against the REAL manifests
  (6/6 assertions, correct resumed-notice copy).
- **Prior session's feed-paused honesty pass rides along** (was uncommitted
  in the tree, now verified + landed): cockpit FEED PAUSED tags +
  paused rail chips + world-composite "GOES-East paused" wedge badge,
  ascat/microwave BACKDROP STALE tags, recon chrome-free `bd_key` backdrop
  adoption with a 3 h age gate, satellite-page outage attribution
  (source-aware, never blames GOES for a producer stall) + friendlier 5xx
  copy while the feed is down.
- **Cache-bust bumps for every edited script** (?v= — the CDN edge masked a
  fix once): sat-health v2, cockpit core5, objfix_panel ofx7, tc_diag tcd4,
  microwave 13, ascat 0017, recon c4f45767b5.
- **Auto-resume verified at the ingest level**: the box emit pipeline never
  stopped — conus manifest `as_of` 17:21Z (fresh emit 5 min after the first
  new scan). FD refills on its own as FD scans publish; the paused chrome
  on FD panes is manifest-age-driven and clears itself. No manual step
  anywhere.
- Suite: `python -m unittest discover tests` green. A 4-lens adversarial
  review workflow (17 agents) over the full diff CONFIRMED 16 real defects
  before landing — all fixed + re-verified: archive/Time-Machine analyses
  no longer get false "feed paused" stamps (objfix warn + canvas prov tag +
  TC-diag completion line are live-source-gated); the East gate is now
  longitude-aware (shared `eastFed`: AL always, EP only east of ~106°W —
  a GOES-West-fed EP floater can no longer collect GOES-19 caveats); the
  nav window anchors to the EARLIEST restore boundary only (an FD feed
  resuming hours later is a late emit, not a second nav event); the
  "GOES-19 anomaly (NOAA)" attribution copy is scoped to the anomaly
  window everywhere (banner, cockpit wedge badge, floater inactive note,
  5xx toast — future stalls state data age without inventing a cause);
  the floater inactive note also requires the source's own stall to
  overlap the outage (the pre-outage-dead-floater case, live-confirmed on
  EP05); objfix honesty warns now survive renderStats rebuilds; the 5xx
  outage toast fires only on time=latest renders; two em-dashes scrubbed.

**Marker-gate (ace_core 0.8.5) + stream-page rewrites remain uncommitted in
this Codespace on purpose** — they are separate in-flight threads (the
ATCF-number marker gate is ACE/data-critical and still owes its byte-identical
ACE gate; the stream re-point has its own smoke harness). Not mine to land
from this session.

## 2026-07-15 (~20:5x UTC) — SUBSEASONAL PHASE 2 LANDED: Hovmöllers + equatorial-wave filtering (@42fcb5ef)

The dead session's stranded Phase-2 files (subseasonal/wk_filter.py,
generate_hovmollers.py, build_u_climatology.py + its built
u_climo_1991_2020.nc, tests/test_wk_filter.py) were found uncommitted,
**verified, hardened, and landed** — never stranded again:

- **wk_filter**: plain-numpy WK99 space-time filter, direction convention
  and dispersion masks test-locked (16/16); gained the **lowfreq band**
  (>=120 d, |k|<=10, WW01 monitor) the selector spec calls for.
  u_climo verified clean (ERA5 monthly u 200/850, 0 NaNs, July equatorial
  easterlies at both levels).
- **REAL catch while verifying: PSL THREDDS silently returns ALL-ZERO
  data on large multi-timestep DAP subsets** of the OLR LTM aggregation
  (365-step read = 0.0 everywhere, <=60-step slabs correct; no error).
  The stranded draft would have shipped raw-OLR-as-anomaly (uniform
  saturated panels — seen live before the fix). Now: slabbed loads +
  `_guard_degenerate` refusing to render corrupt reads. Gotcha added to
  CLAUDE.md.
- **Genesis markers were double/triple-marking systems** — keyed by
  (sid, name) while a system's tcvitals name evolves (INVEST → FIVE →
  ELIDA, all 05E). Now keyed by ATCF id (60-day recycle window), marker
  at the earliest fix, label wears the latest name.
- Render layout rebuilt (explicit geometry, no tight_layout): three-row
  header, colorbar label beside the bar, per-variable credits — the
  draft's header/footer text collided everywhere. main() split into
  per-section functions with fault isolation: a PSL outage can't blank
  the u/chi panels; fails loudly only if everything failed.
- **Rendered locally end-to-end: 240 panels** — OLR+waves (168 = 7 wave
  sets x 4 bands x 3 days x 2 sectors), u850/u200 (48), chi200 (24, from
  the real 223-day chi archive pulled via the CDN). u path proven against
  live GFS via herbie (52-day local archive built; an rda.ucar.edu cert
  failure walked past by design). Panels eyeballed: WP-enhanced OLR field
  matches the known MJO state, chi200 dipole coherent, u850 dateline
  westerly burst consistent with the season.
- **/subseasonal/ gains the Hovmöller section**: hov_meta.json-driven
  selector (field / waves / band / days / sector) with baked fallback;
  new tests/hov_page_smoke.cjs **19/19 green** (build, panel swaps,
  wave-row hiding off-OLR, fallback, zero page errors). Nav untouched.
- **update-subseasonal.yml**: hovmöller render step AFTER the VP render
  (chi200 reads the just-topped-up archive) bracketed by restore/save of
  a new rolling `_buildcache/u_daily_archive.nc`; publish sync already
  covers hov/*.png + hov_meta.json. Suite failure set byte-identical to
  clean HEAD (11 env-dependent, verified via a HEAD worktree run).
- **Landing fight worth recording (now in CLAUDE.md):** the Codespace
  disk filled to 100% because `git fetch origin` (all-heads refspec)
  re-downloads the SST **orphan branch** (multi-GB MP4s, disjoint
  history) after every force-push, and each aborted fetch stranded a
  multi-GB `tmp_pack_*` in .git — 12.4 GB of dead packs removed, refspec
  narrowed to main-only, push landed clean (@26321b91 = origin/main).
- **First publish LIVE-VERIFIED** (run 29451530427, backfill_days=250,
  success): the u archive bootstrapped cold to **221 days on R2** in one
  run; CDN meta fresh (generated 21:28Z; olr through 07-12, u/chi through
  07-15; genesis markers 27 — the deduped count); sampled panels incl.
  the lowfreq view all fetch 200 from cdn.triple-a-tropics.com; the page
  section confirmed serving at triple-a-tropics.com/subseasonal/ (Pages
  deployed the push). Daily 15:41Z crons now carry the product.
- Also landed earlier today: **one-off analog composite** (@a4299410) —
  TC frequency anomaly for analog seasons 1972/82/91/97/2015 vs 1979-2014
  per 1°x1°, 3° Gaussian, analog tracks + current NHC GTWO MDR area
  hatched (10% 7-day), house chrome — `analog_composite.png` at repo root.
- **FOUND UNCOMMITTED, NOT MINE, NOT TOUCHED**: modified ace_core/
  (__init__, pyproject), enscenters/anchors.py, generate_tracks_plot.py,
  models/{enscenters.js,index.html}, stream/index.html,
  tests/{stream_smoke.cjs,test_invest_x_anchor.py,
  test_marker_type_agreement.py,test_ptc_activation.py} + untracked
  tests/test_designated_marker_number_gate.py — looks like an
  interrupted enscenters/marker-gate workstream. Auto-stashed around the
  rebase and popped back byte-identical. Needs the usual verify-then-land
  pass before anything wipes it.

---

## 2026-07-14 (~23:3x UTC) — ITEM 2 LANDED (tsr @863d6df) · ITEM 4 BUILT (encoder stack)

- **Item 2 (ASCAT-backdrop fixes) — the stranded diff verified, hardened,
  landed.** The dead session had authored the whole fix chain but left it
  uncommitted; on re-entry it was reviewed (3-lens adversarial workflow),
  test-run, FIXED, and pushed to tsr main:
  - 429s: own retry budget honoring Retry-After with a 15/30/60 s
    exponential floor + the ROOT fix — co-located pollers (private peer,
    no XFF) now get RATE_LIMIT_INTERNAL 600/min instead of sharing the
    public 10/min that starved the 16-region sweep at wpac.
  - swpac antimeridian: /render accepts unwrapped-E>180 and pre-wrapped
    crossing bboxes, renders them correctly (crossing-aware cartopy
    geometry, continuity unwrap, satellite routing).
  - **Review catches fixed before landing** (all locked with tests, 17
    groups green): forged-XFF "internal|" injection into the internal
    rate bucket (critical), full-globe span % 360 == 0 pixel-budget
    bypass (critical), widen_bbox_to_view midpointing a dateline storm
    box to its ANTIPODE → wrong-side backdrop published (major, two
    lenses found it independently), float-mod cache-key perturbation,
    reversed-edge typos silently rendering near-world frames, RATE_429_*
    vs RENDER_429_* env naming.
  - Full suite: failure set identical to clean HEAD (82 pre-existing env
    failures in test_cyclolab_shell.py only — cross-checked by stash).
  - **Deploys ONLY via the box pull+rebuild → MORNING-TO-DO #3.**
- **Item 4 (render→RTMP) — built + container-proven, NOT deployed
  (@87f10fb):** stream-encoder/ Dockerfile + compose profile +
  three-leg supervisor (Xvfb / kiosk-chromium with 12 h recycle /
  ffmpeg 6 Mbps 2s-GOP with no-progress watchdog + backoff restarts),
  RUNBOOK-STREAM.md. E2E-minus-ingest proven in a real container: the
  LIVE page renders on the virtual display with live-hydrated data and
  x11grab captures it (frame eyeballed; chromium single-launch after
  fixing the docker-seccomp sandbox crash-loop with a documented
  --no-sandbox). Only the YouTube leg awaits the key → MORNING-TO-DO #4.

---

## 2026-07-14 (~23:1x UTC) — STREAM PAGE LIVE at /stream/ (item 1 done, built in-house)

- **Andrew's stream.html never arrived** (no file/branch/PR, no
  "AGENT: verify" marker anywhere) — so the page was BUILT to the spec
  rather than blocking (@fb3d979): fixed 1920×1080 broadcast canvas,
  TAT chrome (Metropolis + locked palette + logo bar), UTC clock, amber
  TROPICS NOW ticker, active-systems rail, sidecard carousel (AL/EP/WP
  names boards + season-ACE board) with chevron wipe, Pacific-centered
  world map (house SSHWS colors, ace_core-mirroring D/S/1–5 glyphs,
  invest ×), overview + Cat-1+ storm-focus modes with a full-frame
  stinger. noindex, not in the nav (nav test unaffected — no nav-links
  block). If Andrew's own stream.html shows up it can replace this
  wholesale; wiring notes are all in the page header.
- **Live wiring verified field-by-field** against the real feeds first
  (schema documented in the page header): active systems are
  `kind=="active_marker"`, invest = `marker_type=="invest_x"`, there is
  NO basin/active/invest property — basin joins storm_id→track feature;
  categories are TD/TS/C1..C5 so the focus trigger is /^C[1-5]$/ or
  ≥64 kt. Hydrates CDN geojson + 3 ACE feeds every 5 min (CORS
  verified); embedded fallback snapshot renders the FULL canvas
  synchronously — never blanks; fetch failures keep last-good +
  honest DATA DELAYED chip.
- **Verified headless at 1920×1080** (tests/stream_smoke.cjs, 19 checks
  green): fully-offline fallback render (zero blanks, zero page
  errors), synthetic Cat-2 → stinger plays → focus engages with correct
  name/chip/intensity → returns to overview; carousel wipe fires.
  Shots in scratchpad (stream_offline/stream_stinger/stream_focus.png).
  **Confirmed serving 200 at https://triple-a-tropics.com/stream/.**

---

## 2026-07-14 (~22:4x UTC) — 3a + 3b LANDED + LIVE-VERIFIED: mislabel dead, coastlines legible

- **3a (@7f23c48):** every rendered ERA5 credit reverted to honest
  "NCEP/NCAR Reanalysis 1 1991–2020 (NOAA PSL)" — map footer
  (generate_velocity_potential.py) + both page mentions
  (subseasonal/index.html). Docstring now carries the rule: credits
  flip back ONLY in the same commit that lands the ERA5 .nc.
- **3b (same commit):** VP-map coastlines get a thin light casing
  (#dce7f3 lw 1.6) under a crisp dark line (#0a0e15 lw 0.7) — verified
  on a synthetic full-range render, legible over the darkest BrBG fills
  at both ends.
- **Live-verified:** dispatched update-subseasonal run 29372393918
  (success); pulled chi_anom_200_30d.png fresh from the CDN — new
  credit line + cased coastlines confirmed serving.
- **ERA5 rebuild attempt #3 running** (this session's scratchpad):
  supervisor with 300 s hard-kills per APDRC slab child, atomic .npz
  slab resume, 2-wide; assembles via the committed builder's own
  main(). If APDRC roulette blocks it again, next stop is an alternate
  anonymous source (RDA/PSL probe) — CDS API needs an account key we
  can't self-serve.

---

## 2026-07-14 (~21:1x UTC) — RE-KICK: queue re-entered, state assessed

- **IN PROGRESS.** Overnight session died after the stopgap retire; queue
  items 1–4 untouched. Findings on re-entry:
  - **stream.html is NOT in the repo** (no file, no branch, no PR, no
    "AGENT: verify" marker anywhere) — Andrew's add hasn't landed yet.
    Plan: verify the live feed schemas now, keep polling for the file,
    build to spec if it still hasn't arrived when the earlier items land.
  - **Item 2 (ASCAT backdrop) was AUTHORED but left uncommitted in tsr**
    by the dead session: app.py/render.py/satellites.py/floater_poller.py
    diff + tests/test_antimeridian.py (antimeridian bbox + 429 backoff,
    end-to-end). Verifying + landing it next — never leaving it stranded
    again.
  - **3a mislabel CONFIRMED**: committed chi_climo_1991_2020.nc attrs say
    `source: NCEP/NCAR Reanalysis 1` while page + map credits say ERA5.
    Fix order: revert credits to honest R1 + coastline casing (3b) in one
    commit + one re-render dispatch NOW; ERA5 build re-attempt runs in
    the background and flips credits back only when the file actually
    lands (no mislabel at any instant).

---

## 2026-07-13 (~18:4x UTC) — ace-core-v0.8.4: designated Central Pacific systems reach the live layer

- **The gap v0.8.3 left open, closed the same day** (@bc531118 TAT +
  @155276a2 tsr, tag ON main this time): if 90C designated (TD 01C, deck
  `bcp012026`), it would have VANISHED from the live layer — the EP sweep
  only fetched `bep` decks, while the historical basis already includes CP
  (IBTrACS files CP under BASIN=EP; Ioke 2006 = CP01). Now: EP sweeps the
  CPHC `bcp` chain (`atcf_patterns_extra`, both generators + feed-base
  passthrough + intensity_poller per-chain mirror); `agency_sid_from_atcf_id`
  maps CP-under-EP so the IBTrACS provisional row and the live designation
  collapse onto ONE storm; `GENESIS###` name-column values are placeholders;
  young-designation labels wear the storm's OWN letter on BOTH feeds
  (tracks "01C" + ACE gantt "TD 01C" — the reviewer caught the gantt still
  page-lettered and both FIX-FIRST findings were folded before the tag).
- Gates: --no-live A/B byte-identical vs pristine 0.8.2; 545-test suite;
  adversarial review with empirical evidence (archived Dora 2023 decks
  prove crossers never alias into bcp → no double-count; measured sweep
  cost = 6 extra requests/run when no CP storm exists). 11 new locked
  tests. Poller repo: 31/31 green, repinned v0.8.4, GH stopgap picks it up
  on its next run; **the Q17 box pull+rebuild now delivers floater fix +
  v0.8.4 + the bcp sweep in one shot.**
- Verified live post-land: update-ace re-ran under 0.8.4 → EP feed still
  carries 90C/91C with correct atcf_ids.

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

---

# 2026-07-19 · Repo size audit (GitHub quota warning)

## Findings

- **GitHub reports ~87.6 GB** for the repo; local `main` is only ~129 MB
  packed and the checked-out tree ~60 MB (largest files: the vendored
  basemap GeoJSONs, all needed). Nothing in the current tree or in main's
  history is meaningfully large — **no filter-repo rewrite is needed, at
  all**. The bulk is server-side:
- **`mp4-artifacts` orphan branch = the quota eater.** Tip = 1.47 GB
  (456 files). Since 2026-04-24 the SST + subsurface animation workflows
  force-pushed ~1.5 GB of disjoint history to it daily; every force-push
  strands the previous multi-GB pack as unreachable server-side garbage
  that GitHub GCs lazily. ~60 such pushes ≈ the 87 GB reported. No client
  can see or fix those objects — this was never a filter-repo case.
- **35 stale feature branches** (recon-*/hafs-*/sat-*/…) all predate the
  earlier main-history rewrite ("diverged ahead:1000+ behind:2567" vs
  main) — they anchor the pre-rewrite blobs on the server. Individually
  small (~100 MB-class, heavily shared), collectively the last tie to the
  old history. `sat-explorer-fixes` was the only provably-merged branch
  (ahead:0).

## Actions taken (safe, no history rewrite, no force-push)

- Retired the `mp4-artifacts` publish path — it was already dead weight:
  `product_animator.js` reads R2 (`cdn.triple-a-tropics.com`) since R2
  phase 3, verified live for all 7 families (fresh manifests + MP4 HEAD
  200 video/mp4). Removed both orphan-push steps from `update-sst.yml`,
  the push step from `update-subsurface-animations.yml`, deleted
  `scripts/sst_publish_orphan.sh`, guarded the historical seed step in
  `r2-backfill.yml` with `if: false`.
- Deleted remote branches: `mp4-artifacts` (was `b9dce6178c62`) and the
  merged `sat-explorer-fixes` (was `edc0c330d897`). Recovery: SHAs above
  restorable via API until GitHub GCs.
- With the branch gone and pushes stopped, GitHub's background
  maintenance reclaims the ~87 GB over time (days-to-weeks).

## QUEUED manual steps (Andrew)

- **2026-07-29 · HAFS cron wedged behind a doomed run; I cannot cancel it.**
  `gh run cancel` and `gh workflow run` both 403 from this Codespace (the token
  has no Actions write). Run **30459587743** started 17:20 UTC on `5fa39de7`,
  which PREDATES the width-2 + heartbeat fix (`b6b09f30`); it is the same
  `--ingest-jobs 4` config whose identical predecessor (30434658444) failed to
  finish 430 ingest frames in 5h50m. It holds the `update-hafs` concurrency slot
  until its 23:10 UTC timeout, with run **30486383133** (which HAS the fix)
  queued behind it. Safe to cancel: its Upload-to-R2 step never ran, so no
  partial sync is possible and the prior cycle stays live - the workflow's
  "don't cancel mid-flight" warning covers the sync phase only.
      gh run cancel 30459587743        # or the Actions UI
  NOT required: it self-clears at 23:10 and the fixed run then starts on its own.
  Cancelling only saves ~2.5 h of extra staleness on `/models/` (already ~15 h
  stale). The underlying gap, if worth closing: this Codespace's token cannot
  cancel or dispatch Actions runs, so it cannot unwedge this class of problem.

- **Optional, accelerates reclaim:** ask GitHub Support to run a GC on
  WeathermanAAA/Triple-A-Tropics ("we deleted a branch that accumulated
  ~85 GB of unreachable objects from daily force-pushes; please GC").
  Without it the size number still drops, just slower.
- **Decide on the 35 pre-rewrite branches** (list + tip SHAs:
  `git ls-remote --heads origin` snapshot in this commit's parent).
  Their features all shipped in main long ago; deleting them releases
  the last pre-rewrite anchors. The 97 MB pre-rewrite bundle backup
  (kept in the Codespace) covers recovery. One `git push origin
  --delete <name>` each when you bless the list.

## 2026-07-19 · Stale-branch sweep (Andrew's decisions executed)

- **Decision (1) DONE — 34 stale pre-rewrite branches deleted** (35th,
  `sat-explorer-fixes`, went earlier as provably merged). None were
  ahead:0 (all carried pre-rewrite history), so per the "or
  bundle-backed" clause every tip was fetched and bundled FIRST:
  `/workspaces/_backups/TAT-stale-branches-pre-delete-2026-07-19.bundle`
  (101 MB, 34 heads, `git bundle verify` OK, self-contained — includes
  the full pre-rewrite history those branches sat on).
- **FLAG: the July-9 pre-expunge bundle is GONE** —
  `/workspaces/_backups/TAT-main-pre-expunge.bundle` no longer exists
  (backup-experiments cleanup or Codespace rebuild; the /tmp copy was
  ephemeral). The new bundle above is now the ONLY pre-rewrite backup.
  **Recommend copying it off-Codespace** (it also still contains the
  expunged third-party names in old blobs — treat it like the old one).
  Local refs/backup/* in this Codespace's clone mirror it.
- Recovery: `git bundle unbundle` / fetch from the bundle, or push any
  ref back. Deleted refs (tip SHAs):
  - ace-core-0.7.1 e7f97635
  - ace-single-source 41e707dd
  - ascat-observations 1e9c98ad
  - cyclolab-design 0e3c2bbd
  - feat/mw-viewer-v2 b821a712
  - feat/truecolor-frontend 0bd482b4
  - fnv3-clusters 8f1b7b98
  - hafs-89pct 9d65aad5
  - hafs-89pct-polish 0888f971
  - hafs-emptied-cycle-fallback af2e357a
  - hafs-manifest-v2-cron 70390cbe
  - hafs-presentation-fixes b5353737
  - hafs-progressive-generator 901333de
  - hafs-render-package 45eb5586
  - hafs-sim-89h f16cf6e4
  - hafs-storm-anchored-stats 67dfac48
  - june-corridor 28d6cd7c
  - meso-sectors 6146e8bb
  - poller-framework 7d0f8e8e
  - recon-backfill-safe 14b29b75
  - recon-backfill-years-override b83bc81b
  - recon-filter-research 4a206e58
  - recon-fixes-and-archive 7ba2da2e
  - recon-nav-link 9ee7c678
  - recon-rain-suspect 3c7a33e1
  - recon-v2-selfcontained dfffdff0
  - recon-viewer-v1 ef520d13
  - recon-viewer-v2 a8aa3f38
  - recon-window-7 93fa860e
  - sat-one-loop be895fb4
  - sat-simple e2973f2f
  - sat-smooth 36f17b5d
  - tchp-records 6ed667fc
  - upstream-active-retirement 5d2e48ce
- **Decision (2) recorded — GitHub Support GC request SKIPPED** per
  Andrew: let background maintenance reclaim naturally; revisit only if
  a push actually fails on quota. This also completes expunge queue ②
  (the stale branches were the last pre-rewrite anchors on GitHub;
  orphaned SHAs remain fetchable until GitHub GCs them naturally).

---

# 2026-07-19 · /obs/ observations section + SAR winds (+ aircraft marker)

## Shipped

- **/obs/ hub** (Recon · Scatterometer · SAR cards; card grid leaves room
  for Microwave later). Recon page moved UNCHANGED to /obs/recon/, ASCAT
  viewer page to /obs/ascat/; old /recon/, /satellite/ascat/ and /ascat/
  are single-hop redirect stubs (canonical + og:url + hash-preserving).
  Site nav: Recon -> Obs everywhere; satellite subnav keeps a
  Scatterometer entry pointing across to /obs/ascat/.
- **SAR winds** (sarobs/ + generate_sar_winds.py + /obs/sar/): storm-tasked
  Level-2 SAR wind passes discovered per storm from the provider listing
  (page-parse), rendered on the provider's published m/s scale (sampled
  ramp, 0-51.44 m/s, calm-black + gale breaks), per-storm indexed archive
  on R2 under sar/. Box sar-poller (tat-overlays), 600 s ticks, R2-resident
  watermark, per-tick budget, dead-letter after 3 failures, antimeridian
  handling, per-constellation imagery credits (RCM / Sentinel-1).
- **Recon aircraft marker**: plane glyph at the newest ob oriented on the
  last track segment; click -> compact popover (aircraft identity from the
  flight id, mission/storm/window, current fix incl. FL wind, SFMR with
  suspect flag, static p + altitude, extrap sfc p, fresh VDM center MSLP).
  Upstream strings escaped (adversarial review caught the injection sink).

## Decisions taken (flag if you want them revisited)

- recon.js and ascat.js STAY at /recon/recon.js and /ascat/ascat.js — the
  CycloLab per-storm pages (tsr-built, on R2) lazy-load those URLs; only
  the PAGES moved. The ASCAT viewer itself is untouched (its storm index
  already lists storm-tagged passes newest-first, one entry per orbit).
- SAR scope: AL/EP/CP/WP storms. Southern-hemisphere storms and INVEST
  acquisitions (different upstream layout) are excluded for v1.
- SAR archive seeded with the 2026 season at deploy. 2025 backfill is one
  command when wanted:
  `docker exec tat-overlays-sar-poller-1 sh -c "cd /work/tat && python generate_sar_winds.py --store r2 --year 2025 --sweep --max-new 80"`
- Aircraft marker also shows on archived missions (at the final fix; the
  popover labels it "position at last ob"), not just live ones.

## Rollback

- Site move: revert the commit (pages + stubs are one atomic commit);
  nothing external depends on /obs/ yet.
- SAR: `docker compose -p tat-overlays -f docker-compose.overlays.yml rm -sf sar-poller`
  on the box stops the writer; SAR_ENABLED=0 in /root/tsr-s2/.env is the
  kill switch; the R2 sar/ prefix is additive and owned only by this
  poller (safe to delete wholesale to reset).
- Marker: revert the recon.js hunk + restamp obs/recon/index.html.

## 2026-07-19 · obs icons + SAR peak readout (verify-and-harden pass)

**Already present (verified, untouched):** the box sar-poller was ALREADY a
persistent supervised service (compose restart=always, while-true 600 s
loop, off-season no-op; the seed backfill was a separate one-shot exec) —
its own ticks were observed backfilling independently. The /obs/sar/ page
ALREADY had the content-gated 300 s manifest poll for the storm GRID.

**Newly added:** hub emoji -> three monochrome stroke SVG icons (plane /
sat+wind arcs / sat+radar beam; 34 px, stroke 1.5, low-opacity accent);
emoji sweep of the obs UI incl. ascat.js storm-select + canvas glyphs
(ascat.js -> v0018 on both including pages). SAR peak-wind readout:
despeckled_peak (3x3-mean max, fully-valid 3x3 + >=20/25 5x5 neighborhood
gate) on the render header as "~kt (m/s) near lat lon", subtle cross at
the peak cell, peak_ms/peak_kt in indexes + pass cards (kt primary);
--rerender flag; ARCHIVE RE-RENDERED on the box (126 passes, 15 storms,
2026 season) so old and new match. /obs/sar/ live: in-storm view now
refreshes in place when that storm's manifest entry moves (selection
kept), honest "Index updated ... newest pass ..." line, __sar debug hook.
Also fixed: pass cards' meta line was never attached (appendChild missing
since the original commit).

**Known limitation (decision):** the peak is labeled "instantaneous scene
peak" deliberately — in post-landfall coastal scenes the product's own
QC passes bay/estuary contamination that can carry the scene peak (seen
on the Arthur Gulf-coast scene; the printed peak COORDS make it
self-evident). A storm-centered peak (needs a best-track join) is the
possible v2 if you want it.

## 2026-07-20 · SAR peak: interior/edge-robust + small marker

Fixed the peak-wind readout latching onto swath-edge/coastal artifacts
(tester: ~69 kt on the Elida TOP edge). Peak is now interior open-water:
valid mask eroded inward ~10 cells (~5 km, O(N) summed-area table) — one
erosion covers swath-edge + coastal/bay + hole buffers; plus an incidence
gate (>47 deg dropped). Also fixed a latent nomask bug on all-water scenes
(getmaskarray). No qualifying interior cell -> "peak n/a" (frontend no
longer falls back to the raw single-pixel max). Marker: big "+" -> small
dark-haloed hollow ring. Archive re-rendered on the box (128 passes).
Verified live (Elida newest: 52 kt @ 24.1N, on the storm).
Rollback: revert render.py hunk + `--rerender --sweep` re-render; EDGE
knobs are EDGE_MARGIN_CELLS / INCID_MAX_DEG constants in render.py.

## 2026-07-20 · MJO subseasonal audit/fixes + recon export

**Part 1 (audit) — observed RMM CONFIRMED CORRECT, no fix.** Cross-checked
the ingest against BoM rmm.74toRealtime.txt (fetched with the generator's
UA): amplitudes match to 7 sig figs, the WH04 phase formula matches BoM
across 120 days (0 mismatches), dates align. 17 Jul = phase 8 / amp 2.070
as expected. GEFS forecast projection uses the canonical WH04 norms
(NORM_OLR/U850/U200, PC1/PC2_NORM) + seam-anchoring to BoM — methodology
sound.

**Part 1 honesty + Part 2 plume (e4a7624a).** Amplitude min/max envelope
-> member-amplitude percentile fan (10-90 + 25-75, median-member line);
the ensemble-mean VECTOR amplitude is now a thin dashed line, so a drooping
mean above a still-strong band reads as phase dispersion, labeled as such.

**Part 3 OLR forecast Hovmoller (92420cbc).** u/u850/v850/chi200 already
extended; OLR was analysis-only. Added GEFS ensemble-mean ULWRF fetch
(gefs_mean.fetch_olr_tail) + do_olr forecast extension: anomaly vs the same
CDR 3-harmonic climo, per-cell seam-anchored (removes GEFS-vs-CDR bias,
preserves the MJO pattern), honest ~4-day CDR-to-forecast gap (WK filter
bridges it), init divider + 'GEFS mean below' + 'bias-anchored' credit.
End-to-end verified locally (192 panels, fc_to 2026-08-05).

RENDER TIMING: the subseasonal products are R2-only, re-rendered by the
update-subseasonal cron (15:41 + 16:11 UTC). Both changes verified locally
(synthetic + real OLR render); the live PNGs refresh on the next cron. I
cannot force the dispatch (gh Actions token 403).

**Mid-turn recon export (03612e93).** /obs/recon/ gains Copy stats (text
summary), Copy data (TSV), Download CSV of the HDOB records (units header,
QC flag kept not dropped, VDM+sonde sections). reconobs now carries plane_z
(geopotential height) in the track JSON — decoded upstream but previously
dropped. Box recon-poller clone is at 03612e9; plane_z populates on each
mission's next republish (new obs / ~10-min heartbeat); older cached
missions export a blank height column honestly.

## 2026-07-20 · Tester-batch (areas shipped + large features deferred)

SHIPPED THIS PASS (per-area scoped commits, all screenshot/headless-verified):
- RECON (e6f09fb6): #5 FL→surface converter (Franklin et al. 2003 Table-2
  factors, level×region toggles, factor table w/ active-cell highlight,
  honest caveat + citation; center-no-eyewall reuses the non-convective
  outer factor w/ a stated caveat). #6 synced time-series hover crosshair +
  Z-time value tooltip (HTML overlay, snapped to nearest ob, touch-friendly,
  torn down in destroy()). #7 FL-wind labeling: barbs were COLORED by the
  10-s peak while length showed the average FL wind (colorbar said "Peak
  10-second Average") — color now uses the same average as the length;
  colorbar/prose relabeled; 10-s peak stays in the hover/popover.
- SUBSEASONAL Hovmoller (9990b4af): #1a CDR→forecast gap BRIDGED by per-lon
  interpolation (continuous field through the init line; == the #1d "2-panel
  compare / blank panel" report = the analysis+forecast split's blank gap).
  #1b all-basin genesis (|lat| 25→30N; Arthur/Bertha now mark). #1c label
  declutter (edge-aware anchor + greedy vertical stagger + leader lines).
  #1e high-contrast markers (bright dot + dark ring + dark halo, reads on
  tan AND teal). #2 amplitude percentile fan + #3 RMM audit shipped last
  turn and confirmed LIVE (cron re-rendered).
- ASCAT (fd0c6e76): #12 Global-view under-coverage — cap 40→90 so the whole
  ~60 h window (~68 orbits) draws (density control culls overlap).

DEFERRED — large multi-session features. Each has a concrete plan; none
started (better than half-shipping into a shared tree). Decisions-for-Andrew
flagged **[DECISION]**.

- **#4 AIFS + ECMWF-IFS + their ensembles for RMM + Hovmoller** (subseasonal,
  tester "top priority"). Plan: add per-model open-data fetchers beside
  subseasonal/gefs_mean.py (ECMWF open-data: IFS/AIFS oper + enfo on the
  0.25° open set — OLR is NOT in the IFS open-data param list, so RMM needs
  ttr/OLR proxy or a str-flux derivation; AIFS carries a limited field set —
  **[DECISION]** confirm AIFS exposes u200/u850 + an OLR-equivalent, else
  AIFS RMM is winds-only w/ caveat). Reuse rmm_wh04 projection unchanged
  (one series per model). Frontend: a model selector on the RMM phase/amp +
  Hovmoller. Effort: high (fetchers + a per-model tail cache + selector UI).
- **#8 SAR transect tool** (/obs/sar/): client-side draw-a-line → sample the
  rendered wind field along it → profile chart. Needs the per-pass wind GRID
  in the browser (today only the PNG + stats ship). Plan: emit a downsampled
  wind grid JSON per pass (sarobs) OR sample pixel-space against the known
  colorbar LUT. Effort: medium-high.
- **#9 SAR quadrant plots**: NE/SE/SW/NW-vs-center wind distributions. Needs
  the storm CENTER per pass (VDM/best-track join) + the wind grid (as #8).
  Effort: medium (depends on #8's grid emit).
- **#10 SAR salinity overlay**: SSS reliability cue (low-salinity/plume/rain
  degrade C-band). Source: an anonymous SSS product (SMAP/SMOS L3 via PODAAC
  or Copernicus) — **[DECISION]** confirm a no-cred SSS source; honest
  "reliability" shading, not a wind edit. Effort: high (new ingest).
- **#11 TLE overpass prediction (ASCAT+MW+SAR)** (tester "top priority"):
  pyorbital (already an image dep) + Celestrak TLEs (no cred), cached daily.
  Per-sensor swath widths → "next/most-recent pass over AOI/storm", with the
  SAR-is-tasking caveat. Plan: a small box/GH job writing overpass JSON per
  AOI + a compact schedule widget on the ascat/MW/SAR pages. Effort: high
  (orbit prop + per-sensor geometry + UI). Pairs with #13/#14.
- **#12 done** (above).
- **#13 ASCAT time-machine**: step to older passes. BLOCKED by retention —
  passes are pruned at 60 h. Needs a longer non-pruned archive (R2) + a
  time-step UI mirroring the imagery time-machine. Effort: high (retention
  change + UI). **[DECISION]** how far back to retain (cost vs. utility).
- **#14 other scatterometers (QuikScat/RapidScat archived)**: add as
  selectable sources. **[DECISION]** confirm a no-cred archived source
  (PODAAC L2B) + that this is an ARCHIVE (QuikScat ended 2009, RapidScat
  2016) — i.e. a historical browser, not live. Effort: high (per-source
  ingest + decode).
- **#15 Microwave /obs/ subpage**: mirror the recon/ascat/sar move pattern
  (the hub already reserves the "Microwave" card). New /obs/microwave/
  mounting the existing satellite/microwave viewer, redirect the old path,
  hub card + nav + subnav. Effort: medium — the cleanest next win; no new
  data, follows a proven pattern.
- **#16 Explorer 3D-IR view**: extrude the IR field as elevation (colder =
  taller) in the MapLibre cockpit, tiltable, as a settings toggle. Plan:
  MapLibre custom layer / raster-DEM-style height from the IR BT LUT while
  keeping time-lock/loop/product-picker. Effort: high (WebGL/MapLibre 3D).

Rollback: every item above is a single scoped commit; revert the SHA. The
subseasonal generator changes are R2-only (cron re-renders); ascat/recon
frontend changes are stamp-bumped.

## 2026-07-22 · TC History Records Phase 1 (SHADOW - engine + static suite)

Built the archive-stats foundation for the TC-history records feature, as a
live-but-unlinked shadow section (sat-explorer pattern: no nav links anywhere,
noindex meta, robots.txt Disallow /records/, "(shadow)" titles). NOT merged
into the climatology hub - waiting on Andrew's approval.

- Engine: tc_records/ package + generate_tc_records.py. HURDAT2 (NHC) is the
  AL/EP authority (full lifetimes incl. dateline crossers), IBTrACS v04r01
  the WP authority (JTWC columns via ace_core.WIND_PREFERENCE) and the
  current-season spine; live ATCF b-decks join via generate_ace_plot's
  fetch stack. Non-negotiables enforced: sums on 00/06/12/18Z synoptic
  tropical/subtropical >=34 kt fixes only; per-fix averaging provenance,
  never silently mixed (10-min /0.88 disclosed); SID identity with
  genesis-basin+season attribution (Ioke = EP-2006, full 85.3); missing
  pressure excluded from pressure boards. ~40 leaderboards + season pace
  matrices (leap-aligned 366-slot grids) + per-season gantt segments.
- Validation gate (hard-fails publish): Tip 870, Wilma 882 + rank-1 on
  6/12/24-h deepening (54/83/97 mb), Gilbert 888, Ivan ACE 70.38,
  Ioke 85.265, John 1994 30.0 d rank 1, AL-2005 28 named. All PASS; 2005
  per-storm ACE parity vs al_ace_data.json confirmed (site self-consistency).
- Workflow: update-tc-records.yml, daily 16:23Z + 16:53Z backup, R2-only
  (contents: read), gate runs before the sync so bad parses never publish.
  Output: records/v1/{basin}_{records,seasons}.json + global_records.json +
  meta.json on the media bucket (CDN). First data set seeded from the box
  (docker aws-cli + tsr-s2 R2 creds) so the shadow pages are reviewable now.
- Suite: /records/ hub + seasons/intensity/duration/timing/concurrency
  (the 5 record pages, ~40 tables off the JSON) + pace (count/ACE cumulative
  vs 1991-2020 climo bands + record traces + current season) + gantt
  (per-season SSHS-colored storm bars, contiguous 6-h class runs,
  150 units/month with horizontal scroll). House chrome tokens, ace_core
  SSHS palette, per-board definition/notes/caveats, basin chips (AL/EP/WP),
  provenance stamp, satellite-era caveats, cross-basin boards carry the
  1-min/10-min disclaimer. Tests: tests/test_tc_records_engine.py (11) +
  playwright screenshot harness (scratchpad) - 14 page/basin combos clean.
- Art calls made without sign-off (flag for eyeball): overview card emoji
  set, gantt peak-class chip + ACE readout per row, pace record-trace
  violet + amber current-season halo (ACE-template grammar), TD segments
  at 0.55 opacity. TD-only storms show HURDAT2 number-word names (TEN,
  NINETEEN) rather than "TD 10L" designation labels - divergence from the
  ACE-iframe gantt naming, revisit if it grates.
- Spec note: the referenced records spec file was NOT on this machine (or
  box/transcripts); authored /workspaces/TC-HISTORY-RECORDS-SPEC.md
  (OUT of repo per instruction) from the brief + domain records canon -
  review it.

Rollback: three scoped commits (engine+tests / workflow / shadow frontend);
revert the SHAs. R2 records/v1/ objects are additive-only.

## 2026-07-23 · TC records touch-ups + Phase 2 track explorer (SHADOW)

Phase-1 touch-ups (commit 1fdae5b3): ACE/PDI boards + overview methodology
now disclose the consistent subtropical-inclusion policy (AL 2005: 250.1
here vs 245.5 official, by design). Spec gap-check: wind-RI and track
distance already existed; added most-C5s-per-season, strongest-storm-per-
decade, consecutive-seasons-with-a-major streaks (AL top 1995 to 2012, 18),
and out-of-season storm count + ACE (al/ep official windows). New gate
sentinel: AL 2005 C5 count = 4. Landfall/size/costliest deferred per spec.

Phase 2 (commits e5b925e4 / fe761e5f / 7b1d5dd6): the interactive track
explorer, shadow at /records/explorer/ (linked only from the records
subnav; noindex; robots already disallow /records/).
- Pre-render model: tc_records/explorer.py emits per-basin catalogs
  (landfalls from HURDAT2 L rows, report links, record-board cross-links
  per SID), decade track bundles with per-storm unwrapped lons (dateline
  crossers = one line; Ioke/Genevieve/Paka verified), 1-deg density
  rasters, manifest -> explorer/v1/ on R2 via the same daily workflow run
  (one compute pass, gate before sync). 82 objects seeded from the box.
- Client: vendored MapLibre GL 5.24.0 on OUR CDN (vendor/maplibre-gl/),
  basemap from repo Natural Earth GeoJSON (no glyphs/labels/tiles).
  explorer-data.js (pure logic, 72+28 node assertions on real data),
  explorer-map.js, explorer-ui.js. Radius search via 7k-place NE
  gazetteer (explorer/v1/gazetteer.json, one-off - the workflow does NOT
  regenerate it). Overview mode (peak-colored lines) above 800 storms,
  segment-colored detail + hoverable fixes at or below it.
- Verified on real storms (screenshots in session scratchpad): Katrina
  card (902 mb / 150 kt / 20.0 ACE / 3 landfalls / TCR pdf), New Orleans
  150 km radius ranks Katrina correctly (Cindy 2005 is genuinely closer),
  Ioke deep-link (continuous dateline track + tops single-storm ACE and
  PDI boards), WP default viewport, CB palette, units, URL round-trips.
- Deferred (flag for Andrew): season-vs-season compare overlay (pace page
  covers vs-climatology; two-storm pinning shipped instead); WP landfall
  markers (JTWC decks rarely carry L rows); month-density overlays exist
  only for months with data (client hints when absent).

Rollback: revert the SHAs; explorer/v1/ + vendor/ on R2 are additive.

## 2026-07-23 · sat-explorer tester fixes: truecolor z7 + MP4 loop export

1) ZOOM RESOLUTION (box, tat-satellite-render branch box-ops, 9b66862 +
b86667a + the 14g bump): CONUS truecolor and the 0.5/1 km RGB recipe
classes now carry a native-z7 ceiling (registry _PX_BY_KM 0.5/1.0 -> 6144
px, recipe fetch_max_px 4800 -> 6144). The conus-fast lane emits truecolor
at z7 alongside c02 (which keeps its 10240 px override); cron lanes stay
capped at --max-zoom 5 so the geometry guard never trips there. First live
cycles: 485 tiles/frame maxzoom=7, manifest migrated off the old z5
geometry (lane runs --allow-geometry-change by design), 60-min backfill
regrew the loop. Verified: z7 Florida tile has 2.4x the edge energy of the
same-stamp z5 upscale; side-by-side in the session scratchpad
(tc_compare.png). Ops: one pre-existing-marginality c02 rc=137 at 12g ->
lane bumped to 14g. NOTE the repo hygiene mess found on the way: the box
tsr-s2 clone has ~55 commits not on origin/main (now pushed as box-ops)
and origin/main has 21 the box lacks; ALSO /workspaces/tsr-s2 in the
Codespace holds 5+ unpushed commits (slot-backfill work) on a diverged
base. Needs a dedicated reconciliation session - do not force-merge
casually.

2) LOOP EXPORT (TAT 06df3e3d): .webm testers could not open on iOS/Safari
(no webm MediaRecorder there; the old code threw uncaught). New
satellite/explorer/loop_export.js: WebCodecs VideoEncoder avc1 +
mp4-muxer 5.2.2 vendored at cdn vendor/mp4-muxer/ (faststart in-memory,
moov first). tiled_viewer gains exportLoop (frame-stepped: showFrame ->
tile settle -> cockpit composites chrome -> encode; no realtime
captureStream smear); both cockpit paths go MP4-first with the hardened
WebM recorder as no-WebCodecs fallback. Budget unchanged (9MB/HQ 24MB,
8% margin). Verified: headless Chromium encode (ftyp@4, moov<mdat, avc1),
box ffprobe (h264 yuv420p progressive), and a LIVE UI-driven export off
the deployed site (geo-global-ir_loop.mp4, 456KB, faststart). GIF option
deferred: needs heavy downscale + another vendored encoder to fit 10MB;
MP4 covers camera-roll saving. WebCodecs needs a secure context -
anything driving the exporter headless must serve via localhost/https.

## 2026-07-23 · tat-satellite-render reconciliation: READY, awaiting Andrew's go

reconcile branch (186f47f) = production superset, verified; NOT promoted.
Backups: tag prod-pre-reconcile (ed6fdca) + codespace-backfill-backup
(c5da203, already in prod as byte-identical 397d9fe) + box-ops intact.
Full conflict log + verification in the session report. PROMOTE STEP
(reconcile -> main, box repoint) waits for Andrew's explicit go.

## 2026-07-23 · tsr reconciliation PROMOTED (Andrew's go)

origin/main = 186f47f reconcile + runbook discipline (34d75eb). Box clone
repointed to main; tat-s2 + tat-render images rebuilt from main; all five
s2 lanes + render stack recreated and verified (lane emits, floater
mosaics ok on all three sats, guidance heartbeat 5/5 with the cyclolab
prefix pin live, /health 200). Rollback refs parked on origin: box-ops,
codespace-backfill-backup, tag prod-pre-reconcile. Discipline recorded in
RUNBOOK-RENDER/RUNBOOK-S2: box pulls from AND pushes to main only.

## 2026-07-24 · tsr TRUE-COLOR RING REBUILT (Satpy/pyspectral recipe) + GK-2A LIVE

truecolor.py rebuilt to the operational recipe, ONE shared pipeline across
ABI/AHI/AMI/FCI (constants verbatim from Satpy 0.60/pyspectral 0.14):
rayleigh_only+us-standard LUT with per-sensor SRF effective wavelengths +
red-keyed cloud relax + 70->95deg high-SZA taper; sunz 1/cos clamped 88deg
log-tapered to 0 at 95 (Li&Shibata pathlength on the NIR term); per-sensor
greens aimed at ~0.55um (ABI Bah 0.45/0.45/0.10, AHI+AMI HybridGreen F=0.15,
FCI NDVI-hybrid [0.15,0.05] s3); satpy SelfSharpened ratio sharpen;
cira_stretch as the ONE ring tone curve; IR cross-fade rides the same
88->95 taper; conservative water-gated glint tame. Retired: learned AHI
green + bump/floor/vibrance/land-relax/highlight-knee/warm-tint stack (all
compensations for marine-aerosol Rayleigh + the old LUT curve). Verified vs
SLIDER GeoColor same-scan (pink interior-West cured -> tan, matches SLIDER);
GOES-E/W overlap + GK-2A-vs-AHI same-slot overlap: neutral cloud tops within
~0.015-0.02, matched-class |d| ~0.02-0.04 -> ring Layer-A holds. tsr commits
4b1f366 (rebuild) / f2c7467 (FCI fetcher) / 05bef36 (GK-2A); image rebuilt,
all five lanes recreated, live conus-fast z7 truecolor emitting clean.

GK-2A AMI is LIVE: s2_gk2a.py (public noaa-gk2a-pds, in-file calibration,
J2000-NOON epoch gotcha, 1-based CGMS centers, stride reads keep 0.5km
VI006 at ~120MB), gk2a-fd suite (truecolor/ir/irbd) in the base rotation
(.env S2_CRON_SUITES) + first R2 emit verified on CDN
(shadow/sat/gk2a/fd/truecolor). Explorer wiring for the new sat is NOT done
yet (viewer picker/domain switch) — next session.

Phase 2 (gold standard) documented in truecolor.py docstring: per-sensor
SRF -> CIE XYZ -> sRGB 3x3 matrices (JMA TCR/CIRA), applied just before
cira_stretch, would make the ring identical BY CONSTRUCTION.

QUEUED manual steps (Andrew):
- ~~**FCI activation** creds~~ DONE 2026-07-24: keys landed on the box .env,
  v1 token flow verified live, full activation built (see the 2026-07-24
  FCI section below). ONE manual step remains — the licence click below.

## 2026-07-24 (later) · MTG FCI ACTIVATED end-to-end (licence click pending)

EUMETSAT creds are on the box `.env` (gitignored; never committed). Token
mint verified from the box: HTTP 200 at POST api.eumetsat.int/token.

**Auth-migration check (Andrew asked)**: the api-key page banner's "new
authentication method" = the v2 Data Access Services flow (OAuth2 auth-code
+ PKCE at user.eumetsat.int/cas, live 2026-06-30). It needs an INTERACTIVE
browser login to bootstrap a 30-day refresh-token chain — wrong shape for a
headless box. Our POST /token client-credentials flow is the documented v1
method, still fully supported ("will be depreciated in due time", NO date),
and is exactly what eumdac 3.1.1 (latest, 2025-12) ships. Verdict: we are
on the CURRENT supported method; stay on v1 until eumdac gains v2 or a
deadline appears. Finding + v2 delta documented in s2_meteosat.py.

**tsr build (fci-onboard -> main)**: mtgi1-fd suite (truecolor/ir/irbd) on
the GK-2A model — FCI_RECIPES + MTGI1_FD_ROWS + produce_fci_truecolor
through the SHARED assemble_truecolor (sensor="fci", NDVI-hybrid green
[0.15,0.05] s3, zero FCI-specific compensation); never-miss chunked fetch
(Range-resume download, 41-chunk completeness gate — a partial slot NEVER
renders; newest_fci_slot pin + 10-min slot tolerance; emit backfill grid =
the watermark/idempotent-backfill layer); products.json now SKIPPED on
total-failure passes (a gated suite can no longer advertise empty
products); satpy layer in Dockerfile.meso; dedicated mtg lane
(docker-compose.s2.mtg-lane.yml: 600s interval, 150-min backfill for the
1-h licence delay, 12g); validate_fci_seam.py (FCI-vs-GOES-East Atlantic
overlap co-registration, rebuild tolerances 0.02/0.04) ready to run at
first data. Explorer: Meteosat-12 · 0° picker row + mtgi1-fd domain +
NADIR auto-switch at 0°, availability-gated off sat/mtgi1/fd/products.json
(verified headless: row stays greyed pre-licence, GK-2A row un-greys and
switches with live frames).

**Licence state (verified empirically)**: OpenSearch works; downloads 403:
`"GeneralLicense required to access this collection"` — same wall on FCI
(EO:EUM:DAT:0662) AND both SEVIRI collections, so ONE acceptance unlocks
all three. No API surface for acceptance (probed; 404s) — it is a real
portal click.

- **LICENCE CLICK (Andrew, the one remaining manual step)**: log in at
  user.eumetsat.int with the account that owns this consumer key →
  accept the EUMETSAT **General Licence** (Data Store licensing /
  data-registration page; the FAQ says acceptance can take up to 1 h to
  activate — log out/in). Everything downstream is already running and
  self-heals on the first tick after activation: mtg lane starts emitting,
  explorer row un-greys, geo-global's SEVIRI wedge members light up.
  After activation the agent still owes: run validate_fci_seam.py + the
  live screenshot pass (FCI TC + IR + ring).

**SEVIRI IODC assessment (Andrew asked; report-only, not built)**: WORTH
ADDING as a BT-only cockpit satellite. The N Indian Ocean is a real gap:
Arabian Sea sits at 60-75° zenith from MTG-0° and ~65-80° from Himawari;
Bay of Bengal is past MTG's usable limb — IODC at 45.5°E sees both at
15-50°. The geo-global BT ring's IODC member is ALREADY COMMITTED (dormant
kind="seviri" row) and lights up with the same licence click — zero build.
The remaining build for a picker satellite (msg-iodc-fd: ir/irbd/wv on the
gk2a row model, reusing fetch_seviri_disk + the existing honest-degrade
plumbing) is one focused session; ops cost ~270 MB/15-min slot on the
already-verified key/licence. SEVIRI never joins the true-color ring (no
blue/green band) — "natural color" only, per standing policy.
