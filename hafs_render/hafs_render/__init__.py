"""hafs_render - the Triple-A-Tropics HAFS model-plot render pipeline as an
installable package: ONE source of truth shared by the Actions cron
(update-hafs.yml, via the repo-root generate_hafs_plots.py shim) AND the
tat-satellite-render box render worker (which pins this package as a git
dependency, the ace_core pattern).

The four modules (hafs_plot, hafs_registry, hafs_cache, generate_hafs_plots) are
unchanged in render logic - only their cross-imports were made absolute
(``from hafs_render import ...``) and generate_hafs_plots' default --out-dir is
now CWD-relative. The package bundles its own Natural Earth basemaps
(``*.geojson``) and the TAT radar palette (``assets/TAT-radar.pal``) as
package_data, resolved via ``Path(__file__).parent`` (already how hafs_plot did
it), so a fresh install renders with no external asset fetch.

Entrypoint: ``from hafs_render.generate_hafs_plots import build_cycle, main``.

Runtime dependency intentionally NOT declared in pyproject (it is not on PyPI;
the environment provides it): ``tat_palettes`` - installed via the main repo's
``palette/`` package (``pip install ./palette``) or the ``tat-palettes`` git
dependency the satellite worker already carries.
"""

__version__ = "0.1.0"
