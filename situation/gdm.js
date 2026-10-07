/* Situation Room: Google DeepMind Weather Lab ensembles (FNV3, WeatherNext 3, GenCast, FNV3 1000-member) and the
   derived Google super ensemble (FNV3 + WN3 + GenCast, equal weight per model).
   Data: models/gdmdiag/index.json + <cycle>/<ATCF>.json on the CDN (gdmdiag/build.py precomputes every statistic).
   Feeds three places: the Models tab's ensemble layer (members + mean + position ellipses), the Ensembles chart, and
   the Google diagnostics card under the stage; wind probabilities draw on the main map as their own layer.
   Loaded after sr.js and uses its globals (SID, D, MAP, pos, units, time helpers). */
"use strict";
const GDM = { idx: null, doc: null, id: null, cyc: null, t: 0, busy: null, suite: "gdm", tab: "over", metric: "v", dvk: "24", dvh: "48", wk: "34", wh: "120" };
const GDM_CDN = "https://cdn.triple-a-tropics.com/models/gdmdiag/";
const GSUITES = [["fnv3", "FNV3", "FNV3 (50)"], ["wnv3", "WN3", "WeatherNext 3 (64)"], ["genc", "GenCast", "GenCast (50)"], ["fnv3x", "FNV3 1000", "FNV3 1000-member"], ["gdm", "Google super", "Google super ensemble"]];
const GCOL = { fnv3: "#5dd3ff", wnv3: "#c084fc", genc: "#7ee08a", fnv3x: "#ff6b9a", gdm: "#ffb83a" };
const GNAME = Object.fromEntries(GSUITES.map(s => [s[0], s[1]]));
const gkm = (la1, lo1, la2, lo2) => { const r = Math.PI / 180, dl = (((lo2 - lo1 + 540) % 360) - 180) * r, a = Math.sin((la2 - la1) * r / 2) ** 2 + Math.cos(la1 * r) * Math.cos(la2 * r) * Math.sin(dl / 2) ** 2; return 12742 * Math.asin(Math.min(1, Math.sqrt(a))); };

/* ---------------- data ---------------- */
/* Weather Lab keeps an invest's id for a while after NHC/JTWC names it (Nine was AL92 there), so after the exact id
   the storm is matched by position: same basin, analysis centre within 400 km of ours */
function gdmMatch(storms) {
  const want = SID.toUpperCase(); if (storms[want]) return want;
  const [lo, la] = pos(); let best = null;
  for (const [id, s] of Object.entries(storms)) {
    if (id.slice(0, 2) !== want.slice(0, 2)) continue;
    const m = (s.suites.gdm || s.suites.fnv3 || Object.values(s.suites)[0] || {}).mean?.[0]; if (!m) continue;
    const d = gkm(m[0], m[1], la, lo); if (d < 400 && (!best || d < best[0])) best = [d, id];
  }
  return best ? best[1] : null;
}
async function gdmLoad(force) {
  if (!force && GDM.t && Date.now() - GDM.t < 6e5) return GDM.doc;
  if (GDM.busy) return GDM.busy;
  GDM.busy = (async () => {
    try {
      const ix = await (await fetch(GDM_CDN + "index.json?t=" + Math.floor(Date.now() / 3e5))).json(); GDM.idx = ix;
      let pick = null;
      for (const c of Object.keys(ix.cycles).sort().reverse()) { const id = gdmMatch(ix.cycles[c].storms); if (id) { pick = [c, id]; break; } }
      if (!pick) GDM.doc = null;
      else if (!GDM.doc || GDM.cyc !== pick[0] || GDM.id !== pick[1]) {
        GDM.doc = await (await fetch(`${GDM_CDN}${pick[0]}/${pick[1]}.json?v=${encodeURIComponent(ix.cycles[pick[0]].built || "")}`)).json();
        GDM.cyc = pick[0]; GDM.id = pick[1];
      }
    } catch (e) { GDM.doc = GDM.doc || null; }
    GDM.t = Date.now(); GDM.busy = null; return GDM.doc;
  })();
  return GDM.busy;
}
const gdmInit = () => GDM.doc ? Date.UTC(+GDM.cyc.slice(0, 4), +GDM.cyc.slice(4, 6) - 1, +GDM.cyc.slice(6, 8), +GDM.cyc.slice(8, 10)) : 0;
/* member tracks for the map: [{c, pts:[[h, lat, lon]]}]; the super ensemble shows every pooled model's members */
function gdmTracks(k) {
  const d = GDM.doc; if (!d || !d.suites[k]) return null; const S = d.suites[k];
  const of = x => (x?.members || []).map(m => ({ c: GCOL[m.m] || GCOL[k], pts: m.t.map(p => [p[0], p[1], p[2]]) }));
  const tracks = k === "gdm" ? (S.pooled || []).flatMap(p => of(d.suites[p])) : of(S);
  const mean = S.mean.map((p, i) => p ? [S.leads[i], p[0], p[1]] : null).filter(Boolean);
  return { tracks, mean, ell: S.ellipses, label: S.label, cyc: GDM.cyc, derived: !!S.derived, pooled: S.pooled, n: S.n };
}
/* Ensembles-chart sources (time axis = valid ms); the super ensemble's band and mean come from its equal-weight stats */
function gdmChartSrcs(metric, show) {
  const d = GDM.doc; if (!d) return [];
  const t0 = gdmInit(), col = metric === "vmax" ? 3 : 4, qk = metric === "vmax" ? "vmax_q" : "mslp_q", out = [];
  for (const [k] of GSUITES) {
    const S = d.suites[k]; if (!S || !show.has(k)) continue;
    const mems = k === "gdm" ? (S.pooled || []).flatMap(p => (d.suites[p]?.members || [])) : (S.members || []);
    const src = { key: k, label: `${S.label}${S.n ? ` (${S.n})` : ""}`, col: GCOL[k], mem: mems.map(m => m.t.map(p => [t0 + p[0] * 36e5, p[col]]).filter(p => p[1] != null)),
      memCol: k === "gdm" ? mems.map(m => GCOL[m.m]) : null };
    if (k === "gdm" || !S.members) {
      const ok = i => S[qk][i] && (S.alive[i] == null || S.alive[i] >= .1);
      src.stats = { mean: S.leads.map((h, i) => ok(i) ? [t0 + h * 36e5, metric === "vmax" ? (S.vmax_mean[i] ?? S[qk][i][3]) : S[qk][i][3]] : null).filter(Boolean),
        p10: S.leads.map((h, i) => ok(i) ? [t0 + h * 36e5, S[qk][i][1]] : null).filter(Boolean), p90: S.leads.map((h, i) => ok(i) ? [t0 + h * 36e5, S[qk][i][5]] : null).filter(Boolean) };
    }
    out.push(src);
  }
  return out;
}

/* ---------------- wind-probability layer on the main map ---------------- */
const b64u8 = s => { const b = atob(s), a = new Uint8Array(b.length); for (let i = 0; i < b.length; i++) a[i] = b.charCodeAt(i); return a; };
const PRAMP = [[5, "#b8e3ff"], [10, "#7cc3ea"], [20, "#3fa4ff"], [30, "#46c56a"], [40, "#c7e66c"], [50, "#ffe14d"], [60, "#ffb83a"], [70, "#ff7a2f"], [80, "#f5333c"], [90, "#b0186c"]];
const prgb = v => { let c = null; for (const x of PRAMP) if (v >= x[0]) c = x[1]; return c && [parseInt(c.slice(1, 3), 16), parseInt(c.slice(3, 5), 16), parseInt(c.slice(5, 7), 16)]; };
const GW = { cache: {} };
function gwindGrid() { const S = GDM.doc?.suites[GDM.suite]; return S?.grid ? S : GDM.doc?.suites.gdm?.grid ? GDM.doc.suites.gdm : null; }
function gwindImage() {
  const S = gwindGrid(); if (!S) return null; const g = S.grid, key = `${GDM.wk}_${GDM.wh}`; if (!g.p[key]) return null;
  const v = b64u8(g.p[key]), my = la => Math.log(Math.tan(Math.PI / 4 + la * Math.PI / 360)), b = g.bbox;
  const y0 = my(b[3]), y1 = my(b[1]), H = g.ny * 2, W = g.nx;
  const c = document.createElement("canvas"); c.width = W; c.height = H; const x = c.getContext("2d"), im = x.createImageData(W, H);
  for (let r = 0; r < H; r++) {   // rows resampled from equal-angle to equal-Mercator so the image source lines up
    const la = (2 * Math.atan(Math.exp(y0 + (r + .5) / H * (y1 - y0))) - Math.PI / 2) * 180 / Math.PI, sr = Math.floor((b[3] - la) / g.res);
    if (sr < 0 || sr >= g.ny) continue;
    for (let q = 0; q < W; q++) { const p = prgb(v[sr * W + q]); if (!p) continue; const o = (r * W + q) * 4; im.data[o] = p[0]; im.data[o + 1] = p[1]; im.data[o + 2] = p[2]; im.data[o + 3] = 205; }
  }
  x.putImageData(im, 0, 0);
  return { url: c.toDataURL(), coords: [[b[0], b[3]], [b[2], b[3]], [b[2], b[1]], [b[0], b[1]]], S, g };
}
function gwindReadout(e) {
  const S = gwindGrid(), out = $("gdRead"); if (!S || !out || !ON.has("gwind")) return;
  const g = S.grid, b = g.bbox; let lo = e.lngLat.lng; lo = b[0] + ((lo - b[0]) % 360 + 360) % 360;
  const c = Math.floor((lo - b[0]) / g.res), r = Math.floor((b[3] - e.lngLat.lat) / g.res);
  if (c < 0 || r < 0 || c >= g.nx || r >= g.ny) { out.innerHTML = "Point at the map for the odds there."; return; }
  const i = r * g.nx + c, cell = k => { const a = g.p[`${k}_${GDM.wh}`]; if (!a) return null; GW.cache[a.length + a.slice(0, 30)] ||= b64u8(a); return GW.cache[a.length + a.slice(0, 30)][i]; };
  const arr = g.arrival34 ? (GW.cache.arr34 && GW.cache.arrKey === g.arrival34.slice(0, 40) ? GW.cache.arr34 : (GW.cache.arrKey = g.arrival34.slice(0, 40), GW.cache.arr34 = b64u8(g.arrival34)))[i] : 255;
  out.innerHTML = `<b>${Math.abs(e.lngLat.lat).toFixed(1)}°${e.lngLat.lat < 0 ? "S" : "N"} ${Math.abs(e.lngLat.lng).toFixed(1)}°${e.lngLat.lng < 0 ? "W" : "E"}</b> · within ${GDM.wh} h: ` +
    ["34", "50", "64"].map(k => cell(k) != null ? `${k} kt <em>${cell(k)}%</em>` : "").filter(Boolean).join(" · ") + ` · centre within 120 km <em>${cell("c120") ?? "–"}%</em>` +
    (arr < 255 ? ` · 34-kt winds arrive ${dayhm(gdmInit() + arr * 2 * 36e5)} ${TZ} (median)` : "");
}

/* ---------------- SVG chart kit (room styling) ---------------- */
const GH = [0, 24, 48, 72, 96, 120, 144, 168, 192, 216, 240];
function GChart(o) {
  const W = o.w || 470, H = o.h || 210, P = { l: 40, r: 12, t: 26, b: 28 }, [x0, x1] = o.x, [y0, y1] = o.y;
  const X = v => P.l + (v - x0) / (x1 - x0) * (W - P.l - P.r), Y = v => H - P.b - (v - y0) / (y1 - y0) * (H - P.t - P.b);
  let s = `<svg viewBox="0 0 ${W} ${H}" class="gsvg"><text class="gt" x="${P.l}" y="15">${esc(o.title)}</text>${o.sub ? `<text class="gs" x="${W - P.r}" y="15" text-anchor="end">${esc(o.sub)}</text>` : ""}`;
  (o.yt || []).forEach(v => { s += `<line class="gg" x1="${P.l}" x2="${W - P.r}" y1="${Y(v)}" y2="${Y(v)}"/><text class="ga" x="${P.l - 5}" y="${Y(v) + 3}" text-anchor="end">${o.yf ? o.yf(v) : v}</text>`; });
  (o.xt || []).forEach(v => { s += `<line class="gg" x1="${X(v)}" x2="${X(v)}" y1="${P.t}" y2="${H - P.b}"/><text class="ga" x="${X(v)}" y="${H - P.b + 13}" text-anchor="middle">${o.xf ? o.xf(v) : v}</text>`; });
  if (o.yl) s += `<text class="ga" x="4" y="${P.t - 5}">${esc(o.yl)}</text>`;
  const seg = pts => { let d = "", pen = false; for (const p of pts) { if (p[1] == null || !isFinite(p[1])) { pen = false; continue; } d += (pen ? "L" : "M") + X(p[0]).toFixed(1) + " " + Y(p[1]).toFixed(1); pen = true; } return d; };
  return { X, Y, W, H, P,
    line(pts, c, w = 2, dash) { const d = seg(pts); if (d) s += `<path d="${d}" fill="none" stroke="${c}" stroke-width="${w}"${dash ? ` stroke-dasharray="${dash}"` : ""} stroke-linejoin="round"/>`; },
    band(lo, hi, c, op = .25) { const a = [], b = []; lo.forEach((p, i) => { if (p[1] != null && hi[i][1] != null) { a.push(p); b.unshift(hi[i]); } }); if (a.length > 1) s += `<path d="${seg(a)}L${seg(b).slice(1)}Z" fill="${c}" opacity="${op}"/>`; },
    bar(xa, xb, ya, yb, c, op) { s += `<rect x="${X(xa)}" y="${Y(yb)}" width="${Math.max(0, X(xb) - X(xa))}" height="${Math.max(0, Y(ya) - Y(yb))}" fill="${c}"${op != null ? ` opacity="${op}"` : ""}/>`; },
    text(x, y, t, c = "#cfd6e0", anc = "start") { s += `<text class="ga" x="${X(x)}" y="${Y(y)}" fill="${c}" text-anchor="${anc}">${esc(t)}</text>`; },
    raw(t) { s += t; },
    legend(items) { let x = P.l; for (const [t, c] of items) { s += `<rect x="${x}" y="${H - 9}" width="9" height="4" fill="${c}"/><text class="ga" x="${x + 12}" y="${H - 5}">${esc(t)}</text>`; x += 18 + t.length * 5.6; } },
    done: () => s + "</svg>" };
}
const gstep = r => r > 400 ? 100 : r > 160 ? 40 : r > 80 ? 20 : r > 40 ? 10 : r > 16 ? 5 : r > 6 ? 2 : 1;
const gpct = p => p == null ? "–" : (p > 0 && p < .005 ? "<1" : Math.round(p * 100)) + "%";
const GCAT = [["TD", 0, "#3fa4ff"], ["TS", 34, "#46c56a"], ["C1", 64, "#ffe14d"], ["C2", 83, "#ff9a2f"], ["C3", 96, "#f5333c"], ["C4", 113, "#e33ad4"], ["C5", 137, "#b03bff"]];
const gcatC = v => { let c = GCAT[0][2]; for (const x of GCAT) if (v >= x[1]) c = x[2]; return c; };
/* wind axes tick in the viewer's unit (settings) while the data stay in kt */
function windTicks(hi) { const f = WF().f, st = WF().step, t = []; for (let u = 0; u / f <= hi; u += st) t.push(u / f); return { yt: t, yf: v => Math.round(v * f) }; }
function gFan(S, key, title, unit, o = {}) {
  const q = S[key], L = S.leads; if (!q || !q.some(Boolean)) return "";
  const ok = i => q[i] && (S.alive[i] == null || S.alive[i] >= .1), all = [];
  q.forEach((r, i) => { if (ok(i)) all.push(r[0], r[6]); }); if (!all.length) return "";
  const lo = o.lo != null ? o.lo : Math.floor(Math.min(...all) / 10) * 10, hi = Math.ceil(Math.max(...all) / 10) * 10 + 5;
  let ax; if (o.wind) ax = windTicks(hi); else { const st = gstep(hi - lo), yt = []; for (let v = Math.ceil(lo / st) * st; v <= hi; v += st) yt.push(v); ax = { yt, yf: o.yf }; }
  const c = GChart({ title, sub: o.sub, x: [0, 240], y: [lo, hi], xt: GH, ...ax, yl: o.wind ? wl() : unit });
  const colq = i => L.map((h, k) => [h, ok(k) ? q[k][i] : null]), col = o.color || GCOL[GDM.suite];
  if (o.cats) GCAT.forEach((x, i) => { const a = x[1], b = GCAT[i + 1] ? GCAT[i + 1][1] : hi; if (a < hi && b > lo) c.bar(0, 240, Math.max(lo, a), Math.min(hi, b), x[2], .07); });
  c.band(colq(0), colq(6), col, .12); c.band(colq(1), colq(5), col, .22); c.band(colq(2), colq(4), col, .35); c.line(colq(3), "#fff", 2.3);
  if (o.mean) c.line(L.map((h, k) => [h, ok(k) ? o.mean[k] : null]), "#ffb83a", 1.8, "5 3");
  const cut = L.find((h, k) => q[k] && !ok(k)); if (cut != null) c.raw(`<text class="ga" x="${cut > 150 ? c.X(cut) - 4 : c.X(cut) + 4}" y="38" text-anchor="${cut > 150 ? "end" : "start"}">fewer than 10 % of members beyond here</text>`);
  c.legend([["P10-P90", col], ["P25-P75", col], ["median", "#fff"]].concat(o.mean ? [["mean", "#ffb83a"]] : []));
  return c.done();
}

/* ---------------- the diagnostics card ---------------- */
const GTABS = [["over", "Overview"], ["int", "Intensity"], ["chg", "Intensity change"], ["str", "Structure"], ["wind", "Wind risk"], ["land", "Landfall"], ["cmp", "Compare"], ["trend", "Trends"]];
const gLI = h => Math.round(h / 6);
const gvt = h => `${dayhm(gdmInit() + h * 36e5)} ${TZ}`;
async function gdmCard(force) {
  const el = $("cGdm"); if (!el) return;
  const d = await gdmLoad(force);
  if (!d) { el.innerHTML = `<div class="ch"><b>Google ensembles</b><span></span></div><div class="rnote">No Google DeepMind Weather Lab run tracks this storm yet. Weather Lab posts each 00, 06, 12 and 18 UTC run a few hours late.</div>`; return; }
  if (!d.suites[GDM.suite]) GDM.suite = d.suites.gdm ? "gdm" : Object.keys(d.suites)[0];
  const S = d.suites[GDM.suite];
  el.innerHTML = `<div class="ch"><span class="ctab">${GTABS.map(([k, t]) => `<button data-gt="${k}" class="${k === GDM.tab ? "on" : ""}">${t}</button>`).join("")}</span>
      <span class="gmeta">${esc(GDM.id)} · ${GDM.cyc.slice(8, 10)}Z ${esc(GDM.cyc.slice(6, 8))}</span></div>
    <div class="gsel ensm">${GSUITES.map(([k, s]) => `<button data-gs="${k}" class="${k === GDM.suite ? "on" : ""}"${d.suites[k] ? "" : " disabled"} style="--sc:${GCOL[k]}">${s.toUpperCase()}</button>`).join("")}</div>
    <div class="gbody">${gdmTab(S)}</div>
    ${S.derived ? `<div class="rnote">Derived, not a model run: pools ${S.pooled.map(k => GNAME[k]).join(", ")} with equal weight per model${S.radii_from && S.radii_from.length < S.pooled.length ? `; size and wind products use ${S.radii_from.map(k => GNAME[k]).join(" and ")} (GenCast carries no wind radii)` : ""}.</div>` : ""}
    <div class="rnote gattr">Google DeepMind Weather Lab, experimental, not for real-world use. Not an official forecast.</div>`;
  el.querySelectorAll("[data-gt]").forEach(b => b.onclick = () => { GDM.tab = b.dataset.gt; gdmCard(); });
  el.querySelectorAll("[data-gs]").forEach(b => b.onclick = () => { GDM.suite = b.dataset.gs; gdmCard(); if (ON.has("gwind")) { lclear("gwind"); LY.gwind.off(); LY.gwind.on(false); } });
  el.querySelectorAll("[data-gm]").forEach(b => b.onclick = () => { GDM.metric = b.dataset.gm; gdmCard(); });
  el.querySelectorAll("[data-gk]").forEach(b => b.onclick = () => { GDM.dvk = b.dataset.gk; if (!S["dv" + GDM.dvk]?.[GDM.dvh]) GDM.dvh = "48"; gdmCard(); });
  el.querySelector("#gdDvh")?.addEventListener("change", e => { GDM.dvh = e.target.value; gdmCard(); });
  el.querySelectorAll("[data-gw]").forEach(b => b.onclick = () => { const [k, v] = b.dataset.gw.split(":"); GDM[k] = v; gdmCard(); if (ON.has("gwind")) { lclear("gwind"); LY.gwind.off(); LY.gwind.on(false); } });
  el.querySelector("[data-gwon]")?.addEventListener("click", () => setLayer("gwind", !ON.has("gwind"), false));
  el.querySelector("[data-ens]")?.addEventListener("click", () => { ENSEL = GDM.suite === "fnv3x" ? "gdm" : GDM.suite; const i = TABS.findIndex(t => t.id === "models"); if (TABS[TAB]?.id === "models") { lclear("ens"); LY.ens.off(); LY.ens.on(true); ensBar(); } else tab(i); });
}
function gdmTiles(S) {
  const c72 = (S.cat || []).find(r => r.h === 72), hu = c72 ? ["C1", "C2", "C3", "C4", "C5"].reduce((s, k) => s + (c72[k] || 0), 0) : null, pk = S.peak_q || [];
  const T = [["Peak P10 / P50 / P90", pk.length ? `${wnd(pk[1])} / <b>${wnd(pk[3])}</b> / ${wnd(pk[5])} ${wl()}` : "–"], ["RI chance by 48 h", gpct(S.ri_cum[gLI(48)])], ["RI chance by 120 h", gpct(S.ri_cum[gLI(120)])],
    ["Hurricane at 72 h", gpct(hu)], ["Landfall by 120 h", gpct(S.landfall?.p120)], ["Still tracked at 120 h", gpct(S.alive[gLI(120)])]];
  return `<div class="gtiles">${T.map(t => `<div><small>${t[0]}</small><span>${t[1]}</span></div>`).join("")}</div>`;
}
function gdmTab(S) {
  const t = GDM.tab;
  if (t === "over") return gdmTiles(S) + `<div class="gtwo">${gFan(S, "vmax_q", "Max sustained wind", "kt", { wind: true, lo: 0, cats: true, mean: S.vmax_mean, sub: `init ${GDM.cyc.slice(8, 10)}Z` })}${gSpread(S)}</div>
    <div class="gact"><button class="gbtn" data-ens>Show these members on the map</button></div>`;
  if (t === "int") return `<div class="ensm"><button data-gm="v" class="${GDM.metric === "v" ? "on" : ""}">WIND</button><button data-gm="p" class="${GDM.metric === "p" ? "on" : ""}">PRESSURE</button></div><div class="gtwo">` +
    (GDM.metric === "v" ? gFan(S, "vmax_q", "Max sustained wind", "kt", { wind: true, lo: 0, cats: true, mean: S.vmax_mean }) : gFan(S, "mslp_q", "Minimum pressure", "mb", { color: "#7cc3ea" })) + gAce(S) + `</div><div class="gtwo">${gPeak(S)}${gPeak2d(S)}</div>`;
  if (t === "chg") return `<div class="gtwo">${gRi(S)}<div><div class="ensm"><button data-gk="24" class="${GDM.dvk === "24" ? "on" : ""}">24-H</button><button data-gk="12" class="${GDM.dvk === "12" ? "on" : ""}">12-H</button>
    <select id="gdDvh">${Object.keys(S["dv" + GDM.dvk] || {}).map(h => `<option${h === GDM.dvh ? " selected" : ""}>${h}</option>`).join("")}</select></div>${gDv(S)}</div></div>${gCats(S)}`;
  if (t === "str") return S.rmw_q ? `<div class="gtwo">${gFan(S, "rmw_q", "Radius of maximum wind", "km", { lo: 0, color: "#c084fc", sub: "members at 34 kt or more" })}${gFan(S, "r34_q", "34-kt radius, mean of quadrants", "km", { lo: 0, color: "#46c56a", sub: "storm size" })}</div>
    <div class="gtwo">${gNest(S)}${gRose(S)}</div><div class="gtwo">${gFan(S, "area34_q", "Area inside 34-kt winds", "", { lo: 0, color: "#ffb83a", sub: "thousand km²" })}<div></div></div>` : `<div class="rnote">${GNAME[GDM.suite]} carries no wind radii.</div>`;
  if (t === "wind") { const G = gwindGrid();
    return G ? `<div class="ensm">${[["34", "34 KT"], ["50", "50 KT"], ["64", "64 KT"], ["c120", "CENTRE WITHIN 120 KM"]].map(([k, l]) => `<button data-gw="wk:${k}" class="${GDM.wk === k ? "on" : ""}"${G.grid.p[k + "_120"] ? "" : " disabled"}>${l}</button>`).join("")}</div>
      <div class="ensm">${[["72", "72 H"], ["120", "120 H"]].map(([k, l]) => `<button data-gw="wh:${k}" class="${GDM.wh === k ? "on" : ""}">${l}</button>`).join("")} <button class="gbtn" data-gwon>${ON.has("gwind") ? "Hide on map" : "Show on map"}</button></div>
      <div class="gramp">${PRAMP.map(x => `<i style="background:${x[1]}">${x[0]}</i>`).join("")}<em>%</em></div><div class="rnote" id="gdRead">${ON.has("gwind") ? "Point at the map for the odds there." : "Show it on the map, then point at any place for the odds there."}</div>
      <div class="rnote">${G === S ? "" : `${GNAME[GDM.suite]} carries no wind radii, so this uses the Google super ensemble. `}A place counts for a member when the member's own quadrant radius covers it at any time in the window (tracks every 2 h).</div>` : `<div class="rnote">No wind-probability grid for this run.</div>`; }
  if (t === "land") { const lf = S.landfall || {};
    return `<div class="gtiles"><div><small>Landfall by 120 h</small><span>${gpct(lf.p120)}</span></div><div><small>Landfall by 240 h</small><span>${gpct(lf.p240)}</span></div><div><small>Most likely area</small><span>${lf.where?.length ? `${esc(lf.where[0][0])} ${gpct(lf.where[0][1])}` : "–"}</span></div></div>
      <div class="gtwo">${gLfTiming(lf)}${gLfCat(lf)}</div>${lf.where?.length ? `<div class="gwhere">${lf.where.map(w => `<span>${esc(w[0])} <em>${gpct(w[1])}</em></span>`).join("")}</div>` : ""}
      <div class="rnote">First move from sea onto land within 240 h (Natural Earth 10 m coast, tracks hourly). Shares are of all members.</div>`; }
  if (t === "cmp") return gCmpTable() + `<div class="gtwo">${gCmpChart()}<div></div></div>`;
  if (t === "trend") return gTrend();
  return "";
}
function gSpread(S) {
  const mx = Math.max(100, ...S.spread_km.filter(Number.isFinite)) * 1.1, st = gstep(mx) * 2, yt = []; for (let v = 0; v <= mx; v += st) yt.push(v);
  const c = GChart({ title: "Track spread", sub: "km from the ensemble-mean position", x: [0, 240], y: [0, mx], xt: GH, yt, yl: "km" });
  c.line(S.leads.map((h, k) => [h, S.spread_km[k]]), GCOL[GDM.suite], 2.3); c.line(S.leads.map((h, k) => [h, S.alive[k] * mx]), "#9fb3d6", 1.2, "3 3");
  c.legend([["spread", GCOL[GDM.suite]], ["members still tracking (scaled)", "#9fb3d6"]]); return c.done();
}
function gAce(S) {
  const q = S.ace_q, L = S.leads.filter((_, k) => k % 2 === 0), mx = Math.max(1, ...q.filter(Boolean).map(r => r[5]));
  const c = GChart({ title: "Accumulated cyclone energy", sub: "34 kt and above", x: [0, 240], y: [0, mx * 1.1], xt: GH, yt: [0, mx / 2, mx].map(v => +v.toFixed(1)), yl: "ACE" });
  const col = i => L.map((h, k) => [h, q[k] ? q[k][i] : null]); c.band(col(1), col(5), "#ffb83a", .2); c.band(col(2), col(4), "#ffb83a", .35); c.line(col(3), "#fff", 2.1); return c.done();
}
function gPeak(S) {
  const hb = S.peak_hist, mx = Math.max(.05, ...hb.p), f = WF().f;
  const c = GChart({ title: "Lifetime peak wind", sub: "share of members, 0-240 h", x: [0, 190], y: [0, mx * 1.15], xt: [0, 34, 64, 96, 137, 180], xf: v => Math.round(v * f), yt: [0, mx / 2, mx], yf: v => Math.round(v * 100) + "%" });
  hb.p.forEach((p, i) => c.bar(hb.bins[i] + .6, hb.bins[i + 1] - .6, 0, p, gcatC(hb.bins[i] + 5)));
  if (S.peak_q) { c.raw(`<line x1="${c.X(S.peak_q[3])}" x2="${c.X(S.peak_q[3])}" y1="26" y2="${c.H - 28}" stroke="#fff" stroke-dasharray="4 3"/>`); c.text(S.peak_q[3] + 2, mx * 1.07, `median ${wnd(S.peak_q[3])} ${wl()}`, "#fff"); }
  return c.done();
}
function gPeak2d(S) {
  const P = S.peak2d, mx = Math.max(1e-9, ...P.p.flat()), w = windTicks(190);
  const c = GChart({ title: "When the peak happens", sub: "peak wind against its forecast hour", x: [0, 240], y: [0, 190], xt: GH, ...w, yl: wl() });
  P.p.forEach((row, i) => row.forEach((v, j) => { if (v > 0) c.bar(P.hbins[j], P.hbins[j + 1], P.vbins[i], P.vbins[i + 1], "#5dd3ff", Math.min(1, .15 + .85 * v / mx)); })); return c.done();
}
function gRi(S) {
  const c = GChart({ title: "Rapid intensification", sub: "30 kt or more in 24 h", x: [0, 240], y: [0, 1], xt: GH, yt: [0, .25, .5, .75, 1], yf: v => Math.round(v * 100) + "%" });
  c.line(S.leads.map((h, k) => [h, S.ri_cum[k]]), "#ff6b5e", 2.5); c.line(S.leads.map((h, k) => [h, S.ri_lead[k]]), "#ffd24a", 1.9, "4 3");
  c.legend([["by this hour", "#ff6b5e"], ["ending at this hour", "#ffd24a"]]); return c.done();
}
function gDv(S) {
  const hb = (S["dv" + GDM.dvk] || {})[GDM.dvh]; if (!hb) return `<div class="rnote">No members at that hour.</div>`;
  const B = S.dv_bins, mx = Math.max(.05, ...hb), f = WF().f;
  const c = GChart({ title: `${GDM.dvk}-h wind change to F${GDM.dvh.padStart(3, "0")}`, sub: `valid ${gvt(+GDM.dvh)}`, x: [B[0], B[B.length - 1]], y: [0, mx * 1.15], xt: B.filter((_, i) => i % 2 === 0), xf: v => Math.round(v * f), yt: [0, mx / 2, mx], yf: v => Math.round(v * 100) + "%" });
  hb.forEach((p, i) => c.bar(B[i] + .8, B[i + 1] - .8, 0, p, B[i] >= 30 && GDM.dvk === "24" ? "#ff6b5e" : B[i] >= 0 ? "#ffb83a" : "#5dd3ff"));
  c.raw(`<text class="ga" x="${c.W - 12}" y="${c.H - 3}" text-anchor="end">${wl()}</text>`); return c.done();
}
function gCats(S) {
  const rows = S.cat, keys = ["diss", "TD", "TS", "C1", "C2", "C3", "C4", "C5"], col = { diss: "#3a4560", TD: "#3fa4ff", TS: "#46c56a", C1: "#ffe14d", C2: "#ff9a2f", C3: "#f5333c", C4: "#e33ad4", C5: "#b03bff" };
  const c = GChart({ w: 960, h: 190, title: "Intensity category by forecast hour", sub: "share of members", x: [0, 240], y: [0, 1], xt: GH, yt: [0, .5, 1], yf: v => Math.round(v * 100) + "%" });
  const base = rows.map(() => 0);
  for (const k of keys) { const lo = rows.map((r, i) => [r.h, base[i]]), hi = rows.map((r, i) => { base[i] += r[k] || 0; return [r.h, base[i]]; }); c.band(lo, hi, col[k], .85); }
  c.legend(keys.map(k => [k === "diss" ? "lost" : k, col[k]])); return c.done();
}
function gNest(S) {
  const L = S.leads, all = [...S.r50_med, ...S.r64_med, ...(S.r34_q || []).map(r => r && r[3])].filter(Number.isFinite), mx = Math.max(100, ...all) * 1.1, st = gstep(mx) * 2, yt = []; for (let v = 0; v <= mx; v += st) yt.push(v);
  const c = GChart({ title: "Wind-field nesting", sub: "median radii", x: [0, 240], y: [0, mx], xt: GH, yt, yl: "km" });
  c.line(L.map((h, k) => [h, S.r34_q[k]?.[3]]), "#46c56a", 2.3); c.line(L.map((h, k) => [h, S.r50_med[k]]), "#ffe14d", 2.1); c.line(L.map((h, k) => [h, S.r64_med[k]]), "#f5333c", 2.1);
  c.legend([["34 kt", "#46c56a"], ["50 kt", "#ffe14d"], ["64 kt", "#f5333c"]]); return c.done();
}
function gRose(S) {
  const R = S.rose_at_peak; if (!R) return "<div></div>";
  const W = 470, H = 210, cx = 235, cy = 118, mx = Math.max(50, ...R["34"]), k = 80 / mx;
  let s = `<svg viewBox="0 0 ${W} ${H}" class="gsvg"><text class="gt" x="40" y="15">Wind radii at each member's peak</text><text class="gs" x="458" y="15" text-anchor="end">median by quadrant</text>`;
  [.5, 1].forEach(f => { s += `<circle cx="${cx}" cy="${cy}" r="${80 * f}" fill="none" stroke="rgba(160,190,255,.2)"/><text class="ga" x="${cx + 82 * f}" y="${cy + 12}">${Math.round(mx * f)} km</text>`; });
  for (const [kk, col] of [["34", "#46c56a"], ["50", "#ffe14d"], ["64", "#f5333c"]]) R[kk].forEach((r, q) => { const a0 = (-90 + q * 90) * Math.PI / 180, a1 = a0 + Math.PI / 2, rr = (r || 0) * k;
    s += `<path d="M${cx} ${cy}L${cx + rr * Math.cos(a0)} ${cy + rr * Math.sin(a0)}A${rr} ${rr} 0 0 1 ${cx + rr * Math.cos(a1)} ${cy + rr * Math.sin(a1)}Z" fill="${col}" fill-opacity=".33" stroke="${col}"/>`; });
  return s + `<text class="ga" x="${cx}" y="${cy - 86}" text-anchor="middle">N</text></svg>`;
}
function gLfTiming(lf) {
  if (!lf.timing) return "<div></div>";
  const B = lf.bins, tot = B.slice(0, -1).map((_, i) => Object.keys(lf.timing).reduce((s, k) => s + lf.timing[k][i], 0)), mx = Math.max(.02, ...tot);
  const c = GChart({ title: "When members make landfall", sub: "12-h bins, by intensity at landfall", x: [0, 240], y: [0, mx * 1.15], xt: GH, yt: [0, mx / 2, mx], yf: v => Math.round(v * 100) + "%" });
  B.slice(0, -1).forEach((b, i) => { let y = 0; for (const x of GCAT) { const v = lf.timing[x[0]][i]; if (v) { c.bar(b + 1, B[i + 1] - 1, y, y + v, x[2]); y += v; } } });
  c.legend(GCAT.map(x => [x[0], x[2]])); return c.done();
}
function gLfCat(lf) {
  if (!lf.cat || !lf.p240) return "<div></div>";
  const c = GChart({ title: "Intensity at landfall", sub: "share of landfalling members", x: [0, 7], y: [0, 1], yt: [0, .5, 1], yf: v => Math.round(v * 100) + "%" });
  GCAT.forEach((x, i) => { c.bar(i + .12, i + .88, 0, lf.cat[x[0]] || 0, x[2]); c.text(i + .5, -.1, x[0], "#cfd6e0", "middle"); }); return c.done();
}
function gCmpTable() {
  const d = GDM.doc, rows = GSUITES.filter(([k]) => d.suites[k]), ref = d.suites.gdm || d.suites[rows[0][0]];
  let h = `<div class="gtw"><table class="gtab"><thead><tr><th>Hour</th>${rows.map(([k]) => `<th style="color:${GCOL[k]}">${GNAME[k]}</th>`).join("")}</tr></thead><tbody>`;
  for (const hh of [24, 48, 72, 96, 120]) {
    const k = gLI(hh), kc = hh / 12;
    h += `<tr><td><b>F${String(hh).padStart(3, "0")}</b><small>${gvt(hh)}</small></td>` + rows.map(([s]) => { const S = d.suites[s], q = S.vmax_q[k], c = S.cat[kc], hu = c ? ["C1", "C2", "C3", "C4", "C5"].reduce((a, x) => a + (c[x] || 0), 0) : null;
      const dk = S.mean[k] && ref.mean[k] && S !== ref ? Math.round(gkm(S.mean[k][0], S.mean[k][1], ref.mean[k][0], ref.mean[k][1])) : null;
      return `<td><b>${q ? `${wnd(q[3])} ${wl()}` : "–"}</b><small>hurricane ${gpct(hu)} · spread ${S.spread_km[k] ?? "–"} km${dk != null ? ` · ${dk} km off` : ""}</small></td>`; }).join("") + "</tr>";
  }
  return h + `</tbody></table></div><div class="rnote">Median wind, chance of hurricane strength, track spread and distance of each mean position from the ${d.suites.gdm ? "super-ensemble" : "first suite's"} mean, by valid time.</div>`;
}
function gCmpChart() {
  const d = GDM.doc, rows = GSUITES.filter(([k]) => d.suites[k]), hi = Math.max(80, ...rows.flatMap(([k]) => d.suites[k].vmax_q.filter(Boolean).map(q => q[4]))) + 10;
  const c = GChart({ title: "Median wind by suite", sub: "with the interquartile range", x: [0, 240], y: [0, hi], xt: GH, ...windTicks(hi), yl: wl() });
  for (const [k] of rows) { const S = d.suites[k], ok = i => S.vmax_q[i] && S.alive[i] >= .1, col = i => S.leads.map((h, j) => [h, ok(j) ? S.vmax_q[j][i] : null]); c.band(col(2), col(4), GCOL[k], .12); c.line(col(3), GCOL[k], 2); }
  c.legend(rows.map(([k]) => [GNAME[k], GCOL[k]])); return c.done();
}
function gTrend() {
  const ix = GDM.idx, rows = [];
  for (const c of Object.keys(ix.cycles).sort()) { const id = gdmMatch(ix.cycles[c].storms); const st = id && ix.cycles[c].storms[id]; if (st?.suites[GDM.suite]) rows.push([c, id, st.suites[GDM.suite]]); }
  if (!rows.length) return `<div class="rnote">No earlier runs for this storm.</div>`;
  const xs = rows.map((_, i) => i), mxp = Math.max(80, ...rows.map(r => r[2].peak50 || 0)) + 10;
  const c = GChart({ title: "Run to run", sub: `${GNAME[GDM.suite]}, oldest to newest`, x: [-.5, rows.length - .5], y: [0, 1], xt: xs, xf: i => rows[i][0].slice(6, 8) + "/" + rows[i][0].slice(8, 10) + "Z", yt: [0, .5, 1], yf: v => Math.round(v * 100) + "%" });
  c.line(rows.map((r, i) => [i, r[2].ri72]), "#ff6b5e", 2.3); c.line(rows.map((r, i) => [i, r[2].lf120]), "#46c56a", 2.3); c.line(rows.map((r, i) => [i, (r[2].peak50 || 0) / mxp]), "#fff", 2.3, "5 3");
  c.legend([["RI by 72 h", "#ff6b5e"], ["landfall by 120 h", "#46c56a"], [`median peak (scaled, top = ${wnd(mxp)} ${wl()})`, "#fff"]]);
  return `<div class="gtwo">${c.done()}<div class="gtw"><table class="gtab"><thead><tr><th>Run</th><th>Median peak</th><th>RI by 72 h</th><th>Landfall by 120 h</th></tr></thead><tbody>` +
    rows.slice().reverse().map(r => `<tr${r[0] === GDM.cyc ? ' class="on"' : ""}><td><b>${r[0].slice(6, 8)}/${r[0].slice(8, 10)}Z</b><small>${esc(r[1])}</small></td><td>${r[2].peak50 != null ? `${wnd(r[2].peak50)} ${wl()}` : "–"}</td><td>${gpct(r[2].ri72)}</td><td>${gpct(r[2].lf120)}</td></tr>`).join("") + "</tbody></table></div></div>";
}
