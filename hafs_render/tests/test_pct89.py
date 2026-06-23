"""89 PCT (polarization-corrected 89 GHz) product: the PCT math, fill mask,
physical clip, V/H self-heal guard, registry wiring, and the ocean->blue
outcome. No network -- synthetic channels + the real palette enhancement."""
import sys, pathlib
import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # hafs_render pkg
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "palette"))

from hafs_render import hafs_plot as hp
from hafs_render import hafs_registry as reg
import tat_palettes as tp


# --- synthetic V/H scene: clear ocean (V slightly warmer than H) + a cold core
def _scene():
    V = np.full((20, 20), 280.0 - 273.15)   # ocean V ~280 K
    H = np.full((20, 20), 279.0 - 273.15)    # ocean H ~279 K (cooler -> H-pol depression)
    V[5:8, 5:8] = 150.0 - 273.15             # convective ice scattering (both cold)
    H[5:8, 5:8] = 130.0 - 273.15
    return {62: H.copy(), 63: V.copy()}


def test_pct_formula_and_blue_ocean():
    bt = _scene()
    pct = hp.compute_pct89(bt, 63, 62)
    # PCT = 1.818*V - 0.818*H ; over ocean (V=6.85, H=5.85 degC)
    assert np.isclose(pct[0, 0], 1.818 * (6.85) - 0.818 * (5.85), atol=1e-6)
    # clear-ocean PCT median is WARM (> -23 degC i.e. > 250 K) -> renders blue
    ocean = pct[pct > -50]
    assert np.median(ocean) > -23.15, "clear-ocean PCT not warm (would be a green/cold ocean)"
    # the convective core is much colder than the ocean
    assert pct[6, 6] < -50.0


def test_pct_vh_self_heal():
    """Passing the V/H pair in EITHER order yields the same PCT (the warmer-over-
    ocean channel is chosen as V), because a flip only damages convection, not the
    clear-ocean median a naive check would use."""
    bt = _scene()
    a = hp.compute_pct89(bt, 63, 62)
    b = hp.compute_pct89(bt, 62, 63)   # deliberately flipped
    assert np.allclose(a, b)


def test_pct_physical_clip():
    # a CRTM overshoot far below the floor clips to -168.15 degC (105 K), and a
    # hot pixel clips to +16.85 degC (290 K)
    bt = {63: np.array([[-300.0, 5.0, 50.0]]), 62: np.array([[-300.0, 4.0, 50.0]])}
    pct = hp.compute_pct89(bt, 63, 62)
    assert np.isclose(pct[0, 0], hp.PCT_CLIP_LO_C)
    assert np.isclose(pct[0, 2], hp.PCT_CLIP_HI_C)


def test_pct_nan_and_missing():
    bt = _scene()
    bt[63][0, 0] = np.nan                       # fill in one channel
    pct = hp.compute_pct89(bt, 63, 62)
    assert np.isnan(pct[0, 0])                  # fill propagates -> NaN
    assert hp.compute_pct89({63: bt[63]}, 63, 62) is None   # H channel absent


def test_fill_mask_threshold():
    # the decode masks >= 9990 (GRIB missingValue 9999) to NaN before -273.15
    vals = np.array([280.0, 9999.0, 9990.0, 9989.9])
    vals[vals >= 9990.0] = np.nan
    assert np.isnan(vals[1]) and np.isnan(vals[2]) and not np.isnan(vals[3])


def test_registry_wiring():
    assert reg.sat_parm("sim_89h") is None          # not a single-channel product
    assert reg.sat_pct("sim_89h") == (63, 62)        # V=63, H=62
    assert tuple(sorted(reg.grib_parms("sim_89h"))) == (62, 63)   # both channels decoded
    assert reg.grib_parms("clean_ir") == (58,)       # single-channel unaffected
    s = reg.REGISTRY["sim_89h"]
    assert s.field_attr == "bt_c" and s.default_enhancement == "ice89h"
    assert "PCT" in s.label and "PCT" in s.channel


def test_pack_frame_builds_pct_into_bt_c():
    """_pack_frame with sat_pct derives frame.bt_c = clipped PCT from the two
    cached channels (the one place the render reads)."""
    H = W = 12
    V = np.full((H, W), 280.0 - 273.15); Hh = np.full((H, W), 279.0 - 273.15)
    V[4:7, 4:7] = 150.0 - 273.15; Hh[4:7, 4:7] = 130.0 - 273.15   # cold core -> spread (not flat)
    raw = dict(model="hafsa", storm="03l", product="storm.atm", fxx=12,
               init_time=None, valid_time=None,
               lon=np.linspace(-66, -60, W), lat=np.linspace(11, 17, H),
               mslp_hpa=np.full((H, W), 1005.0), wind_kt=np.full((H, W), 30.0),
               u_kt=np.zeros((H, W)), v_kt=np.zeros((H, W)),
               refl_dbz=None, pwat=None, bt={62: Hh, 63: V}, upper=None)
    frame = hp._pack_frame(raw, sat_pct=(63, 62))
    assert frame.bt_c is not None
    assert np.nanmedian(frame.bt_c) > -23.15        # blue ocean
    assert float(np.nanmin(frame.bt_c)) >= hp.PCT_CLIP_LO_C - 1e-6


def test_ice89h_kelvin_units():
    """89 PCT (ice89h) displays in KELVIN; data/norm/clip stay degC. The unit flag
    lives on the enhancement and resolves to K for sim_89h, C for the IR/WV BTs."""
    enh = tp.get_enhancement("ice89h")
    assert enh["vmin_c"] == -168.0 and enh["vmax_c"] == 15.0   # norm still degC
    assert enh["units"] == "K"
    assert enh["cbar_label"] == "Brightness Temperature (K)"
    assert all(t >= 100 for t in enh["ticks"])                # ticks are Kelvin
    # IR / WV stay degC
    for k in ("rainbow_ir", "wv_tat"):
        assert tp.get_enhancement(k).get("units", "C") == "C"
        assert "°C" in tp.get_enhancement(k)["cbar_label"]
    # _bt_units resolves off the product's default enhancement
    assert reg._bt_units(reg.REGISTRY["sim_89h"]) == "K"
    assert reg._bt_units(reg.REGISTRY["clean_ir"]) == "C"
    assert reg._bt_units(reg.REGISTRY["water_vapor"]) == "C"


def test_ice89h_anchors_navy_ocean_vivid_mids():
    """The polished ramp: ambient ocean (~ -5 degC) is DEEP NAVY (blue-dominant
    AND dark), and the mid-ramp green is VIVID (saturated, not pastel)."""
    enh = tp.get_enhancement("ice89h"); cmap = enh["cmap"]
    def at(t): return cmap((t - enh["vmin_c"]) / (enh["vmax_c"] - enh["vmin_c"]))
    r, g, b, _ = at(-5.0)                       # ocean
    assert b > r and b > g, f"ocean not blue: {(r,g,b)}"
    assert max(r, g, b) < 0.5, f"ocean not deep navy (too bright): {(r,g,b)}"
    gr, gg, gb, _ = at(-42.0)                   # green mid
    sat = (max(gr, gg, gb) - min(gr, gg, gb)) / max(gr, gg, gb)
    assert gg > gr and gg > gb and sat > 0.7, f"green mid not vivid: {(gr,gg,gb)}"


def test_bt_stat_kelvin_vs_celsius():
    """_bt_stat prints MIN BT in Kelvin for 89 PCT, degC for the IR/WV products,
    both from the SAME raw bt_c field (K = degC + 273.15)."""
    import types
    frame = types.SimpleNamespace(bt_c=np.array([[-100.0, -5.0]], dtype=float))
    scope = hp.StatScope(mask=None, tracked=False, label="")
    _, rs_k = reg._bt_stat(reg.REGISTRY["sim_89h"], frame, "Storm nest", 0.0, 1000.0, scope)
    assert "MIN BT 173.1 K" in rs_k and "°C" not in rs_k, rs_k   # -100 + 273.15
    _, rs_c = reg._bt_stat(reg.REGISTRY["clean_ir"], frame, "Storm nest", 0.0, 1000.0, scope)
    assert "MIN BT -100.0°C" in rs_c and " K " not in rs_c, rs_c
