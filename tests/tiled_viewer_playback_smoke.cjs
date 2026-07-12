// Node smoke test for the tiled explorer playback engine (tiled_viewer.js).
//
// Proves the no-strobe contract introduced with the full-loop-residency
// rewrite (satellite/explorer/tiled_viewer.js header, "PLAYBACK CONTRACT"):
//   1. boot reveals NOTHING until the first frame's tiles are confirmed
//      loaded ('frame' status only fires from a gated reveal)
//   2. the whole loop preloads (staggered <= MOUNT_AHEAD in flight) with
//      monotonic onStatus('loading', {done,total}) then ONE 'loaded'
//   3. in-loop frames are NEVER hidden or removed during playback (the old
//      keep-window eviction was the strobe: a hidden source lies that it is
//      loaded, so wrap-around revealed empty sources over the dark basemap)
//   4. a reveal NEVER runs for a source that is not loaded; the prior frame
//      holds opaque until the target's tiles land
//   5. out-of-order tile arrivals cannot resurrect a stale scrub target
//      (the newest showFrame wins -- _wantStamp token)
//   6. product switch: outgoing product stays visible until the incoming
//      frame is loaded; stamp-keyed readiness does NOT leak across products
//      that share stamps; switch-back resurrects retained sources instantly
//
// No jsdom needed: the module under test only touches maplibregl + fetch in
// these paths, both stubbed below.
// Usage: node tests/tiled_viewer_playback_smoke.cjs
"use strict";
const path = require("path");

let failures = 0;
function ok(cond, msg) {
  console.log((cond ? "ok" : "NOT OK") + " - " + msg);
  if (!cond) failures++;
}
const tick = () => new Promise((r) => setImmediate(r));

// ---- fake MapLibre --------------------------------------------------------
class FakeMap {
  constructor() {
    this.sources = {};
    this.layerOrder = [];
    this.paint = {};      // layerId -> raster-opacity
    this.layout = {};     // layerId -> visibility
    this.loaded = {};     // sourceId -> bool (test-controlled)
    this.handlers = {};
    this.removedLayers = [];
    this.hiddenEver = {}; // layerId -> true if visibility was EVER set 'none'
    this.dragRotate = { disable() {} };
    this.touchZoomRotate = { disableRotation() {} };
    this.boxZoom = { disable() {} };
  }
  on(ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); }
  off(ev, fn) {
    const a = this.handlers[ev] || [];
    const i = a.indexOf(fn);
    if (i >= 0) a.splice(i, 1);
  }
  once(ev, fn) {
    const self = this;
    const wrap = function (e) { self.off(ev, wrap); fn(e); };
    this.on(ev, wrap);
  }
  fire(ev, e) { (this.handlers[ev] || []).slice().forEach((f) => f(e)); }
  addControl() {}
  addSource(id, def) { this.sources[id] = def; if (!(id in this.loaded)) this.loaded[id] = false; }
  removeSource(id) { delete this.sources[id]; }
  addLayer(l, before) {
    this.layerOrder.push(l.id);
    if (l.paint && "raster-opacity" in l.paint) this.paint[l.id] = l.paint["raster-opacity"];
    this.layout[l.id] = "visible";
  }
  removeLayer(id) {
    const i = this.layerOrder.indexOf(id);
    if (i >= 0) this.layerOrder.splice(i, 1);
    this.removedLayers.push(id);
    delete this.paint[id];
    delete this.layout[id];
  }
  getLayer(id) { return this.layerOrder.indexOf(id) >= 0 ? { id } : undefined; }
  getSource(id) { return this.sources[id]; }
  setPaintProperty(id, k, v) { if (k === "raster-opacity") this.paint[id] = v; }
  setLayoutProperty(id, k, v) {
    this.layout[id] = v;
    if (v === "none") this.hiddenEver[id] = true;
  }
  isSourceLoaded(id) { return !!this.loaded[id]; }
  setMinZoom() {} setMaxZoom() {} getZoom() { return 3; }
  cameraForBounds() { return { zoom: 3 }; }
  setRenderWorldCopies() {} fitBounds() {}
  // ---- test helpers ----
  loadSource(id) { this.loaded[id] = true; this.fire("sourcedata", { sourceId: id }); }
  loadAllPending(pfx) {
    // settle everything mounted for a product prefix, like a burst of tile
    // completions; loops because loading one source pumps more mounts
    for (let guard = 0; guard < 500; guard++) {
      const next = Object.keys(this.sources).find(
        (id) => id.indexOf(pfx) === 0 && !this.loaded[id]);
      if (!next) return;
      this.loadSource(next);
    }
  }
  idle() { this.fire("idle"); }
}

let lastMap = null;
global.window = {};   // _onLoad probes window.TATRegions / window.BTProbe
global.maplibregl = {
  Map: function () { lastMap = new FakeMap(); return lastMap; },
  NavigationControl: function () {},
  Marker: function () { return { setLngLat() { return this; }, addTo() { return this; }, remove() {} }; },
};

// ---- manifests ------------------------------------------------------------
function stampFor(i) {
  const mm = String(i % 60).padStart(2, "0");
  const hh = String(Math.floor(i / 60)).padStart(2, "0");
  return "20260712T" + hh + mm + "17Z";
}
function manifest(product, n) {
  return {
    product: product,
    tile: product + "/{t}/{z}/{x}/{y}.webp",
    times: Array.from({ length: n }, (_, i) => stampFor(i)),
    latest: stampFor(n - 1),
    bounds: [-150, 10, -60, 55], minzoom: 0, maxzoom: 5, tile_size: 512,
  };
}
const N = 20;
const MANIFESTS = {
  "https://x/sat/g/conus/ir/latest_times.json": manifest("sat/g/conus/ir", N),
  // truecolor shares EVERY stamp with ir on purpose (readiness-leak check)
  "https://x/sat/g/conus/truecolor/latest_times.json": manifest("sat/g/conus/truecolor", N),
};
global.fetch = function (url) {
  const m = MANIFESTS[url];
  return Promise.resolve({ ok: !!m, status: m ? 200 : 404, json: () => Promise.resolve(m) });
};

const { TiledViewer } = require(path.join(__dirname, "..", "satellite", "explorer", "tiled_viewer.js"));

(async () => {
  const events = [];
  const v = new TiledViewer({
    container: "c",
    manifest: "https://x/sat/g/conus/ir/latest_times.json",
    onStatus: (kind, data) => events.push({ kind, data }),
  });
  await v.boot();
  const map = lastMap;
  ok(!!map, "boot created the map");
  map.fire("load");
  await tick();

  const irSid = (s) => "sat/g/conus/ir-" + s;
  const newest = stampFor(N - 1);

  // ---- 1. boot: nothing revealed before tiles confirmed --------------------
  ok(!events.some((e) => e.kind === "frame"),
    "boot: no 'frame' status before the first frame's tiles load");
  ok(map.paint[irSid(newest)] === 1, "boot: newest frame mounted at opacity 1 (held, not revealed)");
  ok(events.some((e) => e.kind === "loading" && e.data.total === N),
    "boot: 'loading' progress started (total " + N + ")");

  map.loadSource(irSid(newest));
  ok(events.some((e) => e.kind === "frame" && e.data.stamp === newest),
    "boot: 'frame' fires once the newest frame's source is loaded");
  ok(v.frameIdx === N - 1, "boot: frameIdx settled on newest");

  // ---- 2. staggered preload completes with one 'loaded' --------------------
  let inflight = Object.keys(map.sources)
    .filter((id) => id.indexOf("sat/g/conus/ir-") === 0 && !map.loaded[id]).length;
  ok(inflight <= 6, "preload: <= MOUNT_AHEAD (6) unloaded sources in flight (got " + inflight + ")");
  map.loadAllPending("sat/g/conus/ir-");
  map.idle();
  const loadedEvents = events.filter((e) => e.kind === "loaded");
  ok(loadedEvents.length === 1, "preload: exactly one 'loaded' event (got " + loadedEvents.length + ")");
  const dones = events.filter((e) => e.kind === "loading").map((e) => e.data.done);
  ok(dones.every((d, i) => i === 0 || d >= dones[i - 1]),
    "preload: 'loading' done-count is monotonic (" + dones.join(",") + ")");
  const mounted = MANIFESTS["https://x/sat/g/conus/ir/latest_times.json"].times
    .filter((s) => map.getLayer(irSid(s)));
  ok(mounted.length === N, "preload: all " + N + " in-loop frames mounted (got " + mounted.length + ")");

  // ---- 3+4. playback: gated reveals, full residency -------------------------
  for (let lap = 0; lap < 2; lap++)
    for (let i = 0; i < N; i++) v.showFrame(i);
  const hiddenLoopLayers = Object.keys(map.hiddenEver).filter((id) => id.indexOf("sat/g/conus/ir-") === 0);
  ok(hiddenLoopLayers.length === 0,
    "residency: no in-loop layer was ever hidden across 2 full laps");
  ok(map.removedLayers.filter((id) => id.indexOf("sat/g/conus/ir-") === 0).length === 0,
    "residency: no in-loop layer was removed");
  const opaque = Object.keys(map.paint).filter((id) => map.paint[id] === 1);
  ok(opaque.length === 1 && opaque[0] === irSid(stampFor(N - 1)),
    "playback: exactly one opaque frame after the laps (got " + opaque.join(",") + ")");

  // unready target: reveal must WAIT (prior frame holds), even though the
  // sticky flag is set -- the live isSourceLoaded check catches camera moves
  const target = stampFor(4);
  map.loaded[irSid(target)] = false;          // simulate tiles needed again
  const framesBefore = events.filter((e) => e.kind === "frame").length;
  v.showFrame(4);
  ok(events.filter((e) => e.kind === "frame").length === framesBefore,
    "gate: no reveal while the target's source is unloaded");
  ok(map.paint[irSid(stampFor(N - 1))] === 1, "gate: prior frame still opaque (holds)");
  map.loadSource(irSid(target));
  ok(v.frameIdx === 4, "gate: reveal lands when the tiles do");

  // ---- 5. out-of-order arrivals: newest request wins ------------------------
  map.loaded[irSid(stampFor(7))] = false;
  map.loaded[irSid(stampFor(9))] = false;
  v.showFrame(7);
  v.showFrame(9);
  map.loadSource(irSid(stampFor(7)));         // stale target's tiles arrive first
  ok(v.frameIdx === 4, "token: stale target (7) did NOT reveal (frameIdx still 4)");
  map.loadSource(irSid(stampFor(9)));
  ok(v.frameIdx === 9, "token: newest target (9) revealed on its own load");

  // ---- 6. product switch ----------------------------------------------------
  const tcSid = (s) => "sat/g/conus/truecolor-" + s;
  const evBefore = events.length;
  const p = v.setProduct("https://x/sat/g/conus/truecolor/latest_times.json", {});
  await p;
  await tick();
  ok(map.paint[irSid(stampFor(9))] === 1,
    "switch: outgoing product's frame still opaque before incoming tiles land");
  ok(map.getLayer(irSid(stampFor(0))), "switch: outgoing sources retained (retired, not destroyed)");
  // readiness must NOT leak: truecolor shares the stamp but its own source is unloaded
  ok(map.paint[tcSid(newest)] === 1 && !map.loaded[tcSid(newest)],
    "switch: incoming frame mounted+held, NOT treated as ready via the shared stamp");
  ok(!events.slice(evBefore).some((e) => e.kind === "frame" && e.data.n === N && e.data.stamp === newest
       && map.loaded[tcSid(newest)]),
    "switch: no reveal before the incoming source loads");
  map.loadSource(tcSid(newest));
  ok(map.layout[irSid(stampFor(9))] === "none",
    "switch: outgoing product hidden only AFTER the incoming frame revealed");
  map.loadAllPending("sat/g/conus/truecolor-");
  map.idle();
  ok(events.filter((e) => e.kind === "loaded").length === 2,
    "switch: incoming loop preloaded to its own 'loaded'");

  // ---- switch-back: instant resurrection ------------------------------------
  await v.setProduct("https://x/sat/g/conus/ir/latest_times.json", {});
  await tick();
  ok(v._pfx === "sat/g/conus/ir", "switch-back: ir re-adopted");
  ok(map.getLayer(irSid(stampFor(0))), "switch-back: retained ir sources still mounted");
  // resurrected sources were HIDDEN by retirement -- their adopted readiness
  // is revoked (parked) and must re-confirm through the event gate; each idle
  // confirms the visible batch and unhides the next (staggered)
  const settle = async () => {
    for (let k = 0; k < 30; k++) { map.idle(); await tick(); }
  };
  await settle();
  // the background freshness merge jumps to the newest frame (same semantics
  // as the pre-rewrite engine's frameIdx = len-1 on merge), gated on tiles
  ok(v.frameIdx === N - 1, "switch-back: freshness merge lands on the newest frame (post-gate)");

  // ---- 7. camera fetch discipline (parking) ---------------------------------
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const visibleIr = () => MANIFESTS["https://x/sat/g/conus/ir/latest_times.json"].times
    .filter((s) => map.getLayer(irSid(s)) && map.layout[irSid(s)] !== "none");
  map.fire("movestart");
  ok(visibleIr().length === 1 && visibleIr()[0] === newest,
    "camera: movestart parks every frame except the on-screen one (visible: " +
    visibleIr().join(",") + ")");
  const parkedStamp = stampFor(3);
  ok(!v._frameReady(parkedStamp), "camera: a parked frame is never 'ready'");
  // scrub during the move: request parks, nothing unhides mid-drag
  v.showFrame(3);
  ok(map.layout[irSid(parkedStamp)] === "none",
    "camera: a scrub during the move does not unpark mid-drag");
  ok(v.frameIdx === N - 1, "camera: no reveal during the move (frameIdx holds)");
  map.fire("moveend");
  await wait(380);            // resume debounce (300 ms)
  const evAfterMove = events.length;
  // the scrubbed-to frame resumes FIRST
  ok(map.layout[irSid(parkedStamp)] === "visible",
    "camera: the mid-move scrub target resumes first after the camera rests");
  const unhidden = visibleIr().length;
  ok(unhidden <= 1 + 1 + 6,
    "camera: resume is staggered, not a loop-wide unhide (visible: " + unhidden + ")");
  await settle();
  ok(v.frameIdx === 3, "camera: the parked reveal lands after re-confirmation");
  ok(visibleIr().length === N, "camera: whole loop resident again after settle");
  ok(!events.slice(evAfterMove).some((e) => e.kind === "loading"),
    "camera: resume fill is QUIET (no loading-toast churn on pan)");

  // ---- 8. live-manifest densification (the 10-min backfill) -----------------
  // grow the ir manifest 20 -> 60 stamps mid-session; cap (48) bounds residency.
  // Scrub to a stamp that SURVIVES the merge (60-48=12 oldest roll off): the
  // preserve-current-stamp contract applies to in-window frames.
  v.showFrame(15);
  await settle();
  ok(v.frameIdx === 15, "densify setup: scrubbed to a surviving stamp");
  const curStamp = v.frames[v.frameIdx];
  MANIFESTS["https://x/sat/g/conus/ir/latest_times.json"] = manifest("sat/g/conus/ir", 60);
  const evBeforeDensify = events.length;
  v._refreshManifest();
  await tick(); await tick();
  ok(v.frames.length === 48,
    "densify: loop = trailing loopCap slice (48 of 60; got " + v.frames.length + ")");
  ok(v.frames[v.frameIdx] === curStamp,
    "densify: merge preserved the CURRENT stamp (no mid-play index remap)");
  ok(map.removedLayers.some((id) => id === irSid(stampFor(0))),
    "densify: sources outside the window were torn down");
  await settle();
  ok(!events.slice(evBeforeDensify).some((e) => e.kind === "loading" || e.kind === "loaded"),
    "densify: background merge is QUIET (no toasts)");
  const mountedNow = MANIFESTS["https://x/sat/g/conus/ir/latest_times.json"].times
    .filter((s) => map.getLayer(irSid(s)));
  ok(mountedNow.length === 48, "densify: all 48 in-window frames mounted after settle");

  // ---- 9. loop cap ------------------------------------------------------------
  const capStamp = v.frames[v.frameIdx];
  v.setLoopCap(12);
  await settle();
  ok(v.frames.length === 12, "cap: setLoopCap(12) re-slices the loop");
  ok(v.frames[v.frameIdx] === capStamp || v.frameIdx === 0,
    "cap: current stamp preserved (or clamped to nearest surviving frame)");
  const resident = Object.keys(map.sources).filter((id) => id.indexOf("sat/g/conus/ir-") === 0);
  ok(resident.length === 12, "cap: residency shrank with the cap (got " + resident.length + ")");

  console.log("\n" + (failures ? "FAILED: " + failures + " check(s)" : "ALL CHECKS PASSED"));
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error("HARNESS ERROR:", e); process.exit(2); });
