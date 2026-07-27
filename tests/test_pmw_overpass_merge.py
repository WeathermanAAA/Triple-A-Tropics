"""One satellite overpass = one pass, even when PPS cuts it across granules.

PPS slices an orbit into fixed segments (GMI is 5 min), so a storm sitting near
a cut lands in two files. The per-granule build loop rendered each as its own
pass: two half-swaths of one overpass, each cropped to a different part of the
storm, and -- because the two renders could land in different cron runs -- each
stamped with whatever best-track intensity was current then.

Reproduced live on DOLPHIN (wp122026), 2026-07-27:
    GMI GPM granule S150454 -> 15:09:52Z, 45 kt
    GMI GPM granule S150954 -> 15:09:59Z, 40 kt
Seven seconds and five knots apart. Same overpass.
"""

import datetime as dt
import unittest

import numpy as np

from tcprimed import pps

UTC = dt.timezone.utc


def _g(sensor, platform, start, end):
    return {"sensor": sensor, "platform": platform, "url": "u",
            "file": f"{sensor}_{platform}_{start}",
            "start": dt.datetime(2026, 7, 27, *start, tzinfo=UTC),
            "end": dt.datetime(2026, 7, 27, *end, tzinfo=UTC)}


# The two real DOLPHIN pairs, by their actual granule boundaries.
DOLPHIN_1509 = [_g("GMI", "GPM", (15, 4, 54), (15, 9, 54)),
                _g("GMI", "GPM", (15, 9, 54), (15, 14, 54))]
DOLPHIN_0338 = [_g("GMI", "GPM", (3, 34, 55), (3, 39, 55)),
                _g("GMI", "GPM", (3, 39, 55), (3, 44, 55))]


def _seg(lat0, lat1, nscan=40, rays=20, tb=250.0):
    """A descending swath segment centred on 130E."""
    lat = np.linspace(lat0, lat1, nscan)[:, None] * np.ones((1, rays))
    lon = 130.0 + np.linspace(-4, 4, rays)[None, :] * np.ones((nscan, 1))
    val = np.full((nscan, rays), tb)
    return {"sensor": "GMI", "platform": "GPM",
            "lat89": lat, "lon89": lon, "tb89v": val, "tb89h": val,
            "lat37": lat, "lon37": lon, "tb37v": val, "tb37h": val}


class TestGrouping(unittest.TestCase):
    def test_dolphin_1509_merges(self):
        self.assertEqual([len(g) for g in pps.group_overpasses(DOLPHIN_1509)],
                         [2])

    def test_dolphin_0338_merges(self):
        self.assertEqual([len(g) for g in pps.group_overpasses(DOLPHIN_0338)],
                         [2])

    def test_three_contiguous_segments_are_one_pass(self):
        gs = [_g("GMI", "GPM", (1, 0, 0), (1, 5, 0)),
              _g("GMI", "GPM", (1, 5, 0), (1, 10, 0)),
              _g("GMI", "GPM", (1, 10, 0), (1, 15, 0))]
        self.assertEqual([len(g) for g in pps.group_overpasses(gs)], [3])

    def test_different_sensor_never_merges(self):
        gs = [_g("GMI", "GPM", (15, 4, 54), (15, 9, 54)),
              _g("AMSR2", "GCOMW1", (15, 4, 54), (15, 9, 54))]
        self.assertEqual(len(pps.group_overpasses(gs)), 2)

    def test_different_platform_never_merges(self):
        """F16 and F17 both fly SSMIS; they are not the same overpass."""
        gs = [_g("SSMIS", "F16", (6, 0, 0), (6, 5, 0)),
              _g("SSMIS", "F17", (6, 5, 0), (6, 10, 0))]
        self.assertEqual(len(pps.group_overpasses(gs)), 2)

    def test_real_time_gap_never_merges(self):
        """A revisit an orbit later is a separate pass."""
        gs = [_g("GMI", "GPM", (15, 4, 54), (15, 9, 54)),
              _g("GMI", "GPM", (16, 44, 54), (16, 49, 54))]
        self.assertEqual(len(pps.group_overpasses(gs)), 2)

    def test_gap_threshold_is_far_below_an_orbit(self):
        self.assertLess(pps.MAX_OVERPASS_GAP_S, 15 * 60)

    def test_groups_come_back_in_time_order(self):
        gs = [_g("GMI", "GPM", (16, 44, 54), (16, 49, 54)),
              _g("AMSR2", "GCOMW1", (1, 10, 32), (1, 15, 32)),
              _g("GMI", "GPM", (15, 4, 54), (15, 9, 54))]
        starts = [g[0]["start"] for g in pps.group_overpasses(gs)]
        self.assertEqual(starts, sorted(starts))


class TestMosaic(unittest.TestCase):
    def test_joins_on_the_scan_axis(self):
        m = pps.concat_swaths([_seg(26.0, 20.5), _seg(20.4, 15.0)])
        self.assertEqual(m["lat89"].shape, (80, 20))
        self.assertTrue(np.all(np.diff(m["lat89"][:, 0]) < 0))

    def test_every_band_survives(self):
        m = pps.concat_swaths([_seg(26.0, 20.5), _seg(20.4, 15.0)])
        for k in ("lat37", "lon37", "tb37v", "tb37h",
                  "lat89", "lon89", "tb89v", "tb89h"):
            self.assertEqual(m[k].shape, (80, 20), k)

    def test_single_granule_passes_through_untouched(self):
        one = _seg(26.0, 15.0)
        self.assertIs(pps.concat_swaths([one]), one)

    def test_ray_mismatch_drops_the_band_rather_than_force_fitting(self):
        """A silent mis-join would corrupt geolocation; losing the band is the
        safe failure."""
        m = pps.concat_swaths([_seg(26.0, 20.5), _seg(20.4, 15.0, rays=25)])
        self.assertNotIn("lat89", m)

    def test_empty_input_is_none(self):
        self.assertIsNone(pps.concat_swaths([]))


class TestOnePassOneTime(unittest.TestCase):
    """The intensity symptom: two granules -> two valid times -> two
    best-track interpolations -> two different kt labels for one overpass."""

    T0 = dt.datetime(2026, 7, 27, 15, 4, 54, tzinfo=UTC)
    TM = dt.datetime(2026, 7, 27, 15, 9, 54, tzinfo=UTC)
    T1 = dt.datetime(2026, 7, 27, 15, 14, 54, tzinfo=UTC)

    def test_split_granules_disagree(self):
        a, b = _seg(26.0, 20.5), _seg(20.4, 15.0)
        va = pps.overpass_time(a["lat89"], a["lon89"], 20.0, 130.0,
                               self.T0, self.TM)
        vb = pps.overpass_time(b["lat89"], b["lon89"], 20.0, 130.0,
                               self.TM, self.T1)
        self.assertNotEqual(va, vb)

    def test_mosaic_yields_exactly_one_time_inside_the_span(self):
        m = pps.concat_swaths([_seg(26.0, 20.5), _seg(20.4, 15.0)])
        v = pps.overpass_time(m["lat89"], m["lon89"], 20.0, 130.0,
                              self.T0, self.T1)
        self.assertGreaterEqual(v, self.T0)
        self.assertLessEqual(v, self.T1)

    def test_time_is_continuous_across_the_granule_cut(self):
        """The row-fraction -> time mapping must stay linear across the join,
        so a storm at the cut gets the cut's time, not an end-of-segment time."""
        m = pps.concat_swaths([_seg(26.0, 20.5), _seg(20.4, 15.0)])
        v = pps.overpass_time(m["lat89"], m["lon89"], 20.45, 130.0,
                              self.T0, self.T1)
        self.assertLess(abs((v - self.TM).total_seconds()), 40)


if __name__ == "__main__":
    unittest.main()
