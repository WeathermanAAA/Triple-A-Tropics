#!/usr/bin/env python3
"""NASA GIBS MODIS/VIIRS true-color "latest pass" stills (Part B of the
true-color feature).

For each region we ask NASA's Global Imagery Browse Services (GIBS) for the
most-recent, adequately-filled CorrectedReflectance_TrueColor *daily composite*
and render it as a clean photographic JPEG with a slim title strip + watermark.

GIBS is free and needs NO credentials/API key — this is a plain HTTP WMS
GetMap fetch, so the whole script runs anywhere (locally or in CI) without
secrets. That's the reason Part B lands first: it's self-contained and fully
testable offline of the render service.

What "latest pass" means here
------------------------------
GIBS ``*_CorrectedReflectance_TrueColor`` layers are *daily global composites*
of the daytime swaths, not literally one satellite pass. A fully-elapsed UTC
day is filled globally; the in-progress day has gaps where the bird hasn't
passed yet. So "latest" = walk back from today and take the first day whose
returned crop is filled past ``--min-fill`` (no-data on GIBS is pure black, so
we measure the non-black fraction). Among sensors we prefer the current-gen
VIIRS birds, falling back to MODIS Aqua/Terra (250 m) if VIIRS GIBS is down.

Output
------
``{outdir}/{slug}_truecolor.jpg`` per region + ``{outdir}/manifest.json``.
Both are uploaded to R2 (cdn.triple-a-tropics.com/gibs/...) by the workflow and
are gitignored here (large, daily-churning media — same policy as sst/*.png).

Region config mirrors ``generate_sst_plots.py`` REGIONS (D4: reuse the SST
region set). Keep the two aligned if regions are ever added/removed — the
basin/region config dicts across this repo are intentionally duplicated, not
imported, so each generator stays self-contained.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np
import requests
from PIL import Image

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt

log = logging.getLogger("gibs-truecolor")

# ---------------------------------------------------------------------------
# GIBS endpoint + layers
# ---------------------------------------------------------------------------
# WMS 1.1.1 (SRS=EPSG:4326) deliberately, NOT 1.3.0: 1.1.1 takes BBOX in
# lon,lat (minx,miny,maxx,maxy) order. 1.3.0 + EPSG:4326 flips to lat,lon,
# which is the single most common GIBS-WMS footgun. 1.1.1 keeps the bbox
# math readable.
GIBS_WMS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"

# Sensor preference, best-first. VIIRS NOAA-20 / SNPP are the current
# operational workhorses with guaranteed daily global coverage; MODIS
# Aqua/Terra (250 m, aging past design life) are sharper but are the
# fallback if VIIRS GIBS has a gap. native_res_m is informational (surfaced
# in the manifest + title strip).
LAYER_PRIORITY: list[tuple[str, str, int]] = [
    ("VIIRS_NOAA20_CorrectedReflectance_TrueColor", "VIIRS NOAA-20", 375),
    ("VIIRS_SNPP_CorrectedReflectance_TrueColor", "VIIRS SNPP", 375),
    ("MODIS_Aqua_CorrectedReflectance_TrueColor", "MODIS Aqua", 250),
    ("MODIS_Terra_CorrectedReflectance_TrueColor", "MODIS Terra", 250),
]

# Sensor-set variants the frontend can toggle. The contiguous VIIRS-then-MODIS
# ordering of LAYER_PRIORITY makes each variant a plain slice: "both" is the
# full gap-fill chain, "viirs" the two VIIRS birds, "modis" the two MODIS birds.
# Every run bakes all three (one JPG each) so the frontend switches instantly.
LAYER_VARIANTS: list[tuple[str, str]] = [
    ("both", "Both"),
    ("viirs", "VIIRS"),
    ("modis", "MODIS"),
]
DEFAULT_LAYER_VARIANT = "both"


def select_layers(choice: str):
    """Slice LAYER_PRIORITY down to the sensors a variant uses.

    "viirs" -> the two VIIRS birds, "modis" -> the two MODIS birds, anything
    else ("both", default) -> the full priority chain. fetch_region consumes
    the returned list generically, so no other code changes by sensor set.
    """
    c = (choice or "both").lower()
    if c == "viirs":
        return LAYER_PRIORITY[0:2]
    if c == "modis":
        return LAYER_PRIORITY[2:4]
    return LAYER_PRIORITY  # both

# Native true-color GSD we target before capping. 250 m ≈ 0.00225°/px at the
# equator. We cap the long axis at MAX_PX so wide basins become daily-mosaic
# overviews instead of absurd 40k-px tiles, while small TC-scale regions keep
# near-native photographic detail.
DEG_PER_PX_NATIVE = 0.00225
DEFAULT_MAX_PX = 2400
# Accept a day once the composite is this filled. Tuned from real GIBS data:
# a *complete* day fills tropical/mid-lat regions to ~1.0 and the worst case
# (the global -75..75 view) to ~0.87, while an *in-progress* "today" sits well
# below (~0.6-0.75). 0.80 rejects today's partial composite and falls back to
# the freshest complete day; high-lat winter regions that can't reach it
# degrade gracefully via the best-so-far path in fetch_region().
DEFAULT_MIN_FILL = 0.80
DEFAULT_LOOKBACK_DAYS = 4     # today + 3 days back
BLACK_THRESH = 10             # per-channel value at/below which a px is "no-data"

DARK_BG = "#0a0d12"
TEXT_COLOR = "#e8eef5"
ACCENT_COLOR = "#79f0d6"

# ---------------------------------------------------------------------------
# Region definitions — MIRRORS generate_sst_plots.py REGIONS (see module
# docstring). Extents are (lon_min, lon_max, lat_min, lat_max); some use the
# 0–360 convention and/or cross the dateline — fetch_region() normalizes both.
# ---------------------------------------------------------------------------
REGIONS: dict[str, dict] = {
    "global": {"label": "Global", "extent": (30.0, 390.0, -75.0, 75.0)},
    "global-tropics": {"label": "Global Tropics", "extent": (30.0, 390.0, -45.0, 45.0)},
    "enso": {"label": "ENSO Regions", "extent": (120.0, 290.0, -15.0, 15.0)},
    "north-atlantic": {"label": "North Atlantic", "extent": (-100.0, 0.0, 0.0, 65.0)},
    "tropical-atlantic": {"label": "Tropical Atlantic", "extent": (-90.0, -10.0, 0.0, 30.0)},
    "western-atlantic": {"label": "Western Atlantic", "extent": (-100.0, -55.0, 8.0, 42.0)},
    "equatorial-atlantic": {"label": "Equatorial Atlantic", "extent": (-50.0, 15.0, -10.0, 15.0)},
    "south-atlantic": {"label": "South Atlantic", "extent": (-70.0, 20.0, -55.0, 0.0)},
    "northeast-pacific": {"label": "Northeast Pacific", "extent": (-160.0, -80.0, 0.0, 50.0)},
    "east-pacific": {"label": "East Pacific", "extent": (-140.0, -80.0, 5.0, 35.0)},
    "central-pacific": {"label": "Central Pacific", "extent": (-180.0, -140.0, 0.0, 35.0)},
    "northwest-pacific": {"label": "Northwest Pacific", "extent": (100.0, 180.0, 0.0, 60.0)},
    "north-pacific": {"label": "North Pacific", "extent": (100.0, 260.0, 0.0, 65.0)},
    "southwest-pacific": {"label": "Southwest Pacific", "extent": (140.0, 220.0, -45.0, 0.0)},
    "southeast-pacific": {"label": "Southeast Pacific", "extent": (-140.0, -70.0, -50.0, 0.0)},
    "australia": {"label": "Australia", "extent": (95.0, 180.0, -45.0, 5.0)},
    "indian-ocean": {"label": "Indian Ocean", "extent": (30.0, 130.0, -40.0, 30.0)},
    "mediterranean": {"label": "Mediterranean", "extent": (-10.0, 42.0, 28.0, 48.0)},
}


# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------
def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {"User-Agent": "triple-a-tropics.com GIBS true-color fetcher (+https://triple-a-tropics.com)"}
    )
    return s


# ---------------------------------------------------------------------------
# Bbox normalization + dateline split
# ---------------------------------------------------------------------------
def _split_bbox(extent: tuple[float, float, float, float]) -> list[tuple[float, float, float, float]]:
    """Turn a (possibly 0–360 / dateline-crossing) SST extent into one or two
    standard -180..180 sub-bboxes, west-to-east.

    A region defined in the 0–360 convention with an eastern edge > 180°
    (e.g. ENSO 120→290) wraps the antimeridian; WMS can't take a bbox with
    minlon > maxlon, so we split at ±180 into [w,180] and [-180, e-360] and
    stitch the two crops horizontally after fetch.
    """
    lon_min, lon_max, lat_min, lat_max = extent
    if lon_max > 180.0:
        west = (lon_min, 180.0, lat_min, lat_max)
        east = (-180.0, lon_max - 360.0, lat_min, lat_max)
        # Degenerate guard: a full 360° span (global) -> [30,180] + [-180,30].
        return [west, east]
    return [(lon_min, lon_max, lat_min, lat_max)]


def _target_dims(lon_span: float, lat_span: float, max_px: int) -> tuple[int, int]:
    """Pixel (width, height) for the full region, native 250 m GSD capped so
    the long axis ≤ max_px. Returned equirectangular so we can display 1:1."""
    native_w = lon_span / DEG_PER_PX_NATIVE
    native_h = lat_span / DEG_PER_PX_NATIVE
    scale = min(1.0, max_px / max(native_w, native_h))
    w = max(16, int(round(native_w * scale)))
    h = max(16, int(round(native_h * scale)))
    return w, h


# ---------------------------------------------------------------------------
# WMS fetch
# ---------------------------------------------------------------------------
def _wms_getmap(
    session: requests.Session,
    layer: str,
    bbox: tuple[float, float, float, float],
    width: int,
    height: int,
    date: dt.date,
    timeout: float = 60.0,
) -> Optional[np.ndarray]:
    """One WMS GetMap -> HxWx3 uint8 RGB array, or None on HTTP/parse failure."""
    lon_min, lon_max, lat_min, lat_max = bbox
    params = {
        "SERVICE": "WMS",
        "REQUEST": "GetMap",
        "VERSION": "1.1.1",
        "LAYERS": layer,
        "SRS": "EPSG:4326",
        "BBOX": f"{lon_min},{lat_min},{lon_max},{lat_max}",  # 1.1.1 = lon,lat order
        "WIDTH": str(width),
        "HEIGHT": str(height),
        "FORMAT": "image/jpeg",
        "TIME": date.isoformat(),
    }
    for attempt in range(3):
        try:
            r = session.get(GIBS_WMS, params=params, timeout=timeout)
            if r.status_code != 200:
                log.warning("  GIBS %s %s: HTTP %d", layer, date, r.status_code)
                return None
            ctype = r.headers.get("Content-Type", "")
            if "image" not in ctype:
                # GIBS reports errors as an XML ServiceException body.
                log.warning("  GIBS %s %s: non-image response (%s): %s",
                            layer, date, ctype, r.text[:200])
                return None
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            return np.asarray(img)
        except (requests.RequestException, OSError) as e:
            log.warning("  GIBS %s %s attempt %d failed: %s", layer, date, attempt + 1, e)
            time.sleep(1.5 * (attempt + 1))
    return None


def _fill_fraction(rgb: np.ndarray) -> float:
    """Fraction of pixels that carry imagery (not GIBS black no-data fill)."""
    if rgb.size == 0:
        return 0.0
    nonblack = np.any(rgb > BLACK_THRESH, axis=-1)
    return float(nonblack.mean())


@dataclass
class RegionResult:
    slug: str
    label: str
    file: str
    layer: str                 # primary (first) contributing layer
    sensor: str                # human label, e.g. "VIIRS NOAA-20 + MODIS Aqua"
    contributors: list[str]    # ordered list of sensors that filled pixels
    date: str
    native_res_m: int          # primary contributor's native GSD
    bbox: list[float]          # [lon_min, lat_min, lon_max, lat_max] in -180..180 terms
    width: int
    height: int
    fill: float


# Once a multi-sensor composite reaches this fraction we stop pulling more
# sensors — the residual holes (true polar night, etc.) won't fill anyway.
STOP_FILL = 0.985


def fetch_region(
    session: requests.Session,
    slug: str,
    cfg: dict,
    max_px: int,
    min_fill: float,
    lookback_days: int,
    layers: list[tuple[str, str, int]],
    date_override: Optional[dt.date],
    file_name: Optional[str] = None,
    crop_cache: Optional[dict] = None,
) -> Optional[tuple[np.ndarray, RegionResult]]:
    """Fetch the best gap-filled RGB crop for a region.

    A single sensor's daily composite has black inter-orbit gaps (big on wide
    tropical strips). Different sensors cross the equator at different times,
    so their gaps fall at different longitudes — layering them fills the holes.

    For each candidate date (most recent first) we composite the priority
    sensors: start with the highest-priority crop, then fill still-black pixels
    from each next sensor, stopping early once coverage passes STOP_FILL. The
    freshest date whose composite reaches min_fill wins.

    ``file_name`` overrides the RegionResult.file (so per-variant runs write
    distinct JPEGs). ``crop_cache`` is an optional per-region dict memoizing
    fetched layer crops keyed by ``(layer, date)`` — pass the SAME dict across
    the "both"/"viirs"/"modis" runs of one region so a (layer,date) crop the
    "both" run already pulled is reused instead of re-fetched over WMS.
    """
    lon_min, lon_max, lat_min, lat_max = cfg["extent"]
    lon_span = (lon_max - lon_min)
    lat_span = (lat_max - lat_min)
    width, height = _target_dims(lon_span, lat_span, max_px)
    subboxes = _split_bbox(cfg["extent"])

    # Per-subbox pixel widths split proportionally so the stitch lines up. All
    # layers are fetched at the SAME bbox/width/height, so crops are pixel-
    # aligned and compositing is a straight masked fill.
    sub_widths = []
    for sb in subboxes:
        frac = (sb[1] - sb[0]) / lon_span
        sub_widths.append(max(16, int(round(width * frac))))

    def _fetch_layer(layer: str, date: dt.date) -> Optional[np.ndarray]:
        # Reuse a (layer, date) crop already fetched for another variant of the
        # same region. None is a legitimate cached result (the layer had no
        # imagery on that date), so distinguish "miss" via the key's presence.
        cache_key = (layer, date)
        if crop_cache is not None and cache_key in crop_cache:
            return crop_cache[cache_key]
        parts: list[np.ndarray] = []
        for sb, sw in zip(subboxes, sub_widths):
            arr = _wms_getmap(session, layer, sb, sw, height, date)
            if arr is None:
                if crop_cache is not None:
                    crop_cache[cache_key] = None
                return None
            parts.append(arr)
        out = parts[0] if len(parts) == 1 else np.hstack(parts)
        if crop_cache is not None:
            crop_cache[cache_key] = out
        return out

    if date_override is not None:
        candidate_dates = [date_override]
    else:
        today = dt.datetime.now(dt.timezone.utc).date()
        candidate_dates = [today - dt.timedelta(days=d) for d in range(lookback_days)]

    best: Optional[tuple[np.ndarray, RegionResult]] = None
    for date in candidate_dates:
        composite: Optional[np.ndarray] = None
        contributors: list[str] = []
        primary_res = 250
        primary_layer = ""
        for layer, sensor, res_m in layers:
            arr = _fetch_layer(layer, date)
            if arr is None:
                continue
            if composite is None:
                composite = arr.copy()
                primary_res, primary_layer = res_m, layer
            else:
                hole = ~np.any(composite > BLACK_THRESH, axis=-1)
                if not hole.any():
                    break
                composite[hole] = arr[hole]
            contributors.append(sensor)
            if _fill_fraction(composite) >= STOP_FILL:
                break

        if composite is None:
            continue
        fill = _fill_fraction(composite)
        sensor_label = " + ".join(contributors)
        log.info("  %s: %s %s fill=%.2f (%dx%d)",
                 slug, sensor_label, date, fill, composite.shape[1], composite.shape[0])
        result = RegionResult(
            slug=slug,
            label=cfg["label"],
            file=file_name or f"{slug}_truecolor.jpg",
            layer=primary_layer,
            sensor=sensor_label,
            contributors=contributors,
            date=date.isoformat(),
            native_res_m=primary_res,
            bbox=[lon_min, lat_min, lon_max, lat_max],
            width=int(composite.shape[1]),
            height=int(composite.shape[0]),
            fill=round(fill, 3),
        )
        candidate = (composite, result)
        if fill >= min_fill:
            return candidate
        # Keep the best-so-far so a region that never reaches min_fill (e.g. a
        # high-lat winter strip) still gets its fullest available image rather
        # than nothing.
        if best is None or fill > best[1].fill:
            best = candidate

    if best is not None:
        log.warning("  %s: no date reached min_fill=%.2f; using best (%s, fill=%.2f)",
                    slug, min_fill, best[1].date, best[1].fill)
        return best
    log.warning("  %s: no imagery found in last %d day(s)", slug, lookback_days)
    return None


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
def render_jpeg(rgb: np.ndarray, res: RegionResult, out_path: str) -> None:
    """Photographic tile: the RGB crop + a slim title strip + watermark.

    No vector coastlines/graticule — true-color imagery already shows the
    land/sea boundary photographically; overlays only clutter the look.
    """
    h, w = rgb.shape[:2]
    aspect = w / max(h, 1)

    # Compact sensor label: full list lives in the manifest; the strip shows
    # the primary + "+N" so a 4-sensor composite doesn't overflow.
    if len(res.contributors) <= 1:
        sensor_short = res.sensor
    else:
        sensor_short = f"{res.contributors[0]} +{len(res.contributors) - 1}"

    # ~1400 px target width for the figure; title strip is a fixed fraction.
    fig_w = 12.0
    fig_h = max(3.0, fig_w / max(aspect, 0.3))
    title_h = 0.055
    fig = plt.figure(figsize=(fig_w, fig_h + fig_w * title_h / aspect), facecolor=DARK_BG)

    # Image axes fill the figure below the title strip.
    map_h = 1.0 - title_h
    ax = fig.add_axes([0.0, 0.0, 1.0, map_h])
    ax.imshow(rgb, aspect="auto", interpolation="nearest")
    ax.axis("off")

    # Watermark, bottom-left of the imagery (dark backing for legibility).
    ax.text(
        0.008, 0.02,
        "@WeathermanAAA_  ·  NASA GIBS",
        ha="left", va="bottom",
        color=ACCENT_COLOR, fontsize=9, transform=ax.transAxes,
        bbox=dict(facecolor="black", alpha=0.45, edgecolor="none", pad=4),
    )

    # Title strip.
    title_ax = fig.add_axes([0.0, map_h, 1.0, title_h])
    title_ax.set_facecolor(DARK_BG)
    title_ax.axis("off")
    title_ax.text(
        0.012, 0.5,
        f"{res.label} · True Color",
        ha="left", va="center",
        color=TEXT_COLOR, fontsize=14, fontweight="bold", transform=title_ax.transAxes,
    )
    title_ax.text(
        0.988, 0.5,
        f"{sensor_short} · {res.date} · ~{res.native_res_m} m",
        ha="right", va="center",
        color=ACCENT_COLOR, fontsize=10, transform=title_ax.transAxes,
    )

    fig.savefig(out_path, format="jpeg", dpi=120, facecolor=DARK_BG,
                pil_kwargs={"quality": 90})
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--regions", default="", help="comma-separated region slugs (default: all)")
    ap.add_argument("--outdir", default="gibs", help="output directory (default: gibs)")
    ap.add_argument("--max-px", type=int, default=DEFAULT_MAX_PX, help="cap on the long-axis pixels")
    ap.add_argument("--min-fill", type=float, default=DEFAULT_MIN_FILL,
                    help="min non-black fraction to accept a day")
    ap.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS,
                    help="how many days back (incl. today) to search for a filled composite")
    ap.add_argument("--date", default="", help="force a specific YYYY-MM-DD (skips the lookback search)")
    ap.add_argument("--layers", choices=["both", "viirs", "modis", "all"], default="all",
                    help="sensor variant to bake: 'all' (default) renders both+viirs+modis per "
                         "region; the specific choices render just one (debugging)")
    args = ap.parse_args()

    if args.regions.strip():
        slugs = [s.strip() for s in args.regions.split(",") if s.strip()]
        unknown = [s for s in slugs if s not in REGIONS]
        if unknown:
            log.error("unknown region(s): %s", ", ".join(unknown))
            return 2
    else:
        slugs = list(REGIONS.keys())

    if args.layers == "all":
        variants = [v[0] for v in LAYER_VARIANTS]
    else:
        variants = [args.layers]

    date_override = dt.date.fromisoformat(args.date) if args.date.strip() else None

    os.makedirs(args.outdir, exist_ok=True)
    session = _make_session()

    # One manifest entry per region. The region's top-level fields mirror the
    # DEFAULT_LAYER_VARIANT ("both") for back-compat with the current frontend
    # (which reads r.file / r.sensor / r.date / r.native_res_m); the per-variant
    # detail lives under r["variants"][<id>].
    region_entries: dict[str, dict] = {}
    failures: list[str] = []
    n_regions = 0
    for slug in slugs:
        log.info("region %s …", slug)
        # Shared across this region's variants so a (layer,date) crop fetched
        # for "both" is reused by "viirs"/"modis" instead of re-pulled. Order
        # the variants so the gap-fill superset ("both") fetches first.
        crop_cache: dict = {}
        ordered = [v for v in ("both", "viirs", "modis") if v in variants]
        region_variants: dict[str, dict] = {}
        for variant in ordered:
            file_name = f"{slug}_truecolor_{variant}.jpg"
            got = fetch_region(
                session, slug, REGIONS[slug],
                max_px=args.max_px, min_fill=args.min_fill,
                lookback_days=args.lookback_days, layers=select_layers(variant),
                date_override=date_override,
                file_name=file_name, crop_cache=crop_cache,
            )
            if got is None:
                log.warning("  %s/%s: no imagery for this variant", slug, variant)
                continue
            rgb, res = got
            out_path = os.path.join(args.outdir, file_name)
            render_jpeg(rgb, res, out_path)
            region_variants[variant] = {
                "file": res.file,
                "sensor": res.sensor,
                "contributors": res.contributors,
                "date": res.date,
                "native_res_m": res.native_res_m,
                "fill": res.fill,
            }
            log.info("  wrote %s", out_path)
            # Back-compat: also write {slug}_truecolor.jpg (== "both") so the
            # legacy r.file path keeps resolving for any cached frontend.
            if variant == DEFAULT_LAYER_VARIANT:
                legacy_res = RegionResult(**{**asdict(res), "file": f"{slug}_truecolor.jpg"})
                legacy_path = os.path.join(args.outdir, legacy_res.file)
                render_jpeg(rgb, legacy_res, legacy_path)
                log.info("  wrote %s", legacy_path)

        if not region_variants:
            failures.append(slug)
            continue

        # The region's top-level fields come from the default variant if it
        # rendered, else the best available one (so a region missing "both"
        # still shows something).
        base_id = DEFAULT_LAYER_VARIANT if DEFAULT_LAYER_VARIANT in region_variants \
            else next(iter(region_variants))
        base = region_variants[base_id]
        region_entries[slug] = {
            "slug": slug,
            "label": REGIONS[slug]["label"],
            # Legacy {slug}_truecolor.jpg only exists when "both" was baked;
            # otherwise point r.file at the available default variant's file.
            "file": f"{slug}_truecolor.jpg" if DEFAULT_LAYER_VARIANT in region_variants
                    else base["file"],
            "sensor": base["sensor"],
            "contributors": base["contributors"],
            "date": base["date"],
            "native_res_m": base["native_res_m"],
            "fill": base["fill"],
            "default_variant": base_id,
            "variants": region_variants,
        }
        n_regions += 1

    manifest = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kind": "gibs-truecolor",
        "source": "NASA GIBS CorrectedReflectance_TrueColor (daily composite)",
        "layers": [{"id": vid, "label": vlabel} for vid, vlabel in LAYER_VARIANTS],
        "default_layer": DEFAULT_LAYER_VARIANT,
        "file_template": "{slug}_truecolor_{layer}.jpg",
        "regions": [region_entries[s] for s in slugs if s in region_entries],
    }
    manifest_path = os.path.join(args.outdir, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    log.info("wrote %s (%d region(s))", manifest_path, n_regions)

    results = manifest["regions"]
    if failures:
        log.warning("FAILED regions (no imagery): %s", ", ".join(failures))
    # Succeed as long as we got at least one region; a single transient GIBS
    # gap shouldn't fail the whole workflow run.
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
