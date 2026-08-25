#!/usr/bin/env python3
"""Tests for the box-side R2 write kill switch (PHASE0 spec, 2026-08-25),
TAT side. Hermetic: no network, no boto client, no docker.

  * tat_killswitch module rules, driven through the module's injectable
    env / fetcher / clocks: env off, the fleet/ allowlist, disabled URL,
    a tripped doc blocks in ANY mode (armed auto-trip or manual /trip),
    an alert doc with writes enabled allows, /disarm alone never re-enables,
    stale worker_ts allows, fetch error keeps the last good document then
    fails open, unparseable allows, the
    real urllib path against file:// documents, the import guard, log
    throttling, a thread-safety smoke;
  * the module is importable the way the overlays pollers run the
    generators (cwd = repo root, `python <GEN>` / `python scripts/<x>.py`);
  * one integration test per guarded writer -- class R2Store in
    generate_mimic_tpw / generate_mrms_overlay / generate_nhc_overlay /
    generate_sfc_analysis / generate_metar_obs / generate_uhr_ascat, the
    shared sarobs.store.R2Store behind generate_sar_winds /
    generate_sar_salinity / generate_hy2_winds, scripts/recon_r2_publish.py
    (_put) and scripts/ascat_r2_publish.py (the put loop) -- with a recording
    stub in place of the boto client: puts are skipped when blocked, happen
    when allowed, deletes are never touched (the two scripts' reaps are tied
    to the manifest having landed, never to the switch), fleet/* always
    lands, and a missing module (import guard -> None) means allowed;
  * a source-level net: every guarded put calls writes_allowed, no delete
    method does, so a later edit cannot silently drop the guard.

Run: python -m unittest tests.test_killswitch
"""
from __future__ import annotations

import ast
import datetime as dt
import importlib
import importlib.util
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tat_killswitch as K  # noqa: E402

UTC = dt.timezone.utc
STATE_URL = "https://cdn.example.invalid/fleet/breaker.json"

# The production singleton was built from THIS process's env at import (the
# default URL is the live CDN). Re-seed it hermetically before any test can
# reach the public functions, and again after every test (tearDown below).
K._reset_for_tests(env={"TAT_BREAKER_STATE_URL": ""})
# A handler on the logger keeps the print fallback (for unconfigured scripts)
# from spraying every expected DISABLED/ENABLED line onto the test stderr;
# the fallback itself is exercised explicitly in LoggingTest.
K._log.addHandler(logging.NullHandler())

GENERATOR_MODULES = (
    "generate_mimic_tpw", "generate_mrms_overlay", "generate_nhc_overlay",
    "generate_sfc_analysis", "generate_metar_obs", "generate_uhr_ascat",
)
SHARED_STORE_MODULE = "sarobs.store"     # generate_sar_winds / sar_salinity / hy2_winds
SCRIPTS = ("scripts/recon_r2_publish.py", "scripts/ascat_r2_publish.py")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
class Clock:
    """Wall + monotonic clocks that only move when a test says so."""
    def __init__(self, wall=1_800_000_000.0, mono=1000.0):
        self.t_wall, self.t_mono = wall, mono

    def wall(self):
        return self.t_wall

    def mono(self):
        return self.t_mono

    def tick(self, s):
        self.t_wall += s
        self.t_mono += s

    def iso(self, offset_s=0.0):
        return dt.datetime.fromtimestamp(self.t_wall + offset_s, UTC).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")


class Fetcher:
    """Stub breaker-document fetch: returns ``doc`` (or calls it) or raises
    ``exc``; counts calls so the poll cadence can be asserted."""
    def __init__(self, doc=None, exc=None):
        self.doc, self.exc, self.calls = doc, exc, 0

    def __call__(self):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.doc() if callable(self.doc) else self.doc


def status_doc(clock, mode="armed", writes_enabled=False, age_s=0.0, **extra):
    """A status-contract v1 document as the heartbeat mirrors it."""
    d = {"v": 1, "mode": mode, "writes_enabled": writes_enabled,
         "tripped_at": None if writes_enabled else clock.iso(-300),
         "trip_reason": None if writes_enabled else "rate_1h=200000",
         "worker_ts": clock.iso(-age_s), "mirrored_by": "box1"}
    d.update(extra)
    return d


def make(env=None, fetch=None, clock=None):
    clock = clock or Clock()
    env = {"TAT_BREAKER_STATE_URL": STATE_URL, **(env or {})}
    return K._Switch(env=env, fetch=fetch, wall=clock.wall, mono=clock.mono), clock


class LogCapture:
    """Every line tat.killswitch emits through the logger."""
    def __enter__(self):
        self.lines = []
        self.h = logging.Handler()
        self.h.emit = lambda r: self.lines.append(r.getMessage())
        K._log.addHandler(self.h)
        return self

    def __exit__(self, *a):
        K._log.removeHandler(self.h)


class _NoSuchKey(Exception):
    pass


class _RecorderS3:
    """The subset of the boto3 S3 client the writers touch, recording every
    call. ``objects`` answers get_object; ``listing`` answers list_objects_v2
    by prefix (for the recon reap)."""
    def __init__(self, objects=None, listing=None):
        self.puts, self.deletes, self.bulk_deletes, self.lists = [], [], [], []
        self.objects = dict(objects or {})
        self.listing = dict(listing or {})
        self.exceptions = types.SimpleNamespace(NoSuchKey=_NoSuchKey)

    def put_object(self, **kw):
        self.puts.append(kw)

    def delete_object(self, **kw):
        self.deletes.append(kw)

    def delete_objects(self, **kw):
        self.bulk_deletes.append(kw)

    def get_object(self, **kw):
        if kw["Key"] in self.objects:
            return {"Body": io.BytesIO(self.objects[kw["Key"]])}
        raise _NoSuchKey(kw["Key"])

    def list_objects_v2(self, **kw):
        self.lists.append(kw)
        keys = self.listing.get(kw["Prefix"], [])
        return {"Contents": [{"Key": k} for k in keys], "IsTruncated": False}

    @property
    def put_keys(self):
        return [p["Key"] for p in self.puts]


def _import_or_skip(name):
    try:
        return importlib.import_module(name)
    except ImportError as e:  # a missing heavy dep (numpy) on a thin box
        raise unittest.SkipTest(f"{name}: {e}")


def _load_script(rel):
    """Load a scripts/*.py by path (they are not on any package path)."""
    spec = importlib.util.spec_from_file_location(Path(rel).stem, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class HermeticBase(unittest.TestCase):
    def tearDown(self):
        K._reset_for_tests(env={"TAT_BREAKER_STATE_URL": ""})


# --------------------------------------------------------------------------- #
# module rules
# --------------------------------------------------------------------------- #
class EnvRuleTest(HermeticBase):
    def test_off_values_block_any_case_and_whitespace(self):
        for v in ("0", "false", "FALSE", "off", "Off", "no", " NO "):
            sw, _ = make(env={"TAT_R2_WRITES": v})
            self.assertFalse(sw.writes_allowed("radar/mrms/conus/x.webp"), v)
            self.assertEqual(sw.reason(), f"env TAT_R2_WRITES={v}")

    def test_on_or_unset_allows(self):
        for env in ({}, {"TAT_R2_WRITES": "1"}, {"TAT_R2_WRITES": "on"},
                    {"TAT_R2_WRITES": ""}):
            sw, _ = make(env={"TAT_BREAKER_STATE_URL": "", **env})
            self.assertTrue(sw.writes_allowed("radar/x"), env)
            self.assertIsNone(sw.reason())

    def test_env_is_read_once_at_import(self):
        sw, _ = make(env={"TAT_BREAKER_STATE_URL": "", "TAT_R2_WRITES": "1"})
        with mock.patch.dict(os.environ, {"TAT_R2_WRITES": "0"}):
            self.assertTrue(sw.writes_allowed("radar/x"))
        sw2 = K._reset_for_tests(env={"TAT_BREAKER_STATE_URL": "", "TAT_R2_WRITES": "0"})
        with mock.patch.dict(os.environ, {"TAT_R2_WRITES": "1"}):
            self.assertFalse(K.writes_allowed("radar/x"))
        self.assertIs(sw2, K._SW)

    def test_module_public_functions_route_to_the_singleton(self):
        K._reset_for_tests(env={"TAT_BREAKER_STATE_URL": "", "TAT_R2_WRITES": "0"})
        self.assertFalse(K.writes_allowed("radar/x"))
        self.assertEqual(K.reason(), "env TAT_R2_WRITES=0")
        st = K.state()
        self.assertFalse(st["writes_allowed"])
        self.assertEqual(st["env_TAT_R2_WRITES"], "0")


class AllowlistTest(HermeticBase):
    def test_fleet_prefix_beats_env(self):
        sw, _ = make(env={"TAT_R2_WRITES": "0"})
        self.assertTrue(sw.writes_allowed("fleet/box1.json"))
        self.assertTrue(sw.writes_allowed("fleet/breaker.json"))
        self.assertTrue(sw.writes_allowed("fleet/index.json"))
        self.assertFalse(sw.writes_allowed("fleetx/box1.json"))
        self.assertFalse(sw.writes_allowed("x/fleet/box1.json"))

    def test_fleet_prefix_beats_armed_doc(self):
        clock = Clock()
        sw, _ = make(fetch=Fetcher(doc=status_doc(clock)), clock=clock)
        self.assertFalse(sw.writes_allowed("radar/x"))
        self.assertTrue(sw.writes_allowed("fleet/box1.json"))

    def test_allowlisted_keys_never_fetch_or_count(self):
        f = Fetcher(doc=status_doc(Clock()))
        sw, _ = make(fetch=f)
        for _ in range(5):
            self.assertTrue(sw.writes_allowed("fleet/box2.json"))
        self.assertEqual(f.calls, 0)
        self.assertEqual(sw.state()["dropped_total"], 0)

    def test_empty_and_non_string_keys_are_judged_not_allowlisted(self):
        sw, _ = make(env={"TAT_R2_WRITES": "0"})
        self.assertFalse(sw.writes_allowed(""))
        self.assertFalse(sw.writes_allowed(None))
        self.assertFalse(sw.writes_allowed(b"fleet/x"))   # bytes: judged, not allowlisted
        self.assertFalse(sw.writes_allowed(42))


class BreakerDocTest(HermeticBase):
    def test_disabled_url_never_fetches(self):
        f = Fetcher(doc=status_doc(Clock()))
        for url in ("", "   "):
            sw, _ = make(env={"TAT_BREAKER_STATE_URL": url}, fetch=f)
            self.assertTrue(sw.writes_allowed("radar/x"))
            self.assertIsNone(sw.state()["state_url"])
        self.assertEqual(f.calls, 0)

    def test_default_url_is_the_cdn_mirror(self):
        sw = K._Switch(env={}, fetch=Fetcher(exc=OSError("no net")))
        self.assertEqual(sw._url, "https://cdn.triple-a-tropics.com/fleet/breaker.json")
        self.assertEqual(K.DEFAULT_STATE_URL, sw._url)

    def test_armed_and_tripped_blocks(self):
        clock = Clock()
        sw, _ = make(fetch=Fetcher(doc=status_doc(clock)), clock=clock)
        self.assertFalse(sw.writes_allowed("radar/x"))
        self.assertIn("armed", sw.reason())
        self.assertIn("rate_1h=200000", sw.reason())

    def test_alert_mode_with_writes_enabled_allows(self):
        clock = Clock()
        sw, _ = make(fetch=Fetcher(doc=status_doc(clock, mode="alert",
                                                  writes_enabled=True)), clock=clock)
        self.assertTrue(sw.writes_allowed("radar/x"))
        self.assertIsNone(sw.reason())

    def test_manual_trip_blocks_in_any_mode(self):
        """The Worker's POST /trip sets writes_enabled=false WITHOUT changing
        mode (works in any mode); alert mode never sets false on its own, so
        a fresh false is always an explicit STOP and mode is not consulted."""
        clock = Clock()
        for mode in ("alert", "armed"):
            sw, _ = make(fetch=Fetcher(doc=status_doc(clock, mode=mode, writes_enabled=False,
                                                      trip_reason="manual")), clock=clock)
            self.assertFalse(sw.writes_allowed("radar/x"), mode)
            self.assertIn(f"mode={mode}", sw.reason())
            self.assertIn("'manual'", sw.reason())

    def test_disarm_after_trip_stays_blocked_until_reset(self):
        """arm -> trip -> disarm publishes {mode: alert, writes_enabled: false};
        /disarm does NOT re-enable writes, /reset is the only way back."""
        clock = Clock()
        docs = iter([status_doc(clock, mode="armed", writes_enabled=False),   # tripped
                     status_doc(clock, mode="alert", writes_enabled=False),   # disarmed
                     status_doc(clock, mode="alert", writes_enabled=True)])   # reset
        sw, _ = make(fetch=Fetcher(doc=lambda: next(docs)), clock=clock)
        self.assertFalse(sw.writes_allowed("radar/x"))
        clock.tick(61)
        self.assertFalse(sw.writes_allowed("radar/x"))      # disarm alone: still blocked
        clock.tick(61)
        self.assertTrue(sw.writes_allowed("radar/x"))       # reset: writing again

    def test_armed_with_writes_enabled_allows(self):
        clock = Clock()
        sw, _ = make(fetch=Fetcher(doc=status_doc(clock, writes_enabled=True)), clock=clock)
        self.assertTrue(sw.writes_allowed("radar/x"))

    def test_stale_worker_ts_allows(self):
        clock = Clock()
        for age in (15 * 60 + 1, 3600, 86400):
            sw, _ = make(fetch=Fetcher(doc=status_doc(clock, age_s=age)), clock=clock)
            self.assertTrue(sw.writes_allowed("radar/x"), age)
        sw, _ = make(fetch=Fetcher(doc=status_doc(clock, age_s=14 * 60)), clock=clock)
        self.assertFalse(sw.writes_allowed("radar/x"))

    def test_fetch_error_keeps_last_good_then_allows(self):
        clock = Clock()
        f = Fetcher(doc=status_doc(clock))
        sw, _ = make(fetch=f, clock=clock)
        self.assertFalse(sw.writes_allowed("radar/x"))
        f.exc = OSError("cdn down")
        # only the monotonic clock advances: isolates the last-good retention
        # rule from the worker_ts staleness rule
        for _ in range(14):
            clock.t_mono += 60
            self.assertFalse(sw.writes_allowed("radar/x"), "last good doc stands")
        clock.t_mono += 60 + 1                       # > 15 min since the good fetch
        self.assertTrue(sw.writes_allowed("radar/x"))
        self.assertGreaterEqual(f.calls, 15)
        self.assertIn("cdn down", sw.state()["last_error"])

    def test_first_fetch_error_allows_and_never_raises(self):
        for exc in (OSError("refused"), TimeoutError(), ValueError("bad json"),
                    RuntimeError("boom")):
            sw, _ = make(fetch=Fetcher(exc=exc))
            self.assertTrue(sw.writes_allowed("radar/x"), exc)
            self.assertIsNone(sw.reason())

    def test_unparseable_allows(self):
        clock = Clock()
        bad = [
            [], "armed", 7, None,
            {},
            {"v": 1, "mode": "armed"},                                   # no writes_enabled / worker_ts
            {"v": 1, "mode": "armed", "writes_enabled": False},          # no worker_ts
            {"v": 1, "mode": "armed", "writes_enabled": False, "worker_ts": "yesterday"},
            {"v": 1, "mode": "armed", "writes_enabled": "false", "worker_ts": clock.iso()},
            {"v": 2, "mode": "armed", "writes_enabled": False, "worker_ts": clock.iso()},
            {"mode": "armed", "writes_enabled": False, "worker_ts": clock.iso()},   # no v
        ]
        for doc in bad:
            sw, _ = make(fetch=Fetcher(doc=doc), clock=clock)
            self.assertTrue(sw.writes_allowed("radar/x"), doc)

    def test_worker_ts_forms(self):
        clock = Clock()
        base = dt.datetime.fromtimestamp(clock.t_wall, UTC)
        forms = (base.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 base.strftime("%Y-%m-%dT%H:%M:%S.123Z"),
                 base.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                 base.strftime("%Y-%m-%dT%H:%M:%S"))
        for ts in forms:
            sw, _ = make(fetch=Fetcher(doc=status_doc(clock, worker_ts=ts)), clock=clock)
            self.assertFalse(sw.writes_allowed("radar/x"), ts)

    def test_poll_cadence_once_per_window(self):
        clock = Clock()
        f = Fetcher(doc=status_doc(clock))
        sw, _ = make(fetch=f, clock=clock)
        for _ in range(30):
            sw.writes_allowed("radar/x")
            clock.tick(1)
        self.assertEqual(f.calls, 1)
        clock.tick(30)                                  # t = 60 s
        sw.writes_allowed("radar/x")
        self.assertEqual(f.calls, 2)
        sw, _ = make(env={"TAT_BREAKER_POLL_S": "5"}, fetch=f, clock=clock)
        f.calls = 0
        for _ in range(20):
            sw.writes_allowed("radar/x")
            clock.tick(1)
        self.assertEqual(f.calls, 4)

    def test_reset_document_unblocks_on_next_poll(self):
        clock = Clock()
        f = Fetcher(doc=status_doc(clock))
        sw, _ = make(fetch=f, clock=clock)
        self.assertFalse(sw.writes_allowed("radar/x"))
        f.doc = status_doc(clock, writes_enabled=True)      # POST /reset happened
        self.assertFalse(sw.writes_allowed("radar/x"))      # cached until the poll
        clock.tick(61)
        self.assertTrue(sw.writes_allowed("radar/x"))
        self.assertFalse(sw.state()["blocked"])

    def test_state_and_reason_are_side_effect_free(self):
        clock = Clock()
        f = Fetcher(doc=status_doc(clock))
        sw, _ = make(fetch=f, clock=clock)
        for _ in range(3):
            st = sw.state()
            sw.reason()
        self.assertEqual(f.calls, 0, "state()/reason() never fetch")
        self.assertTrue(st["writes_allowed"])
        self.assertFalse(sw.writes_allowed("radar/x"))
        st = sw.state()
        self.assertEqual(f.calls, 1)
        for k in ("writes_allowed", "reason", "state_url", "poll_s", "doc_mode",
                  "doc_writes_enabled", "doc_worker_ts", "doc_worker_age_s",
                  "fetch_ok", "fetch_errors", "last_error", "blocked",
                  "dropped_total"):
            self.assertIn(k, st)
        self.assertEqual(st["doc_mode"], "armed")
        self.assertEqual(st["dropped_total"], 1)
        self.assertEqual(json.loads(json.dumps(st)), st, "state() is JSON-safe")


class RealUrllibPathTest(HermeticBase):
    """The stdlib fetch itself, against file:// documents (no network)."""
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.path = Path(self.td.name) / "breaker.json"

    def tearDown(self):
        self.td.cleanup()
        super().tearDown()

    def _switch(self, clock):
        env = {"TAT_BREAKER_STATE_URL": self.path.as_uri()}
        return K._Switch(env=env, wall=clock.wall, mono=clock.mono)

    def test_armed_document_blocks_via_urllib(self):
        clock = Clock(wall=time.time())
        self.path.write_text(json.dumps(status_doc(clock)))
        sw = self._switch(clock)
        self.assertFalse(sw.writes_allowed("radar/x"))
        self.assertEqual(sw.state()["fetch_ok"], 1)

    def test_garbage_body_allows(self):
        clock = Clock(wall=time.time())
        self.path.write_text("<html>cloudflare error</html>")
        sw = self._switch(clock)
        self.assertTrue(sw.writes_allowed("radar/x"))
        self.assertEqual(sw.state()["fetch_errors"], 1)

    def test_missing_document_allows(self):
        clock = Clock(wall=time.time())
        sw = self._switch(clock)                    # file does not exist yet
        self.assertTrue(sw.writes_allowed("radar/x"))
        self.assertEqual(sw.state()["fetch_errors"], 1)
        self.assertIsNone(sw.reason())

    def test_fetch_timeout_is_five_seconds(self):
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["timeout"] = timeout
            seen["ua"] = req.get_header("User-agent")
            raise OSError("stub")

        with mock.patch.object(K.urllib.request, "urlopen", fake_urlopen):
            sw = K._Switch(env={"TAT_BREAKER_STATE_URL": STATE_URL})
            self.assertTrue(sw.writes_allowed("radar/x"))
        self.assertEqual(seen["timeout"], 5.0)
        self.assertTrue(seen["ua"])


class LoggingTest(HermeticBase):
    def test_transitions_once_and_blocked_notes_throttled(self):
        clock = Clock()
        sw, _ = make(env={"TAT_R2_WRITES": "0"}, clock=clock)
        with LogCapture() as cap:
            for _ in range(100):                                # 50 s
                sw.writes_allowed("radar/x")
                clock.tick(0.5)
            self.assertEqual(len(cap.lines), 1, cap.lines)
            self.assertIn("R2 writes DISABLED (env TAT_R2_WRITES=0)", cap.lines[0])
            clock.tick(10)                                      # >= 60 s since the note
            sw.writes_allowed("radar/x")
            self.assertEqual(len(cap.lines), 2, cap.lines)
            self.assertRegex(cap.lines[1], r"dropped 100 put\(s\) since last note")
            for _ in range(10):                                 # inside the window
                sw.writes_allowed("radar/x")
            self.assertEqual(len(cap.lines), 2)
        self.assertEqual(sw.state()["dropped_total"], 111)

    def test_transition_lines_are_warning_level_and_re_enable_logs_once(self):
        clock = Clock()
        f = Fetcher(doc=status_doc(clock))
        sw, _ = make(fetch=f, clock=clock)
        levels = []
        h = logging.Handler()
        h.emit = lambda r: levels.append((r.levelno, r.getMessage()))
        K._log.addHandler(h)
        try:
            sw.writes_allowed("radar/x")
            sw.writes_allowed("radar/x")
            f.doc = status_doc(clock, writes_enabled=True)
            clock.tick(61)
            for _ in range(3):
                sw.writes_allowed("radar/x")
        finally:
            K._log.removeHandler(h)
        self.assertEqual(len(levels), 2, levels)
        self.assertTrue(all(lv == logging.WARNING for lv, _ in levels))
        self.assertIn("DISABLED", levels[0][1])
        self.assertIn("ENABLED", levels[1][1])

    def test_stale_note_once_per_ten_minutes(self):
        clock = Clock()
        sw, _ = make(fetch=Fetcher(doc=status_doc(clock, age_s=3600)), clock=clock)
        with LogCapture() as cap:
            for _ in range(50):
                sw.writes_allowed("radar/x")
                clock.tick(6)                                   # 5 min
            self.assertEqual(sum("stale/unparseable" in l for l in cap.lines), 1, cap.lines)
            clock.tick(6 * 60)
            sw.writes_allowed("radar/x")
            self.assertEqual(sum("stale/unparseable" in l for l in cap.lines), 2, cap.lines)

    def test_internal_error_fails_open_and_logs_once(self):
        sw, _ = make(env={"TAT_R2_WRITES": "0"})
        with mock.patch.object(sw, "_decide", side_effect=RuntimeError("boom")):
            with LogCapture() as cap:
                self.assertTrue(sw.writes_allowed("radar/x"))
                self.assertTrue(sw.writes_allowed("radar/x"))
                self.assertIsNone(sw.reason())
                self.assertTrue(sw.state()["writes_allowed"])
        self.assertEqual(len(cap.lines), 1, cap.lines)
        self.assertIn("internal error", cap.lines[0])

    def test_print_fallback_without_logging_config(self):
        sw, _ = make(env={"TAT_R2_WRITES": "0"})
        err = io.StringIO()
        with mock.patch.object(K._log, "hasHandlers", return_value=False), \
             mock.patch.object(sys, "stderr", err):
            sw.writes_allowed("radar/x")
        self.assertIn("[killswitch] R2 writes DISABLED", err.getvalue())

    def test_logger_name(self):
        self.assertEqual(K._log.name, "tat.killswitch")
        self.assertEqual(K.LOGGER_NAME, "tat.killswitch")


class ThreadSafetyTest(HermeticBase):
    def test_smoke(self):
        flip = {"n": 0}
        clock = Clock()

        def doc():
            flip["n"] += 1
            time.sleep(0.002)                                   # a slow CDN
            return status_doc(clock, writes_enabled=(flip["n"] % 2 == 0))

        f = Fetcher(doc=doc)
        sw = K._Switch(env={"TAT_BREAKER_STATE_URL": STATE_URL},
                       fetch=f, wall=lambda: clock.t_wall, mono=time.monotonic)
        sw._poll_s = 0.01                          # several refreshes race
        errors, results = [], []

        def worker():
            try:
                for i in range(300):
                    results.append(sw.writes_allowed(f"radar/mrms/conus/{i}.webp"))
                    if i % 50 == 0:
                        sw.state()
                        sw.reason()
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        ts = [threading.Thread(target=worker) for _ in range(12)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 3600)
        self.assertTrue(all(r in (True, False) for r in results))
        self.assertGreaterEqual(f.calls, 1)
        self.assertFalse(sw._fetching, "the in-flight flag must always be released")

    def test_single_fetch_under_concurrency_within_one_window(self):
        f = Fetcher(doc=lambda: (time.sleep(0.01), status_doc(Clock()))[1])
        sw = K._Switch(env={"TAT_BREAKER_STATE_URL": STATE_URL}, fetch=f)
        ts = [threading.Thread(target=lambda: [sw.writes_allowed("radar/x")
                                                for _ in range(50)])
              for _ in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(10)
        self.assertEqual(f.calls, 1, "one fetch per poll window, not one per thread")


class ModuleSurfaceTest(HermeticBase):
    def test_public_api_and_mirror_header(self):
        for name in ("writes_allowed", "state", "reason"):
            self.assertTrue(callable(getattr(K, name)))
        self.assertEqual(K.ALLOW_PREFIXES, ("fleet/",))
        src = (ROOT / "tat_killswitch.py").read_text()
        head = "\n".join(src.splitlines()[:8])
        self.assertIn("MIRROR: identical copy lives in", head)
        self.assertIn("tat_killswitch.py", head)
        self.assertIn("edit both", head)

    def test_stdlib_only(self):
        src = (ROOT / "tat_killswitch.py").read_text()
        tree = ast.parse(src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertTrue(imported <= set(sys.stdlib_module_names), imported)

    def test_importable_the_way_the_pollers_run(self):
        """cwd = repo root, `python <GEN> --help` / `python scripts/<x> --help`:
        the guarded import must resolve from a bare script invocation (the
        generator's dir for GEN, the appended repo root for scripts/)."""
        env = {**os.environ, "TAT_BREAKER_STATE_URL": "", "PYTHONDONTWRITEBYTECODE": "1"}
        for argv in (["generate_nhc_overlay.py", "--help"],
                     ["scripts/ascat_r2_publish.py", "--help"],
                     ["scripts/recon_r2_publish.py", "--help"]):
            r = subprocess.run([sys.executable] + argv, cwd=ROOT, env=env,
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, (argv, r.stderr[-800:]))
        probe = ("import sys, generate_nhc_overlay as g, tat_killswitch as k;"
                 "assert g.tat_killswitch is k, 'guard bound to the root module';"
                 "assert sys.modules['tat_killswitch'].__file__.startswith(sys.argv[1]);"
                 "print('ok')")
        r = subprocess.run([sys.executable, "-c", probe, str(ROOT)], cwd=ROOT, env=env,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])


# --------------------------------------------------------------------------- #
# source-level net: every guarded put calls writes_allowed, no delete does
# --------------------------------------------------------------------------- #
def _class_methods(tree, cls_name):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            return {n.name: n for n in node.body if isinstance(n, ast.FunctionDef)}
    return None


def _calls(fn_node, attr):
    return [n for n in ast.walk(fn_node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == attr]


def _has_import_guard(tree):
    for node in tree.body:
        if isinstance(node, ast.Try):
            names = [a.name for b in node.body if isinstance(b, ast.Import) for a in b.names]
            if "tat_killswitch" in names:
                assigns = [t.id for h in node.handlers for s in h.body
                           if isinstance(s, ast.Assign) for t in s.targets
                           if isinstance(t, ast.Name)]
                return "tat_killswitch" in assigns and all(
                    isinstance(h.type, ast.Name) and h.type.id == "Exception"
                    for h in node.handlers)
    return False


class GuardSourceTest(unittest.TestCase):
    def _check_store(self, path):
        tree = ast.parse(path.read_text())
        self.assertTrue(_has_import_guard(tree), f"{path}: no guarded import")
        r2 = _class_methods(tree, "R2Store")
        self.assertIsNotNone(r2, f"{path}: no class R2Store")
        put = r2["put"]
        self.assertEqual(len(_calls(put, "writes_allowed")), 1, f"{path}: put unguarded")
        self.assertEqual(len(_calls(put, "put_object")), 1)
        # the guard is the FIRST statement of put (nothing writes before it)
        first = put.body[0]
        self.assertIsInstance(first, ast.If, f"{path}: guard is not first in put")
        self.assertTrue(_calls(first, "writes_allowed"))
        if "delete" in r2:
            self.assertEqual(_calls(r2["delete"], "writes_allowed"), [],
                             f"{path}: delete must never be guarded")
        # the put_object site is the only one in the module
        self.assertEqual(sum(len(_calls(n, "put_object")) for n in ast.walk(tree)
                             if isinstance(n, ast.FunctionDef)), 1)
        local = _class_methods(tree, "LocalStore")
        if local:
            self.assertEqual(_calls(local["put"], "writes_allowed"), [],
                             f"{path}: LocalStore is a verification path, never guarded")

    def test_generators(self):
        for name in GENERATOR_MODULES:
            with self.subTest(name):
                self._check_store(ROOT / f"{name}.py")

    def test_shared_sarobs_store(self):
        self._check_store(ROOT / "sarobs" / "store.py")

    def test_unlisted_shims_write_only_through_the_shared_store(self):
        """generate_sar_winds / generate_sar_salinity / generate_hy2_winds are
        shims over sarobs.store.make_store: the only put_object in their
        packages must be the guarded sarobs.store.R2Store.put."""
        sites = []
        for pkg in ("sarobs", "hy2obs"):
            for p in (ROOT / pkg).glob("*.py"):
                tree = ast.parse(p.read_text())
                for n in ast.walk(tree):
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                            and n.func.attr == "put_object":
                        sites.append(p.relative_to(ROOT).as_posix())
        self.assertEqual(sites, ["sarobs/store.py"])
        for shim in ("generate_sar_winds.py", "generate_sar_salinity.py",
                     "generate_hy2_winds.py"):
            self.assertNotIn("put_object", (ROOT / shim).read_text())

    def test_scripts(self):
        for rel in SCRIPTS:
            src = (ROOT / rel).read_text()
            tree = ast.parse(src)
            self.assertTrue(_has_import_guard(tree), rel)
            self.assertIn("sys.path.append(_REPO_ROOT)", src, rel)
            main = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == "main")
            self.assertEqual(len(_calls(main, "writes_allowed")), 1, rel)
            self.assertEqual(len(_calls(main, "put_object")), 1, rel)
            # the reap paths are untouched
            for attr in ("delete_object", "delete_objects", "list_objects_v2"):
                for call in _calls(main, attr):
                    self.assertNotIn("writes_allowed", ast.unparse(call))


# --------------------------------------------------------------------------- #
# writer integrations (recording stub in place of the boto client)
# --------------------------------------------------------------------------- #
def _block():
    return K._reset_for_tests(env={"TAT_BREAKER_STATE_URL": "", "TAT_R2_WRITES": "0"})


def _allow():
    return K._reset_for_tests(env={"TAT_BREAKER_STATE_URL": ""})


def _block_by_doc():
    clock = Clock()
    return K._reset_for_tests(env={"TAT_BREAKER_STATE_URL": STATE_URL},
                              fetch=Fetcher(doc=status_doc(clock)),
                              wall=clock.wall, mono=clock.mono)


class GeneratorStoreTest(HermeticBase):
    """Each generator's class R2Store: put(key, data, cache, ctype)."""

    def _store(self, mod):
        rec = _RecorderS3()
        st = mod.R2Store.__new__(mod.R2Store)      # skip boto3.client()/env
        st.c, st.bucket = rec, "triple-a-tropics-media"
        return st, rec

    def test_guard_bound_to_the_root_module(self):
        for name in GENERATOR_MODULES:
            mod = _import_or_skip(name)
            self.assertIs(mod.tat_killswitch, K, name)

    def test_blocked_puts_are_dropped_deletes_unaffected(self):
        for name in GENERATOR_MODULES:
            with self.subTest(name):
                mod = _import_or_skip(name)
                st, rec = self._store(mod)
                _block()
                self.assertIsNone(st.put("radar/mrms/conus/x.webp", b"x", "c", "image/webp"))
                st.put("env/tpw/latest_times.json", b"{}", "c", "application/json")
                self.assertEqual(rec.puts, [], name)
                if hasattr(st, "delete"):
                    st.delete("radar/mrms/conus/old.webp")
                    self.assertEqual([d["Key"] for d in rec.deletes],
                                     ["radar/mrms/conus/old.webp"], name)
                self.assertEqual(K.state()["dropped_total"], 2)

    def test_blocked_by_breaker_document(self):
        for name in GENERATOR_MODULES:
            with self.subTest(name):
                mod = _import_or_skip(name)
                st, rec = self._store(mod)
                _block_by_doc()
                st.put("nhc/overlay/latest.json", b"{}", "c", "application/json")
                self.assertEqual(rec.puts, [], name)
                self.assertIn("armed", K.reason())

    def test_fleet_keys_always_land(self):
        for name in GENERATOR_MODULES:
            with self.subTest(name):
                mod = _import_or_skip(name)
                st, rec = self._store(mod)
                _block()
                st.put("fleet/box1.json", b"{}", "public, max-age=60", "application/json")
                self.assertEqual(rec.put_keys, ["fleet/box1.json"])

    def test_allowed_puts_reach_the_client_unchanged(self):
        for name in GENERATOR_MODULES:
            with self.subTest(name):
                mod = _import_or_skip(name)
                st, rec = self._store(mod)
                _allow()
                st.put("radar/mrms/conus/x.webp", b"bytes", "public, max-age=31536000, immutable",
                       "image/webp")
                self.assertEqual(rec.puts, [{
                    "Bucket": "triple-a-tropics-media", "Key": "radar/mrms/conus/x.webp",
                    "Body": b"bytes", "CacheControl": "public, max-age=31536000, immutable",
                    "ContentType": "image/webp"}], name)

    def test_import_guard_none_means_allowed(self):
        for name in GENERATOR_MODULES:
            with self.subTest(name):
                mod = _import_or_skip(name)
                st, rec = self._store(mod)
                _block()
                with mock.patch.object(mod, "tat_killswitch", None):
                    st.put("radar/x", b"x", "c", "t")
                self.assertEqual(rec.put_keys, ["radar/x"], name)
                st.put("radar/y", b"x", "c", "t")
                self.assertEqual(rec.put_keys, ["radar/x"], name)   # guard is back


class SarobsStoreTest(HermeticBase):
    """sarobs.store.R2Store.put(key, body, content_type, cache_control): the
    shared helper behind generate_sar_winds / sar_salinity / hy2_winds."""

    def setUp(self):
        self.mod = _import_or_skip(SHARED_STORE_MODULE)
        self.rec = _RecorderS3(objects={"sar/manifest.json": b'{"n": 1}'})
        self.st = self.mod.R2Store.__new__(self.mod.R2Store)
        self.st.c, self.st.bucket = self.rec, "triple-a-tropics-media"

    def test_guard_bound(self):
        self.assertIs(self.mod.tat_killswitch, K)

    def test_blocked_and_allowed(self):
        _block()
        self.assertIsNone(self.st.put("sar/al012026/x.png", b"png", "image/png", "c"))
        self.st.put("hy2/meta.json", b"{}", "application/json", "c")
        self.assertEqual(self.rec.puts, [])
        # reads (the poller's watermark) are untouched while blocked
        self.assertEqual(self.st.get_json("sar/manifest.json"), {"n": 1})
        self.assertIsNone(self.st.get_json("sar/absent.json"))
        _allow()
        self.st.put("sar/al012026/x.png", b"png", "image/png", "public, max-age=1")
        self.assertEqual(self.rec.puts, [{
            "Bucket": "triple-a-tropics-media", "Key": "sar/al012026/x.png", "Body": b"png",
            "ContentType": "image/png", "CacheControl": "public, max-age=1"}])

    def test_make_store_r2_is_the_guarded_class_and_local_is_not(self):
        with tempfile.TemporaryDirectory() as td:
            local = self.mod.make_store(f"local:{td}")
            _block()
            local.put("sar/x.json", b"{}", "application/json", "c")
            self.assertTrue((Path(td) / "sar" / "x.json").exists(),
                            "LocalStore is a verification path, never guarded")
        with mock.patch.dict(os.environ, {"R2_ENDPOINT": "https://r2.invalid",
                                          "R2_ACCESS_KEY_ID": "k", "R2_SECRET_ACCESS_KEY": "s"}), \
             mock.patch.dict(sys.modules, {"boto3": types.SimpleNamespace(
                 client=lambda *a, **kw: self.rec)}):
            r2 = self.mod.make_store("r2")
        self.assertIsInstance(r2, self.mod.R2Store)
        self.assertIs(r2.c, self.rec)

    def test_import_guard_none_means_allowed(self):
        _block()
        with mock.patch.object(self.mod, "tat_killswitch", None):
            self.st.put("sar/x", b"x", "t", "c")
        self.assertEqual(self.rec.put_keys, ["sar/x"])


class _ScriptBase(HermeticBase):
    R2_ENV = {"R2_ENDPOINT": "https://r2.invalid", "R2_ACCESS_KEY_ID": "k",
              "R2_SECRET_ACCESS_KEY": "s", "R2_BUCKET": "triple-a-tropics-media"}

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()
        super().tearDown()

    def run_main(self, mod, rec, argv, env=None):
        fake_boto3 = types.SimpleNamespace(client=lambda *a, **kw: rec)
        out = io.StringIO()
        with mock.patch.dict(os.environ, {**self.R2_ENV, **(env or {})}), \
             mock.patch.dict(sys.modules, {"boto3": fake_boto3}), \
             mock.patch.object(sys, "argv", [mod.__name__] + argv), \
             mock.patch.object(sys, "stdout", out):
            rc = mod.main()
        return rc, out.getvalue()


class ReconPublishTest(_ScriptBase):
    """scripts/recon_r2_publish.py: _put is guarded; the targeted reap is
    never guarded by the switch but runs only under a manifest that landed
    (the pruned slugs are still listed by the LIVE manifest until then)."""

    def setUp(self):
        super().setUp()
        self.mod = _load_script("scripts/recon_r2_publish.py")
        now = dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        manifest = {"generated_utc": now, "current_slug": "al012026",
                    "has_active_recon": True, "tcpod_number": "26-050",
                    "storms": [{"slug": "al012026", "last_ob_utc": now,
                                "mission_count": 1, "latest_mission_id": "AF301-0101A-ONE",
                                "peak_sfmr_kt": 55, "min_p_sfc_hpa": 1001.0}]}
        (self.root / "manifest.json").write_text(json.dumps(manifest))
        (self.root / "current.json").write_text(json.dumps(
            {"generated_utc": now, "storm_slug": "al012026", "has_active": True,
             "mission": {"mission_id": "AF301-0101A-ONE", "n_obs": 10}}))
        (self.root / "tcpod.json").write_text(json.dumps({"raw": "TCPOD"}))
        (self.root / "al012026").mkdir()
        (self.root / "al012026" / "missions.json").write_text("[]")
        (self.root / "_pruned_slugs.json").write_text(json.dumps(["al992025"]))
        self.rec = _RecorderS3(listing={"recon/al992025/": ["recon/al992025/missions.json"]})

    def test_guard_bound(self):
        self.assertIs(self.mod.tat_killswitch, K)

    def test_blocked_uploads_nothing_and_defers_the_reap(self):
        """With the manifest dropped, the live index still lists al992025:
        reaping its tree now would 404 the live product. Deferred (the
        builder re-derives the slug next tick), never lost."""
        _block()
        rc, out = self.run_main(self.mod, self.rec, [str(self.root)])
        self.assertEqual(rc, 0)
        self.assertEqual(self.rec.puts, [])
        self.assertEqual(self.rec.bulk_deletes, [])
        self.assertEqual(self.rec.lists, [])                # no listing either
        self.assertIn("uploaded 0 file(s)", out)
        self.assertIn("reap of 1 pruned slug(s) deferred (manifest.json did not land)", out)
        self.assertIn("kill switch dropped 4 put(s)", out)
        self.assertEqual(K.state()["dropped_total"], 4)

    def test_blocked_by_breaker_document_defers_the_reap(self):
        _block_by_doc()
        rc, _ = self.run_main(self.mod, self.rec, [str(self.root)])
        self.assertEqual(rc, 0)
        self.assertEqual(self.rec.puts, [])
        self.assertEqual(self.rec.bulk_deletes, [])

    def test_reap_follows_the_manifest_when_the_switch_flips_mid_loop(self):
        """Upload order: al012026/missions.json, tcpod.json, current.json,
        manifest.json. Manifest dropped -> reap deferred (even though every
        other file landed); manifest landed -> reap runs (even though an
        earlier file was dropped)."""
        _allow()
        with mock.patch.object(K, "writes_allowed", side_effect=[True, True, True, False]):
            rc, out = self.run_main(self.mod, self.rec, [str(self.root)])
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(self.rec.put_keys),
                         ["recon/al012026/missions.json", "recon/current.json",
                          "recon/tcpod.json"])
        self.assertEqual(self.rec.bulk_deletes, [])
        self.assertIn("deferred", out)
        rec2 = _RecorderS3(listing=self.rec.listing)
        with mock.patch.object(K, "writes_allowed", side_effect=[False, True, True, True]):
            rc, out = self.run_main(self.mod, rec2, [str(self.root)])
        self.assertEqual(rc, 0)
        self.assertEqual(rec2.put_keys[-1], "recon/manifest.json")
        self.assertEqual(rec2.bulk_deletes[0]["Delete"]["Objects"],
                         [{"Key": "recon/al992025/missions.json"}])
        self.assertIn("reaped 1 object(s) across 1 pruned slug(s)", out)
        self.assertNotIn("deferred", out)

    def test_allowed_uploads_index_last(self):
        _allow()
        rc, out = self.run_main(self.mod, self.rec, [str(self.root)])
        self.assertEqual(rc, 0)
        self.assertEqual(self.rec.put_keys[-2:], ["recon/current.json", "recon/manifest.json"])
        self.assertEqual(sorted(self.rec.put_keys),
                         ["recon/al012026/missions.json", "recon/current.json",
                          "recon/manifest.json", "recon/tcpod.json"])
        self.assertIn("uploaded 4 file(s), reaped 1 object(s) across 1 pruned slug(s)", out)
        self.assertNotIn("kill switch", out)
        self.assertEqual(self.rec.bulk_deletes[0]["Delete"]["Objects"],
                         [{"Key": "recon/al992025/missions.json"}])

    def test_shrink_guard_spotlight_puts_are_guarded_too(self):
        live = {"generated_utc": "2026-01-01T00:00:00Z",
                "storms": [{"slug": f"al0{i}2026"} for i in range(1, 5)]}
        self.rec.objects["recon/manifest.json"] = json.dumps(live).encode()
        _block()
        rc, out = self.run_main(self.mod, self.rec, [str(self.root)])
        self.assertEqual(rc, 1)                     # the guard still aborts
        self.assertEqual(self.rec.puts, [])
        _allow()
        rc, _ = self.run_main(self.mod, self.rec, [str(self.root)])
        self.assertEqual(rc, 1)
        self.assertEqual(sorted(self.rec.put_keys), ["recon/current.json", "recon/tcpod.json"])

    def test_import_guard_none_means_allowed(self):
        _block()
        with mock.patch.object(self.mod, "tat_killswitch", None):
            rc, _ = self.run_main(self.mod, self.rec, [str(self.root)])
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.rec.puts), 4)
        self.assertEqual(len(self.rec.bulk_deletes), 1)     # manifest landed: reap runs


class AscatPublishTest(_ScriptBase):
    """scripts/ascat_r2_publish.py: the put loop is guarded; the reap is
    never guarded by the switch but runs only under a manifest that landed
    (the pruned ids are still listed by the LIVE manifest until then)."""

    def setUp(self):
        super().setUp()
        self.mod = _load_script("scripts/ascat_r2_publish.py")
        for name in ("pass_a.json", "pass_b.json", "manifest.json"):
            (self.root / name).write_text("{}")
        (self.root / "_pruned_ids.json").write_text(json.dumps(["old1", "old2"]))
        self.rec = _RecorderS3()

    def test_guard_bound(self):
        self.assertIs(self.mod.tat_killswitch, K)

    def test_blocked_uploads_nothing_and_defers_the_reap(self):
        """With the manifest dropped, the live index still lists old1/old2:
        deleting their JSON now would 404 the live loop tail. Deferred (the
        builder re-derives the ids from the live manifest next tick)."""
        _block()
        rc, out = self.run_main(self.mod, self.rec, [str(self.root)])
        self.assertEqual(rc, 0)
        self.assertEqual(self.rec.puts, [])
        self.assertEqual(self.rec.deletes, [])
        self.assertIn("uploaded 0 file(s), reaped 0", out)
        self.assertIn("kill switch dropped 3 put(s)", out)
        self.assertIn("reap of 2 pruned pass(es) deferred: manifest.json did not land", out)

    def test_blocked_by_breaker_document_defers_the_reap(self):
        _block_by_doc()
        rc, _ = self.run_main(self.mod, self.rec, [str(self.root)])
        self.assertEqual(rc, 0)
        self.assertEqual(self.rec.puts, [])
        self.assertEqual(self.rec.deletes, [])

    def test_reap_follows_the_manifest_when_the_switch_flips_mid_loop(self):
        """Upload order: pass_a, pass_b, manifest. Manifest dropped -> reap
        deferred although both passes landed; manifest landed -> reap runs
        although pass_a was dropped."""
        _allow()
        with mock.patch.object(K, "writes_allowed", side_effect=[True, True, False]):
            rc, out = self.run_main(self.mod, self.rec, [str(self.root)])
        self.assertEqual(rc, 0)
        self.assertEqual(self.rec.put_keys, ["ascat/pass_a.json", "ascat/pass_b.json"])
        self.assertEqual(self.rec.deletes, [])
        self.assertIn("deferred", out)
        rec2 = _RecorderS3()
        with mock.patch.object(K, "writes_allowed", side_effect=[False, True, True]):
            rc, out = self.run_main(self.mod, rec2, [str(self.root)])
        self.assertEqual(rc, 0)
        self.assertEqual(rec2.put_keys, ["ascat/pass_b.json", "ascat/manifest.json"])
        self.assertEqual([d["Key"] for d in rec2.deletes],
                         ["ascat/old1.json", "ascat/old2.json"])
        self.assertIn("uploaded 2 file(s), reaped 2", out)
        self.assertNotIn("deferred", out)

    def test_allowed_uploads_manifest_last(self):
        _allow()
        rc, out = self.run_main(self.mod, self.rec, [str(self.root), "--prefix", "ascat"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.rec.put_keys,
                         ["ascat/pass_a.json", "ascat/pass_b.json", "ascat/manifest.json"])
        self.assertEqual(self.rec.puts[0]["CacheControl"], "public, max-age=300")
        self.assertIn("uploaded 3 file(s), reaped 2", out)
        self.assertNotIn("kill switch", out)

    def test_import_guard_none_means_allowed(self):
        _block()
        with mock.patch.object(self.mod, "tat_killswitch", None):
            rc, _ = self.run_main(self.mod, self.rec, [str(self.root)])
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.rec.puts), 3)
        self.assertEqual(len(self.rec.deletes), 2)          # manifest landed: reap runs


if __name__ == "__main__":
    unittest.main()
