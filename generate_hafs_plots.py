#!/usr/bin/env python3
"""HAFS full-cycle plot builder - first product of the ``/models/`` page.

Scales the validated single-frame vertical slice in :mod:`hafs_plot` into a
batch renderer that, for the latest complete HAFS cycle, loops every active
storm × {HAFS-A, HAFS-B} × {storm nest, parent domain} × {MSLP+10 m wind,
composite reflectivity} × forecast hour and writes a TAT-styled PNG per frame
plus a ``manifest.json`` the ``/models/`` frontend reads.

Pipeline (mirrors the GIBS still pattern - see ``generate_gibs_truecolor.py``):

1. Resolve the newest *complete* cycle for HAFS-A by listing the public
   ``noaa-nws-hafs-pds`` S3 bucket over HTTP (no boto3, no credentials). A cycle
   is "complete" once at least one storm's storm-nest run reaches ``f126`` -
   i.e. the model finished and uploaded, so we never catch a half-written cycle.
2. Enumerate that cycle's storms with one ``delimiter=.`` list call (the storm
   id is each key's filename prefix, so S3 hands back the distinct ids directly).
3. For each (storm, model, domain) list the available forecast hours, then
   fetch + render each frame for every product, reusing
   ``hafs_plot.fetch_hafs_frame`` / ``render_frame``. Frames render in a process
   pool; a single failed frame is logged and skipped, never fatal - partial
   coverage still publishes.
4. Emit ``manifest.json`` listing, per storm, the fxx that actually rendered for
   each (model, domain, product). The frontend derives valid times as
   init + fxx·3 h.

Output layout (also the R2 key layout under ``models/hafs/``)::

    models/hafs/manifest.json
    models/hafs/{model}/{storm}/{domain}/{product}/f{FFF}.png
      e.g. models/hafs/hafsa/13l/storm/mslp_wind/f012.png
           models/hafs/hafsa/13l/storm/refl/f012.png

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
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence
from urllib.parse import quote
from xml.etree import ElementTree as ET

import requests

# Reuse the validated fetch + render from the single-frame slice. Importing it
# also pulls matplotlib (Agg) and the Herbie template override.
import hafs_plot as hp

log = logging.getLogger("hafs-build")

HERE = Path(__file__).resolve().parent

S3_BASE = "https://noaa-nws-hafs-pds.s3.amazonaws.com"
S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"

# model slug → (bucket dir / filename token). Both are "hfsX"; kept as one map
# so a future model with a different token (e.g. an ensemble) is a one-line add.
MODEL_TOKEN = {"hafsa": "hfsa", "hafsb": "hfsb"}
MODEL_LABEL = {"hafsa": "HAFS-A", "hafsb": "HAFS-B"}

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
PRODUCTS = {
    "mslp_wind": {"slug": "mslp_wind", "label": "MSLP + 10 m Wind",
                  "short": "Wind"},
    "refl": {"slug": "refl", "label": "Composite Reflectivity + MSLP",
             "short": "Reflectivity"},
}
DEFAULT_PRODUCTS = ["mslp_wind", "refl"]

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


def _worker_init() -> None:
    """Load the Natural Earth basemap once per pool worker."""
    global _COUNTRIES, _COAST
    _COUNTRIES = (hp._load_geojson("ne_10m_admin_0_countries.geojson")
                  or hp._load_geojson("ne_50m_admin_0_countries.geojson")
                  or hp._load_geojson("ne_110m_admin_0_countries.geojson"))
    _COAST = (hp._load_geojson("ne_10m_coastline.geojson")
              or hp._load_geojson("ne_50m_coastline.geojson")
              or hp._load_geojson("ne_110m_coastline.geojson"))


@dataclass
class FrameJob:
    model: str
    storm: str
    domain: str          # raw, e.g. "storm.atm"
    product: str         # product slug, e.g. "mslp_wind" / "refl"
    fxx: int
    cycle_dt: dt.datetime
    out_path: str
    save_dir: str
    cen_lat: Optional[float] = None
    cen_lon: Optional[float] = None


_RENDER_RETRIES = 3   # AWS S3 throws sporadic 500s on the .idx range reads;
                      # the file is there, so a short retry clears the hole.


def _render_one(job: FrameJob) -> dict:
    """Fetch + render a single frame. Returns a result dict (never raises).

    Retries transient fetch failures a few times - over the hundreds of frames
    in a cycle these are routine and would otherwise leave random gaps. This
    includes ``FileNotFoundError``: Herbie decides "no GRIB" from a ``HEAD``
    whose ``.ok`` is False for *any* status >= 400, so a transient S3 5xx on the
    existence check is indistinguishable from a real 404 and surfaces as
    FileNotFoundError. Since build_cycle only plans frames that ``list_fxx``
    reported present, a FileNotFoundError here is far more likely a blip than a
    true absence - so we retry it too, and only give up after the last attempt.
    """
    want_refl = job.product == "refl"
    last_err: Optional[Exception] = None
    for attempt in range(1, _RENDER_RETRIES + 1):
        try:
            frame = hp.fetch_hafs_frame(
                job.model, job.storm, job.domain, job.cycle_dt, job.fxx,
                job.save_dir, remove_grib=True, want_refl=want_refl,
            )
            os.makedirs(os.path.dirname(job.out_path), exist_ok=True)
            hp.render_frame(frame, job.out_path, _COUNTRIES, _COAST,
                            product=job.product,
                            cen_lat=job.cen_lat, cen_lon=job.cen_lon)
            return {
                "ok": True, "model": job.model, "storm": job.storm,
                "domain": job.domain, "product": job.product, "fxx": job.fxx,
                "valid": frame.valid_time.replace(microsecond=0).isoformat() + "Z",
            }
        except Exception as e:  # noqa: BLE001 - one bad frame must not sink the run
            last_err = e
            if attempt < _RENDER_RETRIES:
                time.sleep(0.6 * attempt)
    return {"ok": False, "model": job.model, "storm": job.storm,
            "domain": job.domain, "product": job.product, "fxx": job.fxx,
            "error": f"{type(last_err).__name__}: {last_err}"}


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def _manifest_skeleton(models: Sequence[str], domains: Sequence[str],
                       products: Sequence[str], fxx_step: int,
                       cycle: Optional[str], storms: list) -> dict:
    """The manifest shape, in ONE place, so the off-season/empty path in main()
    can't drift from build_cycle's output.

    Each storm's ``frames`` is nested model -> domain -> product -> [fxx]; the
    ``path_template`` carries the ``{product}`` segment. ``product`` (singular)
    is retained pointing at the default product so a reader of the prior schema
    still resolves a sensible default.
    """
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc)
                          .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "product": PRODUCTS[products[0]],
        "products": [PRODUCTS[p] for p in products],
        "models": [{"slug": m, "label": MODEL_LABEL[m]} for m in models],
        "domains": [{"slug": DOMAINS[d][0], "label": DOMAINS[d][1], "raw": d}
                    for d in domains],
        "fxx_step": fxx_step,
        "fxx_pad": 3,
        "path_template": "{model}/{storm}/{domain}/{product}/f{fxx}.png",
        "cycle": cycle,
        "storms": storms,
    }


def build_cycle(date: str, hh: str, out_dir: Path, *,
                models: Sequence[str], domains: Sequence[str],
                products: Sequence[str],
                storms_filter: Optional[Sequence[str]] = None,
                basins_filter: Optional[Sequence[str]] = None,
                max_fxx: int = TERMINAL_FXX, fxx_step: int = 3,
                jobs: int = 4, save_dir: str = "/tmp/herbie_data"
                ) -> tuple[dict, int, int, int]:
    """Render every frame for one cycle.

    Returns ``(manifest, n_storms_found, n_ok, n_fail)``. The caller uses the
    counts to tell genuine off-season (no storms) from a total render failure
    (storms found but nothing rendered) - the latter must NOT publish an empty
    manifest, or the workflow's pruning sync would wipe the live CDN frames.
    """
    session = requests.Session()
    cycle = f"{date}{hh}"
    cycle_dt = dt.datetime.strptime(cycle, "%Y%m%d%H")

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

    # Plan every (storm, model, domain) frame up front so the pool stays busy.
    jobs_list: list[FrameJob] = []
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
            "frames": {},   # filled from render results
        }
        # cycle_is_complete only confirms HAFS-A's storm nest reached the
        # terminal hour; the second model and the parent domain can still be
        # uploading. Require each (model, domain) to have its OWN terminal frame
        # before accepting it - a mid-upload pair is skipped and picked up by
        # the next (or backup) run rather than published half-written.
        terminal = min(max_fxx, TERMINAL_FXX)
        for model in models:
            for domain in domains:
                avail = list_fxx(model, date, hh, storm, domain, session=session)
                avail = [f for f in avail if f <= max_fxx and f % fxx_step == 0]
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
                if max(avail) < terminal:
                    log.info("skip %s %s %s, incomplete (max f%03d < f%03d)",
                             model, storm, domain, max(avail), terminal)
                    continue
                dom_slug = DOMAINS[domain][0]
                # Both products share this (model, domain)'s GRIB files, so each
                # available fxx is rendered once per product into its own path
                # segment (.../<dom_slug>/<product>/f###.png).
                for fxx in avail:
                    for product in products:
                        out_path = str(out_dir / model / storm / dom_slug
                                       / product / f"f{fxx:03d}.png")
                        jobs_list.append(FrameJob(
                            model=model, storm=storm, domain=domain,
                            product=product, fxx=fxx, cycle_dt=cycle_dt,
                            out_path=out_path, save_dir=save_dir))

    log.info("planned %d frames across %d storm(s) - rendering with %d worker(s)",
             len(jobs_list), len(storms), jobs)

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
        else:
            n_fail += 1
            log.warning("frame failed: %s %s %s f%03d - %s", res["model"],
                        res["storm"], res["domain"], res["fxx"], res["error"])

    t0 = time.time()
    if jobs <= 1:
        _worker_init()
        for job in jobs_list:
            _record(_render_one(job))
    else:
        # ex.map() would discard the WHOLE cycle's results if one worker dies
        # (a native eccodes/cfgrib crash or OOM raises BrokenProcessPool). Use
        # submit + as_completed and track which jobs haven't been recorded; if
        # the pool breaks, rebuild it and re-run only the unfinished jobs. After
        # a few rebuilds, record the stragglers as failed so the run still
        # publishes every frame that did render.
        remaining = list(jobs_list)
        for pool_attempt in range(1, 4):
            if not remaining:
                break
            batch, remaining = remaining, []
            not_done = set(range(len(batch)))
            try:
                with cf.ProcessPoolExecutor(max_workers=jobs,
                                            initializer=_worker_init) as ex:
                    fut_to_i = {ex.submit(_render_one, job): i
                                for i, job in enumerate(batch)}
                    for fut in cf.as_completed(fut_to_i):
                        i = fut_to_i[fut]
                        _record(fut.result())
                        not_done.discard(i)
            except BrokenProcessPool:
                remaining = [batch[i] for i in sorted(not_done)]
                log.warning("worker pool died (attempt %d) - retrying %d "
                            "unfinished frame(s) in a fresh pool",
                            pool_attempt, len(remaining))
        for job in remaining:
            _record({"ok": False, "model": job.model, "storm": job.storm,
                     "domain": job.domain, "product": job.product, "fxx": job.fxx,
                     "error": "BrokenProcessPool (unrecoverable after retries)"})

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
        if meta["frames"]:
            storms_out.append(meta)

    log.info("rendered %d ok, %d failed in %.0fs", n_ok, n_fail, time.time() - t0)

    manifest = _manifest_skeleton(models, domains, products, fxx_step,
                                  cycle if storms_out else None, storms_out)
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
                    help="comma list of products to render: mslp_wind,refl")
    ap.add_argument("--storm", help="restrict to one or more storm ids (comma list)")
    ap.add_argument("--basins", help="restrict to basin slugs (al,ep,wp,…; comma list)")
    ap.add_argument("--max-fxx", type=int, default=TERMINAL_FXX)
    ap.add_argument("--fxx-step", type=int, default=3)
    ap.add_argument("--jobs", type=int,
                    default=min((os.cpu_count() or 2), 6))
    ap.add_argument("--out-dir", default=str(HERE / "models" / "hafs"))
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

    manifest, n_storms, n_ok, n_fail = build_cycle(
        date, hh, out_dir, models=models, domains=domains, products=products,
        storms_filter=(args.storm.split(",") if args.storm else None),
        basins_filter=(args.basins.split(",") if args.basins else None),
        max_fxx=args.max_fxx, fxx_step=args.fxx_step,
        jobs=args.jobs, save_dir=args.save_dir,
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
    manifest_path.write_text(json.dumps(manifest, indent=2))
    log.info("wrote %s - %d storm(s), cycle %s", manifest_path,
             len(manifest["storms"]), manifest["cycle"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
