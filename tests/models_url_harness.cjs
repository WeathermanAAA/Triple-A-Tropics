/*
 * Browser harness for the /models/ orthogonal URL scheme (item 18).
 *
 * The HISTORY POLICY is the part that has to be proven rather than eyeballed:
 * scrubbing must use debounced replaceState (43 forecast hours must never
 * become 43 back-button presses), pushState fires only on real navigation
 * (storm/model/domain/product/cycle/mode), back restores the full view, and a
 * shared link whose run has expired falls back gracefully.
 *
 * Serves the repo over a local HTTP server (history.pushState is not reliable
 * on file://) and intercepts the manifest with a synthetic two-cycle document.
 *
 * Run: node tests/models_url_harness.cjs
 */
const http = require('http');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const REPO = path.join(__dirname, '..');

let failures = 0;
function ok(cond, msg) {
  console.log((cond ? 'ok   - ' : 'FAIL - ') + msg);
  if (!cond) failures++;
}

function manifest() {
  const frames = [0, 3, 6, 9, 12, 15, 18, 21, 24];
  const storm = (id, basin) => ({
    id, name: id.toUpperCase(), basin,
    cycle: '2026073100', init: '2026-07-31T00:00:00Z',
    frames: {
      hafsa: { storm: { mslp_wind: frames, refl: frames } },
      hafsb: { storm: { mslp_wind: frames, refl: frames } },
    },
    expected: { hafsa: { storm: frames }, hafsb: { storm: frames } },
  });
  const cyc = (key, storms) => ({
    cycle: key, in_progress: false, frames_done: 9, frames_expected: 9,
    started_utc: 't', storms,
  });
  return {
    generated_at: 't', fxx_step: 3, fxx_pad: 3, fxx_end: 24,
    products: [{ slug: 'mslp_wind', label: 'MSLP + Wind', short: 'Wind' },
               { slug: 'refl', label: 'Reflectivity', short: 'Refl' }],
    models: [{ slug: 'hafsa', label: 'HAFS-A' }, { slug: 'hafsb', label: 'HAFS-B' }],
    domains: [{ slug: 'storm', label: 'Storm nest', raw: 'storm.atm' }],
    path_template_cycles: '{cycle}/{model}/{storm}/{domain}/{product}/f{fxx}.png',
    cycles: [cyc('2026073100', [storm('95e', 'ep'), storm('07e', 'ep'),
                                storm('12w', 'wp')]),
             cyc('2026073018', [storm('07e', 'ep')])],
  };
}

// Give ONE storm (07e, newest cycle) the shear diagnostic + per-frame
// geometry, hafsa only - so eligibility (storm+model+domain gated) and the
// honest no-fix-hour degradation are both exercised.
function withShear(m) {
  const s07 = m.cycles[0].storms[1];
  const geo = { axes_px: [96.1, 114.7, 1504.7, 1627.5],
                bbox: [-154.45, 19.65, -148.95, 25.15],
                crosses_antimeridian: false };
  s07.geometry = { hafsa: { storm: { 0: geo, 3: geo } } };
  s07.shear = {
    params: { method: 'azimuthal_mean', layer_hpa: [200, 850], radius_km: 500,
              center: 'model_vortex_trak', heading: 'toward' },
    hours: { hafsa: { 0: { kt: 12.3, hdg: 53.2, naive_kt: 14.1, naive_hdg: 51.9 } } },
  };
  return m;
}

(async () => {
  const server = http.createServer((req, res) => {
    const p = path.join(REPO, req.url.split('?')[0].replace(/\/$/, '/index.html'));
    fs.readFile(p, (err, data) => {
      if (err) { res.writeHead(404); res.end(); return; }
      const type = p.endsWith('.js') ? 'application/javascript'
        : p.endsWith('.html') ? 'text/html' : 'application/octet-stream';
      res.writeHead(200, { 'Content-Type': type });
      res.end(data);
    });
  });
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const port = server.address().port;
  const base = `http://127.0.0.1:${port}/models/index.html`;

  const browser = await chromium.launch();
  const page = await browser.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push(String(e.message)));
  await page.route('**/manifest.json*', r => r.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(withShear(manifest())) }));
  // Frames get a real 1x1 PNG (naturalWidth must be non-zero for the shear
  // overlay); everything else off-origin 404s.
  const PNG1 = Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
    'base64');
  await page.route('**/cdn.triple-a-tropics.com/**', r => {
    const u = r.request().url();
    if (/manifest\.json/.test(u)) return r.fallback();
    if (/\.png/.test(u)) {
      return r.fulfill({ status: 200, contentType: 'image/png', body: PNG1 });
    }
    r.fulfill({ status: 404, body: '' });
  });
  await page.route('**/global_storms.geojson*', r => r.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({
      features: [
        // 12W is the STRONGEST active storm; 07E weaker; 95E is an invest.
        { properties: { storm_id: 'JTWC_WP122026', is_active: true, peak_kt: 95 } },
        { properties: { storm_id: 'NHC_EP072026', is_active: true, peak_kt: 60 } },
        { properties: { storm_id: 'NHC_EP952026', is_active: true, peak_kt: 120 } },
      ]
    }) }));
  await page.route('**/cdnjs.cloudflare.com/**', r => r.fulfill({ status: 200, contentType: 'application/javascript', body: '' }));
  await page.route('**/fonts.cdnfonts.com/**', r => r.fulfill({ status: 200, contentType: 'text/css', body: '' }));

  // ---- 1. boot from a fully-specified shared link -------------------------
  await page.goto(base + '?run=2026073018&storm=07e&model=hafsb&domain=storm&product=refl&fxx=12',
                  { waitUntil: 'load', timeout: 30000 });
  await page.waitForFunction(() => {
    const v = window.__hafsViewer;
    return v && v.storm && v.fxxList.length;
  }, { timeout: 15000 }).catch(() => {});
  const st = await page.evaluate(() => {
    const v = window.__hafsViewer;
    return { cycle: v.cycle.cycle, storm: v.storm.id, model: v.model,
             domain: v.domain, product: v.product, fxx: v.fxxList[v.idx] };
  });
  console.log('# boot from shared link');
  ok(errs.length === 0, 'no page errors: ' + (errs[0] || 'none'));
  ok(st.cycle === '2026073018', 'run restored: ' + st.cycle);
  ok(st.storm === '07e', 'storm restored');
  ok(st.model === 'hafsb', 'model restored (not the default)');
  ok(st.product === 'refl', 'product restored (not the default)');
  ok(st.fxx === 12, 'hour restored: F' + st.fxx);

  // ---- 2. scrubbing never grows history -----------------------------------
  const h0 = await page.evaluate(() => history.length);
  await page.evaluate(() => {
    const v = window.__hafsViewer;
    for (let i = 0; i < v.fxxList.length; i++) v._show(i);   // scrub them all
  });
  await page.waitForTimeout(400);   // let the 250 ms debounce land
  const h1 = await page.evaluate(() => history.length);
  const urlFxx = await page.evaluate(() =>
    new URLSearchParams(location.search).get('fxx'));
  console.log('# scrub history policy');
  ok(h1 === h0, `scrubbing all hours grew history by ${h1 - h0} (must be 0)`);
  ok(urlFxx === '24', 'debounced replaceState landed on the final hour: fxx=' + urlFxx);

  // ---- 3. product click pushes exactly one entry --------------------------
  await page.evaluate(() => {
    [...document.querySelectorAll('#hafs-products .hafs-seg')]
      .find(b => b.getAttribute('data-slug') === 'mslp_wind').click();
  });
  await page.waitForTimeout(100);
  const h2 = await page.evaluate(() => history.length);
  ok(h2 === h1 + 1, `product switch pushed exactly one entry (${h2 - h1})`);
  ok(await page.evaluate(() => new URLSearchParams(location.search).get('product')) === 'mslp_wind',
     'URL reflects the new product');

  // ---- 4. back restores the previous view ---------------------------------
  await page.goBack();
  await page.waitForTimeout(600);
  const backProduct = await page.evaluate(() => window.__hafsViewer.product);
  ok(backProduct === 'refl', 'back restored the previous product: ' + backProduct);

  // ---- 5. expired run falls back gracefully -------------------------------
  await page.goto(base + '?run=2026070100&storm=07e&product=refl&fxx=6',
                  { waitUntil: 'load' });
  await page.waitForFunction(() => {
    const v = window.__hafsViewer;
    return v && v.storm && v.fxxList.length;
  }, { timeout: 15000 });
  const exp = await page.evaluate(() => {
    const v = window.__hafsViewer;
    return { cycle: v.cycle.cycle, product: v.product, fxx: v.fxxList[v.idx],
             toast: (document.querySelector('.hafs-toast') || {}).textContent || '' };
  });
  console.log('# expired shared run');
  ok(exp.cycle === '2026073100', 'expired run fell back to the default run');
  ok(exp.product === 'refl' && exp.fxx === 6,
     'the REST of the link still applied (product + hour)');
  ok(/expired/.test(exp.toast), 'the fallback is said out loud: ' + exp.toast.slice(0, 60));

  // ---- 6. smart default (item 11): deterministic, strongest active storm --
  await page.goto(base, { waitUntil: 'load' });
  await page.waitForFunction(() => {
    const v = window.__hafsViewer;
    return v && v.storm && v.fxxList.length;
  }, { timeout: 15000 });
  const smart = await page.evaluate(() => window.__hafsViewer.storm.id);
  console.log('# smart default');
  ok(smart === '12w',
     'default = strongest active NAMED storm (12w @95kt), beating manifest ' +
     'order (95e listed first) and a stronger INVEST (95e @120kt): got ' + smart);
  const url = await page.goto(base + '?storm=07e', { waitUntil: 'load' });
  await page.waitForFunction(() => {
    const v = window.__hafsViewer;
    return v && v.storm && v.storm.id === '07e';
  }, { timeout: 15000 }).catch(() => {});
  ok(await page.evaluate(() => window.__hafsViewer.storm.id) === '07e',
     'an explicit URL storm still beats the smart default');

  // ---- 7. expert keyboard layer (item 9) ----------------------------------
  console.log('# keyboard layer');
  await page.goto(base + '?storm=07e&fxx=6', { waitUntil: 'load' });
  await page.waitForFunction(() => {
    const v = window.__hafsViewer;
    return v && v.storm && v.fxxList.length;
  }, { timeout: 15000 });
  await page.click('#hafs-stage');   // ensure focus is not on a control
  const fx = () => page.evaluate(() => {
    const v = window.__hafsViewer;
    return { fxx: v.fxxList[v.idx], run: v.cycle.cycle, playing: v.playing };
  });
  await page.keyboard.press('ArrowRight');
  ok((await fx()).fxx === 9, 'ArrowRight steps the forecast hour');
  await page.keyboard.press('ArrowLeft');
  ok((await fx()).fxx === 6, 'ArrowLeft steps back');

  // RUN TREND: same valid time across inits. 2026073100 F6 -> older run
  // 2026073018 is 6 h earlier, so the same valid time is F12 there.
  await page.keyboard.press('ArrowDown');
  await page.waitForTimeout(200);
  let s7 = await fx();
  ok(s7.run === '2026073018' && s7.fxx === 12,
     'ArrowDown = older run, SAME VALID TIME (F6@00Z -> F12@18Z): got ' +
     s7.run + ' F' + s7.fxx);
  await page.keyboard.press('ArrowUp');
  await page.waitForTimeout(200);
  s7 = await fx();
  ok(s7.run === '2026073100' && s7.fxx === 6,
     'ArrowUp returns to the newer run at the same valid time');
  const trendToast = await page.evaluate(() =>
    (document.querySelector('.hafs-toast') || {}).textContent || '');
  ok(/same valid time/.test(trendToast), 'the trend move is narrated: ' + trendToast.slice(0, 50));

  await page.keyboard.press('End');
  ok((await fx()).fxx === 24, 'End jumps to the last rendered hour');
  await page.keyboard.press('Home');
  ok((await fx()).fxx === 0, 'Home jumps to the first');

  await page.keyboard.press(' ');
  ok((await fx()).playing === true, 'Space starts playback');
  await page.keyboard.press('Escape');
  ok((await fx()).playing === false, 'Esc pauses');

  await page.keyboard.press('?');
  ok(await page.evaluate(() =>
       document.querySelector('.hafs-kbd-sheet').style.display === 'block'),
     '? opens the shortcut sheet');
  // WCAG 2.1.4: disable character keys via the sheet toggle.
  await page.evaluate(() => {
    const cb = document.querySelector('.hafs-kbd-sheet input');
    cb.checked = false; cb.dispatchEvent(new Event('change'));
  });
  await page.evaluate(() =>
    document.querySelector('.hafs-kbd-close').click());
  await page.keyboard.press(' ');
  ok((await fx()).playing === false,
     'with the toggle OFF, Space no longer plays (WCAG 2.1.4)');
  await page.keyboard.press('ArrowRight');
  ok((await fx()).fxx === 3, 'arrow keys still work with character keys off');
  ok(await page.evaluate(() =>
       !!document.querySelector('.hafs-kbd-btn')),
     'a visible Shortcuts button exists, so the sheet is reachable without ?');

  // ---- 6. shear-relative view (spec #4) -----------------------------------
  console.log('# shear-relative view');
  // 12w has no shear block in the fixture: the toggle must not exist there.
  await page.goto(base + '?run=2026073100&storm=12w&model=hafsa&domain=storm&product=mslp_wind&fxx=0',
                  { waitUntil: 'load', timeout: 30000 });
  await page.waitForFunction(() => {
    const v = window.__hafsViewer; return v && v.storm && v.fxxList.length;
  }, { timeout: 15000 }).catch(() => {});
  ok(await page.evaluate(() => {
    const b = document.querySelector('.hafs-shear-btn');
    return !b || b.style.display === 'none';
  }), 'no shear button for a storm without shear data');
  // 07e + hafsa has it.
  await page.goto(base + '?run=2026073100&storm=07e&model=hafsa&domain=storm&product=mslp_wind&fxx=0',
                  { waitUntil: 'load', timeout: 30000 });
  await page.waitForFunction(() => {
    const v = window.__hafsViewer; return v && v.storm && v.fxxList.length;
  }, { timeout: 15000 }).catch(() => {});
  ok(await page.evaluate(() => {
    const b = document.querySelector('.hafs-shear-btn');
    return !!b && b.style.display !== 'none';
  }), 'shear button appears for 07e/hafsa (manifest carries shear.hours)');
  const hs = await page.evaluate(() => history.length);
  await page.evaluate(() => document.querySelector('.hafs-shear-btn').click());
  await page.waitForTimeout(400);      // replaceState debounce
  const shOn = await page.evaluate(() => ({
    view: window.__hafsViewer.shearView,
    hist: history.length,
    url: location.search,
    pressed: document.querySelector('.hafs-shear-btn').getAttribute('aria-pressed'),
    svg: !!document.querySelector('.hafs-shear-ov svg'),
    labs: Array.from(document.querySelectorAll('.hafs-shear-ov .sh-lab'))
      .map(t => t.textContent).sort().join(','),
    chip: (document.querySelector('.hafs-shear-chip') || {}).textContent || '',
  }));
  ok(shOn.view === true && shOn.pressed === 'true', 'toggle turns the view on');
  ok(shOn.hist === hs, 'toggling shear adds NO history entry (display state)');
  ok(/shear=1/.test(shOn.url), 'URL carries shear=1 via replaceState');
  ok(shOn.svg, 'overlay SVG drawn (frame has a shear value + geometry)');
  ok(shOn.labs === 'DL,DR,UL,UR', 'quadrant labels are the four rotation-defined tags: ' + shOn.labs);
  ok(/downshear-left in the NH/.test(shOn.chip) && /downshear-right in the SH/.test(shOn.chip),
     'chip states BOTH hemispheres’ convective preference');
  ok(/12\.3 kt/.test(shOn.chip) && /naive 14\.1 kt/.test(shOn.chip),
     'chip shows the removed AND naive numbers');
  // F003 has geometry but NO shear entry (no vortex fix): honest degradation.
  await page.evaluate(() => window.__hafsViewer._show(1));
  await page.waitForTimeout(300);   // img src swap -> overlay redraws on load
  const noFix = await page.evaluate(() => ({
    svg: !!document.querySelector('.hafs-shear-ov svg'),
    chip: (document.querySelector('.hafs-shear-chip') || {}).textContent || '',
  }));
  ok(!noFix.svg && /No shear diagnostic at F003/.test(noFix.chip),
     'hour without a vortex fix: no geometry drawn, chip says why');
  // Boot from a shared link with shear=1.
  await page.goto(base + '?run=2026073100&storm=07e&model=hafsa&domain=storm&product=mslp_wind&fxx=0&shear=1',
                  { waitUntil: 'load', timeout: 30000 });
  await page.waitForFunction(() => {
    const v = window.__hafsViewer; return v && v.storm && v.fxxList.length;
  }, { timeout: 15000 }).catch(() => {});
  ok(await page.evaluate(() => window.__hafsViewer.shearView === true),
     'shear=1 in a shared link boots the view on');
  ok(errs.length === 0, 'no page errors through the shear section: ' + (errs[errs.length - 1] || 'none'));

  await browser.close();
  server.close();
  console.log(failures ? `\n${failures} assertion(s) FAILED` : '\nall assertions passed');
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
