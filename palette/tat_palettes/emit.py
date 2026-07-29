#!/usr/bin/env python3
"""Emit the browser-side mirrors of :mod:`tat_palettes.categories`.

The site's JavaScript cannot import a Python package, so the category palette
crosses the language boundary as two GENERATED, committed files served from the
site root:

  ``tat_palette.js``   -> ``window.TATPalette`` (classic script, no module
                          system, no build step - the house pattern)
  ``tat_palette.css``  -> ``:root { --cat-td: ...; --cat-td-ink: ...; }``

They are generated rather than hand-mirrored deliberately. The repo already
carries several hand-maintained "byte-identical mirror" pairs (the tracks SVG
builders, for one) and they hold only because a test compares them; a palette
mirror is pure data, so it can be generated outright and the test reduces to
"the committed file equals a fresh emit". Regenerate with::

    python -m tat_palettes.emit --out-dir .

``tests/test_category_palette_ssot.py`` runs that emit into a temp dir and
diffs, so a hand-edit of either generated file - or a recolor in
``categories.py`` without a regenerate - fails the suite.

Why a runtime global instead of inlining the colors into each consumer: the
requirement is that a category hex appears NOWHERE outside this package, and an
inlined copy is exactly the drift this consolidation exists to kill. The cost is
a load-order dependency (``tat_palette.js`` must be loaded before any consumer
draws), which the pages satisfy with ordered ``defer`` script tags and the tsr
CycloLab shell satisfies by chaining it ahead of its lazy component loads.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .categories import (CATEGORY_GLYPH, CATEGORY_HEX, CATEGORY_INK,
                         CATEGORY_LABEL, CATEGORY_MAX_KT, CATEGORY_MIN_KT,
                         CATEGORY_ORDER, UNKNOWN_CATEGORY, wind_ramp)

JS_NAME = "tat_palette.js"
CSS_NAME = "tat_palette.css"

_BANNER = (
    "GENERATED FILE - DO NOT EDIT.\n"
    " * Source of truth: palette/tat_palettes/categories.py\n"
    " * Regenerate:      python -m tat_palettes.emit --out-dir .\n"
    " * A hand-edit here is caught by tests/test_category_palette_ssot.py."
)


def _js_obj(mapping, keys, quote=True):
    """Render ``{ TD: 'x', TS: 'y' }`` in CATEGORY_ORDER, one line per 4 keys."""
    parts = []
    for k in keys:
        v = mapping[k]
        if v is None:
            parts.append(f"{k}: null")
        elif quote:
            parts.append(f"{k}: '{v}'")
        else:
            parts.append(f"{k}: {v}")
    lines, row = [], []
    for i, p in enumerate(parts):
        row.append(p)
        if len(row) == 4 or i == len(parts) - 1:
            lines.append("    " + ", ".join(row))
            row = []
    return "{\n" + ",\n".join(lines) + "\n  }"


def render_js() -> str:
    order = ", ".join(f"'{c}'" for c in CATEGORY_ORDER)
    # Weakest-first (minKt, hex) pairs, pre-rendered so consumers never rebuild
    # the ramp from the tables and get the boundary convention subtly wrong.
    steps = ",\n".join(
        f"    [{CATEGORY_MIN_KT[c]}, '{CATEGORY_HEX[c]}']" for c in CATEGORY_ORDER)
    ramp_pairs = wind_ramp()
    ramp_rows, row = [], []
    for i, (kt, hx) in enumerate(ramp_pairs):
        row.append(f"[{kt}, '{hx}']")
        if len(row) == 4 or i == len(ramp_pairs) - 1:
            ramp_rows.append("    " + ", ".join(row))
            row = []
    ramp = ",\n".join(ramp_rows)
    return f"""/* {_BANNER}
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
(function (root) {{
  'use strict';

  var CATS = {_js_obj(CATEGORY_HEX, CATEGORY_ORDER)};
  var INK = {_js_obj(CATEGORY_INK, CATEGORY_ORDER)};
  var LABELS = {_js_obj(CATEGORY_LABEL, CATEGORY_ORDER)};
  var GLYPHS = {_js_obj(CATEGORY_GLYPH, CATEGORY_ORDER)};
  var MIN_KT = {_js_obj(CATEGORY_MIN_KT, CATEGORY_ORDER, quote=False)};
  var MAX_KT = {_js_obj(CATEGORY_MAX_KT, CATEGORY_ORDER, quote=False)};
  var ORDER = [{order}];
  var UNKNOWN = '{UNKNOWN_CATEGORY}';
  var STEPS = [
{steps}
  ];
  /* Fine obs wind ramp (recon SFMR / flight-level barbs, ASCAT). Hard bins:
     a speed takes the LAST bin whose minKt it meets. Category-exact at every
     SSHWS threshold; the in-between bins blend toward the next category. */
  var WIND_RAMP = [
{ramp}
  ];

  function windColor(kt) {{
    if (kt === null || kt === undefined || isNaN(kt)) return WIND_RAMP[0][1];
    var out = WIND_RAMP[0][1];
    for (var i = 0; i < WIND_RAMP.length; i++) {{
      if (kt >= WIND_RAMP[i][0]) out = WIND_RAMP[i][1]; else break;
    }}
    return out;
  }}

  /* kt (1-min sustained) -> class code. null/NaN -> UNKNOWN (the weakest
     class), matching tat_palettes.categories.category_for_kt exactly. */
  function catForKt(kt) {{
    if (kt === null || kt === undefined || isNaN(kt)) return UNKNOWN;
    for (var i = ORDER.length - 1; i >= 0; i--) {{
      if (kt >= MIN_KT[ORDER[i]]) return ORDER[i];
    }}
    return UNKNOWN;
  }}

  function colorForKt(kt) {{ return CATS[catForKt(kt)]; }}
  function inkForKt(kt) {{ return INK[catForKt(kt)]; }}

  /* Flat maplibre `["step", input, <default>, stop, color, ...]` tail:
     the TD color followed by (minKt, color) for every category above it. */
  function stepExpr() {{
    var out = [CATS[ORDER[0]]];
    for (var i = 1; i < ORDER.length; i++) {{
      out.push(MIN_KT[ORDER[i]], CATS[ORDER[i]]);
    }}
    return out;
  }}

  var P = {{
    cats: CATS, ink: INK, labels: LABELS, glyphs: GLYPHS,
    minKt: MIN_KT, maxKt: MAX_KT, order: ORDER, unknown: UNKNOWN,
    steps: STEPS, catForKt: catForKt, colorForKt: colorForKt,
    inkForKt: inkForKt, stepExpr: stepExpr,
    windRamp: WIND_RAMP, windColor: windColor
  }};
  if (Object.freeze) {{
    Object.freeze(CATS); Object.freeze(INK); Object.freeze(LABELS);
    Object.freeze(GLYPHS); Object.freeze(MIN_KT); Object.freeze(MAX_KT);
    Object.freeze(ORDER); Object.freeze(WIND_RAMP); Object.freeze(P);
  }}
  root.TATPalette = P;
}})(typeof globalThis !== 'undefined' ? globalThis : this);
"""


def render_css() -> str:
    swatches = "\n".join(
        f"  --cat-{c.lower()}: {CATEGORY_HEX[c]};" for c in CATEGORY_ORDER)
    inks = "\n".join(
        f"  --cat-{c.lower()}-ink: {CATEGORY_INK[c]};" for c in CATEGORY_ORDER)
    return f"""/* {_BANNER}
 *
 * Canonical SSHWS category custom properties. Link this before any stylesheet
 * that colors a category chip, badge or track, then use var(--cat-c3) /
 * var(--cat-c3-ink) - never a literal hex.
 */
:root {{
{swatches}

{inks}
}}
"""


def write(out_dir: Path) -> list[Path]:
    """Write both generated files into ``out_dir``. Returns the paths written."""
    out_dir = Path(out_dir)
    written = []
    for name, body in ((JS_NAME, render_js()), (CSS_NAME, render_css())):
        path = out_dir / name
        path.write_text(body, encoding="utf-8")
        written.append(path)
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--out-dir", default=None,
        help="directory to write tat_palette.js / .css into (default: the "
             "Triple-A-Tropics repo root inferred from this file's location, "
             "which only works from a source checkout)")
    args = ap.parse_args(argv)
    if args.out_dir is None:
        # palette/tat_palettes/emit.py -> palette/tat_palettes -> palette -> root
        root = Path(__file__).resolve().parents[2]
        if not (root / "styles.css").exists():
            print("cannot infer the repo root (running from an installed "
                  "copy?) - pass --out-dir explicitly", file=sys.stderr)
            return 2
        out_dir = root
    else:
        out_dir = Path(args.out_dir)
    for path in write(out_dir):
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
