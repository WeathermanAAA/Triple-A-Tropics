// Triple-A-Tropics · GOES-East data-freshness probe + outage/recovery notice.
//
// Measures the age of the newest GOES-East scan actually published to the
// CDN and renders honest state into any `.sat-health-wrap` mount on the
// page, exposing the signal at window.TATSatHealth for page-specific chrome
// (pane badges, player notes, friendlier render-error copy). Three states,
// all data-driven so every transition happens on its own, no manual step:
//   PAUSED   newest scan across the monitored GOES-19 manifests is > 3 h
//            old · amber notice, with the NOAA return ETA while relevant
//   RESUMED  scans flowing again after an anomaly-window gap · shows the
//            NOAA first-hour navigation caveat for ~90 min, then clears
//   CLEAR    nothing rendered
//
// Signal choice (deliberate): the fd + conus `latest_times.json` `times`
// arrays (newest full-disk AND CONUS SCAN times; conus resumes first after
// an outage, so satellite-level state keys on the freshest of the two).
// Never `as_of` (a duplicate re-emit refreshes as_of with no new data) and
// never the floater manifests (the floater fleet can stall for
// non-satellite reasons; attributing that to GOES would be a lie).
// Threshold 3 h: the series is emitted hourly with 10-min slots, so the
// honest worst case is ~80 min plus dropped crons; 3 h is the same margin
// objfix's WP freshness gate uses.
//
// RESTORE DETECTION + NAV CAVEAT (2026-07-15/16 GOES-19 anomaly): NOAA
// warns navigation is "slightly degraded" for about the first hour after
// an ABI restore. The restore boundary is found in the DATA: a > 6 h gap
// in a manifest's scan list whose post-gap side falls inside the
// configured ANOMALY window. Frames scanned within NAV_MS after that
// boundary answer navDegraded(scanMs) = true so pages can tag them (player
// NAV CAVEAT readouts, objfix warnings, cockpit chrome). Scoped to the
// anomaly window ON PURPOSE: an ordinary producer stall also leaves a gap
// in the list, and blaming satellite navigation for a cron outage would be
// dishonest. Once ANOMALY.endByMs passes, no new caveats can trigger and
// the constant is inert; the paused/clear logic is evergreen.
//
// Usage in a page:
//   <div class="sat-health-wrap"></div>          <!-- optional banner mount -->
//   <script src="/sat-health.js"></script>
//   window.TATSatHealth.subscribe(function (h) { ... });
//   // h = { checked, stale, ageMs, latestMs, latestLabel,
//   //       resumeMs, resumeLabel } or checked:false
//   window.TATSatHealth.navDegraded(scanMs)  // first-hour-after-restore?

(function () {
  var CDN = "https://cdn.triple-a-tropics.com/shadow/sat/goes19/";
  var MANIFESTS = [CDN + "fd/ir/latest_times.json",
                   CDN + "conus/ir/latest_times.json"];
  var STALE_MS = 3 * 3600e3;
  var GAP_MS = 6 * 3600e3;    // a real outage, not a dropped cron or two
  var NAV_MS = 70 * 60e3;     // NOAA "first hour" + emit-stamp slack
  var NOTE_MS = 90 * 60e3;    // resumed-note visibility after the boundary
  var POLL_MS = 5 * 60e3;

  // The 2026-07-15 GOES-19 anomaly (NOAA): ABI paused ~20:20Z 15 Jul,
  // imagery expected back ~19:00Z 16 Jul (CONUS actually resumed 17:16Z).
  // Only gaps ending inside this window count as the ABI restore.
  var ANOMALY = {
    startMs: Date.UTC(2026, 6, 15, 17, 0),
    endByMs: Date.UTC(2026, 6, 18, 0, 0),
    etaMs: Date.UTC(2026, 6, 16, 19, 0),
    etaLabel: "~19:00 UTC 16 Jul"
  };

  var state = { checked: false, stale: false, ageMs: 0, latestMs: 0,
                latestLabel: "", resumeMs: 0, resumeLabel: "" };
  var subs = [];

  function stampMs(t) {
    // "20260715T202021Z" -> epoch ms
    var m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/.exec(t || "");
    if (!m) return NaN;
    return Date.parse(m[1] + "-" + m[2] + "-" + m[3] + "T" + m[4] + ":" + m[5] + ":" + m[6] + "Z");
  }
  function fmtZ(ms) {
    var d = new Date(ms);
    function p(n) { return String(n).padStart(2, "0"); }
    return d.getUTCFullYear() + "-" + p(d.getUTCMonth() + 1) + "-" + p(d.getUTCDate()) +
      " " + p(d.getUTCHours()) + ":" + p(d.getUTCMinutes()) + "Z";
  }
  function fmtAge(ms) {
    var h = ms / 3600e3;
    return h >= 48 ? Math.round(h / 24) + " days" : (h >= 9.95 ? Math.round(h) : h.toFixed(1)) + " h";
  }

  function emit() {
    subs.forEach(function (fn) {
      try { fn(state); } catch (e) { /* one bad subscriber never blocks the rest */ }
    });
    renderBanners();
  }

  // newest scan + ABI-restore boundary of ONE manifest's times list
  function scanManifest(m) {
    var times = (m && m.times && m.times.length) ? m.times
              : (m && m.latest ? [m.latest] : []);
    var latest = NaN, gapEnd = 0, prev = NaN;
    for (var i = 0; i < times.length; i++) {
      var t = stampMs(times[i]);
      if (!isFinite(t)) continue;
      if (isFinite(prev) && t - prev > GAP_MS &&
          t >= ANOMALY.startMs && t <= ANOMALY.endByMs) gapEnd = t;
      if (!(latest >= t)) latest = t;
      prev = t;
    }
    return { latest: latest, gapEnd: gapEnd };
  }

  function fetchManifest(url) {
    return fetch(url + "?t=" + Date.now(), { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
  }

  function check() {
    return Promise.all(MANIFESTS.map(fetchManifest)).then(function (ms) {
      var latest = NaN, ends = [];
      ms.forEach(function (m) {
        var s = scanManifest(m);
        if (isFinite(s.latest) && !(latest >= s.latest)) latest = s.latest;
        if (s.gapEnd) ends.push(s.gapEnd);
      });
      if (!isFinite(latest)) return state; // nothing readable: keep last state
      var age = Date.now() - latest;
      // earliest boundary = the instrument restore (see navDegraded)
      var resume = ends.length ? Math.min.apply(null, ends) : 0;
      state = {
        checked: true,
        stale: age > STALE_MS,
        ageMs: age,
        latestMs: latest,
        latestLabel: fmtZ(latest),
        resumeMs: resume,
        resumeLabel: resume ? fmtZ(resume) : ""
      };
      emit();
      return state;
    });
  }

  // Is a frame scanned inside the first hour after the ABI restore?
  // NOAA's caveat is about the INSTRUMENT restore, so only the EARLIEST
  // boundary counts: fd products resuming hours after conus are late
  // EMITS of a long-recovered instrument, not a second nav event.
  function navDegraded(scanMs) {
    if (!isFinite(scanMs) || !state.resumeMs) return false;
    return scanMs >= state.resumeMs && scanMs - state.resumeMs <= NAV_MS;
  }
  function navWindow() {
    if (!state.resumeMs) return null;
    return { fromMs: state.resumeMs, toMs: state.resumeMs + NAV_MS };
  }

  // Shared copy for the banner + page chrome (cockpit renders its own note
  // from this, so the wording can never drift between surfaces).
  // Attribution honesty: the "GOES-19 anomaly (NOAA)" copy is used ONLY
  // while the stall itself lies inside the configured ANOMALY window — a
  // future stall (satellite OR our own producer) must never be blamed on
  // a long-past NOAA event, so outside the window the paused notice
  // states the data age and nothing more (kind: "paused-generic").
  function notice() {
    var now = Date.now();
    if (!state.checked) return null;
    if (state.stale) {
      var isAnomaly = state.latestMs >= ANOMALY.startMs &&
                      now <= ANOMALY.endByMs;
      if (isAnomaly) {
        var eta = now < ANOMALY.etaMs + 3 * 3600e3
          ? "imagery expected back " + ANOMALY.etaLabel + " (NOAA)"
          : "recovery underway";
        return {
          kind: "paused",
          headline: "GOES-East imagery paused",
          detail: "GOES-19 satellite anomaly (NOAA) · last scan " +
            state.latestLabel + " (" + fmtAge(state.ageMs) + " ago) · " + eta
        };
      }
      return {
        kind: "paused-generic",
        headline: "GOES-East imagery paused",
        detail: "no new scans on this feed since " + state.latestLabel +
          " (" + fmtAge(state.ageMs) + " ago) · players fall back to the " +
          "newest available frames"
      };
    }
    if (state.resumeMs && now - state.resumeMs < NOTE_MS) {
      return {
        kind: "resumed",
        headline: "GOES-East imagery resumed",
        detail: "back since " + state.resumeLabel + " after the GOES-19 " +
          "anomaly · NOAA: navigation may be slightly degraded for about " +
          "the first hour of restored frames · affected frames are tagged " +
          "NAV CAVEAT and this note clears itself"
      };
    }
    return null;
  }

  var styled = false;
  function ensureStyle() {
    if (styled) return;
    styled = true;
    var css =
      ".sat-health-note{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;" +
      "margin:0 0 14px;padding:10px 14px;border:1px solid rgba(210,169,63,.45);" +
      "border-left:3px solid #d2a93f;border-radius:6px;background:rgba(210,169,63,.08);" +
      "color:#d2a93f;font:600 12.5px/1.45 Metropolis,system-ui,sans-serif;letter-spacing:.3px}" +
      ".sat-health-note b{color:#e6c968;font-weight:800}" +
      ".sat-health-note span{color:rgba(210,169,63,.85);font-weight:500}" +
      ".sat-health-wrap:empty{display:none}";
    var el = document.createElement("style");
    el.textContent = css;
    document.head.appendChild(el);
  }

  function renderBanners() {
    var mounts = document.querySelectorAll(".sat-health-wrap");
    if (!mounts.length) return;
    ensureStyle();
    var n = notice();
    mounts.forEach(function (mount) {
      if (!n) { mount.innerHTML = ""; return; }
      var tail = n.kind === "paused"
        ? " · GOES-West and Himawari products are unaffected" : "";
      mount.innerHTML =
        '<div class="sat-health-note" role="status">' +
        "<b>" + n.headline + "</b>" +
        "<span>" + n.detail + tail + "</span>" +
        "</div>";
    });
  }

  window.TATSatHealth = {
    get: function () { return state; },
    subscribe: function (fn) {
      subs.push(fn);
      if (state.checked) { try { fn(state); } catch (e) {} }
    },
    check: check,
    fmtAge: fmtAge,
    navDegraded: navDegraded,
    navWindow: navWindow,
    notice: notice
  };

  function start() {
    check();
    // re-render on a 1-min tick too: the resumed note and NAV CAVEAT tags
    // are time-window states that must clear even between polls
    setInterval(function () { if (!document.hidden) check(); }, POLL_MS);
    setInterval(function () { if (!document.hidden) { renderBanners(); } }, 60e3);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
