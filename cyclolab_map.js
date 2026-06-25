/* cyclolab_map.js - reusable stacking-map module (CycloLab revamp, Phase 1).
 *
 * Ports the canonical MapLibre engine from global_tracks.html (inline Protomaps
 * vector STYLE - no Mapbox token; storm track + intensity-colored observation
 * shapes + hover popups) and the spinning hurricane glyph from active-banner.js
 * (HURRICANE_PATH + the D/S/1-5 label, SSHS_COLORS), and adds:
 *   - a RASTER-LAYER FRAMEWORK (type:image 4-corner source + type:raster layer
 *     drawn beforeId "tracks-line-solid", swapped per frame via updateImage) that
 *     is READY to receive satellite / microwave / model frames once a producer
 *     publishes per-frame WGS84 corners + chrome-free tiles. No raster layers are
 *     wired in P1 (the floater frames have no corner bounds and are chrome-burned;
 *     there is no radar product) - the API + rail + time plumbing simply stand
 *     ready, and layers light up additively when addRasterLayer() is called.
 *   - an EXPLORER RAIL (grouped Imagery / Fields / Tracks; per-layer toggle +
 *     opacity; one ACTIVE layer; back-to-map on mobile).
 *   - a TIME-CONTROL framework (master timeline over the storm's track fixes;
 *     play / pause / step +/-1 / loop / speed; current valid time). P2 extends
 *     this to source-adaptive nearest-in-time across raster layers.
 *
 * One implementation, two mounts: a standalone page auto-mounts on
 * #cyclolab-map-standalone (absent inside CycloLab, so no double-boot); CycloLab
 * lazy-_loadScript's this file and calls `new CycloLabMap(rootEl, {storm})`.
 * MapLibre GL is lazy-loaded from unpkg the first time a map is built. No build
 * step, no bundler, no CDN framework beyond MapLibre itself.
 */
(function () {
  'use strict';

  var MAPLIBRE_JS = "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js";
  var MAPLIBRE_CSS = "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css";

  // ===================================================================
  // Reused constants (verbatim from global_tracks.html / active-banner.js)
  // ===================================================================
  var SSHS_COLORS = {
    "TD": "#3fa4ff", "TS": "#46c56a", "C1": "#ffe14d",
    "C2": "#ff9a2f", "C3": "#f5333c", "C4": "#e33ad4", "C5": "#b03bff"
  };
  var CAT_LABELS = {
    "TD": "Depression", "TS": "Tropical Storm",
    "C1": "Category 1", "C2": "Category 2", "C3": "Category 3",
    "C4": "Category 4", "C5": "Category 5"
  };
  // Verbatim from active-banner.js / global_tracks.html - the spinning glyph path.
  var HURRICANE_PATH = "M 16.37,-28.27 C 13.58,-28.13 11.51,-27.90 9.23,-27.49 C 1.27,-26.06 -5.88,-22.70 -10.92,-18.02 C -14.83,-14.40 -17.41,-10.06 -18.49,-5.32 C -18.95,-3.30 -19.15,-1.42 -19.15,0.91 C -19.15,2.53 -19.09,3.28 -18.89,4.45 C -18.38,7.38 -17.47,9.46 -15.41,12.37 C -13.88,14.54 -13.43,15.31 -13.20,16.13 C -13.11,16.44 -13.09,16.62 -13.09,17.14 C -13.10,17.93 -13.20,18.32 -13.67,19.28 C -15.30,22.59 -18.65,24.93 -23.49,26.14 C -25.26,26.58 -27.29,26.87 -29.18,26.95 L -30.00,26.98 L -29.65,27.06 C -27.33,27.62 -24.41,28.05 -21.57,28.27 C -20.04,28.38 -16.31,28.38 -14.80,28.27 C -12.93,28.13 -11.43,27.95 -9.77,27.67 C -0.59,26.14 7.56,22.03 12.68,16.37 C 16.22,12.45 18.28,8.10 18.93,3.13 C 19.64,-2.25 18.99,-6.47 16.84,-10.16 C 16.48,-10.80 15.79,-11.82 14.99,-12.95 C 13.61,-14.89 13.18,-15.77 13.12,-16.83 C 13.07,-17.61 13.23,-18.26 13.71,-19.23 C 14.97,-21.79 17.38,-23.84 20.67,-25.16 C 23.13,-26.14 26.24,-26.77 29.15,-26.87 L 30.00,-26.90 L 29.67,-26.98 C 29.13,-27.12 27.57,-27.44 26.66,-27.58 C 24.96,-27.87 23.39,-28.05 21.66,-28.18 C 20.72,-28.25 17.16,-28.30 16.37,-28.27 Z";

  // ---- Style spec: TAT palette + Protomaps vector tiles (verbatim) ----
  var STYLE = {
    "version": 8,
    "name": "TAT CycloLab Stacking Map",
    "sources": {
      "protomaps": {
        "type": "vector",
        "tiles": ["https://api.protomaps.com/tiles/v3/{z}/{x}/{y}.mvt?key=9d1a52d8fc230b5f"],
        "minzoom": 0,
        "maxzoom": 14,
        "attribution": "&copy; <a href=\"https://protomaps.com\">Protomaps</a> &copy; <a href=\"https://openstreetmap.org/copyright\">OSM</a>"
      }
    },
    "layers": [
      { "id": "background", "type": "background",
        "paint": { "background-color": "#aeb2b5" } },
      { "id": "water", "type": "fill",
        "source": "protomaps", "source-layer": "water",
        "paint": { "fill-color": "#2463a0" } },
      { "id": "earth", "type": "fill",
        "source": "protomaps", "source-layer": "earth",
        "paint": { "fill-color": "#aeb2b5" } },
      { "id": "coastline", "type": "line",
        "source": "protomaps", "source-layer": "earth",
        "paint": {
          "line-color": "#ffffff", "line-opacity": 0.85,
          "line-width": ["interpolate", ["linear"], ["zoom"],
            0, 0.4, 4, 0.8, 8, 1.2, 12, 1.8]
        } },
      { "id": "country-border", "type": "line",
        "source": "protomaps", "source-layer": "boundaries",
        "filter": ["==", ["get", "kind"], "country"],
        "paint": {
          "line-color": "#ffffff", "line-opacity": 0.9,
          "line-width": ["interpolate", ["linear"], ["zoom"],
            0, 0.6, 4, 1.0, 8, 1.4, 12, 2.0]
        } },
      { "id": "state-border", "type": "line",
        "source": "protomaps", "source-layer": "boundaries",
        "filter": ["==", ["get", "kind"], "region"],
        "minzoom": 4,
        "paint": {
          "line-color": "#ffffff", "line-opacity": 0.5,
          "line-width": ["interpolate", ["linear"], ["zoom"],
            4, 0.3, 8, 0.6, 12, 1.0]
        } }
    ]
  };

  // Shared paint expressions (verbatim from global_tracks.html addStormLayers).
  var COLOR_STEP = [
    "step", ["coalesce", ["get", "intensity_kt"], 0],
    "#3fa4ff", 34, "#46c56a", 64, "#ffe14d", 83, "#ff9a2f",
    96, "#f5333c", 113, "#e33ad4", 137, "#b03bff"
  ];
  var ZOOM_RADIUS = ["interpolate", ["linear"], ["zoom"],
    0, 2.0, 4, 3.0, 8, 4.0, 12, 5.0];
  var ZOOM_ICON_SIZE = ["interpolate", ["linear"], ["zoom"],
    0, 0.20, 4, 0.28, 8, 0.36, 12, 0.45];

  var TRACKS_BEFORE = "tracks-line-solid";   // rasters insert below this id

  // ===================================================================
  // Helpers (verbatim from global_tracks.html)
  // ===================================================================
  function ktToMph5(k) { return Math.round(k * 1.15077945 / 5) * 5; }
  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function fmtTime(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    var m = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    var hh = String(d.getUTCHours()).padStart(2, "0");
    var mm = String(d.getUTCMinutes()).padStart(2, "0");
    return m[d.getUTCMonth()] + " " + d.getUTCDate() + ", " + hh + ":" + mm + "Z";
  }
  function sshsLabel(cls) {
    if (cls === "TD") return "D";
    if (cls === "TS") return "S";
    return (cls || "").replace("C", "") || "D";
  }
  function prefersReducedMotion() {
    try {
      return window.matchMedia &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (e) { return false; }
  }

  // Nature code -> phase-shape flags (mirrors the per-basin renderer's
  // tropical-circle / subtropical-square / non-tropical-triangle fork). TS / NR
  // (provisional current-season) read tropical; SS subtropical; everything else
  // (DS disturbance, EX extratropical, LO low, WV wave, ...) non-tropical.
  function natureFlags(nature) {
    var n = String(nature || "").toUpperCase();
    if (n === "SS") return { sub: true, non: false };
    if (n === "TS" || n === "NR" || n === "") return { sub: false, non: false };
    return { sub: false, non: true };
  }

  // ===================================================================
  // Storm object -> GeoJSON (sliced to a playhead index for the timeline)
  // ===================================================================
  // Builds the FeatureCollection the ported engine consumes: a track LineString
  // (solid for designated storms, dashed for invests/PTCs) + one observation
  // Point per fix (intensity-colored, phase-shaped). `upto` clips to the fixes
  // at-or-before the playhead (>= the whole track when upto is the last index),
  // so setData() animates the track reveal. The current/active glyph is an HTML
  // marker placed separately (not in this FC).
  function stormToGeoJSON(storm, upto) {
    var pts = (storm && storm.points) || [];
    var n = pts.length;
    if (upto == null || upto > n - 1) upto = n - 1;
    var isInvest = !!storm.is_invest, isPtc = !!storm.is_ptc;
    var feats = [];
    var line = [];
    for (var i = 0; i <= upto && i < n; i++) {
      var p = pts[i];
      if (p.lon == null || p.lat == null) continue;
      line.push([p.lon, p.lat]);
      var nf = natureFlags(p.nature);
      feats.push({
        type: "Feature",
        geometry: { type: "Point", coordinates: [p.lon, p.lat] },
        properties: {
          kind: "observation",
          storm_name: storm.name || "Storm",
          time_iso: p.t,
          intensity_kt: (p.wind_kt != null ? p.wind_kt : null),
          mslp_mb: (p.pressure_mb != null ? p.pressure_mb : null),
          sshws_cat: p.cls || "TD",
          is_subtropical: nf.sub,
          is_nontropical: nf.non
        }
      });
    }
    if (line.length >= 2) {
      feats.unshift({
        type: "Feature",
        geometry: { type: "LineString", coordinates: line },
        properties: { is_invest: isInvest, is_ptc: isPtc,
                      storm_name: storm.name || "Storm" }
      });
    }
    return { type: "FeatureCollection", features: feats };
  }

  // ===================================================================
  // CSS (injected once; scoped .clm-*; adopts CycloLab navy tokens via var())
  // ===================================================================
  var CSS = [
    ".clm{position:relative;display:flex;flex-direction:column;width:100%;",
      "background:var(--panel,#11161f);border:1px solid var(--border,#232a36);",
      "border-radius:10px;overflow:hidden;color:var(--fg,#e8eef5);",
      "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;}",
    ".clm-body{position:relative;display:flex;min-height:0;",
      "height:var(--clm-h,clamp(320px,56vh,560px));}",
    ".clm-map{position:relative;flex:1 1 auto;min-width:0;background:#0b2a48;}",
    ".clm-map canvas{outline:none;}",
    // rail
    ".clm-rail{flex:0 0 232px;max-width:62%;display:flex;flex-direction:column;",
      "background:var(--bg,#0b0e13);border-right:1px solid var(--border,#232a36);",
      "overflow:auto;font-size:12px;}",
    ".clm-rail-head{display:flex;align-items:center;gap:8px;padding:9px 11px;",
      "border-bottom:1px solid var(--border,#232a36);position:sticky;top:0;",
      "background:var(--bg,#0b0e13);z-index:2;}",
    ".clm-rail-head .clm-id{font-weight:800;letter-spacing:.04em;color:var(--fg,#e8eef5);}",
    ".clm-rail-head .clm-id small{display:block;font-weight:600;font-size:10px;",
      "letter-spacing:.06em;color:var(--muted,#8ea2bd);text-transform:uppercase;}",
    ".clm-grp{padding:6px 0 4px;}",
    ".clm-grp-h{display:flex;align-items:center;justify-content:space-between;",
      "padding:6px 11px 4px;font-size:10px;font-weight:800;letter-spacing:.09em;",
      "text-transform:uppercase;color:var(--muted,#8ea2bd);}",
    ".clm-row{display:flex;align-items:center;gap:8px;padding:6px 11px;",
      "border-left:2px solid transparent;cursor:pointer;}",
    ".clm-row.active{border-left-color:var(--cat-accent,#3fa4ff);",
      "background:rgba(63,164,255,.08);}",
    ".clm-row .clm-eye{flex:0 0 auto;width:26px;height:16px;border-radius:9px;",
      "background:#2a3343;position:relative;transition:background .15s;}",
    ".clm-row .clm-eye::after{content:'';position:absolute;top:2px;left:2px;",
      "width:12px;height:12px;border-radius:50%;background:#8ea2bd;transition:transform .15s,background .15s;}",
    ".clm-row.on .clm-eye{background:var(--cat-accent,#3fa4ff);}",
    ".clm-row.on .clm-eye::after{transform:translateX(10px);background:#fff;}",
    ".clm-row .clm-name{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;",
      "white-space:nowrap;color:var(--fg,#e8eef5);}",
    ".clm-row .clm-sw{flex:0 0 auto;width:11px;height:11px;border-radius:3px;}",
    ".clm-op{padding:0 11px 8px 41px;}",
    ".clm-op input[type=range]{width:100%;accent-color:var(--cat-accent,#3fa4ff);height:3px;}",
    ".clm-empty{padding:4px 11px 8px 11px;color:var(--muted,#8ea2bd);font-size:11px;font-style:italic;}",
    ".clm-rail-foot{margin-top:auto;padding:8px 11px;border-top:1px solid var(--border,#232a36);",
      "color:var(--muted,#8ea2bd);font-size:10px;line-height:1.45;}",
    // mobile rail drawer
    ".clm-railbtn{display:none;position:absolute;top:10px;left:10px;z-index:6;",
      "background:rgba(11,14,19,.88);color:var(--fg,#e8eef5);border:1px solid var(--border,#232a36);",
      "border-radius:8px;padding:6px 11px;font-size:12px;font-weight:700;cursor:pointer;}",
    // time control
    ".clm-time{display:flex;align-items:center;gap:10px;padding:8px 12px;",
      "border-top:1px solid var(--border,#232a36);background:var(--bg,#0b0e13);}",
    ".clm-tbtns{display:flex;align-items:center;gap:4px;flex:0 0 auto;}",
    ".clm-tb{background:var(--panel,#11161f);border:1px solid var(--border,#232a36);",
      "color:var(--fg,#e8eef5);border-radius:7px;width:30px;height:28px;cursor:pointer;",
      "font-size:13px;display:flex;align-items:center;justify-content:center;line-height:1;}",
    ".clm-tb:hover{border-color:var(--cat-accent,#3fa4ff);}",
    ".clm-tb.on{background:var(--cat-accent,#3fa4ff);color:#06121f;border-color:transparent;}",
    ".clm-scrub{flex:1 1 auto;min-width:0;position:relative;}",
    ".clm-scrub input[type=range]{width:100%;accent-color:var(--cat-accent,#3fa4ff);}",
    ".clm-ticks{position:relative;height:6px;margin-top:-2px;}",
    ".clm-ticks i{position:absolute;top:0;width:2px;height:6px;border-radius:1px;transform:translateX(-1px);}",
    ".clm-valid{flex:0 0 auto;font-size:11px;font-variant-numeric:tabular-nums;",
      "color:var(--fg,#e8eef5);min-width:118px;text-align:right;}",
    ".clm-valid small{color:var(--muted,#8ea2bd);}",
    ".clm-speed{flex:0 0 auto;background:var(--panel,#11161f);color:var(--fg,#e8eef5);",
      "border:1px solid var(--border,#232a36);border-radius:7px;font-size:11px;padding:4px 6px;}",
    // active-storm map markers (verbatim look from global_tracks.html)
    ".clm .active-marker{position:absolute;transform:translate(-50%,-50%);pointer-events:none;}",
    ".clm .active-marker svg{display:block;overflow:visible;width:100%;height:100%;}",
    ".clm .active-marker.active-hurricane{width:64px;height:64px;}",
    ".clm .active-marker.active-hurricane svg{filter:drop-shadow(0 0 6px currentColor);}",
    "@keyframes clm-spin{from{transform:rotate(360deg);}to{transform:rotate(0);}}",
    ".clm .active-marker .spinning{animation:clm-spin 2.6s linear infinite;",
      "transform-origin:50% 50%;transform-box:fill-box;}",
    ".clm .active-marker .hurricane-label{font-size:14px;font-weight:900;fill:#fff;",
      "paint-order:stroke;stroke:rgba(0,0,0,.55);stroke-width:1.8;stroke-linejoin:round;}",
    ".clm .active-marker.invest-x-marker{width:30px;height:30px;}",
    ".clm .active-marker .invest-label{fill:#ff5050;font-size:15px;font-weight:700;",
      "paint-order:stroke;stroke:#07101c;stroke-width:3;stroke-linejoin:round;dominant-baseline:middle;}",
    // maplibre chrome -> navy
    ".clm .maplibregl-ctrl-group{background:rgba(11,14,19,.9)!important;",
      "border:1px solid var(--border,#232a36)!important;}",
    ".clm .maplibregl-ctrl-group button{background-color:transparent!important;}",
    ".clm .maplibregl-ctrl-icon{filter:invert(.85);}",
    ".clm .maplibregl-popup-tip{display:none!important;}",
    ".clm .maplibregl-popup-content{background:rgba(10,18,34,.96)!important;color:var(--fg,#e8eef5)!important;",
      "border:1px solid #2a3e5c;border-radius:8px;padding:8px 12px!important;font-size:12px;line-height:1.45;}",
    ".clm .tt-name{font-weight:800;color:#f1f7fd;font-size:13px;}",
    ".clm .tt-time{color:var(--muted,#8ea2bd);font-size:11px;margin-bottom:4px;}",
    ".clm .tt-row{display:flex;justify-content:space-between;gap:10px;margin-top:2px;}",
    ".clm .tt-lbl{color:var(--muted,#8ea2bd);}",
    ".clm .tt-val{color:var(--fg,#e8eef5);font-variant-numeric:tabular-nums;}",
    ".clm .tt-cat{display:inline-block;padding:1px 8px;border-radius:999px;font-size:10px;font-weight:700;color:#07101c;}",
    // responsive
    "@media (max-width:720px){",
      ".clm-rail{position:absolute;top:0;left:0;bottom:0;z-index:5;width:80%;max-width:300px;",
        "transform:translateX(-102%);transition:transform .2s;box-shadow:4px 0 18px rgba(0,0,0,.5);}",
      ".clm.rail-open .clm-rail{transform:translateX(0);}",
      ".clm-railbtn{display:block;}",
      ".clm-valid{min-width:0;}",
      ".clm-speed{display:none;}",
    "}",
    "@media (prefers-reduced-motion: reduce){.clm .active-marker .spinning{animation:none;}}"
  ].join("\n");

  function injectCss() {
    if (document.getElementById("cyclolab-map-css")) return;
    var st = document.createElement("style");
    st.id = "cyclolab-map-css";
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  // ===================================================================
  // MapLibre lazy loader (self-contained; no page-level dependency)
  // ===================================================================
  var _mlState = 0;   // 0 idle, 1 loading, 2 ready
  var _mlWaiters = [];
  function ensureMaplibre(cb) {
    if (window.maplibregl) { cb(); return; }
    _mlWaiters.push(cb);
    if (_mlState === 1) return;
    _mlState = 1;
    if (!document.querySelector('link[data-clm-ml]')) {
      var lk = document.createElement("link");
      lk.rel = "stylesheet"; lk.href = MAPLIBRE_CSS; lk.setAttribute("data-clm-ml", "1");
      document.head.appendChild(lk);
    }
    var s = document.createElement("script");
    s.src = MAPLIBRE_JS;
    s.onload = function () {
      _mlState = 2;
      _mlWaiters.splice(0).forEach(function (f) { try { f(); } catch (e) {} });
    };
    s.onerror = function () {
      _mlState = 0;
      _mlWaiters.splice(0).forEach(function (f) { try { f(); } catch (e) {} });
    };
    document.head.appendChild(s);
  }

  // ===================================================================
  // Active-storm glyph markers (verbatim look from global_tracks.html)
  // ===================================================================
  var _investSeq = 0;
  function buildActiveMarkerEl(storm) {
    var el = document.createElement("div");
    el.className = "active-marker";
    var designation = String(storm.name || "").toUpperCase();
    if (storm.is_invest || storm.is_ptc) {
      el.classList.add("invest-x-marker");
      var fid = "clm-invest-glow-" + (++_investSeq);
      el.innerHTML =
        '<svg viewBox="-15 -15 30 30" xmlns="http://www.w3.org/2000/svg">' +
          '<defs><filter id="' + fid + '" x="-200%" y="-200%" width="500%" height="500%">' +
            '<feGaussianBlur in="SourceAlpha" stdDeviation="3.2" result="blur"/>' +
            '<feFlood flood-color="#ff0000" flood-opacity="0.95" result="red"/>' +
            '<feComposite in="red" in2="blur" operator="in" result="redblur"/>' +
            '<feMerge><feMergeNode in="redblur"/><feMergeNode in="redblur"/>' +
              '<feMergeNode in="SourceGraphic"/></feMerge></filter></defs>' +
          '<g filter="url(#' + fid + ')">' +
            '<path d="M -7 -7 L 7 7 M -7 7 L 7 -7" stroke="#ff2a2a" stroke-width="2.4" ' +
              'stroke-linecap="round" fill="none"/></g>' +
          '<text class="invest-label" x="11" y="3" text-anchor="start">' +
            escapeHtml(designation) + '</text>' +
        '</svg>';
      return el;
    }
    el.classList.add("active-hurricane");
    var cls = storm.current_category || "TD";
    var color = SSHS_COLORS[cls] || "#888";
    el.style.color = color;
    el.innerHTML =
      '<svg viewBox="-34 -34 68 68" xmlns="http://www.w3.org/2000/svg">' +
        '<g transform="scale(0.7)"><g class="spinning">' +
          '<path d="' + HURRICANE_PATH + '" fill="' + color + '"/>' +
        '</g></g>' +
        '<text class="hurricane-label" x="0" y="0" text-anchor="middle" ' +
          'dominant-baseline="central">' + sshsLabel(cls) + '</text>' +
      '</svg>';
    return el;
  }

  // ===================================================================
  // The module
  // ===================================================================
  function CycloLabMap(root, opts) {
    if (!root) throw new Error("CycloLabMap: root element required");
    opts = opts || {};
    this.root = root;
    this.storm = opts.storm || { points: [] };
    this.map = null;
    this.ready = false;
    this.activeMarker = null;
    this.playhead = 0;          // index into points
    this.playing = false;
    this._raf = null;
    this._lastStep = 0;
    this.speedMs = 700;
    this.loop = true;
    this.layers = [];           // [{id,group,label,type,visible,opacity,...}]
    this.activeLayerId = "track";
    this._reduced = prefersReducedMotion();
    this.dom = {};
    injectCss();
    this._buildDom();
    var self = this;
    ensureMaplibre(function () { self._initMap(); });
  }
  var P = CycloLabMap.prototype;

  // ---- DOM scaffold (rail + map + time bar) ----
  P._buildDom = function () {
    var r = this.root;
    r.classList.add("clm");
    r.innerHTML = "";
    var body = document.createElement("div"); body.className = "clm-body";
    var rail = document.createElement("div"); rail.className = "clm-rail";
    var mapWrap = document.createElement("div"); mapWrap.className = "clm-map";
    var railBtn = document.createElement("button");
    railBtn.className = "clm-railbtn"; railBtn.type = "button";
    railBtn.textContent = "☰ Layers";
    var self = this;
    railBtn.addEventListener("click", function () { r.classList.toggle("rail-open"); });
    body.appendChild(rail); body.appendChild(mapWrap); mapWrap.appendChild(railBtn);
    r.appendChild(body);

    var time = document.createElement("div"); time.className = "clm-time";
    r.appendChild(time);

    this.dom.body = body; this.dom.rail = rail; this.dom.mapWrap = mapWrap;
    this.dom.time = time;

    // The track layer is the always-present base layer (Tracks group, active).
    this.layers.push({
      id: "track", group: "tracks", label: "Storm track",
      type: "track", visible: true, opacity: 1, swatch: "#ffffff"
    });
    this._buildRail();
    this._buildTime();
  };

  // ---- MapLibre init (ported engine) ----
  P._initMap = function () {
    if (!window.maplibregl) {
      this.dom.mapWrap.insertAdjacentHTML("beforeend",
        '<div style="position:absolute;inset:0;display:flex;align-items:center;' +
        'justify-content:center;color:#8ea2bd;font-size:13px;">Map unavailable ' +
        '(could not load MapLibre).</div>');
      return;
    }
    var self = this;
    var fc = stormToGeoJSON(this.storm, (this.storm.points || []).length - 1);
    this._fullFc = fc;
    var center = this._stormCenter();
    this.map = new maplibregl.Map({
      container: this.dom.mapWrap,
      style: STYLE,
      center: center.center,
      zoom: center.zoom,
      minZoom: 1, maxZoom: 14,
      renderWorldCopies: true,
      attributionControl: false,
      cooperativeGestures: true
    });
    this.map.addControl(new maplibregl.NavigationControl({
      visualizePitch: false, showCompass: false
    }), "top-right");
    this.map.dragRotate.disable();
    this.map.touchZoomRotate.disableRotation();

    this.map.on("load", function () {
      self._registerPhaseIcons();
      self._addStormLayers(fc);
      self._fitToTrack();
      self._setPlayhead((self.storm.points || []).length - 1, true);
      self.ready = true;
      // Re-apply any raster layers requested before load (deferred-safe).
      self.layers.forEach(function (L) {
        if (L.type === "raster" && !L._added) self._mountRaster(L);
      });
    });
  };

  P._stormCenter = function () {
    var pts = (this.storm.points || []).filter(function (p) {
      return p.lat != null && p.lon != null;
    });
    if (!pts.length) return { center: [180, 10], zoom: 1.5 };
    var last = pts[pts.length - 1];
    return { center: [last.lon, last.lat], zoom: 4 };
  };

  P._fitToTrack = function () {
    var pts = (this.storm.points || []).filter(function (p) {
      return p.lat != null && p.lon != null;
    });
    if (pts.length < 2 || !this.map) return;
    var lons = pts.map(function (p) { return p.lon; });
    var lats = pts.map(function (p) { return p.lat; });
    var w = Math.min.apply(null, lons), e = Math.max.apply(null, lons);
    var s = Math.min.apply(null, lats), nn = Math.max.apply(null, lats);
    try {
      this.map.fitBounds([[w, s], [e, nn]], {
        padding: { top: 50, bottom: 40, left: 40, right: 60 },
        maxZoom: 7, duration: 0
      });
    } catch (e2) {}
  };

  // ---- ported addStormLayers (track line + phase-shaped obs + popups) ----
  P._addStormLayers = function (geojson) {
    var map = this.map;
    map.addSource("storms", { type: "geojson", data: geojson });

    map.addLayer({
      id: "tracks-line-solid", type: "line", source: "storms",
      filter: ["all", ["==", ["geometry-type"], "LineString"],
        ["!=", ["get", "is_invest"], true], ["!=", ["get", "is_ptc"], true]],
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": "#ffffff", "line-opacity": 0.55, "line-width": 1.6 }
    });
    map.addLayer({
      id: "tracks-line-invest", type: "line", source: "storms",
      filter: ["all", ["==", ["geometry-type"], "LineString"],
        ["any", ["==", ["get", "is_invest"], true], ["==", ["get", "is_ptc"], true]]],
      layout: { "line-join": "round", "line-cap": "round" },
      paint: { "line-color": "#ffffff", "line-opacity": 0.55, "line-width": 1.6,
        "line-dasharray": [4, 3] }
    });
    map.addLayer({
      id: "observations-tropical", type: "circle", source: "storms",
      filter: ["all", ["==", ["geometry-type"], "Point"],
        ["==", ["get", "kind"], "observation"],
        ["!=", ["get", "is_subtropical"], true],
        ["!=", ["get", "is_nontropical"], true]],
      paint: {
        "circle-color": COLOR_STEP, "circle-radius": ZOOM_RADIUS,
        "circle-stroke-color": ["step", ["coalesce", ["get", "intensity_kt"], 0],
          "rgba(63,164,255,0)", 34, "#ffffff"],
        "circle-stroke-width": ["step", ["coalesce", ["get", "intensity_kt"], 0], 0, 34, 0.5],
        "circle-stroke-opacity": 0.7
      }
    });
    map.addLayer({
      id: "observations-subtropical", type: "symbol", source: "storms",
      filter: ["all", ["==", ["geometry-type"], "Point"],
        ["==", ["get", "kind"], "observation"], ["==", ["get", "is_subtropical"], true]],
      layout: { "icon-image": "phase-square", "icon-size": ZOOM_ICON_SIZE,
        "icon-allow-overlap": true, "icon-ignore-placement": true },
      paint: { "icon-color": COLOR_STEP, "icon-halo-color": "#ffffff", "icon-halo-width": 1.0 }
    });
    map.addLayer({
      id: "observations-nontropical", type: "symbol", source: "storms",
      filter: ["all", ["==", ["geometry-type"], "Point"],
        ["==", ["get", "kind"], "observation"], ["==", ["get", "is_nontropical"], true]],
      layout: { "icon-image": "phase-triangle", "icon-size": ZOOM_ICON_SIZE,
        "icon-allow-overlap": true, "icon-ignore-placement": true },
      paint: { "icon-color": COLOR_STEP, "icon-halo-color": "#ffffff", "icon-halo-width": 1.0 }
    });
    this._wireObsPopups();
  };

  P._wireObsPopups = function () {
    var map = this.map, popup = null;
    var OBS = ["observations-tropical", "observations-subtropical", "observations-nontropical"];
    function enter(e) {
      map.getCanvas().style.cursor = "pointer";
      var f = e.features[0], props = f.properties || {};
      var coords = f.geometry.coordinates.slice();
      while (e.lngLat.lng - coords[0] > 180) coords[0] += 360;
      while (e.lngLat.lng - coords[0] < -180) coords[0] -= 360;
      var kt = props.intensity_kt, pres = props.mslp_mb, cls = props.sshws_cat || "TD";
      var color = SSHS_COLORS[cls] || "#888";
      var windTxt = (kt != null && kt !== "" && !isNaN(parseFloat(kt)))
        ? (Math.round(parseFloat(kt)) + " kt &middot; " + ktToMph5(parseFloat(kt)) + " mph") : "-";
      var presTxt = (pres != null && pres !== "" && !isNaN(parseFloat(pres)))
        ? (Math.round(parseFloat(pres)) + " mb") : "-";
      var html = '<div class="tt-name">' + escapeHtml(props.storm_name || "Storm") + '</div>' +
        '<div class="tt-time">' + fmtTime(props.time_iso) + '</div>' +
        '<div class="tt-row"><span class="tt-cat" style="background:' + color + '">' +
          (CAT_LABELS[cls] || cls) + '</span></div>' +
        '<div class="tt-row"><span class="tt-lbl">Wind</span><span class="tt-val">' + windTxt + '</span></div>' +
        '<div class="tt-row"><span class="tt-lbl">Pressure</span><span class="tt-val">' + presTxt + '</span></div>';
      if (popup) popup.remove();
      popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 8, maxWidth: "240px" })
        .setLngLat(coords).setHTML(html).addTo(map);
    }
    function move(e) {
      if (!popup) return;
      var coords = e.features[0].geometry.coordinates.slice();
      while (e.lngLat.lng - coords[0] > 180) coords[0] += 360;
      while (e.lngLat.lng - coords[0] < -180) coords[0] -= 360;
      popup.setLngLat(coords);
    }
    function leave() {
      map.getCanvas().style.cursor = "";
      if (popup) { popup.remove(); popup = null; }
    }
    OBS.forEach(function (id) {
      map.on("mouseenter", id, enter); map.on("mousemove", id, move); map.on("mouseleave", id, leave);
    });
  };

  // ---- SDF phase icons (verbatim) ----
  P._registerPhaseIcons = function () {
    var map = this.map, size = 24;
    function makeShape(draw) {
      var c = document.createElement("canvas"); c.width = size; c.height = size;
      var ctx = c.getContext("2d"); ctx.fillStyle = "#000"; draw(ctx, size);
      return ctx.getImageData(0, 0, size, size);
    }
    if (!map.hasImage("phase-square")) {
      map.addImage("phase-square", makeShape(function (ctx, s) {
        var pad = 4; ctx.fillRect(pad, pad, s - 2 * pad, s - 2 * pad);
      }), { sdf: true });
    }
    if (!map.hasImage("phase-triangle")) {
      map.addImage("phase-triangle", makeShape(function (ctx, s) {
        var pad = 3; ctx.beginPath(); ctx.moveTo(s / 2, pad);
        ctx.lineTo(s - pad, s - pad); ctx.lineTo(pad, s - pad); ctx.closePath(); ctx.fill();
      }), { sdf: true });
    }
  };

  // ===================================================================
  // RASTER FRAMEWORK (deferred: ready to receive sat / MW / model frames)
  // ===================================================================
  // addRasterLayer({id, group:'imagery'|'fields', label, frames:[{url,corners,time}],
  //   opacity, subProducts}) - corners = [TL,TR,BR,BL] each [lon,lat] (WGS84).
  // No layers are wired in P1; this is the producer-facing entry point so sat /
  // microwave / model imagery light up additively once chrome-free georeferenced
  // tiles exist. Frame swapping uses source.updateImage() (zero re-add cost).
  P.addRasterLayer = function (def) {
    if (!def || !def.id) return;
    var L = {
      id: def.id, group: def.group === "fields" ? "fields" : "imagery",
      label: def.label || def.id, type: "raster",
      visible: def.visible !== false, opacity: (def.opacity != null ? def.opacity : 1),
      frames: def.frames || [], activeFrame: 0, swatch: def.swatch || "#5dd3ff",
      subProducts: def.subProducts || null, _added: false
    };
    this.layers.push(L);
    if (this.ready && this.map) this._mountRaster(L);
    this._buildRail();
    return L;
  };

  P._mountRaster = function (L) {
    if (!this.map || !L.frames.length) return;
    var fr = L.frames[L.activeFrame] || L.frames[0];
    var srcId = "clm-raster-" + L.id;
    try {
      if (!this.map.getSource(srcId)) {
        this.map.addSource(srcId, { type: "image", url: fr.url, coordinates: fr.corners });
        this.map.addLayer({
          id: srcId + "-layer", type: "raster", source: srcId,
          paint: { "raster-opacity": L.opacity, "raster-fade-duration": 0 }
        }, this.map.getLayer(TRACKS_BEFORE) ? TRACKS_BEFORE : undefined);
      }
      this.map.setLayoutProperty(srcId + "-layer", "visibility", L.visible ? "visible" : "none");
      L._added = true;
    } catch (e) {}
  };

  P.setActiveFrame = function (id, idx) {
    var L = this._layer(id); if (!L || L.type !== "raster") return;
    L.activeFrame = idx;
    var fr = L.frames[idx]; if (!fr || !this.map) return;
    var src = this.map.getSource("clm-raster-" + L.id);
    if (src && src.updateImage) src.updateImage({ url: fr.url, coordinates: fr.corners });
  };

  // ===================================================================
  // Layer controls (toggle + opacity now; z-order/drag + persistence in P2)
  // ===================================================================
  P._layer = function (id) {
    for (var i = 0; i < this.layers.length; i++) if (this.layers[i].id === id) return this.layers[i];
    return null;
  };
  P.toggleLayer = function (id, on) {
    var L = this._layer(id); if (!L) return;
    L.visible = (on == null) ? !L.visible : !!on;
    this._applyVisibility(L);
    this._buildRail();
  };
  P._applyVisibility = function (L) {
    if (!this.map) return;
    var vis = L.visible ? "visible" : "none";
    var ids = (L.type === "track")
      ? ["tracks-line-solid", "tracks-line-invest", "observations-tropical",
         "observations-subtropical", "observations-nontropical"]
      : ["clm-raster-" + L.id + "-layer"];
    ids.forEach(function (lid) {
      if (this.map.getLayer(lid)) this.map.setLayoutProperty(lid, "visibility", vis);
    }, this);
    if (L.type === "track" && this.activeMarker) {
      var elx = this.activeMarker.getElement();
      if (elx) elx.style.display = L.visible ? "" : "none";
    }
  };
  P.setLayerOpacity = function (id, op) {
    var L = this._layer(id); if (!L) return;
    L.opacity = Math.max(0, Math.min(1, op));
    if (!this.map) return;
    if (L.type === "track") {
      ["tracks-line-solid", "tracks-line-invest"].forEach(function (lid) {
        if (this.map.getLayer(lid)) this.map.setPaintProperty(lid, "line-opacity", 0.55 * L.opacity);
      }, this);
      ["observations-tropical", "observations-subtropical", "observations-nontropical"].forEach(function (lid) {
        if (this.map.getLayer(lid)) this.map.setPaintProperty(lid,
          lid === "observations-tropical" ? "circle-opacity" : "icon-opacity", L.opacity);
      }, this);
      if (this.activeMarker) {
        var elx = this.activeMarker.getElement(); if (elx) elx.style.opacity = L.opacity;
      }
    } else {
      var lid2 = "clm-raster-" + L.id + "-layer";
      if (this.map.getLayer(lid2)) this.map.setPaintProperty(lid2, "raster-opacity", L.opacity);
    }
  };
  P.setActiveLayer = function (id) {
    if (!this._layer(id)) return;
    this.activeLayerId = id;
    this._buildRail();
  };

  // ---- explorer rail render ----
  P._buildRail = function () {
    var rail = this.dom.rail; if (!rail) return;
    var self = this;
    var s = this.storm;
    var cls = s.current_category || (s.max_category || "TD");
    var color = SSHS_COLORS[cls] || "#3fa4ff";
    var idTxt = escapeHtml(s.name || s.atcf_id || s.sid || "Storm");
    var sub = escapeHtml([s.basin_label || "", s.season || s.year || ""].filter(Boolean).join(" · ")) ||
      (s.is_invest ? "Invest" : "Tropical cyclone");
    var html = '<div class="clm-rail-head">' +
      '<span class="clm-sw" style="width:13px;height:13px;border-radius:50%;background:' + color + '"></span>' +
      '<span class="clm-id">' + idTxt + '<small>' + sub + '</small></span></div>';

    var GROUPS = [
      { key: "imagery", label: "Imagery" },
      { key: "fields", label: "Fields" },
      { key: "tracks", label: "Tracks" }
    ];
    GROUPS.forEach(function (g) {
      var rows = self.layers.filter(function (L) { return L.group === g.key; });
      html += '<div class="clm-grp"><div class="clm-grp-h"><span>' + g.label + '</span></div>';
      if (!rows.length) {
        html += '<div class="clm-empty">' +
          (g.key === "tracks" ? "No track" :
            "No layers yet — satellite & model imagery arrive via a producer update.") +
          '</div>';
      }
      rows.forEach(function (L) {
        var active = (L.id === self.activeLayerId);
        html += '<div class="clm-row' + (L.visible ? " on" : "") + (active ? " active" : "") +
          '" data-id="' + L.id + '">' +
          '<span class="clm-eye" data-act="toggle" data-id="' + L.id + '"></span>' +
          '<span class="clm-sw" style="background:' + (L.swatch || "#5dd3ff") + '"></span>' +
          '<span class="clm-name">' + escapeHtml(L.label) + '</span></div>';
        html += '<div class="clm-op"><input type="range" min="0" max="100" value="' +
          Math.round(L.opacity * 100) + '" data-act="opacity" data-id="' + L.id + '"></div>';
      });
      html += '</div>';
    });
    html += '<div class="clm-rail-foot">Drag map to pan · Ctrl/⌘+scroll to zoom. ' +
      'Imagery & model layers stack here when published.</div>';
    rail.innerHTML = html;

    // wire rows
    rail.querySelectorAll('[data-act="toggle"]').forEach(function (eye) {
      eye.addEventListener("click", function (ev) {
        ev.stopPropagation(); self.toggleLayer(eye.getAttribute("data-id"));
      });
    });
    rail.querySelectorAll('[data-act="opacity"]').forEach(function (sl) {
      sl.addEventListener("input", function () {
        self.setLayerOpacity(sl.getAttribute("data-id"), parseInt(sl.value, 10) / 100);
      });
    });
    rail.querySelectorAll('.clm-row').forEach(function (row) {
      row.addEventListener("click", function () { self.setActiveLayer(row.getAttribute("data-id")); });
    });
  };

  // ===================================================================
  // TIME control (master timeline over track fixes)
  // ===================================================================
  P._buildTime = function () {
    var t = this.dom.time; if (!t) return;
    var self = this;
    var n = (this.storm.points || []).length;
    t.innerHTML =
      '<div class="clm-tbtns">' +
        '<button class="clm-tb" data-act="step-" title="Step back">◀</button>' +
        '<button class="clm-tb" data-act="play" title="Play / pause">▶</button>' +
        '<button class="clm-tb" data-act="step+" title="Step forward">▶▶</button>' +
        '<button class="clm-tb" data-act="loop" title="Loop">↻</button>' +
      '</div>' +
      '<div class="clm-scrub"><input type="range" min="0" max="' + Math.max(0, n - 1) +
        '" value="' + Math.max(0, n - 1) + '" data-act="scrub">' +
        '<div class="clm-ticks"></div></div>' +
      '<select class="clm-speed" data-act="speed">' +
        '<option value="1200">0.5×</option>' +
        '<option value="700" selected>1×</option>' +
        '<option value="380">2×</option>' +
        '<option value="180">4×</option></select>' +
      '<div class="clm-valid"><span data-act="valid">—</span></div>';
    this.dom.scrub = t.querySelector('[data-act="scrub"]');
    this.dom.validEl = t.querySelector('[data-act="valid"]');
    this.dom.playBtn = t.querySelector('[data-act="play"]');
    this.dom.loopBtn = t.querySelector('[data-act="loop"]');
    if (this.loop) this.dom.loopBtn.classList.add("on");
    this._buildTicks();

    t.querySelector('[data-act="step-"]').addEventListener("click", function () { self.step(-1); });
    t.querySelector('[data-act="step+"]').addEventListener("click", function () { self.step(1); });
    this.dom.playBtn.addEventListener("click", function () { self.toggle(); });
    this.dom.loopBtn.addEventListener("click", function () {
      self.loop = !self.loop; self.dom.loopBtn.classList.toggle("on", self.loop);
    });
    this.dom.scrub.addEventListener("input", function () {
      self.pause(); self._setPlayhead(parseInt(self.dom.scrub.value, 10));
    });
    t.querySelector('[data-act="speed"]').addEventListener("change", function (e) {
      self.speedMs = parseInt(e.target.value, 10) || 700;
    });
  };

  P._buildTicks = function () {
    var box = this.dom.time.querySelector(".clm-ticks");
    if (!box) return;
    var pts = this.storm.points || [];
    var n = pts.length; if (n < 2) { box.innerHTML = ""; return; }
    var html = "";
    for (var i = 0; i < n; i++) {
      var p = pts[i];
      var color = SSHS_COLORS[p.cls || "TD"] || "#3fa4ff";
      var pct = (i / (n - 1)) * 100;
      html += '<i style="left:' + pct.toFixed(2) + '%;background:' + color + '"></i>';
    }
    box.innerHTML = html;
  };

  P._setPlayhead = function (idx, silent) {
    var pts = this.storm.points || [];
    var n = pts.length; if (!n) return;
    idx = Math.max(0, Math.min(n - 1, idx));
    this.playhead = idx;
    if (this.map && this.map.getSource("storms")) {
      this.map.getSource("storms").setData(stormToGeoJSON(this.storm, idx));
    }
    this._placeActiveGlyph(idx);
    if (this.dom.scrub && !silent) this.dom.scrub.value = String(idx);
    if (this.dom.scrub && silent) this.dom.scrub.value = String(idx);
    var p = pts[idx] || {};
    if (this.dom.validEl) {
      var cls = p.cls || "TD";
      this.dom.validEl.innerHTML = fmtTime(p.t) +
        ' <small>· ' + (p.wind_kt != null ? Math.round(p.wind_kt) + " kt" : "—") +
        ' ' + sshsLabel(cls) + '</small>';
    }
  };

  // Place / move the spinning glyph (or invest X) at the playhead fix. The glyph
  // reflects the CURRENT-stage category at that fix (cls), matching the global map.
  P._placeActiveGlyph = function (idx) {
    if (!this.map || !window.maplibregl) return;
    var pts = this.storm.points || [];
    var p = pts[idx]; if (!p || p.lat == null || p.lon == null) return;
    var glyphStorm = {
      name: this.storm.name, is_invest: this.storm.is_invest, is_ptc: this.storm.is_ptc,
      current_category: p.cls || "TD"
    };
    var el = buildActiveMarkerEl(glyphStorm);
    if (this._reduced) {
      var sp = el.querySelector(".spinning"); if (sp) sp.classList.remove("spinning");
    }
    if (this.activeMarker) this.activeMarker.remove();
    this.activeMarker = new maplibregl.Marker({ element: el, anchor: "center" })
      .setLngLat([p.lon, p.lat]).addTo(this.map);
    // honor the track layer's visibility/opacity on the marker
    var tl = this._layer("track");
    if (tl) { el.style.display = tl.visible ? "" : "none"; el.style.opacity = tl.opacity; }
  };

  // ---- transport ----
  P.toggle = function () { this.playing ? this.pause() : this.play(); };
  P.play = function () {
    var n = (this.storm.points || []).length; if (n < 2) return;
    if (this.playhead >= n - 1) this._setPlayhead(0);
    this.playing = true;
    if (this.dom.playBtn) { this.dom.playBtn.textContent = "⎉"; this.dom.playBtn.classList.add("on"); }
    var self = this; this._lastStep = 0;
    var tick = function (ts) {
      if (!self.playing) return;
      if (!self._lastStep) self._lastStep = ts;
      if (ts - self._lastStep >= self.speedMs) {
        self._lastStep = ts;
        var n2 = (self.storm.points || []).length;
        if (self.playhead >= n2 - 1) {
          if (self.loop) self._setPlayhead(0);
          else { self.pause(); return; }
        } else {
          self._setPlayhead(self.playhead + 1);
        }
      }
      self._raf = requestAnimationFrame(tick);
    };
    this._raf = requestAnimationFrame(tick);
  };
  P.pause = function () {
    this.playing = false;
    if (this._raf) { cancelAnimationFrame(this._raf); this._raf = null; }
    if (this.dom.playBtn) { this.dom.playBtn.textContent = "▶"; this.dom.playBtn.classList.remove("on"); }
  };
  P.step = function (d) { this.pause(); this._setPlayhead(this.playhead + d); };

  // ---- lifecycle (paused on tab hide; CycloLab calls these) ----
  P._pause = function () { this._wasPlaying = this.playing; this.pause(); };
  P._resume = function () { if (this._wasPlaying) this.play(); };
  P.resize = function () { if (this.map) this.map.resize(); };
  P.destroy = function () {
    this.pause();
    if (this.activeMarker) { this.activeMarker.remove(); this.activeMarker = null; }
    if (this.map) { try { this.map.remove(); } catch (e) {} this.map = null; }
  };

  // ===================================================================
  // Exports + standalone auto-mount
  // ===================================================================
  if (typeof window !== "undefined") window.CycloLabMap = CycloLabMap;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { CycloLabMap: CycloLabMap, stormToGeoJSON: stormToGeoJSON,
      natureFlags: natureFlags };
  }
  if (typeof document !== "undefined" && document.addEventListener) {
    document.addEventListener("DOMContentLoaded", function () {
      var el = document.getElementById("cyclolab-map-standalone");
      if (!el) return;   // absent inside CycloLab -> manual mount only, no double-boot
      var storm = null;
      try { storm = JSON.parse(el.getAttribute("data-storm") || "null"); } catch (e) {}
      if (storm) el.__clMap = new CycloLabMap(el, { storm: storm });
    });
  }
})();
