/* objfix.js — objective TC center-fix (ARCHER port) + objective intensity
 * (ADT port). Pure compute, no DOM: works in the browser (window.ObjFix),
 * a Web Worker, and node (module.exports) for the unit tests.
 *
 * PROVENANCE — this is a line-faithful port, not a reimplementation from a
 * paper. Every constant and formula below was read from primary source on
 * 2026-07-09 (see satellite/explorer/OBJFIX-METHODS.md §D):
 *   ARCHER : github.com/ajwimmers/archer @ d09f5c7 (Wimmers & Velden) —
 *            archer4_visir.py, utilities/ScoreFuncs.py, utilities/Conversions.py
 *   ADT    : the SSEC/CIMSS McIDAS-V port of ADT v8.x (mcidasv @ e42ad6d,
 *            edu/wisc/ssec/mcidasv/adt/{Data,Scene,Intensity,Functions,FFT}
 *            .java), cross-checked against AODT v7.2 C (Unidata/gempak).
 * Comments of the form  [src file:func]  cite where each block comes from.
 *
 * DOCUMENTED DEPARTURES from the operational codes (each flagged inline):
 *   D1 parallax correction: none (spec §A flags it out of scope for v1;
 *      archer4_visir para_fix path is skipped -> like para_fix=False).
 *   D2 ARCHER regrid: the source griddata('linear') over scattered swath
 *      points reduces to bilinear interpolation for our already-regular
 *      lat/lon grids (mathematically identical on a regular grid).
 *   D3 ADT shear distance: the v8.x Java port itself leaves adt_shearbw
 *      unimplemented (Scene.adt_cdoshearcalc type 3 returns -99). We use
 *      distance from center to the nearest pixel colder than the threshold
 *      as a stand-in, clamped like the source (>=4 km). APPROXIMATION.
 *   D4 rapid-dissipation slope: Functions.adt_slopecal was not ported; the
 *      6-h Final-T# decline extrapolated to /24 h stands in. APPROXIMATION.
 *   D5 no ADT microwave-eye-score adjustment (needs the MW history stream);
 *      MWAnalysisFlag path is a no-op, Rule 8 runs in its MW-off form.
 *   D6 surface-level cloud-mask dilation: source dilates 2 cells "for ~10 km
 *      resolution"; we scale the cell count to the input resolution to keep
 *      the same ~20 km physical reach.
 *   D7 ADT latitude-bias MSLP adjustment (CIadjp) not applied; the raw
 *      Dvorak table MSLP is reported and labeled as unadjusted.
 * The HONESTY CONTRACT for anything shown from this module lives in the
 * panel: AUTOMATED OBJECTIVE SATELLITE ESTIMATE, never official.
 */
(function () {
  'use strict';

  var DEG = Math.PI / 180;

  // =========================================================================
  // Small numerics shared by both ports
  // =========================================================================

  // [ScoreFuncs.py:distance_deg] flat-earth degrees with cos(mean target lat)
  function distanceDeg(lon1, lat1, lon2, lat2) {
    var avgLat = lat2;
    var dLat = lat1 - lat2;
    var dLon = (lon1 - lon2) * Math.cos(DEG * avgLat);
    return Math.sqrt(dLat * dLat + dLon * dLon);
  }

  // [Functions.java:distance_angle] great-circle km + the ADT bearing
  // convention (0=N, increasing toward WEST — internal-only; every consumer
  // below uses the same convention so it cancels out).
  function distanceAngle(endLat, endLon, startLat, startLon) {
    var sLa = startLat * DEG, sLo = startLon * DEG;
    var eLa = endLat * DEG, eLo = endLon * DEG;
    var cc = Math.cos(eLa) * Math.cos(eLo) - Math.cos(sLa) * Math.cos(sLo);
    var sc = Math.cos(eLa) * Math.sin(eLo) - Math.cos(sLa) * Math.sin(sLo);
    var ls = Math.sin(eLa) - Math.sin(sLa);
    var chord = Math.sqrt(cc * cc + sc * sc + ls * ls);
    var distKm = 2.0 * Math.asin(chord / 2.0) * 6371.0;
    var ang = 0.0;
    if (Math.abs(distKm) > 0.0001) {
      ang = (Math.sin(sLo - eLo) * Math.sin(Math.PI / 2 - eLa)) / Math.sin(chord);
    }
    if (Math.abs(ang) > 1.0) ang = ang > 0 ? 1.0 : -1.0;
    ang = Math.asin(ang) / DEG;
    if (eLa < sLa) ang = 180.0 - ang;
    if (ang < 0.0) ang = 360.0 + ang;
    return [distKm, ang];
  }

  // [Functions.java:distance_angle2] inverse of the above (used by logspiral)
  function distanceAngle2(startLat, startLon, distKm, angleDeg) {
    var la0 = (90.0 - startLat) * DEG;
    var la0f = la0;
    var lo0 = startLon * DEG;
    var ang = angleDeg;
    if (startLat < 0.0) {
      la0f = -(90.0 + startLat) * DEG;
      lo0 = (startLon - 180.0) * DEG;
      ang = 360.0 - ang;
    }
    var angI = Math.trunc(ang);
    var angR = -1.0 * (((540 - angI) % 360)) * DEG;
    var dR = (distKm / 111.1) * DEG;
    var latA = Math.acos(Math.cos(la0) * Math.cos(dR) + Math.sin(la0) * Math.sin(dR) * Math.cos(angR));
    var lonA = 0.0;
    if (Math.abs(latA) >= 1e-7) {
      var arg = (Math.sin(dR) * Math.sin(angR)) / Math.sin(latA);
      if (Math.abs(arg) > 1.0) arg = arg > 0 ? 1.0 : -1.0;
      lonA = Math.asin(arg);
      var atanV = Math.atan(Math.sin(1.570797 - angR)) / Math.tan(1.570797 - dR);
      if (atanV > la0f) lonA = 2.0 * 1.570797 - lonA;
    }
    lonA = lo0 - lonA;
    var endLat = 90.0 - latA / DEG;
    var endLon = (Math.trunc(10000 * (lonA / DEG)) % 3600000) / 10000.0;
    if (endLon < -180.0) endLon += 360.0;
    return [endLat, endLon];
  }

  // numpy.gradient semantics on a 2D row-major Float64Array: central
  // differences inside, one-sided at the edges, NaN propagation as in numpy.
  function npGradient2D(a, nr, nc, dRow, dCol) {
    var gr = new Float64Array(nr * nc), gc = new Float64Array(nr * nc);
    var i, j, k;
    for (i = 0; i < nr; i++) {
      for (j = 0; j < nc; j++) {
        k = i * nc + j;
        if (i === 0) gr[k] = (a[k + nc] - a[k]) / dRow;
        else if (i === nr - 1) gr[k] = (a[k] - a[k - nc]) / dRow;
        else gr[k] = (a[k + nc] - a[k - nc]) / (2 * dRow);
        if (j === 0) gc[k] = (a[k + 1] - a[k]) / dCol;
        else if (j === nc - 1) gc[k] = (a[k] - a[k - 1]) / dCol;
        else gc[k] = (a[k + 1] - a[k - 1]) / (2 * dCol);
      }
    }
    return { row: gr, col: gc };
  }

  // Bilinear sample from a regular grid (latArr descending, lonArr ascending),
  // NaN outside / when any corner is NaN — matches griddata('linear') on a
  // regular grid [departure D2].
  function bilinear(latArr, lonArr, data, nr, nc, lat, lon) {
    var lat0 = latArr[0], latN = latArr[nr - 1];
    var lon0 = lonArr[0], lonN = lonArr[nc - 1];
    if (lat > lat0 || lat < latN || lon < lon0 || lon > lonN) return NaN;
    var fi = (lat0 - lat) / (lat0 - latN) * (nr - 1);
    var fj = (lon - lon0) / (lonN - lon0) * (nc - 1);
    var i0 = Math.floor(fi), j0 = Math.floor(fj);
    if (i0 >= nr - 1) i0 = nr - 2;
    if (j0 >= nc - 1) j0 = nc - 2;
    var di = fi - i0, dj = fj - j0;
    var a = data[i0 * nc + j0], b = data[i0 * nc + j0 + 1];
    var c = data[(i0 + 1) * nc + j0], d = data[(i0 + 1) * nc + j0 + 1];
    return a * (1 - di) * (1 - dj) + b * (1 - di) * dj + c * di * (1 - dj) + d * di * dj;
  }

  function nanMax(arr) {
    var m = -Infinity;
    for (var i = 0; i < arr.length; i++) if (!isNaN(arr[i]) && arr[i] > m) m = arr[i];
    return m === -Infinity ? NaN : m;
  }

  function interp1(x, xs, ys) {
    if (x <= xs[0]) return ys[0];
    var n = xs.length;
    if (x >= xs[n - 1]) return ys[n - 1];
    for (var i = 1; i < n; i++) {
      if (x <= xs[i]) {
        var f = (x - xs[i - 1]) / (xs[i] - xs[i - 1]);
        return ys[i - 1] + f * (ys[i] - ys[i - 1]);
      }
    }
    return ys[n - 1];
  }

  // =========================================================================
  // ARCHER — center fix                    [archer4_visir.py + ScoreFuncs.py]
  // =========================================================================
  // field: { latArr, lonArr (deg; lat descending), bt (Float64Array, KELVIN,
  //          NaN = void), nr, nc, resKm }
  // firstGuess: { lat, lon, vmax (kt) }
  // opts: { channelType: 'IR' (v1 is IR-only), searchRadiusDeg (default 2.0 —
  //         tighten for loop continuation), onProgress }

  // [ScoreFuncs.py:spiral_center_calc]
  function spiralCenterCalc(xArr, yArr, dataGrid, nGrid, sensorType, opLat,
                            filterRadiusDeg, searchRadiusDeg, spacingDeg) {
    var alpha = 5 * DEG;
    var outsideFactor = (sensorType === 'IR' || sensorType === 'Vis') ? 0.50 : 0.62;
    var n = nGrid, k, i, j;

    // usable disk surrounded by NaN
    var disk = new Float64Array(n * n);
    var nIn = 0, nClean = 0;
    for (i = 0; i < n; i++) {
      for (j = 0; j < n; j++) {
        k = i * n + j;
        var r2 = xArr[j] * xArr[j] + yArr[i] * yArr[i];
        if (r2 <= filterRadiusDeg * filterRadiusDeg) {
          nIn++;
          disk[k] = dataGrid[k];
          if (!isNaN(dataGrid[k])) nClean++;
        } else disk[k] = NaN;
      }
    }
    var fractionInput = nIn ? nClean / nIn : 0;

    var inc = xArr[1] - xArr[0];
    var g = npGradient2D(disk, n, n, inc, inc);
    // grad_n = -gradient(axis0) : rows run north->south  [ScoreFuncs L184-185]
    // clean 1-D arrays with log-compressed gradient magnitude [L186-193]
    var cx = new Float64Array(nClean), cy = new Float64Array(nClean);
    var gN = new Float64Array(nClean), gE = new Float64Array(nClean);
    var m = 0;
    for (i = 0; i < n; i++) {
      for (j = 0; j < n; j++) {
        k = i * n + j;
        if (isNaN(disk[k])) continue;
        var gn = -g.row[k], ge = g.col[k];
        var mag = Math.sqrt(gn * gn + ge * ge);
        var red = mag > 0 ? Math.log(1 + mag) / mag : 0;
        cx[m] = xArr[j]; cy[m] = yArr[i];
        gN[m] = isNaN(gn) ? NaN : red * gn;
        gE[m] = isNaN(ge) ? NaN : red * ge;
        m++;
      }
    }

    // coarse candidate search [L196-233]
    var nOff = Math.round(2 * searchRadiusDeg / spacingDeg) + 1;
    var offArr = new Float64Array(nOff);
    for (i = 0; i < nOff; i++) offArr[i] = -searchRadiusDeg + i * spacingDeg;
    var coarse = new Float64Array(nOff * nOff);
    coarse.fill(NaN);
    var cornerCut = searchRadiusDeg + 2 * spacingDeg / 3;
    var signLat = opLat >= 0 ? 1 : -1;
    var a2 = Math.sqrt(1 + alpha * alpha);
    for (var ri = 0; ri < nOff; ri++) {
      var xOff = offArr[ri];
      for (var ci = 0; ci < nOff; ci++) {
        var yOff = offArr[ci];
        if (xOff * xOff + yOff * yOff > cornerCut * cornerCut) continue;
        var sum = 0, cnt = 0;
        for (m = 0; m < nClean; m++) {
          var px = cx[m] - xOff, py = cy[m] - yOff;
          var norm = a2 * Math.sqrt(px * px + py * py);
          if (norm === 0) continue;
          var sxv = (alpha * px + signLat * py) / norm;
          var syv = (alpha * py - signLat * px) / norm;
          var raw = sxv * gN[m] - syv * gE[m];
          if (isNaN(raw)) continue;
          // inward-aligned counts fully; counter-aligned at outsideFactor [L226-227]
          sum += raw < 0 ? -raw : outsideFactor * raw;
          cnt++;
        }
        coarse[ri * nOff + ci] = cnt ? sum / cnt : NaN;
      }
    }

    // interpolate coarse (x=row-coord, y=col-coord) onto the fine grid [L236-240]
    var sp = new Float64Array(n * n);
    for (i = 0; i < n; i++) {
      for (j = 0; j < n; j++) {
        k = i * n + j;
        var xq = xArr[j], yq = yArr[i];
        if (xq * xq + yq * yq >= searchRadiusDeg * searchRadiusDeg) { sp[k] = NaN; continue; }
        var fr = (xq - offArr[0]) / spacingDeg;    // coarse row index (x)
        var fc = (yq - offArr[0]) / spacingDeg;    // coarse col index (y)
        var r0 = Math.floor(fr), c0 = Math.floor(fc);
        if (r0 < 0) r0 = 0; if (r0 >= nOff - 1) r0 = nOff - 2;
        if (c0 < 0) c0 = 0; if (c0 >= nOff - 1) c0 = nOff - 2;
        var dr = fr - r0, dc = fc - c0;
        var v00 = coarse[r0 * nOff + c0], v01 = coarse[r0 * nOff + c0 + 1];
        var v10 = coarse[(r0 + 1) * nOff + c0], v11 = coarse[(r0 + 1) * nOff + c0 + 1];
        sp[k] = v00 * (1 - dr) * (1 - dc) + v01 * (1 - dr) * dc +
                v10 * dr * (1 - dc) + v11 * dr * dc;
      }
    }
    return { grid: sp, fractionInput: fractionInput };
  }

  // [ScoreFuncs.py:ring_score_calc]
  function ringScoreCalc(xArr, yArr, dataGrid, nGrid, sensorType, isInBounds,
                         minRadiusDeg, maxRadiusDeg) {
    var n = nGrid, i, j, k;
    var incAng = 5;
    var nAng = 72;
    var ringPointThresh = 0.425 * nAng;
    var data = dataGrid;
    if (sensorType.indexOf('37') >= 0) {
      data = new Float64Array(n * n);
      for (k = 0; k < n * n; k++) data[k] = 450 - dataGrid[k];
    }
    // cube-root image, the author's ±1.14 spacing [L262-263]
    var cbrt = new Float64Array(n * n);
    for (k = 0; k < n * n; k++) cbrt[k] = Math.pow(data[k], 1 / 3);
    var g = npGradient2D(cbrt, n, n, -1.14, 1.14);
    var gradN = g.row, gradE = g.col;

    var degPerPix = Math.abs(yArr[0] - yArr[1]);
    var ringScore = new Float64Array(n * n); ringScore.fill(NaN);
    var ringRadius = new Float64Array(n * n);

    // inward radial unit vectors [L274-276]
    var uvx = new Float64Array(nAng), uvy = new Float64Array(nAng);
    for (i = 0; i < nAng; i++) {
      uvx[i] = -Math.cos(DEG * i * incAng);
      uvy[i] = -Math.sin(DEG * i * incAng);
    }

    // offset points within the radius range [L279-289]
    var midRow = Math.round(n / 2), midCol = Math.round(n / 2);
    var offRow = [], offCol = [], offX = [], offY = [];
    var rr = (maxRadiusDeg + degPerPix) * (maxRadiusDeg + degPerPix);
    for (i = 0; i < n; i++) {
      for (j = 0; j < n; j++) {
        if (xArr[j] * xArr[j] + yArr[i] * yArr[i] < rr) {
          offRow.push(i - midRow); offCol.push(j - midCol);
          offX.push(xArr[j]); offY.push(yArr[i]);
        }
      }
    }

    var nRad = Math.round((maxRadiusDeg - minRadiusDeg) / 0.05) + 1;
    var radArr = [];
    for (i = 0; i < nRad; i++) radArr.push(minRadiusDeg + i * 0.05);
    var fullByCell = {};   // "i,j" -> Float64Array(nRad)  (score_by_radius)

    for (var radIdx = nRad - 1; radIdx >= 0; radIdx--) {
      var radiusDeg = radArr[radIdx];
      // nearest-cell offsets for this radius's 72 ring points [L305-314]
      var rowOff = new Int32Array(nAng), colOff = new Int32Array(nAng);
      for (var ai = 0; ai < nAng; ai++) {
        var rx = radiusDeg * Math.cos(DEG * ai * incAng);
        var ry = radiusDeg * Math.sin(DEG * ai * incAng);
        var best = Infinity, bi = 0;
        for (m = 0; m < offX.length; m++) {
          var d = (offX[m] - rx) * (offX[m] - rx) + (offY[m] - ry) * (offY[m] - ry);
          if (d < best) { best = d; bi = m; }
        }
        rowOff[ai] = offRow[bi]; colOff[ai] = offCol[bi];
      }
      var radiusFactor = Math.pow(radiusDeg, 0.1);
      var m;
      for (i = 0; i < n; i++) {
        for (j = 0; j < n; j++) {
          if (!isInBounds[i * n + j]) continue;
          var sum = 0, nReal = 0;
          for (ai = 0; ai < nAng; ai++) {
            var ri2 = i + rowOff[ai], ci2 = j + colOff[ai];
            if (ri2 < 0 || ri2 >= n || ci2 < 0 || ci2 >= n) continue;
            var dot = uvx[ai] * gradE[ri2 * n + ci2] + uvy[ai] * gradN[ri2 * n + ci2];
            if (isNaN(dot)) continue;
            sum += dot; nReal++;
          }
          // too few valid ring points -> 0 (the source's "nicer" path) [L347-354]
          var score = (nReal <= ringPointThresh) ? 0 : radiusFactor * (sum / nReal);
          k = i * n + j;
          if (radIdx === nRad - 1 || score > ringScore[k]) {
            ringScore[k] = score;
            ringRadius[k] = radiusDeg;
          }
          var key = i + ',' + j;
          if (!fullByCell[key]) { fullByCell[key] = new Float64Array(nRad); fullByCell[key].fill(NaN); }
          fullByCell[key][radIdx] = score;
        }
      }
    }
    return { score: ringScore, radius: ringRadius, radArr: radArr, fullByCell: fullByCell };
  }

  // [ScoreFuncs.py:combo_parts_calc_3_0] IR/Vis branch
  function comboPartsCalc(field, opLon, opLat, sensor, penaltyWeight, opts) {
    opts = opts || {};
    var lonInc = 0.025, latInc = 0.025;
    var perimDeg = 2.5, filterRadiusDeg = 2.5;
    var spiralSearchRadiusDeg = Math.min(2.0, opts.searchRadiusDeg || 2.0);
    var maxRadiusDeg = 0.50;
    var spiralWeight = 15, spiralOffset = 20, spiralSpacingDeg = 0.05;
    var ringWeightInternal = 250.0, minRadiusDeg = 0.05;

    // false-data removal [L55-57]
    var bt = field.bt;
    var btc = new Float64Array(bt.length);
    for (var k0 = 0; k0 < bt.length; k0++) {
      var v = bt[k0];
      btc[k0] = (v < 80) ? NaN : v;
    }

    // offset grid centered on the first guess [L72-77]
    var n = Math.round(2 * perimDeg / lonInc) + 1;
    var xArr = new Float64Array(n), yArr = new Float64Array(n);
    var i, j, k;
    for (i = 0; i < n; i++) {
      xArr[i] = -perimDeg + i * lonInc;
      yArr[i] = perimDeg - i * latInc;
    }
    var cosLat = Math.cos(DEG * opLat);
    var lonArr1 = new Float64Array(n), latArr1 = new Float64Array(n);
    for (i = 0; i < n; i++) {
      lonArr1[i] = xArr[i] / cosLat + opLon;
      latArr1[i] = yArr[i] + opLat;
    }
    // regrid [departure D2: bilinear == griddata linear on regular input]
    var dataGrid = new Float64Array(n * n);
    for (i = 0; i < n; i++) {
      for (j = 0; j < n; j++) {
        dataGrid[i * n + j] = bilinear(field.latArr, field.lonArr, btc,
                                       field.nr, field.nc, latArr1[i], lonArr1[j]);
      }
    }

    var spiral = spiralCenterCalc(xArr, yArr, dataGrid, n, sensor, opLat,
                                  filterRadiusDeg, spiralSearchRadiusDeg, spiralSpacingDeg);
    var spiralScore = new Float64Array(n * n);
    for (k = 0; k < n * n; k++) spiralScore[k] = spiralWeight * spiral.grid[k] - spiralOffset;

    // distance penalty [L106-108]. The source's distance_deg uses ONE
    // cos(mean grid lat) = cos(opLat); with lon_grid = x/cos(opLat)+opLon that
    // reduces exactly to offset-space euclidean distance — computed as such.
    var penalty = new Float64Array(n * n);
    for (i = 0; i < n; i++) {
      for (j = 0; j < n; j++) {
        penalty[i * n + j] = penaltyWeight * Math.sqrt(xArr[j] * xArr[j] + yArr[i] * yArr[i]);
      }
    }
    var withPen = new Float64Array(n * n);
    var maxPen = -Infinity;
    for (k = 0; k < n * n; k++) {
      var w = spiralScore[k] - penalty[k];
      withPen[k] = isNaN(w) ? -1e9 : w;
      if (withPen[k] > maxPen && withPen[k] !== -1e9) maxPen = withPen[k];
    }

    // swarm of ring candidates [L110-135]
    var spiralFitBuffer = 1.5, swarmReach = 0.25;
    var bufX = [], bufY = [];
    for (i = 0; i < n; i++) {
      for (j = 0; j < n; j++) {
        if (withPen[i * n + j] > maxPen - spiralFitBuffer) { bufX.push(xArr[j]); bufY.push(yArr[i]); }
      }
    }
    var isInBounds = new Uint8Array(n * n);
    for (i = 0; i < n; i++) {
      for (j = 0; j < n; j++) {
        var xv = xArr[j], yv = yArr[i];
        for (var b = 0; b < bufX.length; b++) {
          var dx = xv - bufX[b], dy = yv - bufY[b];
          // swarm distance in offset-deg space (grid is aspect-corrected)
          if (dx * dx + dy * dy < swarmReach * swarmReach) { isInBounds[i * n + j] = 1; break; }
        }
      }
    }

    var ring = ringScoreCalc(xArr, yArr, dataGrid, n, sensor, isInBounds,
                             minRadiusDeg, maxRadiusDeg);
    var ringScore = new Float64Array(n * n);
    for (k = 0; k < n * n; k++) ringScore[k] = ringWeightInternal * ring.score[k];

    return {
      n: n, lonArr1: lonArr1, latArr1: latArr1, xArr: xArr, yArr: yArr,
      dataGrid: dataGrid,
      spiralScoreGrid: spiralScore,
      penaltyGrid: penalty,
      ringScoreGrid: ringScore,
      ringRadiusGrid: ring.radius,
      ringFullByCell: ring.fullByCell,
      ringRadArr: ring.radArr,
      fractionInput: spiral.fractionInput
    };
  }

  // [ScoreFuncs.py:quality_check]
  function qualityCheck(sd) {
    var n = sd.n;
    var s = new Float64Array(n * n);
    var maxV = -Infinity, maxK = 0, k;
    for (k = 0; k < n * n; k++) {
      s[k] = isNaN(sd.spiralScoreGrid[k]) ? -1e9 : sd.spiralScoreGrid[k];
      if (s[k] > maxV) { maxV = s[k]; maxK = k; }
    }
    var iM = Math.floor(maxK / n), jM = maxK % n;
    if (sd.fractionInput < 0.5) return false;
    if (s[maxK] === -1e9) return false;
    if (iM <= 1 || jM <= 1) return false;
    if (iM >= n - 2 || jM >= n - 2) return false;
    if (s[(iM - 2) * n + jM] === -1e9 || s[(iM + 2) * n + jM] === -1e9) return false;
    if (s[iM * n + jM - 2] === -1e9 || s[iM * n + jM + 2] === -1e9) return false;
    return true;
  }

  // [Conversions.py:confidence_to_alpha]
  function confidenceToAlpha(confidenceScore, channelType, fxHr, vmax) {
    var alphaFloor = 0.5, mLo, bLo, mHi, bHi;
    if (channelType === 'IR') { mLo = 9.89; bLo = -2.07; mHi = 9.26; bHi = 1.95; }
    else if (channelType === 'SWIR') { mLo = 8.68; bLo = -0.37; mHi = 14.2; bHi = -0.24; }
    else if (channelType === 'Vis' || channelType === 'DNB') { mLo = 14.44; bLo = -0.83; mHi = 14.64; bHi = 3.45; }
    else { mLo = 3.28; bLo = 2.63; mHi = 1.61; bHi = 9.58; }  // MW channels
    var aLo = Math.max(mLo * confidenceScore + bLo, alphaFloor);
    var aHi = Math.max(mHi * confidenceScore + bHi, alphaFloor);
    var a0 = interp1(vmax, [0, 60, 85, 300], [aLo, aLo, aHi, aHi]);
    var c1 = 4.00 / 13.4 / Math.pow(15, 1.5);
    return a0 / (1 + c1 * a0 * Math.pow(fxHr || 0, 1.5));
  }

  // certainty radius: P(err<=x) = 1-(ax+1)e^(-ax)  [archer4_visir L255-260]
  function certaintyRadii(alpha) {
    var r50 = null, r95 = null, b50 = Infinity, b95 = Infinity;
    for (var x = 0; x < 10; x += 0.01) {
      var cdf = 1 - (alpha * x + 1) * Math.exp(-alpha * x);
      var d50 = Math.abs(cdf - 0.50), d95 = Math.abs(cdf - 0.95);
      if (d50 < b50) { b50 = d50; r50 = x; }
      if (d95 < b95) { b95 = d95; r95 = x; }
    }
    return { r50: r50, r95: r95 };
  }

  // [archer4_visir.py:archer4_visir] IR path, para_fix=False [departure D1]
  function archerFix(field, firstGuess, opts) {
    opts = opts || {};
    var channelType = opts.channelType || 'IR';
    var ringWeight = (channelType === 'Vis' || channelType === 'DNB') ? 0.0020 : 0.0167;
    var penaltyWeight = 0.33;
    var maskVal = 265;  // IR: keep low cloud only on the surface pass
    var out = { channelType: channelType };
    var confidenceBest = -1e6;

    var levels = ['feature', 'surface'];
    for (var li = 0; li < levels.length; li++) {
      var level = levels[li];
      var f = field;
      if (level === 'surface') {
        // cloud-mask + NaN dilation scaled to input resolution [departure D6]
        var btM = new Float64Array(field.bt.length);
        for (var k = 0; k < field.bt.length; k++) {
          btM[k] = field.bt[k] < maskVal ? NaN : field.bt[k];
        }
        var dil = Math.max(1, Math.round(20 / Math.max(1, field.resKm) / 2));
        btM = dilateNaN(btM, field.nr, field.nc, dil);
        f = { latArr: field.latArr, lonArr: field.lonArr, bt: btM,
              nr: field.nr, nc: field.nc, resKm: field.resKm };
      }

      var sd = comboPartsCalc(f, firstGuess.lon, firstGuess.lat, channelType,
                              penaltyWeight, opts);
      var n = sd.n;
      // NaN cleanup [archer4_visir L207-208]
      for (k = 0; k < n * n; k++) {
        if (isNaN(sd.spiralScoreGrid[k])) sd.spiralScoreGrid[k] = -1e9;
        if (isNaN(sd.ringScoreGrid[k])) sd.ringScoreGrid[k] = 0;
      }
      // combo [L213-219]
      var combo = new Float64Array(n * n);
      var comboMax = -Infinity, comboK = 0;
      for (k = 0; k < n * n; k++) {
        combo[k] = (sd.spiralScoreGrid[k] - sd.penaltyGrid[k]) + ringWeight * sd.ringScoreGrid[k];
        if (combo[k] > comboMax) { comboMax = combo[k]; comboK = k; }
      }
      var iC = Math.floor(comboK / n), jC = comboK % n;
      var lonComboMax = sd.lonArr1[jC], latComboMax = sd.latArr1[iC];
      var ringRadiusDeg = sd.ringRadiusGrid[comboK];
      var ringScoreAt = sd.ringScoreGrid[comboK];
      var scoreByRadius = sd.ringFullByCell[iC + ',' + jC] || null;

      // confidence on the no-penalty combo [L233-247]
      var conf = new Float64Array(n * n);
      var confMax = -Infinity, confK = 0;
      for (k = 0; k < n * n; k++) {
        conf[k] = sd.spiralScoreGrid[k] + ringWeight * sd.ringScoreGrid[k];
        if (conf[k] > confMax) { confMax = conf[k]; confK = k; }
      }
      var iF = Math.floor(confK / n), jF = confK % n;
      var latConfMax = sd.latArr1[iF], lonConfMax = sd.lonArr1[jF];
      var CONFIDENCE_DIST_DEG = 0.75;
      var awayMax = -Infinity;
      var cosC = Math.cos(DEG * latConfMax);
      for (var ii = 0; ii < n; ii++) {
        for (var jj = 0; jj < n; jj++) {
          var dLat = sd.latArr1[ii] - latConfMax;
          var dLon = cosC * (sd.lonArr1[jj] - lonConfMax);
          if (dLat * dLat + dLon * dLon > CONFIDENCE_DIST_DEG * CONFIDENCE_DIST_DEG) {
            var cv = conf[ii * n + jj];
            if (cv > awayMax) awayMax = cv;
          }
        }
      }
      var confidenceScore = confMax - awayMax;
      var alpha = confidenceToAlpha(confidenceScore, channelType, 0, firstGuess.vmax || 0);
      var radii = certaintyRadii(alpha);

      // IR eye probability [L264-268]
      var eyeProb = null;
      if (channelType === 'IR') {
        var stat = confidenceScore * ringScoreAt;
        eyeProb = interp1(stat,
          [0, .1, .5, 1, 1.5, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
          [0, 1, 7, 15, 40, 47, 50, 60, 65, 70, 75, 80, 85, 95, 98, 99, 100, 100, 100]);
      }

      var usesTarget = qualityCheck(sd);
      out.usesTarget = usesTarget;
      out.level = level;
      out.ringRadiusDeg = ringRadiusDeg;
      out.scoreByRadius = scoreByRadius ? Array.prototype.slice.call(scoreByRadius) : null;
      out.ringRadArr = sd.ringRadArr;
      out.confidenceScore = confidenceScore;
      out.alpha = alpha;
      out.radius50percCertDeg = radii.r50;
      out.radius95percCertDeg = radii.r95;
      out.eyeProb = eyeProb;
      out.fractionInput = sd.fractionInput;
      // rejected-candidate extraction for the display layer: local maxima of
      // the no-penalty combo grid away from the chosen point (not part of the
      // source algorithm — display only, drawn as faint crosshairs).
      out.rejected = localMaxima(conf, n, sd.latArr1, sd.lonArr1, iC, jC, 4);

      if (usesTarget) {
        out.center = { lat: latComboMax, lon: lonComboMax };
        out.weakCenter = null;
        if (level === 'feature') break;
      } else {
        out.center = null;
        out.weakCenter = { lat: latComboMax, lon: lonComboMax };
      }
      confidenceBest = Math.max(confidenceScore, confidenceBest);
    }
    return out;
  }

  // separable NaN dilation (min-filter style) for the surface-level mask
  function dilateNaN(a, nr, nc, d) {
    var out = new Float64Array(a);
    var tmp = new Float64Array(a);
    var i, j, k, o;
    for (i = 0; i < nr; i++) {
      for (j = 0; j < nc; j++) {
        k = i * nc + j;
        if (!isNaN(a[k])) continue;
        for (o = Math.max(0, j - d); o <= Math.min(nc - 1, j + d); o++) tmp[i * nc + o] = NaN;
      }
    }
    for (j = 0; j < nc; j++) {
      for (i = 0; i < nr; i++) {
        k = i * nc + j;
        if (!isNaN(tmp[k])) continue;
        for (o = Math.max(0, i - d); o <= Math.min(nr - 1, i + d); o++) out[o * nc + j] = NaN;
      }
    }
    return out;
  }

  // display-only: strongest local maxima of the score surface away from the fix
  function localMaxima(score, n, latArr1, lonArr1, iSkip, jSkip, count) {
    var cands = [];
    for (var i = 2; i < n - 2; i += 2) {
      for (var j = 2; j < n - 2; j += 2) {
        var k = i * n + j, v = score[k];
        if (v <= -1e8 || isNaN(v)) continue;
        if (v > score[k - 2] && v > score[k + 2] && v > score[k - 2 * n] && v > score[k + 2 * n]) {
          if (Math.abs(i - iSkip) < 12 && Math.abs(j - jSkip) < 12) continue;  // ~0.3 deg
          cands.push({ lat: latArr1[i], lon: lonArr1[j], score: v });
        }
      }
    }
    cands.sort(function (a, b) { return b.score - a.score; });
    return cands.slice(0, count);
  }

  // =========================================================================
  // ADT — objective intensity                        [McIDAS-V adt/*.java]
  // =========================================================================
  var KtoC = 273.16;                                     // [Data.java]
  var BDCurve = [30.0, 9.0, -30.0, -42.0, -54.0, -64.0, -70.0, -76.0, -80.0, -84.0, -100.0];
  var OUTER_RADIUS = 136.0, INNER_RADIUS = 24.0, RING_WIDTH = 4.0;
  var EYE_SEARCH_RADIUS = 24.0, MANUAL_EYE_RADIUS = 24.0;
  var LARGE_EYE_RADIUS = 38.0;                           // v8.x (v7.2 was 45)

  // [Functions.java] Dvorak pressure/wind tables, T# 1.0..9.0 step 0.1
  var PW_Tno = (function () {
    var t = [];
    for (var i = 0; i <= 80; i++) t.push(1.0 + i * 0.1);
    return t;
  })();
  var PW_Wind = [25.0, 25.0, 25.0, 25.0, 25.0, 25.0, 26.0,
    27.0, 28.0, 29.0, 30.0, 31.0, 32.0, 33.0, 34.0, 35.0, 37.0, 39.0, 41.0, 43.0, 45.0,
    47.0, 49.0, 51.0, 53.0, 55.0, 57.0, 59.0, 61.0, 63.0, 65.0, 67.4, 69.8, 72.2, 74.6,
    77.0, 79.6, 82.2, 84.8, 87.4, 90.0, 92.4, 94.8, 97.2, 99.6, 102.0, 104.6, 107.2, 109.8,
    112.4, 115.0, 117.4, 119.8, 122.2, 124.6, 127.0, 129.6, 132.2, 134.8, 137.4, 140.0,
    143.0, 146.0, 149.0, 152.0, 155.0, 158.0, 161.0, 164.0, 167.0, 170.0, 173.0, 176.0,
    179.0, 182.0, 185.0, 188.0, 191.0, 194.0, 197.0, 200.0];
  var PW_Pressure = [
    /* Atlantic */
    [1014.0, 1013.6, 1013.2, 1012.8, 1012.4, 1012.0, 1011.4, 1010.8,
      1010.2, 1009.6, 1009.0, 1008.2, 1007.4, 1006.6, 1005.8, 1005.0, 1004.0, 1003.0,
      1002.0, 1001.0, 1000.0, 998.8, 997.6, 996.4, 995.2, 994.0, 992.6, 991.2, 989.8,
      988.4, 987.0, 985.4, 983.8, 982.2, 980.6, 979.0, 977.2, 975.4, 973.6, 971.8,
      970.0, 968.0, 966.0, 964.0, 962.0, 960.0, 957.6, 955.2, 952.8, 950.4, 948.0,
      945.4, 942.8, 940.2, 937.6, 935.0, 932.2, 929.4, 926.6, 923.8, 921.0, 918.0,
      915.0, 912.0, 909.0, 906.0, 902.8, 899.6, 896.4, 893.2, 890.0, 886.6, 883.2,
      879.8, 876.4, 873.0, 869.4, 865.8, 862.2, 858.6, 855.0],
    /* Pacific */
    [1005.0, 1004.6, 1004.2, 1003.8, 1003.4, 1003.0, 1002.4, 1001.8,
      1001.2, 1000.6, 1000.0, 999.4, 998.8, 998.2, 997.6, 997.0, 995.8, 994.6, 993.4,
      992.2, 991.0, 989.6, 988.2, 986.8, 985.4, 984.0, 982.4, 980.8, 979.2, 977.6,
      976.0, 974.0, 972.0, 970.0, 968.0, 966.0, 963.6, 961.2, 958.8, 956.4, 954.0,
      951.4, 948.8, 946.2, 943.6, 941.0, 938.2, 935.4, 932.6, 929.8, 927.0, 924.4,
      921.8, 919.2, 916.6, 914.0, 910.8, 907.6, 904.4, 901.2, 898.0, 894.2, 890.4,
      886.6, 882.8, 879.0, 874.8, 870.6, 866.4, 862.2, 858.0, 853.4, 848.8, 844.2,
      839.6, 835.0, 830.0, 825.0, 820.0, 815.0, 810.0]];

  function ciToVmax(ci) {
    if (ci == null || ci < 1.0) return null;
    var idx = Math.round((Math.min(9.0, ci) - 1.0) / 0.1);
    return PW_Wind[Math.max(0, Math.min(PW_Wind.length - 1, idx))];
  }
  function ciToMslp(ci, domainID) {
    if (ci == null || ci < 1.0) return null;
    var idx = Math.round((Math.min(9.0, ci) - 1.0) / 0.1);
    var col = PW_Pressure[domainID === 1 ? 1 : 0];
    return col[Math.max(0, Math.min(col.length - 1, idx))];
  }

  // [FFT.java:calculateFFT] plain DFT of the 64-bin histogram, then count
  // magnitude local maxima over bins 1..30.
  function calculateFFT(hist64) {
    var N = 64, mag = new Float64Array(N), re, im, i, k;
    for (k = 0; k < N; k++) {
      re = 0; im = 0;
      for (i = 0; i < N; i++) {
        re += hist64[i] * Math.cos(-2 * Math.PI * k * i / N);
        im += hist64[i] * Math.sin(-2 * Math.PI * k * i / N);
      }
      mag[k] = Math.sqrt(re * re + im * im);
    }
    if (mag[0] === 0) return -99;
    var harmonics = 0;
    for (i = 2; i <= 31; i++) {
      if (mag[i - 1] > mag[i - 2] && mag[i - 1] > mag[i]) harmonics++;
    }
    return harmonics;
  }

  // [Data.java] ring stats about a fixed center. field bt in KELVIN.
  // Returns the History-record fields (temps in °C like the Java records).
  function calcEyeCloudTemps(field, centerLat, centerLon) {
    var dist = [], ang = [], temp = [];
    var i, j, k;
    for (i = 0; i < field.nr; i++) {
      var la = field.latArr[i];
      for (j = 0; j < field.nc; j++) {
        var t = field.bt[i * field.nc + j];
        if (isNaN(t)) continue;
        var da = distanceAngle(la, field.lonArr[j], centerLat, centerLon);
        if (da[0] <= OUTER_RADIUS + 80.0) {
          dist.push(da[0]); ang.push(da[1]); temp.push(t);
        }
      }
    }
    var nPts = dist.length;

    // eye temp: warmest within 24 km [Data:CalcEyeTemperature]
    var eyeMax = -99.0;
    for (k = 0; k < nPts; k++) {
      if (dist[k] <= EYE_SEARCH_RADIUS && temp[k] > eyeMax) eyeMax = temp[k];
    }

    // CW cloud: per-4km-ring warmest, then coldest of those [Data:CalcCWCloudInfo]
    var nRings = Math.floor((OUTER_RADIUS - INNER_RADIUS) / RING_WIDTH);
    var ringMax = new Float64Array(nRings); ringMax.fill(-999.0);
    for (k = 0; k < nPts; k++) {
      if (dist[k] >= INNER_RADIUS && dist[k] < OUTER_RADIUS) {
        var ring = Math.floor((dist[k] - INNER_RADIUS) / RING_WIDTH);
        if (temp[k] > ringMax[ring]) ringMax[ring] = temp[k];
      }
    }
    var cwTemp = 10000.0, cwRing = 0;
    for (j = 0; j < nRings; j++) {
      if (ringMax[j] < cwTemp && ringMax[j] > 160.0) {
        cwTemp = ringMax[j];
        cwRing = j * RING_WIDTH + INNER_RADIUS;
      }
    }

    // FFT values for cloud + eye regions [Data:CalcEyeCloudInfo]
    var fft = [0, 0];
    for (var scene = 0; scene <= 1; scene++) {
      var innerR = scene === 0 ? INNER_RADIUS : 0;
      var outerR = scene === 0 ? OUTER_RADIUS : INNER_RADIUS;
      var hist = new Float64Array(64);
      var bins = new Float64Array(64);
      for (i = 0; i < 64; i++) bins[i] = KtoC + 26.0 - i * 2.0;
      for (k = 0; k < nPts; k++) {
        if (dist[k] >= innerR && dist[k] <= outerR) {
          for (i = 0; i < 63; i++) {
            if (temp[k] <= bins[i] && temp[k] > bins[i + 1]) { hist[i] += 1; break; }
          }
        }
      }
      fft[scene] = calculateFFT(hist);
    }

    // 24 x 15° sector means over 24-136 km; annulus temp; symmetry; eye stdv
    var MAXSECTOR = 24;
    var secSum = new Float64Array(MAXSECTOR), secN = new Int32Array(MAXSECTOR);
    var eyeVals = [];
    for (k = 0; k < nPts; k++) {
      var a = ang[k] === 360.0 ? 0.0 : ang[k];
      if (dist[k] >= INNER_RADIUS && dist[k] <= OUTER_RADIUS) {
        var s = Math.min(MAXSECTOR - 1, Math.floor(a / 15.0));
        secSum[s] += temp[k]; secN[s]++;
      }
      if (dist[k] >= 0 && dist[k] < INNER_RADIUS) eyeVals.push(temp[k]);
    }
    var secAvg = new Float64Array(MAXSECTOR);
    for (i = 0; i < MAXSECTOR; i++) secAvg[i] = secN[i] ? secSum[i] / secN[i] : 0;
    var cloud2 = 0;
    for (i = 0; i < MAXSECTOR; i++) cloud2 += secAvg[i];
    cloud2 /= MAXSECTOR;
    var symSum = 0;
    for (i = 0; i < 12; i++) symSum += Math.abs(secAvg[i] - secAvg[i + 12]);
    var cloudSym = symSum / 12;

    // annulus: max(28, cwring-40) .. max(108, cwring+40) [Data L344-359]
    var annStart = Math.max(28.0, cwRing - 40.0);
    var annEnd = Math.max(108.0, cwRing + 40.0);
    var annSum = 0, annN = 0;
    for (k = 0; k < nPts; k++) {
      if (dist[k] >= annStart && dist[k] <= annEnd) { annSum += temp[k]; annN++; }
    }
    var cloudT = annN ? annSum / annN : NaN;

    var eyeAvg = 0;
    for (k = 0; k < eyeVals.length; k++) eyeAvg += eyeVals[k];
    eyeAvg = eyeVals.length ? eyeAvg / eyeVals.length : 0;
    var eyeVar = 0;
    for (k = 0; k < eyeVals.length; k++) {
      var dv = eyeVals[k] - eyeAvg;
      eyeVar += dv * dv;
    }
    var eyeStdv = eyeVals.length > 1 ? Math.sqrt(eyeVar / (eyeVals.length - 1)) : 0;

    return {
      eyet: eyeMax - KtoC,
      cwcloudt: cwTemp - KtoC,
      cwring: Math.trunc(cwRing),
      cloudt: cloudT - KtoC,
      cloudt2: cloud2 - KtoC,
      cloudsymave: cloudSym,
      eyestdv: eyeStdv,
      eyefft: fft[1],
      cloudfft: fft[0],
      nPts: nPts
    };
  }

  // [Data.java:CalcRMW] eye size / RMW via critical-temp crossings on the
  // center row/column, iterated 5x. Grid-index port of the pixel walk.
  function calcRMW(field, centerLat, centerLon, cloudtC, eyetC) {
    var nr = field.nr, nc = field.nc;
    // index of the center point
    var ci = nearestIndex(field.latArr, centerLat, true);
    var cj = nearestIndex(field.lonArr, centerLon, false);
    var critK = 228.0;
    if (cloudtC >= (223.0 - KtoC)) critK = KtoC + ((eyetC + 2.0 * cloudtC) / 3.0);
    var xMax = Math.min(nc, cj + 320), xMin = Math.max(0, cj - 320);
    var yMax = Math.min(nr, ci + 240), yMin = Math.max(0, ci - 240);
    var x0 = 0, x1 = 0, y0 = 0, y1 = 0;
    var t = function (i, j) { var v = field.bt[i * nc + j]; return isNaN(v) ? -9999 : v; };
    for (var it = 0; it < 5; it++) {
      var x = cj;
      while (t(ci, x) > critK) { x--; if (x === xMin) return { rmw: -99.5, eyeRadius: -99.5 }; }
      x0 = x; x = cj;
      while (t(ci, x) > critK) { x++; if (x === xMax) return { rmw: -99.5, eyeRadius: -99.5 }; }
      x1 = x;
      var y = ci;
      while (t(y, cj) > critK) { y--; if (y === yMin) return { rmw: -99.5, eyeRadius: -99.5 }; }
      y0 = y; y = ci;
      while (t(y, cj) > critK) { y++; if (y === yMax) return { rmw: -99.5, eyeRadius: -99.5 }; }
      y1 = y;
      cj = Math.trunc((x0 + x1) / 2);
      ci = Math.trunc((y0 + y1) / 2);
    }
    var cLat = field.latArr[ci], cLon = field.lonArr[cj];
    var d1 = distanceAngle(field.latArr[ci], field.lonArr[x0], cLat, cLon)[0];
    var d2 = distanceAngle(field.latArr[ci], field.lonArr[x1], cLat, cLon)[0];
    var d3 = distanceAngle(field.latArr[y0], field.lonArr[cj], cLat, cLon)[0];
    var d4 = distanceAngle(field.latArr[y1], field.lonArr[cj], cLat, cLon)[0];
    var avg = (d1 + d2 + d3 + d4) / 4.0;
    if (avg > 0) return { rmw: 2.8068 + 0.8361 * avg, eyeRadius: avg };
    return { rmw: -99.5, eyeRadius: -99.5 };
  }

  function nearestIndex(arr, v, descending) {
    var best = 0, bd = Infinity;
    for (var i = 0; i < arr.length; i++) {
      var d = Math.abs(arr[i] - v);
      if (d < bd) { bd = d; best = i; }
    }
    return best;
  }

  // [Scene.java:adt_logspiral] 10° log-spiral fit; counts consecutive 15° arcs
  // with >=4 near cold pixels. analysisType 1 = at point, 2 = 2°-box search.
  function logSpiral(field, inLat, inLon, tempThreshK, analysisType) {
    var resKm = field.resKm;
    var incAdd = (resKm > RING_WIDTH) ? 1 : Math.trunc(RING_WIDTH - resKm + 1.0);
    // The source computes the 1.5x near-pixel criterion from the raw image
    // resolution and assumes ~4 km input (IncAddVal strides finer grids to
    // ~4 km but the criterion kept the raw res, which starves the >=4-pixel
    // test). Generalized to the EFFECTIVE post-stride spacing — identical for
    // res >= 4 km inputs, correct on finer floater grids. [port note]
    var effKm = Math.max(resKm, incAdd * resKm);
    var distMaxKm = effKm + effKm / 2.0;
    var latMaxI, latMinI, lonMaxI, lonMinI;
    if (analysisType === 2) {
      latMaxI = Math.trunc((inLat + 1.0) * 100); latMinI = Math.trunc((inLat - 1.0) * 100);
      lonMaxI = Math.trunc((inLon + 1.0) * 100); lonMinI = Math.trunc((inLon - 1.0) * 100);
    } else {
      latMaxI = latMinI = Math.trunc(inLat * 100);
      lonMaxI = lonMinI = Math.trunc(inLon * 100);
    }
    // valid (cold) pixels; bounded to 300 km from center — the spiral itself
    // only reaches ~130 km + the 1° search box, so this is lossless in effect
    // and keeps the pixel list small on high-res floater grids.
    var vLat = [], vLon = [];
    for (var i = 0; i < field.nr; i += incAdd) {
      var la = field.latArr[i];
      if (Math.abs(la - inLat) > 3.0) continue;
      for (var j = 0; j < field.nc; j += incAdd) {
        var t = field.bt[i * field.nc + j];
        if (isNaN(t) || t > tempThreshK) continue;
        if (Math.abs((field.lonArr[j] - inLon) * Math.cos(DEG * inLat)) > 3.0) continue;
        vLat.push(la); vLon.push(field.lonArr[j]);
      }
    }
    var nValid = vLat.length;
    var bestArcs = -99, bestLat = -999.99, bestLon = -999.99, bestRot = 0;
    var A = 25.0, B = 10.0 / 57.29578;

    for (var xI = latMinI; xI <= latMaxI; xI += 20) {
      var sLat = xI / 100.0;
      for (var yI = lonMinI; yI <= lonMaxI; yI += 20) {
        var sLon = yI / 100.0;
        var skip = false;
        if (analysisType === 2) {
          for (var z = 0; z < nValid; z++) {
            if (distanceAngle(sLat, sLon, vLat[z], vLon[z])[0] <= 12.0) { skip = true; break; }
          }
        }
        if (skip) continue;
        var maxArcs = 0, maxRot = 0;
        for (var rot = 0; rot <= 330; rot += 30) {
          var run = 0, runMax = 0;
          for (var th = 0; th <= 540; th += 15) {
            var rKm = A * Math.exp(B * (th / 57.29578));
            var fTh = (sLat < 0 ? -th : th) + rot;
            var pt = distanceAngle2(sLat, sLon, rKm, fTh + 180.0);
            var cnt = 0;
            for (z = 0; z < nValid; z++) {
              if (Math.abs(pt[0] - vLat[z]) > 0.1 || Math.abs(pt[1] - vLon[z]) > 0.1) continue;
              if (distanceAngle(pt[0], pt[1], vLat[z], vLon[z])[0] <= distMaxKm) cnt++;
            }
            if (cnt >= 4) {
              run++;
              if (run > runMax) runMax = run;
            } else run = 0;
            if (runMax > maxArcs) { maxArcs = runMax; maxRot = rot; }
          }
        }
        if (maxArcs > bestArcs) {
          bestArcs = maxArcs; bestLat = sLat; bestLon = sLon; bestRot = maxRot;
        }
      }
    }
    return { arcs: bestArcs, lat: bestLat, lon: bestLon, rot: bestRot };
  }

  // [Scene.java:adt_cdoshearcalc] type 1 (CDO radius). Type 3 (shear) is a
  // FLAGGED APPROXIMATION [departure D3]: nearest cold pixel distance.
  function cdoShearCalc(field, inLat, inLon, tempThreshK, analysisType) {
    var i, j, k;
    if (analysisType === 1) {
      var r1 = 300.0, r2 = 300.0, r3 = 300.0, r4 = 300.0;
      var maxD = 0.0, warm = 0, total = 0;
      for (i = 0; i < field.nr; i++) {
        for (j = 0; j < field.nc; j++) {
          var t = field.bt[i * field.nc + j];
          if (isNaN(t)) continue;
          total++;
          if (t <= tempThreshK) continue;
          warm++;
          var da = distanceAngle(inLat, inLon, field.latArr[i], field.lonArr[j]);
          var d = da[0], a = da[1];
          if (d > maxD) maxD = d;
          if (d > MANUAL_EYE_RADIUS) {
            if (Math.abs(a - 45.0) <= 15.0 && d < r1) r1 = d;
            if (Math.abs(a - 135.0) <= 15.0 && d < r2) r2 = d;
            if (Math.abs(a - 225.0) <= 15.0 && d < r3) r3 = d;
            if (Math.abs(a - 315.0) <= 15.0 && d < r4) r4 = d;
          }
        }
      }
      if (warm >= total) { r1 = r2 = r3 = r4 = 0.0; }
      var v3 = MANUAL_EYE_RADIUS + RING_WIDTH, valid = 4;
      if (r1 < v3) valid--;
      if (r2 < v3) valid--;
      if (r3 < v3) valid--;
      if (r4 < v3) valid--;
      if (valid < 3) return 0.0;
      r1 = Math.min(r1, maxD); r2 = Math.min(r2, maxD);
      r3 = Math.min(r3, maxD); r4 = Math.min(r4, maxD);
      return (r1 + r2 + r3 + r4) / 4.0;
    }
    if (analysisType === 3) {
      // APPROXIMATION D3 (the v8.x Java port also has no adt_shearbw)
      var nearest = 1e9;
      for (i = 0; i < field.nr; i++) {
        for (j = 0; j < field.nc; j++) {
          var tv = field.bt[i * field.nc + j];
          if (isNaN(tv) || tv > tempThreshK) continue;
          var dd = distanceAngle(inLat, inLon, field.latArr[i], field.lonArr[j])[0];
          if (dd < nearest) nearest = dd;
        }
      }
      return nearest === 1e9 ? 999.0 : nearest;
    }
    return -99.0;
  }

  function bdCategory(tC) {
    for (var x = 0; x < 10; x++) {
      if (tC <= BDCurve[x] && tC > BDCurve[x + 1]) {
        var f = (tC - BDCurve[x]) / (BDCurve[x + 1] - BDCurve[x]);
        if (x === 0) f = 0.0;
        return { cat: x, flt: x + f };
      }
    }
    // out of table (warmer than +30 or colder than -100)
    return tC > BDCurve[0] ? { cat: 0, flt: 0.0 } : { cat: 9, flt: 10.0 };
  }

  // [Scene.java:DetermineSceneType] — rec carries the Data-derived stats;
  // history = array of prior records (oldest first); env = {landFlag,
  // initRawT, rmwSize}. Mutates rec with scene results.
  function determineSceneType(field, rec, history, env) {
    var eyeFFT = rec.eyefft, cloudFFT = rec.cloudfft;
    var lat = rec.latitude, lon = rec.longitude;
    var eyeT = rec.eyet, eyeStdv = rec.eyestdv;
    var cwT = rec.cwcloudt, cloudT = rec.cloudt, cloudSym = rec.cloudsymave;

    var cb = bdCategory(cloudT), eb = bdCategory(eyeT), wb = bdCategory(cwT);
    var cloudCat = cb.cat, cloudFlt = cb.flt;
    var eyeFlt = eb.flt;
    var cwCat = wb.cat, cwFlt = wb.flt;

    var cloudTempDiff = cloudT - cwT;
    var eyeCwFltDiff = cwFlt - eyeFlt;
    var eyeCloudFltDiff = cloudFlt - eyeFlt;
    var cloudFltDiff = cloudFlt - cwFlt;
    var cloudBDDiff = cloudCat - wb.cat;
    var eyeCloudCatDiff = cloudCat - eb.cat;
    var eyeCloudTempDiff2 = eyeT - Math.min(cloudT, cwT);

    var currentTime = rec.timeDays;
    var t12 = currentTime - 0.5;
    var prevEyeScene = -1, prevCloudScene = -1;
    var tno12 = 0.0, found12 = false, foundEyeScene = false;
    var maxRule9 = -99.0, lastRule9 = 0;
    var prevTno = maxRule9, prevValidTno = prevTno;
    var init = env.initRawT || 0.0;

    if (!history.length) {
      found12 = true; prevEyeScene = 3; lastRule9 = 1;
      if (cwFlt < 3.5 && init < 3.5) { prevCloudScene = 3; tno12 = init; }
      else { prevCloudScene = 0; tno12 = Math.max(init, 4.0); }
    } else {
      prevCloudScene = 3;
      var lastValidTime = 0.0;
      for (var x = 0; x < history.length; x++) {
        var h = history[x];
        var landOK = !((env.landFlag && h.land === 1) || h.Traw < 1.0);
        if (h.timeDays < currentTime && landOK) {
          lastValidTime = h.timeDays;
          if (h.timeDays >= t12 && !found12) { tno12 = h.Tfinal; found12 = true; }
          prevTno = h.Tfinal;
          prevCloudScene = h.cloudscene;
          prevEyeScene = h.eyescene;
          if (prevEyeScene <= 2) foundEyeScene = true;
          if (prevCloudScene === 4 && prevEyeScene === 3) foundEyeScene = false;
          prevValidTno = prevTno;
          lastRule9 = h.rule9;
          if (prevTno > maxRule9) maxRule9 = prevTno;
        } else if (!landOK) {
          if (h.timeDays - lastValidTime > 0.5) {
            foundEyeScene = false;
            prevTno = prevValidTno - 1.0 * (h.timeDays - lastValidTime);
          }
        }
      }
      if (!found12) tno12 = prevTno;
    }

    // ---- EYE score [Scene L276-321] ----
    var eA = 1.0 - (eyeFFT - 2) * 0.1;
    var eB = -(eyeFlt * 0.5);
    var eC = eyeStdv > 10.0 ? 0.50 : 0.0;
    var eD = eyeCloudFltDiff * 0.25 + eyeCwFltDiff * 0.50;
    var eE = 0.0;
    if (found12 && prevEyeScene < 3 && maxRule9 > 5.0) eC += 0.25;
    if (tno12 <= 4.5) eE = Math.max(-1.0, tno12 - 4.5);
    if (lastRule9 > 0 && prevTno < 4.0) eE -= 0.5;
    var eyeTotal = eA + eB + eC + eD + eE;
    var eyeScene = eyeTotal >= 0.50 ? 0 : 3;

    // RMW / eye size
    var eyeCDOSize = 0.0;
    if (env.rmwSize > 0) {
      rec.rmw = env.rmwSize;
      eyeCDOSize = env.rmwSize - 1.0;
    } else {
      var rmwv = calcRMW(field, lat, lon, cloudT, eyeT);
      rec.rmw = rmwv.rmw;
    }
    if (eyeScene === 0 && eyeCDOSize >= LARGE_EYE_RADIUS) eyeScene = 2;

    // ---- CLOUD score [Scene L348-433] ----
    var shear = false, irrCDO = false, curvedBand = true;
    var cbGray = true, cbBW = false, embCheck = false, embScene = false;
    var cA = cwFlt * 0.25, cB = cloudFlt * 0.25;
    var cC = cloudFFT <= 2 ? Math.min(1.50, cwFlt * 0.25) : 0.0;
    var cD = prevCloudScene >= 3 ? -0.50 : 0.5;
    var cE = 0.0;
    if (cwFlt > 2.0) {
      if (tno12 >= 2.5) {
        if (eyeScene === 0) cE = Math.min(1.00, tno12 - 2.5);
        if (tno12 >= 3.5) cE += 1.00;
      }
      if (found12 && foundEyeScene) cE += 1.25;
    }
    var cloudTotal = cA + cB + cC + cD + cE;
    if (cloudTotal < 0.0) shear = true;
    if (cloudTotal >= 1.00) {
      if (eyeCloudTempDiff2 < 0.0 && cloudSym > 40.0) irrCDO = true;
    }
    if (cloudTotal >= 2.00 && cloudTotal < 3.00) {
      if (eyeCloudTempDiff2 < 0.0 && cloudSym > 30.0) irrCDO = true;
      if (cwCat >= 3) {
        if (cloudBDDiff > 0 && cloudTempDiff < -8.0) { cbGray = false; cbBW = true; }
        if (eyeScene === 0 || (eyeFlt > 1.00 && eyeCloudCatDiff >= 2.00)) curvedBand = false;
        if (cloudFltDiff <= 0.0 && eyeCwFltDiff < 1.00) curvedBand = false;
      }
    }
    if (cloudTotal >= 3.00) {
      curvedBand = false;
      if (cloudBDDiff < 0 && cloudTempDiff > 8.0 && cloudSym > 30.0) { irrCDO = true; curvedBand = true; }
    }
    if (cloudT < cwT && cwT < eyeT) embCheck = true;
    var lsa = 0;
    if (!curvedBand && embCheck) {
      var thr = BDCurve[wb.cat + 1] + KtoC;
      lsa = logSpiral(field, lat, lon, thr, 1).arcs;
      if (lsa >= 8 && lsa < 20) embScene = true;
    }

    // ---- classify cloud region [Scene L478-648] ----
    var cbCat = 0, cbAmt = 0, cbMaxAmt = 0, cbMaxLat = lat, cbMaxLon = lon;
    var cloudScene = -99;
    var shearDist = -99.0;
    if (curvedBand) {
      if (shear) {
        eyeScene = 3; cloudScene = 4;
        shearDist = cdoShearCalc(field, lat, lon, (BDCurve[2] + BDCurve[3]) / 2.0 + KtoC, 3);
        eyeCDOSize = Math.max(4.0, shearDist);
      } else if (irrCDO) {
        eyeScene = 3; cloudScene = 2;
      } else {
        var foundCB = false;
        var xi;
        if (cbGray) {
          xi = 4;
          while (xi >= 2 && !foundCB) {
            lsa = logSpiral(field, lat, lon, BDCurve[xi] + KtoC, 1).arcs;
            if (lsa >= 8 || xi === 2) {
              if (lsa > 25) {
                if (xi === 4) { cbGray = false; cbBW = true; foundCB = true; }
                else xi = 0;
              } else {
                if (xi === 2 && lsa < 7) {
                  foundCB = false; cbBW = false; shear = true;
                  if (eyeFlt > 1.5 || cloudFlt > 2.5) { shear = false; irrCDO = true; }
                  xi--;
                } else foundCB = true;
              }
            } else xi--;
          }
        }
        if (cbBW) {
          foundCB = false; curvedBand = false;
          xi = 6;
          while (xi > 4 && !foundCB) {
            lsa = logSpiral(field, lat, lon, BDCurve[xi] + KtoC, 1).arcs;
            if (lsa >= 9 && lsa <= 25) foundCB = true;
            else xi--;
          }
        }
        if (foundCB && (cbGray || cbBW)) {
          cbCat = xi; cbAmt = lsa;
          eyeScene = 3; cloudScene = 3;
          var mx = logSpiral(field, lat, lon, BDCurve[cbCat] + KtoC, 2);
          cbMaxAmt = mx.arcs; cbMaxLat = mx.lat; cbMaxLon = mx.lon;
        } else {
          cloudScene = 0; curvedBand = false; embScene = false;
        }
      }
    }
    if (!curvedBand) {
      if (shear) {
        eyeScene = 3; cloudScene = 4;
        shearDist = cdoShearCalc(field, lat, lon, (BDCurve[2] + BDCurve[3]) / 2.0 + KtoC, 3);
        eyeCDOSize = Math.max(4.0, shearDist);
      } else if (cloudScene !== 3) {
        cloudScene = 0;
        if (embScene) cloudScene = 1;
        if (irrCDO) cloudScene = 2;
        if (env.rmwSize > 0 && env.rmwSize < 12.0) eyeScene = 1;   // pinhole
        if (eyeTotal > -0.25 && eyeTotal < 1.50 && eyeCloudCatDiff >= 2 && eyeFFT <= 2 &&
            cwFlt > 6.0 && cloudScene <= 1 && cloudFFT <= 4 && tno12 >= 3.5) {
          eyeScene = 1;   // pinhole
        }
      }
    }
    // CDO size at Dark Gray for CDO-type non-eye scenes [Scene L655-673]
    if (cloudScene <= 2 && eyeScene === 3) {
      eyeCDOSize = cdoShearCalc(field, lat, lon, BDCurve[2] + KtoC, 1);
    }

    rec.eyescene = eyeScene;
    rec.cloudscene = cloudScene;
    rec.eyecdosize = eyeCDOSize;
    rec.ringcb = cbCat;
    rec.ringcbval = cbAmt;
    rec.ringcbvalmax = cbMaxAmt;
    rec.ringcbvalmaxlat = cbMaxLat;
    rec.ringcbvalmaxlon = cbMaxLon;
    rec.eyeFactorTotal = eyeTotal;
    rec.cloudFactorTotal = cloudTotal;
    return rec;
  }

  // [Intensity.java:adt_TnoRaw] — no MW path [departure D5]
  function adtTnoRaw(rec, history, env) {
    var EyeBase = [
      [1.00, 2.00, 3.25, 4.00, 4.75, 5.25, 5.75, 6.50, 7.25, 7.75, 8.25],
      [1.50, 2.25, 3.30, 3.85, 4.50, 4.75, 5.15, 5.50, 6.00, 6.25, 6.75]];
    var CloudBase = [
      [2.00, 2.40, 3.25, 3.50, 3.75, 4.00, 4.10, 4.20, 4.30, 4.40, 4.70],
      [2.05, 2.40, 3.00, 3.20, 3.40, 3.55, 3.65, 3.75, 3.80, 3.90, 4.10]];
    var CurvedBandArr = [1.5, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0];
    var ShearDist = [0.0, 35.0, 50.0, 80.0, 110.0, 140.0];
    var ShearInt = [3.50, 3.00, 2.50, 2.25, 2.00, 1.50];
    var Rule8Adj = [
      [0.0, 0.51, 1.01, 1.71, 2.21, 2.71, 0.0, 0.0, 0.21, 0.51],
      [0.0, 0.51, 1.01, 2.71, 3.21, 3.71, 1.31, 0.0, 0.21, 0.51],
      [0.0, 0.51, 0.71, 1.21, 1.71, 2.21, 0.0, 0.0, 0.21, 0.51]];
    var EyeTDiffFac = [0.011, 0.015];
    var CloudSymFacEYE = [-0.015, -0.015];
    var CDOSizeFac = [0.002, 0.001];
    var CloudSymFacCLD = [-0.030, -0.015];

    var dom = env.domainID === 1 ? 1 : 0;
    var currentTime = rec.timeDays;
    var first48Shear = false;
    var prevCI = 4.0;
    var initT = env.initRawT || 0.0;

    if (!history.length) {
      if (env.initStrengthTF) {
        rec.TrawO = initT;
        rec.rule8 = 0;
        return initT;
      }
    } else {
      if ((currentTime - history[0].timeDays) <= 2.0) first48Shear = true;
      var lastValid = -1e9, prevPtr = 0;
      for (var x = 0; x < history.length; x++) {
        var h = history[x];
        if (h.timeDays > currentTime) break;
        var ok = !((env.landFlag && h.land === 1) || h.Traw < 1.0);
        if (ok) { prevPtr = x; lastValid = h.timeDays; }
      }
      prevCI = history[prevPtr].CI;
      if (currentTime - lastValid > 1.0) {
        // >24 h break -> reinitialize [Intensity L307-318]
        rec.TrawO = initT;
        rec.rule8 = 0;
        rec.reinitialized = true;
        return initT;
      }
    }

    var cloudT = rec.cloudt, eyeT = rec.eyet;
    var cbv = bdCategory(cloudT);
    var cloudCat = cbv.cat;
    var cloudTnoIntensity = (cloudT - BDCurve[cloudCat]) / (BDCurve[cloudCat + 1] - BDCurve[cloudCat]);
    if (rec.eyescene === 1) {
      eyeT = (eyeT + 9.0) / 2.0;   // pinhole eye adjustment [Intensity L347-353]
      rec.eyet = eyeT;
    }

    var est = 0.0, rule8Cat = 0;
    if (rec.cloudscene === 3) {
      // CURVED BAND [Intensity L380-405]
      var amt = Math.min(30, rec.ringcbval + 1);
      var pct = Math.trunc(amt / 5);
      var mult = pct === 1 ? 0.2 : 0.1;
      est = CurvedBandArr[pct] + mult * (amt - pct * 5);
      if (rec.ringcb === 5) est = Math.min(4.0, est + 0.5);
      if (rec.ringcb === 6) est = Math.min(4.5, est + 1.0);
      rule8Cat = 2;
    } else if (rec.cloudscene === 4) {
      // SHEAR [Intensity L406-430]
      est = 1.5;
      var sd = rec.eyecdosize;
      for (x = 0; x < 5; x++) {
        if (sd >= ShearDist[x] && sd < ShearDist[x + 1]) {
          var f = (sd - ShearDist[x]) / (ShearDist[x + 1] - ShearDist[x]);
          est = ShearInt[x] + f * (ShearInt[x + 1] - ShearInt[x]);
          break;
        }
      }
      rule8Cat = 0;
      if (first48Shear) est = Math.min(2.5, est);
    } else {
      if (rec.eyescene <= 2) {
        // EYE [Intensity L434-476]
        var interp = cloudTnoIntensity * (EyeBase[dom][cloudCat + 1] - EyeBase[dom][cloudCat]);
        est = EyeBase[dom][cloudCat] + interp +
              EyeTDiffFac[dom] * (eyeT - cloudT) +
              CloudSymFacEYE[dom] * rec.cloudsymave;
        est = Math.min(est, 9.0);
        if (rec.eyescene === 2) est = Math.min(est - 0.5, 6.5);   // large eye
        rule8Cat = 1;
      } else {
        // CDO family [Intensity L478-547]
        interp = cloudTnoIntensity * (CloudBase[dom][cloudCat + 1] - CloudBase[dom][cloudCat]);
        est = CloudBase[dom][cloudCat] + interp +
              CDOSizeFac[dom] * rec.eyecdosize +
              CloudSymFacCLD[dom] * rec.cloudsymave;
        est -= 0.1;   // bias adjustment
        var adj = 0.0;
        if (rec.cloudscene === 0) {
          if (prevCI >= 4.5) adj = Math.max(0.0, Math.min(1.0, prevCI - 4.5));
          if (prevCI <= 3.0) adj = Math.min(0.0, Math.max(-1.0, prevCI - 3.0));
          est += adj;
        }
        if (rec.cloudscene === 1) est += Math.max(0.0, Math.min(1.5, prevCI - 4.0));
        if (rec.cloudscene === 2) { est += 0.3; est = Math.min(3.5, Math.max(2.5, est)); }
        rule8Cat = 2;
      }
    }

    var finalEst = Math.trunc((est + 0.01) * 10.0) / 10.0;
    rec.TrawO = finalEst;

    // ---- Rule 8 [Intensity L593-953], MW-off form ----
    rec.rule8 = rule8Cat * 10;
    if (history.length) {
      var prevFinal = finalEst;
      var t1 = finalEst, t6 = finalEst, t12v = finalEst, t18 = finalEst, t24 = finalEst;
      var raw6 = finalEst, raw6t = currentTime, raw1 = finalEst;
      var got1 = false, got6 = false, got12 = false, got18 = false, got24 = false;
      var eyeCnt = 0, nonEyeCnt = 0, veldenHist = false, applyVelden = true;
      var prevRule9 = 0, prevRapid = 0;
      var first6hr = history[0].timeDays >= currentTime - 0.26;
      for (x = 0; x < history.length; x++) {
        h = history[x];
        if (h.timeDays >= currentTime) break;
        ok = !((env.landFlag && h.land === 1) || h.Traw < 1.0);
        if (h.timeDays >= currentTime - 1.01 && !got24 && ok) { got24 = true; t24 = h.Tfinal; }
        if (h.timeDays >= currentTime - 0.76 && !got18 && ok) { got18 = true; t18 = h.Tfinal; }
        if (h.timeDays >= currentTime - 0.51 && !got12 && ok) { got12 = true; t12v = h.Tfinal; }
        if (h.timeDays >= currentTime - 0.26 && !got6 && ok) {
          got6 = true; t6 = h.Tfinal; raw6 = h.Traw; raw6t = h.timeDays;
        }
        if (h.timeDays >= currentTime - 0.05 && !got1 && ok) { got1 = true; t1 = h.Tfinal; raw1 = h.Traw; }
        if (ok) {
          prevFinal = h.Tfinal; prevRule9 = h.rule9; prevRapid = h.rapiddiss;
          if (h.eyescene <= 2) {
            eyeCnt++; nonEyeCnt = 0;
            if (eyeCnt >= 3 || veldenHist) { applyVelden = false; veldenHist = true; }
          } else {
            eyeCnt = 0; nonEyeCnt++;
            if (nonEyeCnt >= 3) { applyVelden = true; veldenHist = false; }
          }
          if (prevRapid >= 2) applyVelden = false;
        }
      }
      var R = Rule8Adj[rule8Cat];
      if (prevFinal < 4.0) {
        if (first6hr) {
          if (got1) {
            if (Math.abs(raw1 - finalEst) > R[8]) {
              finalEst = Math.max(raw1 - R[8], Math.min(raw1 + R[8], finalEst));
              rec.rule8 = rule8Cat * 10 + 8;
            }
          } else {
            var d1h = 0.1 * (Math.abs(currentTime - raw6t) / 0.0416);
            var lo = raw6 - d1h, hi = raw6 + d1h;
            if (finalEst > hi || finalEst < lo) {
              finalEst = Math.max(lo, Math.min(hi, finalEst));
              rec.rule8 = rule8Cat * 10 + 8;
            }
          }
        } else {
          if (Math.abs(t1 - finalEst) > R[9] && got1 && applyVelden) {
            finalEst = Math.max(t1 - R[9], Math.min(t1 + R[9], finalEst));
            rec.rule8 = rule8Cat * 10 + 9;
          }
          var lim = prevRule9 < 2 ? R[2] : R[1];
          var slot = prevRule9 < 2 ? 2 : 1;
          if (Math.abs(t6 - finalEst) > lim && got6) {
            finalEst = Math.max(t6 - lim, Math.min(t6 + lim, finalEst));
            rec.rule8 = rule8Cat * 10 + slot;
          }
        }
      } else {
        if (Math.abs(t1 - finalEst) > R[9] && got1 && applyVelden) {
          finalEst = Math.max(t1 - R[9], Math.min(t1 + R[9], finalEst));
          rec.rule8 = rule8Cat * 10 + 9;
        }
        if (Math.abs(t6 - finalEst) > R[2] && got6) {
          finalEst = Math.max(t6 - R[2], Math.min(t6 + R[2], finalEst));
          rec.rule8 = rule8Cat * 10 + 2;
        } else if (Math.abs(t12v - finalEst) > R[3] && got12) {
          finalEst = Math.max(t12v - R[3], Math.min(t12v + R[3], finalEst));
          rec.rule8 = rule8Cat * 10 + 3;
        } else if (Math.abs(t18 - finalEst) > R[4] && got18) {
          finalEst = Math.max(t18 - R[4], Math.min(t18 + R[4], finalEst));
          rec.rule8 = rule8Cat * 10 + 4;
        } else if (Math.abs(t24 - finalEst) > R[5] && got24) {
          finalEst = Math.max(t24 - R[5], Math.min(t24 + R[5], finalEst));
          rec.rule8 = rule8Cat * 10 + 5;
        }
      }
    }
    return finalEst;
  }

  // [Intensity.java:adt_TnoFinal] TimeAvgDurationID=1 => 3 h straight average
  function adtTnoFinal(rec, history, timeAvgID) {
    var baseHrs = timeAvgID === 1 ? 3.0 : (timeAvgID === 2 ? 12.0 : 6.0);
    var currentTime = rec.timeDays;
    var begin = currentTime - baseHrs / 24.0;
    var sum = 0, wsum = 0, found = false;
    for (var x = 0; x < history.length; x++) {
      var h = history[x];
      if (h.timeDays >= begin && h.timeDays < currentTime) {
        var avg = h.Traw;
        var ok = !((false) || avg < 1.0);   // landFlag handled upstream via Traw<1
        if (h.land === 1) ok = false;
        if (ok) {
          var w = timeAvgID === 0
            ? (baseHrs - (currentTime - h.timeDays) / (1.0 / 24.0))
            : baseHrs;
          sum += w * avg; wsum += w; found = true;
        }
      } else if (found) break;
    }
    if (found) {
      sum += baseHrs * rec.Traw;
      wsum += baseHrs;
      return Math.trunc(((sum / wsum) + 0.01) * 10.0) / 10.0;
    }
    return rec.Traw;
  }

  // [Intensity.java:adt_CIno] Rule 9 + rapid dissipation (slope: departure D4)
  function adtCIno(rec, history, env) {
    if (!history.length) {
      var s0 = 0;
      if ((env.initRawT || 0) >= 6.0) s0 = 2;
      rec.rapiddiss = 0;
      return { CI: rec.Traw, rule9: s0 };
    }
    var currentTime = rec.timeDays;
    var max6 = 0.0, prevCI = 0.0, prevRule9 = 0, prevRapid = 0, prevRapidMin = 99;
    var landOnly = true;
    var count = 0;
    for (var x = 0; x < history.length; x++) {
      var h = history[x];
      if (h.timeDays >= currentTime) break;
      count++;
      var ok = !((env.landFlag && h.land === 1) || h.Traw < 1.0);
      if (ok) {
        prevCI = h.CI; prevRule9 = h.rule9r || 0; prevRapid = h.rapiddiss;
        if (h.timeDays >= currentTime - 0.25) {
          if (h.Tfinal > max6) max6 = h.Tfinal;
          landOnly = false;
          if (prevRapid < prevRapidMin) prevRapidMin = prevRapid;
        }
      }
    }
    if (count === 0) { rec.rapiddiss = 0; return { CI: rec.Tfinal, rule9: 0 }; }

    // rapid dissipation via the 6-h Final-T# slope per 24 h [departure D4]
    var slope6 = slopePer24h(history, currentTime, 0.25);
    var eastPac = env.basinID === 2;
    var rapid = 0, rule9Add = 1.0;
    if (prevRapid <= 1) {
      rapid = 0;
      if ((!eastPac && slope6 >= 2.0) || (eastPac && slope6 >= 1.5)) rapid = 1;
      if (prevRapidMin === 1 && rapid === 1) { rule9Add = 0.5; rapid = 2; }
    } else {
      rule9Add = 0.5; rapid = 2;
      if ((!eastPac && slope6 < 1.5) || (eastPac && slope6 < 1.0)) rapid = 3;
      if (prevRapidMin === 3 && rapid === 3) { rule9Add = 1.0; rapid = 0; }
    }

    var curT = rec.Tfinal;
    var ci = Math.min(curT + rule9Add, Math.max(max6, curT));
    var rule9 = prevRule9;
    if (ci > curT) rule9 = 1;
    if (prevRule9 === 1 && prevCI <= curT) rule9 = 0;
    if (landOnly) { rule9 = 0; ci = rec.Traw; rapid = 0; }
    rec.rapiddiss = rapid;
    return { CI: ci, rule9: rule9 };
  }

  // decline of Final T# over the trailing window extrapolated to /24 h;
  // positive = weakening. Stand-in for Functions.adt_slopecal [departure D4].
  function slopePer24h(history, currentTime, windowDays) {
    var oldest = null, newest = null;
    for (var x = 0; x < history.length; x++) {
      var h = history[x];
      if (h.timeDays >= currentTime - windowDays && h.timeDays < currentTime) {
        if (!oldest) oldest = h;
        newest = h;
      }
    }
    if (!oldest || !newest || newest.timeDays <= oldest.timeDays) return 0.0;
    return (oldest.Tfinal - newest.Tfinal) / (newest.timeDays - oldest.timeDays) / 1.0;
  }

  // ---- one-frame ADT orchestration ----------------------------------------
  // field: KELVIN grid; center {lat,lon}; history: prior records oldest-first;
  // env: { domainID (0 Atl / 1 Pac), basinID (2=EPac for the relaxed rapid-
  //        weakening bound), landFlag, isLand(lat,lon)|null, initRawT,
  //        initStrengthTF, rmwSize }
  function adtEstimate(field, center, timeMs, history, env) {
    env = env || {};
    var rec = {
      timeDays: timeMs / 86400000.0,
      latitude: center.lat, longitude: center.lon,
      land: env.isLand && env.isLand(center.lat, center.lon) ? 1 : 0
    };
    var stats = calcEyeCloudTemps(field, center.lat, center.lon);
    rec.eyet = stats.eyet; rec.cwcloudt = stats.cwcloudt; rec.cwring = stats.cwring;
    rec.cloudt = stats.cloudt; rec.cloudt2 = stats.cloudt2;
    rec.cloudsymave = stats.cloudsymave; rec.eyestdv = stats.eyestdv;
    rec.eyefft = stats.eyefft; rec.cloudfft = stats.cloudfft;

    determineSceneType(field, rec, history, env);
    rec.Traw = adtTnoRaw(rec, history, env);
    rec.Tfinal = adtTnoFinal(rec, history, 1);
    var ci = adtCIno(rec, history, env);
    rec.CI = ci.CI;
    rec.rule9r = ci.rule9;
    rec.vmax = ciToVmax(rec.CI);
    rec.mslp = ciToMslp(rec.CI, env.domainID);
    return rec;
  }

  var SCENE_NAMES = {
    eye: ['EYE', 'PINHOLE EYE', 'LARGE EYE', 'NO EYE'],
    cloud: ['UNIFORM CDO', 'EMBEDDED CENTER', 'IRREGULAR CDO', 'CURVED BAND', 'SHEAR']
  };
  // Guide §3H1 skill tiers, surfaced with every estimate (honesty contract)
  function sceneSkill(rec) {
    if (rec.eyescene <= 2) return { tier: 'moderate', note: 'eye scene — regression r≈0.70 vs recon (ADT Guide §3H1)' };
    return { tier: 'low', note: 'cloud scene — regression r≈0.50 vs recon; CDO is the weakest scene (ADT Guide §3H1)' };
  }

  // Dvorak table inverse: official Vmax -> nearest T# (loop seeding only)
  function vmaxToTno(vmaxKt) {
    if (vmaxKt == null || !(vmaxKt > 0)) return 0.0;
    var best = 1.0, bd = Infinity;
    for (var i = 0; i < PW_Wind.length; i++) {
      var d = Math.abs(PW_Wind[i] - vmaxKt);
      if (d < bd) { bd = d; best = PW_Tno[i]; }
    }
    return best;
  }

  var ObjFix = {
    // ARCHER
    archerFix: archerFix,
    confidenceToAlpha: confidenceToAlpha,
    certaintyRadii: certaintyRadii,
    // ADT
    adtEstimate: adtEstimate,
    calcEyeCloudTemps: calcEyeCloudTemps,
    determineSceneType: determineSceneType,
    adtTnoRaw: adtTnoRaw,
    adtTnoFinal: adtTnoFinal,
    adtCIno: adtCIno,
    logSpiral: logSpiral,
    cdoShearCalc: cdoShearCalc,
    calculateFFT: calculateFFT,
    bdCategory: bdCategory,
    ciToVmax: ciToVmax,
    ciToMslp: ciToMslp,
    vmaxToTno: vmaxToTno,
    SCENE_NAMES: SCENE_NAMES,
    sceneSkill: sceneSkill,
    // shared internals exposed for tests
    _internal: {
      distanceDeg: distanceDeg,
      distanceAngle: distanceAngle,
      distanceAngle2: distanceAngle2,
      npGradient2D: npGradient2D,
      bilinear: bilinear,
      qualityCheck: qualityCheck,
      comboPartsCalc: comboPartsCalc,
      dilateNaN: dilateNaN,
      interp1: interp1
    }
  };

  if (typeof window !== 'undefined') window.ObjFix = ObjFix;
  if (typeof self !== 'undefined' && typeof window === 'undefined') self.ObjFix = ObjFix;
  if (typeof module !== 'undefined' && module.exports) module.exports = ObjFix;
})();
