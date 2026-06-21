"""
Tests for the b-deck ANCHOR association layer (``enscenters.anchors`` +
``enscenters.tracking`` cluster-with-anchors). These pin the two real failure modes
of spatial-only clustering and prove the anchored pre-pass fixes BOTH while leaving
the no-anchor density path byte-identical:

  * OVER-SPLIT - one recurving/fanning system that the density method breaks into TWO
    clusters collapses to ONE under a single moving anchor.
  * UNDER-MERGE - two close systems the density method swallows into ONE split back
    into TWO under two anchors.
  * GENESIS - a system far from every anchor still clusters via density (new invests
    are not lost), coexisting with an anchored system.
  * REGRESSION - ``anchors=None`` reproduces the exact prior clustering.
  * FETCH - the live feed is parsed correctly and degrades to [] on any failure /
    stale cycle.

Hermetic: synthetic ensembles, no network.
"""
import datetime as dt
import unittest

import numpy as np

from enscenters import anchors as A
from enscenters import tracking as T


CYCLE = dt.datetime(2026, 6, 14, 18)
STEPS = list(range(0, 121, 6))


class _Spec:
    def __init__(self, slug="ecens", label="Test", source_kind="self_detect"):
        self.slug, self.label, self.source_kind = slug, label, source_kind


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------
def _line_member(rng, lat0, lon0, dlat6, dlon6, *, steps=STEPS, jitter=0.1,
                 p0=1004.0, dp=-2.0, v0=25.0, dv=3.0):
    """A clean moving low: lat/lon advance per 6 h step with small jitter."""
    out = []
    for k, s in enumerate(steps):
        lat = lat0 + dlat6 * k + rng.normal(0, jitter)
        lon = ((lon0 + dlon6 * k + rng.normal(0, jitter) + 180) % 360) - 180
        out.append([s, round(lat, 3), round(lon, 3),
                    round(p0 + dp * k, 1), round(max(10.0, v0 + dv * k), 1)])
    return out


def _bifurcating_system(n=16, seed=3):
    """ONE system: a shared trunk (leads 0..12) that FORKS symmetrically about due
    west at lead >= 18 - half the members recurve north, half dive south, diverging to
    ~20 deg apart by lead 120. The fork dominates the full-track mean separation, so
    the spatial method splits it into TWO clusters (the over-split bug)."""
    rng = np.random.default_rng(seed)
    lat0, lon0 = 15.0, -150.0
    west6 = -1.2                                     # ~due-west drift per 6 h
    fork_lat6 = 0.95                                 # per-step latitude divergence
    members = []
    for i in range(n):
        north = (i % 2 == 0)
        fixes = []
        for k, s in enumerate(STEPS):
            lat = lat0 + rng.normal(0, 0.1)
            lon = lon0 + west6 * k + rng.normal(0, 0.1)
            if k >= 3:                               # fork after the trunk
                d = k - 3
                lat += (fork_lat6 * d) if north else (-fork_lat6 * d)
            fixes.append([s, round(lat, 3), round(((lon + 180) % 360) - 180, 3),
                          round(1004 - 2.0 * k, 1), round(25 + 2.5 * k, 1)])
        members.append({"id": f"M{i:02d}", "centers": fixes})
    return {"n_members": n, "run_steps": STEPS, "members": members}, (lat0, lon0, west6)


def _two_close_systems(n=20, seed=4, sep_deg=5.0):
    """TWO distinct systems only ``sep_deg`` apart, moving in parallel - close enough
    that the density method unions + merges them into ONE cluster (the under-merge
    bug)."""
    rng = np.random.default_rng(seed)
    members = []
    for i in range(n):
        centers = []
        if i % 5 != 0 or i == 0:                     # most members carry both
            centers += _line_member(rng, 12.0, -120.0, 0.2, -0.5, jitter=0.18)
        centers += _line_member(rng, 12.0 + sep_deg, -120.0, 0.2, -0.5, jitter=0.18,
                                p0=1006, v0=22)
        members.append({"id": f"M{i:02d}", "centers": centers})
    return {"n_members": n, "run_steps": STEPS, "members": members}


def _anchor(sid, name, lat_fn, lon_fn, *, leads=STEPS, is_invest=False):
    """Build a moving-anchor payload entry from lat/lon functions of the lead step."""
    return {"sid": sid, "name": name, "is_invest": is_invest,
            "pos": {int(s): (lat_fn(s), lon_fn(s)) for s in leads}}


def _doc_clusters(doc, anchors):
    pmt, sp = T.per_member_tracks_from_centers(doc)
    out = T.assemble_tracks_doc(pmt, spec=_Spec(), cycle=CYCLE,
                                n_members=doc["n_members"], spacing_h=sp,
                                source_kind="self_detect", anchors=anchors)
    return out


# ---------------------------------------------------------------------------
# Great-circle forward progress
# ---------------------------------------------------------------------------
class TestForwardProgress(unittest.TestCase):
    def test_destination_matches_distance(self):
        # 5 deg due north from the equator lands at 5N, same lon.
        la, lo = A.gc_destination(0.0, 0.0, 0.0, 5.0)
        self.assertAlmostEqual(la, 5.0, places=4)
        self.assertAlmostEqual(lo, 0.0, places=4)
        # and the arc back is 5 deg
        self.assertAlmostEqual(T.gc_deg(0, 0, la, lo), 5.0, places=4)

    def test_anchor_persistence_motion(self):
        # anchor moving due west at 0.2 deg/h from (10, -140); at +24 h it is 4.8 deg
        # of arc further west, latitude ~unchanged.
        anc = A.Anchor(sid="AL05", name="X", lat1=10.0, lon1=-140.0,
                       bearing_deg=270.0, speed_deg_h=0.2, age1_h=0.0)
        la, lo = anc.position_at(24)
        self.assertAlmostEqual(T.gc_deg(10, -140, la, lo), 4.8, places=2)
        self.assertLess(abs(la - 10.0), 0.3)
        self.assertLess(lo, -140.0)

    def test_anchor_dateline_safe(self):
        # crossing 180 going west must wrap, never jump by ~360
        anc = A.Anchor(sid="WP07", name="X", lat1=12.0, lon1=178.0,
                       bearing_deg=270.0, speed_deg_h=0.3, age1_h=0.0)
        la, lo = anc.position_at(48)                 # ~14.4 deg west of 178E -> ~163.6E
        self.assertTrue(-180.0 <= lo <= 180.0)
        self.assertGreater(lo, 150.0)                # stayed in the eastern hemisphere
        self.assertAlmostEqual(T.gc_deg(12, 178, la, lo), 14.4, places=1)

    def test_age_offset_anchors_on_latest_fix(self):
        # latest fix is 6 h AFTER init (age1_h=6): at lead 6 the anchor sits exactly on
        # the fix; at lead 0 it is one step BEHIND (east of) it.
        anc = A.Anchor(sid="AL05", name="X", lat1=10.0, lon1=-140.0,
                       bearing_deg=270.0, speed_deg_h=0.25, age1_h=6.0)
        la6, lo6 = anc.position_at(6)
        self.assertAlmostEqual(la6, 10.0, places=6)
        self.assertAlmostEqual(lo6, -140.0, places=6)
        la0, lo0 = anc.position_at(0)
        self.assertGreater(lo0, -140.0)              # 6 h before the fix -> further east


# ---------------------------------------------------------------------------
# global_storms.geojson -> anchors
# ---------------------------------------------------------------------------
class TestGeojsonParse(unittest.TestCase):
    @staticmethod
    def _feature_collection():
        # two active systems: AL05 (named, moving WNW) + 91L (invest, moving W).
        def obs(sid, t, lat, lon):
            return {"type": "Feature", "geometry": {"type": "Point",
                    "coordinates": [lon, lat]},
                    "properties": {"kind": "observation", "storm_id": sid,
                                   "time_iso": t}}

        def marker(sid, lat, lon, designation, mtype, last):
            return {"type": "Feature", "geometry": {"type": "Point",
                    "coordinates": [lon, lat]},
                    "properties": {"kind": "active_marker", "storm_id": sid,
                                   "name": designation, "designation": designation,
                                   "marker_type": mtype, "last_fix": last}}
        return {"type": "FeatureCollection", "features": [
            obs("al052026", "2026-06-14T06:00:00Z", 13.0, -58.0),
            obs("al052026", "2026-06-14T12:00:00Z", 13.5, -59.5),
            obs("al052026", "2026-06-14T18:00:00Z", 14.0, -61.0),
            marker("al052026", 14.0, -61.0, "AL05", "hurricane", "2026-06-14T18:00:00Z"),
            obs("ep912026", "2026-06-14T12:00:00Z", 11.0, -120.0),
            obs("ep912026", "2026-06-14T18:00:00Z", 11.1, -121.4),
            marker("ep912026", 11.1, -121.4, "91E", "invest_x", "2026-06-14T18:00:00Z"),
            # a non-active track-only feature must be ignored
            {"type": "Feature", "geometry": {"type": "LineString",
             "coordinates": [[0, 0], [1, 1]]},
             "properties": {"kind": "track", "storm_id": "x"}},
        ]}

    def test_builds_one_anchor_per_active_system(self):
        anchors = A.anchors_from_geojson(self._feature_collection(), CYCLE)
        self.assertEqual(len(anchors), 2)
        sids = {a.sid for a in anchors}
        self.assertEqual(sids, {"AL05", "91E"})

    def test_motion_and_invest_flag(self):
        anchors = {a.sid: a for a in A.anchors_from_geojson(self._feature_collection(), CYCLE)}
        al = anchors["AL05"]
        # latest fix is the init time -> age ~0, anchored on (14,-61)
        self.assertAlmostEqual(al.lat1, 14.0, places=3)
        self.assertAlmostEqual(al.lon1, -61.0, places=3)
        self.assertAlmostEqual(al.age1_h, 0.0, places=3)
        self.assertGreater(al.speed_deg_h, 0.0)      # it is moving
        self.assertFalse(al.is_invest)
        self.assertTrue(anchors["91E"].is_invest)
        # projected forward it keeps heading WNW (lat up a touch, lon west)
        la, lo = al.position_at(24)
        self.assertGreater(la, 14.0)
        self.assertLess(lo, -61.0)

    def test_fetch_graceful_on_bad_url(self):
        got = A.fetch_global_anchors(CYCLE, url="http://127.0.0.1:0/nope.json",
                                     now=CYCLE, progress=lambda *a, **k: None)
        self.assertEqual(got, [])

    def test_fetch_skips_stale_cycle(self):
        old = CYCLE - dt.timedelta(days=5)
        got = A.fetch_global_anchors(old, now=CYCLE, progress=lambda *a, **k: None)
        self.assertEqual(got, [])


# ---------------------------------------------------------------------------
# THE TWO FAILURE MODES
# ---------------------------------------------------------------------------
class TestOverSplit(unittest.TestCase):
    """One bifurcating system: density over-splits into TWO; one moving anchor (down
    the fork bisector) holds it as ONE."""

    def setUp(self):
        self.doc, (lat0, lon0, west6) = _bifurcating_system()
        # anchor = the trunk continued straight (the fork's bisector): due west at the
        # genesis latitude. west6 deg / 6 h -> west6/6 deg/h.
        self.anchor = _anchor("AL09", "BISECTOR",
                              lambda s: lat0,
                              lambda s: ((lon0 + (west6 / 6.0) * s + 180) % 360) - 180)

    def test_density_alone_over_splits(self):
        # baseline (no anchor) reproduces the bug: the single system reads as TWO.
        self.assertEqual(_doc_clusters(self.doc, None)["n_clusters"], 2)

    def test_anchor_collapses_to_one(self):
        out = _doc_clusters(self.doc, [self.anchor])
        self.assertEqual(out["n_clusters"], 1)
        self.assertEqual(out["clusters"][0].get("anchor", {}).get("sid"), "AL09")
        # the one cluster holds (nearly) all members of both branches
        self.assertGreaterEqual(out["clusters"][0]["member_count"], 14)


class TestUnderMerge(unittest.TestCase):
    """Two close systems: density under-merges into ONE; two anchors split them into
    TWO."""

    def setUp(self):
        self.doc = _two_close_systems(sep_deg=5.0)
        self.anchors = [
            _anchor("AL01", "SOUTH", lambda s: 12.0,
                    lambda s: ((-120.0 - 0.5 / 6.0 * s + 180) % 360) - 180),
            _anchor("AL02", "NORTH", lambda s: 17.0,
                    lambda s: ((-120.0 - 0.5 / 6.0 * s + 180) % 360) - 180),
        ]

    def test_density_alone_under_merges(self):
        self.assertEqual(_doc_clusters(self.doc, None)["n_clusters"], 1)

    def test_two_anchors_split_into_two(self):
        out = _doc_clusters(self.doc, self.anchors)
        self.assertEqual(out["n_clusters"], 2)
        sids = sorted(c.get("anchor", {}).get("sid") for c in out["clusters"])
        self.assertEqual(sids, ["AL01", "AL02"])
        # each anchored system has its own well-supported membership
        for c in out["clusters"]:
            self.assertGreaterEqual(c["member_count"], 3)


class TestGenesisCoexists(unittest.TestCase):
    """An anchored system + a brand-new system FAR from any anchor: the genesis system
    is NOT lost - it clusters by density and carries no anchor label."""

    def test_anchored_plus_genesis(self):
        rng = np.random.default_rng(8)
        members = []
        for i in range(20):
            centers = _line_member(rng, 14.0, -135.0, 0.25, -0.5, jitter=0.15)   # anchored
            if i % 2 == 0:                            # half also carry a far genesis low
                centers += _line_member(rng, 8.0, 130.0, 0.2, 0.4, jitter=0.15,
                                        p0=1006, v0=22)
            members.append({"id": f"M{i:02d}", "centers": centers})
        doc = {"n_members": 20, "run_steps": STEPS, "members": members}
        anc = [_anchor("EP07", "ANCHORED", lambda s: 14.0 + 0.25 / 6.0 * s,
                       lambda s: ((-135.0 - 0.5 / 6.0 * s + 180) % 360) - 180)]
        out = _doc_clusters(doc, anc)
        self.assertEqual(out["n_clusters"], 2)
        labeled = [c for c in out["clusters"] if c.get("anchor")]
        genesis = [c for c in out["clusters"] if not c.get("anchor")]
        self.assertEqual(len(labeled), 1)
        self.assertEqual(len(genesis), 1)
        self.assertEqual(labeled[0]["anchor"]["sid"], "EP07")
        # the genesis cluster is the far west-Pacific system
        self.assertLess(T.gc_deg(genesis[0]["genesis"]["lat"],
                                 genesis[0]["genesis"]["lon"], 8, 130), 8)


class TestRegression(unittest.TestCase):
    """anchors=None / empty must reproduce the exact prior clustering."""

    def test_none_equals_empty_equals_prior(self):
        doc = _two_close_systems(sep_deg=5.0)
        pmt, sp = T.per_member_tracks_from_centers(doc)
        flat = [{"member": m, "fixes": [list(r) for r in t]}
                for m in sorted(pmt) for t in pmt[m] if len(t) >= 2]
        # the public wrapper with no anchors == cluster_with_anchors(None) == density
        base = T.cluster_tracks(flat, doc["n_members"])
        none = T.cluster_tracks(flat, doc["n_members"], anchors=None)
        empty = T.cluster_tracks(flat, doc["n_members"], anchors=[])
        self.assertEqual(base, none)
        self.assertEqual(base, empty)

    def test_far_anchor_is_inert(self):
        # an anchor nowhere near the ensemble leaves the density result unchanged.
        doc = _two_close_systems(sep_deg=5.0)
        far = [_anchor("ZZ99", "FAR", lambda s: -60.0, lambda s: 30.0)]
        self.assertEqual(_doc_clusters(doc, None)["n_clusters"],
                         _doc_clusters(doc, far)["n_clusters"])


if __name__ == "__main__":
    unittest.main()
