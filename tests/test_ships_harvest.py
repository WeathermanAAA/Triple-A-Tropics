#!/usr/bin/env python3
"""The SHIPS raw archiver (``guidance.harvest_ships``).

This job exists to not lose data that cannot be re-fetched later, so the tests
are weighted toward the ways an archiver silently loses things: capturing an
empty body over a real one, treating a missing sibling as a failure, letting
test-deck fixtures into the archive, or reporting success after storing
nothing. No network and no R2 - the opener and the S3 client are injected.

Run: ``python -m unittest discover tests``
"""
import io
import json
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from guidance import harvest_ships as hs  # noqa: E402


def _listing(stems, suffix):
    """A directory listing page like the one NHC serves."""
    rows = "".join(f'<a href="{s}{suffix}">{s}{suffix}</a>  12-Jul-2026 03:14  9179\n'
                   for s in stems)
    return f"<html><body><pre>{rows}</pre></body></html>".encode()


class FakeClient:
    """The slice of the S3 client the harvester uses."""

    def __init__(self, objects=None, index=None):
        self.objects = dict(objects or {})
        self.puts = []
        self._index = index

    def get_object(self, Bucket, Key):
        from botocore.exceptions import ClientError
        if Key == hs.INDEX_KEY:
            if self._index is None:
                raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
            return {"Body": io.BytesIO(self._index)}
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}

    def list_objects_v2(self, **kw):
        pre = kw.get("Prefix", "")
        items = [{"Key": k, "Size": len(v)} for k, v in self.objects.items()
                 if k.startswith(pre)]
        return {"Contents": items, "IsTruncated": False}

    def put_object(self, Bucket, Key, Body, **kw):
        self.puts.append((Key, Body))
        self.objects[Key] = Body
        if Key == hs.INDEX_KEY:
            self._index = Body


class TestStemParsing(unittest.TestCase):

    def test_two_digit_storm_year(self):
        """The live filename carries a TWO-digit storm year - a four-digit
        assumption matches nothing on the feed."""
        s = hs.parse_stem("26072818EP0726")
        self.assertIsNotNone(s)
        self.assertEqual(s.dtg, "2026072818")
        self.assertEqual(s.basin, "EP")
        self.assertEqual(s.cy, 7)
        self.assertEqual(s.year, 2026)

    def test_sid_matches_the_cyclolab_key(self):
        """Keying the archive the same way the site keys storms is what makes
        it joinable later without a lookup table."""
        self.assertEqual(hs.parse_stem("26072818EP0726").sid, "NHC_EP072026")
        self.assertEqual(hs.parse_stem("26071100CP9026").sid, "NHC_CP902026")
        self.assertEqual(hs.parse_stem("26072400AL0226").sid, "NHC_AL022026")

    def test_r2_key_is_storm_and_cycle_scoped(self):
        s = hs.parse_stem("26072818EP0726")
        self.assertEqual(
            s.key("ships", "_ships.txt"),
            "ships/NHC_EP072026/2026072818/26072818EP0726_ships.txt")

    def test_only_ships_basins(self):
        """No JTWC SHIPS files exist; a WP stem is not a gap, it is not a
        thing."""
        self.assertIsNone(hs.parse_stem("26072818WP1226"))
        for b in ("AL", "EP", "CP"):
            self.assertIsNotNone(hs.parse_stem(f"26072818{b}0126"), b)

    def test_impossible_dates_rejected(self):
        self.assertIsNone(hs.parse_stem("26073218EP0726"))   # 32nd
        self.assertIsNone(hs.parse_stem("26130118EP0726"))   # month 13

    def test_malformed_rejected(self):
        for bad in ("", "nonsense", "2607281EP0726", "26072818EP072",
                    "26072818ep0726"):
            self.assertIsNone(hs.parse_stem(bad), bad)

    def test_test_decks_are_flagged(self):
        """cy 80-89 are GSTEST/ATCFTEST fixtures with absurd values."""
        for cy in (80, 81, 85, 89):
            self.assertTrue(hs.parse_stem(f"26072818AL{cy:02d}26").is_test_deck)
        for cy in (1, 7, 79, 90, 99):
            self.assertFalse(hs.parse_stem(f"26072818AL{cy:02d}26").is_test_deck)


class TestListing(unittest.TestCase):

    def _opener(self, ships, lsdiag):
        def f(url, timeout=60.0):
            if "stext" in url:
                return _listing(ships, "_ships.txt")
            return _listing(lsdiag, "_lsdiag.dat")
        return f

    def test_test_decks_never_enter_the_listing(self):
        """Filtered at ingest, so a later caller cannot forget."""
        up = hs.list_upstream(self._opener(
            ["26072818EP0726", "26072818AL8126"], ["26072818EP0726"]))
        self.assertIn("26072818EP0726", up["ships"])
        self.assertNotIn("26072818AL8126", up["ships"])

    def test_sides_are_listed_independently(self):
        up = hs.list_upstream(self._opener(
            ["26071100CP9026"], ["26072400AL0226"]))
        self.assertEqual(list(up["ships"]), ["26071100CP9026"])
        self.assertEqual(list(up["lsdiag"]), ["26072400AL0226"])


class TestPlan(unittest.TestCase):

    def _up(self, ships, lsdiag):
        return {"ships": {s: hs.parse_stem(s) for s in ships},
                "lsdiag": {s: hs.parse_stem(s) for s in lsdiag}}

    def test_unpaired_sibling_is_still_fetched(self):
        """~3% of live stems have only one side. Requiring both would silently
        drop them - including the 7 lsdiag-only stems measured on 2026-07-28,
        which are the ONLY source of their low/high RH rows."""
        up = self._up([], ["26072400AL0226"])
        todo = hs.plan(up, {"archived": {}})
        self.assertEqual(todo, [("lsdiag", up["lsdiag"]["26072400AL0226"])])

    def test_each_side_decided_separately(self):
        """A stem whose bulletin is archived still yields its sibling."""
        stem = "26072818EP0726"
        up = self._up([stem], [stem])
        todo = hs.plan(up, {"archived": {stem: {"ships": 100}}})
        self.assertEqual([t[0] for t in todo], ["lsdiag"])

    def test_already_archived_is_not_refetched(self):
        """Immutable archive: a re-fetch risks replacing a good capture with a
        truncated read, and buys nothing."""
        stem = "26072818EP0726"
        up = self._up([stem], [stem])
        todo = hs.plan(up, {"archived": {stem: {"ships": 1, "lsdiag": 2}}})
        self.assertEqual(todo, [])

    def test_oldest_first(self):
        """If a run is cut short the archive advances forward in time rather
        than leaving a hole behind the newest captures."""
        up = self._up(["26072818EP0726", "26060112EP9026"], [])
        todo = hs.plan(up, {"archived": {}})
        self.assertEqual([t[1].raw for t in todo],
                         ["26060112EP9026", "26072818EP0726"])


class TestHarvest(unittest.TestCase):

    def _opener(self, bodies, ships=("26072818EP0726",), lsdiag=("26072818EP0726",)):
        def f(url, timeout=60.0):
            if url.endswith("/stext/"):
                return _listing(ships, "_ships.txt")
            if url.endswith("/lsdiag/"):
                return _listing(lsdiag, "_lsdiag.dat")
            name = url.rsplit("/", 1)[-1]
            if name in bodies:
                if isinstance(bodies[name], Exception):
                    raise bodies[name]
                return bodies[name]
            raise RuntimeError("404 Not Found")
        return f

    def test_stores_both_sides_and_updates_the_index(self):
        c = FakeClient()
        s = hs.harvest(opener=self._opener({
            "26072818EP0726_ships.txt": b"SHIPS BODY",
            "26072818EP0726_lsdiag.dat": b"LSDIAG BODY"}), client=c)
        self.assertEqual(s["stored"], 2)
        keys = [k for k, _ in c.puts if k != hs.INDEX_KEY]
        self.assertIn("ships/NHC_EP072026/2026072818/26072818EP0726_ships.txt", keys)
        self.assertIn("ships/NHC_EP072026/2026072818/26072818EP0726_lsdiag.dat", keys)
        idx = json.loads(c.objects[hs.INDEX_KEY])
        self.assertEqual(idx["archived"]["26072818EP0726"],
                         {"ships": 10, "lsdiag": 11})

    def test_an_empty_body_is_never_archived(self):
        """An empty capture would permanently mask the real file: the stem
        would read as archived and never be retried, and upstream will be gone
        at season rollover."""
        c = FakeClient()
        s = hs.harvest(opener=self._opener({
            "26072818EP0726_ships.txt": b"",
            "26072818EP0726_lsdiag.dat": b"LSDIAG"}), client=c)
        self.assertEqual(s["stored"], 1)
        self.assertEqual(s["failed"], 1)
        idx = json.loads(c.objects[hs.INDEX_KEY])
        self.assertNotIn("ships", idx["archived"]["26072818EP0726"])

    def test_one_fetch_failure_does_not_sink_the_run(self):
        c = FakeClient()
        s = hs.harvest(opener=self._opener({
            "26072818EP0726_ships.txt": RuntimeError("500 boom"),
            "26072818EP0726_lsdiag.dat": b"LSDIAG"}), client=c)
        self.assertEqual(s["stored"], 1)
        self.assertEqual(s["failed"], 1)

    def test_index_is_rebuilt_when_absent(self):
        """Losing the index costs one slow run, not the archive."""
        c = FakeClient(objects={
            "ships/NHC_EP072026/2026072818/26072818EP0726_ships.txt": b"OLD"})
        s = hs.harvest(opener=self._opener({
            "26072818EP0726_lsdiag.dat": b"LSDIAG"}), client=c)
        self.assertEqual(s["archive_stems"], 1)
        self.assertEqual(s["stored"], 1)   # only the missing sibling

    def test_corrupt_index_is_rebuilt_not_trusted(self):
        c = FakeClient(index=b"{not json")
        c.objects["ships/NHC_EP072026/2026072818/26072818EP0726_ships.txt"] = b"OLD"
        s = hs.harvest(opener=self._opener({
            "26072818EP0726_lsdiag.dat": b"LSDIAG"}), client=c)
        self.assertEqual(s["stored"], 1)

    def test_steady_state_stores_nothing(self):
        idx = json.dumps({"version": 1, "archived": {
            "26072818EP0726": {"ships": 5, "lsdiag": 6}}}).encode()
        c = FakeClient(index=idx)
        s = hs.harvest(opener=self._opener({}), client=c)
        self.assertEqual(s["planned"], 0)
        self.assertEqual(s["stored"], 0)
        self.assertEqual(c.puts, [], "a no-op run must not rewrite the index")

    def test_dry_run_touches_nothing(self):
        c = FakeClient()
        s = hs.harvest(opener=self._opener({}), client=c, dry_run=True)
        self.assertTrue(s["dry_run"])
        self.assertEqual(c.puts, [])

    def test_limit_caps_the_run(self):
        c = FakeClient()
        s = hs.harvest(opener=self._opener({
            "26072818EP0726_ships.txt": b"A",
            "26072818EP0726_lsdiag.dat": b"B"}), client=c, limit=1)
        self.assertEqual(s["stored"], 1)

    def test_stored_objects_are_marked_immutable(self):
        c = FakeClient()
        puts = []
        orig = c.put_object

        def spy(**kw):
            puts.append(kw)
            return orig(**kw)
        c.put_object = spy
        hs.harvest(opener=self._opener({
            "26072818EP0726_ships.txt": b"A"}), client=c)
        data = [p for p in puts if p["Key"] != hs.INDEX_KEY]
        self.assertTrue(data)
        self.assertIn("immutable", data[0]["CacheControl"])


if __name__ == "__main__":
    unittest.main()
