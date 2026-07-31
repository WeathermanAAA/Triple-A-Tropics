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
    cycles: [cyc('2026073100', [storm('07e', 'ep'), storm('12w', 'wp')]),
             cyc('2026073018', [storm('07e', 'ep')])],
  };
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
    status: 200, contentType: 'application/json', body: JSON.stringify(manifest()) }));
  // Frames + everything else off-origin: tiny 1px png / 404s are fine.
  await page.route('**/cdn.triple-a-tropics.com/**', r => {
    if (/manifest\.json/.test(r.request().url())) return r.fallback();
    r.fulfill({ status: 404, body: '' });
  });
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

  await browser.close();
  server.close();
  console.log(failures ? `\n${failures} assertion(s) FAILED` : '\nall assertions passed');
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
