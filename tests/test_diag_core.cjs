// Unit tests for satellite/explorer/diag_core.js — the radial-profile
// (Hovmöller column) + DAV computations. Synthetic-field tests: known
// structures in, verify the physics-facing invariants out.
// Run: node tests/test_diag_core.cjs
"use strict";
const assert = require("assert");
const path = require("path");
const DC = require(path.join(__dirname, "..", "satellite", "explorer", "diag_core.js"));

function makeField(nr, nc, latTop, lonLeft, dDeg, fill) {
  const latArr = new Float64Array(nr), lonArr = new Float64Array(nc);
  for (let i = 0; i < nr; i++) latArr[i] = latTop - i * dDeg;   // descending
  for (let j = 0; j < nc; j++) lonArr[j] = lonLeft + j * dDeg;
  const bt = new Float64Array(nr * nc);
  for (let i = 0; i < nr; i++)
    for (let j = 0; j < nc; j++)
      bt[i * nc + j] = fill(latArr[i], lonArr[j]);
  return { latArr, lonArr, bt, nr, nc, resKm: dDeg * 111 };
}

const C = { lat: 20.0, lon: -60.0 };
const KM = 111.32;
function rKm(lat, lon) {
  const dx = (lon - C.lon) * KM * Math.cos((20 * Math.PI) / 180);
  const dy = (lat - C.lat) * KM;
  return Math.sqrt(dx * dx + dy * dy);
}

// A deterministic pseudo-random (no Math.random: reproducible)
let seed = 42;
function rnd() { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; }

// ---------------------------------------------------------------- radial profile
{
  // cold ring (eyewall) at 100-130 km over a warm background + warm eye
  const field = makeField(251, 251, 25.0, -65.4, 0.04, (lat, lon) => {
    const r = rKm(lat, lon);
    if (r < 30) return 290.0;                 // eye
    if (r >= 100 && r < 130) return 200.0;    // eyewall ring
    return 285.0 - 10 * Math.exp(-((r - 115) ** 2) / (2 * 60 * 60));
  });
  const p = DC.radialProfile(field, C, { maxKm: 400, ringKm: 10 });
  assert.strictEqual(p.radii.length, 40);
  // ring means: the minimum azimuthal-mean BT must sit inside 100-130 km
  let minV = Infinity, minR = -1;
  for (let r = 0; r < p.radii.length; r++) {
    if (p.meanC[r] != null && p.meanC[r] < minV) { minV = p.meanC[r]; minR = p.radii[r]; }
  }
  assert.ok(minR >= 100 && minR <= 130, `eyewall ring recovered, got ${minR} km`);
  assert.ok(Math.abs(minV - (200 - 273.15)) < 3, `ring BT ≈ -73C, got ${minV}`);
  // eye is warm: innermost ring mean far warmer than the eyewall min
  assert.ok(p.meanC[0] > minV + 50, "warm eye vs cold eyewall");
  // full coverage on a fully-valid grid (rings inside the domain)
  for (let r = 0; r < 30; r++) assert.ok(p.coverage[r] > 0.99, `coverage ring ${r}`);
}

{
  // coverage honesty: NaN out the eastern half -> ring coverage ≈ 0.5
  const field = makeField(251, 251, 25.0, -65.4, 0.04, (lat, lon) =>
    lon > -60.0 ? NaN : 280.0);
  const p = DC.radialProfile(field, C, { maxKm: 300, ringKm: 20 });
  for (let r = 2; r < 12; r++) {
    assert.ok(Math.abs(p.coverage[r] - 0.5) < 0.06,
      `half-masked ring coverage ~0.5, got ${p.coverage[r]} at ring ${r}`);
  }
}

// ---------------------------------------------------------------- DAV
{
  // perfectly axisymmetric vortex: BT = f(r) increasing outward
  // (cold core) -> every gradient is radial -> DAV ≈ 0
  const axi = makeField(301, 301, 26.0, -66.4, 0.04, (lat, lon) => {
    const r = rKm(lat, lon);
    return 200 + 90 * (1 - Math.exp(-r / 200));   // smooth monotone in r
  });
  const d = DC.dav(axi, C, { radiusKm: 250 });
  assert.ok(d.varDeg2 != null, "axisym DAV computed");
  assert.ok(d.varDeg2 < 150, `axisymmetric vortex DAV ~0, got ${d.varDeg2}`);
  assert.ok(d.nPix > 500, `enough pixels, got ${d.nPix}`);

  // random noise field: gradient directions ~uniform -> variance near the
  // published uniform-random value 180^2/12 = 2700 deg^2
  const noise = makeField(301, 301, 26.0, -66.4, 0.04, () => 240 + 40 * rnd());
  const dn = DC.dav(noise, C, { radiusKm: 250 });
  assert.ok(dn.varDeg2 > 2000 && dn.varDeg2 < 3200,
    `noise DAV near 2700 deg², got ${dn.varDeg2}`);

  // ordering invariant: organized << disorganized (the technique's core claim)
  assert.ok(d.varDeg2 < dn.varDeg2 / 5, "axisym << noise");
}

{
  // banded (linear) field: gradients all parallel; deviation angle spans the
  // fold range around the disk -> mid-range variance, far above axisym
  const band = makeField(301, 301, 26.0, -66.4, 0.04, (lat) => 200 + (26 - lat) * 12);
  const db = DC.dav(band, C, { radiusKm: 250 });
  assert.ok(db.varDeg2 > 800, `parallel-band DAV well above axisym, got ${db.varDeg2}`);
}

{
  // insufficient data: an almost-empty field returns null honestly
  const empty = makeField(60, 60, 22, -62, 0.04, () => NaN);
  const de = DC.dav(empty, C, { radiusKm: 250 });
  assert.strictEqual(de.varDeg2, null);
}

console.log("diag_core: all tests passed");
