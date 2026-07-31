/*
 * Headless harness for the ensemble PAINTBALL panel (guidance/guidance.js).
 *
 * The panel's whole justification is that member tracks ship as VECTORS, so
 * the member interactions are free client-side operations. That is exactly
 * what is asserted here - not that a picture appears, but that the
 * interactions actually work on the DOM:
 *
 *   - the tab is offered in EVERY basin (the ECMWF BUFR is global);
 *   - one member path per member of the active source, switchable per source;
 *   - click = solo (others dimmed, not removed), click again = un-solo;
 *   - Ctrl-click = hide (path gone from the map, chip stays to re-enable);
 *   - the member-axis sweep advances the solo on its own.
 *
 * Run: node tests/ensemble_viewer_harness.cjs <ep-guid.json> <ep-ens.json>
 *                                             <wp-guid.json> <wp-ens.json>
 */
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const SRC = path.join(__dirname, '..', 'guidance', 'guidance.js');

let failures = 0;
function ok(cond, msg) {
  console.log((cond ? 'ok   - ' : 'FAIL - ') + msg);
  if (!cond) failures++;
}

async function mount(page, gdoc, edoc) {
  await page.route('**/guidance_v2.json*', r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(gdoc) }));
  await page.route('**/ensemble_v2.json*', r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(edoc) }));
  await page.route('**/guidance.json*', r => r.fulfill({ status: 404, body: '' }));
  await page.route('**/ships_v2.json*', r => r.fulfill({ status: 404, body: '' }));
  await page.setContent('<!doctype html><html><body><div id="g"></div></body></html>');
  await page.addScriptTag({ path: SRC });
  await page.evaluate(sid => {
    window.__v = new window.GuidanceViewer(document.getElementById('g'),
      { base: 'https://example.invalid/cyclolab', stormLock: sid });
  }, gdoc.sid);
  await page.waitForFunction(() => {
    const s = document.querySelector('.gv-status');
    return s && s.hidden;
  }, { timeout: 8000 });
  await page.evaluate(() => {
    [...document.querySelectorAll('.gv-tab')]
      .find(b => b.textContent.trim() === 'Ensemble').click();
  });
  await page.waitForFunction(
    () => document.querySelectorAll('.gv-ens-track').length > 0, { timeout: 8000 });
}

const trackCount = p => p.$$eval('.gv-ens-track', ns => ns.length);

(async () => {
  const [epG, epE, wpG, wpE] = process.argv.slice(2).map(f =>
    JSON.parse(fs.readFileSync(f, 'utf8')));
  const browser = await chromium.launch();
  const OUT = process.env.GV_SHOT_DIR || '/tmp';

  // ---------- NHC storm: both sources ----------
  let page = await browser.newPage({ viewport: { width: 900, height: 1000 } });
  const errs = [];
  page.on('pageerror', e => errs.push(String(e.message)));
  await mount(page, epG, epE);

  console.log('# NHC storm (' + epG.sid + ')');
  ok(errs.length === 0, 'no page errors: ' + (errs[0] || 'none'));

  const nEc = epE.sources[0].n_members, nGefs = epE.sources[1].n_members;
  ok(await trackCount(page) === nEc,
     'one vector path per ECMWF member (' + nEc + ')');
  const segs = await page.$$eval('.gv-panel .gv-tabs .gv-tab', ns => ns.map(n => n.textContent.trim()));
  ok(segs.some(s => /ECMWF ENS/.test(s)) && segs.some(s => /GEFS/.test(s)),
     'both sources offered as a segmented control: ' + JSON.stringify(segs));

  // source switch
  await page.evaluate(() => {
    [...document.querySelectorAll('.gv-panel .gv-tabs .gv-tab')]
      .find(b => /GEFS/.test(b.textContent)).click();
  });
  await page.waitForTimeout(150);
  ok(await trackCount(page) === nGefs,
     'switching source redraws ' + nGefs + ' GEFS member paths');
  await page.evaluate(() => {
    [...document.querySelectorAll('.gv-panel .gv-tabs .gv-tab')]
      .find(b => /ECMWF/.test(b.textContent)).click();
  });
  await page.waitForTimeout(150);

  // solo via chip click
  const firstMid = await page.$eval('.gv-ens-chip', n => n.getAttribute('data-mid'));
  await page.click('.gv-ens-chip[data-mid="' + firstMid + '"]');
  await page.waitForTimeout(150);
  ok(await page.evaluate(() => window.__v.ensSolo) === firstMid,
     'clicking a member chip solos it (ensSolo=' + firstMid + ')');
  const dimmed = await page.$$eval('.gv-ens-track', ns =>
    ns.filter(n => parseFloat(n.getAttribute('opacity')) < 0.2).length);
  ok(dimmed === nEc - 1, 'solo DIMS the other ' + (nEc - 1) + ' members rather than removing them');
  ok(await trackCount(page) === nEc, 'all member paths still present under solo');
  await page.screenshot({ path: path.join(OUT, 'ensemble_nhc_solo.png') });

  await page.click('.gv-ens-chip[data-mid="' + firstMid + '"]');
  await page.waitForTimeout(150);
  ok(await page.evaluate(() => window.__v.ensSolo) === null,
     'clicking the soloed member again un-solos');

  // hide via ctrl-click
  await page.click('.gv-ens-chip[data-mid="' + firstMid + '"]', { modifiers: ['Control'] });
  await page.waitForTimeout(150);
  ok(await trackCount(page) === nEc - 1, 'Ctrl-click hides the member from the map');
  ok(await page.$eval('.gv-ens-chip[data-mid="' + firstMid + '"]',
                      n => n.classList.contains('off')),
     'the hidden member keeps a dimmed chip so it can be re-enabled');
  await page.click('.gv-ens-chip[data-mid="' + firstMid + '"]', { modifiers: ['Control'] });
  await page.waitForTimeout(150);
  ok(await trackCount(page) === nEc, 'Ctrl-click again restores it');

  // member-axis sweep
  await page.evaluate(() => {
    [...document.querySelectorAll('button')]
      .find(b => b.getAttribute('data-role') === 'sweep').click();
  });
  await page.waitForTimeout(500);
  const soloA = await page.evaluate(() => window.__v.ensSolo);
  await page.waitForTimeout(700);
  const soloB = await page.evaluate(() => window.__v.ensSolo);
  ok(soloA !== null && soloB !== null && soloA !== soloB,
     'the sweep advances the solo on its own (' + soloA + ' -> ' + soloB + ')');
  await page.evaluate(() => {
    [...document.querySelectorAll('button')]
      .find(b => b.getAttribute('data-role') === 'sweep').click();
  });
  await page.waitForTimeout(400);
  const soloC = await page.evaluate(() => window.__v.ensSolo);
  await page.waitForTimeout(400);
  ok(await page.evaluate(() => window.__v.ensSolo) === soloC, 'stop sweep stops it');
  const legend = await page.$eval('.gv-legend', n => n.textContent);
  ok(/Best track/.test(legend), 'verifying best track is drawn and named');
  await page.screenshot({ path: path.join(OUT, 'ensemble_nhc.png') });
  await page.close();

  // ---------- JTWC storm: ECMWF-only, full quality ----------
  page = await browser.newPage({ viewport: { width: 900, height: 1000 } });
  const errs2 = [];
  page.on('pageerror', e => errs2.push(String(e.message)));
  await mount(page, wpG, wpE);

  console.log('# JTWC storm (' + wpG.sid + ')');
  ok(errs2.length === 0, 'no page errors: ' + (errs2[0] || 'none'));
  ok(wpE.sources.length === 1 && wpE.sources[0].model === 'ecmwf_ens',
     'document carries the ECMWF source despite the basin having no NHC decks');
  ok(await trackCount(page) === wpE.sources[0].n_members,
     'full ECMWF member count drawn in a JTWC basin (' +
     wpE.sources[0].n_members + ')');
  ok(wpE.sources[0].matched_by === 'name',
     'the ECMWF record was matched by NAME, not by the untrustworthy id');
  const sub = await page.$eval('.gv-panel .gv-head .gv-sub', n => n.textContent);
  ok(/matched by name/.test(sub), 'the panel says how the match was made: ' + sub);
  await page.screenshot({ path: path.join(OUT, 'ensemble_jtwc.png') });
  await page.close();

  await browser.close();
  console.log(failures ? `\n${failures} assertion(s) FAILED` : '\nall assertions passed');
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
