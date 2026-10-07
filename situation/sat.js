/* Situation Room satellite engine.
   NASA GIBS geostationary imagery (GOES-East, GOES-West, Himawari; every 10 min), fetched as ONE image per frame for
   exactly the area on screen (WMS GetMap, Web Mercator, sized to the screen's pixels), then played through a MapLibre
   canvas source, so loops run at full resolution. Playback follows the site's satellite loop contract
   (satellite/explorer/tiled_viewer.js): hard cuts at a fixed frame rate (6 fps default), 6x dwell on the newest
   frame, the whole loop preloaded and decoded before it plays, and a frame is only ever shown once decoded. Pan or zoom and the frames are
   re-fetched for the new view. Any number of maps (the 4-panel view) share one playhead, so their loops stay in step.
   Frame times come from situation/index.json (build_situation.py reads the GIBS time dimensions). */
"use strict";
const SatX = (() => {
  const WMS = "https://gibs.earthdata.nasa.gov/wms/epsg3857/best/wms.cgi";
  const SATS = {
    east: { pre: "GOES-East_ABI", name: "GOES-19", bands: { ir: "Band13_Clean_Infrared", vis: "Band2_Red_Visible_1km", geocolor: "GeoColor", airmass: "Air_Mass", dust: "Dust" } },
    west: { pre: "GOES-West_ABI", name: "GOES-18", bands: { ir: "Band13_Clean_Infrared", vis: "Band2_Red_Visible_1km", geocolor: "GeoColor", airmass: "Air_Mass", dust: "Dust" } },
    him: { pre: "Himawari_AHI", name: "Himawari-9", bands: { ir: "Band13_Clean_Infrared", vis: "Band3_Red_Visible_1km", airmass: "Air_Mass" } }
  };
  const BANDS = {
    ir: { t: "Infrared", short: "IR", sub: "10.3 µm clean IR" },
    vis: { t: "Visible", short: "VIS", sub: "0.64 µm, 1 km" },
    geocolor: { t: "GeoColor", short: "GEO", sub: "true colour by day, IR by night" },
    airmass: { t: "Air Mass", short: "AIR", sub: "RGB" },
    dust: { t: "Dust", short: "DUST", sub: "RGB" }
  };
  const SPEEDS = [{ k: "Slow", fps: 6 }, { k: "Medium", fps: 10 }, { k: "Fast", fps: 15 }];
  const DWELL_NEWEST = 6;   // the newest frame holds 6 frame-intervals, as in tiled_viewer.js
  /* IR ramps: RGB for T = -100..+50 °C in 0.5 °C steps (the site's own colour bars: satellite/explorer/cbars) */
  const RAMPS = {"tat": "/////////////////////////////////////////////Ov++df+9b399b398an97ZD86nz85mP7407640763z732zzy1zns0zbozzPiyzDdyzDdxy3XwyvSvyfMvCXHuCLCtB+8tB+8sB23rBmxqBespBSmoBGhoBGhnQ+bmg+Wlg6PlA6JkA2Djg19jg19igx3iAxxhAtrggtlfwtgfwtgfApZeQpUdQlNcwlIbwhBbQg7bQg7bwc0dQcsfAYjggYciQUTiQUTkAYPmwcOowgNrgkLtgoKvgsJvgsJyQwIzw8I1BII2BUI3hkJ4hwJ4hwJ5yAJ7CMK8SYK9SoK9jAK9jAK9jUL9zsL+EAL+UYL+UoL+lEM+lEM+1UM/FoM/GAM/WUM/msM/msM/3AN/3cN/30O/4QP/4kP/5AQ/5AQ/5YR/50S/6IS/6kT/68U/7YW/7YW/7sY/8Aa/8cd/8wf/9Ii/9Ii/9ck/94n/+Mp/+ks/e0u9e0v9e0v7+0v5+ww4eww2ewx0+wx0+wxy+syxesyv+sztuozqug0m+Q0m+Q0juE1f941c9s2ZNg2WNU3SdI3SdI3Pc84Lsw4LMk+KcVFJ8JLJ8JLJL5TIbtYH7dgHLRlGrBrF6xzF6xzFal4FqqDGKuMGq2YHK6iHK6iHrCtILG3IrPDJLTMJrbYKLfhKLfhOLznSsLpYcnrdM7th9Tvh9TvntvxsOHzyOj22e332Ov01+nx1+nx1ubu1eTs1OLp1ODm093j0tzg0tzg0dnd0Nfbz9XXz9PVztHSztHSzc7PzMzNycnJx8fHxMTEwcHBwcHBvr6+vLy8ubm5tra2s7Ozs7OzsbGxrq6uq6urqKiopqamo6Ojo6OjoKCgnp6em5ubmJiYlZWVk5OTk5OTkZGRj4+PjIyMioqKiIiIiIiIhoaGhISEgoKCgICAfn5+fHx8fHx8enp6eHh4dnZ2dHR0cXFxcXFxb29vbW1ta2traWlpZ2dnZWVlZWVlY2NjYGBgXl5eXFxcWlpaWlpaV1dXVVVVU1NTUVFRT09PTExMTExMS0tLSEhIRkZGREREQkJCPz8/Pz8/PT09Ozs7OTk5NjY2NDQ0NDQ0MjIyMDAwLi4uLi4uLi4uLi4uLi4uLi4uLi4uLi4uLi4uLi4uLi4uLi4uLi4uLi4uLi4uLi4uLi4uLi4uLi4uLi4uLi4u", "bd": "VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eH////////////////////////////////////////////////AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgbm5ubm5ubm5ubm5ubm5ubm5ubm5ubm5ubm5ubm5ubm5ubm5ubm5ubm5ubm5ubm5ubm5ubm5ubm5ubm5ubm5ubm5ubm5uPDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8PDw8ycnJx8fHxsbGxMTEw8PDw8PDwsLCwMDAv7+/vb29vLy8vLy8urq6ubm5uLi4tra2tbW1s7Ozs7OzsrKysbGxr6+vrq6ura2tra2tq6urqqqqqKiop6enpaWlpKSkpKSkoqKioaGhoKCgnp6enZ2dnZ2dnJycmpqamZmZl5eXlpaWlZWVlZWVk5OTkpKSkJCQj4+PjY2NjIyMjIyMi4uLiYmJiIiIhoaGhYWFhYWFhISEgoKCgYGBgICAfn5+fX19fX19e3t7enp6eHh4d3d3dXV1dXV1dHR0c3NzcXFxcHBwb29v////////+fn58vLy6urq5OTk3Nzc1dXV1dXVzc3Nx8fHv7+/uLi4sLCwsLCwqqqqoqKinJyclJSUjY2NhYWFhYWFf39/eHh4cHBwampqYmJiYmJiW1tbU1NTTU1NRUVFPz8/Nzc3Nzc3MDAwKCgoIiIiGhoaExMTExMTCwsLBQUFAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "gray": "/////////////////////////////////////////////////////////////////////////////////////v7+/f39/Pz8+/v7+vr6+fn59/f39vb29fX19PT09PT08/Pz8vLy8fHx8PDw7u7u7e3t7Ozs6+vr6urq6enp6Ojo5+fn5ubm5OTk4+Pj4uLi4eHh4ODg39/f3t7e3d3d3d3d29vb2tra2dnZ2NjY19fX1tbW1dXV1NTU09PT0dHR0NDQz8/Pzs7Ozc3NzMzMy8vLysrKyMjIx8fHxsbGxcXFxcXFxMTEw8PDwsLCwcHBwMDAvr6+vb29vLy8u7u7urq6ubm5uLi4t7e3tbW1tLS0s7OzsrKysbGxsLCwr6+vrq6urq6ura2tq6urqqqqqampqKiop6enpqampaWlpKSko6OjoqKioKCgn5+fnp6enZ2dnJycm5ubmpqamZmZmJiYlpaWlpaWlZWVlJSUk5OTkpKSkZGRkJCQj4+PjY2NjIyMi4uLioqKiYmJiIiIh4eHhoaGhYWFg4ODgoKCgYGBgICAf39/f39/fn5+fX19fHx8enp6eXl5eHh4d3d3dnZ2dXV1dHR0c3NzcnJycHBwb29vbm5ubW1tbGxsa2trampqaWlpaWlpZ2dnZmZmZWVlZGRkY2NjYmJiYWFhYGBgX19fXV1dXFxcW1tbWlpaWVlZWFhYV1dXVlZWVVVVVFRUUlJSUVFRUVFRUFBQT09PTk5OTU1NTExMS0tLSkpKSEhIR0dHRkZGRUVFREREQ0NDQkJCQUFBPz8/Pj4+PT09PDw8Ozs7Ojo6Ojo6OTk5ODg4Nzc3NTU1NDQ0MzMzMjIyMTExMDAwLy8vLi4uLCwsKysrKioqKSkpKCgoJycnJiYmJSUlJCQkIiIiIiIiISEhICAgHx8fHh4eHR0dHBwcGxsbGRkZGBgYFxcXFhYWFRUVFBQUExMTEhISERERDw8PDg4ODQ0NDAwMCwsLCwsLCgoKCQkJCAgIBgYGBQUFBAQEAwMDAgICAQEBAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"};
  /* GIBS Band 13 colour -> brightness temperature (°C), learned against our GOES-19 BT grids: [T, r, g, b] */
  const GIBS_IR = [[-76, 129, 129, 129], [-75, 100, 100, 100], [-74, 82, 82, 82], [-73, 54, 54, 54], [-72, 34, 29, 29], [-71, 22, 5, 5], [-70, 34, 0, 0], [-69, 51, 0, 0], [-68, 78, 0, 0], [-67, 104, 0, 0], [-66, 130, 0, 0], [-65, 154, 0, 0], [-64, 183, 0, 0], [-63, 205, 0, 0], [-62, 243, 0, 0], [-61, 255, 6, 0], [-60, 255, 27, 0], [-59, 255, 53, 0], [-58, 255, 79, 0], [-57, 255, 103, 0], [-56, 255, 133, 0], [-55, 255, 154, 0], [-54, 255, 179, 0], [-53, 255, 204, 0], [-52, 255, 230, 0], [-51, 247, 245, 0], [-50, 220, 255, 0], [-49, 190, 255, 0], [-48, 170, 255, 0], [-47, 146, 255, 0], [-46, 123, 255, 0], [-45, 102, 255, 0], [-44, 68, 255, 0], [-43, 44, 255, 0], [-42, 26, 247, 6], [-41, 16, 212, 28], [-40, 24, 205, 29], [-39, 0, 193, 32], [-38, 0, 182, 39], [-37, 0, 170, 43], [-36, 0, 149, 53], [-35, 0, 128, 59], [-34, 0, 106, 69], [-33, 0, 85, 79], [-32, 0, 64, 90], [-31, 0, 39, 105], [-30, 0, 16, 119], [-29, 0, 26, 128], [-28, 0, 47, 140], [-27, 0, 75, 156], [-26, 0, 102, 171], [-25, 0, 128, 181], [-24, 0, 153, 193], [-23, 0, 172, 207], [-22, 0, 193, 220], [-21, 0, 216, 233], [-20, 0, 230, 241], [-19, 184, 197, 197], [-18, 193, 194, 194], [-17, 191, 191, 192], [-16, 188, 189, 189], [-15, 186, 186, 186], [-14, 183, 183, 183], [-13, 181, 181, 181], [-12, 179, 179, 179], [-11, 176, 176, 176], [-10, 174, 174, 174], [-9, 171, 171, 171], [-8, 168, 168, 168], [-7, 166, 166, 166], [-6, 163, 163, 163], [-5, 161, 161, 161], [-4, 158, 158, 158], [-3, 156, 156, 156], [-2, 153, 153, 153], [-1, 150, 150, 150], [0, 148, 148, 148], [1, 145, 145, 145], [2, 143, 143, 143], [3, 140, 140, 140], [4, 137, 137, 137], [5, 135, 135, 135], [6, 132, 132, 132], [7, 129, 129, 129], [8, 127, 127, 127], [9, 125, 125, 125], [10, 122, 122, 122], [11, 119, 119, 119], [12, 117, 117, 117], [13, 114, 114, 114], [14, 112, 112, 112], [15, 109, 109, 109], [16, 106, 106, 106], [17, 104, 104, 104], [18, 101, 101, 101], [19, 99, 99, 99], [20, 96, 96, 96], [21, 94, 94, 94], [22, 91, 91, 91], [23, 88, 88, 88], [24, 86, 86, 86], [25, 83, 83, 83], [26, 81, 81, 81], [27, 78, 78, 78], [28, 75, 75, 75], [29, 73, 73, 73], [30, 71, 71, 71], [31, 67, 67, 67], [32, 65, 65, 65], [33, 63, 63, 63], [34, 60, 60, 60], [35, 56, 56, 56], [36, 55, 55, 55], [37, 53, 53, 53]];

  /* repaint a GIBS Band 13 frame in one of our ramps: colour -> brightness temperature -> ramp colour, per pixel.
     GIBS draws the coldest tops (below about -73 °C) in greys that also mean warm ground, so a grey blob counts as
     a cold core only when it sits inside the black/dark-red ring (most of its edge colder than -60 °C) and stays small. */
  const dec = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
  const RAMP = Object.fromEntries(Object.entries(RAMPS).map(([k, v]) => [k, dec(v)]));
  let CUBE = null, WARM = null, COLD = null;
  const isGray = r => Math.max(r[1], r[2], r[3]) - Math.min(r[1], r[2], r[3]) < 12;
  function fit1(pts, v) {   // pts [[gray, T]] sorted by gray; linear with end extrapolation
    let i = 1; while (i < pts.length - 1 && pts[i][0] < v) i++;
    const a = pts[i - 1], b = pts[i]; return a[1] + (b[1] - a[1]) * (v - a[0]) / ((b[0] - a[0]) || 1);
  }
  function tables() {
    if (CUBE) return;
    const col = GIBS_IR.filter(r => !isGray(r));
    CUBE = new Float32Array(32768);
    for (let i = 0; i < 32768; i++) {
      const r = ((i >> 10) << 3) + 4, g = (((i >> 5) & 31) << 3) + 4, b = ((i & 31) << 3) + 4; let best = 1e9, T = 0;
      for (const c of col) { const d = (r - c[1]) ** 2 + (g - c[2]) ** 2 + (b - c[3]) ** 2; if (d < best) { best = d; T = c[0]; } }
      CUBE[i] = T;
    }
    const warm = GIBS_IR.filter(r => r[0] >= -19 && isGray(r)).map(r => [r[1], r[0]]).sort((a, b) => a[0] - b[0]);
    const cold = GIBS_IR.filter(r => r[0] <= -73).map(r => [r[1], r[0]]).sort((a, b) => a[0] - b[0]);
    WARM = new Float32Array(256); COLD = new Float32Array(256);
    for (let v = 0; v < 256; v++) { WARM[v] = Math.max(-30, Math.min(55, fit1(warm, v))); COLD[v] = v < 54 ? -72 : Math.max(-100, fit1(cold, v)); }
  }
  function recolor(src, w, h, ramp) {
    tables(); const R = RAMP[ramp];
    const cv = document.createElement("canvas"); cv.width = w; cv.height = h; const x = cv.getContext("2d", { willReadFrequently: true });
    x.drawImage(src, 0, 0, w, h); const id = x.getImageData(0, 0, w, h), d = id.data, n = w * h;
    const T = new Float32Array(n), G = new Int16Array(n).fill(-1);
    for (let i = 0, p = 0; i < n; i++, p += 4) {
      if (d[p + 3] < 8) { T[i] = NaN; continue; }
      const r = d[p], g = d[p + 1], b = d[p + 2], mx = Math.max(r, g, b), mn = Math.min(r, g, b);
      if (mx - mn < 12) { const v = (r + g + b) / 3 | 0; G[i] = v; T[i] = WARM[v]; }
      else T[i] = CUBE[((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3)];
    }
    const seen = new Uint8Array(n), comp = new Int32Array(n), MAXC = Math.max(4000, n * .03 | 0);
    for (let s = 0; s < n; s++) {
      if (G[s] < 0 || seen[s]) continue;
      // only start from grey pixels touching the cold ring
      const sx = s % w; let seed = false;
      for (const j of [s - 1, s + 1, s - w, s + w]) if (j >= 0 && j < n && Math.abs((j % w) - sx) <= 1 && G[j] < 0 && T[j] <= -69) seed = true;
      if (!seed) continue;
      let len = 0, head = 0, ok = true, edgeCold = 0, edge = 0; comp[len++] = s; seen[s] = 1;
      while (head < len) {
        const i = comp[head++], ix = i % w;
        if (ix === 0 || ix === w - 1 || i < w || i >= n - w) ok = false;
        for (const j of [i - 1, i + 1, i - w, i + w]) {
          if (j < 0 || j >= n || Math.abs((j % w) - ix) > 1) continue;
          if (G[j] >= 0) { if (!seen[j] && Math.abs(G[j] - G[i]) < 70) { seen[j] = 1; if (len < MAXC) comp[len++] = j; else ok = false; } }
          else if (!Number.isNaN(T[j])) { edge++; if (T[j] <= -60) edgeCold++; }
        }
      }
      if (ok && edge && edgeCold / edge >= .7) for (let k = 0; k < len; k++) T[comp[k]] = COLD[G[comp[k]]];
    }
    for (let i = 0, p = 0; i < n; i++, p += 4) {
      if (Number.isNaN(T[i])) { d[p + 3] = 0; continue; }
      const k = Math.max(0, Math.min(300, Math.round((T[i] + 100) * 2))) * 3;
      d[p] = R[k]; d[p + 1] = R[k + 1]; d[p + 2] = R[k + 2]; d[p + 3] = 255;
    }
    x.putImageData(id, 0, 0); return cv;
  }
  const rampCSS = name => { const R = RAMP[name]; if (!R) return ""; const st = [];
    for (let T = 40; T >= -95; T -= 15) { const k = Math.round((T + 100) * 2) * 3; st.push(`rgb(${R[k]},${R[k + 1]},${R[k + 2]})`); } return `linear-gradient(90deg,${st.join(",")})`; };
  let TIMES = {};
  const R = 6378137, mx = lon => lon * Math.PI / 180 * R, my = lat => R * Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360));
  const lonOf = x => x / R * 180 / Math.PI, latOf = y => (2 * Math.atan(Math.exp(y / R)) - Math.PI / 2) * 180 / Math.PI;
  const satFor = lon => { lon = ((lon + 540) % 360) - 180; return lon >= -112 && lon <= -15 ? "east" : lon < -112 && lon >= -178 ? "west" : "him"; };
  const layerOf = (sat, band) => { const s = SATS[sat]; return `${s.pre}_${s.bands[band] || s.bands.ir}`; };
  const has = (sat, band) => !!SATS[sat].bands[band] && !!(TIMES[layerOf(sat, band)] || []).length;

  /* the shared playhead */
  const P = { ramp: "tat", t: 0, playing: true, speed: 1, hours: 3, opacity: 1, last: 0, loops: new Set(), subs: new Set() };
  function span() {
    let end = 0; for (const L of P.loops) if (L.frames.length) end = Math.max(end, L.frames[L.frames.length - 1].t);
    return end ? [end - P.hours * 36e5, end] : null;
  }
  /* the master clock = the first loop's frame stamps; every loop shows its newest frame at or before P.t */
  function master() { for (const L of P.loops) if (L.ready && L.frames.length) return L; return null; }
  function frame(now) {
    const s = span(), M = master();
    if (s && M) {
      const F = M.frames;
      if (P.t < s[0] || P.t > s[1] || F.findIndex(f => f.t >= P.t - 1) < 0) P.t = F[F.length - 1].t;
      if (P.playing) {
        const k = F.findIndex(f => f.t >= P.t - 1), interval = 1000 / SPEEDS[P.speed].fps * (k === F.length - 1 ? DWELL_NEWEST : 1);
        if (!P.last) P.last = now;
        if (now - P.last >= interval) { P.last = now; P.t = F[(k + 1) % F.length].t; }
      }
      for (const L of P.loops) L.draw(P.t);
      for (const f of P.subs) f(P.t, s);
    } else if (s) { for (const L of P.loops) L.draw(P.t); for (const f of P.subs) f(P.t, s); }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  class Loop {
    constructor(map, id, before, opt = {}) {
      Object.assign(this, { map, id, before, band: opt.band || "ir", frames: [], on: false, gen: 0, bbox: null, maxPx: opt.maxPx || 2400 });
      this.canvas = document.createElement("canvas"); this.ctx = this.canvas.getContext("2d");
      map.on("moveend", () => { if (this.on) this.maybeReload(); });
    }
    get sat() { return satFor(this.map.getCenter().lng); }
    get layer() { let s = this.sat, b = this.band; if (!has(s, b)) b = "ir"; return layerOf(s, b); }
    get current() { return this.cur || this.frames[this.frames.length - 1]; }
    setBand(b) { this.band = b; if (this.on) this.reload(true); }
    show(on) {
      this.on = on;
      if (on) { P.loops.add(this); this.reload(true); }
      else { P.loops.delete(this); this.gen++; if (this.map.getLayer(this.id)) this.map.setLayoutProperty(this.id, "visibility", "none"); }
    }
    view() {
      const b = this.map.getBounds(), c = this.map.getContainer(), pad = .15;
      let x0 = mx(b.getWest()), x1 = mx(b.getEast()), y0 = my(Math.max(-80, b.getSouth())), y1 = my(Math.min(80, b.getNorth()));
      const dx = (x1 - x0) * pad, dy = (y1 - y0) * pad; x0 -= dx; x1 += dx; y0 -= dy; y1 += dy;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      let w = c.clientWidth * (1 + 2 * pad) * dpr, h = c.clientHeight * (1 + 2 * pad) * dpr; const k = Math.min(1, this.maxPx / Math.max(w, h));
      return { x0, x1, y0, y1, w: Math.max(64, Math.round(w * k)), h: Math.max(64, Math.round(h * k)), z: this.map.getZoom(), layer: this.layer };
    }
    maybeReload() {
      const b = this.bbox, m = this.map.getBounds(); if (!b) return this.reload(true);
      const inside = mx(m.getWest()) >= b.x0 && mx(m.getEast()) <= b.x1 && my(Math.max(-80, m.getSouth())) >= b.y0 && my(Math.min(80, m.getNorth())) <= b.y1;
      if (!inside || Math.abs(this.map.getZoom() - b.z) > .5 || this.layer !== b.layer) { clearTimeout(this.rt); this.rt = setTimeout(() => this.reload(false), 300); }
    }
    url(v, iso) {
      return `${WMS}?SERVICE=WMS&REQUEST=GetMap&VERSION=1.3.0&LAYERS=${v.layer}&STYLES=&CRS=EPSG:3857&BBOX=${v.x0.toFixed(0)},${v.y0.toFixed(0)},${v.x1.toFixed(0)},${v.y1.toFixed(0)}` +
        `&WIDTH=${v.w}&HEIGHT=${v.h}&FORMAT=image/png&TRANSPARENT=TRUE&TIME=${iso}`;
    }
    /* progressive = the band changed (show the newest frame at once); otherwise keep playing the old set until the new one is in */
    async reload(progressive) {
      const gen = ++this.gen, v = this.view(), ts = TIMES[v.layer] || []; if (!ts.length) return;
      const end = Date.parse(ts[ts.length - 1]);
      const fr = ts.filter(t => Date.parse(t) >= end - P.hours * 36e5 - 1).map(t => ({ t: Date.parse(t), iso: t, img: null }));
      const load = f => new Promise(res => { const im = new Image(); im.crossOrigin = "anonymous"; im.decoding = "async";
        im.onload = async () => { try { await im.decode(); } catch (e) {} f.raw = im; f.img = this.paint(im, v); if (f.img !== im) await new Promise(r => setTimeout(r, 0)); res(); };
        im.onerror = () => res(); im.src = this.url(v, f.iso); });
      const order = [fr.length - 1, ...fr.map((_, i) => i).slice(0, -1).reverse()];
      this.loading = { done: 0, total: fr.length }; this.emit();
      // the newest frame goes up first (static) while the rest of the loop preloads; play starts only when all are decoded
      if (progressive) { await load(fr[order[0]]); if (gen !== this.gen) return; this.ready = false; this.install(v, fr.filter(f => f.img)); this.loading.done = 1; this.emit(); }
      let i = progressive ? 1 : 0;
      const worker = async () => { while (i < order.length && gen === this.gen) { const f = fr[order[i++]]; await load(f); this.loading.done++; this.emit(); } };
      await Promise.all(Array.from({ length: 6 }, worker));
      if (gen !== this.gen) return;
      this.loading = null;
      if (progressive) { this.frames = fr.filter(x => x.img); this.ready = true; this.key = null; } else this.install(v, fr.filter(f => f.img));
      this.ready = true; this.emit();
    }
    paint(im, v) { return v.layer.includes("Band13") && P.ramp !== "native" ? recolor(im, v.w, v.h, P.ramp) : im; }
    repaint() { const v = this.bbox; if (!v) return; for (const f of this.frames) if (f.raw) f.img = this.paint(f.raw, v); this.key = null; this.draw(P.t, true); }
    install(v, frames) {
      this.bbox = v; this.frames = frames; this.key = null;
      const c = this.canvas; c.width = v.w; c.height = v.h;
      const co = [[lonOf(v.x0), latOf(v.y1)], [lonOf(v.x1), latOf(v.y1)], [lonOf(v.x1), latOf(v.y0)], [lonOf(v.x0), latOf(v.y0)]], m = this.map;
      if (!m.getSource(this.id)) {
        m.addSource(this.id, { type: "canvas", canvas: c, coordinates: co, animate: true });
        m.addLayer({ id: this.id, type: "raster", source: this.id, paint: { "raster-fade-duration": 0, "raster-opacity": P.opacity, "raster-resampling": "linear" } }, this.before);
      } else m.getSource(this.id).setCoordinates(co);
      m.setLayoutProperty(this.id, "visibility", this.on ? "visible" : "none");
      this.draw(P.t, true); this.emit();
    }
    draw(t, force) {
      const F = this.frames; if (!F.length || !this.on) return;
      let f = F[0]; for (const x of F) { if (x.t <= t + 1) f = x; else break; }
      if (!this.ready) f = F[F.length - 1];
      if (!force && this.key === f.t) return; this.key = f.t;
      const c = this.canvas, x = this.ctx; x.clearRect(0, 0, c.width, c.height); x.drawImage(f.img, 0, 0, c.width, c.height);
      this.cur = f;
    }
    opacity(o) { if (this.map.getLayer(this.id)) this.map.setPaintProperty(this.id, "raster-opacity", o); }
    emit() { for (const f of P.subs) f(P.t, span()); }
  }

  return {
    Loop, P, SATS, BANDS, SPEEDS, satFor, layerOf, has, rampCSS,
    RAMPS: { tat: "TAT", bd: "Dvorak BD", gray: "Grayscale", native: "NASA" },
    setRamp(r) { P.ramp = r; for (const L of P.loops) L.repaint(); },
    setTimes(t) { const was = JSON.stringify(TIMES); TIMES = t || {}; return was !== JSON.stringify(TIMES); },
    times: () => TIMES, span,
    step(d) { const L = [...P.loops][0]; if (!L || !L.frames.length) return; P.playing = false;
      const F = L.frames; let i = F.findIndex(f => f.t >= P.t - 1); if (i < 0) i = F.length - 1; i = Math.max(0, Math.min(F.length - 1, i + d)); P.t = F[i].t; },
    seek(p) { const s = span(), M = [...P.loops][0]; if (!s || !M) return; const F = M.frames; P.t = F[Math.min(F.length - 1, Math.round(p * (F.length - 1)))].t; },
    setOpacity(o) { P.opacity = o; for (const L of P.loops) L.opacity(o); },
    setHours(h) { P.hours = h; for (const L of P.loops) L.reload(false); }
  };
})();
