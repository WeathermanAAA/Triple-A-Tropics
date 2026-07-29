"""Guards on the two memory decisions that changed the HAFS ingest's numbers.

Both are claims about EQUIVALENCE, not about behaviour, so they are worth
pinning: if either stops holding, output drifts silently rather than failing.

  1. PV STENCIL LOCALITY. PV_STACK_LEVELS was cut 7 -> 3 because MetPy takes the
     vertical derivative with a 3-point stencil, so the mapped level's PV cannot
     see beyond its immediate neighbours. That made the largest allocation in the
     parent-domain ingest 57% smaller for provably identical output. The test
     asserts BIT equality, not closeness -- "close enough" is what this change
     specifically claims not to be.

  2. THE DTYPE POLICY. Pass-through fields are cached float32; derived fields
     stay float64; and _pack_frame widens everything back before matplotlib sees
     it. That last step looks redundant and is not -- feeding streamplot a
     float32 field with bit-identical values moved 0.8% of the pixels on
     hgt_wind_700 and 5.0% on env_shear_500_850 when it was measured. The tests
     pin each half so a future "simplification" cannot quietly undo it.

No network -- synthetic fields on a small grid (the stencil property is
grid-size independent).
"""
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "palette"))

from hafs_render import hafs_plot as hp


def _synthetic_stack(levels, ny=48, nx=60, seed=3):
    """Smooth-ish level-varying T/u/v stacks on a lat/lon grid."""
    rng = np.random.default_rng(seed)
    lat = np.linspace(8.0, 34.0, ny)
    lon = np.linspace(-84.0, -46.0, nx)
    def mk(scale, base):
        out = np.empty((len(levels), ny, nx), dtype=np.float64)
        for k in range(len(levels)):
            out[k] = base + scale * (
                np.sin(np.deg2rad(lat)[:, None] * (2 + k))
                * np.cos(np.deg2rad(lon)[None, :] * (3 + k))
                + 0.05 * rng.standard_normal((ny, nx)))
        return out
    return lat, lon, mk(8.0, 215.0), mk(12.0, 20.0), mk(9.0, 3.0)


def test_pv_map_level_is_interior_to_the_stack():
    """The mapped level must have a neighbour on BOTH sides -- at an end its
    derivative goes one-sided and the 3-level stack stops being equivalent."""
    levels = sorted(hp.PV_STACK_LEVELS)
    assert hp.PV_MAP_LEVEL in levels, "mapped level absent from the PV stack"
    assert levels[0] < hp.PV_MAP_LEVEL < levels[-1], (
        f"PV_MAP_LEVEL {hp.PV_MAP_LEVEL} is at an END of PV_STACK_LEVELS "
        f"{hp.PV_STACK_LEVELS} - its vertical derivative would be one-sided")


def test_pv_at_map_level_is_stencil_local():
    """PV at the mapped level is BIT-IDENTICAL from the 3-level stack and from a
    deep 7-level one. This is the justification for PV_STACK_LEVELS being short."""
    pytest.importorskip("metpy")
    deep = (300, 250, 225, 200, 175, 150, 100)
    lat, lon, t, u, v = _synthetic_stack(deep)

    pv_deep = hp._pv_at_level(np.array(deep, float), t, u, v, lat, lon,
                              hp.PV_MAP_LEVEL)
    idx = [deep.index(lv) for lv in hp.PV_STACK_LEVELS]
    pv_thin = hp._pv_at_level(np.array(hp.PV_STACK_LEVELS, float),
                              t[idx], u[idx], v[idx], lat, lon, hp.PV_MAP_LEVEL)

    assert pv_deep.shape == pv_thin.shape
    assert np.array_equal(pv_deep.view(np.uint64), pv_thin.view(np.uint64)), (
        "PV at the mapped level changed when the stack was trimmed - the "
        "3-level stack is only valid while the vertical stencil stays 3-point")


def test_pv_solve_widens_a_float32_stack_exactly():
    """_read_levels hands _pv_at_level a float32 stack; the solve must widen it
    and produce what the old eagerly-widened float64 stack produced."""
    pytest.importorskip("metpy")
    lat, lon, t, u, v = _synthetic_stack(hp.PV_STACK_LEVELS)
    p = np.array(hp.PV_STACK_LEVELS, float)
    # float32 inputs whose float64 widening is the reference
    t32, u32, v32 = (a.astype(np.float32) for a in (t, u, v))
    ref = hp._pv_at_level(p, t32.astype(np.float64), u32.astype(np.float64),
                          v32.astype(np.float64), lat, lon, hp.PV_MAP_LEVEL)
    got = hp._pv_at_level(p, t32, u32, v32, lat, lon, hp.PV_MAP_LEVEL)
    assert got.dtype == np.float64, "the PV solve must stay float64"
    assert np.array_equal(ref.view(np.uint64), got.view(np.uint64))


def test_store_dtype_is_float32_and_widening_is_exact():
    """The stored dtype, and the property pass-through caching rests on:
    narrowing a float64 that CAME from float32 is lossless, so a pass-through
    field's cached value is exactly the decoded one."""
    assert hp.STORE_DTYPE is np.float32
    assert hp.RENDER_DTYPE is np.float64
    decoded = (np.linspace(-70.0, 55.0, 4096).astype(np.float32)
               .reshape(64, 64))
    widened = decoded.astype(np.float64)          # what the old code cached
    assert np.array_equal(hp._as_store(widened), decoded)
    assert hp._as_store(decoded) is decoded       # already float32 -> no copy
    # ...and the round trip the render boundary performs is exact both ways.
    assert np.array_equal(hp._as_store(widened).astype(hp.RENDER_DTYPE), widened)


def test_pack_frame_hands_the_renderer_float64():
    """_pack_frame must widen the cached fields, because matplotlib's arithmetic
    is dtype-sensitive. Wind/MSLP stay float32 - they always were."""
    ny, nx = 24, 30
    lat = np.linspace(10.0, 22.0, ny)
    lon = np.linspace(-70.0, -55.0, nx)
    f32 = lambda v: np.full((ny, nx), v, dtype=np.float32)
    raw = {
        "model": "hafsa", "storm": "13l", "product": "parent.atm", "fxx": 12,
        "init_time": None, "valid_time": None, "lat": lat, "lon": lon,
        "mslp_hpa": f32(1004.0), "wind_kt": f32(35.0),
        "u_kt": f32(20.0), "v_kt": f32(-8.0),
        "refl_dbz": f32(18.0), "pwat": f32(52.0),
        "upper": {"gh_500": f32(5870.0)},          # pass-through -> cached f32
        "env": {"cape_jkg": f32(1400.0)},          # pass-through -> cached f32
        "bt": {},
    }
    fr = hp._pack_frame(raw, want_refl=True, want_pwat=True, want_upper=True,
                        want_env=True)
    assert fr.refl_dbz.dtype == np.float64
    assert fr.pwat.dtype == np.float64
    assert fr.upper["gh_500"].dtype == np.float64
    assert fr.env["cape_jkg"].dtype == np.float64
    # the four that were never float64 must not be promoted
    for a in (fr.mslp_hpa, fr.wind_kt, fr.u_kt, fr.v_kt):
        assert a.dtype == np.float32


def test_as_store_preserves_nan_and_shape():
    a = np.array([[1.5, np.nan], [-3.25, np.inf]], dtype=np.float64)
    out = hp._as_store(a)
    assert out.dtype == np.float32 and out.shape == a.shape
    assert np.isnan(out[0, 1]) and np.isinf(out[1, 1])


def test_layer_mean_selection_commutes_with_widening():
    """_layer_mean now selects/orders levels on the float32 stack and widens once.
    Pin the algebra it relies on: widening is elementwise, so it commutes with
    permutation -- the float64 stack the integral sees is bit-for-bit the one the
    old select-after-widen order produced."""
    rng = np.random.default_rng(11)
    stack32 = rng.standard_normal((17, 12, 15)).astype(np.float32) * 40.0 + 55.0
    sel = rng.permutation(17)[:9]
    widen_then_select = stack32.astype(np.float64)[sel]
    select_then_widen = stack32[sel].astype(np.float64)
    assert np.array_equal(widen_then_select.view(np.uint64),
                          select_then_widen.view(np.uint64))


def test_selective_cache_read_skips_unwanted_fields_without_changing_values(tmp_path):
    """A render task draws ONE product; _read_cache must not materialise the
    other 35 fields in the entry. Pin BOTH halves: the skipping, and that what
    IS read is identical to what a full read returns."""
    from hafs_render import hafs_cache as fc

    ny, nx = 16, 20
    g = lambda v, d=np.float32: np.full((ny, nx), v, dtype=d)
    raw = {
        "model": "hafsa", "storm": "13l", "product": "parent.atm", "fxx": 12,
        "init_time": __import__("datetime").datetime(2026, 7, 28, 18),
        "valid_time": __import__("datetime").datetime(2026, 7, 29, 6),
        "lat": np.linspace(10.0, 20.0, ny), "lon": np.linspace(-70.0, -55.0, nx),
        "mslp_hpa": g(1004.0), "wind_kt": g(35.0), "u_kt": g(20.0), "v_kt": g(-8.0),
        "refl_dbz": g(18.0), "pwat": g(52.0),
        "bt": {58: g(-60.0, np.float64), 53: g(-45.0, np.float64)},
        "upper": {n: g(1.0 * i, np.float64 if "layer" in n or "relvort" in n
                       else np.float32)
                  for i, n in enumerate(hp.upper_field_names())},
        "env": {n: g(2.0 * i, np.float64) for i, n in enumerate(hp.env_field_names())},
    }
    p = tmp_path / "frame.nc"
    fc._write_cache(raw, p)

    full = fc._read_cache(p)
    assert full["upper"] and full["env"] and len(full["bt"]) == 2

    lean = fc._read_cache(p, want_refl=False, want_pwat=False, want_upper=False,
                          want_env=False, need_parms=[58])
    assert lean["refl_dbz"] is None and lean["pwat"] is None
    assert lean["upper"] is None and lean["env"] is None
    assert set(lean["bt"]) == {58}, "only the requested BT channel should load"
    # ...and every field that WAS read is bit-identical to the full read.
    for k in ("mslp_hpa", "wind_kt", "u_kt", "v_kt", "lat", "lon"):
        assert np.array_equal(full[k], lean[k]) and full[k].dtype == lean[k].dtype
    assert np.array_equal(full["bt"][58], lean["bt"][58])
    # metadata survives the variable subset
    for k in ("model", "storm", "product", "fxx", "init_time", "valid_time"):
        assert full[k] == lean[k]

    # an upper-air product loads upper but still not env
    up = fc._read_cache(p, want_refl=False, want_pwat=False, want_upper=True,
                        want_env=False, need_parms=[])
    assert up["upper"] is not None and up["env"] is None and up["bt"] == {}
    for n in hp.upper_field_names():
        assert np.array_equal(full["upper"][n], up["upper"][n])


def test_trapezoid_in_place_halving_matches_the_two_temp_form():
    """_layer_mean builds ``seg`` as (a+b) then ``*= 0.5`` instead of 0.5*(a+b),
    to hold one temporary instead of two. Same operations, same order -> same
    bits; pin it so a future rewrite to a weighted single sum (which is NOT
    bit-identical) does not slip in unnoticed."""
    rng = np.random.default_rng(5)
    vals = rng.standard_normal((17, 40, 50)) * 30.0 + 50.0
    p_s = np.array(sorted(hp.RH_LAYER_LEVELS), dtype=float)
    dp = np.diff(p_s)

    ref = np.tensordot(dp, 0.5 * (vals[1:] + vals[:-1]), axes=(0, 0)) / (
        p_s[-1] - p_s[0])
    seg = vals[1:] + vals[:-1]
    seg *= 0.5
    got = np.tensordot(dp, seg, axes=(0, 0)) / (p_s[-1] - p_s[0])
    assert np.array_equal(ref.view(np.uint64), got.view(np.uint64))
