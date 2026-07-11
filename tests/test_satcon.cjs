// Unit tests for satellite/explorer/satcon.js — the SATCON-method consensus
// core (Velden & Herndon 2020). Pure-math tests: the published 3-member
// combination equation verified against a hand-expanded evaluation, the
// 2-member reduction, the operational age decay, member construction from
// real record shapes, membership rules, and the uncertainty floors.
// Run: node tests/test_satcon.cjs
"use strict";
const assert = require("assert");
const path = require("path");
const SatCon = require(path.join(__dirname, "..", "satellite", "explorer", "satcon.js"));
const C = SatCon.core;

const H = 3600e3;

// ---------------------------------------------------------------- combine()
{
  // V&H 2020 §2c 3-member equation, hand-expanded:
  //   SATCON = [W1W2(W1+W2)E3 + W1W3(W1+W3)E2 + W2W3(W3+W2)E1] / [ ... ]
  const W1 = 11, W2 = 9, W3 = 14;         // member RMSEs (weights)
  const E1 = 100, E2 = 90, E3 = 110;
  const c3 = W1 * W2 * (W1 + W2);          // coeff of E3
  const c2 = W1 * W3 * (W1 + W3);          // coeff of E2
  const c1 = W2 * W3 * (W3 + W2);          // coeff of E1
  const expected = (c3 * E3 + c2 * E2 + c1 * E1) / (c1 + c2 + c3);
  const got = C.combine([E1, E2, E3], [W1, W2, W3], [1, 1, 1]);
  assert.ok(Math.abs(got.value - expected) < 1e-9,
    `3-member combination matches V&H printed form (${got.value} vs ${expected})`);
  // the best member (lowest RMSE = W2) must carry the largest coefficient
  assert.ok(got.coeffs[1] > got.coeffs[0] && got.coeffs[1] > got.coeffs[2],
    "lowest-RMSE member gets the largest blend coefficient");
}

{
  // 2-member reduction: coeff(Ei) = W_other^2 -> inverse-MSE weights
  const got = C.combine([100, 80], [10, 20], [1, 1]);
  const expected = (20 * 20 * 100 + 10 * 10 * 80) / (400 + 100);
  assert.ok(Math.abs(got.value - expected) < 1e-9, "2-member inverse-MSE reduction");
  // equal weights -> plain mean
  const eq = C.combine([100, 80], [12, 12], [1, 1]);
  assert.ok(Math.abs(eq.value - 90) < 1e-9, "equal RMSEs -> arithmetic mean");
}

{
  // age factor scales a member's coefficient
  const full = C.combine([100, 80], [10, 10], [1, 1]);
  const decayed = C.combine([100, 80], [10, 10], [1, 0.1]);
  assert.ok(Math.abs(full.value - 90) < 1e-9);
  assert.ok(decayed.value > 97, `decayed member barely pulls (${decayed.value})`);
  // zero factors on all members -> null
  assert.strictEqual(C.combine([100], [10], [0]), null);
}

// ---------------------------------------------------------------- ageFactor()
{
  const t = Date.parse("2026-07-11T12:00:00Z");
  assert.strictEqual(C.ageFactor(t - 1 * H, t), 1.0, "full weight <= 3 h");
  assert.strictEqual(C.ageFactor(t - 3 * H, t), 1.0, "full weight at 3 h");
  const at45 = C.ageFactor(t - 4.5 * H, t);
  assert.ok(at45 > 0.05 && at45 < 0.5, `decaying at 4.5 h (${at45})`);
  assert.strictEqual(C.ageFactor(t - 6 * H, t), 0, "zero at 6 h (CIMSS rule)");
  assert.strictEqual(C.ageFactor(t + 1 * H, t), 0, "future overpass never counts");
}

// ---------------------------------------------------------------- adtMember()
{
  const base = {
    frame: { timeMs: 1e12 },
    archer: { center: { lat: 20, lon: -60 }, confidenceScore: 1.2 },
    rec: { vmax: 90, mslp: 970, eyescene: 0, cloudscene: 0, land: 0 }
  };
  const m = C.adtMember(base);
  assert.strictEqual(m.scene, "EYE");
  assert.strictEqual(m.sigmaV, 11.0, "EYE scene RMSE 11 kt (V&H Fig. 4)");
  assert.strictEqual(m.mslp, 970);

  const cdo = C.adtMember({ ...base, rec: { ...base.rec, eyescene: 3, cloudscene: 0 } });
  assert.strictEqual(cdo.scene, "CDO");
  assert.strictEqual(cdo.sigmaV, 13.0);

  const shear = C.adtMember({ ...base, rec: { ...base.rec, eyescene: 3, cloudscene: 4 } });
  assert.strictEqual(shear.scene, "SHEAR");
  assert.strictEqual(shear.sigmaV, 16.0);

  const weak = C.adtMember({ ...base, archer: { center: null, weakCenter: {}, confidenceScore: 0.1 } });
  assert.ok(Math.abs(weak.sigmaV - 11 * 1.25) < 1e-9, "weak-fix degradation x1.25");

  const land = C.adtMember({ ...base, rec: { ...base.rec, land: 1 } });
  assert.strictEqual(land, null, "over land: Dvorak-family member suspended");
}

// ---------------------------------------------------------------- mwMember()
{
  const card = {
    error_overall: { rmse: 12.0 },
    error_by_bin: [{ lo: 0, hi: 64, rmse: 9.0, bias: 3.0 },
                   { lo: 64, hi: 250, rmse: 15.0, bias: -4.0 }],
    error_by_sensor: { GMI: { rmse: 10.0 }, SSMIS: { rmse: 16.0 } },
    mslp_error: { rmse: 7.0, bias: 1.0 }
  };
  const op = {
    id: "GMI_GPM_20260711090000", sensor: "GMI",
    valid_utc: "2026-07-11T09:00:00Z",
    intensity: { usable: true, vmax_kt: 50.0, mslp_hpa: 990.0, confidence: "moderate" }
  };
  const m = C.mwMember(op, card);
  assert.strictEqual(m.vmax, 47.0, "per-bin bias (+3) subtracted");
  assert.strictEqual(m.sigmaV, 10.0, "sigma = max(bin 9, sensor 10)");
  assert.strictEqual(m.mslp, 989.0, "MSLP bias subtracted");
  assert.strictEqual(m.sigmaP, 7.0);

  const ssmis = C.mwMember({ ...op, sensor: "SSMIS",
    intensity: { usable: true, vmax_kt: 100.0 } }, card);
  assert.strictEqual(ssmis.sigmaV, 16.0, "sigma = max(bin 15, sensor 16)");
  assert.strictEqual(ssmis.vmax, 104.0, "high-bin bias (-4) subtracted");

  assert.strictEqual(C.mwMember({ ...op, intensity: { usable: false, reasons: ["x"] } }, card),
    null, "gate-failed overpass is not a member");
  assert.strictEqual(C.mwMember(op, null), null, "no model card -> no member");
}

// ---------------------------------------------------------------- consensusAt()
{
  const t = Date.parse("2026-07-11T12:00:00Z");
  const adt = { kind: "adt", label: "ADT", t: t, vmax: 100, sigmaV: 11,
                mslp: 960, sigmaP: 9.3, scene: "EYE", bias: 0 };
  const mwFresh = { kind: "mw", label: "MW", t: t - 1 * H, vmax: 80,
                    sigmaV: 11, mslp: null, sigmaP: null, bias: 0 };

  // ADT alone -> no consensus (V&H §2c: at least two members)
  assert.strictEqual(C.consensusAt(t, adt, [], "vmax"), null);

  // fresh MW with equal sigma -> halfway
  const c1 = C.consensusAt(t, adt, [mwFresh], "vmax");
  assert.ok(Math.abs(c1.value - 90) < 1e-9, `equal-sigma midpoint (${c1.value})`);
  assert.strictEqual(c1.n, 2);

  // stale MW (5.5 h) -> consensus rides much closer to ADT
  const mwStale = { ...mwFresh, t: t - 5.5 * H };
  const c2 = C.consensusAt(t, adt, [mwStale], "vmax");
  assert.ok(c2.value > 98, `stale overpass barely pulls (${c2.value})`);

  // dead MW (>6 h) -> membership fails again
  const mwDead = { ...mwFresh, t: t - 7 * H };
  assert.strictEqual(C.consensusAt(t, adt, [mwDead], "vmax"), null);

  // band floor: two agreeing members with small sigmas -> +/-10 kt floor
  const adtTight = { ...adt, vmax: 100, sigmaV: 3 };
  const mwTight = { ...mwFresh, vmax: 100, sigmaV: 3 };
  const c3 = C.consensusAt(t, adtTight, [mwTight], "vmax");
  assert.strictEqual(c3.half, 10.0, "±10 kt floor (D3)");

  // MSLP consensus only when both members carry a pressure
  assert.strictEqual(C.consensusAt(t, adt, [mwFresh], "mslp"), null,
    "MW without MSLP -> no MSLP consensus");
  const mwP = { ...mwFresh, mslp: 975, sigmaP: 7.0 };
  const cp = C.consensusAt(t, adt, [mwP], "mslp");
  assert.ok(cp && cp.value > 960 && cp.value < 975, "MSLP blend inside member range");

  // member cap: at most 4 members enter (V&H §2c)
  const many = [1, 2, 2.5].map(function (h) {
    return { ...mwFresh, t: t - h * H };
  }).concat([{ ...mwFresh, t: t - 2.8 * H }]);
  const c4 = C.consensusAt(t, adt, many, "vmax");
  assert.strictEqual(c4.n, 4, "capped at 4 members");
}

// ---------------------------------------------------------------- series()
{
  const t0 = Date.parse("2026-07-11T00:00:00Z");
  const mkResult = (h, vmax) => ({
    frame: { timeMs: t0 + h * H },
    archer: { center: {}, confidenceScore: 1.0 },
    rec: { vmax: vmax, mslp: 980, eyescene: 3, cloudscene: 0, land: 0 }
  });
  const results = [mkResult(0, 60), mkResult(2, 62), mkResult(4, 64),
                   mkResult(9, 70), mkResult(11, 72)];
  const mw = [{ kind: "mw", label: "MW", t: t0 + 1.5 * H, vmax: 90,
                sigmaV: 13, mslp: null, sigmaP: null, bias: 0 }];
  const sc = C.series(results, mw);
  assert.strictEqual(sc.length, 5);
  assert.strictEqual(sc[0], null, "no MW yet at t=0 (past-only) -> no consensus");
  assert.ok(sc[1] && sc[1].value > 62, "fresh MW pulls the consensus up");
  assert.ok(sc[2] && sc[2].value > 64 && sc[2].value - 64 < sc[1].value - 62,
    "older MW pulls less");
  assert.strictEqual(sc[3], null, "MW dead after 6 h -> honest gap");
  assert.strictEqual(sc[4], null);
}

// ---------------------------------------------------------------- stormSlug()
{
  assert.strictEqual(C.stormSlug({ id: "JTWC_WP072026" }), "wp072026");
  assert.strictEqual(C.stormSlug({ id: "NHC_AL092024" }), "al092024");
  assert.strictEqual(C.stormSlug({ id: "archive" }), null);
  assert.strictEqual(C.stormSlug(null), null);
}

console.log("test_satcon.cjs: all assertions passed");
