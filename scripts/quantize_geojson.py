#!/usr/bin/env python3
"""Quantize + strip the committed Natural Earth basemap GeoJSONs in place.

The ne_* files are the site's basemap substitute (no cartopy) and ship to
EVERY viewer — but upstream copies carry full double precision (15+ digit
coordinates) and rich per-feature attribute tables that nothing reads
(models/regions.js and the explorer draw geometry only). Rounding
coordinates to 3 decimals (~110 m — far below one screen pixel at any zoom
these vectors draw at) and emptying properties cuts the decompressed
payload roughly in half, which is a direct page-weight/parse-time win on
models + the explorer (the coastline alone was ~10 MB decompressed).

Idempotent; run after re-downloading any upstream copy:
    python scripts/quantize_geojson.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = [
    "ne_10m_coastline.geojson",
    "ne_110m_coastline.geojson",
    "ne_50m_coastline.geojson",
    "ne_50m_admin_0_countries.geojson",
    "ne_110m_admin_0_countries.geojson",
    "ne_50m_admin_1_states_provinces.geojson",
    "ne_50m_admin_0_boundary_lines_land.geojson",
    "ne_50m_admin_1_states_provinces_lines.geojson",
]


def _q(coords):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], 3), round(coords[1], 3)]
    return [_q(c) for c in coords]


def main() -> int:
    for name in FILES:
        p = ROOT / name
        if not p.exists():
            print(f"[skip] {name} (absent)")
            continue
        before = p.stat().st_size
        d = json.loads(p.read_text())
        for f in d.get("features", []):
            f["properties"] = {}
            f.pop("bbox", None)
            g = f.get("geometry")
            if g and "coordinates" in g:
                g["coordinates"] = _q(g["coordinates"])
        d.pop("bbox", None)
        p.write_text(json.dumps(d, separators=(",", ":")))
        after = p.stat().st_size
        print(f"[ok] {name}: {before/1e6:.1f} MB -> {after/1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
