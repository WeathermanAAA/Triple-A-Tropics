#!/usr/bin/env python3
"""Fixed-palette PNG-8 encoding for rendered frames - WHERE IT CAN BE HONEST.

The premise of this encoder is bit-exactness: a frame may be re-encoded as
PNG-8 only if every pixel whose colour is a PRODUCT COLORTABLE colour survives
byte-identical. That is not a sampling claim - :func:`transcode` decodes its
own output and compares EVERY pixel against the original before the PNG-8 is
allowed to exist, and any violation falls back to the original bytes.

WHAT THE REAL FRAMES SAY (measured on production CDN frames, 2026-07-31, one
frame per product family, 1963x1813 px):

    product              colortable   ct colours     distinct
                             size      in frame    frame colours
    refl (discrete .pal)      14           14          6,875
    env_precip               257          257         10,427
    vort_wind_850             459          458          5,309
    mslp_pwat                 494          494         15,216
    mslp_wind / hgt_wind      513          513         17-18k
    rh_layer                  454          454         30,953
    clean_ir                  871          871          2,489
    sim_89h                   863          862          3,294

Every LUT entry of a continuous ramp APPEARS in a real frame - a smooth field
sweeps its whole colormap. A PNG palette holds 256 entries, so for every
pcolormesh product the bit-exactness requirement is UNSATISFIABLE, not merely
hard: 257-871 required colours cannot fit in 256 slots. Quantizing those
products would nearest-map genuine fill colours, which is precisely the
"probably identical" outcome this build was told to refuse.

So the encoder is structurally scoped: :func:`eligible` admits a product only
when its FULL colortable fits comfortably inside the palette with room left
for overlay/AA colours (``MAX_CT`` reserved entries). Today that is the
discrete reflectivity table (14 colours); any future contourf-discrete product
inherits the win automatically, and any future palette growth is caught by the
same gate rather than silently degrading fills.

ANTI-ALIASED text, contour lines and overlay halos are the non-fill pixels.
They cannot all be preserved (a frame carries thousands of distinct AA
blends), so the palette reserves the colortable first, then fills its
remaining slots with the frame's most frequent non-colortable colours (which
is where the pure text/coast/background colours land - they are frequent), and
nearest-maps the rest. The per-frame stats report how many pixels moved and by
how much.

The transcode runs AFTER the normal PNG-24 encode (savefig is untouched), so
rendering is byte-identical to before and the swap is encoding-only. On any
failure - oversized colortable, verification miss, PIL surprise - the original
PNG-24 bytes are written and the frame is served exactly as it always was.
"""
from __future__ import annotations

import io
import logging
from typing import Optional

import numpy as np

log = logging.getLogger("png8")

#: A product's colortable may claim at most this many palette slots; the rest
#: are kept for overlay/text/AA colours. Raising this squeezes AA quality;
#: the gate exists so a growing colortable degrades to PNG-24, never to
#: nearest-mapped fills.
MAX_CT = 200

#: Palette size of PNG-8.
PALETTE = 256


class Unrepresentable(Exception):
    """The product's colortable cannot fit in a PNG-8 palette."""


# ---------------------------------------------------------------------------
# Colortable derivation (frame-independent; norms are frame-independent too)
# ---------------------------------------------------------------------------
class _StubUpper(dict):
    def __getitem__(self, k):
        return np.array([[1.0, 2.0], [3.0, 4.0]])


class _StubFrame:
    """The minimal frame make_colors needs; only the cmap/norm are read."""
    wind_kt = np.array([[10., 20.], [30., 40.]])
    refl_dbz = wind_kt
    pwat = wind_kt
    bt_c = np.array([[-50., -60.], [-70., -80.]])
    upper = _StubUpper()
    env = _StubUpper()


_CT_CACHE: dict = {}


def product_colortable(product: str) -> np.ndarray:
    """The product's complete fill colortable as unique uint8 RGB rows.

    Samples the cmap LUT end to end plus the under/over/bad extremes, so a
    colour the fill could ever produce is either here or is an AA blend.
    Memoized: the table is a per-product constant and this runs per frame.
    """
    if product in _CT_CACHE:
        return _CT_CACHE[product]
    from hafs_render import hafs_registry as reg
    s = reg.get_spec(product)
    c = s.make_colors(s, _StubFrame(), s.resolve_enhancement(None))
    cm = c.cmap
    xs = np.linspace(0.0, 1.0, int(getattr(cm, "N", 256)))
    rgba = [np.asarray(cm(xs)).reshape(-1, 4)]
    for extreme in (cm.get_over(), cm.get_under(), cm.get_bad()):
        rgba.append(np.asarray(extreme, dtype=float).reshape(1, -1))
    allc = np.vstack(rgba)[:, :3]
    rgb = (allc * 255.0 + 0.5).astype(np.uint8)
    _CT_CACHE[product] = np.unique(rgb, axis=0)
    return _CT_CACHE[product]


def eligible(product: str) -> bool:
    """Whether this product's colortable can be held bit-exact in a palette."""
    try:
        return len(product_colortable(product)) <= MAX_CT
    except Exception:  # noqa: BLE001 - an unknown product is simply not eligible
        return False


# ---------------------------------------------------------------------------
# Transcode
# ---------------------------------------------------------------------------
def transcode(png24: bytes, colortable: np.ndarray) -> tuple:
    """PNG-24 bytes -> ``(png8_bytes, stats)``, verified, or raises.

    Raises :class:`Unrepresentable` when the colortable colours present in the
    frame plus one slot cannot fit, and ``AssertionError`` if the decoded
    PNG-8 fails the bit-exactness check (callers treat both as "keep PNG-24").
    """
    from PIL import Image

    src = Image.open(io.BytesIO(png24))
    rgb = np.asarray(src.convert("RGB"))
    h, w, _ = rgb.shape
    flat = rgb.reshape(-1, 3)

    # All colour bookkeeping runs on RGB packed into one int32 - np.unique on a
    # 1-D int array is several times faster than axis=0 on Nx3 rows, and set
    # membership becomes np.isin. (The first cut used tuple dicts and took
    # ~6.7 s per frame; this path is ~15x faster on the same frame.)
    def pack(a):
        a = a.astype(np.uint32)
        return (a[..., 0] << 16) | (a[..., 1] << 8) | a[..., 2]

    def unpack(p):
        return np.stack([(p >> 16) & 255, (p >> 8) & 255, p & 255],
                        axis=-1).astype(np.uint8)

    flat_p = pack(flat)
    colors_p, inverse, counts = np.unique(flat_p, return_inverse=True,
                                          return_counts=True)
    is_ct = np.isin(colors_p, pack(colortable))
    ct_present = int(is_ct.sum())
    if ct_present > MAX_CT:
        raise Unrepresentable(
            f"{ct_present} colortable colours present > {MAX_CT} reserved slots")

    if len(colors_p) <= PALETTE:
        # Everything fits: fully lossless, bit-exact on EVERY pixel.
        palette_p = colors_p
        mapping = np.arange(len(colors_p))
        lossless = True
    else:
        # Reserve the colortable colours, then the most frequent of the rest;
        # everything else nearest-maps (vectorised, ~6k x 256 distances).
        order = np.argsort(-counts)
        keep = list(np.where(is_ct)[0])
        room = PALETTE - len(keep)
        keep.extend(int(i) for i in order[~is_ct[order]][:room])
        keep = np.array(keep[:PALETTE])
        palette_p = colors_p[keep]
        mapping = np.empty(len(colors_p), dtype=np.int32)
        in_pal = np.isin(colors_p, palette_p)
        sorter = np.argsort(palette_p)
        mapping[in_pal] = sorter[np.searchsorted(palette_p[sorter],
                                                 colors_p[in_pal])]
        others = np.where(~in_pal)[0]
        if others.size:
            oth = unpack(colors_p[others]).astype(np.int32)
            pal = unpack(palette_p).astype(np.int32)
            d2 = ((oth[:, None, :] - pal[None, :, :]) ** 2).sum(axis=2)
            mapping[others] = d2.argmin(axis=1)
        lossless = False
    palette = unpack(palette_p)

    idx_img = mapping[inverse].reshape(h, w).astype(np.uint8)
    out = Image.fromarray(idx_img, mode="P")
    pal = np.zeros((PALETTE, 3), dtype=np.uint8)
    pal[:len(palette)] = palette
    out.putpalette(pal.flatten().tolist())
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    png8 = buf.getvalue()

    # ---- THE VERIFICATION - every pixel, every frame, not a sample --------
    back = np.asarray(Image.open(io.BytesIO(png8)).convert("RGB"))
    same = (back == rgb).all(axis=2)
    ct_pixels = is_ct[inverse].reshape(h, w)
    violations = int((ct_pixels & ~same).sum())
    if violations:
        raise AssertionError(
            f"{violations} colortable pixel(s) not bit-exact after PNG-8 - "
            f"refusing the encode")
    changed = ~same
    deltas = (np.abs(back.astype(np.int16) - rgb.astype(np.int16))
              .max(axis=2)[changed])
    stats = {
        "lossless": lossless,
        "distinct_colors": int(len(colors_p)),
        "ct_colors_present": ct_present,
        "ct_pixel_pct": round(float(counts[is_ct].sum()) / flat.shape[0] * 100, 2),
        "png24_bytes": len(png24),
        "png8_bytes": len(png8),
        "ratio_pct": round(len(png8) / len(png24) * 100, 1),
        "aa_pixels_changed": int(changed.sum()),
        "aa_pixels_changed_pct": round(float(changed.sum()) / (h * w) * 100, 2),
        "aa_max_channel_delta": int(deltas.max()) if deltas.size else 0,
        "aa_mean_channel_delta": round(float(deltas.mean()), 2) if deltas.size else 0.0,
    }
    return png8, stats


def save_fig(fig, out_path: str, *, product: str, dpi: int,
             facecolor) -> Optional[dict]:
    """Drop-in for ``fig.savefig`` that emits verified PNG-8 where honest.

    Rendering is untouched: savefig produces the same PNG-24 raster as always,
    and the transcode is attempted only for products whose colortable fits
    (:func:`eligible` - the discrete tables). Any failure of any kind writes
    the original PNG-24 bytes, so the worst case is exactly the status quo.

    Returns the transcode stats dict when PNG-8 was written, else None.
    """
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, facecolor=facecolor)
    png24 = buf.getvalue()
    stats = None
    data = png24
    if eligible(product):
        try:
            data, stats = transcode(png24, product_colortable(product))
        except (Unrepresentable, AssertionError, Exception) as e:  # noqa: BLE001
            log.warning("png8: %s fell back to PNG-24 (%s: %s)",
                        product, type(e).__name__, e)
            data, stats = png24, None
    with open(out_path, "wb") as f:
        f.write(data)
    return stats
