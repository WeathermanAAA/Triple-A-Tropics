"""reconobs.fetch - guarded, no-auth HTTP for NHC recon text + archive dirs.

Pure stdlib (urllib) so it runs anywhere in CI with no extra deps. Every
network call is bounded (timeout + bounded retries) and returns None on
failure rather than raising, so a single dead endpoint degrades gracefully
and never crashes the ingest (the "guarded poller" contract: tolerate
empty/missing, keep last-known-good upstream).
"""
from __future__ import annotations

import re
import time
import urllib.request
import urllib.error

NHC = "https://www.nhc.noaa.gov"
IEM_AFOS = "https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py"
_UA = "triple-a-tropics-recon/1.0 (+https://triple-a-tropics.com)"
_PRE = re.compile(r"<pre>(.*?)</pre>", re.S | re.I)
_HREF = re.compile(r'href="([^"]+)"', re.I)


def get(url: str, *, timeout: float = 20.0, retries: int = 2) -> str | None:
    """GET text; bounded retries on transient errors; None on give-up."""
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return None                      # genuinely absent, do not retry
            last = e
        except Exception as e:                   # noqa: BLE001 - any network error
            last = e
        if attempt < retries:
            time.sleep(0.6 * (attempt + 1))
    return None


def get_pre(url: str, **kw) -> str | None:
    """GET an NHC .shtml product page and return the <pre> body (the raw
    bulletin text), unescaped. None if the page or its <pre> is missing."""
    html = get(url, **kw)
    if not html:
        return None
    m = _PRE.search(html)
    body = m.group(1) if m else html
    # minimal entity unescape (bulletins are plain ASCII inside <pre>)
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                 ("&#39;", "'")):
        body = body.replace(a, b)
    return body.strip("\n")


def list_dir_txt(url: str, **kw) -> list[str]:
    """List the *.txt filenames in an NHC Apache archive directory index.
    Returns a sorted list (filenames only). Empty list on any failure or an
    empty/off-season directory."""
    html = get(url, **kw)
    if not html:
        return []
    names = []
    for href in _HREF.findall(html):
        if href.endswith(".txt") and "/" not in href.strip("/"):
            names.append(href)
    return sorted(set(names))


def iem_afos(pil: str, *, limit: int = 1, **kw) -> str | None:
    """IEM AFOS retrieval fallback (NHC down / historical pulls). Returns the
    concatenated product text for ``pil`` (e.g. 'REPRPD'), most-recent first."""
    return get(f"{IEM_AFOS}?pil={pil}&limit={int(limit)}&fmt=text", **kw)
