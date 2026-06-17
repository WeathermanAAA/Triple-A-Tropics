// jsdom harness for Stage 2b OBS-vs-envelope (models/enscenters.js): match a live
// observed system to its ensemble cluster, rank it within the envelope, draw the
// focal marker, and degrade cleanly (no active system / no tracks / feed fail).
// Also asserts the obs feed is the sanctioned global_storms.geojson (isolation).
//
//   node enscenters_obs_smoke.cjs <enscenters.js>
"use strict";
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");
const [, , JS] = process.argv;
const REGIONS = path.join(path.dirname(JS), "regions.js");
const CYC = "2026061418";
const STEPS = [0, 24, 48, 72, 96];

function centers(lat, lon) { return STEPS.map((s) => [s, lat, lon, 1000 - s * 0.3, 20 + s * 0.3]); }
function cycleDoc(model) {
  return {
    schema_version: 1, model, model_label: model.toUpperCase(), init_time: "2026-06-14T18:00:00Z",
    init_cycle: CYC, cycle_hour: 18, generated_at: "2026-06-15T00:00:00Z", attribution: "t", grid: "0.25",
    run_steps: STEPS, n_members: 2, n_centers: 10, detect: { closed_threshold_hpa: 2 },
    center_fields: ["step_h", "lat", "lon", "mslp_hpa", "vmax_kt"],
    pressure_bins: [{ key: "gt1000", label: ">1000", lo: 1000, hi: null }, { key: "p990_1000", label: "990-1000", lo: 990, hi: 1000 }, { key: "p970_990", label: "970-990", lo: 970, hi: 990 }, { key: "p950_970", label: "950-970", lo: 950, hi: 970 }, { key: "lt950", label: "<950", lo: null, hi: 950 }],
    members: [{ id: "CTL", label: "C", peak: { mslp_hpa: 980, vmax_kt: 50, lat: -15, lon: 170, step_h: 0 }, n_centers: 5, centers: centers(-15, 170) },
              { id: "P01", label: "P", peak: { mslp_hpa: 985, vmax_kt: 45, lat: -15, lon: 172, step_h: 0 }, n_centers: 5, centers: centers(-15, 172) }],
  };
}
function circlePoly(lat, lon, r) { const a = []; for (let k = 0; k < 8; k++) { const t = 2 * Math.PI * k / 8; a.push([lat + r * Math.sin(t), lon + r * Math.cos(t)]); } return a; }
function meanTrack(lat0, lon0, dlat, dlon) { return STEPS.map((s, i) => [s, lat0 + dlat * i, lon0 + dlon * i, 990 - s * 0.3, 25 + s * 0.4]); }
function envelope(mt) { return mt.map((p) => ({ step: p[0], n: 18, mean_lat: p[1], mean_lon: p[2], cov_km: [[2500, 0], [0, 2500]], ell50: { a_km: 60, b_km: 60, bearing_deg: 0, poly: circlePoly(p[1], p[2], 0.6) }, ell90: { a_km: 110, b_km: 110, bearing_deg: 0, poly: circlePoly(p[1], p[2], 1.1) } })); }
function plume() { const L = STEPS; return { vmax: { lead: L, p10: L.map(() => 20), p25: L.map(() => 24), p50: L.map(() => 28), p75: L.map(() => 33), p90: L.map(() => 40), min: L.map(() => 18), max: L.map(() => 45), n: L.map(() => 18) }, mslp: { lead: L, p10: L.map(() => 1004), p25: L.map(() => 1001), p50: L.map(() => 998), p75: L.map(() => 995), p90: L.map(() => 992), min: L.map(() => 1006), max: L.map(() => 990), n: L.map(() => 18) } }; }
const mtDate = meanTrack(-15, 170, -3, 6);   // dateline-ward S. Pacific system
const mtAtl = meanTrack(20, -60, 1, -1);     // North Atlantic system
const tracksDoc = {
  schema_version: 1, model: "ecens", init_cycle: CYC, generated_at: "g", source_kind: "self_detect",
  spacing_h: 24, n_members: 2, n_member_tracks: 2, n_clusters: 2,
  members: [{ id: "CTL", tracks: [centers(-15, 170)] }, { id: "P01", tracks: [centers(-15, 172)] }],
  clusters: [
    { id: 0, members: ["CTL", "P01"], member_count: 20, coverage_fraction: 0.9, population: 22, low_confidence: false, genesis: { lat: -15, lon: 170, step: 0 }, mean_track: mtDate, plume: plume(), envelope: envelope(mtDate) },
    { id: 1, members: ["A", "B"], member_count: 15, coverage_fraction: 0.7, population: 16, low_confidence: false, genesis: { lat: 20, lon: -60, step: 0 }, mean_track: mtAtl, plume: plume(), envelope: envelope(mtAtl) },
  ],
};
const manifest = {
  schema_version: 1, generated_at: "g", default_model: "ecens",
  models: [
    { slug: "ecens", label: "ECMWF ENS", cycles: [CYC], latest: CYC, cycle_versions: { [CYC]: "cv" }, tracks_versions: { [CYC]: "tv" } },
    { slug: "noend", label: "No Tracks", cycles: [CYC], latest: CYC, cycle_versions: { [CYC]: "cv" } },
  ],
};
// global_storms.geojson: an invest matched to the dateline cluster (lead ~6h -> bucket
// 0 near -15,170), a far invest (no match), and an ACTIVE named storm via track is_active.
const obsFeed = {
  type: "FeatureCollection", features: [
    { type: "Point", geometry: { type: "Point", coordinates: [170.5, -15.5] }, properties: { kind: "active_marker", marker_type: "invest_x", storm_id: "INV_A", name: "90P", designation: "90P", current_intensity_kt: 30, current_category: "TD", last_fix: "2026-06-15T00:00:00" } },
    { type: "Point", geometry: { type: "Point", coordinates: [150.0, 41.0] }, properties: { kind: "active_marker", marker_type: "invest_x", storm_id: "INV_B", name: "91W", designation: "91W", current_intensity_kt: 25, last_fix: "2026-06-15T00:00:00" } },
    { type: "Feature", geometry: { type: "LineString", coordinates: [[171, -14], [171.5, -15]] }, properties: { kind: "track", is_active: true, is_invest: false, storm_id: "STM_S", name: "PAULA" } },
    { type: "Point", geometry: { type: "Point", coordinates: [171.5, -15.0] }, properties: { kind: "observation", storm_id: "STM_S", storm_name: "PAULA", intensity_kt: 55, time_iso: "2026-06-15T00:00:00" } },
    { type: "Point", geometry: { type: "Point", coordinates: [171.0, -14.0] }, properties: { kind: "observation", storm_id: "STM_S", storm_name: "PAULA", intensity_kt: 45, time_iso: "2026-06-14T18:00:00" } },
  ],
};

const HTML = `<!doctype html><html><body><div id="enscenters-viewer" tabindex="0">
<div id="enscenters-mapframe"><canvas id="enscenters-canvas" width="900" height="560"></canvas>
<div id="enscenters-tooltip"></div><div id="enscenters-status" style="display:none"><span></span></div></div>
<div class="ens-controlbar"><button id="enscenters-region-btn"><span id="enscenters-region-label"></span></button>
<div class="ens-modelgroup"><div id="enscenters-models" class="hafs-seg-group"></div></div>
<button id="enscenters-step-back"></button><button id="enscenters-play"></button><button id="enscenters-step-fwd"></button>
<button id="enscenters-trail"></button><button id="enscenters-style" style="display:none"></button>
<button id="enscenters-mean" style="display:none"></button><button id="enscenters-obs" style="display:none"></button>
<span id="enscenters-fhour"></span><span id="enscenters-valid"></span>
<select id="enscenters-speed"></select><select id="enscenters-run"></select></div>
<input id="enscenters-scrub" class="ens-scrub" type="range" min="0" max="0" value="0">
<p id="enscenters-caption" class="ens-caption" data-default="x"></p><div id="enscenters-empty" style="display:none"></div>
</div></body></html>`;

const dom = new JSDOM(HTML, { runScripts: "outside-only", url: "https://triple-a-tropics.com/models/" });
const win = dom.window;
const fake2d = new Proxy({}, { get(_t, k) { if (k === "canvas") return { width: 0, height: 0 }; if (k === "measureText") return (s) => ({ width: String(s == null ? "" : s).length * 6 }); return typeof k === "string" ? () => {} : undefined; }, set() { return true; } });
win.HTMLCanvasElement.prototype.getContext = function () { return fake2d; };
win.requestAnimationFrame = () => 0; win.cancelAnimationFrame = () => {};
win.ResizeObserver = function () { this.observe = () => {}; }; win.devicePixelRatio = 1;
try { win.localStorage.clear(); } catch (e) {}
const EMPTY_GEO = { type: "FeatureCollection", features: [] };
const fetched = [];
let failObs = false;
win.fetch = function (url) {
  fetched.push(url); let body, ok = true;
  if (/manifest\.json/.test(url)) body = manifest;
  else if (/\.geojson/.test(url) && /global_storms/.test(url)) { if (failObs) { ok = false; body = {}; } else body = obsFeed; }
  else if (/\.geojson/.test(url)) body = EMPTY_GEO;
  else if (/ecens\/.*\.tracks\.json/.test(url)) body = tracksDoc;
  else if (/\/(ecens|noend)\/.*\.json/.test(url)) body = cycleDoc(/noend/.test(url) ? "noend" : "ecens");
  else body = {};
  return Promise.resolve({ ok, status: ok ? 200 : 404, json: () => Promise.resolve(body) });
};
win.eval(fs.readFileSync(REGIONS, "utf8"));
win.eval(fs.readFileSync(JS, "utf8"));
const flush = () => new Promise((r) => setTimeout(r, 0));
const vis = (b) => b && b.style.display !== "none";

(async () => {
  const out = {};
  const root = win.document.getElementById("enscenters-viewer");
  const V = new win.EnsCentersViewer(root);
  for (let i = 0; i < 8; i++) await flush();
  V._selectRegion("global"); for (let i = 0; i < 3; i++) await flush();

  out.obs_btn_visible = vis(V.dom.obs);

  // spy on draw passes (envelope ellipses retired - obs draws just the marker + note)
  const calls = { markers: 0, note: 0 };
  const o = { m: V._drawObsMarkers, n: V._drawObsNote };
  V._drawObsMarkers = function () { calls.markers++; return o.m.apply(this, arguments); };
  V._drawObsNote = function () { calls.note++; return o.n.apply(this, arguments); };

  // turn obs ON
  V._setObs(true);
  for (let i = 0; i < 8; i++) await flush();
  out.ls_obs = win.localStorage.getItem("ens.obs");
  out.obs_fetched_url = fetched.filter((u) => /global_storms\.geojson/.test(u)).slice(-1)[0] || null;
  out.obs_fetched_any_floater = fetched.some((u) => /floater/i.test(u));   // must be false (isolation)
  calls.markers = calls.note = 0;
  V._show(V.idx);
  out.markers_drawn = calls.markers;

  // resolve directly: 3 active systems in global view; INV_A + STM_S matched, INV_B not
  const res = V._resolveObs();
  out.resolved_n = res.length;
  const byId = {}; res.forEach((r) => { byId[r.obs.id] = r; });
  out.invA_matched = !!(byId["INV_A"] && byId["INV_A"].match);
  out.invB_matched = !!(byId["INV_B"] && byId["INV_B"].match);
  out.stmS_matched = !!(byId["STM_S"] && byId["STM_S"].match);
  // rank for INV_A (near the dateline cluster mean): sane 0..100 + a compass side
  if (byId["INV_A"] && byId["INV_A"].match) {
    const m = byId["INV_A"].match;
    const rk = V._obsRank(m.cluster, m.step, byId["INV_A"].obs.lat, byId["INV_A"].obs.lon);
    out.invA_rank = rk ? { pct: rk.pct, side: rk.side, clusterGenesisLon: m.cluster.genesis.lon } : null;
  }

  // no-active case: crop to the Atlantic (all obs are Pacific) -> note, no markers
  V._selectRegion("atlantic"); for (let i = 0; i < 3; i++) await flush();
  calls.markers = calls.note = 0;
  V._show(V.idx);
  out.natl_resolved = V._resolveObs().length;
  out.natl_markers = calls.markers;
  out.natl_note = calls.note;

  // persistence: fresh viewer reads ens.obs
  const V2 = new win.EnsCentersViewer(root);
  out.persist_obs = V2.obsOn;
  for (let i = 0; i < 6; i++) await flush();

  // no-tracks model: obs toggle hidden, no throw
  let threw = false;
  try { V._selectModel("noend"); for (let i = 0; i < 8; i++) await flush(); V._show(V.idx); }
  catch (e) { threw = true; out.noend_err = String(e); }
  out.noend_obs_visible = vis(V.dom.obs);
  out.noend_threw = threw;

  // obs feed FAILS to load: clean no-op (empty + note), no throw
  let threw2 = false;
  failObs = true;
  try {
    const V3 = new win.EnsCentersViewer(root);
    for (let i = 0; i < 8; i++) await flush();
    V3._selectRegion("global"); for (let i = 0; i < 3; i++) await flush();
    V3._setObs(true);
    for (let i = 0; i < 8; i++) await flush();
    out.fail_obs_len = (V3.obs || []).length;     // [] after a failed fetch
    out.fail_threw = false;
  } catch (e) { threw2 = true; out.fail_err = String(e); }
  out.fail_threw = threw2;

  // REGRESSION (#3 obs marker doubling): a NAMED storm now carries BOTH an
  // active_marker ('hurricane' marker_type) AND an is_active track. It must yield
  // exactly ONE 'storm' entry (green glyph), not a storm+invest dupe; an invest
  // still yields exactly one 'invest' entry (red X).
  const dupFeed = { type: "FeatureCollection", features: [
    { type: "Point", geometry: { type: "Point", coordinates: [-60, 25] }, properties: { kind: "active_marker", marker_type: "hurricane", storm_id: "AL012026", name: "ARTHUR", current_intensity_kt: 45, last_fix: "2026-06-15T00:00:00" } },
    { type: "Feature", geometry: { type: "LineString", coordinates: [[-61, 24], [-60, 25]] }, properties: { kind: "track", is_active: true, is_invest: false, storm_id: "AL012026", name: "ARTHUR" } },
    { type: "Point", geometry: { type: "Point", coordinates: [-60, 25] }, properties: { kind: "observation", storm_id: "AL012026", storm_name: "ARTHUR", intensity_kt: 45, time_iso: "2026-06-15T00:00:00" } },
    { type: "Point", geometry: { type: "Point", coordinates: [-55, 20] }, properties: { kind: "active_marker", marker_type: "invest_x", storm_id: "INV_C", name: "92L", current_intensity_kt: 25, last_fix: "2026-06-15T00:00:00" } },
  ] };
  const dup = V._extractActiveObs(dupFeed);
  out.dup_total = dup.length;                                                // 2 (no duplicate)
  out.dup_arthur_count = dup.filter((o) => o.id === "AL012026").length;      // 1
  out.dup_arthur_kind = (dup.find((o) => o.id === "AL012026") || {}).kind;   // 'storm'
  out.dup_invC_count = dup.filter((o) => o.id === "INV_C").length;           // 1
  out.dup_invC_kind = (dup.find((o) => o.id === "INV_C") || {}).kind;        // 'invest'

  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})().catch((e) => { process.stderr.write(String((e && e.stack) || e)); process.exit(1); });
