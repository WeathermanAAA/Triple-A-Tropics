# SATELLITE-REVIEW.md — Stage-0 Adversarial Review of the Satellite Backend Re-Architecture

> Companion to **`SATELLITE-REARCH.md` (r2)**. Reviews the r1 design; its surviving findings are folded into r2. Stage-0 only — a hard sign-off gate; nothing is built or deployed until Andrew signs off.

**Date:** 2026-06-18 · **Status:** complete · **Scope:** the full `SATELLITE-REARCH.md` design, every section §0–§13.

---

## Executive summary

`SATELLITE-REARCH.md` is a mature, unusually self-aware design — §11 already lists eight real open problems (A–H) and the §0/§2 "by construction" framing is the right spine. **The value of this review is the deltas it surfaced *beyond* §11**, not a re-litigation of the doc. Forty-six findings were generated across six adversarial dimensions plus a completeness critic; each went through a refutation-minded skeptic. **Two were fully refuted** (one rested on a fabricated doc claim + an inverted external fact; one on an empirically-false grouping-key assumption). **Forty-four survived as confirmed or partial** — but the skeptic pass dissolved most of the headline "critical" framings: after refutation the genuinely **major** residue is a focused set, and the rest are surgical clarifications.

The major survivors cluster in five places the doc does not yet cover: **(1)** the `SNS` subscription filter as written matches nothing (NOAA puts the object key in the message *body*, not attributes — default `FilterPolicyScope=MessageAttributes` silently drops every event); **(2)** the never-miss ledger/audit is keyed on *raw* NOAA slots while the products users see are *derived per-storm crops*, so a clean audit can be false-green for a missing floater frame; **(3)** the full-disk pyramid's **render-compute and wall-clock** (not just its PUT cost or per-tile RAM) are unbudgeted on a single shared box, and a **`EPSG:3857` reprojection stage** is required by MapLibre but appears nowhere in the architecture; **(4)** the cutover plan has no "old writer stops at flip" step, changes the prod key *namespace and manifest schema* (so the "one-line, no-redeploy" revert is false for the viewer), and never sequences the viewer's schema migration; **(5)** whole missing dimensions — **observability/alerting**, **IAM/secret blast-radius**, **SPOF/host-loss**, **burst backpressure**, and a **logic test plan** that pixel-diff structurally cannot provide.

None of these invalidate an architectural decision. Every one is fixable with design-text additions (new §11 flags, a §3.5/§3.6/§5.x subsection, tier-splitting one gate, sequencing one migration step). The architecture — frozen renderer, event-driven never-miss, manifest SSOT, shadow-first pixel-diff cutover — is sound and should proceed.

## Gate verdict

**PROCEED WITH REQUIRED CHANGES.** The design is approved in principle; the r2 revision must fold the major-severity deltas below before any build (S1) begins. Reasoning: the architecture is correct and the "by construction" spine holds, but five concrete gaps would let a build start on false premises — most acutely INGEST-1 (the primary trigger is unwired by default and would silently fall back to the poll, falsely green) and INGEST-3 (the never-miss audit measures the wrong layer). These are spec defects, not design errors: each is closed by text, and r2 + S1 acceptance checks resolve them before code. The doc's own §11 already concedes the right things; the gate condition is simply that r2 captures the deltas this review found on top of §11.

---

## Methodology

Six adversarial dimensions plus a completeness critic, one refutation-minded skeptic per finding:

1. **Zero-visual-change "by construction" guarantee** (zvc) — does pinning + frozen renderer actually yield identity?
2. **Never-miss event-driven ingest correctness** (ingest) — SNS/SQS semantics, completeness gate, backfill.
3. **R2 Class-A-PUT cost model** (r2cost) — is the cost story quantified and honest?
4. **Crop-before-composite RAM trap + tiled full-disk feasibility** (ram) — RAM, reproject, wall-clock, OOM.
5. **MapLibre tiled-animation approach** (maplibre) — engine choice, preload, memory, idioms.
6. **Cutover / rollback safety** (cutover) — dual-writer, namespace, schema, rollback recoverability.
7. **Completeness critic** — high-risk dimensions the six axes might miss (SPOF, observability, IAM, DR, backpressure, testing, process).

Every finding was put through a skeptic instructed to refute it on mechanism *and* premise, to check it against the doc's actual text and §11 flags, to verify external facts (AWS SNS/SQS/R2 semantics, MapLibre APIs, satpy/libwebp behavior), and to downgrade or kill anything overstated. Only **confirmed** and **partial** findings became r2 edits; **refuted / already-addressed** findings appear below in the dismissal table so coverage is auditable.

**Counts:** 46 findings total → **2 refuted**, **44 confirmed/partial**. Of the 44 survivors, by final (post-refutation) severity: **0 critical** (every "critical" claim was downgraded), **16 major**, **~15 minor**, **~13 nit**. Three findings were *upgraded in confidence* to confirmed (INGEST-3, RAM-3, several CUTOVER/COMPLETENESS) where refutation strengthened the kernel.

---

## Confirmed & partial findings, by dimension

Dimensions ordered by max surviving severity. Within each, major first. Each finding gives the surviving kernel, *why it survived refutation* (the skeptic's decisive point), and the concrete r2 fix.

### Dimension: Never-miss event-driven ingest correctness — max severity MAJOR

**INGEST-1 — SNS filter policy as written matches NOTHING (default scope is MessageAttributes; NOAA puts the key in the body).** *[major]*
NOAA's `NewGOES19/18Object` / `NewHimawariNineObject` deliver S3-event payloads with the object key in `Records[].s3.object.key` — no usable message attributes. SNS subscription filter policies default to `FilterPolicyScope=MessageAttributes`; an attribute-scoped policy on an attribute-less message accepts nothing.
*Survived because:* all three external facts verified (default scope, body-only key, attribute-scope-matches-nothing), and the doc names SQS the **primary** never-miss trigger and leans on the filter for SQS volume/cost — so this is load-bearing, not notation. Downgraded from critical to **major** only because §3.3 layer-2 backfill would silently mask it (system still renders via the poll, primary path dead, metric falsely green) — silent loss of the low-latency primary, not missed frames.
*Fix:* §3.1 must require `FilterPolicyScope=MessageBody` with a body-path policy, e.g. `{"Records":{"s3":{"object":{"key":[{"prefix":"ABI-L2-CMIPC/"}]}}}}`, and add an S1 metric check (`NumberOfMessagesReceived` non-zero, `NumberOfNotificationsFilteredOut-*` ~0) so a silent filter no-op cannot pass as green.

**INGEST-3 — the completeness gate guarantees never-miss of RAW slots, but the product is a DERIVED per-storm crop with no join and no trigger.** *[major, confirmed]*
§3.2/§3.3 define completeness/watermark/ledger/backfill over raw NOAA objects per `(product, sat, channel/segment, s-slot)`. Floaters/meso are crops on an extrapolated center driven by the storm feed (§5.4) — a raw slot can be "complete" yet produce no crop, and the set of frames that *must* exist is storm-driven with no triggering raw event. The S1 audit (§8) compares against raw `ListObjectsV2`, so it never catches a missing derived crop.
*Survived because:* the render key in §3.3 literally omits the storm slug today's path carries; §3 ingest is 100% NOAA-raw-object SNS (the storm feed is never an ingest trigger); and S3 floaters get only a pixel-*identity* gate, never a frame-*coverage* gate. The skeptic granted that a render job re-reading the live storm list bounds steady-state misses to ~one slot — so the durable defect is the missing coverage ledger/audit, not guaranteed loss (kept at major, not critical).
*Fix:* add a derived-product ledger keyed on `(storm/sector slug, band, s-slot)`; make the storm feed an explicit ingest *trigger* (re-fire the latest complete raw slot when a storm enters coverage / its bbox shifts); define the gate as "for each active storm/sector at slot t, a frame exists OR is logged no-data/off-sat"; make the S1/S3 audit compare published *frames* against the expected `(storm×band×slot)` set, not raw objects against `ListObjectsV2`.

**INGEST-5 — DLQ relies on an undefined "slow alarm"; the metrics that make never-miss auditable are unspecified.** *[minor]*
§3.4's "a slow alarm drains/inspects it" is the only alerting in the doc; §3.3 promises "auditable, never false-green" via `log()`.
*Survived because:* `log()` is forensics, not active detection, and §11 has no monitoring flag. Downgraded to **minor** because the §3.3 backfill reconcile already protects correctness independently of the SQS/filter plane — the failure mode is "degraded-but-green on the fallback path," not data loss.
*Fix:* replace "a slow alarm" with named CloudWatch alarms (DLQ `ApproximateNumberOfMessagesVisible` > 0; main-queue `ApproximateAgeOfOldestMessage` > visibility-timeout×2; `NumberOfNotificationsFilteredOut-InvalidMessageBody` > 0 and sustained `-MessageBody` spike) + an owner. Folds into the COMPLETENESS-2 observability subsection; cross-reference INGEST-1/3 as the modes these make visible.

**INGEST-2 — filter values use glob/brace shorthand SNS does not support.** *[nit]*
`ABI-L2-CMIPC/F/M*, MCMIP*` is not valid SNS policy syntax (prefix matching is `[{"prefix":"…"}]` arrays; no globs, no `C/F/M` alternation).
*Survived because:* the external syntax facts are correct and the constraint limits (≤5 keys/policy, ≤150 value-combinations, 256 KB) are real.
*Downgraded to nit* because the doc presents these as diagram shorthand, and the finding's "couples the completeness gate to the filter pattern" sub-claim is refuted by §3.1 line 82 (the gate keys off the *parsed object key*, independent of the delivery filter).
*Fix:* render the policy as an explicit prefix-array in §3.1 with a one-line note on SNS syntax + policy limits, so an implementer doesn't mis-translate the shorthand.

**INGEST-6 — backfill `ListObjectsV2` reconcile is unbounded in the doc.** *[nit]*
§3.3 layer-2 lists "the recent prefix" without stating a bound; §4.2 names "Class-A PUT (and LIST)" as the cost driver.
*Downgraded to nit* because the headline "internal contradiction" conflates a *free* NOAA-source anon LIST with R2 Class-A LIST (verified: NODD anon LIST is free), and the bound is already implied (hour-level key layout + "recent prefix" + watermark = ~one page).
*Fix:* one optional clarifying clause in §3.3/§10 making the implied bound explicit (current+previous hour prefix, single page, no-op cost on NODD); drop the "contradiction" framing.

---

### Dimension: Cutover / rollback safety — max severity MAJOR

**CUTOVER-1 — no "old writer STOPS at flip" step → dual-writer prune conflict on the shared prod frame namespace during soak.** *[major, confirmed]*
§7.1 keeps the old Railway poller "running untouched," §7.4 flips reads/writes to prod per product, §7.5 decommissions only after a soak — with no step that stops the old writer for a just-flipped product.
*Survived because:* on flip, S3 sends new floaters to the *same* prod prefix the old poller writes (§1 `floaters/{slug}/{band}/*.webp`), and the app-side prune (§1/§4.3) runs from both writers with divergent liveness views — deletion is **not** idempotent across two writers, so the old poller can prune/thin frames the new `latest_times.json` still references → live 404s. The skeptic refined the mechanism: not a pointer clobber (old writes `manifest.json`, new writes `latest_times.json` — different files) but a **prune/liveness conflict on the shared frame prefix** (kept at major).
*Fix:* §7.4 adds a per-product writer hand-off: at flip, the old poller stops writing AND pruning that product's prod keys (scoped kill-switch, confirmed before the new writer is authoritative). State that soak runs with the old poller read-only/idle for flipped products. Note that frame-byte idempotency (§3.3) does not cover prune — one prune owner per product at any moment.

**CUTOVER-2 — prod key NAMESPACE changes (`floaters/…` → `sat/…`), so the "one-line, no-redeploy" flip is false for the viewer.** *[major]*
Today's frames live under `floaters/{slug}/{band}/{t}.webp` + `floaters/{slug}/manifest.json`; the new keys are `sat/goes19/meso2/ir/{t}.webp` + `latest_times.json`. The serve path is the static viewer fetching directly from R2 (no read proxy in `app.py`), so the read prefix is baked into the deployed HTML/JS.
*Survived because:* the `WRITE_LIVE_FEEDS` precedent the doc cites has a **fixed** live key (verified in `update-ace.yml`) — it only changes *who writes*, never the path read. Here both the namespace and the manifest schema change, so "mirror `WRITE_LIVE_FEEDS` → no redeploy" does not transfer to the read side. Major because the rollback *mechanism* still exists (redeploy), so it is a migration-doc-accuracy defect, not loss of rollback.
*Fix:* §7.4 splits the flag into a WRITER flip (per-product, env-flag, no redeploy) and a READER cutover (one coordinated all-products viewer deploy; rollback = redeploy). Correct the "no redeploy / one-line revert" claim to apply to the writer only. Optionally dual-write `latest_times.json` + legacy `manifest.json` during soak so old/new viewers coexist.

**CUTOVER-3 — manifest schema + filename change (`manifest.json` → `latest_times.json`) creates a viewer read/format skew the plan never sequences.** *[major, confirmed]*
§4.1 supersedes the `{t,key}` list with a path-template + times schema; one deployed viewer must serve flipped products (new schema) and un-flipped products (old schema) during the partial-flip window.
*Survived because:* §4.1's example `sat/goes19/meso2/ir/latest_times.json` is explicitly the floater/meso product — so the "dumb player UNCHANGED" language in §6.1/S3/§9 is in direct contradiction with §4.1 (the player cannot be byte-unchanged AND parse a superseded schema). The §8 S3 gate checks frame pixel-identity only (§7.2 is "pixel/ink, never DOM/metadata"), so a manifest-parser skew slips the gate entirely — that blind spot is the teeth. None of §11 A–H cover schema-migration sequencing.
*Fix:* state that "dumb player UNCHANGED" = playback mechanics only; the manifest-parsing layer migrates. Pick one: dual-write both schemas during soak, OR ship a dual-schema-tolerant viewer before the first flip. Add an S3 gate item that the viewer renders correctly against a *mixed-schema* prod.

**CUTOVER-5 — §9's "0 / never widen" contradicts §7.2's 0.1% AA-filtered budget + SSIM fallback on the by-construction products.** *[minor]*
§9 calls any non-zero diff "a real bug … not a tolerance to widen" and the gate column says "= 0 over budget"; §7.2 defines a 0.1% AA-filtered budget with an SSIM fallback, applied uniformly.
*Survived because:* the doc specifies one gate and applies it to two incompatible promises; the AA-filter actively excludes antialiased pixels, so a freetype/libwebp/host-move sub-pixel shift passes silently as green on the tier whose whole claim is byte-identity. (See also ZVC-2, ZVC-5 — same kernel from the zvc axis.)
*Downgraded to minor* (from the finding's major) because the doc's *intent* (§9 prose) is correct, libwebp is pinned (making true-zero achievable), and the fix is a localized spec-tightening.
*Fix:* in §7.2/§9, tier the gate — strict-identity (sha256 / threshold-0, AA-detection off) for by-construction products; the 0.1% AA + SSIM budget only for the reference-render-gated tiled map. Reword §9's ambiguous "= 0 over budget" to "zero frames over budget." Add an S1 prerequisite that the OVH box reproduces the Railway box within the budget on a frozen input set before the shadow diff is trusted.

**CUTOVER-4 — rollback not provably clean: lifecycle TTL + app-side prune can delete the old-pipeline state "flip back" depends on.** *[minor]*
§7.4 promises a clean revert; §4.3 runs R2 lifecycle TTL (7–14 d) + app-side prune on the prod prefix with no carve-out for soaking products.
*Downgraded to minor* because the §7.1/§7.5 ordering keeps the old poller writing the prod prefix through the soak (the recovery substrate), so TTL/prune are harmless *if* that's stated — but the doc never makes the dual-writer coexistence or the rollback recovery target explicit, so a reader of §4.3 in isolation reasonably worries.
*Fix:* add a rollback-recovery model to §7.4 (old poller writes prod untouched through soak; TTL/prune safe-by-virtue-of-or-suspended — pick one; rollback serves the old-poller-kept-fresh prefix). Add a rollback-drill to the §8 soak gate: after a worst-case TTL/prune interval, flip back and assert zero 404 frames.

**CUTOVER-6 — "decommission only after a defined soak" — "defined" is undefined (no duration, no per-product green bar, no rollback deadline).** *[minor, confirmed]*
§7.5 gates the most dangerous step (removing the rollback fallback) on "a defined soak" with no definition.
*Survived because:* the §8 S1 row *is* concrete ("zero missed slots vs an independent ListObjectsV2 ground-truth"), so the doc can be concrete here too; §11 has no soak flag. Minor because the rollback *mechanism* is designed — only its exit criteria are missing.
*Fix:* define the soak in §7.5: ≥14 days AND ≥1 active-storm cycle per flipped product, §8 gate green throughout (zero missed slots, zero over-budget frames, zero DLQ-stuck slots); old poller stays deployed as rollback target for the full soak; decommission requires explicit sign-off mirroring the Stage-0 gate.

---

### Dimension: Crop-before-composite RAM trap + tiled full-disk feasibility — max severity MAJOR

**RAM-3 — Web-Mercator reprojection is mandatory for MapLibre raster tiles, is a NEW step outside the frozen renderer, and is unbudgeted/unplaced.** *[major, confirmed]*
MapLibre raster XYZ tiles are EPSG:3857 (verified); `render.py` emits PlateCarree from geos source (§1). Nothing in §5/§6 says where the geos/PlateCarree → 3857 reprojection happens.
*Survived because:* it cannot live in the frozen unit (which emits PlateCarree), so either `render.py` is edited (breaks "frozen") or a new reproject stage is inserted that the §2 diagram and §8 staging do not show; §6.3/§11-B concede only the *chrome* exception, so this is a genuinely SECOND renderer-scope exception. The reproject resample's RAM/CPU is unbudgeted, and Web-Mercator clips beyond ±85.05° (a real FD framing delta). Major not critical because it endangers only the new additive tiled map, not the by-construction invariant.
*Fix:* add a §5.x "Reprojection to EPSG:3857" stage (name GDAL warp / pyresample vs per-tile cartopy-in-Mercator; state resample method; fold its halo into §5.2); place it in the §2 diagram + §8 staging; declare it a second sanctioned renderer-scope exception in §6.3/§11-B; add its RAM/CPU to §10; document the ±85.05° poleward clip as a known gated delta.

**RAM-1 — satpy #1902 is a dask-chunking blowup, not array-size-proportional, so the per-tile RAM envelope is not a guaranteed mosaic bound.** *[major]*
§5.2 cites #1902's >24 GB and infers crop → bounded RSS, assuming RAM scales with composited array size.
*Survived because:* verified that #1902's author calls 24 GB "considerably more than necessary / Dask … not working correctly," and the satpy FAQ attributes peak memory to (workers×chunk-size) + Rayleigh intermediates + GDAL cache — a per-invocation floor cropping doesn't divide away. §5.2 never mentions tile *concurrency* and sets no go/no-go peak-RSS number; the heartbeat assertion is post-commit telemetry, not an S1 feasibility gate. Partial/major (not critical) because input cropping genuinely does reduce loaded arrays — the gap is the missing concurrency model + acceptance number, not that proportionality is fiction.
*Fix:* §5.2 keeps crop-before-composite but adds (1) a measured per-tile peak-RSS *floor* in S1 (incl. pyspectral LUT, Rayleigh intermediates, dask/GDAL overhead); (2) max safe tile-concurrency = (box_RAM − ingest_reserve) / floor; (3) a Stage-0/S1 acceptance number ("one true-color FD tile under X GB or the mosaic is not viable on 16 GB"). Pin `DASK_NUM_WORKERS` / chunk size in §7 so the floor is reproducible.

**RAM-5 — no hard memory ceiling; "peak RSS in the heartbeat" is observe-only — a runaway composite can OOM-kill the ingest co-tenant.** *[major]*
§5.2 "bounded … in the heartbeat" + §5.1 `Restart=always` are post-hoc/restart-after-death, not prevention. The Linux OOM-killer's victim is heuristic; on one 8–16 GB box also running the floater poller + meso lanes, a render OOM can kill ingest — the never-miss spine.
*Survived because:* crop-before-composite is a real *design-level* bound (so "none at runtime" is overstated → partial), but it's conditional on a correctly-sized per-tile envelope for an unbuilt path with no stated floor (RAM-1), nothing sits between "RSS grew" and the kernel OOM-killer, and the repo's own HAFS telemetry (peak ~23 GB; one VPS insufficient; render split from pollers) is a directly analogous workload the team already rejected co-locating. The systemd `MemoryMax`/`OOMScoreAdjust` fix is verified standard.
*Fix:* add a hard cgroup-v2 `MemoryMax`/`MemoryHigh` on the render unit (reserve a slice for ingest), `OOMScoreAdjust` to make the renderer the preferred victim, `MemoryLow`/negative score on the ingest units; reconcile §5.1's single-box decision with the HAFS precedent; gate on S1's measured floor (RAM-1) + reproject (RAM-3) — split FD off if it won't fit with headroom. New §11 flag.

**RAM-6 — wall-clock for the FD pyramid is unbudgeted: hundreds of per-sub-sector composites + reproject + tile-cut, every 10 min, sharing the box with the 60 s floater/meso loops.** *[minor]*
§10 sizes RAM and PUTs but no wall-clock; §11-F covers only visibility-timeout-vs-single-render.
*Survived because:* §5.2's own RAM fix ("mosaic of cropped sub-sectors") IS many composites at "tens of seconds" each, so the multiplier is doc-confirmed; the S2 gate has no "completes within cadence" or "doesn't degrade the 60 s loop" criterion despite §5.1 consolidating everything on one box. Downgraded to **minor** because §10 already names the tiled FD "the risk," bounds it (z0–4, render-on-change, TTL), keeps it off the by-construction path, and lists the descope levers — the fix is "tighten the gate," not "the design is wrong."
*Fix:* add a throughput/contention acceptance criterion to S2 (one FD cycle < cadence with headroom; concurrent floater 60 s loop undegraded) + a CPU/concurrency budget line to §10 + a §11 flag citing the HAFS wall-clock precedent and the descope levers.

**RAM-2 — no overlap halo on the cropped tiles; the final resample is a neighbourhood op → tile-edge seams.** *[minor]*
§5.2 assembles "cropped sub-sectors … then assembled" with no halo.
*Downgraded from critical to minor* because the dominant true-color steps (Rayleigh, sun-norm, tone curve) are per-pixel and tiling-invariant; only the final geos→display/Mercator resample is a neighbourhood op needing a few *source* pixels of halo (not the finding's 16–32 px output halo, which overstated RAM by ~10×); and the §9 reference-render gate would catch a seam before cutover.
*Fix:* one §5.2 sentence — crop each sub-sector with a small *source-pixel* halo sized to the resample kernel, reproject, trim before assembly; add a §11 flag and make the §9/§6.3 reference-render gate explicitly diff seam lines against a single-pass crop.

**RAM-4 — the §5.2 native-space "cropped sub-sectors" and the §4.2 Web-Mercator "341-tile z0–4 pyramid" are two different tilings with no bridging stage named.** *[minor]*
*Downgraded to minor* because §4.2 cites 341 explicitly as the *rejected* naive worst case (not a budget), and the PUT number is explicitly deferred to S1 — so "all three numbers untrustworthy / 341 ungrounded" is overstated. The real kernel: the geos→3857 reproject + XYZ re-cut stage (where RAM-3's cost lands and which sets the true non-empty tile count) is never named.
*Fix:* one §5.2 sub-step naming the pipeline (render N native sub-sectors with halos → composite → assemble native FD raster → reproject to 3857 → cut z0–4 XYZ → dedup-PUT non-empty/changed) + a sentence that 341 is the *global*-grid upper bound and the disk occupies only a subset (S1-measured count feeds the real PUT budget).

---

### Dimension: Completeness critic — max severity MAJOR

**COMPLETENESS-2 — no observability/alerting stack; a silent stall has no defined detector beyond "a slow alarm."** *[major]*
The only alerting in the doc is §3.4's "slow alarm"; §3.3's heartbeat is asserted but nothing consumes it.
*Survived because:* every signal the doc defines (`log()`, heartbeat, `as_of`) is a *producer*; there is no *consumer* — no scrape target, threshold, paging, or staleness detector — so "auditable, never false-green" is undercut on the consumer side (a log on an unwatched box is exactly false-green). The named event-driven silent-stall modes (mis-configured SQS delivering nothing, dead backfill unit beside a live SQS unit, disk-full PUTs after dedup says "changed") are real and specific to the poll→event cutover. Downgraded to **major** because the producer primitives already exist to build on and the fix is one subsection.
*Fix:* add §3.5 "Observability & alerting" (and a §11 flag): the cheapest detector is `latest_times.json` `as_of` staleness (page when older than 2× cadence — no new infra); plus watermark-lag, DLQ-depth, heartbeat-absence, PUT-error/disk-free alarms + an owner. Close the producer→consumer loop.

**COMPLETENESS-6 — no backpressure/burst model: synchronized FD+CONUS+meso+true-color publishes can outrun one box; SQS depth then silently lags the live loop.** *[major, confirmed]*
GOES Mode-6 bursts are real and periodic (verified: FD/10 min + CONUS/5 min + two mesos/60 s coincide, each ×16 bands); true-color renders take "tens of seconds"; the box is bounded by `Semaphore(2)`.
*Survived because:* §10 ("capacity") sizes RAM + PUT only, never throughput-vs-arrival-rate; §11-F is per-message redelivery waste, not aggregate throughput; per-queue isolation is between *sources*, not priority between *products* (a 10-min FD mosaic can head-of-line-block the 60 s hot loop in the same queue, and `Semaphore(2)` is priority-blind); a backed-up queue surfaces only as a stale timestamp (no queue-depth metric). For a fixed VPS replacing Railway's autoscaling, "can one box hold cadence at peak season" is a first-order omitted question.
*Fix:* add a throughput/backpressure subsection (or §11 flag): a measured throughput budget (render-s per product family × peak burst arrival vs cores; yes/no on one box at peak); a priority scheme (hot meso/ir preempts the FD pyramid — separate hot/cold pools per the `webp-frames-meso` precedent); a shed/age-out policy; queue-depth + `latest_times` age into the heartbeat with alerts (ties to COMPLETENESS-2). Note backfill deepens the queue under overload, so gate backfill behind the hot path.

**COMPLETENESS-7 — testing is pixel-diff only; the new logic pixel-diff cannot exercise (gate, ledger, backfill, idempotency, DLQ, cold-start) has no test plan.** *[major, confirmed]*
Every §8/§9 gate is a pixel/reference diff; the genuinely new correctness logic is the part most likely to have bugs and is exactly what pixel-diff cannot test (dropped slots look identical to present ones).
*Survived because:* the S1 `ListObjectsV2` audit is a live prod-shadow *smoke* test — it can't deterministically force a dropped/duplicate SQS message, trip DLQ-after-5, feed a malformed partial-band slot, or replay cold-start; the repo *does* unit-test exactly this class (parity tests, `python -m unittest discover tests`, the meso moto harness) and the enscenters `reconcile()` stale-entry (@165fc05) and clobber (@da9be6b) bugs are direct precedent that this logic ships with bugs. §11 has no testing flag.
*Fix:* add a §9.x "Logic testing (beyond pixels)" obligation + a §11 flag — unit/integration tests (moto/localstack for SNS/SQS, moto/minio for R2) for the completeness-gate truth table, ledger accounting, backfill reconcile (inject a dropped event → assert enqueue; inject a duplicate → assert no-op PUT), idempotent-key derivation, DLQ-after-N, cold-start bootstrap — a per-stage gate alongside the pixel diff, not a substitute.

**COMPLETENESS-1 — single VPS is an unaddressed SPOF for ALL satellite imagery; `Restart=always` restarts a process, not a dead host.** *[major]*
§5.1/§10 offer only `Restart=always`; Railway provided managed host-level migration (verified) which the move drops, with nothing equivalent.
*Survived as the host-loss half* (the RAM-capacity sub-argument was refuted — the cited HAFS telemetry actually shows the satellite-class box fits 8–16 GB with headroom). §11 A–H never mention SPOF/failure-domain. Downgraded to **major** because this is supplementary static-site imagery (not SLA-bound), the renderer/feeds are independent, and today's Railway is itself single-replica (the delta is "lose managed migration," not "remove redundancy").
*Fix:* add a §5.x / §11-flag "Failure domain & host-loss" naming the blast radius ("one box hosts SQS consumer + render + floater + meso + FD mosaic; if it dies all sat imagery goes stale until recovery") + a recovery story — a cold-rebuild runbook (the SQS queues + R2 frames survive the box, so a fresh VPS + backfill reconcile self-heals; a genuine resilience asset worth stating) with a max-staleness SLO, OR an explicit accepted-risk statement. Drop the "strictly larger workload" framing.

**COMPLETENESS-4 — no IAM / least-privilege / secret-handling story for one box holding a prod R2 write+delete token + cross-account SQS access.** *[major]*
Security/IAM/secret-storage/credential-scope/cross-account-authz appear nowhere — not §2, §3.1, §4.3, §5.1, §7.3, and crucially not §11 (which claims to list "the real ones I found").
*Survived because:* the design consolidates render+ingest+poller off Railway onto ONE box, concentrating secrets that were split, and runs app-side prune + lifecycle deletes on the same box holding the prod R2 write token — a genuinely new blast-radius the doc never names; the repo's own discipline (GH_PUSH_TOKEN "never echo, never in repo") shows it's taken seriously. Partial because one mechanism overstated: NOAA's NODD topics are *public* (verified), so the cross-account authz we owe is our own queue policy + minimal IAM, not a NOAA-side resource policy we design.
*Fix:* add a §5.x "Security & IAM (blast radius)": scope the R2 token to write+delete on `sat/*` + `shadow/*` only (viewer/Worker on a separate read-only token); specify our SQS queue policy (`Principal sns.amazonaws.com`, `aws:SourceArn` pinned to the topic ARNs) + minimal `Receive/Delete/GetQueueAttributes` IAM on our queues + `sns:Subscribe`; state secret storage (`systemd EnvironmentFile`, 0600, not in repo) + a rotation plan; name the box-compromise blast radius explicitly.

**COMPLETENESS-3 — SNS attribute-vs-payload filtering mis-modeled: NOAA's key is in the body, so the filter is payload-based ($0.09/GB scanned), not free.** *[minor]*
*Survived because:* attribute filtering is $0, payload filtering is $0.09/GB scanned billing matched + unmatched (verified), and S3-event SNS messages arrive without attributes (AWS's own stated reason for launching payload filtering) — so §3.1's filter is necessarily payload-based, and §10 omits any SNS/SQS message+filter line. The subtlety that strengthens it: an SNS payload filter can cost *more* than no SNS filter (subscribe to one queue + filter in the worker = SQS receive only, no scan charge). Downgraded to **minor** because absolute cost is small (single-digit GB/day) — a correctness/framing fix, not a budget risk.
*Fix:* requalify §3.1 (the filter is payload-based, NOT free; it cuts SQS-receive volume but adds a small scan cost; cheaper alternative = filter in-worker); add the SNS/SQS message-cost line to §10; verify against a real subscription in S1.

**COMPLETENESS-5 — disaster recovery / cold-bootstrap of the ledger + watermark on a fresh box is undefined; an empty watermark risks re-ingesting or skipping the rebuild gap.** *[minor]*
*Survived as the cold-start half* (the R2-outage→DLQ and NOAA-outage sub-claims were largely refuted — the §3.3 backfill is the designed recovery for both; a transient outage at worst wastes the box and exercises DLQ-inspect/backfill, it does not permanently miss slots). On a *replaced* box the on-disk ledger + watermark are wiped and §3.3's "reconcile the recent prefix" has an undefined lower bound. Notably the doc leans on the enscenters "derive from R2 reality" lesson for backfill *ordering* but never applies it to watermark *bootstrap*. Minor because it's a rare DR event and the fix reuses a lesson the doc already cites.
*Fix:* add a short §3.5/§3.6 "Cold start & ledger durability": on empty watermark, seed it from R2 reality (`ListObjectsV2` the `sat/*` prefix for the newest present complete slot per `(sat,product)` → backfill then fills the down-window gap newest-first); state the on-disk store is a cache, R2 is truth, ledger rebuild = re-derive from R2; add one sentence to §3.4 that DLQ'd slots during an R2 outage are re-enqueued by backfill on recovery (not lost).

**COMPLETENESS-9 — on-disk ledger + crop scratch + tile staging on one box, no disk-capacity / disk-full failure model.** *[minor, confirmed]*
*Survived because:* §3.2 (on-disk ledger), §5.2 (mosaic staging), and the fetch+crop+render-before-PUT path all consume disk, yet §10 sizes only RAM/R2 PUTs — disk is absent (not even a "to-confirm-in-S1" item), there's no `disk_free` heartbeat analog, and the repo's SST-frame-cache gotchas establish large local state as a documented footgun. Crop-before-composite bounds per-tile RAM but not total FD-mosaic disk scratch. The one overclaim (disk-full → "silent" never-miss) is partly refuted: §3.4 log()s + slow-alarms DLQ'd slots, so the symptom surfaces — disk is the undiagnosed *cause* behind it.
*Fix:* add a §10 local-disk line (worst-case scratch = per-FD-slot staging × concurrency + ledger growth + tile staging, confirm in S1) + a §5 scratch-cleanup policy (delete source after render+PUT) + a `disk_free_mb` heartbeat assertion with an alert threshold that fires *before* the gate keeps saying "go."

**COMPLETENESS-8 — the doc claims "untracked, uncommitted" but is committed on the public Pages repo main (@ec47eda).** *[nit]*
*Survived as a narrow factual error:* §0 line 3's "(untracked, uncommitted)" is contradicted by git, on a repo where a main commit is live in ~60 s. Downgraded to **nit** because §12's *actual* claims (nothing built/deployed/branched/R2-written) are all true (committing a markdown is none of those), and six sibling design docs (CYCLOLAB_DESIGN.md, ENSEMBLE_DESIGN.md, …) already live tracked at repo root by convention — so the "no business being live / split-brain hazard" framing is inflated.
*Fix:* fix only the §0 wording to state the truth (committed for review visibility, like the sibling design docs; not a rendered page; `git mv` to tat-satellite-render on sign-off; §12's "nothing built/deployed" is unaffected). Leave §12 as-is. Do **not** remove it from the Pages repo.

---

### Dimension: Zero-visual-change "by construction" guarantee — max severity MAJOR (folded into the gate-tiering fix)

The zvc axis produced seven findings; all survived as partial/minor, and several share a kernel with CUTOVER-5 (the gate-tiering fix below resolves ZVC-2 + ZVC-5 + CUTOVER-5 in one place).

**ZVC-2 — "0 over budget" is not "pixel-identical": the 0.1% AA-filtered budget contradicts the "non-zero diff = a real bug" gate.** *[major]*
*Survived because:* the doc specifies one §7.2 gate applied uniformly, and the AA-filter excludes antialiased pixels — so a sub-pixel text/coastline shift (the host-move drift class) passes silently green on the very tier whose claim is byte-identity; §9's promised "fail loudly" is not what §7.2 performs. libwebp is pinned, so true-zero is achievable. *(Same fix as CUTOVER-5: tier the gate — strict-identity for by-construction products, the 0.1% AA + SSIM budget only for the reference-render tiled map.)*

**ZVC-5 — the dumb player's data-fetch / URL-derivation code MUST change for `latest_times.json`; "viewer UNCHANGED" conflates unchanged look with unchanged code.** *[minor]*
*Survived because:* §9's proof obligation verifies floater/meso only via per-frame pixel diff — which cannot detect a frame-*ordering* / latest-pointer / sequence-derivation regression from the new template+times code path (every frame is byte-identical regardless of assembly order). §4.1 already documents the data-layer change, so the framing-contradiction is only true of §6.1's bare label.
*Fix:* add a §9 sequence/derivation gate item — assert the new template+times derivation yields the identical ordered URL sequence + latest pointer as today's manifest for a captured slot set; tighten §6.1 wording ("unchanged playback engine + data layer reduced to template+times per §4.1").

**ZVC-7 — recentering crop bbox is timing-dependent on the feed extrapolation, so event-driven ingest can perturb it → a correctly-frozen renderer produces a valid non-zero diff the §9 gate flags as drift.** *[minor, confirmed]*
*Survived because:* §5.4's bbox is a function of `_extrapolate` evaluated against the live feed at schedule time; §7.1 keeps prod computing its bbox at 60 s-poll time while §7.2 diffs the event-driven shadow — two independent pipelines, each computing its own center, so a sub-degree shift on a 12° crop (several % of frame width, above 0.1% and into SSIM-structural territory) is a *correct* diff that could make S3/S4 un-passable. Scope is floater + meso only (tiled map = fixed TATRegions; custom-zoom = explicit bbox).
*Fix:* §5.4/§7.2/§9 make the bbox a *captured* input (prod logs the bbox per (storm,band,slot); shadow renders with that same bbox) so the diff isolates the renderer/wrapper from valid recentering; one sentence in §2 that the crop bbox is a timing-sensitive input the by-construction guarantee is conditional on.

**ZVC-4 — the additive chrome-free map mode requires extending the frozen renderer, so §2's "only sanctioned reason is perf" contradicts §6.3's "additive renderer mode."** *[minor]*
*Survived because:* §2's "only … perf" is directly contradicted by §6.3's chrome mode, and neither B1 nor B2 escapes the renderer extension (B1/B2 differ only in overlay-chrome fidelity, not in whether the tile is chrome-free); the doc never decides whether the chrome-free raster is a *separate* module or an edit *inside* `render.py`, leaving the floater by-construction claim contingent. Caught by the §9 pixel-diff gate, so minor.
*Fix:* broaden §2's "only sanctioned reason" to admit (a) perf forcing-function AND (b) the additive chrome-free map raster (both behind the per-product gate); in §6.3, prefer building the chrome-free raster in a SEPARATE module sharing no mutable import-time state, or state plainly that the floater guarantee is contingent and §9's S3/S4 diff re-proves it after every map-mode change.

**ZVC-1 — version-pinning the toolchain does not pin thread count / float-reduction order / SIMD across a host move.** *[minor]*
*Largely refuted on mechanism* (the frozen renderer has no dask/satpy/pyresample threaded reductions; the pixel path is element-wise numpy + scipy RegularGridInterpolator + per-pixel pyspectral LUT; the only reductions are order-invariant min/max + threshold-only `.mean()`), and §7.3 already runs shadow on the same pinned image with an empirical cross-host gate. *Survives as cheap insurance:* §7.3 omits execution-environment thread controls.
*Fix:* add `OMP_NUM_THREADS=1`/`OPENBLAS_NUM_THREADS=1`/`MKL_NUM_THREADS=1`/`VECLIB_MAXIMUM_THREADS=1`/`NUMEXPR_NUM_THREADS=1` to the §7.3 pins (insurance, not a fix for a known bug); one sentence that the OVH-shadow-vs-Railway-prod diff IS the cross-host test.

**ZVC-3 — the shadow-vs-prod gate runs both sides on different hosts, so §7.1 "untouched prod" and §7.3 "same pinned image as prod" cannot both hold literally.** *[minor]*
*Survived as a literal unreconciled contradiction* (if prod is untouched it isn't on the pinned image), but the gate is conservative (blocks on any over-budget diff, so no false-green), so it's a doc-precision fix.
*Fix:* reconcile §7.1/§7.3 — either deploy the same frozen image to BOTH hosts, or characterize the host-move floor by re-rendering historical prod slots on OVH and diffing vs the Railway-emitted WebPs first; soften §7.3's "any diff is real drift, not env noise" to acknowledge a residual cross-host floor that the §7.2 budget absorbs.

**ZVC-6 — libwebp pinned by version, but the doc never asserts the encoder is byte-deterministic at fixed settings on the same arch.** *[nit]*
*Largely refuted* (the "same pinned image" clause fixes build flags/SIMD table; the documented `-mt` non-determinism is lossless-only while the products are lossy q90; the actual gate diffs *decoded* pixels under a tolerance, not raw bytes). *Survives as a wording precision:* §2's literal "identical output bytes" overstates.
*Fix:* soften §2 to "identical decoded pixels (the gate compares decoded pixels at the §7.2 tolerance)"; one S1 spot-check that `transcode_frame` is decoded-pixel-stable across hosts.

---

### Dimension: MapLibre tiled-animation approach — max severity MAJOR

**MAPLIBRE-3 — `raster-opacity:0` frames still download tiles AND hold GPU textures; "LRU-evict offscreen frame-sources" is a category error for a same-viewport time-stack.** *[major, partial]*
*Survived because:* verified that MapLibre tile loading is viewport-driven and opacity-independent, the cache is per-source and evicts *offscreen* tiles only, and in a same-extent stack no frame is offscreen — so the stated memory bound does nothing. Partial (not the finding's exact mechanism): the failure is **unbounded simultaneous texture residency** (eviction never fires), not LRU re-fetch churn. Not in §11.
*Fix:* in §6.1 replace "LRU-evict offscreen frame-sources" with a concrete scheme — a small sliding window of frame layers toggled with `visibility:none` (not `opacity:0`, which keeps tiles+textures resident) sized to the device texture budget, OR a single source with `updateImage` for the non-pyramid case; note MapLibre owns eviction (per-source, offscreen-only); add a §11 flag reframing the risk as texture residency.

**MAPLIBRE-4 — the tiled-animation client is far heavier than the dumb player (GETs + decoded bitmaps) and that cost is never modeled.** *[minor]*
*Downgraded from major* because the 341 figure is the *server-side* full-pyramid count — a MapLibre client loads only viewport tiles at the current zoom (verified), so the "15k–40k GETs / thousands resident" arithmetic is inflated (viewport-bound, not pyramid-bound), and §6.1's LRU eviction partly pre-mitigates. *Survives:* §10 is genuinely silent on the client decode/memory/GET budget for the new engine, which is materially heavier than one Image()/frame (MapLibre warns each source has high render overhead); §11-C is server-PUT only.
*Fix:* add a client-side §10 row + §11 flag scoped to *viewport-bound* load (tiles-per-frame = visible-viewport tiles at active zoom × frames), peak resident bitmap memory on a mid mobile device tied to Mobile Phase B, with a ceiling + fallback to single-image `updateImage` / the `/render` custom-box path. Floater/meso unaffected.

**MAPLIBRE-5 — no readiness gate for the tiled map equivalent to the dumb player's `decode()` gate; first-loop/slow-link can flip opacity onto a still-loading frame.** *[minor]*
*Refuted on mechanism* (the offscreen/`maplibre-preload` framing is wrong — same-bbox stacked layers load their in-viewport tiles on add, and readiness is first-class via `isSourceLoaded`/`areTilesLoaded`/`idle`, not plugin-dependent). *Survives as a real timing asymmetry:* the doc elevates the `im.decode()` gate to a stated virtue (§1/§11-A) but the §6.1 tiled engine specifies only opacity-toggle — no "hold prior frame until next is loaded" gate; §8-S2 gates only on reference-render visual diff, not playback readiness.
*Fix:* one §11 / §6.1 line carrying the readiness-gate principle into the tiled engine — before flipping opacity 0→1, gate on `isSourceLoaded`/`areTilesLoaded`/`idle` (NOT `maplibre-preload`); HOLD on the prior frame on first-loop/slow-link rather than painting partial; an S2 implementation gate validated against a live render.

**MAPLIBRE-6 — per-frame raster SOURCES carry documented per-source overhead; a 60–120-source style risks the degradation regime, and the doc records no perf gate.** *[minor]*
*Survived because:* §6.1 names the LRU mitigation but never the *rationale* (so it could be "cleaned up" later, unlike flag-H-style pinned decisions), and the §8 S2 gate is visual-correctness only — no source-add-time / pan-zoom-interactivity measurement for an unusual non-standard idiom. The numeric "danger band" is anecdotal, so no hard cap should be coded.
*Fix:* add a one-clause rationale to the §6.1 LRU line ("MapLibre flags high per-source overhead — bound the working set; load-bearing, do not remove") + a §8-S2 perf acceptance criterion (measure source-add time + pan/zoom smoothness with the full frame set on a mid device; fall back to single-image `updateImage` if it measures poorly).

**MAPLIBRE-1 — the doc never weighs MapLibre's single-source `updateImage` image-animation idiom.** *[nit]*
*Largely refuted:* `updateImage`/ImageSource is a single fixed-corner image with no tile pyramid and no zoom/pan (verified) — it is a strictly *lesser* product already covered by the kept dumb `<img>` player + GIBS image layers, not a lighter equal-capability substitute; tile-pyramid zoom IS the stated hard requirement, so the addSource-per-frame-over-a-pyramid choice stands.
*Fix:* one §6.1 clause ruling out `updateImage` explicitly ("ImageSource = single fixed-corner image, no pyramid — rejected because the product requires true multi-zoom pan/zoom") + a note to set `raster-fade-duration:0` on the per-frame raster layers.

**MAPLIBRE-7 — the per-frame-source + opacity-toggle technique is asserted without citing a precedent, and it is not MapLibre's documented idiom.** *[nit]*
*Survived narrowly:* §6.1 presents the technique with only the anti-`setTiles` justification (which is factually correct — verified `setTiles` cache-rebuild/stale behavior) and §1's "proven dependency" only proves the library loads, not the animation technique. But the finding mis-grounds the fix (the relevant precedent is weather-radar tiled-frame practice, not the inapplicable `updateImage` example).
*Fix:* one §6.1 clause distinguishing the RasterTileSource (pyramid) from the inapplicable ImageSource `updateImage` idiom and citing weather-radar tile-animation practice as the precedent; optionally soften §1's "proven dependency" to note the technique is adapted and gated by the §6.3 reference-render diff.

---

### Dimension: R2 Class-A-PUT cost model — max severity MINOR

The r2cost axis produced five findings; the skeptic confirmed the design *direction* is sound (z0–4 hard count cap + render-on-change + TTL + S1 measurement) and downgraded all but one to minor/nit. The one major-tier concern (render-compute, not PUT cost) is **R2COST-3**, but its decisive kernel overlaps RAM-6 (FD wall-clock) and RAM-1 (per-tile RAM) and is folded there.

**R2COST-3 — render-compute of the pyramid (you must render all sub-tiles to hash them) is never modeled; dedup suppresses the PUT, not the CPU/wall-clock.** *[major, confirmed]*
*Survived because:* §5.2's RAM fix ("mosaic of cropped sub-sectors, each cropped-then-composited … then assembled") is precisely what multiplies wall-clock, and the doc's only suppression mechanism (content-hash dedup) requires the bytes — i.e. requires rendering — before it can no-op the PUT; there is no pre-render change-gate. §10 conflates "PUT cost" with "cost." *(Fix folded into RAM-1 + RAM-6: add a render-throughput line independent of dedup; either a genuine pre-render change-gate or a cadence/zoom bound or a render-box forcing-function.)*

**R2COST-1 — Stage-0 gate defers the most-cited cost driver (PUTs/day) with no number, though it's trivially computable.** *[minor]*
*Survived because:* the doc's own figures (341 tiles × 144 frames/day) put a single tiled FD band at 1.47M PUTs/mo — over the 1M Class-A free tier (verified $4.50/M). Downgraded to minor because the worst case is tens of dollars/month (the design's caps hold) — the gate can approve with eyes open.
*Fix:* add one worst-case line/table to §10 (341 × frames/day × tiled-bands × sats, no-dedup ceiling + one dedup hit-rate, vs the 1M free tier, $ at $4.50/M); note the single-product figure already exceeds the free tier.

**R2COST-2 — dedup helps the static tiles nobody zooms into; active cloudy/storm tiles re-PUT every frame, so the PUT bound can't lean on dedup.** *[nit]*
*Downgraded because* the doc's z0–4 *count cap* is the actual bound (dedup-independent) and §4.2's 341 arithmetic is already the no-dedup worst case — there is no optimistic dedup-credited planning figure to correct. *Survives as prose:* the "static ocean re-PUTs ~never" parenthetical mis-implies dedup does meaningful suppression work.
*Fix:* one honest sentence in §4.2 (dedup suppresses only clear/static tiles, anti-correlated with interest; active tiles re-PUT every frame; the z0–4 count cap is the bound, not dedup) + an S1 note to measure the hit-rate.

**R2COST-4 — the 341 figure assumes a single-rooted global Web-Mercator quadtree; the scheme/root is never pinned and z4 is coarser than native.** *[minor]*
*Refuted as a cost error* (341 is a conservative over-estimate for a geostationary disk) but *survives as a scheme/resolution-pinning gap:* z4 over global Web-Mercator is ~4.9 km/px (512-px tile) — coarser than GOES-F native 2 km, so z0–4 is overview-only, a ceiling the doc presents on cost grounds but never on resolution grounds.
*Fix:* one §4.2 sentence pinning the scheme (MapLibre default global Web-Mercator XYZ rooted at world z0), re-labeling 341 as a global-grid upper bound (disk = a subset, S1-measured), and stating the z4 resolution ceiling + that native detail needs z>4 (gated by §11-C).

**R2COST-5 — the backfill `ListObjectsV2` reconcile is a recurring Class-A cost named ("and LIST") but never budgeted.** *[nit]*
*Downgraded* because the dollars are trivial (~$50/yr worst case) and §3.3 already scopes the scan to "the recent prefix." *Survives as budget completeness:* §4.2 elevates LIST to a cost driver then drops it from §10 and the "Minimize PUTs" heading.
*Fix:* add a §10 LIST/day line + rename §4.3 "Minimize PUTs" → "Minimize Class-A ops (PUT + LIST)" with a note that the reconcile scans the recent prefix tightly (~1 op/tick).

---

## Considered and dismissed (auditable coverage — no false-green)

| ID | Dimension | Title | Why dismissed |
| --- | --- | --- | --- |
| INGEST-4 | Never-miss ingest | True-color 5-band grouping assumes a shared `s`-time token that per-band files might split | **Refuted on fact.** Empirically verified against live `noaa-goes16`: the `s`-time token is byte-identical across all 16 bands of every CMIP slot (CONUS/FD/Meso). The per-band variation is in the `e`-time, which the design does not key on. §3.1's `s`-slot key does not split slots; no silent "complete raw, no frame" path is created. |
| MAPLIBRE-2 | MapLibre animation | `raster-fade-duration` broken/ignored in 4.7.1, so opacity-toggle can't guarantee a hard frame cut | **Refuted three ways.** (1) Fabricated doc claim — the doc contains no "raster-fade-duration", "crossfade", "ghosting", or "hard cut"; §6.1 only asserts `setTiles` *stutter*, so there is no settled spec to contradict and §11 correctly has no flag. (2) The cited bug (maplibre #5038) reports the OPPOSITE — post-4.3.0 the fade is *ignored toward instant appearance*, i.e. the hard cut the design wants. (3) The recommended `updateImage` fallback itself relies on `raster-fade-duration:0`, so if the property were broken the fallback would ghost identically — the two MapLibre findings cancel. |

---

## Residual open questions for sign-off (distinct from §11 A–H)

These are decisions Andrew must still make that the review surfaced beyond the doc's own flags. Where a finding sharpens an existing §11 flag, it is noted.

1. **Gate tiering (ZVC-2 / ZVC-5 / CUTOVER-5).** Accept that the by-construction products get a strict-identity gate (sha256 / threshold-0) and the 0.1% AA + SSIM budget applies *only* to the tiled map? This sharpens **§11-B** by separating the two guarantee tiers explicitly.

2. **Renderer-scope exceptions (ZVC-4 / RAM-3 — sharpens §11-B).** §6.3 currently names one exception (chrome). Sign-off must accept a SECOND (the `EPSG:3857` reprojection stage) and decide isolation: chrome-free raster + reproject in a *separate* module, or edits *inside* `render.py` with the floater guarantee declared contingent on the §9 gate.

3. **Single-box vs split-box (RAM-5 / RAM-6 / COMPLETENESS-1 / COMPLETENESS-6).** Does the FD pyramid renderer co-reside with the never-miss ingest on one 8–16 GB box, gated by S1's measured per-tile RAM floor + wall-clock + cgroup `MemoryMax` — or split onto its own box per the repo's HAFS precedent? This is the binding capacity decision the doc defers; sharpens **§11-E/§11-F**.

4. **Derived-product never-miss authority (INGEST-3).** Accept adding a `(storm/sector slug, band, s-slot)` coverage ledger + storm-feed-as-trigger + a frame-coverage audit, so the never-miss guarantee covers the products users see, not just raw NOAA slots. This is the most consequential delta beyond **§11-E**.

5. **Cutover sequencing (CUTOVER-1/2/3).** Accept the per-product *writer* hand-off (old poller stops writing+pruning at flip), the separate one-time *viewer* cutover (rollback = redeploy, not env flip), and the manifest-schema migration (dual-write or dual-tolerant viewer). The "no-redeploy one-line revert" claim narrows to the writer side.

6. **Soak definition (CUTOVER-6).** Approve the concrete soak/decommission bar (≥14 days + ≥1 active-storm cycle per product, gate green throughout) before the rollback fallback is removed.

7. **New cross-cutting dimensions to own (COMPLETENESS-2/4).** Approve adding observability/alerting (§3.5) and security/IAM (§5.x) subsections — both are entirely absent from §11 today and gate sign-off.

---

## r2 change-list (folds into SATELLITE-REARCH.md)

A short index of the edits being applied. Surgical, in the doc's voice; full text in the companion `docEdits`.

- **§0** — fix the "untracked, uncommitted" wording to match git reality (COMPLETENESS-8).
- **§2** — broaden "only sanctioned reason is perf" to admit the chrome-free raster + reproject exceptions (ZVC-4, RAM-3); soften "identical output bytes" to "identical decoded pixels" (ZVC-6); note the crop bbox is a timing-sensitive input (ZVC-7).
- **§3.1** — require `FilterPolicyScope=MessageBody` + explicit prefix-array policy + payload-filter cost note (INGEST-1, INGEST-2, COMPLETENESS-3).
- **§3.3** — derived-product coverage ledger + storm-feed trigger + frame-coverage audit (INGEST-3); explicit backfill bound (INGEST-6).
- **§3.4** — DLQ-on-R2-outage re-enqueue note (COMPLETENESS-5).
- **New §3.5 — Observability & alerting** (COMPLETENESS-2, INGEST-5).
- **New §3.6 — Cold start & ledger durability** (COMPLETENESS-5).
- **§4.2 / §4.3 / §10** — tier the WebP gate; honest dedup framing; pin the tiling scheme + resolution ceiling; PUT/LIST/disk/client-decode budget lines (R2COST-1/2/4/5, MAPLIBRE-4, COMPLETENESS-9).
- **§5.1 / §5.2 / new §5.x** — per-tile RAM floor + concurrency (RAM-1); halo (RAM-2); cgroup `MemoryMax` + HAFS-precedent reconciliation (RAM-5); reprojection stage (RAM-3); render-throughput line (R2COST-3); security/IAM (COMPLETENESS-4).
- **§6.1** — fix "LRU-evict offscreen" → sliding window / `visibility:none` (MAPLIBRE-3); rule out `updateImage` + `raster-fade-duration:0` note (MAPLIBRE-1); LRU rationale + readiness gate (MAPLIBRE-5/6); RasterTileSource-vs-ImageSource precedent (MAPLIBRE-7); "data layer reduced to template+times" (ZVC-5).
- **§7.1 / §7.3 / §7.4 / §7.5** — reconcile untouched-prod vs same-image (ZVC-3); thread-env pins (ZVC-1); tier the cutover gate (ZVC-2/CUTOVER-5); writer hand-off (CUTOVER-1); writer/reader split + no-redeploy correction (CUTOVER-2); schema sequencing (CUTOVER-3); rollback recovery model (CUTOVER-4); soak definition (CUTOVER-6).
- **§8 / §9** — S2 throughput + perf gates (RAM-6, MAPLIBRE-6); sequence/derivation gate (ZVC-5); seam diff (RAM-2); bbox-captured-input gate (ZVC-7); new §9.x logic-testing obligation (COMPLETENESS-7).
- **§11 — new flags I–N** — render memory protection/co-tenancy (RAM-5), render wall-clock/CPU contention (RAM-6), resample/assembly seams (RAM-2), observability/alerting (COMPLETENESS-2), security/IAM blast radius (COMPLETENESS-4), failure-domain/host-loss (COMPLETENESS-1), backpressure/burst (COMPLETENESS-6), logic-testing (COMPLETENESS-7), tiled-client memory + texture residency (MAPLIBRE-3/4).
- **§13** — r2 revision-log entry.