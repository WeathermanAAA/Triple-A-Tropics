"""Drives ``tests/ensemble_viewer_harness.cjs`` so the paintball panel's
member interactions are covered by the normal suite.

Fixtures are built through the REAL publisher (``build_document``), not
hand-written JSON, so a publisher schema change that would break the panel
breaks here first. The ECMWF side is synthetic (no eccodes needed); the GEFS
side runs through the real a-deck parser via an injected opener.

Also covers the publisher pieces the harness depends on: the feed join and the
newest-published-cycle fallback.

Skipped when node or playwright is unavailable.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from guidance import build_ensemble as be  # noqa: E402
from guidance import build_guidance as bg  # noqa: E402

HARNESS = Path(__file__).resolve().parent / "ensemble_viewer_harness.cjs"
NODE = shutil.which("node")


def _have_playwright() -> bool:
    if not NODE:
        return False
    r = subprocess.run([NODE, "-e", "require.resolve('playwright')"],
                       cwd=str(REPO), capture_output=True, text=True)
    return r.returncode == 0


def _ecmwf_storm(storm_id, name, basin, n_members=5, n_steps=6, lon0=-65.0):
    taus = [6 * (i + 1) for i in range(n_steps)]
    members = []
    for m in range(1, n_members + 1):
        members.append({
            "id": m,
            "lat": [15.0 + 0.4 * i + 0.05 * m for i in range(n_steps)],
            "lon": [lon0 - 0.5 * i - 0.04 * m for i in range(n_steps)],
            "vmax": [40 + 5 * i + m for i in range(n_steps)],
            "mslp": [1000 - 3 * i for i in range(n_steps)],
        })
    return {"storm_id": storm_id, "name": name, "basin": basin,
            "taus": taus, "members": members}


def _adeck(techs, basin="EP", cy=7, dtg="2026072812"):
    out = []
    for t in techs:
        for tau in (0, 12, 24, 36):
            out.append(
                f"{basin}, {cy:02d}, {dtg},   , {t}, {tau:4d}, "
                f"{150 + tau}N, 0{651 + tau}W, {50 + tau // 12}, 0, XX, "
                f" 34, NEQ,    0,    0,    0,    0,")
    return "\n".join(out)


class TestPublisherPieces(unittest.TestCase):

    def test_cycle_candidates_newest_first(self):
        got = be.cycle_candidates(dt.datetime(2026, 7, 31, 1, 30,
                                              tzinfo=dt.timezone.utc))
        self.assertEqual(got, ["2026073100", "2026073012",
                               "2026073000", "2026072912"])

    def test_latest_ecmwf_falls_back_to_the_previous_cycle(self):
        """The newest 00/12Z is usually not published yet (~7-9 h lag); the
        finder must walk back rather than report an empty world."""
        calls = []

        def opener(url, timeout=180.0):
            calls.append(url)
            raise RuntimeError("404")
        cycle, storms = be.latest_ecmwf(opener=opener,
                                        now=dt.datetime(2026, 7, 31, 1, 0,
                                                        tzinfo=dt.timezone.utc))
        self.assertIsNone(cycle)
        self.assertEqual(storms, [])
        self.assertEqual(len(calls), 4, "all four candidates tried")

    def test_feed_join_carries_the_name(self):
        """The feed is the one source with both the CycloLab sid AND the name -
        and the name is what the ECMWF match runs on."""
        gj = {"features": [
            {"properties": {"storm_id": "NHC_EP072026", "name": "GENEVIEVE",
                            "is_active": True}},
            {"properties": {"storm_id": "NHC_EP072026", "name": "GENEVIEVE",
                            "is_active": True}},              # dupe feature
            {"properties": {"storm_id": "JTWC_WP122026", "name": "DOLPHIN",
                            "is_active": True}},
            {"properties": {"storm_id": "NHC_AL012026", "name": "ARTHUR",
                            "is_active": False}},             # retired
            {"properties": {"name": "NAMELESS"}},             # no sid
        ]}
        got = be.active_storms_from_feed(
            opener=lambda url, timeout=30.0: json.dumps(gj).encode())
        self.assertEqual(sorted(s["sid"] for s in got),
                         ["JTWC_WP122026", "NHC_EP072026"])
        by = {s["sid"]: s for s in got}
        self.assertEqual(by["JTWC_WP122026"]["name"], "DOLPHIN")
        self.assertEqual(by["JTWC_WP122026"]["basin"], "wp")
        self.assertEqual(by["JTWC_WP122026"]["cy"], 12)

    def test_short_normalised_name_falls_through_to_id(self):
        """'EP95' normalises to 'EP', which is a basin, not a name."""
        storms = [_ecmwf_storm("95E", "95E", "ep")]
        got = be.match_ecmwf(storms, "NHC_EP952026", "EP95")
        self.assertIsNotNone(got, "id fallback must still find it")

    def test_dedupe_keeps_the_fuller_record(self):
        a = _ecmwf_storm("07E", "GENEVIEVE", "ep", n_members=3)
        b = _ecmwf_storm("07E", "GENEVIEVE", "ep", n_members=5)
        got = be._dedupe_storms([a, b])
        self.assertEqual(len(got), 1)
        self.assertEqual(len(got[0]["members"]), 5)


def _fixtures(tmp: Path) -> list:
    """(ep_guidance, ep_ensemble, wp_guidance, wp_ensemble) file paths."""
    # Guidance docs through the real builder (the panel reads best_track from
    # them). Minimal deck: an OFCL + best track for EP; ensembles for WP.
    def deck(techs, basin, cy):
        rows = []
        for t in techs:
            for tau in (0, 12, 24):
                rows.append(
                    f"{basin}, {cy:02d}, 2026072812,   , {t}, {tau:4d}, "
                    f"{150 + tau}N, 0{651 + tau}W, 60, 990, XX,  34, NEQ, "
                    f"   0,    0,    0,    0,")
        return "\n".join(rows)

    ep_g = bg.build_document(deck(["OFCL", "AVNI"], "EP", 7),
                             deck(["BEST"], "EP", 7),
                             basin="ep", cy=7, year=2026)
    wp_g = bg.build_document(deck(["AEMN"], "WP", 12), None,
                             basin="wp", cy=12, year=2026)

    ecmwf = [_ecmwf_storm("07E", "GENEVIEVE", "ep", n_members=5),
             # DOLPHIN under ECMWF's OWN number - the id trap, live in the
             # fixture: WP12 must match this record by NAME.
             _ecmwf_storm("15W", "DOLPHIN", "wp", n_members=7, lon0=172.0)]

    gefs_deck = _adeck(["AC00", "AP01", "AP02"], basin="EP", cy=7)
    ep_e = be.build_document(
        "NHC_EP072026", "GENEVIEVE", "ep", 7, 2026,
        ecmwf_storms=ecmwf, ecmwf_cycle="2026072812",
        opener=lambda url, timeout=30.0: gefs_deck.encode())

    def deck404(url, timeout=30.0):
        raise RuntimeError("404 Not Found")
    wp_e = be.build_document(
        "JTWC_WP122026", "DOLPHIN", "wp", 12, 2026,
        ecmwf_storms=ecmwf, ecmwf_cycle="2026072812", opener=deck404)

    assert len(ep_e["sources"]) == 2, "EP fixture must carry both sources"
    assert len(wp_e["sources"]) == 1, "WP fixture is ECMWF-only"
    assert wp_e["sources"][0]["upstream_id"] == "15W", \
        "the name match must land on ECMWF's own id"

    paths = []
    for name, doc in (("ep_g", ep_g), ("ep_e", ep_e),
                      ("wp_g", wp_g), ("wp_e", wp_e)):
        p = tmp / f"{name}.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        paths.append(p)
    return paths


class TestFixtureShape(unittest.TestCase):
    """The publisher-side assertions, runnable without a browser."""

    def test_fixture_documents_build(self):
        with tempfile.TemporaryDirectory() as td:
            ep_g, ep_e, wp_g, wp_e = _fixtures(Path(td))
            e = json.loads(ep_e.read_text())
            self.assertEqual(e["sources"][0]["model"], "ecmwf_ens")
            self.assertEqual(e["sources"][0]["cycle"], "2026072812")
            self.assertEqual(e["sources"][1]["model"], "gefs")
            self.assertEqual(e["sources"][1]["n_members"], 3)
            w = json.loads(wp_e.read_text())
            self.assertEqual(w["sources"][0]["matched_by"], "name")
            self.assertEqual(w["sources"][0]["upstream_id"], "15W")


@unittest.skipUnless(_have_playwright(), "node + playwright required")
class TestEnsembleViewer(unittest.TestCase):

    def test_harness_assertions_all_pass(self):
        import os
        with tempfile.TemporaryDirectory() as td:
            paths = _fixtures(Path(td))
            proc = subprocess.run(
                [NODE, str(HARNESS)] + [str(p) for p in paths],
                cwd=str(REPO), capture_output=True, text=True, timeout=240,
                env={**os.environ, "GV_SHOT_DIR": td})
        if proc.returncode != 0:
            self.fail("ensemble viewer harness failed:\n"
                      + proc.stdout + "\n" + proc.stderr)
        self.assertIn("all assertions passed", proc.stdout)


if __name__ == "__main__":
    unittest.main()
