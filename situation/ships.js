/* Situation Room: SHIPS statistical-dynamical intensity guidance (D.ships, parsed from NHC's per-cycle SHIPS text by
   situation/ships.py). One view in the chart card: intensity (land / no land / LGEM / NHC, past runs, storm type, land),
   the rapid-intensification matrix, the environment along the track and what drives the forecast change.
   A hover anywhere reads every panel out at the same forecast hour; a click picks that hour for the drivers. */
const SHP = { tau: null, drv: null };
const SHPT = { TROP: ["Tropical", "#2c4f99"], SUBT: ["Subtropical", "#3d7fa6"], EXTP: ["Extratropical", "#6b7389"], LOW: ["Remnant low", "#55607a"] };
const SHPC = { up: "#ff8a5c", dn: "#5dd3ff", fav: "rgba(70,197,106,.13)", bad: "rgba(245,51,60,.13)" };
const SHPENV = [
  { k: "shear", t: "Vertical wind shear", u: "kt", fav: [0, 10], bad: [20, 99], fmt: v => Math.round(v) },
  { k: "sst", t: "Sea surface temperature", u: "°C", fav: [28, 40], bad: [0, 26], fmt: v => v.toFixed(1) },
  { k: "pot", t: "Potential intensity", u: "kt", vs: "v", fmt: v => Math.round(v) },
  { k: "rh", t: "Mid-level humidity, 700-500 mb", u: "%", fav: [70, 100], bad: [0, 50], fmt: v => Math.round(v) },
  { k: "ohc", t: "Ocean heat content", u: "kJ/cm²", fav: [60, 999], bad: [0, 20], fmt: v => Math.round(v) },
  { k: "div", t: "Upper divergence, 200 mb", u: "×10⁻⁷/s", fav: [40, 999], bad: [-999, 0], fmt: v => Math.round(v) }];
const SHPNAME = { "SAMPLE MEAN CHANGE": "Sample mean", "SST POTENTIAL": "SST potential", "VERTICAL SHEAR MAG": "Shear magnitude", "VERTICAL SHEAR ADJ": "Shear adjustment",
  "VERTICAL SHEAR DIR": "Shear direction", "PERSISTENCE": "Persistence", "200/250 MB TEMP.": "Upper temperature", "THETA_E EXCESS": "Theta-e excess", "700-500 MB RH": "Mid-level humidity",
  "MODEL VTX TENDENCY": "Model vortex trend", "850 MB ENV VORTICITY": "Low-level vorticity", "200 MB DIVERGENCE": "Upper divergence", "850-700 T ADVEC": "Warm advection",
  "ZONAL STORM MOTION": "Zonal motion", "STEERING LEVEL PRES": "Steering depth", "DAYS FROM CLIM. PEAK": "Season timing", "GOES PREDICTORS": "Satellite (GOES)",
  "OCEAN HEAT CONTENT": "Ocean heat", "RI POTENTIAL": "RI potential", "LAND": "Land" };
const shpName = n => SHPNAME[n] || n.toLowerCase().replace(/(^|\s)\w/g, c => c.toUpperCase());

function shipsView() {
  const el = $("shipsView"), S = D.ships;
  if (!S || S.err || !S.taus) { el.innerHTML = `<div class="rnote">No SHIPS guidance for this system yet. NHC runs it once a storm is being advised on.</div>`; $("intLeg").innerHTML = ""; return; }
  const init = Date.parse(S.init), R = S.rows, T = S.taus;
  const last = T.reduce((m, t, i) => R.v?.[i] != null ? t : m, 0), tmax = Math.max(72, last);
  const ix = T.map((t, i) => i).filter(i => T[i] <= tmax);
  if (SHP.drv == null || !(S.ctaus || []).includes(SHP.drv)) SHP.drv = (S.ctaus || []).includes(48) && 48 <= last ? 48 : (S.ctaus || [])[3] ?? null;
  const age = Math.round((Date.now() - init) / 36e5);
  $("intLeg").innerHTML = `<span class="shp-run">${esc(S.init.slice(11, 13))}Z run · ${age} h ago${S.model ? ` · ${esc(S.model)}` : ""}</span>`;
  el.innerHTML = `<div class="shp">
    <div class="shp-p shp-int"><div class="shp-h"><b>Intensity</b><span class="shp-key">
      <i style="--c:${AIDC.DSHP}"></i>SHIPS<i class="d" style="--c:${AIDC.DSHP}"></i>No land<i style="--c:${AIDC.LGEM}"></i>LGEM<i style="--c:#fff"></i>NHC${(S.prior || []).length ? `<i class="p"></i>Earlier runs` : ""}</span></div>
      <div class="shp-svg" data-k="int"></div><div class="shp-read" id="shpRead"></div></div>
    <div class="shp-p shp-ri"></div>
    <div class="shp-p shp-env"><div class="shp-h"><b>Environment along the track</b><span class="shp-key"><i class="z" style="--c:${SHPC.fav}"></i>Favorable<i class="z" style="--c:${SHPC.bad}"></i>Hostile</span></div><div class="shp-grid"></div></div>
    <div class="shp-p shp-drv"></div>
    <div class="shp-foot"></div></div>`;
  const X0 = 34, W0 = Math.max(300, el.querySelector(".shp-int .shp-svg").clientWidth || 600);
  const mk = (W, P) => ({ W, P, X: t => P.l + t / tmax * (W - P.l - P.r) });
  /* ---- intensity ---- */
  {
    const H = 250, g = mk(W0, { l: X0, r: 54, t: 10, b: 40 }), { X } = g;
    const vals = ["v", "vnl", "lgem"].flatMap(k => ix.map(i => R[k]?.[i])).concat(D.fc.map(p => p.kt), ...(S.prior || []).map(p => p.v || [])).filter(v => v != null);
    const ymax = Math.max(80, ...vals) + 12, Y = v => H - g.P.b - v / ymax * (H - g.P.t - g.P.b);
    const pts = k => ix.filter(i => R[k]?.[i] != null).map(i => [T[i], R[k][i]]);
    const path = a => a.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)} ${Y(p[1]).toFixed(1)}`).join("");
    let s = `<svg viewBox="0 0 ${g.W} ${H}" data-l="${g.P.l}" data-r="${g.W - g.P.r}"><defs><pattern id="shpLand" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" stroke="rgba(214,227,255,.22)" stroke-width="2"/></pattern></defs>`;
    CAT.forEach((c, i) => { const lo = c.lo, hi = Math.min(ymax, CAT[i + 1]?.lo ?? ymax); if (lo >= ymax) return;
      s += `<g class="band"><rect x="${g.P.l}" y="${Y(hi)}" width="${g.W - g.P.l - g.P.r}" height="${Y(lo) - Y(hi)}" fill="${c.c}" opacity=".07"/><line x1="${g.P.l}" x2="${g.W - g.P.r}" y1="${Y(lo)}" y2="${Y(lo)}" stroke="${c.c}" stroke-opacity=".3"/>
        <text x="${g.W - g.P.r + 6}" y="${(Y(lo) + Y(hi)) / 2 + 3}" fill="${c.c}">${c.k === "D" ? "TD" : c.k === "S" ? "TS" : "CAT " + c.k}</text></g>`; });
    /* over land: hatch where SHIPS puts the centre inland (LAND distance < 0) */
    for (let j = 0; j < ix.length - 1; j++) { const a = R.land?.[ix[j]], b = R.land?.[ix[j + 1]]; if (a == null || b == null || (a > 0 && b > 0)) continue;
      const ta = a <= 0 ? T[ix[j]] : T[ix[j]] + (T[ix[j + 1]] - T[ix[j]]) * a / (a - b), tb = b <= 0 ? T[ix[j + 1]] : T[ix[j]] + (T[ix[j + 1]] - T[ix[j]]) * a / (a - b);
      s += `<rect x="${X(ta)}" y="${g.P.t}" width="${Math.max(0, X(tb) - X(ta))}" height="${H - g.P.b - g.P.t}" fill="url(#shpLand)"/>`; }
    const fl = ix.find(i => R.land?.[i] != null && R.land[i] <= 0); if (fl != null) { const lx = X(T[fl]), end = lx > g.W - g.P.r - 80; s += `<text class="ax" x="${end ? g.W - g.P.r - 4 : lx + 4}" y="${g.P.t + 11}"${end ? ' text-anchor="end"' : ""}>OVER LAND</text>`; }
    for (let u = 0; u / WF().f <= ymax; u += WF().step) s += `<text class="ax" x="${g.P.l - 6}" y="${Y(u / WF().f) + 3}" text-anchor="end">${u}</text>`;
    s += `<text class="ax" x="${g.P.l - 6}" y="${g.P.t - 1}" text-anchor="end">${wl().toUpperCase()}</text>`;
    for (let t = 0; t <= tmax; t += 12) { s += `<line class="grid" x1="${X(t)}" x2="${X(t)}" y1="${g.P.t}" y2="${H - g.P.b}"${t % 24 ? ' stroke-opacity=".45"' : ""}/>`;
      if (t % 24 === 0) s += `<text class="ax" x="${X(t)}" y="${H - g.P.b + 27}" text-anchor="middle">${t}h</text><text class="ax sub" x="${X(t)}" y="${H - g.P.b + 37}" text-anchor="middle">${DOW[local(init + t * 36e5).getUTCDay()]} ${hm(init + t * 36e5, false)}</text>`; }
    /* storm type ribbon */
    const ry = H - g.P.b + 4;
    for (let j = 0; j < ix.length - 1; j++) { const ty = R.type?.[ix[j + 1]] || R.type?.[ix[j]]; if (!ty) continue; const c = (SHPT[ty] || [ty, "#55607a"])[1];
      s += `<rect x="${X(T[ix[j]])}" y="${ry}" width="${X(T[ix[j + 1]]) - X(T[ix[j]])}" height="9" fill="${c}"/>`; }
    let run = null; const runs = [];
    ix.forEach(i => { const ty = R.type?.[i]; if (!ty) return; if (run && run.ty === ty) run.b = T[i]; else { run && runs.push(run); run = { ty, a: T[i], b: T[i] }; } }); run && runs.push(run);
    runs.forEach(r => { if (X(r.b) - X(r.a) > 60) s += `<text class="rib" x="${(X(r.a) + X(r.b)) / 2}" y="${ry + 7.5}" text-anchor="middle">${(SHPT[r.ty] || [r.ty])[0].toUpperCase()}</text>`; });
    /* earlier runs, shifted to this run's clock */
    (S.prior || []).forEach((p, k) => { const dh = (Date.parse(p.init) - init) / 36e5, a = p.taus.map((t, i) => [t + dh, p.v?.[i]]).filter(q => q[1] != null && q[0] >= 0 && q[0] <= tmax);
      if (a.length > 1) s += `<path d="${path(a)}" fill="none" stroke="#d6e3ff" stroke-opacity="${(.42 - k * .1).toFixed(2)}" stroke-width="1.4"/>`; });
    s += `<path class="ln dash" d="${path(pts("vnl"))}" stroke="${AIDC.DSHP}" stroke-width="1.8" stroke-dasharray="5 4" fill="none" style="stroke-dasharray:5 4"/>`;
    s += `<path class="ln dash" d="${path(pts("lgem"))}" stroke="${AIDC.LGEM}" stroke-width="2.2" fill="none"/>`;
    const fc = D.fc.map(p => [(p.t - init) / 36e5, p.kt, phase(p)]).filter(p => p[1] != null && p[0] >= -1 && p[0] <= tmax);
    if (fc.length > 1) { s += `<path class="ln dash" d="${path(fc)}" stroke="#06101f" stroke-width="6" stroke-opacity=".55" fill="none"/><path class="ln dash" d="${path(fc)}" stroke="#fff" stroke-width="2.6" fill="none"/>`;
      fc.forEach(p => s += p[2] === "tc" ? `<circle cx="${X(p[0])}" cy="${Y(p[1])}" r="3.6" fill="${catOf(p[1]).c}" stroke="#06101f" stroke-width="1.3"/>` : phaseMark(p[2], X(p[0]), Y(p[1]), 4, catOf(p[1]).c, "#06101f", 1.3)); }
    const sv = pts("v");
    s += `<path class="ln dash" d="${path(sv)}" stroke="#06101f" stroke-width="7" stroke-opacity=".5" fill="none"/><path class="ln dash" d="${path(sv)}" stroke="${AIDC.DSHP}" stroke-width="3.2" fill="none"/>`;
    const pk = sv.reduce((m, p) => p[1] > m[1] ? p : m, sv[0]);
    if (pk && pk[0] > 0) { const lab = `PEAK ${wnd(pk[1])} ${wl().toUpperCase()}`, lw = lab.length * 6.2 + 12, lx = Math.min(g.W - g.P.r - lw, Math.max(g.P.l, X(pk[0]) - lw / 2));
      s += `<circle cx="${X(pk[0])}" cy="${Y(pk[1])}" r="4.5" fill="${AIDC.DSHP}" stroke="#06101f" stroke-width="1.5"/><rect x="${lx}" y="${Y(pk[1]) - 25}" width="${lw}" height="15" rx="2" fill="${AIDC.DSHP}"/><text class="pk" x="${lx + lw / 2}" y="${Y(pk[1]) - 14.5}" text-anchor="middle">${lab}</text>`; }
    s += `<line class="cur" x1="0" x2="0" y1="${g.P.t}" y2="${H - g.P.b}"/></svg>`;
    el.querySelector(".shp-int .shp-svg").innerHTML = s;
  }
  /* ---- environment small multiples ---- */
  {
    const grid = el.querySelector(".shp-grid"), have = SHPENV.filter(e => R[e.k]?.some(v => v != null));
    grid.innerHTML = have.map(e => `<div class="shp-sm" data-k="${e.k}"><div class="shp-smh"><span>${esc(e.t)}</span><b data-v="${e.k}"></b></div><div class="shp-svg"></div></div>`).join("");
    const W = Math.max(150, grid.querySelector(".shp-sm .shp-svg")?.clientWidth || 260), H = 64;
    have.forEach(e => {
      const g = mk(W, { l: 4, r: 4, t: 4, b: 4 }), { X } = g, a = ix.filter(i => R[e.k][i] != null).map(i => [T[i], R[e.k][i]]);
      const b = e.vs ? ix.filter(i => R[e.vs]?.[i] != null).map(i => [T[i], R[e.vs][i]]) : [];
      const all = a.concat(b).map(p => p[1]); let lo = Math.min(...all), hi = Math.max(...all);
      const pad = (hi - lo) * .15 || 2; lo -= pad; hi += pad;
      const Y = v => H - g.P.b - (v - lo) / (hi - lo) * (H - g.P.t - g.P.b);
      const path = q => q.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)} ${Y(p[1]).toFixed(1)}`).join("");
      let s = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" data-l="${g.P.l}" data-r="${W - g.P.r}">`;
      const zone = (r, c) => { const y1 = Y(Math.min(hi, r[1])), y0 = Y(Math.max(lo, r[0])); if (y0 > y1) s += `<rect x="${g.P.l}" y="${y1}" width="${W - g.P.l - g.P.r}" height="${y0 - y1}" fill="${c}"/>`; };
      if (e.fav) { zone(e.fav, SHPC.fav); zone(e.bad, SHPC.bad); }
      for (let t = 24; t < tmax; t += 24) s += `<line class="grid" x1="${X(t)}" x2="${X(t)}" y1="0" y2="${H}"/>`;
      if (b.length > 1) s += `<path d="${path(b)}" fill="none" stroke="${AIDC.DSHP}" stroke-width="1.6" stroke-dasharray="4 3" vector-effect="non-scaling-stroke"/>`;
      s += `<path d="${path(a)}" fill="none" stroke="#fff" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>`;
      s += `<line class="cur" x1="0" x2="0" y1="0" y2="${H}" vector-effect="non-scaling-stroke"/></svg>`;
      grid.querySelector(`[data-k="${e.k}"] .shp-svg`).innerHTML = s;
    });
  }
  shipsRI(el.querySelector(".shp-ri"));
  shipsDrivers(el.querySelector(".shp-drv"));
  /* ---- footer: the single-number indices ---- */
  const M = S.misc || {}, chip = (t, v, sub) => `<div class="shp-chip"><span>${esc(t)}</span><b>${v}</b>${sub ? `<small>${esc(sub)}</small>` : ""}</div>`;
  el.querySelector(".shp-foot").innerHTML = [
    M.prelim_ri != null && chip("RI chance, 35 kt in 36 h", `${Math.round(M.prelim_ri)}%`, "SHIPS preliminary"),
    M.ir_cold != null && chip("Cold cloud near the core", `${Math.round(M.ir_cold)}%`, "pixels below -20 °C, 50-200 km"),
    M.ir_sd != null && chip("Cloud-top uniformity", M.ir_sd.toFixed(1), "IR std dev, lower = more symmetric"),
    M.erc && chip("Secondary eyewall, 48 h", `${M.erc[3]}%`, "probability of formation"),
    M.ahi != null && chip("Annular index", Math.round(M.ahi), M.ahi > 0 ? "annular structure" : "not annular"),
    M.steer_p != null && chip("Steering layer", `${Math.round(M.steer_p)} mb`, `track from ${S.track || "NHC"}`)].filter(Boolean).join("");
  shipsHover(SHP.tau ?? 0);
  const box = el.querySelector(".shp");
  box.onmousemove = ev => { const sv = ev.target.closest(".shp-int svg, .shp-sm svg"); if (!sv) return; const r = sv.getBoundingClientRect(), vb = sv.viewBox.baseVal;
    const px = (ev.clientX - r.left) / r.width * vb.width, l = +sv.dataset.l, rr = +sv.dataset.r; shipsHover(Math.max(0, Math.min(tmax, (px - l) / (rr - l) * tmax))); };
  box.onclick = ev => { if (!ev.target.closest(".shp-int svg, .shp-sm svg") || SHP.tau == null) return; const c = (S.ctaus || []).reduce((m, t) => Math.abs(t - SHP.tau) < Math.abs(m - SHP.tau) ? t : m, S.ctaus[0]);
    if (c != null) { SHP.drv = c; shipsDrivers(el.querySelector(".shp-drv")); } };
  [...box.children].forEach((c, j) => c.style.setProperty("--j", j));
  el.classList.remove("in"); void el.offsetWidth; el.classList.add("in");
}

function shipsHover(tau) {
  const S = D.ships, R = S.rows, T = S.taus, el = $("shipsView"); if (!el) return;
  const i = T.reduce((m, t, j) => Math.abs(t - tau) < Math.abs(T[m] - tau) ? j : m, 0), t = T[i]; SHP.tau = t;
  const tmax = Math.max(72, T.reduce((m, x, j) => R.v?.[j] != null ? x : m, 0));
  el.querySelectorAll("svg[data-l]").forEach(sv => { const l = +sv.dataset.l, r = +sv.dataset.r, x = l + t / tmax * (r - l), c = sv.querySelector(".cur"); if (c) { c.setAttribute("x1", x); c.setAttribute("x2", x); } });
  SHPENV.forEach(e => { const b = el.querySelector(`[data-v="${e.k}"]`); if (b) b.textContent = R[e.k]?.[i] != null ? `${e.fmt(R[e.k][i])} ${e.u}` : "–"; });
  const vt = Date.parse(S.init) + t * 36e5, ty = R.type?.[i], f = v => v == null ? "–" : `${wnd(v)}`;
  $("shpRead").innerHTML = `<b>${t} h</b><span>${DOW[local(vt).getUTCDay()]} ${hm(vt)} ${TZ}</span><span><i style="--c:${AIDC.DSHP}"></i>SHIPS ${f(R.v?.[i])}</span><span><i class="d" style="--c:${AIDC.DSHP}"></i>no land ${f(R.vnl?.[i])}</span><span><i style="--c:${AIDC.LGEM}"></i>LGEM ${f(R.lgem?.[i])} ${wl()}</span>${ty ? `<span>${esc((SHPT[ty] || [ty])[0])}</span>` : ""}${R.land?.[i] != null ? `<span>${R.land[i] <= 0 ? "over land" : `${Math.round(R.land[i] * .621)} mi from land`}</span>` : ""}`;
}

function shipsRI(box) {
  const S = D.ships, cols = S.ri_cols?.length ? S.ri_cols : (S.ri || []).map(r => r.k), M = S.ri_matrix || {}, names = Object.keys(M);
  if (!cols.length) { box.innerHTML = `<div class="shp-h"><b>Rapid intensification</b></div><div class="rnote">No RI probabilities in this run.</div>`; return; }
  const cell = p => p == null ? `<td class="na">–</td>` : `<td style="background:rgba(245,51,60,${Math.min(.85, .04 + p / 70).toFixed(2)})">${p < 1 && p > 0 ? "<1" : Math.round(p)}</td>`;
  const byk = Object.fromEntries((S.ri || []).map(r => [r.k.replace(/\s/g, ""), r]));
  const cons = M.Consensus || M.SDCON || M["SHIPS-RII"] || [], best = cons.reduce((m, p, j) => p > (m.p ?? -1) ? { p, j } : m, {});
  const head = cols.map(c => { const [a, b] = c.split("/"); return `<th>${a}<small>kt / ${b} h</small></th>`; }).join("");
  const RIN = { "SHIPS-RII": "SHIPS-RII", Logistic: "Logistic", Bayesian: "Bayesian", Consensus: "Consensus", DTOPS: "DTOPS", SDCON: "SDCON" };
  let rows = names.map(n => `<tr${n === "Consensus" ? ' class="k"' : ""}><th>${esc(RIN[n] || n)}</th>${cols.map((c, j) => cell(M[n][j])).join("")}</tr>`).join("");
  if (!names.length) rows = `<tr class="k"><th>SHIPS-RII</th>${cols.map(c => cell(byk[c.replace(/\s/g, "")]?.p)).join("")}</tr>`;
  rows += `<tr class="cl"><th>Climatology</th>${cols.map(c => { const r = byk[c.replace(/\s/g, "")]; return `<td>${r ? r.c.toFixed(1) : "–"}</td>`; }).join("")}</tr>`;
  rows += `<tr class="cl"><th>× climatology</th>${cols.map(c => { const r = byk[c.replace(/\s/g, "")]; return `<td${r && r.x >= 2 ? ' class="hot"' : ""}>${r ? r.x.toFixed(1) : "–"}</td>`; }).join("")}</tr>`;
  const rii = (S.rii || []).slice().sort((a, b) => Math.abs(b.pc) - Math.abs(a.pc));
  box.innerHTML = `<div class="shp-h"><b>Rapid intensification</b><span>% chance, by threshold</span></div>
    ${best.p != null ? `<div class="shp-big"><b>${Math.round(best.p)}%</b><span>highest consensus chance: ${esc(cols[best.j].split("/")[0])} kt in ${esc(cols[best.j].split("/")[1])} h${byk[cols[best.j]] ? ` · ${byk[cols[best.j]].x.toFixed(1)}× normal` : ""}</span></div>` : ""}
    <div class="shp-tw"><table class="shp-mx"><thead><tr><th></th>${head}</tr></thead><tbody>${rows}</tbody></table></div>
    ${rii.length ? `<details class="hzs shp-rii"><summary>RI predictors <em>${esc((S.rii_title || "").replace(/ OR MORE MAXIMUM WIND INCREASE IN NEXT /, "+ in ").toLowerCase())}</em></summary>
      ${rii.map(r => `<div class="shp-rp"><span>${esc(r.n.replace(/\s*\(.*?\)\s*$/, "").replace(/\s*:$/, ""))}</span><em>${r.v}</em><i><u style="width:${(Math.max(0, Math.min(1, r.s)) * 100).toFixed(0)}%"></u></i><b class="${r.pc < 0 ? "dn" : ""}">${r.pc > 0 ? "+" : ""}${r.pc.toFixed(1)}</b></div>`).join("")}
      <div class="rnote">Bar: where the value sits between the least and most RI-favorable values in the training sample. Right: share of the RI probability it adds.</div></details>` : ""}`;
}

function shipsDrivers(box) {
  const S = D.ships, ct = S.ctaus || [], j = ct.indexOf(SHP.drv), R = S.rows, T = S.taus;
  if (j < 0 || !S.contrib?.length) { box.innerHTML = `<div class="shp-h"><b>What drives the change</b></div><div class="rnote">No predictor breakdown in this run.</div>`; return; }
  const last = T.reduce((m, t, i) => R.v?.[i] != null ? t : m, 0);
  const rows = S.contrib.map(c => ({ n: c.n, v: c.v[j] })).filter(c => c.v != null && Math.abs(c.v) >= .5).sort((a, b) => Math.abs(b.v) - Math.abs(a.v)).slice(0, 9);
  const tot = S.contrib_total?.[j], mx = Math.max(4, ...rows.map(r => Math.abs(r.v)));
  const v0 = R.v?.[0], i = T.indexOf(SHP.drv);
  box.innerHTML = `<div class="shp-h"><b>What drives the change</b><span class="shp-taus">${ct.filter(t => t <= last && [12, 24, 36, 48, 72, 96, 120].includes(t)).map(t => `<button data-t="${t}"${t === SHP.drv ? ' class="on"' : ""}>${t}h</button>`).join("")}</span></div>
    <div class="shp-net">${tot != null ? `<b class="${tot < 0 ? "dn" : "up"}">${tot > 0 ? "+" : ""}${wdel(tot)} ${wl()}</b><span>predicted change by ${SHP.drv} h${R.vnl?.[i] != null ? `, to ${wnd(R.vnl[i])} ${wl()} before land effects` : ""}</span>` : ""}</div>
    ${rows.map(r => { const w = Math.abs(r.v) / mx * 50; return `<div class="shp-bar"><span>${esc(shpName(r.n))}</span><i><u class="${r.v < 0 ? "dn" : "up"}" style="${r.v < 0 ? `right:50%` : `left:50%`};width:${w.toFixed(1)}%"></u></i><b class="${r.v < 0 ? "dn" : "up"}">${r.v > 0 ? "+" : ""}${wdel(r.v)}</b></div>`; }).join("")}
    <div class="rnote">Each predictor's push on the SHIPS forecast, in ${wl()}, relative to the start. Click a time on any chart to switch.</div>`;
  box.querySelectorAll("[data-t]").forEach(b => b.onclick = () => { SHP.drv = +b.dataset.t; shipsDrivers(box); });
}
