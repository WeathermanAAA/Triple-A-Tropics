// jsdom harness for the suite switcher + derived super ensembles in
// models/enscenters.js:
//   * suite row (ECMWF / NOAA / Google / All models) over a per-suite model row,
//     data-driven from the manifest `suite` field with the fallback map for older
//     entries; a registry model with no manifest entry (WN3 pre-launch) shows as a
//     disabled chip and never breaks the viewer; it enables on the next poll once
//     it publishes.
//   * "Google super ensemble" / "All-model super ensemble": pooled from the
//     constituent cycle JSONs (lagged <= 6 h run used + disclosed), burned-in
//     header keeps init / F-hour / valid and adds the "Derived: pooled ..." line,
//     pooled tracks feed the mean / plume overlay.
//
//   node enscenters_suites_smoke.cjs <enscenters.js>
"use strict";
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const [, , JS] = process.argv;
const REGIONS = path.join(path.dirname(JS), "regions.js");

const C12 = "2026100612", C06 = "2026100606";
// an OLDER manifest: fnv3/genc carry no `suite` (fallback map must place them);
// ecens/gefs carry it. No wnv3 entry at all.
const manifest = { schema_version: 1, default_model: "ecens", models: [
  { slug: "ecens", label: "ECMWF ENS", suite: "ecmwf", cycles: [C06], latest: C06 },
  { slug: "ecaie", label: "AIFS-ENS", cycles: [C12, C06], latest: C12 },
  { slug: "gefs", label: "GEFS", suite: "noaa", cycles: [C12, C06], latest: C12 },
  { slug: "fnv3", label: "Google FNV3 (50)", cycles: [C12, C06], latest: C12,
    tracks_versions: { [C12]: "tf" } },
  { slug: "genc", label: "Google GenCast", cycles: [C06], latest: C06,
    tracks_versions: { [C06]: "tg" } },
] };

const N = { ecens: 3, ecaie: 3, gefs: 2, fnv3: 4, genc: 5, wnv3: 6 };
function cycleDoc(slug, cyc) {
  const STEPS = (slug === "ecens") ? [0, 3, 6, 9, 12, 18, 24] : [0, 6, 12, 18, 24];
  const hh = cyc.slice(8, 10);
  return { schema_version: 1, model: slug, model_label: slug, init_cycle: cyc,
    init_time: "2026-10-06T" + hh + ":00:00Z", run_steps: STEPS, n_members: N[slug],
    attribution: slug + " data", source: /fnv3|genc|wnv3/.test(slug) ? "track_csv" : undefined,
    pressure_bins: [{ key: "gt1000", label: ">1000 hPa", lo: 1000, hi: null },
                    { key: "p990_1000", label: "990 to 1000 hPa", lo: 990, hi: 1000 }],
    members: Array.from({ length: N[slug] }, (_, i) => ({ id: "M" + i, label: "Member " + i,
      centers: STEPS.map((s) => [s, 18 + i * 0.1, -55 - s / 6, 1004 - s / 3, 30 + s]) })) };
}
function tracksDoc(slug, n) {
  const steps = [0, 6, 12, 18, 24];
  const pl = (v) => ({ lead: steps, min: steps.map(() => v - 10), p10: steps.map(() => v - 8),
    p25: steps.map(() => v - 4), p50: steps.map(() => v), p75: steps.map(() => v + 4),
    p90: steps.map(() => v + 8), max: steps.map(() => v + 10), n: steps.map(() => n) });
  return { n_members: n,
    members: Array.from({ length: n }, (_, i) => ({ id: "M" + i, tracks: [steps.map((s) => [s, 18, -55 - s / 6, 1000, 40])] })),
    clusters: [{ id: 0, members: Array.from({ length: n }, (_, i) => "M" + i), member_count: n,
      coverage_fraction: 1, low_confidence: false, population: n,
      mean_track: steps.map((s) => [s, 18, -55 - s / 6, n]),
      envelope: steps.map((s) => ({ step: s, n, mean_lat: 18, mean_lon: -55 - s / 6, cov_km: [[900, 0], [0, 900]] })),
      plume: { vmax: pl(slug === "fnv3" ? 60 : 90), mslp: pl(990) } }] };
}

const HTML = `<!doctype html><html><body>
<div id="enscenters-viewer" tabindex="0"><div id="enscenters-mapframe">
<canvas id="enscenters-canvas" width="900" height="560"></canvas>
<div id="enscenters-tooltip"></div><div id="enscenters-status" style="display:none"><span></span></div></div>
<div class="ens-controlbar"><button id="enscenters-region-btn"><span id="enscenters-region-label"></span></button>
<div class="ens-modelgroup">
<div class="ens-suiterow"><div id="enscenters-suites" class="hafs-seg-group"></div></div>
<div class="ens-modelrow"><div id="enscenters-models" class="hafs-seg-group"></div></div></div>
<button id="enscenters-step-back"></button><button id="enscenters-play"></button>
<button id="enscenters-step-fwd"></button><button id="enscenters-trail"></button>
<button id="enscenters-mean" style="display:none"></button>
<span id="enscenters-fhour"></span><span id="enscenters-valid"></span>
<select id="enscenters-speed"></select><select id="enscenters-run"></select></div>
<input id="enscenters-scrub" class="ens-scrub" type="range" min="0" max="0" value="0">
<p id="enscenters-caption" class="ens-caption" data-default="default caption">default caption</p>
<div id="enscenters-empty" style="display:none"></div></div></body></html>`;

const dom = new JSDOM(HTML, { runScripts: "outside-only", url: "https://triple-a-tropics.com/models/" });
const win = dom.window;
let fills = [];
const fake2d = new Proxy({}, { get(_t, k) {
  if (k === "canvas") return { width: 0, height: 0 };
  if (k === "measureText") return (s) => ({ width: String(s == null ? "" : s).length * 6 });
  if (k === "fillText") return (s) => { fills.push(String(s)); };
  return typeof k === "string" ? () => {} : undefined;
}, set() { return true; } });
win.HTMLCanvasElement.prototype.getContext = function () { return fake2d; };
win.requestAnimationFrame = function () { return 0; };
win.cancelAnimationFrame = function () {};
win.ResizeObserver = function () { this.observe = function () {}; };
win.devicePixelRatio = 1;
try { win.localStorage.clear(); } catch (e) {}

let liveManifest = manifest;
const fetched = [];
win.fetch = function (url) {
  fetched.push(url);
  let body;
  if (/manifest\.json/.test(url)) body = liveManifest;
  else if (/\.geojson/.test(url)) body = { type: "FeatureCollection", features: [] };
  else {
    const m = /enscenters\/([^/]+)\/(\d{10})(\.tracks)?\.json/.exec(url);
    if (!m) return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
    body = m[3] ? tracksDoc(m[1], N[m[1]]) : cycleDoc(m[1], m[2]);
  }
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(JSON.parse(JSON.stringify(body))) });
};
// drive ONE viewer: swallow the auto-boot DOMContentLoaded hook so the harness
// instance is the only one wired to the shared selector buttons
const _ael = win.document.addEventListener.bind(win.document);
win.document.addEventListener = (t, f, o) => { if (t !== "DOMContentLoaded") _ael(t, f, o); };
win.eval(fs.readFileSync(REGIONS, "utf8"));
win.eval(fs.readFileSync(JS, "utf8"));
const flush = async (n) => { for (let i = 0; i < (n || 8); i++) await new Promise((r) => setTimeout(r, 0)); };
const $ = (sel) => [...win.document.querySelectorAll(sel)];
const chips = (id) => $("#" + id + " button").map((b) => ({ label: b.textContent.trim(), slug: b.getAttribute("data-slug"),
  disabled: b.disabled, active: b.classList.contains("active") }));

(async () => {
  const out = {};
  const V = new win.EnsCentersViewer(win.document.getElementById("enscenters-viewer"));
  await flush();
  out.boot = { model: V.model, suite: V.suite, suites: chips("enscenters-suites"), models: chips("enscenters-models") };

  // Google suite: FNV3 + WN3 (disabled, no manifest entry) + GenCast + super
  win.document.querySelector('#enscenters-suites button[data-slug="google"]').click();
  await flush();
  out.google = { model: V.model, suite: V.suite, models: chips("enscenters-models") };

  // pick the Google super ensemble
  win.document.querySelector('#enscenters-models button[data-slug="super-google"]').click();
  await flush(16);
  const d = V.data; if (!d.pool) { process.stderr.write(JSON.stringify({out, model: V.model, status: V.dom.status.textContent, fetched})); process.exit(1); }
  out.superGoogle = { model: V.model, label: d.model_label, n_members: d.n_members,
    pool: d.pool.models.map((m) => [m.slug, m.cycle, m.lagH]), dropped: d.pool.dropped.map((x) => x.slug),
    method: d.pool.method_version, runOptions: [...V.dom.run.options].map((o) => o.value),
    caption: V.dom.caption.textContent, steps: V.steps };
  fills = []; V._show(1);
  out.superGoogle.header = fills.join(" | ");
  // mean overlay on pooled tracks (fnv3 12Z + genc 06Z shifted 6 h)
  V._setMean(true);
  await flush(16);
  const cl = V.tracks && V.tracks.clusters;
  out.superGoogle.tracksReady = V.tracksReady();
  out.superGoogle.meanVisible = V.dom.mean.style.display !== "none";
  out.superGoogle.clusters = cl ? cl.map((c) => ({ n_models: c.n_models, member_count: c.member_count, models: c.models,
    p50_0: c.plume.vmax.p50[0], firstStep: c.mean_track[0][0] })) : null;
  out.superGoogle.tracksN = V.tracks ? V.tracks.n_members : null;
  fills = []; V._show(1);
  out.superGoogle.plumeSub = fills.filter((s) => /of \d+ members/.test(s));

  // All models suite -> only the all-model super ensemble; pools every model
  win.document.querySelector('#enscenters-suites button[data-slug="all"]').click();
  await flush(16);
  out.all = { model: V.model, models: chips("enscenters-models"),
    pool: V.data.pool.models.map((m) => [m.slug, m.lagH]), n_members: V.data.n_members,
    steps: V.steps, dropped: V.data.pool.dropped.map((x) => x.slug) };

  // back to a single model keeps the per-model path intact
  win.document.querySelector('#enscenters-suites button[data-slug="noaa"]').click();
  await flush(8);
  out.noaa = { model: V.model, pool: !!V.data.pool, label: V.data.model_label };

  // WN3 publishes -> the next poll enables its chip (no reload) and the Google
  // super ensemble pools three models at 12Z
  liveManifest = JSON.parse(JSON.stringify(manifest));
  liveManifest.models.splice(4, 0, { slug: "wnv3", label: "Google WN3 (64)", suite: "google", cycles: [C12], latest: C12 });
  V._poll(); await flush(8);
  win.document.querySelector('#enscenters-suites button[data-slug="google"]').click();
  await flush(8);
  out.afterPoll = { models: chips("enscenters-models") };
  win.document.querySelector('#enscenters-models button[data-slug="super-google"]').click();
  await flush(16);
  out.afterPoll.pool = V.data.pool.models.map((m) => [m.slug, m.lagH]);
  out.afterPoll.n_members = V.data.n_members;
  win.document.querySelector('#enscenters-models button[data-slug="wnv3"]').click();
  await flush(8);
  out.afterPoll.wnModel = V.model; out.afterPoll.wnMembers = V.data.n_members;
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
