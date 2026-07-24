// /roadmap/ shadow-board smoke.
//
//   node tests/roadmap_smoke.cjs data   # parse+validate roadmap.yml, print a
//                                       # JSON summary (no jsdom needed)
//   node tests/roadmap_smoke.cjs dom    # full board smoke under jsdom:
//                                       # render, filter, modal, content-gated
//                                       # refresh, bad-yaml banner
//
// Driven by tests/test_roadmap_board.py; the dom mode needs jsdom
// (npm install --no-save jsdom), matching the other tests/*.cjs harnesses.
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const R = require(path.join(ROOT, "roadmap", "roadmap.js"));
const YAML_TEXT = fs.readFileSync(path.join(ROOT, "roadmap.yml"), "utf8");

const mode = process.argv[2] || "data";

function summarize(model) {
  const byStatus = {};
  R.STATUSES.forEach((s) => { byStatus[s.key] = model.byStatus[s.key].map((i) => i.id); });
  return {
    updated: model.updated,
    total: model.total,
    shipped: model.shippedCount,
    open: model.openCount,
    byStatus,
    recent: model.recent.map((i) => ({ id: i.id, date: i.date_shipped })),
    areaCounts: model.areaCounts,
    items: model.items.map((i) => ({ id: i.id, status: i.status, area: i.area })),
  };
}

// Parser unit cases: the subset must accept what roadmap.yml uses and
// reject loudly what it documents as unsupported.
function parserUnitChecks() {
  const p = R.parseYaml;
  assert.deepStrictEqual(
    p("a: 1\nb:\n  - x: y\n    z: 'q''r'\n  - x: \"a: b\"\n"),
    { a: "1", b: [{ x: "y", z: "q'r" }, { x: "a: b" }] },
    "nested seq-of-maps + quoting");
  assert.strictEqual(p("n: >-\n  one\n  two\n").n, "one two", "folded block");
  assert.strictEqual(p("n: |-\n  one\n  two\n").n, "one\ntwo", "literal block");
  assert.strictEqual(p("u: https://x.test/a#b?c=d\n").u, "https://x.test/a#b?c=d",
    "no trailing-comment stripping (URLs keep #)");
  assert.strictEqual(p("t: Phase 2: CIE-XYZ\n").t, "Phase 2: CIE-XYZ",
    "colon inside a plain value survives");
  // block-scalar-aware: '#' and blank lines INSIDE a block are literal content
  assert.strictEqual(p("n: >-\n  refs #42 and #43\n  remain open\n").n,
    "refs #42 and #43 remain open", "'#' inside a folded block is literal, not dropped");
  assert.strictEqual(p("n: >-\n  para one\n\n  para two\n").n,
    "para one\npara two", "blank line inside a folded block is a paragraph break");
  assert.strictEqual(p("n: |-\n  a\n  #b\n  c\n").n, "a\n#b\nc", "'#' inside a literal block is literal");
  assert.throws(() => p("a:\tb\n"), /tab/, "tabs rejected");
  assert.throws(() => p("a: [1, 2]\n"), /flow/, "flow seq rejected");
  assert.throws(() => p("a: x\na: y\n"), /duplicate key/, "dup key rejected");
  assert.throws(() => p("a:\n   b: c\n"), /indent/, "3-space indent rejected");
  assert.throws(() => p("a: x\n  stray\n"), /indent/i, "orphan deep line rejected");
  assert.throws(() => p('a: "em\\u2014dash"\n'), /unsupported escape/, "unknown \\-escape rejected loudly");
  assert.throws(() => p("a: >- inline text\n"), /alone on the line/, "inline text after fold indicator rejected");
  assert.throws(() => p("a: Explorer polish # TODO\n"), /trailing comment/, "trailing '# comment' on a plain value rejected");
  assert.throws(() => R.validate(p("updated: nope\nitems:\n  - id: a\n    title: t\n    area: obs\n    status: next\n")),
    /updated:/, "bad updated stamp rejected");
  assert.throws(() => R.validate(p(
    "updated: 2026-07-24T00:00Z\nitems:\n  - id: a\n    title: t\n    area: obs\n    status: shipped\n")),
    /date_shipped/, "shipped without date rejected");
  assert.throws(() => R.validate(p(
    "updated: 2026-07-24T00:00Z\nitems:\n  - id: a\n    title: t\n    area: obs\n    status: next\n    satus: x\n")),
    /unknown key/, "typo'd key rejected");
  assert.throws(() => R.validate(p(
    "updated: 2026-07-24T00:00:00Z\nitems:\n  - id: a\n    title: t\n    area: obs\n    status: next\n    links:\n      - label: x\n        url: //evil.example/pwn\n")),
    /url must be/, "protocol-relative // url rejected");
}

const model = R.derive(R.validate(R.parseYaml(YAML_TEXT)));
parserUnitChecks();

if (mode === "data") {
  process.stdout.write(JSON.stringify(summarize(model)));
  process.exit(0);
}

/* ---- dom mode ---- */

const { JSDOM } = require("jsdom");
const HTML = fs.readFileSync(path.join(ROOT, "roadmap", "index.html"), "utf8");
const JS = fs.readFileSync(path.join(ROOT, "roadmap", "roadmap.js"), "utf8");

const checks = [];
function check(name, fn) {
  fn();
  checks.push(name);
  console.log("ok  " + name);
}

(async () => {
  const dom = new JSDOM(HTML, {
    url: "https://triple-a-tropics.com/roadmap/",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const win = dom.window;
  const doc = win.document;
  win.eval(JS);

  // mutable feed: refresh() re-reads holder.text (content-gated poll path);
  // holder.fail simulates a transient network blip.
  const holder = { text: YAML_TEXT, fetches: 0, fail: false };
  const fetchFn = () => {
    holder.fetches++;
    if (holder.fail) return Promise.reject(new Error("network"));
    return Promise.resolve({ ok: true, text: () => Promise.resolve(holder.text) });
  };
  const app = win.TATRoadmap.boot({ doc, pollMs: 0, fetchFn });
  await app.ready;

  const $ = (sel) => doc.querySelectorAll(sel);

  check("page is noindexed", () => {
    const m = doc.querySelector('meta[name="robots"]');
    assert(m && /noindex/.test(m.content) && /nofollow/.test(m.content));
  });

  check("7 columns in status order", () => {
    const names = [...$(".rm-colname")].map((n) => n.textContent);
    assert.deepStrictEqual(names, R.STATUSES.map((s) => s.label));
  });

  check("every item renders exactly one card", () => {
    assert.strictEqual($(".rm-card").length, model.total);
  });

  check("column counts match the model", () => {
    const counts = [...$(".rm-colcount")].map((n) => n.textContent);
    assert.deepStrictEqual(counts, R.STATUSES.map((s) => String(model.byStatus[s.key].length)));
  });

  check("recently-shipped strip holds 10, newest first", () => {
    const dates = [...$(".rm-minidate")].map((n) => n.textContent);
    assert.strictEqual(dates.length, 10);
    const sorted = [...dates].sort().reverse();
    assert.deepStrictEqual(dates, sorted);
  });

  check("area classes on cards match AREAS in roadmap.js", () => {
    const known = new Set(R.AREAS.map((a) => "a-" + a.key));
    for (const c of $(".rm-card")) {
      const cls = [...c.classList].find((x) => x.startsWith("a-"));
      assert(known.has(cls), "unknown area class " + cls);
    }
  });

  check("summary bars: one per non-empty area, widths sane", () => {
    const rows = $(".rm-sumrow");
    const nonEmpty = R.AREAS.filter((a) => model.areaCounts[a.key].total > 0);
    assert.strictEqual(rows.length, nonEmpty.length);
    for (const f of $(".rm-barfill")) {
      const w = parseInt(f.style.width, 10);
      assert(w >= 0 && w <= 100, "bar width " + f.style.width);
    }
  });

  check("area chip filters the board", () => {
    const obsChip = [...$(".rm-chipbtn")].find((b) => /^obs /.test(b.textContent));
    obsChip.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
    const visible = $(".rm-card").length;
    assert.strictEqual(visible, model.areaCounts.obs.total);
    // filtered column counts read "visible/total"
    assert([...$(".rm-colcount")].some((n) => /^\d+\/\d+$/.test(n.textContent)));
    // back to All
    const allChip = [...$(".rm-chipbtn")].find((b) => /^All /.test(b.textContent));
    allChip.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
    assert.strictEqual($(".rm-card").length, model.total);
  });

  check("card click opens the detail modal; Escape closes it", () => {
    const card = $(".rm-card")[0];
    card.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
    const modal = doc.getElementById("rmModal");
    assert(!modal.hidden, "modal should open");
    assert(modal.querySelector(".rm-modaltitle").textContent.length > 0);
    for (const a of modal.querySelectorAll("a")) {
      assert.strictEqual(a.target, "_blank");
      assert(/noopener/.test(a.rel));
    }
    doc.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    assert(modal.hidden, "Escape should close the modal");
  });

  check("open() sets the deep-link hash; close() clears it", () => {
    const it = model.items[3];
    app.ctx.open(it.id);
    assert.strictEqual(win.location.hash, "#item=" + it.id);
    const modal = doc.getElementById("rmModal");
    assert.strictEqual(modal.querySelector(".rm-modaltitle").textContent, it.title);
    app.ctx.close();
    assert(modal.hidden && win.location.hash === "");
  });

  // async checks below (check() is sync-only)

  // unchanged bytes → no re-render (marker survives)
  doc.querySelector(".rm-cardtitle").setAttribute("data-marker", "1");
  await app.refresh();
  assert(doc.querySelector('[data-marker="1"]'), "unchanged poll must not re-render");
  console.log("ok  unchanged poll leaves DOM untouched");

  // changed bytes → re-render (marker gone, new status honored)
  holder.text = YAML_TEXT.replace("status: active", "status: blocked");
  await app.refresh();
  assert(!doc.querySelector('[data-marker="1"]'), "changed poll must re-render");
  const blockedCount = [...$(".rm-colcount")][R.STATUSES.findIndex((s) => s.key === "blocked")];
  assert.strictEqual(blockedCount.textContent, String(model.byStatus.blocked.length + 1));
  console.log("ok  changed poll re-renders with new statuses");

  // invalid yaml → loud banner, last good board retained
  holder.text = "updated: broken\nitems: what\n";
  await app.refresh();
  const err = doc.getElementById("rmError");
  assert(!err.hidden && /roadmap\.yml invalid/.test(err.textContent), "banner shows");
  assert($(".rm-card").length > 0, "board keeps last good state");
  console.log("ok  invalid yaml shows banner, keeps last good board");

  // recovery
  holder.text = YAML_TEXT;
  await app.refresh();
  assert(doc.getElementById("rmError").hidden, "banner clears on recovery");
  console.log("ok  banner clears when the file is fixed");

  // transient network blip → banner; next successful (unchanged) poll clears it
  holder.fail = true;
  await app.refresh();
  assert(!doc.getElementById("rmError").hidden &&
    /unreachable/.test(doc.getElementById("rmError").textContent), "network blip shows banner");
  holder.fail = false;
  await app.refresh(); // same bytes as before → content-gated, but must still clear
  assert(doc.getElementById("rmError").hidden, "recovered network must clear the stale banner");
  console.log("ok  transient fetch error clears once the feed is back (content-gate does not strand it)");

  // selection drag: mousedown on the panel, mouseup on the backdrop must NOT close
  app.ctx.open(model.items[0].id);
  const modalEl = doc.getElementById("rmModal");
  assert(!modalEl.hidden);
  const panelEl = modalEl.querySelector(".rm-modalpanel");
  panelEl.dispatchEvent(new win.MouseEvent("mousedown", { bubbles: true }));
  modalEl.dispatchEvent(new win.MouseEvent("click", { bubbles: true })); // click target = backdrop
  assert(!modalEl.hidden, "drag-select ending on the backdrop must not close the modal");
  // a genuine backdrop press-and-release DOES close
  modalEl.dispatchEvent(new win.MouseEvent("mousedown", { bubbles: true }));
  modalEl.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  assert(modalEl.hidden, "a real backdrop click closes the modal");
  console.log("ok  backdrop closes only when the gesture began on the backdrop");

  // bogus #item= hash gets normalized away
  win.location.hash = "#item=does-not-exist";
  win.dispatchEvent(new win.Event("hashchange"));
  assert(modalEl.hidden, "unknown item id shows no modal");
  assert(win.location.hash === "" || !/does-not-exist/.test(win.location.hash),
    "bogus #item= id is scrubbed from the URL");
  console.log("ok  bogus deep-link id is normalized out of the hash");

  console.log("ALL CHECKS PASSED (" + (checks.length + 7) + " checks)");
  win.close();
  process.exit(0);
})().catch((e) => {
  console.error("FAIL:", e && e.stack || e);
  process.exit(1);
});
