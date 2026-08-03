#!/usr/bin/env python3
"""HAFS full-cycle plot builder - first product of the ``/models/`` page.

Scales the validated single-frame vertical slice in :mod:`hafs_plot` into a
batch renderer that, for the latest complete HAFS cycle, loops every active
storm × {HAFS-A, HAFS-B} × {storm nest, parent domain} × {MSLP+10 m wind,
composite reflectivity, simulated Clean IR, simulated Water Vapor, MSLP+PWAT}
× forecast hour and writes a TAT-styled PNG per frame plus a ``manifest.json`` the
``/models/`` frontend reads. The two simulated-satellite products pull their
brightness-temperature channel from the sibling ``.sat`` GRIB (the wind/refl
products read ``.atm``); see ``hafs_registry`` for the product catalog.

The build is split into an INGEST stage and a RENDER stage with a persistent
per-cycle FIELD CACHE between them (see ``hafs_cache``), so each model-cycle GRIB
is fetched + decoded ONCE: ingest writes every field a frame's products need into
one cache entry; render reads decoded fields from the cache and never re-fetches.
Previously fetch + render were fused per (product, fxx), so a frame's shared
``.atm`` was re-downloaded once per product that read it.

Pipeline (mirrors the GIBS still pattern - see ``generate_gibs_truecolor.py``):

1. Resolve the newest *complete* cycle for HAFS-A by listing the public
   ``noaa-nws-hafs-pds`` S3 bucket over HTTP (no boto3, no credentials). A cycle
   is "complete" once at least one storm's storm-nest run reaches ``f126`` -
   i.e. the model finished and uploaded, so we never catch a half-written cycle.
2. Enumerate that cycle's storms with one ``delimiter=.`` list call (the storm
   id is each key's filename prefix, so S3 hands back the distinct ids directly).
3. For each (storm, model, domain) list the available forecast hours. Stage 1
   INGEST: one fetch+decode per (model, storm, domain, fxx) frame into the field
   cache (``hafs_cache.ingest_frame``). Stage 2 RENDER: one task per (product,
   frame) reads the cache (``hafs_cache.load_frame``) and draws via
   ``hafs_plot.render_frame``. Both stages run in a process pool with shared
   BrokenProcessPool retries; a failed ingest skips all its products, a failed
   render skips one product - logged, never fatal, partial coverage still
   publishes.
4. Emit ``manifest.json`` listing, per storm, the fxx that actually rendered for
   each (model, domain, product). The frontend derives valid times as
   init + fxx·3 h.

Output layout (also the R2 key layout under ``models/hafs/``)::

    models/hafs/manifest.json
    models/hafs/{model}/{storm}/{domain}/{product}/f{FFF}.png
      e.g. models/hafs/hafsa/13l/storm/mslp_wind/f012.png
           models/hafs/hafsa/13l/storm/refl/f012.png
           models/hafs/hafsa/13l/storm/clean_ir/f012.png
           models/hafs/hafsa/13l/storm/water_vapor/f012.png

``update-hafs.yml`` runs this with no args (live: latest cycle) and syncs
``models/hafs/`` to ``cdn.triple-a-tropics.com/models/hafs/``. Nothing is
committed to git - only the frontend page is tracked; the media lives on R2.

Examples::

    python generate_hafs_plots.py                       # latest complete cycle
    python generate_hafs_plots.py --cycle 2023090900    # a specific dev cycle
    python generate_hafs_plots.py --cycle 2023090900 --storm 13l \\
        --models hafsa --domains storm.atm --max-fxx 24   # quick local smoke
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
from concurrent.futures.process import BrokenProcessPool
import datetime as dt
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence
from urllib.parse import quote
from xml.etree import ElementTree as ET

import requests

# Reuse the validated fetch + render from the single-frame slice. Importing it
# also pulls matplotlib (Agg) and the Herbie template override.
from hafs_render import hafs_plot as hp
# Single source of product truth (identity, GRIB channel, color/colorbar/stat).
from hafs_render import hafs_registry as reg
# The MODEL registry (convection treatment + AI paradigm + the structural gate
# they drive). Light import: no numpy/matplotlib.
from hafs_render import model_registry as mr
# Quantity-keyed palette registry: supplies the manifest value planes.
from tat_palettes import quantities as tq
# Persistent per-cycle field cache (the ingest/render split lives here).
from hafs_render import hafs_cache as fc

log = logging.getLogger("hafs-build")

HERE = Path(__file__).resolve().parent

S3_BASE = "https://noaa-nws-hafs-pds.s3.amazonaws.com"
S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"

# model slug → (bucket dir / filename token). Both are "hfsX"; kept as one map
# so a future model with a different token (e.g. an ensemble) is a one-line add.
MODEL_TOKEN = {"hafsa": "hfsa", "hafsb": "hfsb"}
# Labels are DERIVED from the model registry so there is ONE source of model
# truth (slug/label/convection/AI paradigm all live on the ModelSpec). Kept as a
# module-level name because callers and tests already import it.
MODEL_LABEL = mr.model_labels()

# domain raw name (in the S3 filename) → (url slug, human label)
DOMAINS = {
    "storm.atm": ("storm", "Storm nest (~2 km)"),
    "parent.atm": ("parent", "Parent (~6 km)"),
}

# ATCF basin letter (last char of the storm id) → (basin slug, human label).
BASIN_BY_LETTER = {
    "l": ("al", "North Atlantic"),
    "e": ("ep", "East Pacific"),
    "c": ("cp", "Central Pacific"),
    "w": ("wp", "West Pacific"),
    "a": ("io", "Arabian Sea"),
    "b": ("io", "Bay of Bengal"),
    "s": ("sh", "South-West Indian"),
    "p": ("sh", "South Pacific"),
}

STORM_ID_RE = re.compile(r"^\d{2}[a-z]$")
FXX_RE = re.compile(r"\.f(\d{3})\.grb2$")

# The product dimension: each (storm, model, domain, fxx) is rendered once per
# product. Wind keeps its original "mslp_wind" slug (and R2 path segment) so the
# default view is unchanged; reflectivity is the new "refl" slug. ``short`` is
# the segmented-toggle label on the frontend. Both products come from the SAME
# GRIB2 file, so a (model, domain)'s available forecast hours are identical
# across products and the completeness check applies to both equally.
# The product catalog is derived from the single registry (hafs_registry), so
# there is ONE source of product truth. PRODUCTS keeps its {slug,label,short}
# shape (the manifest + frontend read it); DEFAULT_PRODUCTS is the toggle/render
# order (mslp_wind first, so the default view on load is unchanged = Wind). The
# simulated-satellite products' GRIB channel + color live on the same specs (see
# _render_one's reg.sat_parm lookup). Add or restyle a product in the registry.
PRODUCTS = reg.products_dict()
DEFAULT_PRODUCTS = reg.default_order()

# Phase 2 shared plumbing: ingest the pressure-level (upper-air) fields into the
# field cache every cycle, so the cache already carries them when the planned
# upper-air products land (no further CACHE_VERSION bump / cold-render needed
# then). NO product reads them yet, so this never changes a rendered frame - it
# only adds fields to each cache entry (see hafs_cache CACHE_VERSION v3 and the
# ~+8 MB nest / ~+40 MB parent per-frame cost noted in the spike). Flip to False
# to stop ingesting them (e.g. to shed cache cost) without touching the plumbing.
INGEST_UPPER_AIR = True

# A frame this far in the future from "now" can't possibly exist; the complete-
# cycle check looks for this terminal hour to know a run finished uploading.
TERMINAL_FXX = 126

_S3_RETRIES = 3   # bounded retry for transient S3 5xx on listing calls


# ---------------------------------------------------------------------------
# Public S3 listing (HTTP + XML, paginated, no credentials)
# ---------------------------------------------------------------------------
def _s3_list(prefix: str, delimiter: Optional[str] = None,
             session: Optional[requests.Session] = None) -> tuple[list[str], list[str]]:
    """List ``noaa-nws-hafs-pds`` under ``prefix``.

    Returns ``(keys, common_prefixes)``. Follows ``IsTruncated`` pagination via
    the continuation token. ``delimiter`` rolls up keys into CommonPrefixes at
    the next occurrence of that char after ``prefix`` - we use ``delimiter='.'``
    to get the distinct storm ids in one call (the storm id is each key's
    filename prefix), and ``delimiter='/'`` to list cycle hour dirs.
    """
    sess = session or requests
    keys: list[str] = []
    common: list[str] = []
    token: Optional[str] = None
    while True:
        params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if delimiter:
            params["delimiter"] = delimiter
        if token:
            params["continuation-token"] = token
        # Retry transient S3 hiccups (5xx, connection resets). A single
        # un-retried blip here would abort the whole cycle with no manifest,
        # since enumeration runs before any frame is rendered. 4xx is real and
        # surfaces immediately.
        last_err: Optional[Exception] = None
        r = None
        for attempt in range(1, _S3_RETRIES + 1):
            try:
                r = sess.get(S3_BASE + "/", params=params, timeout=60)
                if r.status_code >= 500:
                    raise requests.HTTPError(f"S3 {r.status_code}", response=r)
                r.raise_for_status()
                break
            except (requests.ConnectionError, requests.Timeout,
                    requests.HTTPError) as e:
                # Don't retry genuine client errors (4xx).
                resp = getattr(e, "response", None)
                if resp is not None and 400 <= resp.status_code < 500:
                    raise
                last_err = e
                if attempt < _S3_RETRIES:
                    time.sleep(0.6 * attempt)
        if r is None:
            raise last_err if last_err else RuntimeError("S3 list failed")
        root = ET.fromstring(r.content)
        for c in root.findall(f"{S3_NS}Contents"):
            k = c.findtext(f"{S3_NS}Key")
            if k:
                keys.append(k)
        for cp in root.findall(f"{S3_NS}CommonPrefixes"):
            p = cp.findtext(f"{S3_NS}Prefix")
            if p:
                common.append(p)
        if root.findtext(f"{S3_NS}IsTruncated") == "true":
            token = root.findtext(f"{S3_NS}NextContinuationToken")
            if not token:
                break
        else:
            break
    return keys, common


def _cycle_prefix(model: str, date: str, hh: str) -> str:
    return f"{MODEL_TOKEN[model]}/{date}/{hh}/"


def list_hours(model: str, date: str, session=None) -> list[str]:
    """Hour subdirs (e.g. ['00','06','12','18']) present for a date."""
    base = f"{MODEL_TOKEN[model]}/{date}/"
    _, common = _s3_list(base, delimiter="/", session=session)
    hours = []
    for p in common:
        m = re.match(rf"^{re.escape(base)}(\d{{2}})/$", p)
        if m:
            hours.append(m.group(1))
    return sorted(hours)


def list_dates(model: str, session=None) -> list[str]:
    """All YYYYMMDD cycle-date subdirs under a model, ascending."""
    base = f"{MODEL_TOKEN[model]}/"
    _, common = _s3_list(base, delimiter="/", session=session)
    dates = []
    for p in common:
        m = re.match(rf"^{re.escape(base)}(\d{{8}})/$", p)
        if m:
            dates.append(m.group(1))
    return sorted(dates)


def list_storms(model: str, date: str, hh: str, session=None) -> list[str]:
    """Distinct storm ids in a cycle (one delimiter='.' list call)."""
    prefix = _cycle_prefix(model, date, hh)
    _, common = _s3_list(prefix, delimiter=".", session=session)
    storms = []
    for p in common:
        # p == "hfsa/20230909/00/13l."  → take the token after the prefix
        tail = p[len(prefix):].rstrip(".")
        if STORM_ID_RE.match(tail):
            storms.append(tail)
    return sorted(set(storms))


def list_fxx(model: str, date: str, hh: str, storm: str, domain: str,
             session=None) -> list[int]:
    """Sorted forecast hours available for a (storm, domain) in a cycle."""
    tok = MODEL_TOKEN[model]
    prefix = f"{tok}/{date}/{hh}/{storm}.{date}{hh}.{tok}.{domain}.f"
    keys, _ = _s3_list(prefix, session=session)
    fxx = set()
    for k in keys:
        m = FXX_RE.search(k)
        if m:
            fxx.add(int(m.group(1)))
    return sorted(fxx)


def cycle_is_complete(model: str, date: str, hh: str, session=None) -> bool:
    """True once any storm's storm-nest run has reached the terminal hour.

    That terminal frame is the last thing a finished run uploads, so its
    presence means the cycle is fully written - we never render a half-cycle.
    """
    tok = MODEL_TOKEN[model]
    for storm in list_storms(model, date, hh, session=session):
        key = (f"{tok}/{date}/{hh}/{storm}.{date}{hh}.{tok}.storm.atm"
               f".f{TERMINAL_FXX:03d}.grb2")
        keys, _ = _s3_list(key, session=session)
        if any(k == key for k in keys):
            return True
    return False


def resolve_latest_cycle(model: str, max_dates_back: int = 4,
                         session=None) -> Optional[tuple[str, str]]:
    """Newest (date, hh) whose cycle is complete, scanning newest-first."""
    dates = list_dates(model, session=session)
    for date in reversed(dates[-max_dates_back:]):
        for hh in reversed(list_hours(model, date, session=session)):
            if cycle_is_complete(model, date, hh, session=session):
                log.info("latest complete cycle: %s %s %sZ", model, date, hh)
                return date, hh
    return None


# ---------------------------------------------------------------------------
# Storm identity
# ---------------------------------------------------------------------------
# We display the ATCF storm id (e.g. "13L") rather than the human name. The
# HAFS run's own trak.atcfunix deck does NOT carry a usable name (it fills the
# standard STORMNAME column with a diagnostic number), and the only reliable
# name source - the NHC/JTWC live ATCF decks - is current-season-only,
# per-agency, and basin-specific (the JTWC half needs the Cloudflare proxy the
# ACE pipeline uses). Showing the id is always correct and basin-agnostic;
# wiring in live-deck names is a future enhancement. [[hafs-model-page]]
def storm_basin(storm: str) -> tuple[str, str]:
    slug, label = BASIN_BY_LETTER.get(storm[-1], ("xx", "Unknown basin"))
    return slug, label


# ---------------------------------------------------------------------------
# Frame rendering (process pool; geojson loaded once per worker)
# ---------------------------------------------------------------------------
_COUNTRIES: Optional[dict] = None
_COAST: Optional[dict] = None
_STATES: Optional[dict] = None


def _worker_init() -> None:
    """Load the Natural Earth basemap once per pool worker."""
    global _COUNTRIES, _COAST, _STATES
    _COUNTRIES = (hp._load_geojson("ne_10m_admin_0_countries.geojson")
                  or hp._load_geojson("ne_50m_admin_0_countries.geojson")
                  or hp._load_geojson("ne_110m_admin_0_countries.geojson"))
    _COAST = (hp._load_geojson("ne_10m_coastline.geojson")
              or hp._load_geojson("ne_50m_coastline.geojson")
              or hp._load_geojson("ne_110m_coastline.geojson"))
    # admin_1 state/province borders for the canonical filled basemap (50m,
    # optional - a missing layer just omits state borders).
    _STATES = hp._load_geojson("ne_50m_admin_1_states_provinces.geojson")


@dataclass
class IngestJob:
    """One INGEST task: fetch + decode every GRIB a frame's products need, ONCE,
    into the field cache. One per (model, storm, domain, fxx) - NOT per product."""
    model: str
    storm: str
    domain: str          # raw, e.g. "storm.atm"
    fxx: int
    cycle_dt: dt.datetime
    cache_path: str      # field-cache .nc to write
    save_dir: str        # where Herbie stages its GRIB subsets
    want_refl: bool      # cycle needs composite reflectivity
    want_pwat: bool      # cycle needs precipitable water (mm)
    want_upper: bool     # cycle ingests the pressure-level (upper-air) fields
    want_env: bool       # frame needs the parent-domain environmental fields
    sat_parms: tuple     # GRIB2 parms for the sim-sat channels the cycle needs


@dataclass
class RenderJob:
    """One RENDER task: read a frame's cached fields and render ONE product from
    them - no GRIB fetch. One per (product, frame).

    ``cen_lat`` / ``cen_lon`` is the NAMESAKE storm's track fix at this fxx
    (from the run's own trak.atcfunix deck) - it anchors the L marker, the
    parent crop, and the headline stats to the run's own storm. ``anchor_lat``
    / ``anchor_lon`` is the LAST KNOWN fix (framing only) for hours past the
    tracker's coverage. All None -> render_frame degrades honestly."""
    model: str
    storm: str
    domain: str          # raw, e.g. "storm.atm"
    product: str         # product slug, e.g. "mslp_wind" / "refl"
    fxx: int
    cache_path: str      # field-cache .nc to read
    out_path: str
    cen_lat: Optional[float] = None
    cen_lon: Optional[float] = None
    anchor_lat: Optional[float] = None
    anchor_lon: Optional[float] = None


def _frame_key(model: str, storm: str, domain: str, fxx: int) -> tuple:
    """Identity of a frame across the ingest/render split."""
    return (model, storm, domain, fxx)


# How often a running pool reports progress. Both stages take hours; without a
# heartbeat their logs are silent from "planned N tasks" to "done N tasks", so a
# stage that is merely slow is indistinguishable from one that is wedged until
# the job's timeout kills it.
_POOL_HEARTBEAT_SECS = 120

# STALL FORENSICS: after this many seconds with ZERO task completions (in a
# stage with a deadline, i.e. ingest), capture evidence from every worker
# process ONCE - Python stacks (py-spy if installed), /proc State/wchan/
# syscall/stack, and the process tree. Incidents #3/#4 wedged at identical
# coordinates with nothing in the logs to say WHERE: two Python-level timeouts
# and SIGTERM all proved no-ops because a call blocked inside a C extension
# never returns to the interpreter, so the bound has to come from a layer the
# interpreter isn't involved in - and choosing that layer needs the evidence
# this captures (in-process C call vs subprocess vs kernel state). Capture
# only; the stage deadline still does the salvaging.
_FORENSICS_AFTER_S = int(os.environ.get("HAFS_STALL_FORENSICS_S", "480"))


def _read_proc(pid: int, name: str) -> str:
    try:
        txt = Path(f"/proc/{pid}/{name}").read_text(errors="replace").strip()
        return txt or "<empty>"
    except Exception as e:  # noqa: BLE001 - forensics never raise
        return f"<unreadable: {e}>"


def _capture_cmd(cmd: list, timeout: float = 20.0) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return out or f"<no output, rc={r.returncode}>"
    except Exception as e:  # noqa: BLE001
        return f"<capture failed: {e}>"


def _stall_forensics(pids: list, pool=None) -> None:
    """ONE-SHOT evidence dump for wedged workers. Every capture is individually
    guarded and time-bounded so the forensics themselves can never hang the
    build - the parent stays in charge throughout."""
    log.error("STALL FORENSICS - no task completion for %ds; capturing "
              "worker state (pids %s)", _FORENSICS_AFTER_S, pids)
    # The PARENT's own threads first - incident #5's capture showed the
    # workers GONE and the parent in a futex wait, which made the executor's
    # internal state (manager thread alive? pool flagged broken? work items
    # pending?) the decisive evidence. faulthandler dumps every thread's
    # Python stack without touching any lock.
    try:
        import faulthandler
        sys.stderr.write("STALL FORENSICS parent thread stacks:\n")
        faulthandler.dump_traceback(file=sys.stderr)
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass
    if pool is not None:
        try:
            log.error("STALL FORENSICS executor: broken=%r shutdown=%r "
                      "processes=%r pending_items=%d threads=%s",
                      getattr(pool, "_broken", "?"),
                      getattr(pool, "_shutdown_thread", "?"),
                      dict.keys(getattr(pool, "_processes", {}) or {}),
                      len(getattr(pool, "_pending_work_items", {}) or {}),
                      [t.name for t in threading.enumerate()])
        except Exception:  # noqa: BLE001
            pass
    # The whole process tree first: worker CHILDREN (a curl/external helper)
    # are exactly what py-spy can't see and what decides subprocess-vs-inproc.
    tree = _capture_cmd(["ps", "-eo", "pid,ppid,stat,wchan:30,etimes,cmd"])
    mine = {os.getpid()}
    lines = tree.splitlines()
    kept = [lines[0]] if lines else []
    changed = True
    while changed:            # transitive descendants of this builder
        changed = False
        for ln in lines[1:]:
            parts = ln.split(None, 2)
            if len(parts) >= 2:
                try:
                    pid, ppid = int(parts[0]), int(parts[1])
                except ValueError:
                    continue
                if ppid in mine and pid not in mine:
                    mine.add(pid)
                    changed = True
    for ln in lines[1:]:
        try:
            if int(ln.split(None, 1)[0]) in mine:
                kept.append(ln)
        except (ValueError, IndexError):
            continue
    log.error("STALL FORENSICS process tree:\n%s", "\n".join(kept))
    for pid in pids:
        state = next((ln for ln in _read_proc(pid, "status").splitlines()
                      if ln.startswith("State:")), "State: <unknown>")
        log.error("STALL FORENSICS pid %d: %s | wchan=%s | syscall=%s",
                  pid, state.strip(), _read_proc(pid, "wchan"),
                  _read_proc(pid, "syscall"))
        # Kernel stack needs root; Actions runners have passwordless sudo.
        log.error("STALL FORENSICS pid %d kernel stack:\n%s", pid,
                  _capture_cmd(["sudo", "-n", "cat", f"/proc/{pid}/stack"]))
        # Python stack: py-spy is optional (workflow installs it); try plain
        # then sudo (ptrace scope).
        spy = _capture_cmd(["py-spy", "dump", "--pid", str(pid)])
        if "capture failed" in spy or "Permission" in spy:
            spy = _capture_cmd(["sudo", "-n", "py-spy", "dump",
                                "--pid", str(pid)])
        log.error("STALL FORENSICS pid %d python stack:\n%s", pid, spy)

# ---------------------------------------------------------------------------
# INGEST HANG-PROOFING (2026-07-31 incident, the third HAFS staleness in two
# weeks). From Jul 29 ~02Z every Actions run wedged the same way: ingest
# reached ~23/258 frames in ~7 minutes, then the log went SILENT for 5h43m
# until timeout-minutes killed the job with zero frames rendered and nothing
# published. Same code, same dependency versions, same runner image on both
# sides of the boundary, and the very frames in the stall window ingest in
# 14-17 s from a codespace - the stall is the Actions-egress path to
# noaa-nws-hafs-pds, which is outside our control. What IS ours: the fetch
# path had NO TIMEOUT of any kind, so one stalled read wedged a worker
# forever, two wedged the whole stage, and the job burned its entire budget
# producing nothing. Three layers of defence, all env-tunable:
#
#   * a default SOCKET timeout in every ingest worker - a stalled read
#     becomes an exception in <= _INGEST_SOCKET_TIMEOUT_S rather than a
#     permanent wedge;
#   * a SIGALRM deadline around each ingest attempt - covers any non-socket
#     hang, turns it into a normal retryable failure (the pipeline already
#     tolerates missing frames and publishes partial coverage);
#   * a whole-stage DEADLINE - if the network is truly blackholed, stop
#     waiting, render what was ingested, and PUBLISH. A partial current
#     cycle beats a perfect 3-day-old one, and the five-state hour strip
#     shows the missing tail honestly.
_INGEST_SOCKET_TIMEOUT_S = int(os.environ.get("HAFS_INGEST_SOCKET_TIMEOUT_S",
                                              "120"))
_INGEST_TASK_TIMEOUT_S = int(os.environ.get("HAFS_INGEST_TASK_TIMEOUT_S",
                                            "300"))
_INGEST_DEADLINE_S = int(os.environ.get("HAFS_INGEST_DEADLINE_S",
                                        str(200 * 60)))


class IngestStalled(Exception):
    """An ingest attempt exceeded its wall-clock deadline (SIGALRM)."""


def _ingest_worker_init() -> None:
    """Per-worker setup for the ingest pool: the socket-level timeout that the
    fetch stack lacks. Set in the WORKER, not the parent - the parent's own
    S3 listing calls carry explicit timeouts already."""
    import socket
    socket.setdefaulttimeout(_INGEST_SOCKET_TIMEOUT_S)


_INGEST_RETRIES = 3   # AWS S3 throws sporadic 500s on the .idx range reads;
                      # the file is there, so a short retry clears the hole. This
                      # is the ONLY stage that touches the network now.

# Ingest workers are recycled after this many frames. NOT a nicety - it is what
# makes the current pool widths possible at all.
#
# Measured: a worker's high-water RSS climbs +21 MB per frame, LINEARLY, with no
# sign of plateauing over 12 frames (1914.7 MB after the first, 2170.7 after the
# twelfth), and malloc_trim does not reclaim it. Extrapolated over a cycle's ~258
# frames an unrecycled worker would reach ~7.3 GB and OOM every host we run on.
# Recycling bounds it:  high-water ~= 1915 + 21 * (N - 1) MB.
#
# HOW recycling happens changed on 2026-08-03, and the old way is BANNED:
# ProcessPoolExecutor's max_tasks_per_child ZOMBIFIES THE POOL AT THE FIRST
# WORKER RECYCLE - reproduced 3/3 on stock CPython 3.11.9 AND 3.12.1 with
# nothing but memory-churning no-op tasks (width 2, max_tasks_per_child 12:
# completions stop at exactly 24, as_completed never returns, ex._processes
# ends up EMPTY so there is nothing to kill or detect). That parameter -
# introduced 2026-07-29 in the ingest memory rework - was the ENTIRE cause of
# staleness incidents #3-#5: the wedge-at-~23-frames signature is the recycle
# boundary, not an egress stall, which is why two Python-level timeouts and
# SIGTERM all changed nothing. Recycling is now done at the POOL level:
# _run_pool slices the job list into chunks of width x N and runs each chunk
# in a FRESH executor, so no worker ever ingests more than N frames (the
# identical RSS bound) and a finished chunk pool is shut down and reaped like
# any other. Chunk pools use the default fork start method - the pre-rework
# regime that ran stable for weeks; one pool spawn per 12xwidth frames is the
# same ~0.3% overhead the per-worker respawn cost.
#
# The budget model below is unchanged: worker high-water ~2.15 GB at N=12,
# _INGEST_FRAME_BUDGET_MB is sized from it, and the two constants move
# together.
_INGEST_TASKS_PER_CHILD = 12

# Memory one ingest WORKER needs over its whole life - not what one frame costs.
# Those differ by more than the margin, which is why the distinction is spelled
# out: a single parent .atm env frame peaks at 1914 MB (measured on three storms
# in two basins: 1913.9 / 1913.8 / 1913.9 - the grid is fixed, so it is
# essentially deterministic), but a worker's high-water reaches 2171 MB by its
# twelfth frame because of the +21 MB/frame ratchet documented at
# _INGEST_TASKS_PER_CHILD. 2300 is that 2171 plus ~6%.
#
# Peak is INDEPENDENT of pool width (two workers at width 2 peaked at 1956 and
# 1927 MB), so width x this budget is the right model.
#
# THIS CONSTANT IS THE GATE, NOT THE CODE. _fit_ingest_width divides by it, so
# lowering the actual peak buys nothing until this is re-measured and lowered to
# match - a future optimisation that forgets it delivers exactly zero extra
# concurrency while looking like it worked. Re-measure whenever the ingest's
# allocation profile OR _INGEST_TASKS_PER_CHILD changes.
_INGEST_FRAME_BUDGET_MB = 2300
# Held back for the parent process, the OS, and page cache.
_HOST_RESERVE_MB = 1024


def _cgroup_limit_mb() -> Optional[int]:
    """This process's container memory limit, or None if unlimited/not in one.

    Load-bearing inside Docker: /proc/meminfo reports the HOST's RAM, so a
    worker in a 24 GB container on a 31 GB box would otherwise size itself
    against 31 GB and get OOM-killed by the cgroup, not by the kernel.
    """
    for p, unlimited in (("/sys/fs/cgroup/memory.max", "max"),               # v2
                         ("/sys/fs/cgroup/memory/memory.limit_in_bytes", None)):  # v1
        try:
            raw = Path(p).read_text().strip()
        except OSError:
            continue
        if raw == unlimited:
            return None
        try:
            v = int(raw)
        except ValueError:
            continue
        # v1 reports a sentinel near 2^63 for "no limit".
        if v <= 0 or v >= (1 << 62):
            return None
        return v // (1024 * 1024)
    return None


def _available_mb() -> Optional[int]:
    """Memory this process may actually use, honouring a container limit."""
    avail = None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                avail = int(line.split()[1]) // 1024
                break
    except (OSError, ValueError, IndexError):
        pass
    lim = _cgroup_limit_mb()
    if lim is not None:
        avail = lim if avail is None else min(avail, lim)
    return avail


def _fit_ingest_width(requested: int) -> int:
    """Clamp the ingest pool to what memory can actually hold.

    ``requested`` is a CEILING, never a target: this only ever lowers it. That
    asymmetry is the whole point - the flag says how wide we WANT to go, and the
    host says how wide it can afford, so the same command is correct on a 7 GB
    runner, a 16 GB runner and a 24 GB box container without anyone re-guessing
    a constant every time the memory profile or the runner class changes. An
    OOM here is expensive out of all proportion: the pool dies, the halving
    backoff re-walks the unfinished frames, and a cycle that would have
    finished does not.

    Unknown memory (no /proc, an odd platform) -> honour the request unchanged;
    this is a guard, not a gate.
    """
    avail = _available_mb()
    if not avail or requested <= 1:
        return requested
    fits = max(1, (avail - _HOST_RESERVE_MB) // _INGEST_FRAME_BUDGET_MB)
    width = min(requested, int(fits))
    if width < requested:
        log.warning("ingest width %d -> %d: %d MB available, %d MB reserved, "
                    "%d MB per worker", requested, width, avail,
                    _HOST_RESERVE_MB, _INGEST_FRAME_BUDGET_MB)
    else:
        log.info("ingest width %d (%d MB available, room for %d)",
                 width, avail, fits)
    return width


def _trim_malloc() -> None:
    """Return freed heap back to the OS (glibc ``malloc_trim``).

    Python releasing an array only returns it to the allocator, not to the
    kernel. Without this an ingest worker's RSS is a high-water mark, so the
    concurrency the pool can safely run is set by the worst frame any worker
    ever touched rather than by the frame it is on. No-op off glibc.
    """
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:  # noqa: BLE001 - musl/macOS/anything else: just skip
        pass


def _ingest_one(job: IngestJob) -> dict:
    """INGEST one frame into the field cache. Returns a result dict (never raises).

    Retries transient fetch failures - over hundreds of frames these are routine
    and would otherwise leave gaps. Includes ``FileNotFoundError``: Herbie infers
    "no GRIB" from a HEAD whose ``.ok`` is False for any status >= 400, so a
    transient S3 5xx on the existence check is indistinguishable from a real 404;
    since we only plan frames ``list_fxx`` reported present, retrying is right.
    """
    import signal

    def _stalled(signum, frame):
        raise IngestStalled(
            f"ingest exceeded {_INGEST_TASK_TIMEOUT_S}s wall clock")

    last_err: Optional[Exception] = None
    for attempt in range(1, _INGEST_RETRIES + 1):
        try:
            # Hard per-attempt deadline. A stalled S3 read used to block a
            # worker FOREVER (no timeout anywhere in the fetch stack); the
            # alarm turns that into a retryable failure like any other, and
            # the retry gets fresh connections. Installed per attempt so it
            # is correct under both fork and spawn workers.
            signal.signal(signal.SIGALRM, _stalled)
            signal.alarm(_INGEST_TASK_TIMEOUT_S)
            fc.ingest_frame(
                job.model, job.storm, job.domain, job.cycle_dt, job.fxx,
                Path(job.cache_path), job.save_dir,
                want_refl=job.want_refl, want_pwat=job.want_pwat,
                want_upper=job.want_upper, want_env=job.want_env,
                sat_parms=job.sat_parms,
                remove_grib=True,
            )
            return {"ok": True, "model": job.model, "storm": job.storm,
                    "domain": job.domain, "fxx": job.fxx}
        except Exception as e:  # noqa: BLE001 - one bad frame must not sink the run
            # Disarm BEFORE the retry sleep: except runs before finally, and a
            # still-armed alarm firing mid-sleep would raise out of the
            # "never raises" contract.
            signal.alarm(0)
            last_err = e
            if attempt < _INGEST_RETRIES:
                time.sleep(0.6 * attempt)
        finally:
            # ALWAYS disarm - covers the success path (and is a no-op after
            # the except path already did it).
            signal.alarm(0)
            # Hand this frame's arenas back before the worker picks up the next
            # one, so a pool's memory tracks the frame in flight rather than the
            # worst frame it has ever seen. Runs on the failure path too - a
            # frame that died mid-decode is exactly the one holding the most.
            _trim_malloc()
    return {"ok": False, "model": job.model, "storm": job.storm,
            "domain": job.domain, "fxx": job.fxx,
            "error": f"{type(last_err).__name__}: {last_err}"}


def _render_one(job: RenderJob) -> dict:
    """RENDER one product from the field cache - NO GRIB fetch. Never raises.

    Reads the frame's cache entry and reconstructs exactly the HafsFrame this
    product needs (the .atm fields for wind/refl, the matching .sat BT channel
    for the sim-sat products). Failures here are deterministic (a degenerate BT
    channel, a missing cache entry), so a single attempt is enough - the retry
    that used to cover transient S3 reads now lives in the ingest stage. A failed
    product is logged and skipped; the rest of the cycle still publishes.
    """
    want_refl = job.product == "refl"
    want_pwat = job.product == "mslp_pwat"
    # Upper-air / env products declare their need via requires_attr (the registry
    # guard), so the render reconstructs frame.upper / frame.env from the cache.
    want_upper = reg.get_spec(job.product).requires_attr == "upper"
    want_env = reg.get_spec(job.product).requires_attr == "env"
    # Sim-sat products read their BT channel by GRIB2 parameterNumber from the
    # product's registry spec (None for wind/refl/pwat/upper); a PCT product
    # (sat_pct set) derives its field from two channels instead.
    sat_parm = reg.sat_parm(job.product)
    sat_pct = reg.sat_pct(job.product)
    try:
        # Last line of the structural gate. The planner already filtered this
        # pair, so reaching here with an incompatible one is a BUG - fail the
        # single product loudly (the pool's per-task isolation turns it into a
        # logged failure) rather than emitting a meaningless PNG.
        reg.assert_renderable(job.model, job.product)
        frame = fc.load_frame(Path(job.cache_path), want_refl=want_refl,
                              want_pwat=want_pwat, want_upper=want_upper,
                              want_env=want_env,
                              sat_parm=sat_parm, sat_pct=sat_pct)
        os.makedirs(os.path.dirname(job.out_path), exist_ok=True)
        geom = hp.render_frame(frame, job.out_path, _COUNTRIES, _COAST,
                               states=_STATES, product=job.product,
                               cen_lat=job.cen_lat, cen_lon=job.cen_lon,
                               anchor_lat=job.anchor_lat,
                               anchor_lon=job.anchor_lon)
        return {
            "ok": True, "model": job.model, "storm": job.storm,
            "domain": job.domain, "product": job.product, "fxx": job.fxx,
            "valid": frame.valid_time.replace(microsecond=0).isoformat() + "Z",
            # Per-frame map geometry (axes rect in px + lon/lat extent). Plain
            # JSON primitives so it survives the ProcessPoolExecutor boundary.
            "geometry": geom,
        }
    except Exception as e:  # noqa: BLE001 - one bad product must not sink the run
        return {"ok": False, "model": job.model, "storm": job.storm,
                "domain": job.domain, "product": job.product, "fxx": job.fxx,
                "error": f"{type(e).__name__}: {e}"}


def _run_pool(jobs_list: list, fn, jobs: int, record, straggler,
              initializer=None, max_tasks_per_child: Optional[int] = None,
              stage_deadline_s: Optional[int] = None) -> None:
    """Run ``fn`` over ``jobs_list`` in a process pool, calling ``record(result)``
    for each, with the BrokenProcessPool retry + per-task failure isolation shared
    by BOTH the ingest and render stages.

    ex.map() would discard the whole batch's results if one worker dies (a native
    eccodes/cfgrib crash or OOM raises BrokenProcessPool), so we submit + track
    which tasks haven't been recorded; if the pool breaks we rebuild it and re-run
    only the unfinished ones. After a few rebuilds the stragglers are recorded as
    failed (via ``straggler(job)``) so the run still publishes everything that did
    complete. ``initializer`` runs once per worker (geojson for render; None for
    ingest, which doesn't draw).

    ``max_tasks_per_child`` recycles a worker after that many tasks - the ingest
    stage uses it so a worker's RSS cannot ratchet to its worst frame and stay
    there (see _INGEST_TASKS_PER_CHILD). Left None for render, whose per-worker
    cost is a one-off geojson basemap that recycling would just re-pay.
    """
    if jobs <= 1:
        # Serial path gets the same heartbeat as the pool below - it is the path
        # a memory-clamped host lands on, i.e. exactly the slow case where being
        # able to see progress matters most.
        if initializer is not None:
            initializer()
        t0 = last_beat = time.time()
        for n, job in enumerate(jobs_list, 1):
            record(fn(job))
            now = time.time()
            if now - last_beat >= _POOL_HEARTBEAT_SECS:
                el = now - t0
                rate = n / el if el > 0 else 0.0
                eta = (len(jobs_list) - n) / rate if rate > 0 else float("nan")
                log.info("  ... %d/%d done in %.0fs (%.2f/s, eta %.0fs, serial)",
                         n, len(jobs_list), el, rate, eta)
                last_beat = now
        return
    width = max(1, jobs)
    # Stage deadline (the ingest stage sets one): a hard wall-clock bound on
    # the WHOLE stage, shared across every chunk below. This stops the stage
    # outright so the run continues with what completed and PUBLISHES. A
    # partial current cycle beats a perfect stale one.
    stage_end = (time.time() + stage_deadline_s) if stage_deadline_s else None
    deadline_hit = False
    # POOL-LEVEL RECYCLING (see _INGEST_TASKS_PER_CHILD): worker recycling via
    # the executor's max_tasks_per_child is BANNED - it zombifies the pool at
    # the first recycle on stock 3.11 and 3.12 (reproduced; incidents #3-#5).
    # A fresh pool per chunk of width x N tasks gives the identical per-worker
    # RSS bound with none of the machinery.
    queue = list(jobs_list)
    # Outer loop: one FRESH executor per chunk (all jobs in one chunk when no
    # recycling is asked for). Inner loop: 4 attempts; on each pool death HALVE
    # the width (jobs -> jobs/2 -> ... -> 1). A dead pool is almost always OOM:
    # a heavy GRIB decode that doesn't fit at this width. Retrying the
    # unfinished frames at the SAME width just OOMs again and abandons them
    # (the cause of the parent.atm/hafsb coverage gap on the memory-tighter
    # render worker). Halving lets the heavy frames fit; the final attempt is
    # fully serial, so every frame gets a minimal-memory try before being
    # recorded as failed. The halved width persists across chunks on purpose -
    # the memory pressure that killed one chunk's pool applies to the next.
    while queue and not deadline_hit:
      # Chunk size tracks the CURRENT width (halving may have shrunk it), so
      # no worker ever exceeds max_tasks_per_child tasks even in the
      # OOM-degraded regime - the RSS bound is the whole point.
      chunk_cap = (width * max_tasks_per_child) if max_tasks_per_child else None
      if chunk_cap:
          remaining, queue = queue[:chunk_cap], queue[chunk_cap:]
      else:
          remaining, queue = queue, []
      for pool_attempt in range(1, 5):
        if not remaining:
            break
        batch, remaining = remaining, []
        not_done = set(range(len(batch)))
        watch_stop = None
        try:
            # NEVER pass max_tasks_per_child here - recycling is the chunk
            # loop above (the executor parameter zombifies the pool at the
            # first recycle; see the ban at _INGEST_TASKS_PER_CHILD).
            with cf.ProcessPoolExecutor(max_workers=width,
                                        initializer=initializer) as ex:
                fut_to_i = {ex.submit(fn, job): i for i, job in enumerate(batch)}
                t_pool = last_beat = time.time()
                done = 0
                # Stall forensics (deadline stages only): a daemon watcher
                # that fires ONCE if completions stop for _FORENSICS_AFTER_S,
                # capturing worker stacks/kernel state while the wedge is
                # LIVE - the deadline salvage below then proceeds as usual.
                watch_stop = None
                if stage_end is not None:
                    watch_stop = threading.Event()
                    progress = {"t": time.time(), "fired": False}

                    def _watch(ev=watch_stop, pr=progress, pool=ex):
                        while not ev.wait(15):
                            if pr["fired"]:
                                return
                            if time.time() - pr["t"] > _FORENSICS_AFTER_S:
                                pr["fired"] = True
                                try:
                                    _stall_forensics(
                                        list(getattr(pool, "_processes", {})
                                             or {}), pool=pool)
                                except Exception:  # noqa: BLE001
                                    log.exception("stall forensics failed")
                                return

                    threading.Thread(target=_watch, daemon=True,
                                     name="stall-forensics").start()
                else:
                    progress = None
                as_completed_kw = {}
                if stage_end is not None:
                    as_completed_kw["timeout"] = max(1.0, stage_end - time.time())
                try:
                    for fut in cf.as_completed(fut_to_i, **as_completed_kw):
                        i = fut_to_i[fut]
                        record(fut.result())
                        not_done.discard(i)
                        done += 1
                        if progress is not None:
                            progress["t"] = time.time()
                        now = time.time()
                        if now - last_beat >= _POOL_HEARTBEAT_SECS:
                            el = now - t_pool
                            rate = done / el if el > 0 else 0.0
                            eta = ((len(batch) - done) / rate
                                   if rate > 0 else float("nan"))
                            log.info("  ... %d/%d done in %.0fs (%.2f/s, "
                                     "eta %.0fs, width %d)", done, len(batch),
                                     el, rate, eta, width)
                            last_beat = now
                except cf.TimeoutError:
                    # THE STAGE DEADLINE - caught INSIDE the with-block on
                    # purpose: letting it unwind through __exit__ would call
                    # shutdown(wait=True) and join the wedged workers forever,
                    # which is the exact hang this exists to break. Terminate
                    # the workers first so the exit join returns promptly.
                    deadline_hit = True
                    log.error("stage deadline (%ss) reached with %d task(s) "
                              "unfinished - killing the pool and "
                              "continuing with what completed",
                              stage_deadline_s, len(not_done))
                    # SIGKILL + reap, NOT terminate(): run 30661097361 proved
                    # SIGTERM does nothing to a worker wedged inside a C call
                    # (signals are delivered between bytecodes; a blocked
                    # native call never returns to the interpreter) - both
                    # workers were still alive 2.5 h later, which kept the
                    # call-queue pipe open, which left the queue's feeder
                    # thread blocked, which let multiprocessing's UNBOUNDED
                    # Queue._finalize_join hang the MAIN thread at whatever
                    # later allocation triggered cyclic GC - one second short
                    # of the manifest write. SIGKILL needs no interpreter
                    # participation; dead readers break the pipe and every
                    # downstream join returns.
                    try:
                        procs = list(getattr(ex, "_processes", {})
                                     .values() or [])
                        for p in procs:
                            try:
                                p.kill()
                            except Exception:  # noqa: BLE001
                                pass
                        for p in procs:       # reap so no zombie/finalizer waits
                            try:
                                p.join(timeout=10)
                            except Exception:  # noqa: BLE001
                                pass
                        ex.shutdown(wait=False, cancel_futures=True)
                    except Exception:  # noqa: BLE001 - teardown must not mask
                        pass
            if deadline_hit:
                # No retry: the deadline exists precisely because retrying a
                # blackholed network buys nothing. Record the unfinished tasks
                # as stragglers so the run continues to render + publish what
                # it has.
                for i in sorted(not_done):
                    record(straggler(batch[i]))
                remaining = []
                break
        except BrokenProcessPool:
            remaining = [batch[i] for i in sorted(not_done)]
            width = max(1, width // 2)
            log.warning("worker pool died (attempt %d) - retrying %d unfinished "
                        "task(s) in a fresh pool at width %d",
                        pool_attempt, len(remaining), width)
        finally:
            if watch_stop is not None:
                watch_stop.set()
      for job in remaining:            # this chunk's attempt-exhausted leftovers
          record(straggler(job))
    # Deadline: every not-yet-attempted chunk is recorded too, so the stage's
    # ledger is complete and the run proceeds to render + publish what it has.
    for job in queue:
        record(straggler(job))


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def _iso_now() -> str:
    return (dt.datetime.now(dt.timezone.utc)
            .replace(microsecond=0).isoformat().replace("+00:00", "Z"))


def _frame_out_path(out_dir: Path, cycle: str, model: str, storm: str,
                    dom_slug: str, product: str, fxx: int, *,
                    cycle_scoped: bool) -> str:
    """Where a rendered frame PNG is written. cycle_scoped=True (the cron) nests
    under {cycle}/ so a direct out_dir->R2 upload lands at the v2
    path_template_cycles {cycle}/{model}/{storm}/{domain}/{product}/f{fxx}.png;
    default flat is the worker's layout (it prepends {cycle} at upload)."""
    base = (out_dir / cycle / model / storm / dom_slug / product if cycle_scoped
            else out_dir / model / storm / dom_slug / product)
    return str(base / f"f{fxx:03d}.png")


def _count_frames(storms: list) -> int:
    """Total rendered frames across a cycle's storms (model->domain->product->[fxx])."""
    return sum(len(prods[p])
              for s in storms for m in s.get("frames", {}).values()
              for prods in m.values() for p in prods)


def _cycle_entry(cycle: str, storms: list, *, started_utc: str,
                 in_progress: bool = False) -> dict:
    """ONE cycle's v2 entry, byte-shape-identical to the render worker's. The
    cron renders a COMPLETE cycle (in_progress=False), so frames_done ==
    frames_expected (the count it actually rendered)."""
    n = _count_frames(storms)
    return {
        "cycle": cycle,
        "in_progress": in_progress,
        "frames_done": n,
        "frames_expected": n,
        "started_utc": started_utc,
        "storms": storms,
    }


def _compose_manifest_v2(entries: list, models: Sequence[str],
                         domains: Sequence[str], products: Sequence[str],
                         fxx_step: int, *, now_iso: Optional[str] = None,
                         fxx_end: int = TERMINAL_FXX, fxx_pad: int = 3) -> dict:
    """The PUBLISHED manifest, ONE schema shared verbatim with the box render
    worker's ``compose_manifest_v2`` so the two writers on
    ``models/hafs/manifest.json`` never clobber each other with incompatible
    shapes. ``entries`` is newest-first cycle entries. Legacy single-cycle fields
    mirror the newest COMPLETE cycle (deploy-skew zero-blink) and bake its cycle
    into ``path_template`` so an old frontend resolves the cycle-scoped keys."""
    legacy = next((e for e in entries if not e.get("in_progress")), None)
    if legacy is None:
        legacy = next((e for e in entries if e.get("storms")), None)
    return {
        "generated_at": now_iso or _iso_now(),
        "product": PRODUCTS[products[0]],
        "products": [PRODUCTS[p] for p in products],
        # --- GEOMETRY (frame-invariant half) --------------------------------
        # The projection and canvas are identical for every frame; only the
        # axes rect and the lon/lat extent vary, and those ride per frame under
        # storms[].geometry. Together they make pixel <-> lon/lat an exact
        # affine on the client. "equirectangular" is literal here: a bare
        # matplotlib axes with degrees on both axes and no projection kwarg -
        # this repo does not use cartopy - so lon and lat are linear in x and y
        # and the only non-unit factor is the per-frame aspect, which is
        # already baked into axes_px.
        "projection": {
            "name": "equirectangular",
            "lon_lat_linear": True,
            "y_origin": "top",     # axes_px is in image coordinates
            # Longitudes in storms[].geometry[..].bbox are in the frame's
            # CONTINUOUS axis and MAY EXCEED +-180 (a West Pacific nest across
            # the antimeridian is drawn on e.g. 168..188). That is the frame the
            # axes was drawn in, so it is the only one where pixel <-> lon is
            # the exact affine; normalising it would make it non-monotonic.
            # Do the affine in this frame, normalise only for DISPLAY. Frames
            # where it bites carry "crosses_antimeridian": true.
            "lon_frame": "continuous",
            "lon_display_rule": "while lon > 180: lon -= 360",
        },
        "image": {"width": hp.IMAGE_W_PX, "height": hp.IMAGE_H_PX,
                  "dpi": hp.DPI},
        # --- VALUE PLANE ----------------------------------------------------
        # Per PHYSICAL QUANTITY, so a client can turn a pixel's color back into
        # a number, and so two models rendering one quantity are guaranteed the
        # same scale. Products point at these by their "quantity" key.
        "quantities": tq.value_planes(),
        # Model defs carry the full ModelSpec meta now (convection treatment, AI
        # paradigm, badge/intensity-stat flags, ATCF aid ids). {slug,label} stay
        # first and unchanged, so an older frontend reading only those two keys
        # is unaffected; everything else is purely additive.
        "models": [mr.get_model(m).model_meta() for m in models],
        "domains": [{"slug": DOMAINS[d][0], "label": DOMAINS[d][1], "raw": d}
                    for d in domains],
        "fxx_step": fxx_step,
        "fxx_pad": fxx_pad,
        "fxx_end": fxx_end,
        "path_template_cycles":
            "{cycle}/{model}/{storm}/{domain}/{product}/f{fxx}.png",
        "cycles": entries,
        # legacy single-cycle view (old frontend), cycle baked into the template:
        "cycle": legacy["cycle"] if legacy else None,
        "storms": legacy["storms"] if legacy else [],
        "path_template": (
            f"{legacy['cycle']}/{{model}}/{{storm}}/{{domain}}/{{product}}/f{{fxx}}.png"
            if legacy else "{model}/{storm}/{domain}/{product}/f{fxx}.png"),
    }


def _manifest_skeleton(models: Sequence[str], domains: Sequence[str],
                       products: Sequence[str], fxx_step: int,
                       cycle: Optional[str], storms: list,
                       *, started_utc: Optional[str] = None) -> dict:
    """v2 manifest for the cron's single rendered cycle (or an empty off-season
    manifest when ``cycle``/``storms`` are falsy). In ONE place so build_cycle and
    main()'s off-season path can't drift. Frames are cycle-scoped under
    ``{cycle}/`` (matching the worker) so ``path_template_cycles`` resolves."""
    now_iso = _iso_now()
    entries = ([_cycle_entry(cycle, storms, started_utc=started_utc or now_iso)]
               if cycle and storms else [])
    return _compose_manifest_v2(entries, models, domains, products, fxx_step,
                                now_iso=now_iso)


def build_cycle(date: str, hh: str, out_dir: Path, *,
                models: Sequence[str], domains: Sequence[str],
                products: Sequence[str],
                storms_filter: Optional[Sequence[str]] = None,
                basins_filter: Optional[Sequence[str]] = None,
                max_fxx: int = TERMINAL_FXX, fxx_step: int = 3,
                jobs: int = 4, ingest_width: Optional[int] = None,
                only_fxx: Optional[set] = None,
                cycle_scoped: bool = False,
                save_dir: str = "/tmp/herbie_data"
                ) -> tuple[dict, int, int, int]:
    """Render every frame for one cycle.

    Returns ``(manifest, n_storms_found, n_ok, n_fail)``. The caller uses the
    counts to tell genuine off-season (no storms) from a total render failure
    (storms found but nothing rendered) - the latter must NOT publish an empty
    manifest, or the workflow's pruning sync would wipe the live CDN frames.

    ``jobs`` sizes the RENDER pool (CPU-bound, light memory). ``ingest_width``
    sizes the INGEST pool (a large multi-field GRIB decode per frame -> memory
    bound); it defaults to ``jobs`` but is set LOWER on memory-tighter hosts so
    the heavy parent.atm/hafsb decodes don't OOM the pool. Both stages still get
    the halving-backoff recovery in ``_run_pool``.

    ``only_fxx`` (the PROGRESSIVE-rendering hook): render ONLY these forecast
    hours (intersected with what's actually posted) and BYPASS the per-pair
    terminal gate - the caller (the render worker's frame ledger) decides what
    is renderable, so a pair's early hours render without waiting for its
    f126. None keeps the classic complete-pair behavior exactly.
    """
    if ingest_width is None:
        ingest_width = jobs
    session = requests.Session()
    cycle = f"{date}{hh}"
    cycle_dt = dt.datetime.strptime(cycle, "%Y%m%d%H")
    build_started = _iso_now()   # this cycle entry's started_utc (v2 manifest)

    # Storms come from HAFS-A's listing (the storm set is shared across models);
    # any per-model gap is handled later by list_fxx returning [] for that pair.
    storms = list_storms(models[0], date, hh, session=session)
    if storms_filter:
        want = {s.lower() for s in storms_filter}
        storms = [s for s in storms if s in want]
    if basins_filter:
        bw = {b.lower() for b in basins_filter}
        storms = [s for s in storms if storm_basin(s)[0] in bw]
    log.info("cycle %s %sZ - storms: %s", date, hh, storms or "(none)")

    # The shared GRIB files a frame needs depend ONLY on which products this cycle
    # renders, not on the frame: refl adds the .atm REFC read, each sim-sat
    # product adds its .sat BT channel. PRMSL + 10 m wind are always read. So one
    # ingest per frame serves every product. Computed once for the whole cycle.
    cycle_want_refl = "refl" in products
    cycle_want_pwat = "mslp_pwat" in products
    cycle_want_upper = INGEST_UPPER_AIR
    # The parent-domain environmental fields (precip/SST/shear/PV/CAPE/SRH/...) are
    # ingested ONLY for parent.atm frames, and only when an env product is in this
    # cycle's product set - they are heavy and parent-only, so the storm nest never
    # pays for them. A product declares its env need via requires_attr == "env".
    cycle_has_env = any(reg.get_spec(p).requires_attr == "env" for p in products)
    # Union of EVERY .sat parm any product needs decoded -- the single channel
    # for clean_ir/water_vapor AND both V/H channels for a PCT product (89 PCT),
    # so the per-frame cache carries them all for one shared ingest.
    cycle_sat_parms = tuple(sorted({p for prod in products
                                    for p in reg.grib_parms(prod)}))

    # STRUCTURAL model x product gate, resolved ONCE per cycle. A product whose
    # signal IS resolved deep convection (reflectivity, simulated 89 GHz) is
    # dropped for any model that parameterises convection - see
    # hafs_registry.product_allowed and model_registry's docstring. Both HAFS
    # models are convection-permitting, so this is currently identity; it is the
    # gate that keeps the first coarse or AI model from silently rendering a
    # meaningless field.
    model_products = {m: set(reg.allowed_products_for_model(m, products))
                      for m in models}
    for m in models:
        dropped = [p for p in products if p not in model_products[m]]
        if dropped:
            log.info("model %s: %d product(s) gated off (parameterised "
                     "convection): %s", m, len(dropped), ", ".join(dropped))

    # Plan two stages up front: one INGEST task per (model, storm, domain, fxx)
    # frame, and one RENDER task per (product, frame). Ingest writes the field
    # cache; render reads it.
    ingest_jobs: list[IngestJob] = []
    render_jobs: list[RenderJob] = []
    storm_meta: dict[str, dict] = {}
    for storm in storms:
        basin_slug, basin_label = storm_basin(storm)
        storm_meta[storm] = {
            "id": storm,
            "name": storm.upper(),
            "basin": basin_slug,
            "basin_label": basin_label,
            "cycle": cycle,
            "init": cycle_dt.replace(microsecond=0).isoformat() + "Z",
            "frames": {},     # filled from render results
            # [model][domain][fxx] -> {"axes_px": [...], "bbox": [...]}. Keyed by
            # FRAME, not by product (see _record).
            "geometry": {},
            # [model][domain] -> the forecast hours upstream has POSTED for the
            # pair (step-filtered, BEFORE any progressive-subset narrowing).
            # This is what lets a client tell PENDING from UNAVAILABLE: an hour
            # in `expected` but not in `frames` is still coming while the cycle
            # is in progress and has failed once it is complete, and an hour
            # absent from `expected` was never going to exist at all.
            "expected": {},
        }
        # cycle_is_complete only confirms HAFS-A's storm nest reached the
        # terminal hour; the second model and the parent domain can still be
        # uploading. Require each (model, domain) to have its OWN terminal frame
        # before accepting it - a mid-upload pair is skipped and picked up by
        # the next (or backup) run rather than published half-written.
        terminal = min(max_fxx, TERMINAL_FXX)
        for model in models:
            # The run's OWN forecast track (trak.atcfunix): one tiny fetch per
            # (model, storm) that anchors every frame's L marker, parent crop,
            # and headline stats to the NAMESAKE storm instead of the domain
            # extremum. {} on failure -> render_frame degrades honestly.
            track = hp.fetch_hafs_track(model, storm, cycle_dt, session=session)
            taus = sorted(track)
            if track:
                log.info("track deck %s %s: %d fixes (f%03d..f%03d)",
                         model, storm, len(track), taus[0], taus[-1])
            else:
                log.warning("no track deck for %s %s - frames render untracked",
                            model, storm)
            # PROVISIONAL fallback - PROGRESSIVE MODE ONLY (only_fxx set):
            # during build-out the own deck may not cover a tau yet, so the
            # PREVIOUS cycle's deck anchors the same VALID time (tau+6 of the
            # 6 h-older run). The classic full/cron path (only_fxx=None) must
            # NOT use it: there a short deck means the tracker genuinely LOST
            # the storm, and the v0.3.0 honest degradation (no L, NA chip,
            # domain-wide labels) is the correct output - a prev-cycle fix
            # would silently re-label the lost tail with a 6 h-older
            # trajectory (review-confirmed regression). A new storm has no
            # previous deck -> {} -> honest degradation either way.
            prev_track: dict = {}
            if (only_fxx is not None
                    and len(taus) < (min(max_fxx, TERMINAL_FXX)
                                     // max(fxx_step, 1)) + 1):
                prev_track = hp.fetch_hafs_track(
                    model, storm, cycle_dt - dt.timedelta(hours=6),
                    session=session)
                if prev_track:
                    log.info("provisional track (previous cycle) %s %s: "
                             "%d fixes available", model, storm, len(prev_track))
            for domain in domains:
                avail = list_fxx(model, date, hh, storm, domain, session=session)
                avail = [f for f in avail if f <= max_fxx and f % fxx_step == 0]
                # EXPECTED = what upstream has posted for this pair, captured
                # BEFORE the progressive-subset narrowing below: the worker's
                # only_fxx ledger names what to render THIS pass, not what the
                # pair will eventually have, and expected must mean the latter
                # or PENDING hours would read as never-coming.
                posted = list(avail)
                if only_fxx is not None:
                    # Progressive subset: the caller names the exact hours.
                    # Intersecting with the posted list keeps this safe when
                    # upstream and the caller's view disagree momentarily.
                    avail = [f for f in avail if f in only_fxx]
                if not avail:
                    # No frames at all for this (model, domain) in this cycle.
                    # The standing case is HAFS-B: it stopped publishing to the
                    # noaa-nws-hafs-pds bucket after 2025-10-31 while HAFS-A
                    # keeps running, so the resolved (HAFS-A) cycle has no hfsb
                    # data. Log it so the absence is visible rather than silent,
                    # skip gracefully (the manifest just omits the model, frames
                    # are never wiped here), and let the pair reappear on its own
                    # if the model resumes publishing and reaches the terminal
                    # hour. See storm-id note above / [[hafs-b-not-published]].
                    log.info("skip %s %s %s, no frames published this cycle",
                             model, storm, domain)
                    continue
                if only_fxx is None and max(avail) < terminal:
                    # Classic complete-pair gate. BYPASSED for an explicit
                    # --only-fxx subset: progressive rendering exists precisely
                    # to render early hours before the terminal frame posts.
                    log.info("skip %s %s %s, incomplete (max f%03d < f%03d)",
                             model, storm, domain, max(avail), terminal)
                    continue
                dom_slug = DOMAINS[domain][0]
                (storm_meta[storm]["expected"]
                    .setdefault(model, {}))[dom_slug] = posted
                # The env fields are parent-only: ingest them only for parent.atm
                # frames (and only when this cycle renders an env product).
                want_env_frame = cycle_has_env and (domain == "parent.atm")
                for fxx in avail:
                    cpath = str(fc.cache_path(save_dir, cycle, model, storm,
                                              dom_slug, fxx))
                    # ONE ingest per frame: fetch + decode each shared GRIB once.
                    ingest_jobs.append(IngestJob(
                        model=model, storm=storm, domain=domain, fxx=fxx,
                        cycle_dt=cycle_dt, cache_path=cpath, save_dir=save_dir,
                        want_refl=cycle_want_refl, want_pwat=cycle_want_pwat,
                        want_upper=cycle_want_upper, want_env=want_env_frame,
                        sat_parms=cycle_sat_parms))
                    # The namesake's fix at THIS hour: own deck first, then the
                    # previous cycle's fix at the same valid time (provisional;
                    # progressive rendering), then last-known framing anchor,
                    # then honest degradation - all via pick_track_fix.
                    cen, anchor = hp.pick_track_fix(track, prev_track, fxx)
                    # One render per product, each reading the SAME cache entry
                    # into its own path segment (.../<dom_slug>/<product>/f###.png).
                    # A product restricted to specific domains (spec.domains, e.g.
                    # the parent-only env products) is skipped on other domains.
                    for product in products:
                        pdomains = reg.get_spec(product).domains
                        if pdomains and domain not in pdomains:
                            continue
                        # STRUCTURAL model gate: a product whose signal IS
                        # resolved deep convection is never SCHEDULED for a
                        # model that parameterises it, so the pair never
                        # renders, never reaches the manifest, and is never
                        # offered by the frontend. (No-op for HAFS, which is
                        # convection-permitting on both domains; the gate exists
                        # so the first coarse model added cannot regress it.)
                        if product not in model_products[model]:
                            continue
                        out_path = _frame_out_path(
                            out_dir, cycle, model, storm, dom_slug, product, fxx,
                            cycle_scoped=cycle_scoped)
                        render_jobs.append(RenderJob(
                            model=model, storm=storm, domain=domain,
                            product=product, fxx=fxx, cache_path=cpath,
                            out_path=out_path,
                            cen_lat=cen[0] if cen else None,
                            cen_lon=cen[1] if cen else None,
                            anchor_lat=anchor[0] if anchor else None,
                            anchor_lon=anchor[1] if anchor else None))

    log.info("planned %d ingest frame(s) + %d render task(s) across %d storm(s) "
             "- %d worker(s)", len(ingest_jobs), len(render_jobs), len(storms),
             jobs)

    t0 = time.time()

    # ----- Stage 1: INGEST. One fetch+decode per frame -> field cache. -----
    ingested_ok: set = set()
    n_ingest_fail = 0

    def _record_ingest(res: dict) -> None:
        nonlocal n_ingest_fail
        if res["ok"]:
            ingested_ok.add(_frame_key(res["model"], res["storm"],
                                       res["domain"], res["fxx"]))
        else:
            n_ingest_fail += 1
            log.warning("ingest failed: %s %s %s f%03d - %s", res["model"],
                        res["storm"], res["domain"], res["fxx"], res["error"])

    def _ingest_straggler(job: IngestJob) -> dict:
        return {"ok": False, "model": job.model, "storm": job.storm,
                "domain": job.domain, "fxx": job.fxx,
                "error": "BrokenProcessPool (unrecoverable after retries)"}

    # Ingest workers don't draw, so no geojson initializer (saves memory/time).
    # Sized by ingest_jobs (<= jobs): the GRIB decode is memory-bound, so a lower
    # width than the CPU-bound render avoids OOMing the pool on heavy frames.
    # Workers are recycled every _INGEST_TASKS_PER_CHILD frames so their RSS
    # tracks the frame in flight rather than the heaviest frame they have seen,
    # and the width is clamped to what this host's memory can actually hold.
    _run_pool(ingest_jobs, _ingest_one, _fit_ingest_width(ingest_width),
              _record_ingest, _ingest_straggler,
              initializer=_ingest_worker_init,
              max_tasks_per_child=_INGEST_TASKS_PER_CHILD,
              stage_deadline_s=_INGEST_DEADLINE_S)
    log.info("ingested %d/%d frame(s) ok (%d failed) in %.0fs",
             len(ingested_ok), len(ingest_jobs), n_ingest_fail, time.time() - t0)

    # ----- Stage 2: RENDER. Read the cache, render each product. No fetch. -----
    # A frame whose ingest failed is skipped for ALL its products (logged once,
    # never fatal), so render only touches frames with a cache entry.
    runnable = [j for j in render_jobs
                if _frame_key(j.model, j.storm, j.domain, j.fxx) in ingested_ok]
    n_skipped = len(render_jobs) - len(runnable)
    if n_skipped:
        log.warning("skipping %d render task(s) whose frame ingest failed",
                    n_skipped)

    # Accumulate successes into storm_meta[*]["frames"][model][dom_slug] = [fxx…]
    n_ok = n_fail = 0

    def _record(res: dict) -> None:
        nonlocal n_ok, n_fail
        if res["ok"]:
            n_ok += 1
            dom_slug = DOMAINS[res["domain"]][0]
            fr = storm_meta[res["storm"]]["frames"]
            (fr.setdefault(res["model"], {})
               .setdefault(dom_slug, {})
               .setdefault(res["product"], [])
               .append(res["fxx"]))
            # GEOMETRY is keyed by FRAME, not by product: every product of one
            # (model, domain, fxx) is drawn on the same axes with the same
            # extent, so storing it per product would repeat the identical eight
            # numbers 21 times. Keyed [model][domain][fxx] instead, which is
            # what a client needs to map a pixel to lon/lat on a nest whose
            # extent moves each forecast hour. First writer wins; the values
            # agree across products by construction.
            geom = res.get("geometry")
            if geom:
                (storm_meta[res["storm"]]["geometry"]
                    .setdefault(res["model"], {})
                    .setdefault(dom_slug, {})
                    .setdefault(str(res["fxx"]), geom))
        else:
            n_fail += 1
            log.warning("render failed: %s %s %s %s f%03d - %s", res["model"],
                        res["storm"], res["domain"], res["product"], res["fxx"],
                        res["error"])

    def _render_straggler(job: RenderJob) -> dict:
        return {"ok": False, "model": job.model, "storm": job.storm,
                "domain": job.domain, "product": job.product, "fxx": job.fxx,
                "error": "BrokenProcessPool (unrecoverable after retries)"}

    # Render workers load the Natural Earth basemap once each (initializer).
    _run_pool(runnable, _render_one, jobs, _record, _render_straggler,
              initializer=_worker_init)

    # Sort the per-pair fxx lists (pool completion order is nondeterministic),
    # and drop storms/models/domains/products that produced nothing.
    storms_out = []
    for storm in storms:
        meta = storm_meta[storm]
        for model in list(meta["frames"]):
            for dom_slug in list(meta["frames"][model]):
                prods = meta["frames"][model][dom_slug]
                for prod in list(prods):
                    prods[prod].sort()
                    if not prods[prod]:
                        del prods[prod]
                if not prods:
                    del meta["frames"][model][dom_slug]
            if not meta["frames"][model]:
                del meta["frames"][model]
        # Prune geometry AND expected in lockstep with frames, so the manifest
        # can never advertise an extent - or promise pending hours - for a
        # (model, domain) whose frames were all dropped: the frontend only
        # offers pairs with frames, so orphaned entries would be dead weight a
        # client could still trip over.
        for aux in ("geometry", "expected"):
            for model in list(meta[aux]):
                for dom_slug in list(meta[aux][model]):
                    if dom_slug not in meta["frames"].get(model, {}):
                        del meta[aux][model][dom_slug]
                if not meta[aux][model]:
                    del meta[aux][model]
        if meta["frames"]:
            storms_out.append(meta)

    log.info("rendered %d ok, %d failed in %.0fs", n_ok, n_fail, time.time() - t0)

    manifest = _manifest_skeleton(models, domains, products, fxx_step,
                                  cycle if storms_out else None, storms_out,
                                  started_utc=build_started)
    return manifest, len(storms), n_ok, n_fail


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cycle", help="explicit cycle YYYYMMDDHH (default: latest complete)")
    ap.add_argument("--models", default="hafsa,hafsb",
                    help="comma list of hafsa,hafsb")
    ap.add_argument("--domains", default="storm.atm,parent.atm",
                    help="comma list of storm.atm,parent.atm")
    ap.add_argument("--products", default=",".join(DEFAULT_PRODUCTS),
                    help="comma list of products to render: "
                         "mslp_wind,refl,clean_ir,water_vapor,mslp_pwat")
    ap.add_argument("--storm", help="restrict to one or more storm ids (comma list)")
    ap.add_argument("--basins", help="restrict to basin slugs (al,ep,wp,…; comma list)")
    ap.add_argument("--max-fxx", type=int, default=TERMINAL_FXX)
    ap.add_argument("--fxx-step", type=int, default=3)
    ap.add_argument("--cycle-scoped", action="store_true",
                    help="render frames under out_dir/{cycle}/... (the cron's "
                         "direct-upload layout, matching the v2 manifest's "
                         "path_template_cycles). Default OFF = flat layout (the "
                         "render worker, which prepends {cycle} at upload)")
    ap.add_argument("--only-fxx", default=None,
                    help="render ONLY these forecast hours (comma list, e.g. "
                         "0,3,6) and bypass the per-pair terminal gate - the "
                         "progressive-rendering hook; hours not yet posted "
                         "upstream are skipped gracefully")
    ap.add_argument("--jobs", type=int,
                    default=min((os.cpu_count() or 2), 6),
                    help="render-pool width (CPU-bound)")
    ap.add_argument("--ingest-jobs", type=int, default=None,
                    help="ingest-pool width (GRIB decode, memory-bound); "
                         "defaults to --jobs. Set LOWER on memory-tight hosts so "
                         "heavy parent.atm/hafsb decodes don't OOM the pool")
    ap.add_argument("--out-dir", default=str(Path.cwd() / "models" / "hafs"))
    ap.add_argument("--save-dir",
                    default=os.environ.get("HERBIE_DATA", "/tmp/herbie_data"))
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    domains = [d.strip() for d in args.domains.split(",") if d.strip()]
    products = [p.strip() for p in args.products.split(",") if p.strip()]
    for m in models:
        if m not in MODEL_TOKEN:
            ap.error(f"unknown model {m!r}")
    for d in domains:
        if d not in DOMAINS:
            ap.error(f"unknown domain {d!r}")
    for p in products:
        if p not in PRODUCTS:
            ap.error(f"unknown product {p!r}")
    if not products:
        ap.error("--products must list at least one of: " + ",".join(PRODUCTS))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.cycle:
        if not re.match(r"^\d{10}$", args.cycle):
            ap.error("--cycle must be YYYYMMDDHH")
        date, hh = args.cycle[:8], args.cycle[8:]
    else:
        resolved = resolve_latest_cycle(models[0])
        if resolved is None:
            log.warning("no complete cycle found - writing empty manifest")
            (out_dir / "manifest.json").write_text(json.dumps(
                _manifest_skeleton(models, domains, products, args.fxx_step,
                                   None, []),
                indent=2))
            return 0
        date, hh = resolved

    only_fxx = None
    if args.only_fxx:
        try:
            only_fxx = {int(x) for x in args.only_fxx.split(",") if x.strip()}
        except ValueError:
            ap.error("--only-fxx must be a comma list of integers")
        if not only_fxx:
            ap.error("--only-fxx must list at least one forecast hour")

    manifest, n_storms, n_ok, n_fail = build_cycle(
        date, hh, out_dir, models=models, domains=domains, products=products,
        storms_filter=(args.storm.split(",") if args.storm else None),
        basins_filter=(args.basins.split(",") if args.basins else None),
        max_fxx=args.max_fxx, fxx_step=args.fxx_step,
        jobs=args.jobs, ingest_width=args.ingest_jobs, only_fxx=only_fxx,
        cycle_scoped=args.cycle_scoped,
        save_dir=args.save_dir,
    )

    # Total failure: storms WERE found but nothing rendered. Do not write a
    # manifest - an empty one is indistinguishable from off-season and would let
    # update-hafs.yml's pruning sync wipe the live CDN frames. Exit non-zero so
    # the workflow aborts before the destructive upload and the prior cycle
    # stays live.
    if n_storms > 0 and n_ok == 0:
        log.error("found %d storm(s) but rendered 0 frames (%d failed) - "
                  "aborting without publishing", n_storms, n_fail)
        return 1

    manifest_path = out_dir / "manifest.json"
    # THE COMMIT POINT. Everything before this line is the build; everything
    # after it is expendable. Nothing that can block (pool teardown, joins,
    # finalizers) is permitted between render completion and this write - and
    # nothing at all runs after it except the log line and the hard exit below.
    manifest_path.write_text(json.dumps(manifest, indent=2))
    log.info("wrote %s - %d storm(s), cycle %s", manifest_path,
             len(manifest["storms"]), manifest["cycle"])
    return 0


if __name__ == "__main__":
    # os._exit, not sys.exit: run 30661097361 finished its build and then hung
    # 2h27m in teardown territory (a multiprocessing finalizer joining a thread
    # blocked on a wedge-survivor's pipe can fire from cyclic GC at ANY
    # allocation, and interpreter exit joins non-daemon threads). A finished
    # build must never be held hostage by cleanup: flush what the workflow log
    # needs, then leave without running any join, atexit hook, or finaliser.
    _rc = main()
    # SELF-RESCUE: run 30764256854 wrote its manifest and then the PROCESS
    # ITSELF sat alive for 2.5 h somewhere in this epilogue's vicinity, so the
    # workflow's publish steps never ran and the salvage was lost anyway.
    # faulthandler's watchdog is C-level - immune to any Python lock, GC pause
    # or logging deadlock - and exit=True calls C _exit after dumping EVERY
    # thread's stack to stderr: if this epilogue ever blocks again, the log
    # gets the exact stacks and the process still dies, so the publish steps
    # still run.
    try:
        import faulthandler
        faulthandler.dump_traceback_later(120, exit=True)
    except Exception:  # noqa: BLE001
        pass
    logging.shutdown()
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass
    os._exit(_rc)
