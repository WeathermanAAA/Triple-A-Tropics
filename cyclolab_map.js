/* cyclolab_map.js - reusable stacking-map module (CycloLab revamp, Phase 1).
 *
 * Ports the canonical MapLibre engine from global_tracks.html (inline Protomaps
 * vector STYLE - no Mapbox token; storm track + intensity-colored observation
 * shapes + hover popups) and the spinning hurricane glyph from active-banner.js
 * (HURRICANE_PATH + the D/S/1-5 label, the shared SSHWS palette), and adds:
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
  // Canonical SSHWS palette + thresholds (tat_palette.js, generated from
  // palette/tat_palettes/categories.py). The CycloLab shell loads it ahead of
  // this component; no local fallback copy, which is what let this file's ramp
  // drift from the home map's in the first place.
  function TATP() {
    var p = window.TATPalette;
    if (!p) throw new Error("cyclolab_map.js: load /tat_palette.js first");
    return p;
  }
  // SSHWS hue for a track fix: prefer an explicit valid `cls`, else derive from
  // wind_kt so the tick reads as the intensity history (the reported bug: fixes
  // lacking `cls` all defaulted to TD-blue).
  function fixCatColor(p) {
    var pal = TATP();
    if (p && p.cls && pal.cats[p.cls]) return pal.cats[p.cls];
    return pal.colorForKt(p && p.wind_kt != null ? +p.wind_kt : 0);
  }
  function _lsGet(k, d) { try { return localStorage.getItem(k) || d; } catch (e) { return d; } }
  function _lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  // Draw preset swatches (Andrew's palette).
  var DRAW_SWATCHES = ["#e24b4a", "#ef9f27", "#ffd400", "#46c46a",
                       "#5dd3ff", "#378add", "#cf4fd6", "#ffffff"];
  var DRAW_SHAPES = [
    { id: "select", label: "Select / move", icon: "✥" },
    { id: "dot", label: "Dot", icon: "●" },
    { id: "circle", label: "Circle", icon: "◯" },
    { id: "square", label: "Square", icon: "■" },
    { id: "triangle", label: "Triangle", icon: "▲" },
    { id: "x", label: "X", icon: "✕" },
    { id: "arrow", label: "Arrow", icon: "➤" },
    { id: "freehand", label: "Freehand", icon: "〜" }
  ];
  // Shape geometry generators (placement-relative size in degrees).
  function _sqCoords(lng, lat, s) {
    return [[lng - s, lat - s], [lng + s, lat - s], [lng + s, lat + s],
            [lng - s, lat + s], [lng - s, lat - s]];
  }
  function _triCoords(lng, lat, s) {
    return [[lng, lat + s], [lng + s * 0.92, lat - s * 0.7],
            [lng - s * 0.92, lat - s * 0.7], [lng, lat + s]];
  }
  function _xCoords(lng, lat, s) {
    return [[[lng - s, lat - s], [lng + s, lat + s]],
            [[lng - s, lat + s], [lng + s, lat - s]]];
  }
  function _arrowCoords(a, b) {     // a=tail, b=head -> shaft + V head as one line
    var dx = b[0] - a[0], dy = b[1] - a[1];
    var ang = Math.atan2(dy, dx), len = Math.hypot(dx, dy);
    var hl = Math.max(0.06, len * 0.24), wa = 0.5;
    var l = [b[0] - hl * Math.cos(ang - wa), b[1] - hl * Math.sin(ang - wa)];
    var r = [b[0] - hl * Math.cos(ang + wa), b[1] - hl * Math.sin(ang + wa)];
    return [a, b, l, b, r];
  }
  // Translate any draw geometry by (dLng, dLat) for the move-drag.
  function _translateGeom(g, dx, dy) {
    function tp(c) { return [c[0] + dx, c[1] + dy]; }
    if (g.type === "Point") g.coordinates = tp(g.coordinates);
    else if (g.type === "LineString") g.coordinates = g.coordinates.map(tp);
    else if (g.type === "MultiLineString") g.coordinates = g.coordinates.map(function (l) { return l.map(tp); });
    else if (g.type === "Polygon") g.coordinates = g.coordinates.map(function (r) { return r.map(tp); });
  }
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

  // Shared paint expressions (the same ramp global_tracks.html bakes).
  // A function, not a constant: the palette global is guaranteed present by
  // the time a layer is added, but not necessarily at this file's parse time.
  function colorStep() {
    return ["step", ["coalesce", ["get", "intensity_kt"], 0]]
      .concat(TATP().stepExpr());
  }
  var ZOOM_RADIUS = ["interpolate", ["linear"], ["zoom"],
    0, 2.0, 4, 3.0, 8, 4.0, 12, 5.0];
  var ZOOM_ICON_SIZE = ["interpolate", ["linear"], ["zoom"],
    0, 0.20, 4, 0.28, 8, 0.36, 12, 0.45];

  var TRACKS_BEFORE = "tracks-line-solid";   // rasters insert below this id
  var IMAGERY_CDN = "https://cdn.triple-a-tropics.com";  // floater + microwave R2 origin

  // ===================================================================
  // Helpers (verbatim from global_tracks.html)
  // ===================================================================
  function ktToMph5(k) { return Math.round(k * 1.15077945 / 5) * 5; }

  // Best-effort JSON GET (a missing/blocked manifest -> null, never throws, so a
  // storm with no floater/MW simply gets no imagery layer).
  function fetchJSON(url) {
    return fetch(url, { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
  }

  // WGS84 [W,S,E,N] -> MapLibre image-source corner quad [TL,TR,BR,BL] (each
  // [lng,lat]). The producers store axis-aligned equirectangular bounds in their
  // native frame; this is the ONE site that unwraps an antimeridian crossing
  // (E<=W, e.g. a WP floater straddling 180 deg), letting lng exceed 180 so
  // MapLibre wraps it continuously instead of spanning the globe backwards.
  function boundsToCorners(b) {
    if (!b || b.length < 4) return null;
    var W = +b[0], S = +b[1], E = +b[2], N = +b[3];
    if (!(isFinite(W) && isFinite(S) && isFinite(E) && isFinite(N))) return null;
    if (E <= W) E += 360;
    return [[W, N], [E, N], [E, S], [W, S]];
  }
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
    ".clm-row.active{border-left-color:var(--cat-accent,var(--cat-td));",
      "background:rgba(63,164,255,.08);}",
    ".clm-row .clm-eye{flex:0 0 auto;width:26px;height:16px;border-radius:9px;",
      "background:#2a3343;position:relative;transition:background .15s;}",
    ".clm-row .clm-eye::after{content:'';position:absolute;top:2px;left:2px;",
      "width:12px;height:12px;border-radius:50%;background:#8ea2bd;transition:transform .15s,background .15s;}",
    ".clm-row.on .clm-eye{background:var(--cat-accent,var(--cat-td));}",
    ".clm-row.on .clm-eye::after{transform:translateX(10px);background:#fff;}",
    ".clm-row .clm-name{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;",
      "white-space:nowrap;color:var(--fg,#e8eef5);}",
    ".clm-row .clm-sw{flex:0 0 auto;width:11px;height:11px;border-radius:3px;}",
    ".clm-op{padding:0 11px 8px 41px;}",
    ".clm-op input[type=range]{width:100%;accent-color:var(--cat-accent,var(--cat-td));height:3px;}",
    ".clm-empty{padding:4px 11px 8px 11px;color:var(--muted,#8ea2bd);font-size:11px;font-style:italic;}",
    ".clm-row.unavailable{opacity:.5;cursor:default;}",
    ".clm-row.unavailable .clm-na{margin-left:auto;font-size:9px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted,#8ea2bd);}",
    ".clm-drag{flex:0 0 auto;cursor:grab;color:var(--muted,#8ea2bd);font-size:11px;",
      "letter-spacing:-2px;touch-action:none;user-select:none;}",
    ".clm-row.clm-drop{box-shadow:inset 0 2px 0 var(--cat-accent,var(--cat-td));}",
    ".clm-subp{padding:0 11px 6px 41px;}",
    ".clm-subp select{width:100%;background:var(--panel,#11161f);color:var(--fg,#e8eef5);",
      "border:1px solid var(--border,#232a36);border-radius:6px;font-size:11px;padding:4px 6px;}",
    ".clm-legend{padding:8px 11px;border-top:1px solid var(--border,#232a36);}",
    ".clm-leg-t{font-size:10px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;",
      "color:var(--muted,#8ea2bd);margin-bottom:5px;}",
    ".clm-leg-note{font-size:11px;color:var(--muted,#8ea2bd);font-style:italic;}",
    ".clm-leg-sshs{display:flex;flex-wrap:wrap;gap:3px 8px;font-size:10px;color:var(--muted,#8ea2bd);}",
    ".clm-leg-sshs span{display:inline-flex;align-items:center;gap:4px;}",
    ".clm-leg-sshs i{width:10px;height:10px;border-radius:2px;display:inline-block;}",
    ".clm-leg-bar{height:10px;border-radius:3px;border:1px solid var(--border,#232a36);}",
    ".clm-leg-ends{display:flex;justify-content:space-between;font-size:10px;",
      "color:var(--muted,#8ea2bd);margin-top:3px;}",
    // tools right rail
    ".clm-tools{flex:0 0 auto;display:flex;flex-direction:column;width:132px;",
      "background:var(--bg,#0b0e13);border-left:1px solid var(--border,#232a36);overflow:auto;}",
    ".clm-toolbtns{display:flex;flex-direction:column;gap:4px;padding:8px;}",
    ".clm-toolb{display:flex;align-items:center;gap:7px;background:var(--panel,#11161f);",
      "color:var(--fg,#e8eef5);border:1px solid var(--border,#232a36);border-radius:8px;",
      "padding:8px 9px;font-size:12px;font-weight:600;cursor:pointer;text-align:left;}",
    ".clm-toolb:hover{border-color:var(--cat-accent,var(--cat-td));}",
    ".clm-toolb.on{background:var(--cat-accent,var(--cat-td));color:#06121f;border-color:transparent;}",
    ".clm-toolb .clm-ti{font-size:14px;line-height:1;}",
    ".clm-toolpanel{padding:0 8px 10px;font-size:11px;color:var(--fg,#e8eef5);}",
    ".clm-tp-h{font-size:10px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;",
      "color:var(--muted,#8ea2bd);margin:6px 0 5px;}",
    ".clm-tp-note{color:var(--muted,#8ea2bd);font-size:10.5px;line-height:1.4;margin-bottom:6px;}",
    ".clm-readout{background:var(--panel,#11161f);border:1px solid var(--border,#232a36);",
      "border-radius:7px;padding:7px 8px;font-size:11px;line-height:1.5;min-height:18px;}",
    ".clm-tp-btn{display:inline-block;margin:6px 4px 0 0;background:var(--panel,#11161f);",
      "color:var(--fg,#e8eef5);border:1px solid var(--border,#232a36);border-radius:7px;",
      "padding:5px 9px;font-size:11px;cursor:pointer;}",
    ".clm-tp-btn.on{background:var(--cat-accent,var(--cat-td));color:#06121f;border-color:transparent;}",
    ".clm-tp-modes{display:flex;gap:5px;}",
    ".clm-tp-colors{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 2px;}",
    ".clm-tp-color{width:18px;height:18px;border-radius:50%;cursor:pointer;",
      "border:2px solid transparent;box-shadow:0 0 0 1px var(--border,#232a36);}",
    ".clm-tp-color.on{border-color:#fff;}",
    ".clm-shapes{display:flex;flex-wrap:wrap;gap:4px;margin:4px 0 8px;}",
    ".clm-shape{width:30px;height:28px;display:flex;align-items:center;justify-content:center;",
      "background:var(--panel,#11161f);color:var(--fg,#e8eef5);border:1px solid var(--border,#232a36);",
      "border-radius:7px;cursor:pointer;font-size:14px;line-height:1;}",
    ".clm-shape.on{background:var(--cat-accent,var(--cat-td));color:#06121f;border-color:transparent;}",
    ".clm-colorrow{display:flex;align-items:center;gap:8px;margin:2px 0;}",
    ".clm-colorpick{width:30px;height:26px;padding:0;border:1px solid var(--border,#232a36);",
      "border-radius:6px;background:none;cursor:pointer;flex:0 0 auto;}",
    ".clm-tp-btnrow{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px;}",
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
    ".clm-tb:hover{border-color:var(--cat-accent,var(--cat-td));}",
    ".clm-tb.on{background:var(--cat-accent,var(--cat-td));color:#06121f;border-color:transparent;}",
    ".clm-scrub{flex:1 1 auto;min-width:0;position:relative;}",
    ".clm-scrub input[type=range]{width:100%;accent-color:var(--cat-accent,var(--cat-td));}",
    ".clm-ticks{position:relative;height:6px;margin-top:-2px;}",
    ".clm-ticks i{position:absolute;top:0;width:2px;height:6px;border-radius:1px;transform:translateX(-1px);}",
    ".clm-ticks i.clm-tick-r{top:2px;height:4px;width:3px;opacity:.8;}",
    ".clm-cover{position:absolute;top:10px;left:50%;transform:translateX(-50%);z-index:3;",
      "background:rgba(10,16,24,.78);color:var(--muted,#8ea2bd);font-size:11px;",
      "padding:4px 10px;border-radius:7px;pointer-events:none;border:1px solid var(--border,#232a36);}",
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
      ".clm-tools{position:absolute;top:8px;right:8px;z-index:6;width:118px;",
        "max-height:calc(100% - 16px);border:1px solid var(--border,#232a36);",
        "border-radius:10px;background:rgba(11,14,19,.94);}",
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

  // Generic one-shot lazy-loader for an external UMD lib (exposes `globalName`).
  // Used for the tool libs (turf for geodesic distance, html2canvas for export).
  // Always invokes cb (even on load failure) so callers degrade gracefully.
  var _libState = {};
  function _ensureLib(url, globalName, cb) {
    if (window[globalName]) { cb(); return; }
    var st = _libState[globalName] || (_libState[globalName] = { loading: false, waiters: [] });
    st.waiters.push(cb);
    if (st.loading) return;
    st.loading = true;
    var s = document.createElement("script");
    s.src = url;
    s.onload = function () { st.waiters.splice(0).forEach(function (f) { try { f(); } catch (e) {} }); };
    s.onerror = function () {
      st.loading = false;
      st.waiters.splice(0).forEach(function (f) { try { f(); } catch (e) {} });
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
    var color = TATP().cats[cls] || "#888";
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
    this.timeMode = (opts.timeMode === "independent") ? "independent" : "synced";
    // Archive mode (a historical storm): track + recon are the live layers;
    // Satellite/Microwave imagery is current-only -> shown greyed/unavailable
    // (no broken fetches). Opt-in: live mounts never pass it -> behave as today.
    this.archive = !!opts.archive;
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
    var tools = document.createElement("div"); tools.className = "clm-tools";
    body.appendChild(tools);
    r.appendChild(body);

    var time = document.createElement("div"); time.className = "clm-time";
    r.appendChild(time);

    this.dom.body = body; this.dom.rail = rail; this.dom.mapWrap = mapWrap;
    this.dom.tools = tools; this.dom.time = time;

    // The track layer is the always-present base layer (Tracks group, active).
    var trackLayer = {
      id: "track", group: "tracks", label: "Storm track",
      type: "track", visible: true, opacity: 1, swatch: "#ffffff"
    };
    this._applySaved(trackLayer);
    this.layers.push(trackLayer);
    this.tool = null;            // active tool: inspect | distance | draw | null
    this._drawColor = _lsGet("clm.drawColor", "#ffd400");   // remembered last color
    this._drawMode = "dot";      // dot|circle|square|triangle|x|arrow|freehand|select
    this._drawFeats = [];
    this._drawId = 0;
    this._distPts = [];
    this._buildRail();
    this._buildTime();
    this._buildTools();
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
      cooperativeGestures: true,
      // EXPORT (P2): keep the GL drawing buffer so getCanvas().toDataURL() can
      // composite the visible stack into a PNG.
      preserveDrawingBuffer: true
    });
    this.map.addControl(new maplibregl.NavigationControl({
      visualizePitch: false, showCompass: false
    }), "bottom-right");
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
      self._restack();
      // Push persisted visibility/opacity onto the freshly-created map layers.
      self.layers.forEach(function (L) {
        self._applyVisibility(L);
        if (L.opacity !== 1) self.setLayerOpacity(L.id, L.opacity);
      });
      // Tool layers (distance + draw) sit ABOVE the track + markers.
      self._setupToolLayers();
      // First real raster layers: satellite floater + microwave (additive,
      // best-effort fetch of the per-storm tiles + bounds).
      self._loadImageryLayers();
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
        "circle-color": colorStep(), "circle-radius": ZOOM_RADIUS,
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
      paint: { "icon-color": colorStep(), "icon-halo-color": "#ffffff", "icon-halo-width": 1.0 }
    });
    map.addLayer({
      id: "observations-nontropical", type: "symbol", source: "storms",
      filter: ["all", ["==", ["geometry-type"], "Point"],
        ["==", ["get", "kind"], "observation"], ["==", ["get", "is_nontropical"], true]],
      layout: { "icon-image": "phase-triangle", "icon-size": ZOOM_ICON_SIZE,
        "icon-allow-overlap": true, "icon-ignore-placement": true },
      paint: { "icon-color": colorStep(), "icon-halo-color": "#ffffff", "icon-halo-width": 1.0 }
    });
    this._wireObsPopups();
  };

  P._wireObsPopups = function () {
    var map = this.map, popup = null, self = this;
    var OBS = ["observations-tropical", "observations-subtropical", "observations-nontropical"];
    function enter(e) {
      if (!self.tool) map.getCanvas().style.cursor = "pointer";   // don't clobber a tool crosshair
      var f = e.features[0], props = f.properties || {};
      var coords = f.geometry.coordinates.slice();
      while (e.lngLat.lng - coords[0] > 180) coords[0] += 360;
      while (e.lngLat.lng - coords[0] < -180) coords[0] -= 360;
      var kt = props.intensity_kt, pres = props.mslp_mb, cls = props.sshws_cat || "TD";
      var color = TATP().cats[cls] || "#888";
      var windTxt = (kt != null && kt !== "" && !isNaN(parseFloat(kt)))
        ? (Math.round(parseFloat(kt)) + " kt &middot; " + ktToMph5(parseFloat(kt)) + " mph") : "-";
      var presTxt = (pres != null && pres !== "" && !isNaN(parseFloat(pres)))
        ? (Math.round(parseFloat(pres)) + " mb") : "-";
      var html = '<div class="tt-name">' + escapeHtml(props.storm_name || "Storm") + '</div>' +
        '<div class="tt-time">' + fmtTime(props.time_iso) + '</div>' +
        '<div class="tt-row"><span class="tt-cat" style="background:' + color + '">' +
          (TATP().labels[cls] || cls) + '</span></div>' +
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
      map.getCanvas().style.cursor = self.tool ? "crosshair" : "";   // restore the tool cursor
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
      subProducts: def.subProducts || null, activeSub: def.activeSub || null,
      onSubProduct: def.onSubProduct || null, legendStops: def.legendStops || null,
      legendHtml: def.legendHtml || null, legendLabel: def.legendLabel || null,
      _added: false
    };
    this._applySaved(L);
    this.layers.push(L);
    this._applySavedOrder();
    if (this.ready && this.map) { this._mountRaster(L); this._restack(); }
    this._buildRail();
    this._buildTime();        // a raster layer extends the master timeline
    this._persist();
    return L;
  };
  // Reorder the raster layers to a previously-saved order (best-effort; rasters
  // not in the saved order keep their relative position at the end).
  P._applySavedOrder = function () {
    var sv = this._savedState();
    if (!sv || !sv.order || !sv.order.length) return;
    var rank = {}; sv.order.forEach(function (id, i) { rank[id] = i; });
    var rasters = this._rasterOrder().slice();
    rasters.sort(function (a, b) {
      return (rank[a.id] == null ? 1e9 : rank[a.id]) - (rank[b.id] == null ? 1e9 : rank[b.id]);
    });
    var ri = 0;
    this.layers = this.layers.map(function (L) {
      return L.type === "raster" ? rasters[ri++] : L;
    });
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
  // Imagery layers (Satellite floater + Microwave) — the first REAL raster
  // layers. Reads the chrome-free georeferenced tiles + WGS84 bounds the
  // producers now emit; everything below (mount, scrubber, legend) is the
  // existing raster framework. Best-effort + additive: a storm with no floater
  // or no MW simply gets no layer. Called once, after the map is ready.
  // ===================================================================
  P._loadImageryLayers = function () {
    var self = this, s = this.storm || {};
    // Archive (historical) storm: no current floater/MW exists, so don't fetch -
    // register Satellite + Microwave as explicit greyed/unavailable rows (an
    // honest note, not a broken/silent layer). Track + recon stay the live layers.
    if (this.archive) {
      var note = " imagery is current-only - unavailable for archived storms.";
      this.layers.push({ id: "sat", group: "imagery", label: "Satellite",
        type: "raster", unavailable: true, visible: false, frames: [],
        swatch: "#5dd3ff", note: "Satellite" + note });
      this.layers.push({ id: "mw", group: "imagery", label: "Microwave",
        type: "raster", unavailable: true, visible: false, frames: [],
        swatch: "#b06cff", note: "Microwave" + note });
      this._buildRail();
      return;
    }
    // Derive the microwave slug + floater slug from the storm id. sid is the
    // canonical id (NHC bare "al012026"; JTWC "JTWC_WP072026"); atcf_long is the
    // shell's pre-stripped form when present. MW slug = lowercase atcf (wp072026);
    // floater slug = drop the trailing 4-digit year (wp07).
    var atcf = String(s.atcf_long || s.atcf_id || s.sid || "")
      .replace(/^[A-Za-z]+_/, "").toLowerCase();
    if (!atcf) return;
    var floaterSlug = atcf.replace(/\d{4}$/, "");

    // ---- Microwave (sparse overpasses) ----
    fetchJSON(IMAGERY_CDN + "/microwave/manifest.json").then(function (man) {
      if (!man) return;
      var ent = (man.storms || []).filter(function (e) {
        return String(e.slug || "").toLowerCase() === atcf;
      })[0];
      if (!ent) return;
      var legends = man.legends || {};
      var leg = function (p) { return legends[p] || {}; };
      fetchJSON(IMAGERY_CDN + "/microwave/" + atcf + "/overpasses.json").then(function (doc) {
        if (!doc) return;
        var framesFor = function (prod) {
          return (doc.overpasses || [])
            .filter(function (o) { return o.tiles && o.tiles[prod] && o.bounds_wgs84; })
            .map(function (o) {
              return { url: IMAGERY_CDN + "/microwave/" + o.tiles[prod],
                       corners: boundsToCorners(o.bounds_wgs84), time: o.valid_utc };
            })
            .filter(function (fr) { return fr.corners; });
        };
        if (!framesFor("89pct").length && !framesFor("37color").length) return;
        var g0 = leg("89pct");
        self.addRasterLayer({
          id: "mw", group: "imagery", label: "Microwave (89 PCT)", swatch: "#b06cff",
          visible: false, frames: framesFor("89pct"),
          subProducts: [{ value: "89pct", label: "89 PCT" }, { value: "37color", label: "37 Color" }],
          activeSub: "89pct",
          legendStops: g0.stops || null, legendHtml: g0.legendHtml || null, legendLabel: g0.label || null,
          onSubProduct: function (sub, host) {
            var L = host._layer("mw"); if (!L) return;
            var g = leg(sub);
            L.frames = framesFor(sub); L.activeFrame = 0;
            L.legendStops = g.stops || null; L.legendHtml = g.legendHtml || null;
            L.legendLabel = g.label || null;
            L.label = sub === "37color" ? "Microwave (37 Color)" : "Microwave (89 PCT)";
            host._buildTime();
            if (host.ready) host.setActiveFrame("mw", 0);
          }
        });
      });
    });

    // ---- Satellite floater (dense frames) ----
    fetchJSON(IMAGERY_CDN + "/floaters/manifest.json").then(function (top) {
      if (!top) return;
      var ent = (top.storms || []).filter(function (e) {
        var id = String(e.id || "").toLowerCase();
        return id === atcf || id.replace(/^[a-z]+_/, "") === atcf
            || String(e.slug || "").toLowerCase() === floaterSlug;
      })[0];
      if (!ent || !ent.manifest) return;
      fetchJSON(IMAGERY_CDN + "/" + String(ent.manifest).replace(/^\//, "")).then(function (man) {
        if (!man || !man.bands) return;
        var bandKeys = Object.keys(man.bands);
        var framesFor = function (bk) {
          var b = man.bands[bk] || {};
          return (b.frames || [])
            .filter(function (f) { return f.tile_key && f.bounds; })
            .map(function (f) {
              return { url: IMAGERY_CDN + "/" + String(f.tile_key).replace(/^\//, ""),
                       corners: boundsToCorners(f.bounds), time: f.t };
            })
            .filter(function (fr) { return fr.corners; });
        };
        var first = null;
        for (var i = 0; i < bandKeys.length; i++) {
          if (framesFor(bandKeys[i]).length) { first = bandKeys[i]; break; }
        }
        if (!first) return;
        var b0 = man.bands[first];
        var bandLabel = function (k) { return (man.bands[k] && man.bands[k].label) || k; };
        self.addRasterLayer({
          id: "sat", group: "imagery", label: "Satellite (" + bandLabel(first) + ")",
          swatch: "#5dd3ff", frames: framesFor(first),
          subProducts: bandKeys.map(function (k) { return { value: k, label: bandLabel(k) }; }),
          activeSub: first,
          legendStops: (b0.legend || {}).stops || null,
          legendHtml: (b0.legend || {}).legendHtml || null,
          legendLabel: (b0.legend || {}).label || null,
          onSubProduct: function (sub, host) {
            var L = host._layer("sat"); if (!L) return;
            var b = man.bands[sub] || {};
            L.frames = framesFor(sub); L.activeFrame = 0;
            L.legendStops = (b.legend || {}).stops || null;
            L.legendHtml = (b.legend || {}).legendHtml || null;
            L.legendLabel = (b.legend || {}).label || null;
            L.label = "Satellite (" + bandLabel(sub) + ")";
            host._buildTime();
            if (host.ready) host.setActiveFrame("sat", 0);
          }
        });
      });
    });
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
    this._persist();
  };
  P._applyVisibility = function (L) {
    if (!this.map) return;
    var vis = L.visible ? "visible" : "none";
    // Coverage gate (sparse rasters, e.g. microwave): hidden when the nearest
    // overpass is outside the hold window, even if the user toggled it on -
    // _rasterToTime sets L._covered and re-applies as the scrubber moves.
    if (L.type === "raster" && L._covered === false) vis = "none";
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
    this._persist();
  };
  P.setActiveLayer = function (id) {
    if (!this._layer(id)) return;
    this.activeLayerId = id;
    this._buildRail();
    this._persist();
  };

  // ===================================================================
  // Layer-state PERSISTENCE (order + visibility + opacity + sub-product)
  // ===================================================================
  P._persistKey = function () {
    return "clm:" + (this.storm.sid || this.storm.atcf_id || this.storm.name || "default");
  };
  P._persist = function () {
    try {
      var st = {};
      this.layers.forEach(function (L) {
        st[L.id] = { v: L.visible, o: L.opacity, s: L.activeSub || null };
      });
      var order = this._rasterOrder().map(function (L) { return L.id; });
      window.localStorage.setItem(this._persistKey(),
        JSON.stringify({ order: order, state: st }));
    } catch (e) {}
  };
  P._savedState = function () {
    if (this._saved !== undefined) return this._saved;
    try { this._saved = JSON.parse(window.localStorage.getItem(this._persistKey()) || "null"); }
    catch (e) { this._saved = null; }
    return this._saved;
  };
  // Apply saved visibility/opacity/sub-product to a layer as it is added.
  P._applySaved = function (L) {
    var sv = this._savedState();
    if (!sv || !sv.state || !sv.state[L.id]) return;
    var e = sv.state[L.id];
    if (typeof e.v === "boolean") L.visible = e.v;
    if (typeof e.o === "number") L.opacity = e.o;
    if (e.s) L.activeSub = e.s;
  };

  // ===================================================================
  // Z-ORDER (rail TOP = stack TOP; rasters always below track + markers)
  // ===================================================================
  P._rasterOrder = function () {
    return this.layers.filter(function (L) { return L.type === "raster"; });
  };
  // Reorder raster `id` by delta among rasters (delta<0 = up = toward stack top).
  P.moveLayer = function (id, delta) {
    var ras = this._rasterOrder();
    var idx = -1, i;
    for (i = 0; i < ras.length; i++) if (ras[i].id === id) idx = i;
    if (idx < 0) return;
    var to = Math.max(0, Math.min(ras.length - 1, idx + delta));
    if (to === idx) return;
    var L = ras[idx];
    this.layers.splice(this.layers.indexOf(L), 1);
    var target = ras[to];
    var gTo = this.layers.indexOf(target);
    this.layers.splice(delta > 0 ? gTo + 1 : gTo, 0, L);
    this._restack(); this._buildRail(); this._persist();
  };
  // Drop raster `id` to land just before raster `beforeId` (or end). Used by drag.
  P.reorderBefore = function (id, beforeId) {
    var L = this._layer(id); if (!L || L.type !== "raster") return;
    this.layers.splice(this.layers.indexOf(L), 1);
    if (beforeId) {
      var b = this._layer(beforeId);
      this.layers.splice(b ? this.layers.indexOf(b) : this.layers.length, 0, L);
    } else {
      this.layers.push(L);
    }
    this._restack(); this._buildRail(); this._persist();
  };
  // Apply the raster list order to the map. Rail TOP = stack TOP, so the FIRST
  // raster in list order must be highest; move each (bottom->top of list) to just
  // below the track so the first list item ends up topmost among rasters.
  P._restack = function () {
    if (!this.map) return;
    var ras = this._rasterOrder();
    var before = this.map.getLayer(TRACKS_BEFORE) ? TRACKS_BEFORE : undefined;
    for (var i = ras.length - 1; i >= 0; i--) {
      var lid = "clm-raster-" + ras[i].id + "-layer";
      if (this.map.getLayer(lid)) { try { this.map.moveLayer(lid, before); } catch (e) {} }
    }
  };

  // ---- sub-product selection (Satellite IR/true-color/band; MW 89/37; ...) ----
  P.setSubProduct = function (id, sub) {
    var L = this._layer(id); if (!L || !L.subProducts) return;
    L.activeSub = sub;
    // Producer hook: swap this layer's frame set for the chosen sub-product.
    if (typeof L.onSubProduct === "function") {
      try { L.onSubProduct(sub, this); } catch (e) {}
    }
    this._buildRail(); this._persist();
  };

  // ---- legend for the ACTIVE layer ----
  P._legendHtml = function (L) {
    if (!L) return "";
    if (L.type === "track") {
      var pal = TATP();
      return '<div class="clm-leg-t">SSHWS (kt)</div><div class="clm-leg-sshs">' +
        pal.order.map(function (c, i) {
          var lo = pal.minKt[c], hi = pal.maxKt[c];
          var span = i === 0 ? "<" + (hi + 1)
            : (hi == null ? "≥" + lo : lo + "-" + hi);
          return '<span><i style="background:' + pal.cats[c] + '"></i>' + span + '</span>';
        }).join("") + '</div>';
    }
    if (L.legendHtml) return L.legendHtml;
    if (L.legendStops && L.legendStops.length) {
      var grad = L.legendStops.map(function (s) { return s.color; }).join(",");
      var lo = L.legendStops[0], hi = L.legendStops[L.legendStops.length - 1];
      return '<div class="clm-leg-t">' + escapeHtml(L.legendLabel || L.label) + '</div>' +
        '<div class="clm-leg-bar" style="background:linear-gradient(90deg,' + grad + ')"></div>' +
        '<div class="clm-leg-ends"><span>' + escapeHtml(String(lo.label != null ? lo.label : "")) +
        '</span><span>' + escapeHtml(String(hi.label != null ? hi.label : "")) + '</span></div>';
    }
    return '<div class="clm-leg-t">' + escapeHtml(L.label) + '</div>' +
      '<div class="clm-leg-note">Legend appears when this layer publishes a palette.</div>';
  };

  // ---- explorer rail render ----
  P._buildRail = function () {
    var rail = this.dom.rail; if (!rail) return;
    var self = this;
    var s = this.storm;
    var cls = s.current_category || (s.max_category || "TD");
    var color = TATP().cats[cls] || TATP().cats[TATP().unknown];
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
        // Archive: a current-only layer with no data -> greyed, no eye/opacity/
        // sub-product, with an honest note. Never reaches _mountRaster.
        if (L.unavailable) {
          html += '<div class="clm-row unavailable" data-id="' + L.id +
            '" data-type="' + L.type + '" aria-disabled="true">' +
            '<span class="clm-sw" style="background:' + (L.swatch || "#5dd3ff") + '"></span>' +
            '<span class="clm-name">' + escapeHtml(L.label) + '</span>' +
            '<span class="clm-na">unavailable</span></div>';
          if (L.note) html += '<div class="clm-empty">' + escapeHtml(L.note) + '</div>';
          return;
        }
        var drag = (L.type === "raster")
          ? '<span class="clm-drag" data-drag="' + L.id + '" title="Drag to reorder">⋮⋮</span>' : '';
        html += '<div class="clm-row' + (L.visible ? " on" : "") + (active ? " active" : "") +
          '" data-id="' + L.id + '" data-type="' + L.type + '">' + drag +
          '<span class="clm-eye" data-act="toggle" data-id="' + L.id + '"></span>' +
          '<span class="clm-sw" style="background:' + (L.swatch || "#5dd3ff") + '"></span>' +
          '<span class="clm-name">' + escapeHtml(L.label) + '</span></div>';
        if (L.subProducts && L.subProducts.length) {
          var cur = L.activeSub || (typeof L.subProducts[0] === "string"
            ? L.subProducts[0] : L.subProducts[0].value);
          html += '<div class="clm-subp"><select data-act="subp" data-id="' + L.id + '">' +
            L.subProducts.map(function (sp) {
              var val = (typeof sp === "string") ? sp : sp.value;
              var lab = (typeof sp === "string") ? sp : (sp.label || sp.value);
              return '<option value="' + escapeHtml(val) + '"' +
                (val === cur ? ' selected' : '') + '>' + escapeHtml(lab) + '</option>';
            }).join("") + '</select></div>';
        }
        html += '<div class="clm-op"><input type="range" min="0" max="100" value="' +
          Math.round(L.opacity * 100) + '" data-act="opacity" data-id="' + L.id + '"></div>';
      });
      html += '</div>';
    });
    // Active-layer legend (drives the readout/legend panel per the brief).
    var actL = this._layer(this.activeLayerId) || this._layer("track");
    html += '<div class="clm-legend">' + this._legendHtml(actL) + '</div>';
    html += '<div class="clm-rail-foot">Drag map to pan · Ctrl/⌘+scroll to zoom. ' +
      'Imagery & model layers stack here when published.</div>';
    rail.innerHTML = html;

    rail.querySelectorAll('[data-act="toggle"]').forEach(function (eye) {
      eye.addEventListener("click", function (ev) {
        ev.stopPropagation(); self.toggleLayer(eye.getAttribute("data-id"));
      });
    });
    rail.querySelectorAll('[data-act="opacity"]').forEach(function (sl) {
      sl.addEventListener("input", function (ev) {
        ev.stopPropagation();
        self.setLayerOpacity(sl.getAttribute("data-id"), parseInt(sl.value, 10) / 100);
      });
    });
    rail.querySelectorAll('[data-act="subp"]').forEach(function (se) {
      se.addEventListener("change", function (ev) {
        ev.stopPropagation(); self.setSubProduct(se.getAttribute("data-id"), se.value);
      });
    });
    rail.querySelectorAll('.clm-row').forEach(function (row) {
      row.addEventListener("click", function () { self.setActiveLayer(row.getAttribute("data-id")); });
    });
    this._wireDrag(rail);
  };

  // Pointer-drag reorder of raster rows (drop -> reorderBefore). Mobile-friendly.
  P._wireDrag = function (rail) {
    var self = this;
    rail.querySelectorAll('[data-drag]').forEach(function (h) {
      h.addEventListener("pointerdown", function (ev) {
        ev.preventDefault(); ev.stopPropagation();
        var id = h.getAttribute("data-drag");
        function over(e) {
          var el = document.elementFromPoint(e.clientX, e.clientY);
          var row = el && el.closest ? el.closest('.clm-row[data-type="raster"]') : null;
          rail.querySelectorAll('.clm-row').forEach(function (r) { r.classList.remove("clm-drop"); });
          if (row && row.getAttribute("data-id") !== id) row.classList.add("clm-drop");
        }
        function up(e) {
          document.removeEventListener("pointermove", over);
          document.removeEventListener("pointerup", up);
          var el = document.elementFromPoint(e.clientX, e.clientY);
          var row = el && el.closest ? el.closest('.clm-row[data-type="raster"]') : null;
          if (row) {
            var beforeId = row.getAttribute("data-id");
            if (beforeId !== id) self.reorderBefore(id, beforeId);
          }
        }
        document.addEventListener("pointermove", over);
        document.addEventListener("pointerup", up);
      });
    });
  };

  // ===================================================================
  // TIME control (master timeline over track fixes)
  // ===================================================================
  // Master timeline = sorted unique union of every layer's frame/fix times (ms).
  // Track-only -> this is just the fix times, so the per-fix behavior is
  // unchanged. With raster layers it spans their frame times too, and each layer
  // renders its NEAREST-in-time frame to the playhead.
  P._allStops = function () {
    var set = {};
    function add(t) { var ms = +new Date(t); if (!isNaN(ms)) set[ms] = 1; }
    (this.storm.points || []).forEach(function (p) { if (p.t) add(p.t); });
    this.layers.forEach(function (L) {
      if (L.type === "raster") (L.frames || []).forEach(function (f) { if (f.time) add(f.time); });
    });
    return Object.keys(set).map(Number).sort(function (a, b) { return a - b; });
  };

  P._buildTime = function () {
    var t = this.dom.time; if (!t) return;
    var self = this;
    this._stops = this._allStops();
    var n = this._stops.length;
    var keepPos = (this.dom.scrub && n) ? Math.min(this.playhead || (n - 1), n - 1) : (n - 1);
    t.innerHTML =
      '<div class="clm-tbtns">' +
        '<button class="clm-tb" data-act="step-" title="Step back">◀</button>' +
        '<button class="clm-tb" data-act="play" title="Play / pause">▶</button>' +
        '<button class="clm-tb" data-act="step+" title="Step forward">▶▶</button>' +
        '<button class="clm-tb" data-act="loop" title="Loop">↻</button>' +
      '</div>' +
      '<div class="clm-scrub"><input type="range" min="0" max="' + Math.max(0, n - 1) +
        '" value="' + Math.max(0, keepPos) + '" data-act="scrub">' +
        '<div class="clm-ticks"></div></div>' +
      '<select class="clm-speed" data-act="speed">' +
        '<option value="1200">0.5×</option>' +
        '<option value="700">1×</option>' +
        '<option value="380">2×</option>' +
        '<option value="180">4×</option></select>' +
      '<div class="clm-valid"><span data-act="valid">—</span></div>';
    this.dom.scrub = t.querySelector('[data-act="scrub"]');
    this.dom.validEl = t.querySelector('[data-act="valid"]');
    this.dom.playBtn = t.querySelector('[data-act="play"]');
    this.dom.loopBtn = t.querySelector('[data-act="loop"]');
    if (this.loop) this.dom.loopBtn.classList.add("on");
    var spd = t.querySelector('[data-act="speed"]');
    spd.value = String(this.speedMs || 700);
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
    spd.addEventListener("change", function (e) {
      self.speedMs = parseInt(e.target.value, 10) || 700;
    });
  };

  // Per-layer color-coded ticks positioned by TIME across the master span. Track
  // ticks are colored by SSHWS category; raster ticks by the layer swatch (on a
  // lower row so overlapping sources stay legible).
  P._buildTicks = function () {
    var box = this.dom.time.querySelector(".clm-ticks");
    if (!box) return;
    var stops = this._stops || this._allStops();
    if (stops.length < 2) { box.innerHTML = ""; return; }
    var t0 = stops[0], t1 = stops[stops.length - 1], span = (t1 - t0) || 1;
    var html = "";
    (this.storm.points || []).forEach(function (p) {
      if (!p.t) return;
      var pct = ((+new Date(p.t) - t0) / span) * 100;
      html += '<i class="clm-tick-fix" style="left:' + pct.toFixed(2) + '%;background:' +
        fixCatColor(p) + '"></i>';
    });
    this.layers.forEach(function (L) {
      if (L.type !== "raster") return;
      (L.frames || []).forEach(function (f) {
        if (!f.time) return;
        var pct = ((+new Date(f.time) - t0) / span) * 100;
        html += '<i class="clm-tick-r" style="left:' + pct.toFixed(2) + '%;background:' +
          (L.swatch || "#5dd3ff") + '"></i>';
      });
    });
    box.innerHTML = html;
  };

  // Move the playhead to master-stop `idx`; apply that TIME to the layers
  // (source-adaptive: each layer renders its nearest frame). Synced = all layers
  // follow; independent = only the ACTIVE layer follows, others hold.
  P._setPlayhead = function (idx) {
    var stops = this._stops && this._stops.length ? this._stops : (this._stops = this._allStops());
    if (!stops.length) return;
    idx = Math.max(0, Math.min(stops.length - 1, idx));
    this.playhead = idx;
    var t = stops[idx];
    this._applyTimeToLayers(t);
    if (this.dom.scrub) this.dom.scrub.value = String(idx);
    if (this.dom.validEl) {
      var p = (this.storm.points || [])[this._trackIdx] || {};
      this.dom.validEl.innerHTML = fmtTime(new Date(t).toISOString()) +
        ' <small>· ' + (p.wind_kt != null ? Math.round(p.wind_kt) + " kt" : "—") +
        ' ' + sshsLabel(p.cls || "TD") +
        (this.timeMode === "independent" ? ' · indep' : '') + '</small>';
    }
  };

  P._applyTimeToLayers = function (t) {
    var mode = this.timeMode || "synced";
    if (mode === "independent") {
      var aL = this._layer(this.activeLayerId);
      if (aL && aL.type === "raster") this._rasterToTime(aL, t);
      else this._trackToTime(t);    // active track (or default)
      return;
    }
    this._trackToTime(t);
    var self = this;
    this.layers.forEach(function (L) { if (L.type === "raster") self._rasterToTime(L, t); });
    this._updateCoverageHint(t);
  };

  // Sparse-raster coverage window: HOLD the nearest overpass within +/-3 h so the
  // layer doesn't blink on/off between passes; beyond that there genuinely is no
  // pass (the live MW tier only carries recent overpasses) -> hide + a quiet hint.
  var RASTER_HOLD_MS = 3 * 3600 * 1000;
  // Per-frame honest hint when the ACTIVE raster has no pass near the scrub time.
  P._updateCoverageHint = function (t) {
    // Hint for the active raster if it's out of coverage, else any VISIBLE raster
    // that has frames but none near this time (user toggled it on into a gap).
    var aL = this._layer(this.activeLayerId), L = null;
    if (aL && aL.type === "raster" && aL.visible) L = aL;
    if (!L || L._covered !== false) {
      var vis = this.layers.filter(function (x) {
        return x.type === "raster" && x.visible && x._covered === false && (x.frames || []).length;
      });
      L = vis.length ? vis[0] : L;
    }
    var msg = null;
    if (L && L.type === "raster" && L.visible && L._covered === false && (L.frames || []).length) {
      msg = "No " + (L.label || "imagery").replace(/\s*\(.*\)$/, "") + " pass near this time";
    }
    var el = this.dom && this.dom.coverHint;
    if (!el && this.dom && this.dom.mapWrap) {
      el = document.createElement("div"); el.className = "clm-cover";
      this.dom.mapWrap.appendChild(el); this.dom.coverHint = el;
    }
    if (el) { el.textContent = msg || ""; el.style.display = msg ? "" : "none"; }
  };
  P._trackToTime = function (t) {
    var pts = this.storm.points || [];
    if (!pts.length) return;
    var best = 0, bd = Infinity;
    for (var i = 0; i < pts.length; i++) {
      if (!pts[i].t) continue;
      var d = Math.abs(+new Date(pts[i].t) - t);
      if (d < bd) { bd = d; best = i; }
    }
    this._trackIdx = best;
    if (this.map && this.map.getSource("storms")) {
      this.map.getSource("storms").setData(stormToGeoJSON(this.storm, best));
    }
    this._placeActiveGlyph(best);
  };
  P._rasterToTime = function (L, t) {
    if (!L.frames || !L.frames.length) return;
    var best = 0, bd = Infinity;
    for (var i = 0; i < L.frames.length; i++) {
      if (!L.frames[i].time) { if (bd === Infinity) best = i; continue; }
      var d = Math.abs(+new Date(L.frames[i].time) - t);
      if (d < bd) { bd = d; best = i; }
    }
    if (best !== L.activeFrame) this.setActiveFrame(L.id, best);
    // HOLD the nearest pass within the window; beyond it, hide (no blink) - the
    // coverage gate in _applyVisibility honors this + the user's toggle.
    var covered = bd <= RASTER_HOLD_MS;
    if (covered !== (L._covered !== false)) { L._covered = covered; this._applyVisibility(L); }
    else { L._covered = covered; }
  };

  // Synced (default) vs independent per-layer time. Persisted by the caller (the
  // CycloLab Settings modal); exposed so that toggle can drive it.
  P.setTimeMode = function (mode) {
    this.timeMode = (mode === "independent") ? "independent" : "synced";
    var stops = this._stops || this._allStops();
    if (stops.length) this._setPlayhead(this.playhead != null ? this.playhead : stops.length - 1);
  };

  // Place / move the spinning glyph (or invest X) at the playhead fix. The glyph
  // reflects the CURRENT-stage category at that fix (cls), matching the global map.
  P._placeActiveGlyph = function (idx) {
    if (!this.map || !window.maplibregl) return;
    var pts = this.storm.points || [];
    var p = pts[idx]; if (!p || p.lat == null || p.lon == null) return;
    var isInvest = !!(this.storm.is_invest || this.storm.is_ptc);
    var cls = p.cls || TATP().catForKt(p.wind_kt);
    // PRESERVE the rotating glyph node across scrubber steps. Recreating the SVG
    // every frame re-armed the CSS spin -> choppy (same repaint lesson as the
    // cone-reveal). Build once; afterwards MOVE it + update color/label in place
    // (the .spinning <g> is never re-created, so the rotation is continuous).
    if (!this.activeMarker || this._glyphInvest !== isInvest) {
      var el = buildActiveMarkerEl({
        name: this.storm.name, is_invest: this.storm.is_invest,
        is_ptc: this.storm.is_ptc, current_category: cls });
      if (this._reduced) {
        var sp = el.querySelector(".spinning"); if (sp) sp.classList.remove("spinning");
      }
      if (this.activeMarker) this.activeMarker.remove();
      this.activeMarker = new maplibregl.Marker({ element: el, anchor: "center" })
        .setLngLat([p.lon, p.lat]).addTo(this.map);
      this._glyphInvest = isInvest; this._glyphCls = cls;
    } else {
      this.activeMarker.setLngLat([p.lon, p.lat]);     // move only -> spin continues
      if (!isInvest && cls !== this._glyphCls) {        // category changed: recolor in place
        var elx = this.activeMarker.getElement();
        var color = TATP().cats[cls] || "#888";
        elx.style.color = color;
        var path = elx.querySelector(".spinning path"); if (path) path.setAttribute("fill", color);
        var lbl = elx.querySelector(".hurricane-label"); if (lbl) lbl.textContent = sshsLabel(cls);
        this._glyphCls = cls;
      }
    }
    // honor the track layer's visibility/opacity on the marker
    var tl = this._layer("track");
    if (tl) {
      var e2 = this.activeMarker.getElement();
      e2.style.display = tl.visible ? "" : "none"; e2.style.opacity = tl.opacity;
    }
  };

  // ---- transport ----
  P.toggle = function () { this.playing ? this.pause() : this.play(); };
  P.play = function () {
    var n = (this._stops || this._allStops()).length; if (n < 2) return;
    if (this.playhead >= n - 1) this._setPlayhead(0);
    this.playing = true;
    if (this.dom.playBtn) { this.dom.playBtn.textContent = "⎉"; this.dom.playBtn.classList.add("on"); }
    var self = this; this._lastStep = 0;
    var tick = function (ts) {
      if (!self.playing) return;
      if (!self._lastStep) self._lastStep = ts;
      if (ts - self._lastStep >= self.speedMs) {
        self._lastStep = ts;
        var n2 = (self._stops || self._allStops()).length;
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

  // ===================================================================
  // TOOLS (right rail): inspect / distance / draw / export
  // ===================================================================
  P._buildTools = function () {
    var t = this.dom.tools; if (!t) return;
    var self = this;
    var BTNS = [
      { id: "inspect", label: "Inspect", icon: "&#9678;", title: "Inspect values" },
      { id: "distance", label: "Distance", icon: "&#8596;", title: "Measure distance + bearing" },
      { id: "draw", label: "Draw", icon: "&#9998;", title: "Draw annotations" },
      { id: "export", label: "Export", icon: "&#8615;", title: "Export PNG" }
    ];
    var html = '<div class="clm-toolbtns">';
    BTNS.forEach(function (b) {
      html += '<button class="clm-toolb" type="button" data-tool="' + b.id + '" title="' +
        b.title + '"><span class="clm-ti">' + b.icon + '</span>' + b.label + '</button>';
    });
    html += '</div><div class="clm-toolpanel" id="clm-toolpanel"></div>';
    t.innerHTML = html;
    t.querySelectorAll("[data-tool]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var id = btn.getAttribute("data-tool");
        if (id === "export") { self.exportPng(); return; }
        self._setTool(self.tool === id ? null : id);
      });
    });
    this.dom.toolpanel = t.querySelector("#clm-toolpanel");
  };

  P._setTool = function (name) {
    this.tool = name;
    var btns = this.dom.tools ? this.dom.tools.querySelectorAll(".clm-toolb") : [];
    for (var i = 0; i < btns.length; i++) {
      btns[i].classList.toggle("on", btns[i].getAttribute("data-tool") === name);
    }
    // distance resets its in-progress points when (re)entered or left
    this._distPts = [];
    this._refreshDistance();
    if (name === "distance") {
      _ensureLib("https://unpkg.com/@turf/turf@7/turf.min.js", "turf", function () {});
    }
    if (this.map) this.map.getCanvas().style.cursor = name ? "crosshair" : "";
    this._drawSetEnabled(name === "draw");
    this._renderToolPanel();
  };

  P._renderToolPanel = function () {
    var pan = this.dom.toolpanel; if (!pan) return;
    var self = this;
    if (this.tool === "inspect") {
      pan.innerHTML = '<div class="clm-tp-h">Inspect</div>' +
        '<div class="clm-tp-note">Tap the map to read the nearest fix (and the ' +
        'active layer\'s value where available).</div>' +
        '<div class="clm-readout" id="clm-readout">&mdash;</div>';
    } else if (this.tool === "distance") {
      pan.innerHTML = '<div class="clm-tp-h">Distance</div>' +
        '<div class="clm-tp-note">Tap two points for great-circle distance + bearing.</div>' +
        '<div class="clm-readout" id="clm-distout">&mdash;</div>' +
        '<button class="clm-tp-btn" data-act="distclear">Clear</button>';
      pan.querySelector('[data-act="distclear"]').addEventListener("click", function () {
        self._distPts = []; self._refreshDistance();
      });
    } else if (this.tool === "draw") {
      pan.innerHTML = '<div class="clm-tp-h">Draw</div>' +
        '<div class="clm-shapes">' + DRAW_SHAPES.map(function (s) {
          return '<button class="clm-shape" type="button" data-mode="' + s.id +
            '" title="' + s.label + '">' + s.icon + '</button>';
        }).join("") + '</div>' +
        '<div class="clm-colorrow">' +
          '<input type="color" class="clm-colorpick" value="' + self._drawColor + '" title="Pick a color">' +
          '<div class="clm-tp-colors">' + DRAW_SWATCHES.map(function (c) {
            return '<span class="clm-tp-color" data-color="' + c + '" style="background:' + c + '"></span>';
          }).join("") + '</div></div>' +
        '<div class="clm-tp-note" data-act="drawhint">Tap the map to place.</div>' +
        '<div class="clm-tp-btnrow">' +
          '<button class="clm-tp-btn" data-act="delsel">Delete sel</button>' +
          '<button class="clm-tp-btn" data-act="undo">Undo</button>' +
          '<button class="clm-tp-btn" data-act="drawclear">Clear all</button></div>';
      var setColor = function (c) {
        self._drawColor = c; _lsSet("clm.drawColor", c);
        pan.querySelectorAll("[data-color]").forEach(function (x) {
          x.classList.toggle("on", x.getAttribute("data-color") === c);
        });
        var cp = pan.querySelector(".clm-colorpick"); if (cp && cp.value !== c) cp.value = c;
        if (self._drawSel != null) {                 // recolor the selected shape live
          var f = (self._drawFeats || []).filter(function (x) { return x.properties.id === self._drawSel; })[0];
          if (f) { f.properties.color = c; self._drawRender(); }
        }
      };
      var syncMode = function () {
        pan.querySelectorAll("[data-mode]").forEach(function (m) {
          m.classList.toggle("on", m.getAttribute("data-mode") === self._drawMode);
        });
      };
      pan.querySelectorAll("[data-mode]").forEach(function (m) {
        m.addEventListener("click", function () {
          self._drawMode = m.getAttribute("data-mode"); self._arrowTail = null;
          if (self._drawMode !== "select") self._drawSelect(null);
          self._drawHint(self._drawMode === "arrow" ? "Tap the arrow tail, then the head."
            : self._drawMode === "select" ? "Tap a shape to select; drag to move."
            : self._drawMode === "freehand" ? "Drag to draw." : "Tap the map to place.");
          syncMode();
        });
      });
      pan.querySelectorAll("[data-color]").forEach(function (c) {
        c.addEventListener("click", function () { setColor(c.getAttribute("data-color")); });
      });
      var cp = pan.querySelector(".clm-colorpick");
      if (cp) cp.addEventListener("input", function () { setColor(cp.value); });
      pan.querySelector('[data-act="delsel"]').addEventListener("click", function () { self._drawDeleteSel(); });
      pan.querySelector('[data-act="undo"]').addEventListener("click", function () { self._drawUndo(); });
      pan.querySelector('[data-act="drawclear"]').addEventListener("click", function () { self._drawClear(); });
      pan.querySelectorAll("[data-color]").forEach(function (x) {
        x.classList.toggle("on", x.getAttribute("data-color") === self._drawColor);
      });
      syncMode();
    } else {
      pan.innerHTML = "";
    }
  };

  // ---- tool map layers (distance + draw); called on map load ----
  P._setupToolLayers = function () {
    var map = this.map; if (!map) return;
    if (!map.getSource("clm-dist")) {
      map.addSource("clm-dist", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "clm-dist-line", type: "line", source: "clm-dist",
        filter: ["==", ["geometry-type"], "LineString"],
        paint: { "line-color": "#ffd400", "line-width": 2, "line-dasharray": [2, 1.5] } });
      map.addLayer({ id: "clm-dist-pt", type: "circle", source: "clm-dist",
        filter: ["==", ["geometry-type"], "Point"],
        paint: { "circle-radius": 4, "circle-color": "#ffd400",
          "circle-stroke-color": "#07101c", "circle-stroke-width": 1.5 } });
    }
    if (!map.getSource("clm-draw")) {
      var cc = ["coalesce", ["get", "color"], "#ffd400"];
      var sel = ["==", ["get", "sel"], true];
      map.addSource("clm-draw", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "clm-draw-fill", type: "fill", source: "clm-draw",
        filter: ["==", ["geometry-type"], "Polygon"],
        paint: { "fill-color": cc, "fill-opacity": 0.22 } });
      map.addLayer({ id: "clm-draw-line", type: "line", source: "clm-draw",
        filter: ["match", ["geometry-type"], ["LineString", "MultiLineString", "Polygon"], true, false],
        layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-color": cc, "line-width": ["case", sel, 5, 3] } });
      map.addLayer({ id: "clm-draw-pt", type: "circle", source: "clm-draw",
        filter: ["==", ["geometry-type"], "Point"],
        paint: {
          "circle-radius": ["case", ["==", ["get", "shape"], "circle"], 12, ["case", sel, 8, 6]],
          "circle-color": cc,
          "circle-opacity": ["case", ["==", ["get", "shape"], "circle"], 0, 0.95],
          "circle-stroke-color": ["case", sel, "#ffffff", cc],
          "circle-stroke-width": ["case", ["==", ["get", "shape"], "circle"], 3, ["case", sel, 2.5, 1.4]]
        } });
    }
    this._drawFeats = this._drawFeats || [];
    this._drawLayers = ["clm-draw-fill", "clm-draw-line", "clm-draw-pt"];
    // Map interactions for the active tool.
    var self = this;
    map.on("click", function (e) {
      if (self.tool === "inspect") self._onInspectClick(e);
      else if (self.tool === "distance") self._onDistClick(e);
      else if (self.tool === "draw") self._drawClick(e);
    });
  };

  // ---- INSPECT (map-native: nearest fix + active raster value where exposed) ----
  P._onInspectClick = function (e) {
    var out = this.dom.toolpanel && this.dom.toolpanel.querySelector("#clm-readout");
    if (!out) return;
    var lng = e.lngLat.lng, lat = e.lngLat.lat;
    var bits = ['<b>' + lat.toFixed(2) + '&deg;, ' + lng.toFixed(2) + '&deg;</b>'];
    // nearest track fix
    var pts = this.storm.points || [];
    var best = -1, bd = 1e9;
    for (var i = 0; i < pts.length; i++) {
      if (pts[i].lat == null) continue;
      var dx = (pts[i].lon - lng) * Math.cos(lat * Math.PI / 180), dy = pts[i].lat - lat;
      var d = dx * dx + dy * dy;
      if (d < bd) { bd = d; best = i; }
    }
    if (best >= 0) {
      var p = pts[best];
      // Honest about ALIGNMENT: the nearest fix is rarely AT the click, so report
      // how far away it is (great-circle) rather than implying it's the value here.
      var awayKm = this._haversineKm([lng, lat], [p.lon, p.lat]);
      bits.push('Nearest fix ' + fmtTime(p.t) + ' &middot; ' +
        (p.wind_kt != null ? Math.round(p.wind_kt) + ' kt' : '&mdash;') + ' ' +
        sshsLabel(p.cls || TATP().catForKt(p.wind_kt)) +
        (p.pressure_mb != null ? ' &middot; ' + Math.round(p.pressure_mb) + ' mb' : '') +
        ' <small>(' + Math.round(awayKm) + ' km away)</small>');
    }
    // Active imagery layer: report the layer + the frame currently shown (the
    // tiles are rendered images, not a value grid, so report the honest frame
    // time/coverage rather than a fabricated pixel value).
    var aL = this._layer(this.activeLayerId);
    if (aL && aL.type === "raster") {
      if (aL.unavailable) {
        bits.push(escapeHtml(aL.label) + ': <small>unavailable for archived storms</small>');
      } else if (aL._covered === false) {
        bits.push(escapeHtml(aL.label) + ': <small>no pass near this time</small>');
      } else {
        var fr = (aL.frames || [])[aL.activeFrame];
        if (typeof aL.sampleValue === "function" && fr) {
          try { var v = aL.sampleValue(lng, lat, fr); if (v != null) bits.push(escapeHtml(aL.label) + ': <b>' + escapeHtml(String(v)) + '</b>'); } catch (e2) {}
        } else if (fr && fr.time) {
          bits.push(escapeHtml(aL.label) + ' &middot; <small>' + fmtTime(fr.time) + '</small>');
        }
      }
    }
    out.innerHTML = bits.join("<br>");
  };

  // ---- DISTANCE (great-circle via turf if available, else haversine) ----
  P._onDistClick = function (e) {
    this._distPts.push([e.lngLat.lng, e.lngLat.lat]);
    if (this._distPts.length > 2) this._distPts = [[e.lngLat.lng, e.lngLat.lat]];
    this._refreshDistance();
  };
  P._refreshDistance = function () {
    if (!this.map || !this.map.getSource("clm-dist")) return;
    var pts = this._distPts || [];
    var feats = pts.map(function (c) {
      return { type: "Feature", geometry: { type: "Point", coordinates: c }, properties: {} };
    });
    var out = this.dom.toolpanel && this.dom.toolpanel.querySelector("#clm-distout");
    if (pts.length === 2) {
      var a = pts[0], b = pts[1];
      var line = (window.turf && window.turf.greatCircle)
        ? window.turf.greatCircle(a, b, { npoints: 64 })
        : { type: "Feature", geometry: { type: "LineString", coordinates: [a, b] }, properties: {} };
      feats.push(line);
      var km = this._haversineKm(a, b), brg = this._bearing(a, b);
      if (window.turf && window.turf.distance) {
        try { km = window.turf.distance(a, b, { units: "kilometers" }); } catch (e) {}
      }
      if (out) out.innerHTML = '<b>' + km.toFixed(0) + ' km</b> &middot; ' +
        (km * 0.539957).toFixed(0) + ' nmi<br>bearing ' + brg.toFixed(0) + '&deg;';
    } else if (out) {
      out.innerHTML = pts.length === 1 ? 'Tap a second point&hellip;' : '&mdash;';
    }
    this.map.getSource("clm-dist").setData({ type: "FeatureCollection", features: feats });
  };
  P._haversineKm = function (a, b) {
    var R = 6371, toR = Math.PI / 180;
    var dLat = (b[1] - a[1]) * toR, dLon = (b[0] - a[0]) * toR;
    var s = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
      Math.cos(a[1] * toR) * Math.cos(b[1] * toR) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
    return 2 * R * Math.atan2(Math.sqrt(s), Math.sqrt(1 - s));
  };
  P._bearing = function (a, b) {
    var toR = Math.PI / 180, toD = 180 / Math.PI;
    var y = Math.sin((b[0] - a[0]) * toR) * Math.cos(b[1] * toR);
    var x = Math.cos(a[1] * toR) * Math.sin(b[1] * toR) -
      Math.sin(a[1] * toR) * Math.cos(b[1] * toR) * Math.cos((b[0] - a[0]) * toR);
    return (Math.atan2(y, x) * toD + 360) % 360;
  };

  // ---- DRAW (native: freehand strokes + points, color, undo, clear) ----
  P._drawSetEnabled = function (on) {
    if (!this.map) return;
    var self = this;
    if (on && !this._drawWired) {
      this._drawWired = true;
      var canvas = this.map.getCanvasContainer();
      function px(ev) { return [ev.offsetX != null ? ev.offsetX : ev.layerX,
                                ev.offsetY != null ? ev.offsetY : ev.layerY]; }
      this._drawDown = function (ev) {
        if (self.tool !== "draw") return;
        // SELECT mode: grab the shape under the cursor + drag to move it.
        if (self._drawMode === "select") {
          var id = self._drawHitId(px(ev));
          self._drawSelect(id);
          if (id == null) return;
          ev.preventDefault(); self.map.dragPan.disable();
          var f = self._drawFeats.filter(function (x) { return x.properties.id === id; })[0];
          var last = self.map.unproject(px(ev));
          var mv = function (m2) {
            var ll = self.map.unproject(px(m2));
            _translateGeom(f.geometry, ll.lng - last.lng, ll.lat - last.lat);
            last = ll; self._drawRender();
          };
          var up = function () {
            canvas.removeEventListener("mousemove", mv); document.removeEventListener("mouseup", up);
            self.map.dragPan.enable();
          };
          canvas.addEventListener("mousemove", mv); document.addEventListener("mouseup", up);
          return;
        }
        // FREEHAND mode: drag a stroke.
        if (self._drawMode !== "freehand") return;
        ev.preventDefault(); self.map.dragPan.disable();
        self._stroke = { type: "Feature",
          geometry: { type: "LineString", coordinates: [] },
          properties: { shape: "freehand", color: self._drawColor } };
        var move = function (m2) {
          var ll = self.map.unproject(px(m2));
          self._stroke.geometry.coordinates.push([ll.lng, ll.lat]);
          self._drawRender(true);
        };
        var fup = function () {
          canvas.removeEventListener("mousemove", move); document.removeEventListener("mouseup", fup);
          self.map.dragPan.enable();
          if (self._stroke.geometry.coordinates.length > 1) {
            self._stroke.properties.id = "d" + (++self._drawId);
            self._drawFeats.push(self._stroke);
          }
          self._stroke = null; self._drawRender();
        };
        canvas.addEventListener("mousemove", move); document.addEventListener("mouseup", fup);
      };
      canvas.addEventListener("mousedown", this._drawDown);
    }
  };
  // Click router for the draw tool: select (hit-test) vs place a shape.
  P._drawClick = function (e) {
    if (this._drawMode === "select") { this._drawSelect(this._drawHitId(e.point)); return; }
    if (this._drawMode === "freehand") return;     // freehand is a drag, not a click
    this._drawPlace(e);
  };
  // Placement-relative shape half-size in degrees (so a shape is a sensible
  // on-screen size at the zoom it was placed; it then scales with the map).
  P._drawSize = function () {
    if (!this.map) return 0.5;
    var b = this.map.getBounds();
    return Math.max(0.12, (b.getEast() - b.getWest()) * 0.028);
  };
  P._drawPlace = function (e) {
    var lng = e.lngLat.lng, lat = e.lngLat.lat, s = this._drawSize();
    var c = this._drawColor, m = this._drawMode, id = "d" + (++this._drawId), f;
    function feat(geom) { return { type: "Feature", properties: { shape: m, color: c, id: id }, geometry: geom }; }
    if (m === "arrow") {
      if (!this._arrowTail) { this._arrowTail = [lng, lat]; this._drawHint("Tap the arrow head…"); return; }
      f = feat({ type: "LineString", coordinates: _arrowCoords(this._arrowTail, [lng, lat]) });
      this._arrowTail = null; this._drawHint("Tap the arrow tail, then the head.");
    } else if (m === "dot" || m === "circle") {
      f = feat({ type: "Point", coordinates: [lng, lat] });
    } else if (m === "square") {
      f = feat({ type: "Polygon", coordinates: [_sqCoords(lng, lat, s)] });
    } else if (m === "triangle") {
      f = feat({ type: "Polygon", coordinates: [_triCoords(lng, lat, s)] });
    } else if (m === "x") {
      f = feat({ type: "MultiLineString", coordinates: _xCoords(lng, lat, s) });
    } else { return; }
    this._drawFeats.push(f); this._drawRender();
  };
  P._drawHitId = function (point) {
    if (!this.map) return null;
    var fs = this.map.queryRenderedFeatures(point, { layers: this._drawLayers || [] });
    for (var i = 0; i < fs.length; i++) {
      var id = fs[i].properties && fs[i].properties.id; if (id != null) return id;
    }
    return null;
  };
  P._drawSelect = function (id) { this._drawSel = (id != null) ? id : null; this._drawRender(); };
  P._drawDeleteSel = function () {
    if (this._drawSel == null) return;
    var sel = this._drawSel;
    this._drawFeats = (this._drawFeats || []).filter(function (x) { return x.properties.id !== sel; });
    this._drawSel = null; this._drawRender();
  };
  P._drawHint = function (txt) {
    var el = this.dom.toolpanel && this.dom.toolpanel.querySelector('[data-act="drawhint"]');
    if (el) el.textContent = txt || "";
  };
  P._drawRender = function (withStroke) {
    if (!this.map || !this.map.getSource("clm-draw")) return;
    var sel = this._drawSel;
    var feats = (this._drawFeats || []).map(function (f) {
      f.properties.sel = (f.properties.id === sel); return f;
    });
    if (withStroke && this._stroke) feats.push(this._stroke);
    this.map.getSource("clm-draw").setData({ type: "FeatureCollection", features: feats });
  };
  P._drawUndo = function () { (this._drawFeats || []).pop(); this._drawSel = null; this._drawRender(); };
  P._drawClear = function () {
    this._drawFeats = []; this._drawSel = null; this._arrowTail = null; this._drawRender();
  };

  // ---- EXPORT (GL canvas + html2canvas overlay -> downloaded PNG) ----
  P.exportPng = function () {
    var self = this;
    if (!this.map) return;
    var glUrl;
    try { glUrl = this.map.getCanvas().toDataURL("image/png"); }
    catch (e) { return; }
    var w = this.map.getCanvas().width, h = this.map.getCanvas().height;
    function download(dataUrl) {
      var a = document.createElement("a");
      a.href = dataUrl;
      a.download = "cyclolab-" + (self.storm.sid || self.storm.name || "map") + ".png";
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
    }
    function composite(overlayCanvas) {
      var cv = document.createElement("canvas"); cv.width = w; cv.height = h;
      var ctx = cv.getContext("2d");
      var gl = new Image();
      gl.onload = function () {
        ctx.drawImage(gl, 0, 0, w, h);
        if (overlayCanvas) {
          try { ctx.drawImage(overlayCanvas, 0, 0, w, h); } catch (e) {}
        }
        download(cv.toDataURL("image/png"));
      };
      gl.src = glUrl;
    }
    // html2canvas captures the HTML overlays (glyph markers, controls); skip the
    // GL canvas (already captured) so it isn't drawn blank over the basemap.
    _ensureLib("https://unpkg.com/html2canvas@1.4.1/dist/html2canvas.min.js",
      "html2canvas", function () {
        if (!window.html2canvas) { composite(null); return; }
        window.html2canvas(self.dom.mapWrap, {
          backgroundColor: null, logging: false, scale: w / self.dom.mapWrap.clientWidth,
          ignoreElements: function (el) {
            return el.classList && el.classList.contains("maplibregl-canvas");
          }
        }).then(composite).catch(function () { composite(null); });
      });
  };

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
