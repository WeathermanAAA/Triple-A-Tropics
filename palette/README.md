# tat-palettes (canonical TAT color palettes)

Single source of truth for every Triple-A-Tropics color palette: `rainbow_ir`,
`dvorak` (BD), `wv_tat`, `ir_gray`/`grayscale`, `tat_neon`, the enhancement
helpers (`get_enhancement`, `enhancement_norm`, `list_enhancements_for_domain`),
and the IR-vs-WV domain split. The palette definitions were extracted VERBATIM
from `tat-satellite-render/colormaps.py`; the no-drift self-test proves the
extraction changed no rendered color.

## Why this package exists

The same palettes are needed by two repos and (soon) ~50 models:

- the satellite backend `tat-satellite-render` (`render.py` / `app.py` /
  `floater_poller.py`), which renders `/satellite/` and the floater loop, and
- the main Pages repo's HAFS model pipeline (the upcoming sim-sat product).

Copying anchors into each consumer would drift. Instead this package lives in
the main Pages repo under `palette/` (the only repo the dev environment can push)
and every consumer imports it, so a color edit is ONE edit here that propagates
to every product.

## How each consumer resolves it

- **Main repo (HAFS pipeline + GitHub Actions):** the package is in-tree.
  `.github/workflows/update-hafs.yml` runs `pip install ./palette`, then any
  generator can `import tat_palettes`.
- **tat-satellite-render (Railway):** `requirements.txt` pins
  `tat-palettes @ git+https://github.com/WeathermanAAA/Triple-A-Tropics.git@main#subdirectory=palette`.
  Railway's nixpacks installs with `--no-cache-dir`, so each deploy refetches
  `@main` and picks up palette edits automatically. Its `colormaps.py` is a thin
  re-export shim, so `render.py` keeps importing from `colormaps` unchanged.

Tracking `@main` gives one-edit propagation. If you ever want a frozen build,
pin the requirement to a tag or commit SHA instead and bump it deliberately.

## Self-test (zero-drift guarantee)

```
pip install ./palette
python -m tat_palettes          # prints PASSED (57 golden RGBA checks)
python palette/tests/test_no_drift.py
```

`tat_palettes/_selftest.py` holds RGBA tuples captured from the pre-refactor
`colormaps.py` at known brightness-temperature inputs and asserts the shared
module reproduces them EXACTLY. Run it after any palette edit.

## Do not

- Do not add or change palette definitions in a consumer's `colormaps.py`;
  edit `tat_palettes` here.
- Do not recolor here unless you intend to recolor every consumer; update the
  golden values in `_selftest.py` only when a recolor is intentional.
