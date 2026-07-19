// Headless harness for the satellite explorer — boots the real page against
// the live CDN, then runs a named scenario. Usage:
//   node explorer_harness.cjs <scenario> [outdir]
//   LIVE=1 node explorer_harness.cjs <scenario> [outdir]   # deployed site, no local routes
// Scenarios: boot | drawbox | shiftdrag
"use strict";
const { chromium } = require("playwright");
const http = require("http");
const path = require("path");
const fs = require("fs");

const ROOT = "/workspaces/Triple-A-Tropics";
const OUT = process.argv[3] || "/tmp/claude-1000/-workspaces-Triple-A-Tropics/e63ecbc7-5df9-454e-acf7-368d4bb1506f/scratchpad/shots";
const MIME = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".geojson": "application/json", ".png": "image/png",
  ".webp": "image/webp", ".svg": "image/svg+xml" };

function serve() {
  return new Promise((res) => {
    const srv = http.createServer((req, rsp) => {
      let p = decodeURIComponent(req.url.split("?")[0]);
      if (p.endsWith("/")) p += "index.html";
      const f = path.join(ROOT, p);
      fs.readFile(f, (err, data) => {
        if (err) { rsp.writeHead(404); rsp.end("nf"); return; }
        rsp.writeHead(200, { "content-type": MIME[path.extname(f)] || "application/octet-stream" });
        rsp.end(data);
      });
    });
    srv.listen(0, "127.0.0.1", () => res(srv));
  });
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const scenario = process.argv[2] || "boot";
  const LIVE = process.env.LIVE === "1";   // deployed site as-is: no local page, no feed aliases
  const srv = LIVE ? null : await serve();
  const base = LIVE ? "https://triple-a-tropics.com" : "http://127.0.0.1:" + srv.address().port;
  const browser = await chromium.launch({ args: ['--enable-unsafe-swiftshader', '--use-gl=angle', '--use-angle=swiftshader'] });
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text().slice(0, 300)); });
  page.on("pageerror", (e) => errors.push("PAGEERROR " + String(e).slice(0, 300)));
  page.on("requestfailed", (r) => errors.push("REQFAIL " + ((r.failure() || {}).errorText || "?") + " " + r.url().slice(0, 160)));
  page.on("response", (r) => { if (r.status() >= 400) errors.push("HTTP" + r.status() + " " + r.url().slice(0, 160)); });

  // the CDN's CORS allowlist doesn't cover localhost: re-serve its responses
  // with permissive headers so the page behaves as it does on the real origin
  if (!LIVE) await page.route("https://cdn.triple-a-tropics.com/**", async (route) => {
    try {
      const r = await route.fetch();
      const headers = Object.assign({}, r.headers(),
        { "access-control-allow-origin": "*" });
      delete headers["cross-origin-resource-policy"];
      await route.fulfill({ response: r, headers });
    } catch (e) { await route.abort(); }
  });

  // Local-feed aliases (non-LIVE only, and only when a locally-emitted dir
  // exists): stand a not-yet-launched R2 prefix in with local emitter output
  // so the full client path exercises for real. Once a feed is live on R2
  // the alias dir is simply absent and the CDN route above serves it.
  const aliasDir = (p) => !LIVE && fs.existsSync(p) ? p : null;
  const SCRATCH = process.env.HARNESS_FEEDS ||
    "/tmp/claude-1000/-workspaces-Triple-A-Tropics/e63ecbc7-5df9-454e-acf7-368d4bb1506f/scratchpad";
  const MRMS_LOCAL = aliasDir(SCRATCH + "/mrms/");
  // bench aliasing (mrmszoom only): present the freshest local scan under a
  // stamp near the cached sat loop so the nearest-join + skew gate exercise
  // normally while displaying the NEW pipeline's frame
  const MRMS_ALIAS = scenario === "mrmszoom" && MRMS_LOCAL ? { bench: "20260718T170000Z", real: "20260718T190440Z" } : null;
  if (MRMS_LOCAL) await page.route("https://cdn.triple-a-tropics.com/radar/mrms/**", async (route) => {
    let rel = route.request().url().replace("https://cdn.triple-a-tropics.com/", "").split("?")[0];
    try {
      if (MRMS_ALIAS && rel.includes(MRMS_ALIAS.bench)) rel = rel.replace(MRMS_ALIAS.bench, MRMS_ALIAS.real);
      let body = fs.readFileSync(path.join(MRMS_LOCAL, rel));
      const ctype = rel.endsWith(".json") ? "application/json" : "image/webp";
      if (MRMS_ALIAS && rel.endsWith(".json"))
        body = Buffer.from(body.toString().split(MRMS_ALIAS.real).join(MRMS_ALIAS.bench));
      await route.fulfill({ status: 200, body, headers: {
        "content-type": ctype, "access-control-allow-origin": "*" } });
    } catch (e) { await route.fulfill({ status: 404, body: "nf", headers: { "access-control-allow-origin": "*" } }); }
  });

  const SFC_LOCAL = aliasDir(SCRATCH + "/sfc/");
  if (SFC_LOCAL) await page.route("https://cdn.triple-a-tropics.com/sfc/**", async (route) => {
    const rel = route.request().url().replace("https://cdn.triple-a-tropics.com/", "").split("?")[0];
    try {
      const body = fs.readFileSync(path.join(SFC_LOCAL, rel));
      await route.fulfill({ status: 200, body, headers: {
        "content-type": "application/json", "access-control-allow-origin": "*" } });
    } catch (e) { await route.fulfill({ status: 404, body: "nf", headers: { "access-control-allow-origin": "*" } }); }
  });

  const UHR_LOCAL = aliasDir(SCRATCH + "/uhr/");
  if (UHR_LOCAL) await page.route("https://cdn.triple-a-tropics.com/ascat/uhr/**", async (route) => {
    const rel = route.request().url().replace("https://cdn.triple-a-tropics.com/", "").split("?")[0];
    try {
      const body = fs.readFileSync(path.join(UHR_LOCAL, rel));
      const ctype = rel.endsWith(".webp") ? "image/webp" : "application/json";
      await route.fulfill({ status: 200, body, headers: {
        "content-type": ctype, "access-control-allow-origin": "*" } });
    } catch (e) { await route.fulfill({ status: 404, body: "nf", headers: { "access-control-allow-origin": "*" } }); }
  });

  const NHC_LOCAL = aliasDir(SCRATCH + "/nhc/");
  if (NHC_LOCAL) await page.route("https://cdn.triple-a-tropics.com/nhc/**", async (route) => {
    const rel = route.request().url().replace("https://cdn.triple-a-tropics.com/", "").split("?")[0];
    try {
      const body = fs.readFileSync(path.join(NHC_LOCAL, rel));
      await route.fulfill({ status: 200, body, headers: {
        "content-type": "application/json", "access-control-allow-origin": "*" } });
    } catch (e) { await route.fulfill({ status: 404, body: "nf", headers: { "access-control-allow-origin": "*" } }); }
  });

  // metar series bench-alias: present the single local frame near the cached
  // sat loop so the nearest-join exercises (sfc's 15Z frame joins for real)
  const OBS_LOCAL = aliasDir(SCRATCH + "/metar/");
  if (OBS_LOCAL) {
    const OBS_ALIAS = { bench: "20260718T170000Z", real: null };
    try {
      const m = JSON.parse(fs.readFileSync(path.join(OBS_LOCAL, "obs/metar/latest_times.json")));
      OBS_ALIAS.real = m.latest;
    } catch (e) {}
    await page.route("https://cdn.triple-a-tropics.com/obs/metar/**", async (route) => {
      let rel = route.request().url().replace("https://cdn.triple-a-tropics.com/", "").split("?")[0];
      try {
        if (OBS_ALIAS.real && rel.includes(OBS_ALIAS.bench)) rel = rel.replace(OBS_ALIAS.bench, OBS_ALIAS.real);
        let body = fs.readFileSync(path.join(OBS_LOCAL, rel));
        if (OBS_ALIAS.real && rel.endsWith("latest_times.json"))
          body = Buffer.from(body.toString().split(OBS_ALIAS.real).join(OBS_ALIAS.bench));
        await route.fulfill({ status: 200, body, headers: {
          "content-type": "application/json", "access-control-allow-origin": "*" } });
      } catch (e) { await route.fulfill({ status: 404, body: "nf", headers: { "access-control-allow-origin": "*" } }); }
    });
  }

  const qs = scenario === "coldgeo" ? "?domain=conus&product=ir" : "";
  await page.goto(base + "/satellite/explorer/" + qs, { waitUntil: "domcontentloaded" });

  // wait for pane 0's first real pixels (the boot overlay hides on 'frame')
  await page.waitForFunction(() => {
    const ld = document.getElementById("cx-load-0");
    return ld && ld.style.display === "none";
  }, null, { timeout: 90000 }).catch(() => errors.push("TIMEOUT waiting for first frame"));
  await page.waitForTimeout(2500);

  const state = () => page.evaluate(() => {
    const S = window.__cockpit || {};
    const tv = S.panes && S.panes[0] && S.panes[0].tv;
    const err = document.getElementById('cx-err');
    return {
      domain: S.domain, active: S.active,
      hasMap: !!(tv && tv.map),
      frame: tv && tv.frames && tv.frames[tv.frameIdx],
      nFrames: tv && tv.frames && tv.frames.length,
      cap: tv && tv._loopCapFor ? tv._loopCapFor() : null,
      center: tv && tv.map ? tv.map.getCenter() : null,
      zoom: tv && tv.map ? tv.map.getZoom() : null,
      armed: !!(tv && tv._armed),
      errShown: !!(err && err.style.display === 'flex'),
      errText: err ? (err.textContent || '').slice(0, 200) : null,
    };
  });

  console.log("BOOT STATE:", JSON.stringify(await state()));
  await page.screenshot({ path: path.join(OUT, scenario + "_1_boot.png") });

  if (scenario === "drawbox" || scenario === "shiftdrag") {
    if (scenario === "drawbox") {
      await page.click("#cx-box");
      await page.waitForTimeout(300);
      console.log("AFTER ARM:", JSON.stringify(await state()));
    }
    const box = await page.locator("#cx-map-0").boundingBox();
    const sx = box.x + 400, sy = box.y + 250, ex = box.x + 800, ey = box.y + 550;
    if (scenario === "shiftdrag") await page.keyboard.down("Shift");
    await page.mouse.move(sx, sy);
    await page.mouse.down();
    for (let i = 1; i <= 10; i++)
      await page.mouse.move(sx + (ex - sx) * i / 10, sy + (ey - sy) * i / 10, { steps: 2 });
    // screenshot mid-drag: is the .tv-drawbox rectangle visible?
    const boxVisible = await page.evaluate(() => !!document.querySelector(".tv-drawbox"));
    await page.screenshot({ path: path.join(OUT, scenario + "_2_middrag.png") });
    await page.mouse.up();
    if (scenario === "shiftdrag") await page.keyboard.up("Shift");
    await page.waitForTimeout(1200);
    console.log("MID-DRAG BOX VISIBLE:", boxVisible);
    console.log("AFTER DRAG:", JSON.stringify(await state()));
    await page.screenshot({ path: path.join(OUT, scenario + "_3_after.png") });
  }

  if (scenario === "autoswitch") {
    const go = (lng, lat, z) => page.evaluate(([lng, lat, z]) => {
      window.__cockpit.panes[0].tv.map.jumpTo({ center: [lng, lat], zoom: z });
    }, [lng, lat, z]);
    await go(135, 20, 4);           // deep in the WPAC
    await page.waitForTimeout(3000);
    console.log("AFTER WPAC ZOOM:", JSON.stringify(await state()));
    await page.screenshot({ path: path.join(OUT, "auto_1_wpac.png") });
    await page.waitForTimeout(2200);  // cooldown
    await go(-95, 32, 4.2);          // over to CONUS
    await page.waitForTimeout(3000);
    console.log("AFTER CONUS PAN:", JSON.stringify(await state()));
    await page.screenshot({ path: path.join(OUT, "auto_2_conus.png") });
    await page.waitForTimeout(2200);
    // zoom out to the domain's floor -> expect the ring
    await page.evaluate(() => {
      const tv = window.__cockpit.panes[0].tv;
      tv.map.jumpTo({ zoom: tv._fitZoom != null ? tv._fitZoom : 1 });
    });
    await page.waitForTimeout(3000);
    console.log("AFTER ZOOM OUT:", JSON.stringify(await state()));
    await page.screenshot({ path: path.join(OUT, "auto_3_world.png") });
  }

  if (scenario === "tmback") {
    await page.evaluate(() => {
      window.__cockpit.panes[0].tv.map.jumpTo({ center: [-95, 32], zoom: 4.2 });
    });
    await page.waitForTimeout(3500);   // auto-switch to conus + persistURL
    console.log("PRE-TM:", JSON.stringify(await state()), "URL:", await page.evaluate(() => location.search));
    await page.click("#cx-tm");
    await page.waitForTimeout(800);
    console.log("IN TM:", JSON.stringify(await page.evaluate(() => ({
      tm: window.__cockpit.tm.on, hist: history.state }))));
    await page.goBack();
    await page.waitForTimeout(800);
    console.log("AFTER BACK:", JSON.stringify(await state()),
      "tm:", await page.evaluate(() => window.__cockpit.tm.on),
      "loc:", await page.evaluate(() => location.pathname));
    await page.screenshot({ path: path.join(OUT, "tmback_after.png") });
  }

  if (scenario === "mrms") {
    const btn = await page.evaluate(() => {
      const b = document.getElementById("cx-ov-mrms");
      return { disabled: b.disabled, text: b.textContent.trim() };
    });
    console.log("MRMS BUTTON:", JSON.stringify(btn));
    await page.evaluate(() => {
      window.__cockpit.panes[0].tv.map.jumpTo({ center: [-90, 33], zoom: 4 });
    });
    await page.waitForTimeout(3200);   // auto-switch to conus
    await page.click("#cx-ov-mrms");
    await page.waitForTimeout(4000);   // image fetch + decode
    const st = await page.evaluate(() => {
      const p = window.__cockpit.panes[0];
      return { domain: window.__cockpit.domain, radOn: p.rad && p.rad.on,
               stamp: p.rad && p.rad.stamp, layers: p._radLayers };
    });
    console.log("MRMS STATE:", JSON.stringify(st));
    await page.screenshot({ path: path.join(OUT, "mrms_overlay.png") });
  }

  if (scenario === "metar") {
    console.log("METAR BUTTON:", JSON.stringify(await page.evaluate(() => {
      const b = document.getElementById("cx-ov-metar");
      return { disabled: b.disabled, text: b.textContent.trim() };
    })));
    await page.evaluate(() => {
      window.__cockpit.panes[0].tv.map.jumpTo({ center: [-88, 33], zoom: 4.6 });
    });
    await page.waitForTimeout(3200);
    await page.click("#cx-ov-metar");
    await page.waitForTimeout(2500);
    console.log("METAR STATE:", JSON.stringify(await page.evaluate(() => {
      const p = window.__cockpit.panes[0];
      const cv = p._obsCanvas;
      let drawn = 0;
      if (cv) {
        const g = cv.getContext("2d");
        const d = g.getImageData(0, 0, cv.width, cv.height).data;
        for (let i = 3; i < d.length; i += 400) if (d[i] > 0) drawn++;
      }
      return { domain: window.__cockpit.domain, obsOn: p.obs && p.obs.on,
               canvas: !!cv, paintedSamples: drawn };
    })));
    await page.screenshot({ path: path.join(OUT, "metar_overlay.png") });
    // WPAC coverage check: jump to the western Pacific
    await page.waitForTimeout(2200);
    await page.evaluate(() => {
      window.__cockpit.panes[0].tv.map.jumpTo({ center: [135, 33], zoom: 4.6 });
    });
    await page.waitForTimeout(3500);
    await page.screenshot({ path: path.join(OUT, "metar_wpac.png") });
    console.log("WPAC DOMAIN:", await page.evaluate(() => window.__cockpit.domain));
  }

  if (scenario === "sfc") {
    console.log("SFC BUTTON:", JSON.stringify(await page.evaluate(() => {
      const b = document.getElementById("cx-ov-sfc");
      return { disabled: b.disabled, text: b.textContent.trim() };
    })));
    await page.evaluate(() => {
      window.__cockpit.panes[0].tv.map.jumpTo({ center: [-92, 40], zoom: 3.6 });
    });
    await page.waitForTimeout(3200);
    await page.click("#cx-ov-sfc");
    await page.click("#cx-ov-metar");   // obs + analysis together: the synoptic view
    await page.waitForTimeout(2500);
    console.log("SFC STATE:", JSON.stringify(await page.evaluate(() => {
      const p = window.__cockpit.panes[0];
      return { domain: window.__cockpit.domain, sfcOn: p.sfc && p.sfc.on,
               canvas: !!p._sfcCanvas };
    })));
    await page.screenshot({ path: path.join(OUT, "sfc_overlay.png") });
  }

  if (scenario === "selector") {
    // 1) GOES-19 sat-row click from the ring: tiles AND label must both switch
    await page.click('#cx-sats .cx-item[data-sat="goes19"]');
    await page.waitForTimeout(4000);
    const st1 = await page.evaluate(() => {
      const S = window.__cockpit, p = S.panes[0];
      return { sel: S.domain, renders: p.tv.manifest && p.tv.manifest.product,
               header: document.getElementById("cx-pht-0").textContent };
    });
    console.log("AFTER GOES-19 CLICK:", JSON.stringify(st1));
    // 2) back to the ring
    await page.click('#cx-sats .cx-item[data-sat="geo"]');
    await page.waitForTimeout(4000);
    // 3) RE-SELECT the ring while already on it (the crash path)
    await page.click('#cx-sats .cx-item[data-sat="geo"]');
    await page.waitForTimeout(1500);
    // 4) same-product re-select through setProduct directly (belt+braces)
    await page.evaluate(() => {
      const p = window.__cockpit.panes[0];
      return p.tv.setProduct(p.tv.manifestUrl).then(() => true);
    });
    await page.waitForTimeout(800);
    const st2 = await page.evaluate(() => {
      const S = window.__cockpit, p = S.panes[0];
      return { sel: S.domain, renders: p.tv.manifest && p.tv.manifest.product,
               header: document.getElementById("cx-pht-0").textContent,
               errShown: document.getElementById("cx-err").style.display === "flex" };
    });
    console.log("AFTER RING RE-SELECT x2:", JSON.stringify(st2));
    await page.screenshot({ path: path.join(OUT, "selector_after.png") });
  }

  if (scenario === "mrmszoom") {
    // MRMS_ZOOM="lon,lat,zoom" picks the target (default Upper Midwest) —
    // point it at wherever the live composite has echoes
    const zt = (process.env.MRMS_ZOOM || "-96.5,42.5,6.4").split(",").map(Number);
    await page.evaluate(([lng, lat, z]) => {
      window.__cockpit.panes[0].tv.map.jumpTo({ center: [lng, lat], zoom: z });
    }, zt);
    if (process.env.MRMS_FIELD) {   // e.g. irbd — a gray base reads radar best
      await page.waitForTimeout(3200);
      await page.evaluate((key) => {
        const row = [...document.querySelectorAll(".cx-field")].find(r => r.dataset.key === key);
        if (row) row.click();
      }, process.env.MRMS_FIELD);
      await page.waitForTimeout(4000);
    }
    await page.waitForTimeout(3500);
    await page.click("#cx-ov-mrms");
    // bench-only: the local series holds one fresh scan; the cached sat loop
    // is hours older, so bypass the skew gate to display the scan for the
    // quality check (production keeps the gate)
    await page.evaluate(() => {
      const p = window.__cockpit.panes[0];
      p.rad.on = true;
      const m = window.CockpitFields;
    });
    await page.waitForTimeout(4000);
    await page.screenshot({ path: path.join(OUT, "mrms_zoom_quality.png") });
    console.log("ZOOMED SHOT SAVED");
  }

  if (scenario === "mrmsanim") {
    await page.evaluate(() => {
      window.__cockpit.panes[0].tv.map.jumpTo({ center: [-90, 33], zoom: 4 });
    });
    await page.waitForTimeout(3200);   // auto-switch to conus
    await page.click("#cx-ov-mrms");
    await page.waitForTimeout(2500);
    const probe = async (label) => {
      const s = await page.evaluate(() => {
        const p = window.__cockpit.panes[0];
        return { sat: p.tv.frames[p.tv.frameIdx], shown: p.rad && p.rad.shown,
                 vis: p.tv.map.getLayer("ofrad-0")
                   ? p.tv.map.getLayoutProperty("ofrad-0", "visibility") : "none" };
      });
      console.log(label, JSON.stringify(s));
      return s;
    };
    // scrub to the loop tail (near-now sat frame -> newest scan)
    await page.evaluate(() => {
      const tv = window.__cockpit.panes[0].tv;
      tv.showFrame(tv.frames.length - 1);
    });
    await page.waitForTimeout(1500);
    await probe("TAIL:");
    // scrub ~2h back: should time-lock to the OLDER scan or hide on skew
    await page.evaluate(() => {
      const tv = window.__cockpit.panes[0].tv;
      tv.showFrame(Math.max(0, tv.frames.length - 14));
    });
    await page.waitForTimeout(1500);
    await probe("BACK-2H:");
    // scrub far back: no scan within 45 min -> hidden
    await page.evaluate(() => {
      window.__cockpit.panes[0].tv.showFrame(0);
    });
    await page.waitForTimeout(1500);
    await probe("OLDEST:");
    await page.screenshot({ path: path.join(OUT, "mrmsanim.png") });
    // burial check: after a product switch + new frame mounts, the radar
    // layer must still sit directly under 'grat' (above all frame layers)
    await page.evaluate(() => {
      const tv = window.__cockpit.panes[0].tv;
      tv.showFrame(tv.frames.length - 1);
    });
    await page.waitForTimeout(1200);
    const order = await page.evaluate(() => {
      const layers = window.__cockpit.panes[0].tv.map.getStyle().layers.map(l => l.id);
      const grat = layers.indexOf("grat"), rad = layers.indexOf("ofrad-0");
      const frames = layers.filter(id => /^sat\//.test(id));
      const maxFrame = Math.max(...frames.map(id => layers.indexOf(id)));
      return { radIdx: rad, gratIdx: grat, maxFrameIdx: maxFrame,
               radAboveFrames: rad > maxFrame && rad < grat };
    });
    console.log("LAYER ORDER:", JSON.stringify(order));
  }

  if (scenario === "playsmooth") {
    await page.evaluate(() => {
      window.__cockpit.panes[0].tv.map.jumpTo({ center: [-90, 33], zoom: 4 });
    });
    await page.waitForTimeout(3500);
    await page.click("#cx-ov-mrms");
    await page.click("#cx-ov-metar");
    await page.click("#cx-ov-sfc");
    await page.waitForTimeout(6000);    // preload settles
    // record reveals over 6 s of playback with all overlays on
    await page.evaluate(() => {
      const tv = window.__cockpit.panes[0].tv;
      window.__reveals = [];
      const orig = tv._reveal.bind(tv);
      tv._reveal = (idx, stamp) => { window.__reveals.push([performance.now() | 0, stamp]); orig(idx, stamp); };
    });
    await page.click("#cx-play");
    await page.waitForTimeout(16000);
    await page.click("#cx-play");
    const r = await page.evaluate(() => {
      const rv = window.__reveals || [];
      const gaps = [];
      for (let i = 1; i < rv.length; i++) gaps.push(rv[i][0] - rv[i - 1][0]);
      // the loop's DATA density: valid-time span + spacing of the frame list
      const fr = window.__cockpit.panes[0].tv.frames || [];
      const ms = (s) => Date.UTC(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8),
                                +s.slice(9, 11), +s.slice(11, 13), +s.slice(13, 15) || 0);
      const fg = [];
      for (let i = 1; i < fr.length; i++) fg.push((ms(fr[i]) - ms(fr[i - 1])) / 60e3);
      fg.sort((a, b) => a - b);
      window.__frameStats = {
        nFrames: fr.length,
        spanH: fr.length > 1 ? +((ms(fr[fr.length - 1]) - ms(fr[0])) / 3600e3).toFixed(1) : 0,
        gapMinMedMax: fg.length ? [fg[0], fg[Math.floor(fg.length / 2)], fg[fg.length - 1]] : null,
        perHour: fr.length > 1 ? +(fr.length / ((ms(fr[fr.length - 1]) - ms(fr[0])) / 3600e3)).toFixed(1) : 0,
      };
      // split: first 8 s = cold fill, last 8 s = steady-state cadence (what
      // a settled loop feels like). jitter = p95-ish spread of warm gaps.
      const t0 = rv.length ? rv[0][0] : 0;
      const warm = [];
      for (let i = 1; i < rv.length; i++)
        if (rv[i][0] - t0 >= 8000) warm.push(rv[i][0] - rv[i - 1][0]);
      const sorted = warm.slice().sort((a, b) => a - b);
      return { reveals: rv.length,
               maxGap: gaps.length ? Math.max(...gaps) : null,
               warmGaps: warm, warmMax: warm.length ? Math.max(...warm) : null,
               warmMedian: sorted.length ? sorted[Math.floor(sorted.length / 2)] : null,
               frameStats: window.__frameStats,
               probeStamps: rv.slice(0, 3).map(x => x[1]) };
    });
    console.log("PLAYBACK:", JSON.stringify(r));
    await page.screenshot({ path: path.join(OUT, "playsmooth.png") });
  }

  if (scenario === "persist") {
    const snap = async (label) => {
      const s = await page.evaluate(() => {
        const S = window.__cockpit, p = S.panes[0], map = p.tv.map;
        const layers = map.getStyle().layers.map(l => l.id);
        const cv = (c) => {
          if (!c) return 0;
          const g = c.getContext("2d");
          const d = g.getImageData(0, 0, c.width, c.height).data;
          let n = 0;
          for (let i = 3; i < d.length; i += 800) if (d[i] > 0) n++;
          return n;
        };
        return {
          domain: S.domain, product: p.tv.manifest && p.tv.manifest.product,
          radOn: !!(p.rad && p.rad.on),
          radLayer: layers.includes("ofrad-0"),
          radVis: map.getLayer("ofrad-0") ? map.getLayoutProperty("ofrad-0", "visibility") : null,
          obsOn: !!(p.obs && p.obs.on), obsPaint: cv(p._obsCanvas),
          sfcOn: !!(p.sfc && p.sfc.on), sfcPaint: cv(p._sfcCanvas),
          btnRad: document.getElementById("cx-ov-mrms").classList.contains("on"),
          btnObs: document.getElementById("cx-ov-metar").classList.contains("on"),
          btnSfc: document.getElementById("cx-ov-sfc").classList.contains("on"),
        };
      });
      console.log(label, JSON.stringify(s));
      return s;
    };
    await page.evaluate(() => {
      window.__cockpit.panes[0].tv.map.jumpTo({ center: [-90, 33], zoom: 4 });
    });
    await page.waitForTimeout(3200);
    await page.click("#cx-ov-mrms");
    await page.click("#cx-ov-metar");
    await page.click("#cx-ov-sfc");
    await page.waitForTimeout(2500);
    await snap("BASELINE:");
    // (a) channel switch ir -> wv (c08)
    await page.evaluate(() => {
      const rows = [...document.querySelectorAll(".cx-field")];
      const wv = rows.find(r => r.dataset.key === "c08");
      if (wv) wv.click();
    });
    await page.waitForTimeout(4500);
    await snap("AFTER CHANNEL SWITCH (c08):");
    // (b) cross-sat domain switch
    await page.click('#cx-sats .cx-item[data-sat="himawari9"]');
    await page.waitForTimeout(5000);
    await snap("AFTER SAT SWITCH (himawari):");
    await page.click('#cx-sats .cx-item[data-sat="goes19"]');
    await page.waitForTimeout(5000);
    await snap("AFTER SWITCH BACK (goes19):");
    await page.screenshot({ path: path.join(OUT, "persist_final.png") });
  }

  if (scenario === "coldgeo") {
    const geoCount = () => page.evaluate(() => {
      const tv = window.__cockpit.panes[0].tv;
      return Object.keys(tv._added || {}).filter(() => true).length &&
        Object.keys(tv.map.getStyle().sources)
          .filter(id => id.indexOf("sat/geo/global") === 0).length;
    });
    console.log("PRE-SWITCH:", JSON.stringify(await state()));
    await page.click('#cx-sats .cx-item[data-sat="geo"]');
    await page.waitForTimeout(1200);
    const t1 = await geoCount();
    await page.waitForTimeout(2500);
    const t2 = await geoCount();
    await page.waitForTimeout(5000);
    const t3 = await geoCount();
    await page.waitForTimeout(8000);
    const t4 = await geoCount();
    console.log("GEO SOURCES over time:", JSON.stringify({ t1, t2, t3, t4 }));
    console.log("FINAL:", JSON.stringify(await state()));
    await page.screenshot({ path: path.join(OUT, "coldgeo.png") });
  }

  if (scenario === "nhcanim") {
    await page.evaluate(() => {
      window.__cockpit.panes[0].tv.map.jumpTo({ center: [-100, 25], zoom: 3.4 });
    });
    await page.waitForTimeout(3200);
    await page.click("#cx-ov-nhc");
    await page.click("#cx-ov-metar");
    await page.click("#cx-ov-sfc");
    await page.waitForTimeout(3500);
    const st = await page.evaluate(() => {
      const p = window.__cockpit.panes[0], map = p.tv.map;
      const layers = map.getStyle().layers.map(l => l.id);
      const cvPaint = (c) => {
        if (!c) return 0;
        const g = c.getContext("2d");
        const d = g.getImageData(0, 0, c.width, c.height).data;
        let n = 0;
        for (let i = 3; i < d.length; i += 800) if (d[i] > 0) n++;
        return n;
      };
      return {
        nhcLayers: layers.filter(id => id.startsWith("ofnhc-0")),
        nhcGlyphs: cvPaint(p._nhcCanvas),
        nhcBtn: document.getElementById("cx-ov-nhc").classList.contains("on"),
        obsShown: p.obs && p.obs.shown, sfcShown: p.sfc && p.sfc.shown,
        obsPaint: cvPaint(p._obsCanvas), sfcPaint: cvPaint(p._sfcCanvas),
      };
    });
    console.log("NHC/ANIM STATE:", JSON.stringify(st));
    // scrub far back: the obs join must drop (skew) while sfc may hold
    await page.evaluate(() => { window.__cockpit.panes[0].tv.showFrame(0); });
    await page.waitForTimeout(1800);
    console.log("AFTER OLD SCRUB:", JSON.stringify(await page.evaluate(() => {
      const p = window.__cockpit.panes[0];
      return { sat: p.tv.frames[p.tv.frameIdx], obsShown: p.obs.shown, sfcShown: p.sfc.shown };
    })));
    await page.screenshot({ path: path.join(OUT, "nhc_overlay.png") });
  }

  if (scenario === "uhr") {
    // Scatterometer layer with the UHR companion feed: passes must appear in
    // the pass list (sensor-labelled), the ~2 km field raster must mount
    // under the barbs, and the barbs must draw from the decimated wvc set.
    const um = UHR_LOCAL
      ? JSON.parse(fs.readFileSync(path.join(UHR_LOCAL, "ascat/uhr/manifest.json")))
      : await (await fetch("https://cdn.triple-a-tropics.com/ascat/uhr/manifest.json")).json();
    // prefer a storm-tagged pass (the product exists for storms); else newest
    const up = um.passes.find((p) => (p.storms || []).length) || um.passes[0];
    const [ux, uy] = [(up.bbox[0] + up.bbox[2]) / 2, (up.bbox[1] + up.bbox[3]) / 2];
    await page.evaluate(([lng, lat]) => {
      window.__cockpit.panes[0].tv.map.jumpTo({ center: [lng, lat], zoom: 4.6 });
    }, [ux, uy]);
    await page.waitForTimeout(3200);
    await page.click("#cx-ov-sc");
    await page.waitForTimeout(4000);
    const st = await page.evaluate(() => {
      const p = window.__cockpit.panes[0], map = p.tv.map;
      const layers = map.getStyle().layers.map(l => l.id);
      const cv = p._scCanvas;
      let painted = 0;
      if (cv) {
        const d = cv.getContext("2d").getImageData(0, 0, cv.width, cv.height).data;
        for (let i = 3; i < d.length; i += 400) if (d[i] > 0) painted++;
      }
      return {
        passes: (p._scPasses || []).map(x => x.id),
        uhrLoaded: (p._scPasses || []).some(x => x.uhr),
        fieldLayers: layers.filter(id => id.startsWith("ofscf-")),
        barbsPainted: painted,
      };
    });
    console.log("UHR STATE:", JSON.stringify(st));
    await page.screenshot({ path: path.join(OUT, "uhr_overlay.png") });
  }

  if (scenario === "fieldroute") {
    // boot lands on the GEO ring (3 BT fields); clicking a per-satellite
    // field (True Color, a channel) must ROUTE to the nadir-nearest single
    // sat + select it there — not dead-end on a "no data yet" chip
    const chipTxt = await page.evaluate(() => {
      const row = [...document.querySelectorAll(".cx-field")].find(r => r.dataset.key === "truecolor");
      return row ? row.textContent.replace(/\s+/g, " ") : null;
    });
    console.log("RING TRUECOLOR ROW:", JSON.stringify(chipTxt));
    await page.evaluate(() => {
      const row = [...document.querySelectorAll(".cx-field")].find(r => r.dataset.key === "truecolor");
      if (row) row.click();
    });
    await page.waitForTimeout(4500);
    const st1 = await page.evaluate(() => {
      const S = window.__cockpit, p = S.panes[0];
      return { domain: S.domain, product: p.product && p.product.key,
               renders: p.tv.manifest && p.tv.manifest.product };
    });
    console.log("AFTER TRUECOLOR CLICK:", JSON.stringify(st1));
    await page.screenshot({ path: path.join(OUT, "fieldroute_truecolor.png") });
    // channel from the ring: switch back to global first
    await page.click('#cx-sats .cx-item[data-sat="geo"]');
    await page.waitForTimeout(3000);
    await page.evaluate(() => {
      const tabs = [...document.querySelectorAll(".cx-tab")];
      const ch = tabs.find(t => /channel/i.test(t.textContent));
      if (ch) ch.click();
      const row = [...document.querySelectorAll(".cx-field")].find(r => r.dataset.key === "c02");
      if (row) row.click();
    });
    await page.waitForTimeout(4500);
    const st2 = await page.evaluate(() => {
      const S = window.__cockpit, p = S.panes[0];
      return { domain: S.domain, product: p.product && p.product.key,
               renders: p.tv.manifest && p.tv.manifest.product };
    });
    console.log("AFTER C02 CLICK FROM RING:", JSON.stringify(st2));
    await page.screenshot({ path: path.join(OUT, "fieldroute_channel.png") });
  }

  if (scenario === "nhcpolish") {
    // needs a local NHC feed alias (HARNESS_FEEDS) or the live feed; reads
    // the doc to find a cone + an AOI, then exercises the forecast glyphs
    // and the click dialog for real
    const feed = NHC_LOCAL
      ? JSON.parse(fs.readFileSync(path.join(NHC_LOCAL, "nhc/overlay/latest.json")))
      : await (await fetch("https://cdn.triple-a-tropics.com/nhc/overlay/latest.json")).json();
    const centroid = (coords) => {
      const ring = coords[0];
      let sx = 0, sy = 0;
      ring.forEach(([x, y]) => { sx += x; sy += y; });
      return [sx / ring.length, sy / ring.length];
    };
    const cone = feed.features.find((f) => f.properties.kind === "cone");
    const area = feed.features.find((f) => f.properties.kind === "area");
    await page.click("#cx-ov-nhc");
    await page.waitForTimeout(2000);
    if (cone) {
      const [cx, cy] = centroid(cone.geometry.coordinates);
      await page.evaluate(([lng, lat]) => {
        window.__cockpit.panes[0].tv.map.jumpTo({ center: [lng, lat], zoom: 4.4 });
      }, [cx, cy]);
      await page.waitForTimeout(3500);
      const st = await page.evaluate(() => {
        const p = window.__cockpit.panes[0], map = p.tv.map;
        const layers = map.getStyle().layers.map(l => l.id);
        const cv = p._nhcCanvas;
        let painted = 0;
        if (cv) {
          const d = cv.getContext("2d").getImageData(0, 0, cv.width, cv.height).data;
          for (let i = 3; i < d.length; i += 400) if (d[i] > 0) painted++;
        }
        return { trackCase: layers.includes("ofnhc-0-track-case"),
                 track: layers.includes("ofnhc-0-track"), painted };
      });
      console.log("CONE/FORECAST:", JSON.stringify(st));
      await page.screenshot({ path: path.join(OUT, "nhc_cone_forecast.png") });
    } else console.log("CONE/FORECAST: no active storm in feed");
    if (area) {
      const [ax, ay] = centroid(area.geometry.coordinates);
      await page.evaluate(([lng, lat]) => {
        window.__cockpit.panes[0].tv.map.jumpTo({ center: [lng, lat], zoom: 4.2 });
      }, [ax, ay]);
      await page.waitForTimeout(3500);
      const pt = await page.evaluate(([lng, lat]) => {
        const p = window.__cockpit.panes[0];
        const xy = p.tv.map.project([lng, lat]);
        return { x: xy.x, y: xy.y };
      }, [ax, ay]);
      const mapBox = await page.locator("#cx-map-0").boundingBox();
      await page.mouse.click(mapBox.x + pt.x, mapBox.y + pt.y);
      await page.waitForTimeout(900);
      const dlg = await page.evaluate(() => {
        const el = window.__cockpit.panes[0]._nhcDialog;
        return { shown: !!(el && el.style.display === "block"),
                 text: el ? el.textContent.replace(/\s+/g, " ").slice(0, 160) : null };
      });
      console.log("AOI DIALOG:", JSON.stringify(dlg));
      await page.screenshot({ path: path.join(OUT, "nhc_aoi_dialog.png") });
    } else console.log("AOI DIALOG: no formation areas in feed");
  }

  console.log("CONSOLE ERRORS:", errors.length ? JSON.stringify(errors.slice(0, 10), null, 1) : "none");
  await browser.close();
  if (srv) srv.close();
})();
