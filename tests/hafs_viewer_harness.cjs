// Node harness for the HAFS progressive-frames viewer (models/hafs.js).
//
// Loads hafs.js under node behind a minimal DOM shim (getElementById /
// createElement / classList / style / events, plus new Image()), feeds it a
// sequence of manifests through a controllable fetch, and emits a JSON probe of
// the viewer's internal state after each step so tests/test_hafs_viewer.py can
// assert the progressive-load behavior offline.
//
//   node hafs_viewer_harness.cjs <hafs.js> <plan.json>
//
// plan.json:
//   { "manifests": [ <manifest1>, <manifest2>, ... ],
//     "actions":   [ {<action>}, ... ] }   // applied after the first manifest
//
// Actions (executed in order, each followed by a state snapshot):
//   {"op":"poll"}                       -> deliver the NEXT manifest via _poll()
//   {"op":"selectCycle","key":"..."}    -> _selectCycle(key, false)
//   {"op":"selectStorm","id":"..."}     -> _selectStorm(id, false)
//   {"op":"selectModel","slug":"..."}   -> _selectModel(slug, true)
//   {"op":"selectDomain","slug":"..."}  -> _selectDomain(slug, true)
//   {"op":"selectProduct","slug":"..."} -> _selectProduct(slug, true)
//   {"op":"clickHour","fxx":N}          -> click the F{N} hour-grid button
//                                          (inert when pending/disabled)
//   {"op":"clickBadge"}                 -> click the pending-cycle "view" button
//   {"op":"snapshot"}                   -> just record state
//
// Output: {"steps":[<state>, ...]} where steps[0] is after the first manifest
// load and steps[i>0] is after actions[i-1].
"use strict";

const fs = require("fs");
const path = require("path");

// ---- minimal DOM shim ------------------------------------------------------

function StubClassList() { this._set = {}; }
StubClassList.prototype.add = function (c) { this._set[c] = true; };
StubClassList.prototype.remove = function (c) { delete this._set[c]; };
StubClassList.prototype.contains = function (c) { return !!this._set[c]; };
StubClassList.prototype.toggle = function (c, on) {
  if (on === undefined) on = !this._set[c];
  if (on) this._set[c] = true; else delete this._set[c];
  return on;
};

function StubEl(tag) {
  this.tagName = (tag || "DIV").toUpperCase();
  this.children = [];
  this.parentNode = null;
  this.classList = new StubClassList();
  this.style = {};
  this._attrs = {};
  this._listeners = {};
  this._text = "";
  this._html = "";
  this.value = "";
  this.disabled = false;
  this.min = 0;
  this.max = 0;
  this.src = "";
  this.type = "";
}
Object.defineProperty(StubEl.prototype, "className", {
  get: function () { return Object.keys(this.classList._set).join(" "); },
  set: function (v) {
    this.classList = new StubClassList();
    String(v || "").split(/\s+/).forEach((c) => { if (c) this.classList.add(c); });
  },
});
Object.defineProperty(StubEl.prototype, "textContent", {
  get: function () {
    if (this.children.length) return this.children.map((c) => c.textContent).join("");
    return this._text;
  },
  set: function (v) { this._text = String(v); this.children = []; this._html = ""; },
});
Object.defineProperty(StubEl.prototype, "innerHTML", {
  get: function () { return this._html; },
  set: function (v) { this._html = String(v); this.children = []; this._text = ""; },
});
StubEl.prototype.appendChild = function (c) { c.parentNode = this; this.children.push(c); return c; };
StubEl.prototype.replaceChild = function (n, o) {
  const i = this.children.indexOf(o);
  if (i >= 0) { this.children[i] = n; n.parentNode = this; }
  return o;
};
StubEl.prototype.setAttribute = function (k, v) { this._attrs[k] = String(v); };
StubEl.prototype.getAttribute = function (k) { return this._attrs.hasOwnProperty(k) ? this._attrs[k] : null; };
StubEl.prototype.addEventListener = function (ev, fn) {
  (this._listeners[ev] = this._listeners[ev] || []).push(fn);
};
StubEl.prototype._fire = function (ev, evtObj) {
  (this._listeners[ev] || []).forEach((fn) => fn.call(this, evtObj || {}));
};
function _matchSel(el, sel) {
  if (sel.charAt(0) === ".") return el.classList.contains(sel.slice(1));
  return el.tagName === sel.toUpperCase();
}
StubEl.prototype.querySelectorAll = function (sel) {
  const out = [];
  (function walk(node) {
    node.children.forEach((c) => { if (_matchSel(c, sel)) out.push(c); walk(c); });
  })(this);
  return out;
};
StubEl.prototype.querySelector = function (sel) {
  const all = this.querySelectorAll(sel);
  return all.length ? all[0] : null;
};

const ELEMENTS = {};
function getEl(id) {
  if (!ELEMENTS[id]) { ELEMENTS[id] = new StubEl("div"); ELEMENTS[id]._id = id; }
  return ELEMENTS[id];
}

// The element ids hafs.js looks up; #hafs-status needs a queryable child span.
[
  "hafs-stage", "hafs-img", "hafs-status", "hafs-empty", "hafs-controls",
  "hafs-cycle-group", "hafs-cycles", "hafs-storm", "hafs-models", "hafs-domains",
  "hafs-products", "hafs-hours", "hafs-play", "hafs-step-back",
  "hafs-step-fwd", "hafs-speed", "hafs-fhour", "hafs-valid", "hafs-meta",
  "hafs-badge", "hafs-pill", "hafs-buffer", "hafs-player", "hafs-caption",
  "hafs-viewer",
].forEach(getEl);
{
  const span = new StubEl("span");
  getEl("hafs-status").appendChild(span);
}
// hafs.js's _buildToggle reads `container.parentNode.style` to hide a 1-option
// group, mirroring the real .hafs-group wrappers. Mirror the page markup: the
// cycle toggle host lives inside #hafs-cycle-group; the others get a fresh
// .hafs-group wrapper each.
getEl("hafs-cycle-group").appendChild(getEl("hafs-cycles"));
["hafs-models", "hafs-domains", "hafs-products"].forEach((id) => {
  const wrap = new StubEl("div");
  wrap.appendChild(getEl(id));
});
// #hafs-cycle-group starts hidden in the page; the shim has no inline-style
// parsing, so seed it to match the markup.
getEl("hafs-cycle-group").style.display = "none";
getEl("hafs-pill").style.display = "none";
getEl("hafs-badge").style.display = "none";
getEl("hafs-buffer").style.display = "none";

global.document = {
  getElementById: getEl,
  createElement: (tag) => new StubEl(tag),
  addEventListener: function () {},   // DOMContentLoaded - we instantiate manually
};

// new Image() preload: resolve onload synchronously-ish on a microtask so the
// buffering counter advances without real network.
global.Image = function () {
  const self = this;
  this.onload = null; this.onerror = null;
  Object.defineProperty(this, "src", {
    get: function () { return self._src; },
    set: function (v) {
      self._src = v;
      Promise.resolve().then(() => { if (self.onload) self.onload(); });
    },
  });
};

// ---- controllable fetch ----------------------------------------------------

const PLAN = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const MANIFESTS = PLAN.manifests || [];
let manifestCursor = 0;

const FETCHED_URLS = [];

global.fetch = function (url) {
  // Hand out manifests in sequence; the first _load() takes index 0, each
  // subsequent _poll() advances. Clamp at the last so extra polls re-deliver it.
  FETCHED_URLS.push(String(url));
  const idx = Math.min(manifestCursor, MANIFESTS.length - 1);
  const body = MANIFESTS[idx];
  manifestCursor = Math.min(manifestCursor + 1, MANIFESTS.length);
  return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
};

// Stub timers we don't want to actually fire (poll scheduling); the harness
// drives polls explicitly via the "poll" action.
const _setTimeout = global.setTimeout;
global.setTimeout = function (fn, ms) {
  // Allow zero/short flush timers (used by tests to drain microtasks) but
  // swallow the long poll scheduler so node exits and polls stay explicit.
  if (ms && ms >= 1000) return 0;
  return _setTimeout(fn, ms);
};
global.clearTimeout = function () {};
global.setInterval = function () { return 0; };
global.clearInterval = function () {};
// The player paces playback on requestAnimationFrame (the satellite canon).
// These behavioral tests never enter playback (no "play" action, no autoplay),
// so a no-op stub is enough to satisfy the symbol if a play path is ever added.
global.requestAnimationFrame = function () { return 0; };
global.cancelAnimationFrame = function () {};

// ---- load the viewer -------------------------------------------------------

const mod = require(path.resolve(process.argv[2]));
const HafsViewer = mod.HafsViewer;

// drain queued microtasks/macrotasks
function flush() {
  return new Promise((res) => _setTimeout(res, 0)).then(
    () => new Promise((res) => _setTimeout(res, 0)));
}

function snapshot(v) {
  return {
    legacyMode: v.legacyMode,
    cycleKeys: v.cycles.map((c) => c.cycle),
    selectedCycle: v.cycle ? v.cycle.cycle : null,
    inProgress: v.cycle ? !!v.cycle.in_progress : null,
    storm: v.storm ? v.storm.id : null,
    model: v.model,
    domain: v.domain,
    product: v.product,
    fxxList: v.fxxList.slice(),
    fxxGrid: v.fxxGrid.slice(),
    idx: v.idx,
    fxx: v.fxxList.length ? v.fxxList[v.idx] : null,
    imgSrc: getEl("hafs-img").src,
    fhour: getEl("hafs-fhour").textContent,
    valid: getEl("hafs-valid").textContent,
    hours: getEl("hafs-hours").children.map((b) => ({
      fxx: parseInt(b.getAttribute("data-fxx"), 10),
      label: b.textContent,
      lit: b.classList.contains("lit"),
      pending: b.classList.contains("pending"),
      current: b.classList.contains("current"),
      disabled: !!b.disabled,
      // five-state availability (item 17): lit / pending / unavail / unsched
      state: b.getAttribute("data-state") || null,
    })),
    cyclePickerShown: getEl("hafs-cycle-group").style.display !== "none",
    cycleButtons: getEl("hafs-cycles").children.map((b) => ({
      slug: b.getAttribute("data-slug"),
      label: b.textContent,
      active: b.classList.contains("active"),
    })),
    pendingCycleKey: v.pendingCycleKey,
    preAnnounce: v.preAnnounce,
    badgeShown: getEl("hafs-badge").style.display !== "none",
    badgeText: getEl("hafs-badge").textContent,
    pillShown: getEl("hafs-pill").style.display !== "none",
    pillText: getEl("hafs-pill").textContent,
    meta: getEl("hafs-meta").textContent,
    emptyShown: getEl("hafs-empty").style.display === "block",
    stormOptions: getEl("hafs-storm").children.map((o) => o.value),
    // mount-config probes (CycloLab second mount)
    stormSelHidden: getEl("hafs-storm").style.display === "none",
    fetchedUrls: FETCHED_URLS.slice(),
  };
}

(async () => {
  // viewer_opts (plan.viewer_opts): mount-config pass-through. els_injected
  // builds an EXPLICIT element table from the hafs-* stubs and passes it as
  // opts.els - and then poisons document.getElementById for hafs-* ids, so
  // any residual global lookup in the viewer crashes the harness loudly.
  const vOpts = PLAN.viewer_opts ? Object.assign({}, PLAN.viewer_opts) : null;
  if (vOpts && vOpts.els_injected) {
    delete vOpts.els_injected;
    const table = {
      stage: getEl("hafs-stage"), img: getEl("hafs-img"),
      status: getEl("hafs-status"), empty: getEl("hafs-empty"),
      controls: getEl("hafs-controls"), cycleGroup: getEl("hafs-cycle-group"),
      cycles: getEl("hafs-cycles"), stormSel: getEl("hafs-storm"),
      models: getEl("hafs-models"), domains: getEl("hafs-domains"),
      products: getEl("hafs-products"), hours: getEl("hafs-hours"),
      play: getEl("hafs-play"), stepB: getEl("hafs-step-back"),
      stepF: getEl("hafs-step-fwd"), speed: getEl("hafs-speed"),
      fhour: getEl("hafs-fhour"), valid: getEl("hafs-valid"),
      meta: getEl("hafs-meta"), badge: getEl("hafs-badge"),
      pill: getEl("hafs-pill"), buffer: getEl("hafs-buffer"),
      player: getEl("hafs-player"), caption: getEl("hafs-caption"),
    };
    vOpts.els = table;
    const realGetEl = global.document.getElementById;
    global.document.getElementById = function (id) {
      if (/^hafs-/.test(id)) {
        throw new Error("global el('" + id + "') lookup despite injected els");
      }
      return realGetEl(id);
    };
  }
  const viewer = vOpts ? new HafsViewer(getEl("hafs-viewer"), vOpts)
                       : new HafsViewer(getEl("hafs-viewer"));
  await flush();

  const steps = [snapshot(viewer)];

  const actions = PLAN.actions || [];
  for (const a of actions) {
    switch (a.op) {
      case "poll":
        viewer._poll();
        await flush();
        break;
      case "selectCycle":
        viewer._selectCycle(a.key, false);
        await flush();
        break;
      case "selectStorm":
        viewer._selectStorm(a.id, false);
        await flush();
        break;
      case "selectModel":
        viewer._selectModel(a.slug, true);
        await flush();
        break;
      case "selectDomain":
        viewer._selectDomain(a.slug, true);
        await flush();
        break;
      case "selectProduct":
        viewer._selectProduct(a.slug, true);
        await flush();
        break;
      case "clickHour": {
        const host = getEl("hafs-hours");
        const btn = host.children.find(
          (b) => b.getAttribute("data-fxx") === String(a.fxx));
        // Mirror the browser: a disabled (pending) button never fires click.
        if (btn && !btn.disabled) btn._fire("click", {});
        await flush();
        break;
      }
      case "clickBadge": {
        const badge = getEl("hafs-badge");
        const btn = badge.querySelector(".hafs-badge-btn");
        if (btn) btn._fire("click", {});
        await flush();
        break;
      }
      case "snapshot":
      default:
        break;
    }
    steps.push(snapshot(viewer));
  }

  process.stdout.write(JSON.stringify({ steps }));
  process.exit(0);
})();
