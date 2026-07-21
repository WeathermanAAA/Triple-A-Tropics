"""hy2obs.build - watermark-gated daily tick: newest granule per
(satellite, pass direction) -> fixed-region renders -> R2.

R2 layout: hy2/{region}_{sat}_{dir}.png + hy2/meta.json (per-slot date +
time span; the frontend hides slots older than its staleness gate). A feed
gap (upstream outage) skips the slot and keeps the prior render live.
"""
from __future__ import annotations

import datetime as dt
import json

from . import fetch, render

REGIONS = {
    # slug: (lon_w, lon_e, lat_s, lat_n, label)  [deg, -180..180]
    "atl": (-100.0, -15.0, 5.0, 45.0, "Atlantic"),
    "epac": (-140.0, -85.0, 3.0, 35.0, "East Pacific"),
    "wpac": (105.0, 180.0, 3.0, 40.0, "West Pacific"),
}


def build(store, *, log=print) -> dict:
    meta = store.get_json("hy2/meta.json") or {"slots": {}}
    published = 0
    for sat in fetch.SATS:
        for dirn in fetch.DIRS:
            slot = f"{sat}_{dirn}"
            got = fetch.newest_granule(sat, dirn)
            if not got:
                log(f"hy2: {slot}: no granule within window (feed gap) "
                    "- keeping prior render")
                continue
            date, key = got
            dstr = date.strftime("%Y-%m-%d")
            if meta["slots"].get(slot, {}).get("date") == dstr:
                log(f"hy2: {slot}: {dstr} already published - no-op")
                continue
            raw = fetch.fetch_key(key)
            if not raw:
                log(f"hy2: {slot}: fetch failed (retry next tick)")
                continue
            try:
                d = fetch.decode(raw)
            except Exception as e:           # noqa: BLE001 — skip slot
                log(f"hy2: {slot}: decode failed {type(e).__name__}: {e}")
                continue
            for reg, cfg in REGIONS.items():
                png = render.render_region(d, cfg, sat=sat, dirn=dirn,
                                           date=date)
                store.put(f"hy2/{reg}_{slot}.png", png, "image/png",
                          "public, max-age=3600")
            meta["slots"][slot] = {
                "date": dstr,
                "tmin": d["tmin"].strftime("%Y-%m-%dT%H:%MZ")
                if d["tmin"] else None,
                "tmax": d["tmax"].strftime("%Y-%m-%dT%H:%MZ")
                if d["tmax"] else None}
            published += 1
            log(f"hy2: {slot}: published {dstr} x{len(REGIONS)} regions")
    if published:
        meta["regions"] = {k: v[4] for k, v in REGIONS.items()}
        meta["generated_utc"] = dt.datetime.now(dt.timezone.utc) \
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        store.put("hy2/meta.json",
                  json.dumps(meta, separators=(",", ":")).encode(),
                  "application/json", "public, max-age=3600")
    return {"published_slots": published}
