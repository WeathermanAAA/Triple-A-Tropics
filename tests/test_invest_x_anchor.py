"""Invest-X anchoring + same-stage marker rendering, on BOTH map paths.

THE ANCHOR RULE: the X crosshair centre sits EXACTLY on the invest's fix
pixel; the designation label ("91W") is an OFFSET SIBLING that can never
shift the X. The 2026-06-07 bug: the global page's HTML marker gave the
label room INSIDE the SVG viewBox ("-22 -16 92 32") while MapLibre
anchored the element's CENTER on the fix — so the X (at viewBox 0,0,
i.e. 24px left of the box centre) rendered 24px west of the fix, which
visually stacked 92E onto TWO-E.

Two render paths are pinned:

  * per-basin SVG (render_tracks_svg + LIVE_BASIN_JS buildTracksSvg):
    the X <path> lives in a <g transform="translate(fix_px)"> centred on
    (0,0); the label is a sibling <text> at fix_px + (11, 4) OUTSIDE
    that group.
  * global MapLibre page (GLOBAL_MAPLIBRE_HTML addActiveMarkers): the
    marker element is anchored "center", its CSS box equals its viewBox
    (1 SVG unit == 1 CSS px), the viewBox is symmetric about (0,0), and
    the X path is centred on (0,0) — therefore X centre == element
    centre == projected fix. The label <text x="11"> overflows the box
    (overflow:visible) exactly like .hurricane-name does.

Also pinned here (global render path of THE STAGE RULE — the
classification side lives in test_marker_type_agreement.py): a
"hurricane" marker and a LEGACY "td_circle" marker with the same
current_category render byte-identical glyphs modulo the name — the
poller repin gap must not resurrect the retired ring.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from overlay_test_util import NODE, gtp, run_harness  # noqa: E402

GLOBAL_HARNESS = Path(__file__).resolve().parent / "global_map_harness.cjs"

X_PATH_D = "M -7 -7 L 7 7 M -7 7 L 7 -7"


def _path_centroid(d: str) -> tuple[float, float]:
    """Centroid of all coordinate pairs in a (M/L-only) path data string."""
    nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", d)]
    xs, ys = nums[0::2], nums[1::2]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _invest_storm(sid: str, atcf_id: str, lat: float, lon: float) -> dict:
    return {
        "sid": sid, "name": "INVEST", "atcf_id": atcf_id, "basin": "ep",
        "is_active": True, "is_invest": True,
        "peak_wind_kt": 25.0, "peak_pressure_mb": 1006.0,
        "max_category": "TD", "current_category": "TD",
        "ace": 0.0, "start": "2026-06-07T00:00:00",
        "end": "2026-06-07T06:00:00",
        "points": [
            {"t": "2026-06-07T00:00:00", "lat": lat - 0.5, "lon": lon + 0.5,
             "wind_kt": 20.0, "pressure_mb": 1007.0, "cls": "TD",
             "nature": "DB"},
            {"t": "2026-06-07T06:00:00", "lat": lat, "lon": lon,
             "wind_kt": 25.0, "pressure_mb": 1006.0, "cls": "TD",
             "nature": "DB"},
        ],
    }


class TestPerBasinInvestXAnchor(unittest.TestCase):
    """Per-basin SVG path: X group translated to the fix px, centred
    path, label a sibling <text> offset (+11, +4) outside the group."""

    BASIN = "ep"
    LAT, LON = 11.3, -88.4   # the real 92E fix that exposed the bug

    def _expected_px(self) -> tuple[float, float]:
        extent = gtp.BASINS[self.BASIN]["extent"]
        project, _ = gtp.build_projection(extent, gtp.MAP_W, gtp.MAP_H)
        return project(self.LON, self.LAT)

    def _assert_anchored(self, svg: str, label: str):
        x, y = self._expected_px()
        g_re = re.compile(
            r'<g class="invest-current" '
            r'transform="translate\(([-\d.]+),([-\d.]+)\)" '
            r'filter="url\(#invest-red-glow\)">(.*?)</g>', re.S)
        m = g_re.search(svg)
        self.assertIsNotNone(m, "invest-current group missing")
        gx, gy, g_body = float(m.group(1)), float(m.group(2)), m.group(3)
        # 1. The group lands on the projected fix pixel.
        self.assertAlmostEqual(gx, x, places=1)
        self.assertAlmostEqual(gy, y, places=1)
        # 2. The X path inside is centred on the group origin (0,0) —
        #    centroid computed from the RENDERED path data, so a marker
        #    whose crosshair drifts off its anchor fails here.
        dm = re.search(r'<path class="track-dot" d="([^"]+)"', g_body)
        self.assertIsNotNone(dm, "X path missing from invest group")
        self.assertEqual(_path_centroid(dm.group(1)), (0.0, 0.0),
                         "rendered X path is not centred on the anchor")
        self.assertEqual(dm.group(1), X_PATH_D)
        # 3. The label is an OFFSET SIBLING outside the group, at
        #    fix + (11, 4) — it shares no geometry with the X.
        self.assertNotIn("invest-label", g_body)
        lbl_re = re.compile(
            r'<text class="invest-label" x="([-\d.]+)" y="([-\d.]+)" '
            r'text-anchor="start">' + re.escape(label) + r'</text>')
        lm = lbl_re.search(svg)
        self.assertIsNotNone(lm, "invest label missing/not a sibling text")
        self.assertAlmostEqual(float(lm.group(1)), x + 11, places=1)
        self.assertAlmostEqual(float(lm.group(2)), y + 4, places=1)

    def test_python_renderer_anchors_x_on_fix(self):
        storm = _invest_storm("XTEST", "92E", self.LAT, self.LON)
        svg = gtp.render_tracks_svg([storm],
                                    gtp.BASINS[self.BASIN]["extent"])
        self._assert_anchored(svg, "92E")

    def test_label_length_cannot_shift_x(self):
        short = _invest_storm("XSHORT", "92E", self.LAT, self.LON)
        long = _invest_storm("XLONG", "INVEST-LONGLABEL", self.LAT, self.LON)
        extent = gtp.BASINS[self.BASIN]["extent"]
        svg_s = gtp.render_tracks_svg([short], extent)
        svg_l = gtp.render_tracks_svg([long], extent)
        g_re = re.compile(r'<g class="invest-current" transform="([^"]+)"')
        self.assertEqual(g_re.search(svg_s).group(1),
                         g_re.search(svg_l).group(1),
                         "label width moved the X anchor")

    @unittest.skipIf(NODE is None, "node not on PATH")
    def test_js_overlay_anchors_x_on_fix(self):
        storm = _invest_storm("XTEST", "92E", self.LAT, self.LON)
        payload = {
            "storms": [json.loads(json.dumps(storm))], "year": 2026,
            "header": {"named": 0, "cat1plus": 0, "cat3plus": 0, "cat5": 0,
                       "total_ace": 0.0},
            "vocab": gtp.BASINS[self.BASIN]["vocab"],
        }
        svg = run_harness(self.BASIN, payload)["tracks"]
        self._assert_anchored(svg, "92E")


def _marker_feature(marker_type: str, name: str, lon: float, lat: float,
                    **extra) -> dict:
    props = {
        "kind": "active_marker", "storm_id": f"SID_{name}", "name": name,
        "designation": name, "marker_type": marker_type,
        "current_intensity_kt": 30.0, "current_category": "TD",
        "current_mslp_mb": 1005, "last_fix": "2026-06-07T12:00:00",
    }
    props.update(extra)
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": props,
    }


@unittest.skipIf(NODE is None, "node not on PATH")
class TestGlobalMarkerRender(unittest.TestCase):
    """Global MapLibre path, rendered through the REAL page (jsdom +
    stubbed maplibre-gl). One page load serves every assertion."""

    FEATURES = [
        _marker_feature("invest_x", "92E", -88.4, 11.3),
        _marker_feature("invest_x", "INVEST-LONGLABEL", 140.0, 20.0),
        # LEGACY pre-0.4.0 type — must render as the unified X.
        _marker_feature("L", "91W", 133.3, 32.0),
        # Same current stage (TD), three classification histories:
        # current-rule glyph, LEGACY td_circle (pre-0.5.0 poller), and a
        # minimal-properties brand-new storm. All must wear the D glyph.
        _marker_feature("hurricane", "AMANDA", -135.2, 11.6),
        _marker_feature("td_circle", "TWO-E", -100.0, 15.4),
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [-120.0, 14.0]},
         "properties": {"kind": "active_marker", "storm_id": "SID_MIN",
                        "name": "MINIMAL", "marker_type": "hurricane"}},
    ]

    @classmethod
    def setUpClass(cls):
        payload = {
            "year": 2026, "updated": "2026-06-07 12:00 UTC",
            "header": {"named": 0, "cat1plus": 0, "cat3plus": 0, "cat5": 0,
                       "total_ace": 0.0},
            "vocab": gtp.BASINS["global"]["vocab"],
        }
        page = gtp._apply_icon_tokens(
            gtp.render_global_maplibre_html(payload))
        geojson = {"type": "FeatureCollection", "features": cls.FEATURES}
        with tempfile.TemporaryDirectory() as td:
            page_path = Path(td) / "global_tracks.html"
            page_path.write_text(page, encoding="utf-8")
            gj_path = Path(td) / "global_storms.geojson"
            gj_path.write_text(json.dumps(geojson), encoding="utf-8")
            proc = subprocess.run(
                [NODE, str(GLOBAL_HARNESS), str(page_path), str(gj_path)],
                capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(f"global harness failed:\n{proc.stderr}")
        out = json.loads(proc.stdout)
        cls.css = out["css"]
        # Index recorded markers by storm name via their fix coordinates.
        cls.by_name = {}
        for m in out["markers"]:
            for f in cls.FEATURES:
                if f["geometry"]["coordinates"] == m["lngLat"]:
                    cls.by_name[f["properties"]["name"]] = m
        assert len(cls.by_name) == len(cls.FEATURES), "marker count mismatch"

    # ---- the anchoring invariant chain -----------------------------------

    def test_every_marker_anchored_center_on_its_fix(self):
        for f in self.FEATURES:
            name = f["properties"]["name"]
            m = self.by_name[name]
            self.assertEqual(m["anchor"], "center", name)
            self.assertEqual(m["lngLat"], f["geometry"]["coordinates"], name)

    def test_invest_box_is_the_x_glyph_only(self):
        # CSS box == viewBox dims (1:1) and both symmetric about the X.
        self.assertRegex(
            self.css,
            r"\.active-marker\.invest-x-marker\s*\{\s*width:\s*32px;\s*"
            r"height:\s*32px;\s*\}")
        # The generic marker rules that complete the chain.
        self.assertRegex(
            self.css,
            r"\.active-marker\s*\{\s*position:\s*absolute;\s*"
            r"transform:\s*translate\(-50%,\s*-50%\)")
        self.assertRegex(
            self.css,
            r"\.active-marker svg\s*\{\s*display:\s*block;\s*"
            r"overflow:\s*visible;\s*width:\s*100%;\s*height:\s*100%")

    def test_invest_x_centered_label_offset(self):
        for name in ("92E", "INVEST-LONGLABEL", "91W"):
            m = self.by_name[name]
            self.assertIn("invest-x-marker", m["className"], name)
            vb = re.search(r'viewBox="([-\d. ]+)"', m["html"])
            self.assertIsNotNone(vb, name)
            min_x, min_y, w, h = (float(v) for v in vb.group(1).split())
            # viewBox symmetric about (0,0) -> element centre == (0,0).
            self.assertEqual((min_x + w / 2, min_y + h / 2), (0.0, 0.0),
                             f"{name}: viewBox not centred on the X")
            # The X path is centred on (0,0) == the anchored fix —
            # centroid computed from the RENDERED path data.
            dm = re.search(r'<path d="([^"]+)" stroke="#ff2a2a"', m["html"])
            self.assertIsNotNone(dm, f"{name}: X path missing")
            self.assertEqual(_path_centroid(dm.group(1)), (0.0, 0.0),
                             f"{name}: rendered X not centred on the anchor")
            self.assertEqual(dm.group(1), X_PATH_D, name)
            # Label: an offset <text> sibling, start-anchored at +11.
            lbl = re.search(
                r'<text class="invest-label" x="([-\d.]+)" y="([-\d.]+)" '
                r'text-anchor="start">([^<]*)</text>', m["html"])
            self.assertIsNotNone(lbl, f"{name}: label missing")
            self.assertEqual((float(lbl.group(1)), float(lbl.group(2))),
                             (11.0, 4.0), name)
            self.assertEqual(lbl.group(3), name)

    def test_label_length_cannot_shift_x_global(self):
        def geometry(m):
            # Strip the designation text + per-marker filter ids; what
            # remains is the marker's geometry and must be identical.
            h = re.sub(r'(text-anchor="start">)[^<]*(</text>)',
                       r"\1\2", m["html"])
            return re.sub(r"invest-red-glow-\d+", "invest-red-glow-N", h)
        self.assertEqual(geometry(self.by_name["92E"]),
                         geometry(self.by_name["INVEST-LONGLABEL"]),
                         "label width changed the marker geometry")

    # ---- the stage rule on the global render path -------------------------

    def test_same_stage_same_glyph_incl_legacy_td_circle(self):
        glyph = self.by_name["AMANDA"]
        legacy = self.by_name["TWO-E"]
        for name, m in (("AMANDA", glyph), ("TWO-E", legacy)):
            self.assertIn("active-hurricane", m["className"], name)
            self.assertNotIn("active-td", m["className"], name)
            self.assertIn('class="hurricane-label"', m["html"], name)
            self.assertRegex(m["html"],
                             r'class="hurricane-label"[^>]*>\s*D\s*<',
                             f"{name}: current-stage letter is not D")
        # Byte-identical modulo the name text.
        def normalized(m, name):
            return m["html"].replace(name, "NAME")
        self.assertEqual(normalized(glyph, "AMANDA"),
                         normalized(legacy, "TWO-E"),
                         "legacy td_circle did not fold into the glyph")

    def test_minimal_properties_marker_gets_default_d_glyph(self):
        m = self.by_name["MINIMAL"]
        self.assertIn("active-hurricane", m["className"])
        self.assertRegex(m["html"],
                         r'class="hurricane-label"[^>]*>\s*D\s*<')


if __name__ == "__main__":
    unittest.main(verbosity=2)
