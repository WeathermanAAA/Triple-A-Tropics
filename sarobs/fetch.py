"""sarobs.fetch - guarded anonymous HTTP for the SAR listing + data files.

Pure stdlib. Every call is bounded (timeout + bounded retries) and returns
None on give-up rather than raising — a dead upstream degrades a tick to a
no-op, never crashes the poller. Gzip requested for the (large) listing pages.
"""
from __future__ import annotations

import gzip
import time
import urllib.error
import urllib.request

BASE = "https://www.star.nesdis.noaa.gov/socd/mecb/sar/"
LISTING = BASE + "sarwinds_tropical.php"
_UA = "triple-a-tropics-sar/1.0 (+https://triple-a-tropics.com)"


def _open(url: str, timeout: float):
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA, "Accept-Encoding": "gzip"})
    return urllib.request.urlopen(req, timeout=timeout)


def get_text(url: str, *, timeout: float = 30.0, retries: int = 2) -> str | None:
    """GET text (gzip-aware); None on give-up; no retry on 404/410."""
    for attempt in range(retries + 1):
        try:
            with _open(url, timeout) as r:
                raw = r.read()
                if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return None
        except Exception:                        # noqa: BLE001 - any network error
            pass
        if attempt < retries:
            time.sleep(1.0 * (attempt + 1))
    return None


def get_bytes(url: str, *, timeout: float = 120.0, retries: int = 1) -> bytes | None:
    """GET a binary file (the ~10 MB Level-2 NetCDFs). None on give-up."""
    for attempt in range(retries + 1):
        try:
            with _open(url, timeout) as r:
                raw = r.read()
                if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                return raw
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return None
        except Exception:                        # noqa: BLE001
            pass
        if attempt < retries:
            time.sleep(2.0)
    return None
