// Unit harness for the derived super-ensemble pooling helpers in
// models/enscenters.js (EnsCentersViewer.Pool). Pure functions: no DOM, no canvas,
// so the file is evaluated in a bare vm context with a stub `window`.
//
//   node enscenters_pool_smoke.cjs <enscenters.js>
// Prints one JSON object of measured results; the Python test asserts on it.
"use strict";
const fs = require("fs");
const vm = require("vm");

const [, , JS] = process.argv;
const ctx = { window: {}, console };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(JS, "utf8"), ctx);
const P = ctx.window.EnsCentersViewer.Pool;
const out = {};

// ---- resolvePool / poolCycles: same-cycle first, else <= 6 h older, else drop ----
const entries = [
  { slug: "fnv3", label: "Google FNV3 (50)", cycles: ["2026100612", "2026100606"] },
  { slug: "wnv3", label: "Google WN3 (64)", cycles: ["2026100606", "2026100600"] },
  { slug: "genc", label: "Google GenCast", cycles: ["2026100600"] },
];
const r12 = P.resolvePool(entries, "2026100612", 6);
out.resolve12 = {
  used: r12.used.map((u) => [u.slug, u.cycle, u.lagH]),
  dropped: r12.dropped.map((d) => d.slug),
};
out.poolCycles = P.poolCycles(entries, 2, 6);

// ---- commonSteps: off-cadence steps dropped where a covering model lacks them ----
out.commonSteps = P.commonSteps([[0, 3, 6, 9, 12], [0, 6, 12, 18]]);

// ---- poolCenters: tags, valid-time shift of a lagged run, member counts ----
function doc(slug, n, steps, src) {
  return {
    model: slug, init_time: "x", run_steps: steps, attribution: slug + " attr",
    source: src || "track_csv",
    pressure_bins: [{ key: "gt1000", label: ">1000 hPa", lo: 1000, hi: null }],
    members: Array.from({ length: n }, (_, i) => ({
      id: "M" + String(i).padStart(2, "0"), label: "Member " + i,
      centers: steps.map((s) => [s, 15 + i * 0.01, -50, 1000 - s / 6, 30 + s / 6]),
    })),
  };
}
const docs = { fnv3: doc("fnv3", 50, [0, 6, 12, 18]), wnv3: doc("wnv3", 64, [0, 6, 12, 18, 24]) };
const res = P.resolvePool(entries.slice(0, 2), "2026100612", 6);
const pooled = P.poolCenters(docs, res, P.SUPERS["super-google"]);
const wn = pooled.members.filter((m) => m.model === "wnv3");
out.pooled = {
  n_members: pooled.n_members, model: pooled.model, label: pooled.model_label,
  init_cycle: pooled.init_cycle, init_time: pooled.init_time, run_steps: pooled.run_steps,
  method: pooled.pool.method_version, poolModels: pooled.pool.models.map((m) => [m.tag, m.lagH, m.n_members]),
  firstIds: [pooled.members[0].id, wn[0].id], firstTags: [pooled.members[0].tag, wn[0].tag],
  // WN3's run is 6 h older: its F006 center is valid at the pooled F000
  wnFirstCenter: wn[0].centers[0],
  wnSteps: wn[0].centers.map((c) => c[0]),
  caption: pooled.caption,
};

// ---- mixQuantile: equal weight PER MODEL, not per member ----
// model A (50 members) ~ 90..110 kt, model B (5 members) ~ 40..60 kt. Equal model
// weights put the pooled p25 at B's median (50) and p75 at A's median (100); a
// per-member pool would put p25 near 93.
const A = [90, 92, 95, 100, 105, 108, 110], B = [40, 42, 45, 50, 55, 58, 60];
out.mix = { p25: P.mixQuantile([A, B], 0.25), p75: P.mixQuantile([A, B], 0.75),
            sameP50: P.mixQuantile([A, A], 0.5) };

// ---- clusters: matching, dateline-safe mean, mixture envelope, plume ----
function cl(model, lat, lon, steps, members, vmed, cov) {
  const pl = (v) => ({ lead: steps, min: steps.map(() => v - 10), p10: steps.map(() => v - 8),
    p25: steps.map(() => v - 5), p50: steps.map(() => v), p75: steps.map(() => v + 5),
    p90: steps.map(() => v + 8), max: steps.map(() => v + 10), n: steps.map(() => members) });
  return { model, member_count: members, coverage_fraction: members / 50, low_confidence: false, population: members,
    members: Array.from({ length: members }, (_, i) => model + i),
    mean_track: steps.map((s) => [s, lat, lon, members]),
    envelope: steps.map((s) => ({ step: s, n: members, mean_lat: lat, mean_lon: lon, cov_km: cov || [[0, 0], [0, 0]] })),
    plume: { vmax: pl(vmed), mslp: pl(1000 - vmed / 2) } };
}
// dateline pair: 10N 170E (A) and 10N 170W (B) -> pooled mean on the dateline,
// not the Greenwich side
const g = P.poolGroup([cl("fnv3", 10, 170, [0, 6, 12], 40, 60), cl("wnv3", 10, -170, [0, 6, 12], 10, 100)], 0);
out.dateline = { lat: g.mean_track[0][1], lon: g.mean_track[0][2], n: g.mean_track[0][3],
                 member_count: g.member_count, n_models: g.n_models, p50: g.plume.vmax.p50[0] };
// envelope: two point-mass models offset ~ +/- d km east-west from their mean ->
// mixture variance (east) = d^2, north variance 0
const e = P.poolGroup([cl("fnv3", 0, -1, [0, 6], 20, 50), cl("wnv3", 0, 1, [0, 6], 20, 50)], 1).envelope[0];
out.envelope = { mean_lat: e.mean_lat, mean_lon: e.mean_lon, cxx: e.cov_km[0][0], cyy: e.cov_km[1][1],
                 cxy: e.cov_km[0][1], n_models: e.n_models };
// matching: two nearby Atlantic clusters (different models) group; a far WPac
// cluster and a second same-model cluster stay apart
const groups = P.matchClusters([
  cl("fnv3", 15, -50, [0, 6, 12], 40, 60),
  cl("wnv3", 16, -51, [0, 6, 12], 30, 70),
  cl("wnv3", 15, 130, [0, 6, 12], 35, 80),
  cl("fnv3", 15.5, -50.5, [0, 6, 12], 5, 40),
]);
out.groups = groups.map((gr) => gr.map((c) => c.model + "@" + c.mean_track[0][2]).sort());

// ---- poolTracks: tagged members, lag shift, pooled clusters ----
function tdoc(slug, n, lat, lon) {
  const steps = [0, 6, 12, 18];
  return { n_members: n,
    members: Array.from({ length: n }, (_, i) => ({ id: "M" + i, tracks: [steps.map((s) => [s, lat, lon - s / 6, 1000, 40])] })),
    clusters: [cl(slug, lat, lon, steps, n, 50)] };
}
const pt = P.poolTracks({ fnv3: tdoc("fnv3", 50, 15, -50), wnv3: tdoc("wnv3", 64, 15.2, -50.2) }, res);
const wnT = pt.members.find((m) => m.model === "wnv3");
out.tracks = { n_members: pt.n_members, n_clusters: pt.clusters.length, n_models: pt.clusters[0].n_models,
               member_count: pt.clusters[0].member_count, method: pt.method_version,
               wnFirstStep: wnT.tracks[0][0][0], wnId: wnT.id, models: pt.tracks_models };

process.stdout.write(JSON.stringify(out));
