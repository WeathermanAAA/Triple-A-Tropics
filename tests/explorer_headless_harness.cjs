// MANUAL headless harness for the satellite explorer (not part of unittest
// discovery — needs `npm install --no-save playwright jsdom` + `npx
// playwright install chromium`). Boots the REAL page against the live CDN
// (CORS re-served via a Playwright route) and runs a named scenario with
// screenshots to <outdir>/. Local overlay feeds (mrms/metar/sfc emitted via
// the generators' --store local:...) can be routed in over the CDN paths —
// edit the *_LOCAL constants. Built 2026-07-18 for the tester-bug sweep;
// every scenario printed its state + console errors and screenshotted.
// Usage: node tests/explorer_headless_harness.cjs <scenario> [outdir]
// Scenarios: boot | drawbox | shiftdrag | autoswitch | tmback | mrms | metar | sfc
"use strict";
const { chromium } = require("playwright");
const http = require("http");
const path = require("path");
const fs = require("fs");

const ROOT = "/workspaces/Triple-A-Tropics";
const OUT = process.argv[3] || "/tmp/tat-explorer-shots";
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
  const srv = await serve();
  const base = "http://127.0.0.1:" + srv.address().port;
  const browser = await chromium.launch({ args: ['--enable-unsafe-swiftshader', '--use-gl=angle', '--use-angle=swiftshader'] });
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text().slice(0, 300)); });
  page.on("pageerror", (e) => errors.push("PAGEERROR " + String(e).slice(0, 300)));

  // the CDN's CORS allowlist doesn't cover localhost: re-serve its responses
  // with permissive headers so the page behaves as it does on the real origin
  await page.route("https://cdn.triple-a-tropics.com/**", async (route) => {
    try {
      const r = await route.fetch();
      const headers = Object.assign({}, r.headers(),
        { "access-control-allow-origin": "*" });
      delete headers["cross-origin-resource-policy"];
      await route.fulfill({ response: r, headers });
    } catch (e) { await route.abort(); }
  });

  // MRMS scenario: the R2 emit hasn't run yet — serve the locally-emitted
  // overlay files for its prefix so the full client path exercises for real
  const MRMS_LOCAL = process.env.MRMS_LOCAL || "/tmp/tat-mrms/";
  await page.route("https://cdn.triple-a-tropics.com/radar/mrms/**", async (route) => {
    const rel = route.request().url().replace("https://cdn.triple-a-tropics.com/", "").split("?")[0];
    try {
      const body = fs.readFileSync(path.join(MRMS_LOCAL, rel));
      const ctype = rel.endsWith(".json") ? "application/json" : "image/webp";
      await route.fulfill({ status: 200, body, headers: {
        "content-type": ctype, "access-control-allow-origin": "*" } });
    } catch (e) { await route.fulfill({ status: 404, body: "nf", headers: { "access-control-allow-origin": "*" } }); }
  });


  const OBS_LOCAL = process.env.OBS_LOCAL || "/tmp/tat-metar/";
  await page.route("https://cdn.triple-a-tropics.com/obs/metar/**", async (route) => {
    const rel = route.request().url().replace("https://cdn.triple-a-tropics.com/", "").split("?")[0];
    try {
      const body = fs.readFileSync(path.join(OBS_LOCAL, rel));
      await route.fulfill({ status: 200, body, headers: {
        "content-type": "application/json", "access-control-allow-origin": "*" } });
    } catch (e) { await route.fulfill({ status: 404, body: "nf", headers: { "access-control-allow-origin": "*" } }); }
  });

  const SFC_LOCAL = process.env.SFC_LOCAL || "/tmp/tat-sfc/";
  await page.route("https://cdn.triple-a-tropics.com/sfc/**", async (route) => {
    const rel = route.request().url().replace("https://cdn.triple-a-tropics.com/", "").split("?")[0];
    try {
      const body = fs.readFileSync(path.join(SFC_LOCAL, rel));
      await route.fulfill({ status: 200, body, headers: {
        "content-type": "application/json", "access-control-allow-origin": "*" } });
    } catch (e) { await route.fulfill({ status: 404, body: "nf", headers: { "access-control-allow-origin": "*" } }); }
  });

  await page.goto(base + "/satellite/explorer/", { waitUntil: "domcontentloaded" });

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

  console.log("CONSOLE ERRORS:", errors.length ? JSON.stringify(errors.slice(0, 10), null, 1) : "none");
  await browser.close();
  srv.close();
})();
