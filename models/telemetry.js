/*
 * telemetry.js - per-ProductSpec VIEW COUNTERS for /models/.
 *
 * Why this exists: the hero-set scheduler needs a POPULARITY term. At ~50
 * models x 21 products the render budget cannot cover the full cross product,
 * so something has to decide which combinations are worth pre-rendering, and
 * "which products do people actually open" is the only honest input to that.
 * This module is that input. It is deliberately the smallest thing that
 * produces a defensible number.
 *
 * WHAT COUNTS AS A VIEW. Not a call - a call proves nothing. `_selectProduct`
 * in hafs.js fires from nine distinct paths (initial load, every cycle / storm
 * / model / domain change, the 45 s manifest poll's selection-regrow, and the
 * user's chip click), so counting calls would inflate a product's score by
 * whatever else the user happened to change and by how long they left the tab
 * open. A view here is a DISTINCT (cycle, storm, model, domain, product)
 * tuple: re-selecting the same tuple - which is exactly what the poll does -
 * is not a new view. Switching product and switching back IS, because the user
 * genuinely looked at it twice.
 *
 * DWELL. Each view also accumulates the seconds it stayed selected, because
 * "opened and immediately left" and "studied for two minutes" should not weigh
 * the same in a scheduler. Dwell stops accruing while the tab is hidden.
 *
 * STORAGE AND TRANSPORT. There is no analytics backend on this site and no
 * Worker with a KV/D1/Analytics-Engine binding, so by default the counters
 * aggregate LOCALLY in localStorage and go nowhere. That is still useful (the
 * numbers are inspectable via TatTelemetry.snapshot(), and instrumenting now
 * means no retrofit later), and it is the only option that ships without new
 * infrastructure. If `window.TAT_TELEMETRY_ENDPOINT` is set, batches are also
 * POSTed with navigator.sendBeacon on pagehide - fire-and-forget, never
 * blocking, never retried.
 *
 * PRIVACY. No identifiers of any kind: no user id, no session id, no cookie,
 * no IP-derived field, no timestamps finer than a UTC day. The payload is a
 * count per product. Do Not Track and Global Privacy Control are honored by
 * disabling collection entirely, not merely by withholding transmission.
 *
 * Dependency-free, ES5, safe to load before or after hafs.js.
 */
(function (w, d) {
  'use strict';

  var STORE_KEY = 'tat.models.telemetry.v1';
  // Bound the stored object so a long-lived browser profile cannot grow it
  // without limit. Products are ~21, so this is generous headroom; if it is
  // ever hit, the least-viewed entries are dropped first.
  var MAX_PRODUCTS = 200;
  // Dwell longer than this is a tab left open, not attention. Capped rather
  // than discarded so an idle tab cannot dominate the popularity term.
  var MAX_DWELL_S = 600;

  function optedOut() {
    try {
      if (w.doNotTrack === '1' || w.navigator.doNotTrack === '1' ||
          w.navigator.msDoNotTrack === '1') return true;
      if (w.navigator.globalPrivacyControl === true) return true;
    } catch (e) { /* a hostile environment counts as opted out */ }
    return false;
  }

  var DISABLED = optedOut();

  function utcDay() {
    try { return new Date().toISOString().slice(0, 10); } catch (e) { return ''; }
  }

  // --- storage (every access guarded: localStorage throws in some private
  // modes, and telemetry must never break the viewer) ----------------------
  function load() {
    try {
      var raw = w.localStorage.getItem(STORE_KEY);
      var o = raw ? JSON.parse(raw) : null;
      if (!o || typeof o !== 'object' || !o.products) throw new Error('shape');
      return o;
    } catch (e) {
      return { v: 1, since: utcDay(), products: {} };
    }
  }

  function save(state) {
    try {
      var keys = Object.keys(state.products);
      if (keys.length > MAX_PRODUCTS) {
        keys.sort(function (a, b) {
          return state.products[a].views - state.products[b].views;
        });
        for (var i = 0; i < keys.length - MAX_PRODUCTS; i++) {
          delete state.products[keys[i]];
        }
      }
      w.localStorage.setItem(STORE_KEY, JSON.stringify(state));
    } catch (e) { /* full or unavailable: drop the write, keep the page alive */ }
  }

  // --- current view ---------------------------------------------------------
  var currentKey = null;      // the deduped (cycle|storm|model|domain|product)
  var currentProduct = null;
  var dwellStart = 0;         // ms, 0 while hidden or with no active view

  function now() { return (w.Date && Date.now) ? Date.now() : +new Date(); }

  function bucket(state, product) {
    var b = state.products[product];
    if (!b) b = state.products[product] = { views: 0, dwell_s: 0 };
    return b;
  }

  /* Fold the elapsed dwell of the current view into storage and stop the clock. */
  function commitDwell() {
    if (!currentProduct || !dwellStart) return;
    var secs = Math.round((now() - dwellStart) / 1000);
    dwellStart = 0;
    if (secs <= 0) return;
    if (secs > MAX_DWELL_S) secs = MAX_DWELL_S;
    var state = load();
    bucket(state, currentProduct).dwell_s += secs;
    save(state);
  }

  /**
   * Record that `dims.product` is now being viewed.
   *
   * dims: {product, model, domain, storm, cycle}. Only `product` is required;
   * the rest form the dedupe tuple, so omitting them makes the dedupe coarser
   * (and the counts more conservative), never wrong.
   */
  function view(dims) {
    if (DISABLED || !dims || !dims.product) return;
    var key = [dims.cycle, dims.storm, dims.model, dims.domain, dims.product]
      .join('|');
    if (key === currentKey) return;   // the poll's re-selection: not a new view

    commitDwell();                    // close out the previous view first

    currentKey = key;
    currentProduct = dims.product;
    dwellStart = (d.visibilityState === 'hidden') ? 0 : now();

    var state = load();
    bucket(state, dims.product).views += 1;
    save(state);
  }

  /** The accumulated counters. Read by the hero-set scheduler / by hand. */
  function snapshot() {
    var s = load();
    return { v: s.v, since: s.since, products: s.products };
  }

  /** Zero the counters (after a successful upload, or for a clean measurement). */
  function reset() {
    try { w.localStorage.removeItem(STORE_KEY); } catch (e) { /* ignore */ }
    currentKey = null;
    currentProduct = null;
    dwellStart = 0;
  }

  /**
   * Best-effort ship. No-op unless window.TAT_TELEMETRY_ENDPOINT is set - there
   * is no collector deployed yet, so the default build stays purely local.
   */
  function flush() {
    if (DISABLED) return false;
    var url = w.TAT_TELEMETRY_ENDPOINT;
    if (!url || !w.navigator || !w.navigator.sendBeacon) return false;
    var snap = snapshot();
    var n = 0, k;
    for (k in snap.products) { if (snap.products.hasOwnProperty(k)) n++; }
    if (!n) return false;
    try {
      var body = new Blob([JSON.stringify({
        page: 'models', day: utcDay(), products: snap.products
      })], { type: 'application/json' });
      if (w.navigator.sendBeacon(url, body)) { reset(); return true; }
    } catch (e) { /* fire-and-forget: a failed send is simply lost */ }
    return false;
  }

  // Stop the dwell clock when the tab goes away; restart when it comes back.
  try {
    d.addEventListener('visibilitychange', function () {
      if (d.visibilityState === 'hidden') commitDwell();
      else if (currentProduct && !dwellStart) dwellStart = now();
    });
    // pagehide fires on bfcache navigation where unload does not.
    w.addEventListener('pagehide', function () { commitDwell(); flush(); });
  } catch (e) { /* no event support: counters still work, dwell is coarser */ }

  w.TatTelemetry = {
    view: view,
    snapshot: snapshot,
    reset: reset,
    flush: flush,
    disabled: DISABLED
  };
})(window, document);
