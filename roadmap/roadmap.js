/* /roadmap/ board engine (shadow page, unlinked).
 *
 * Single source of truth: /roadmap.yml at the repo root, parsed CLIENT-SIDE
 * by the tiny subset parser below (no CDN YAML lib — the site is CDN-free by
 * rule). The page polls the file (content-gated: re-render only when the
 * bytes change) so testers see updates without a hard reload.
 *
 * YAML SUBSET, enforced loudly (parse/validate errors render as a banner,
 * never a silently-wrong board):
 *   - 2-space indents, spaces only (tabs are an error)
 *   - block maps (`key: value` / `key:`) and block lists (`- item`)
 *   - one-line plain, 'single' or "double" quoted scalars (double-quote
 *     escapes limited to \n \t \\ \" — any other escape is a loud error)
 *   - `>` / `>-` folded and `|` / `|-` literal block scalars; blank lines and
 *     '#' lines inside a block are literal content (standard folding), never
 *     silently dropped
 *   - full-line # comments only; a plain value may not carry a trailing
 *     "# comment" (loud error) — but URLs keep their '#' (no leading space)
 *   - NO flow collections [ ] { }, anchors, tags, or multi-doc
 * The file header in roadmap.yml documents the same rules for editors.
 *
 * UMD-lite: window.TATRoadmap in the page; module.exports under node so
 * tests/roadmap_smoke.cjs can drive parse/validate/derive and (with jsdom)
 * the render + boot path headless.
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.TATRoadmap = api;
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  /* ---- vocab -------------------------------------------------------- */

  // Column order is Andrew's: shipped first so momentum leads the board.
  var STATUSES = [
    { key: "shipped", label: "Shipped", dot: "#7fd6a4", hint: "live on the site" },
    { key: "active", label: "Active", dot: "#9199a4", hint: "in progress now" },
    { key: "shadow", label: "Shadow", dot: "#9199a4", hint: "built, awaiting Andrew's review" },
    { key: "needs-andrew", label: "Needs Andrew", dot: "#ffb83a", hint: "blocked on a decision or credential" },
    { key: "next", label: "Next", dot: "#9199a4", hint: "specced and queued" },
    { key: "planned", label: "Planned", dot: "#9199a4", hint: "backlog" },
    { key: "blocked", label: "Blocked", dot: "#ff9d94", hint: "waiting on something external" },
  ];

  // deep = fills/borders/bars (validated 7-slot categorical set on the dark
  // panel: lightness band, chroma, CVD + normal-vision separation, 3:1
  // contrast); text = chip text (all >= 7.8:1 on --panel). Identity is never
  // color-alone: every colored mark sits beside the area's name.
  var AREAS = [
    { key: "satellite", label: "satellite", deep: "#2d96c8", text: "#6ec4ea" },
    { key: "obs", label: "obs", deep: "#bd821a", text: "#ffb83a" },
    { key: "records", label: "records", deep: "#a06be8", text: "#c9a1f5" },
    { key: "models", label: "models", deep: "#39a469", text: "#7fd6a4" },
    { key: "infra", label: "infra", deep: "#7086e0", text: "#a9b8f0" },
    { key: "apps", label: "apps", deep: "#d4638f", text: "#f0a1c0" },
    { key: "community", label: "community", deep: "#93992b", text: "#c9cf6a" },
  ];

  var STATUS_KEYS = STATUSES.map(function (s) { return s.key; });
  var AREA_KEYS = AREAS.map(function (a) { return a.key; });

  function statusMeta(key) {
    for (var i = 0; i < STATUSES.length; i++) if (STATUSES[i].key === key) return STATUSES[i];
    return null;
  }

  /* ---- YAML subset parser ------------------------------------------- */

  function perr(n, msg) { return new Error("roadmap.yml:" + n + ": " + msg); }

  function parseYaml(text) {
    var rawLines = String(text).split(/\r?\n/);
    // Keep EVERY line with its metadata. Blank/comment lines are skipped by
    // the map/seq walkers but stay visible to the block-scalar consumer, so a
    // '#'-leading line or a blank line INSIDE a note block is literal content
    // (standard YAML), never silently dropped by a pre-scan.
    var lines = [];
    for (var i = 0; i < rawLines.length; i++) {
      var raw = rawLines[i];
      if (raw.indexOf("\t") !== -1) throw perr(i + 1, "tab character (2-space indents only)");
      var body = raw.replace(/\s+$/, "");
      var indent = /^( *)/.exec(body)[1].length;
      var content = body.slice(indent);
      lines.push({
        indent: indent, text: content, raw: body, n: i + 1,
        blank: content === "", comment: content.charAt(0) === "#",
      });
    }
    var pos = { i: 0 };
    skipInsignificant(lines, pos);
    var doc = parseMap(lines, pos, 0);
    skipInsignificant(lines, pos);
    if (pos.i < lines.length) throw perr(lines[pos.i].n, "unexpected indent (this line belongs to nothing above it)");
    return doc;
  }

  function skipInsignificant(lines, pos) {
    while (pos.i < lines.length && (lines[pos.i].blank || lines[pos.i].comment)) pos.i++;
  }

  var KEY_RE = /^([A-Za-z0-9_-]+):(.*)$/;

  function parseMap(lines, pos, indent) {
    var out = {};
    while (pos.i < lines.length) {
      skipInsignificant(lines, pos);
      if (pos.i >= lines.length) break;
      var L = lines[pos.i];
      if (L.indent < indent) break;
      if (L.indent > indent) throw perr(L.n, "bad indent: expected " + indent + " spaces");
      if (L.text === "-" || L.text.slice(0, 2) === "- ") throw perr(L.n, 'list item where a "key:" was expected');
      var m = KEY_RE.exec(L.text);
      if (!m) throw perr(L.n, 'expected "key: value" or "key:" (quote values with special leading chars)');
      var key = m[1], rest = m[2];
      if (Object.prototype.hasOwnProperty.call(out, key)) throw perr(L.n, 'duplicate key "' + key + '"');
      if (rest === "") {
        pos.i++;
        skipInsignificant(lines, pos);
        var C = pos.i < lines.length ? lines[pos.i] : null;
        if (!C || C.indent <= indent) throw perr(L.n, '"' + key + ':" has no value (nested block must be indented ' + (indent + 2) + " spaces)");
        if (C.indent !== indent + 2) throw perr(C.n, 'bad indent: children of "' + key + ':" must start at exactly ' + (indent + 2) + " spaces");
        out[key] = (C.text === "-" || C.text.slice(0, 2) === "- ")
          ? parseSeq(lines, pos, indent + 2)
          : parseMap(lines, pos, indent + 2);
      } else {
        if (rest.charAt(0) !== " ") throw perr(L.n, 'missing space after "' + key + ':"');
        var v = rest.slice(1);
        if (v === ">" || v === ">-" || v === "|" || v === "|-") {
          pos.i++;
          out[key] = readBlockScalar(lines, pos, indent + 2, v, L);
        } else if (v.charAt(0) === ">" || v.charAt(0) === "|") {
          // 'note: >- text' — indicator and text on one line. Standard YAML
          // rejects this; parsing it as a plain string would silently keep
          // the stray '>- ' prefix, so throw instead.
          throw perr(L.n, 'block-scalar indicator "' + v.split(" ")[0] +
            '" must be alone on the line; put the text on the next line, indented ' + (indent + 2) + " spaces");
        } else {
          out[key] = parseScalar(v, L);
          pos.i++;
        }
      }
    }
    return out;
  }

  // Consume a folded (>) / literal (|) block scalar. Body = every line that
  // is blank OR indented >= `bodyIndent`, until the first non-blank line that
  // dedents. Blank lines fold to paragraph breaks (>) or literal newlines (|),
  // matching standard YAML; the '-' chomp keeps no trailing newline.
  function readBlockScalar(lines, pos, bodyIndent, indicator, startL) {
    var body = [];
    var blockIndent = null;
    while (pos.i < lines.length) {
      var B = lines[pos.i];
      if (B.blank) { body.push({ blank: true }); pos.i++; continue; }
      if (B.indent < bodyIndent) break;
      if (blockIndent === null) blockIndent = B.indent;
      body.push({ blank: false, text: B.raw.slice(blockIndent) });
      pos.i++;
    }
    while (body.length && body[body.length - 1].blank) body.pop(); // trailing blanks
    if (!body.length) throw perr(startL.n, 'empty block scalar under "' + KEY_RE.exec(startL.text)[1] + ':"');
    if (indicator.charAt(0) === "|") {
      return body.map(function (l) { return l.blank ? "" : l.text; }).join("\n");
    }
    var out = "";
    for (var i = 0; i < body.length; i++) {
      if (body[i].blank) { out += "\n"; continue; }
      if (out === "" || out.charAt(out.length - 1) === "\n") out += body[i].text;
      else out += " " + body[i].text;
    }
    return out;
  }

  function parseSeq(lines, pos, indent) {
    var out = [];
    while (pos.i < lines.length) {
      skipInsignificant(lines, pos);
      if (pos.i >= lines.length) break;
      var L = lines[pos.i];
      if (L.indent < indent) break;
      if (L.indent > indent) throw perr(L.n, "bad indent inside list");
      if (L.text === "-") throw perr(L.n, 'empty "-" list item');
      if (L.text.slice(0, 2) !== "- ") throw perr(L.n, 'expected a "- " list item at this indent');
      var rest = L.text.slice(2);
      if (KEY_RE.test(rest)) {
        // map item: hoist `- key: ...` to a virtual `key: ...` line two
        // spaces deeper, then let parseMap consume it + its sibling keys.
        lines[pos.i] = { indent: indent + 2, text: rest, raw: "  " + rest, n: L.n, blank: false, comment: false };
        out.push(parseMap(lines, pos, indent + 2));
      } else {
        out.push(parseScalar(rest, L));
        pos.i++;
      }
    }
    return out;
  }

  function parseScalar(s, L) {
    if (s.charAt(0) === '"') {
      var dm = /^"((?:[^"\\]|\\.)*)"$/.exec(s);
      if (!dm) throw perr(L.n, "unterminated double-quoted string");
      return dm[1].replace(/\\(.)/g, function (_, c) {
        if (c === "n") return "\n";
        if (c === "t") return "\t";
        if (c === '"' || c === "\\" || c === "/") return c;
        throw perr(L.n, 'unsupported escape "\\' + c + '" (only \\n \\t \\\\ \\" are supported; paste the literal character instead)');
      });
    }
    if (s.charAt(0) === "'") {
      var sm = /^'((?:[^']|'')*)'$/.exec(s);
      if (!sm) throw perr(L.n, "unterminated single-quoted string");
      return sm[1].replace(/''/g, "'");
    }
    if (/^[\[{&*!]/.test(s)) throw perr(L.n, "flow collections / anchors / tags not supported (quote the value)");
    // A plain value can't carry a trailing '# comment' (the subset has none —
    // URLs keep their '#' because it has no leading space). Reject anything
    // that reads like one instead of silently baking it into the text.
    if (/ #/.test(s)) throw perr(L.n, 'plain value contains " #", which reads as a trailing comment; quote the value to keep the # or move the comment to its own full line');
    return s;
  }

  /* ---- schema validation -------------------------------------------- */

  var ITEM_KEYS = { id: 1, title: 1, area: 1, status: 1, note: 1, links: 1, date_shipped: 1 };

  function isIsoDate(s) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
    var d = new Date(s + "T00:00:00Z");
    return !isNaN(d.getTime()) && d.toISOString().slice(0, 10) === s;
  }

  function validate(doc) {
    var errs = [];
    if (!doc || typeof doc !== "object" || Array.isArray(doc)) {
      throw new Error("roadmap.yml invalid:\n- top level must be a map with updated: and items:");
    }
    Object.keys(doc).forEach(function (k) {
      if (k !== "updated" && k !== "items") errs.push('unknown top-level key "' + k + '"');
    });
    if (typeof doc.updated !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?Z$/.test(doc.updated)) {
      errs.push("updated: must be a UTC stamp like 2026-07-24T18:00:00Z (bump it in every commit that edits this file)");
    }
    var items = doc.items;
    if (!Array.isArray(items) || !items.length) { errs.push("items: must be a non-empty list"); items = []; }
    var seen = {};
    items.forEach(function (it, idx) {
      var where = "items[" + (idx + 1) + "]" + (it && typeof it.id === "string" ? " (" + it.id + ")" : "");
      if (!it || typeof it !== "object" || Array.isArray(it)) { errs.push(where + ": must be a map"); return; }
      Object.keys(it).forEach(function (k) {
        if (!ITEM_KEYS[k]) errs.push(where + ': unknown key "' + k + '"');
      });
      if (typeof it.id !== "string" || !/^[a-z0-9][a-z0-9-]*$/.test(it.id)) errs.push(where + ": id must be kebab-case ([a-z0-9-])");
      else if (seen[it.id]) errs.push(where + ": duplicate id");
      else seen[it.id] = 1;
      if (typeof it.title !== "string" || !it.title.trim()) errs.push(where + ": title required");
      else if (it.title.length > 90) errs.push(where + ": title too long (>90 chars); put detail in note");
      if (AREA_KEYS.indexOf(it.area) === -1) errs.push(where + ": area must be one of " + AREA_KEYS.join(" / "));
      if (STATUS_KEYS.indexOf(it.status) === -1) errs.push(where + ": status must be one of " + STATUS_KEYS.join(" / "));
      if (it.note !== undefined && (typeof it.note !== "string" || !it.note.trim())) errs.push(where + ": note must be non-empty text");
      if (it.status === "shipped") {
        if (typeof it.date_shipped !== "string" || !isIsoDate(it.date_shipped)) errs.push(where + ": shipped items need date_shipped: YYYY-MM-DD");
      } else if (it.date_shipped !== undefined) {
        errs.push(where + ": date_shipped only belongs on shipped items");
      }
      if (it.links !== undefined) {
        if (!Array.isArray(it.links) || !it.links.length) { errs.push(where + ": links must be a non-empty list"); return; }
        it.links.forEach(function (ln, j) {
          var lw = where + ".links[" + (j + 1) + "]";
          if (!ln || typeof ln !== "object" || Array.isArray(ln)) { errs.push(lw + ": must be a {label, url} pair"); return; }
          Object.keys(ln).forEach(function (k) {
            if (k !== "label" && k !== "url") errs.push(lw + ': unknown key "' + k + '"');
          });
          if (typeof ln.label !== "string" || !ln.label.trim() || ln.label.length > 40) errs.push(lw + ": label required (<=40 chars)");
          // site-relative "/…" (but NOT protocol-relative "//host") or https://…
          if (typeof ln.url !== "string" || !/^(https:\/\/|\/(?!\/))/.test(ln.url)) errs.push(lw + ": url must be site-relative (/…) or https://…");
        });
      }
    });
    if (errs.length) throw new Error("roadmap.yml invalid:\n- " + errs.join("\n- "));
    return { updated: doc.updated, items: items };
  }

  /* ---- derived model ------------------------------------------------- */

  function derive(data) {
    var byStatus = {};
    STATUSES.forEach(function (s) { byStatus[s.key] = []; });
    data.items.forEach(function (it, i) { it._i = i; byStatus[it.status].push(it); });
    byStatus.shipped.sort(function (a, b) {
      if (a.date_shipped !== b.date_shipped) return a.date_shipped < b.date_shipped ? 1 : -1;
      return a._i - b._i;
    });
    var areaCounts = {};
    AREAS.forEach(function (a) { areaCounts[a.key] = { total: 0, shipped: 0 }; });
    data.items.forEach(function (it) {
      areaCounts[it.area].total++;
      if (it.status === "shipped") areaCounts[it.area].shipped++;
    });
    var shipped = byStatus.shipped.length;
    return {
      updated: data.updated,
      items: data.items,
      byStatus: byStatus,
      recent: byStatus.shipped.slice(0, 10),
      areaCounts: areaCounts,
      shippedCount: shipped,
      openCount: data.items.length - shipped,
      total: data.items.length,
    };
  }

  function findItem(model, id) {
    for (var i = 0; i < model.items.length; i++) if (model.items[i].id === id) return model.items[i];
    return null;
  }

  /* ---- rendering ------------------------------------------------------ */

  function el(doc, tag, cls, text) {
    var n = doc.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function fmtUpdated(iso) { return iso.slice(0, 16).replace("T", " ") + " UTC"; }

  function renderStamp(ctx) {
    var m = ctx.model;
    ctx.els.stamp.textContent = "Board updated " + fmtUpdated(m.updated) + " · " +
      m.shippedCount + " shipped · " + m.openCount + " open · auto-refreshes";
  }

  function renderSummary(ctx) {
    var doc = ctx.doc, m = ctx.model;
    var box = ctx.els.summary;
    box.textContent = "";
    box.appendChild(el(doc, "h3", "rm-h3", "Progress by area"));
    AREAS.forEach(function (a) {
      var c = m.areaCounts[a.key];
      if (!c.total) return;
      var row = el(doc, "div", "rm-sumrow a-" + a.key);
      row.appendChild(el(doc, "span", "rm-sumdot"));
      row.appendChild(el(doc, "span", "rm-sumlabel", a.label));
      var track = el(doc, "span", "rm-bar");
      var fill = el(doc, "span", "rm-barfill");
      fill.style.width = Math.round((c.shipped / c.total) * 100) + "%";
      track.appendChild(fill);
      row.appendChild(track);
      row.appendChild(el(doc, "span", "rm-sumcount", c.shipped + "/" + c.total));
      box.appendChild(row);
    });
    box.appendChild(el(doc, "p", "rm-sumfoot", m.shippedCount + " shipped · " + m.openCount + " open, of " + m.total + " tracked"));
  }

  function renderRecent(ctx) {
    var doc = ctx.doc, m = ctx.model;
    var box = ctx.els.recent;
    box.textContent = "";
    box.appendChild(el(doc, "h3", "rm-h3", "Recently shipped"));
    var row = el(doc, "div", "rm-recentrow");
    m.recent.forEach(function (it) {
      var c = el(doc, "div", "rm-mini a-" + it.area);
      c.tabIndex = 0;
      c.setAttribute("role", "button");
      c.appendChild(el(doc, "span", "rm-minidate", it.date_shipped));
      c.appendChild(el(doc, "span", "rm-minititle", it.title));
      var ar = el(doc, "span", "rm-miniarea");
      ar.appendChild(el(doc, "span", "rm-areadot"));
      ar.appendChild(el(doc, "span", null, it.area));
      c.appendChild(ar);
      wireOpen(ctx, c, it.id);
      row.appendChild(c);
    });
    box.appendChild(row);
  }

  function renderChips(ctx) {
    var doc = ctx.doc, m = ctx.model;
    var box = ctx.els.chips;
    box.textContent = "";
    var all = el(doc, "button", "rm-chipbtn" + (ctx.state.area === "all" ? " on" : ""), "All " + m.total);
    all.type = "button";
    all.addEventListener("click", function () { ctx.setArea("all"); });
    box.appendChild(all);
    AREAS.forEach(function (a) {
      var c = m.areaCounts[a.key];
      var b = el(doc, "button", "rm-chipbtn a-" + a.key + (ctx.state.area === a.key ? " on" : ""), a.label + " " + c.total);
      b.type = "button";
      b.addEventListener("click", function () { ctx.setArea(ctx.state.area === a.key ? "all" : a.key); });
      box.appendChild(b);
    });
  }

  function card(ctx, it) {
    var doc = ctx.doc;
    var d = el(doc, "div", "rm-card a-" + it.area);
    d.tabIndex = 0;
    d.setAttribute("role", "button");
    d.appendChild(el(doc, "div", "rm-cardtitle", it.title));
    var meta = el(doc, "div", "rm-cardmeta");
    meta.appendChild(el(doc, "span", "rm-chip", it.area));
    if (it.status === "shipped") meta.appendChild(el(doc, "span", "rm-carddate", it.date_shipped));
    d.appendChild(meta);
    wireOpen(ctx, d, it.id);
    return d;
  }

  function wireOpen(ctx, node, id) {
    node.addEventListener("click", function () { ctx.open(id); });
    node.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); ctx.open(id); }
    });
  }

  function renderBoard(ctx) {
    var doc = ctx.doc, m = ctx.model;
    var wrap = ctx.els.board;
    wrap.textContent = "";
    var board = el(doc, "div", "rm-board");
    STATUSES.forEach(function (s) {
      var itemsAll = m.byStatus[s.key];
      var items = ctx.state.area === "all" ? itemsAll
        : itemsAll.filter(function (it) { return it.area === ctx.state.area; });
      var col = el(doc, "section", "rm-col");
      var head = el(doc, "div", "rm-colhead");
      var dot = el(doc, "span", "rm-statusdot");
      dot.style.background = s.dot;
      head.appendChild(dot);
      head.appendChild(el(doc, "span", "rm-colname", s.label));
      head.appendChild(el(doc, "span", "rm-colcount",
        ctx.state.area === "all" ? String(itemsAll.length) : items.length + "/" + itemsAll.length));
      col.appendChild(head);
      col.appendChild(el(doc, "div", "rm-colhint", s.hint));
      var body = el(doc, "div", "rm-colcards");
      if (!items.length) body.appendChild(el(doc, "div", "rm-empty", "none"));
      items.forEach(function (it) { body.appendChild(card(ctx, it)); });
      col.appendChild(body);
      board.appendChild(col);
    });
    wrap.appendChild(board);
  }

  function renderModal(ctx) {
    var doc = ctx.doc;
    var modal = ctx.els.modal;
    var it = ctx.state.openId ? findItem(ctx.model, ctx.state.openId) : null;
    if (!it) {
      modal.hidden = true;
      modal.textContent = "";
      doc.body.style.overflow = "";
      return;
    }
    modal.textContent = "";
    var panel = el(doc, "div", "rm-modalpanel a-" + it.area);
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    panel.setAttribute("aria-label", it.title);
    // Keep Tab inside the dialog while it's open (basic focus trap).
    panel.addEventListener("keydown", function (ev) {
      if (ev.key !== "Tab") return;
      var f = panel.querySelectorAll("button, a[href]");
      if (!f.length) return;
      var first = f[0], last = f[f.length - 1];
      if (ev.shiftKey && doc.activeElement === first) { ev.preventDefault(); last.focus(); }
      else if (!ev.shiftKey && doc.activeElement === last) { ev.preventDefault(); first.focus(); }
    });
    var close = el(doc, "button", "rm-modalclose", "×");
    close.type = "button";
    close.setAttribute("aria-label", "Close");
    close.addEventListener("click", function () { ctx.close(); });
    panel.appendChild(close);
    panel.appendChild(el(doc, "h2", "rm-modaltitle", it.title));
    var chips = el(doc, "div", "rm-modalchips");
    var sm = statusMeta(it.status);
    var st = el(doc, "span", "rm-chip rm-statuschip");
    var sd = el(doc, "span", "rm-statusdot");
    sd.style.background = sm.dot;
    st.appendChild(sd);
    st.appendChild(doc.createTextNode(sm.label));
    st.title = sm.hint;
    chips.appendChild(st);
    chips.appendChild(el(doc, "span", "rm-chip", it.area));
    panel.appendChild(chips);
    if (it.status === "shipped") panel.appendChild(el(doc, "p", "rm-modaldate", "Shipped " + it.date_shipped));
    if (it.note) panel.appendChild(el(doc, "p", "rm-modalnote", it.note));
    if (it.links && it.links.length) {
      var ul = el(doc, "ul", "rm-modallinks");
      it.links.forEach(function (ln) {
        var li = el(doc, "li");
        var a = el(doc, "a", null, ln.label);
        a.href = ln.url;
        a.target = "_blank";
        a.rel = "noopener";
        li.appendChild(a);
        li.appendChild(el(doc, "span", "rm-modalurl", ln.url));
        ul.appendChild(li);
      });
      panel.appendChild(ul);
    }
    panel.appendChild(el(doc, "p", "rm-modalid", "id: " + it.id + " · roadmap.yml"));
    modal.appendChild(panel);
    modal.hidden = false;
    doc.body.style.overflow = "hidden";
    // Move focus into the dialog on open (and after a poll re-render swaps the
    // panel out from under it) so keyboard users land in the modal, not the
    // dimmed page behind it.
    if (!panel.contains(doc.activeElement)) { try { close.focus(); } catch (e) { /* headless */ } }
  }

  function renderError(ctx, msg) {
    ctx.els.error.textContent = msg;
    ctx.els.error.hidden = !msg;
  }

  function renderAll(ctx) {
    renderStamp(ctx);
    renderSummary(ctx);
    renderRecent(ctx);
    renderChips(ctx);
    renderBoard(ctx);
    renderModal(ctx);
  }

  /* ---- state / boot ---------------------------------------------------- */

  function readHash(loc) {
    var h = String((loc && loc.hash) || "");
    var am = /[#&]area=([a-z]+)/.exec(h);
    var im = /[#&]item=([a-z0-9-]+)/.exec(h);
    return {
      area: am && AREA_KEYS.indexOf(am[1]) !== -1 ? am[1] : "all",
      openId: im ? im[1] : null,
    };
  }

  function writeHash(win, state) {
    if (!win || !win.history || !win.history.replaceState) return;
    var parts = [];
    if (state.area !== "all") parts.push("area=" + state.area);
    if (state.openId) parts.push("item=" + state.openId);
    var loc = win.location;
    try {
      win.history.replaceState(null, "", loc.pathname + loc.search + (parts.length ? "#" + parts.join("&") : ""));
    } catch (e) { /* sandboxed contexts */ }
  }

  function boot(opts) {
    opts = opts || {};
    var doc = opts.doc || (typeof document !== "undefined" ? document : null);
    if (!doc) throw new Error("TATRoadmap.boot: no document");
    var win = doc.defaultView;
    var els = {};
    ["rmStamp", "rmSummary", "rmRecent", "rmChips", "rmBoard", "rmModal", "rmError"].forEach(function (id) {
      var n = doc.getElementById(id);
      if (!n) throw new Error("TATRoadmap.boot: missing #" + id);
      els[id.slice(2).toLowerCase()] = n;
    });
    var yamlUrl = opts.yamlUrl || "/roadmap.yml";
    var pollMs = opts.pollMs != null ? opts.pollMs : 60000;
    var fetchFn = opts.fetchFn || function (u) { return win.fetch(u, { cache: "no-store" }); };

    var ctx = {
      doc: doc,
      els: els,
      model: null,
      state: readHash(win && win.location),
      _lastFocus: null,
      setArea: function (area) {
        ctx.state.area = area;
        writeHash(win, ctx.state);
        renderChips(ctx);
        renderBoard(ctx);
      },
      open: function (id) {
        if (!ctx.state.openId) ctx._lastFocus = doc.activeElement; // the card that opened it
        ctx.state.openId = id;
        writeHash(win, ctx.state);
        renderModal(ctx);
      },
      close: function () {
        ctx.state.openId = null;
        writeHash(win, ctx.state);
        renderModal(ctx);
        var f = ctx._lastFocus;
        ctx._lastFocus = null;
        if (f && f.focus && doc.contains(f)) { try { f.focus(); } catch (e) { /* headless */ } }
      },
    };

    // Drop a stale/unknown open id and keep the URL honest (no #item= for an
    // item that isn't on the board).
    function normalizeOpen() {
      if (ctx.state.openId && ctx.model && !findItem(ctx.model, ctx.state.openId)) {
        ctx.state.openId = null;
        writeHash(win, ctx.state);
      }
    }

    var lastText = null;
    var parseOk = false; // did the currently-held bytes parse cleanly?

    function apply(text) {
      var model = derive(validate(parseYaml(text)));
      ctx.model = model;
      normalizeOpen();
      renderError(ctx, "");
      renderAll(ctx);
    }

    function tick() {
      var sep = yamlUrl.indexOf("?") === -1 ? "?" : "&";
      return fetchFn(yamlUrl + sep + "ts=" + Date.now())
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.text();
        })
        .then(function (text) {
          if (text === lastText) {
            // Unchanged bytes: the fetch itself succeeded, so clear any
            // transient "feed unreachable" banner — but leave a parse-error
            // banner up, since the same bad bytes are still on disk.
            if (parseOk) renderError(ctx, "");
            return;
          }
          lastText = text;
          try { apply(text); parseOk = true; }
          catch (e) { parseOk = false; throw e; }
        })
        .catch(function (e) {
          var msg = /^roadmap\.yml/.test(e.message)
            ? e.message + (ctx.model ? "\n(the board below is the last good state)" : "")
            : "Roadmap feed unreachable (" + e.message + ")" + (ctx.model ? ", showing the last good state." : ". Tell Andrew.");
          renderError(ctx, msg);
        });
    }

    // Content-gated poll: fetch on an interval, skip work when the bytes are
    // unchanged, skip fetching entirely while the tab is hidden (an immediate
    // catch-up tick fires on return).
    function loop() {
      win.setTimeout(function () {
        (doc.hidden ? Promise.resolve() : tick()).then(loop, loop);
      }, pollMs);
    }

    // Backdrop close, but only when the gesture BEGAN on the backdrop —
    // otherwise selecting note text and releasing the drag past the panel edge
    // would close the modal mid-copy.
    var downOnBackdrop = false;
    els.modal.addEventListener("mousedown", function (ev) { downOnBackdrop = ev.target === els.modal; });
    els.modal.addEventListener("click", function (ev) {
      if (ev.target === els.modal && downOnBackdrop) ctx.close();
      downOnBackdrop = false;
    });
    doc.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && ctx.state.openId) ctx.close();
    });
    if (win) {
      win.addEventListener("hashchange", function () {
        var s = readHash(win.location);
        ctx.state.area = s.area;
        ctx.state.openId = s.openId;
        normalizeOpen(); // drop a bogus #item= id + rewrite the hash
        if (ctx.model) renderAll(ctx);
      });
      win.addEventListener("focus", function () { if (pollMs > 0) tick(); });
    }
    doc.addEventListener("visibilitychange", function () {
      if (!doc.hidden && pollMs > 0) tick();
    });

    var ready = tick().then(function () {
      if (pollMs > 0) loop();
    });
    return { ready: ready, refresh: tick, ctx: ctx };
  }

  return {
    STATUSES: STATUSES,
    AREAS: AREAS,
    parseYaml: parseYaml,
    validate: validate,
    derive: derive,
    findItem: findItem,
    boot: boot,
  };
});
