/* GENERATED FILE - DO NOT EDIT.
 * Source of truth: palette/tat_palettes/categories.py
 * Regenerate:      python -m tat_palettes.emit --out-dir .
 * A hand-edit here is caught by tests/test_category_palette_ssot.py.
 *
 * The canonical Saffir-Simpson (SSHWS) category palette for every Triple-A-
 * Tropics browser surface: the home/tracks maps, CycloLab (track history, wind
 * history, guidance spaghetti, diagnostic bands), the season animation, recon,
 * models/enscenters, the records explorer and the active banner.
 *
 * Load this BEFORE any consumer. Pages use ordered `defer` script tags; the
 * CycloLab shell chains it ahead of its lazy component loads. Consumers hold no
 * fallback copy on purpose - a stale local ramp is the bug this file fixes, so
 * a missing palette must fail visibly rather than quietly render last year's
 * colors.
 */
(function (root) {
  'use strict';

  var CATS = {
    TD: '#6eebf9', TS: '#9cf94d', C1: '#fdfc53', C2: '#f1af3d',
    C3: '#e63222', C4: '#e732f4', C5: '#f6c5fb'
  };
  var INK = {
    TD: '#0a1324', TS: '#0a1324', C1: '#0a1324', C2: '#0a1324',
    C3: '#ffffff', C4: '#0a1324', C5: '#0a1324'
  };
  var LABELS = {
    TD: 'Depression', TS: 'Tropical Storm', C1: 'Category 1', C2: 'Category 2',
    C3: 'Category 3', C4: 'Category 4', C5: 'Category 5'
  };
  var GLYPHS = {
    TD: 'D', TS: 'S', C1: '1', C2: '2',
    C3: '3', C4: '4', C5: '5'
  };
  var MIN_KT = {
    TD: 0, TS: 34, C1: 64, C2: 83,
    C3: 96, C4: 113, C5: 137
  };
  var MAX_KT = {
    TD: 33, TS: 63, C1: 82, C2: 95,
    C3: 112, C4: 136, C5: null
  };
  var ORDER = ['TD', 'TS', 'C1', 'C2', 'C3', 'C4', 'C5'];
  var UNKNOWN = 'TD';
  var STEPS = [
    [0, '#6eebf9'],
    [34, '#9cf94d'],
    [64, '#fdfc53'],
    [83, '#f1af3d'],
    [96, '#e63222'],
    [113, '#e732f4'],
    [137, '#f6c5fb']
  ];
  /* Fine obs wind ramp (recon SFMR / flight-level barbs, ASCAT). Hard bins:
     a speed takes the LAST bin whose minKt it meets. Category-exact at every
     SSHWS threshold; the in-between bins blend toward the next category. */
  var WIND_RAMP = [
    [0, '#6eebf9'], [10, '#75eddd'], [20, '#7df0c1'], [30, '#84f2a6'],
    [34, '#9cf94d'], [40, '#a7f94e'], [45, '#b0fa4e'], [50, '#b8fa4f'],
    [55, '#c1fa4f'], [60, '#cafa50'], [64, '#fdfc53'], [83, '#f1af3d'],
    [96, '#e63222'], [113, '#e732f4'], [137, '#f6c5fb']
  ];

  function windColor(kt) {
    if (kt === null || kt === undefined || isNaN(kt)) return WIND_RAMP[0][1];
    var out = WIND_RAMP[0][1];
    for (var i = 0; i < WIND_RAMP.length; i++) {
      if (kt >= WIND_RAMP[i][0]) out = WIND_RAMP[i][1]; else break;
    }
    return out;
  }

  /* kt (1-min sustained) -> class code. null/NaN -> UNKNOWN (the weakest
     class), matching tat_palettes.categories.category_for_kt exactly. */
  function catForKt(kt) {
    if (kt === null || kt === undefined || isNaN(kt)) return UNKNOWN;
    for (var i = ORDER.length - 1; i >= 0; i--) {
      if (kt >= MIN_KT[ORDER[i]]) return ORDER[i];
    }
    return UNKNOWN;
  }

  function colorForKt(kt) { return CATS[catForKt(kt)]; }
  function inkForKt(kt) { return INK[catForKt(kt)]; }

  /* Flat maplibre `["step", input, <default>, stop, color, ...]` tail:
     the TD color followed by (minKt, color) for every category above it. */
  function stepExpr() {
    var out = [CATS[ORDER[0]]];
    for (var i = 1; i < ORDER.length; i++) {
      out.push(MIN_KT[ORDER[i]], CATS[ORDER[i]]);
    }
    return out;
  }

  var P = {
    cats: CATS, ink: INK, labels: LABELS, glyphs: GLYPHS,
    minKt: MIN_KT, maxKt: MAX_KT, order: ORDER, unknown: UNKNOWN,
    steps: STEPS, catForKt: catForKt, colorForKt: colorForKt,
    inkForKt: inkForKt, stepExpr: stepExpr,
    windRamp: WIND_RAMP, windColor: windColor
  };
  if (Object.freeze) {
    Object.freeze(CATS); Object.freeze(INK); Object.freeze(LABELS);
    Object.freeze(GLYPHS); Object.freeze(MIN_KT); Object.freeze(MAX_KT);
    Object.freeze(ORDER); Object.freeze(WIND_RAMP); Object.freeze(P);
  }
  root.TATPalette = P;
})(typeof globalThis !== 'undefined' ? globalThis : this);
