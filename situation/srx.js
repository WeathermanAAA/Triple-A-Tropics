/* Situation Room extras: viewer settings (units, time zone), shareable + saved views, and export (MP4 loop, PNG snapshot).
   Loaded after sr.js and shares its globals (MAP, ON, LY, TABS, SatX loops, U, ...).
   Export composites the stage the way the viewer sees it: the map canvas, then the DOM overlays (city pills, storm and
   forecast markers, the header frame, legend, clock) painted onto a 2D canvas. Video goes through the explorer's
   WebCodecs MP4 encoder (/satellite/explorer/loop_export.js) with a WebM MediaRecorder fallback. */
"use strict";

/* ---------------- settings ---------------- */
const SETOPT = {
  wind: { t: "Wind", o: { kt: "kt", mph: "mph", kmh: "km/h", ms: "m/s" } },
  pres: { t: "Pressure", o: { mb: "mb", inhg: "inHg" } },
  tz: { t: "Time zone", o: { storm: "Storm local", local: "My time", utc: "UTC" } }
};
function saveUnits() { try { localStorage.setItem("sr.units", JSON.stringify(U)); } catch (e) {} }
function buildSettings() {
  const p = $("setp");
  p.innerHTML = `<div class="lyr-h"><b>Settings</b><button data-x aria-label="Close">×</button></div>` +
    Object.entries(SETOPT).map(([k, g]) => `<h5>${g.t}</h5><div class="pseg">${Object.entries(g.o).map(([v, l]) => `<button data-k="${k}" data-v="${v}" class="${U[k] === v ? "on" : ""}">${l}</button>`).join("")}</div>`).join("") +
    `<div class="pnote">Storm local follows the NHC advisory's zone. Saved in this browser.</div>`;
  p.querySelector("[data-x]").onclick = () => panel("setp", false);
  p.querySelectorAll("[data-k]").forEach(b => b.onclick = () => { U[b.dataset.k] = b.dataset.v; saveUnits(); buildSettings(); rerender(); });
}
/* re-render everything that prints a wind, a pressure or a clock time */
function rerender() {
  if (!D) return;
  setZone(D.text?.issued);
  vitals(); dials(); railKey(); railRecon(); crawl();
  const v = document.querySelector(".c-int .ctab button.on")?.dataset.v;
  if (typeof gdmCard === "function") gdmCard();
  if (ON.has("ens")) { lclear("ens"); LY.ens.off(); LY.ens.on(false); }
  if (v === "ens") ensChart(); else if (v === "guid") guidanceBoard(); else intensityChart();
  for (const id of ["points", "mw", "ascat", "fields", "fixes"]) if (ON.has(id)) { lclear(id); LY[id].off(); LY[id].on(false); }
  if (TABS[TAB]) frame(...TABS[TAB].hdr());
  legendNow(); creditNow(); if (ON.has("sat")) { LCKEY = ""; buildLoopBar(); }
}

/* ---------------- panels (share + settings sit where the layers panel does) ---------------- */
const PBTN = { vwp: "vwBtn", setp: "setBtn", lyr: "lyrBtn" };
function panel(id, open) {
  for (const [p, b] of Object.entries(PBTN)) {
    if (p !== id && !open) continue;
    const o = p === id ? open : false, el = $(p); if (!el) continue;
    el.classList.toggle("open", o); $(b)?.classList.toggle("on", o); $(b)?.setAttribute("aria-expanded", o);
  }
  if (open && id === "vwp") buildViews();
  if (open && id === "setp") buildSettings();
}
function srxInit() {
  $("vwBtn").onclick = () => panel("vwp", !$("vwp").classList.contains("open"));
  $("setBtn").onclick = () => panel("setp", !$("setp").classList.contains("open"));
  $("lyrBtn").addEventListener("click", () => { if ($("lyr").classList.contains("open")) { panel("vwp", false); panel("setp", false); } });
}

/* ---------------- views: the URL hash always carries the current view; named views live in this browser ---------------- */
let RESTORING = false, HASHT = null;
function viewState() {
  const c = MAP.getCenter(), L = SATL();
  return { tab: TABS[TAB]?.id, l: [...ON].filter(id => LY[id]).join(","), c: `${c.lng.toFixed(2)},${c.lat.toFixed(2)},${MAP.getZoom().toFixed(2)}`,
    src: SRC, b: L?.band, r: SatX.P.ramp, h: SatX.P.hours, f: FLD.field, fh: FLD.fh };
}
const viewHash = v => "#" + Object.entries(v).filter(([, x]) => x != null && x !== "").map(([k, x]) => `${k}=${encodeURIComponent(x)}`).join("&");
function parseView() { return Object.fromEntries(new URLSearchParams(location.hash.slice(1))); }
function hashNow() {
  if (!MAP || RESTORING || TAB < 0) return;
  clearTimeout(HASHT); HASHT = setTimeout(() => history.replaceState(null, "", location.pathname + location.search + viewHash(viewState())), 400);
}
async function applyView(v) {
  RESTORING = true;
  try {
    if (v.c) CAMLOCK = true;
    const i = TABS.findIndex(t => t.id === v.tab && (!t.ok || t.ok())); if (i >= 0 && i !== TAB) await tab(i, true);
    if (v.f && FIELDN[v.f]) FLD.field = v.f;
    if (v.fh != null && v.fh !== "" && isFinite(+v.fh)) FLD.fh = +v.fh;
    if (v.r && SatX.RAMPS[v.r] && v.r !== SatX.P.ramp) SatX.setRamp(v.r);
    if (v.src && v.src !== SRC && ["fd", "live", "meso"].includes(v.src)) await setSrc(v.src);
    if (v.b && v.b !== SATL().band && v.b in BANDSET()) SATL().setBand(v.b);
    if (v.h && isFinite(+v.h) && +v.h !== SatX.P.hours) SatX.setHours(+v.h);
    if (v.l != null) { const want = new Set(v.l.split(",").filter(id => LY[id])); for (const id of Object.keys(LY)) if (ON.has(id) !== want.has(id)) setLayer(id, want.has(id), false); }
    if (v.c) { const [lo, la, z] = v.c.split(",").map(Number); if ([lo, la, z].every(isFinite)) MAP.jumpTo({ center: [lo, la], zoom: z }); }
    if (ON.has("sat")) buildLoopBar();
    if (ON.has("fields") && FLD.idx) { fieldBar(true); fieldShow(); }
    if (TABS[TAB]) frame(...TABS[TAB].hdr());
  } finally { RESTORING = false; hashNow(); }
}
function loadViews() { try { return JSON.parse(localStorage.getItem("sr.views") || "[]") || []; } catch (e) { return []; } }
function storeViews(a) { try { localStorage.setItem("sr.views", JSON.stringify(a.slice(0, 40))); return true; } catch (e) { return false; } }
function buildViews() {
  const p = $("vwp"), list = loadViews();
  p.innerHTML = `<div class="lyr-h"><b>Share &amp; views</b><button data-x aria-label="Close">×</button></div>
    <div class="pact"><button data-a="link">Copy link</button><button data-a="png">Snapshot PNG</button></div>
    <h5>Save this view</h5><form class="vnew"><input maxlength="40" placeholder="Name it" aria-label="View name"><button>Save</button></form>
    <h5>Saved views</h5>${list.length ? list.map((x, i) => `<div class="vrow"><button data-i="${i}"><b>${esc(x.name)}</b><small>${esc(x.storm)} · ${esc((TABS.find(t => t.id === x.v.tab) || {}).label || x.v.tab || "")}</small></button><button data-d="${i}" aria-label="Delete ${esc(x.name)}">×</button></div>`).join("") : `<div class="pnote">None yet. A view keeps the tab, layers, camera and satellite settings.</div>`}
    <div class="pnote" id="vmsg"></div>`;
  const msg = t => { const m = p.querySelector("#vmsg"); if (m) m.textContent = t; };
  p.querySelector("[data-x]").onclick = () => panel("vwp", false);
  p.querySelector('[data-a="link"]').onclick = async () => {
    const url = location.origin + location.pathname + location.search + viewHash(viewState());
    try { await navigator.clipboard.writeText(url); msg("Link copied."); } catch (e) { history.replaceState(null, "", url); msg("Link is in the address bar."); }
  };
  p.querySelector('[data-a="png"]').onclick = e => snapshot(e.currentTarget);
  p.querySelector(".vnew").onsubmit = e => {
    e.preventDefault(); const name = e.target.querySelector("input").value.trim() || `${(D.nhc.name || SID).toUpperCase()} ${TABS[TAB]?.label || ""}`.trim();
    const a = loadViews(); a.unshift({ name, sid: SID, storm: (D.nhc.name || SID).toUpperCase(), v: viewState(), t: Date.now() });
    if (storeViews(a)) buildViews(); else msg("This browser is not letting the page save.");
  };
  p.querySelectorAll("[data-i]").forEach(b => b.onclick = () => {
    const x = loadViews()[+b.dataset.i]; if (!x) return;
    if (x.sid !== SID) { location.href = `${location.pathname.replace(/\/cyclolab\/\w+\//, "/situation/")}?storm=${x.sid}${viewHash(x.v)}`; return; }
    applyView(x.v);
  });
  p.querySelectorAll("[data-d]").forEach(b => b.onclick = () => { const a = loadViews(); a.splice(+b.dataset.d, 1); storeViews(a); buildViews(); });
}

/* ---------------- export: paint the stage onto a canvas ---------------- */
const IMG = new Map();
function img(src) {
  if (!IMG.has(src)) IMG.set(src, new Promise(res => { const i = new Image(); i.crossOrigin = "anonymous"; i.onload = () => res(i); i.onerror = () => res(null); i.src = src; }));
  if (IMG.size > 300) IMG.delete(IMG.keys().next().value);
  return IMG.get(src);
}
function svgSrc(el) {
  const c = el.cloneNode(true), cs = getComputedStyle(el);
  c.setAttribute("xmlns", "http://www.w3.org/2000/svg"); c.setAttribute("width", parseFloat(cs.width) * 2); c.setAttribute("height", parseFloat(cs.height) * 2);
  c.querySelectorAll(".spin").forEach(g => g.removeAttribute("class"));
  return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(new XMLSerializer().serializeToString(c));
}
/* the element's on-screen scale and rotation, composed from every CSS transform between it and the stage */
function xf(el, stop) {
  let m = new DOMMatrix();
  for (let e = el; e && e !== stop; e = e.parentElement) { const t = getComputedStyle(e).transform; if (t && t !== "none") m = new DOMMatrix(t).multiply(m); }
  return { s: Math.hypot(m.a, m.b) || 1, a: Math.atan2(m.b, m.a) };
}
function gradient(g, bg, r) {
  const body = bg.slice(bg.indexOf("(") + 1, bg.lastIndexOf(")")), parts = []; let d = 0, cur = "";
  for (const ch of body) { if (ch === "(") d++; if (ch === ")") d--; if (ch === "," && !d) { parts.push(cur.trim()); cur = ""; } else cur += ch; }
  parts.push(cur.trim());
  let ang = 180; if (/deg$|^to /.test(parts[0])) { const p0 = parts.shift(); ang = /deg$/.test(p0) ? parseFloat(p0) : { "to right": 90, "to left": 270, "to top": 0, "to bottom": 180 }[p0] ?? 180; }
  const th = ang * Math.PI / 180, dx = Math.sin(th), dy = -Math.cos(th), len = Math.abs(r.width * dx) + Math.abs(r.height * dy), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  const G = g.createLinearGradient(cx - dx * len / 2, cy - dy * len / 2, cx + dx * len / 2, cy + dy * len / 2);
  parts.forEach((p, i) => { const m = p.match(/^((?:rgba?|hsla?|color)\([^)]*\)|#[0-9a-f]+|[a-z]+)\s*(?:([\d.]+)%)?/i); const col = m ? m[1] : p, at = m && m[2] != null ? +m[2] / 100 : i / Math.max(1, parts.length - 1);
    try { G.addColorStop(Math.max(0, Math.min(1, at)), col); } catch (e) {} });
  return G;
}
const SKIP = /maplibregl-ctrl|maplibregl-canvas|lyr|qgrid|swp|strip|flash/;
async function paint(g, el, stage) {
  if (!(el instanceof Element) || SKIP.test(el.className?.baseVal ?? el.className ?? "")) return;
  const cs = getComputedStyle(el); if (cs.display === "none" || cs.visibility === "hidden" || +cs.opacity < .02) return;
  const r = el.getBoundingClientRect(); if (!r.width && !r.height && !el.children.length) return;
  g.save(); g.globalAlpha *= +cs.opacity;
  try {
    if (el instanceof SVGSVGElement || el.tagName === "IMG") {
      const im = await img(el.tagName === "IMG" ? el.currentSrc || el.src : svgSrc(el)); if (!im) return;
      const t = xf(el, stage), w = parseFloat(cs.width) * t.s, h = parseFloat(cs.height) * t.s;
      g.translate(r.left + r.width / 2, r.top + r.height / 2); g.rotate(t.a); g.drawImage(im, -w / 2, -h / 2, w, h); return;
    }
    const s = xf(el, stage).s, br = cs.borderTopLeftRadius, rad = Math.min(r.width, r.height) / 2 * (br.endsWith("%") ? Math.min(1, parseFloat(br) / 50) : 0) || Math.min(parseFloat(br) * s || 0, Math.min(r.width, r.height) / 2);
    const box = () => { g.beginPath(); g.roundRect ? g.roundRect(r.left, r.top, r.width, r.height, rad) : g.rect(r.left, r.top, r.width, r.height); };
    if (cs.backgroundColor && !/rgba\(.*,\s*0\)|transparent/.test(cs.backgroundColor)) { g.fillStyle = cs.backgroundColor; box(); g.fill(); }
    if (/^linear-gradient/.test(cs.backgroundImage)) { g.fillStyle = gradient(g, cs.backgroundImage, r); box(); g.fill(); }
    const bw = ["Top", "Right", "Bottom", "Left"].map(k => parseFloat(cs[`border${k}Width`]) || 0);
    if (bw.every(b => b > 0)) { g.strokeStyle = cs.borderTopColor; g.lineWidth = bw[0] * s; box(); g.stroke(); }
    else if (bw[2] > 0) { g.fillStyle = cs.borderBottomColor; g.fillRect(r.left, r.bottom - bw[2] * s, r.width, bw[2] * s); }
    if (cs.overflow === "hidden") { box(); g.clip(); }
    for (const n of el.childNodes) {
      if (n.nodeType === 3 && n.textContent.trim()) {
        const rg = document.createRange(); rg.selectNodeContents(n); const rr = rg.getClientRects()[0]; if (!rr) continue;
        let txt = n.textContent.replace(/\s+/g, " ").trim(); if (cs.textTransform === "uppercase") txt = txt.toUpperCase();
        g.save(); g.font = `${cs.fontStyle} ${cs.fontWeight} ${parseFloat(cs.fontSize) * s}px ${cs.fontFamily}`; g.textBaseline = "middle"; g.fillStyle = cs.color;
        if ("letterSpacing" in g && parseFloat(cs.letterSpacing)) g.letterSpacing = `${parseFloat(cs.letterSpacing) * s}px`;
        if (cs.textShadow && cs.textShadow !== "none") { const T = g.getTransform(); g.shadowColor = "rgba(0,0,0,.6)"; g.shadowOffsetX = g.shadowOffsetY = 1.5 * s * T.a; g.shadowBlur = 1.5 * s * T.a; }
        g.fillText(txt, rr.left, rr.top + rr.height / 2 + .5 * s); g.restore();
      } else if (n.nodeType === 1) await paint(g, n, stage);
    }
  } finally { g.restore(); }
}
const nextRender = () => new Promise(res => { MAP.once("render", res); MAP.triggerRepaint(); });
/* map pixels are only valid inside the render event, so they are copied there */
async function composite(cv) {
  const g = cv.getContext("2d"), st = $("stage"), R = st.getBoundingClientRect();
  await nextRender();
  await new Promise(res => { MAP.once("render", () => { g.drawImage(MAP.getCanvas(), 0, 0, cv.width, cv.height); res(); }); MAP.triggerRepaint(); });
  g.save(); g.scale(cv.width / R.width, cv.height / R.height); g.translate(-R.left, -R.top);
  g.beginPath(); g.rect(R.left, R.top, R.width, R.height); g.clip();
  for (const el of [$("ovl"), ...$("map").querySelectorAll(".maplibregl-marker"), $("legend"), $("tau"), $("frame")]) await paint(g, el, st);
  g.restore();
}
function download(blob, name) {
  const a = document.createElement("a"), u = URL.createObjectURL(blob); a.href = u; a.download = name; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(u), 6e4);
}
const stamp = t => { const d = new Date(t); return `${d.getUTCFullYear()}${z2(d.getUTCMonth() + 1)}${z2(d.getUTCDate())}_${z2(d.getUTCHours())}${z2(d.getUTCMinutes())}Z`; };
const fname = (kind, t, ext) => `${(D.nhc.name || SID).toLowerCase()}_${kind}_${stamp(t)}.${ext}`;
let BUSY = false;
async function snapshot(btn) {
  if (BUSY) return; BUSY = true; const was = btn?.textContent, P = SatX.P, play = P.playing; if (btn) btn.textContent = "Rendering…";
  P.playing = false;   // hold the loop still while the frame is painted
  try {
    const st = $("stage"), w = Math.min(2560, Math.round(st.clientWidth * 2)), cv = document.createElement("canvas");
    cv.width = w; cv.height = Math.round(w * st.clientHeight / st.clientWidth); await composite(cv);
    const blob = await new Promise(r => cv.toBlob(r, "image/png")); download(blob, fname(TABS[TAB]?.id || "map", Date.now(), "png"));
  } catch (e) { console.error(e); } finally { BUSY = false; P.playing = play; P.last = 0; if (btn) btn.textContent = was; }
}
/* the loop as a video: each frame is stepped on the shared playhead, composited, encoded; the newest frame holds like the live loop */
async function exportLoop(btn) {
  if (BUSY) return;
  const P = SatX.P, L = [...P.loops][0];
  if (!L?.frames.length || L.loading) { if (btn) { btn.textContent = "Wait…"; setTimeout(() => btn.textContent = "MP4", 1500); } return; }
  BUSY = true; const wasPlaying = P.playing, t0 = P.t; P.playing = false;
  const st = $("stage"), W = 1280, H = Math.round(W * st.clientHeight / st.clientWidth / 2) * 2, cv = document.createElement("canvas"); cv.width = W; cv.height = H;
  const F = L.frames.map(f => f.t), fps = SatX.SPEEDS[P.speed].fps, hold = Math.round(fps * .8);
  let enc = null, rec = null, track = null, chunks = [];
  try {
    if (window.LoopExport && LoopExport.available()) enc = await LoopExport.create({ width: W, height: H, fps, frames: F.length + hold, maxBytes: 40e6, maxBitrate: 8e6 });
    if (!enc) {
      if (!cv.captureStream || !window.MediaRecorder) throw new Error("this browser cannot record video");
      const stream = cv.captureStream(0); track = stream.getVideoTracks()[0];
      const mime = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"].find(m => MediaRecorder.isTypeSupported(m));
      rec = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 8e6 }); rec.ondataavailable = e => e.data.size && chunks.push(e.data); rec.start();
    }
    const push = async () => { if (enc) await enc.addFrame(cv); else { track.requestFrame(); await sleep(1000 / fps); } };
    for (let k = 0; k < F.length; k++) {
      P.t = F[k]; for (const l of P.loops) l.draw(P.t, true);
      await composite(cv); await push();
      if (btn) btn.textContent = `${Math.round((k + 1) / F.length * 100)}%`;
    }
    for (let k = 0; k < hold; k++) await push();
    let blob, ext = "mp4";
    if (enc) blob = (await enc.finish()).blob;
    else { await new Promise(r => { rec.onstop = r; rec.stop(); }); blob = new Blob(chunks, { type: "video/webm" }); ext = "webm"; }
    download(blob, fname(`${SRC === "fd" ? "" : SRC + "_"}${SATL().band}`, F[F.length - 1], ext));
  } catch (e) { console.error("export failed", e); if (btn) btn.title = "Export failed: " + (e.message || e); }
  finally { BUSY = false; P.t = t0; P.playing = wasPlaying; P.last = 0; if (btn) btn.textContent = "MP4"; }
}
