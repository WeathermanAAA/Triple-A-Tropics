#!/usr/bin/env python3
"""
Content-hash cache-busting for the /models/ viewer JS.

The static site serves ``models/*.js`` unversioned, so after a deploy a browser
keeps the STALE cached file until a manual hard-refresh - the model viewers
(enscenters.js, regions.js, hafs.js, and future model JS) silently run old code.
This stamper rewrites the ``?v=<hash>`` query on every local ``/models/*.js``
<script src> in ``models/index.html`` to the file's current content hash, so the
URL changes exactly when the file changes and browsers always fetch the current
code. A deploy that doesn't touch a file keeps its hash (no needless re-fetch).

Run it whenever a model JS changes (and it is checked by
``tests/test_asset_versioning.py``, which fails if the HTML is out of sync):

    python scripts/stamp_model_assets.py

Idempotent: re-running with no JS change is a no-op. External scripts (the cdnjs
gif.js) and any non-``/models/`` src are left untouched.
"""
from __future__ import annotations

import hashlib
import pathlib
import re
import sys
from typing import Dict, Tuple

# <script ... src="/models/<file>.js[?query]" ...>  -> capture prefix/src/query/suffix.
# The query group requires a leading "?" (so `/models/x.json"` does NOT mis-match
# as `/models/x.js` + `on`) and accepts ANY existing query (not just ?v=...), so a
# src carrying a different/extra param is rewritten, never silently skipped.
SCRIPT_RE = re.compile(
    r'(<script\b[^>]*\bsrc=")(/models/[A-Za-z0-9_./-]+\.js)(\?[^"]*)?(")'
)


def file_hash(path: pathlib.Path, n: int = 10) -> str:
    """First ``n`` hex of the file's SHA-256 - stable, collision-safe for cache busting."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:n]


def stamp_html(html: str, repo_root: pathlib.Path) -> Tuple[str, bool, Dict[str, str]]:
    """Rewrite ``?v=`` on local ``/models/*.js`` script srcs to current content
    hashes. Returns (new_html, changed, {src: hash}). A src whose file is missing
    is left untouched (e.g. an external or not-yet-created asset)."""
    versions: Dict[str, str] = {}

    def repl(m: "re.Match") -> str:
        prefix, src, _oldv, suffix = m.group(1), m.group(2), m.group(3), m.group(4)
        fpath = repo_root / src.lstrip("/")
        if not fpath.is_file():
            return m.group(0)
        h = file_hash(fpath)
        versions[src] = h
        return f"{prefix}{src}?v={h}{suffix}"

    new_html = SCRIPT_RE.sub(repl, html)
    return new_html, (new_html != html), versions


def stamp(repo_root: pathlib.Path, write: bool = True) -> Tuple[bool, Dict[str, str]]:
    """Stamp ``models/index.html`` in place (when ``write``). Returns (changed, versions)."""
    html_path = repo_root / "models" / "index.html"
    html = html_path.read_text()
    new_html, changed, versions = stamp_html(html, repo_root)
    if changed and write:
        html_path.write_text(new_html)
    return changed, versions


def main(argv=None) -> int:
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    changed, versions = stamp(repo_root, write=True)
    for src, h in sorted(versions.items()):
        print(f"  {src}?v={h}")
    print("stamped (changed)" if changed else "already current (no change)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
