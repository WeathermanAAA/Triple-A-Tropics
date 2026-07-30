// Triple-A-Tropics · "Active Now" banner for each basin's tracks page.
// Runs on parent pages (e.g. /climatology/west-pacific/tracks/) and
// fetches that basin's JSON payload to render a big top-of-page banner
// with the current intensity of any storm JTWC/NHC is warning on.
//
// Usage in the parent page HTML (data-src now points at the R2 feed so
// the streaming poller can refresh it without a git commit; the fetch
// below already cache-busts with ?t= + cache:"no-store"):
//   <div class="active-banner-wrap"
//        data-src="https://cdn.triple-a-tropics.com/feeds/wp_tracks_data.json"></div>
//   <script src="/active-banner.js" defer></script>
//
// If no storm is active, the wrap stays empty (and CSS hides it).

(function () {
  var SSHS_COLORS = {
    "TD": "#3fa4ff", "TS": "#46c56a", "C1": "#ffe14d",
    "C2": "#ff9a2f", "C3": "#ff4d3b", "C4": "#e33ad4", "C5": "#b03bff"
  };
  var CAT_LABELS = {
    "TD": "Depression", "TS": "Tropical Storm",
    "C1": "Category 1", "C2": "Category 2", "C3": "Category 3",
    "C4": "Category 4", "C5": "Category 5"
  };
  // Same icon path the map uses, so the banner's corner spinner matches
  // the spinning hurricane placed over the active storm.
  var HURRICANE_PATH = "M 16.37,-28.27 C 13.58,-28.13 11.51,-27.90 9.23,-27.49 C 1.27,-26.06 -5.88,-22.70 -10.92,-18.02 C -14.83,-14.40 -17.41,-10.06 -18.49,-5.32 C -18.95,-3.30 -19.15,-1.42 -19.15,0.91 C -19.15,2.53 -19.09,3.28 -18.89,4.45 C -18.38,7.38 -17.47,9.46 -15.41,12.37 C -13.88,14.54 -13.43,15.31 -13.20,16.13 C -13.11,16.44 -13.09,16.62 -13.09,17.14 C -13.10,17.93 -13.20,18.32 -13.67,19.28 C -15.30,22.59 -18.65,24.93 -23.49,26.14 C -25.26,26.58 -27.29,26.87 -29.18,26.95 L -30.00,26.98 L -29.65,27.06 C -27.33,27.62 -24.41,28.05 -21.57,28.27 C -20.04,28.38 -16.31,28.38 -14.80,28.27 C -12.93,28.13 -11.43,27.95 -9.77,27.67 C -0.59,26.14 7.56,22.03 12.68,16.37 C 16.22,12.45 18.28,8.10 18.93,3.13 C 19.64,-2.25 18.99,-6.47 16.84,-10.16 C 16.48,-10.80 15.79,-11.82 14.99,-12.95 C 13.61,-14.89 13.18,-15.77 13.12,-16.83 C 13.07,-17.61 13.23,-18.26 13.71,-19.23 C 14.97,-21.79 17.38,-23.84 20.67,-25.16 C 23.13,-26.14 26.24,-26.77 29.15,-26.87 L 30.00,-26.90 L 29.67,-26.98 C 29.13,-27.12 27.57,-27.44 26.66,-27.58 C 24.96,-27.87 23.39,-28.05 21.66,-28.18 C 20.72,-28.25 17.16,-28.30 16.37,-28.27 Z";

  function ktToMph(k) { return Math.round(k * 1.15077945); }
  function ktToKmh(k) { return Math.round(k * 1.852); }
  function esc(s) {
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
  function fmtLatLon(lat, lon) {
    while (lon > 180) lon -= 360;
    while (lon < -180) lon += 360;
    var la = Math.abs(lat).toFixed(1) + "\u00B0 " + (lat >= 0 ? "N" : "S");
    var lo = Math.abs(lon).toFixed(1) + "\u00B0 " + (lon >= 0 ? "E" : "W");
    return la + "   " + lo;
  }
  function compass(b) {
    var dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
                "S","SSW","SW","WSW","W","WNW","NW","NNW"];
    return dirs[Math.round(b / 22.5) % 16];
  }
  function computeMovement(pts) {
    for (var i = pts.length - 2; i >= 0; i--) {
      var a = pts[i], b = pts[pts.length - 1];
      var ta = new Date(a.t).getTime(), tb = new Date(b.t).getTime();
      var dtH = (tb - ta) / 3600000;
      if (dtH < 1) continue;
      var latm = (b.lat - a.lat) * 60;
      var lonm = (b.lon - a.lon) * 60 * Math.cos((a.lat + b.lat) / 2 * Math.PI / 180);
      var dist = Math.sqrt(latm*latm + lonm*lonm);
      if (dist < 0.5) return "Nearly stationary";
      var kt = dist / dtH;
      var bearing = (Math.atan2(lonm, latm) * 180 / Math.PI + 360) % 360;
      return compass(bearing) + " at " + ktToMph(kt) + " mph";
    }
    return "-";
  }
  function bannerTextColor(cls) {
    // Dark text on bright yellow/green/orange; white on the reds/magenta/purples.
    return (cls === "TS" || cls === "C1" || cls === "C2") ? "#0a1324" : "#ffffff";
  }
  function sshsLabel(cls) {
    if (cls === "TD") return "D";
    if (cls === "TS") return "S";
    return (cls || "").replace("C", "") || "D";  // C1→1, C2→2, etc.
  }
  function spinnerSvg(color, cls) {
    // <animateTransform> spins the hurricane path only; the center
    // label (D/S/1–5) sits outside the rotating group so it stays still.
    var label = sshsLabel(cls);
    return '<div class="ab-spinner">' +
      '<svg viewBox="-34 -34 68 68" aria-hidden="true">' +
        '<g>' +
          '<path d="' + HURRICANE_PATH + '" fill="' + color + '" ' +
            'stroke="rgba(0,0,0,0.35)" stroke-width="1.2"/>' +
          '<animateTransform attributeName="transform" attributeType="XML" ' +
            'type="rotate" from="360" to="0" dur="2.6s" repeatCount="indefinite"/>' +
        '</g>' +
        '<text x="0" y="0" text-anchor="middle" dominant-baseline="central" ' +
          'font-size="28" font-weight="900" fill="#ffffff" ' +
          'paint-order="stroke" stroke="rgba(0,0,0,0.6)" stroke-width="2.4" ' +
          'stroke-linejoin="round">' + label + '</text>' +
      '</svg>' +
    '</div>';
  }

  function renderBanner(storm) {
    var pts = storm.points || [];
    var valid = pts.filter(function (p) { return p.wind_kt != null; });
    var last = pts[pts.length - 1] || {};
    var lastValid = valid[valid.length - 1] || last;
    var cls = storm.current_category || "TD";
    var color = SSHS_COLORS[cls] || "#888";
    var txt = bannerTextColor(cls);
    var cat = CAT_LABELS[cls] || cls;
    var windKt = lastValid.wind_kt || 0;
    var pres = lastValid.pressure_mb;
    var loc = (last.lat != null && last.lon != null) ? fmtLatLon(last.lat, last.lon) : "-";
    var movement = computeMovement(pts);
    return (
      '<div class="active-banner" style="background:' + color + ';color:' + txt + '">' +
        spinnerSvg(color, cls) +
        '<div class="ab-title"><span class="ab-cat">' + cat + '</span><b>' +
          esc(storm.name || "UNNAMED") + '</b></div>' +
        '<div class="ab-intensity">' +
          '<div class="ab-big">' + ktToMph(windKt) + '</div>' +
          '<div class="ab-units">mph<br>' + ktToKmh(windKt) + ' km/h</div>' +
        '</div>' +
        '<div class="ab-deets">' +
          '<div><span>Updated</span><b>' + fmtTime(last.t) + '</b></div>' +
          '<div><span>Location</span><b>' + loc + '</b></div>' +
          '<div><span>Pressure</span><b>' + (pres ? Math.round(pres) + " mb" : "-") + '</b></div>' +
          '<div><span>Movement</span><b>' + movement + '</b></div>' +
        '</div>' +
      '</div>'
    );
  }

  function init() {
    var mounts = document.querySelectorAll(".active-banner-wrap[data-src]");
    mounts.forEach(function (mount) {
      var url = mount.getAttribute("data-src");
      // Cache-bust so we always see the freshest JSON that GitHub Pages
      // has published (otherwise the browser can serve a stale copy for
      // up to a few minutes after the workflow deploys).
      var bust = url + (url.indexOf("?") >= 0 ? "&" : "?") + "t=" + Date.now();
      fetch(bust, { cache: "no-store" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data || !data.storms) return;
          var actives = data.storms.filter(function (s) { return s.is_active; });
          if (!actives.length) return;
          mount.innerHTML =
            '<div class="ab-label">Active Now</div>' +
            actives.map(renderBanner).join("");
        })
        .catch(function () { /* silently omit banner on fetch failure */ });
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
