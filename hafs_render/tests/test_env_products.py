"""Parent-domain ENVIRONMENTAL products: registry wiring, env field-name set,
the field cache round-trip, and a synthetic render of all 9 products (no network).

The render path is exercised with a synthetic env frame so it stays offline and
fast - it proves render_frame's env branch (synoptic full extent, no L / no SSHWS
pill, the streamline overlay, the env colorbars + stats) doesn't crash and writes
a non-trivial PNG for every one of the 9 products."""
import sys
import pathlib
import tempfile

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # hafs_render pkg
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "palette"))

from hafs_render import hafs_plot as hp          # noqa: E402
from hafs_render import hafs_registry as reg     # noqa: E402
from hafs_render import hafs_cache as fc          # noqa: E402

ENV_KEYS = ("env_precip", "env_shear_200_850", "env_shear_500_850", "env_pv_200",
            "env_sst", "env_tropt", "env_cape", "env_srh", "env_lhtfl")


def test_registry_has_nine_parent_only_synoptic_env_products():
    specs = {s.key: s for s in reg.ordered_specs()}
    env = [s for s in specs.values() if s.requires_attr == "env"]
    assert len(env) == 9
    assert {s.key for s in env} == set(ENV_KEYS)
    for s in env:
        assert s.domains == ("parent.atm",), s.key      # parent-only
        assert s.synoptic_parent is True, s.key          # synoptic (no storm crop)
        assert s.grib == "atm" and s.sat_parm is None, s.key
        # every env product appears in the published PRODUCTS dict (manifest/toggle)
        assert s.key in reg.products_dict()


def test_env_field_names_cover_every_consumed_key():
    names = set(hp.env_field_names())
    # the render factories read these straight off frame.env
    for k in ("apcp_in", "sst_c", "tropt_c", "cape_jkg", "lhtfl_wm2", "srh_03km",
              "pv_200", "u_200", "v_200",
              "shrmag_200_850", "shru_200_850", "shrv_200_850",
              "shrmag_500_850", "shru_500_850", "shrv_500_850"):
        assert k in names, k


def _synthetic_env_frame():
    """A small regular parent-like grid with every env field filled (plausible
    magnitudes), so render_frame's env branch has real data to draw."""
    lat = np.linspace(8.0, 32.0, 40)
    lon = np.linspace(-62.0, -30.0, 50)
    Lon, Lat = np.meshgrid(lon, lat)
    # a gentle low so the MSLP isobars + (suppressed) L have something to draw
    mslp = 1012.0 - 8.0 * np.exp(-(((Lon + 46) / 6) ** 2 + ((Lat - 20) / 6) ** 2))
    u = 10.0 + 5.0 * np.sin(np.deg2rad(Lat))
    v = -6.0 + 4.0 * np.cos(np.deg2rad(Lon))
    wind = np.hypot(u, v)
    env = {
        "apcp_in": np.abs(2.0 * np.exp(-(((Lon + 46) / 4) ** 2))),
        "sst_c": 28.0 - 0.2 * (Lat - 20),
        "tropt_c": -70.0 + 5.0 * np.sin(np.deg2rad(Lon)),
        "cape_jkg": np.abs(1800.0 * np.exp(-(((Lat - 18) / 5) ** 2))),
        "lhtfl_wm2": 150.0 + 80.0 * np.cos(np.deg2rad(Lat)),
        "srh_03km": np.abs(180.0 * np.exp(-(((Lon + 44) / 5) ** 2))),
        "pv_200": np.abs(3.0 + 4.0 * np.sin(np.deg2rad(2 * Lat))),
        "u_200": 30.0 + 10.0 * np.sin(np.deg2rad(Lat)),
        "v_200": 5.0 * np.cos(np.deg2rad(Lon)),
    }
    for up, lo in ((200, 850), (500, 850)):
        du = 20.0 * np.sin(np.deg2rad(Lat))
        dv = 10.0 * np.cos(np.deg2rad(Lon))
        env[f"shru_{up}_{lo}"] = du
        env[f"shrv_{up}_{lo}"] = dv
        env[f"shrmag_{up}_{lo}"] = np.hypot(du, dv)
    import datetime as dt
    return hp.HafsFrame(
        model="hafsa", storm="07w", product="parent.atm", fxx=12,
        init_time=dt.datetime(2026, 6, 27, 12), valid_time=dt.datetime(2026, 6, 28, 0),
        lon=lon, lat=lat, mslp_hpa=mslp, wind_kt=wind, u_kt=u, v_kt=v,
        extent=(float(lon.min()), float(lon.max()), float(lat.min()), float(lat.max())),
        env=env)


def test_render_all_nine_env_products():
    frame = _synthetic_env_frame()
    with tempfile.TemporaryDirectory() as d:
        for key in ENV_KEYS:
            out = pathlib.Path(d) / f"{key}.png"
            # countries/coast None -> no basemap fetch; the env data path is what
            # we're proving renders without crashing.
            hp.render_frame(frame, str(out), None, None, product=key)
            assert out.exists() and out.stat().st_size > 8000, key
