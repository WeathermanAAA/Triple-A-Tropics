"""
HTML/CSS/JS template for the per-basin ACE iframe widget.

This module exists to keep the ~500 line template out of generate_ace_plot.py.
The string is consumed by `HTML_TEMPLATE.format(...)` so all literal `{` and `}`
must be doubled (`{{` and `}}`); Python format placeholders are single-braced.

Format placeholders consumed by render_html:
  {basin_full_name} {basin_short_label} {current_year}
  {climo_start} {climo_end} {updated} {live_note} {payload}
"""

HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{basin_full_name} TC ACE · {current_year}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  :root {{
    --bg: #131519; --panel: #1b1e24; --border: #2a2e36;
    --fg: #e8ebef; --muted: #9199a4;
    --accent: #ffb83a; --accent-2: #5dd3ff; --accent-3: #c084fc;
    --rank-line: #4ade80;
    --hot-pink: #ff4dd2;
    --grid-dim: #232730;
  }}
  html, body {{ margin: 0; background: var(--bg); color: var(--fg);
    font-family: "Metropolis", -apple-system, BlinkMacSystemFont, "Segoe UI",
                 Roboto, Helvetica, Arial, sans-serif;
    font-weight: 600;
    -webkit-font-smoothing: antialiased; }}
  .wrap {{ max-width: 1180px; margin: 0 auto; padding: 12px 16px 16px; }}
  .hdr {{ margin: 0 0 8px; }}
  .hdr-title {{ font-size: 17px; font-weight: 800; color: var(--fg);
    letter-spacing: 0.2px; line-height: 1.2; }}
  .hdr-title .basin {{ color: var(--fg); }}
  .hdr-title .ace-val {{ color: var(--accent-2); }}
  .hdr-title .delta-pos {{ color: var(--accent-2); }}
  .hdr-title .delta-neg {{ color: var(--accent); }}
  .hdr-title .rank-val {{ color: var(--accent); }}
  .hdr-title .sep {{ color: var(--muted); font-weight: 500; padding: 0 6px; }}
  .hdr-credit {{ font-size: 11px; color: var(--muted); margin-top: 3px;
    font-weight: 500; }}
  .row-main {{ display: flex; gap: 14px; align-items: stretch; margin-top: 6px; }}
  .chartbox-stack {{ position: relative; flex: 1 1 auto; min-width: 0;
    background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
    padding: 6px 6px 6px; display: flex; flex-direction: column; gap: 0; }}
  .chartbox-stack svg {{ width: 100%; height: auto; display: block;
    touch-action: none; }}
  .rank-wrap {{ flex: 0 0 240px; display: flex; flex-direction: column;
    max-height: 760px; }}
  .rank-title {{ font-size: 12px; color: var(--muted); margin-bottom: 4px;
    font-weight: 600; }}
  .rank-title b {{ color: var(--accent); font-weight: 700; }}
  .rank-scroll {{ overflow-y: auto; border: 1px solid var(--border);
    border-radius: 8px; background: var(--panel);
    scrollbar-color: #2f343c transparent; }}
  .rank-scroll::-webkit-scrollbar {{ width: 8px; }}
  .rank-scroll::-webkit-scrollbar-thumb {{ background: #2f343c;
    border-radius: 4px; }}
  table.rank {{ width: 100%; border-collapse: collapse; font-size: 12px;
    table-layout: fixed; }}
  table.rank thead th {{ position: sticky; top: 0; background: #2a3140;
    color: var(--fg); font-weight: 700; padding: 6px 4px; text-align: center;
    border-right: 1px solid var(--border); line-height: 1.2;
    border-bottom: 1px solid var(--border); }}
  table.rank thead th:last-child {{ border-right: 0; }}
  table.rank td {{ padding: 6px 4px; text-align: center;
    border-bottom: 1px solid var(--border); color: #d0d6df;
    border-left: 3px solid transparent; }}
  table.rank td.rank-col {{ color: var(--muted); font-variant-numeric: tabular-nums; }}
  table.rank td.num {{ font-variant-numeric: tabular-nums; }}
  table.rank tr {{ cursor: pointer; transition: background 0.12s; }}
  table.rank tr:nth-child(odd) td {{ background: rgba(255,255,255,0.015); }}
  table.rank tr:nth-child(even) td {{ background: transparent; }}
  table.rank tr:hover td {{ background: rgba(93,211,255,0.08); color: var(--fg); }}
  table.rank tr.is-current td {{
    background: rgba(255,184,58,0.12);
    color: var(--fg);
    font-weight: 700;
  }}
  table.rank tr.is-current td:first-child {{
    border-left-color: var(--accent);
  }}
  table.rank tr.is-current:hover td {{ background: rgba(255,184,58,0.18); }}
  table.rank tr.is-selected td {{
    background: rgba(93,211,255,0.16);
    color: var(--fg);
    font-weight: 700;
  }}
  table.rank tr.is-selected td:first-child {{
    border-left-color: var(--accent-2);
  }}
  table.rank tr.is-selected:hover td {{ background: rgba(93,211,255,0.22); }}
  table.rank tr.is-disabled {{ cursor: default; opacity: 0.55; }}
  table.rank tr.is-disabled:hover td {{ background: transparent; color: #d0d6df; }}
  .clear-btn {{ margin-left: 8px; font-size: 11px; padding: 2px 8px;
    background: transparent; border: 1px solid var(--border);
    color: var(--muted); border-radius: 999px; cursor: pointer;
    display: none; font-weight: 600; }}
  .clear-btn:hover {{ color: var(--accent-2); border-color: var(--accent-2); }}
  .clear-btn.show {{ display: inline-block; }}
  @media (max-width: 760px) {{
    .row-main {{ flex-direction: column; }}
    .rank-wrap {{ flex: 0 0 auto; max-height: 420px; }}
  }}
  .tooltip {{ position: absolute; pointer-events: none; background: #1f242c;
    border: 1px solid var(--border); border-radius: 6px; padding: 6px 9px;
    font-size: 12px; color: var(--fg);
    box-shadow: 0 4px 14px rgba(0,0,0,0.45);
    transform: translate(-50%, -100%); white-space: nowrap; opacity: 0;
    transition: opacity 0.12s; font-weight: 600; }}
  .tooltip .row {{ display: flex; align-items: center; gap: 6px; }}
  .tooltip .dot {{ width: 8px; height: 8px; border-radius: 50%;
    display: inline-block; }}
  .tooltip .date {{ font-weight: 700; margin-bottom: 3px; color: var(--fg); }}
  footer {{ font-size: 11px; color: var(--muted); margin-top: 8px;
    font-weight: 500; }}
  footer a {{ color: var(--muted); }}
</style>
</head>
<body>
<div class="wrap">
  <header class="hdr">
    <div class="hdr-title" id="headerTitle">{basin_full_name} · Accumulated Cyclone Energy</div>
    <div class="hdr-credit" id="headerCredit">@WeathermanAAA_ · Triple-A-Tropics · {updated}</div>
  </header>
  <div class="row-main">
    <div class="chartbox-stack" id="chartbox">
      <svg id="chartAce" viewBox="0 0 1000 380" preserveAspectRatio="xMidYMid meet"></svg>
      <svg id="chartRank" viewBox="0 0 1000 90" preserveAspectRatio="xMidYMid meet"></svg>
      <svg id="chartDaily" viewBox="0 0 1000 90" preserveAspectRatio="xMidYMid meet"></svg>
      <svg id="chartGantt" viewBox="0 0 1000 100" preserveAspectRatio="xMidYMid meet"></svg>
      <svg id="chartLegend" viewBox="0 0 1000 36" preserveAspectRatio="xMidYMid meet"></svg>
      <div class="tooltip" id="tip"></div>
    </div>
    <div class="rank-wrap">
      <div class="rank-title">
        <span id="rankIntro">Click any year for its profile</span>
        <button type="button" class="clear-btn" id="clearSelBtn"
                title="Clear selected year">clear ×</button>
      </div>
      <div class="rank-scroll" id="rankScroll">
        <table class="rank">
          <thead>
            <tr>
              <th>#</th><th>Year</th><th>ACE<br>To Date</th><th>Total<br>ACE</th>
            </tr>
          </thead>
          <tbody id="rankBody"></tbody>
        </table>
      </div>
    </div>
  </div>
  <footer>
    Source: IBTrACS v04r01 (NOAA NCEI){live_note}.
    ACE = Σ wind²/10⁴ at 6-hourly resolution for tropical-phase points
    with 1-min sustained winds ≥ 34 kt. Climatology bands span
    {climo_start}–{climo_end}; rankings include all seasons in the dataset.
  </footer>
</div>
<script>
const DATA = {payload};
const BASIN_SHORT = "{basin_short_label}";
const CURRENT_YEAR = parseInt(DATA.current.label, 10);
const NS = "http://www.w3.org/2000/svg";

// Storm SSHWS palette: (ceiling_kt, label, fill).
const SSHWS = [
  [33,  "TD", "#fff5cc"],
  [63,  "TS", "#4ade80"],
  [82,  "C1", "#5dd3ff"],
  [95,  "C2", "#ffb83a"],
  [112, "C3", "#ec4899"],
  [136, "C4", "#ef4444"],
  [9999,"C5", "#c084fc"],
];
function sshwsColor(kt) {{
  if (kt == null || isNaN(kt)) return ["?", "#4ade80"];
  for (const [c, lab, col] of SSHWS) {{
    if (kt <= c) return [lab, col];
  }}
  return ["C5", "#c084fc"];
}}

// Shared X scale (DOY 1..366 → SVG x in viewBox units).
const W = 1000, M_L = 60, M_R = 18;
const PW = W - M_L - M_R;
const xs = (doy) => M_L + (doy - 1) / 365 * PW;

const MONTH_STARTS = [1,32,60,91,121,152,182,213,244,274,305,335];
const MONTH_LABELS = ["Jan","Feb","Mar","Apr","May","Jun","Jul",
                      "Aug","Sep","Oct","Nov","Dec"];

function el(tag, attrs, parent) {{
  const e = document.createElementNS(NS, tag);
  if (attrs) for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}}
function clear(node) {{ while (node.firstChild) node.removeChild(node.firstChild); }}
function niceStep(x) {{
  if (!isFinite(x) || x <= 0) return 1;
  const pow = Math.pow(10, Math.floor(Math.log10(x)));
  const n = x / pow;
  let step;
  if (n < 1.5) step = 1; else if (n < 3) step = 2;
  else if (n < 7) step = 5; else step = 10;
  return step * pow;
}}
function doyToDate(doy, year) {{
  const d = new Date(Date.UTC(year, 0, 1));
  d.setUTCDate(doy);
  return d.toLocaleDateString(undefined,
    {{ month: "short", day: "numeric", timeZone: "UTC" }});
}}
function isoToDoy(iso) {{
  if (!iso) return null;
  const t = new Date(iso);
  if (isNaN(t.getTime())) return null;
  const start = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
  const ms = t.getTime() - start.getTime();
  return Math.floor(ms / 86400000) + 1 + ((t.getUTCHours() + t.getUTCMinutes()/60) / 24);
}}

// ===== Panel 1: ACE percentile chart =====
const aceSvg = document.getElementById("chartAce");
const tip    = document.getElementById("tip");
const box    = document.getElementById("chartbox");

const ACE_M = {{ t: 12, b: 28 }};
const ACE_PH = 380 - ACE_M.t - ACE_M.b;

const yMaxCandidates = [DATA.climo.max[DATA.climo.max.length - 1] || 0];
for (const y in DATA.all_years) {{
  const arr = DATA.all_years[y];
  if (arr && arr.length) yMaxCandidates.push(arr[arr.length - 1]);
}}
const aceYMax = (Math.max.apply(null, yMaxCandidates) || 1) * 1.05;
const aceY = (v) => ACE_M.t + ACE_PH - (v / aceYMax) * ACE_PH;

(function initAcePanel() {{
  // Y gridlines + labels
  const ySteps = 5;
  const yStep = niceStep(aceYMax / ySteps);
  for (let v = 0; v <= aceYMax; v += yStep) {{
    el("line", {{ x1: M_L, x2: M_L + PW, y1: aceY(v), y2: aceY(v),
      stroke: "var(--grid-dim)", "stroke-width": 1 }}, aceSvg);
    el("text", {{ x: M_L - 8, y: aceY(v) + 4, "text-anchor": "end",
      "font-size": 11, fill: "var(--muted)" }}, aceSvg).textContent =
      Math.round(v);
  }}
  el("text", {{ x: 14, y: ACE_M.t + ACE_PH / 2, "text-anchor": "middle",
    "font-size": 12, fill: "var(--muted)",
    transform: `rotate(-90 14 ${{ACE_M.t + ACE_PH / 2}})` }}, aceSvg)
    .textContent = "Cumulative ACE (×10⁴ kt²)";

  // Month dividers + labels
  MONTH_STARTS.forEach((d, i) => {{
    el("line", {{ x1: xs(d), x2: xs(d), y1: ACE_M.t, y2: ACE_M.t + ACE_PH,
      stroke: "var(--border)", "stroke-width": 1, "stroke-opacity": 0.4 }},
      aceSvg);
    el("text", {{ x: xs(d + 15), y: ACE_M.t + ACE_PH + 18,
      "text-anchor": "middle", "font-size": 11,
      fill: "var(--muted)" }}, aceSvg).textContent = MONTH_LABELS[i];
  }});
  el("line", {{ x1: M_L, x2: M_L + PW,
    y1: ACE_M.t + ACE_PH, y2: ACE_M.t + ACE_PH,
    stroke: "var(--border)", "stroke-width": 1 }}, aceSvg);

  // Climo bands, light → dark
  function band(upper, lower, fill) {{
    let d = "";
    for (let i = 0; i < DATA.doy.length; i++)
      d += (i === 0 ? "M" : "L") + xs(DATA.doy[i]) + "," + aceY(upper[i]) + " ";
    for (let i = DATA.doy.length - 1; i >= 0; i--)
      d += "L" + xs(DATA.doy[i]) + "," + aceY(lower[i]) + " ";
    d += "Z";
    el("path", {{ d, fill, stroke: "none" }}, aceSvg);
  }}
  band(DATA.climo.max, DATA.climo.min, "rgba(70,140,200,0.10)");
  band(DATA.climo.p90, DATA.climo.p10, "rgba(70,180,220,0.18)");
  band(DATA.climo.p75, DATA.climo.p25, "rgba(80,210,240,0.28)");

  function linePath(xa, ya, stroke, width, dash, opacity) {{
    let d = "";
    for (let i = 0; i < xa.length; i++)
      d += (i === 0 ? "M" : "L") + xs(xa[i]) + "," + aceY(ya[i]) + " ";
    const a = {{ d, fill: "none", stroke, "stroke-width": width,
      "stroke-linejoin": "round", "stroke-linecap": "round" }};
    if (dash)        a["stroke-dasharray"] = dash;
    if (opacity != null) a["stroke-opacity"] = opacity;
    el("path", a, aceSvg);
  }}
  // Faint band edges
  linePath(DATA.doy, DATA.climo.min, "#2e6a96", 0.9, null, 0.55);
  linePath(DATA.doy, DATA.climo.max, "#2e6a96", 0.9, null, 0.55);
  linePath(DATA.doy, DATA.climo.p10, "#3aa2cf", 1.0, null, 0.70);
  linePath(DATA.doy, DATA.climo.p90, "#3aa2cf", 1.0, null, 0.70);
  linePath(DATA.doy, DATA.climo.p25, "#5dd3ff", 1.4, null, 0.85);
  linePath(DATA.doy, DATA.climo.p75, "#5dd3ff", 1.4, null, 0.85);

  // Climo mean (dashed cyan), prior-year (solid violet)
  linePath(DATA.doy, DATA.climo.mean, "var(--accent-2)", 2, "6 4");
  if (DATA.prior_year && DATA.prior_year.values)
    linePath(DATA.doy, DATA.prior_year.values, "var(--accent-3)", 2);

  // Current-year amber line + today marker (always shown for current
  // calendar year; the pink overlay handles other selected years)
  linePath(DATA.current.doy, DATA.current.values, "var(--accent)", 3);
  if (DATA.today_doy) {{
    el("circle", {{ cx: xs(DATA.today_doy), cy: aceY(DATA.current.latest_value),
      r: 5, fill: "var(--accent)", stroke: "var(--bg)", "stroke-width": 2 }},
      aceSvg);
  }}

  // Selected-year overlay group (populated by setOverlay)
  el("g", {{ id: "selGroup" }}, aceSvg);

  // Watermark
  el("text", {{ x: M_L + PW - 10, y: ACE_M.t + 28,
    "text-anchor": "end", "font-size": 26, "font-weight": 700,
    fill: "var(--fg)", "fill-opacity": 0.18, "letter-spacing": 0.5 }}, aceSvg)
    .textContent = "@WeathermanAAA_";
}})();

// Hover tooltip on panel 1
const cross = el("line", {{ x1: 0, x2: 0, y1: ACE_M.t, y2: ACE_M.t + ACE_PH,
  stroke: "var(--muted)", "stroke-width": 1, "stroke-dasharray": "3 3",
  opacity: 0 }}, aceSvg);
const dotCurrent = el("circle", {{ r: 4, fill: "var(--accent)",
  stroke: "var(--bg)", "stroke-width": 1.5, opacity: 0 }}, aceSvg);
const dotPrior = el("circle", {{ r: 3.5, fill: "var(--accent-3)",
  stroke: "var(--bg)", "stroke-width": 1.5, opacity: 0 }}, aceSvg);
const dotMean = el("circle", {{ r: 3.5, fill: "var(--accent-2)",
  stroke: "var(--bg)", "stroke-width": 1.5, opacity: 0 }}, aceSvg);
const dotSel = el("circle", {{ r: 4, fill: "var(--hot-pink)",
  stroke: "var(--bg)", "stroke-width": 1.5, opacity: 0 }}, aceSvg);

let selectedYear = CURRENT_YEAR;

function setAceOverlay(year) {{
  // Manage the pink overlay on panel 1. year=null hides it.
  const existing = document.getElementById("selGroup");
  clear(existing);
  dotSel.setAttribute("opacity", 0);
  if (year == null) return;
  const vals = DATA.all_years && DATA.all_years[year];
  if (!vals) return;
  let d = "";
  for (let i = 0; i < vals.length; i++)
    d += (i === 0 ? "M" : "L") + xs(DATA.doy[i]) + "," + aceY(vals[i]) + " ";
  const p = el("path", {{
    d, fill: "none", stroke: "var(--hot-pink)", "stroke-width": 2.5,
    "stroke-linejoin": "round", "stroke-linecap": "round"
  }}, existing);
  const lastIdx = vals.length - 1;
  el("circle", {{ cx: xs(DATA.doy[lastIdx]), cy: aceY(vals[lastIdx]),
    r: 3.5, fill: "var(--hot-pink)", stroke: "var(--bg)",
    "stroke-width": 1.5 }}, existing);
  el("text", {{ x: xs(DATA.doy[lastIdx]) - 6, y: aceY(vals[lastIdx]) - 6,
    "text-anchor": "end", "font-size": 12, "font-weight": 700,
    fill: "var(--hot-pink)" }}, existing).textContent = year;
}}

function rowTip(c, name, val) {{
  return '<div class="row"><span class="dot" style="background:' + c +
         '"></span><span>' + name + ':</span><b>' + val + '</b></div>';
}}
function onMove(evt) {{
  const pt = aceSvg.createSVGPoint();
  const src = evt.touches ? evt.touches[0] : evt;
  pt.x = src.clientX; pt.y = src.clientY;
  const p = pt.matrixTransform(aceSvg.getScreenCTM().inverse());
  if (p.x < M_L || p.x > M_L + PW) {{ onLeave(); return; }}
  const doy = Math.max(1, Math.min(366, Math.round(1 + (p.x - M_L) / PW * 365)));
  cross.setAttribute("x1", xs(doy));
  cross.setAttribute("x2", xs(doy));
  cross.setAttribute("opacity", 1);

  const idx = doy - 1;
  const curIdx = DATA.current.doy.indexOf(doy);
  const curVal = curIdx >= 0 ? DATA.current.values[curIdx] :
    (doy > DATA.current.doy[DATA.current.doy.length - 1]
      ? null : DATA.current.values[DATA.current.values.length - 1]);
  const meanVal  = DATA.climo.mean[idx];
  const priorVal = DATA.prior_year.values ? DATA.prior_year.values[idx] : null;
  const selVal   = (selectedYear !== CURRENT_YEAR && DATA.all_years[selectedYear])
                   ? DATA.all_years[selectedYear][idx] : null;

  if (curVal != null) {{
    dotCurrent.setAttribute("cx", xs(doy));
    dotCurrent.setAttribute("cy", aceY(curVal));
    dotCurrent.setAttribute("opacity", 1);
  }} else dotCurrent.setAttribute("opacity", 0);
  dotMean.setAttribute("cx", xs(doy));
  dotMean.setAttribute("cy", aceY(meanVal));
  dotMean.setAttribute("opacity", 1);
  if (priorVal != null) {{
    dotPrior.setAttribute("cx", xs(doy));
    dotPrior.setAttribute("cy", aceY(priorVal));
    dotPrior.setAttribute("opacity", 1);
  }} else dotPrior.setAttribute("opacity", 0);
  if (selVal != null) {{
    dotSel.setAttribute("cx", xs(doy));
    dotSel.setAttribute("cy", aceY(selVal));
    dotSel.setAttribute("opacity", 1);
  }} else dotSel.setAttribute("opacity", 0);

  const rect = box.getBoundingClientRect();
  const svgRect = aceSvg.getBoundingClientRect();
  const scale = svgRect.width / W;
  const tipX = (xs(doy) * scale) + (svgRect.left - rect.left);
  const tipY = (ACE_M.t * scale) + (svgRect.top - rect.top) + 6;
  tip.style.left = tipX + "px";
  tip.style.top  = tipY + "px";
  tip.style.opacity = 1;
  const fmt = v => (v == null ? "—" : v.toFixed(1));
  const label = doyToDate(doy, parseInt(DATA.current.label, 10));
  tip.innerHTML =
    '<div class="date">' + label + ' (DOY ' + doy + ')</div>' +
    (curVal != null ? rowTip("var(--accent)", DATA.current.label, fmt(curVal)) : '') +
    (selVal != null ? rowTip("var(--hot-pink)", String(selectedYear), fmt(selVal)) : '') +
    (priorVal != null ? rowTip("var(--accent-3)", DATA.prior_year.label, fmt(priorVal)) : '') +
    rowTip("var(--accent-2)", "Climo mean", fmt(meanVal)) +
    rowTip("transparent", "10–90%",
      fmt(DATA.climo.p10[idx]) + ' – ' + fmt(DATA.climo.p90[idx]));
}}
function onLeave() {{
  cross.setAttribute("opacity", 0);
  dotCurrent.setAttribute("opacity", 0);
  dotPrior.setAttribute("opacity", 0);
  dotMean.setAttribute("opacity", 0);
  dotSel.setAttribute("opacity", 0);
  tip.style.opacity = 0;
}}
aceSvg.addEventListener("mousemove", onMove);
aceSvg.addEventListener("mouseleave", onLeave);
aceSvg.addEventListener("touchmove", onMove, {{ passive: true }});
aceSvg.addEventListener("touchend", onLeave);

// ===== Panel 2: Rank trajectory =====
const rankSvg = document.getElementById("chartRank");
const RANK_M = {{ t: 12, b: 22 }};
const RANK_PH = 90 - RANK_M.t - RANK_M.b;

function renderRankPanel(year) {{
  clear(rankSvg);
  const arr = DATA.all_years && DATA.all_years[year];
  // Y axis bounds
  const totalSeasons = DATA.total_seasons || 1;
  // Compute rank at each DOY
  const ranks = new Array(366).fill(null);
  if (arr) {{
    for (let i = 0; i < 366; i++) {{
      const target = arr[i];
      let higher = 0;
      for (const y in DATA.all_years) {{
        const v = DATA.all_years[y][i];
        if (v > target) higher++;
      }}
      ranks[i] = higher + 1;
    }}
  }}
  // Mask future DOY for the in-progress current year
  let drawDoyMax = 366;
  if (year === CURRENT_YEAR && DATA.today_doy) drawDoyMax = DATA.today_doy;
  // Y scale (inverted: rank 1 at top)
  const yMaxRank = totalSeasons;
  const rY = (rank) => RANK_M.t + ((rank - 1) / Math.max(1, yMaxRank - 1)) * RANK_PH;
  // Gridlines
  for (const r of [1, Math.ceil(totalSeasons / 2), totalSeasons]) {{
    el("line", {{ x1: M_L, x2: M_L + PW, y1: rY(r), y2: rY(r),
      stroke: "var(--grid-dim)", "stroke-width": 1 }}, rankSvg);
    el("text", {{ x: M_L - 8, y: rY(r) + 4, "text-anchor": "end",
      "font-size": 10, fill: "var(--muted)" }}, rankSvg).textContent = r;
  }}
  el("text", {{ x: 14, y: RANK_M.t + RANK_PH / 2, "text-anchor": "middle",
    "font-size": 11, fill: "var(--muted)",
    transform: `rotate(-90 14 ${{RANK_M.t + RANK_PH / 2}})` }}, rankSvg)
    .textContent = "Rank";
  // Month dividers (no labels — keep panel slim)
  MONTH_STARTS.forEach((d) => {{
    el("line", {{ x1: xs(d), x2: xs(d), y1: RANK_M.t, y2: RANK_M.t + RANK_PH,
      stroke: "var(--border)", "stroke-width": 0.8,
      "stroke-opacity": 0.35, "stroke-dasharray": "3 3" }}, rankSvg);
  }});
  el("line", {{ x1: M_L, x2: M_L + PW, y1: RANK_M.t + RANK_PH,
    y2: RANK_M.t + RANK_PH, stroke: "var(--border)", "stroke-width": 1 }}, rankSvg);

  // Rank line
  let bestR = null, worstR = null;
  if (arr) {{
    let d = "", started = false;
    for (let i = 0; i < drawDoyMax && i < ranks.length; i++) {{
      const r = ranks[i];
      if (r == null || !isFinite(r)) continue;
      if (i === 0 && bestR == null) {{ bestR = r; worstR = r; }}
      else {{ if (r < bestR) bestR = r; if (r > worstR) worstR = r; }}
      d += (started ? "L" : "M") + xs(DATA.doy[i]) + "," + rY(r) + " ";
      started = true;
    }}
    if (d) {{
      el("path", {{ d, fill: "none", stroke: "var(--rank-line)",
        "stroke-width": 2.5, "stroke-linejoin": "round",
        "stroke-linecap": "round" }}, rankSvg);
    }}
  }}
  // Top-left summary
  const summary = (arr && bestR != null)
    ? `Rank: ${{bestR}}–${{worstR}} of ${{totalSeasons}}`
    : "Rank trajectory unavailable";
  el("text", {{ x: M_L + 6, y: RANK_M.t + 12, "font-size": 11,
    "font-weight": 700, fill: "var(--fg)" }}, rankSvg).textContent = summary;
}}

// ===== Panel 3: Daily ACE bars =====
const dailySvg = document.getElementById("chartDaily");
const DAILY_M = {{ t: 12, b: 22 }};
const DAILY_PH = 90 - DAILY_M.t - DAILY_M.b;

function renderDailyPanel(year) {{
  clear(dailySvg);
  const arr = DATA.all_years && DATA.all_years[year];
  const daily = new Array(366).fill(0);
  if (arr) {{
    for (let i = 0; i < 366; i++) {{
      daily[i] = i === 0 ? arr[i] : Math.max(0, arr[i] - arr[i - 1]);
    }}
  }}
  let drawDoyMax = 366;
  if (year === CURRENT_YEAR && DATA.today_doy) drawDoyMax = DATA.today_doy;
  // Find max
  let peak = 0, peakDoy = -1;
  for (let i = 0; i < drawDoyMax; i++) {{
    if (daily[i] > peak) {{ peak = daily[i]; peakDoy = i + 1; }}
  }}
  const yMax = Math.max(peak * 1.15, 0.1);
  const dY = (v) => DAILY_M.t + DAILY_PH - (v / yMax) * DAILY_PH;
  // Gridlines (top + bottom only, slim)
  el("line", {{ x1: M_L, x2: M_L + PW, y1: DAILY_M.t, y2: DAILY_M.t,
    stroke: "var(--grid-dim)", "stroke-width": 1 }}, dailySvg);
  el("line", {{ x1: M_L, x2: M_L + PW, y1: DAILY_M.t + DAILY_PH,
    y2: DAILY_M.t + DAILY_PH, stroke: "var(--border)",
    "stroke-width": 1 }}, dailySvg);
  el("text", {{ x: M_L - 8, y: dY(0) + 4, "text-anchor": "end",
    "font-size": 10, fill: "var(--muted)" }}, dailySvg).textContent = "0";
  el("text", {{ x: M_L - 8, y: dY(yMax) + 4, "text-anchor": "end",
    "font-size": 10, fill: "var(--muted)" }}, dailySvg)
    .textContent = peak.toFixed(2);
  el("text", {{ x: 14, y: DAILY_M.t + DAILY_PH / 2, "text-anchor": "middle",
    "font-size": 11, fill: "var(--muted)",
    transform: `rotate(-90 14 ${{DAILY_M.t + DAILY_PH / 2}})` }}, dailySvg)
    .textContent = "Daily ACE";
  // Month dividers
  MONTH_STARTS.forEach((d) => {{
    el("line", {{ x1: xs(d), x2: xs(d), y1: DAILY_M.t, y2: DAILY_M.t + DAILY_PH,
      stroke: "var(--border)", "stroke-width": 0.8,
      "stroke-opacity": 0.35, "stroke-dasharray": "3 3" }}, dailySvg);
  }});
  // Bars
  const barW = PW / 365;
  for (let i = 0; i < drawDoyMax; i++) {{
    const v = daily[i];
    if (v <= 0) continue;
    const x = xs(i + 1) - barW / 2;
    const y = dY(v);
    const color = (i + 1 === peakDoy) ? "var(--accent)" : "var(--accent-2)";
    el("rect", {{ x, y, width: barW, height: dY(0) - y,
      fill: color }}, dailySvg);
  }}
  // Top-left max-day legend
  if (peakDoy > 0) {{
    el("text", {{ x: M_L + 6, y: DAILY_M.t + 12, "font-size": 11,
      "font-weight": 700, fill: "var(--fg)" }}, dailySvg).textContent =
      `Max daily ACE: ${{peak.toFixed(4)}}  on ${{doyToDate(peakDoy, year)}}`;
  }} else {{
    el("text", {{ x: M_L + 6, y: DAILY_M.t + 12, "font-size": 11,
      "font-weight": 700, fill: "var(--muted)" }}, dailySvg).textContent =
      "No daily ACE yet for this season";
  }}
}}

// ===== Panel 4: Storm Gantt =====
const ganttSvg = document.getElementById("chartGantt");
const GANTT_M = {{ t: 8, b: 22 }};
const ROW_H = 14;            // px per row in viewBox units
const LABEL_PAD_DAYS = 6;    // greedy bin-pack spacing

function renderGanttPanel(year) {{
  clear(ganttSvg);
  const storms = (DATA.storms_by_year && DATA.storms_by_year[year]) || [];
  // Greedy row-pack
  const sorted = storms.filter(s => s.formation && s.dissipation)
    .map(s => ({{
      name: s.name || "UNNAMED",
      d0: isoToDoy(s.formation),
      d1: isoToDoy(s.dissipation),
      pk: s.peak_wind_kt,
    }}))
    .filter(s => s.d0 != null && s.d1 != null && s.d1 >= s.d0)
    .sort((a, b) => a.d0 - b.d0);
  const rows = [];
  for (const s of sorted) {{
    let placed = false;
    for (const row of rows) {{
      const last = row[row.length - 1];
      if (s.d0 > last.d1 + LABEL_PAD_DAYS) {{
        row.push(s); placed = true; break;
      }}
    }}
    if (!placed) rows.push([s]);
  }}
  const nRows = Math.max(1, rows.length);
  // Resize SVG height
  const totalH = GANTT_M.t + nRows * ROW_H + GANTT_M.b;
  ganttSvg.setAttribute("viewBox", `0 0 ${{W}} ${{totalH}}`);
  const PH = nRows * ROW_H;
  // Month dividers
  MONTH_STARTS.forEach((d, i) => {{
    el("line", {{ x1: xs(d), x2: xs(d), y1: GANTT_M.t,
      y2: GANTT_M.t + PH, stroke: "var(--border)", "stroke-width": 0.8,
      "stroke-opacity": 0.5, "stroke-dasharray": "3 3" }}, ganttSvg);
    el("text", {{ x: xs(d + 15), y: GANTT_M.t + PH + 16,
      "text-anchor": "middle", "font-size": 11,
      fill: "var(--muted)" }}, ganttSvg).textContent = MONTH_LABELS[i];
  }});
  el("line", {{ x1: M_L, x2: M_L + PW,
    y1: GANTT_M.t + PH, y2: GANTT_M.t + PH,
    stroke: "var(--border)", "stroke-width": 1 }}, ganttSvg);

  if (sorted.length === 0) {{
    el("text", {{ x: W / 2, y: totalH / 2,
      "text-anchor": "middle", "font-size": 12, "font-weight": 700,
      fill: "var(--muted)" }}, ganttSvg).textContent =
      "No named storms this season";
    return;
  }}
  // Render pills + labels
  rows.forEach((row, r) => {{
    const yCenter = GANTT_M.t + r * ROW_H + ROW_H / 2;
    for (const s of row) {{
      const x0 = xs(s.d0);
      const x1 = xs(s.d1);
      const w = Math.max(2, x1 - x0);
      const [cat, color] = sshwsColor(s.pk);
      el("rect", {{ x: x0, y: yCenter - 4.5, width: w, height: 9,
        rx: 4, ry: 4, fill: color }}, ganttSvg);
      el("text", {{ x: x1 + 3, y: yCenter + 3.2, "font-size": 9,
        "font-weight": 700, fill: "var(--fg)" }}, ganttSvg)
        .textContent = `${{s.name}} (${{cat}})`;
    }}
  }});
}}

// ===== Panel 5: SSHWS legend strip (renders once) =====
const legSvg = document.getElementById("chartLegend");
(function initLegend() {{
  const items = [
    ["TD", "≤33 kt"], ["TS", "34–63"], ["C1", "64–82"],
    ["C2", "83–95"],  ["C3", "96–112"],["C4", "113–136"],
    ["C5", "≥137"],
  ];
  const swatchColors = SSHWS.map(([c,l,col]) => col);
  const totalW = 920;
  const left  = (W - totalW) / 2;
  const slotW = totalW / items.length;
  const sw = 16, sh = 9;
  items.forEach(([cat, krange], i) => {{
    const x0 = left + i * slotW;
    el("rect", {{ x: x0, y: 14, width: sw, height: sh,
      rx: 2, ry: 2, fill: swatchColors[i],
      stroke: "var(--border)", "stroke-width": 1 }}, legSvg);
    el("text", {{ x: x0 + sw + 5, y: 22, "font-size": 11,
      "font-weight": 700, fill: "var(--fg)" }}, legSvg)
      .textContent = `${{cat}} (${{krange}})`;
  }});
}})();

// ===== Header =====
function fmtNum(n, dp) {{
  if (n == null || isNaN(n)) return "—";
  return n.toFixed(dp);
}}
function renderHeader(year) {{
  const titleEl = document.getElementById("headerTitle");
  let totalAce = null, deltaAce = null, rankShow = null;
  let suffix = "";
  if (year === CURRENT_YEAR) {{
    totalAce = DATA.current.latest_value;
    if (DATA.today_doy) {{
      const idx = DATA.today_doy - 1;
      if (idx >= 0 && idx < DATA.climo.mean.length) {{
        deltaAce = totalAce - DATA.climo.mean[idx];
      }}
    }}
    rankShow = DATA.current_rank;
    suffix = " · YTD";
  }} else {{
    const arr = DATA.all_years && DATA.all_years[year];
    if (arr) totalAce = arr[arr.length - 1];
    if (totalAce != null) {{
      deltaAce = totalAce - DATA.climo.mean[DATA.climo.mean.length - 1];
    }}
    // Rank by total ACE among all years
    if (totalAce != null) {{
      let higher = 0;
      for (const y in DATA.all_years) {{
        const t = DATA.all_years[y][DATA.all_years[y].length - 1];
        if (parseInt(y, 10) !== year && t > totalAce) higher++;
      }}
      rankShow = higher + 1;
    }}
  }}
  const sign = deltaAce == null ? "" :
               (deltaAce >= 0 ? "+" : "−");
  const deltaCls = deltaAce == null ? "delta-pos" :
                   (deltaAce >= 0 ? "delta-pos" : "delta-neg");
  titleEl.innerHTML =
    '<span class="basin">' + year + ' ' + BASIN_SHORT + '</span>' +
    '<span class="sep">·</span>' +
    'ACE: <span class="ace-val">' + fmtNum(totalAce, 1) + '</span>' +
    (deltaAce != null
      ? '<span class="' + deltaCls + '"> (' + sign + fmtNum(Math.abs(deltaAce), 1) + ' vs avg' + suffix + ')</span>'
      : '') +
    '<span class="sep">·</span>' +
    'Rank: <span class="rank-val">' + (rankShow != null ? rankShow : '—') +
    '/' + (DATA.total_seasons || '—') + '</span>';
}}

// ===== Top-level setSelectedYear: update all 4 panels + header =====
const lblIntro = document.getElementById("rankIntro");
const clearBtn = document.getElementById("clearSelBtn");
function setSelectedYear(year) {{
  // Validate: must be a year present in all_years (gantt may be missing for some)
  const yearN = (typeof year === "string") ? parseInt(year, 10) : year;
  const valid = (yearN != null && DATA.all_years && DATA.all_years[yearN]);
  selectedYear = valid ? yearN : CURRENT_YEAR;

  // Panel 1 overlay: pink only when selectedYear is NOT current
  setAceOverlay(selectedYear !== CURRENT_YEAR ? selectedYear : null);
  // Panels 2/3/4 + header: always reflect selectedYear
  renderRankPanel(selectedYear);
  renderDailyPanel(selectedYear);
  renderGanttPanel(selectedYear);
  renderHeader(selectedYear);

  // Rank-table active row
  document.querySelectorAll("#rankBody tr.is-selected").forEach(tr =>
    tr.classList.remove("is-selected"));
  if (selectedYear !== CURRENT_YEAR) {{
    const tr = document.querySelector(
      `#rankBody tr[data-year="${{selectedYear}}"]`);
    if (tr) tr.classList.add("is-selected");
    lblIntro.textContent = "Viewing " + selectedYear;
    clearBtn.classList.add("show");
  }} else {{
    lblIntro.textContent = "Click any year for its profile";
    clearBtn.classList.remove("show");
  }}
}}

// Public hook (kept for backwards compat / debugging)
window.WPAceChart = {{
  setSelectedYear,
  getSelectedYear: () => selectedYear,
}};

// ===== Rank table =====
(function initRankTable() {{
  const body = document.getElementById("rankBody");
  const scroll = document.getElementById("rankScroll");
  const rows = (DATA.rankings || []).slice().sort((a, b) =>
    (b.total - a.total) || (a.year - b.year));
  let currentRow = null;
  rows.forEach((r, i) => {{
    const tr = document.createElement("tr");
    tr.dataset.year = r.year;
    const hasGantt = !!(DATA.storms_by_year && DATA.storms_by_year[r.year]);
    const hasCum = !!(DATA.all_years && DATA.all_years[r.year]);
    // The Gantt panel is the only consumer of storms_by_year, but it's
    // also the most differentiated piece of the per-year profile — clicking
    // a year with no storm data lands on a half-empty widget. Per spec we
    // disable the click outright for those rows (pre-1970 historical years).
    const clickable = hasCum && (r.year === CURRENT_YEAR || hasGantt);
    if (!clickable) tr.classList.add("is-disabled");
    if (r.year === CURRENT_YEAR) tr.classList.add("is-current");
    tr.innerHTML =
      '<td class="rank-col">' + (i + 1) + '</td>' +
      '<td>' + r.year + '</td>' +
      '<td class="num">' + r.ytd.toFixed(2) + '</td>' +
      '<td class="num">' + r.total.toFixed(2) + '</td>';
    if (clickable) {{
      tr.addEventListener("click", () => setSelectedYear(r.year));
    }}
    body.appendChild(tr);
    if (r.year === CURRENT_YEAR) currentRow = tr;
  }});
  clearBtn.addEventListener("click", () => setSelectedYear(CURRENT_YEAR));
  if (currentRow) {{
    requestAnimationFrame(() => {{
      const topOffset = currentRow.offsetTop - scroll.clientHeight / 2 +
        currentRow.offsetHeight / 2;
      scroll.scrollTop = Math.max(0, topOffset);
    }});
  }}
}})();

// Initial render — current year on load
setSelectedYear(CURRENT_YEAR);
</script>
</body>
</html>
"""
