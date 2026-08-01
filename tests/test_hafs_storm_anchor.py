#!/usr/bin/env python3
"""Unit tests for the HAFS storm-anchoring machinery (hafs_plot): the ATCF
track parser, the stat scope reductions, the radius mask (incl. dateline
wrap), and the fix snapping. Pure functions, no network, no GRIB.

Skipped wholesale when the hafs_render dep stack (matplotlib/herbie) is not
installed - the rest of the repo's test suite must keep running without it.
"""
import unittest

try:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hafs_render"))
    import numpy as np
    from hafs_render import hafs_plot as hp
except Exception as e:  # noqa: BLE001 - optional dep stack
    raise unittest.SkipTest(f"hafs_render stack unavailable: {e}")


DECK = """\
EP, 01, 2026060406, 03, HFSA, 000, 118N, 1294W,  37, 1007, XX,  34, NEQ, 0050, 0000, 0029, 0050, 1011
EP, 01, 2026060406, 03, HFSA, 006, 121N, 1299W,  69,  997, XX,  34, NEQ, 0050, 0037, 0037, 0047, 1005
EP, 01, 2026060406, 03, HFSA, 006, 121N, 1299W,  69,  997, XX,  50, NEQ, 0020, 0000, 0000, 0018, 1005
EP, 01, 2026060406, 03, HFSA, 006, 121N, 1299W,  69,  997, XX,  64, NEQ, 0011, 0000, 0000, 0010, 1005
WP, 06, 2026060406, 03, HFSA, 012, 153N, 1413E,  55,  990, XX,  34, NEQ, 0080, 0060, 0050, 0070, 1006
SH, 22, 2026060406, 03, HFSA, 018, 85S, 0621E,  45,  995, XX,  34, NEQ, 0, 0, 0, 0, 0
this line is junk and must be skipped
EP, 01, 2026060406, 03, HFSA, BAD, 118N, 1294W,  37, 1007
EP, 01, 2026060406, 03, HFSA, 024, XXXX, YYYY,  37, 1007, XX, 34, NEQ, 0, 0, 0, 0, 0
"""


class TestParseAtcfTrack(unittest.TestCase):
    def test_parses_positions_and_dedupes_radii_lines(self):
        track = hp.parse_atcf_track(DECK)
        # tau 006 appears 3x (34/50/64 kt radii) -> ONE fix; junk lines skipped
        self.assertEqual(sorted(track), [0, 6, 12, 18])
        self.assertEqual(track[0], (11.8, -129.4))      # 118N 1294W
        self.assertEqual(track[6], (12.1, -129.9))
        self.assertEqual(track[12], (15.3, 141.3))      # WP: 1413E
        self.assertEqual(track[18], (-8.5, 62.1))       # SH: 85S 0621E

    def test_empty_and_garbage_never_raise(self):
        self.assertEqual(hp.parse_atcf_track(""), {})
        self.assertEqual(hp.parse_atcf_track("total, garbage\nno, commas"), {})


def _mini_frame(lon, lat):
    """A minimal duck-typed frame for the mask/snap helpers (lon/lat only)."""
    class F:  # noqa: D401 - test stub
        pass
    f = F()
    f.lon = np.asarray(lon, dtype=float)
    f.lat = np.asarray(lat, dtype=float)
    return f


class TestRadiusMaskAndSnap(unittest.TestCase):
    def test_radius_mask_basic(self):
        f = _mini_frame(np.arange(-140.0, -119.9, 0.5), np.arange(0.0, 30.1, 0.5))
        m = hp._radius_mask(f, 15.0, -130.0, 3.0)
        LON, LAT = np.meshgrid(f.lon, f.lat)
        # the fix cell is inside; a cell 5 deg away is outside
        self.assertTrue(m[np.argmin(np.abs(f.lat - 15.0)),
                          np.argmin(np.abs(f.lon + 130.0))])
        self.assertFalse(m[np.argmin(np.abs(f.lat - 15.0)),
                           np.argmin(np.abs(f.lon + 135.5))])
        self.assertFalse(m[np.argmin(np.abs(f.lat - 20.0)),
                           np.argmin(np.abs(f.lon + 130.0))])

    def test_radius_mask_dateline_wrap(self):
        # WP continuous frame (168..188); a fix at -175 (=185 E) must wrap in
        f = _mini_frame(np.arange(168.0, 188.1, 0.5), np.arange(5.0, 25.1, 0.5))
        m = hp._radius_mask(f, 15.0, -175.0, 3.0)
        self.assertTrue(m[np.argmin(np.abs(f.lat - 15.0)),
                          np.argmin(np.abs(f.lon - 185.0))])

    def test_snap_fix_on_and_off_grid(self):
        f = _mini_frame(np.arange(-140.0, -119.9, 0.5), np.arange(0.0, 30.1, 0.5))
        self.assertEqual(hp._snap_fix(f, 15.02, -130.01), (15.0, -130.0))
        self.assertIsNone(hp._snap_fix(f, None, -130.0))
        self.assertIsNone(hp._snap_fix(f, 15.0, None))
        # far off the grid (> 1 deg margin) -> None, never a clamped wrong point
        self.assertIsNone(hp._snap_fix(f, 45.0, -130.0))
        self.assertIsNone(hp._snap_fix(f, 15.0, -60.0))


class TestPickTrackFix(unittest.TestCase):
    OWN = {0: (10.0, -130.0), 3: (10.2, -130.4), 6: (10.4, -130.8)}
    PREV = {t: (9.0 + t / 60.0, -129.0 - t / 30.0) for t in range(0, 133, 3)}

    def test_own_deck_wins(self):
        cen, anchor = hp.pick_track_fix(self.OWN, self.PREV, 3)
        self.assertEqual(cen, (10.2, -130.4))
        self.assertEqual(anchor, (10.2, -130.4))

    def test_provisional_from_previous_cycle_same_valid_time(self):
        # tau 9 not in the own deck yet -> previous cycle's tau 15 (same valid
        # time) anchors; framing anchor = last OWN fix (tau 6).
        cen, anchor = hp.pick_track_fix(self.OWN, self.PREV, 9)
        self.assertEqual(cen, self.PREV[15])
        self.assertEqual(anchor, self.OWN[6])

    def test_no_own_deck_at_all(self):
        cen, anchor = hp.pick_track_fix({}, self.PREV, 12)
        self.assertEqual(cen, self.PREV[18])
        self.assertEqual(anchor, self.PREV[18])

    def test_new_storm_degrades_honestly(self):
        cen, anchor = hp.pick_track_fix({}, {}, 0)
        self.assertIsNone(cen)
        self.assertIsNone(anchor)

    def test_prev_deck_terminal_edge(self):
        # fxx 126 needs prev tau 132 for the same valid time, but decks end at
        # 126 -> no provisional cen; the framing anchor still falls back to the
        # last previous-cycle fix at-or-before that valid time.
        prev = {t: (1.0, 2.0) for t in range(0, 127, 3)}
        cen, anchor = hp.pick_track_fix({}, prev, 126)
        self.assertIsNone(cen)         # prev[132] absent
        self.assertEqual(anchor, (1.0, 2.0))


class TestOnlyFxxPlanning(unittest.TestCase):
    """Offline planning-path test for --only-fxx: stub the S3 listings, deck
    fetches, and the process pools; assert which frames build_cycle PLANS."""

    def _run(self, only_fxx, posted):
        from hafs_render import generate_hafs_plots as gen
        import tempfile
        from pathlib import Path
        captured = {"ingest": [], "render": []}

        def fake_pool(jobs_list, fn, jobs, record, straggler, initializer=None,
                      max_tasks_per_child=None, stage_deadline_s=None):
            stage = "ingest" if jobs_list and isinstance(
                jobs_list[0], gen.IngestJob) else "render"
            captured[stage] = list(jobs_list)
            for j in jobs_list:
                res = {"ok": True, "model": j.model, "storm": j.storm,
                       "domain": j.domain, "fxx": j.fxx}
                if stage == "render":
                    res["product"] = j.product
                record(res)

        saved = (gen._run_pool, gen.list_storms, gen.list_fxx,
                 gen.hp.fetch_hafs_track)
        gen._run_pool = fake_pool
        gen.list_storms = lambda model, date, hh, session=None: ["01e"]
        gen.list_fxx = (lambda model, date, hh, storm, domain, session=None:
                        list(posted))
        gen.hp.fetch_hafs_track = lambda *a, **k: {}
        try:
            with tempfile.TemporaryDirectory() as td:
                manifest, n_storms, n_ok, n_fail = gen.build_cycle(
                    "20260604", "18", Path(td), models=["hafsa"],
                    domains=["storm.atm"], products=["mslp_wind"],
                    only_fxx=only_fxx, jobs=1, save_dir=td)
        finally:
            (gen._run_pool, gen.list_storms, gen.list_fxx,
             gen.hp.fetch_hafs_track) = saved
        return captured, manifest

    def test_subset_bypasses_terminal_gate(self):
        # Only f000..f009 posted (incomplete pair). only_fxx renders the subset.
        cap, man = self._run({0, 3}, posted=[0, 3, 6, 9])
        self.assertEqual([j.fxx for j in cap["ingest"]], [0, 3])
        self.assertEqual(man["storms"][0]["frames"]["hafsa"]["storm"]
                         ["mslp_wind"], [0, 3])

    def test_subset_intersects_with_posted(self):
        # Caller asks for f006,f012 but only f006 is actually posted.
        cap, _ = self._run({6, 12}, posted=[0, 3, 6])
        self.assertEqual([j.fxx for j in cap["ingest"]], [6])

    def test_prev_deck_only_in_progressive_mode(self):
        """REVIEW FINDING (confirmed): the prev-cycle provisional anchor must
        NOT leak into the classic full/cron path - a short deck there means
        the tracker LOST the storm and v0.3.0's honest degradation applies."""
        from hafs_render import generate_hafs_plots as gen
        calls = []
        own = {0: (10.0, -130.0)}                     # tracker lost after f000
        prev = {t: (9.0, -129.0) for t in range(0, 133, 3)}

        def fake_fetch(model, storm, cycle_dt, session=None, **k):
            calls.append(cycle_dt)
            return own if cycle_dt.hour == 18 else prev

        import tempfile
        from pathlib import Path
        saved = (gen._run_pool, gen.list_storms, gen.list_fxx,
                 gen.hp.fetch_hafs_track)
        cen_seen = {}

        def fake_pool(jobs_list, fn, jobs, record, straggler, initializer=None,
                      max_tasks_per_child=None, stage_deadline_s=None):
            for j in jobs_list:
                if isinstance(j, gen.RenderJob):
                    cen_seen[j.fxx] = (j.cen_lat, j.cen_lon)
                res = {"ok": True, "model": j.model, "storm": j.storm,
                       "domain": j.domain, "fxx": j.fxx}
                if isinstance(j, gen.RenderJob):
                    res["product"] = j.product
                record(res)

        gen._run_pool = fake_pool
        gen.list_storms = lambda model, date, hh, session=None: ["01e"]
        gen.list_fxx = (lambda model, date, hh, storm, domain, session=None:
                        list(range(0, 127, 3)))
        gen.hp.fetch_hafs_track = fake_fetch
        try:
            with tempfile.TemporaryDirectory() as td:
                # CLASSIC path (only_fxx=None): NO prev fetch, lost tail
                # degrades honestly (cen None beyond the own deck)
                calls.clear(); cen_seen.clear()
                gen.build_cycle("20260604", "18", Path(td), models=["hafsa"],
                                domains=["storm.atm"], products=["mslp_wind"],
                                jobs=1, save_dir=td)
                self.assertEqual(len(calls), 1)        # own deck only
                self.assertEqual(cen_seen[0], (10.0, -130.0))
                self.assertEqual(cen_seen[6], (None, None))
                self.assertEqual(cen_seen[126], (None, None))
                # PROGRESSIVE path (only_fxx set): prev deck anchors
                calls.clear(); cen_seen.clear()
                gen.build_cycle("20260604", "18", Path(td), models=["hafsa"],
                                domains=["storm.atm"], products=["mslp_wind"],
                                only_fxx={6}, jobs=1, save_dir=td)
                self.assertEqual(len(calls), 2)        # own + prev
                self.assertEqual(cen_seen[6], (9.0, -129.0))
        finally:
            (gen._run_pool, gen.list_storms, gen.list_fxx,
             gen.hp.fetch_hafs_track) = saved

    def test_classic_gate_still_skips_incomplete_pair(self):
        # only_fxx=None keeps the complete-pair behavior: incomplete -> skip.
        cap, man = self._run(None, posted=[0, 3, 6, 9])
        self.assertEqual(cap["ingest"], [])
        self.assertEqual(man["storms"], [])


class TestStatScope(unittest.TestCase):
    def test_scope_reductions_respect_mask(self):
        arr = np.array([[1.0, 2.0], [3.0, 4.0]])
        mask = np.array([[True, False], [True, False]])
        s = hp.StatScope(mask=mask, tracked=True)
        self.assertEqual(hp.scope_max(arr, s), 3.0)
        self.assertEqual(hp.scope_min(arr, s), 1.0)
        self.assertEqual(hp.scope_mean(arr, s), 2.0)
        # domain scope (mask None) and None scope = whole-array reductions
        self.assertEqual(hp.scope_max(arr, hp.StatScope()), 4.0)
        self.assertEqual(hp.scope_max(arr, None), 4.0)

    def test_scope_all_nan_is_nan_not_raise(self):
        arr = np.full((2, 2), np.nan)
        s = hp.StatScope(mask=np.ones((2, 2), bool), tracked=True)
        self.assertTrue(np.isnan(hp.scope_max(arr, s)))
        self.assertTrue(np.isnan(hp.scope_mean(arr, s)))

    def test_mask_excluding_everything_is_nan(self):
        arr = np.array([[1.0, 2.0]])
        s = hp.StatScope(mask=np.zeros((1, 2), bool), tracked=True)
        self.assertTrue(np.isnan(hp.scope_min(arr, s)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
