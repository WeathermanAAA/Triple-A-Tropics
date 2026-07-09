// Unit tests for satellite/explorer/objfix.js — the ARCHER + ADT ports.
// Synthetic-field tests: known centers/scenes in, verify the ports recover
// them. Run: node tests/test_objfix.cjs
"use strict";
const assert = require("assert");
const path = require("path");
const OF = require(path.join(__dirname, "..", "satellite", "explorer", "objfix.js"));

function makeField(nr, nc, latTop, lonLeft, dDeg, fill) {
  const latArr = new Float64Array(nr), lonArr = new Float64Array(nc);
  for (let i = 0; i < nr; i++) latArr[i] = latTop - i * dDeg;
  for (let j = 0; j < nc; j++) lonArr[j] = lonLeft + j * dDeg;
  const bt = new Float64Array(nr * nc);
  for (let i = 0; i < nr; i++)
    for (let j = 0; j < nc; j++)
      bt[i * nc + j] = fill(latArr[i], lonArr[j]);
  return { latArr, lonArr, bt, nr, nc, resKm: dDeg * 111 };
}

// ---------------------------------------------------------------- utilities
{
  // distance_angle: due north => angle 0; distance ~111 km/deg
  const [d, a] = OF._internal.distanceAngle(21.0, 130.0, 20.0, 130.0);
  assert.ok(Math.abs(d - 111.2) < 1.5, `1 deg N ~111 km, got ${d}`);
  assert.ok(a < 1 || a > 359, `due north angle ~0, got ${a}`);
  // distance_angle2 moves OPPOSITE the bearing — the ADT source always calls
  // it with angle+180 (FinalArcAngleThetaPlus180), so +180 recovers north.
  const [la2, lo2] = OF._internal.distanceAngle2(20.0, 130.0, 111.2, 180.0);
  assert.ok(Math.abs(la2 - 21.0) < 0.05, `da2 north lat, got ${la2}`);
  assert.ok(Math.abs(lo2 - 130.0) < 0.05, `da2 north lon, got ${lo2}`);
  // round-trip at an arbitrary bearing (mirrors the source's +180 usage)
  const [dd, aa] = OF._internal.distanceAngle(21.3, 128.6, 20.0, 130.0);
  const [rl, rn] = OF._internal.distanceAngle2(20.0, 130.0, dd, aa + 180.0);
  assert.ok(Math.abs(rl - 21.3) < 0.1 && Math.abs(rn - 128.6) < 0.1,
    `distance_angle round-trip, got ${rl},${rn}`);
}

// numpy.gradient semantics
{
  const a = new Float64Array([0, 1, 2, 10, 11, 12, 20, 21, 22]);
  const g = OF._internal.npGradient2D(a, 3, 3, 1, 1);
  assert.strictEqual(g.row[4], 10, "central row gradient");
  assert.strictEqual(g.col[4], 1, "central col gradient");
  assert.strictEqual(g.row[1], 10, "one-sided edge row gradient");
}

// BD categories: -55C is Light Gray (cat 4); +10C is cat 0; fractional interp
{
  assert.strictEqual(OF.bdCategory(-55).cat, 4, "-55C -> LG");
  assert.strictEqual(OF.bdCategory(10).cat, 0, "+10C -> warmest cat");
  const b = OF.bdCategory(-36); // halfway between -30 and -42 -> cat 2 + 0.5
  assert.strictEqual(b.cat, 2);
  assert.ok(Math.abs(b.flt - 2.5) < 0.01, `flt interp, got ${b.flt}`);
}

// Dvorak table: the 16 spec-checked half-T# points
{
  const want = { 1.0: 25, 1.5: 25, 2.0: 30, 2.5: 35, 3.0: 45, 3.5: 55, 4.0: 65,
    4.5: 77, 5.0: 90, 5.5: 102, 6.0: 115, 6.5: 127, 7.0: 140, 7.5: 155,
    8.0: 170, 8.5: 185 };
  for (const [ci, kt] of Object.entries(want)) {
    assert.strictEqual(OF.ciToVmax(+ci), kt, `CI ${ci} -> ${kt} kt`);
  }
  // non-uniform 0.1 steps (from Functions.java): 4.1 -> 67.4
  assert.strictEqual(OF.ciToVmax(4.1), 67.4, "0.1-step table value");
  // MSLP: Atlantic vs Pacific columns (index-aligned with the 77 kt wind row)
  assert.strictEqual(OF.ciToMslp(4.5, 0), 979.0, "AL MSLP at 4.5");
  assert.strictEqual(OF.ciToMslp(4.5, 1), 966.0, "Pac MSLP at 4.5");
  // inverse for seeding
  assert.strictEqual(OF.vmaxToTno(90), 5.0, "90 kt -> T5.0");
}

// FFT harmonic counter: a single-peak (smooth) histogram has few harmonics,
// a noisy multi-modal one has more
{
  const smooth = new Float64Array(64), noisy = new Float64Array(64);
  for (let i = 0; i < 64; i++) {
    smooth[i] = Math.exp(-((i - 20) ** 2) / 30) * 100;
    noisy[i] = 50 + 45 * Math.sin(i * 2.1) + 30 * Math.sin(i * 0.9 + 1);
  }
  const hs = OF.calculateFFT(smooth), hn = OF.calculateFFT(noisy);
  assert.ok(hs >= 0 && hn >= 0, "FFT returns counts");
  assert.ok(hn > hs, `noisy has more harmonics (${hn} > ${hs})`);
}

// ------------------------------------------------------------------- ARCHER
// synthetic cyclone: cold cloud shield with a 5-degree log-spiral banding
// pattern and a warm eye, centered OFF the first guess; ARCHER must pull the
// fix to the true center.
function vortexField(centerLat, centerLon) {
  const alpha = 5 * Math.PI / 180;
  return makeField(240, 240, centerLat + 3.0, centerLon - 3.0, 0.025, (la, lo) => {
    const x = (lo - centerLon) * Math.cos(centerLat * Math.PI / 180);
    const y = la - centerLat;
    const r = Math.sqrt(x * x + y * y);
    if (r < 0.09) return 273 + 10 - r * 30;                // warm eye
    // spiral-banded cold shield: BT rises with angle mismatch to a log spiral
    const theta = Math.atan2(y, x);
    const spiralPhase = theta - Math.log(Math.max(r, 1e-6) / 0.08) / Math.tan(alpha);
    const band = Math.cos(spiralPhase);                     // banding
    const shield = 210 + r * 28;                            // colder near core
    return shield - 12 * band * Math.exp(-r / 1.4);
  });
}
{
  const truth = { lat: 20.0, lon: 130.0 };
  const field = vortexField(truth.lat, truth.lon);
  const guess = { lat: 20.45, lon: 130.5, vmax: 90 };       // ~0.65 deg off
  const out = OF.archerFix(field, guess, { channelType: "IR", searchRadiusDeg: 1.5 });
  assert.ok(out.center, "quality gates pass on a clean vortex");
  const errDeg = Math.hypot(out.center.lat - truth.lat,
    (out.center.lon - truth.lon) * Math.cos(truth.lat * Math.PI / 180));
  assert.ok(errDeg < 0.15, `center within 0.15 deg of truth (got ${errDeg.toFixed(3)})`);
  assert.ok(out.confidenceScore > 0, "positive confidence on a clean scene");
  assert.ok(out.alpha >= 0.5, "alpha floor respected");
  assert.ok(out.radius95percCertDeg > out.radius50percCertDeg, "r95 > r50");
  assert.ok(out.eyeProb === null || (out.eyeProb >= 0 && out.eyeProb <= 100), "eye prob in range");
}

// void scene: mostly-NaN input must NOT produce a confident fix
{
  const field = makeField(240, 240, 23, 127, 0.025, () => NaN);
  const out = OF.archerFix(field, { lat: 20, lon: 130, vmax: 50 },
    { channelType: "IR", searchRadiusDeg: 1.0 });
  assert.ok(!out.center, "no fix from an empty scene");
  assert.ok(out.weakCenter !== undefined, "demoted to weak-center status");
}

// confidence->alpha: exact IR fit values from Conversions.py
{
  // vmax 40 (< 60) -> pure lo fit: 9.89c - 2.07
  const a = OF.confidenceToAlpha(1.0, "IR", 0, 40);
  assert.ok(Math.abs(a - (9.89 - 2.07)) < 1e-9, `IR lo alpha, got ${a}`);
  // vmax 200 (> 85) -> pure hi fit: 9.26c + 1.95
  const b = OF.confidenceToAlpha(1.0, "IR", 0, 200);
  assert.ok(Math.abs(b - (9.26 + 1.95)) < 1e-9, `IR hi alpha, got ${b}`);
  // floor
  assert.ok(OF.confidenceToAlpha(-5, "IR", 0, 40) === 0.5, "alpha floor 0.5");
}

// ---------------------------------------------------------------------- ADT
// synthetic EYE scene: warm eye (+5C) inside a cold (-70C) symmetric CDO
function eyeSceneField(centerLat, centerLon) {
  return makeField(200, 200, centerLat + 2.0, centerLon - 2.0, 0.02, (la, lo) => {
    const dKm = 111 * Math.hypot(la - centerLat,
      (lo - centerLon) * Math.cos(centerLat * Math.PI / 180));
    if (dKm < 18) return 273.16 + 5;         // warm eye
    if (dKm < 150) return 273.16 - 70;       // cold uniform shield
    return 273.16 - 5 + dKm * 0.05;          // warm outside
  });
}
{
  const field = eyeSceneField(20, 130);
  const stats = OF.calcEyeCloudTemps(field, 20, 130);
  assert.ok(Math.abs(stats.eyet - 5) < 1.0, `eye temp ~+5C, got ${stats.eyet}`);
  assert.ok(Math.abs(stats.cwcloudt - (-70)) < 2.0, `cw cloud ~-70C, got ${stats.cwcloudt}`);
  assert.ok(stats.cloudsymave < 5, `symmetric scene, sym=${stats.cloudsymave}`);

  const rec = OF.adtEstimate(field, { lat: 20, lon: 130 }, Date.UTC(2026, 6, 9), [],
    { domainID: 1, basinID: 1, initRawT: 4.5, initStrengthTF: false });
  assert.ok(rec.eyescene <= 2, `eye scene detected (got eyescene=${rec.eyescene})`);
  assert.ok(rec.Traw >= 4.0, `strong T# for a -70C shield + warm eye (got ${rec.Traw})`);
  assert.ok(rec.vmax >= 65, `hurricane-strength Vmax (got ${rec.vmax})`);
  assert.ok(rec.mslp != null, "Pacific MSLP present");
  const skill = OF.sceneSkill(rec);
  assert.ok(skill.note.indexOf("r≈0.70") >= 0, "eye scene skill tier surfaced");
}

// synthetic SHEAR scene: all cold cloud displaced well east of the center
{
  const field = makeField(200, 200, 22, 128, 0.02, (la, lo) => {
    const east = (lo - 131.2) * 111 * Math.cos(20 * Math.PI / 180);
    if (east > 0 && Math.abs(la - 20) < 1.5) return 273.16 - 60;  // cold blob far east
    return 273.16 + 10;                                            // warm elsewhere
  });
  const rec = OF.adtEstimate(field, { lat: 20, lon: 130 }, Date.UTC(2026, 6, 9), [],
    { domainID: 1, basinID: 1, initRawT: 2.0, initStrengthTF: false });
  assert.strictEqual(rec.cloudscene, 4, `shear scene (got cloudscene=${rec.cloudscene})`);
  assert.ok(rec.Traw <= 3.5, `weak T# for a sheared system (got ${rec.Traw})`);
}

// Rule 8: a jump of Raw T# vs 6h-ago Final T# is clamped (eye row: 1.01/6h)
{
  const t0 = Date.UTC(2026, 6, 9) / 86400000;
  const mkRec = (hoursAgo, tf) => ({
    timeDays: t0 - hoursAgo / 24, Traw: tf, Tfinal: tf, CI: tf,
    land: 0, rule9: 0, rapiddiss: 0, eyescene: 0, cloudscene: 0
  });
  const history = [mkRec(6, 4.0), mkRec(3, 4.0), mkRec(1.5, 4.0)];
  const rec = {
    timeDays: t0, latitude: 20, longitude: 130,
    cloudt: -70, eyet: 5, cwcloudt: -70, cloudsymave: 0, eyecdosize: 30,
    eyescene: 0, cloudscene: 0, ringcb: 0, ringcbval: 0, land: 0
  };
  const raw = OF.adtTnoRaw(rec, history, { domainID: 0, landFlag: false });
  // unconstrained estimate would be far above 4.0 + 1.01
  assert.ok(raw <= 4.0 + 1.02, `Rule 8 clamps the 6-h jump (got ${raw})`);
  assert.ok(rec.rule8 % 10 !== 0, "rule 8 flag set");
}

// Rule 9: CI holds the 6-h max Final T# while weakening, within +1.0
{
  const t0 = Date.UTC(2026, 6, 9) / 86400000;
  const mk = (hoursAgo, tf, ci) => ({
    timeDays: t0 - hoursAgo / 24, Traw: tf, Tfinal: tf, CI: ci ?? tf,
    land: 0, rule9r: 0, rapiddiss: 0, eyescene: 0, cloudscene: 0
  });
  const history = [mk(5, 5.0), mk(3, 4.8), mk(1, 4.6)];
  const rec = { timeDays: t0, Traw: 4.2, Tfinal: 4.2, land: 0 };
  const out = OF.adtCIno(rec, history, { domainID: 0, basinID: 0, landFlag: false });
  assert.ok(Math.abs(out.CI - 5.0) < 1e-9, `CI holds 6-h max (got ${out.CI})`);
  assert.strictEqual(out.rule9, 1, "rule 9 flagged while holding");
  // never more than +1.0 above current
  assert.ok(out.CI <= rec.Tfinal + 1.0 + 1e-9, "rule 9 additive cap");
}

// 3-h Final T# = straight average of Raw over trailing 3 h (+current)
{
  const t0 = Date.UTC(2026, 6, 9) / 86400000;
  const mk = (hoursAgo, traw) => ({
    timeDays: t0 - hoursAgo / 24, Traw: traw, Tfinal: traw, CI: traw, land: 0
  });
  const history = [mk(5, 9.0) /* outside window */, mk(2.5, 4.0), mk(1, 4.4)];
  const rec = { timeDays: t0, Traw: 4.8 };
  const tf = OF.adtTnoFinal(rec, history, 1);
  assert.ok(Math.abs(tf - 4.4) < 0.011, `3-h straight mean of {4.0,4.4,4.8} (got ${tf})`);
}

// log-spiral: a solid cold disk wraps fully; empty scene wraps not at all
{
  const disk = makeField(150, 150, 21.5, 128.5, 0.02, (la, lo) => {
    const dKm = 111 * Math.hypot(la - 20, (lo - 130) * Math.cos(20 * Math.PI / 180));
    return dKm < 160 ? 273.16 - 70 : 273.16 + 10;
  });
  const full = OF.logSpiral(disk, 20, 130, 273.16 - 54, 1);
  assert.ok(full.arcs >= 25, `solid disk wraps the whole spiral (got ${full.arcs})`);
  const warm = makeField(150, 150, 21.5, 128.5, 0.02, () => 273.16 + 10);
  const none = OF.logSpiral(warm, 20, 130, 273.16 - 54, 1);
  assert.ok(none.arcs <= 0, `warm scene has no spiral arcs (got ${none.arcs})`);
}

console.log("objfix: all tests passed");
