/*
 * Headless harness for guidance/guidance.js (the model-guidance component).
 *
 * Renders the viewer against REAL built documents and asserts the honesty
 * properties that are the whole reason the component exists - most of which
 * are properties of what it must REFUSE to draw:
 *
 *   - a JTWC-basin storm gets NO consensus tab, NO consensus envelope and NO
 *     skill baseline, because those decks carry none;
 *   - an ensemble MEAN is never presented as a consensus;
 *   - withheld consensus members render as WITHHELD, distinct from absent;
 *   - late aids are badged, never silently blended with early ones.
 *
 * Run: node tests/guidance_viewer_harness.cjs <ep-doc.json> <wp-doc.json>
 * Exits non-zero on the first failed assertion. Uses playwright from the repo
 * root node_modules (same as the other viewer harnesses).
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

async function mount(page, doc) {
  // Serve the document to the component's own fetch, so the real load path
  // (v2 first, legacy fallback) is exercised rather than bypassed.
  await page.route('**/guidance_v2.json*', route =>
    route.fulfill({ status: 200, contentType: 'application/json',
                    body: JSON.stringify(doc) }));
  await page.route('**/guidance.json*', route => route.fulfill({ status: 404, body: '' }));

  await page.setContent('<!doctype html><html><body><div id="g"></div></body></html>');
  await page.addScriptTag({ path: SRC });
  await page.evaluate((sid) => {
    window.__v = new window.GuidanceViewer(document.getElementById('g'),
      { base: 'https://example.invalid/cyclolab', stormLock: sid });
  }, doc.sid);
  await page.waitForFunction(() => {
    const s = document.querySelector('.gv-status');
    return s && s.hidden;
  }, { timeout: 8000 });
}

const tabs = page => page.$$eval('.gv-tab', ns => ns.map(n => n.textContent.trim()));
const show = async (page, name) => {
  await page.evaluate((n) => {
    [...document.querySelectorAll('.gv-tab')]
      .find(b => b.textContent.trim() === n).click();
  }, name);
  await page.waitForTimeout(120);
};

(async () => {
  const [epPath, wpPath] = process.argv.slice(2);
  const ep = JSON.parse(fs.readFileSync(epPath, 'utf8'));
  const wp = JSON.parse(fs.readFileSync(wpPath, 'utf8'));

  const browser = await chromium.launch();
  const OUT = process.env.GV_SHOT_DIR || '/tmp';

  // ---------- NHC basin: the full suite ----------
  let page = await browser.newPage({ viewport: { width: 900, height: 1000 } });
  const errs = [];
  page.on('pageerror', e => errs.push(String(e.message)));
  await mount(page, ep);

  console.log('# NHC basin (' + ep.sid + ')');
  ok(errs.length === 0, 'no page errors: ' + (errs[0] || 'none'));
  const epTabs = await tabs(page);
  ok(epTabs.includes('Consensus'), 'consensus tab offered (basin supports it)');
  ok(epTabs.includes('Tracks') && epTabs.includes('Intensity'), 'tracks + intensity tabs offered');

  await show(page, 'Tracks');
  await page.screenshot({ path: path.join(OUT, 'guidance_nhc_tracks.png') });
  const legend = await page.$eval('.gv-legend', n => n.textContent);
  ok(/Official forecast \(OFCL\)/.test(legend), 'official forecast is distinguished in the legend');
  ok(/Best track/.test(legend), 'best track is distinguished in the legend');
  ok(/dashed = LATE/.test(legend), 'late aids are called out as dashed');
  const nPaths = await page.$$eval('.gv-svg path', n => n.length);
  ok(nPaths > 3, 'spaghetti drew multiple track paths (' + nPaths + ')');

  await show(page, 'Intensity');
  await page.screenshot({ path: path.join(OUT, 'guidance_nhc_intensity.png') });
  const iLeg = await page.$eval('.gv-legend', n => n.textContent);
  ok(/no-skill baseline/.test(iLeg), 'OCD5 skill baseline is present and named as no-skill');
  ok(new RegExp(ep.skill_baseline || 'OCD5').test(iLeg), 'baseline aid id shown in the legend');

  await show(page, 'Consensus');
  await page.screenshot({ path: path.join(OUT, 'guidance_nhc_consensus.png') });
  const states = await page.$$eval('.gv-mem span', ns =>
    ns.map(n => n.className));
  ok(states.some(c => /withheld/.test(c)), 'a WITHHELD member state is rendered');
  ok(states.some(c => /present/.test(c)), 'a PRESENT member state is rendered');
  const withheldDistinct = await page.$$eval('.gv-mem span.withheld', ns => ns.length);
  const absentCount = await page.$$eval('.gv-mem span.absent', ns => ns.length);
  ok(withheldDistinct > 0, 'withheld is a distinct class from absent (' +
     withheldDistinct + ' withheld vs ' + absentCount + ' absent)');
  const warn = await page.$eval('.gv-note.gv-warn', n => n.textContent);
  ok(/not independently reproducible/i.test(warn),
     'page states the consensus is not independently reproducible');

  await show(page, 'Aids');
  await page.screenshot({ path: path.join(OUT, 'guidance_nhc_aids.png') });
  const badges = await page.$$eval('.gv-badge', ns => ns.map(n => n.textContent));
  ok(badges.includes('EARLY'), 'EARLY badges rendered');
  ok(badges.includes('LATE') || ep.late_aids.length === 0,
     'LATE badges rendered when late aids exist');
  const kinds = await page.$eval('.gv-aids', n => n.textContent);
  ok(/Skill baseline/.test(kinds), 'skill baseline is its own kind, not a forecast');
  ok(/Ensemble mean/.test(kinds) || ep.ensemble_mean_aids.length === 0,
     'ensemble mean is its own kind, not consensus');
  await page.close();

  // ---------- JTWC basin: must degrade honestly ----------
  page = await browser.newPage({ viewport: { width: 900, height: 1000 } });
  const errs2 = [];
  page.on('pageerror', e => errs2.push(String(e.message)));
  await mount(page, wp);

  console.log('# JTWC basin (' + wp.sid + ')');
  ok(errs2.length === 0, 'no page errors: ' + (errs2[0] || 'none'));
  const wpTabs = await tabs(page);
  ok(!wpTabs.includes('Consensus'),
     'NO consensus tab in a JTWC basin (those decks carry no consensus aid)');
  ok(wp.consensus_aids.length === 0, 'document declares zero consensus aids');
  ok(wp.consensus_membership.length === 0, 'document declares no consensus membership');
  ok(wp.skill_baseline === null, 'document declares no skill baseline');
  ok(wp.official === null, 'document declares no official aid');
  ok(wp.ensemble_mean_aids.length > 0,
     'ensemble MEANS are present and classified as means, not consensus');

  await show(page, 'Tracks');
  await page.screenshot({ path: path.join(OUT, 'guidance_jtwc_tracks.png') });
  const head = await page.$eval('.gv-head', n => n.textContent);
  ok(/raw ensembles only/.test(head), 'header labels the basin as raw ensembles only');
  const note = await page.$eval('.gv-note', n => n.textContent);
  ok(/never carried official, consensus or statistical aids/.test(note),
     'page explains WHY there is no consensus here');

  await show(page, 'Intensity');
  await page.screenshot({ path: path.join(OUT, 'guidance_jtwc_intensity.png') });
  const iNote = await page.$eval('.gv-note.gv-warn', n => n.textContent);
  ok(/No skill baseline is available in this basin/.test(iNote),
     'intensity panel says the basin has no baseline, rather than omitting it silently');

  const bodyText = await page.$eval('.gv-root', n => n.textContent);
  ok(!/NOT REPRODUCIBLE/.test(bodyText), 'no consensus reproducibility claim in a JTWC basin');
  await page.close();

  await browser.close();
  console.log(failures ? `\n${failures} assertion(s) FAILED` : '\nall assertions passed');
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
