"""tcprimed.pps - LIVE tier: NASA PPS Near-Real-Time GPM constellation 1C.

The TC-PRIMED archive (tcprimed.fetch) lags ~months. For genuinely-current storms
this module pulls inter-calibrated Level-1C brightness temperatures straight from
the PPS NRT server (jsimpsonhttps.pps.eosdis.nasa.gov) — the whole GPM constellation
(GMI, SSMIS F16/F17/F18, AMSR2) in ONE unified HDF5 format, ~1-3 h after the
overpass. A single free PPS account; the registered email is BOTH the HTTP-basic
username and password (lowercase). Credential resolution order:
  env PPS_EMAIL  ->  ~/.pps_email  ->  (none -> live tier disabled).

The 1C swath/channel layout is the same physical layout TC-PRIMED is derived from,
so the 37/89 V/H assignments mirror tcprimed.PMW_CHANNELS; only the on-disk shape
differs (a 3-D ``/S<n>/Tc`` channel cube + ``/S<n>/Latitude`` / ``/S<n>/Longitude``,
vs TC-PRIMED's per-channel named datasets). read_1c() returns the SAME swath arrays
the renderer (tcprimed.render) already consumes, so live and archive overpasses
render identically and merge into one manifest.
"""
from __future__ import annotations

import math
import os
import re
import sys
from pathlib import Path
from typing import Optional

import numpy as np

# PPS NRT 1C channel map: sensor -> {band: (swath, v_index, h_index)} into the
# swath's ``Tc`` channel axis. Mirrors the TC-PRIMED swath assignment (GPM 1C is
# the common source) -- GMI keeps 37 & 89 in S1; AMSR2/SSMIS split them. The
# indices are the 1C fixed channel order; read_1c VERIFIES them against the file's
# own channel-frequency header when present.
PPS_1C_CHANNELS: dict[str, dict[str, tuple[str, int, int]]] = {
    # GMI S1 = [10V,10H,19V,19H,23V,37V,37H,89V,89H]
    "GMI":   {"37": ("S1", 5, 6), "89": ("S1", 7, 8)},
    # AMSR2 1C: S4 = [36.5V,36.5H]; S5 = [89.0V,89.0H] (A-scan)
    "AMSR2": {"37": ("S4", 0, 1), "89": ("S5", 0, 1)},
    # SSMIS 1C: S2 = [37V,37H]; S4 = [91.665V,91.665H]
    "SSMIS": {"37": ("S2", 0, 1), "89": ("S4", 0, 1)},
}

# Target frequency (GHz) per band, for the channel-header self-check.
_BAND_FREQ = {"37": (36.0, 38.0), "89": (85.0, 92.0)}

FILL_FLOOR_K = 50.0   # Tc <= this (or <= -9000 fill) -> NaN

# Product prefix on the NRT server -> our sensor key. (One product type per
# constellation member; SSMIS spans F16/F17/F18.)
PPS_PRODUCTS = {
    "1C.GPM.GMI":      ("GMI", "GPM"),
    "1C.GCOMW1.AMSR2": ("AMSR2", "GCOMW1"),
    "1C.F16.SSMIS":    ("SSMIS", "F16"),
    "1C.F17.SSMIS":    ("SSMIS", "F17"),
    "1C.F18.SSMIS":    ("SSMIS", "F18"),
}

PPS_NRT_HOST = "https://jsimpsonhttps.pps.eosdis.nasa.gov"

# NRT 1C imager granules live FLAT in per-SENSOR dirs (verified against the live
# server). SSMIS F16/F17/F18 share /1C/SSMIS/ (the product prefix in the filename
# disambiguates the platform). NRT files are `.RT-NC` (netCDF-4 = HDF5; h5py reads
# them). A sensor dir holds a large rolling window, so we list + filter by time.
PPS_1C_DIRS = {"GMI": "/1C/GMI/", "AMSR2": "/1C/AMSR2/", "SSMIS": "/1C/SSMIS/"}


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
def pps_credential() -> Optional[str]:
    """The registered PPS email (used as both HTTP-basic user AND password,
    lowercase). env PPS_EMAIL, else ~/.pps_email, else None (live tier off)."""
    email = (os.environ.get("PPS_EMAIL") or "").strip()
    if not email:
        p = Path(os.path.expanduser("~/.pps_email"))
        if p.exists():
            email = p.read_text(encoding="utf-8").strip()
    return email.lower() or None if email else None


# ---------------------------------------------------------------------------
# 1C filename parse
# ---------------------------------------------------------------------------
# NRT:      1C.GPM.GMI.XCAL2016-C.20260617-S055200-E055658.V08A.RT-NC
# research: 1C.GPM.GMI.XCAL2016-C.20260617-S055200-E055658.054321.V07A.HDF5
# (NRT omits the orbit field; suffix is .RT-NC = netCDF-4/HDF5.)
_NRT_RE = re.compile(
    r"^(?P<product>1C\.[A-Z0-9]+\.[A-Z0-9]+)\."
    r"[^.]+\."                                       # cal tag, e.g. XCAL2016-C
    r"(?P<date>\d{8})-S(?P<start>\d{6})-E(?P<end>\d{6})"
    r"(?:\.(?P<orbit>\d+))?"                          # orbit (research only)
    r"\.(?P<ver>V\d+\w*)\.(?:RT-)?(?:NC|H5|HDF5)$", re.IGNORECASE)


def parse_1c_filename(name: str) -> Optional[dict]:
    """Parse a PPS 1C (NRT or standard) filename. Returns sensor/platform, the
    granule start/end datetimes, orbit, product. None if not a known 1C imager
    product (we skip sounders ATMS/MHS - no 37/89 imager pair)."""
    import datetime as dt
    base = name.split("/")[-1]
    m = _NRT_RE.match(base)
    if not m:
        return None
    product = m.group("product")
    if product not in PPS_PRODUCTS:
        return None
    sensor, platform = PPS_PRODUCTS[product]
    d = m.group("date")
    start = dt.datetime.strptime(d + m.group("start"), "%Y%m%d%H%M%S") \
        .replace(tzinfo=dt.timezone.utc)
    end = dt.datetime.strptime(d + m.group("end"), "%Y%m%d%H%M%S") \
        .replace(tzinfo=dt.timezone.utc)
    if end < start:                       # granule crosses UTC midnight
        end += dt.timedelta(days=1)
    return {"product": product, "sensor": sensor, "platform": platform,
            "start": start, "end": end, "orbit": m.group("orbit"),
            "file": base}


# ---------------------------------------------------------------------------
# 1C HDF5 reader (h5py)
# ---------------------------------------------------------------------------
def _swath_channels(grp) -> list[str]:
    """Best-effort channel-frequency list for a swath group, parsed from the 1C
    ``S<n>.SwathHeader`` / file metadata when present (used only to self-check the
    hardcoded indices). Empty list if unavailable."""
    for attr in ("S1.SwathHeader", "SwathHeader"):
        v = grp.attrs.get(attr)
        if v is not None:
            return [v.decode() if isinstance(v, bytes) else str(v)]
    return []


def _read_band(h5, sensor: str, band: str):
    """Return (lat, lon, tb_v, tb_h) Kelvin arrays for one band of one sensor,
    masking fill -> NaN. Reads ``/S<n>/Tc[:,:,idx]`` + ``/S<n>/Latitude`` /
    ``Longitude``."""
    swath, vi, hi = PPS_1C_CHANNELS[sensor][band]
    g = h5[swath]
    lat = np.asarray(g["Latitude"][:], dtype=float)
    lon = np.asarray(g["Longitude"][:], dtype=float)     # -180..180 in 1C
    tc = np.asarray(g["Tc"][:], dtype=float)             # (nscan, npix, nchan)
    if tc.ndim != 3 or tc.shape[2] <= max(vi, hi):
        raise ValueError(f"{sensor} {swath}/Tc has unexpected shape {tc.shape}")
    v = tc[:, :, vi].copy()
    h = tc[:, :, hi].copy()
    for a in (v, h):
        a[a <= -9000.0] = np.nan
        a[a <= FILL_FLOOR_K] = np.nan
    return lat, lon, v, h


# ---------------------------------------------------------------------------
# PPS NRT HTTP (basic auth: email is BOTH user and password, lowercase)
# ---------------------------------------------------------------------------
def _auth_header(email: str) -> dict:
    import base64
    tok = base64.b64encode(f"{email}:{email}".encode()).decode()
    return {"Authorization": f"Basic {tok}",
            "User-Agent": "tat-tcprimed-live/1.0"}


# --- expired-cert contingency (observed 2026-07-12) -------------------------
# At 2026-07-10T23:59:59Z the PPS NRT server's TLS certificate EXPIRED
# (DigiCert/Thawte, CN=jsimpson.pps.eosdis.nasa.gov, SANs cover this host) and
# NASA had not renewed it. Every list/download then died with
# SSLCertVerificationError - swallowed per-dir - so the live tier reported
# "0 candidate granules" while looking healthy. Fallback: when (and ONLY when)
# the normal request fails certificate verification, retry with a context
# that KEEPS full CA-chain and hostname verification and exempts only the
# validity-time check (OpenSSL X509_V_FLAG_NO_CHECK_TIME), then additionally
# requires the peer cert's SHA-256 fingerprint to be exactly a pinned one.
# Nothing is disabled: the chain must still verify to the system trust store,
# the hostname must still match a SAN, and only the one certificate NASA is
# actually serving is accepted. The strict path is always tried first, so the
# moment NASA deploys a renewed cert this fallback goes dormant and the pin
# entries should be deleted.
_PINNED_CERT_SHA256 = {
    # jsimpson*.pps.eosdis.nasa.gov, notAfter=2026-07-10T23:59:59Z,
    # fingerprint taken from the live server 2026-07-12:
    "f24df0fa2b3ae581d59e61dba1070811b1a94fffe88fc96a3bb12429a618931b",
}
# OpenSSL x509_vfy.h X509_V_FLAG_NO_CHECK_TIME (stable since 1.0.2; not
# exposed as an ssl-module constant): skip ONLY notBefore/notAfter.
_X509_V_FLAG_NO_CHECK_TIME = 0x200000
_PIN_WARNED = False


def _pinned_get(url: str, headers: dict, timeout: float) -> bytes:
    """GET with full certificate verification EXCEPT the validity window
    (chain to system CAs + hostname check both enforced by the handshake),
    plus an exact-fingerprint pin on the peer cert. Same-host redirects
    followed (urllib did that transparently on the normal path); non-2xx
    raises HTTPError like urlopen does."""
    import hashlib
    import http.client
    import ssl
    import urllib.error
    import urllib.parse

    global _PIN_WARNED
    for _hop in range(4):
        u = urllib.parse.urlsplit(url)
        ctx = ssl.create_default_context()      # verify chain + hostname
        ctx.verify_flags |= _X509_V_FLAG_NO_CHECK_TIME   # ...but not expiry
        conn = http.client.HTTPSConnection(u.hostname, u.port or 443,
                                           context=ctx, timeout=timeout)
        try:
            conn.connect()
            der = conn.sock.getpeercert(binary_form=True) or b""
            fp = hashlib.sha256(der).hexdigest()
            if fp not in _PINNED_CERT_SHA256:
                raise ssl.SSLError(
                    f"pps: cert chain+hostname verified but the presented "
                    f"cert (sha256 {fp}) is not the pinned expired PPS cert "
                    f"- refusing (delete the fallback if NASA re-keyed)")
            if not _PIN_WARNED:
                _PIN_WARNED = True
                print("pps: server TLS cert expired 2026-07-10; proceeding "
                      "with chain+hostname-verified, time-exempt, PINNED "
                      f"connection (sha256 {fp[:16]}...) - remove the pin "
                      "once NASA renews", file=sys.stderr)
            path = (u.path or "/") + (f"?{u.query}" if u.query else "")
            conn.request("GET", path, headers=headers)
            r = conn.getresponse()
            if 300 <= r.status < 400 and r.getheader("Location"):
                nxt = urllib.parse.urljoin(url, r.getheader("Location"))
                if urllib.parse.urlsplit(nxt).hostname != u.hostname:
                    raise urllib.error.URLError(
                        f"pps: pinned fallback refuses cross-host redirect "
                        f"to {nxt}")
                url = nxt
                continue
            if r.status >= 400:
                raise urllib.error.HTTPError(url, r.status, r.reason,
                                             r.headers, None)
            return r.read()
        finally:
            conn.close()
    raise urllib.error.URLError("pps: too many redirects in pinned fallback")


def http_get(url: str, email: str, *, timeout: float = 60.0) -> bytes:
    """Authenticated GET -> raw bytes. Raises urllib HTTPError on auth/other
    failure so the caller can distinguish a bad credential from 'no granules'.
    On a certificate-verification failure ONLY, falls back to the pinned-cert
    path above (the 2026-07 expired-cert contingency)."""
    import ssl
    import urllib.error
    import urllib.request
    if not url.startswith("http"):
        url = PPS_NRT_HOST + url
    req = urllib.request.Request(url, headers=_auth_header(email))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return r.read()
    except urllib.error.URLError as e:
        if not isinstance(getattr(e, "reason", None),
                          ssl.SSLCertVerificationError):
            raise
        return _pinned_get(url, _auth_header(email), timeout)


_GRANULE_RE = re.compile(r'(1C\.[A-Za-z0-9.\-]+\.(?:RT-)?(?:NC|H5|HDF5))',
                         re.IGNORECASE)


def list_dir(url: str, email: str) -> list[str]:
    """List 1C granule filenames in a PPS directory (Apache autoindex HTML or the
    /text/ plain listing -- we just regex out 1C.*.{RT-NC,H5,HDF5} tokens)."""
    body = http_get(url, email).decode("utf-8", "ignore")
    return sorted({m.group(1).split("/")[-1]
                   for m in _GRANULE_RE.finditer(body)})


def download(url: str, email: str, dest_dir: str) -> str:
    """Download a granule to dest_dir; returns the local path."""
    name = url.split("/")[-1]
    path = os.path.join(dest_dir, name)
    data = http_get(url, email, timeout=180.0)
    with open(path, "wb") as f:
        f.write(data)
    return path


def recent_granule_urls(email: str, since, *,
                        products: Optional[set] = None) -> list[dict]:
    """Return [{url, ...parse_1c_filename fields}] for imager-1C granules whose END
    time is >= ``since``, by listing each per-sensor NRT dir (/1C/{GMI,AMSR2,SSMIS}/)
    and filtering by the time parsed from the filename. ``products`` optionally
    restricts to a subset of PPS_PRODUCTS keys."""
    seen, out = set(), []
    for _sensor, d in PPS_1C_DIRS.items():
        try:
            names = list_dir(d, email)
        except Exception as e:  # noqa: BLE001
            # LOUD: a dead credential or TLS failure on every dir is
            # indistinguishable from quiet skies without this line (the
            # 2026-07 expired-cert outage hid behind a silent continue here).
            print(f"pps: list {d} FAILED: {type(e).__name__}: {e}",
                  file=sys.stderr)
            continue
        for n in names:
            if n in seen:
                continue
            meta = parse_1c_filename(n)
            if not meta:
                continue
            if products and meta["product"] not in products:
                continue
            if meta["end"] < since:
                continue
            seen.add(n)
            out.append({**meta, "url": d + n})
    out.sort(key=lambda m: m["end"])
    return out


def crop_swath(lat, lon, v, h, clat: float, clon: float, pad: float = 8.0):
    """Crop a GLOBAL 1C swath band to the contiguous scan/pixel window covering a
    [clat+/-pad, clon+/-pad] box (lon compared in a centre-unwrapped frame, so the
    dateline is safe). Returns (lat,lon,v,h) sub-arrays, or None if the swath does
    not reach the box. Keeps 2-D structure so the renderer's griddata works."""
    la = np.asarray(lat, dtype=float)
    lo = np.asarray(lon, dtype=float)
    lou = lo.copy()
    d = lou - clon
    lou[d > 180] -= 360.0
    lou[d < -180] += 360.0
    box = (np.abs(la - clat) <= pad) & (np.abs(lou - clon) <= pad)
    if not box.any():
        return None
    rows = np.where(box.any(axis=1))[0]
    cols = np.where(box.any(axis=0))[0]
    r0, r1, c0, c1 = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
    sl = (slice(r0, r1), slice(c0, c1))
    return la[sl], lo[sl], np.asarray(v)[sl], np.asarray(h)[sl]


def overpass_time(lat, lon, clat: float, clon: float, start, end):
    """Interpolate the time the satellite was over the storm: find the swath scan
    row nearest the centre and place it fractionally between the granule start/end.
    For a full-orbit AMSR2/SSMIS granule the mid-time can be ~45 min off the actual
    overpass; this pins it to the storm's along-track position."""
    la = np.asarray(lat, dtype=float)
    lou = np.asarray(lon, dtype=float).copy()
    d = lou - clon
    lou[d > 180] -= 360.0
    lou[d < -180] += 360.0
    cosl = max(math.cos(math.radians(clat)), 0.2)
    dist2 = (la - clat) ** 2 + ((lou - clon) * cosl) ** 2
    dist2 = np.where(np.isfinite(dist2), dist2, np.inf)
    row = int(np.unravel_index(int(np.argmin(dist2)), dist2.shape)[0])
    n = la.shape[0]
    frac = row / max(n - 1, 1)
    return start + (end - start) * frac


def read_1c(path: str, sensor: str, platform: str):
    """Read a PPS 1C granule into the swath arrays tcprimed.render consumes.

    Returns a dict with sensor/platform and, per band, the swath lat/lon (degrees;
    1C longitude is already -180..180) + Tb V/H in Kelvin:
    lat37/lon37/tb37v/tb37h, lat89/lon89/tb89v/tb89h. The granule is GLOBAL -- the
    caller crops to a storm center. Raises if a required band is absent."""
    import h5py
    out = {"sensor": sensor, "platform": platform}
    with h5py.File(path, "r") as h5:
        if sensor not in PPS_1C_CHANNELS:
            raise KeyError(f"unsupported 1C sensor {sensor!r}")
        for band, key in (("37", "37"), ("89", "89")):
            lat, lon, v, h = _read_band(h5, sensor, band)
            out[f"lat{key}"] = lat
            out[f"lon{key}"] = lon
            out[f"tb{key}v"] = v
            out[f"tb{key}h"] = h
    return out
