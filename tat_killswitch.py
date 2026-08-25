#!/usr/bin/env python3
"""tat_killswitch -- the box-side R2 WRITE kill switch (Phase 0, 2026-08-25).

MIRROR: identical copy lives in <other repo>/tat_killswitch.py -- edit both.
(Triple-A-Tropics/tat_killswitch.py and tat-satellite-render/tat_killswitch.py
are the same bytes; the orchestrator cmp's them.)

Every R2 writer on the boxes asks ``writes_allowed(key)`` before it PUTs. The
answer comes from three sources, checked in this order, and the module FAILS
OPEN at every step: no answer, a broken document, an exception, an unreachable
CDN all mean "write". Only a positive, fresh, explicit STOP blocks anything.

  1. Allowlist. Keys under ``fleet/`` are always allowed. The heartbeat
     (fleet/<box>.json), the roster (fleet/index.json) and the breaker mirror
     (fleet/breaker.json) are the liveness signal; darkening them would turn
     "writes stopped" into "box dead" on every dashboard.
  2. Env. ``TAT_R2_WRITES`` in {0, false, off, no} (any case) blocks. Read
     ONCE at import: a container cannot change its env without a restart,
     and that restart (``fleet.sh writes off``) IS the 30-second manual path.
  3. Breaker document. ``TAT_BREAKER_STATE_URL`` (default: the CDN mirror of
     the r2-breaker Worker's /status, written by tsr scripts/heartbeat.sh;
     empty string disables) is fetched with urllib (stdlib only -- some
     images have no ``requests``), 5 s timeout, at most once per
     ``TAT_BREAKER_POLL_S`` (default 60) per process, cached in-process.
     Blocks iff writes_enabled is False AND worker_ts is within 15 min of
     now (and v == 1, mode a string). writes_enabled is the Worker's ONE
     authoritative gate: it goes false only on an auto-trip while armed or
     on an operator's manual /trip (any mode), and only /reset turns it
     back on -- /disarm does not. Alert mode never sets it false by itself,
     so the alert-only first week cannot stop writes; a manual trip always
     does. Gating on mode == "armed" as well (the spec's first wording)
     would make /disarm a silent second path back to writing and turn a
     manual trip in alert mode into a no-op that fleet.sh reports as a
     fleet-wide stop -- both contradict the contract's "reset is the ONLY
     path back" (adversarial review, 2026-08-25). Stale or missing fields
     -> allowed (one log line per 10 min). Fetch error -> the last good
     document stands for up to 15 min, then allowed. The first fetch never
     blocks longer than the timeout and never raises.
  4. Any exception anywhere in here -> allowed, logged once.

Box processes NEVER poll the Worker itself (Workers free plan: 100K req/day
shared by every worker) -- only the CDN copy, which the heartbeat refreshes.

Deletes are never guarded (free on R2; prune must keep reducing storage).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sys
import threading
import time
import urllib.request

__all__ = ["writes_allowed", "state", "reason", "ALLOW_PREFIXES"]

LOGGER_NAME = "tat.killswitch"
ALLOW_PREFIXES = ("fleet/",)
DEFAULT_STATE_URL = "https://cdn.triple-a-tropics.com/fleet/breaker.json"
DEFAULT_POLL_S = 60.0
FETCH_TIMEOUT_S = 5.0
DOC_MAX_AGE_S = 15 * 60          # worker_ts further than this from now -> stale -> allowed
LAST_GOOD_MAX_AGE_S = 15 * 60    # fetch errors: the last fetched doc stands this long
BLOCKED_NOTE_EVERY_S = 60.0      # while blocked: one log line per minute, max
STALE_NOTE_EVERY_S = 600.0       # stale/unparseable/fetch-error notes: one per 10 min
_OFF_VALUES = frozenset({"0", "false", "off", "no"})

_log = logging.getLogger(LOGGER_NAME)


def _emit(level: int, msg: str) -> None:
    """stdlib logging, with a stderr print for scripts that never configured
    logging (otherwise INFO is dropped and WARNING comes out lastResort-bare).
    Logging must never break a write, so this swallows everything."""
    try:
        if _log.hasHandlers():
            _log.log(level, msg)
        else:
            print(msg, file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001
        pass


def _parse_ts(s) -> float | None:
    """ISO-8601 -> epoch seconds. None for anything that is not a timestamp.
    Handles the Worker's JS ``toISOString()`` form (fractional seconds + Z)
    on every Python the images ship (3.11 accepts Z itself; older ones do not,
    so the suffix is normalised by hand)."""
    if not isinstance(s, str) or not s.strip():
        return None
    t = s.strip()
    if t[-1] in "Zz":
        t = t[:-1] + "+00:00"
    try:
        d = dt.datetime.fromisoformat(t)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.timestamp()


def _http_get_json(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": "tat-killswitch/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(1 << 16)
    return json.loads(raw.decode("utf-8"))


class _Switch:
    """One per process. ``env``/``fetch``/clocks are injectable for tests
    only; production goes through the module-level singleton below."""

    def __init__(self, env=None, fetch=None, wall=time.time, mono=time.monotonic) -> None:
        env = os.environ if env is None else env
        self._env_raw = env.get("TAT_R2_WRITES")
        self._env_off = (self._env_raw or "").strip().lower() in _OFF_VALUES
        url = env.get("TAT_BREAKER_STATE_URL")
        self._url = DEFAULT_STATE_URL if url is None else url.strip()
        try:
            self._poll_s = max(1.0, float(env.get("TAT_BREAKER_POLL_S", DEFAULT_POLL_S)))
        except (TypeError, ValueError):
            self._poll_s = DEFAULT_POLL_S
        self._fetch = fetch or (lambda: _http_get_json(self._url, FETCH_TIMEOUT_S))
        self._wall = wall
        self._mono = mono
        # RLock: the note helpers lock on their own and are reachable from
        # inside locked sections; a plain Lock would self-deadlock there.
        self._lock = threading.RLock()
        # breaker document cache (the last SUCCESSFULLY fetched JSON value)
        self._doc = None
        self._doc_at = None          # mono clock at fetch
        self._last_attempt = None    # mono clock of the last fetch attempt
        self._fetching = False       # one fetcher at a time; others use the cache
        self._fetch_ok = 0
        self._fetch_errors = 0
        self._last_error = None
        # blocked/allowed bookkeeping + log throttles
        self._blocked = False
        self._reason = None
        self._blocked_since = None
        self._dropped_total = 0
        self._dropped_since_note = 0
        self._last_note_at = None
        self._last_stale_note_at = None
        self._last_fetch_note_at = None
        self._internal_error_logged = False

    # ---- public -----------------------------------------------------------
    def writes_allowed(self, key: str = "") -> bool:
        try:
            k = key if isinstance(key, str) else str(key or "")
            if k.startswith(ALLOW_PREFIXES):
                return True
            now = self._mono()
            allowed, why = self._decide(now, refresh=True)
            self._record(allowed, why, now)
            return allowed
        except Exception as e:  # noqa: BLE001 - rule 4: fail OPEN, always
            self._note_internal_error(e)
            return True

    def reason(self) -> str | None:
        try:
            return self._decide(self._mono(), refresh=False)[1]
        except Exception as e:  # noqa: BLE001
            self._note_internal_error(e)
            return None

    def state(self) -> dict:
        """Side-effect free snapshot for /health endpoints and logs: judges
        the CACHED document (no fetch), so a health probe every 30 s never
        adds CDN traffic of its own."""
        try:
            now = self._mono()
            allowed, why = self._decide(now, refresh=False)
            with self._lock:
                doc = self._doc if isinstance(self._doc, dict) else None
                wts = _parse_ts(doc.get("worker_ts")) if doc else None
                return {
                    "writes_allowed": allowed,
                    "reason": why,
                    "env_TAT_R2_WRITES": self._env_raw,
                    "state_url": self._url or None,
                    "poll_s": self._poll_s,
                    "doc_present": self._doc is not None,
                    "doc_fetched_age_s": (int(now - self._doc_at)
                                          if self._doc_at is not None else None),
                    "doc_mode": doc.get("mode") if doc else None,
                    "doc_writes_enabled": doc.get("writes_enabled") if doc else None,
                    "doc_worker_ts": doc.get("worker_ts") if doc else None,
                    "doc_worker_age_s": (int(self._wall() - wts)
                                         if wts is not None else None),
                    "fetch_ok": self._fetch_ok,
                    "fetch_errors": self._fetch_errors,
                    "last_error": self._last_error,
                    "blocked": self._blocked,
                    "blocked_for_s": (int(now - self._blocked_since)
                                      if self._blocked_since is not None else None),
                    "dropped_total": self._dropped_total,
                }
        except Exception as e:  # noqa: BLE001
            self._note_internal_error(e)
            return {"writes_allowed": True, "reason": None, "error": repr(e)}

    # ---- rules ------------------------------------------------------------
    def _decide(self, now: float, refresh: bool) -> tuple[bool, str | None]:
        if self._env_off:                                   # rule 2
            return False, f"env TAT_R2_WRITES={self._env_raw}"
        if not self._url:                                   # rule 3 disabled
            return True, None
        if refresh:
            self._maybe_refresh(now)
        return self._decide_doc(now)

    def _maybe_refresh(self, now: float) -> None:
        with self._lock:
            if self._fetching:
                return
            if (self._last_attempt is not None
                    and (now - self._last_attempt) < self._poll_s):
                return
            self._fetching = True
            self._last_attempt = now
        doc, err = None, None
        try:
            doc = self._fetch()
        except Exception as e:  # noqa: BLE001 - network/HTTP/JSON: a fetch error
            err = e
        done = self._mono()
        with self._lock:
            self._fetching = False
            if err is None:
                self._doc, self._doc_at = doc, done
                self._fetch_ok += 1
                self._last_error = None
            else:
                self._fetch_errors += 1
                self._last_error = repr(err)
                self._maybe_note(done, "_last_fetch_note_at",
                                 f"[killswitch] breaker document fetch failed ({err!r}) "
                                 f"-- last good document stands for {LAST_GOOD_MAX_AGE_S // 60} min, "
                                 f"then failing open")

    def _decide_doc(self, now: float) -> tuple[bool, str | None]:
        with self._lock:
            doc, doc_at = self._doc, self._doc_at
        if doc_at is None:
            return True, None                               # nothing known yet
        if now - doc_at > LAST_GOOD_MAX_AGE_S:
            self._maybe_note(now, "_last_stale_note_at",
                             "[killswitch] breaker document stale/unparseable -- failing open "
                             f"(last successful fetch {int(now - doc_at)} s ago)")
            return True, None
        if not isinstance(doc, dict):
            self._maybe_note(now, "_last_stale_note_at",
                             "[killswitch] breaker document stale/unparseable -- failing open "
                             f"(not a JSON object: {type(doc).__name__})")
            return True, None
        mode = doc.get("mode")
        enabled = doc.get("writes_enabled")
        wts = _parse_ts(doc.get("worker_ts"))
        if (doc.get("v") != 1 or not isinstance(mode, str)
                or not isinstance(enabled, bool) or wts is None):
            self._maybe_note(now, "_last_stale_note_at",
                             "[killswitch] breaker document stale/unparseable -- failing open "
                             "(missing/invalid v, mode, writes_enabled or worker_ts)")
            return True, None
        age = self._wall() - wts
        if abs(age) > DOC_MAX_AGE_S:
            self._maybe_note(now, "_last_stale_note_at",
                             "[killswitch] breaker document stale/unparseable -- failing open "
                             f"(worker_ts is {int(age)} s from now)")
            return True, None
        if enabled is False:
            # writes_enabled is the one gate (see the module docstring): an
            # auto-trip while armed or a manual /trip in ANY mode; only
            # /reset clears it. The mode rides along in the reason so a
            # log line says which kind of stop this is.
            return False, (f"breaker STOP: writes_enabled=false (mode={mode}, "
                           f"trip_reason={doc.get('trip_reason')!r}, "
                           f"tripped_at={doc.get('tripped_at')}, "
                           f"worker_ts={doc.get('worker_ts')})")
        return True, None

    # ---- bookkeeping + logging ---------------------------------------------
    def _record(self, allowed: bool, why: str | None, now: float) -> None:
        msg = None
        with self._lock:
            if allowed:
                if self._blocked:
                    n, self._dropped_since_note = self._dropped_since_note, 0
                    msg = (f"[killswitch] R2 writes ENABLED again (was: {self._reason}) "
                           f"-- dropped {n} put(s) since last note, "
                           f"{self._dropped_total} in total")
                    self._blocked, self._reason, self._blocked_since = False, None, None
            else:
                self._dropped_total += 1
                self._dropped_since_note += 1
                self._reason = why
                if not self._blocked:
                    # the transition line is itself a note: later notes count
                    # the drops AFTER it, so "dropped n since last note" is exact
                    self._blocked, self._blocked_since, self._last_note_at = True, now, now
                    self._dropped_since_note = 0
                    msg = (f"[killswitch] R2 writes DISABLED ({why}) -- dropping puts; "
                           f"noted at most once per {int(BLOCKED_NOTE_EVERY_S)} s")
                elif now - self._last_note_at >= BLOCKED_NOTE_EVERY_S:
                    n, self._dropped_since_note = self._dropped_since_note, 0
                    self._last_note_at = now
                    msg = (f"[killswitch] R2 writes DISABLED ({why}) "
                           f"-- dropped {n} put(s) since last note")
        if msg:
            _emit(logging.WARNING, msg)

    def _maybe_note(self, now: float, slot: str, msg: str) -> None:
        with self._lock:
            last = getattr(self, slot)
            if last is not None and now - last < STALE_NOTE_EVERY_S:
                return
            setattr(self, slot, now)
        _emit(logging.WARNING, msg)

    def _note_internal_error(self, e: BaseException) -> None:
        try:
            with self._lock:
                if self._internal_error_logged:
                    return
                self._internal_error_logged = True
            _emit(logging.WARNING, f"[killswitch] internal error -- failing open: {e!r}")
        except Exception:  # noqa: BLE001
            pass


def _build_default() -> _Switch:
    try:
        return _Switch()
    except Exception as e:  # noqa: BLE001 - a broken env must not break import
        try:
            _emit(logging.WARNING, f"[killswitch] init failed -- failing open: {e!r}")
        except Exception:  # noqa: BLE001
            pass
        return _Switch(env={"TAT_BREAKER_STATE_URL": ""})


_SW = _build_default()


def writes_allowed(key: str = "") -> bool:
    """True if a PUT of ``key`` may proceed. Never raises."""
    return _SW.writes_allowed(key)


def state() -> dict:
    """Snapshot for /health endpoints and logs (no fetch)."""
    return _SW.state()


def reason() -> str | None:
    """Why writes are blocked right now, or None (no fetch)."""
    return _SW.reason()


def _reset_for_tests(env=None, fetch=None, wall=time.time, mono=time.monotonic) -> _Switch:
    """TEST HOOK ONLY. Production reads env once at import; this rebuilds the
    singleton so a test can pin env, the fetcher and both clocks."""
    global _SW
    _SW = _Switch(env=env, fetch=fetch, wall=wall, mono=mono)
    return _SW
