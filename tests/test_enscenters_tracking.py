"""
Tests for the Ensemble Cyclone Centers tracking + clustering keystone
(``enscenters.tracking``): Stage A linkage, Stage B per-system clustering, Stage C
derived products, and the dateline-safe geometry they all stand on.

The synthetic ensemble has TWO clear systems + spurious one-off centers; the real-
cycle smokes (ecens self-detect, fnv3 native) run only if a cached cycle file is
present (downloaded by the VERIFY step), so the core suite stays hermetic.
"""
import datetime as dt
import json
import math
import os
import unittest

import numpy as np

from enscenters import tracking as T


CYCLE = dt.datetime(2026, 6, 14, 18)


class _Spec:
    """Minimal stand-in for an EnsModelSpec (only the attrs tracking reads)."""
    def __init__(self, slug="ecens", label="Test", source_kind="self_detect"):
        self.slug = slug
        self.label = label
        self.source_kind = source_kind


# ---------------------------------------------------------------------------
# Synthetic ensemble builders
# ---------------------------------------------------------------------------
def _system_member(rng, lat0, lon0, dlat, dlon, *, steps, p0=1004.0, dp=-2.0,
                   v0=25.0, dv=3.0, drift=(0.0, 0.0), spread=0.0, jitter=0.15, start=0):
    """One member's centers for a moving, intensifying low (steps in hours). The
    track is SMOOTH (small per-step ``jitter``) but carries a member-specific
    ``drift`` that grows with lead (``spread`` deg/step), mimicking a real ensemble
    that fans out down the forecast - so different members of one system form a
    proper density blob while each member's own track stays linkable."""
    dla, dlo = drift
    centers = []
    for k, s in enumerate(steps):
        if s < start:
            continue
        j = k
        lat = lat0 + dlat * j + dla * spread * j + rng.normal(0, jitter)
        lon = lon0 + dlon * j + dlo * spread * j + rng.normal(0, jitter)
        # wrap lon to [-180,180] so the input mimics the real centers JSON
        lon = ((lon + 180) % 360) - 180
        mslp = p0 + dp * j + rng.normal(0, 1.0)
        vmax = max(10.0, v0 + dv * j + rng.normal(0, 2.0))
        centers.append([s, round(lat, 2), round(lon, 2), round(mslp, 1), round(vmax, 1)])
    return centers


def _two_system_ensemble(n=24, seed=7):
    """n members; ~3/4 carry System A (15N,140W, WNW), ~2/3 carry System B
    (10N,60W, W); a late-forming member joins B at step 48; a few members carry a
    spurious one-off center; one member carries an isolated long spurious track."""
    rng = np.random.default_rng(seed)
    steps = list(range(0, 121, 6))
    members = []
    for i in range(n):
        centers = []
        driftA = (rng.normal(0, 1), rng.normal(0, 1))
        driftB = (rng.normal(0, 1), rng.normal(0, 1))
        if i % 4 != 0:                              # ~75% have System A
            centers += _system_member(rng, 15.0, -140.0, 0.3, -0.6, steps=steps,
                                      p0=1003, dp=-2.5, v0=28, dv=3.2,
                                      drift=driftA, spread=0.14)
        if i % 3 != 0:                              # ~67% have System B
            start = 48 if i == 1 else 0             # member 1 is the LATE-former
            centers += _system_member(rng, 10.0, -60.0, 0.1, -0.7, steps=steps,
                                      p0=1005, dp=-2.0, v0=24, dv=2.6, start=start,
                                      drift=driftB, spread=0.14)
        if i % 7 == 0:                              # a spurious one-off (dropped in A)
            centers.append([36, round(-30 + rng.normal(0, 1), 2),
                            round(20 + rng.normal(0, 1), 2), 1011.0, 12.0])
        if i == 5:                                  # an isolated LONG spurious track
            for s in steps[:6]:
                centers.append([s, round(-40 + 0.1 * s, 2), round(120 + 0.2 * s, 2),
                                1010.0, 14.0])
        members.append({"id": f"M{i:02d}", "centers": centers})
    return {"n_members": n, "run_steps": steps, "members": members}


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
class TestGeometry(unittest.TestCase):
    def test_dateline_mean_and_distance(self):
        la, lo = T.spherical_mean([(0, 179.0), (0, -179.0)])
        self.assertAlmostEqual(la, 0.0, places=6)
        self.assertAlmostEqual(abs(lo), 180.0, places=4)
        self.assertAlmostEqual(T.gc_deg(0, 179, 0, -179), 2.0, places=3)

    def test_geometric_median_resists_outlier(self):
        pts = [(10, -60)] * 8 + [(40, 0)]           # 8 clustered + 1 far outlier
        la, lo = T.geometric_median(pts)
        self.assertLess(T.gc_deg(la, lo, 10, -60), 5.0)   # stays near the cluster

    def test_unwrap_continuous_over_dateline(self):
        u = T.unwrap_lons([179, -179, -177])
        self.assertEqual(u[0], 179)
        self.assertTrue(all(abs(b - a) < 180 for a, b in zip(u, u[1:])))

    def test_xy_km_roundtrip(self):
        la, lo = T._xy_km_to_latlon(*T._local_xy_km(12.3, 178.5, 11.0, 179.9), 11.0, 179.9)
        self.assertAlmostEqual(la, 12.3, places=2)
        self.assertAlmostEqual(((lo + 180) % 360) - 180, 178.5, places=2)


# ---------------------------------------------------------------------------
# Stage A - linkage
# ---------------------------------------------------------------------------
class TestLinkage(unittest.TestCase):
    def test_links_moving_low_drops_oneoff(self):
        rng = np.random.default_rng(1)
        steps = list(range(0, 73, 6))
        centers = _system_member(rng, 12, -140, 0.3, -0.6, steps=steps)
        centers.append([24, -45.0, 30.0, 1012.0, 10.0])  # one-off, far
        tracks = T.link_tracks(centers, 6.0)
        self.assertEqual(len(tracks), 1)
        self.assertGreaterEqual(len(tracks[0]), len(steps) - 1)

    def test_short_track_dropped_by_min_duration(self):
        # two fixes, 6 h apart -> below 24 h min duration
        tracks = T.link_tracks([[0, 10, -50, 1005, 25], [6, 10.3, -50.6, 1004, 27]], 6.0)
        self.assertEqual(tracks, [])

    def test_min_duration_is_spacing_aware(self):
        # The duration floor is 4 STEP INTERVALS, so it scales with cadence: a
        # 6-hourly model keeps the ~24 h wall, while ECMWF ENS's 3-hourly cadence
        # does NOT shred weak, finely-sampled systems on an 8-fix/24 h wall (the
        # Arthur regression). The track drifts >2 deg so the unchanged path gate
        # never decides the outcome — only the duration floor does.
        def mk(sp, end):
            return [[s, 10.0, -50.0 - s * 0.3, 1005.0, 25.0] for s in range(0, end + 1, sp)]
        # 3 h spacing: a 12 h / 5-fix track is KEPT (floor 12 h = 4 x 3 h)
        self.assertEqual(len(T.link_tracks(mk(3, 12), 3.0)), 1)
        # 6 h spacing: the SAME 12 h span (3 fixes) is DROPPED (floor 24 h, unchanged)
        self.assertEqual(T.link_tracks(mk(6, 12), 6.0), [])
        # 6 h spacing: a 24 h / 5-fix track is KEPT (floor 24 h)
        self.assertEqual(len(T.link_tracks(mk(6, 24), 6.0)), 1)

    def test_link_gates_speed_and_bridge_are_spacing_aware(self):
        # Fix A: the 6-hourly link must allow the SAME storm SPEED (range = speed x
        # spacing -> 6 deg @ 6 h, not the old 5) and BRIDGE a 2-step detection miss
        # (maxgap 3 @ 6 h, not 1), so a moving / intermittently-detected system (the
        # AIFS-ENS Arthur regression) links into ONE track instead of fragmenting.
        # ECMWF ENS's 3-hourly gates are unchanged (range 3.0, maxgap 2).
        # (a) SPEED: ~1 deg/h track = ~5.8 deg gc per 6 h step -> links @ 6 h (broke
        #     at the old 5.0-deg range).
        fast6 = [[s, 15.0, -50.0 - (s / 6.0) * 6.0, 1000.0, 30.0] for s in range(0, 31, 6)]
        self.assertEqual(len(T.link_tracks(fast6, 6.0)), 1)
        # (b) BRIDGE: a 2-step (12 h) detection miss is spanned @ 6 h (maxgap 3); the
        #     same gap broke into sub-floor fragments at the old maxgap 1.
        gappy6 = [[0, 15, -50, 1004, 25], [6, 15, -53, 1003, 27],
                  [24, 15, -62, 1002, 28], [30, 15, -65, 1001, 30]]  # 12 h + 18 h missing
        self.assertEqual(len(T.link_tracks(gappy6, 6.0)), 1)
        # (c) ECMWF 3-hourly UNCHANGED: range stays 3.0, so a ~3.4-deg/step jump still
        #     splits (does not form one track).
        jump3 = [[s, 15.0, -50.0 - (s / 3.0) * 3.5, 1000.0, 30.0] for s in range(0, 25, 3)]
        self.assertNotEqual(len(T.link_tracks(jump3, 3.0)), 1)

    def test_two_distinct_lows_split(self):
        rng = np.random.default_rng(2)
        steps = list(range(0, 73, 6))
        c = (_system_member(rng, 12, -140, 0.3, -0.6, steps=steps)
             + _system_member(rng, 30, -50, -0.1, -0.5, steps=steps))
        tracks = T.link_tracks(c, 6.0)
        self.assertEqual(len(tracks), 2)

    def test_pressure_jump_cap_prevents_crosslink(self):
        # two centers at each step: a deep low and a weak low far apart in MSLP.
        rng = np.random.default_rng(3)
        steps = list(range(0, 73, 6))
        deep = _system_member(rng, 12, -140, 0.3, -0.5, steps=steps, p0=985, dp=-1, v0=70, dv=1)
        weak = _system_member(rng, 12.4, -140.5, 0.3, -0.5, steps=steps, p0=1010, dp=0, v0=15, dv=0)
        tracks = T.link_tracks(deep + weak, 6.0)
        # the deep and weak lows are co-located; the MSLP cap must keep them as two
        self.assertEqual(len(tracks), 2)


# ---------------------------------------------------------------------------
# Stage B + C + D - clustering + derived products + assembly
# ---------------------------------------------------------------------------
class TestClusteringAndProducts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        doc = _two_system_ensemble()
        pmt, spacing = T.per_member_tracks_from_centers(doc)
        cls.doc = doc
        cls.out = T.assemble_tracks_doc(pmt, spec=_Spec(), cycle=CYCLE,
                                        n_members=doc["n_members"], spacing_h=spacing,
                                        source_kind="self_detect")

    def test_exactly_two_clusters(self):
        self.assertEqual(self.out["n_clusters"], 2)

    def test_clusters_at_the_two_systems(self):
        gens = sorted((c["genesis"]["lat"], c["genesis"]["lon"])
                      for c in self.out["clusters"])
        # one near (10,-60) [System B], one near (15,-140) [System A]
        near_b = any(T.gc_deg(la, lo, 10, -60) < 6 for la, lo in gens)
        near_a = any(T.gc_deg(la, lo, 15, -140) < 6 for la, lo in gens)
        self.assertTrue(near_a and near_b)

    def test_spurious_isolated_track_is_noise(self):
        # member M05's isolated southern-Indian-Ocean track must be in NO cluster
        for c in self.out["clusters"]:
            for la, lo in [(p["lat"], p["lon"]) for p in [c["genesis"]]]:
                self.assertFalse(la < -30 and lo > 100,
                                 "isolated spurious track leaked into a cluster")

    def test_cluster_population_weighted_fields(self):
        for c in self.out["clusters"]:
            self.assertGreaterEqual(c["member_count"], 2)
            self.assertLessEqual(c["coverage_fraction"], 1.0)
            self.assertGreaterEqual(c["population"], c["member_count"] - 0)  # >=1 track/member
            self.assertIn("low_confidence", c)

    def test_plume_percentiles_monotone(self):
        for c in self.out["clusters"]:
            for var in ("vmax", "mslp"):
                s = c["plume"][var]
                for i in range(len(s["lead"])):
                    row = [s["p10"][i], s["p25"][i], s["p50"][i], s["p75"][i], s["p90"][i]]
                    self.assertEqual(row, sorted(row),
                                     f"{var} percentiles not monotone at lead {s['lead'][i]}")
                    self.assertLessEqual(s["min"][i], s["p10"][i])
                    self.assertGreaterEqual(s["max"][i], s["p90"][i])

    def test_mean_track_within_system_bounds(self):
        # the System A cluster's mean track must stay in the NE Pacific quadrant
        a = max(self.out["clusters"],
                key=lambda c: 1 if T.gc_deg(c["genesis"]["lat"], c["genesis"]["lon"], 15, -140) < 6 else 0)
        for s, la, lo, n in a["mean_track"]:
            self.assertTrue(0 < la < 40)
            self.assertTrue(-180 < lo < -120 or lo < -180)   # unwrapped allowed
            self.assertGreaterEqual(n, 1)

    def test_late_member_aligned_by_valid_time(self):
        # member M01 joins System B only at step 48; it must appear in B's cluster
        # and its first contribution must be at lead 48 (valid-time aligned).
        b = max(self.out["clusters"],
                key=lambda c: 1 if T.gc_deg(c["genesis"]["lat"], c["genesis"]["lon"], 10, -60) < 6 else 0)
        self.assertIn("M01", b["members"])
        # find the member track for M01 in the emitted per-member tracks
        m01 = next(m for m in self.out["members"] if m["id"] == "M01")
        b_tracks = [t for t in m01["tracks"]
                    if T.gc_deg(t[0][1], t[0][2], 10, -60) < 8]
        self.assertTrue(b_tracks)
        self.assertGreaterEqual(b_tracks[0][0][0], 48)       # starts no earlier than lead 48

    def test_envelope_has_ellipses_where_enough_members(self):
        any_ell = False
        for c in self.out["clusters"]:
            for e in c["envelope"]:
                if e["n"] >= 3:
                    self.assertIn("ell50", e)
                    self.assertIn("ell90", e)
                    self.assertGreaterEqual(e["ell90"]["a_km"], e["ell50"]["a_km"])
                    any_ell = True
        self.assertTrue(any_ell)


class TestDateline(unittest.TestCase):
    def test_dateline_system_handled(self):
        rng = np.random.default_rng(11)
        steps = list(range(0, 97, 6))
        members = []
        for i in range(16):
            # genesis ~178E, drifting east across 180 to ~ -176 (184E)
            centers = _system_member(rng, 8.0, 178.0, 0.2, 0.7, steps=steps,
                                     p0=1004, dp=-2, v0=25, dv=3,
                                     drift=(rng.normal(0, 1), rng.normal(0, 1)), spread=0.12)
            members.append({"id": f"M{i:02d}", "centers": centers})
        doc = {"n_members": 16, "run_steps": steps, "members": members}
        pmt, sp = T.per_member_tracks_from_centers(doc)
        out = T.assemble_tracks_doc(pmt, spec=_Spec(), cycle=CYCLE, n_members=16,
                                    spacing_h=sp, source_kind="self_detect")
        self.assertEqual(out["n_clusters"], 1)
        mt = out["clusters"][0]["mean_track"]
        # mean longitudes must be CONTINUOUS across the dateline (no +/-360 jump)
        lons = [p[2] for p in mt]
        self.assertTrue(all(abs(b - a) < 90 for a, b in zip(lons, lons[1:])),
                        f"mean track lon jumped across the dateline: {lons}")
        # and the system genuinely straddles 180 (some leads each side once wrapped)
        wrapped = [((lo + 180) % 360) - 180 for lo in lons]
        self.assertTrue(max(wrapped) > 170 or min(wrapped) < -170)


class TestBifurcatedMeanIsClusterAware(unittest.TestCase):
    """A single cluster that BIFURCATES: members ride a shared trunk, then fork into
    two well-separated groups at long range. The plain all-member geometric median
    lands in the EMPTY GAP between the groups; mean_track must instead follow ONE real
    branch - never the gap, never a thin-air point - at every lead."""

    @staticmethod
    def _bimodal_member_tracks():
        steps = list(range(0, 121, 6))
        split = 54                                   # leads >= this fork into two groups
        mt = {}
        for i in range(12):
            north = (i % 2 == 0)                     # even split: 6 north, 6 east
            fixes = []
            for s in steps:
                la = 15.0 + 0.05 * s                 # shared trunk
                lo = -140.0 - 0.05 * s
                if s >= split:                       # clean N vs E fork, gap grows with lead
                    d = s - split
                    la += (0.20 * d) if north else (0.02 * d)
                    lo += (-0.02 * d) if north else (0.22 * d)
                # tiny deterministic per-member jitter (each branch a tight blob, well
                # under the 4 deg mode-link gap) with NO rng - keeps the test hermetic
                la += 0.15 * ((i % 3) - 1)
                lo += 0.15 * (((i // 2) % 3) - 1)
                fixes.append([s, round(la, 3), round(lo, 3),
                              1000.0 - 0.25 * s, 25.0 + 0.25 * s])
            mt[f"M{i:02d}"] = fixes
        return mt, steps

    def test_scenario_is_genuinely_bimodal(self):
        mt, steps = self._bimodal_member_tracks()
        bl = T._by_lead(mt)
        pos = [(r[1], r[2]) for r in bl[max(steps)]]
        modes = T._lead_modes(pos)
        self.assertEqual(len(modes), 2, "last lead should split into exactly two modes")
        self.assertTrue(all(len(m) >= 4 for m in modes), "both branches well-populated")
        a = T.geometric_median([pos[i] for i in modes[0]])
        b = T.geometric_median([pos[i] for i in modes[1]])
        self.assertGreater(T.gc_deg(a[0], a[1], b[0], b[1]), 8.0, "branches well separated")

    def test_naive_all_member_mean_would_land_in_the_gap(self):
        # the bug we are fixing: a plain geometric median over ALL members sits in the
        # empty corridor between the two branches (far from every actual member).
        mt, steps = self._bimodal_member_tracks()
        pos = [(r[1], r[2]) for r in T._by_lead(mt)[max(steps)]]
        naive = T.geometric_median(pos)
        self.assertGreater(min(T.gc_deg(naive[0], naive[1], la, lo) for la, lo in pos),
                           3.0, "naive mean must be in the gap for the test to be meaningful")

    def test_mean_track_follows_one_real_branch(self):
        mt, steps = self._bimodal_member_tracks()
        bl = T._by_lead(mt)
        track = T.mean_track(mt)
        self.assertTrue(track, "mean track should not be empty")
        # 1) every mean point sits INSIDE a real member group at its lead (never the gap)
        for s, la, lo, n in track:
            members = [(r[1], r[2]) for r in bl[int(s)]]
            self.assertLess(min(T.gc_deg(la, lo, mla, mlo) for mla, mlo in members),
                            T.MEAN_MODE_LINK_DEG + 2.0,
                            f"mean point at lead {s} is in empty space")
            self.assertGreaterEqual(n, T.MEAN_SUPPORT_MIN_ABS)
        # 2) no cross-gap teleport between consecutive leads
        for (s0, la0, lo0, _), (s1, la1, lo1, _) in zip(track, track[1:]):
            self.assertLess(T.gc_deg(la0, lo0, la1, lo1),
                            T.MEAN_STEP_GATE_DEG + 2.0,
                            f"mean teleported between leads {s0} and {s1}")
        # 3) at the final lead the cluster-aware mean differs from the naive one (the
        #    naive one is in the gap; ours is on a branch)
        last = track[-1]
        naive = T.geometric_median([(r[1], r[2]) for r in bl[int(last[0])]])
        self.assertGreater(T.gc_deg(last[1], last[2], naive[0], naive[1]), 2.0)


class TestNativeSkipStageA(unittest.TestCase):
    def test_native_member_tracks_cluster_without_linkage(self):
        # native input: per-member tracks already separated by track_id
        rng = np.random.default_rng(5)
        steps = list(range(0, 97, 6))
        pmt = {}
        for i in range(20):
            tracks = []
            if i % 4 != 0:
                tracks.append(_system_member(rng, 14, -135, 0.3, -0.5, steps=steps,
                                             drift=(rng.normal(0, 1), rng.normal(0, 1)), spread=0.13))
            if i % 3 != 0:
                tracks.append(_system_member(rng, 9, -55, 0.1, -0.6, steps=steps,
                                             drift=(rng.normal(0, 1), rng.normal(0, 1)), spread=0.13))
            pmt[f"M{i:02d}"] = tracks
        out = T.assemble_tracks_doc(pmt, spec=_Spec(slug="fnv3", source_kind="track_csv"),
                                    cycle=CYCLE, n_members=20, spacing_h=6.0,
                                    source_kind="track_csv")
        self.assertEqual(out["n_clusters"], 2)
        self.assertTrue(all(c["member_count"] >= 3 for c in out["clusters"]))


class TestObsSupport(unittest.TestCase):
    def test_obs_rank_and_zscore(self):
        rng = np.random.default_rng(9)
        steps = list(range(0, 49, 6))
        mt = {}
        for i in range(15):
            mt[f"M{i:02d}"] = _system_member(rng, 12, -140, 0.3, -0.5, steps=steps,
                                             drift=(rng.normal(0, 1), rng.normal(0, 1)), spread=0.15)
        # obs right at the cluster mean -> low percentile + small mahalanobis
        bl = T._by_lead(mt)
        mlat, mlon = T.spherical_mean([(r[1], r[2]) for r in bl[24]])
        res = T.obs_support(mt, mlat, mlon, 24)
        self.assertIsNotNone(res)
        self.assertLess(res["percentile"], 60.0)
        self.assertEqual(res["matched_lead"], 24)
        if res["mahalanobis"] is not None:
            self.assertLess(res["mahalanobis"], 1.5)


# ---------------------------------------------------------------------------
# Real-cycle smokes (skip if the cached cycle file is absent)
# ---------------------------------------------------------------------------
class TestRealCycleSmoke(unittest.TestCase):
    @unittest.skipUnless(os.path.exists("/tmp/ecens18.json"), "no cached ecens cycle")
    def test_ecens_self_detect_smoke(self):
        doc = json.load(open("/tmp/ecens18.json"))
        pmt, sp = T.per_member_tracks_from_centers(doc)
        out = T.assemble_tracks_doc(pmt, spec=_Spec(), cycle=CYCLE,
                                    n_members=doc["n_members"], spacing_h=sp,
                                    source_kind="self_detect")
        self.assertGreaterEqual(out["n_clusters"], 1)
        self.assertGreaterEqual(out["n_member_tracks"], 1)
        for c in out["clusters"]:
            s = c["plume"]["vmax"]
            for i in range(len(s["lead"])):
                row = [s["p10"][i], s["p25"][i], s["p50"][i], s["p75"][i], s["p90"][i]]
                self.assertEqual(row, sorted(row))


if __name__ == "__main__":
    unittest.main()
