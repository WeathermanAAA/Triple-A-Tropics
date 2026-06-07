// Render harness for the GLOBAL MapLibre page's active markers.
//
// Driven by tests/test_invest_x_anchor.py. Loads a rendered
// global_tracks.html under jsdom with maplibre-gl stubbed out, feeds the
// page a fixture global_storms.geojson via a stubbed fetch, and dumps
// every HTML marker the page creates (anchor mode, lngLat, classes,
// innerHTML) plus the page's <style> text so the Python side can assert
// the anchoring invariant chain:
//
//   maplibregl.Marker anchor:"center"
//   + CSS box == viewBox dimensions (1 SVG unit == 1 CSS px)
//   + viewBox symmetric about (0,0)
//   + glyph path centered on (0,0)
//   => glyph center renders EXACTLY on the projected fix pixel, and side
//      labels (offset <text> siblings overflowing the box) cannot move it.
//
// Requires jsdom (same dependency as dom_smoke.cjs):
//   npm install --no-save jsdom
//
// Usage: node global_map_harness.cjs <global_tracks.html> <geojson.json>
"use strict";
const fs = require("fs");
const { JSDOM } = require("jsdom");

const PAGE = fs.readFileSync(process.argv[2], "utf8");
const GEOJSON = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));

const markers = [];

function stubMapLibre(window) {
  function Map() {
    this.dragRotate = { disable: function () {} };
    this.touchZoomRotate = { disableRotation: function () {} };
    this.doubleClickZoom = { disable: function () {} };
  }
  Map.prototype.addControl = function () { return this; };
  Map.prototype.on = function (evt, a) {
    // Fire "load" asynchronously like the real map; layer-scoped events
    // (mousemove etc.) are never fired by the harness.
    if (evt === "load" && typeof a === "function") setTimeout(a, 0);
    return this;
  };
  Map.prototype.easeTo = function () {};
  Map.prototype.addSource = function () {};
  Map.prototype.addLayer = function () {};
  Map.prototype.getCanvas = function () { return { style: {} }; };
  // hasImage -> true keeps registerPhaseIcons() off jsdom's canvas-less
  // 2D context (the SDF icons are irrelevant to marker anchoring).
  Map.prototype.hasImage = function () { return true; };
  Map.prototype.addImage = function () {};

  function NavigationControl() {}

  function Marker(opts) { this.opts = opts || {}; }
  Marker.prototype.setLngLat = function (ll) { this.lngLat = ll; return this; };
  Marker.prototype.addTo = function () {
    var el = this.opts.element;
    markers.push({
      anchor: this.opts.anchor,
      lngLat: this.lngLat,
      className: el ? String(el.className) : "",
      html: el ? el.innerHTML : "",
    });
    return this;
  };
  Marker.prototype.remove = function () {};

  function Popup() {}
  Popup.prototype.setLngLat = function () { return this; };
  Popup.prototype.setHTML = function () { return this; };
  Popup.prototype.addTo = function () { return this; };
  Popup.prototype.on = function () { return this; };
  Popup.prototype.remove = function () {};

  window.maplibregl = {
    Map: Map,
    NavigationControl: NavigationControl,
    Marker: Marker,
    Popup: Popup,
  };
  window.fetch = function () {
    return Promise.resolve({
      json: function () { return Promise.resolve(GEOJSON); },
    });
  };
}

const dom = new JSDOM(PAGE, {
  runScripts: "dangerously",
  url: "https://triple-a-tropics.com/global_tracks.html",
  beforeParse: stubMapLibre,
});

// load-handler fires on a macrotask, fetch settles on microtasks; one
// generous macrotask covers both.
setTimeout(function () {
  var styles = Array.prototype.map.call(
    dom.window.document.querySelectorAll("style"),
    function (s) { return s.textContent; }).join("\n");
  process.stdout.write(JSON.stringify({ markers: markers, css: styles }));
}, 250);
