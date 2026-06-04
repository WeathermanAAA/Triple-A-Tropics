"""Shared plumbing for the live-overlay parity tests.

Renders LIVE_BASIN_JS for a basin exactly as a published page would carry
it (token substitution + _apply_icon_tokens), writes it to a temp .cjs,
and runs tests/js_harness.cjs under node to get the JS-built fragments.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import generate_tracks_plot as gtp  # noqa: E402

HARNESS = Path(__file__).resolve().parent / "js_harness.cjs"

NODE = shutil.which("node")


def overlay_js_body(basin: str, year: int) -> str:
    """The overlay script body as the browser sees it: __LIVE_*__ tokens
    substituted by build_live_overlay_js, icon geometry substituted by
    _apply_icon_tokens (which normally runs on the whole page)."""
    script = gtp.build_live_overlay_js(basin, year)
    assert script.startswith("<script>\n") and script.endswith("\n</script>")
    body = script[len("<script>\n"):-len("\n</script>")]
    body = gtp._apply_icon_tokens(body)
    assert "__ICON_" not in body and "__LIVE_" not in body, "unreplaced tokens"
    return body


def run_harness(basin: str, payload: dict) -> dict:
    """Round-trip ``payload`` through the node harness for ``basin``.

    Returns the JS-built fragments. The caller should feed its Python
    renderers the SAME json.loads(json.dumps(payload)) round-trip so both
    sides see identical float values.
    """
    if NODE is None:
        raise RuntimeError("node not on PATH")
    with tempfile.TemporaryDirectory() as td:
        overlay_path = Path(td) / f"overlay_{basin}.cjs"
        overlay_path.write_text(overlay_js_body(basin, payload["year"]),
                                encoding="utf-8")
        input_path = Path(td) / "input.json"
        input_path.write_text(json.dumps(payload), encoding="utf-8")
        proc = subprocess.run(
            [NODE, str(HARNESS), str(overlay_path), str(input_path)],
            capture_output=True, text=True, timeout=60,
        )
    if proc.returncode != 0:
        raise RuntimeError(f"node harness failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


def python_fragments(basin: str, payload: dict) -> dict:
    """The Python-side renders of the same payload (the parity oracle)."""
    extent = gtp.BASINS[basin]["extent"]
    storms = payload["storms"]
    return {
        "tracks": gtp.render_tracks_svg(storms, extent),
        "active": gtp.render_active_icons(storms, extent),
        "cards": gtp.render_cards_html(storms),
        "panel_title": gtp.render_panel_title_html(storms, payload["year"]),
        "stats": gtp.render_stats_html(payload["header"], payload["vocab"]),
        "fmt1": [f"{v:.1f}" for v in payload.get("fmt1_values", [])],
        "fmt2": [f"{v:.2f}" for v in payload.get("fmt2_values", [])],
    }
