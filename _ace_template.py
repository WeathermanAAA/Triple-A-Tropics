"""
HTML/CSS/JS template for the per-basin ACE iframe widget.

This module exists to keep the ~1000 line template out of generate_ace_plot.py.
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
  .wrap {{ max-width: 1400px; margin: 0 auto; padding: 14px 18px 18px; }}
  .hdr {{ margin: 0 0 10px; }}
  .hdr-title {{ font-size: 26px; font-weight: 700; color: var(--fg);
    letter-spacing: 0.2px; line-height: 1.2; }}
  .hdr-title .basin {{ color: var(--fg); }}
  .hdr-title .ace-val {{ color: var(--accent-2); }}
  .hdr-title .delta-pos {{ color: var(--accent-2); }}
  .hdr-title .delta-neg {{ color: var(--accent); }}
  .hdr-title .rank-val {{ color: var(--accent); }}
  .hdr-title .sep {{ color: var(--muted); font-weight: 500; padding: 0 8px; }}
  .hdr-credit {{ font-size: 13px; color: var(--muted); margin-top: 4px;
    font-weight: 500; }}
  .row-main {{ display: flex; gap: 16px; align-items: stretch; margin-top: 10px; }}
  .chartbox-stack {{ position: relative; flex: 1 1 auto; min-width: 0;
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px 14px 12px; display: flex; flex-direction: column; gap: 24px; }}
  .panel {{ display: flex; flex-direction: column; gap: 6px; }}
  .panel-title {{ font-size: 16px; font-weight: 700; color: var(--fg);
    letter-spacing: 0.2px; padding-left: 4px; }}
  .panel-title .panel-sub {{ font-size: 13px; font-weight: 500;
    color: var(--muted); padding-left: 8px; }}
  .chartbox-stack svg {{ width: 100%; height: auto; display: block;
    touch-action: none; }}
  .panel-gantt-scroll {{ max-height: 80vh; overflow-y: auto;
    scrollbar-color: #2f343c transparent; }}
  .panel-gantt-scroll::-webkit-scrollbar {{ width: 8px; }}
  .panel-gantt-scroll::-webkit-scrollbar-thumb {{ background: #2f343c;
    border-radius: 4px; }}
  .rank-wrap {{ flex: 0 0 280px; display: flex; flex-direction: column;
    gap: 8px; max-height: 1200px; }}
  .rank-title {{ font-size: 13px; color: var(--muted); font-weight: 600;
    display: flex; align-items: center; }}
  .rank-title b {{ color: var(--accent); font-weight: 700; }}
  .search-wrap {{ position: relative; }}
  .search-input {{ width: 100%; box-sizing: border-box;
    background: #15181d; color: var(--fg);
    border: 1px solid var(--border); border-radius: 6px;
    padding: 7px 30px 7px 10px;
    font: inherit; font-size: 13px; font-weight: 600;
    outline: none;
    transition: border-color 180ms ease;
  }}
  .search-input::placeholder {{ color: var(--muted); font-weight: 500; }}
  .search-input:focus {{ border-color: var(--accent-2); }}
  .search-clear {{ position: absolute; right: 6px; top: 50%;
    transform: translateY(-50%);
    background: transparent; border: 0; color: var(--muted);
    font-size: 16px; line-height: 1; cursor: pointer; padding: 2px 6px;
    border-radius: 4px; display: none; }}
  .search-clear:hover {{ color: var(--fg); background: #2a3140; }}
  .search-clear.show {{ display: inline-block; }}
  .search-count {{ font-size: 11px; color: var(--muted);
    font-weight: 500; padding: 0 2px; min-height: 14px; }}
  .clear-btn {{ margin-left: 8px; font-size: 11px; padding: 2px 8px;
    background: transparent; border: 1px solid var(--border);
    color: var(--muted); border-radius: 999px; cursor: pointer;
    display: none; font-weight: 600; }}
  .clear-btn:hover {{ color: var(--accent-2); border-color: var(--accent-2); }}
  .clear-btn.show {{ display: inline-block; }}
  .rank-list-wrap {{ flex: 1 1 auto; overflow-y: auto;
    border: 1px solid var(--border); border-radius: 8px; background: var(--panel);
    scrollbar-color: #2f343c transparent; }}
  .rank-list-wrap::-webkit-scrollbar {{ width: 8px; }}
  .rank-list-wrap::-webkit-scrollbar-thumb {{ background: #2f343c;
    border-radius: 4px; }}
  ul.rank-list {{ list-style: none; margin: 0; padding: 0;
    font-size: 13px; outline: none; }}
  ul.rank-list:focus-visible {{ box-shadow: inset 0 0 0 2px var(--accent-2); }}
  ul.rank-list .rank-head {{
    position: sticky; top: 0; background: #2a3140;
    color: var(--fg); font-weight: 700; font-size: 12px;
    display: flex; align-items: center; padding: 8px 10px;
    border-bottom: 1px solid var(--border); z-index: 1;
  }}
  ul.rank-list .rank-head .col-rank {{ flex: 0 0 36px; text-align: left; }}
  ul.rank-list .rank-head .col-year {{ flex: 0 0 60px; text-align: left; }}
  ul.rank-list .rank-head .col-ytd {{ flex: 1 1 auto; text-align: right; }}
  ul.rank-list .rank-head .col-tot {{ flex: 0 0 60px; text-align: right; }}
  ul.rank-list li.row {{
    display: flex; align-items: center; padding: 7px 10px;
    border-bottom: 1px solid var(--border); color: #d0d6df;
    cursor: pointer; position: relative;
    border-left: 3px solid transparent;
    transform-origin: left center;
    transition: background 180ms ease, border-left-color 180ms ease,
                color 180ms ease, transform 180ms ease;
  }}
  ul.rank-list li.row:nth-child(odd) {{ background: rgba(255,255,255,0.015); }}
  ul.rank-list li.row:hover {{ background: rgba(93,211,255,0.08); color: var(--fg); }}
  ul.rank-list li.row .col-rank {{ flex: 0 0 36px; color: var(--muted);
    font-variant-numeric: tabular-nums; }}
  ul.rank-list li.row .col-year {{ flex: 0 0 60px; font-weight: 700; }}
  ul.rank-list li.row .col-ytd {{ flex: 1 1 auto; text-align: right;
    font-variant-numeric: tabular-nums; }}
  ul.rank-list li.row .col-tot {{ flex: 0 0 60px; text-align: right;
    font-variant-numeric: tabular-nums; color: var(--muted); }}
  ul.rank-list li.row.is-current .col-year {{
    border-bottom: 2px solid var(--accent);
    padding-bottom: 1px;
  }}
  ul.rank-list li.row.is-selected {{
    background: rgba(93,211,255,0.12);
    color: var(--fg);
    font-weight: 700;
    border-left-color: var(--accent-2);
    transform: scale(1.02);
  }}
  ul.rank-list li.row.is-selected:hover {{ background: rgba(93,211,255,0.18); }}
  ul.rank-list li.row.is-current.is-selected {{
    background: rgba(255,184,58,0.12);
    border-left-color: var(--accent-2);
  }}
  ul.rank-list li.row.is-current.is-selected:hover {{
    background: rgba(255,184,58,0.20);
  }}
  ul.rank-list li.row.is-key-focus {{
    box-shadow: inset 0 0 0 2px var(--accent-2);
  }}
  ul.rank-list li.row.is-disabled {{ cursor: default; opacity: 0.55; }}
  ul.rank-list li.row.is-disabled:hover {{
    background: transparent; color: #d0d6df;
  }}
  ul.rank-list li.row.ripple::after {{
    content: ""; position: absolute; left: 0; top: 0; right: 0; bottom: 0;
    background: rgba(93,211,255,0.32);
    animation: ripple 120ms ease-out forwards;
    pointer-events: none;
  }}
  @keyframes ripple {{
    from {{ opacity: 1; }}
    to {{ opacity: 0; }}
  }}
  ul.rank-list li.row.is-hidden {{ display: none; }}
  @media (max-width: 900px) {{
    .row-main {{ flex-direction: column; }}
    .rank-wrap {{ flex: 0 0 auto; max-height: 480px; }}
  }}
  .tooltip {{ position: absolute; pointer-events: none; background: var(--panel);
    border: 1px solid var(--border); border-radius: 8px; padding: 9px 12px;
    font-size: 14px; color: var(--fg);
    box-shadow: 0 6px 18px rgba(0,0,0,0.55);
    transform: translate(-50%, -100%); white-space: nowrap; opacity: 0;
    transition: opacity 0.12s; font-weight: 600; line-height: 1.45;
    z-index: 5; }}
  .tooltip.persistent {{ pointer-events: auto; }}
  .tooltip .row {{ display: flex; align-items: center; gap: 8px; }}
  .tooltip .dot {{ width: 9px; height: 9px; border-radius: 50%;
    display: inline-block; }}
  .tooltip .head {{ font-weight: 700; margin-bottom: 4px; color: var(--fg);
    font-size: 14px; }}
  .tooltip .meta {{ color: var(--muted); font-weight: 500; font-size: 12px;
    margin-top: 2px; }}
  footer {{ font-size: 12px; color: var(--muted); margin-top: 12px;
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
      <div class="panel">
        <div class="panel-title">Cumulative ACE
          <span class="panel-sub">vs. {climo_start}–{climo_end} climatology</span>
        </div>
        <svg id="chartAce" viewBox="0 0 1000 440" preserveAspectRatio="xMidYMid meet"></svg>
      </div>
      <div class="panel">
        <div class="panel-title">Rank Trajectory
          <span class="panel-sub">selected season's daily rank vs all on record</span>
        </div>
        <svg id="chartRank" viewBox="0 0 1000 130" preserveAspectRatio="xMidYMid meet"></svg>
      </div>
      <div class="panel">
        <div class="panel-title">Daily ACE Increments
          <span class="panel-sub">per-day contribution to the total</span>
        </div>
        <svg id="chartDaily" viewBox="0 0 1000 130" preserveAspectRatio="xMidYMid meet"></svg>
      </div>
      <div class="panel">
        <div class="panel-title">Storm Activity
          <span class="panel-sub" id="ganttSub"></span>
        </div>
        <svg id="chartLegend" viewBox="0 0 1000 36" preserveAspectRatio="xMidYMid meet"></svg>
        <div class="panel-gantt-scroll">
          <svg id="chartGantt" viewBox="0 0 1000 100" preserveAspectRatio="xMidYMid meet"></svg>
        </div>
      </div>
      <div class="tooltip" id="tipShared"></div>
      <div class="tooltip" id="tipGantt"></div>
    </div>
    <div class="rank-wrap">
      <div class="rank-title">
        <span id="rankIntro">Click any year for its profile</span>
        <button type="button" class="clear-btn" id="clearSelBtn"
                title="Clear selected year">clear ×</button>
      </div>
      <div class="search-wrap">
        <input type="text" id="yearSearch" class="search-input"
               placeholder="Filter years…" autocomplete="off"
               aria-label="Filter years by year or ACE">
        <button type="button" class="search-clear" id="searchClearBtn"
                title="Clear filter" aria-label="Clear filter">×</button>
      </div>
      <div class="search-count" id="searchCount"></div>
      <div class="rank-list-wrap" id="rankScroll">
        <ul class="rank-list" id="rankList" role="listbox"
            tabindex="0" aria-label="Seasons by ACE rank">
          <li class="rank-head" aria-hidden="true">
            <span class="col-rank">#</span>
            <span class="col-year">Year</span>
            <span class="col-ytd">ACE</span>
            <span class="col-tot">Total</span>
          </li>
        </ul>
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
function lighten(hex, amt) {{
  // amt 0..1; lighten the hex color toward white by amt fraction.
  const m = /^#([0-9a-f]{{2}})([0-9a-f]{{2}})([0-9a-f]{{2}})$/i.exec(hex);
  if (!m) return hex;
  const r = parseInt(m[1], 16), g = parseInt(m[2], 16), b = parseInt(m[3], 16);
  const f = v => Math.round(v + (255 - v) * amt);
  return "#" + [f(r), f(g), f(b)].map(v =>
    v.toString(16).padStart(2, "0")).join("");
}}

// Shared X scale (DOY 1..366 → SVG x in viewBox units).
const W = 1000, M_L = 60, M_R = 18;
const PW = W - M_L - M_R;
const xs = (doy) => M_L + (doy - 1) / 365 * PW;
const xToDoy = (x) => Math.max(1, Math.min(366,
  Math.round(1 + (x - M_L) / PW * 365)));

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
function fmtDateLong(iso) {{
  if (!iso) return "-";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "-";
  return d.toLocaleDateString(undefined, {{
    month: "short", day: "numeric", year: "numeric",
    timeZone: "UTC"
  }});
}}
function isoToDoy(iso) {{
  if (!iso) return null;
  const t = new Date(iso);
  if (isNaN(t.getTime())) return null;
  const start = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
  const ms = t.getTime() - start.getTime();
  return Math.floor(ms / 86400000) + 1
    + ((t.getUTCHours() + t.getUTCMinutes()/60) / 24);
}}
function durationDays(isoStart, isoEnd) {{
  if (!isoStart || !isoEnd) return null;
  const a = new Date(isoStart).getTime();
  const b = new Date(isoEnd).getTime();
  if (isNaN(a) || isNaN(b)) return null;
  return Math.max(0, (b - a) / 86400000);
}}

// ===== Panel 1: ACE percentile chart =====
const aceSvg = document.getElementById("chartAce");
const tipShared = document.getElementById("tipShared");
const tipGantt = document.getElementById("tipGantt");
const box = document.getElementById("chartbox");

const ACE_M = {{ t: 14, b: 30 }};
const ACE_VBH = 440;
const ACE_PH = ACE_VBH - ACE_M.t - ACE_M.b;

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
      "font-size": 13, fill: "var(--muted)" }}, aceSvg).textContent =
      Math.round(v);
  }}
  el("text", {{ x: 14, y: ACE_M.t + ACE_PH / 2, "text-anchor": "middle",
    "font-size": 14, fill: "var(--muted)",
    transform: `rotate(-90 14 ${{ACE_M.t + ACE_PH / 2}})` }}, aceSvg)
    .textContent = "Cumulative ACE (×10⁴ kt²)";

  // Month dividers + labels
  MONTH_STARTS.forEach((d, i) => {{
    el("line", {{ x1: xs(d), x2: xs(d), y1: ACE_M.t, y2: ACE_M.t + ACE_PH,
      stroke: "var(--border)", "stroke-width": 1, "stroke-opacity": 0.4 }},
      aceSvg);
    el("text", {{ x: xs(d + 15), y: ACE_M.t + ACE_PH + 20,
      "text-anchor": "middle", "font-size": 13,
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
  // Climo band edges
  linePath(DATA.doy, DATA.climo.min, "#2e6a96", 1.5, null, 0.6);
  linePath(DATA.doy, DATA.climo.max, "#2e6a96", 1.5, null, 0.6);
  linePath(DATA.doy, DATA.climo.p10, "#3aa2cf", 1.5, null, 0.75);
  linePath(DATA.doy, DATA.climo.p90, "#3aa2cf", 1.5, null, 0.75);
  linePath(DATA.doy, DATA.climo.p25, "#5dd3ff", 1.7, null, 0.85);
  linePath(DATA.doy, DATA.climo.p75, "#5dd3ff", 1.7, null, 0.85);

  // Climo mean (dashed cyan), prior-year (solid violet)
  linePath(DATA.doy, DATA.climo.mean, "var(--accent-2)", 2.2, "6 4");
  if (DATA.prior_year && DATA.prior_year.values)
    linePath(DATA.doy, DATA.prior_year.values, "var(--accent-3)", 2.2);

  // Current-year amber line: thicker than the original (5px white core
  // + 3.5px cyan halo achieved with stacked paths) — but we want amber
  // for CURRENT_YEAR specifically (matches header amber accent).
  linePath(DATA.current.doy, DATA.current.values, "var(--accent)", 3.5);
  if (DATA.today_doy) {{
    el("circle", {{ cx: xs(DATA.today_doy), cy: aceY(DATA.current.latest_value),
      r: 5.5, fill: "var(--accent)", stroke: "var(--bg)", "stroke-width": 2 }},
      aceSvg);
  }}

  // Selected-year overlay group (populated by setOverlay)
  el("g", {{ id: "selGroup" }}, aceSvg);

  // Watermark
  el("text", {{ x: M_L + PW - 10, y: ACE_M.t + 30,
    "text-anchor": "end", "font-size": 28, "font-weight": 700,
    fill: "var(--fg)", "fill-opacity": 0.18, "letter-spacing": 0.5 }}, aceSvg)
    .textContent = "@WeathermanAAA_";
}})();

let selectedYear = CURRENT_YEAR;
// Cached per-render arrays, used for crosshair tooltip.
let cachedRankAtDoy = new Array(366).fill(null);
let cachedDailyAtDoy = new Array(366).fill(0);

function setAceOverlay(year) {{
  // Manage the pink overlay on panel 1. year=null hides it.
  const existing = document.getElementById("selGroup");
  clear(existing);
  if (year == null) return;
  const vals = DATA.all_years && DATA.all_years[year];
  if (!vals) return;
  let d = "";
  for (let i = 0; i < vals.length; i++)
    d += (i === 0 ? "M" : "L") + xs(DATA.doy[i]) + "," + aceY(vals[i]) + " ";
  // Stacked stroke: white halo under, pink top, gives the "thicker line"
  // visual presence that the spec calls for on the selected-year trace.
  el("path", {{
    d, fill: "none", stroke: "rgba(255,255,255,0.85)", "stroke-width": 5,
    "stroke-linejoin": "round", "stroke-linecap": "round"
  }}, existing);
  el("path", {{
    d, fill: "none", stroke: "var(--hot-pink)", "stroke-width": 3.5,
    "stroke-linejoin": "round", "stroke-linecap": "round"
  }}, existing);
  const lastIdx = vals.length - 1;
  el("circle", {{ cx: xs(DATA.doy[lastIdx]), cy: aceY(vals[lastIdx]),
    r: 4.5, fill: "var(--hot-pink)", stroke: "var(--bg)",
    "stroke-width": 1.8 }}, existing);
  el("text", {{ x: xs(DATA.doy[lastIdx]) - 6, y: aceY(vals[lastIdx]) - 7,
    "text-anchor": "end", "font-size": 14, "font-weight": 700,
    fill: "var(--hot-pink)" }}, existing).textContent = year;
}}

// ===== Panel 2: Rank trajectory =====
const rankSvg = document.getElementById("chartRank");
const RANK_VBH = 130;
const RANK_M = {{ t: 16, b: 24 }};
const RANK_PH = RANK_VBH - RANK_M.t - RANK_M.b;

function renderRankPanel(year) {{
  clear(rankSvg);
  const arr = DATA.all_years && DATA.all_years[year];
  const totalSeasons = DATA.total_seasons || 1;
  // Compute rank at each DOY (cached for crosshair tooltip)
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
  cachedRankAtDoy = ranks;
  // Mask future DOY for the in-progress current year
  let drawDoyMax = 366;
  if (year === CURRENT_YEAR && DATA.today_doy) drawDoyMax = DATA.today_doy;
  const yMaxRank = totalSeasons;
  const rY = (rank) => RANK_M.t + ((rank - 1) / Math.max(1, yMaxRank - 1)) * RANK_PH;
  // Gridlines
  for (const r of [1, Math.ceil(totalSeasons / 2), totalSeasons]) {{
    el("line", {{ x1: M_L, x2: M_L + PW, y1: rY(r), y2: rY(r),
      stroke: "var(--grid-dim)", "stroke-width": 1 }}, rankSvg);
    el("text", {{ x: M_L - 8, y: rY(r) + 4, "text-anchor": "end",
      "font-size": 12, fill: "var(--muted)" }}, rankSvg).textContent = r;
  }}
  el("text", {{ x: 14, y: RANK_M.t + RANK_PH / 2, "text-anchor": "middle",
    "font-size": 13, fill: "var(--muted)",
    transform: `rotate(-90 14 ${{RANK_M.t + RANK_PH / 2}})` }}, rankSvg)
    .textContent = "Rank";
  // Month dividers + labels
  MONTH_STARTS.forEach((d, i) => {{
    el("line", {{ x1: xs(d), x2: xs(d), y1: RANK_M.t, y2: RANK_M.t + RANK_PH,
      stroke: "var(--border)", "stroke-width": 0.8,
      "stroke-opacity": 0.35, "stroke-dasharray": "3 3" }}, rankSvg);
    el("text", {{ x: xs(d + 15), y: RANK_M.t + RANK_PH + 16,
      "text-anchor": "middle", "font-size": 12,
      fill: "var(--muted)" }}, rankSvg).textContent = MONTH_LABELS[i];
  }});
  el("line", {{ x1: M_L, x2: M_L + PW, y1: RANK_M.t + RANK_PH,
    y2: RANK_M.t + RANK_PH, stroke: "var(--border)", "stroke-width": 1 }}, rankSvg);

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
        "stroke-width": 3.5, "stroke-linejoin": "round",
        "stroke-linecap": "round" }}, rankSvg);
    }}
  }}
  const summary = (arr && bestR != null)
    ? `Rank: ${{bestR}}–${{worstR}} of ${{totalSeasons}}`
    : "Rank trajectory unavailable";
  el("text", {{ x: M_L + 6, y: RANK_M.t - 4, "font-size": 13,
    "font-weight": 700, fill: "var(--fg)" }}, rankSvg).textContent = summary;
}}

// ===== Panel 3: Daily ACE bars =====
const dailySvg = document.getElementById("chartDaily");
const DAILY_VBH = 130;
const DAILY_M = {{ t: 16, b: 24 }};
const DAILY_PH = DAILY_VBH - DAILY_M.t - DAILY_M.b;

function renderDailyPanel(year) {{
  clear(dailySvg);
  const arr = DATA.all_years && DATA.all_years[year];
  const daily = new Array(366).fill(0);
  if (arr) {{
    for (let i = 0; i < 366; i++) {{
      daily[i] = i === 0 ? arr[i] : Math.max(0, arr[i] - arr[i - 1]);
    }}
  }}
  cachedDailyAtDoy = daily;
  let drawDoyMax = 366;
  if (year === CURRENT_YEAR && DATA.today_doy) drawDoyMax = DATA.today_doy;
  let peak = 0, peakDoy = -1;
  for (let i = 0; i < drawDoyMax; i++) {{
    if (daily[i] > peak) {{ peak = daily[i]; peakDoy = i + 1; }}
  }}
  const yMax = Math.max(peak * 1.15, 0.1);
  const dY = (v) => DAILY_M.t + DAILY_PH - (v / yMax) * DAILY_PH;
  el("line", {{ x1: M_L, x2: M_L + PW, y1: DAILY_M.t, y2: DAILY_M.t,
    stroke: "var(--grid-dim)", "stroke-width": 1 }}, dailySvg);
  el("line", {{ x1: M_L, x2: M_L + PW, y1: DAILY_M.t + DAILY_PH,
    y2: DAILY_M.t + DAILY_PH, stroke: "var(--border)",
    "stroke-width": 1 }}, dailySvg);
  el("text", {{ x: M_L - 8, y: dY(0) + 4, "text-anchor": "end",
    "font-size": 12, fill: "var(--muted)" }}, dailySvg).textContent = "0";
  el("text", {{ x: M_L - 8, y: dY(yMax) + 4, "text-anchor": "end",
    "font-size": 12, fill: "var(--muted)" }}, dailySvg)
    .textContent = peak.toFixed(2);
  el("text", {{ x: 14, y: DAILY_M.t + DAILY_PH / 2, "text-anchor": "middle",
    "font-size": 13, fill: "var(--muted)",
    transform: `rotate(-90 14 ${{DAILY_M.t + DAILY_PH / 2}})` }}, dailySvg)
    .textContent = "Daily ACE";
  MONTH_STARTS.forEach((d, i) => {{
    el("line", {{ x1: xs(d), x2: xs(d), y1: DAILY_M.t, y2: DAILY_M.t + DAILY_PH,
      stroke: "var(--border)", "stroke-width": 0.8,
      "stroke-opacity": 0.35, "stroke-dasharray": "3 3" }}, dailySvg);
    el("text", {{ x: xs(d + 15), y: DAILY_M.t + DAILY_PH + 16,
      "text-anchor": "middle", "font-size": 12,
      fill: "var(--muted)" }}, dailySvg).textContent = MONTH_LABELS[i];
  }});
  // Bars (slightly wider than 1 DOY-slot, less gap between bars)
  const barW = (PW / 365) * 1.15;
  for (let i = 0; i < drawDoyMax; i++) {{
    const v = daily[i];
    if (v <= 0) continue;
    const x = xs(i + 1) - barW / 2;
    const y = dY(v);
    const color = (i + 1 === peakDoy) ? "var(--accent)" : "var(--accent-2)";
    el("rect", {{ x, y, width: barW, height: dY(0) - y,
      fill: color }}, dailySvg);
  }}
  if (peakDoy > 0) {{
    el("text", {{ x: M_L + 6, y: DAILY_M.t - 4, "font-size": 13,
      "font-weight": 700, fill: "var(--fg)" }}, dailySvg).textContent =
      `Max daily ACE: ${{peak.toFixed(4)}} on ${{doyToDate(peakDoy, year)}}`;
  }} else {{
    el("text", {{ x: M_L + 6, y: DAILY_M.t - 4, "font-size": 13,
      "font-weight": 700, fill: "var(--muted)" }}, dailySvg).textContent =
      "No daily ACE yet for this season";
  }}
}}

// ===== Panel 4: Storm Gantt — Wikipedia style, 1 storm per row, blocks of 8
const ganttSvg = document.getElementById("chartGantt");
const ganttSubEl = document.getElementById("ganttSub");
const STORMS_PER_BLOCK = 8;
const ROW_H = 24;          // viewBox units per storm row (~33px @ 1400 wide)
const PILL_H = 16;         // viewBox units (pill is centered in row)
const AXIS_H = 24;         // axis area below each block
const BLOCK_GAP = 20;      // viewBox units between blocks
const GANTT_TOP = 8;

let persistentGanttKey = null;  // "block-i" of the click-locked pill, or null

function renderGanttPanel(year) {{
  clear(ganttSvg);
  hideGanttTip(true);
  const storms = (DATA.storms_by_year && DATA.storms_by_year[year]) || [];
  const sorted = storms.filter(s => s.formation && s.dissipation)
    .map(s => ({{
      name: s.name || "UNNAMED",
      d0: isoToDoy(s.formation),
      d1: isoToDoy(s.dissipation),
      pk: s.peak_wind_kt,
      pkTime: s.peak_wind_time,
      formationIso: s.formation,
      dissipationIso: s.dissipation,
      ace: s.ace_total != null ? s.ace_total : 0,
    }}))
    .filter(s => s.d0 != null && s.d1 != null && s.d1 >= s.d0)
    .sort((a, b) => a.d0 - b.d0);

  ganttSubEl.textContent = sorted.length
    ? `${{sorted.length}} storm${{sorted.length === 1 ? "" : "s"}} in ${{year}}`
    : "";

  if (sorted.length === 0) {{
    const totalH = GANTT_TOP + 30 + AXIS_H;
    ganttSvg.setAttribute("viewBox", `0 0 ${{W}} ${{totalH}}`);
    el("text", {{ x: W / 2, y: totalH / 2,
      "text-anchor": "middle", "font-size": 14, "font-weight": 700,
      fill: "var(--muted)" }}, ganttSvg).textContent =
      "No named storms this season";
    return;
  }}

  const seasonAceTotal = sorted.reduce((acc, s) => acc + (s.ace || 0), 0);
  const blocks = [];
  for (let i = 0; i < sorted.length; i += STORMS_PER_BLOCK) {{
    blocks.push(sorted.slice(i, i + STORMS_PER_BLOCK));
  }}

  // Total height
  let totalH = GANTT_TOP;
  for (let bi = 0; bi < blocks.length; bi++) {{
    totalH += blocks[bi].length * ROW_H + AXIS_H;
    if (bi < blocks.length - 1) totalH += BLOCK_GAP;
  }}
  ganttSvg.setAttribute("viewBox", `0 0 ${{W}} ${{totalH}}`);

  // Drop-shadow filter (defined once)
  const defs = el("defs", {{}}, ganttSvg);
  const filt = el("filter", {{ id: "ganttDrop", x: "-5%", y: "-25%",
    width: "115%", height: "150%" }}, defs);
  el("feDropShadow", {{ dx: 0, dy: 1, "stdDeviation": 1,
    "flood-color": "#000", "flood-opacity": 0.45 }}, filt);

  let yCursor = GANTT_TOP;
  blocks.forEach((block, bi) => {{
    const blockTop = yCursor;
    const blockH = block.length * ROW_H;
    // Vertical month dividers
    MONTH_STARTS.forEach((d) => {{
      el("line", {{ x1: xs(d), x2: xs(d), y1: blockTop,
        y2: blockTop + blockH, stroke: "var(--border)", "stroke-width": 0.8,
        "stroke-opacity": 0.5, "stroke-dasharray": "3 3" }}, ganttSvg);
    }});
    // Bottom rule
    el("line", {{ x1: M_L, x2: M_L + PW,
      y1: blockTop + blockH, y2: blockTop + blockH,
      stroke: "var(--border)", "stroke-width": 1 }}, ganttSvg);
    // Storm pills
    block.forEach((s, ri) => {{
      const yCenter = blockTop + ri * ROW_H + ROW_H / 2;
      const x0 = xs(s.d0);
      const x1 = xs(s.d1);
      const w = Math.max(3, x1 - x0);
      const [cat, color] = sshwsColor(s.pk);
      const key = `pill-${{bi}}-${{ri}}`;
      const g = el("g", {{ "data-pill": key,
        style: "cursor: pointer;" }}, ganttSvg);
      const rect = el("rect", {{
        x: x0, y: yCenter - PILL_H / 2, width: w, height: PILL_H,
        rx: 8, ry: 8, fill: color,
        filter: "url(#ganttDrop)",
        "data-base-color": color
      }}, g);
      const lbl = el("text", {{ x: x1 + 6, y: yCenter + 4,
        "font-size": 13, "font-weight": 700, fill: "var(--fg)" }}, g);
      lbl.textContent = `${{s.name}} (${{cat}})`;
      // Hover/click handlers
      g.addEventListener("mouseenter", (evt) => {{
        rect.setAttribute("fill", lighten(color, 0.10));
        if (persistentGanttKey === null) {{
          showGanttTip(s, seasonAceTotal, evt);
        }}
      }});
      g.addEventListener("mousemove", (evt) => {{
        if (persistentGanttKey === null) {{
          positionGanttTip(evt);
        }}
      }});
      g.addEventListener("mouseleave", () => {{
        rect.setAttribute("fill", color);
        if (persistentGanttKey === null) {{
          hideGanttTip();
        }}
      }});
      g.addEventListener("click", (evt) => {{
        evt.stopPropagation();
        if (persistentGanttKey === key) {{
          persistentGanttKey = null;
          hideGanttTip();
        }} else {{
          persistentGanttKey = key;
          showGanttTip(s, seasonAceTotal, evt);
          positionGanttTip(evt);
          tipGantt.classList.add("persistent");
        }}
      }});
    }});
    // Per-block axis (month labels)
    const axisY = blockTop + blockH + AXIS_H - 8;
    MONTH_STARTS.forEach((d, i) => {{
      el("text", {{ x: xs(d + 15), y: axisY,
        "text-anchor": "middle", "font-size": 12,
        fill: "var(--muted)" }}, ganttSvg).textContent = MONTH_LABELS[i];
    }});
    yCursor += blockH + AXIS_H;
    if (bi < blocks.length - 1) yCursor += BLOCK_GAP;
  }});
}}

function showGanttTip(s, seasonAceTotal, evt) {{
  const [cat, color] = sshwsColor(s.pk);
  const dur = durationDays(s.formationIso, s.dissipationIso);
  const pct = (seasonAceTotal > 0 && s.ace != null)
    ? (100 * s.ace / seasonAceTotal) : 0;
  const peakWind = (s.pk != null && !isNaN(s.pk))
    ? `${{Math.round(s.pk)}} kt`
    : "-";
  const peakAt = s.pkTime ? fmtDateLong(s.pkTime) : "-";
  const ace = s.ace != null ? s.ace.toFixed(1) : "-";
  tipGantt.innerHTML =
    '<div class="head">' +
      '<span class="dot" style="background:' + color +
      ';margin-right:6px;display:inline-block;width:9px;height:9px;border-radius:50%;"></span>' +
      s.name + ' <span style="color:var(--muted);font-weight:600;">(' + cat + ')</span>' +
    '</div>' +
    '<div>Formation: ' + fmtDateLong(s.formationIso) +
      ' · Dissipation: ' + fmtDateLong(s.dissipationIso) +
      ' · Duration: ' + (dur != null ? dur.toFixed(1) : "-") + ' d</div>' +
    '<div>Peak winds: ' + peakWind + ' at ' + peakAt + '</div>' +
    '<div>ACE contribution: ' + ace + ' (~' + pct.toFixed(1) + '% of season)</div>';
  tipGantt.style.opacity = 1;
  positionGanttTip(evt);
}}
function positionGanttTip(evt) {{
  const rect = box.getBoundingClientRect();
  tipGantt.style.left = (evt.clientX - rect.left) + "px";
  tipGantt.style.top  = (evt.clientY - rect.top - 14) + "px";
}}
function hideGanttTip(force) {{
  if (!force && persistentGanttKey !== null) return;
  tipGantt.style.opacity = 0;
  tipGantt.classList.remove("persistent");
}}
// Click outside any pill clears persistent
document.addEventListener("click", (evt) => {{
  if (persistentGanttKey !== null) {{
    const onPill = evt.target.closest && evt.target.closest("[data-pill]");
    if (!onPill) {{
      persistentGanttKey = null;
      hideGanttTip(true);
    }}
  }}
}});

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
  const sw = 18, sh = 10;
  items.forEach(([cat, krange], i) => {{
    const x0 = left + i * slotW;
    el("rect", {{ x: x0, y: 14, width: sw, height: sh,
      rx: 2, ry: 2, fill: swatchColors[i],
      stroke: "var(--border)", "stroke-width": 1 }}, legSvg);
    el("text", {{ x: x0 + sw + 6, y: 23, "font-size": 13,
      "font-weight": 700, fill: "var(--fg)" }}, legSvg)
      .textContent = `${{cat}} (${{krange}})`;
  }});
}})();

// ===== Cross-panel crosshair (panels 1, 2, 3 share X axis) =====
// renderRankPanel and renderDailyPanel both clear() their svg on every
// re-render, which would also wipe a once-attached crosshair line. So we
// re-create the crosshair lines from a single helper that runs after every
// year change. crossAce is safe (its panel is never cleared) but we route
// it through the same helper for consistency.
function makeCrossLine(svg, top, bottom) {{
  return el("line", {{ x1: 0, x2: 0, y1: top, y2: bottom,
    stroke: "var(--accent-2)", "stroke-width": 1.2,
    "stroke-dasharray": "4 4", opacity: 0,
    "pointer-events": "none" }}, svg);
}}
let crossAce  = makeCrossLine(aceSvg,   ACE_M.t,   ACE_M.t + ACE_PH);
let crossRank = makeCrossLine(rankSvg,  RANK_M.t,  RANK_M.t + RANK_PH);
let crossDaily = makeCrossLine(dailySvg, DAILY_M.t, DAILY_M.t + DAILY_PH);
function ensureCrosshairs() {{
  if (!aceSvg.contains(crossAce))
    crossAce = makeCrossLine(aceSvg, ACE_M.t, ACE_M.t + ACE_PH);
  if (!rankSvg.contains(crossRank))
    crossRank = makeCrossLine(rankSvg, RANK_M.t, RANK_M.t + RANK_PH);
  if (!dailySvg.contains(crossDaily))
    crossDaily = makeCrossLine(dailySvg, DAILY_M.t, DAILY_M.t + DAILY_PH);
}}

let pendingHoverFrame = null;
let pendingHoverEvt = null;
function scheduleHover(evt, sourceSvg) {{
  pendingHoverEvt = {{ evt, sourceSvg }};
  if (pendingHoverFrame) return;
  pendingHoverFrame = requestAnimationFrame(() => {{
    pendingHoverFrame = null;
    if (pendingHoverEvt) drawCrosshair(pendingHoverEvt.evt, pendingHoverEvt.sourceSvg);
  }});
}}

function drawCrosshair(evt, sourceSvg) {{
  const pt = sourceSvg.createSVGPoint();
  const src = evt.touches ? evt.touches[0] : evt;
  pt.x = src.clientX; pt.y = src.clientY;
  const p = pt.matrixTransform(sourceSvg.getScreenCTM().inverse());
  if (p.x < M_L || p.x > M_L + PW) {{ hideCrosshair(); return; }}
  const doy = xToDoy(p.x);
  const x = xs(doy);
  for (const c of [crossAce, crossRank, crossDaily]) {{
    c.setAttribute("x1", x);
    c.setAttribute("x2", x);
    c.setAttribute("opacity", 1);
  }}
  // Tooltip values pulled from cached arrays + selectedYear
  const arr = DATA.all_years && DATA.all_years[selectedYear];
  const idx = doy - 1;
  let cum = null, daily = null, rank = null;
  if (arr) {{
    // For the in-progress current year, mask future DOYs.
    const usable = (selectedYear === CURRENT_YEAR && DATA.today_doy)
      ? Math.min(arr.length, DATA.today_doy) : arr.length;
    if (idx < usable) {{
      cum = arr[idx];
      daily = (idx === 0) ? arr[0] : Math.max(0, arr[idx] - arr[idx - 1]);
    }}
  }}
  if (cachedRankAtDoy && cachedRankAtDoy[idx] != null) {{
    if (!(selectedYear === CURRENT_YEAR && DATA.today_doy
          && doy > DATA.today_doy)) {{
      rank = cachedRankAtDoy[idx];
    }}
  }}
  const fmt1 = v => (v == null ? "-" : v.toFixed(1));
  const fmt2 = v => (v == null ? "-" : v.toFixed(2));
  const totalSeasons = DATA.total_seasons || "-";
  const dateLabel = doyToDate(doy, selectedYear);
  tipShared.innerHTML =
    '<div class="head">' + selectedYear + ' · ' + dateLabel +
      ' <span style="color:var(--muted);font-weight:500;">(DOY ' + doy + ')</span></div>' +
    '<div>Cumulative ACE: <b>' + fmt1(cum) + '</b></div>' +
    '<div>Rank: <b>' + (rank != null ? rank : '-') + '</b>' +
      '<span style="color:var(--muted);font-weight:500;"> / ' + totalSeasons + '</span></div>' +
    '<div>Daily ACE: <b>' + fmt2(daily) + '</b></div>';
  // Position relative to chartbox container at the source SVG's screen coords
  const boxRect = box.getBoundingClientRect();
  const srcRect = sourceSvg.getBoundingClientRect();
  const scale = srcRect.width / W;
  const tipX = (x * scale) + (srcRect.left - boxRect.left);
  const tipY = (srcRect.top - boxRect.top) - 6;
  tipShared.style.left = tipX + "px";
  tipShared.style.top  = tipY + "px";
  tipShared.style.opacity = 1;
}}
function hideCrosshair() {{
  for (const c of [crossAce, crossRank, crossDaily]) c.setAttribute("opacity", 0);
  tipShared.style.opacity = 0;
}}
function attachCrosshair(svg) {{
  svg.addEventListener("mousemove", (evt) => scheduleHover(evt, svg));
  svg.addEventListener("mouseleave", hideCrosshair);
  svg.addEventListener("touchmove", (evt) => scheduleHover(evt, svg),
    {{ passive: true }});
  svg.addEventListener("touchend", hideCrosshair);
}}
attachCrosshair(aceSvg);
attachCrosshair(rankSvg);
attachCrosshair(dailySvg);

// ===== Header =====
function fmtNum(n, dp) {{
  if (n == null || isNaN(n)) return "-";
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
    'Rank: <span class="rank-val">' + (rankShow != null ? rankShow : '-') +
    '/' + (DATA.total_seasons || '-') + '</span>';
}}

// ===== Top-level setSelectedYear: update all 4 panels + header =====
const lblIntro = document.getElementById("rankIntro");
const clearBtn = document.getElementById("clearSelBtn");
const rankList = document.getElementById("rankList");
const rankScroll = document.getElementById("rankScroll");

function setSelectedYear(year, opts) {{
  opts = opts || {{}};
  const yearN = (typeof year === "string") ? parseInt(year, 10) : year;
  const valid = (yearN != null && DATA.all_years && DATA.all_years[yearN]);
  selectedYear = valid ? yearN : CURRENT_YEAR;

  setAceOverlay(selectedYear !== CURRENT_YEAR ? selectedYear : null);
  renderRankPanel(selectedYear);
  renderDailyPanel(selectedYear);
  renderGanttPanel(selectedYear);
  renderHeader(selectedYear);
  ensureCrosshairs();

  // Active row state
  rankList.querySelectorAll("li.row.is-selected").forEach(li =>
    li.classList.remove("is-selected"));
  const li = rankList.querySelector(`li.row[data-year="${{selectedYear}}"]`);
  if (li) {{
    li.classList.add("is-selected");
    if (opts.scroll !== false && typeof li.scrollIntoView === "function") {{
      li.scrollIntoView({{ behavior: "smooth", block: "center" }});
    }}
  }}
  if (selectedYear !== CURRENT_YEAR) {{
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

// ===== Rank list (with search, keyboard nav) =====
(function initRankList() {{
  const rankings = DATA.rankings || [];
  // Historical years sorted by total desc; current year inserted at its
  // YTD-rank position. The displayed "#" is the row index after insertion.
  const historical = rankings.filter(r => r.year !== CURRENT_YEAR)
    .sort((a, b) => (b.total - a.total) || (a.year - b.year));
  const currentRow = rankings.find(r => r.year === CURRENT_YEAR);
  const insertAt = (DATA.current_rank != null
    ? DATA.current_rank - 1
    : historical.length);
  const finalList = historical.slice();
  if (currentRow) {{
    const at = Math.max(0, Math.min(insertAt, historical.length));
    finalList.splice(at, 0, currentRow);
  }}

  finalList.forEach((r, i) => {{
    const li = document.createElement("li");
    li.className = "row";
    li.setAttribute("role", "option");
    li.setAttribute("data-year", r.year);
    const hasGantt = !!(DATA.storms_by_year && DATA.storms_by_year[r.year]);
    const hasCum = !!(DATA.all_years && DATA.all_years[r.year]);
    const clickable = hasCum && (r.year === CURRENT_YEAR || hasGantt);
    if (!clickable) li.classList.add("is-disabled");
    if (r.year === CURRENT_YEAR) {{
      li.classList.add("is-current");
      li.title = "Live current season: YTD";
      li.setAttribute("aria-current", "true");
    }}
    const isCur = r.year === CURRENT_YEAR;
    const aceShown = isCur ? r.ytd : r.total;
    const aceCellClass = isCur ? "col-ytd" : "col-ytd";
    const totCell = isCur
      ? '<span class="col-tot">YTD</span>'
      : ('<span class="col-tot">' + r.total.toFixed(1) + '</span>');
    li.innerHTML =
      '<span class="col-rank">' + (i + 1) + '</span>' +
      '<span class="col-year">' + r.year + '</span>' +
      '<span class="' + aceCellClass + '">ACE: ' + aceShown.toFixed(2) + '</span>' +
      totCell;
    if (clickable) {{
      li.addEventListener("click", () => {{
        // ripple
        li.classList.remove("ripple");
        void li.offsetWidth;  // force reflow to restart animation
        li.classList.add("ripple");
        setSelectedYear(r.year);
      }});
    }}
    rankList.appendChild(li);
  }});

  clearBtn.addEventListener("click", () => setSelectedYear(CURRENT_YEAR));

  // Search/filter
  const searchInput = document.getElementById("yearSearch");
  const searchClear = document.getElementById("searchClearBtn");
  const searchCount = document.getElementById("searchCount");

  function applyFilter() {{
    const q = (searchInput.value || "").trim().toLowerCase();
    let visible = 0;
    let total = 0;
    rankList.querySelectorAll("li.row").forEach(li => {{
      total++;
      if (!q) {{
        li.classList.remove("is-hidden");
        visible++;
        return;
      }}
      const year = li.getAttribute("data-year") || "";
      const txt = li.textContent.toLowerCase();
      if (year.includes(q) || txt.includes(q)) {{
        li.classList.remove("is-hidden");
        visible++;
      }} else {{
        li.classList.add("is-hidden");
      }}
    }});
    searchCount.textContent = q ? `${{visible}} of ${{total}}` : "";
    searchClear.classList.toggle("show", !!q);
  }}
  searchInput.addEventListener("input", applyFilter);
  searchInput.addEventListener("keydown", (evt) => {{
    if (evt.key === "Escape") {{
      if (searchInput.value) {{
        searchInput.value = "";
        applyFilter();
      }} else {{
        searchInput.blur();
      }}
    }}
  }});
  searchClear.addEventListener("click", () => {{
    searchInput.value = "";
    applyFilter();
    searchInput.focus();
  }});

  // Keyboard navigation on the listbox
  function visibleRows() {{
    return Array.from(rankList.querySelectorAll(
      "li.row:not(.is-hidden):not(.is-disabled)"));
  }}
  rankList.addEventListener("keydown", (evt) => {{
    const rows = visibleRows();
    if (rows.length === 0) return;
    const sel = rankList.querySelector("li.row.is-selected");
    let idx = sel ? rows.indexOf(sel) : -1;
    if (evt.key === "ArrowDown") {{
      evt.preventDefault();
      idx = (idx < 0) ? 0 : Math.min(rows.length - 1, idx + 1);
      const row = rows[idx];
      const yr = parseInt(row.getAttribute("data-year"), 10);
      setSelectedYear(yr);
    }} else if (evt.key === "ArrowUp") {{
      evt.preventDefault();
      idx = (idx <= 0) ? 0 : idx - 1;
      const row = rows[idx];
      const yr = parseInt(row.getAttribute("data-year"), 10);
      setSelectedYear(yr);
    }} else if (evt.key === "Enter" || evt.key === " ") {{
      evt.preventDefault();
      // Selection already mirrors hover — no-op on confirm.
    }} else if (evt.key === "Escape") {{
      if (searchInput.value) {{
        searchInput.value = "";
        applyFilter();
      }} else {{
        rankList.blur();
      }}
    }} else if (evt.key === "Home") {{
      evt.preventDefault();
      const yr = parseInt(rows[0].getAttribute("data-year"), 10);
      setSelectedYear(yr);
    }} else if (evt.key === "End") {{
      evt.preventDefault();
      const yr = parseInt(rows[rows.length - 1].getAttribute("data-year"), 10);
      setSelectedYear(yr);
    }}
  }});
}})();

// Initial render — current year on load
setSelectedYear(CURRENT_YEAR);
</script>
</body>
</html>
"""
