#!/usr/bin/env python3
"""Persistent per-cycle FIELD CACHE between the HAFS ingest and render stages.

The full-cycle builder used to FUSE fetch + render per (product, fxx): the wind
job downloaded a frame's ``.atm`` GRIB, then the reflectivity job downloaded the
SAME ``.atm`` again, and Clean IR / Water Vapor each re-downloaded the shared
``.sat`` - the shared GRIB was re-fetched once per product that read it. This
module removes that waste:

  INGEST  - ``ingest_frame`` fetches + decodes each required GRIB for a frame
            ONCE (via hafs_plot._read_raw_fields: the .atm for MSLP/wind/refl, the
            .sat for the sim-sat channels) and writes ALL fields any product needs
            into ONE cache entry keyed by cycle/model/storm/domain/fxx.
  RENDER  - ``load_frame`` reads that entry and reconstructs the exact HafsFrame a
            given product needs (hafs_plot._pack_frame) with NO GRIB fetch.

CACHE FORMAT: per-fxx NetCDF (netCDF4 backend). Chosen over npz and Zarr:
  - vs npz: NetCDF is self-describing (named variables + coords + attrs), which
    matters for an R2-hosted cache a future on-demand renderer / debugger reads
    with xarray.open_dataset; npz would bolt metadata on as side arrays. npz was
    only ~5% smaller in measurement. netCDF4 is already a sanctioned repo dep
    (the SST/subsurface/armor3d generators use it).
  - vs Zarr: Zarr's win is chunked PARTIAL reads across a big array; here each
    entry is one small frame read whole, so Zarr buys nothing and adds a dep.
  Round-trip is bit-exact and dtype-preserving (measured), so rendering from the
  cache is byte-identical to a direct fetch. zlib complevel 4 compresses the
  NaN-padded grid to the same size as the trimmed frame (~6 MB nest, ~26 MB
  parent for all the fields), so we store the product-neutral PRE-TRIM grid and
  let each product's _pack_frame apply its own trim/guard.

DTYPE: pass-through fields are stored float32 (``hafs_plot.STORE_DTYPE``) -
  GRIB2 packs to scaled integers and cfgrib unpacks at float32, so the float64
  this cache used to hold was a widening that carried no information. Derived
  fields stay float64, and ``_pack_frame`` widens everything back before it
  reaches matplotlib. The full reasoning, including why that last step is not
  optional, is the dtype policy block in ``hafs_plot``.

CACHE VERSION: ``CACHE_VERSION`` is baked into the key/path, so invalidation is a
version bump (old paths are simply not looked at), not a delete - matching the
repo's path-based cache-busting convention (cf. the SST animator).

R2-PORTABILITY: ``cache_relpath`` returns a slash-separated relative key with no
local-FS assumptions; ``cache_path`` just joins it under a local root today. The
same relpath could be an R2 object key tomorrow without changing the contract -
this is exactly where the FUTURE on-demand render path would read the cache from
(fetch the .nc for a requested model/storm/domain/fxx, _pack_frame, render). That
on-demand path / cache-warming / pre-render hybrid is intentionally NOT built
here; this module is only the ingest/render split + the field cache.
"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from hafs_render import hafs_plot as hp

log = logging.getLogger("hafs-cache")

# Bump to invalidate every cache entry (e.g. if the stored field set or decode
# changes). Old version paths are never read, so this is a bump, not a delete.
# v2: added the PWAT field (precipitable water, mm) to the cached field set.
# v3: added the 12 pressure-level (upper-air) fields - gh/u/v at 850/700/500,
#     relative vorticity at 850/500, and the 700-300 mb layer-mean RH.
# v4: added the 700-300 mb layer-mean u/v (for the RH product's barbs) -> 14.
# v5: added the PARENT-domain environmental fields (env_field_names(): precip,
#     SST, tropopause T, surface CAPE, latent-heat flux, 0-3 km SRH, 200 mb PV +
#     wind, and the 200-850 / 500-850 mb shear vectors) - ingested only for
#     parent.atm frames when an env product is in the cycle.
# v6: PASS-THROUGH fields are stored float32 (hafs_plot.STORE_DTYPE); derived
#     fields stay float64. See the dtype policy in hafs_plot. A version bump
#     rather than an in-place change because a v5 entry is all-float64: mixing
#     the two would let a warm cache and a cold ingest of the same frame
#     disagree, which is exactly what the version path exists to prevent.
CACHE_VERSION = "v6"

# Sub-root under save_dir where the local field cache lives.
CACHE_DIRNAME = "fieldcache"

# Stored BT channels are named bt_<parm> so load can map a product's sat_parm
# back to its variable.
_BT_VAR = "bt_{parm}"
_BT_PREFIX = "bt_"


def cache_relpath(cycle: str, model: str, storm: str, domain_slug: str,
                  fxx: int, version: str = CACHE_VERSION) -> str:
    """R2-portable cache key for one frame: a slash-separated relative path with
    the cache version first (so a version bump sidesteps every old entry). The
    same string is a local sub-path today and could be an R2 object key tomorrow.
    ``domain_slug`` is the short form ("storm"/"parent")."""
    return f"{version}/{cycle}/{model}/{storm}/{domain_slug}/f{fxx:03d}.nc"


def cache_path(save_dir: str, cycle: str, model: str, storm: str,
               domain_slug: str, fxx: int,
               version: str = CACHE_VERSION) -> Path:
    """Local filesystem path for a frame's cache entry (save_dir / fieldcache /
    <relpath>). The relpath part is the R2-portable key."""
    return (Path(save_dir) / CACHE_DIRNAME
            / cache_relpath(cycle, model, storm, domain_slug, fxx, version))


def ingest_frame(model: str, storm: str, domain: str, cycle_dt: dt.datetime,
                 fxx: int, path: Path, save_dir: str, *,
                 want_refl: bool = False, want_pwat: bool = False,
                 want_upper: bool = False, want_env: bool = False,
                 sat_parms: Sequence[int] = (),
                 remove_grib: bool = True, overwrite: bool = False) -> Path:
    """INGEST one frame: fetch + decode the UNION of fields every selected product
    needs (ONE read per GRIB file) and write them to the cache entry at ``path``.
    ``save_dir`` is where Herbie stages its byte-range GRIB subsets (removed after
    decode); ``path`` is the cache .nc to write.

    ``want_refl`` adds composite reflectivity; ``want_pwat`` adds precipitable
    water (mm); ``want_upper`` adds the pressure-level fields; ``want_env`` adds
    the parent-domain environmental fields (parent.atm only); ``sat_parms`` adds
    each requested simulated-satellite BT channel.
    PRMSL + 10 m wind are always read (every
    product overlays MSLP isobars + the VMAX-derived pill). Idempotent: an
    existing entry is reused unless ``overwrite`` (so a re-run / backup cron skips
    work the version path already holds). Returns ``path``.
    """
    if path.exists() and not overwrite:
        return path

    raw = hp._read_raw_fields(
        model, storm, domain, cycle_dt, fxx, save_dir,
        remove_grib=remove_grib, want_refl=want_refl, want_pwat=want_pwat,
        want_upper=want_upper, want_env=want_env, sat_parms=sat_parms,
    )
    _write_cache(raw, path)
    return path


def _write_cache(raw: dict, path: Path) -> None:
    """Serialize a raw field dict (full pre-trim grid) to a per-fxx NetCDF, native
    dtypes + zlib. Metadata that _pack_frame needs (model/storm/domain/fxx, init
    + valid times) rides as dataset attributes. Fields arrive at the dtype the
    policy assigns them (float32 pass-through / float64 derived); lat/lon coords
    stay float64 - they drive the map extent, the tick placement and the
    published pixel<->lon/lat affine, and cost a few KB."""
    import xarray as xr

    data_vars = {
        "mslp_hpa": (("lat", "lon"), raw["mslp_hpa"]),
        "wind_kt": (("lat", "lon"), raw["wind_kt"]),
        "u_kt": (("lat", "lon"), raw["u_kt"]),
        "v_kt": (("lat", "lon"), raw["v_kt"]),
    }
    if raw.get("refl_dbz") is not None:
        data_vars["refl_dbz"] = (("lat", "lon"), raw["refl_dbz"])
    if raw.get("pwat") is not None:
        data_vars["pwat"] = (("lat", "lon"), raw["pwat"])
    for parm, arr in raw.get("bt", {}).items():
        data_vars[_BT_VAR.format(parm=int(parm))] = (("lat", "lon"), arr)
    # Pressure-level (upper-air) fields, stored under their clear names
    # (gh_850, ..., relvort_850, relvort_500, rh_layer_700_300). Empty unless the
    # frame was ingested with want_upper.
    for name, arr in raw.get("upper", {}).items():
        data_vars[name] = (("lat", "lon"), arr)
    # PARENT-domain environmental fields (env_field_names()), stored under their
    # clear names. Empty unless the frame was ingested with want_env (parent.atm).
    for name, arr in (raw.get("env") or {}).items():
        data_vars[name] = (("lat", "lon"), arr)

    ds = xr.Dataset(
        data_vars,
        coords={"lat": raw["lat"], "lon": raw["lon"]},
        attrs={
            "model": raw["model"], "storm": raw["storm"],
            "product": raw["product"], "fxx": int(raw["fxx"]),
            "init_time": raw["init_time"].isoformat(),
            "valid_time": raw["valid_time"].isoformat(),
            "cache_version": CACHE_VERSION,
        },
    )
    enc = {v: {"zlib": True, "complevel": 4} for v in ds.data_vars}
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp sibling then atomically rename, so a crash mid-write never
    # leaves a half-written .nc that a render worker would read as a real entry.
    tmp = path.with_suffix(".nc.tmp")
    ds.to_netcdf(tmp, encoding=enc)
    ds.close()
    tmp.replace(path)


def _read_cache(path: Path, *, want_refl: bool = True, want_pwat: bool = True,
                want_upper: bool = True, want_env: bool = True,
                need_parms: Optional[Sequence[int]] = None) -> dict:
    """Read a cache entry back into the raw field dict _pack_frame expects (the
    inverse of _write_cache); bt vars are collected into ``{parm: array}``.

    SELECTIVE by default-off-nothing: an entry holds the UNION of every product's
    fields (39 on a parent frame), but a render task draws ONE product and
    _pack_frame throws the rest away. Materialising all of them cost every render
    worker the whole entry - ~646 MB on a parent frame for a product that needs
    four fields - so the flags here skip the reads instead of the arrays. They
    mirror ``load_frame``'s, and default to loading everything so a caller that
    wants the full entry (tests, a debugger, a future on-demand renderer) still
    gets it by passing nothing. Values are untouched: this changes WHICH
    variables are read out of the NetCDF, never what they contain.
    """
    import xarray as xr

    keep_bt = None if need_parms is None else {int(p) for p in need_parms}

    def _wanted(name: str) -> bool:
        if name.startswith(_BT_PREFIX):
            return keep_bt is None or int(name[len(_BT_PREFIX):]) in keep_bt
        if name in ("refl_dbz",):
            return want_refl
        if name in ("pwat",):
            return want_pwat
        if name in hp.upper_field_names():
            return want_upper
        if name in hp.env_field_names():
            return want_env
        return True          # mslp/wind/u/v - every product overlays them

    with xr.open_dataset(path) as ds:
        ds = ds[[v for v in ds.data_vars if _wanted(v)]]
        ds.load()
        bt = {}
        for name in ds.data_vars:
            if name.startswith(_BT_PREFIX):
                bt[int(name[len(_BT_PREFIX):])] = ds[name].values
        # Reconstruct the upper-air dict from the known field names present.
        upper = {name: ds[name].values for name in hp.upper_field_names()
                 if name in ds.data_vars}
        # Reconstruct the env dict from the known env field names present.
        env = {name: ds[name].values for name in hp.env_field_names()
               if name in ds.data_vars}
        return {
            "model": ds.attrs["model"], "storm": ds.attrs["storm"],
            "product": ds.attrs["product"], "fxx": int(ds.attrs["fxx"]),
            "init_time": dt.datetime.fromisoformat(ds.attrs["init_time"]),
            "valid_time": dt.datetime.fromisoformat(ds.attrs["valid_time"]),
            "lon": ds["lon"].values, "lat": ds["lat"].values,
            "mslp_hpa": ds["mslp_hpa"].values, "wind_kt": ds["wind_kt"].values,
            "u_kt": ds["u_kt"].values, "v_kt": ds["v_kt"].values,
            "refl_dbz": (ds["refl_dbz"].values if "refl_dbz" in ds.data_vars
                         else None),
            "pwat": (ds["pwat"].values if "pwat" in ds.data_vars else None),
            "upper": (upper or None),
            "env": (env or None),
            "bt": bt,
        }


def load_frame(path: Path, *, want_refl: bool = False, want_pwat: bool = False,
               want_upper: bool = False, want_env: bool = False,
               sat_parm: Optional[int] = None,
               sat_pct: "tuple | None" = None) -> hp.HafsFrame:
    """RENDER STAGE: read a frame's field cache entry and reconstruct the exact
    HafsFrame the given product needs - NO GRIB fetch. ``want_refl`` /
    ``want_pwat`` / ``want_upper`` / ``sat_parm`` pick the optional fields (the
    same flags fetch_hafs_frame took), so the result is byte-identical to the
    pre-split fetch. Raises if a requested BT channel is absent (a degenerate/
    never-ingested channel), which the render orchestration treats as a
    per-product skip - never fatal to the rest of the frame.
    """
    # PCT products need BOTH V/H channels cached; single-channel products need one.
    need_parms = list(sat_pct) if sat_pct is not None else (
        [sat_parm] if sat_parm is not None else [])
    # Read ONLY what this product draws (see _read_cache): a render worker has no
    # use for the other 35 fields in a parent entry, and materialising them was
    # most of its footprint.
    raw = _read_cache(path, want_refl=want_refl, want_pwat=want_pwat,
                      want_upper=want_upper, want_env=want_env,
                      need_parms=need_parms)
    for p in need_parms:
        if int(p) not in raw["bt"]:
            raise KeyError(f"BT channel parm={p} not in cache {path.name}")
    if want_pwat and raw.get("pwat") is None:
        raise KeyError(f"PWAT field not in cache {path.name}")
    if want_upper and raw.get("upper") is None:
        raise KeyError(f"upper-air fields not in cache {path.name}")
    if want_env and raw.get("env") is None:
        raise KeyError(f"env fields not in cache {path.name}")
    return hp._pack_frame(raw, want_refl=want_refl, want_pwat=want_pwat,
                          want_upper=want_upper, want_env=want_env,
                          sat_parm=sat_parm, sat_pct=sat_pct)
