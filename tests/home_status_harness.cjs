// Node harness for the homepage live-status strip (the inline script at the
// bottom of index.html). Extracts the page's LAST <script> block (footer year
// stamp + iframe resizer + basin-row hydrate IIFEs), runs it under a minimal
// DOM/fetch shim fed by fixture JSON, and prints the resulting row states so
// tests/test_home_status.py can assert the count-cell vocabulary
// ("N active" / "N invest(s)" / "N named YTD") and the green-dot class.
//
//   node home_status_harness.cjs <index.html> <fixtures.json>
//
// fixtures.json: { "<basin>_tracks_data.json": {...}|null,
//                  "<basin>_ace_data.json": {...}|null, ... }
// (keyed by feed filename; null/absent -> fetch resolves !ok, like a 404)
//
// Output: {"basins": {"al": {"active": bool, "count": str, "season": str},
//                     "ep": {...}, "wp": {...}}}
"use strict";

const fs = require("fs");

const html = fs.readFileSync(process.argv[2], "utf8");
const fixtures = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));

// ---- extract the last <script> block (the homepage-logic block) ------------
const blocks = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
if (!blocks.length) throw new Error("no inline <script> blocks found");
const script = blocks[blocks.length - 1];
if (script.indexOf("Live basin status rows") === -1) {
  throw new Error("last script block is not the live-status block");
}

// ---- minimal DOM shim -------------------------------------------------------
function StubClassList() { this._set = {}; }
StubClassList.prototype.add = function (c) { this._set[c] = true; };
StubClassList.prototype.remove = function (c) { delete this._set[c]; };
StubClassList.prototype.contains = function (c) { return !!this._set[c]; };

function cell() { return { innerHTML: "", textContent: "" }; }

function makeRow() {
  const cells = { count: cell(), season: cell() };
  return {
    classList: new StubClassList(),
    querySelector(sel) {
      const m = sel.match(/data-role="(\w+)"/);
      return m ? cells[m[1]] || null : null;
    },
    _cells: cells,
  };
}

const rows = { al: makeRow(), ep: makeRow(), wp: makeRow() };

global.document = {
  querySelector(sel) {
    const m = sel.match(/data-basin="(\w+)"/);
    return m ? rows[m[1]] || null : null;
  },
  getElementById(id) {
    if (id === "year") return cell();          // footer stamp target
    return null;                                // globalMapFrame -> resizer no-op
  },
  addEventListener() {},
};
global.window = { addEventListener() {} };

// fetch shim: resolve fixtures by feed filename; missing/null -> !ok (404-ish).
global.fetch = function (url) {
  const name = String(url).split("/").pop().split("?")[0];
  const body = fixtures[name];
  if (body === undefined || body === null) {
    return Promise.resolve({ ok: false, json: () => Promise.reject(new Error("404")) });
  }
  return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
};

// ---- run + drain the promise chains ----------------------------------------
eval(script);

setTimeout(function () {
  const out = { basins: {} };
  for (const b of ["al", "ep", "wp"]) {
    out.basins[b] = {
      active: rows[b].classList.contains("active"),
      count: rows[b]._cells.count.innerHTML,
      season: rows[b]._cells.season.innerHTML,
    };
  }
  process.stdout.write(JSON.stringify(out));
}, 20);
