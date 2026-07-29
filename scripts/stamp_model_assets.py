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


# ---------------------------------------------------------------------------
# Shared category palette (/tat_palette.js + /tat_palette.css)
# ---------------------------------------------------------------------------
# These two are generated from palette/tat_palettes/categories.py and consumed
# by pages ALL OVER the site, not just /models/. That makes stale caching worse
# here than anywhere else: the whole point of the palette is that ONE edit
# recolors every surface, and a browser holding last week's tat_palette.js
# would leave exactly the drift this consolidation removed. So every page that
# references them gets its ?v= restamped, repo-wide.
PALETTE_ASSETS = ("/tat_palette.js", "/tat_palette.css")
PALETTE_RE = re.compile(
    r'((?:src|href)=")(/tat_palette\.(?:js|css))(\?[^"]*)?(")'
)
# Directories with no hand-maintained HTML worth walking (build output, deps,
# and the generated per-basin pages, which bake their colors at render time).
_SKIP_DIRS = {".git", "node_modules", "__pycache__", "build", ".venv"}


def stamp_palette_html(html: str, repo_root: pathlib.Path) -> Tuple[str, bool, Dict[str, str]]:
    """Rewrite ``?v=`` on any /tat_palette.{js,css} reference in one document."""
    versions: Dict[str, str] = {}

    def repl(m: "re.Match") -> str:
        prefix, src, _oldv, suffix = m.group(1), m.group(2), m.group(3), m.group(4)
        fpath = repo_root / src.lstrip("/")
        if not fpath.is_file():
            return m.group(0)
        h = file_hash(fpath)
        versions[src] = h
        return f"{prefix}{src}?v={h}{suffix}"

    new_html = PALETTE_RE.sub(repl, html)
    return new_html, (new_html != html), versions


def iter_html(repo_root: pathlib.Path):
    """Every hand-maintained .html file in the repo."""
    for path in sorted(repo_root.rglob("*.html")):
        if any(part in _SKIP_DIRS for part in path.relative_to(repo_root).parts):
            continue
        yield path


def stamp_palette(repo_root: pathlib.Path, write: bool = True) -> Tuple[bool, Dict[str, str]]:
    """Restamp the palette assets across every page. Returns (changed, {page: hash})."""
    changed_any = False
    touched: Dict[str, str] = {}
    for path in iter_html(repo_root):
        html = path.read_text()
        if not any(a in html for a in PALETTE_ASSETS):
            continue
        new_html, changed, versions = stamp_palette_html(html, repo_root)
        if changed:
            changed_any = True
            if write:
                path.write_text(new_html)
        for src, h in versions.items():
            touched[f"{path.relative_to(repo_root)}{src}"] = h
    return changed_any, touched


def main(argv=None) -> int:
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    changed, versions = stamp(repo_root, write=True)
    for src, h in sorted(versions.items()):
        print(f"  {src}?v={h}")
    pal_changed, pal_versions = stamp_palette(repo_root, write=True)
    print(f"  palette: {len(pal_versions)} reference(s) across the site")
    changed = changed or pal_changed
    print("stamped (changed)" if changed else "already current (no change)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
