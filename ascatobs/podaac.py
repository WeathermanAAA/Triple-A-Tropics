"""ascatobs.podaac - NASA PO.DAAC (Earthdata) source for ASCAT-B/C coastal NRT.

Per-orbit ASCAT 12.5 km coastal Level-2 ocean winds - the SAME EUMETSAT/OSI SAF
product as the KNMI feed (identical NetCDF schema + ``ascat_*_ovw.l2.nc`` naming)
but distributed via PO.DAAC at ~2-4 h latency instead of KNMI's ~daily batch.
Granules are found with the PUBLIC CMR granule search (no auth) and downloaded from
the EDL-protected PO.DAAC archive with an Earthdata bearer token. ONLY the source +
auth differ - decode/mask/decimate (``ascatobs.decode``) is reused unchanged.

Auth (minimise operator effort): ``EARTHDATA_TOKEN`` if set; else mint a token from
``EARTHDATA_USERNAME`` + ``EARTHDATA_PASSWORD`` via the URS tokens API (so the
no-expiry user/pass path needs no manual 60-day token rotation). The CMR search is
public; only the granule download needs the token. Every call is guarded (returns
None/[]/False on failure) so a dead source leaves the prior R2 tree live - exactly
like ``ascatobs.fetch`` (KNMI), which stays as the fallback source.

CMR collection concept-ids (verified live against CMR 2026-06-27; ASCAT-A retired):
  ASCATB-L2-Coastal -> C2075141605-POCLOUD
  ASCATC-L2-Coastal -> C2075141684-POCLOUD
"""
from __future__ import annotations

import datetime as _dt
import gzip
import os
import shutil
import time

import requests

from .fetch import _UA, parse_filename     # reuse the shared UA + filename parser

CMR = "https://cmr.earthdata.nasa.gov/search/granules.umm_json"
URS = "https://urs.earthdata.nasa.gov/api/users"

# PO.DAAC CMR collections for the ASCAT coastal NRT products (B + C; A retired).
COLLECTIONS = {
    "metop-b": {"cid": "C2075141605-POCLOUD", "sat": "metopb", "label": "ASCAT-B"},
    "metop-c": {"cid": "C2075141684-POCLOUD", "sat": "metopc", "label": "ASCAT-C"},
}


def _iso(d: _dt.datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def creds_from_env(log=print) -> str | None:
    """The Earthdata bearer token used to download PO.DAAC granules, or None.

    Prefers ``EARTHDATA_TOKEN`` (paste a token from urs.earthdata.nasa.gov). If
    that's absent but ``EARTHDATA_USERNAME`` + ``EARTHDATA_PASSWORD`` are set, mint
    a token from the URS tokens API (re-used if one already exists) so the user/pass
    secret pair never needs manual token rotation. None when neither is configured -
    the build then falls back to KNMI (see ``ascatobs.build``)."""
    tok = os.environ.get("EARTHDATA_TOKEN")
    if tok and tok.strip():
        return tok.strip()
    user = (os.environ.get("EARTHDATA_USERNAME") or "").strip()
    pw = os.environ.get("EARTHDATA_PASSWORD") or ""
    if user and pw:
        t = _mint_token(user, pw, log=log)
        if t:
            return t
        log("ascat.podaac: EARTHDATA_USERNAME/PASSWORD set but token mint failed")
    return None


def _mint_token(user: str, pw: str, *, log=print) -> str | None:
    """Find-or-create an Earthdata User Acceptable Use bearer token via the URS API.
    Re-uses an existing non-revoked token (GET /tokens) before creating a new one
    (POST /token), so we don't accumulate tokens across runs. None on any failure."""
    hdr = {"User-Agent": _UA}
    try:
        r = requests.get(URS + "/tokens", auth=(user, pw), headers=hdr, timeout=30)
        if r.status_code == 200:
            toks = r.json()
            if isinstance(toks, list):
                for t in toks:
                    at = (t or {}).get("access_token")
                    if at:
                        return at
        r = requests.post(URS + "/token", auth=(user, pw), headers=hdr, timeout=30)
        if r.status_code in (200, 201):
            return (r.json() or {}).get("access_token")
        log(f"ascat.podaac: URS token API HTTP {r.status_code}")
    except Exception as e:                               # noqa: BLE001
        log(f"ascat.podaac: token mint error: {type(e).__name__}: {e}")
    return None


def _cmr_get(params: dict, *, timeout: float = 30.0, retries: int = 3):
    """One guarded CMR granule-search request (public, no auth). JSON body or None.
    Backs off on transient 429/5xx; a 4xx other than 429 gives up immediately."""
    for attempt in range(retries + 1):
        r = None
        try:
            r = requests.get(CMR, params=params, headers={"User-Agent": _UA},
                             timeout=timeout)
        except Exception:                                # noqa: BLE001
            r = None
        if r is not None:
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    return None
            if r.status_code < 500 and r.status_code != 429:
                return None
        if attempt < retries:
            time.sleep(min(30.0, 1.5 * (2 ** attempt)))
    return None


def _data_url(umm: dict) -> str | None:
    """The HTTPS granule download URL from a CMR umm record (the GET DATA link that
    ends in .nc), or None. Prefers the .nc link; falls back to any GET DATA URL."""
    rels = umm.get("RelatedUrls") or []
    for ru in rels:
        if ru.get("Type") == "GET DATA":
            u = ru.get("URL") or ""
            if u.endswith(".nc"):
                return u
    for ru in rels:
        if ru.get("Type") == "GET DATA" and ru.get("URL"):
            return ru["URL"]
    return None


def fetch_recent(sensor_key: str, *, token: str | None = None,
                 since: "_dt.datetime | None" = None, max_keys: int = 120,
                 max_pages: int = 1, log=print) -> list[dict]:
    """Recent granule records for one sensor ('metop-b'/'metop-c') via CMR, newest
    first, each enriched with the parsed {start, sat, orbit} + the EDL download URL.
    ``since`` bounds the CMR temporal search (the build also re-filters by window).
    Records are shaped to match the KNMI ``fetch.fetch_recent`` output the build
    consumes (start/sat/orbit/name) plus ``download_url``. [] on failure."""
    cfg = COLLECTIONS.get(sensor_key)
    if not cfg:
        return []
    params = {"collection_concept_id": cfg["cid"], "sort_key": "-start_date",
              "page_size": int(max_keys)}
    if since is not None:
        params["temporal"] = _iso(since) + ","
    out: list[dict] = []
    for page in range(1, max(1, max_pages) + 1):
        p = dict(params)
        p["page_num"] = page
        body = _cmr_get(p)
        if not body:
            break
        items = body.get("items") or []
        for it in items:
            umm = it.get("umm", {}) or {}
            gid = umm.get("GranuleUR") or (it.get("meta", {}) or {}).get("native-id")
            meta = parse_filename(gid or "")
            if not meta:
                continue
            url = _data_url(umm)
            if not url:
                continue
            out.append({**meta, "name": gid, "filename": gid,
                        "download_url": url, "sensor_key": sensor_key,
                        "label": cfg["label"], "source": "podaac"})
        if len(items) < params["page_size"]:
            break
    return out


def download(url: str, dest_path: str, *, token: str | None,
             timeout: float = 120.0) -> bool:
    """Stream an EDL-protected PO.DAAC granule to ``dest_path`` with the Earthdata
    bearer token. requests strips the Authorization header on the cross-host redirect
    to the signed S3 object (rebuild_auth) - exactly right: the token authorizes the
    protected endpoint, S3 then serves via its own signature. gzip-aware. False on
    any failure."""
    headers = {"User-Agent": _UA}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    tmp = dest_path + ".part"
    try:
        with requests.get(url, headers=headers, stream=True, timeout=timeout,
                          allow_redirects=True) as r:
            if r.status_code != 200:
                print(f"ascat.podaac: download HTTP {r.status_code} "
                      f"{url.split('?', 1)[0]}")
                return False
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    if chunk:
                        f.write(chunk)
        with open(tmp, "rb") as f:
            magic = f.read(2)
        if url.split("?", 1)[0].endswith(".gz") or magic == b"\x1f\x8b":
            with gzip.open(tmp, "rb") as gz, open(dest_path, "wb") as out:
                shutil.copyfileobj(gz, out)
            os.remove(tmp)
        else:
            os.replace(tmp, dest_path)
        return True
    except Exception as e:                               # noqa: BLE001
        print(f"ascat.podaac: download failed: {type(e).__name__}: {e}")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False
