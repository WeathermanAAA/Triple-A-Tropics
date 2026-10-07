/* Situation Room GOES loops read straight from NOAA: the 1-minute mesoscale sectors and the 5-minute CONUS/PACUS sectors.
   Original header -- mesoscale loops: GOES-East/West 1-minute mesoscale sectors, read straight from NOAA's public GOES
   buckets (ABI-L2-CMIPM netCDF, CORS-open) and decoded in the browser (h5wasm, loaded only when meso is first used).
   Pixels are true brightness temperature / reflectance, reprojected from the satellite's fixed grid to Web Mercator,
   so the IR ramps (TAT, Dvorak BD, grayscale) are exact rather than recoloured imagery.
   Which sector covers the storm comes from the site's own meso index (meso/manifest.json, kept current by the meso
   poller's sector discovery). Same interface as SatX.Loop, so the shared playhead, loop bar and 4-panel drive it. */
"use strict";
const Meso = (() => {
  const H5 = "https://cdn.jsdelivr.net/npm/h5wasm@0.10.3/dist/iife/h5wasm.js";
  const BUCKET = { goes19: "https://noaa-goes19.s3.amazonaws.com", goes18: "https://noaa-goes18.s3.amazonaws.com" };
  const BANDS = { ir: { ch: "C13", t: "Infrared", short: "IR" }, wv: { ch: "C08", t: "Water Vapor", short: "WV" }, vis: { ch: "C02", t: "Visible", short: "VIS" } };
  const req = 6378137, rpol = 6356752.31414, Hs = 35786023 + 6378137, e2 = req * req / (rpol * rpol);
  let H5P = null;
  function h5() {
    if (!H5P) H5P = new Promise((res, rej) => { const s = document.createElement("script"); s.src = H5; s.onload = () => window.h5wasm.ready.then(() => res(window.h5wasm)); s.onerror = rej; document.head.appendChild(s); });
    return H5P;
  }
  let SECT = null, SECT_T = 0;
  async function sectors() {
    if (SECT && Date.now() - SECT_T < 120e3) return SECT;
    try { const m = await (await fetch(`https://cdn.triple-a-tropics.com/meso/manifest.json?t=${Date.now()}`, { cache: "no-store" })).json(); SECT = m.sectors || []; SECT_T = Date.now(); }
    catch (e) { SECT = SECT || []; }
    return SECT;
  }
  /* the GOES mesoscale sector (if any) whose box holds a point */
  async function cover(lon, lat) {
    for (const s of await sectors()) {
      const m = (s.slug || "").match(/^(goes1[89])-m([12])$/); if (!m || !s.bbox) continue;
      let [w, so, e, n] = s.bbox, x = lon; if (e < w) { e += 360; if (x < w) x += 360; }
      if (x >= w + .3 && x <= e - .3 && lat >= so + .3 && lat <= n - .3) return { sat: m[1], m: +m[2], label: s.label, bbox: s.bbox };
    }
    return null;
  }
  /* the 5-minute sectors (CONUS from GOES-East, PACUS from GOES-West): fixed-grid extents, radians (GOES-R PUG) */
  const LIVE = { goes19: { lon0: -75, x: [-0.101332, 0.038612], y: [0.044268, 0.128212], label: "GOES-19 CONUS" },
    goes18: { lon0: -137, x: [-0.069972, 0.069972], y: [0.044268, 0.128212], label: "GOES-18 PACUS" } };
  function coverLive(lon, lat) {
    let best = null;
    for (const [sat, S] of Object.entries(LIVE)) {
      const xy = toXY(lon, lat, S.lon0), m = .006; if (!xy) continue;
      if (xy[0] < S.x[0] + m || xy[0] > S.x[1] - m || xy[1] < S.y[0] + m || xy[1] > S.y[1] - m) continue;
      const d = Math.abs(((lon - S.lon0 + 540) % 360) - 180); if (!best || d < best.d) best = { sat, kind: "C", label: S.label, d };
    }
    return best;
  }
  const LIVEBANDS = { ir: BANDS.ir, wv: BANDS.wv };
  const jday = d => Math.floor((Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) - Date.UTC(d.getUTCFullYear(), 0, 0)) / 864e5);
  const keyTime = k => { const m = k.match(/_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})/); return m ? Date.UTC(+m[1], 0, +m[2], +m[3], +m[4], +m[5]) : 0; };
  async function list(sec, ch, from, to) {
    const keys = [];
    for (let t = from - 36e5; t <= to + 36e5; t += 36e5) {
      const d = new Date(t), P = sec.kind === "C" ? "ABI-L2-CMIPC" : "ABI-L2-CMIPM",
        pre = `${P}/${d.getUTCFullYear()}/${String(jday(d)).padStart(3, "0")}/${String(d.getUTCHours()).padStart(2, "0")}/OR_${P}${sec.kind === "C" ? "" : sec.m}-M6${ch}_`;
      try {
        const x = await (await fetch(`${BUCKET[sec.sat]}/?list-type=2&prefix=${encodeURIComponent(pre)}`)).text();
        for (const m of x.matchAll(/<Key>([^<]+)<\/Key>/g)) { const tt = keyTime(m[1]); if (tt >= from && tt <= to + 6e4) keys.push({ k: m[1], t: tt }); }
      } catch (e) {}
    }
    return keys.sort((a, b) => a.t - b.t);
  }
  /* fixed grid <-> lat/lon (GOES-R PUG vol. 4, 4.2.8) */
  function toLL(x, y, lon0) {
    const a = Math.sin(x) ** 2 + Math.cos(x) ** 2 * (Math.cos(y) ** 2 + e2 * Math.sin(y) ** 2), b = -2 * Hs * Math.cos(x) * Math.cos(y), c = Hs * Hs - req * req;
    const disc = b * b - 4 * a * c; if (disc < 0) return null;
    const rs = (-b - Math.sqrt(disc)) / (2 * a), sx = rs * Math.cos(x) * Math.cos(y), sy = -rs * Math.sin(x), sz = rs * Math.cos(x) * Math.sin(y);
    return [lon0 - Math.atan(sy / (Hs - sx)) * 180 / Math.PI, Math.atan(e2 * sz / Math.sqrt((Hs - sx) ** 2 + sy * sy)) * 180 / Math.PI];
  }
  function toXY(lon, lat, lon0) {
    const la = lat * Math.PI / 180, dl = (lon - lon0) * Math.PI / 180, pc = Math.atan(Math.tan(la) / e2), rc = rpol / Math.sqrt(1 - (1 - 1 / e2) * Math.cos(pc) ** 2);
    const sx = Hs - rc * Math.cos(pc) * Math.cos(dl), sy = -rc * Math.cos(pc) * Math.sin(dl), sz = rc * Math.sin(pc);
    if (Hs * (Hs - sx) < sy * sy + e2 * sz * sz) return null;
    return [Math.asin(-sy / Math.sqrt(sx * sx + sy * sy + sz * sz)), Math.atan(sz / sx)];
  }
  const R = 6378137, mx = lon => lon * Math.PI / 180 * R, my = lat => R * Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360));
  const lonOf = x => x / R * 180 / Math.PI, latOf = y => (2 * Math.atan(Math.exp(y / R)) - Math.PI / 2) * 180 / Math.PI;

  /* crop = {lon, lat, dlon, dlat}: read only the fixed-grid window around the storm (a hyperslab, not the whole sector) */
  async function decode(buf, crop) {
    const h = await h5(), name = "/m" + Math.random().toString(36).slice(2) + ".nc";
    h.FS.writeFile(name, new Uint8Array(buf));
    const f = new h.File(name, "r");
    try {
      const c = f.get("CMI"), x = f.get("x"), y = f.get("y"), p = f.get("goes_imager_projection");
      const sh = c.shape, at = (d, k) => d.attrs[k] ? d.attrs[k].value[0] ?? d.attrs[k].value : null;
      const xv = x.value, yv = y.value;
      const g = { w: sh[1], h: sh[0], sf: at(c, "scale_factor"), ao: at(c, "add_offset"), fill: at(c, "_FillValue"),
        x0: xv[0] * at(x, "scale_factor") + at(x, "add_offset"), dx: at(x, "scale_factor") * (xv[1] - xv[0]),
        y0: yv[0] * at(y, "scale_factor") + at(y, "add_offset"), dy: at(y, "scale_factor") * (yv[1] - yv[0]), lon0: at(p, "longitude_of_projection_origin") };
      if (!crop) { g.v = c.value; return g; }
      let i0 = Infinity, i1 = -Infinity, j0 = Infinity, j1 = -Infinity;
      for (let a = 0; a <= 20; a++) for (let b = 0; b <= 20; b++) {
        if (a % 20 && b % 20) continue;
        const xy = toXY(crop.lon - crop.dlon + crop.dlon * a / 10, crop.lat - crop.dlat + crop.dlat * b / 10, g.lon0); if (!xy) continue;
        const i = (xy[0] - g.x0) / g.dx, j = (xy[1] - g.y0) / g.dy; i0 = Math.min(i0, i); i1 = Math.max(i1, i); j0 = Math.min(j0, j); j1 = Math.max(j1, j);
      }
      i0 = Math.max(0, Math.floor(i0)); j0 = Math.max(0, Math.floor(j0)); i1 = Math.min(g.w, Math.ceil(i1) + 1); j1 = Math.min(g.h, Math.ceil(j1) + 1);
      if (!(i1 - i0 > 8 && j1 - j0 > 8)) throw new Error("storm outside sector");
      g.v = c.slice([[j0, j1], [i0, i1]]); g.x0 += i0 * g.dx; g.y0 += j0 * g.dy; g.w = i1 - i0; g.h = j1 - j0;
      return g;
    } finally { f.close(); try { h.FS.unlink(name); } catch (e) {} }
  }
  /* the output grid (Web Mercator) for a geometry: bbox from the sector's perimeter */
  function outGrid(g, maxPx) {
    let w = Infinity, e = -Infinity, s = Infinity, n = -Infinity;
    const step = Math.max(1, Math.floor(g.w / 40));
    for (let i = 0; i <= g.w; i += step) for (const j of [0, g.h - 1]) edge(i, j);
    for (let j = 0; j <= g.h; j += step) for (const i of [0, g.w - 1]) edge(i, j);
    function edge(i, j) { const ll = toLL(g.x0 + Math.min(i, g.w - 1) * g.dx, g.y0 + Math.min(j, g.h - 1) * g.dy, g.lon0); if (!ll) return; w = Math.min(w, ll[0]); e = Math.max(e, ll[0]); s = Math.min(s, ll[1]); n = Math.max(n, ll[1]); }
    const X0 = mx(w), X1 = mx(e), Y0 = my(s), Y1 = my(n), asp = (X1 - X0) / (Y1 - Y0);
    const W = Math.min(maxPx, Math.round(g.w * 2)), Hh = Math.round(W / asp);
    return { X0, X1, Y0, Y1, W, H: Hh, coords: [[w, n], [e, n], [e, s], [w, s]] };
  }
  const IDX = new Map();
  function indexMap(g, o) {
    const key = [g.lon0, g.x0.toFixed(7), g.y0.toFixed(7), g.dx.toFixed(9), g.w, g.h, o.X0.toFixed(0), o.Y1.toFixed(0), o.W, o.H].join("|");
    if (IDX.has(key)) return IDX.get(key);
    const m = new Int32Array(o.W * o.H).fill(-1);
    for (let r = 0; r < o.H; r++) {
      const lat = latOf(o.Y1 - (r + .5) / o.H * (o.Y1 - o.Y0));
      for (let c = 0; c < o.W; c++) {
        const xy = toXY(lonOf(o.X0 + (c + .5) / o.W * (o.X1 - o.X0)), lat, g.lon0); if (!xy) continue;
        const i = Math.round((xy[0] - g.x0) / g.dx), j = Math.round((xy[1] - g.y0) / g.dy);
        if (i >= 0 && i < g.w && j >= 0 && j < g.h) m[r * o.W + c] = j * g.w + i;
      }
    }
    if (IDX.size > 12) IDX.delete(IDX.keys().next().value);
    IDX.set(key, m); return m;
  }
  function paint(fr, o, band, ramp) {
    const g = fr.g, m = indexMap(g, o), cv = fr.canvas || document.createElement("canvas"); cv.width = o.W; cv.height = o.H; fr.canvas = cv;
    const x = cv.getContext("2d"), id = x.createImageData(o.W, o.H), d = id.data, v = g.v;
    const tbl = band === "vis" ? null : SatX.rampTable(band === "wv" ? "wv" : (ramp === "native" ? "tat" : ramp));
    for (let p = 0, q = 0; p < m.length; p++, q += 4) {
      const k = m[p]; if (k < 0) continue; const raw = v[k]; if (raw === g.fill || raw < 0) continue;
      const val = raw * g.sf + g.ao;
      if (band === "vis") { const b = Math.round(255 * Math.sqrt(Math.max(0, Math.min(1, val)))); d[q] = d[q + 1] = d[q + 2] = b; }
      else { const i = Math.max(0, Math.min(300, Math.round((val - 273.15 + 100) * 2))) * 3; d[q] = tbl[i]; d[q + 1] = tbl[i + 1]; d[q + 2] = tbl[i + 2]; }
      d[q + 3] = 255;
    }
    x.putImageData(id, 0, 0);
    if (band === "vis") fr.g = { ...g, v: null };   // a visible frame is 2000x2000: keep the picture, drop the counts
    return cv;
  }

  class Loop {
    constructor(map, id, before, opt = {}) {
      Object.assign(this, { map, id, before, band: opt.band || "ir", frames: [], on: false, gen: 0, sector: null, center: null, ready: false, loading: null, maxPx: opt.maxPx || 1600 });
      this.canvas = document.createElement("canvas"); this.ctx = this.canvas.getContext("2d");
    }
    get sat() { return this.sector?.sat === "goes18" ? "west" : "east"; }
    get layer() { return `${this.sector?.kind === "C" ? "live" : "meso"}-${this.sector?.sat}-m${this.sector?.m}-${this.band}`; }
    get current() { return this.cur || this.frames[this.frames.length - 1]; }
    get label() { return !this.sector ? "Mesoscale" : this.sector.kind === "C" ? this.sector.label : `${this.sector.sat === "goes18" ? "GOES-18" : "GOES-19"} Meso ${this.sector.m}`; }
    setBand(b) { this.band = b; if (this.on) this.reload(); }
    show(on) {
      this.on = on;
      if (on) { SatX.P.loops.add(this); this.reload(); }
      else { SatX.P.loops.delete(this); this.gen++; if (this.map.getLayer(this.id)) this.map.setLayoutProperty(this.id, "visibility", "none"); }
    }
    async reload() {
      const gen = ++this.gen; if (!this.sector) return;
      const P = SatX.P, live = this.sector.kind === "C", hours = Math.min(live ? 6 : 2, P.hours), step = live ? (hours > 1 ? 10 : 5) : this.band === "vis" || hours > 1 ? 2 : 1;
      const crop = live && this.center ? { lon: this.center[0], lat: this.center[1], dlon: 13, dlat: 9.5 } : null;
      this.ready = false; this.loading = { done: 0, total: 0 }; this.emit();
      const now = Date.now(), keys = await list(this.sector, BANDS[this.band].ch, now - hours * 36e5 - 3 * 6e4, now);
      if (gen !== this.gen) return;
      const pick = []; let last = -Infinity; for (const k of keys) if (k.t - last >= step * 6e4 - 5e3) { pick.push(k); last = k.t; }
      if (!pick.length) { this.loading = null; this.emit(); return; }
      this.loading.total = pick.length; this.emit();
      const fr = pick.map(k => ({ t: k.t, k: k.k, g: null, canvas: null, img: null }));
      let o = null;
      const load = async f => {
        try { const r = await fetch(`${BUCKET[this.sector.sat]}/${f.k}`); if (!r.ok) return; f.g = await decode(await r.arrayBuffer(), crop); } catch (e) {}
      };
      const newest = fr[fr.length - 1]; await load(newest); if (gen !== this.gen || !newest.g) { this.loading = null; this.emit(); return; }
      o = outGrid(newest.g, this.band === "vis" ? 1800 : this.maxPx); this.out = o;
      newest.img = paint(newest, o, this.band, P.ramp); this.install(o, [newest]); this.loading.done = 1; this.emit();
      const rest = fr.slice(0, -1).reverse(); let i = 0;
      const worker = async () => { while (i < rest.length && gen === this.gen) { const f = rest[i++]; await load(f); if (f.g) f.img = paint(f, o, this.band, P.ramp); this.loading.done++; this.emit(); } };
      await Promise.all(Array.from({ length: 4 }, worker));
      if (gen !== this.gen) return;
      this.frames = fr.filter(f => f.img); this.loading = null; this.ready = true; this.key = null; this.emit();
    }
    install(o, frames) {
      this.frames = frames; this.key = null; const c = this.canvas; c.width = o.W; c.height = o.H; const m = this.map;
      if (!m.getSource(this.id)) {
        m.addSource(this.id, { type: "canvas", canvas: c, coordinates: o.coords, animate: true });
        m.addLayer({ id: this.id, type: "raster", source: this.id, paint: { "raster-fade-duration": 0, "raster-opacity": SatX.P.opacity, "raster-resampling": "linear" } }, this.before);
      } else m.getSource(this.id).setCoordinates(o.coords);
      m.setLayoutProperty(this.id, "visibility", this.on ? "visible" : "none");
      this.draw(SatX.P.t, true);
    }
    draw(t, force) {
      const F = this.frames; if (!F.length || !this.on) return;
      let f = F[0]; for (const x of F) { if (x.t <= t + 1) f = x; else break; }
      if (!this.ready) f = F[F.length - 1];
      if (!force && this.key === f.t) return; this.key = f.t;
      const c = this.canvas, x = this.ctx; x.clearRect(0, 0, c.width, c.height); x.drawImage(f.img, 0, 0); this.cur = f;
    }
    repaint() { if (!this.out || this.band === "vis") return; for (const f of this.frames) if (f.g?.v) f.img = paint(f, this.out, this.band, SatX.P.ramp); this.key = null; this.draw(SatX.P.t, true); }
    opacity(o) { if (this.map.getLayer(this.id)) this.map.setPaintProperty(this.id, "raster-opacity", o); }
    emit() { for (const f of SatX.P.subs) f(SatX.P.t, SatX.span()); }
  }
  return { Loop, cover, coverLive, BANDS, LIVEBANDS, h5, BUCKET, jday, keyTime };
})();
