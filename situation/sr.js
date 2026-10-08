/* Situation Room (CycloLab revamp): one storm, everything about it, broadcast grade.
   Data: situation/<sid>.json on the CDN (situation/build_situation.py) = NHC advisory + cone, best track, ATCF guidance,
   microwave, recon (current mission + TCPOD), GOES-19 IR frames.  Map: the baked basemap (situation/bake_tiles.py).
   Frame: Tulsa-Live's header (img/header-tat.webp) + title tag.  Dials: Tulsa-Live's dial set. */
"use strict";
const $ = id => document.getElementById(id);
const esc = t => String(t ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const z2 = n => String(n).padStart(2, "0");
const sleep = ms => new Promise(r => setTimeout(r, ms));
const Q = new URLSearchParams(location.search);
let SID = ((location.pathname.match(/\/cyclolab\/(\w{8})\//) || [])[1] || Q.get("storm") || "").toLowerCase();
const IN_CYCLOLAB = /\/cyclolab\//.test(location.pathname);
const FAST = Q.has("fast");
/* everything comes from the site's CDN: situation/<sid>.json (update-situation.yml), the baked basemap
   (bake-situation-tiles.yml), microwave + recon products; satellite loops come from NASA GIBS via sat.js */
const CDN = "https://cdn.triple-a-tropics.com";

/* ---------------- storm vocabulary ---------------- */
const CAT = [{ n: "TD", k: "D", c: "#3fa4ff", lo: 0 }, { n: "TS", k: "S", c: "#46c56a", lo: 34 }, { n: "1", k: "1", c: "#ffe14d", lo: 64 }, { n: "2", k: "2", c: "#ff9a2f", lo: 83 },
  { n: "3", k: "3", c: "#f5333c", lo: 96 }, { n: "4", k: "4", c: "#e33ad4", lo: 113 }, { n: "5", k: "5", c: "#b03bff", lo: 137 }];
const catOf = kt => { let c = CAT[0]; for (const x of CAT) if ((kt || 0) >= x.lo) c = x; return c; };
const catIdx = kt => CAT.indexOf(catOf(kt));
const inkOn = c => { const n = parseInt(c.slice(1), 16), r = n >> 16, g = (n >> 8) & 255, b = n & 255; return (r * .299 + g * .587 + b * .114) > 150 ? "#141414" : "#fff"; };
const mph = kt => Math.round(kt * 1.15078 / 5) * 5;
/* viewer settings (units, time zone), remembered in this browser; the gear in the tab bar sets them (srx.js) */
const U = (() => { let u = {}; try { u = JSON.parse(localStorage.getItem("sr.units") || "{}") || {}; } catch (e) {} return { wind: u.wind || "kt", pres: u.pres || "mb", tz: u.tz || "storm" }; })();
const WUN = { kt: { f: 1, l: "kt", w: "knots", step: 20 }, mph: { f: 1.15078, l: "mph", w: "mph", step: 25, r5: true }, kmh: { f: 1.852, l: "km/h", w: "km/h", step: 40, r5: true }, ms: { f: .514444, l: "m/s", w: "m/s", step: 10 } };
const WF = () => WUN[U.wind] || WUN.kt;
const wnd = kt => kt == null || !isFinite(kt) ? "–" : WF().r5 ? Math.round(kt * WF().f / 5) * 5 : Math.round(kt * WF().f);   // NHC rounds mph and km/h to 5
const wdel = kt => Math.round(kt * WF().f), wl = () => WF().l;
const walt = kt => U.wind === "kt" ? `${mph(kt)} mph` : `${Math.round(kt)} kt`;
const spd = mi => U.wind === "mph" ? mi : U.wind === "kmh" ? Math.round(mi * 1.609344) : U.wind === "ms" ? Math.round(mi * .44704) : Math.round(mi / 1.15078);   // NHC motion is in mph
const prs = mb => mb == null || !isFinite(mb) ? "–" : U.pres === "inhg" ? (mb * .02953).toFixed(2) : Math.round(mb);
const pl = () => U.pres === "inhg" ? "inHg" : "mb";
const palt = mb => U.pres === "inhg" ? `${mb} mb` : `${(mb * .02953).toFixed(2)} inHg`;
const catWord = kt => kt >= 64 ? `Cat ${catOf(kt).n}` : kt >= 34 ? "Trop Storm" : "Trop Dep";
/* forecast-point phase from NHC's KMZ style id: x* = post-tropical, l_ = remnant low, sd_/ss_ = subtropical, else tropical */
const phase = p => { const y = (p && p.style) || ""; return /^x/.test(y) ? "pt" : /^l_/.test(y) ? "low" : /^s[ds]_/.test(y) ? "st" : "tc"; };
const ptWord = p => { const f = phase(p); return f === "pt" ? "Post-Trop" : f === "low" ? "Remnant Low" : f === "st" ? (p.kt >= 34 ? "Subtrop Storm" : "Subtrop Dep") : catWord(p.kt); };
/* non-tropical marker, same shapes as the season tracks maps: triangle = post-tropical / low, square = subtropical */
const phaseMark = (f, x, y, r, fill, stroke, sw) => f === "st" ? `<rect x="${(x - r * .85).toFixed(1)}" y="${(y - r * .85).toFixed(1)}" width="${(r * 1.7).toFixed(1)}" height="${(r * 1.7).toFixed(1)}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`
  : `<path d="M${x.toFixed(1)} ${(y - r * 1.15).toFixed(1)}L${(x + r * 1.05).toFixed(1)} ${(y + r * .7).toFixed(1)}L${(x - r * 1.05).toFixed(1)} ${(y + r * .7).toFixed(1)}Z" fill="${fill}" stroke="${stroke}" stroke-width="${sw}" stroke-linejoin="round"/>`;
const KIND = { TD: "Tropical Depression", TS: "Tropical Storm", HU: "Hurricane", MH: "Major Hurricane", STD: "Subtropical Depression", STS: "Subtropical Storm",
  PTC: "Potential Tropical Cyclone", PT: "Post-Tropical Cyclone", PC: "Post-Tropical Cyclone" };
const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
const compass = d => COMPASS[Math.round(((d % 360) + 360) % 360 / 22.5) % 16];
const TAT_GLYPH = "M 16.37,-28.27 C 13.58,-28.13 11.51,-27.90 9.23,-27.49 C 1.27,-26.06 -5.88,-22.70 -10.92,-18.02 C -14.83,-14.40 -17.41,-10.06 -18.49,-5.32 C -18.95,-3.30 -19.15,-1.42 -19.15,0.91 C -19.15,2.53 -19.09,3.28 -18.89,4.45 C -18.38,7.38 -17.47,9.46 -15.41,12.37 C -13.88,14.54 -13.43,15.31 -13.20,16.13 C -13.11,16.44 -13.09,16.62 -13.09,17.14 C -13.10,17.93 -13.20,18.32 -13.67,19.28 C -15.30,22.59 -18.65,24.93 -23.49,26.14 C -25.26,26.58 -27.29,26.87 -29.18,26.95 L -30.00,26.98 L -29.65,27.06 C -27.33,27.62 -24.41,28.05 -21.57,28.27 C -20.04,28.38 -16.31,28.38 -14.80,28.27 C -12.93,28.13 -11.43,27.95 -9.77,27.67 C -0.59,26.14 7.56,22.03 12.68,16.37 C 16.22,12.45 18.28,8.10 18.93,3.13 C 19.64,-2.25 18.99,-6.47 16.84,-10.16 C 16.48,-10.80 15.79,-11.82 14.99,-12.95 C 13.61,-14.89 13.18,-15.77 13.12,-16.83 C 13.07,-17.61 13.23,-18.26 13.71,-19.23 C 14.97,-21.79 17.38,-23.84 20.67,-25.16 C 23.13,-26.14 26.24,-26.77 29.15,-26.87 L 30.00,-26.90 L 29.67,-26.98 C 29.13,-27.12 27.57,-27.44 26.66,-27.58 C 24.96,-27.87 23.39,-28.05 21.66,-28.18 C 20.72,-28.25 17.16,-28.30 16.37,-28.27 Z";
function glyph(kt, { spin = true, plate = false, south = false, ph = "tc" } = {}) {
  const c = catOf(kt), L = c.k;
  if (ph !== "tc") return `<svg viewBox="-34 -34 68 68">${plate ? `<circle r="31" fill="#0c1634" stroke="#fff" stroke-width="3"/>` : ""}${phaseMark(ph, 0, 2, 17, c.c, "none", 0)}</svg>`;
  const body = kt >= 34 ? `<g transform="scale(${south ? "-0.62,0.62" : "0.62"})"><g class="${spin ? "spin" : ""}"><path d="${TAT_GLYPH}" fill="${c.c}"/></g></g>`
    : `<circle r="15" fill="${c.c}"/>`;
  return `<svg viewBox="-34 -34 68 68">${plate ? `<circle r="31" fill="#0c1634" stroke="#fff" stroke-width="3"/>` : ""}${body}
    <text x="0" y="1" text-anchor="middle" dominant-baseline="central" font-family="Metropolis,Arial" font-weight="900" font-size="${plate ? 17 : 20}" fill="${kt >= 34 && kt < 64 ? "#fff" : inkOn(c.c) === "#fff" ? "#fff" : "#141414"}" ${plate ? 'stroke="rgba(0,0,0,.45)" stroke-width="2" paint-order="stroke"' : ""}>${L}</text></svg>`;
}

/* ---------------- time, in the advisory's own zone ---------------- */
const TZO = { EDT: -4, EST: -5, CDT: -5, CST: -6, MDT: -6, MST: -7, PDT: -7, PST: -8, HST: -10, AST: -4, ChST: 10 };
let TZ = "UTC", OFF = 0;
function setZone(issued) { const m = (issued || "").match(/\b([ECMPHA]S?[DT]T|[ECMPA]ST|HST)\b/); TZ = m && TZO[m[1]] != null ? m[1] : "UTC"; OFF = TZO[TZ] || 0;
  if (U.tz === "utc") { TZ = "UTC"; OFF = 0; }
  else if (U.tz === "local") { OFF = -new Date().getTimezoneOffset() / 60; try { TZ = new Intl.DateTimeFormat("en-US", { timeZoneName: "short" }).formatToParts(new Date()).find(p => p.type === "timeZoneName").value; } catch (e) { TZ = "LOCAL"; } } }
const local = t => { const d = new Date(typeof t === "string" ? Date.parse(t) : t); return new Date(d.getTime() + OFF * 36e5); };
const DOW = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"], MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
function hm(t, min = true) { const d = local(t), h = d.getUTCHours(), m = d.getUTCMinutes(); if (U.tz === "utc") return `${z2(h)}:${z2(m)}`; return `${h % 12 || 12}${min && m ? ":" + z2(m) : ""} ${h < 12 ? "AM" : "PM"}`; }
const dayhm = t => `${DOW[local(t).getUTCDay()]} ${hm(t)}`;
const ago = t => { const m = Math.round((Date.now() - Date.parse(t)) / 6e4); return m < 60 ? `${m} min ago` : m < 48 * 60 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`; };
const until = t => { let s = Math.max(0, Math.round(((typeof t === "number" ? t : Date.parse(t)) - Date.now()) / 1e3)); const h = Math.floor(s / 3600); s -= h * 3600; return `${h}:${z2(Math.floor(s / 60))}:${z2(s % 60)}`; };
const isoMs = s => Date.parse(s.length === 17 ? s.replace("Z", ":00Z") : s);

/* ---------------- state ---------------- */
let D = null, MAP = null, TAB = -1, K = 1, CAMLOCK = false;   // CAMLOCK: a restored view owns the camera, so no tab fly-to
const hashKick = () => { if (typeof hashNow === "function") hashNow(); };
const ease = p => 1 - Math.pow(1 - p, 3);

/* ---------------- boot ---------------- */
(async function boot() {
  if (!SID) {
    try { const ix = await (await fetch(`${CDN}/situation/index.json?t=${Date.now()}`, { cache: "no-store" })).json();
      const live = ix.active.filter(s => s.has_bundle), pick = live.find(s => ix.rooms.includes(s.id)) || live.sort((a, b) => (+b.kt || 0) - (+a.kt || 0))[0];
      if (pick) { SID = pick.id; history.replaceState(null, "", `?storm=${SID}`); } } catch (e) {}
  }
  if (!SID) { $("vit").innerHTML = `<div class="v-name"><span class="k">Situation Room</span><b>All quiet</b></div><div class="rnote">No active NHC storms.</div>`; return; }
  await refresh(true);
  /* near-instant updates: the live writer (situation/live.py) publishes a ~200-byte heartbeat with a content hash per
     document; poll it every 8 s and re-download the bundle only when its hash moves. The 60 s full refresh is the
     fallback for when the heartbeat is missing or stale. */
  setInterval(liveTick, 8e3);
  setInterval(() => { if (!LIVE.ok || Date.now() - LIVE.seen > 90e3) refresh(false); }, 60e3);
  setInterval(tick, 1000);
})();

const LIVE = { ok: false, seen: 0, h: null, t: null };
async function liveTick() {
  let j; try { j = await (await fetch(`${CDN}/situation/live.json?t=${Date.now()}`, { cache: "no-store" })).json(); } catch (e) { LIVE.ok = false; return; }
  const age = Date.now() - Date.parse(j.t); LIVE.ok = age < 5 * 60e3; LIVE.t = j.t;
  if (!LIVE.ok) { stale(true); return; }
  LIVE.seen = Date.now();
  const h = `${j.h?.[SID] || ""}|${j.h?.index || ""}`;
  if (LIVE.h !== null && h !== LIVE.h) refresh(false);
  LIVE.h = h;
}
const sig = d => ({ adv: [d.nhc.advisory, d.nhc.intensity, d.nhc.pressure, (d.nhc.points || []).length, d.nhc.lastUpdate].join("|"), best: (d.best || []).length,
  models: d.models?.cycle || "", mw: (d.mw?.overpasses || []).slice(-1)[0]?.id || "", recon: (d.recon?.flights || [d.recon?.current || {}]).map(f => `${f.mission_id}|${f.n_obs || 0}|${f.valid_end || ""}`).join(","),
  ships: d.ships?.init || "", plan: `${d.recon?.plan?.number || ""}|${(d.recon?.plan?.flights || []).length}`, sat: d.sat?.latest || "" });
const NEEDS = { fields: [], radii: ["adv"], ww: ["adv"], ascat: ["adv"], glm: [], cone: ["adv"], track: ["adv"], points: ["adv"], best: ["best", "adv"], sat: ["sat"], mw: ["mw"], recon: ["recon"], fixes: ["plan"], models: ["models"], gefs: ["models"] };
async function refresh(first) {
  let d;
  try { d = await (await fetch(`${CDN}/situation/${SID}.json?t=${Date.now()}`, { cache: "no-store" })).json(); } catch (e) { stale(true); return; }
  try { const ix = await (await fetch(`${CDN}/situation/index.json?t=${Date.now()}`, { cache: "no-store" })).json();
    if (SatX.setTimes(ix.gibs) && !first) for (const L of SatX.P.loops) L.reload(false); } catch (e) {}
  if (d.err) { if (first) $("vit").innerHTML = `<div class="v-name"><span class="k">${esc(SID.toUpperCase())}</span><b>Not found</b></div><div class="rnote">${esc(d.err)}</div>`; stale(true); return; }
  if (IN_CYCLOLAB && d.room_open === false) { location.reload(); return; }   // room closed: hand the page back to CycloLab
  const was = D ? sig(D) : null, now = sig(d), ch = k => !was || was[k] !== now[k];
  D = d; D.fetched = Date.now(); setZone(d.text?.issued); stale(false);
  D.kt = +d.nhc.intensity || 0; D.mb = +d.nhc.pressure || null;
  const t0 = advTime();
  D.fc = (d.nhc.points || []).map((p, i) => ({ ...p, t: i ? validMs(p.valid) || t0 + p.hr * 36e5 : t0 }));
  D.peak = D.fc.reduce((m, p) => (p.kt || 0) > (m.kt || 0) ? p : m, { kt: D.kt, hr: 0, t: t0 });
  document.documentElement.style.setProperty("--cat", catOf(D.kt).c);
  document.documentElement.style.setProperty("--catInk", inkOn(catOf(D.kt).c));
  if (first) {
    await initMap(); buildTabs(); buildLayers(); vitals(); dials(); railKey(); railRecon(); intensityChart(); guidanceBoard(); crawl(); syncData();
    await Promise.race([MAP.once("idle"), sleep(3000)]);
    if (typeof srxInit === "function") srxInit();
    const hv = typeof parseView === "function" ? parseView() : {}, ti = TABS.findIndex(t => t.id === hv.tab && (!t.ok || t.ok()));
    if (hv.c) CAMLOCK = true;
    await tab(ti >= 0 ? ti : 0, true); if (Object.keys(hv).length && typeof applyView === "function") await applyView(hv); return;
  }
  if (ch("adv")) { vitals(); railKey(); crawl(); flash(`New advisory ${D.nhc.advisory}`, `${wnd(D.kt)} ${wl()} · ${prs(D.mb)} ${pl()}`); }
  else vitalsQuiet();
  if (ch("adv") || ch("mw") || ch("plan") || ch("best")) dials();
  if (ch("adv") || ch("models") || ch("best")) { intensityChart(); guidanceBoard(); }
  if (!$("shipsView").hidden && ["ships", "adv", "models", "best"].some(ch)) shipsView();
  if (ch("recon") || ch("plan")) railRecon();
  syncData();
  if (ch("adv")) { nowMarker(); if (QUAD.on) QUAD.cells.forEach(quadData); }
  let hdr = false;
  for (const id of DATA) if (ON.has(id) && NEEDS[id].some(ch)) { lclear(id); LY[id].off(); LY[id].on(ch("adv") && ["cone", "track", "points"].includes(id)); hdr = hdr || TABS[TAB]?.layers.includes(id); }
  if (hdr) frame(...TABS[TAB].hdr());
  legendNow(); creditNow();
}
/* a new advisory lands: a bar sweeps across the top of the map */
function flash(t, s) {
  const f = document.createElement("div"); f.className = "flash"; f.innerHTML = `<b>${esc(t)}</b><span>${esc(s)}</span>`;
  $("stage").appendChild(f); setTimeout(() => f.remove(), 7000);
}
function stale(bad) { const e = $("vUpd"); if (e) e.classList.toggle("bad", !!bad); }

/* '1:00 PM CDT October 09, 2026' -> ms */
function validMs(v) {
  const m = (v || "").match(/(\d+):(\d+) (AM|PM) (\w+) (\w+) (\d+), (\d+)/); if (!m) return null;
  const mo = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"].indexOf(m[5]);
  return Date.UTC(+m[7], mo, +m[6], (+m[1] % 12) + (m[3] === "PM" ? 12 : 0) - (TZO[m[4]] ?? 0), +m[2]);
}
/* advisory time: the forecast's initial point, in UTC */
function advTime() {
  const p = (D.nhc.points || [])[0]; const m = p && p.valid.match(/(\d+):(\d+) (AM|PM) (\w+) (\w+) (\d+), (\d+)/);
  if (!m) return Date.parse(D.nhc.lastUpdate) || Date.now();
  const mo = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"].indexOf(m[5]);
  const h = (+m[1] % 12) + (m[3] === "PM" ? 12 : 0), off = TZO[m[4]] ?? 0;
  return Date.UTC(+m[7], mo, +m[6], h - off, +m[2]);
}

/* ---------------- storm vitals (left column, top) ---------------- */
function vitals() {
  const n = D.nhc, kt = D.kt, c = catOf(kt), kind = KIND[n.classification] || "Tropical Cyclone";
  const nowI = catIdx(kt), pkI = catIdx(D.peak.kt);
  const act = (D.active || []);
  $("vit").innerHTML = `
    ${act.length > 1 ? `<div class="v-sw">${act.map(s => `<a href="${IN_CYCLOLAB ? `/cyclolab/${s.id.toLowerCase()}/` : `?storm=${s.id.toLowerCase()}`}" class="${s.id.toLowerCase() === SID ? "on" : ""}" style="--c:${catOf(+s.kt).c}"><i></i>${esc(s.name)}</a>`).join("")}</div>` : ""}
    <div class="v-name"><span class="k">${esc(kind)}</span><b>${esc((n.name || "").toUpperCase())}</b></div>
    <div class="v-main"><div class="v-g"><svg viewBox="-34 -34 68 68"><g transform="scale(.9)"><g class="spin"><path d="${TAT_GLYPH}" fill="${c.c}"/></g></g>
      <text x="0" y="1" text-anchor="middle" dominant-baseline="central">${c.k}</text></svg></div>
      <div class="v-t"><b>${wnd(kt)}<small>${wl().toUpperCase()}</small></b><span>${walt(kt).toUpperCase()} · ${kt >= 64 ? "CAT " + c.n : catWord(kt).toUpperCase()}</span></div></div>
    <div class="v-lad">${CAT.map((x, i) => `<i class="${i <= nowI ? "on" : i <= pkI ? "fc" : ""}" style="--c:${x.c};--i:${i}">${i === nowI ? "<em>NOW</em>" : i === pkI && pkI > nowI ? '<em class="pk">PEAK</em>' : ""}<b>${x.n}</b></i>`).join("")}</div>
    <div class="v-grid">
      <div><small>Pressure</small><b>${prs(D.mb)} ${pl()}</b></div>
      <div><small>Moving</small><b>${n.movementDir != null ? `${compass(+n.movementDir)} ${spd(+n.movementSpeed)} ${wl()}` : "–"}</b></div>
</div>
    <div class="v-adv"><svg viewBox="0 0 44 44" class="nring"><circle cx="22" cy="22" r="18"/><circle class="v" id="nRing" cx="22" cy="22" r="18" pathLength="1"/></svg>
      <div><small>Advisory ${esc(n.advisory || "–")} · ${hm(advTime())} ${TZ}</small><b>Next <span id="nTime">–</span></b><em id="nCount"></em></div></div>
    <div class="v-upd" id="vUpd">Live · updated <span id="vAgo">just now</span></div>`;
  document.title = `${(n.name || "").toUpperCase()} · Situation Room · Triple-A-Tropics`;
  inCard("vit", 0);
}

function nextAdvisory() {
  const m = (D.text?.next || "").match(/at (\d{1,2})(\d\d) (AM|PM) (\w+)/);
  if (!m) return null;
  const off = TZO[m[4]] ?? OFF, h = (+m[1] % 12) + (m[3] === "PM" ? 12 : 0), now = new Date();
  let t = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), h - off, +m[2]);
  while (t < Date.now() - 6e4) t += 864e5;
  while (t - Date.now() > 864e5) t -= 864e5;
  return t;
}
function vitalsQuiet() { const e = $("vAgo"); if (e) e.textContent = "just now"; }
function tick() {
  if (!D) return;
  const ag = $("vAgo"), last = Math.max(D.fetched || 0, LIVE.ok ? LIVE.seen : 0);
  if (ag && last) { const s = Math.round((Date.now() - last) / 1e3); ag.textContent = s < 50 ? "just now" : `${Math.round(s / 60)} min ago`; }
  const t = nextAdvisory();
  if (t && $("nTime")) { $("nTime").textContent = `${hm(t)} ${TZ}`; $("nCount").textContent = `in ${until(t)}`;
    $("nRing").style.strokeDashoffset = String(1 - Math.max(0, Math.min(1, (t - Date.now()) / (6 * 36e5)))); }
  const rc = document.querySelector("[data-count]");
  if (rc) rc.textContent = until(+rc.dataset.count).slice(0, -3);
  document.querySelectorAll(".fl em[data-t]").forEach(e => { const ms = +e.dataset.t - Date.now(); e.innerHTML = ms > 0 ? `T-${until(+e.dataset.t).slice(0, -3)}<br><small>${e.dataset.w}</small>` : `${e.dataset.w}`; });
}

/* ---------------- dials (Tulsa-Live's dial set) ---------------- */
const R = 80, C = 2 * Math.PI * R, L270 = C * .75;
const face = `<circle cx="100" cy="100" r="96" fill="#101b33"/><circle cx="100" cy="100" r="96" fill="none" stroke="rgba(160,190,255,.25)" stroke-width="1.5"/>`;
function arc(x) {
  return face + `<circle cx="100" cy="100" r="${R}" fill="none" stroke="#4a5670" stroke-width="20" stroke-dasharray="${L270} ${C}" transform="rotate(135 100 100)"/>
    <circle class="dm-val" cx="100" cy="100" r="${R}" fill="none" stroke="${x.color}" stroke-width="20" stroke-dasharray="${L270 * x.frac} ${C}" style="--len:${L270 * x.frac}" transform="rotate(135 100 100)"/>`;
}
function sshs(x) {   // Saffir-Simpson arc: one segment per category, lit up to the value, needle on the value
  const lo = 0, hi = 160, A = v => Math.min(1, Math.max(0, (v - lo) / (hi - lo)));
  let s = face;
  CAT.forEach((c, i) => { const a = A(c.lo), b = A(CAT[i + 1]?.lo ?? hi), lit = x.kt >= c.lo;
    s += `<circle cx="100" cy="100" r="${R}" fill="none" stroke="${lit ? c.c : "#4a5670"}" stroke-opacity="${lit ? 1 : .55}" stroke-width="20"
      stroke-dasharray="${Math.max(0, L270 * (b - a) - 2)} ${C}" stroke-dashoffset="${-L270 * a}" transform="rotate(135 100 100)" class="${lit ? "dm-seg" : ""}" style="--s:${i}"/>`; });
  const rot = 270 * A(x.kt);
  s += `<g class="dm-needle" style="--rot:${rot}deg"><g transform="rotate(135 100 100)"><path d="M${100 + R + 12} 100l14 -8v16z" fill="#fff" stroke="#101b33" stroke-width="1.5"/></g></g>`;
  if (x.ghost != null && x.ghost > x.kt) { const g = 135 + 270 * A(x.ghost), gx = 100 + (R + 15) * Math.cos(g * Math.PI / 180), gy = 100 + (R + 15) * Math.sin(g * Math.PI / 180);
    s += `<circle cx="${gx}" cy="${gy}" r="5" fill="none" stroke="#e8b53a" stroke-width="2.5"/>`; }
  return s;
}
function compassFace(x) {
  let tk = ""; for (let i = 0; i < 36; i++) { const a = i * 10 * Math.PI / 180, c = Math.cos(a), s = Math.sin(a), r1 = i % 9 === 0 ? 66 : 72;
    tk += `<line x1="${100 + r1 * c}" y1="${100 + r1 * s}" x2="${100 + 80 * c}" y2="${100 + 80 * s}" stroke="#9fb3d6" stroke-width="${i % 9 === 0 ? 3 : 1.5}"/>`; }
  const Lb = [["N", 100, 33], ["E", 167, 105], ["S", 100, 176], ["W", 33, 105]].map(([l, a, b]) => `<text x="${a}" y="${b}" text-anchor="middle" font-family="Metropolis" font-weight="800" font-size="13" fill="#fff">${l}</text>`).join("");
  return face + `<circle cx="100" cy="100" r="84" fill="none" stroke="#2c3d63" stroke-width="10"/>${tk}${Lb}<g class="dm-needle" style="--rot:${x.dir}deg"><path d="M100 18 l9 20 h-18z" fill="#ffc24a" stroke="#101b33" stroke-width="1.5"/></g>`;
}
function countFace(x) {   // countdown: ticks for 24 h, lit arc = time remaining, sweep hand
  let tk = ""; for (let i = 0; i < 24; i++) { const a = (i * 15 - 90) * Math.PI / 180;
    tk += `<line x1="${100 + 70 * Math.cos(a)}" y1="${100 + 70 * Math.sin(a)}" x2="${100 + (i % 6 ? 75 : 79) * Math.cos(a)}" y2="${100 + (i % 6 ? 75 : 79) * Math.sin(a)}" stroke="#9fb3d6" stroke-width="${i % 6 ? 1.5 : 3}"/>`; }
  return face + `<circle cx="100" cy="100" r="${R + 6}" fill="none" stroke="#2c3d63" stroke-width="8"/>
    <circle class="dm-val" cx="100" cy="100" r="${R + 6}" fill="none" stroke="${x.color}" stroke-width="8" stroke-dasharray="${2 * Math.PI * (R + 6) * x.frac} 999" style="--len:${2 * Math.PI * (R + 6) * x.frac}" transform="rotate(-90 100 100)"/>
    ${tk}<g class="dm-sweep"><line x1="100" y1="100" x2="100" y2="30" stroke="${x.color}" stroke-width="2" stroke-opacity=".55"/></g>`;
}
/* one dial at a time, like Tulsa-Live's sidebar: 7 s each, the next two queued below; click to advance, hover to hold */
let DL = [], DK = 0, DT = null;
function dials() {
  const L = [], kt = D.kt, n = D.nhc;
  L.push({ t: "Max Sustained Wind", face: sshs({ kt, ghost: D.peak.kt }), big: wnd(kt), lab: WF().w, sub: `${walt(kt)} · ${catWord(kt)}` });
  if (D.mb) L.push({ t: "Minimum Pressure", face: arc({ color: "#7cc3ea", frac: Math.max(.04, Math.min(1, (1012 - D.mb) / 122)) }), big: prs(D.mb), lab: pl(), sub: palt(D.mb) });
  const ch = change24();
  L.push({ t: "24-Hour Wind Change", face: arc({ color: ch > 0 ? "#ff6b5e" : ch < 0 ? "#5dd3ff" : "#9fb3d6", frac: Math.min(1, Math.abs(ch) / 50) || .02 }), big: (ch > 0 ? "+" : "") + wdel(ch), lab: wl(), sub: ch >= 30 ? "Rapid intensification" : ch > 0 ? "Strengthening" : ch < 0 ? "Weakening" : "Steady" });
  const f24 = D.fc.find(p => p.hr >= 24);
  if (f24) { const d = f24.kt - kt; L.push({ t: "NHC Next 24 Hours", face: arc({ color: d >= 30 ? "#ff3b6b" : d > 0 ? "#ff9a2f" : "#5dd3ff", frac: Math.min(1, Math.abs(d) / 50) || .02 }), big: (d > 0 ? "+" : "") + wdel(d), lab: wl(), sub: d >= 30 ? "Rapid intensification" : d >= 15 ? "Strengthening" : d > 0 ? "Slow strengthening" : d < 0 ? "Weakening" : "Steady", hot: d >= 30 ? "#ff3b6b" : null }); }
  if (n.movementDir != null) L.push({ t: "Motion", face: compassFace({ dir: +n.movementDir }), big: compass(+n.movementDir), lab: "", sub: `${spd(+n.movementSpeed)} ${wl()} · ${n.movementDir}°`, small: true });
  const mw = (D.mw?.overpasses || []).filter(o => o.kt).slice(-1)[0];
  L.push({ t: "Microwave Estimate", face: arc({ color: "#b48cff", frac: mw ? Math.min(1, mw.kt / 160) : .02 }), big: mw ? wnd(mw.kt) : "–", lab: mw ? WF().w : "", sub: mw ? `${mw.sensor} · ${ago(mw.t)}` : "no usable pass" });
  L.push({ t: "NHC Forecast Peak", face: sshs({ kt: D.peak.kt }), big: wnd(D.peak.kt), lab: WF().w, sub: `${ptWord(D.peak)} · ${DOW[local(D.peak.t).getUTCDay()]} ${hm(D.peak.t, false)}`, hot: D.peak.kt >= 64 ? catOf(D.peak.kt).c : null });
  /* storm energy, timing, spread */
  const syn = (D.best || []).filter(p => /T(00|06|12|18):00/.test(p.t) && ["TS", "HU", "SS"].includes(p.ty) && p.kt >= 34);
  const ace = syn.reduce((s, p) => s + p.kt * p.kt / 1e4, 0);
  let fAce = 0; for (let h = 6; h <= 120; h += 6) { const k = fcKt(h); if (k != null && k >= 34) fAce += k * k / 1e4; }
  L.push({ t: "Storm ACE so far", face: arc({ color: "#ffb83a", frac: Math.min(1, ace / 30) || .02 }), big: ace.toFixed(1), lab: "ACE", sub: syn.length ? `${syn.length} six-hourly fixes` : "not a storm yet", small: true });
  L.push({ t: "NHC Forecast ACE", face: arc({ color: "#ff9a2f", frac: Math.min(1, fAce / 30) || .02 }), big: fAce.toFixed(1), lab: "ACE", sub: "next 5 days, NHC track", small: true });
  const hu = kt < 64 && D.fc.find(p => p.kt >= 64 && phase(p) === "tc");
  if (hu) { const ms = hu.t - Date.now();
    L.push({ t: "Forecast Hurricane", face: countFace({ color: "#ffe14d", frac: Math.max(.02, Math.min(1, ms / (5 * 864e5))) }), big: `${Math.max(0, Math.round(ms / 36e5))}`, lab: "hours", sub: `by ${dayhm(hu.t)} ${TZ}` }); }
  const mb24 = pressure24(); if (mb24 != null) L.push({ t: "Pressure, 24 Hours", face: arc({ color: mb24 < 0 ? "#ff6b5e" : "#5dd3ff", frac: Math.min(1, Math.abs(mb24) / 40) || .02 }), big: (mb24 > 0 ? "+" : "") + (U.pres === "inhg" ? (mb24 * .02953).toFixed(2) : mb24), lab: pl(), sub: mb24 <= -24 ? "Bombing out" : mb24 < 0 ? "Deepening" : mb24 > 0 ? "Filling" : "Steady" });
  const na = nextAdvisory(); if (na) L.push({ t: "Next Advisory", face: countFace({ color: "#e8b53a", frac: Math.max(.02, Math.min(1, (na - Date.now()) / (6 * 36e5))) }), big: `<span data-count="${na}">${until(na).slice(0, -3)}</span>`, lab: "hrs:min", sub: `${hm(na)} ${TZ}`, small: true });
  const nf = nextFlight();
  if (nf) { const ms = isoMs(nf.fix[0]) - Date.now();
    L.push({ t: "Next Recon Fix", face: countFace({ color: "#ffd24a", frac: Math.max(.02, Math.min(1, ms / 864e5)) }), big: `<span data-count="${isoMs(nf.fix[0])}">${until(isoMs(nf.fix[0])).slice(0, -3)}</span>`, lab: "hrs:min", sub: `${nf.flight.replace(/^FLIGHT \w+ - /, "")} · ${dayhm(isoMs(nf.fix[0]))}`, small: true }); }
  const keepT = DL[DK]?.t; DL = L; const ki = L.findIndex(x => x.t === keepT); DK = ki >= 0 ? ki : Math.min(DK, L.length - 1); showDial();
  const box = $("dials");
  box.onclick = e => { const d = e.target.closest("[data-k]"); DK = d ? +d.dataset.k : (DK + 1) % DL.length; showDial(); dialClock(); };
  dialClock();
}
function dialClock() { clearInterval(DT); DT = setInterval(() => { DK = (DK + 1) % DL.length; showDial(); }, 7000); }
function showDial() {
  const x = DL[DK], box = $("dials"); if (!x) return;
  const nx = [1, 2].map(i => DL[(DK + i) % DL.length]);
  box.innerHTML = `<div class="dh"><b>By the numbers</b><span>${DK + 1} / ${DL.length}</span></div>
    <div class="dm${x.hot ? " hot" : ""}" style="--d:0s${x.hot ? `;--hot:${x.hot}` : ""}">
      <div class="dm-t">${esc(x.t)}</div>
      <div class="dm-dial"><svg viewBox="0 0 200 200">${x.face}</svg>
        <div class="dm-c"><div class="dm-big"${x.small ? ' style="font-size:40px"' : ""} data-n="${typeof x.big === "number" ? x.big : ""}">${typeof x.big === "number" ? 0 : x.big}</div>${x.lab ? `<div class="dm-lab">${esc(x.lab)}</div>` : ""}</div></div>
      <div class="dm-cap">${esc(x.sub)}</div></div>
    <div class="dm-list">${nx.map((d, i) => `<div style="--o:${[.9, .5][i]}" data-k="${(DK + i + 1) % DL.length}">${esc(d.t)}</div>`).join("")}</div>
    <div class="dm-dots">${DL.map((_, i) => `<i data-k="${i}" class="${i === DK ? "on" : ""}"></i>`).join("")}</div>`;
  const dm = box.querySelector(".dm"); dm.classList.add("in");
  box.querySelector(".dm-list").classList.add("in");
  const e = box.querySelector(".dm-big[data-n]"); if (e && e.dataset.n !== "") { const v = +e.dataset.n, t0 = performance.now() + 250;
    const f = now => { const p = Math.max(0, Math.min(1, (now - t0) / 1300)); e.textContent = Math.round(v * ease(p)); if (p < 1) requestAnimationFrame(f); }; requestAnimationFrame(f); }
}

function fcKt(h) { const F = D.fc; for (let i = 1; i < F.length; i++) if (F[i].hr >= h) { const a = F[i - 1], b = F[i]; if (a.kt == null || b.kt == null) return null; return a.kt + (b.kt - a.kt) * (h - a.hr) / ((b.hr - a.hr) || 1); } return null; }
function guidancePeaks() { const A = D.models?.aids || {}; return ["IVCN", "HFAI", "HFBI", "HWFI", "HMNI", "DSHP", "LGEM", "AVNI", "CTCI"].filter(t => A[t]).map(t => Math.max(...aidPts(A[t]).filter(x => x[0] <= 120).map(x => x[3] || 0))).filter(v => v > 0); }
function pressure24() { const b = D.best || [], last = b[b.length - 1]; if (!last || !D.mb) return null; const t = Date.parse(last.t) - 864e5, p = b.find(q => Math.abs(Date.parse(q.t) - t) < 3 * 36e5); return p && p.mb ? D.mb - p.mb : null; }
function change24() {
  const b = D.best || []; if (!b.length) return 0;
  const last = b[b.length - 1], t = Date.parse(last.t) - 864e5, prev = b.find(p => Math.abs(Date.parse(p.t) - t) < 3 * 36e5);
  return prev ? D.kt - prev.kt : 0;
}
/* recon flights in the storm in the last 12 h; RSEL picks which one the map draws (rail rows and legend chips switch it) */
let RSEL = null;
const rFlights = () => D.recon?.flights || (D.recon?.current ? [D.recon.current] : []);
const rCur = () => rFlights().find(f => f.mission_id === RSEL) || D.recon?.current || null;
function pickFlight(mid) { RSEL = mid; const r = TABS.findIndex(t => t.id === "recon");
  if (TABS[TAB]?.id !== "recon" && r >= 0) { tab(r); railRecon(); return; }
  if (ON.has("recon")) { lclear("recon"); LY.recon.off(); LY.recon.on(true); TABS[TAB]?.cam(); legendNow(); } railRecon(); }
document.addEventListener("click", e => { const b = e.target.closest("#legend [data-mid], #cRecon [data-mid]"); if (b) pickFlight(b.dataset.mid); });
function nextFlight() { return (D.recon?.plan?.flights || []).filter(f => f.fix?.length && isoMs(f.fix[f.fix.length - 1]) > Date.now() && /FIX/.test(f.task)).sort((a, b) => isoMs(a.fix[0]) - isoMs(b.fix[0]))[0]; }

/* ---------------- the map: Tulsa-Live's basemap ---------------- */
async function initMap() {
  const st = $("stage");
  MAP = new maplibregl.Map({ container: "map", attributionControl: false, fadeDuration: 0, renderWorldCopies: false, dragRotate: false, pitchWithRotate: false,
    center: [D.nhc.longitudeNumeric, D.nhc.latitudeNumeric], zoom: 4.6,
    style: { version: 8, glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
      sources: { omt: { type: "vector", url: "https://tiles.openfreemap.org/planet" },
        base: { type: "raster", tiles: [`${CDN}/situation/tiles/base/{z}/{x}/{y}.jpg`], tileSize: 256, maxzoom: 6 },
        lines: { type: "raster", tiles: [`${CDN}/situation/tiles/lines/{z}/{x}/{y}.png?v=2`], tileSize: 256, maxzoom: 6 },
        roads: { type: "raster", tiles: [`${CDN}/situation/tiles/roads/{z}/{x}/{y}.png`], tileSize: 256, minzoom: 5, maxzoom: 6 } },
      layers: [{ id: "bg", type: "background", paint: { "background-color": "#2463a0" } }, { id: "base", type: "raster", source: "base", paint: { "raster-fade-duration": 0 } }] } });
  MAP.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
  const ro = new ResizeObserver(() => { K = st.clientWidth / 1027; st.style.setProperty("--k", K); MAP.resize(); }); ro.observe(st);
  K = st.clientWidth / 1027; st.style.setProperty("--k", K);
  await new Promise(r => MAP.on("load", r));
  MAP.on("moveend", hashKick);
  MAINLOOP = new SatX.Loop(MAP, "satloop", "mw"); MESOLOOP = new Meso.Loop(MAP, "mesoloop", "mw"); LIVELOOP = new Meso.Loop(MAP, "liveloop", "mw"); LIVELOOP.visBase = `${CDN}/situation/vis/${SID}/`; LIVELOOP.fdBase = `${CDN}/situation/fd/${SID}/`;
  LIVESEC = Meso.coverLive(...pos()) || await fdCover(); if (LIVESEC) { SRC = "live"; LIVELOOP.sector = LIVESEC; LIVELOOP.center = pos(); }
  const E = { type: "FeatureCollection", features: [] }, gj = id => MAP.addSource(id, { type: "geojson", data: E, lineMetrics: true });
  MAP.addSource("mw", { type: "image", url: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=", coordinates: [[-1, 1], [1, 1], [1, -1], [-1, -1]] });
  MAP.addLayer({ id: "mw", type: "raster", source: "mw", layout: { visibility: "none" }, paint: { "raster-opacity": 0, "raster-opacity-transition": { duration: 700 }, "raster-fade-duration": 0 } });
  MAP.addSource("fld", { type: "image", url: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=", coordinates: [[-1, 1], [1, 1], [1, -1], [-1, -1]] });
  MAP.addLayer({ id: "fld", type: "raster", source: "fld", layout: { visibility: "none" }, paint: { "raster-opacity": .92, "raster-fade-duration": 0, "raster-resampling": "linear" } });
  MAP.addLayer({ id: "lines", type: "raster", source: "lines", paint: { "raster-fade-duration": 0 } });
  MAP.addLayer({ id: "roads", type: "raster", source: "roads", layout: { visibility: "none" }, paint: { "raster-fade-duration": 0, "raster-opacity": .9 } });
  ["cone", "best", "bestpts", "fc", "gefs", "aids", "ofcl", "recon", "sondes"].forEach(gj);
  MAP.addLayer({ id: "cone-fill", type: "fill", source: "cone", paint: { "fill-color": "#ffffff", "fill-opacity": 0, "fill-opacity-transition": { duration: 900 } } });
  MAP.addLayer({ id: "cone-line", type: "line", source: "cone", paint: { "line-color": "#ffffff", "line-width": 2.2, "line-opacity": 0, "line-opacity-transition": { duration: 900 } } });
  ["radii", "radiifc", "ww", "ascat", "glm"].forEach(gj);
  MAP.addLayer({ id: "radiifc", type: "line", source: "radiifc", layout: { visibility: "none" }, paint: { "line-color": ["get", "c"], "line-width": 1, "line-opacity": .55, "line-dasharray": [2, 2] } });
  MAP.addLayer({ id: "radii-fill", type: "fill", source: "radii", layout: { visibility: "none" }, paint: { "fill-color": ["get", "c"], "fill-opacity": .22 } });
  MAP.addLayer({ id: "radii", type: "line", source: "radii", layout: { visibility: "none" }, paint: { "line-color": ["get", "c"], "line-width": 1.8 } });
  MAP.addLayer({ id: "ww-case", type: "line", source: "ww", layout: { visibility: "none", "line-cap": "round" }, paint: { "line-color": "#06101f", "line-width": 9 } });
  MAP.addLayer({ id: "ww", type: "line", source: "ww", layout: { visibility: "none", "line-cap": "round" }, paint: { "line-color": ["get", "c"], "line-width": 6 } });
  MAP.addLayer({ id: "gefs", type: "line", source: "gefs", layout: { "line-cap": "round", visibility: "none" }, paint: { "line-color": "#cfd8e6", "line-width": 1, "line-opacity": .38 } });
  MAP.addLayer({ id: "aids-case", type: "line", source: "aids", layout: { "line-cap": "round", "line-join": "round", visibility: "none" }, paint: { "line-color": "#06101f", "line-width": ["case", ["get", "con"], 5.5, 4], "line-opacity": .55 } });
  MAP.addLayer({ id: "aids", type: "line", source: "aids", layout: { "line-cap": "round", "line-join": "round", visibility: "none" }, paint: { "line-color": ["get", "c"], "line-width": ["case", ["get", "con"], 3.4, 2.2] } });
  MAP.addLayer({ id: "best-line", type: "line", source: "best", layout: { "line-cap": "round" }, paint: { "line-color": "#ffffff", "line-width": 2, "line-dasharray": [0.1, 2.2], "line-opacity": .95 } });
  MAP.addLayer({ id: "best-pts", type: "circle", source: "bestpts", paint: { "circle-radius": 3.4, "circle-color": ["get", "c"], "circle-stroke-color": "#06101f", "circle-stroke-width": 1.2 } });
  MAP.addLayer({ id: "fc-case", type: "line", source: "fc", layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": "#06101f", "line-width": 6.5, "line-opacity": .6 } });
  MAP.addLayer({ id: "fc", type: "line", source: "fc", layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": "#ffffff", "line-width": 3.2 } });
  MAP.addLayer({ id: "recon-case", type: "line", source: "recon", layout: { "line-cap": "round", "line-join": "round", visibility: "none" }, paint: { "line-color": "#06101f", "line-width": 5.5, "line-opacity": .6 } });
  MAP.addLayer({ id: "recon", type: "line", source: "recon", layout: { "line-cap": "round", "line-join": "round", visibility: "none" },
    paint: { "line-width": 3.2, "line-color": ["get", "c"] } });   // each segment wears its observed flight-level wind (FLW)
  gj("fldvec");
  MAP.addLayer({ id: "fldvec", type: "symbol", source: "fldvec", layout: { visibility: "none", "icon-image": ["get", "i"], "icon-rotate": ["get", "d"], "icon-rotation-alignment": "map",
    "icon-size": ["get", "s"], "icon-allow-overlap": false, "icon-padding": 2 } });
  MAP.addLayer({ id: "ascat", type: "symbol", source: "ascat", layout: { visibility: "none", "icon-image": ["get", "i"], "icon-rotate": ["get", "d"], "icon-rotation-alignment": "map",
    "icon-allow-overlap": false, "icon-padding": 1, "symbol-sort-key": ["-", 0, ["get", "kt"]] } });
  MAP.addLayer({ id: "glm", type: "circle", source: "glm", layout: { visibility: "none" }, paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, 1.6, 8, 3], "circle-color": ["get", "c"],
    "circle-stroke-color": "#000", "circle-stroke-width": .4, "circle-opacity": .95 } });
  MAP.addLayer({ id: "sondes", type: "circle", source: "sondes", layout: { visibility: "none" }, paint: { "circle-radius": 4.5, "circle-color": ["get", "c"], "circle-stroke-color": "#fff", "circle-stroke-width": 1.5 } });
  // place names: placed by MapLibre, drawn as Tulsa-Live's navy pills (HTML)
  MAP.addLayer({ id: "cities", type: "symbol", source: "omt", "source-layer": "place",
    filter: ["all", ["in", ["get", "class"], ["literal", ["city", "town"]]], ["<=", ["coalesce", ["get", "rank"], 99], ["step", ["zoom"], 4, 4.5, 5, 5.5, 7, 6.5, 8, 8, 10]]],
    layout: { "text-field": ["upcase", ["coalesce", ["get", "name:en"], ["get", "name"]]], "text-font": ["Noto Sans Bold"], "text-size": 14, "text-padding": 14, "symbol-sort-key": ["coalesce", ["get", "rank"], 99] },
    paint: { "text-opacity": 0 } });
  MAP.on("render", drawLabels);
}
const LABS = new Map();
function drawLabels() {
  const ov = $("ovl"); let fs; try { fs = MAP.queryRenderedFeatures({ layers: ["cities"] }); } catch (e) { return; }
  const seen = new Set(), H = $("stage").clientHeight;
  for (const f of fs) {
    const name = (f.properties["name:en"] || f.properties.name || "").toUpperCase(); if (!name || seen.has(name)) continue;
    const p = MAP.project(f.geometry.coordinates); if (p.y < 140 * K || p.y > H - 22 * K) continue;   // clear of the header frame and the credit line seen.add(name);
    let e = LABS.get(name);
    if (!e) { e = document.createElement("div"); e.className = "lab" + (f.properties.class === "city" ? " big" : ""); e.innerHTML = `<span class="n">${esc(name)}</span>`; ov.appendChild(e); LABS.set(name, e); }
    e.style.transform = `translate(${p.x.toFixed(1)}px,${p.y.toFixed(1)}px) translate(-50%,-50%)`;
  }
  for (const [n, e] of LABS) if (!seen.has(n)) { e.remove(); LABS.delete(n); }
}
const set = (id, d) => MAP.getSource(id)?.setData(d);
const FC = f => ({ type: "FeatureCollection", features: f });
const line = (c, p = {}) => ({ type: "Feature", properties: p, geometry: { type: "LineString", coordinates: c } });
function vis(ids, on) { for (const id of [].concat(ids)) if (MAP.getLayer(id)) MAP.setLayoutProperty(id, "visibility", on ? "visible" : "none"); }

/* model colours (spaghetti) */
const AIDC = { OFCL: "#ffffff", TVCN: "#ff4fa3", IVCN: "#ff4fa3", HCCA: "#ff9ad5", AVNI: "#4fc3ff", AVNO: "#4fc3ff", EMXI: "#ff5252", EMX: "#ff5252", ECMF: "#ff5252",
  HFAI: "#ffd24a", HFSA: "#ffd24a", HFBI: "#ff9a2f", HFSB: "#ff9a2f", HMNI: "#7cff6b", HMON: "#7cff6b", HWFI: "#c08bff", HWRF: "#c08bff", CMCI: "#9be7ff", CMC: "#9be7ff",
  UKXI: "#48e0b0", UKX: "#48e0b0", NVG2: "#b9c3d3", NVGM: "#b9c3d3", CTCI: "#ff8c8c", CTCX: "#ff8c8c", AEMI: "#6fa8ff", AEMN: "#6fa8ff", DSHP: "#e8b53a", LGEM: "#f2e39b", SHIP: "#d1a85a" };
const TRACK_AIDS = ["TVCN", "HCCA", "AVNI", "EMXI", "HFAI", "HFBI", "HMNI", "HWFI", "CMCI", "UKXI", "NVG2", "CTCI", "AEMI"];
const AIDNAME = { TVCN: "Consensus", HCCA: "Corrected cons.", AVNI: "GFS", EMXI: "ECMWF", HFAI: "HAFS-A", HFBI: "HAFS-B", HMNI: "HMON", HWFI: "HWRF", CMCI: "Canadian", UKXI: "UKMET",
  NVG2: "Navy NAVGEM", CTCI: "COAMPS-TC", AEMI: "GFS ens mean", DSHP: "SHIPS", LGEM: "LGEM", IVCN: "Intensity cons.", OFCL: "NHC", HFSA: "HAFS-A", HFSB: "HAFS-B", AVNO: "GFS", AEMN: "GEFS mean", HMON: "HMON", HWRF: "HWRF", SHIP: "SHIPS (no land)" };
const aidPts = a => a.pts.filter((p, i, arr) => p[1] && (i === 0 || p[0] !== arr[i - 1][0]));
/* interpolate an aid's position at tau (hours) */
function at(pts, tau) { for (let i = 1; i < pts.length; i++) if (pts[i][0] >= tau) { const a = pts[i - 1], b = pts[i], f = (tau - a[0]) / ((b[0] - a[0]) || 1); return [a[2] + (b[2] - a[2]) * f, a[1] + (b[1] - a[1]) * f]; } return null; }
function grow(pts, tau) { const c = []; for (const p of pts) { if (p[0] <= tau) c.push([p[2], p[1]]); else { const q = at(pts, tau); if (q) c.push(q); break; } } return c; }

function syncData() {
  if (!MAP) return;
  set("cone", FC((D.nhc.cone || []).map(r => ({ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [r] } }))));
  const b = (D.best || []).filter(p => Date.parse(p.t) > advTime() - 5 * 864e5);
  set("best", FC([line(b.map(p => [p.lon, p.lat]).concat([[+D.nhc.longitudeNumeric, +D.nhc.latitudeNumeric]]))]));
  set("bestpts", FC(b.map(p => ({ type: "Feature", properties: { c: catOf(p.kt).c }, geometry: { type: "Point", coordinates: [p.lon, p.lat] } }))));
}
function wrap(el) { const w = document.createElement("div"); w.className = "fpw"; w.appendChild(el); return w; }
/* ---------------- header frame (Tulsa-Live) ---------------- */
function frame(title, time, sub, leg = "") {
  const now = local(Date.now());
  $("frame").innerHTML = `<img class="hdr" alt="" src="/situation/img/header-tat.webp">
    <div class="tag"><h2>${esc(title)}</h2><div class="trow">${time ? `<div class="tTime">${esc(time)}</div>` : ""}${leg ? `<div class="hleg">${leg}</div>` : ""}</div></div>
    ${sub ? `<div class="tSub"><span><b>${esc(sub.toUpperCase())}</b></span></div>` : ""}
    <div class="datebox"><div class="u">${MON[now.getUTCMonth()]} ${now.getUTCDate()}</div><div class="d">${["SUNDAY", "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY"][now.getUTCDay()]}</div></div>
    <div class="logobox"></div><img class="tatlogo" alt="" src="/situation/img/logo.svg"><div class="mapcred" id="mapcred"></div>`;
  const h = $("frame").querySelector("h2"); let f = 36; while (h.scrollWidth > 520 && f > 20) { f--; h.style.fontSize = f + "px"; }
  const fr = $("frame"); fr.classList.remove("wipe"); void fr.offsetWidth; fr.classList.add("wipe");
}
function legend(html) { const l = $("legend"); l.classList.add("out"); setTimeout(() => { l.innerHTML = html; l.classList.remove("out"); }, 120); }
function credit(t) { const c = $("mapcred"); if (c) c.textContent = t; }

/* ---------------- map layers: each one can be switched on/off on its own; tabs are presets ---------------- */
const G = {};
const grp = id => G[id] || (G[id] = { mk: [], an: new Set(), el: [] });
function lkeep(id, m) { m.addTo(MAP); grp(id).mk.push(m); return m; }
function ladd(id, el) { $("stage").appendChild(el); grp(id).el.push(el); return el; }
function lanim(id, dur, step, done) { const a = {}, t0 = performance.now(), S = grp(id).an; S.add(a);
  const f = now => { const p = Math.min(1, (now - t0) / dur); step(p); if (p < 1) a.raf = requestAnimationFrame(f); else { S.delete(a); done && done(); } };
  a.raf = requestAnimationFrame(f); return a; }
function llater(id, ms, fn) { const a = {}, S = grp(id).an; a.to = setTimeout(() => { S.delete(a); fn(); }, ms); S.add(a); return a; }
function lclear(id) { const x = grp(id); for (const a of x.an) cancelAnimationFrame(a.raf), clearInterval(a.iv), clearTimeout(a.to); x.an.clear();
  x.mk.forEach(m => m.remove()); x.mk = []; x.el.forEach(e => e.remove()); x.el = []; if (CLK === id) clock(null); }
let CLK = null;
function clock(owner, k, v, small) { if (!owner) { $("tau").innerHTML = ""; CLK = null; return; } CLK = owner;
  $("tau").innerHTML = `<div class="k">${esc(k)}</div><div class="v">${esc(v)}${small ? `<small>${esc(small)}</small>` : ""}</div>`; }
function wrap(el) { const w = document.createElement("div"); w.className = "fpw"; w.appendChild(el); return w; }
const pos = () => [+D.nhc.longitudeNumeric, +D.nhc.latitudeNumeric];
function fit(coords, padTop = 140, opt = {}) {
  if (!coords.length) return;
  const b = coords.reduce((b, c) => b.extend(c), new maplibregl.LngLatBounds(coords[0], coords[0]));
  const cam = MAP.cameraForBounds(b, { padding: { top: padTop * K, bottom: (opt.bottom ?? 40) * K, left: (opt.left ?? 40) * K, right: (opt.right ?? 40) * K }, maxZoom: opt.maxZoom ?? 6.6 });
  MAP.jumpTo(cam);
}
function labelSides(pts) { return pts.map((p, i) => { const a = pts[Math.max(0, i - 1)], b = pts[Math.min(pts.length - 1, i + 1)]; const dx = b.lon - a.lon, dy = b.lat - a.lat;
  return dy > Math.abs(dx) * 1.2 ? (i % 2 === 1) : dx < 0; }); }
/* flight-level wind colours (kt): the storm's own Saffir-Simpson colours above 34 kt, cool tones below */
const FLW = [[0, "#7f8fa6", "<34"], [34, "#46c56a", "34"], [50, "#2fd3b0", "50"], [64, "#ffe14d", "64"], [83, "#ff9a2f", "83"], [96, "#f5333c", "96"], [113, "#e33ad4", "113"], [137, "#b03bff", "137"]];
const flwCol = kt => { let c = FLW[0][1]; if (kt == null) return c; for (const x of FLW) if (kt >= x[0]) c = x[1]; return c; };
const PLANE = c => `<svg viewBox="0 0 24 24"><path d="M2 13l8-1 4-8h3l-2 8 6 0 2-3h1.5l-1 4.5 1 4.5H23l-2-3-6 0 2 8h-3l-4-8-8-1z" fill="${c}" stroke="#06101f" stroke-width="1"/></svg>`;
const ACFT = s => /NOAA ?9|NOAA 49/.test(s) ? "NOAA G-IV" : /NOAA ?[23]|NOAA 4[23]/.test(s) ? "NOAA P-3" : /TEAL|AF/.test(s) ? "USAF WC-130J" : s;
let MWSEL = null, MWPROD = "color91";
function mwPasses() { return (D.mw?.overpasses || []).filter(o => o.geo?.color91 || o.geo?.color37); }
function mwPick() { const list = mwPasses(), last = list[list.length - 1]; if (!last) return null;
  const good = list.filter(x => x.kt && Date.parse(last.t) - Date.parse(x.t) < 864e5).slice(-1)[0]; return MWSEL && list.find(x => x.id === MWSEL) || good || last; }
function trackData() { return D.fc.map(q => [q.hr, q.lat, q.lon]); }
function modelSet() { const M = D.models || { aids: {} };
  return { M, aids: TRACK_AIDS.filter(t => M.aids[t]).map(t => ({ t, pts: aidPts(M.aids[t]) })).filter(a => a.pts.length > 1),
    ofcl: M.aids.OFCL ? aidPts(M.aids.OFCL) : D.fc.map(p => [p.hr, p.lat, p.lon, p.kt]), gefs: Object.values(M.gefs || {}).map(aidPts).filter(p => p.length > 1) }; }

const LY = {
  cone: { name: "Cone of uncertainty", grp: "Forecast",
    on(a) { const go = () => { MAP.setPaintProperty("cone-fill", "fill-opacity", .26); MAP.setPaintProperty("cone-line", "line-opacity", .95); }; a ? llater("cone", 350, go) : go(); },
    off() { MAP.setPaintProperty("cone-fill", "fill-opacity", 0); MAP.setPaintProperty("cone-line", "line-opacity", 0); },
    leg: () => `<div class="r"><i style="background:rgba(255,255,255,.35);border:1px solid #fff;height:9px"></i>Cone of uncertainty</div>`, cred: "Cone: NOAA/NHC" },
  track: { name: "NHC forecast track", grp: "Forecast",
    on(a) { vis(["fc", "fc-case"], true); const pts = trackData(), hmax = D.fc[D.fc.length - 1]?.hr || 1;
      if (!a) return set("fc", FC([line(grow(pts, 999))]));
      set("fc", FC([])); llater("track", 700, () => lanim("track", 2600, p => { const tau = ease(p) * hmax; set("fc", FC([line(grow(pts, tau))])); LY.points.reveal?.(tau);
        clock("track", "NHC +", `${Math.round(tau)} H`, dayhm(advTime() + tau * 36e5)); }, () => llater("track", 1400, () => { if (CLK === "track") clock(null); if (!CAMLOCK) TABS[TAB]?.after?.(); }))); },
    off() { set("fc", FC([])); vis(["fc", "fc-case"], false); },
    leg: () => `<div class="r"><i style="background:#fff"></i>NHC forecast track</div>`, cred: "Track: NOAA/NHC" },
  points: { name: "Forecast points", grp: "Forecast",
    on(a) { const pts = D.fc, side = labelSides(pts);
      const els = pts.slice(1).map((p, k) => { const i = k + 1, c = catOf(p.kt), el = document.createElement("div"); el.className = "fp"; const lab = p.hr % 24 === 0 || i === pts.length - 1;
        el.innerHTML = glyph(p.kt, { plate: true, spin: p.kt >= 34, ph: phase(p) }) + (lab ? `<div class="lbl${side[i] ? " l" : ""}" style="--c:${c.c};--ci:${inkOn(c.c)}"><b>${dayhm(p.t)}</b><i>${wnd(p.kt)} ${wl().toUpperCase()} · ${ptWord(p).toUpperCase()}</i></div>` : "");
        if (!lab) el.style.transform = "scale(.7)";
        lkeep("points", new maplibregl.Marker({ element: wrap(el) }).setLngLat([p.lon, p.lat])); return { el, hr: p.hr }; });
      const drawing = a && ON.has("track") && grp("track").an.size;
      this.reveal = tau => els.forEach(e => { if (e.hr <= tau + .1) e.el.classList.add("in"); });
      if (!drawing) els.forEach((e, i) => setTimeout(() => e.el.classList.add("in"), a ? 80 + i * 90 : 30)); },
    off() { this.reveal = null; },
    leg: () => `<div class="r"><i class="dot" style="background:#ffe14d;border:1.5px solid #fff"></i>Forecast position</div>` + [["pt", "Post-tropical"], ["low", "Remnant low"], ["st", "Subtropical"]].filter(([f]) => D.fc.some(p => phase(p) === f)).map(([f, l]) => `<div class="r"><svg viewBox="-8 -8 16 16" style="width:calc(12px*var(--k));height:calc(12px*var(--k));flex:none">${phaseMark(f === "low" ? "pt" : f, 0, 1, 5.5, "#ffe14d", "#fff", 1.2)}</svg>${l}</div>`).join("") },
  best: { name: "Past track", grp: "Forecast", on() { vis(["best-line", "best-pts"], true); }, off() { vis(["best-line", "best-pts"], false); },
    leg: () => `<div class="r"><i class="dot" style="background:#3fa4ff;border:1.5px solid #06101f"></i>Past track</div>` },
  now: { name: "Storm position", grp: "Forecast", on() { nowMarker(); }, off() { NOWM?.remove(); NOWM = null; } },
  sat: { name: "Satellite loop", grp: "Observations",
    on() { SATL().show(true); loopBar(true); },
    off() { MAINLOOP.show(false); MESOLOOP.show(false); LIVELOOP.show(false); loopBar(false); if (CLK === "sat") clock(null); },
    leg: () => { const L = SATL(), b = BANDSET()[L.band]; return `<h4>${esc(SRC !== "fd" ? L.label : SatX.SATS[L.sat].name)} ${esc(b.t)}</h4><div class="r"><i style="width:calc(90px*var(--k));background:${L.band === "ir" && SatX.P.ramp !== "native" ? SatX.rampCSS(SatX.P.ramp) : L.band === "wv" && SRC !== "fd" ? SatX.rampCSS("wv") : L.band === "ir" ? "linear-gradient(90deg,#444,#ddd,#3fa4ff,#46c56a,#ffe14d,#f5333c,#888)" : "linear-gradient(90deg,#123,#9ab,#fff)"}"></i>${esc(b.sub || "")}</div>${L.band === "ir" || (L.band === "wv" && SRC !== "fd") ? `<div class="rt"><span>+40°C</span><span>−95°C</span></div>` : ""}`; },
    get cred() { return SRC === "fd" ? "Satellite: NOAA GOES / JMA Himawari via NASA GIBS" : "Satellite: NOAA GOES-R ABI (NOAA Open Data)"; } },
  mw: { name: "Microwave (latest pass)", grp: "Observations",
    on() { const list = mwPasses(), o = mwPick(); if (!o) return; vis("mw", true); showMW(o);
      const st = $("strip"); st.innerHTML = list.slice(-7).map((x, i) => `<button data-id="${x.id}" class="${x.id === o.id ? "on" : ""}" style="--i:${i}"><img alt="" src="${CDN}/microwave/${x.img.color91 || x.img.color37}"><span>${hm(x.t)}</span></button>`).join("");
      st.querySelectorAll("button").forEach(b => b.onclick = () => { MWSEL = b.dataset.id; showMW(list.find(x => x.id === b.dataset.id)); st.querySelectorAll("button").forEach(c => c.classList.toggle("on", c === b)); }); },
    off() { vis("mw", false); MAP.setPaintProperty("mw", "raster-opacity", 0); $("strip").innerHTML = ""; },
    leg: () => `<h4>89 GHz colour composite</h4><div class="r"><i style="background:#ff2d55"></i>Deep convection / ice</div><div class="r"><i style="background:#26e0d0"></i>Low cloud, warm rain</div>`,
    cred: "Microwave: NASA GPM/PPS, processed by Triple-A-Tropics" },
  recon: { name: "Recon flight", grp: "Observations",
    on(a) { const cur = rCur(); if (!cur || cur.track.length < 2) return;
      const tr = cur.track.filter((p, i) => i % 2 === 0).map(p => [p[0], p[1]]), fw = cur.track.filter((p, i) => i % 2 === 0).map(p => p[2]);
      /* one feature per run of same-coloured segments, coloured by the flight-level wind the aircraft measured there */
      const segs = n => { const out = []; let run = null;
        for (let i = 1; i < n; i++) { const c = flwCol(fw[i] ?? fw[i - 1]);
          if (run && run.c === c) run.pts.push(tr[i]); else { run && out.push(line(run.pts, { c: run.c })); run = { c, pts: [tr[i - 1], tr[i]] }; } }
        run && out.push(line(run.pts, { c: run.c })); return FC(out); };
      vis(["recon", "recon-case", "sondes"], true);
      const pl = document.createElement("div"); pl.className = "plane"; pl.innerHTML = PLANE("#fff");
      const pm = lkeep("recon", new maplibregl.Marker({ element: pl, rotationAlignment: "map" }).setLngLat(tr[0]));
      const sd = cur.sondes || [], sEls = sd.map(s => { const e = document.createElement("div"); e.className = "sonde"; e.textContent = s.kt != null ? Math.round(s.kt) : "";
        lkeep("recon", new maplibregl.Marker({ element: wrap(e), anchor: "bottom" }).setLngLat([s.lon, s.lat])); return { e, t: Date.parse(s.t) }; });
      const t0 = Date.parse(cur.track[0][4]), t1 = Date.parse(cur.track[cur.track.length - 1][4]);
      const step = p => { const n = Math.max(2, Math.round(tr.length * ease(p))), tnow = t0 + (t1 - t0) * ease(p);
        set("recon", segs(n));
        const q = tr[n - 2], b = tr[n - 1]; pm.setLngLat(b).setRotation(Math.atan2(b[0] - q[0], b[1] - q[1]) * 180 / Math.PI + 90);   // the icon's nose points west
        const done = sd.filter(s => Date.parse(s.t) <= tnow);
        set("sondes", FC(done.map(s => ({ type: "Feature", properties: { c: s.kt != null && s.kt >= 34 ? "#46c56a" : "#7cc3ea" }, geometry: { type: "Point", coordinates: [s.lon, s.lat] } }))));
        sEls.forEach(x => { if (x.t <= tnow) x.e.classList.add("in"); });
        clock("recon", ACFT(cur.aircraft), hm(tnow), `${done.length} sonde${done.length === 1 ? "" : "s"}`); };
      if (a) { set("recon", FC([])); set("sondes", FC([])); llater("recon", 600, () => lanim("recon", 5200, step)); } else step(1); },
    off() { vis(["recon", "recon-case", "sondes"], false); },
    leg: () => rCur() ? `${rFlights().length > 1 ? `<div class="rsw">${rFlights().map(f => `<button data-mid="${esc(f.mission_id)}"${f === rCur() ? ' class="on"' : ""}>${esc(ACFT(f.aircraft))} ${esc(f.flight || "")}</button>`).join("")}</div>` : ""}<h4>${esc(ACFT(rCur().aircraft))} flight-level wind, kt</h4><div class="flw">${FLW.map(([k, c, l]) => `<span><i style="background:${c}"></i>${l}</span>`).join("")}</div><div class="r"><i class="dot" style="background:#7cc3ea;border:1.5px solid #fff"></i>Dropsonde (sfc wind kt)</div>` : "",
    cred: "Recon: NOAA/NHC HDOB + dropsondes" },
  fixes: { name: "Tasked recon fixes", grp: "Observations",
    on(a) { const tgs = (D.recon?.plan?.flights || []).filter(f => f.pos).sort((x, y) => isoMs(x.fix[0]) - isoMs(y.fix[0]));
      tgs.forEach((f, i) => { const el = document.createElement("div"); el.className = "tgt"; el.innerHTML = `<span class="num">${i + 1}</span>`;
        lkeep("fixes", new maplibregl.Marker({ element: wrap(el) }).setLngLat(f.pos)); setTimeout(() => el.classList.add("in"), a ? 900 + i * 150 : 30); });
      if (!tgs.length) return;
      const pb = document.createElement("div"); pb.className = "plan";
      pb.innerHTML = `<h4>Tasked center fixes</h4>` + tgs.map((f, i) => `<div style="--i:${i}"><i>${i + 1}</i><span>${esc(f.flight.replace(/^FLIGHT \w+ - /, ""))}<small>${esc(ACFT(f.flight))} · ${esc(f.task.toLowerCase().replace("tail doppler radar", "TDR"))}</small></span><em>${esc(dayhm(isoMs(f.fix[0])))}</em></div>`).join("");
      ladd("fixes", pb); },
    off() {}, leg: () => `<div class="r"><i class="dot" style="background:#ffd24a"></i>Tasked center fix</div>`, cred: "CARCAH Plan of the Day" },
  radii: { name: "Wind radii (34/50/64 kt)", grp: "Forecast",
    on() { const R0 = D.nhc.radii || {}, col = { 34: "#ffd24a", 50: "#ff8a1f", 64: "#f5333c" };
      set("radii", FC((R0.initial || []).sort((a, b) => a.kt - b.kt).map(r => ({ type: "Feature", properties: { c: col[r.kt] || "#fff", kt: r.kt }, geometry: { type: "Polygon", coordinates: r.rings } }))));
      set("radiifc", FC((R0.forecast || []).map(r => ({ type: "Feature", properties: { c: col[r.kt] || "#fff" }, geometry: { type: "Polygon", coordinates: r.rings } }))));
      vis(["radii", "radii-fill", "radiifc"], true); },
    off() { vis(["radii", "radii-fill", "radiifc"], false); },
    leg: () => `<h4>Wind field (NHC)</h4><div class="r"><i style="background:#ffd24a"></i>34 kt</div><div class="r"><i style="background:#ff8a1f"></i>50 kt</div><div class="r"><i style="background:#f5333c"></i>64 kt</div><div class="r"><i class="dash" style="--c:#ffd24a"></i>Forecast radii</div>`,
    cred: "Wind radii: NOAA/NHC" },
  ww: { name: "Watches & warnings", grp: "Forecast",
    on() { const C = { "Hurricane Warning": "#e3001b", "Hurricane Watch": "#ff6bd6", "Tropical Storm Warning": "#1f6bff", "Tropical Storm Watch": "#ffe14d" }, W = D.nhc.ww || [];
      set("ww", FC(W.flatMap(w => w.lines.map(l => line(l, { c: C[w.type] || "#fff", t: w.type }))))); vis(["ww", "ww-case"], true);
      if (!W.length) clock("ww", "WATCHES", "NONE", "no coastal watches or warnings"); },
    off() { vis(["ww", "ww-case"], false); if (CLK === "ww") clock(null); },
    leg: () => (D.nhc.ww || []).length ? `<h4>Coastal watches & warnings</h4>${[...new Set(D.nhc.ww.map(w => w.type))].map(t => `<div class="r"><i style="background:${{ "Hurricane Warning": "#e3001b", "Hurricane Watch": "#ff6bd6", "Tropical Storm Warning": "#1f6bff", "Tropical Storm Watch": "#ffe14d" }[t]};height:6px"></i>${esc(t)}</div>`).join("")}` : "",
    cred: "Watches/warnings: NOAA/NHC" },
  ascat: { name: "ASCAT winds", grp: "Observations",
    async on() { const r = await ascatLoad(); if (!ON.has("ascat")) return; if (!r) { clock("ascat", "ASCAT", "NO PASS", "none over this storm in 60 h"); return; }
      vis("ascat", true); this.pass = r; clock("ascat", r.sensor, hm(Date.parse(r.mid_utc)), `${ago(r.mid_utc)} · peak ${wnd(r.peak)} ${wl()}`); legendNow(); creditNow(); },
    off() { vis("ascat", false); if (CLK === "ascat") clock(null); },
    leg() { return this.pass ? `<h4>${esc(this.pass.sensor)} · ${hm(Date.parse(this.pass.mid_utc))} ${TZ}</h4><div class="r"><i style="width:calc(120px*var(--k));background:linear-gradient(90deg,${(window.AscatViewer?.KT_SCALE || []).map(s => s[1]).join(",")})"></i></div><div class="rt"><span>0</span><span>34</span><span>64</span><span>137 kt</span></div>` : ""; },
    cred: "ASCAT: EUMETSAT / OSI SAF via NASA PO.DAAC" },
  glm: { name: "Lightning (GLM, 10 min)", grp: "Observations",
    on() { glmTick(); this.iv = setInterval(glmTick, 60e3); vis("glm", true); },
    off() { clearInterval(this.iv); vis("glm", false); if (CLK === "glm") clock(null); },
    leg: () => `<h4>Lightning flashes</h4><div class="r"><i class="dot" style="background:#fff"></i>last 2 min</div><div class="r"><i class="dot" style="background:#ffd24a"></i>2 to 5 min</div><div class="r"><i class="dot" style="background:#ff6a1f"></i>5 to 10 min</div>`,
    cred: "Lightning: NOAA GOES GLM" },
  fields: { name: "Model fields (GFS)", grp: "Guidance",
    async on() { await fieldsLoad(); if (!ON.has("fields")) return; if (!FLD.idx) { clock("fields", "MODEL FIELDS", "N/A", "not built for this storm yet"); return; }
      vis(["fld", "fldvec"], true); fieldBar(true); fieldShow(); },
    off() { vis(["fld", "fldvec"], false); fieldBar(false); if (CLK === "fields") clock(null); },
    leg: () => { if (!FLD.idx) return ""; const sc = FLD.idx.scales[FLD.field], st = sc.stops;
      return `<h4>${esc(FIELDN[FLD.field])} (${sc.unit})</h4><div class="r"><i style="width:calc(150px*var(--k));height:calc(8px*var(--k));background:linear-gradient(90deg,${st.map(s => s[1]).join(",")})"></i></div><div class="rt"><span>${st[0][0]}</span><span>${st[Math.floor(st.length / 2)][0]}</span><span>${st[st.length - 1][0]}+</span></div>`; },
    cred: "Model fields: NOAA GFS 0.25°, processed by Triple-A-Tropics" },
  models: { name: "Model tracks", grp: "Guidance",
    on(a) { const { M, aids, ofcl } = modelSet(); if (!aids.length) return; vis(["aids", "aids-case"], true);
      const tags = aids.map(x => { const el = document.createElement("div"); el.className = "aid"; el.style.setProperty("--c", AIDC[x.t] || "#fff"); el.textContent = x.t;
        const q = at(x.pts, 72) || x.pts.filter(p => p[0] <= 72).slice(-1).map(p => [p[2], p[1]])[0]; lkeep("models", new maplibregl.Marker({ element: wrap(el), anchor: "left", offset: [6, 0] }).setLngLat(q)); return { el, q, t: x.t }; });
      const draw = tau => set("aids", FC(aids.map(x => line(grow(x.pts, tau), { c: AIDC[x.t] || "#fff", con: /TVCN|HCCA/.test(x.t) })).concat([line(grow(ofcl, tau), { c: "#ffffff", con: true })])));
      const label = () => { const R = [], h = 15 * K;
        tags.forEach((g, i) => { const p = MAP.project(g.q), r = { l: p.x + 6, r: p.x + 6 + (g.t.length * 8.8 + 14) * K, t: p.y - h / 2 - 2, b: p.y + h / 2 + 2 };
          if (R.some(o => r.l < o.r + 2 && r.r > o.l - 2 && r.t < o.b + 1 && r.b > o.t - 1)) return; R.push(r); setTimeout(() => g.el.classList.add("in"), i * 60); }); };
      if (!a) { draw(999); return label(); }
      set("aids", FC([])); llater("models", 500, () => lanim("models", 5200, p => { const tau = ease(p) * 120; draw(tau);
        clock("models", "FORECAST", `+${Math.round(tau)} H`, dayhm(isoMs(M.cycle) + tau * 36e5)); }, label)); },
    off() { set("aids", FC([])); vis(["aids", "aids-case"], false); },
    leg: () => { const { aids } = modelSet(); return `<h4>${esc(D.models?.cycle?.slice(11, 13) || "")}Z early-cycle aids</h4><div class="cols">${[["OFCL", "NHC"]].concat(aids.map(x => [x.t, AIDNAME[x.t] || x.t])).map(([t, n]) => `<div class="r"><i style="background:${AIDC[t] || "#fff"}${t === "OFCL" ? ";height:5px" : ""}"></i>${esc(n)}</div>`).join("")}</div>`; },
    cred: "Guidance: NHC ATCF a-deck" },
  gefs: { name: "GEFS members", grp: "Guidance",
    on(a) { const { gefs } = modelSet(); if (!gefs.length) return; vis("gefs", true);
      if (!a) return set("gefs", FC(gefs.map(g => line(grow(g, 999)))));
      set("gefs", FC([])); llater("gefs", 500, () => lanim("gefs", 5200, p => set("gefs", FC(gefs.map(g => line(grow(g, ease(p) * 120))))))); },
    off() { set("gefs", FC([])); vis("gefs", false); },
    leg: () => `<div class="r"><i style="background:#cfd8e6;height:2px"></i>GEFS members (${Object.keys(D.models?.gefs || {}).length})</div>`, cred: "GEFS via ATCF" },
  cities: { name: "City names", grp: "Map", on() { $("ovl").style.display = ""; }, off() { $("ovl").style.display = "none"; } },
  roads: { name: "Highways", grp: "Map", on() { vis("roads", true); }, off() { vis("roads", false); } },
  lines: { name: "Borders & counties", grp: "Map", on() { vis("lines", true); }, off() { vis("lines", false); } }
};
const DATA = ["fields", "cone", "radii", "ww", "track", "points", "best", "sat", "mw", "ascat", "glm", "recon", "fixes", "models", "gefs"];
const ON = new Set(["now", "cities", "lines"]);
/* layers parked while they are reworked: kept in code, kept off the page (the Environment tab and its GFS fields) */
const LY_HIDDEN = new Set(["fields"]);
function setLayer(id, on, a = true) {
  if (on && LY_HIDDEN.has(id)) return;
  if (on && ON.has(id)) { lclear(id); LY[id].off(); }
  if (!on && !ON.has(id)) return;
  if (on) { ON.add(id); LY[id].on(a); } else { ON.delete(id); lclear(id); LY[id].off(); }
  syncLayerUI(); legendNow(); creditNow(); hashKick();
}
function legendNow() { $("legend").classList.toggle("dense", DATA.filter(id => ON.has(id)).length > 4); legend(DATA.filter(id => ON.has(id) && LY[id].leg).map(id => LY[id].leg()).filter(Boolean).join("")); }
function creditNow() { credit([...new Set(DATA.filter(id => ON.has(id) && LY[id].cred).map(id => LY[id].cred))].concat("Map: Triple-A-Tropics").join(" · ")); }
function showMW(o) {
  if (!o) return;
  const p = o.geo[MWPROD] || o.geo.color91 || o.geo.color37, b = o.bounds;
  MAP.getSource("mw").updateImage({ url: `${CDN}/microwave/${p}`, coordinates: [[b[0], b[3]], [b[2], b[3]], [b[2], b[1]], [b[0], b[1]]] });
  MAP.setPaintProperty("mw", "raster-opacity", 0); setTimeout(() => MAP.setPaintProperty("mw", "raster-opacity", .95), 60);
  if (TABS[TAB]?.id === "mw") frame("Microwave Imagery", `${o.sensor} · ${hm(o.t)} ${TZ}`, MWPROD === "color37" ? "37 GHZ" : "89 GHZ");
  creditNow();
  clock("mw", "TAT MW ESTIMATE", o.kt ? `${wnd(o.kt)} ${wl().toUpperCase()}` : "N/A", o.kt ? `${walt(o.kt)} · ${o.sensor} ${hm(o.t)}` : `partial coverage · ${o.sensor} ${hm(o.t)}`);
}


/* ---------------- ASCAT (the site's ascat feed + ascat.js's barb painter and kt scale) ---------------- */
async function ascatLoad() {
  try {
    const man = await (await fetch(`${CDN}/ascat/manifest.json?t=${Math.floor(Date.now() / 6e5)}`)).json(), ids = [SID, D.precursor].filter(Boolean);
    const p = (man.passes || []).filter(x => (x.storms || []).some(s => ids.includes(s.slug) && s.dist_km < 450)).sort((a, b) => Date.parse(b.mid_utc) - Date.parse(a.mid_utc))[0];
    if (!p) return null;
    const d = await (await fetch(`${CDN}/ascat/${p.file || p.id + ".json"}`)).json(), w = d.wvc, c = pos(), F = [];
    let peak = 0; const scale = window.AscatViewer?.KT_SCALE || [[0, "#fff"]];
    for (let i = 0; i < w.la.length; i++) { let lo = w.lo[i]; if (lo > 180) lo -= 360; if (Math.abs(w.la[i] - c[1]) > 12 || Math.abs(((lo - c[0] + 540) % 360) - 180) > 14) continue;
      const kt = w.kt[i]; peak = Math.max(peak, kt); const b = Math.min(140, Math.round(kt / 5) * 5);
      F.push({ type: "Feature", properties: { i: barbIcon(b, scale), d: w.dir[i], kt }, geometry: { type: "Point", coordinates: [lo, w.la[i]] } }); }
    set("ascat", FC(F)); return { ...p, peak };
  } catch (e) { return null; }
}
function barbIcon(kt, scale) {
  const id = "barb" + kt; if (MAP.hasImage(id)) return id;
  const c = document.createElement("canvas"), s = 2, W = 40; c.width = c.height = W * s; const g = c.getContext("2d"); g.scale(s, s); g.lineCap = "round";
  const col = scale.reduce((a, x) => kt >= x[0] ? x[1] : a, scale[0][1]);
  AscatViewer.drawBarb(g, W / 2, W / 2, kt, 0, "rgba(5,10,20,.85)", 3.2); AscatViewer.drawBarb(g, W / 2, W / 2, kt, 0, col, 1.4);
  MAP.addImage(id, g.getImageData(0, 0, W * s, W * s), { pixelRatio: s }); return id;
}
/* ---------------- GLM lightning: the last 10 minutes of flashes, straight from NOAA's GOES bucket ---------------- */
const GLM = { files: new Map(), busy: false };
async function glmTick() {
  if (GLM.busy) return; GLM.busy = true;
  try {
    const sat = SatX.satFor(pos()[0]) === "west" ? "goes18" : "goes19", B = Meso.BUCKET[sat], now = Date.now(), from = now - 10.5 * 6e4, keys = [];
    for (const t of [now - 36e5, now]) { const d = new Date(t), pre = `GLM-L2-LCFA/${d.getUTCFullYear()}/${String(Meso.jday(d)).padStart(3, "0")}/${String(d.getUTCHours()).padStart(2, "0")}/`;
      const x = await (await fetch(`${B}/?list-type=2&prefix=${encodeURIComponent(pre)}`)).text();
      for (const m of x.matchAll(/<Key>([^<]+)<\/Key>/g)) { const tt = Meso.keyTime(m[1]); if (tt >= from) keys.push({ k: m[1], t: tt }); } }
    for (const k of [...GLM.files.keys()]) if (!keys.some(x => x.k === k)) GLM.files.delete(k);
    const h = await Meso.h5(), todo = keys.filter(k => !GLM.files.has(k.k));
    let i = 0; await Promise.all(Array.from({ length: 4 }, async () => { while (i < todo.length) { const k = todo[i++];
      try { const buf = await (await fetch(`${B}/${k.k}`)).arrayBuffer(), name = "/g" + Math.random().toString(36).slice(2); h.FS.writeFile(name, new Uint8Array(buf));
        const f = new h.File(name, "r"); GLM.files.set(k.k, { t: k.t, la: f.get("flash_lat").value, lo: f.get("flash_lon").value }); f.close(); h.FS.unlink(name); } catch (e) {} } }));
    const F = [], n = Date.now();
    for (const v of GLM.files.values()) { const age = (n - v.t) / 6e4, c = age < 2 ? "#ffffff" : age < 5 ? "#ffd24a" : "#ff6a1f";
      for (let j = 0; j < v.la.length; j++) F.push({ type: "Feature", properties: { c }, geometry: { type: "Point", coordinates: [v.lo[j], v.la[j]] } }); }
    set("glm", FC(F));
    if (ON.has("glm")) { const c = pos(); let near = 0; for (const f of F) { const [x, y] = f.geometry.coordinates; if (Math.hypot((x - c[0]) * Math.cos(c[1] * Math.PI / 180), y - c[1]) < 3) near++; }
      clock("glm", "LIGHTNING", `${near}`, `flashes within 200 mi · 10 min`); }
  } catch (e) {} finally { GLM.busy = false; }
}


/* ---------------- GFS model fields (situation/build_fields.py): shear, steering, precipitable water ---------------- */
const FLD = { idx: null, field: "shear", fh: 0, playing: false, iv: null };
const FIELDN = { shear: "Deep-layer shear", steering: "Steering flow", pwat: "Precipitable water" };
async function fieldsLoad() {
  try { FLD.idx = await (await fetch(`${CDN}/situation/fields/${SID}/index.json?t=${Math.floor(Date.now() / 6e5)}`)).json(); if (!FLD.idx.hours.includes(FLD.fh)) FLD.fh = FLD.idx.hours[0]; }
  catch (e) { FLD.idx = null; }
}
const fldValid = () => isoMs(FLD.idx.cycle) + FLD.fh * 36e5;
async function fieldShow() {
  const I = FLD.idx; if (!I || !ON.has("fields")) return; const b = I.bounds, base = `${CDN}/situation/fields/${SID}/`, F = String(FLD.fh).padStart(3, "0");
  MAP.getSource("fld").updateImage({ url: `${base}${FLD.field}_f${F}.png`, coordinates: [[b[0], b[3]], [b[2], b[3]], [b[2], b[1]], [b[0], b[1]]] });
  if (FLD.field === "pwat") set("fldvec", FC([]));
  else try { const v = (await (await fetch(`${base}vec_f${F}.json`)).json())[FLD.field];
    set("fldvec", FC(v.map(([lo, la, u, w]) => { const s = Math.hypot(u, w); return { type: "Feature", properties: FLD.field === "shear" ? { i: whiteBarb(Math.min(140, Math.round(s / 5) * 5)), d: (Math.atan2(-u, -w) * 180 / Math.PI + 360) % 360, s: 1 } : { i: arrowIcon(), d: (Math.atan2(u, w) * 180 / Math.PI + 360) % 360, s: Math.max(.35, Math.min(1.1, s / 25)) }, geometry: { type: "Point", coordinates: [lo, la] } }; }))); } catch (e) {}
  const lbl = `${I.model} ${I.cycle.slice(11, 13)}Z · F${F} · ${dayhm(fldValid())} ${TZ}`;
  clock("fields", `${FIELDN[FLD.field].toUpperCase()}`, `F${F}`, `valid ${dayhm(fldValid())} ${TZ}`);
  if (TABS[TAB]?.id === "env") frame(...TABS[TAB].hdr());
  const ft = $("fbT"); if (ft) ft.textContent = lbl;
  $("fb")?.querySelectorAll("[data-fh]").forEach(x => x.classList.toggle("on", +x.dataset.fh === FLD.fh));
  legendNow(); creditNow(); hashKick();
}
function whiteBarb(kt) { const id = "wbarb" + kt; if (MAP.hasImage(id)) return id; const c = document.createElement("canvas"), s = 2, W = 40; c.width = c.height = W * s; const g = c.getContext("2d"); g.scale(s, s); g.lineCap = "round";
  AscatViewer.drawBarb(g, W / 2, W / 2, kt, 0, "rgba(5,10,20,.8)", 3); AscatViewer.drawBarb(g, W / 2, W / 2, kt, 0, "#ffffff", 1.3); MAP.addImage(id, g.getImageData(0, 0, W * s, W * s), { pixelRatio: s }); return id; }
function arrowIcon() { const id = "farrow"; if (MAP.hasImage(id)) return id; const c = document.createElement("canvas"), s = 2, W = 40; c.width = c.height = W * s; const g = c.getContext("2d"); g.scale(s, s);
  g.translate(20, 20); g.beginPath(); g.moveTo(0, -15); g.lineTo(7, -3); g.lineTo(2.2, -3); g.lineTo(2.2, 14); g.lineTo(-2.2, 14); g.lineTo(-2.2, -3); g.lineTo(-7, -3); g.closePath();
  g.lineWidth = 2.5; g.strokeStyle = "rgba(5,10,20,.85)"; g.stroke(); g.fillStyle = "#fff"; g.fill(); MAP.addImage(id, g.getImageData(0, 0, W * s, W * s), { pixelRatio: s }); return id; }
function fieldBar(on) {
  const fb = $("fb"); fb.hidden = !on; if (!on) { clearInterval(FLD.iv); FLD.playing = false; return; }
  const I = FLD.idx; if (!I) return;
  fb.innerHTML = `<button class="pp" data-fa="play" aria-label="Play forecast">${PLAYI}</button>
    <div class="seg">${Object.entries(FIELDN).map(([k, v]) => `<button data-fld="${k}" class="${k === FLD.field ? "on" : ""}">${k === "pwat" ? "PWAT" : k === "shear" ? "SHEAR" : "STEERING"}</button>`).join("")}</div>
    <div class="seg">${I.hours.map(h => `<button data-fh="${h}" class="${h === FLD.fh ? "on" : ""}">${h}</button>`).join("")}</div>
    <span class="lt" id="fbT" style="width:auto"></span>`;
  fb.querySelectorAll("[data-fld]").forEach(b => b.onclick = () => { FLD.field = b.dataset.fld; fieldBar(true); fieldShow(); });
  fb.querySelectorAll("[data-fh]").forEach(b => b.onclick = () => { FLD.fh = +b.dataset.fh; fieldShow(); });
  fb.querySelector("[data-fa]").onclick = e => { FLD.playing = !FLD.playing; e.currentTarget.innerHTML = FLD.playing ? PAUSEI : PLAYI; clearInterval(FLD.iv);
    if (FLD.playing) FLD.iv = setInterval(() => { const H = FLD.idx.hours; FLD.fh = H[(H.indexOf(FLD.fh) + 1) % H.length]; fieldShow(); }, 1200); };
}

/* ---------------- satellite loop bar + 4-panel view ---------------- */
let MAINLOOP = null, MESOLOOP = null, LIVELOOP = null, SRC = "fd", MESOSEC = null, LIVESEC = null;
const SATL = () => SRC === "meso" ? MESOLOOP : SRC === "live" ? LIVELOOP : MAINLOOP;
const BANDSET = () => SRC === "meso" ? Meso.BANDS : SRC === "live" ? Meso.bandsFor(LIVESEC) : SatX.BANDS;
/* outside both 5-minute sectors the live writer publishes full-disk brightness temperature around the storm (vis.py FDWriter) */
async function fdCover() { const f = Meso.coverFD(...pos()); if (!f) return null;
  try { const r = await fetch(`${CDN}/situation/fd/${SID}/ir/index.json?t=${Math.floor(Date.now() / 6e4)}`); return r.ok && (await r.json()).frames?.length ? f : null; } catch (e) { return null; } }
const liveTag = () => LIVESEC?.kind === "F" ? "10-MIN" : "5-MIN";
const SRCNAME = { fd: "Full disk", live: "5-min", meso: "Meso 1-min" };
/* GIBS full disk (10 min, but 1.5-2.5 h behind), NOAA's 5-minute CONUS/PACUS sector (~10 min behind; the default when it
   covers the storm), or the 1-minute mesoscale sector that covers the storm */
async function setSrc(s) {
  if (s === "meso") { MESOSEC = await Meso.cover(...pos()); if (!MESOSEC) return; MESOLOOP.sector = MESOSEC; if (!(MESOLOOP.band in Meso.BANDS)) MESOLOOP.band = "ir"; }
  if (s === "live") { LIVESEC = Meso.coverLive(...pos()) || await fdCover(); if (!LIVESEC) return; LIVELOOP.sector = LIVESEC; LIVELOOP.center = pos(); if (!(LIVELOOP.band in Meso.bandsFor(LIVESEC))) LIVELOOP.band = "ir"; }
  const was = ON.has("sat"); if (was) SATL().show(false);
  SRC = s; if (s === "meso") SatX.P.hours = Math.min(SatX.P.hours, 1); else if (SatX.P.hours < 1) SatX.P.hours = 3; if (s === "live") SatX.P.hours = Math.min(SatX.P.hours, 6);
  if (was) SATL().show(true);
  buildLoopBar(); legendNow(); creditNow(); if (TABS[TAB]?.id === "sat") frame(...TABS[TAB].hdr());
}
const PLAYI = `<svg viewBox="0 0 24 24"><path d="M7 4l13 8-13 8z" fill="currentColor"/></svg>`, PAUSEI = `<svg viewBox="0 0 24 24"><path d="M6 4h4v16H6zM14 4h4v16h-4z" fill="currentColor"/></svg>`;
function loopBar(on) { const lc = $("lc"); lc.hidden = !(on || QUAD.on); if (!lc.hidden) buildLoopBar(); }
function buildLoopBar() {
  const P = SatX.P, sat = SatX.satFor((QUAD.on ? QUAD.cells[0]?.map : MAP)?.getCenter().lng ?? pos()[0]);
  const bands = SRC !== "fd" ? Object.keys(BANDSET()) : Object.keys(SatX.BANDS).filter(b => SatX.has(sat, b)), L = SATL();
  if (MESOSEC === null && !QUAD.on) Meso.cover(...pos()).then(c => { MESOSEC = c || false; if (c) buildLoopBar(); });
  $("lc").innerHTML = `<button class="pp" data-a="play" aria-label="Play or pause">${P.playing ? PAUSEI : PLAYI}</button>
    <button data-a="prev" aria-label="Previous frame">‹</button><button data-a="next" aria-label="Next frame">›</button>
    <div class="tl"><input type="range" min="0" max="1000" value="1000" aria-label="Loop position"><span class="lt" id="lcT">loading</span></div>
    ${(QUAD.on ? QUAD.cells.some(c => c.loop.on && c.loop.band === "ir") : L.band === "ir") ? `<div class="seg" title="IR colour ramp">${Object.entries(SatX.RAMPS).filter(([k]) => SRC === "fd" || k !== "native").map(([k, v]) => `<button data-r="${k}" class="${k === P.ramp ? "on" : ""}" title="${v}">${k === "tat" ? "TAT" : k === "bd" ? "BD" : k === "gray" ? "B/W" : "NASA"}</button>`).join("")}</div>` : ""}
    ${!QUAD.on && (MESOSEC || LIVESEC) ? `<div class="seg" title="Imagery source"><button data-src="fd" class="${SRC === "fd" ? "on" : ""}" title="Full disk via NASA GIBS, every 10 minutes (runs 1.5-2.5 h behind)">FULL DISK</button>${LIVESEC ? `<button data-src="live" class="${SRC === "live" ? "on" : ""}" title="${esc(LIVESEC.label)}, every ${LIVESEC.kind === "F" ? 10 : 5} minutes, straight from NOAA">${liveTag()}</button>` : ""}${MESOSEC ? `<button data-src="meso" class="${SRC === "meso" ? "on" : ""}" title="${esc(MESOSEC.label)}, every minute">MESO 1-MIN</button>` : ""}</div>` : ""}
    ${QUAD.on ? "" : `<div class="seg" title="Band">${bands.map(b => `<button data-b="${b}" class="${b === L.band ? "on" : ""}" title="${esc(BANDSET()[b].t)}">${BANDSET()[b].short}</button>`).join("")}</div>`}
    <div class="seg" title="Loop length">${(SRC === "meso" && !QUAD.on ? [.5, 1, 2] : SRC === "live" && !QUAD.on ? [1, 2, 3, 6] : [1, 3, 6, 12]).map(h => `<button data-h="${h}" class="${h === P.hours ? "on" : ""}">${h < 1 ? "30M" : h + "H"}</button>`).join("")}</div>
    <div class="seg" title="Speed">${SatX.SPEEDS.map((s, i) => `<button data-s="${i}" class="${i === P.speed ? "on" : ""}" title="${s.k}">${s.k[0]}</button>`).join("")}</div>
    <label class="op" title="Satellite opacity">Opacity<input type="range" min="20" max="100" value="${Math.round(P.opacity * 100)}"></label>
    ${QUAD.on ? "" : `<button class="xp" data-a="mp4" title="Export this loop as a video, header and labels included">MP4</button>`}`;
  const lc = $("lc");
  lc.querySelector('[data-a="play"]').onclick = () => { P.playing = !P.playing; P.last = 0; lc.querySelector(".pp").innerHTML = P.playing ? PAUSEI : PLAYI; };
  lc.querySelector('[data-a="prev"]').onclick = () => { SatX.step(-1); lc.querySelector(".pp").innerHTML = PLAYI; };
  lc.querySelector('[data-a="next"]').onclick = () => { SatX.step(1); lc.querySelector(".pp").innerHTML = PLAYI; };
  const sl = lc.querySelector(".tl input"); sl.oninput = () => { P.playing = false; lc.querySelector(".pp").innerHTML = PLAYI; SatX.seek(sl.value / 1000); };
  lc.querySelectorAll("[data-src]").forEach(b => b.onclick = () => setSrc(b.dataset.src));
  lc.querySelectorAll("[data-b]").forEach(b => b.onclick = () => { SATL().setBand(b.dataset.b); buildLoopBar(); legendNow(); if (TABS[TAB]?.id === "sat") frame(...TABS[TAB].hdr()); });
  lc.querySelectorAll("[data-r]").forEach(b => b.onclick = () => { SatX.setRamp(b.dataset.r); buildLoopBar(); legendNow(); });
  lc.querySelectorAll("[data-h]").forEach(b => b.onclick = () => { SatX.setHours(+b.dataset.h); buildLoopBar(); });
  lc.querySelectorAll("[data-s]").forEach(b => b.onclick = () => { P.speed = +b.dataset.s; buildLoopBar(); });
  lc.querySelector(".op input").oninput = e => SatX.setOpacity(e.target.value / 100);
  lc.querySelector('[data-a="mp4"]')?.addEventListener("click", () => typeof exportLoop === "function" && exportLoop(lc.querySelector('[data-a="mp4"]')));
  hashKick();
}
let LCKEY = "";
SatX.P.subs.add((t, s) => {
  const lc = $("lc"); if (!lc || lc.hidden || !s) return;
  const sl = lc.querySelector(".tl input"), L = [...SatX.P.loops][0], f = L?.current;
  if (sl && document.activeElement !== sl && L?.frames.length) { const F = L.frames, k = Math.max(0, F.findIndex(x => x.t >= t - 1)); sl.value = Math.round(k / Math.max(1, F.length - 1) * 1000); }
  const ld = L?.loading, key = `${f?.t}|${ld ? ld.done + "/" + ld.total : ""}`; if (key === LCKEY) return; LCKEY = key;
  const lt = $("lcT"); if (lt && f) { const age = Math.round((Date.now() - s[1]) / 6e4); lt.innerHTML = `${hm(f.t)} ${TZ}${ld ? `<em>loading ${ld.done}/${ld.total}</em>` : `<em class="${age > 45 ? "old" : ""}">${age < 60 ? age + " min" : Math.floor(age / 60) + "h" + z2(age % 60)} old</em>`}`; }
  if (!QUAD.on && ON.has("sat") && f) {
    const back = Math.round((s[1] - f.t) / 6e4);
    clock("sat", `${SRC === "meso" ? "MESO " : SRC === "live" ? liveTag() + " " : ""}${BANDSET()[SATL().band].short} LOOP`, hm(f.t), back ? `-${Math.floor(back / 60)}:${z2(back % 60)}` : "latest");
    if (TABS[TAB]?.id === "sat") { const e = $("frame").querySelector(".tTime"); if (e) e.textContent = `${hm(f.t)} ${TZ}`; else frame(...TABS[TAB].hdr()); }
  }
  for (const c of QUAD.cells) c.stamp?.();
});

/* 4-panel: four synced maps, each showing its own product (any band, the microwave pass, or track & cone) */
const QPROD = { ir: "Infrared", wv: "Water Vapor", vis: "Visible", geocolor: "GeoColor", airmass: "Air Mass", dust: "Dust", mw: "Microwave 89 GHz", track: "Track & cone" };
const QUAD = { on: false, cells: [], prods: ["ir", "vis", "wv", "mw"], wasSat: false };
function miniStyle() {
  return { version: 8, sources: {
      base: { type: "raster", tiles: [`${CDN}/situation/tiles/base/{z}/{x}/{y}.jpg`], tileSize: 256, maxzoom: 6 },
      lines: { type: "raster", tiles: [`${CDN}/situation/tiles/lines/{z}/{x}/{y}.png?v=2`], tileSize: 256, maxzoom: 6 } },
    layers: [{ id: "bg", type: "background", paint: { "background-color": "#2463a0" } }, { id: "base", type: "raster", source: "base", paint: { "raster-fade-duration": 0 } }] };
}
function quadToggle() {
  QUAD.on = !QUAD.on; $("stage").classList.toggle("quad", QUAD.on); $("quadBtn").classList.toggle("on", QUAD.on);
  if (QUAD.on) { QUAD.wasSat = ON.has("sat"); if (QUAD.wasSat) SATL().show(false); buildQuad(); }
  else { QUAD.cells.forEach(c => { c.gibs.show(false); c.live.show(false); }); if (QUAD.wasSat && ON.has("sat")) SATL().show(true); }
  loopBar(ON.has("sat"));
}
async function buildQuad() {
  const q = $("quad");
  if (!QUAD.cells.length) {
    q.innerHTML = QUAD.prods.map((p, i) => `<div class="qc"><div class="qm"></div><div class="qt"><select aria-label="Panel ${i + 1} product">${Object.entries(QPROD).map(([k, v]) => `<option value="${k}"${k === p ? " selected" : ""}>${v}</option>`).join("")}</select><span class="qs"></span></div></div>`).join("");
    const E = { type: "FeatureCollection", features: [] };
    QUAD.cells = [...q.querySelectorAll(".qc")].map((el, i) => {
      const m = new maplibregl.Map({ container: el.querySelector(".qm"), style: miniStyle(), center: MAP.getCenter(), zoom: MAP.getZoom() - .5,
        attributionControl: false, fadeDuration: 0, dragRotate: false, pitchWithRotate: false, renderWorldCopies: false });
      m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
      /* IR / WV / visible come straight from NOAA (true brightness temperature and reflectance) when the 5-minute sector
         covers the storm, like the main loop; GIBS supplies the RGB products and everything outside CONUS/PACUS */
      const c = { el, map: m, prod: QUAD.prods[i], gibs: new SatX.Loop(m, "qsat", "qlines", { maxPx: 1400 }), live: new Meso.Loop(m, "qlive", "qlines", { maxPx: 1400 }), useLive: false,
        get loop() { return this.useLive ? this.live : this.gibs; } };
      c.live.visBase = `${CDN}/situation/vis/${SID}/`; c.live.fdBase = `${CDN}/situation/fd/${SID}/`;
      c.ready = new Promise(r => m.on("load", () => {
        m.addSource("mw", { type: "image", url: MAP.getSource("mw").url || "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=", coordinates: [[-1, 1], [1, 1], [1, -1], [-1, -1]] });
        m.addLayer({ id: "mw", type: "raster", source: "mw", layout: { visibility: "none" }, paint: { "raster-opacity": .95, "raster-fade-duration": 0 } });
        m.addLayer({ id: "qlines", type: "raster", source: "lines", paint: { "raster-fade-duration": 0 } });
        m.addSource("cone", { type: "geojson", data: E }); m.addSource("fc", { type: "geojson", data: E });
        m.addLayer({ id: "cone", type: "fill", source: "cone", layout: { visibility: "none" }, paint: { "fill-color": "#fff", "fill-opacity": .26 } });
        m.addLayer({ id: "cone-l", type: "line", source: "cone", layout: { visibility: "none" }, paint: { "line-color": "#fff", "line-width": 1.6 } });
        m.addLayer({ id: "fc", type: "line", source: "fc", paint: { "line-color": "#fff", "line-width": 2, "line-opacity": .85 } });
        r(); }));
      m.on("move", e => { if (!e.originalEvent) return; for (const o of QUAD.cells) if (o.map !== m) o.map.jumpTo({ center: m.getCenter(), zoom: m.getZoom() }); });
      el.querySelector("select").onchange = e => { c.prod = e.target.value; QUAD.prods[i] = c.prod; setProd(c); };
      c.stamp = () => { const s = el.querySelector(".qs"), f = c.loop.current;
        if (c.loop.on && f) s.textContent = `${c.useLive ? c.live.label : SatX.SATS[c.loop.sat].name} · ${hm(f.t)} ${TZ}`;
        else if (c.prod === "mw") { const o = mwPick(); s.textContent = o ? `${o.sensor} · ${hm(o.t)} ${TZ}` : "no pass"; }
        else if (c.prod === "track") s.textContent = `NHC advisory ${D.nhc.advisory}`; };
      return c;
    });
    new ResizeObserver(() => QUAD.cells.forEach(c => c.map.resize())).observe(q);
  }
  for (const c of QUAD.cells) { await c.ready; c.map.resize(); c.map.jumpTo({ center: pos(), zoom: Math.max(4.4, MAP.getZoom() - .4) }); quadData(c); setProd(c); }
}
function quadData(c) {
  const m = c.map; m.getSource("cone").setData(FC((D.nhc.cone || []).map(r => ({ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [r] } }))));
  m.getSource("fc").setData(FC([line(D.fc.map(p => [p.lon, p.lat]))]));
  c.now?.remove(); const el = document.createElement("div"); el.className = "fp now in qnow"; el.innerHTML = glyph(D.kt, { plate: true });
  c.now = new maplibregl.Marker({ element: wrap(el) }).setLngLat(pos()).addTo(m);
}
function setProd(c) {
  const m = c.map, p = c.prod, live = !!LIVESEC && p in Meso.bandsFor(LIVESEC), sat = live || p in SatX.BANDS;
  c.gibs.show(false); c.live.show(false); c.useLive = live;
  if (live) { c.live.sector = LIVESEC; c.live.center = pos(); c.live.band = p; c.live.show(true); }
  else if (sat) { c.gibs.band = p; c.gibs.show(true); }
  m.setLayoutProperty("mw", "visibility", p === "mw" ? "visible" : "none");
  if (p === "mw") { const o = mwPick(); if (o) { const b = o.bounds, g = o.geo[MWPROD] || o.geo.color91 || o.geo.color37;
    m.getSource("mw").updateImage({ url: `${CDN}/microwave/${g}`, coordinates: [[b[0], b[3]], [b[2], b[3]], [b[2], b[1]], [b[0], b[1]]] }); } }
  ["cone", "cone-l"].forEach(id => m.setLayoutProperty(id, "visibility", p === "track" ? "visible" : "none"));
  c.stamp();
}

/* ---------------- tabs: presets of layers + a camera + the header ---------------- */
/* tab-bar icons: Lucide (lucide.dev, ISC licence), inlined */
const LUC = b => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${b}</svg>`;
const ICON = {
  track: LUC(`<circle cx="6" cy="19" r="3" /> <path d="M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15" /> <circle cx="18" cy="5" r="3" />`),
  sat: LUC(`<path d="M13 7 9 3 5 7l4 4" /> <path d="m17 11 4 4-4 4-4-4" /> <path d="m8 12 4 4 6-6-4-4Z" /> <path d="m16 8 3-3" /> <path d="M9 21a6 6 0 0 0-6-6" />`),
  mw: LUC(`<path d="M2 6c.6.5 1.2 1 2.5 1C7 7 7 5 9.5 5c2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1" /> <path d="M2 12c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1" /> <path d="M2 18c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1" />`),
  recon: LUC(`<path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z" />`),
  env: LUC(`<path d="M12.8 19.6A2 2 0 1 0 14 16H2" /> <path d="M17.5 8a2.5 2.5 0 1 1 2 4H2" /> <path d="M9.8 4.4A2 2 0 1 1 11 8H2" />`),
  models: LUC(`<circle cx="19" cy="5" r="2" /> <circle cx="5" cy="19" r="2" /> <path d="M5 17A12 12 0 0 1 17 5" />`),
  quad: LUC(`<rect width="7" height="7" x="3" y="3" rx="1" /> <rect width="7" height="7" x="14" y="3" rx="1" /> <rect width="7" height="7" x="14" y="14" rx="1" /> <rect width="7" height="7" x="3" y="14" rx="1" />`),
  layers: LUC(`<path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z" /> <path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65" /> <path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65" />`),
  share: LUC(`<path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z" />`),
  gear: LUC(`<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" /> <circle cx="12" cy="12" r="3" />`)
};
const TABS = [
  { id: "track", label: "Track & Cone", layers: ["cone", "track", "points", "best"], anim: ["cone", "track", "points"],
    cam() { const c = (D.nhc.cone || []).flat(); fit(c.length ? c : D.fc.map(p => [p.lon, p.lat]), 135, { right: 60, left: 60, bottom: 30 }); },
    after() { const near = D.fc.filter(p => p.hr <= 72).map(p => [p.lon, p.lat]); if (near.length < 3) return;
      const b = near.reduce((b, c) => b.extend(c), new maplibregl.LngLatBounds(near[0], near[0]));
      MAP.easeTo({ ...MAP.cameraForBounds(b, { padding: { top: 150 * K, bottom: 60 * K, left: 120 * K, right: 260 * K }, maxZoom: 6.4 }), duration: 3200, easing: t => t * t * (3 - 2 * t) }); },
    hdr: () => [`${(D.nhc.name || "").toUpperCase()} Forecast Track`, `Advisory ${D.nhc.advisory} · ${hm(advTime())} ${TZ}`, "NHC",
      CAT.slice(0, 5).map(c => `<i style="background:${c.c};color:${inkOn(c.c)}">${c.k === "D" ? "TD" : c.k === "S" ? "TS" : "CAT " + c.k}</i>`).join("")] },
  { id: "sat", label: "Satellite", layers: ["sat", "track", "best"], anim: ["sat"], ok: () => Object.keys(SatX.times()).length,
    cam() { const c = pos(); MAP.jumpTo({ center: [c[0] + .4, c[1] + 1.2], zoom: 5.4 }); },
    hdr: () => { const L = SATL(), f = L.current; return [`${BANDSET()[L.band].t} Satellite`, f ? `${hm(f.t)} ${TZ}` : "", SRC === "meso" ? `MESO ${L.sector?.m || ""}` : SRC === "live" ? L.label.replace(/^GOES-\d+ /, "") : SatX.SATS[SatX.satFor(pos()[0])].name]; } },
  { id: "mw", label: "Microwave", layers: ["mw", "track", "best"], anim: ["mw"], ok: () => mwPasses().length,
    cam() { const o = mwPick(), b = o.bounds; fit([[b[0], b[1]], [b[2], b[3]]], 120, { bottom: 10, left: 10, right: 10, maxZoom: 6 }); },
    hdr: () => { const o = mwPick(); return ["Microwave Imagery", `${o.sensor} · ${hm(o.t)} ${TZ}`, MWPROD === "color37" ? "37 GHZ" : "89 GHZ"]; } },
  { id: "recon", label: "Recon", layers: ["recon", "track"], anim: ["recon"],
    cam() { const cur = rCur(), c = [pos(), ...(D.recon?.plan?.flights || []).filter(f => f.pos).map(f => f.pos)];
      if (cur) c.push(...cur.track.filter((p, i) => i % 4 === 0).map(p => [p[0], p[1]])); fit(c, 140, { right: 290, bottom: 70, left: 60, maxZoom: 6.2 }); },
    hdr: () => { const nf = nextFlight(), cur = rCur(); return ["Hurricane Hunters", nf ? `Next fix ${dayhm(isoMs(nf.fix[0]))} ${TZ}` : cur ? `${ACFT(cur.aircraft)} today` : "No flights tasked", "RECON"]; } },
  { id: "models", label: "Models", layers: ["models", "gefs", "best"], anim: ["models", "gefs"], ok: () => Object.keys(D.models?.aids || {}).length,
    cam() { const { aids } = modelSet(); fit(aids.flatMap(a => a.pts.filter(p => p[0] <= 84).map(p => [p[2], p[1]])).concat(D.fc.filter(p => p.hr <= 84).map(p => [p.lon, p.lat])), 135, { right: 300, bottom: 90, left: 60, maxZoom: 6.2 }); },
    hdr: () => { const cyc = D.models?.cycle ? `${D.models.cycle.slice(11, 13)}Z` : ""; return ["Track Guidance", `${cyc} early-cycle aids`, cyc ? `${cyc} MODELS` : "MODELS"]; } }
];
let NOWM = null;
function nowMarker() {
  NOWM?.remove(); NOWM = null; if (!ON.has("now")) return; const el = document.createElement("div"); el.className = "fp now"; el.innerHTML = glyph(D.kt, { plate: true });
  setTimeout(() => el.classList.add("in"), 80);
  NOWM = new maplibregl.Marker({ element: wrap(el) }).setLngLat(pos()).addTo(MAP);
}
function buildTabs() {
  $("tabs").innerHTML = TABS.map((s, i) => `<button role="tab" data-i="${i}"${s.ok && !s.ok() ? " disabled" : ""}>${ICON[s.id]}<span>${s.label}</span></button>`).join("") +
    `<button class="quadbtn" id="quadBtn" title="4-panel view">${ICON.quad}<span>4-Panel</span></button><button class="lyrbtn" id="lyrBtn" aria-expanded="false">${ICON.layers}<span>Layers</span></button>` +
    `<button class="icobtn" id="vwBtn" title="Share, snapshot and saved views" aria-label="Share and saved views" aria-expanded="false">${ICON.share}</button><button class="icobtn" id="setBtn" title="Units and time zone" aria-label="Settings" aria-expanded="false">${ICON.gear}</button>`;
  $("tabs").querySelectorAll("button[data-i]").forEach(b => b.onclick = () => tab(+b.dataset.i));
  $("quadBtn").onclick = quadToggle;
  $("lyrBtn").onclick = () => { const p = $("lyr"), o = !p.classList.contains("open"); p.classList.toggle("open", o); $("lyrBtn").classList.toggle("on", o); $("lyrBtn").setAttribute("aria-expanded", o); };
}
function buildLayers() {
  const gs = [...new Set(Object.entries(LY).filter(([id]) => !LY_HIDDEN.has(id)).map(([, l]) => l.grp))];
  $("lyr").innerHTML = `<div class="lyr-h"><b>Map layers</b><button id="lyrX" aria-label="Close">×</button></div>` + gs.map(g => `<h5>${g}</h5>` + Object.entries(LY).filter(([id, l]) => l.grp === g && !LY_HIDDEN.has(id)).map(([id, l]) =>
    `<label class="tg"><span>${esc(l.name)}</span><input type="checkbox" data-l="${id}"><i></i></label>`).join("")).join("");
  $("lyr").querySelectorAll("input[data-l]").forEach(c => c.onchange = () => setLayer(c.dataset.l, c.checked, true));
  $("lyrX").onclick = () => $("lyrBtn").click();
  syncLayerUI();
}
function syncLayerUI() { $("lyr").querySelectorAll("input[data-l]").forEach(c => { c.checked = ON.has(c.dataset.l); });
  const n = DATA.filter(id => ON.has(id)).length; const b = $("lyrBtn"); if (b) b.querySelector("span").textContent = `Layers · ${n}`; }
async function tab(i, first) {
  const s = TABS[i]; if (!s || (s.ok && !s.ok())) return;
  if (QUAD.on) quadToggle();
  $("tabs").querySelectorAll("button[data-i]").forEach(b => b.classList.toggle("on", +b.dataset.i === i));
  if (!first) { const w = $("wipe"); w.classList.remove("go"); void w.offsetWidth; w.classList.add("go"); await sleep(450); }
  TAB = i; if (!first) CAMLOCK = false;
  for (const id of DATA) if (ON.has(id)) { ON.delete(id); lclear(id); LY[id].off(); }
  clock(null);
  s.cam(); nowMarker(); frame(...s.hdr());
  for (const id of s.layers) { ON.add(id); LY[id].on(s.anim.includes(id)); }
  syncLayerUI(); legendNow(); creditNow(); hashKick();
}

/* ---------------- rail ---------------- */
/* watches & warnings: the advisory's "in effect for" bullets, warnings first, in the map legend's colours */
const WWC = { "Hurricane Warning": "#e3001b", "Hurricane Watch": "#ff6bd6", "Storm Surge Warning": "#b44cff", "Storm Surge Watch": "#d9a8ff", "Tropical Storm Warning": "#1f6bff", "Tropical Storm Watch": "#ffe14d" };
const WWO = ["Hurricane Warning", "Storm Surge Warning", "Tropical Storm Warning", "Hurricane Watch", "Storm Surge Watch", "Tropical Storm Watch"];
const wwSort = L => (L || []).slice().sort((a, b) => (WWO.indexOf(a.t) + 1 || 99) - (WWO.indexOf(b.t) + 1 || 99));
function wwBox(T, on) {
  if (!on) return `<div class="ww">No coastal watches or warnings.</div>`;
  const L = wwSort(T.ww_list), P = T.ww_paras?.length ? T.ww_paras : [T.watches || ""];
  const sum = L.length ? [...new Set(L.map(w => w.t.replace("Tropical Storm", "TS").replace("Storm Surge", "Surge")))].join(" · ") : "In effect";
  return `<details class="hzs wws"><summary>Watches & warnings <em>${esc(sum)}</em></summary><div class="wwt">${P.map(x => /:$/.test(x) ? `<h5>${esc(x.replace(/:$/, ""))}</h5>` : `<p>${esc(x).replace(/\s\*\s/g, "<br>• ")}</p>`).join("")}</div></details>`;
}
function railKey() {
  const T = D.text || {}, s = T.summary || {}, hz = T.hazards || [];
  const ww = T.watches || "", on = /warning|watch/i.test(ww) && !/no coastal watches or warnings/i.test(ww);
  const about = (s.about || [])[0];
  const hzH = h => { const m = h.match(/^([A-Z ]+):\s*(.*)$/); return `<div class="hz"><b>${esc(m ? m[1] : "")}</b> ${esc(m ? m[2] : h)}</div>`; };
  $("cKey").innerHTML = `<div class="ch"><b>The latest</b><span>NHC advisory ${esc(D.nhc.advisory)}</span></div>
    <div class="hl">${esc((T.headlines || [])[0] || "")}${T.headlines?.[1] ? `<span>${esc(T.headlines[1])}</span>` : ""}</div>
    ${s.location ? `<div class="kv"><span>Location</span><b>${esc(s.location)}</b></div>` : ""}
    ${about ? `<div class="kv"><span>${esc(about.replace(/^ABOUT \d+ MI \d+ KM /, "").replace(/^(\w+) OF /, "$1 of ").toLowerCase().replace(/(^|\s)\w/g, c => c.toUpperCase()).replace(/^([NSEW][nsew]{0,2}) of /i, (m, d) => d.toUpperCase() + " of "))}</span><b>${esc((about.match(/^ABOUT (\d+ MI)/) || [])[1] || "")}</b></div>` : ""}
    ${wwBox(T, on)}
    ${hz.length ? `<details class="hzs"><summary>Hazards <em>${hz.map(h => (h.match(/^([A-Z]+)/) || [""])[0]).filter(Boolean).join(" · ")}</em></summary>${hz.map(hzH).join("")}</details>` : ""}`;
  inCard("cKey", 0);
}
function railMW() {}
function zLocal(s) { const P = D.recon?.plan?.valid?.[0] || D.generated; return s.replace(/\b(\d\d)\/(\d\d)(\d\d)Z\b/gi, (m, d, h, mi) => dayhm(Date.UTC(+P.slice(0, 4), +P.slice(5, 7) - 1, +d, +h, +mi)) + " " + TZ); }
function railRecon() {
  const R0 = D.recon || {}, fl = (R0.plan?.flights || []).slice().sort((a, b) => isoMs(a.fix[0] || a.depart) - isoMs(b.fix[0] || b.depart)), cur = R0.current, nf = nextFlight();
  $("cRecon").innerHTML = `<div class="ch"><b>Recon</b><span>${R0.plan?.number ? `plan of the day ${esc(R0.plan.number)}` : "hurricane hunters"}</span></div>
    ${rFlights().slice().reverse().map(cur => { const live = Date.now() - Date.parse(cur.valid_end) < 30 * 6e4, sel = rFlights().length > 1 && cur === rCur(); return `<div class="fl pick${live ? " next" : " done"}${sel ? " sel" : ""}" data-mid="${esc(cur.mission_id)}" title="Show this flight on the map"><span class="ic">${PLANE("#ffd24a")}</span><b>${esc(ACFT(cur.aircraft))} · ${esc(cur.flight)}</b><small>${cur.ours ? "in the storm" : "synoptic surveillance"} · ${(cur.sondes || []).length} sonde${(cur.sondes || []).length === 1 ? "" : "s"}</small><em>${hm(cur.valid_end)}<br><small>${live ? "airborne" : "landed"}</small></em></div>`; }).join("")}
    ${fl.length ? fl.filter(f => isoMs(f.fix[f.fix.length - 1] || f.depart) > Date.now() - 36e5).slice(0, 3).map(f => { const t = isoMs(f.fix[0] || f.depart); return `<div class="fl${f === nf ? " next" : ""}"><span class="ic">${PLANE("#ffd24a")}</span>
      <b>${esc(f.flight.replace(/^FLIGHT \w+ - /, ""))} · ${esc(ACFT(f.flight))}</b><small>${esc(f.task.toLowerCase())} · ${esc(f.fix.map(x => dayhm(isoMs(x))).join(", "))}</small>
      <em data-t="${t}" data-w="${esc(dayhm(t))}">${esc(dayhm(t))}</em></div>`; }).join("")
      : `<div class="rnote">No reconnaissance tasked for this system in today's Plan of the Day.</div>`}
    ${(R0.plan?.outlook || []).slice(0, 1).map(o => `<div class="rnote">› ${esc(zLocal(o).replace(/^SUCCEEDING DAY OUTLOOK:\s*/, "").replace(/^[A-Z]\.\s*/, "").toLowerCase().replace(/(^|\.\s+)\w/g, c => c.toUpperCase()).replace(/\bal(\d\d)\b/gi, "AL$1").replace(/\bnoaa\b/gi, "NOAA").replace(/\b(am|pm|cdt|edt|cst|est|pdt|mdt|ast|hst)\b/g, m => m.toUpperCase()).replace(/\b(mon|tue|wed|thu|fri|sat|sun)\b/g, m => m[0].toUpperCase() + m.slice(1)).replace(/for for/g, "for").replace(/\bg-iv\b/g, "G-IV").replace(/\bp-3\b/g, "P-3").replace(/\bklal\b/g, "Lakeland"))}</div>`).join("")}`;
  inCard("cRecon", .3); tick();
}
function inCard(id, d) { const c = $(id); [...c.children].forEach((e, j) => e.style.setProperty("--j", j)); c.style.setProperty("--d", d + "s"); c.classList.remove("in"); void c.offsetWidth; c.classList.add("in"); }

/* ---------------- intensity chart ---------------- */
function intensityChart() {
  const el = $("intChart"), W = Math.max(320, el.clientWidth || 900), H = el.clientHeight || 290, P = { l: 40, r: 64, t: 12, b: 30 };
  const t0a = advTime(), best = (D.best || []).filter(p => Date.parse(p.t) >= t0a - 3 * 864e5);
  const AIDS = ["DSHP", "LGEM", "HFAI", "HFBI", "HWFI", "HMNI", "AVNI", "IVCN"].filter(t => D.models.aids[t]);
  const ser = AIDS.map(t => ({ t, pts: aidPts(D.models.aids[t]).filter(p => p[3] > 0).map(p => [isoMs(D.models.aids[t].cycle) + p[0] * 36e5, p[3]]) })).filter(s => s.pts.length > 1);
  const fc = D.fc.map(p => [p.t, p.kt, phase(p)]);
  const xs = [...best.map(p => Date.parse(p.t)), ...fc.map(p => p[0])], x0 = Math.min(...xs), x1 = Math.max(...fc.map(p => p[0]), t0a + 5 * 864e5);
  const ymax = Math.max(80, ...fc.map(p => p[1]), ...ser.flatMap(s => s.pts.map(p => p[1])), ...best.map(p => p.kt)) + 10;
  const X = t => P.l + (t - x0) / (x1 - x0) * (W - P.l - P.r), Y = v => H - P.b - v / ymax * (H - P.t - P.b);
  const path = pts => pts.filter(p => p[0] <= x1).map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)} ${Y(p[1]).toFixed(1)}`).join("");
  let s = `<svg viewBox="0 0 ${W} ${H}"><defs><clipPath id="rev"><rect class="revr" x="${X(t0a)}" y="0" width="${W}" height="${H}"/></clipPath></defs>`;
  CAT.forEach((c, i) => { const lo = c.lo, hi = Math.min(ymax, CAT[i + 1]?.lo ?? ymax); if (lo >= ymax) return;
    s += `<g class="band"><rect x="${P.l}" y="${Y(hi)}" width="${W - P.l - P.r}" height="${Y(lo) - Y(hi)}" fill="${c.c}" opacity=".07"/><line x1="${P.l}" x2="${W - P.r}" y1="${Y(lo)}" y2="${Y(lo)}" stroke="${c.c}" stroke-opacity=".35"/>
      <text x="${W - P.r + 6}" y="${(Y(lo) + Y(hi)) / 2 + 3}" fill="${c.c}">${c.k === "D" ? "TD" : c.k === "S" ? "TS" : "CAT " + c.k}</text></g>`; });
  for (let u = 0; u / WF().f <= ymax; u += WF().step) s += `<text class="ax" x="${P.l - 6}" y="${Y(u / WF().f) + 3}" text-anchor="end">${u}</text>`;
  for (let t = Math.ceil(x0 / 864e5) * 864e5; t <= x1; t += 864e5) { const d = new Date(t + 12 * 36e5 - OFF * 0); const lx = X(t);
    s += `<line class="grid" x1="${lx}" x2="${lx}" y1="${P.t}" y2="${H - P.b}"/>${dayLab(t, lx, H - P.b + 16, X(t + 864e5) - lx)}`; }
  s += `<text class="ax" x="${P.l - 6}" y="${P.t - 2}" text-anchor="end">${wl().toUpperCase()}</text>`;
  s += `<g clip-path="url(#rev)">`;
  ser.forEach((q, i) => { s += `<path class="ln dash" d="${path(q.pts)}" stroke="${AIDC[q.t]}" stroke-width="1.8" stroke-opacity=".9"/>`; });
  s += `<path class="ln dash" d="${path(fc)}" stroke="#06101f" stroke-width="7" stroke-opacity=".6"/><path class="ln dash" d="${path(fc)}" stroke="#fff" stroke-width="3.4"/>`;
  fc.forEach((p, i) => s += p[2] === "tc" ? `<circle cx="${X(p[0])}" cy="${Y(p[1])}" r="4.5" fill="${catOf(p[1]).c}" stroke="#06101f" stroke-width="1.5"/>` : phaseMark(p[2], X(p[0]), Y(p[1]), 4.8, catOf(p[1]).c, "#06101f", 1.5));
  ser.forEach(q => { const l = q.pts.filter(p => p[0] <= x1).slice(-1)[0]; });
  s += `</g>`;
  s += `<path class="ln" pathLength="1" d="${path(best.map(p => [Date.parse(p.t), p.kt]).concat([[t0a, D.kt]]))}" stroke="#ffffff" stroke-width="2.4" stroke-dasharray="1 1" style="--d:.1s;stroke-dasharray:1 1"/>`;
  best.forEach((p, i) => s += `<circle class="pt" style="--d:${(.1 + i / best.length * 1.6).toFixed(2)}s" cx="${X(Date.parse(p.t))}" cy="${Y(p.kt)}" r="3" fill="${catOf(p.kt).c}" stroke="#06101f"/>`);
  s += `<g class="now"><line x1="${X(t0a)}" x2="${X(t0a)}" y1="${P.t}" y2="${H - P.b}"/><text x="${X(t0a) + 5}" y="${P.t + 10}">NOW</text></g>`;
  const pk = D.peak, pkT = `PEAK ${wnd(pk.kt)} ${wl().toUpperCase()}`, pkW = Math.round(pkT.length * 7.8 + 16);   // the box grows with the unit (KT, MPH, KM/H)
  s += `<g class="lab" style="--d:1.9s"><rect x="${Math.min(W - P.r - pkW, X(pk.t) - pkW / 2)}" y="${Y(pk.kt) - 30}" width="${pkW}" height="18" rx="2" fill="#e8b53a"/><text x="${Math.min(W - P.r - pkW / 2, X(pk.t))}" y="${Y(pk.kt) - 17}" text-anchor="middle" fill="#141007">${pkT}</text></g>`;
  s += `</svg>`;
  el.innerHTML = s;
  { const t = el.querySelector(".lab text"), r = el.querySelector(".lab rect");   // fit the peak box to the rendered text
    if (t && r) try { const w = t.getComputedTextLength() + 16, cx = +t.getAttribute("x"); r.setAttribute("width", w); r.setAttribute("x", cx - w / 2); } catch (e) {} }
  const legendH = `<span style="color:#fff">━ NHC</span> ${ser.map(q => `<span style="color:${AIDC[q.t]}">━ ${q.t}</span>`).join(" ")}`;
  $("intLeg").innerHTML = legendH;
  el.classList.remove("in"); void el.offsetWidth; el.classList.add("in");
  const r = el.querySelector(".revr"), w0 = W - X(t0a); r.setAttribute("width", 0);
  const ts = performance.now() + 900; const f = now => { const p = Math.max(0, Math.min(1, (now - ts) / 2200)); r.setAttribute("width", w0 * ease(p)); if (p < 1) requestAnimationFrame(f); }; requestAnimationFrame(f);
}

/* day labels on a time axis, thinned to the space a day gets: full "TUE OCT 6", short "TUE 6", or every other day */
function dayLab(t, x, y, px, short) {
  const d = local(t + 12 * 36e5);
  if (px < 44 && Math.round(t / 864e5) % 2) return "";
  return `<text class="ax" x="${x + 4}" y="${y}">${px < 92 || short ? `${DOW[d.getUTCDay()]} ${d.getUTCDate()}` : `${DOW[d.getUTCDay()]} ${MON[d.getUTCMonth()]} ${d.getUTCDate()}`}</text>`;
}

/* ---------------- guidance board ---------------- */
function guidanceBoard() {
  const A = D.models.aids, rows = [];
  for (const t of ["OFCL", "IVCN", "HFAI", "HFBI", "HWFI", "HMNI", "DSHP", "LGEM", "AVNI", "CTCI"]) {
    if (!A[t]) continue; const p = aidPts(A[t]).filter(x => x[3] > 0 && x[0] <= 120); if (!p.length) continue;
    const pk = p.reduce((m, x) => x[3] > m[3] ? x : m), c0 = isoMs(A[t].cycle);
    rows.push({ t, kt: pk[3], when: c0 + pk[0] * 36e5 });
  }
  rows.sort((a, b) => b.kt - a.kt);
  const mx = Math.max(100, ...rows.map(r => r.kt));

  $("guid").innerHTML = `<div class="gb h"><span>Aid</span><span>Peak wind</span><span style="text-align:right">${wl()}</span><span style="text-align:right">when</span></div>` +
    rows.map((r, j) => { const c = r.t === "OFCL" ? "#ffffff" : AIDC[r.t] || "#9fb3d6"; return `<div class="gb" style="--c:${c};--j:${j}" title="${esc(AIDNAME[r.t] || r.t)}"><span class="n"><i></i>${r.t === "OFCL" ? "NHC" : r.t}</span>
      <span class="bar"><i style="--w:${(r.kt / mx * 100).toFixed(1)}%;--c:${catOf(r.kt).c}"></i></span><b>${wnd(r.kt)}</b><em>${DOW[local(r.when).getUTCDay()]} ${hm(r.when, false)}</em></div>`; }).join("") +
    `<div class="gnote">Bars wear the Saffir-Simpson colour of each aid's peak. Early-cycle (interpolated) aids, ${esc(D.models.cycle?.slice(11, 13) || "")}Z.</div>`;
}

/* ---------------- crawl ---------------- */
function crawl() {
  const T = D.text || {}, s = T.summary || {}, nm = (D.nhc.name || "").toUpperCase();
  $("crawlLbl").textContent = `${{ TD: "TD", TS: "TS", HU: "HURRICANE", MH: "HURRICANE", PTC: "PTC" }[D.nhc.classification] || ""} ${nm}`.trim();
  const it = [...(T.headlines || []).map(h => h.toLowerCase().replace(/(^|\s)\w/g, c => c.toUpperCase())),
    s["maximum sustained winds"] && `Max winds ${s["maximum sustained winds"].replace(/\s+\d+ KM\/H/, "").toLowerCase()}`,
    s["present movement"] && `Moving ${s["present movement"].replace(/ OR \d+ DEGREES AT/, " at").replace(/\s+\d+ KM\/H/, "").toLowerCase()}`,
    ...(s.about || []).map(a => a.replace(/\s+\d+ KM/, "").toLowerCase().replace(/^about/, "About").replace(/\b(mexico|mississippi|progreso|river)\b/g, w => w[0].toUpperCase() + w.slice(1))),
    ...(T.ww_list?.length ? wwSort(T.ww_list).map(w => `${w.t}: ${w.a.join("; ")}`) : []), ...(T.hazards || []).map(h => h.replace(/^([A-Z ]+):/, (m, a) => a.trim() + " —")),
    nextFlight() && `Next Hurricane Hunter fix: ${nextFlight().flight.replace(/^FLIGHT \w+ - /, "")} ${dayhm(isoMs(nextFlight().fix[0]))} ${TZ}`,
    T.next].filter(Boolean);
  const sp = $("crawlTx"); sp.innerHTML = it.map(esc).join("<i>◆</i>");
  sp.style.setProperty("--dur", Math.max(40, sp.textContent.length / 9) + "s");
}

document.querySelectorAll(".ctab button").forEach(b => b.onclick = () => {
  document.querySelectorAll(".ctab button").forEach(x => x.classList.toggle("on", x === b));
  const v = b.dataset.v; $("intChart").hidden = v !== "int"; $("guid").hidden = v !== "guid"; $("ensChart").hidden = v !== "ens"; $("shipsView").hidden = v !== "ships"; $("intLeg").hidden = v === "guid";
  if (v === "int") intensityChart(); else if (v === "ens") ensChart(); else if (v === "ships") shipsView(); else guidanceBoard();
});

/* ---------------- ensemble intensity (the site's cyclolab/<sid>/ensemble_v2.json: ECMWF ENS + GEFS members) ---------------- */
const ENS = { doc: null, t: 0, metric: "vmax" };
async function ensLoad() {
  if (ENS.doc && Date.now() - ENS.t < 6e5) return ENS.doc;
  try { const r = await fetch(`${CDN}/cyclolab/NHC_${SID.toUpperCase()}/ensemble_v2.json?t=${Math.floor(Date.now() / 6e5)}`); ENS.doc = r.ok ? await r.json() : null; } catch (e) { ENS.doc = null; }
  ENS.t = Date.now(); return ENS.doc;
}
const cycMs = c => Date.UTC(+c.slice(0, 4), +c.slice(4, 6) - 1, +c.slice(6, 8), +c.slice(8, 10));
async function ensChart() {
  const el = $("ensChart"), doc = await ensLoad(), M = ENS.metric;
  const leg = $("intLeg");
  if (!doc || !(doc.sources || []).length) { el.innerHTML = `<div class="rnote" style="padding:30px 10px">No ensemble members for this storm yet. They appear after the next guidance run that tracks it (every 6 hours).</div>`; leg.innerHTML = ""; return; }
  const gefsCyc = D.models?.gefs && Object.values(D.models.gefs)[0]?.cycle;
  const srcs = doc.sources.map(s => { const c = s.cycle ? cycMs(s.cycle) : gefsCyc ? isoMs(gefsCyc) : null; if (c == null) return null;
    return { label: s.label, col: s.model === "gefs" ? "#5dd3ff" : "#ff7a5c", mem: s.members.map(m => s.taus.map((t, i) => [c + t * 36e5, m[M]?.[i]]).filter(p => p[1] != null)) }; }).filter(Boolean);
  const W = Math.max(320, el.clientWidth || 900), H = el.clientHeight || 290, P = { l: 44, r: 64, t: 12, b: 30 }, t0a = advTime();
  const best = (D.best || []).filter(p => Date.parse(p.t) >= t0a - 2 * 864e5).map(p => [Date.parse(p.t), M === "vmax" ? p.kt : p.mb]).filter(p => p[1]);
  const fc = M === "vmax" ? D.fc.map(p => [p.t, p.kt]) : [];
  const x0 = Math.min(t0a - 2 * 864e5, ...srcs.map(s => s.mem[0]?.[0]?.[0] ?? t0a)), x1 = t0a + 7 * 864e5;
  const all = srcs.flatMap(s => s.mem.flat().filter(p => p[0] <= x1).map(p => p[1])).concat(best.map(p => p[1]), fc.map(p => p[1]));
  const lo = M === "vmax" ? 0 : Math.floor(Math.min(...all) / 10) * 10 - 5, hi = M === "vmax" ? Math.max(80, ...all) + 10 : 1015;
  const X = t => P.l + (t - x0) / (x1 - x0) * (W - P.l - P.r), Y = v => H - P.b - (v - lo) / (hi - lo) * (H - P.t - P.b);
  const path = pts => pts.filter(p => p[0] >= x0 && p[0] <= x1).map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)} ${Y(p[1]).toFixed(1)}`).join("");
  let s = `<svg viewBox="0 0 ${W} ${H}">`;
  if (M === "vmax") CAT.forEach((c, i) => { const a = c.lo, b = Math.min(hi, CAT[i + 1]?.lo ?? hi); if (a >= hi) return;
    s += `<g class="band"><rect x="${P.l}" y="${Y(b)}" width="${W - P.l - P.r}" height="${Y(a) - Y(b)}" fill="${c.c}" opacity=".07"/><text x="${W - P.r + 6}" y="${(Y(a) + Y(b)) / 2 + 3}" fill="${c.c}">${c.k === "D" ? "TD" : c.k === "S" ? "TS" : "CAT " + c.k}</text></g>`; });
  const tf = M === "vmax" ? WF().f : U.pres === "inhg" ? .02953 : 1, tst = M === "vmax" ? WF().step : U.pres === "inhg" ? .3 : 10;
  for (let u = Math.ceil(lo * tf / tst) * tst; u / tf <= hi; u += tst) s += `<line class="grid" x1="${P.l}" x2="${W - P.r}" y1="${Y(u / tf)}" y2="${Y(u / tf)}"/><text class="ax" x="${P.l - 6}" y="${Y(u / tf) + 3}" text-anchor="end">${M === "mslp" && U.pres === "inhg" ? u.toFixed(1) : Math.round(u)}</text>`;
  for (let t = Math.ceil(x0 / 864e5) * 864e5; t <= x1; t += 864e5) s += `<line class="grid" x1="${X(t)}" x2="${X(t)}" y1="${P.t}" y2="${H - P.b}"/>${dayLab(t, X(t), H - P.b + 16, X(t + 864e5) - X(t), true)}`;
  s += `<text class="ax" x="${P.l - 6}" y="${P.t - 2}" text-anchor="end">${M === "vmax" ? wl().toUpperCase() : pl().toUpperCase()}</text>`;
  for (const src of srcs) {
    for (const m of src.mem) s += `<path class="ln dash" d="${path(m)}" stroke="${src.col}" stroke-width="1" stroke-opacity=".28" fill="none"/>`;
    // mean and the 10-90 % band, step by step across members
    const steps = [...new Set(src.mem.flat().map(p => p[0]))].sort((a, b) => a - b), mean = [], p10 = [], p90 = [];
    for (const t of steps) { const v = src.mem.map(m => m.find(p => p[0] === t)?.[1]).filter(x => x != null).sort((a, b) => a - b); if (v.length < 5) continue;
      mean.push([t, v.reduce((a, b) => a + b, 0) / v.length]); p10.push([t, v[Math.floor(v.length * .1)]]); p90.push([t, v[Math.ceil(v.length * .9) - 1]]); }
    if (p10.length > 1) s += `<path d="${path(p90)}L${path(p10.slice().reverse()).slice(1)}Z" fill="${src.col}" opacity=".14"/>`;
    s += `<path class="ln dash" d="${path(mean)}" stroke="#06101f" stroke-width="5" stroke-opacity=".5" fill="none"/><path class="ln dash" d="${path(mean)}" stroke="${src.col}" stroke-width="2.6" fill="none"/>`;
  }
  if (fc.length) s += `<path class="ln dash" d="${path(fc)}" stroke="#06101f" stroke-width="6" stroke-opacity=".6" fill="none"/><path class="ln dash" d="${path(fc)}" stroke="#fff" stroke-width="3" fill="none"/>`;
  s += `<path class="ln dash" d="${path(best)}" stroke="#fff" stroke-width="2" stroke-dasharray="2 4" fill="none"/>`;
  s += `<g class="now"><line x1="${X(t0a)}" x2="${X(t0a)}" y1="${P.t}" y2="${H - P.b}"/><text x="${X(t0a) + 5}" y="${P.t + 10}">NOW</text></g></svg>`;
  el.innerHTML = s;
  leg.innerHTML = `<span class="ensm">${["vmax", "mslp"].map(k => `<button data-m="${k}" class="${k === M ? "on" : ""}">${k === "vmax" ? "WIND" : "PRESSURE"}</button>`).join("")}</span> ${srcs.map(x => `<span style="color:${x.col}">━ ${esc(x.label)} (${x.mem.length})</span>`).join(" ")}${fc.length ? ` <span style="color:#fff">━ NHC</span>` : ""}`;
  leg.querySelectorAll("[data-m]").forEach(b => b.onclick = () => { ENS.metric = b.dataset.m; ensChart(); });
}
