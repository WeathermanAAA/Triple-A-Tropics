"""ascatobs.fetch - KNMI Open Data API client for ASCAT coastal NRT NetCDF.

Verified live against the API (2026-06-26):
  base : https://api.dataplatform.knmi.nl/open-data/v1
  auth : header  ``Authorization: <raw key>``  (NO 'Bearer' prefix)
  list : GET /datasets/{ds}/versions/{ver}/files
           ?maxKeys&orderBy=created&sorting=desc   (created+desc = newest first)
         -> {files:[{filename,size,created,lastModified}], isTruncated, nextPageToken}
  url  : GET /datasets/{ds}/versions/{ver}/files/{filename}/url
         -> {temporaryDownloadUrl: <S3 presigned, valid 3600 s>}
There is NO list-datasets endpoint - the OSI SAF ASCAT coastal dataset names are
pinned in ``DATASETS`` (resolved from the KNMI catalog, not guessed). Every call
is guarded (returns None/[] on failure) and backs off on HTTP 429 (the API
exposes no rate-limit headers, so we back off blindly). The presigned download URL
is fetched with a plain GET and NO auth header. A ``.gz`` object is transparently
decompressed to plain NetCDF on disk.

NetCDF decode lives in ``ascatobs.decode``; this module only moves bytes.
"""
from __future__ import annotations

import datetime as _dt
import gzip
import os
import re
import shutil
import time

import requests

BASE = "https://api.dataplatform.knmi.nl/open-data/v1"
_UA = "triple-a-tropics-ascat/1.0 (+https://triple-a-tropics.com)"

# OSI SAF ASCAT 12.5 km COASTAL NRT winds. ASCAT-A (Metop-A) is retired - only B
# and C are active. dataset/version come from the KNMI Data Platform catalog
# (datasetName uses underscores; the URL slug's hyphens are NOT the API name).
DATASETS = {
    "metop-b": {"dataset": "osisaf_ascat_b_coa", "version": "nrt",
                "sat": "metopb", "label": "ASCAT-B"},
    "metop-c": {"dataset": "osisaf_ascat_c_coa", "version": "nrt",
                "sat": "metopc", "label": "ASCAT-C"},
}

# ascat_YYYYMMDD_HHMMSS_metopb_NNNNN_eps_o_coa_VVVV_ovw.l2.nc[.gz]
# (timestamp = UTC of the FIRST data in the file = the overpass-start watermark)
_FNAME_RE = re.compile(
    r"ascat_(\d{8})_(\d{6})_(metop[abc])_(\d+)_", re.I)


def api_key_from_env() -> str | None:
    """The KNMI Open Data API key from KNMI_API_KEY (never hardcode/commit it)."""
    k = os.environ.get("KNMI_API_KEY")
    return k.strip() if k and k.strip() else None


def parse_filename(name: str) -> dict | None:
    """Decompose an ASCAT NetCDF filename. Returns
    {start: datetime(UTC), sat: 'metopb'|'metopc', orbit: int, name: str} or None
    when the name does not match the ASCAT L2 convention."""
    m = _FNAME_RE.search(name or "")
    if not m:
        return None
    ymd, hms, sat, orbit = m.group(1), m.group(2), m.group(3).lower(), m.group(4)
    try:
        start = _dt.datetime.strptime(ymd + hms, "%Y%m%d%H%M%S").replace(
            tzinfo=_dt.timezone.utc)
    except ValueError:
        return None
    return {"start": start, "sat": sat, "orbit": int(orbit), "name": name}


def _request(method: str, url: str, *, api_key: str | None, params=None,
             timeout: float = 30.0, retries: int = 3,
             stream: bool = False) -> "requests.Response | None":
    """One guarded API request with 429 back-off. ``api_key`` -> Authorization
    header (raw, no scheme). 404/410 -> None immediately (genuinely absent)."""
    headers = {"User-Agent": _UA}
    if api_key:
        headers["Authorization"] = api_key
    last = None
    for attempt in range(retries + 1):
        try:
            r = requests.request(method, url, headers=headers, params=params,
                                  timeout=timeout, stream=stream)
        except Exception as e:                       # noqa: BLE001
            last = e
            r = None
        if r is not None:
            if r.status_code == 200:
                return r
            if r.status_code in (404, 410):
                return None
            if r.status_code != 429:
                last = f"HTTP {r.status_code}"
        # 429 or transient: blind exponential back-off (no Retry-After exposed)
        if attempt < retries:
            time.sleep(min(60.0, 2.0 * (2 ** attempt)))
    if last:
        print(f"ascat.fetch: give up {method} {url}: {last}")
    return None


def list_files(dataset: str, version: str, *, api_key: str | None,
               max_keys: int = 100, newest_first: bool = True,
               max_pages: int = 1, begin: str | None = None) -> list[dict]:
    """File records for a dataset version, newest-first by default (orderBy=created,
    sorting=desc). Pages up to ``max_pages`` via nextPageToken. Each record:
    {filename, size, created, lastModified}. Returns [] on any failure."""
    url = f"{BASE}/datasets/{dataset}/versions/{version}/files"
    params = {"maxKeys": int(max_keys), "orderBy": "created",
              "sorting": "desc" if newest_first else "asc"}
    if begin:
        params["begin"] = begin
    out: list[dict] = []
    token = None
    for _ in range(max(1, max_pages)):
        p = dict(params)
        if token:
            p["nextPageToken"] = token
        r = _request("GET", url, api_key=api_key, params=p)
        if r is None:
            break
        try:
            body = r.json()
        except ValueError:
            break
        files = body.get("files") or []
        out.extend(files)
        token = body.get("nextPageToken")
        if not body.get("isTruncated") or not token:
            break
    return out


def get_download_url(dataset: str, version: str, filename: str, *,
                     api_key: str | None) -> str | None:
    """The temporary (1 h) S3 presigned download URL for one file, or None."""
    url = f"{BASE}/datasets/{dataset}/versions/{version}/files/{filename}/url"
    r = _request("GET", url, api_key=api_key)
    if r is None:
        return None
    try:
        return (r.json() or {}).get("temporaryDownloadUrl")
    except ValueError:
        return None


def download(temp_url: str, dest_path: str, *, timeout: float = 120.0) -> bool:
    """Stream a presigned download to ``dest_path`` (no auth header - the URL is
    pre-signed). A gzip object (``.gz`` name or gzip magic) is decompressed so
    ``dest_path`` is always plain NetCDF. Returns True on success."""
    r = _request("GET", temp_url, api_key=None, timeout=timeout, stream=True)
    if r is None:
        return False
    tmp = dest_path + ".part"
    try:
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                if chunk:
                    f.write(chunk)
        # decompress if the payload is gzip (by name or by magic bytes)
        with open(tmp, "rb") as f:
            magic = f.read(2)
        if temp_url.split("?", 1)[0].endswith(".gz") or magic == b"\x1f\x8b":
            with gzip.open(tmp, "rb") as gz, open(dest_path, "wb") as out:
                shutil.copyfileobj(gz, out)
            os.remove(tmp)
        else:
            os.replace(tmp, dest_path)
        return True
    except Exception as e:                           # noqa: BLE001
        print(f"ascat.fetch: download failed: {type(e).__name__}: {e}")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def fetch_recent(sensor_key: str, *, api_key: str | None, max_keys: int = 60,
                 newest_first: bool = True) -> list[dict]:
    """Recent file records for one sensor key ('metop-b'/'metop-c'), each enriched
    with the parsed {start, sat, orbit} and the dataset/version needed to download
    it. Skips names that don't parse. Returns [] on failure / unknown sensor."""
    cfg = DATASETS.get(sensor_key)
    if not cfg:
        return []
    recs = list_files(cfg["dataset"], cfg["version"], api_key=api_key,
                      max_keys=max_keys, newest_first=newest_first)
    out = []
    for rec in recs:
        meta = parse_filename(rec.get("filename", ""))
        if not meta:
            continue
        out.append({**rec, **meta, "sensor_key": sensor_key,
                    "dataset": cfg["dataset"], "version": cfg["version"],
                    "label": cfg["label"]})
    return out
